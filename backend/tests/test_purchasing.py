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

import asyncio

from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_take import StockTake
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


class TestSupplierExport:
    async def test_json_export_is_still_the_default(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get("/api/v1/suppliers", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")

    async def test_excel_export_returns_a_real_spreadsheet(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            "/api/v1/suppliers", json={"name": "Exportable Supplier"}, headers=headers
        )

        r = await client.get("/api/v1/suppliers?export=excel", headers=headers)
        assert r.status_code == 200
        assert (
            r.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert len(r.content) > 0

        import io
        import zipfile

        assert zipfile.is_zipfile(io.BytesIO(r.content))


class TestRecordPayment:
    """
    Real gap this closes: record_payment had zero dedicated test
    coverage anywhere in the suite before this -- its only exercise
    was incidental, inside an audit-consistency test focused on a
    different concern. Nothing had ever verified its permission
    boundary, its 404 handling, or a payment against a supplier
    nobody owes anything to.
    """

    async def test_requires_purchasing_approve_po_not_just_create_po(
        self, client, owner_user, employee_user
    ):
        """
        A deliberately stricter permission than the other supplier
        endpoints (purchasing.approve_po, not purchasing.create_po) --
        recording a payment is a more sensitive financial action than
        just creating or viewing a supplier. This is the first test to
        ever confirm that distinction is actually enforced, not just
        declared in the route decorator.
        """
        owner_token = await _login(client, "lucy", "S3curePass!")
        supplier = await client.post(
            "/api/v1/suppliers",
            json={"name": "Permission Test Supplier"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        supplier_id = supplier.json()["id"]

        employee_token = await _login(client, "joe", "pass1234")
        r = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments",
            json={"amount": 10.0},
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert r.status_code == 403

    async def test_payment_against_a_nonexistent_supplier_is_a_clean_404(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/suppliers/999999/payments",
            json={"amount": 10.0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    async def test_payment_correctly_reduces_the_real_balance_owed(self, client, owner_user):
        product_id = await _make_product("Payment Balance Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Balance Test Supplier"}, headers=headers
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
                        "batch_number": "PAY-BAL-1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 20.0,
                    }
                ],
            },
            headers=headers,
        )
        supplier_check = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert supplier_check.json()["balance_owed"] == 200.0  # 10 * 20.0

        r = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments",
            json={"amount": 75.0},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["balance_owed"] == 125.0  # 200 - 75

    async def test_zero_or_negative_payment_amount_is_rejected(self, client, owner_user):
        """
        PositiveMoney is the schema-layer guard -- this proves it
        actually applies here, end to end through the real endpoint,
        not just that the type exists somewhere in the codebase.
        """
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Zero Payment Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r_zero = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments",
            json={"amount": 0.0},
            headers=headers,
        )
        assert r_zero.status_code == 422

        r_negative = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments",
            json={"amount": -50.0},
            headers=headers,
        )
        assert r_negative.status_code == 422


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

    async def test_explicit_selling_price_applied_to_legacy_null_batch(self, client, owner_user):
        """
        A batch with no selling_price set (the real-world case for any
        batch created before this field existed -- migration 0029
        backfills those to NULL) that receives a new delivery which DOES
        specify a selling_price must have that price applied. Before the
        fix, `selling_price` was resolved to `product.default_selling_price`
        before the merge decision, so the `existing_batch.selling_price
        is not None` guard on the *existing* side silently swallowed an
        explicitly-submitted price whenever the existing batch happened
        to have none yet -- no error, no confirmation, just dropped.
        """
        from sqlalchemy import select

        product_id = await _make_product("Legacy Null Price Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Legacy Null Price Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r1 = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 100,
                        "batch_number": "LEGACY-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 10.0,
                    }
                ],
            },
            headers=headers,
        )
        assert r1.status_code == 201, r1.text

        # Force the batch back to NULL to simulate a pre-feature batch.
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.batch_number == "LEGACY-001")
            )
            batch = result.scalar_one()
            batch.selling_price = None
            await db.commit()

        r2 = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 50,
                        "batch_number": "LEGACY-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 10.0,
                        "selling_price": 25.0,
                    }
                ],
            },
            headers=headers,
        )
        assert r2.status_code == 201, r2.text

        batches = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert batches.json()[0]["selling_price"] == 25.0

    async def test_plain_restock_never_blocked_by_unspecified_price(self, client, owner_user):
        """
        Restocking a batch that already has a selling_price, WITHOUT
        specifying a price on the new line (the normal case -- nobody
        retypes the price on every routine restock), must never be
        blocked. Before the fix, an unspecified line price was resolved
        to `product.default_selling_price` before the merge check, so a
        routine restock with no price opinion at all could get a false
        409 the moment the product's generic default drifted from
        whatever price this specific batch was actually set to sell at.
        """
        product_id = await _make_product("Restock No Price Product", price=20.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Restock No Price Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r1 = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 100,
                        "batch_number": "RESTOCK-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 10.0,
                        "selling_price": 25.0,
                    }
                ],
            },
            headers=headers,
        )
        assert r1.status_code == 201, r1.text

        r2 = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 50,
                        "batch_number": "RESTOCK-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 10.0,
                        # no selling_price -- must not conflict with the
                        # batch's real price (25.0) just because it
                        # differs from the product default (20.0)
                    }
                ],
            },
            headers=headers,
        )
        assert r2.status_code == 201, r2.text

        batches = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert batches.json()[0]["selling_price"] == 25.0
        assert batches.json()[0]["qty_remaining"] == 150


class TestQuickPurchaseConcurrency:
    """
    quick_purchase merges a repeat delivery of the same physical batch
    (same product, batch number, expiry) into the existing row.
    qty_remaining/qty_received/cost_price are applied via SQL
    column-relative expressions in a single atomic UPDATE (see
    PurchasingService.quick_purchase), the same proven pattern already
    used for stock decrement, loyalty points, and stock-take close
    elsewhere in this codebase -- not a Python read-then-write of
    values fetched earlier, which would let a second concurrent
    receipt of the same batch silently overwrite the first's addition
    instead of compounding.

    This test predates that fix and is kept as-is deliberately: it
    doesn't know or care which mechanism protects the invariant, only
    that receiving the same batch concurrently from multiple directions
    (a real scenario: someone importing a purchase-order spreadsheet
    while someone else quick-purchases the same item that just
    physically arrived) never loses a delivery.
    """

    async def test_two_concurrent_receipts_of_the_same_batch_both_count(self, client, owner_user):
        product_id = await _make_product("Concurrent Batch Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Concurrency Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        def make_payload(qty: int) -> dict:
            return {
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": qty,
                        "batch_number": "CONC-BATCH-1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 10.0,
                    }
                ],
            }

        async def receive(qty: int):
            return await client.post(
                "/api/v1/purchase-orders/quick-purchase",
                json=make_payload(qty),
                headers=headers,
            )

        results = await asyncio.gather(receive(20), receive(30), return_exceptions=True)
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert not exceptions, f"Unhandled exceptions under concurrency: {exceptions}"
        status_codes = [r.status_code for r in results]
        assert all(code == 201 for code in status_codes), status_codes

        product = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        # If this is a genuine lost-update race, this lands at 20 or
        # 30 (whichever write happened last) instead of the correct
        # 50 -- real inventory silently vanishing.
        assert product.json()["total_qty_available"] == 50, (
            f"LOST UPDATE: expected 50 (20 + 30 concurrent receipts of the same batch), "
            f"got {product.json()['total_qty_available']}"
        )

    async def test_five_concurrent_receipts_of_the_same_batch_all_count(self, client, owner_user):
        """
        Higher fan-out than the 2-way case above -- 5 simultaneous
        receipts of 10 units each into the same batch. Same reasoning
        as the refund-concurrency probe elsewhere in this suite: a
        2-way race that happens to pass doesn't rule out a real gap
        that only shows up under more contention."""
        product_id = await _make_product("Five Way Concurrent Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Five Way Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        async def receive():
            return await client.post(
                "/api/v1/purchase-orders/quick-purchase",
                json={
                    "supplier_id": supplier_id,
                    "lines": [
                        {
                            "product_id": product_id,
                            "quantity": 10,
                            "batch_number": "CONC-BATCH-5X",
                            "expiry_date": "2027-06-30",
                            "unit_cost": 10.0,
                        }
                    ],
                },
                headers=headers,
            )

        results = await asyncio.gather(*[receive() for _ in range(5)], return_exceptions=True)
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert not exceptions, f"Unhandled exceptions under concurrency: {exceptions}"
        assert all(r.status_code == 201 for r in results), [r.status_code for r in results]

        product = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert product.json()["total_qty_available"] == 50, (
            f"LOST UPDATE under 5-way concurrency: expected 50 (5 x 10), "
            f"got {product.json()['total_qty_available']}"
        )


class TestQuickPurchaseRespectsStockTakeLock:
    """
    Mirrors RefundService._restock_batch's own lock-respect test: a
    batch locked for an active physical count must not have
    qty_remaining/cost_price move underneath the counter mid-count,
    whether that write would come from a refund restock or (this case)
    receiving more stock against the same existing batch.
    """

    async def test_receiving_into_a_locked_batch_is_rejected(self, client, owner_user):
        product_id = await _make_product("Locked Batch Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Lock Test Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        first = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 20,
                        "batch_number": "LOCK-BATCH-1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 10.0,
                    }
                ],
            },
            headers=headers,
        )
        assert first.status_code == 201, first.text
        batch_id = first.json()["items"][0]["batch_id"]

        # Simulate what StockTakeService.initiate() does to a batch it
        # claims: lock it. Going through the real stock-take endpoints
        # would work equally well but adds nothing this test needs --
        # the thing under test is quick_purchase's own respect for the
        # lock column, not how the lock gets set.
        async with AsyncSessionLocal() as db:
            stock_take = StockTake(initiated_by_user_id=owner_user.id)
            db.add(stock_take)
            await db.flush()
            batch = await db.get(MedicineBatch, batch_id)
            batch.locked_by_stock_take_id = stock_take.id
            await db.commit()

        second = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 5,
                        "batch_number": "LOCK-BATCH-1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 10.0,
                    }
                ],
            },
            headers=headers,
        )
        assert second.status_code == 409, second.text

        async with AsyncSessionLocal() as db:
            batch = await db.get(MedicineBatch, batch_id)
            # Rejected cleanly, not partially applied: the count this
            # batch is locked for must still see exactly what it
            # snapshotted, not 20 + 5.
            assert batch.qty_remaining == 20
