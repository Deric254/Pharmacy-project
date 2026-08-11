"""add setup_lock table

Revision ID: 0014_setup_lock
Revises: 0013_dynamic_roles
Create Date: 2026-07-21

Backs the atomic first-user-creation guard used by POST
/api/v1/setup/first-user -- the one unauthenticated write endpoint in
the whole app, needed because the desktop app's first-run flow now
happens in the web UI (no console to prompt in under Electron) rather
than an interactive CLI script. A single row with id=1; a primary-key
conflict on inserting it is atomic on every backend, including SQLite,
which is what actually stops two near-simultaneous setup requests from
both succeeding -- confirmed necessary by reproducing that race for
real, not just reasoning about it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_setup_lock"
down_revision: str | None = "0013_dynamic_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "setup_lock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("setup_lock")
