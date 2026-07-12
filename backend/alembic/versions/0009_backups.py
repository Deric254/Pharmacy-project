"""add backup logs and oauth tokens

Revision ID: 0009_backups
Revises: 0008_ai_assistant
Create Date: 2026-07-05

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_backups"
down_revision: str | None = "0008_ai_assistant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_oauth_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "provider",
            sa.Enum("GOOGLE_DRIVE", name="backupprovidername"),
            nullable=False,
            unique=True,
        ),
        sa.Column("encrypted_refresh_token", sa.LargeBinary(2000), nullable=False),
        sa.Column("connected_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "backup_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.Enum("SUCCESS", "FAILED", name="backupstatus"), nullable=False),
        sa.Column(
            "provider",
            sa.Enum("GOOGLE_DRIVE", name="backupprovidername"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("manifest_json", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("restored_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("backup_logs")
    op.drop_table("backup_oauth_tokens")
