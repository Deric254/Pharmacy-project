"""add selling price to inventory batches

Revision ID: 0029_batch_selling_price
Revises: 0028_sales_idempotency_key
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_batch_selling_price"
down_revision: str | None = "0028_sales_idempotency_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "medicine_batches",
        sa.Column("selling_price", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE medicine_batches
        SET selling_price = (
            SELECT default_selling_price
            FROM products
            WHERE products.id = medicine_batches.product_id
        )
        WHERE selling_price IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("medicine_batches", "selling_price")
