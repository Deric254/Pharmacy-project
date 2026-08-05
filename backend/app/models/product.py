from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.money_types import MoneyCents

if TYPE_CHECKING:
    from app.models.category import Category


class Product(Base):
    """
    Catalog identity only -- name, unit, barcode, reorder point. Never
    holds a quantity itself; physical stock always lives in
    MedicineBatch rows. A product can exist with zero batches (not yet
    stocked) or many batches (multiple expiry lots in the shop at once).

    Both name and barcode are unique among ACTIVE products only (see
    __table_args__) -- partial indexes scoped to deleted_at IS NULL,
    not a plain column-level unique. Two real requirements drove this:
    the same drug must never be enterable twice as two "different"
    catalog entries with stock fragmented across them, but a
    deactivated product's name or barcode must remain free for a real
    replacement to reuse later. Name matching is case-insensitive
    (COLLATE NOCASE) -- "Panadol" and "panadol" are the same duplicate
    risk a byte-exact comparison would miss.
    """

    __tablename__ = "products"
    __table_args__ = (
        Index(
            "ix_products_name_active_unique",
            text("name COLLATE NOCASE"),
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_products_barcode_active_unique",
            "barcode",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str] = mapped_column(String(30), default="unit")  # e.g. tablet, bottle, box
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    category: Mapped[Category | None] = relationship(lazy="selectin")

    reorder_point: Mapped[int] = mapped_column(Integer, default=10)
    default_selling_price: Mapped[float] = mapped_column(MoneyCents, default=0.0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # soft delete
