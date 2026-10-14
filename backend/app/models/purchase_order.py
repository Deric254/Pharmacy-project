from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.money_types import MoneyCents

if TYPE_CHECKING:
    from app.models.product import Product


class PurchaseOrderStatus(enum.StrEnum):
    RECEIVED = "RECEIVED"
    # Cancelled after the fact by PurchasingService.reverse_purchase. The row,
    # its items and its history stay; only the stock and the debt are undone.
    REVERSED = "REVERSED"


class PurchaseOrder(Base):
    """
    A purchase receipt. quick_purchase is the only thing that creates one,
    always as RECEIVED, and reverse_purchase is the only thing that ever moves
    it on (to REVERSED, once) -- see purchasing_service.py.
    """

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        Enum(PurchaseOrderStatus), default=PurchaseOrderStatus.RECEIVED
    )
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Both set together with status = REVERSED, never otherwise.
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    items: Mapped[list[PurchaseOrderItem]] = relationship(lazy="selectin")


class PurchaseOrderItem(Base):
    """
    quick_purchase sets quantity_received equal to quantity_ordered and
    fills unit_cost_actual and batch_id at the same moment.
    """

    __tablename__ = "purchase_order_items"
    # Database-level rules, listed in app/core/integrity_rules.py (migration 0039).
    __table_args__ = (
        CheckConstraint("quantity_ordered > 0", name="ck_purchase_order_items_ordered_positive"),
        CheckConstraint("quantity_received >= 0", name="ck_purchase_order_items_received_nonneg"),
        CheckConstraint(
            "unit_cost_expected >= 0 AND unit_cost_actual >= 0",
            name="ck_purchase_order_items_costs_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    quantity_ordered: Mapped[int] = mapped_column(Integer)
    unit_cost_expected: Mapped[float] = mapped_column(MoneyCents)

    quantity_received: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_cost_actual: Mapped[float | None] = mapped_column(MoneyCents, nullable=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("medicine_batches.id"), nullable=True)
    # Only on a line that restocked a batch that already existed: the batch's
    # cost price just before and just after this line blended into it. Lets
    # reverse_purchase put the cost back exactly (a weighted average can't be
    # inverted -- every receipt rounds to a cent) and prove nobody has changed
    # it since. NULL on a line that created its batch.
    batch_cost_before: Mapped[float | None] = mapped_column(MoneyCents, nullable=True)
    batch_cost_after: Mapped[float | None] = mapped_column(MoneyCents, nullable=True)

    product: Mapped[Product] = relationship(lazy="selectin")

    @property
    def product_name(self) -> str:
        # Only safe to access because every query that serializes this
        # into an API response eager-loads .product explicitly (see
        # purchasing_service.py) -- otherwise this would trigger a lazy
        # load outside the async session context and crash.
        return self.product.name

    @property
    def category_name(self) -> str | None:
        # Same eager-load guarantee as product_name above -- .product
        # is selectin-loaded here, and Product.category is itself
        # selectin-loaded (see product.py), so this is never a lazy
        # load outside the session either.
        return self.product.category_name
