"""widen business_config.logo_url to TEXT

Revision ID: 0017_logo_url_text
Revises: 0016_audit_view_permission
Create Date: 2026-07-22

logo_url was VARCHAR(500) -- fine for a real hosted image URL, but a
real self-service logo upload (see the new Settings file picker) stores
an embedded data: URI instead, typically tens of thousands of
characters for even a modest image. A 500-character limit would have
silently truncated or rejected a real uploaded logo rather than failing
loudly, which is exactly the kind of corruption this project has
avoided everywhere else.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_logo_url_text"
down_revision: str | None = "0016_audit_view_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("business_config") as batch_op:
        batch_op.alter_column(
            "logo_url",
            existing_type=sa.String(500),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("business_config") as batch_op:
        batch_op.alter_column(
            "logo_url",
            existing_type=sa.Text(),
            type_=sa.String(500),
            existing_nullable=True,
        )
