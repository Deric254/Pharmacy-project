"""add business_config table

Revision ID: 0002_business_config
Revises: 0001_initial_auth_rbac
Create Date: 2026-07-03

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_business_config"
down_revision: str | None = "0001_initial_auth_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_name", sa.String(120), nullable=False, server_default="My Pharmacy"),
        sa.Column("slogan", sa.String(255), nullable=False, server_default=""),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column("primary_color", sa.String(7), nullable=False, server_default="#0EA5E9"),
        sa.Column("secondary_color", sa.String(7), nullable=False, server_default="#0F172A"),
        sa.Column("receipt_header_text", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "receipt_footer_text",
            sa.String(255),
            nullable=False,
            server_default="Thank you for your purchase",
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="KES"),
        sa.Column("tax_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("tax_id", sa.String(50), nullable=True),
        sa.Column("contact_phone", sa.String(30), nullable=True),
        sa.Column("contact_email", sa.String(120), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("default_language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="Africa/Nairobi"),
        sa.Column("low_stock_threshold_default", sa.Integer, nullable=False, server_default="10"),
        sa.Column("expiry_alert_days", sa.String(50), nullable=False, server_default="90,60,30"),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # Seed the single config row so `get()` never has to lazily create
    # it on first request in production (keeps first-request latency
    # predictable and avoids a write happening inside what should be a
    # pure read path in normal operation).
    business_config_table = sa.table(
        "business_config",
        sa.column("id", sa.Integer),
    )
    op.bulk_insert(business_config_table, [{"id": 1}])


def downgrade() -> None:
    op.drop_table("business_config")
