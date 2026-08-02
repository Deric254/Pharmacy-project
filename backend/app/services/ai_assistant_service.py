"""
AI assistant service.

The panel must never show a broken state to the user: `ask()` always
returns a 200-shaped AIAskResponse, never raises past this layer. If
every configured provider fails (or none are configured at all), the
response is a graceful message explaining that, not an exception.

`adapter_factory` is injectable specifically so tests can verify the
fallback chain (first fails -> tries second -> succeeds, or all fail ->
graceful message) using fake adapters, without making real calls to
paid third-party APIs in CI. Production code uses the real default map.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
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
from app.services.report_service import ReportService

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
        today = date.today()
        range_start = viewed_start or today
        range_end = viewed_end or today
        user_permission_codes = {p.code for p in user.role.permissions}
        include_profit = "reports.view_profit" in user_permission_codes

        try:
            report_service = ReportService(self.db)
            kpi = await report_service.kpi_dashboard(range_start, range_end, include_profit)
        except Exception:  # noqa: BLE001 - business context is enrichment, never load-bearing
            return {"person_asking_name": user.full_name}

        period_label = "today" if range_start == range_end == today else "viewed_period"
        context: dict[str, object] = {
            "person_asking_name": user.full_name,
            f"{period_label}_revenue": kpi.revenue,
            f"{period_label}_transaction_count": kpi.transaction_count,
            f"{period_label}_average_basket": kpi.average_basket,
            "low_stock_product_count": kpi.low_stock_count,
            "expiring_soon_batch_count": kpi.expiring_soon_count,
        }
        if kpi.profit is not None:
            context[f"{period_label}_profit"] = kpi.profit
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
        result = await self.db.execute(
            select(AIProviderKey)
            .where(AIProviderKey.is_active.is_(True))
            .order_by(AIProviderKey.priority)
        )
        keys = list(result.scalars().all())

        if not keys:
            user_permission_codes = {p.code for p in user.role.permissions}
            can_self_serve = "users.manage" in user_permission_codes
            message = _NO_KEYS_MESSAGE_SELF_SERVE if can_self_serve else _NO_KEYS_MESSAGE_ESCALATE
            return AIAskResponse(answer=message, provider_used=None, fallback_used=False)

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

        for index, key_row in enumerate(keys):
            try:
                decrypted_key = decrypt_secret(key_row.encrypted_key)
                adapter = self.adapter_factory(key_row.provider, decrypted_key)
                response = await adapter.ask(payload.prompt, full_context)
            except AIProviderError:
                continue  # try the next provider in priority order
            except Exception:  # noqa: BLE001 - any adapter failure must fall through, never crash the panel
                continue

            key_row.last_used_at = datetime.now(UTC)
            await self.db.commit()
            return AIAskResponse(
                answer=response.text, provider_used=key_row.provider, fallback_used=index > 0
            )

        return AIAskResponse(answer=_ALL_FAILED_MESSAGE, provider_used=None, fallback_used=True)
