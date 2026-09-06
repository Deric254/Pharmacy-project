import enum
from datetime import date

from pydantic import BaseModel, Field, model_validator

from app.schemas._money import QuantityDelta


class LowStockProductOut(BaseModel):
    product_id: int
    name: str
    barcode: str | None
    total_qty_available: int
    reorder_point: int


class ExpiringBatchOut(BaseModel):
    batch_id: int
    product_id: int
    product_name: str
    batch_number: str
    expiry_date: date
    days_remaining: int
    qty_remaining: int


class ProductValuationOut(BaseModel):
    product_id: int
    name: str
    qty_on_hand: int
    value: float


class StockValuationOut(BaseModel):
    total_value: float
    by_product: list[ProductValuationOut]


class AdjustmentReason(enum.StrEnum):
    DAMAGED = "DAMAGED"
    THEFT_OR_LOSS = "THEFT_OR_LOSS"
    MISCOUNT = "MISCOUNT"
    EXPIRED = "EXPIRED"
    DATA_ENTRY_ERROR = "DATA_ENTRY_ERROR"
    OTHER = "OTHER"


class AdjustmentRequest(BaseModel):
    batch_id: int
    quantity_delta: QuantityDelta = Field(
        description="Positive to add stock back, negative to remove"
    )
    reason: AdjustmentReason
    notes: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def quantity_delta_must_be_nonzero(self) -> "AdjustmentRequest":
        if self.quantity_delta == 0:
            raise ValueError("quantity_delta must be non-zero")
        return self


class AdjustmentOut(BaseModel):
    batch_id: int
    quantity_delta: int
    qty_remaining_after: int
    reason: AdjustmentReason


class WriteOffResult(BaseModel):
    batch_id: int
    quantity_written_off: int
    qty_remaining_after: int


class BulkWriteOffResult(BaseModel):
    batches_written_off: int
    total_quantity_written_off: int
    details: list[WriteOffResult]


class ReconciliationIssueOut(BaseModel):
    batch_id: int
    batch_number: str
    product_id: int
    product_name: str
    qty_remaining: int
    ledger_sum: int
    discrepancy: int
