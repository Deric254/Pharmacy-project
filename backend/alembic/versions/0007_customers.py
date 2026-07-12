"""add customers and loyalty program fields

Revision ID: 0007_customers
Revises: 0006_purchasing
Create Date: 2026-07-04

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
All changes here are additive: customer_id on sales is nullable
(existing sales become "no customer attached"), and the two new
business_config columns have server defaults so existing config rows
are unaffected (loyalty program defaults to disabled).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_customers"
down_revision: str | None = "0006_purchasing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("phone", sa.String(30), unique=True, nullable=True, index=True),
        sa.Column("email", sa.String(120), nullable=True),
        sa.Column("loyalty_points", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.add_column(
        "sales",
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id"), nullable=True),
    )

    op.add_column(
        "business_config",
        sa.Column("loyalty_program_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "business_config",
        sa.Column("loyalty_points_per_currency_unit", sa.Float, nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("business_config", "loyalty_points_per_currency_unit")
    op.drop_column("business_config", "loyalty_program_enabled")
    op.drop_column("sales", "customer_id")
    op.drop_table("customers")
