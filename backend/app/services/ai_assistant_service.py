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
from datetime import UTC, datetime

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


_NO_KEYS_MESSAGE = (
    "No AI provider is configured yet. Add an API key (OpenAI, Claude, Gemini, "
    "DeepSeek, or NVIDIA) in AI settings to start using the assistant."
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

    async def ask(self, user: User, payload: AIAskRequest) -> AIAskResponse:
        result = await self.db.execute(
            select(AIProviderKey)
            .where(AIProviderKey.user_id == user.id, AIProviderKey.is_active.is_(True))
            .order_by(AIProviderKey.priority)
        )
        keys = list(result.scalars().all())

        if not keys:
            return AIAskResponse(answer=_NO_KEYS_MESSAGE, provider_used=None, fallback_used=False)

        for index, key_row in enumerate(keys):
            try:
                decrypted_key = decrypt_secret(key_row.encrypted_key)
                adapter = self.adapter_factory(key_row.provider, decrypted_key)
                response = await adapter.ask(payload.prompt, dict(payload.context or {}))
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
