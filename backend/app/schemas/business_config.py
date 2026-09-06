from typing import Literal

from pydantic import BaseModel, Field

from app.schemas._money import Money
from app.schemas._text import NonBlankName
from app.schemas._timezone import ValidTimezone

BUILT_IN_THEMES = ("ledger", "clinical", "midnight", "sunrise")
ThemeName = Literal["ledger", "clinical", "midnight", "sunrise"]


class BusinessConfigOut(BaseModel):
    business_name: str
    slogan: str
    logo_url: str | None
    theme_name: str
    primary_color: str
    secondary_color: str
    receipt_header_text: str
    receipt_footer_text: str
    currency: str
    tax_rate: float
    tax_id: str | None
    contact_phone: str | None
    contact_email: str | None
    address: str | None
    default_language: str
    timezone: str
    low_stock_threshold_default: int
    expiry_alert_days: list[int]
    loyalty_program_enabled: bool
    loyalty_points_per_currency_unit: float
    local_backup_dir_override: str | None

    model_config = {"from_attributes": True}


class BusinessConfigUpdate(BaseModel):
    """
    All fields optional — this is a partial update (PATCH semantics).
    Admin changes one field (e.g. just the logo) without needing to
    resend the entire config every time.
    """

    business_name: NonBlankName | None = Field(default=None, min_length=1, max_length=120)
    slogan: str | None = Field(default=None, max_length=255)
    logo_url: str | None = Field(default=None, max_length=3_000_000)
    theme_name: ThemeName | None = None
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    receipt_header_text: str | None = Field(default=None, max_length=255)
    receipt_footer_text: str | None = Field(default=None, max_length=255)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_rate: float | None = Field(default=None, ge=0, le=100)
    tax_id: str | None = Field(default=None, max_length=50)
    contact_phone: str | None = Field(default=None, max_length=30)
    contact_email: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=255)
    default_language: str | None = Field(default=None, max_length=10)
    timezone: ValidTimezone | None = Field(default=None, max_length=50)
    low_stock_threshold_default: int | None = Field(default=None, ge=0)
    expiry_alert_days: list[int] | None = None
    loyalty_program_enabled: bool | None = None
    loyalty_points_per_currency_unit: Money | None = None
    local_backup_dir_override: str | None = Field(default=None, max_length=500)
