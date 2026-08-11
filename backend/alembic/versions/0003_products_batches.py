"""add products, batches, stock movements

Revision ID: 0003_products_batches
Revises: 0002_business_config
Create Date: 2026-07-03

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_products_batches"
down_revision: str | None = "0002_business_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), unique=True, nullable=False),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(150), nullable=False, index=True),
        sa.Column("barcode", sa.String(64), unique=True, nullable=True, index=True),
        sa.Column("unit", sa.String(30), nullable=False, server_default="unit"),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("reorder_point", sa.Integer, nullable=False, server_default="10"),
        sa.Column("default_selling_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "medicine_batches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False, index=True
        ),
        sa.Column("batch_number", sa.String(80), nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=False, index=True),
        sa.Column("qty_received", sa.Integer, nullable=False),
        sa.Column("qty_remaining", sa.Integer, nullable=False),
        sa.Column("cost_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "batch_id", sa.Integer, sa.ForeignKey("medicine_batches.id"), nullable=False, index=True
        ),
        sa.Column(
            "movement_type",
            sa.Enum("PURCHASE", "SALE", "ADJUSTMENT", "RETURN", name="movementtype"),
            nullable=False,
        ),
        sa.Column("quantity_delta", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- New permissions for this module ---
    permissions_table = sa.table(
        "permissions", sa.column("code", sa.String), sa.column("description", sa.String)
    )
    op.bulk_insert(
        permissions_table,
        [
            {"code": "products.manage", "description": "Create/edit products in the catalog"},
            {"code": "batches.create", "description": "Manually add a stock batch"},
        ],
    )

    conn = op.get_bind()
    role_ids = {row.name: row.id for row in conn.execute(sa.text("SELECT id, name FROM roles"))}
    new_perm_ids = {
        row.code: row.id
        for row in conn.execute(
            sa.text(
                "SELECT id, code FROM permissions "
                "WHERE code IN ('products.manage', 'batches.create')"
            )
        )
    }

    # Administrator and ChemistOwner both get these; Employee does not
    # (matches the client's own answer: employees only sell drugs).
    role_permissions_table = sa.table(
        "role_permissions", sa.column("role_id", sa.Integer), sa.column("permission_id", sa.Integer)
    )
    rows = []
    for role_name in ["Administrator", "ChemistOwner"]:
        for perm_code in ["products.manage", "batches.create"]:
            rows.append({"role_id": role_ids[role_name], "permission_id": new_perm_ids[perm_code]})
    op.bulk_insert(role_permissions_table, rows)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE code IN ('products.manage', 'batches.create'))"
        )
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE code IN ('products.manage', 'batches.create')")
    )
    op.drop_table("stock_movements")
    op.drop_table("medicine_batches")
    op.drop_table("products")
    op.drop_table("categories")
