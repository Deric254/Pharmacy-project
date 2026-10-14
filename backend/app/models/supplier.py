from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.money_types import MoneyCents


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
    amount: Mapped[float] = mapped_column(MoneyCents)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "po:12"
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Client-generated token sent with a payment so a repeated request
    # (double click, retry) is answered with the original result instead
    # of paying twice. Unique; NULL on every other kind of row.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    # Set only on a reversal row: the payment it cancels. Unique, so a
    # payment can be reversed at most once.
    reverses_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_transactions.id"), nullable=True, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
