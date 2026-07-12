"""add suppliers and purchase orders

Revision ID: 0006_purchasing
Revises: 0005_stock_takes
Create Date: 2026-07-04

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_purchasing"
down_revision: str | None = "0005_stock_takes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(150), nullable=False, index=True),
        sa.Column("contact_phone", sa.String(30), nullable=True),
        sa.Column("contact_email", sa.String(120), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "supplier_transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False, index=True
        ),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False, index=True
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT", "SENT", "IN_TRANSIT", "RECEIVED", "RECONCILED", name="purchaseorderstatus"
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime, nullable=True),
        sa.Column("in_transit_at", sa.DateTime, nullable=True),
        sa.Column("received_at", sa.DateTime, nullable=True),
        sa.Column("reconciled_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "purchase_order_id",
            sa.Integer,
            sa.ForeignKey("purchase_orders.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity_ordered", sa.Integer, nullable=False),
        sa.Column("unit_cost_expected", sa.Float, nullable=False),
        sa.Column("quantity_received", sa.Integer, nullable=True),
        sa.Column("unit_cost_actual", sa.Float, nullable=True),
        sa.Column("batch_id", sa.Integer, sa.ForeignKey("medicine_batches.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_table("supplier_transactions")
    op.drop_table("suppliers")
