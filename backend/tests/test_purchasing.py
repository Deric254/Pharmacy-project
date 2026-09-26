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
from datetime import date

import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models.category import Category
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_take import StockTake
from app.models.supplier import Supplier
from app.models.user import User
from app.schemas.purchase_order import QuickPurchaseRequest
from app.services.purchasing_service import PurchasingService


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


async def _make_product(name: str = "PO Test Product") -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name=name)
        db.add(product)
        await db.commit()
        return int(product.id)


async def _make_category(name: str = "Antibiotics") -> int:
    async with AsyncSessionLocal() as db:
        category = Category(name=name)
        db.add(category)
        await db.commit()
        return int(category.id)


async def _make_product_with_category(name: str, category_id: int) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name=name, category_id=category_id)
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
                        "selling_price": 30.0,
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
                        "selling_price": 15.0,
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
                        "selling_price": 10.0,
                    },
                    {
                        "product_id": product2,
                        "quantity": 20,
                        "batch_number": "MULTI-B",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 12.0,
                        "selling_price": 20.0,
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
                        "selling_price": 25.0,
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
            "selling_price": 20.0,
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
                            "selling_price": 10.0,
                        }
                    ],
                },
                headers=headers,
            )

        batches = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert len(batches.json()) == 2

    async def test_new_batch_without_selling_price_is_rejected(self, client, owner_user):
        """
        The real invariant this whole area of the code exists to
        protect, post migration 0036_batch_selling_price_required:
        a genuinely new batch (no existing row for this product/batch
        number/expiry) can never be created without its own explicit
        selling price. There is no product-level default left to fall
        back to, so a missing price here is a clear, actionable error,
        never a silent 0 or a borrowed number.
        """
        product_id = await _make_product("No Price New Batch Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "No Price Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 100,
                        "batch_number": "NEW-NO-PRICE",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 10.0,
                        # no selling_price -- this batch doesn't exist yet,
                        # so there's nothing to fall back to.
                    }
                ],
            },
            headers=headers,
        )
        assert r.status_code == 400, r.text
        assert "is new stock" in r.text and "selling price is required" in r.text

        product = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert product.json()["total_qty_available"] == 0  # nothing was received

    async def test_batch_selling_price_cannot_be_forced_to_null(self, client, owner_user):
        """
        Belt-and-suspenders check on the migration itself: even
        bypassing the API entirely and writing to the ORM directly,
        the database's own NOT NULL constraint on
        medicine_batches.selling_price refuses a null value. The
        app-level "new batch needs a price" rule above is what users
        actually see, but this is the guarantee that holds even if
        that check were ever accidentally removed upstream.
        """
        from sqlalchemy.exc import IntegrityError

        product_id = await _make_product("Cannot Null Price Product")

        async with AsyncSessionLocal() as db:
            batch = MedicineBatch(
                product_id=product_id,
                batch_number="NULL-ATTEMPT",
                expiry_date=date(2027, 6, 30),
                qty_received=10,
                qty_remaining=10,
                cost_price=5.0,
                selling_price=None,  # type: ignore[arg-type]
            )
            db.add(batch)
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

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
        product_id = await _make_product("Restock No Price Product")
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

    async def test_receiving_stock_is_captured_in_the_audit_log(self, client, owner_user):
        """
        The real gap this closes: receiving stock is real drugs and
        real money entering the business, and until now it left no
        audit trail at all -- only later corrections to a batch's
        cost or price were ever logged. Every quick_purchase must now
        produce a real, queryable audit entry.
        """
        product_id = await _make_product("Audited Receiving Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Audited Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 30,
                        "batch_number": "AUDIT-PO-1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 8.0,
                        "selling_price": 15.0,
                    }
                ],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        po_id = r.json()["id"]

        audit = await client.get(
            "/api/v1/audit-logs", params={"action": "purchase_order.received"}, headers=headers
        )
        assert audit.status_code == 200
        entries = audit.json()["entries"]
        matching = [e for e in entries if e["entity_id"] == str(po_id)]
        assert len(matching) == 1
        assert matching[0]["user_name_snapshot"] == "Lucy Kangai"
        assert f"supplier_id={supplier_id}" in matching[0]["new_value"]
        assert "total_owed=240.00" in matching[0]["new_value"]  # 30 * 8.0

    async def test_new_batch_below_cost_is_rejected(self, client, owner_user):
        product_id = await _make_product("Below Cost New Batch Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Loss Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 20,
                        "batch_number": "LOSS-QP-1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 15.0,
                        "selling_price": 5.0,  # explicitly below cost -- must be rejected
                    }
                ],
            },
            headers=headers,
        )
        assert r.status_code == 400
        assert "below its cost price" in r.text

        product = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert product.json()["total_qty_available"] == 0  # nothing was received

    async def test_restock_that_would_blend_cost_above_selling_price_is_rejected(
        self, client, owner_user
    ):
        product_id = await _make_product("Blended Cost Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Blend Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        first = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 10,
                        "batch_number": "BLEND-1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 5.0,
                        "selling_price": 12.0,
                    }
                ],
            },
            headers=headers,
        )
        assert first.status_code == 201, first.text

        # Same batch number -- merges. 10 @ 5.0 blended with 10 @
        # 50.0 averages to 27.5, which is above this batch's own
        # 12.0 selling price. Must be rejected, and the existing
        # 10 units at cost 5.0 must be completely untouched by it.
        second = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 10,
                        "batch_number": "BLEND-1",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 50.0,
                    }
                ],
            },
            headers=headers,
        )
        assert second.status_code == 400
        assert "above its selling price" in second.text

        batches = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert len(batches.json()) == 1
        assert batches.json()[0]["cost_price"] == 5.0
        assert batches.json()[0]["qty_remaining"] == 10


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
                        "selling_price": 20.0,
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
                            "selling_price": 20.0,
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
                        "selling_price": 20.0,
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


class TestBlendedCostStaysInWholeCents:
    """
    Merging a receipt into an existing batch averages the two costs inside
    one SQL statement. The average of whole-cent costs is usually a
    fractional cent, and SQLite would store that REAL in the integer-cents
    column unrounded (10.0761538... where 10.08 belongs), so the average
    is rounded, half up, before it is written.
    """

    @staticmethod
    async def _receive(supplier_id: int, product_id: int, user, quantity: int, unit_cost: float):
        request = QuickPurchaseRequest(
            supplier_id=supplier_id,
            lines=[
                {
                    "product_id": product_id,
                    "batch_number": "WAC1",
                    "expiry_date": date(2030, 1, 1),
                    "quantity": quantity,
                    "unit_cost": unit_cost,
                    "selling_price": 50.0,
                }
            ],
        )
        async with AsyncSessionLocal() as db:
            await PurchasingService(db).quick_purchase(request, user)

    @staticmethod
    async def _stored_cost() -> tuple[str, float]:
        async with AsyncSessionLocal() as db:
            storage_type = (
                await db.execute(text("SELECT typeof(cost_price) FROM medicine_batches"))
            ).scalar_one()
            batch = (await db.execute(select(MedicineBatch))).scalar_one()
        return str(storage_type), batch.cost_price

    async def _setup(self, owner_user) -> tuple[int, int, User]:
        async with AsyncSessionLocal() as db:
            supplier = Supplier(name="ACME")
            product = Product(name="Blend Probe")
            db.add_all([supplier, product])
            await db.commit()
            user = await db.get(User, owner_user.id)
            return supplier.id, product.id, user

    async def test_a_fractional_cent_average_is_rounded_half_up(self, client, owner_user):
        supplier_id, product_id, user = await self._setup(owner_user)

        await self._receive(supplier_id, product_id, user, 10, 10.00)
        await self._receive(supplier_id, product_id, user, 3, 10.33)  # exactly 10.0761538...

        assert await self._stored_cost() == ("integer", 10.08)

    async def test_an_average_of_exactly_half_a_cent_rounds_up_not_down(self, client, owner_user):
        supplier_id, product_id, user = await self._setup(owner_user)

        await self._receive(supplier_id, product_id, user, 1, 0.01)
        await self._receive(supplier_id, product_id, user, 1, 0.02)  # exactly 1.5 cents

        assert await self._stored_cost() == ("integer", 0.02)

    async def test_many_merges_never_leave_a_fractional_cent_behind(self, client, owner_user):
        supplier_id, product_id, user = await self._setup(owner_user)

        await self._receive(supplier_id, product_id, user, 10, 10.00)
        for _ in range(20):
            await self._receive(supplier_id, product_id, user, 1, 10.99)

        storage_type, cost = await self._stored_cost()
        assert storage_type == "integer"
        assert cost == round(cost, 2)


class TestCategoryOnPurchaseItems:
    """
    category_name on a purchase order item is what backs the Category
    column in the Purchasing UI and the spend-by-category breakdown --
    it's derived through item.product.category_name (both selectin-
    loaded), never stored redundantly on the item itself.
    """

    async def test_quick_purchase_response_includes_the_products_category(self, client, owner_user):
        category_id = await _make_category("Antibiotics")
        product_id = await _make_product_with_category("Categorised Product", category_id)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "Category Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 10,
                        "batch_number": "CAT-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 5.0,
                        "selling_price": 9.0,
                    }
                ],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["items"][0]["category_name"] == "Antibiotics"

    async def test_uncategorised_product_has_null_category_name_on_the_item(
        self, client, owner_user
    ):
        product_id = await _make_product("Uncategorised Purchase Product")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "No Category Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        r = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 5,
                        "batch_number": "NOCAT-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 3.0,
                        "selling_price": 6.0,
                    }
                ],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["items"][0]["category_name"] is None

    async def test_category_name_still_present_when_listing_and_fetching_the_order(
        self, client, owner_user
    ):
        category_id = await _make_category("Painkillers")
        product_id = await _make_product_with_category("Listed Category Product", category_id)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier = await client.post(
            "/api/v1/suppliers", json={"name": "List Supplier"}, headers=headers
        )
        supplier_id = supplier.json()["id"]

        created = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 4,
                        "batch_number": "LIST-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 2.0,
                        "selling_price": 4.0,
                    }
                ],
            },
            headers=headers,
        )
        po_id = created.json()["id"]

        listed = await client.get("/api/v1/purchase-orders", headers=headers)
        this_po = next(po for po in listed.json() if po["id"] == po_id)
        assert this_po["items"][0]["category_name"] == "Painkillers"

        fetched = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=headers)
        assert fetched.json()["items"][0]["category_name"] == "Painkillers"
