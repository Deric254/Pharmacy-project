"""
AI assistant service.

A question is answered in two model calls with real data in between:

  1. PLAN   -- the model is shown the read-only lookups this person may use
               (see ai/data_tools.py) and replies with the ones it needs.
  2. LOOKUP -- the server validates and runs them, under this person's own
               permissions, against the same services the pages use.
  3. ANSWER -- the model writes the answer from exactly that data.

The panel must never show a broken state: `ask()` always returns a 200-shaped
AIAskResponse for any *provider* problem (no keys configured, a provider
erroring, every provider failing) and never raises past this layer for those.
The one deliberate exception is ConversationNotFound: passing a conversation_id
that doesn't exist or belongs to someone else is a genuine client error, which
the API route translates into a 404.

`adapter_factory` is injectable so tests can verify the whole flow, including
the fallback chain (first fails -> tries second -> succeeds, or all fail ->
graceful message), with fake adapters and no real calls to paid APIs.

Every per-provider failure is logged here precisely because the message the
person sees is, and should stay, generic: without the log line a fixable cause
(a retired model ID, an expired key, a rate limit) would be unrecoverable.
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import NamedTuple

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
from app.services.ai.data_tools import (
    MAX_CALLS,
    ToolContext,
    available_tools,
    catalog_text,
    parse_plan,
    render_results,
    run_tool_calls,
)
from app.services.ai.prompts import answer_prompt, planner_prompt
from app.services.ai_conversation_service import AIConversationService, ConversationNotFound
from app.services.business_config_service import BusinessConfigService

logger = logging.getLogger(__name__)
AdapterFactory = Callable[[AIProviderName, str], AIProvider]

_DEFAULT_ADAPTER_CLASSES: dict[AIProviderName, type[AIProvider]] = {
    AIProviderName.OPENAI: OpenAIAdapter,
    AIProviderName.CLAUDE: ClaudeAdapter,
    AIProviderName.GEMINI: GeminiAdapter,
    AIProviderName.DEEPSEEK: DeepSeekAdapter,
    AIProviderName.NVIDIA: NvidiaAdapter,
}

_HISTORY_TURNS = 3

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


def _default_adapter_factory(provider: AIProviderName, api_key: str) -> AIProvider:
    return _DEFAULT_ADAPTER_CLASSES[provider](api_key=api_key)


def _parse_context_date(
    context: dict[str, str | int | float | bool | None] | None, key: str
) -> date | None:
    """Only a date is ever taken from client-sent context; anything missing,
    malformed or of the wrong type is ignored rather than trusted."""
    raw = (context or {}).get(key)
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class Completion(NamedTuple):
    text: str
    provider: AIProviderName
    fallback_used: bool


class AIAssistantService:
    def __init__(
        self, db: AsyncSession, adapter_factory: AdapterFactory = _default_adapter_factory
    ) -> None:
        self.db = db
        self.adapter_factory = adapter_factory

    async def _complete(self, keys: list[AIProviderKey], prompt: str) -> Completion | None:
        """Ask the configured providers in priority order; None if every one fails."""
        for index, key_row in enumerate(keys):
            try:
                adapter = self.adapter_factory(
                    key_row.provider, decrypt_secret(key_row.encrypted_key)
                )
                response = await adapter.complete(prompt)
            except AIProviderError as exc:
                # Never the API key itself: str(exc) is the adapter's own wrapped
                # message (HTTP status, body snippet), which does not include it.
                logger.warning(
                    "AI provider %s failed, trying next: %s", key_row.provider.value, exc
                )
                continue
            except Exception:  # an adapter failure must fall through, never crash the panel
                logger.exception(
                    "AI provider %s raised an unexpected error", key_row.provider.value
                )
                continue
            key_row.last_used_at = datetime.now(UTC)
            return Completion(response.text, key_row.provider, index > 0)
        return None

    async def _recent_turns(self, conversation: AIConversation) -> list[tuple[str, str]]:
        rows = await self.db.execute(
            select(AIConversationMessage.prompt, AIConversationMessage.answer)
            .where(AIConversationMessage.conversation_id == conversation.id)
            .order_by(AIConversationMessage.id.desc())
            .limit(_HISTORY_TURNS)
        )
        return [(prompt, answer) for prompt, answer in reversed(rows.all())]

    async def _answer(
        self,
        user: User,
        payload: AIAskRequest,
        conversation: AIConversation,
        keys: list[AIProviderKey],
    ) -> Completion | None:
        permission_codes = {p.code for p in user.role.permissions}
        tools = available_tools(permission_codes)
        today = await business_today(self.db)
        history = await self._recent_turns(conversation)

        rendered: str | None = None
        if tools:
            viewing_start = _parse_context_date(payload.context, "viewing_start_date")
            viewing_end = _parse_context_date(payload.context, "viewing_end_date")
            viewing = (viewing_start, viewing_end) if viewing_start and viewing_end else None
            plan = await self._complete(
                keys,
                planner_prompt(
                    payload.prompt, catalog_text(tools), today, MAX_CALLS, history, viewing
                ),
            )
            if plan is None:
                return None  # every provider already failed: don't wait out a second round
            calls = parse_plan(plan.text)
            if calls is None:
                # The model did not follow the plan format. Fall back to the one
                # lookup that is always useful rather than answering blind.
                calls = [{"tool": "business_overview", "args": _today_args(today)}]
            ctx = ToolContext(self.db, "reports.view_profit" in permission_codes)
            results = await run_tool_calls(ctx, tools, calls)
            rendered = render_results(results) if results else None

        currency = (await BusinessConfigService(self.db).get()).currency
        return await self._complete(
            keys,
            answer_prompt(payload.prompt, today, currency, user.full_name, rendered, history),
        )

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

        result = await self.db.execute(
            select(AIProviderKey)
            .where(AIProviderKey.is_active.is_(True))
            .order_by(AIProviderKey.priority)
        )
        keys = list(result.scalars().all())

        provider_used: AIProviderName | None = None
        fallback_used = False
        if not keys:
            can_self_serve = "users.manage" in {p.code for p in user.role.permissions}
            answer = _NO_KEYS_MESSAGE_SELF_SERVE if can_self_serve else _NO_KEYS_MESSAGE_ESCALATE
        else:
            completion = await self._answer(user, payload, conversation, keys)
            if completion is None:
                answer, fallback_used = _ALL_FAILED_MESSAGE, True
            else:
                answer, provider_used, fallback_used = completion

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


def _today_args(today: date) -> dict[str, str]:
    return {"start_date": today.isoformat(), "end_date": today.isoformat()}
