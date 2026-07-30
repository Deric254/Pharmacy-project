from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.product import Product


class Sale(Base):
    """
    The money-and-stock transaction header. subtotal/discount/total are
    stored (not recomputed on read) because a sale is a historical
    financial record -- if the product's price changes next week, this
    sale must still show what was actually charged at the time.
    """

    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    cashier_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    subtotal: Mapped[float] = mapped_column()
    discount_amount: Mapped[float] = mapped_column(default=0.0)
    total_amount: Mapped[float] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    items: Mapped[list[SaleItem]] = relationship(lazy="selectin")
    payments: Mapped[list[Payment]] = relationship(lazy="selectin")


class SaleItem(Base):
    """
    One row per (sale, batch) allocation -- NOT one row per cart line.
    If a customer buys 12 units of a product and FEFO has to draw 5
    from one batch and 7 from the next (nearest-expiry first), that
    single cart line becomes two SaleItem rows. This is deliberate: it
    keeps the batch-level cost/expiry traceability that a merged
    "12 units of Product X" row would lose.
    """

    __tablename__ = "sale_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("medicine_batches.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column()  # price at time of sale, never recomputed later
    line_total: Mapped[float] = mapped_column()

    product: Mapped[Product] = relationship(lazy="selectin")

    @property
    def product_name(self) -> str:
        # Only safe because this relationship is always eager-loaded
        # (lazy="selectin" above) -- otherwise this would trigger a
        # lazy load outside the async session context and crash.
        return self.product.name


class PaymentMethod(enum.StrEnum):
    CASH = "CASH"
    MPESA = "MPESA"
    CARD = "CARD"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    amount: Mapped[float] = mapped_column()
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. M-Pesa code
