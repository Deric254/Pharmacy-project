"""add stock takes and batch locking

Revision ID: 0005_stock_takes
Revises: 0004_sales
Create Date: 2026-07-04

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
The locked_by_stock_take_id column on medicine_batches is additive and
nullable -- existing rows are unaffected and default to unlocked.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_stock_takes"
down_revision: str | None = "0004_sales"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_takes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "status",
            sa.Enum("OPEN", "CLOSED", name="stocktakestatus"),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("initiated_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime, nullable=True),
        sa.Column("notes", sa.String(255), nullable=True),
    )

    op.create_table(
        "stock_take_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stock_take_id", sa.Integer, sa.ForeignKey("stock_takes.id"), nullable=False, index=True
        ),
        sa.Column("batch_id", sa.Integer, sa.ForeignKey("medicine_batches.id"), nullable=False),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("expected_qty", sa.Integer, nullable=False),
        sa.Column("physical_qty", sa.Integer, nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("counted_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("counted_at", sa.DateTime, nullable=True),
        sa.Column("approved_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime, nullable=True),
    )

    # Additive: existing batches are simply unlocked (NULL).
    op.add_column(
        "medicine_batches",
        sa.Column(
            "locked_by_stock_take_id",
            sa.Integer,
            sa.ForeignKey("stock_takes.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("medicine_batches", "locked_by_stock_take_id")
    op.drop_table("stock_take_items")
    op.drop_table("stock_takes")
