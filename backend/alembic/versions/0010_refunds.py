"""add refunds

Revision ID: 0010_refunds
Revises: 0009_backups
Create Date: 2026-07-16

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
Purely additive: two new tables, nothing existing is touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_refunds"
down_revision: str | None = "0009_backups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sale_id", sa.Integer, sa.ForeignKey("sales.id"), nullable=False, index=True),
        sa.Column("processed_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "CUSTOMER_RETURN",
                "DAMAGED",
                "WRONG_ITEM_SOLD",
                "EXPIRED",
                "OTHER",
                name="refundreason",
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("method", sa.Enum("CASH", "MPESA", "CARD", name="paymentmethod"), nullable=False),
        sa.Column("total_amount", sa.Float, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), index=True),
    )

    op.create_table(
        "refund_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("refund_id", sa.Integer, sa.ForeignKey("refunds.id"), nullable=False, index=True),
        sa.Column(
            "sale_item_id",
            sa.Integer,
            sa.ForeignKey("sale_items.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("batch_id", sa.Integer, sa.ForeignKey("medicine_batches.id"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_price", sa.Float, nullable=False),
        sa.Column("line_total", sa.Float, nullable=False),
        sa.Column("restocked", sa.Boolean, nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_table("refund_items")
    op.drop_table("refunds")
