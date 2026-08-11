"""make barcode unique among active products only

Revision ID: 0021_barcode_unique_active
Revises: 0020_product_name_unique
Create Date: 2026-07-29

Related bug found alongside the name-uniqueness fix: barcode's old
constraint was a plain column-level unique with no exception for
soft-deleted rows, so once a product was deactivated, its barcode
could never be reused by a genuinely new product ever again. Same fix
shape as 0020 -- a partial unique index scoped to deleted_at IS NULL.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_barcode_unique_active"
down_revision: str | None = "0020_product_name_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_name")
    op.execute("DROP INDEX IF EXISTS ix_products_barcode")
    op.execute(
        "CREATE UNIQUE INDEX ix_products_barcode_active_unique "
        "ON products (barcode) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_products_barcode_active_unique")
    op.execute("CREATE UNIQUE INDEX ix_products_barcode ON products (barcode)")
    op.execute("CREATE INDEX ix_products_name ON products (name)")
