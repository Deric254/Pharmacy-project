from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PurchaseOrderStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    IN_TRANSIT = "IN_TRANSIT"
    RECEIVED = "RECEIVED"
    RECONCILED = "RECONCILED"


class PurchaseOrder(Base):
    """
    A real state machine, not a free-text status field -- legal
    transitions are enforced in PurchasingService, not left to whatever
    a client happens to send. `version` supports optimistic locking so
    two people can't push conflicting transitions through at once.
    """

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        Enum(PurchaseOrderStatus), default=PurchaseOrderStatus.DRAFT
    )
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    in_transit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list[PurchaseOrderItem]] = relationship(lazy="selectin")


class PurchaseOrderItem(Base):
    """
    quantity_received/unit_cost_actual/batch_id are null until the
    Received transition -- that's the receiving step, where actual
    quantity and cost may differ from what was ordered (a receiving
    variance), and a new MedicineBatch row is created for it.
    """

    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    quantity_ordered: Mapped[int] = mapped_column(Integer)
    unit_cost_expected: Mapped[float] = mapped_column()

    quantity_received: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_cost_actual: Mapped[float | None] = mapped_column(nullable=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("medicine_batches.id"), nullable=True)
