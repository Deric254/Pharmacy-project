"""freeze cost-at-close on stock_take_items

Revision ID: 0033_stock_take_item_unit_cost
Revises: 0032_sale_item_unit_cost
Create Date: 2026-08-29

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

Same gap 0032 closed for sale_items, found in a second place: Stock
Take History's shrinkage/expected value was computed by joining live
to medicine_batches.cost_price every time the report ran, so a batch's
cost changing later would silently change what an already-closed
stock take's shrinkage value shows. unit_cost_at_close freezes it at
the moment the stock take actually closes (see
StockTakeItem.unit_cost_at_close's own comment and
StockTakeService.close(), which now sets it), the same way
sale_items.unit_cost freezes cost at the moment of sale.

Backfill for existing rows: the best available historical data for an
already-closed stock take is its own batch's cost_price as it
currently stands. Open/cancelled stock takes have no close event yet,
so their items are left null -- StockTakeService.close() sets this
column correctly for every stock take from this point forward.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033_stock_take_item_unit_cost"
down_revision: str | None = "0032_sale_item_unit_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stock_take_items", sa.Column("unit_cost_at_close", sa.Integer(), nullable=True))

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE stock_take_items
            SET unit_cost_at_close = (
                SELECT medicine_batches.cost_price
                FROM medicine_batches
                WHERE medicine_batches.id = stock_take_items.batch_id
            )
            WHERE stock_take_id IN (
                SELECT id FROM stock_takes WHERE status = 'CLOSED'
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("stock_take_items", "unit_cost_at_close")
