from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas._money import PositiveMoney
from app.schemas._text import NonBlankName, OptionalStrippedText


class SupplierCreate(BaseModel):
    name: NonBlankName = Field(min_length=1, max_length=150)
    contact_phone: OptionalStrippedText = Field(default=None, max_length=30)
    contact_email: OptionalStrippedText = Field(default=None, max_length=120)
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


class SupplierKpiOut(BaseModel):
    start_date: date
    end_date: date
    # Charged (goods received) and paid within [start_date, end_date] --
    # flows, not a balance.
    total_purchased: float
    total_paid: float
    # The running balance across every supplier, as of the end of the
    # selected period -- a balance is a point-in-time fact, not
    # something scoped to a range, so this is "what's owed as of
    # end_date" rather than "movement of debt during the period".
    net_due: float
    # Suppliers with at least one transaction in the period -- lets a
    # KPI card read "3 suppliers" rather than just a currency figure.
    active_supplier_count: int
