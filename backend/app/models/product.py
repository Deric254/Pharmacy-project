from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.category import Category


class Product(Base):
    """
    Catalog identity only -- name, unit, barcode, reorder point. Never
    holds a quantity itself; physical stock always lives in
    MedicineBatch rows. A product can exist with zero batches (not yet
    stocked) or many batches (multiple expiry lots in the shop at once).
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    unit: Mapped[str] = mapped_column(String(30), default="unit")  # e.g. tablet, bottle, box
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    category: Mapped[Category | None] = relationship(lazy="selectin")

    reorder_point: Mapped[int] = mapped_column(Integer, default=10)
    default_selling_price: Mapped[float] = mapped_column(default=0.0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # soft delete
