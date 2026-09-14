from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas._money import Money, PositiveQuantity


class BatchCreate(BaseModel):
    batch_number: str = Field(min_length=1, max_length=80)
    expiry_date: date
    qty_received: PositiveQuantity
    cost_price: Money
    # Required, same discipline as cost_price -- no product-level
    # fallback exists to borrow from any more (see migration 0036).
    selling_price: Money

    @model_validator(mode="after")
    def selling_price_must_not_be_below_cost(self) -> "BatchCreate":
        # A batch sold below what it cost to bring in loses money on
        # every single unit sold, guaranteed, before any other cost
        # (rent, staff, anything) is even counted -- almost always a
        # typo (swapped fields, a decimal in the wrong place) rather
        # than a deliberate loss-leader, and the one place to catch it
        # is here, before it's ever possible to sell at this price.
        if self.selling_price < self.cost_price:
            raise ValueError(
                f"Selling price ({self.selling_price}) is below cost price "
                f"({self.cost_price}) -- this batch would lose money on every unit sold."
            )
        return self


class BatchUpdate(BaseModel):
    selling_price: Money


class BatchCostCorrection(BaseModel):
    cost_price: Money
    reason: str = Field(min_length=1, max_length=255)


class BatchExpiryCorrection(BaseModel):
    new_expiry_date: date
    reason: str = Field(min_length=1, max_length=255)


class BatchOut(BaseModel):
    id: int
    product_id: int
    batch_number: str
    expiry_date: date
    qty_received: int
    qty_remaining: int
    cost_price: float
    selling_price: float
    created_at: datetime

    model_config = {"from_attributes": True}
