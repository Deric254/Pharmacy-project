from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas._money import Money, PositiveQuantity


class BatchCreate(BaseModel):
    batch_number: str = Field(min_length=1, max_length=80)
    expiry_date: date
    qty_received: PositiveQuantity
    cost_price: Money


class BatchOut(BaseModel):
    id: int
    product_id: int
    batch_number: str
    expiry_date: date
    qty_received: int
    qty_remaining: int
    cost_price: float
    created_at: datetime

    model_config = {"from_attributes": True}
