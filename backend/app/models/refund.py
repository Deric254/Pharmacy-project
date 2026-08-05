from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.money_types import MoneyCents
from app.models.sale import PaymentMethod


class RefundReason(enum.StrEnum):
    CUSTOMER_RETURN = "CUSTOMER_RETURN"
    DAMAGED = "DAMAGED"
    WRONG_ITEM_SOLD = "WRONG_ITEM_SOLD"
    EXPIRED = "EXPIRED"
    OTHER = "OTHER"


class Refund(Base):
    """
    A refund against a specific sale. Always references the original
    sale rather than re-deriving anything from the current product
    catalog: `unit_price`/`line_total` on each RefundItem are copied
    from the SaleItem being refunded, exactly like Sale/SaleItem never
    recompute historical prices from current ones.
    """

    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    processed_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[RefundReason] = mapped_column(Enum(RefundReason))
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    total_amount: Mapped[float] = mapped_column(MoneyCents)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    items: Mapped[list[RefundItem]] = relationship(lazy="selectin")


class RefundItem(Base):
    __tablename__ = "refund_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey("refunds.id"), index=True)
    sale_item_id: Mapped[int] = mapped_column(ForeignKey("sale_items.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("medicine_batches.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(MoneyCents)
    line_total: Mapped[float] = mapped_column(MoneyCents)
    # False when the returned item was NOT put back into sellable
    # stock (damaged/expired) -- the refund still pays the customer
    # back, but the batch's qty_remaining is deliberately left alone.
    restocked: Mapped[bool] = mapped_column(Boolean, default=True)
