"""add business_config.local_backup_dir_override

Revision ID: 0023_local_backup_dir_override
Revises: 0022_terms_accepted
Create Date: 2026-08-01

Without this, the "local file" backup provider could only ever write
next to the database file, on the same machine -- meaning if that
machine fails, is stolen, or burns down, the backup fails with it,
defeating the entire point of disaster recovery. This lets an owner
point it at a USB drive, an external disk, or a network share instead.
Null keeps the existing same-machine default for anyone who hasn't
configured anything yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_local_backup_dir_override"
down_revision: str | None = "0022_terms_accepted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("business_config") as batch_op:
        batch_op.add_column(sa.Column("local_backup_dir_override", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("business_config") as batch_op:
        batch_op.drop_column("local_backup_dir_override")
