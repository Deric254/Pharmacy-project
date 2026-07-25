"""add LOCAL_FILE backup provider

Revision ID: 0018_local_backup_provider
Revises: 0017_logo_url_text
Create Date: 2026-07-23

The confirmed bug this closes: every backup attempt required Google
Drive to be connected first, with no offline path at all -- a direct
contradiction of this app's whole design (one computer, no network
dependency). LOCAL_FILE becomes the default provider (writes to a
backups/ folder next to the actual database file, no connection
required); Google Drive stays available as an optional additional
layer for anyone who wants an off-site copy too.

SQLite enforces sa.Enum as a per-column CHECK constraint at table
creation time (there's no real shared named type the way there is on
Postgres), so adding a new enum member means recreating each column
via batch mode, same pattern as every other SQLite column change in
this project.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_local_backup_provider"
down_revision: str | None = "0017_logo_url_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_ENUM = sa.Enum("LOCAL_FILE", "GOOGLE_DRIVE", name="backupprovidername")
_OLD_ENUM = sa.Enum("GOOGLE_DRIVE", name="backupprovidername")


def upgrade() -> None:
    with op.batch_alter_table("backup_oauth_tokens") as batch_op:
        batch_op.alter_column(
            "provider", existing_type=_OLD_ENUM, type_=_NEW_ENUM, existing_nullable=False
        )
    with op.batch_alter_table("backup_logs") as batch_op:
        batch_op.alter_column(
            "provider", existing_type=_OLD_ENUM, type_=_NEW_ENUM, existing_nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("backup_logs") as batch_op:
        batch_op.alter_column(
            "provider", existing_type=_NEW_ENUM, type_=_OLD_ENUM, existing_nullable=False
        )
    with op.batch_alter_table("backup_oauth_tokens") as batch_op:
        batch_op.alter_column(
            "provider", existing_type=_NEW_ENUM, type_=_OLD_ENUM, existing_nullable=False
        )
