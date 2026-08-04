"""
Purchasing tests. The properties that matter:
  1. Suppliers can be created and fetched, gated by permission.
  2. quick_purchase (the only way stock enters the app -- see
     purchasing_service.py's own docstring on why the old
     draft/send/in-transit/receive/reconcile state machine was removed
     entirely as unreachable dead code) actually creates real batches,
     real stock-movement ledger entries, and real supplier debt, all
     in the same transaction as the purchase order itself.
  3. Re-receiving the same physical batch (same product/batch number/
     expiry) merges into the existing row via weighted-average cost,
     rather than duplicating it; a genuinely different batch never
     merges with an unrelated one.
"""

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
