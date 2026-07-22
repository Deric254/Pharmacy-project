from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    """
    Append-only. The application's DB role must NOT have UPDATE/DELETE
    grants on this table (enforced at the DB user level, not just in
    code) -- so even a compromised app account can't cover its tracks.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Snapshotted at write time, not resolved via a live join to the
    # current users table on read. A user account can later be
    # deactivated, or (if that feature is ever added) renamed -- this
    # column is what keeps a historical audit entry showing who
    # *actually* did it at the time, rather than silently rewriting
    # itself to whatever that account is called today.
    user_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    action: Mapped[str] = mapped_column(String(100))  # e.g. "price.changed", "user.password_reset"
    entity_type: Mapped[str] = mapped_column(String(50))  # e.g. "product", "sale"
    entity_id: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
