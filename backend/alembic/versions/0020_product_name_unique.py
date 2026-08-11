"""add case-insensitive unique index on active product names

Revision ID: 0020_product_name_unique
Revises: 0019_must_change_password
Create Date: 2026-07-29

Confirmed gap: nothing stopped the same drug being entered twice under
different barcodes (or no barcode at all), silently splitting its real
stock across two "different" catalog entries -- exactly the kind of
duplicate this whole system exists to prevent. This is a genuine
database-level constraint, not just an application-level check that
could still race under concurrent creation, matching every other
consistency guarantee built this session.

Case-insensitive (COLLATE NOCASE) so "Paracetamol 500mg" and
"paracetamol 500mg" are correctly treated as the same product.
Partial (WHERE deleted_at IS NULL) so deactivating a product frees its
name for reuse by a genuinely new one later, rather than permanently
locking that name out.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_product_name_unique"
down_revision: str | None = "0019_must_change_password"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX ix_products_name_active_unique "
        "ON products (name COLLATE NOCASE) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_products_name_active_unique")
