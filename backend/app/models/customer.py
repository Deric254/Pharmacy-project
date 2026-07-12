from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Customer(Base):
    """
    loyalty_points is a plain mutable counter, not an append-only
    ledger like stock/money -- deliberately lower-stakes than
    inventory or payments, so a simple running total is an acceptable
    scope choice here rather than a full transaction history.
    """

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    loyalty_points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
