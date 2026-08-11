"""add CANCELLED status to stock_takes

Revision ID: 0024_stock_take_cancel
Revises: 0023_local_backup_dir_override
Create Date: 2026-08-01

The real bug this closes: a stock take, once initiated, locks its
batches out of sale (correctly -- you don't want someone selling stock
mid-count). But there was no way to release that lock except finishing
the entire count and closing it. Abandon a stock take partway through
-- get distracted, decide not to finish, anything -- and those batches
stayed locked forever: still shown as "in stock" everywhere, but every
real sale attempt against them would fail. This adds a real cancel
path that releases the lock without requiring a completed count.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_stock_take_cancel"
down_revision: str | None = "0023_local_backup_dir_override"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("stock_takes") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum("OPEN", "CLOSED", name="stocktakestatus"),
            type_=sa.Enum("OPEN", "CLOSED", "CANCELLED", name="stocktakestatus"),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Any stock take actually left in CANCELLED at this point cannot
    # be represented in the old two-value enum -- reset it to CLOSED
    # (locks already released, nothing further to count) rather than
    # leave a row with a status the old constraint would reject.
    op.execute("UPDATE stock_takes SET status = 'CLOSED' WHERE status = 'CANCELLED'")
    with op.batch_alter_table("stock_takes") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum("OPEN", "CLOSED", "CANCELLED", name="stocktakestatus"),
            type_=sa.Enum("OPEN", "CLOSED", name="stocktakestatus"),
            existing_nullable=False,
        )
