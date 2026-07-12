from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BusinessConfig(Base):
    """
    Single-row table (id is always 1) holding every business-facing
    setting: branding, currency, thresholds. Every screen in the
    system reads from here (via cache) instead of hardcoding a name,
    color, or slogan anywhere. Multi-branch support later would key
    this by business_id instead of hardcoding a single row — the
    service layer is written so that change doesn't ripple outward.
    """

    __tablename__ = "business_config"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    business_name: Mapped[str] = mapped_column(String(120), default="My Pharmacy")
    slogan: Mapped[str] = mapped_column(String(255), default="")
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    primary_color: Mapped[str] = mapped_column(String(7), default="#0EA5E9")  # hex
    secondary_color: Mapped[str] = mapped_column(String(7), default="#0F172A")

    receipt_header_text: Mapped[str] = mapped_column(String(255), default="")
    receipt_footer_text: Mapped[str] = mapped_column(
        String(255), default="Thank you for your purchase"
    )

    currency: Mapped[str] = mapped_column(String(3), default="KES")
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)
    tax_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. KRA PIN

    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)

    default_language: Mapped[str] = mapped_column(String(10), default="en")
    timezone: Mapped[str] = mapped_column(String(50), default="Africa/Nairobi")

    low_stock_threshold_default: Mapped[int] = mapped_column(Integer, default=10)
    # Comma-separated days, e.g. "90,60,30" -- kept simple/portable rather
    # than a JSON column type that behaves differently across DB engines.
    expiry_alert_days: Mapped[str] = mapped_column(String(50), default="90,60,30")

    loyalty_program_enabled: Mapped[bool] = mapped_column(default=False)
    loyalty_points_per_currency_unit: Mapped[float] = mapped_column(default=1.0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
