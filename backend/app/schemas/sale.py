from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.sale import PaymentMethod
from app.schemas._money import Money, PositiveMoney, PositiveQuantity


class SaleItemRequest(BaseModel):
    """
    Deliberately no `unit_price` field. Trusting a client-supplied
    price is exactly the class of bug that lets a tampered request
    check out at a fake price -- the server always looks up the
    current price from the product catalog.
    """

    product_id: int
    quantity: PositiveQuantity


# No real till transaction is anywhere near this many distinct line
# items -- this exists purely so a malformed or malicious request
# can't force the server to loop select_batches_fefo + a DB write per
# line thousands of times inside one transaction, holding this app's
# single SQLite writer for an abnormally long time. Same reasoning as
# the ceilings in _money.py: reject absurd input with a clean 422
# before any DB work starts, rather than let it through and hope.
MAX_SALE_LINE_ITEMS = 500


class PaymentRequest(BaseModel):
    method: PaymentMethod
    amount: PositiveMoney
    reference: str | None = Field(default=None, max_length=100)


class SaleCreate(BaseModel):
    items: list[SaleItemRequest] = Field(min_length=1, max_length=MAX_SALE_LINE_ITEMS)
    payments: list[PaymentRequest] = Field(min_length=1)
    discount_amount: Money = 0.0
    customer_id: int | None = None
    # Optional replay-protection token, one per checkout attempt (see
    # Sale.idempotency_key). A repeat of the same key returns the
    # sale that already exists instead of creating a second one --
    # this is what makes it safe for the frontend to retry a checkout
    # whose response was lost, without risking a duplicate sale.
    idempotency_key: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def items_have_unique_products(self) -> "SaleCreate":
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Each product should appear once per sale; combine quantities instead")
        return self


class SaleQuoteRequest(BaseModel):
    items: list[SaleItemRequest] = Field(min_length=1, max_length=MAX_SALE_LINE_ITEMS)
    discount_amount: Money = 0.0

    @model_validator(mode="after")
    def items_have_unique_products(self) -> "SaleQuoteRequest":
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Each product should appear once per sale; combine quantities instead")
        return self


class SaleQuoteOut(BaseModel):
    subtotal: float
    discount_amount: float
    total_amount: float


class SaleItemOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    batch_id: int
    quantity: int
    unit_price: float
    line_total: float

    model_config = {"from_attributes": True}


class PaymentOut(BaseModel):
    method: PaymentMethod
    amount: float
    reference: str | None

    model_config = {"from_attributes": True}


class SaleOut(BaseModel):
    id: int
    cashier_user_id: int
    customer_id: int | None
    subtotal: float
    discount_amount: float
    total_amount: float
    created_at: datetime
    items: list[SaleItemOut]
    payments: list[PaymentOut]

    model_config = {"from_attributes": True}


class SaleListItemOut(BaseModel):
    id: int
    cashier_name: str
    customer_name: str | None
    item_count: int
    total_amount: float
    created_at: datetime


class SalePage(BaseModel):
    entries: list[SaleListItemOut]
    total: int
    limit: int
    offset: int
