from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIProviderName(enum.StrEnum):
    OPENAI = "OPENAI"
    CLAUDE = "CLAUDE"
    GEMINI = "GEMINI"
    DEEPSEEK = "DEEPSEEK"
    NVIDIA = "NVIDIA"


class AIProviderKey(Base):
    """
    A shared business resource, not siloed per user -- any active key
    works for anyone with ai.use, so an Owner setting one up benefits
    the whole team immediately, matching how a small pharmacy actually
    operates (one AI subscription for the business, not one per
    person). `user_id` records who added it, purely for
    accountability -- it is not an access-control boundary. Only
    users.manage (Owner/Administrator) can add or remove keys.
    `encrypted_key` is AES-256-GCM via app.core.security (the same
    primitive already used and tested for JWT/backup secrets), and
    the raw value is never returned by any API response after the
    initial save -- only a masked reference.
    """

    __tablename__ = "ai_provider_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[AIProviderName] = mapped_column(Enum(AIProviderName))
    encrypted_key: Mapped[str] = mapped_column(String(1000))
    # Last 4 characters only, stored in plaintext specifically for
    # display ("sk-...ab12") -- a deliberate, low-risk exception to
    # "never store secrets in plaintext": 4 characters of a long API
    # key carry no meaningful exposure on their own, and without this
    # the user could never visually confirm which key is which after
    # the initial save (the encrypted value is one-way for display
    # purposes, since decrypting it just to show a suffix would mean
    # holding the full plaintext key in memory on every list request).
    key_hint: Mapped[str] = mapped_column(String(4))
    priority: Mapped[int] = mapped_column(Integer, default=1)  # lower = tried first
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
