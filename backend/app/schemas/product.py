from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas._money import Money, Quantity
from app.schemas._text import NonBlankName


class ProductCreate(BaseModel):
    name: NonBlankName = Field(min_length=1, max_length=150)
    barcode: str | None = Field(default=None, max_length=64)
    unit: str = Field(default="unit", max_length=30)
    category_id: int | None = None
    reorder_point: Quantity = 10
    default_selling_price: Money = 0.0


class ProductUpdate(BaseModel):
    name: NonBlankName | None = Field(default=None, min_length=1, max_length=150)
    barcode: str | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, max_length=30)
    category_id: int | None = None
    reorder_point: Quantity | None = None
    default_selling_price: Money | None = None
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

    # All four populated only when there's real stock to compute a
    # true cost from -- None rather than a fabricated number when a
    # product has no batches at all yet.
    current_cost: float | None = None  # cost of whichever batch would sell next (FEFO)
    current_selling_price: float | None = None  # price of whichever batch would sell next
    margin_amount: float | None = None  # selling price minus cost, in currency
    margin_percent: float | None = None  # profit as a % of selling price
    markup_percent: float | None = None  # profit as a % of cost

    model_config = {"from_attributes": True}


class ImportRowError(BaseModel):
    row: int  # 1-indexed spreadsheet row number, matching what the user sees in Excel
    field: str
    message: str


class BulkImportResult(BaseModel):
    created: int
