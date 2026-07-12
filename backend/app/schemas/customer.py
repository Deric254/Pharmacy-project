from datetime import datetime

from pydantic import BaseModel, Field


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=120)


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str | None
    email: str | None
    loyalty_points: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PurchaseHistoryEntryOut(BaseModel):
    sale_id: int
    total_amount: float
    created_at: datetime
