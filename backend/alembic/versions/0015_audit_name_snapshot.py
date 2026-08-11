"""add audit_logs.user_name_snapshot

Revision ID: 0015_audit_name_snapshot
Revises: 0014_setup_lock
Create Date: 2026-07-22

A bare user_id foreign key on an audit log means a future 'rename
user' feature (or any live join used when displaying audit history)
would silently show whoever that account is called TODAY next to a
past action, not who it actually was at the time. Snapshotting the
acting user's name at write time is what keeps historical audit text
honest regardless of what happens to that account afterward.

Nullable, and existing rows are left as NULL rather than backfilled --
there is no reliable source for "what was this user's name at that
exact past moment" for already-written rows; a live-join backfill
would just reproduce the exact bug this migration exists to prevent.
New rows are the only ones this can be correct for, going forward.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_audit_name_snapshot"
down_revision: str | None = "0014_setup_lock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("user_name_snapshot", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_column("user_name_snapshot")
