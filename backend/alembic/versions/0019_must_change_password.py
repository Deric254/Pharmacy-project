"""add users.must_change_password

Revision ID: 0019_must_change_password
Revises: 0018_local_backup_provider
Create Date: 2026-07-23

Backs the hierarchical admin-assisted password reset: when an
owner/admin resets someone's password, they generate a one-time temp
credential, never choose or learn that person's real password. This
flag is what forces a real password to be set before the temp one is
usable for anything else.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_must_change_password"
down_revision: str | None = "0018_local_backup_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("must_change_password")
