from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AIConversation(Base):
    """
    One saved chat thread, strictly private to the user who created it
    -- unlike AIProviderKey (a shared business resource), a person's
    own back-and-forth with the assistant is personal, so no
    permission grants visibility into someone else's conversations,
    not even users.manage. Every service method that touches a
    conversation must filter by user_id; there is deliberately no
    "admin can see all conversations" escape hatch.

    `title` is derived once from the first prompt in the thread (see
    AIConversationService) and never recomputed afterward, matching
    how a real chat product's thread list behaves -- it's a label for
    finding this conversation again, not a live summary.
    """

    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[AIConversationMessage]] = relationship(
        lazy="selectin", order_by="AIConversationMessage.created_at"
    )


class AIConversationMessage(Base):
    """
    One prompt/answer turn. Deliberately one row per turn (not
    separate "user message" / "assistant message" rows) -- this
    assistant is single-turn Q&A per request (see
    AIAssistantService.ask's own docstring: no conversation memory is
    sent back to the provider), so a turn is genuinely the atomic
    unit here, same reasoning as SaleItem being one row per batch
    allocation rather than per cart line.
    """

    __tablename__ = "ai_conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_conversations.id"), index=True
    )
    prompt: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    provider_used: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
