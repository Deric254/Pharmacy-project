"""add theme_name to business_config

Revision ID: 0011_business_theme
Revises: 0010_refunds
Create Date: 2026-07-16

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
Additive: one nullable-with-default column, existing rows get
"ledger" (today's only theme) so nothing visually changes for an
existing deployment until the owner actively picks a different one.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_business_theme"
down_revision: str | None = "0010_refunds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "business_config",
        sa.Column("theme_name", sa.String(30), nullable=False, server_default="ledger"),
    )


def downgrade() -> None:
    op.drop_column("business_config", "theme_name")
