"""restore the case-insensitive product name index and add sales.customer_id's

Revision ID: 0037_restore_lost_indexes
Revises: 0036_batch_selling_price_required
Create Date: 2026-09-20

Two indexes the models declare were missing from any database built by
running the migrations (the tests build from the models, so they never
saw it):

1. ix_products_name_active_unique. 0020 created it case-insensitively
   (COLLATE NOCASE) so "Paracetamol" and "paracetamol" cannot both be
   active products. A later migration rebuilt the products table in
   batch mode, and SQLite's reflection does not keep an index's
   collation, so the rebuild recreated it as a plain, case-SENSITIVE
   index. Only the service-layer check was left standing guard.

   Because the constraint was silently absent, a database may already
   hold case-variant duplicates, and creating the unique index over them
   would abort the upgrade -- leaving the app unable to start. Any such
   duplicate (all but the oldest of each group) is therefore renamed
   with a visible suffix first, never deleted.

2. ix_sales_customer_id, which the Sale model declares, is the index that
   keeps a customer's purchase history and lifetime-value lookups from
   scanning every sale.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037_restore_lost_indexes"
down_revision: str | None = "0036_batch_selling_price_required"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    connection = op.get_bind()

    duplicates = connection.execute(
        sa.text(
            "SELECT id, name FROM products "
            "WHERE deleted_at IS NULL AND id NOT IN ("
            "  SELECT MIN(id) FROM products WHERE deleted_at IS NULL "
            "  GROUP BY name COLLATE NOCASE)"
        )
    ).all()
    for product_id, name in duplicates:
        renamed = f"{name} (duplicate #{product_id})"
        logger.warning("Renaming case-variant duplicate product %r to %r", name, renamed)
        connection.execute(
            sa.text("UPDATE products SET name = :name WHERE id = :id"),
            {"name": renamed, "id": product_id},
        )

    op.execute("DROP INDEX IF EXISTS ix_products_name_active_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_products_name_active_unique "
        "ON products (name COLLATE NOCASE) "
        "WHERE deleted_at IS NULL"
    )
    op.create_index("ix_sales_customer_id", "sales", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_customer_id", table_name="sales")
    op.execute("DROP INDEX IF EXISTS ix_products_name_active_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_products_name_active_unique "
        "ON products (name) WHERE deleted_at IS NULL"
    )
