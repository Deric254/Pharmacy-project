from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.purchase_order import PurchaseOrderStatus
from app.schemas._money import Money, PositiveMoney, PositiveQuantity, Quantity


class PurchaseOrderItemCreate(BaseModel):
    product_id: int
    quantity_ordered: PositiveQuantity
    unit_cost_expected: Money


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    items: list[PurchaseOrderItemCreate] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=255)


class ReceivingLine(BaseModel):
    """
    One line of what actually arrived. quantity_received/unit_cost_actual
    may differ from what was ordered -- that's the receiving variance,
    detected and flagged, never silently corrected.
    """

    item_id: int
    batch_number: str = Field(min_length=1, max_length=80)
    expiry_date: date
    quantity_received: Quantity
    unit_cost_actual: Money


class ReceiveRequest(BaseModel):
    lines: list[ReceivingLine] = Field(min_length=1)


class ReceivingVarianceOut(BaseModel):
    item_id: int
    product_id: int
    quantity_ordered: int
    quantity_received: int
    variance: int


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


class ReconcileRequest(BaseModel):
    payment_amount: PositiveMoney | None = None
    notes: str | None = Field(default=None, max_length=255)


class ReceiveResponse(BaseModel):
    purchase_order: PurchaseOrderOut
    variances: list[ReceivingVarianceOut]


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


class QuickPurchaseRequest(BaseModel):
    supplier_id: int
    lines: list[QuickPurchaseLine] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=255)
