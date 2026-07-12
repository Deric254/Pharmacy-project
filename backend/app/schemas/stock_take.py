from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from app.models.stock_take import StockTakeStatus
from app.schemas.inventory import AdjustmentReason


class StockTakeCreate(BaseModel):
    product_ids: list[int] | None = Field(
        default=None, description="Specific products to count; omit for a full stock take"
    )
    notes: str | None = Field(default=None, max_length=255)


class CountSubmit(BaseModel):
    physical_qty: int = Field(ge=0)
    reason: AdjustmentReason | None = None
    notes: str | None = Field(default=None, max_length=255)


class StockTakeItemOut(BaseModel):
    id: int
    batch_id: int
    product_id: int
    expected_qty: int
    physical_qty: int | None
    reason: str | None
    counted_by_user_id: int | None
    counted_at: datetime | None
    approved_by_user_id: int | None
    approved_at: datetime | None

    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def variance(self) -> int | None:
        if self.physical_qty is None:
            return None
        return self.physical_qty - self.expected_qty


class StockTakeOut(BaseModel):
    id: int
    status: StockTakeStatus
    initiated_by_user_id: int
    started_at: datetime
    closed_at: datetime | None
    notes: str | None
    items: list[StockTakeItemOut]

    model_config = {"from_attributes": True}
