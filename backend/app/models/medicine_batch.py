from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.money_types import MoneyCents


class MedicineBatch(Base):
    """
    A physical stock lot. Always a NEW row on arrival, even for the
    same product from the same supplier -- expiry date differs between
    deliveries, and FEFO (First-Expiry-First-Out) depends on being able
    to distinguish lots. Never merge quantity into an existing batch
    row; never update qty_remaining directly outside a service function
    that also writes the corresponding StockMovement ledger row in the
    same transaction.
    """

    __tablename__ = "medicine_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)

    batch_number: Mapped[str] = mapped_column(String(80))
    expiry_date: Mapped[date] = mapped_column(Date, index=True)

    qty_received: Mapped[int] = mapped_column(Integer)
    qty_remaining: Mapped[int] = mapped_column(Integer)  # derived/cached, reconciled vs ledger

    cost_price: Mapped[float] = mapped_column(MoneyCents, default=0.0)

    # Non-null while an open stock take is counting this batch -- FEFO
    # selection excludes locked batches so a sale mid-count can't
    # change the quantity out from under the counter. Cleared when the
    # stock take closes.
    locked_by_stock_take_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_takes.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
