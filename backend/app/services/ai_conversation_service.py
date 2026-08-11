"""
AI conversation history service.

Every method here is scoped to a single user's own conversations --
there is no method that can return or touch another user's
conversation, not even for someone with users.manage. See
AIConversation's model docstring for why: unlike AI provider keys (a
shared business resource), a person's own chat history is private.

Ownership failures return None/False rather than raising -- the API
route layer turns that into a 404 (never a 403), so a user asking
about someone else's conversation id learns nothing about whether it
exists, consistent with how the rest of this codebase avoids leaking
existence of resources outside a caller's own scope.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.user import User
from app.schemas.ai import AIConversationDetailOut, AIConversationOut

_TITLE_MAX_LEN = 60


class ConversationNotFound(Exception):
    """
    Raised when a conversation_id doesn't exist or doesn't belong to
    the requesting user. Deliberately a plain exception, not an
    HTTPException -- this service layer stays HTTP-agnostic (see the
    rest of this codebase's convention); the API route is what
    translates this into a 404.
    """


def _derive_title(first_prompt: str) -> str:
    stripped = " ".join(first_prompt.split())  # collapse whitespace/newlines
    if len(stripped) <= _TITLE_MAX_LEN:
        return stripped or "New conversation"
    return stripped[:_TITLE_MAX_LEN].rstrip() + "…"


class AIConversationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_conversations(self, user: User) -> list[AIConversationOut]:
        result = await self.db.execute(
            select(AIConversation)
            .where(AIConversation.user_id == user.id)
            .order_by(AIConversation.updated_at.desc())
        )
        return [AIConversationOut.model_validate(c) for c in result.scalars().all()]

    async def get_conversation_detail(
        self, user: User, conversation_id: int
    ) -> AIConversationDetailOut | None:
        conversation = await self.get_owned_conversation(user, conversation_id)
        if conversation is None:
            return None
        return AIConversationDetailOut.model_validate(conversation)

    async def get_owned_conversation(
        self, user: User, conversation_id: int
    ) -> AIConversation | None:
        """
        Returns the raw ORM row, not a schema -- for internal callers
        (ask(), delete_conversation()) that need to mutate it
        (updated_at, appending messages) or pass its id along. API
        routes that hand a conversation back to the client should use
        list_conversations/get_conversation_detail instead, which
        convert to the response schemas explicitly.
        """
        result = await self.db.execute(
            select(AIConversation).where(
                AIConversation.id == conversation_id,
                AIConversation.user_id == user.id,
            )
        )
        return result.scalar_one_or_none()

    async def create_conversation(self, user: User, first_prompt: str) -> AIConversation:
        conversation = AIConversation(user_id=user.id, title=_derive_title(first_prompt))
        self.db.add(conversation)
        await self.db.flush()  # assigns conversation.id without ending the transaction
        return conversation

    async def delete_conversation(self, user: User, conversation_id: int) -> bool:
        conversation = await self.get_owned_conversation(user, conversation_id)
        if conversation is None:
            return False
        # No DB-level cascade anywhere in this codebase (see the FK
        # audit referenced elsewhere) -- children deleted explicitly,
        # same convention as every other delete path here.
        await self.db.execute(
            delete(AIConversationMessage).where(
                AIConversationMessage.conversation_id == conversation.id
            )
        )
        await self.db.delete(conversation)
        await self.db.commit()
        return True
