"""add users.terms_accepted_at

Revision ID: 0022_terms_accepted
Revises: 0021_barcode_unique_active
Create Date: 2026-07-31

Records exactly when each user consented to the terms of service --
not just a boolean, so there's a real, timestamped record of consent
if it's ever needed. Null means never accepted; every real account
(including the very first owner created during setup) must accept
before reaching the rest of the app, the same way must_change_password
gates a temp-credential account.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_terms_accepted"
down_revision: str | None = "0021_barcode_unique_active"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("terms_accepted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("terms_accepted_at")
