"""add idempotency_key to sales

Revision ID: 0028_sales_idempotency_key
Revises: 0027_audit_stock_movement_date_index
Create Date: 2026-08-08

The checkout button already guards against a fast double-click
(synchronous ref + disabled state), but that only ever protected
against the cashier's own two clicks landing in the same tick. It does
nothing for the case that actually matters on a real desktop POS: the
request reaches the backend, the sale genuinely commits -- stock
decremented, payment recorded -- but the HTTP response never makes it
back (a dropped connection, the Electron-managed local backend
restarting mid-request, a flaky machine). The frontend then shows
"Checkout failed. Nothing was charged." and re-enables the button --
which is false. The sale happened. If the cashier retries, believing
the first attempt failed, a second, fully real sale is created:
stock decremented twice, till reconciliation off by a real transaction.

idempotency_key lets the client attach one identifier per checkout
attempt (persisted across retries of that same attempt, not reused for
a genuinely new sale). The server treats a repeat of the same key as
"return what already happened," not "do it again." UNIQUE enforces
this even under real concurrency -- two requests racing with the same
key hit a UNIQUE constraint violation on the second INSERT, at which
point the service re-fetches and returns the first sale instead of
erroring the cashier.

Nullable, not required: existing sales and any programmatic caller
that doesn't send one still work exactly as before, just without the
replay protection.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_sales_idempotency_key"
down_revision: str | None = "0027_audit_stock_movement_date_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_sales_idempotency_key", "sales", ["idempotency_key"], unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_sales_idempotency_key", table_name="sales")
    op.drop_column("sales", "idempotency_key")
