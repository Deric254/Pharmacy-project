"""atomic over-refund guard: sale_items.qty_refunded

Revision ID: 0034_sale_item_qty_refunded
Revises: 0033_stock_take_item_unit_cost
Create Date: 2026-09-05

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

Closes a real concurrency gap, documented in this codebase's own
tests/test_refund_concurrency.py: RefundService's over-refund check
previously read prior RefundItem rows with a plain SELECT and summed
them in Python, only safe from a genuine double-refund race because
the refund header row's own earlier `db.flush()` happens to acquire
SQLite's single-writer lock first -- an incidental side effect of
statement ordering, not anything the database schema itself enforced.
A future edit that reordered those two steps (a natural-looking
refactor, not an obviously dangerous one) would have silently
reopened the exact race the existing concurrency test was written to
catch.

This migration adds an explicit running-total column so the guard can
be enforced the same proven way stock decrements and restocks already
are elsewhere in this codebase: one atomic
`UPDATE sale_items SET qty_refunded = qty_refunded + :n
 WHERE id = :id AND qty_refunded + :n <= quantity`
checked against the row's real state at the moment it runs, not a
value read earlier in the request.

Backfill for existing rows: qty_refunded starts at the sum of
already-recorded RefundItem quantities per sale_item, so no
previously-processed refund is lost or double-counted the first time
this column is read.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_sale_item_qty_refunded"
down_revision: str | None = "0033_stock_take_item_unit_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sale_items", sa.Column("qty_refunded", sa.Integer(), nullable=False, server_default="0")
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE sale_items
            SET qty_refunded = (
                SELECT COALESCE(SUM(refund_items.quantity), 0)
                FROM refund_items
                WHERE refund_items.sale_item_id = sale_items.id
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("sale_items", "qty_refunded")
