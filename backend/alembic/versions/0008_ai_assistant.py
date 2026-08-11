"""add ai provider keys, grant employee ai.use

Revision ID: 0008_ai_assistant
Revises: 0007_customers
Create Date: 2026-07-05

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

This migration also grants the Employee role the ai.use permission,
which migration 0001 omitted. That was a real gap, not a deliberate
restriction: the AI assistant is meant to help whoever is stuck at the
till, and keys are per-user (a cashier's own key, own cost), so there
was never a good reason to exclude Employee. Fixed here as an additive
data change rather than editing 0001, per the migration policy -- past
migrations are never rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_ai_assistant"
down_revision: str | None = "0007_customers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column(
            "provider",
            sa.Enum("OPENAI", "CLAUDE", "GEMINI", "DEEPSEEK", "NVIDIA", name="aiprovidername"),
            nullable=False,
        ),
        sa.Column("encrypted_key", sa.String(1000), nullable=False),
        sa.Column("key_hint", sa.String(4), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
    )

    conn = op.get_bind()
    employee_role_id = conn.execute(
        sa.text("SELECT id FROM roles WHERE name = 'Employee'")
    ).scalar_one()
    ai_use_permission_id = conn.execute(
        sa.text("SELECT id FROM permissions WHERE code = 'ai.use'")
    ).scalar_one()

    already_granted = conn.execute(
        sa.text(
            "SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"
        ),
        {"role_id": employee_role_id, "perm_id": ai_use_permission_id},
    ).scalar_one_or_none()

    if not already_granted:
        role_permissions_table = sa.table(
            "role_permissions",
            sa.column("role_id", sa.Integer),
            sa.column("permission_id", sa.Integer),
        )
        op.bulk_insert(
            role_permissions_table,
            [{"role_id": employee_role_id, "permission_id": ai_use_permission_id}],
        )


def downgrade() -> None:
    conn = op.get_bind()
    employee_role_id = conn.execute(
        sa.text("SELECT id FROM roles WHERE name = 'Employee'")
    ).scalar_one()
    ai_use_permission_id = conn.execute(
        sa.text("SELECT id FROM permissions WHERE code = 'ai.use'")
    ).scalar_one()
    conn.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"
        ),
        {"role_id": employee_role_id, "perm_id": ai_use_permission_id},
    )
    op.drop_table("ai_provider_keys")
