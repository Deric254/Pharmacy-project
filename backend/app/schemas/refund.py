from datetime import datetime

from pydantic import BaseModel, Field

from app.models.refund import RefundReason
from app.models.sale import PaymentMethod
from app.schemas._money import PositiveQuantity


class RefundItemRequest(BaseModel):
    sale_item_id: int
    quantity: PositiveQuantity
    # False for damaged/expired returns -- the customer is still paid
    # back, but the stock is deliberately NOT added back to sellable
    # inventory.
    restock: bool = True


class RefundRequest(BaseModel):
    reason: RefundReason
    method: PaymentMethod
    notes: str | None = Field(default=None, max_length=255)
    items: list[RefundItemRequest] = Field(min_length=1)


class RefundItemOut(BaseModel):
    sale_item_id: int
    product_id: int
    batch_id: int
    quantity: int
    unit_price: float
    line_total: float
    restocked: bool

    model_config = {"from_attributes": True}


class RefundOut(BaseModel):
    id: int
    sale_id: int
    processed_by_user_id: int
    reason: RefundReason
    method: PaymentMethod
    notes: str | None
    total_amount: float
    created_at: datetime
    items: list[RefundItemOut]

    model_config = {"from_attributes": True}
