"""
AI assistant service.

The panel must never show a broken state to the user: `ask()` always
returns a 200-shaped AIAskResponse for any *provider* problem (no keys
configured, a provider erroring, every provider failing) -- never
raises past this layer for those. The one deliberate exception is
ConversationNotFound: passing a conversation_id that doesn't exist or
belongs to someone else is a genuine client error (wrong resource
reference), not a "the AI backend is having a bad day" situation, so
it raises rather than being swallowed into a chat message. The API
route translates that into a 404.

`adapter_factory` is injectable specifically so tests can verify the
fallback chain (first fails -> tries second -> succeeds, or all fail ->
graceful message) using fake adapters, without making real calls to
paid third-party APIs in CI. Production code uses the real default map.

Every per-provider failure is logged here (see the `except` blocks in
`ask()` below) precisely because the user-facing message is, and
should stay, generic ("please try again"). Without the log line, a
real, fixable cause -- a retired model ID, an expired key, a rate
limit -- was completely unrecoverable after the fact: previously
nothing recorded which provider failed or why, so a genuine bug (a
hardcoded model ID going dead) could hide behind the same
generic-sounding failure indefinitely, indistinguishable from a
transient network blip. The fix is a log line, not a UI change --
the person using the AI panel still just sees "try again shortly".
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import business_today
from app.core.security import decrypt_secret
from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.ai_provider_key import AIProviderKey, AIProviderName
from app.models.user import User
from app.schemas.ai import AIAskRequest, AIAskResponse
from app.services.ai.adapters import (
    ClaudeAdapter,
    DeepSeekAdapter,
    GeminiAdapter,
    NvidiaAdapter,
    OpenAIAdapter,
)
from app.services.ai.base import AIProvider, AIProviderError
from app.services.ai_conversation_service import AIConversationService, ConversationNotFound
from app.services.business_config_service import BusinessConfigService
from app.services.report_service import ReportService

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[AIProviderName, str], AIProvider]

_DEFAULT_ADAPTER_CLASSES: dict[AIProviderName, type[AIProvider]] = {
    AIProviderName.OPENAI: OpenAIAdapter,
    AIProviderName.CLAUDE: ClaudeAdapter,
    AIProviderName.GEMINI: GeminiAdapter,
    AIProviderName.DEEPSEEK: DeepSeekAdapter,
    AIProviderName.NVIDIA: NvidiaAdapter,
}


def _default_adapter_factory(provider: AIProviderName, api_key: str) -> AIProvider:
    return _DEFAULT_ADAPTER_CLASSES[provider](api_key=api_key)


def _parse_context_date(
    context: dict[str, str | int | float | bool | None] | None, key: str
) -> date | None:
    """
    Pulls only a date out of client-sent context, nothing else --
    anything missing, malformed, or of the wrong type is silently
    ignored rather than trusted, and _build_business_context simply
    falls back to today exactly as if nothing had been sent at all.
    """
    if not context:
        return None
    raw = context.get(key)
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


_NO_KEYS_MESSAGE_SELF_SERVE = (
    "No AI provider is configured yet. Add an API key (OpenAI, Claude, Gemini, "
    "DeepSeek, or NVIDIA) in AI settings to start getting real answers."
)
_NO_KEYS_MESSAGE_ESCALATE = (
    "No AI provider is configured for your account yet. Ask your pharmacy owner or "
    "administrator to add an AI key so you can use the assistant."
)
_ALL_FAILED_MESSAGE = (
    "AI is temporarily unavailable right now (all configured providers failed to "
    "respond). Please try again shortly, or check that your API keys are still valid."
)


class AIAssistantService:
    def __init__(
        self, db: AsyncSession, adapter_factory: AdapterFactory = _default_adapter_factory
    ) -> None:
        self.db = db
        self.adapter_factory = adapter_factory

    async def _build_business_context(
        self, user: User, viewed_start: date | None = None, viewed_end: date | None = None
    ) -> dict[str, object]:
        """
        Real, current business numbers, computed server-side right
        before every question -- never trusts whatever a client might
        send as "context", which could be stale or spoofed. Reuses
        the exact same KPI computation the dashboard uses (same
        source of truth, same accuracy guarantees, nothing
        duplicated), including the same profit-visibility rule: a
        user without reports.view_profit gets no profit numbers
        here either, matching the dashboard exactly rather than
        accidentally leaking profit into an AI answer through a
        wider door than the dashboard itself allows.

        viewed_start/viewed_end let the assistant discuss whatever
        range the person is actually looking at on the Dashboard
        right now (e.g. "last month") instead of always defaulting to
        today -- this only ever crosses a date range from client to
        server, never a number. Every figure below is still computed
        fresh here, server-side, exactly as if the person had asked
        about today with no range supplied at all.
        """
        today = await business_today(self.db)
        range_start = viewed_start or today
        range_end = viewed_end or today
        user_permission_codes = {p.code for p in user.role.permissions}
        include_profit = "reports.view_profit" in user_permission_codes

        try:
            report_service = ReportService(self.db)
            kpi = await report_service.kpi_dashboard(range_start, range_end, include_profit)
        except Exception:  # noqa: BLE001 - business context is enrichment, never load-bearing
            return {"person_asking_name": user.full_name}

        # The business's own configured currency, not an assumption --
        # this context becomes plain "- key: value" lines in the
        # prompt (see ai/adapters.py's _build_prompt_with_context),
        # and a bare number like "1553.68" with no unit gives a model
        # nothing to go on except its own default assumption, which is
        # USD far more often than not. Every genuinely monetary value
        # below carries the real currency code inline, right next to
        # the figure itself -- not as a separate "currency: KES" line
        # the model would also have to correctly associate with only
        # SOME of the other lines (transaction counts and percentages
        # below are never money and must never get this prefix).
        try:
            business_config = await BusinessConfigService(self.db).get()
            currency = business_config.currency
        except Exception:  # noqa: BLE001 - same enrichment-only principle as above
            currency = ""

        def money(value: float) -> str:
            return f"{currency} {value:.2f}".strip()

        period_label = "today" if range_start == range_end == today else "viewed_period"
        context: dict[str, object] = {
            "person_asking_name": user.full_name,
            f"{period_label}_revenue": money(kpi.revenue),
            f"{period_label}_transaction_count": kpi.transaction_count,
            f"{period_label}_average_basket": money(kpi.average_basket),
            "low_stock_product_count": kpi.low_stock_count,
            "expiring_soon_batch_count": kpi.expiring_soon_count,
        }
        # Real, already-computed comparison against the immediately
        # preceding period of equal length (same figure the dashboard
        # itself shows) -- this is what lets the assistant's mandatory
        # closing summary (see _FORMATTING_RULES) state an actual
        # trajectory instead of guessing "things seem to be going
        # well" with nothing behind it. None when there's no prior
        # period to compare against yet (a brand new business) --
        # passed through as None rather than omitted, so the prompt
        # can tell the model there's genuinely no trend data yet
        # instead of silently having a gap it might paper over.
        if kpi.revenue_change_percent is not None:
            context[f"{period_label}_revenue_change_vs_prior_period_percent"] = round(
                kpi.revenue_change_percent, 1
            )
        if kpi.profit is not None:
            context[f"{period_label}_profit"] = money(kpi.profit)
            context[f"{period_label}_profit_margin_percent"] = kpi.profit_margin_percent
        if kpi.top_products:
            context[f"top_selling_products_{period_label}"] = ", ".join(
                f"{p.name} ({p.quantity_sold} sold)" for p in kpi.top_products[:3]
            )

        try:
            top_customers = await report_service.top_customers(range_start, range_end, limit=5)
            if top_customers.entries:
                context["top_customers_by_revenue"] = ", ".join(
                    f"{c.name} ({c.cumulative_percent:.0f}% cumulative)"
                    for c in top_customers.entries
                )
        except Exception:  # noqa: BLE001 - enrichment only, never load-bearing
            pass

        return context

    async def ask(self, user: User, payload: AIAskRequest) -> AIAskResponse:
        conversation_service = AIConversationService(self.db)
        conversation: AIConversation | None
        if payload.conversation_id is None:
            conversation = await conversation_service.create_conversation(user, payload.prompt)
        else:
            conversation = await conversation_service.get_owned_conversation(
                user, payload.conversation_id
            )
            if conversation is None:
                raise ConversationNotFound(payload.conversation_id)

        answer: str
        provider_used: AIProviderName | None
        fallback_used: bool

        result = await self.db.execute(
            select(AIProviderKey)
            .where(AIProviderKey.is_active.is_(True))
            .order_by(AIProviderKey.priority)
        )
        keys = list(result.scalars().all())

        if not keys:
            user_permission_codes = {p.code for p in user.role.permissions}
            can_self_serve = "users.manage" in user_permission_codes
            answer = _NO_KEYS_MESSAGE_SELF_SERVE if can_self_serve else _NO_KEYS_MESSAGE_ESCALATE
            provider_used = None
            fallback_used = False
        else:
            # Real business numbers always included, computed fresh for
            # this exact question -- whatever the client sent in
            # payload.context is layered underneath, so it can add extra
            # detail (e.g. "the product I'm asking about") but can never
            # override or fake the real business figures. The one thing
            # pulled out of it deliberately is a date range (e.g. "the
            # Dashboard's slicer is currently set to last month") -- never
            # a number, just what period to freshly recompute here.
            viewed_start = _parse_context_date(payload.context, "viewing_start_date")
            viewed_end = _parse_context_date(payload.context, "viewing_end_date")
            business_context = await self._build_business_context(user, viewed_start, viewed_end)
            full_context: dict[str, object] = {**(payload.context or {}), **business_context}

            answer = _ALL_FAILED_MESSAGE
            provider_used = None
            fallback_used = True
            for index, key_row in enumerate(keys):
                try:
                    decrypted_key = decrypt_secret(key_row.encrypted_key)
                    adapter = self.adapter_factory(key_row.provider, decrypted_key)
                    response = await adapter.ask(payload.prompt, full_context)
                except AIProviderError as exc:
                    # Never the API key itself -- str(exc) here is the
                    # adapter's own wrapped message (HTTP status, body
                    # snippet), which does not include the raw key.
                    logger.warning(
                        "AI provider %s failed, trying next: %s", key_row.provider.value, exc
                    )
                    continue  # try the next provider in priority order
                except Exception:  # noqa: BLE001 - adapter failure must fall through, never crash the panel
                    logger.exception(
                        "AI provider %s raised an unexpected error", key_row.provider.value
                    )
                    continue

                key_row.last_used_at = datetime.now(UTC)
                answer = response.text
                provider_used = key_row.provider
                fallback_used = index > 0
                break

        self.db.add(
            AIConversationMessage(
                conversation_id=conversation.id,
                prompt=payload.prompt,
                answer=answer,
                provider_used=provider_used.value if provider_used else None,
            )
        )
        conversation.updated_at = datetime.now(UTC)
        await self.db.commit()

        return AIAskResponse(
            answer=answer,
            provider_used=provider_used,
            fallback_used=fallback_used,
            conversation_id=conversation.id,
        )
