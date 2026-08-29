"""freeze cost-at-sale on sale_items

Revision ID: 0032_sale_item_unit_cost
Revises: 0031_batch_cost_correction_permission
Create Date: 2026-08-28

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

sale_items.unit_price has always been frozen at time of sale (see that
column's own comment) -- but cost had no equivalent. Every profit/COGS
report was computed by joining live to medicine_batches.cost_price,
meaning a batch's cost changing for ANY reason after a sale would
silently change what an already-closed period's profit report shows
the next time someone re-runs it. This migration closes that gap the
same way 0026 closed it for float-vs-cents: cost, like price, is now
captured once at the moment of sale and never recomputed.

Backfill for existing rows: the best available historical data is
each row's own batch's cost_price as it currently stands -- there is
no better source, since cost was never previously recorded per-sale.
Going forward, every new sale_items row gets its own true
point-in-time cost from SaleService itself (see sale_service.py),
making this backfill a one-time approximation for pre-migration data
only, never a mechanism relied on again after this migration runs.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032_sale_item_unit_cost"
down_revision: str | None = "0031_batch_cost_correction_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sale_items", sa.Column("unit_cost", sa.Integer(), nullable=True))

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE sale_items
            SET unit_cost = (
                SELECT medicine_batches.cost_price
                FROM medicine_batches
                WHERE medicine_batches.id = sale_items.batch_id
            )
            """
        )
    )
    # A sale_item whose batch was since deleted (shouldn't happen --
    # batches are never hard-deleted anywhere in this app -- but never
    # trust that from inside a migration) falls back to 0 rather than
    # leaving unit_cost NULL, so the NOT NULL below can never fail.
    conn.execute(sa.text("UPDATE sale_items SET unit_cost = 0 WHERE unit_cost IS NULL"))

    with op.batch_alter_table("sale_items") as batch_op:
        batch_op.alter_column("unit_cost", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.drop_column("sale_items", "unit_cost")
