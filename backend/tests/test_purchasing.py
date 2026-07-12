"""
Purchasing tests. The properties that matter:
  1. The state machine actually enforces legal transitions - you can't
     skip from DRAFT to RECEIVED, or receive twice.
  2. Receiving actually creates real batches/ledger entries/supplier
     debt in the same transaction as the status flip - "drag the card,
     stock updates" is literally true, not aspirational.
  3. A receiving variance (actual != ordered) is detected and reported,
     never silently smoothed over.
"""

import asyncio

from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.models.supplier import Supplier
from tests.conftest import running_on_sqlite


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _make_supplier(name: str = "Test Supplier") -> int:
    async with AsyncSessionLocal() as db:
        supplier = Supplier(name=name)
        db.add(supplier)
        await db.commit()
        return int(supplier.id)


async def _make_product(name: str = "PO Test Product", price: float = 20.0) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name=name, default_selling_price=price)
        db.add(product)
        await db.commit()
        return int(product.id)


class TestSupplierCRUD:
    async def test_create_and_get_supplier(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            "/api/v1/suppliers", json={"name": "MedSupply Kenya"}, headers=headers
        )
        assert r.status_code == 201
        assert r.json()["balance_owed"] == 0.0

        supplier_id = r.json()["id"]
        r2 = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "MedSupply Kenya"

    async def test_requires_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/suppliers",
            json={"name": "Should Fail"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


class TestPurchaseOrderLifecycle:
    async def test_full_happy_path(self, client, owner_user):
        supplier_id = await _make_supplier()
        product_id = await _make_product()
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 100, "unit_cost_expected": 8.0}
                ],
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        po_id = create_resp.json()["id"]
        item_id = create_resp.json()["items"][0]["id"]
        assert create_resp.json()["status"] == "DRAFT"

        sent = await client.post(f"/api/v1/purchase-orders/{po_id}/send", headers=headers)
        assert sent.status_code == 200
        assert sent.json()["status"] == "SENT"

        transit = await client.post(
            f"/api/v1/purchase-orders/{po_id}/mark-in-transit", headers=headers
        )
        assert transit.status_code == 200
        assert transit.json()["status"] == "IN_TRANSIT"

        received = await client.post(
            f"/api/v1/purchase-orders/{po_id}/receive",
            json={
                "lines": [
                    {
                        "item_id": item_id,
                        "batch_number": "PO-BATCH-1",
                        "expiry_date": "2027-06-01",
                        "quantity_received": 100,
                        "unit_cost_actual": 8.0,
                    }
                ]
            },
            headers=headers,
        )
        assert received.status_code == 200
        assert received.json()["purchase_order"]["status"] == "RECEIVED"
        assert received.json()["variances"] == []  # exact match, no variance

        # The actual integration point: real batch + ledger + debt created.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batch = batch_result.scalar_one()
            assert batch.qty_remaining == 100
            assert batch.cost_price == 8.0

            ledger_result = await db.execute(
                select(StockMovement).where(StockMovement.reference == f"po:{po_id}")
            )
            ledger_rows = ledger_result.scalars().all()
            assert len(ledger_rows) == 1
            assert ledger_rows[0].movement_type == MovementType.PURCHASE
            assert ledger_rows[0].quantity_delta == 100

        supplier_check = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert supplier_check.json()["balance_owed"] == 800.0  # 100 * 8.0

        reconciled = await client.post(
            f"/api/v1/purchase-orders/{po_id}/reconcile",
            json={"payment_amount": 800.0},
            headers=headers,
        )
        assert reconciled.status_code == 200
        assert reconciled.json()["status"] == "RECONCILED"

        supplier_after_payment = await client.get(
            f"/api/v1/suppliers/{supplier_id}", headers=headers
        )
        assert supplier_after_payment.json()["balance_owed"] == 0.0

    async def test_can_purchase_product_after_receiving(self, client, owner_user, employee_user):
        """Ties Purchasing back to Sales: newly received stock is immediately sellable."""
        supplier_id = await _make_supplier()
        product_id = await _make_product(price=15.0)
        owner_token = await _login(client, "lucy", "S3curePass!")
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 10, "unit_cost_expected": 5.0}
                ],
            },
            headers=owner_headers,
        )
        po_id = po.json()["id"]
        item_id = po.json()["items"][0]["id"]
        await client.post(f"/api/v1/purchase-orders/{po_id}/send", headers=owner_headers)
        await client.post(f"/api/v1/purchase-orders/{po_id}/mark-in-transit", headers=owner_headers)
        await client.post(
            f"/api/v1/purchase-orders/{po_id}/receive",
            json={
                "lines": [
                    {
                        "item_id": item_id,
                        "batch_number": "B1",
                        "expiry_date": "2027-01-01",
                        "quantity_received": 10,
                        "unit_cost_actual": 5.0,
                    }
                ]
            },
            headers=owner_headers,
        )

        employee_token = await _login(client, "joe", "pass1234")
        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 3}],
                "payments": [{"method": "CASH", "amount": 45.0}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert sale.status_code == 201

    async def test_receiving_variance_detected_and_reported(self, client, owner_user):
        supplier_id = await _make_supplier()
        product_id = await _make_product()
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 50, "unit_cost_expected": 10.0}
                ],
            },
            headers=headers,
        )
        po_id = po.json()["id"]
        item_id = po.json()["items"][0]["id"]
        await client.post(f"/api/v1/purchase-orders/{po_id}/send", headers=headers)
        await client.post(f"/api/v1/purchase-orders/{po_id}/mark-in-transit", headers=headers)

        received = await client.post(
            f"/api/v1/purchase-orders/{po_id}/receive",
            json={
                "lines": [
                    {
                        "item_id": item_id,
                        "batch_number": "SHORT-SHIP",
                        "expiry_date": "2027-01-01",
                        "quantity_received": 42,  # supplier under-shipped
                        "unit_cost_actual": 10.0,
                    }
                ]
            },
            headers=headers,
        )
        assert received.status_code == 200
        variances = received.json()["variances"]
        assert len(variances) == 1
        assert variances[0]["quantity_ordered"] == 50
        assert variances[0]["quantity_received"] == 42
        assert variances[0]["variance"] == -8

        # Variance doesn't block receiving -- the 42 actually delivered
        # are still received into stock and owed for.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            assert batch_result.scalar_one().qty_remaining == 42

        supplier_check = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert supplier_check.json()["balance_owed"] == 420.0  # 42 * 10.0, not 50 * 10.0

    async def test_cannot_skip_states(self, client, owner_user):
        supplier_id = await _make_supplier()
        product_id = await _make_product()
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 10, "unit_cost_expected": 5.0}
                ],
            },
            headers=headers,
        )
        po_id = po.json()["id"]
        item_id = po.json()["items"][0]["id"]

        # Try to receive a DRAFT PO directly, skipping SENT/IN_TRANSIT.
        r = await client.post(
            f"/api/v1/purchase-orders/{po_id}/receive",
            json={
                "lines": [
                    {
                        "item_id": item_id,
                        "batch_number": "X",
                        "expiry_date": "2027-01-01",
                        "quantity_received": 10,
                        "unit_cost_actual": 5.0,
                    }
                ]
            },
            headers=headers,
        )
        assert r.status_code == 400

    async def test_cannot_receive_same_item_twice(self, client, owner_user):
        supplier_id = await _make_supplier()
        product_id = await _make_product()
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 10, "unit_cost_expected": 5.0}
                ],
            },
            headers=headers,
        )
        po_id = po.json()["id"]
        item_id = po.json()["items"][0]["id"]
        await client.post(f"/api/v1/purchase-orders/{po_id}/send", headers=headers)
        await client.post(f"/api/v1/purchase-orders/{po_id}/mark-in-transit", headers=headers)

        receive_line = {
            "lines": [
                {
                    "item_id": item_id,
                    "batch_number": "B1",
                    "expiry_date": "2027-01-01",
                    "quantity_received": 10,
                    "unit_cost_actual": 5.0,
                }
            ]
        }
        first = await client.post(
            f"/api/v1/purchase-orders/{po_id}/receive", json=receive_line, headers=headers
        )
        assert first.status_code == 200

        # PO is already RECEIVED now -- a second call is blocked by the
        # state machine itself before it even re-checks item.batch_id.
        second = await client.post(
            f"/api/v1/purchase-orders/{po_id}/receive", json=receive_line, headers=headers
        )
        assert second.status_code == 400

    async def test_send_requires_approve_po_permission(self, client, owner_user, seeded_roles):
        from sqlalchemy import select

        from app.core.security import hash_password
        from app.models.role import Role
        from app.models.user import User

        supplier_id = await _make_supplier()
        product_id = await _make_product()
        owner_token = await _login(client, "lucy", "S3curePass!")
        po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 10, "unit_cost_expected": 5.0}
                ],
            },
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        po_id = po.json()["id"]

        # Administrator can create/receive but per this test's intent,
        # confirm an Employee (no purchasing perms at all) is rejected.
        async with AsyncSessionLocal() as db:
            role_result = await db.execute(select(Role).where(Role.name == "Employee"))
            role = role_result.scalar_one()
            u = User(
                full_name="Employee No Purchasing",
                username="nopurchasing",
                hashed_password=hash_password("pass1234"),
                role_id=role.id,
            )
            db.add(u)
            await db.commit()

        employee_token = await _login(client, "nopurchasing", "pass1234")
        r = await client.post(
            f"/api/v1/purchase-orders/{po_id}/send",
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert r.status_code == 403

    async def test_kanban_groups_by_status(self, client, owner_user):
        supplier_id = await _make_supplier()
        product_id = await _make_product()
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        draft_po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 5, "unit_cost_expected": 3.0}
                ],
            },
            headers=headers,
        )
        sent_po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 5, "unit_cost_expected": 3.0}
                ],
            },
            headers=headers,
        )
        await client.post(f"/api/v1/purchase-orders/{sent_po.json()['id']}/send", headers=headers)

        board = await client.get("/api/v1/purchase-orders/kanban", headers=headers)
        assert board.status_code == 200
        draft_ids = {po["id"] for po in board.json()["DRAFT"]}
        sent_ids = {po["id"] for po in board.json()["SENT"]}
        assert draft_po.json()["id"] in draft_ids
        assert sent_po.json()["id"] in sent_ids
        assert sent_po.json()["id"] not in draft_ids


class TestConcurrentTransitions:
    async def test_two_concurrent_send_calls_only_one_succeeds(self, client, owner_user):
        """
        Two simultaneous 'send' calls against the same DRAFT PO should
        not both succeed -- the row lock in _transition must serialize
        them, same principle as the Sales concurrency test.

        Skipped on SQLite for the same reason as the Sales concurrency
        test: SQLAlchemy silently drops SELECT...FOR UPDATE on SQLite.
        Verified against real MySQL/InnoDB instead.
        """
        if running_on_sqlite():
            import pytest

            pytest.skip("SQLite has no row-level locking; verified against real MySQL instead.")

        supplier_id = await _make_supplier()
        product_id = await _make_product()
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 5, "unit_cost_expected": 3.0}
                ],
            },
            headers=headers,
        )
        po_id = po.json()["id"]

        async def attempt_send():
            return await client.post(f"/api/v1/purchase-orders/{po_id}/send", headers=headers)

        results = await asyncio.gather(attempt_send(), attempt_send(), return_exceptions=True)
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        assert status_codes.count(200) == 1
        assert status_codes.count(400) == 1
