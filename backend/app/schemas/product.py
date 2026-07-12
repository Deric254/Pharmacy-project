from datetime import datetime

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    barcode: str | None = Field(default=None, max_length=64)
    unit: str = Field(default="unit", max_length=30)
    category_id: int | None = None
    reorder_point: int = Field(default=10, ge=0)
    default_selling_price: float = Field(default=0.0, ge=0)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    barcode: str | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, max_length=30)
    category_id: int | None = None
    reorder_point: int | None = Field(default=None, ge=0)
    default_selling_price: float | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ProductOut(BaseModel):
    id: int
    name: str
    barcode: str | None
    unit: str
    category_id: int | None
    reorder_point: int
    default_selling_price: float
    is_active: bool
    created_at: datetime
    total_qty_available: int = 0  # sum across all non-expired batches, populated by service

    model_config = {"from_attributes": True}
