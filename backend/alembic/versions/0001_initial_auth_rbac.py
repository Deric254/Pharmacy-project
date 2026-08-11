"""initial auth and rbac schema

Revision ID: 0001_initial_auth_rbac
Revises:
Create Date: 2026-07-02

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_auth_rbac"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(50), unique=True, nullable=False),
        sa.Column("description", sa.String(255), server_default=""),
    )

    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("description", sa.String(255), nullable=False),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer, sa.ForeignKey("roles.id"), primary_key=True),
        sa.Column("permission_id", sa.Integer, sa.ForeignKey("permissions.id"), primary_key=True),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("username", sa.String(80), unique=True, nullable=False, index=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("role_id", sa.Integer, sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("security_question", sa.String(255), nullable=True),
        sa.Column("security_answer_hash", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("refresh_token_jti", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("device_label", sa.String(120), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(50), nullable=False),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- Seed permissions (extend this list as each module ships) ---
    permissions_table = sa.table(
        "permissions", sa.column("code", sa.String), sa.column("description", sa.String)
    )
    seed_permissions = [
        {"code": "sales.create", "description": "Make a sale"},
        {"code": "sales.refund", "description": "Process a refund"},
        {"code": "inventory.view", "description": "View stock levels"},
        {"code": "inventory.adjust", "description": "Adjust stock with a reason code"},
        {"code": "purchasing.create_po", "description": "Create a purchase order"},
        {"code": "purchasing.approve_po", "description": "Approve/send a purchase order"},
        {"code": "purchasing.receive_stock", "description": "Receive goods against a PO"},
        {"code": "stocktake.perform", "description": "Perform a stock take count"},
        {"code": "stocktake.approve_variance", "description": "Approve stock take discrepancies"},
        {"code": "reports.view", "description": "View reports"},
        {"code": "reports.export", "description": "Export reports to Excel/PDF"},
        {"code": "config.edit", "description": "Edit the business configuration panel"},
        {"code": "users.manage", "description": "Manage users, roles, and permissions"},
        {"code": "ai.use", "description": "Use the AI assistant panel"},
        {"code": "backups.manage", "description": "Trigger/restore backups"},
    ]
    op.bulk_insert(permissions_table, seed_permissions)

    roles_table = sa.table(
        "roles", sa.column("name", sa.String), sa.column("description", sa.String)
    )
    op.bulk_insert(
        roles_table,
        [
            {"name": "Employee", "description": "Sells drugs / processes sales"},
            {
                "name": "Administrator",
                "description": "Runs the business day-to-day, places orders, manages staff",
            },
            {
                "name": "ChemistOwner",
                "description": "Approves/pays orders, views profit and full reports",
            },
        ],
    )

    conn = op.get_bind()

    role_ids = {row.name: row.id for row in conn.execute(sa.text("SELECT id, name FROM roles"))}
    perm_ids = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM permissions"))
    }

    employee_perms = ["sales.create", "inventory.view", "stocktake.perform"]
    admin_perms = [
        "sales.create",
        "sales.refund",
        "inventory.view",
        "inventory.adjust",
        "purchasing.create_po",
        "purchasing.approve_po",
        "purchasing.receive_stock",
        "stocktake.perform",
        "stocktake.approve_variance",
        "reports.view",
        "reports.export",
        "config.edit",
        "users.manage",
        "ai.use",
        "backups.manage",
    ]
    owner_perms = list(perm_ids.keys())  # Chemist Owner: full visibility, everything

    role_permission_rows = []
    for role_name, perm_codes in [
        ("Employee", employee_perms),
        ("Administrator", admin_perms),
        ("ChemistOwner", owner_perms),
    ]:
        for code in perm_codes:
            role_permission_rows.append(
                {"role_id": role_ids[role_name], "permission_id": perm_ids[code]}
            )

    role_permissions_table = sa.table(
        "role_permissions", sa.column("role_id", sa.Integer), sa.column("permission_id", sa.Integer)
    )
    op.bulk_insert(role_permissions_table, role_permission_rows)


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("user_sessions")
    op.drop_table("users")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
