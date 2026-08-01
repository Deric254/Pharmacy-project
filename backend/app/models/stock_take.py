from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.medicine_batch import MedicineBatch
    from app.models.product import Product


class StockTakeStatus(enum.StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class StockTake(Base):
    """
    A counting session. Starting one snapshots expected_qty per batch
    and LOCKS those batches from sale (see MedicineBatch.locked_by_stock_take_id)
    so a sale mid-count can't corrupt the comparison -- the simpler and
    safer of the two options discussed for a small pharmacy that can't
    run a separate reconciliation pass for concurrent sales.
    """

    __tablename__ = "stock_takes"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[StockTakeStatus] = mapped_column(
        Enum(StockTakeStatus), default=StockTakeStatus.OPEN
    )
    initiated_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)

    items: Mapped[list[StockTakeItem]] = relationship(lazy="selectin")


class StockTakeItem(Base):
    """
    One row per batch included in the count. expected_qty is frozen at
    the moment the stock take starts. physical_qty is null until
    someone counts it. A non-zero variance requires `reason` before it
    can be approved -- approval either happens automatically (variance
    within the self-approve threshold) or requires a manager via a
    separate endpoint (stocktake.approve_variance permission).
    """

    __tablename__ = "stock_take_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_take_id: Mapped[int] = mapped_column(ForeignKey("stock_takes.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("medicine_batches.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    expected_qty: Mapped[int] = mapped_column(Integer)
    physical_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    counted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    counted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    product: Mapped[Product] = relationship(lazy="selectin")
    batch: Mapped[MedicineBatch] = relationship(lazy="selectin")

    @property
    def product_name(self) -> str:
        # Only safe because this relationship is always eager-loaded
        # (lazy="selectin" above) -- otherwise this would trigger a
        # lazy load outside the async session context and crash.
        return self.product.name

    @property
    def batch_number(self) -> str:
        return self.batch.batch_number
