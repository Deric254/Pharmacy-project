from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MovementType(enum.StrEnum):
    PURCHASE = "PURCHASE"  # new stock arrival
    SALE = "SALE"  # sold to a customer
    ADJUSTMENT = "ADJUSTMENT"  # stock take variance, damage, write-off
    RETURN = "RETURN"  # customer return back into stock


class StockMovement(Base):
    """
    Append-only. Never UPDATE or DELETE a row here -- only INSERT.
    batch.qty_remaining is a derived/cached value; this table is the
    actual source of truth, and a scheduled reconciliation job (added
    with the Inventory module) periodically confirms they agree.
    """

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("medicine_batches.id"), index=True)
    movement_type: Mapped[MovementType] = mapped_column(Enum(MovementType))
    quantity_delta: Mapped[int] = mapped_column(
        Integer
    )  # positive = stock in, negative = stock out
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. sale id, PO id
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
