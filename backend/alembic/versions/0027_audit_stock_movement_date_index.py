"""add indexes on audit_logs.created_at and stock_movements.created_at

Revision ID: 0027_audit_stock_movement_date_index
Revises: 0026_money_as_cents
Create Date: 2026-08-05

Both tables are permanent, append-only history -- an audit trail and a
stock ledger, by design never pruned or deleted from -- which means
both grow for as long as this pharmacy uses the app, with no ceiling.

audit_logs.created_at was unindexed while the audit trail endpoint
(GET /audit-logs) already supports filtering by start_date/end_date --
a real, currently-exercised query path, not a hypothetical one. On a
fresh database that's instant regardless; after years of daily use it
degrades to a full scan of the entire audit history on every
date-filtered lookup, getting slower every year, forever.

stock_movements.created_at was also unindexed. Nothing currently
queries it by date range, so this one was dormant rather than actively
slow -- added preemptively so a future report that needs to reconstruct
movement history for a date range doesn't inherit the same problem
audit_logs already had.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027_audit_stock_movement_date_index"
down_revision: str | None = "0026_money_as_cents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_stock_movements_created_at", "stock_movements", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_stock_movements_created_at", table_name="stock_movements")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
