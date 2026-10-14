"""
The additive schema behind purchase reversal (migration 0042), written so it
can be applied more than once and from two places -- exactly like
`supplier_payment_schema` (see its docstring for why):

  * migration 0042 itself, and
  * the desktop launcher, when migration 0039 refuses a shop's existing data
    and the app must keep running on the older schema.

Every statement checks first, so running it again is a no-op.

Two steps, because they shipped in two migrations: `apply` (0042: the reversed
columns and the permission) and `apply_restock_snapshot` (0043: what a restock
must remember so that reversing it can put the batch's cost back exactly).
"""

from app.core.supplier_payment_schema import Runner

PERMISSION_CODE = "purchasing.reverse_purchase"
PERMISSION_DESCRIPTION = "Reverse a received purchase (removes its stock, cancels what is owed)"
OWNER_ROLE = "ChemistOwner"


def _columns(run: Runner) -> set[str]:
    return {row[1] for row in run("PRAGMA table_info(purchase_orders)")}


def apply(run: Runner) -> None:
    existing = _columns(run)
    if "reversed_at" not in existing:
        run("ALTER TABLE purchase_orders ADD COLUMN reversed_at DATETIME")
    if "reversal_reason" not in existing:
        run("ALTER TABLE purchase_orders ADD COLUMN reversal_reason VARCHAR(255)")
    run(
        "INSERT INTO permissions (code, description) SELECT ?, ? "
        "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = ?)",
        (PERMISSION_CODE, PERMISSION_DESCRIPTION, PERMISSION_CODE),
    )
    run(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT r.id, p.id FROM roles r, permissions p "
        "WHERE r.name = ? AND p.code = ? "
        "AND NOT EXISTS (SELECT 1 FROM role_permissions rp "
        "WHERE rp.role_id = r.id AND rp.permission_id = p.id)",
        (OWNER_ROLE, PERMISSION_CODE),
    )


def apply_restock_snapshot(run: Runner) -> None:
    existing = {row[1] for row in run("PRAGMA table_info(purchase_order_items)")}
    if "batch_cost_before" not in existing:
        run("ALTER TABLE purchase_order_items ADD COLUMN batch_cost_before INTEGER")
    if "batch_cost_after" not in existing:
        run("ALTER TABLE purchase_order_items ADD COLUMN batch_cost_after INTEGER")
