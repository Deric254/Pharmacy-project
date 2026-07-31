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

    async def _build_business_context(self, user: User) -> dict[str, object]:
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
        """
        today = date.today()
        user_permission_codes = {p.code for p in user.role.permissions}
        include_profit = "reports.view_profit" in user_permission_codes

        try:
            kpi = await ReportService(self.db).kpi_dashboard(today, today, include_profit)
        except Exception:  # noqa: BLE001 - business context is enrichment, never load-bearing
            return {}

        context: dict[str, object] = {
            "today_revenue": kpi.revenue,
            "today_transaction_count": kpi.transaction_count,
            "today_average_basket": kpi.average_basket,
            "low_stock_product_count": kpi.low_stock_count,
            "expiring_soon_batch_count": kpi.expiring_soon_count,
        }
        if kpi.profit is not None:
            context["today_profit"] = kpi.profit
            context["today_profit_margin_percent"] = kpi.profit_margin_percent
        if kpi.top_products:
            context["top_selling_products_today"] = ", ".join(
                f"{p.name} ({p.quantity_sold} sold)" for p in kpi.top_products[:3]
            )
        return context

    async def ask(self, user: User, payload: AIAskRequest) -> AIAskResponse:
        result = await self.db.execute(
            select(AIProviderKey)
            .where(AIProviderKey.user_id == user.id, AIProviderKey.is_active.is_(True))
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
        # override or fake the real business figures.
        business_context = await self._build_business_context(user)
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
