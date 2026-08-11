"""add sales, sale_items, payments

Revision ID: 0004_sales
Revises: 0003_products_batches
Create Date: 2026-07-03

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_sales"
down_revision: str | None = "0003_products_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "cashier_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True
        ),
        sa.Column("subtotal", sa.Float, nullable=False),
        sa.Column("discount_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Float, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), index=True),
    )

    op.create_table(
        "sale_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sale_id", sa.Integer, sa.ForeignKey("sales.id"), nullable=False, index=True),
        sa.Column(
            "product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False, index=True
        ),
        sa.Column("batch_id", sa.Integer, sa.ForeignKey("medicine_batches.id"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_price", sa.Float, nullable=False),
        sa.Column("line_total", sa.Float, nullable=False),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sale_id", sa.Integer, sa.ForeignKey("sales.id"), nullable=False, index=True),
        sa.Column("method", sa.Enum("CASH", "MPESA", "CARD", name="paymentmethod"), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("reference", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("sale_items")
    op.drop_table("sales")
