from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BackupProviderName(enum.StrEnum):
    LOCAL_FILE = "LOCAL_FILE"
    GOOGLE_DRIVE = "GOOGLE_DRIVE"


class BackupStatus(enum.StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class BackupOAuthToken(Base):
    """
    One row per provider. The refresh token is obtained once, outside
    this app, via the provider's standard OAuth consent flow (an admin
    connects their Google account), then pasted in here and encrypted
    -- this backend never runs an interactive browser consent screen
    itself.
    """

    __tablename__ = "backup_oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[BackupProviderName] = mapped_column(Enum(BackupProviderName), unique=True)
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary(2000))
    connected_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BackupLog(Base):
    """
    One row per backup attempt, success or failure -- never deleted,
    this is the audit trail an admin checks to confirm backups are
    actually running. `manifest` (table name -> row count at backup
    time) is stored unencrypted since row counts aren't sensitive, and
    it's what a restore verifies against before touching anything.
    """

    __tablename__ = "backup_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[BackupStatus] = mapped_column(Enum(BackupStatus))
    provider: Mapped[BackupProviderName] = mapped_column(Enum(BackupProviderName))
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
