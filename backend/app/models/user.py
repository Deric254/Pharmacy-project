from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.role import Role


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set whenever an admin/owner generates a temp password on someone's
    # behalf (see AuthService.admin_reset_password) -- the temp
    # credential only ever gets someone as far as changing it to a real
    # password they alone know, never into the app itself.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    role: Mapped[Role] = relationship(lazy="selectin")

    # Forgot-password flow: security question answers, hashed the same
    # way as passwords — never stored or compared in plaintext.
    security_question: Mapped[str | None] = mapped_column(String(255), nullable=True)
    security_answer_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # soft delete


class UserSession(Base):
    """
    DB-backed session record. Lets Admin see who's logged in right now
    and force-logout a specific device — pure stateless JWT can't do
    that, so we track issued refresh tokens here and check revocation
    on every refresh.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    refresh_token_jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
