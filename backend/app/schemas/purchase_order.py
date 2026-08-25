from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.purchase_order import PurchaseOrderStatus
from app.schemas._money import Money, PositiveQuantity


class PurchaseOrderItemOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity_ordered: int
    unit_cost_expected: float
    quantity_received: int | None
    unit_cost_actual: float | None
    batch_id: int | None

    model_config = {"from_attributes": True}


class PurchaseOrderOut(BaseModel):
    id: int
    supplier_id: int
    status: PurchaseOrderStatus
    created_by_user_id: int
    notes: str | None
    created_at: datetime
    sent_at: datetime | None
    in_transit_at: datetime | None
    received_at: datetime | None
    reconciled_at: datetime | None
    items: list[PurchaseOrderItemOut]

    model_config = {"from_attributes": True}


class QuickPurchaseLine(BaseModel):
    """
    One line of stock that's already physically here -- no separate
    "ordered" vs "received" distinction, since there was no advance
    order. What you type in is what you got.
    """

    product_id: int
    quantity: PositiveQuantity
    batch_number: str = Field(min_length=1, max_length=80)
    expiry_date: date
    unit_cost: Money
    selling_price: Money | None = None


class QuickPurchaseRequest(BaseModel):
    supplier_id: int
    lines: list[QuickPurchaseLine] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=255)
