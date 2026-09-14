"""make selling price mandatory on every batch, set only at purchase time

Revision ID: 0036_batch_selling_price_required
Revises: 0035_batch_expiry_correction_permission
Create Date: 2026-09-14

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

Pricing model change: a product's `default_selling_price` is no longer
consulted anywhere in the app. Inventory (the Product level) sets no
price at all -- both cost and selling price are set explicitly, per
batch, only at the moment stock is received (manual entry or
Purchasing). This closes a real gap: the old fallback let a batch be
created (or imported via the Purchase Order spreadsheet) with no
selling price of its own, quietly borrowing the product's default at
every sale and report until someone noticed. That silent borrowing is
now impossible -- every batch owns its price outright.

Backfill for existing rows: any batch still relying on the old
fallback (selling_price IS NULL) gets the product's current
default_selling_price written directly onto it -- the same number it
was already effectively selling at, just made explicit instead of
computed at read time. Nothing changes about what any batch sells for
today. A batch whose product went missing entirely (should not happen;
products are never hard-deleted) falls back to 0 rather than leaving a
NOT NULL constraint unsatisfiable.

products.default_selling_price itself is left in place -- unused by
the app from this point on, dropped in a later migration once this is
confirmed stable, per this project's standing deprecate-first rule.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036_batch_selling_price_required"
down_revision: str | None = "0035_batch_expiry_correction_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE medicine_batches
            SET selling_price = (
                SELECT products.default_selling_price
                FROM products
                WHERE products.id = medicine_batches.product_id
            )
            WHERE selling_price IS NULL
            """
        )
    )
    # Defensive backstop only -- see docstring. Should be a no-op in practice.
    conn.execute(
        sa.text("UPDATE medicine_batches SET selling_price = 0 WHERE selling_price IS NULL")
    )

    with op.batch_alter_table("medicine_batches") as batch_op:
        batch_op.alter_column("selling_price", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("medicine_batches") as batch_op:
        batch_op.alter_column("selling_price", existing_type=sa.Integer(), nullable=True)
