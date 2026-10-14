"""purchase restock snapshot: the batch cost before and after each restock line

Revision ID: 0043_purchase_restock_snapshot
Revises: 0042_purchase_reversal
Create Date: 2026-10-13

COMPATIBILITY NOTE: purely additive (two nullable columns). Nothing is dropped
or rewritten. See README.md.

purchase_order_items.batch_cost_before / batch_cost_after
    Set only on a line that restocked a batch that already existed (blended
    into its weighted-average cost): the batch's cost price immediately before
    and immediately after that line. A weighted average cannot be inverted
    exactly (every receipt rounds to a whole cent), so reversing a restock
    puts back the recorded "before" -- and the "after" is what proves nobody
    has changed the cost since. NULL on a line that created its batch, and on
    every row received before this migration.

The statement lives in app.core.purchase_reversal_schema and is idempotent,
because the desktop launcher also applies it when migration 0039 refuses a
shop's existing data and the app has to keep running on the older schema.
"""

from collections.abc import Sequence
from typing import Any

from alembic import op
from app.core import purchase_reversal_schema

revision: str = "0043_purchase_restock_snapshot"
down_revision: str | None = "0042_purchase_reversal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    def run(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        result = conn.exec_driver_sql(sql, params)
        return list(result.fetchall()) if result.returns_rows else []

    purchase_reversal_schema.apply_restock_snapshot(run)


def downgrade() -> None:
    with op.batch_alter_table("purchase_order_items") as batch:
        batch.drop_column("batch_cost_after")
        batch.drop_column("batch_cost_before")
