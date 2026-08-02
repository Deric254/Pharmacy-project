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
        # The actual bug this closes: a raw product_id with no name at
        # all, which is what forced the receiving UI to show
        # "Product #1" instead of something a real person can read.
        assert create_resp.json()["items"][0]["product_name"] == "PO Test Product"

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
        # The real fix: a real product name, not just an internal item ID.
        assert variances[0]["product_name"] == "PO Test Product"

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
        not both succeed. Same underlying guarantee as the Sales
        concurrency test, for the same reason: not row-locking (SQLite
        silently drops SELECT...FOR UPDATE entirely), but the atomic
        `UPDATE ... WHERE status = :expected_current` in _transition,
        checked against the row's real state at the moment it runs.
        """
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


class TestQuickPurchase:
    """
    The direct path: no draft/send/in-transit ceremony, straight to a
    received purchase order with real stock -- for the common real-
    world case where the delivery is already here and there was no
    advance order to track.
    """

    async def test_goes_straight_to_received_with_real_stock_and_correct_cost(
        self, client, owner_user
    ):
        product_id = await _make_product("Quick Purchase Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Quick Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 50,
                        "batch_number": "QP-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 8.0,
                    }
                ],
            },
            headers=headers,
        )
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "RECEIVED"
        assert body["received_at"] is not None
        assert body["sent_at"] is not None
        assert body["in_transit_at"] is not None

        product = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert product.json()["total_qty_available"] == 50
        assert product.json()["current_cost"] == 8.0

    async def test_multiple_lines_all_land_correctly(self, client, owner_user):
        product1 = await _make_product("Quick Multi Product A")
        product2 = await _make_product("Quick Multi Product B")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Multi Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product1,
                        "quantity": 30,
                        "batch_number": "MULTI-A",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 5.0,
                    },
                    {
                        "product_id": product2,
                        "quantity": 20,
                        "batch_number": "MULTI-B",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 12.0,
                    },
                ],
            },
            headers=headers,
        )
        assert r.status_code == 201
        assert len(r.json()["items"]) == 2

        p1 = await client.get(f"/api/v1/products/{product1}", headers=headers)
        p2 = await client.get(f"/api/v1/products/{product2}", headers=headers)
        assert p1.json()["total_qty_available"] == 30
        assert p2.json()["total_qty_available"] == 20

    async def test_creates_a_real_supplier_transaction_for_what_is_owed(self, client, owner_user):
        product_id = await _make_product("Quick Debt Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Debt Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 10,
                        "batch_number": "DEBT-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 15.0,
                    }
                ],
            },
            headers=headers,
        )

        supplier_check = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert supplier_check.json()["balance_owed"] == 150.0  # 10 * 15.0

    async def test_nonexistent_supplier_rejected_cleanly(self, client, owner_user):
        product_id = await _make_product("Quick No Supplier Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": 999999,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 10,
                        "batch_number": "B1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 5.0,
                    }
                ],
            },
            headers=headers,
        )
        assert r.status_code == 404

    async def test_requires_create_po_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": 1,
                "lines": [
                    {
                        "product_id": 1,
                        "quantity": 10,
                        "batch_number": "B1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 5.0,
                    }
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_receiving_the_same_batch_again_merges_not_duplicates(self, client, owner_user):
        """
        The real, confirmed bug this closes: receiving the same
        physical batch twice (same product, same batch number, same
        expiry -- e.g. re-uploading the same purchase list, or simply
        restocking the identical batch) created a second, separate
        batch row instead of adding to the existing one. Proven with
        exact weighted-average cost math, not just "no duplicate row".
        """
        product_id = await _make_product("Merge Test Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Merge Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        first_line = {
            "product_id": product_id,
            "quantity": 100,
            "batch_number": "MERGE-001",
            "expiry_date": "2027-06-30",
            "unit_cost": 10.0,
        }
        r1 = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={"supplier_id": supplier_id, "lines": [first_line]},
            headers=headers,
        )
        assert r1.status_code == 201

        # Receive the identical batch again, at a different cost --
        # exactly the "re-uploaded the same list" real-world scenario.
        second_line = {
            "product_id": product_id,
            "quantity": 50,
            "batch_number": "MERGE-001",
            "expiry_date": "2027-06-30",
            "unit_cost": 16.0,
        }
        r2 = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={"supplier_id": supplier_id, "lines": [second_line]},
            headers=headers,
        )
        assert r2.status_code == 201

        product = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        body = product.json()
        # Never duplicated: exactly 150 total, not 100 and 50 sitting
        # in two separate batches.
        assert body["total_qty_available"] == 150
        # Real weighted-average cost: (100*10 + 50*16) / 150 = 12.0
        assert body["current_cost"] == 12.0

        batches = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert len(batches.json()) == 1

    async def test_different_expiry_dates_never_merge(self, client, owner_user):
        """
        Same batch number, different expiry -- genuinely different
        physical batches (a real-world relabeling/re-count case) --
        must never be merged into one, since that would corrupt which
        units expire when.
        """
        product_id = await _make_product("No Merge Expiry Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "No Merge Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        for expiry in ["2027-06-30", "2028-01-15"]:
            await client.post(
                "/api/v1/purchase-orders/quick-purchase",
                json={
                    "supplier_id": supplier_id,
                    "lines": [
                        {
                            "product_id": product_id,
                            "quantity": 20,
                            "batch_number": "SAME-NUMBER",
                            "expiry_date": expiry,
                            "unit_cost": 5.0,
                        }
                    ],
                },
                headers=headers,
            )

        batches = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert len(batches.json()) == 2
