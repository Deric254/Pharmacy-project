from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SupplierTransaction(Base):
    """
    Append-only, same principle as StockMovement: balance owed is
    derived by summing this table, never stored as a single mutable
    field. Positive amount = increases what's owed (goods received);
    negative = decreases it (a payment made).
    """

    __tablename__ = "supplier_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    amount: Mapped[float] = mapped_column()
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "po:12"
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
