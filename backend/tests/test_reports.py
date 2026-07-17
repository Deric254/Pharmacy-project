"""
Report tests. The properties that matter:
  1. Numbers are computed from real ledger data (sales, batches,
     PO items, stock takes), not hardcoded or estimated.
  2. Profit uses the actual batch cost tied to each sale line, not a
     product-level average - proven with two batches at different
     costs feeding one product.
  3. Exports are real, openable files - verified via readback, not
     just "did the endpoint return 200".
"""

import io
from datetime import date, timedelta

import openpyxl
from pypdf import PdfReader

from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.models.supplier import Supplier


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _make_product_with_batch(
    price: float = 10.0, cost: float = 4.0, qty: int = 50, expiry: str = "2027-01-01"
) -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        product = Product(name="Report Test Product", default_selling_price=price)
        db.add(product)
        await db.flush()
        batch = MedicineBatch(
            product_id=product.id,
            batch_number="R1",
            expiry_date=date.fromisoformat(expiry),
            qty_received=qty,
            qty_remaining=qty,
            cost_price=cost,
        )
        db.add(batch)
        await db.flush()
        db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=MovementType.PURCHASE,
                quantity_delta=qty,
                created_by_user_id=None,
            )
        )
        await db.commit()
        return int(product.id), int(batch.id)


class TestSalesSummaryAndProfit:
    async def test_sales_summary_totals_match_real_sales(self, client, owner_user, employee_user):
        product_id, _ = await _make_product_with_batch(price=10.0)
        employee_token = await _login(client, "joe", "pass1234")
        owner_token = await _login(client, "lucy", "S3curePass!")

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 2}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 3}],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )

        today = date.today().isoformat()
        r = await client.get(
            f"/api/v1/reports/sales?start_date={today}&end_date={today}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200
        assert r.json()["total_revenue"] == 50.0
        assert r.json()["total_sale_count"] == 2

    async def test_profit_uses_actual_batch_cost_per_line(self, client, owner_user, employee_user):
        """
        Two batches of the SAME product at different costs -- profit
        must reflect the real cost of whichever batch FEFO actually
        sold from, not a flat product-level cost assumption.
        """
        async with AsyncSessionLocal() as db:
            product = Product(name="Dual Cost Product", default_selling_price=10.0)
            db.add(product)
            await db.flush()
            cheap_batch = MedicineBatch(
                product_id=product.id,
                batch_number="CHEAP",
                expiry_date=date(2026, 8, 1),  # nearer expiry, FEFO picks this first
                qty_received=5,
                qty_remaining=5,
                cost_price=3.0,
            )
            expensive_batch = MedicineBatch(
                product_id=product.id,
                batch_number="EXPENSIVE",
                expiry_date=date(2027, 1, 1),
                qty_received=5,
                qty_remaining=5,
                cost_price=7.0,
            )
            db.add_all([cheap_batch, expensive_batch])
            await db.commit()
            product_id = int(product.id)

        employee_token = await _login(client, "joe", "pass1234")
        owner_token = await _login(client, "lucy", "S3curePass!")

        # Buys 7: 5 from CHEAP (cost 3.0), 2 from EXPENSIVE (cost 7.0)
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 7}],
                "payments": [{"method": "CASH", "amount": 70.0}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )

        today = date.today().isoformat()
        r = await client.get(
            f"/api/v1/reports/profit?start_date={today}&end_date={today}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total_revenue"] == 70.0
        # cost = 5*3.0 + 2*7.0 = 15 + 14 = 29
        assert body["total_cost"] == 29.0
        assert body["total_profit"] == 41.0

    async def test_administrator_cannot_view_profit_only_chemist_owner_can(
        self, client, owner_user, administrator_user
    ):
        """
        Profit is the one number in this system that's deliberately
        owner-only -- matches the original client requirement
        ("ChemistOwner: order approval, profit visibility") as
        distinct from Administrator's broader day-to-day operational
        access. This must hold even though Administrator has the
        general reports.view permission for every other report.
        """
        admin_token = await _login(client, "sam", "AdminPass1")
        owner_token = await _login(client, "lucy", "S3curePass!")
        today = date.today().isoformat()

        admin_r = await client.get(
            f"/api/v1/reports/profit?start_date={today}&end_date={today}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_r.status_code == 403

        owner_r = await client.get(
            f"/api/v1/reports/profit?start_date={today}&end_date={today}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert owner_r.status_code == 200

    async def test_administrator_can_still_view_every_other_report(
        self, client, administrator_user
    ):
        """The profit restriction is specific to profit, not a general
        Administrator report lockout -- confirm the general reports.view
        grant Administrator holds still works for everything else."""
        admin_token = await _login(client, "sam", "AdminPass1")
        today = date.today().isoformat()

        sales = await client.get(
            f"/api/v1/reports/sales?start_date={today}&end_date={today}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert sales.status_code == 200

        expired = await client.get(
            "/api/v1/reports/expired-stock",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert expired.status_code == 200

    async def test_sales_export_produces_real_readable_excel_file(
        self, client, owner_user, employee_user
    ):
        product_id, _ = await _make_product_with_batch(price=15.0)
        employee_token = await _login(client, "joe", "pass1234")
        owner_token = await _login(client, "lucy", "S3curePass!")

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 15.0}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )

        today = date.today().isoformat()
        r = await client.get(
            f"/api/v1/reports/sales?start_date={today}&end_date={today}&export=excel",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")

        workbook = openpyxl.load_workbook(io.BytesIO(r.content))
        sheet = workbook.active
        assert [c.value for c in sheet[1]] == [
            "Period",
            "Sale Count",
            "Total Revenue",
            "Total Discount",
        ]
        assert sheet[2][2].value == 15.0  # Total Revenue for the one sale

    async def test_expired_stock_export_produces_real_readable_pdf(self, client, owner_user):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        await _make_product_with_batch(qty=10, cost=5.0, expiry=yesterday)
        owner_token = await _login(client, "lucy", "S3curePass!")

        r = await client.get(
            "/api/v1/reports/expired-stock?export=pdf",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"

        reader = PdfReader(io.BytesIO(r.content))
        text = reader.pages[0].extract_text()
        assert "Report Test Product" in text

    async def test_export_requires_reports_export_permission(self, client, employee_user):
        # Employee has neither reports.view nor reports.export in this
        # suite's seeded roles -- confirms the base permission gate first.
        token = await _login(client, "joe", "pass1234")
        today = date.today().isoformat()
        r = await client.get(
            f"/api/v1/reports/sales?start_date={today}&end_date={today}&export=excel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


class TestExpiredStockReport:
    async def test_expired_batch_flagged_with_recommendation(self, client, owner_user):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        await _make_product_with_batch(qty=20, cost=3.0, expiry=yesterday)
        token = await _login(client, "lucy", "S3curePass!")

        r = await client.get(
            "/api/v1/reports/expired-stock", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert len(r.json()["entries"]) == 1
        assert r.json()["entries"][0]["value_at_cost"] == 60.0  # 20 * 3.0
        assert "write these off" in r.json()["recommendation"]

    async def test_future_expiry_not_flagged(self, client, owner_user):
        await _make_product_with_batch(qty=20, expiry="2027-01-01")
        token = await _login(client, "lucy", "S3curePass!")

        r = await client.get(
            "/api/v1/reports/expired-stock", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.json()["entries"] == []
        assert "No expired stock" in r.json()["recommendation"]


class TestFastSlowMovers:
    async def test_sold_product_appears_in_fast_movers(self, client, owner_user, employee_user):
        product_id, _ = await _make_product_with_batch(price=5.0, qty=100)
        employee_token = await _login(client, "joe", "pass1234")
        owner_token = await _login(client, "lucy", "S3curePass!")

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 20}],
                "payments": [{"method": "CASH", "amount": 100.0}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )

        r = await client.get(
            "/api/v1/reports/fast-slow-movers", headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert r.status_code == 200
        fast_ids = {m["product_id"] for m in r.json()["fast_movers"]}
        assert product_id in fast_ids

    async def test_never_sold_product_appears_in_never_sold_bucket(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(qty=10)
        token = await _login(client, "lucy", "S3curePass!")

        r = await client.get(
            "/api/v1/reports/fast-slow-movers", headers={"Authorization": f"Bearer {token}"}
        )
        never_sold_ids = {m["product_id"] for m in r.json()["never_sold"]}
        assert product_id in never_sold_ids


class TestReceivingDiscrepancies:
    async def test_variance_from_a_real_po_receipt_appears_in_report(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            supplier = Supplier(name="Discrepancy Test Supplier")
            db.add(supplier)
            await db.flush()
            product = Product(name="Discrepancy Test Product")
            db.add(product)
            await db.commit()
            supplier_id, product_id = int(supplier.id), int(product.id)

        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        po = await client.post(
            "/api/v1/purchase-orders",
            json={
                "supplier_id": supplier_id,
                "items": [
                    {"product_id": product_id, "quantity_ordered": 100, "unit_cost_expected": 5.0}
                ],
            },
            headers=headers,
        )
        po_id = po.json()["id"]
        item_id = po.json()["items"][0]["id"]
        await client.post(f"/api/v1/purchase-orders/{po_id}/send", headers=headers)
        await client.post(f"/api/v1/purchase-orders/{po_id}/mark-in-transit", headers=headers)
        await client.post(
            f"/api/v1/purchase-orders/{po_id}/receive",
            json={
                "lines": [
                    {
                        "item_id": item_id,
                        "batch_number": "SHORT",
                        "expiry_date": "2027-01-01",
                        "quantity_received": 90,
                        "unit_cost_actual": 5.0,
                    }
                ]
            },
            headers=headers,
        )

        r = await client.get("/api/v1/reports/receiving-discrepancies", headers=headers)
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["purchase_order_id"] == po_id
        assert entries[0]["variance"] == -10


class TestStockTakeHistory:
    async def test_closed_stock_take_appears_with_shrinkage(self, client, owner_user):
        product_id, batch_id = await _make_product_with_batch(qty=50, cost=2.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await client.post(
            "/api/v1/stock-takes", json={"product_ids": [product_id]}, headers=headers
        )
        stock_take_id = create_resp.json()["id"]
        item_id = create_resp.json()["items"][0]["id"]
        await client.post(
            f"/api/v1/stock-takes/{stock_take_id}/items/{item_id}/count",
            json={"physical_qty": 48, "reason": "DAMAGED"},  # variance -2, self-approve
            headers=headers,
        )
        await client.post(f"/api/v1/stock-takes/{stock_take_id}/close", headers=headers)

        r = await client.get("/api/v1/reports/stock-take-history", headers=headers)
        assert r.status_code == 200
        entry = next(e for e in r.json()["entries"] if e["stock_take_id"] == stock_take_id)
        assert entry["shrinkage_value"] == 4.0  # 2 units * 2.0 cost
        assert entry["closed_at"] is not None
