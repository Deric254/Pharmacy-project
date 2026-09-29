"""add missing index on products.category_id

Revision ID: 0038_products_category_id_index
Revises: 0037_restore_lost_indexes
Create Date: 2026-09-29

products.category_id has existed since 0003 with no index at either the
model or database level. It went unnoticed because nothing queried it at
volume until the category reporting/analytics endpoints (revenue by
category, its product drill-down, and the plain category filter on
products) were added -- a load test against 200k real sales showed
those queries, which join or GROUP BY this column, running 1.3-1.6s
against a full unindexed table/join scan. The same "restore a lost or
missing index once real usage exposes it" situation 0037 already fixed
for ix_products_name_active_unique and ix_sales_customer_id.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0038_products_category_id_index"
down_revision: str | None = "0037_restore_lost_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_products_category_id", "products", ["category_id"])


def downgrade() -> None:
    op.drop_index("ix_products_category_id", table_name="products")
