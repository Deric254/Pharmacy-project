from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas._money import PositiveMoney


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    contact_phone: str | None = Field(default=None, max_length=30)
    contact_email: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=255)


class SupplierOut(BaseModel):
    id: int
    name: str
    contact_phone: str | None
    contact_email: str | None
    address: str | None
    notes: str | None
    created_at: datetime
    balance_owed: float = 0.0

    model_config = {"from_attributes": True}


class PaymentRecordRequest(BaseModel):
    amount: PositiveMoney
    notes: str | None = Field(default=None, max_length=255)
