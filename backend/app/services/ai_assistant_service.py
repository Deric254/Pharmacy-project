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

import calendar
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import business_today, get_business_timezone
from app.core.security import decrypt_secret
from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.ai_provider_key import AIProviderKey, AIProviderName
from app.models.sale import Sale
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

_MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

_DEFAULT_ADAPTER_CLASSES: dict[AIProviderName, type[AIProvider]] = {
    AIProviderName.OPENAI: OpenAIAdapter,
    AIProviderName.CLAUDE: ClaudeAdapter,
    AIProviderName.GEMINI: GeminiAdapter,
    AIProviderName.DEEPSEEK: DeepSeekAdapter,
    AIProviderName.NVIDIA: NvidiaAdapter,
}


def _default_adapter_factory(provider: AIProviderName, api_key: str) -> AIProvider:
    return _DEFAULT_ADAPTER_CLASSES[provider](api_key=api_key)


_MONTH_NAME_TO_NUM = {name.lower(): index + 1 for index, name in enumerate(_MONTH_NAMES)}
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_LAST_N_DAYS_RE = re.compile(r"\blast\s+(\d{1,3})\s+days?\b")
_MONTH_YEAR_RE = {name: re.compile(rf"\b{name}\s+(\d{{4}})\b") for name in _MONTH_NAME_TO_NUM}

# Deliberately narrow and literal, matching _parse_period_from_prompt's own
# philosophy: every phrase here unambiguously asks for the WHOLE business
# history, never a vague "how are things" that could just as easily mean
# today. Before this existed, a question like "what's our all-time revenue"
# or "how have we done since we started" silently fell through to whatever
# the Dashboard's slicer happened to still be sitting on (or today, if
# nothing was open) -- the assistant would answer a narrow-range question
# with a narrow-range number and never say so, which is a correctness bug,
# not just a missing feature: the number shown was real, but it was the
# answer to a different question than the one actually asked.
_ALL_TIME_RE = re.compile(
    r"\ball[\s-]time\b"
    r"|\bsince (?:we started|inception|day one|the beginning)\b"
    r"|\b(?:entire|whole|full) history\b"
    r"|\boverall(?:,)? (?:how have we|how has the business|performance)\b"
    r"|\bcumulative(?:ly)?\b"
    r"|\blifetime\b"
    r"|\bgrand total\b"
    r"|\bacross (?:all time|everything|every year)\b"
)


def _parse_period_from_prompt(prompt: str, today: date) -> tuple[date, date] | None:
    """
    Best-effort extraction of an explicit period from what the person
    actually TYPED (e.g. "how did we do last month", "sales for
    August 2026") -- this is what lets the assistant answer about a
    different period on request, rather than being stuck on whatever
    the Dashboard's own date filter happens to still be sitting on
    (see viewed_start/viewed_end in _build_business_context, and
    ask() below for how the two interact: an explicit period found
    here always wins over the client-sent viewing range, because
    typing a question about a specific period is a more direct signal
    than an incidentally-still-open dashboard filter).

    Deliberately narrow and literal, not real NLU: every branch below
    is an unambiguous, fixed phrase or an explicit date. Anything not
    recognized returns None, and the caller falls back to the viewed
    range (or today) exactly as it did before this existed -- this
    never guesses at an ambiguous phrase like "recently" or "lately".

    No "today" branch on purpose: falling through to None here already
    yields today via the existing default, so a bare "today" mention
    stays labeled "today" in the context exactly as before, rather
    than being relabeled "requested_period" for no behavioral
    difference other than a confusing key rename.
    """
    text = prompt.lower()

    iso_dates = _ISO_DATE_RE.findall(text)
    if len(iso_dates) >= 2:
        try:
            first, second = date.fromisoformat(iso_dates[0]), date.fromisoformat(iso_dates[1])
        except ValueError:
            pass
        else:
            return (first, second) if first <= second else (second, first)
    elif len(iso_dates) == 1:
        try:
            single = date.fromisoformat(iso_dates[0])
        except ValueError:
            pass
        else:
            return (single, single)

    last_n_days_match = _LAST_N_DAYS_RE.search(text)
    if last_n_days_match:
        n = int(last_n_days_match.group(1))
        if n > 0:
            # Inclusive of today, e.g. "last 7 days" = today and the 6
            # days before it -- matching how the Dashboard's own
            # relative-range filter counts, not an off-by-one.
            return (today - timedelta(days=n - 1), today)

    if "yesterday" in text:
        yesterday = today - timedelta(days=1)
        return (yesterday, yesterday)

    if "last week" in text:
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        return (last_monday, last_monday + timedelta(days=6))

    if "this week" in text:
        this_monday = today - timedelta(days=today.weekday())
        return (this_monday, today)

    if "last month" in text:
        first_of_this_month = today.replace(day=1)
        last_day_of_prev_month = first_of_this_month - timedelta(days=1)
        first_of_prev_month = last_day_of_prev_month.replace(day=1)
        return (first_of_prev_month, last_day_of_prev_month)

    if "this month" in text:
        return (today.replace(day=1), today)

    if "last year" in text:
        return (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))

    if "this year" in text:
        return (date(today.year, 1, 1), today)

    for month_name, month_num in _MONTH_NAME_TO_NUM.items():
        if month_name not in text:
            continue
        year_match = _MONTH_YEAR_RE[month_name].search(text)
        if year_match:
            year = int(year_match.group(1))
        else:
            # No year stated -- assume the most recent occurrence of
            # that month rather than a future one, e.g. asking about
            # "March" in September 2026 means March 2026 (already
            # past), but asking about "November" in September 2026
            # means November 2025 (the only November that's actually
            # happened), not a month that hasn't occurred yet.
            year = today.year if month_num <= today.month else today.year - 1
        last_day = calendar.monthrange(year, month_num)[1]
        return (date(year, month_num, 1), date(year, month_num, last_day))

    return None


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
        self,
        user: User,
        viewed_start: date | None = None,
        viewed_end: date | None = None,
        period_source: Literal["today", "viewed_period", "requested_period", "all_time"]
        | None = None,
    ) -> dict[str, object]:
        """
        Real, current business numbers, computed server-side right
        before every question -- never trusts whatever a client might
        send as "context", which could be stale or spoofed. Reuses
        the exact same KPI computation the dashboard uses (same
        source of truth, same accuracy guarantees, nothing
        duplicated), including the same visibility rules: a user
        without reports.view gets nothing here but their own name,
        and one without reports.view_profit gets no profit numbers,
        matching the dashboard exactly rather than accidentally
        leaking figures into an AI answer through a wider door than
        the dashboard itself allows.

        viewed_start/viewed_end let the assistant discuss whatever
        range the person is actually looking at on the Dashboard
        right now (e.g. "last month") instead of always defaulting to
        today -- this only ever crosses a date range from client to
        server, never a number. Every figure below is still computed
        fresh here, server-side, exactly as if the person had asked
        about today with no range supplied at all.

        period_source names WHY this range was chosen (today's
        default / the Dashboard's own filter / a period the person
        typed directly into this question -- see
        _parse_period_from_prompt in ask()), so the context key
        prefix tells the model something real about where the range
        came from instead of every non-today range looking identical.
        Optional and defaults to the old today-vs-viewed_period
        inference so direct callers (tests included) that don't pass
        it keep their exact previous behavior.
        """
        today = await business_today(self.db)
        range_start = viewed_start or today
        range_end = viewed_end or today
        user_permission_codes = {p.code for p in user.role.permissions}
        if "reports.view" not in user_permission_codes:
            # Revenue, transaction counts, top products and named top
            # customers are what the Dashboard and Reports pages show, and
            # those need reports.view -- ai.use alone (a cashier) must not
            # read them back through the assistant, a wider door than the
            # pages themselves.
            return {"person_asking_name": user.full_name}
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

        period_label = period_source or (
            "today" if range_start == range_end == today else "viewed_period"
        )
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

        try:
            co_occurrence = await report_service.product_co_occurrence(days=90, limit=1)
            if co_occurrence.pairs:
                top_pair = co_occurrence.pairs[0]
                context["most_frequently_bought_together"] = (
                    f"{top_pair.product_a_name} + {top_pair.product_b_name} "
                    f"({top_pair.percent_of_a_sales:.0f}% of {top_pair.product_a_name} "
                    "sales also include the other)"
                )
        except Exception:  # noqa: BLE001 - enrichment only, never load-bearing
            pass

        try:
            seasonal = await report_service.seasonal_trends(days=730)
            if seasonal.has_sufficient_history and seasonal.entries:
                top_seasonal = seasonal.entries[0]
                context["top_seasonal_pattern"] = (
                    f"{top_seasonal.name} sells most in "
                    f"{_MONTH_NAMES[top_seasonal.month - 1]} "
                    f"({top_seasonal.total_quantity_sold} units, summed across every "
                    "year on record)"
                )
        except Exception:  # noqa: BLE001 - enrichment only, never load-bearing
            pass

        return context

    async def _earliest_sale_date(self) -> date | None:
        """
        The local calendar date of the very first sale ever recorded, or
        None for a business with no sales yet. A single MIN() aggregate --
        not a row fetch -- so this stays cheap regardless of how many
        years of sales have accumulated; it never loads a Sale row into
        Python, just the one timestamp SQLite already indexes on
        Sale.created_at.
        """
        result = await self.db.execute(select(func.min(Sale.created_at)))
        earliest = result.scalar_one_or_none()
        if earliest is None:
            return None
        tz = await get_business_timezone(self.db)
        return earliest.replace(tzinfo=UTC).astimezone(tz).date()

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
            # override or fake the real business figures. Two possible
            # sources for which date range to recompute: whatever the
            # Dashboard's own slicer is currently set to (sent as
            # viewing_start_date/viewing_end_date), or a period the
            # person typed directly into THIS question (e.g. "how about
            # last month") -- the latter always wins, since asking about
            # a specific period is a more direct signal than an
            # incidentally-still-open dashboard filter from whatever the
            # person was looking at before they opened the chat. Either
            # way this only ever crosses a date range, never a number --
            # every figure is still computed fresh, server-side.
            today = await business_today(self.db)
            viewed_start = _parse_context_date(payload.context, "viewing_start_date")
            viewed_end = _parse_context_date(payload.context, "viewing_end_date")
            requested_period = _parse_period_from_prompt(payload.prompt, today)
            period_source: Literal["today", "viewed_period", "requested_period", "all_time"]
            if requested_period is not None:
                viewed_start, viewed_end = requested_period
                period_source = "requested_period"
            elif _ALL_TIME_RE.search(payload.prompt.lower()):
                # An explicit "all time" / "since we started" question must
                # never be silently narrowed to whatever the Dashboard's
                # slicer is still open to -- that's the exact gap this
                # branch closes. earliest_sale_date is None only for a
                # brand-new business with no sales yet, in which case
                # there is no history to widen to and today is still the
                # correct, honest answer.
                earliest_sale_date = await self._earliest_sale_date()
                if earliest_sale_date is not None:
                    viewed_start, viewed_end = earliest_sale_date, today
                    period_source = "all_time"
                else:
                    period_source = "today"
            elif viewed_start is not None or viewed_end is not None:
                period_source = "viewed_period"
            else:
                period_source = "today"
            business_context = await self._build_business_context(
                user, viewed_start, viewed_end, period_source
            )
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
                    # logger.exception() reads the current exception
                    # from sys.exc_info() automatically -- no need to
                    # bind `as exc` just to reference it explicitly.
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
