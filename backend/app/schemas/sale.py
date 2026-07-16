from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.sale import PaymentMethod


class SaleItemRequest(BaseModel):
    """
    Deliberately no `unit_price` field. Trusting a client-supplied
    price is exactly the class of bug that lets a tampered request
    check out at a fake price -- the server always looks up the
    current price from the product catalog.
    """

    product_id: int
    quantity: int = Field(gt=0)


class PaymentRequest(BaseModel):
    method: PaymentMethod
    amount: float = Field(gt=0)
    reference: str | None = Field(default=None, max_length=100)


class SaleCreate(BaseModel):
    items: list[SaleItemRequest] = Field(min_length=1)
    payments: list[PaymentRequest] = Field(min_length=1)
    discount_amount: float = Field(default=0.0, ge=0)
    customer_id: int | None = None

    @model_validator(mode="after")
    def items_have_unique_products(self) -> "SaleCreate":
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Each product should appear once per sale; combine quantities instead")
        return self


class SaleItemOut(BaseModel):
    id: int
    product_id: int
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
