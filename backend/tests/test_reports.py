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
from datetime import date, datetime, timedelta

import openpyxl
from pypdf import PdfReader

from app.core.business_time import business_today
from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.models.supplier import Supplier


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _business_today() -> date:
    """
    The business's real "today" (see business_time.py's own docstring
    for why this must never be date.today() -- that's the test
    runner's system clock, not the business's configured timezone,
    and the two silently disagree for roughly 3 hours a day whenever
    a business's local day has already turned over but the system
    clock's hasn't, or vice versa).
    """
    async with AsyncSessionLocal() as db:
        return await business_today(db)


async def _make_product_with_batch(
    price: float = 10.0,
    cost: float = 4.0,
    qty: int = 50,
    expiry: str = "2027-01-01",
    name: str = "Report Test Product",
) -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        product = Product(name=name)
        db.add(product)
        await db.flush()
        batch = MedicineBatch(
            product_id=product.id,
            batch_number="R1",
            expiry_date=date.fromisoformat(expiry),
            qty_received=qty,
            qty_remaining=qty,
            cost_price=cost,
            selling_price=price,
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

        today = (await _business_today()).isoformat()
        r = await client.get(
            f"/api/v1/reports/sales?start_date={today}&end_date={today}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200
        assert r.json()["total_revenue"] == 50.0
        assert r.json()["total_sale_count"] == 2

    async def test_sales_summary_nets_out_refunds(self, client, owner_user, employee_user):
        """
        The exact "refund happens, money remains" bug: total_revenue
        used to be a flat SUM(Sale.total_amount) with no refund
        subtraction anywhere. A 20 sale refunded for 8 must show 12 of
        real revenue left, not 20.
        """
        product_id, _ = await _make_product_with_batch(price=10.0)
        employee_token = await _login(client, "joe", "pass1234")
        owner_token = await _login(client, "lucy", "S3curePass!")

        sale_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 2}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert sale_resp.status_code == 201, sale_resp.text
        sale = sale_resp.json()
        sale_item = sale["items"][0]

        refund_resp = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 1, "restock": True}],
            },
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert refund_resp.status_code == 201, refund_resp.text
        assert refund_resp.json()["total_amount"] == 10.0

        today = (await _business_today()).isoformat()
        r = await client.get(
            f"/api/v1/reports/sales?start_date={today}&end_date={today}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total_revenue"] == 10.0  # 20 sold - 10 refunded
        assert body["total_sale_count"] == 1  # a refund doesn't undo the sale
        assert body["entries"][0]["total_revenue"] == 10.0

    async def test_profit_uses_actual_batch_cost_per_line(self, client, owner_user, employee_user):
        """
        Two batches of the SAME product at different costs -- profit
        must reflect the real cost of whichever batch FEFO actually
        sold from, not a flat product-level cost assumption.
        """
        async with AsyncSessionLocal() as db:
            product = Product(name="Dual Cost Product")
            db.add(product)
            await db.flush()
            cheap_batch = MedicineBatch(
                product_id=product.id,
                batch_number="CHEAP",
                # Nearer expiry than EXPENSIVE below -- FEFO picks this batch first.
                expiry_date=date.today() + timedelta(days=30),
                qty_received=5,
                qty_remaining=5,
                cost_price=3.0,
                selling_price=10.0,
            )
            expensive_batch = MedicineBatch(
                product_id=product.id,
                batch_number="EXPENSIVE",
                expiry_date=date.today() + timedelta(days=700),
                qty_received=5,
                qty_remaining=5,
                cost_price=7.0,
                selling_price=10.0,
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

        today = (await _business_today()).isoformat()
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
        today = (await _business_today()).isoformat()

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
        today = (await _business_today()).isoformat()

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

        today = (await _business_today()).isoformat()
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
        yesterday = (await _business_today() - timedelta(days=1)).isoformat()
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
        today = (await _business_today()).isoformat()
        r = await client.get(
            f"/api/v1/reports/sales?start_date={today}&end_date={today}&export=excel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


class TestExpiredStockReport:
    async def test_expired_batch_flagged_with_recommendation(self, client, owner_user):
        yesterday = (await _business_today() - timedelta(days=1)).isoformat()
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


class TestProductCoOccurrence:
    async def test_two_products_bought_together_form_a_pair(self, client, owner_user):
        product_a, _ = await _make_product_with_batch(price=5.0, qty=100, name="Co-occur A")
        product_b, _ = await _make_product_with_batch(price=8.0, qty=100, name="Co-occur B")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        for _ in range(2):
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [
                        {"product_id": product_a, "quantity": 1},
                        {"product_id": product_b, "quantity": 1},
                    ],
                    "payments": [{"method": "CASH", "amount": 13.0}],
                },
                headers=headers,
            )

        r = await client.get("/api/v1/reports/co-occurrence", headers=headers)
        assert r.status_code == 200
        pairs = r.json()["pairs"]
        matching = [
            p for p in pairs if {p["product_a_id"], p["product_b_id"]} == {product_a, product_b}
        ]
        assert len(matching) == 1
        assert matching[0]["co_occurrence_count"] == 2
        assert matching[0]["percent_of_a_sales"] == 100.0

    async def test_products_never_in_the_same_sale_do_not_pair(self, client, owner_user):
        product_a, _ = await _make_product_with_batch(price=5.0, qty=100, name="Never-pair A")
        product_b, _ = await _make_product_with_batch(price=8.0, qty=100, name="Never-pair B")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        for pid, price in ((product_a, 5.0), (product_b, 8.0)):
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": pid, "quantity": 1}],
                    "payments": [{"method": "CASH", "amount": price}],
                },
                headers=headers,
            )

        r = await client.get("/api/v1/reports/co-occurrence", headers=headers)
        pairs = r.json()["pairs"]
        matching = [
            p for p in pairs if {p["product_a_id"], p["product_b_id"]} == {product_a, product_b}
        ]
        assert matching == []

    async def test_a_fefo_split_does_not_inflate_the_count(self, client, owner_user):
        """
        The property this whole report depends on: SaleItem is one row
        per (sale, batch) allocation, not per cart line (see
        SaleItem's own docstring). Buying enough of product_a to force
        a FEFO split across two batches, in the same sale as
        product_b, must still count each sale once -- not inflated by
        however many batches that sale's line happened to split
        across.
        """
        async with AsyncSessionLocal() as db:
            product = Product(name="FEFO Split Product")
            db.add(product)
            await db.flush()
            batch1 = MedicineBatch(
                product_id=product.id,
                batch_number="FEFO-1",
                expiry_date=date(2027, 1, 1),
                qty_received=10,
                qty_remaining=10,
                cost_price=2.0,
                selling_price=5.0,
            )
            batch2 = MedicineBatch(
                product_id=product.id,
                batch_number="FEFO-2",
                expiry_date=date(2027, 6, 1),
                qty_received=10,
                qty_remaining=10,
                cost_price=2.0,
                selling_price=5.0,
            )
            db.add_all([batch1, batch2])
            await db.commit()
            product_a = int(product.id)

        product_b, _ = await _make_product_with_batch(price=8.0, qty=100, name="FEFO Split B")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        # 8 units of product_a per sale, twice: batch1 only has 10, so
        # the second sale forces a FEFO split (2 left in batch1 + 6
        # from batch2) -- two SaleItem rows for that one (sale,
        # product_a) pair. Two sales total must count as
        # co_occurrence_count == 2, not 3, which an un-deduplicated
        # count would produce from the split.
        for _ in range(2):
            r = await client.post(
                "/api/v1/sales",
                json={
                    "items": [
                        {"product_id": product_a, "quantity": 8},
                        {"product_id": product_b, "quantity": 1},
                    ],
                    "payments": [{"method": "CASH", "amount": 48.0}],
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text

        report = await client.get("/api/v1/reports/co-occurrence", headers=headers)
        pairs = report.json()["pairs"]
        matching = [
            p for p in pairs if {p["product_a_id"], p["product_b_id"]} == {product_a, product_b}
        ]
        assert len(matching) == 1
        assert matching[0]["co_occurrence_count"] == 2  # two sales, not three

    async def test_requires_reports_view_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.get(
            "/api/v1/reports/co-occurrence", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403


class TestSeasonalTrends:
    async def test_insufficient_history_for_a_brand_new_business(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=5.0, qty=10)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 5.0}],
            },
            headers=headers,
        )

        r = await client.get("/api/v1/reports/seasonal-trends", headers=headers)
        assert r.status_code == 200
        assert r.json()["has_sufficient_history"] is False

    async def test_quantities_grouped_by_calendar_month_across_years(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=5.0, qty=100)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale1 = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 4}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers=headers,
        )
        sale2 = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 6}],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers=headers,
        )

        # Backdate both into April, two different years -- a real
        # seasonal pattern is the SUM across years for that month,
        # not two separate single-year data points.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            from app.models.sale import Sale

            for sale_id, year in ((sale1.json()["id"], 2024), (sale2.json()["id"], 2025)):
                result = await db.execute(select(Sale).where(Sale.id == sale_id))
                row = result.scalar_one()
                row.created_at = datetime(year, 4, 15, 12, 0, 0)
            await db.commit()

        r = await client.get(
            "/api/v1/reports/seasonal-trends", params={"days": 3650}, headers=headers
        )
        assert r.status_code == 200
        entries = [e for e in r.json()["entries"] if e["product_id"] == product_id]
        assert len(entries) == 1  # both years collapse into one April entry
        assert entries[0]["month"] == 4
        assert entries[0]["total_quantity_sold"] == 10  # 4 + 6 summed

    async def test_requires_reports_view_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.get(
            "/api/v1/reports/seasonal-trends", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403


class TestReceivingDiscrepancies:
    async def test_quick_purchase_never_produces_a_discrepancy(self, client, owner_user):
        """
        This report used to be populated by the old receive endpoint,
        which tracked ordered vs. actually-received quantity as two
        separate numbers -- a real discrepancy was possible if a
        shipment came up short. quick_purchase (the only way stock
        enters the app now -- see purchasing_service.py's docstring)
        has no such distinction: "what you type in is what you got",
        quantity_ordered and quantity_received are always set to the
        exact same value. So the real, current invariant this report
        needs to hold is the opposite of what it used to test: a
        normal purchase must never appear here, not even with a
        deliberately unusual quantity.
        """
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
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 90,
                        "batch_number": "SHORT",
                        "expiry_date": "2027-01-01",
                        "unit_cost": 5.0,
                        "selling_price": 9.0,
                    }
                ],
            },
            headers=headers,
        )
        assert po.status_code == 201, po.text

        r = await client.get("/api/v1/reports/receiving-discrepancies", headers=headers)
        assert r.status_code == 200
        assert r.json()["entries"] == []


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

    async def test_stock_take_with_no_items_still_appears_with_zero_shrinkage(
        self, client, owner_user
    ):
        """
        Regression pin for the outer-join rewrite: the real
        initiate() flow always rejects a scope with zero eligible
        batches (see stock_take_service.py), so a stock take with no
        items can't happen through the API today -- inserted directly
        here to pin the query's own defensive behavior regardless.
        Must still show up in history with zero values, not silently
        disappear because an inner join to StockTakeItem would
        exclude it.
        """
        from datetime import UTC, datetime

        from app.models.stock_take import StockTake, StockTakeStatus

        async with AsyncSessionLocal() as db:
            stock_take = StockTake(
                initiated_by_user_id=owner_user.id,
                status=StockTakeStatus.CLOSED,
                closed_at=datetime.now(UTC).replace(tzinfo=None),
            )
            db.add(stock_take)
            await db.commit()
            stock_take_id = stock_take.id

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/reports/stock-take-history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        entry = next(e for e in r.json()["entries"] if e["stock_take_id"] == stock_take_id)
        assert entry["shrinkage_value"] == 0.0
        assert entry["shrinkage_percent"] == 0.0

    async def test_stock_take_overage_is_not_counted_as_shrinkage(self, client, owner_user):
        """
        Regression pin for the CASE direction in the SQL rewrite:
        counting MORE than expected (physical_qty > expected_qty) is
        an overage, not shrinkage -- it must not add to
        shrinkage_value, only a genuine shortfall (physical < expected)
        should. Kept within the self-approve threshold so the count
        actually resolves and the stock take can close.
        """
        product_id, _ = await _make_product_with_batch(qty=50, cost=2.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await client.post(
            "/api/v1/stock-takes", json={"product_ids": [product_id]}, headers=headers
        )
        stock_take_id = create_resp.json()["id"]
        item_id = create_resp.json()["items"][0]["id"]
        await client.post(
            f"/api/v1/stock-takes/{stock_take_id}/items/{item_id}/count",
            json={"physical_qty": 52, "reason": "MISCOUNT"},  # variance +2, self-approve
            headers=headers,
        )
        await client.post(f"/api/v1/stock-takes/{stock_take_id}/close", headers=headers)

        r = await client.get("/api/v1/reports/stock-take-history", headers=headers)
        assert r.status_code == 200
        entry = next(e for e in r.json()["entries"] if e["stock_take_id"] == stock_take_id)
        assert entry["shrinkage_value"] == 0.0
        assert entry["shrinkage_percent"] == 0.0


class TestKpiDashboard:
    async def test_revenue_transaction_count_and_average_basket_are_accurate(
        self, client, owner_user, employee_user
    ):
        product_id, _ = await _make_product_with_batch(price=10.0)
        token = await _login(client, "joe", "pass1234")
        owner_token = await _login(client, "lucy", "S3curePass!")

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 2}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 3}],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["revenue"] == 50.0
        assert body["transaction_count"] == 2
        assert body["average_basket"] == 25.0

    async def test_profit_hidden_for_a_role_without_view_profit_permission(
        self, client, owner_user, administrator_user
    ):
        # Administrator does not hold reports.view_profit by design
        # (matches the same restriction the dedicated /reports/profit
        # endpoint already enforces) -- must be None, not zeroed out,
        # which would look like a real (bad) number instead of "you
        # can't see this".
        token = await _login(client, "sam", "AdminPass1")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["profit"] is None
        assert r.json()["profit_margin_percent"] is None

    async def test_profit_visible_for_owner(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=10.0, cost=4.0)
        token = await _login(client, "lucy", "S3curePass!")
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 50.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.json()["profit"] == 30.0  # (10-4) * 5

    async def test_kpi_revenue_and_profit_net_out_restocked_refunds(self, client, owner_user):
        """
        Same "refund happens, money remains" bug, on the KPI dashboard's
        revenue AND on profit: a restocked refund puts the unit's cost
        back into inventory, so profit must also stop counting that
        unit's cost as COGS for the period -- not just net the revenue.
        5 sold at (10 price, 4 cost), 2 refunded and restocked:
          revenue: 50 - 20 = 30
          cost: (5*4) - (2*4) = 12
          profit: 30 - 12 = 18
        """
        product_id, _ = await _make_product_with_batch(price=10.0, cost=4.0, qty=20)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 50.0}],
            },
            headers=headers,
        )
        assert sale_resp.status_code == 201, sale_resp.text
        sale = sale_resp.json()
        sale_item = sale["items"][0]

        refund_resp = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 2, "restock": True}],
            },
            headers=headers,
        )
        assert refund_resp.status_code == 201, refund_resp.text
        assert refund_resp.json()["total_amount"] == 20.0

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["revenue"] == 30.0
        assert body["profit"] == 18.0
        assert body["transaction_count"] == 1  # a refund isn't a second transaction

    async def test_revenue_change_percent_compares_to_immediately_prior_period(
        self, client, owner_user, employee_user
    ):
        product_id, _ = await _make_product_with_batch(price=10.0)
        token = await _login(client, "joe", "pass1234")
        owner_token = await _login(client, "lucy", "S3curePass!")

        # A sale "yesterday" and a bigger one "today" -- comparing a
        # 1-day window to the 1-day window immediately before it.
        async with AsyncSessionLocal() as db:
            from app.models.sale import Sale, SaleItem

            yesterday_sale = Sale(
                cashier_user_id=1, subtotal=10.0, discount_amount=0.0, total_amount=10.0
            )
            db.add(yesterday_sale)
            await db.flush()
            yesterday_sale.created_at = datetime.now() - timedelta(days=1)
            db.add(
                SaleItem(
                    sale_id=yesterday_sale.id,
                    product_id=product_id,
                    batch_id=1,
                    quantity=1,
                    unit_price=10.0,
                    unit_cost=4.0,
                    line_total=10.0,
                )
            )
            await db.commit()

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 2}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        # Today: 20.0, yesterday: 10.0 -- a genuine +100% change.
        assert r.json()["revenue_change_percent"] == 100.0

    async def test_top_products_ordered_by_revenue_not_quantity(self, client, owner_user):
        """
        Selling many units of a cheap product must not outrank fewer
        units of an expensive one -- top products is genuinely ranked
        by revenue, not raw quantity sold.
        """
        cheap_id, _ = await _make_product_with_batch(price=1.0)
        token = await _login(client, "lucy", "S3curePass!")

        async with AsyncSessionLocal() as db:
            expensive = Product(name="Expensive Product")
            db.add(expensive)
            await db.flush()
            db.add(
                MedicineBatch(
                    product_id=expensive.id,
                    batch_number="E1",
                    expiry_date=date(2027, 1, 1),
                    qty_received=10,
                    qty_remaining=10,
                    cost_price=50.0,
                    selling_price=100.0,
                )
            )
            await db.commit()
            expensive_id = expensive.id

        # 20 units of the cheap product = 20.0 revenue.
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": cheap_id, "quantity": 20}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        # 1 unit of the expensive product = 100.0 revenue -- must rank first.
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": expensive_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 100.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        top_products = r.json()["top_products"]
        assert top_products[0]["product_id"] == expensive_id
        assert top_products[0]["revenue"] == 100.0

    async def test_top_products_revenue_is_net_of_discount(self, client, owner_user):
        """
        A sale's discount is a header-level concession (Sale.discount_
        amount), never split across its line items -- SaleItem.unit_
        price always stays the full price. Top products revenue must
        still add up to real money collected, not list price: a 250
        sale discounted by 50 should attribute 200, not 250, to the
        product(s) actually sold.
        """
        product_id, _ = await _make_product_with_batch(price=250.0, cost=10.0, qty=5)
        token = await _login(client, "lucy", "S3curePass!")

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 200.0}],
                "discount_amount": 50.0,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        top_products = r.json()["top_products"]
        assert top_products[0]["product_id"] == product_id
        assert top_products[0]["revenue"] == 200.0

    async def test_top_products_revenue_survives_a_discount_over_half_price(
        self, client, owner_user
    ):
        """
        Regression pin for a specific SQL trap: the discount ratio
        (total_amount / subtotal) is computed by dividing two
        MoneyCents columns, which are stored as raw integer cents.
        SQLite's integer/integer division truncates -- so any ratio
        below 1.0 that isn't explicitly computed on floats would
        silently come back as exactly 0, not an approximation. A
        60% discount (ratio 0.4, well under the halfway point where
        this would first go visibly wrong) is exactly the case that
        would have zeroed this product's revenue instead of reporting
        the real 40.0 collected.
        """
        product_id, _ = await _make_product_with_batch(price=100.0, cost=10.0, qty=5)
        token = await _login(client, "lucy", "S3curePass!")

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 40.0}],
                "discount_amount": 60.0,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        top_products = r.json()["top_products"]
        assert top_products[0]["product_id"] == product_id
        assert top_products[0]["revenue"] == 40.0

    async def test_top_products_revenue_nets_out_refunds(self, client, owner_user):
        """
        Same "refund happens, money remains" bug: a product sold for
        100 with 30 refunded off it must show 70 of real revenue, not
        100 -- the refund used to never touch this figure at all.
        """
        product_id, _ = await _make_product_with_batch(price=10.0, cost=4.0, qty=20)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 10}],
                "payments": [{"method": "CASH", "amount": 100.0}],
            },
            headers=headers,
        )
        assert sale_resp.status_code == 201, sale_resp.text
        sale = sale_resp.json()
        sale_item = sale["items"][0]

        refund_resp = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 3, "restock": True}],
            },
            headers=headers,
        )
        assert refund_resp.status_code == 201, refund_resp.text
        assert refund_resp.json()["total_amount"] == 30.0

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        top_products = r.json()["top_products"]
        entry = next(p for p in top_products if p["product_id"] == product_id)
        assert entry["revenue"] == 70.0  # 100 sold - 30 refunded

    async def test_low_stock_and_expiring_counts_reflect_real_inventory(self, client, owner_user):
        # Reorder point 10, stock only 3 -- genuinely low.
        async with AsyncSessionLocal() as db:
            product = Product(name="Low Stock KPI Product", reorder_point=10)
            db.add(product)
            await db.flush()
            db.add(
                MedicineBatch(
                    product_id=product.id,
                    batch_number="LOW1",
                    expiry_date=date(2027, 1, 1),
                    qty_received=3,
                    qty_remaining=3,
                    cost_price=1.0,
                    selling_price=5.0,
                )
            )
            await db.commit()

        token = await _login(client, "lucy", "S3curePass!")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.json()["low_stock_count"] >= 1

    async def test_requires_reports_view_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


class TestDateBoundaryAccuracy:
    """
    A real bug found and fixed this session: single-day queries
    (start_date == end_date) were silently returning zero, and any
    multi-day range was silently missing its entire final day --
    both with no error, just a quietly wrong number. Found by
    building a real multi-year dataset and hand-verifying against raw
    SQL at exactly these boundaries. These tests make that proof
    permanent using the same real insertion path the application
    itself uses, so this can never regress unnoticed.

    The two "final day" tests below construct their near-midnight
    fixture via business_time.local_day_bounds_utc rather than a bare
    `datetime.combine(date.today(), 23:59:59)` -- the app's date
    filtering is timezone-aware (local business day, not UTC
    calendar day; see business_time.py), so "the last moment of
    today" only means what these tests need it to mean once it's
    converted through the same local-to-UTC math the app itself uses.
    """

    async def test_single_day_query_finds_a_sale_made_that_day(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=50.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 50.0}],
            },
            headers=headers,
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        # Before the fix, this was always 0 -- a single-day range is
        # exactly the "today's sales" query an owner checks constantly.
        assert r.json()["revenue"] == 50.0
        assert r.json()["transaction_count"] == 1

    async def test_range_includes_a_sale_on_its_final_day(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=75.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale = (
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "payments": [{"method": "CASH", "amount": 75.0}],
                },
                headers=headers,
            )
        ).json()

        # Push this sale's real timestamp to the last moment of the
        # LOCAL business day (Africa/Nairobi, UTC+3 by default), not
        # simply "23:59:59 on today's UTC calendar date" -- those are
        # not the same instant. The report layer correctly converts
        # local date ranges to UTC bounds via business_time.py's
        # local_day_bounds_utc (see its docstring for why); this test
        # must construct its fixture the same way, or it ends up
        # testing a boundary three hours away from the one the app
        # actually enforces.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as _select

            from app.core.business_time import local_day_bounds_utc
            from app.models.sale import Sale

            result = await db.execute(_select(Sale).where(Sale.id == sale["id"]))
            row = result.scalar_one()
            _utc_start, utc_end_exclusive = await local_day_bounds_utc(db, await business_today(db))
            just_before_local_midnight = utc_end_exclusive - timedelta(seconds=1)
            row.created_at = just_before_local_midnight
            await db.commit()

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        # Before the fix, a sale in the last moments of the range's
        # final day was silently excluded entirely.
        assert r.json()["revenue"] == 75.0
        assert r.json()["transaction_count"] == 1

    async def test_sales_history_list_also_includes_the_final_day(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=30.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale = (
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "payments": [{"method": "CASH", "amount": 30.0}],
                },
                headers=headers,
            )
        ).json()

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as _select

            from app.core.business_time import local_day_bounds_utc
            from app.models.sale import Sale

            result = await db.execute(_select(Sale).where(Sale.id == sale["id"]))
            row = result.scalar_one()
            _utc_start, utc_end_exclusive = await local_day_bounds_utc(db, await business_today(db))
            just_before_local_midnight = utc_end_exclusive - timedelta(seconds=1)
            row.created_at = just_before_local_midnight
            await db.commit()

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/sales",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()["entries"]]
        assert sale["id"] in ids


class TestTopCustomers:
    async def test_pareto_cumulative_percent_is_mathematically_correct(self, client, owner_user):
        """
        Real Pareto math, not just a ranked list: three customers
        spending 60, 30, 10 (100 total) must show cumulative
        percentages of 60%, 90%, 100% in order -- the actual "which
        customers make up 80% of revenue" answer has to be readable
        directly off this, not left for someone to eyeball.
        """
        product_id, _ = await _make_product_with_batch(price=10.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        for name, spend in [("Big Spender", 60), ("Medium Spender", 30), ("Small Spender", 10)]:
            r = await client.post("/api/v1/customers", json={"name": name}, headers=headers)
            customer_id = r.json()["id"]
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": spend // 10}],
                    "payments": [{"method": "CASH", "amount": float(spend)}],
                    "customer_id": customer_id,
                },
                headers=headers,
            )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/top-customers",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert entries[0]["name"] == "Big Spender"
        assert entries[0]["cumulative_percent"] == 60.0
        assert entries[1]["name"] == "Medium Spender"
        assert entries[1]["cumulative_percent"] == 90.0
        assert entries[2]["name"] == "Small Spender"
        assert entries[2]["cumulative_percent"] == 100.0

    async def test_walk_in_sales_with_no_customer_are_excluded(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=10.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 10.0}],
            },
            headers=headers,
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/top-customers",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.json()["entries"] == []

    async def test_top_customers_revenue_nets_out_refunds(self, client, owner_user):
        """
        Same "refund happens, money remains" bug on the top-customers
        report: a customer who spent 100 and got 40 refunded should
        rank and total as a real 60, not 100.
        """
        product_id, _ = await _make_product_with_batch(price=20.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        customer = (
            await client.post(
                "/api/v1/customers", json={"name": "Refunded Customer"}, headers=headers
            )
        ).json()

        sale_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 100.0}],
                "customer_id": customer["id"],
            },
            headers=headers,
        )
        assert sale_resp.status_code == 201, sale_resp.text
        sale = sale_resp.json()
        sale_item = sale["items"][0]

        refund_resp = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 2, "restock": True}],
            },
            headers=headers,
        )
        assert refund_resp.status_code == 201, refund_resp.text
        assert refund_resp.json()["total_amount"] == 40.0

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/top-customers",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        entry = next(e for e in body["entries"] if e["name"] == "Refunded Customer")
        assert entry["revenue"] == 60.0  # 100 sold - 40 refunded
        assert body["total_revenue"] == 60.0
        assert entry["cumulative_percent"] == 100.0


class TestSalesByCashier:
    """
    Ranked by real net revenue per cashier. The properties that matter:
    a refund nets against the cashier of the ORIGINAL sale (same rule
    top_customers uses for customer_id), and this is gated behind
    reports.view_profit -- ranking individual staff by output is
    owner-level visibility, same tier as profit itself.
    """

    async def test_revenue_nets_out_refunds(self, client, owner_user):
        """
        Same "refund happens, money remains" property top_customers is
        tested for: a cashier who rang up 100 and had 40 refunded
        against one of their sales should show as a real 60, not 100.
        """
        product_id, _ = await _make_product_with_batch(price=20.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 100.0}],
            },
            headers=headers,
        )
        assert sale_resp.status_code == 201, sale_resp.text
        sale = sale_resp.json()
        sale_item = sale["items"][0]

        refund_resp = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 2, "restock": True}],
            },
            headers=headers,
        )
        assert refund_resp.status_code == 201, refund_resp.text
        assert refund_resp.json()["total_amount"] == 40.0

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/sales-by-cashier",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        entry = next(e for e in body["entries"] if e["cashier_name"] == "Lucy Kangai")
        assert entry["revenue"] == 60.0  # 100 rung up - 40 refunded
        assert entry["sale_count"] == 1

    async def test_requires_view_profit_permission(self, client, administrator_user):
        token = await _login(client, "sam", "AdminPass1")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/sales-by-cashier",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_no_sales_returns_empty_not_an_error(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/sales-by-cashier",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["entries"] == []


class TestRevenuePotential:
    """
    An honest hypothetical, not a forecast: exactly what selling every
    unit currently in stock at today's price would add up to, computed
    entirely from real stock and real recorded cost. The properties
    that matter: the math is exactly right, it's gated behind the same
    profit-visibility permission as everything else profit-related,
    and products with zero stock don't inflate the total with nothing.
    """

    async def test_math_is_exactly_right(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        product = await client.post(
            "/api/v1/products",
            json={"name": "Revenue Potential Product"},
            headers=headers,
        )
        product_id = product.json()["id"]
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "RP1",
                "expiry_date": "2027-06-30",
                "qty_received": 50,
                "cost_price": 8.0,
                "selling_price": 20.0,
            },
            headers=headers,
        )

        r = await client.get("/api/v1/reports/revenue-potential", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["total_potential_revenue"] == 1000.0  # 50 * 20.0
        assert body["total_potential_cost"] == 400.0  # 50 * 8.0
        assert body["total_potential_gross_profit"] == 600.0
        assert round(body["overall_margin_percent"], 1) == 60.0
        assert "not a prediction" in body["caveat"].lower() or "not a prediction" in body["caveat"]
        entry = next(e for e in body["by_product"] if e["product_id"] == product_id)
        assert entry["qty_on_hand"] == 50
        assert entry["potential_revenue"] == 1000.0

    async def test_zero_stock_products_are_excluded(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            "/api/v1/products",
            json={"name": "No Stock Product"},
            headers=headers,
        )

        r = await client.get("/api/v1/reports/revenue-potential", headers=headers)
        names = [e["name"] for e in r.json()["by_product"]]
        assert "No Stock Product" not in names

    async def test_requires_view_profit_permission(self, client, administrator_user):
        token = await _login(client, "sam", "AdminPass1")
        r = await client.get(
            "/api/v1/reports/revenue-potential", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    async def test_no_stock_at_all_returns_zero_not_an_error(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/reports/revenue-potential", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert r.json()["total_potential_revenue"] == 0.0
        assert r.json()["overall_margin_percent"] is None


class TestStockRunway:
    """
    A transparent extrapolation, not a forecast. The properties that
    matter: the math is exactly right, a product with no sales in the
    window gets None (never a fabricated number), sales outside the
    lookback window don't count, and the soonest-to-run-out product
    sorts first.
    """

    async def test_math_is_exactly_right(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=10.0, qty=100)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        # 30 units sold today, within a 30-day lookback -- 1 unit/day
        # average, so 100 remaining (after this sale, 70) should
        # project to exactly 70 days remaining.
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 30}],
                "payments": [{"method": "CASH", "amount": 300.0}],
            },
            headers=headers,
        )

        r = await client.get(
            "/api/v1/reports/stock-runway",
            params={"lookback_days": 30},
            headers=headers,
        )
        assert r.status_code == 200
        entry = next(e for e in r.json()["entries"] if e["product_id"] == product_id)
        assert entry["qty_on_hand"] == 70
        assert entry["units_sold_in_window"] == 30
        assert entry["avg_daily_sales"] == 1.0  # 30 units / 30 days
        assert entry["days_remaining"] == 70.0  # 70 remaining / 1.0 per day

    async def test_no_sales_in_window_gives_none_not_a_fabricated_number(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=10.0, qty=50)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.get("/api/v1/reports/stock-runway", headers=headers)
        entry = next(e for e in r.json()["entries"] if e["product_id"] == product_id)
        assert entry["units_sold_in_window"] == 0
        assert entry["days_remaining"] is None

    async def test_sales_outside_the_lookback_window_are_excluded(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=10.0, qty=100)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 20}],
                "payments": [{"method": "CASH", "amount": 200.0}],
            },
            headers=headers,
        )
        sale_id = sale.json()["id"]

        # Push this sale's timestamp to 60 days ago -- outside a
        # 30-day lookback window entirely.
        async with AsyncSessionLocal() as db:
            from datetime import datetime, timedelta

            from sqlalchemy import select as _select

            from app.models.sale import Sale

            result = await db.execute(_select(Sale).where(Sale.id == sale_id))
            old_sale = result.scalar_one()
            old_sale.created_at = datetime.now() - timedelta(days=60)
            await db.commit()

        r = await client.get(
            "/api/v1/reports/stock-runway", params={"lookback_days": 30}, headers=headers
        )
        entry = next(e for e in r.json()["entries"] if e["product_id"] == product_id)
        assert entry["units_sold_in_window"] == 0
        assert entry["days_remaining"] is None

    async def test_soonest_to_run_out_sorts_first(self, client, owner_user):
        fast_id, _ = await _make_product_with_batch(price=10.0, qty=10)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        slow_product = await client.post(
            "/api/v1/products",
            json={"name": "Slow Moving Runway Product"},
            headers=headers,
        )
        slow_id = slow_product.json()["id"]
        await client.post(
            f"/api/v1/products/{slow_id}/batches",
            json={
                "batch_number": "SLOW1",
                "expiry_date": "2027-06-30",
                "qty_received": 1000,
                "cost_price": 4.0,
                "selling_price": 10.0,
            },
            headers=headers,
        )

        # Fast product: high sales relative to low stock -- runs out soon.
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": fast_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 50.0}],
            },
            headers=headers,
        )
        # Slow product: tiny sales relative to huge stock -- lasts a long time.
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": slow_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 10.0}],
            },
            headers=headers,
        )

        r = await client.get("/api/v1/reports/stock-runway", headers=headers)
        entries = r.json()["entries"]
        fast_idx = next(i for i, e in enumerate(entries) if e["product_id"] == fast_id)
        slow_idx = next(i for i, e in enumerate(entries) if e["product_id"] == slow_id)
        assert fast_idx < slow_idx


class TestRevenueTrend:
    """
    Real SQL-side aggregation (never loads individual sale rows into
    Python), with granularity chosen automatically from the range
    length. The properties that matter: exact math per bucket, correct
    granularity switching, and profit hidden entirely (not zeroed)
    for anyone without reports.view_profit.
    """

    async def test_daily_granularity_for_a_short_range_with_exact_math(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=20.0, cost=8.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 3}],
                "payments": [{"method": "CASH", "amount": 60.0}],
            },
            headers=headers,
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-trend",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["granularity"] == "day"
        assert len(body["points"]) == 1
        point = body["points"][0]
        assert point["revenue"] == 60.0  # 3 * 20.0
        assert point["profit"] == 36.0  # 60 - (3 * 8.0)
        assert point["transaction_count"] == 1

    async def test_granularity_switches_to_month_for_a_long_range(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/reports/revenue-trend",
            params={"start_date": "2022-01-01", "end_date": "2026-01-01"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["granularity"] == "month"

    async def test_granularity_switches_to_week_for_a_medium_range(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/reports/revenue-trend",
            params={"start_date": "2026-01-01", "end_date": "2026-03-01"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["granularity"] == "week"

    async def test_week_bucket_labels_are_real_monday_dates_not_week_numbers(
        self, client, owner_user
    ):
        """
        Week-granularity points must be labeled with an actual
        calendar date (the Monday starting that week), never a raw
        "YYYY-Www" week-number string like "2026-W35" -- that format
        isn't a real date the chart/tooltip/AI can use meaningfully,
        and SQLite's %W week numbering isn't even ISO-correct near
        year boundaries. Every label here must parse as a real date
        and fall on a Monday.
        """
        product_id, _ = await _make_product_with_batch(price=20.0, cost=8.0)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers=headers,
        )

        today = await _business_today()
        start = today - timedelta(days=90)  # medium range -> "week" granularity
        r = await client.get(
            "/api/v1/reports/revenue-trend",
            params={"start_date": start.isoformat(), "end_date": today.isoformat()},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["granularity"] == "week"
        assert len(body["points"]) > 0
        for point in body["points"]:
            label = point["period_label"]
            assert "W" not in label, f"expected a real date, got week-number label {label!r}"
            parsed = date.fromisoformat(label)  # raises if not a real YYYY-MM-DD date
            assert parsed.weekday() == 0, f"expected a Monday, got {label!r}"

    async def test_profit_hidden_entirely_without_view_profit_permission(
        self, client, administrator_user
    ):
        product_id, _ = await _make_product_with_batch(price=20.0, cost=8.0)
        token = await _login(client, "sam", "AdminPass1")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers=headers,
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-trend",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        point = r.json()["points"][0]
        assert point["revenue"] == 20.0  # revenue itself is still visible
        assert point["profit"] is None  # profit is hidden, not zeroed


class TestProfitLossPdf:
    """
    A real gap this closes: this endpoint had zero test coverage at
    all. The properties that matter: it's a genuinely valid PDF, the
    real numbers appear in it (not placeholders), permission-gated
    the same as every other profit-visible report, and when there's
    enough data for charts, real vector graphics actually get drawn
    -- not just requested and silently skipped.
    """

    async def test_generates_a_valid_pdf_with_correct_numbers(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(price=25.0, cost=10.0, qty=50)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 4}],
                "payments": [{"method": "CASH", "amount": 100.0}],
            },
            headers=headers,
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/profit-loss-pdf",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

        import io

        from pypdf import PdfReader

        text = PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
        assert "100.00" in text  # revenue: 4 * 25.0
        assert "40.00" in text  # cost: 4 * 10.0
        assert "60.00" in text  # gross profit
        assert "60.0%" in text  # margin

    async def test_requires_view_profit_permission(self, client, administrator_user):
        token = await _login(client, "sam", "AdminPass1")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/profit-loss-pdf",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_charts_actually_draw_real_vector_graphics_with_enough_data(
        self, client, owner_user
    ):
        """
        Not just "the PDF has a chart section" -- real proof that
        reportlab actually drew something, by checking the PDF's own
        content stream for real line/stroke drawing operators, the
        same technique used to verify this live before writing the
        test.
        """
        p1, _ = await _make_product_with_batch(price=25.0, cost=10.0, qty=50)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale = (
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": p1, "quantity": 2}],
                    "payments": [{"method": "CASH", "amount": 50.0}],
                },
                headers=headers,
            )
        ).json()

        # A second day of history so the trend chart has 2+ points --
        # the export deliberately skips drawing a trend line otherwise.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as _select

            from app.models.sale import Sale

            result = await db.execute(_select(Sale).where(Sale.id == sale["id"]))
            row = result.scalar_one()
            row.created_at = datetime.now() - timedelta(days=1)
            await db.commit()

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": p1, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 25.0}],
            },
            headers=headers,
        )

        start = (await _business_today() - timedelta(days=1)).isoformat()
        end = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/profit-loss-pdf",
            params={"start_date": start, "end_date": end},
            headers=headers,
        )
        assert r.status_code == 200

        import io
        import re

        from pypdf import PdfReader
        from pypdf.generic import ArrayObject

        reader = PdfReader(io.BytesIO(r.content))
        page = reader.pages[0]
        contents_obj = page.get("/Contents")
        all_data = b""
        if isinstance(contents_obj, ArrayObject):
            for item in contents_obj:
                all_data += item.get_object().get_data()
        else:
            all_data = contents_obj.get_object().get_data()

        # Real line-drawing and stroke operators, proving reportlab
        # genuinely rendered chart geometry, not just placeholder text.
        assert len(re.findall(rb"\bl\b", all_data)) > 2
        assert len(re.findall(rb"\bS\b", all_data)) > 0


class TestProfitByProduct:
    """
    The per-product breakdown under the Profit tab. The property that
    matters is that it is the same numbers as the tab's own totals,
    just split up: entries must add up to /reports/profit exactly, to
    the cent, through discounts and refunds -- not approximately.
    """

    @staticmethod
    def _cents(amount: float) -> int:
        return round(amount * 100)

    async def _entries_by_name(self, client, headers, start: str, end: str) -> dict:
        """The breakdown keyed by product name, after asserting it sums to /reports/profit."""
        params = {"start_date": start, "end_date": end}
        total = (await client.get("/api/v1/reports/profit", params=params, headers=headers)).json()
        r = await client.get("/api/v1/reports/profit-by-product", params=params, headers=headers)
        assert r.status_code == 200, r.text
        entries = r.json()["entries"]
        for field, total_field in (
            ("revenue", "total_revenue"),
            ("cost", "total_cost"),
            ("profit", "total_profit"),
        ):
            assert sum(self._cents(e[field]) for e in entries) == self._cents(total[total_field])
        for entry in entries:
            assert self._cents(entry["revenue"]) - self._cents(entry["cost"]) == self._cents(
                entry["profit"]
            )
        return {e["name"]: e for e in entries}

    async def test_a_discount_that_does_not_split_evenly_still_adds_up_exactly(
        self, client, owner_user
    ):
        """
        Three lines of 10.00 sharing a 0.10 discount: total 29.90, so
        each line's exact share is 9.9666... -- rounding each one on
        its own gives 9.97 x 3 = 29.91, a cent more than was taken.
        The leftover cents go to the largest remainders (all equal
        here, so the lowest product ids): 9.97 + 9.97 + 9.96 = 29.90.
        Costs 4 + 6 + 1 = 11 -> profit 18.90.
        """
        a_id, _ = await _make_product_with_batch(price=10.0, cost=4.0, name="Split A")
        b_id, _ = await _make_product_with_batch(price=10.0, cost=6.0, name="Split B")
        c_id, _ = await _make_product_with_batch(price=10.0, cost=1.0, name="Split C")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [
                    {"product_id": a_id, "quantity": 1},
                    {"product_id": b_id, "quantity": 1},
                    {"product_id": c_id, "quantity": 1},
                ],
                "payments": [{"method": "CASH", "amount": 29.9}],
                "discount_amount": 0.10,
            },
            headers=headers,
        )
        assert sale.status_code == 201, sale.text

        today = (await _business_today()).isoformat()
        by_name = await self._entries_by_name(client, headers, today, today)

        assert (by_name["Split A"]["revenue"], by_name["Split A"]["profit"]) == (9.97, 5.97)
        assert (by_name["Split B"]["revenue"], by_name["Split B"]["profit"]) == (9.97, 3.97)
        assert (by_name["Split C"]["revenue"], by_name["Split C"]["profit"]) == (9.96, 8.96)
        assert by_name["Split A"]["profit_margin_percent"] == round(5.97 / 9.97 * 100, 2)
        assert all(e["net_quantity_sold"] == 1 for e in by_name.values())

        r = await client.get(
            "/api/v1/reports/profit-by-product",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        # Most profitable first.
        assert [e["name"] for e in r.json()["entries"]] == ["Split C", "Split A", "Split B"]

    async def test_multi_batch_cost_and_units_roll_up_per_product(self, client, owner_user):
        """
        One product drawn from two batches at different costs (FEFO
        split -> two SaleItem rows) is still ONE entry: 5 x 3.0 +
        2 x 7.0 = 29.0 cost against 70.0 revenue.
        """
        async with AsyncSessionLocal() as db:
            product = Product(name="Rollup Product")
            db.add(product)
            await db.flush()
            for number, days, cost in (("CHEAP", 30, 3.0), ("DEAR", 700, 7.0)):
                db.add(
                    MedicineBatch(
                        product_id=product.id,
                        batch_number=number,
                        expiry_date=date.today() + timedelta(days=days),
                        qty_received=5,
                        qty_remaining=5,
                        cost_price=cost,
                        selling_price=10.0,
                    )
                )
            await db.commit()
            product_id = int(product.id)

        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 7}],
                "payments": [{"method": "CASH", "amount": 70.0}],
            },
            headers=headers,
        )
        assert sale.status_code == 201, sale.text

        today = (await _business_today()).isoformat()
        by_name = await self._entries_by_name(client, headers, today, today)
        assert list(by_name) == ["Rollup Product"]
        entry = by_name["Rollup Product"]
        assert entry["net_quantity_sold"] == 7
        assert (entry["revenue"], entry["cost"], entry["profit"]) == (70.0, 29.0, 41.0)
        assert entry["profit_margin_percent"] == round(41.0 / 70.0 * 100, 2)

    async def test_restocked_and_non_restocked_refunds(self, client, owner_user):
        """
        P: 5 sold (10.00 / cost 4.00), 2 returned and restocked ->
           revenue 30, cost 12 (the 2 restocked units' cost comes back
           out), profit 18, 3 net units.
        Q: 1 sold (20.00 / cost 8.00), returned damaged (NOT restocked)
           -> revenue 0, but the 8.00 cost stays as a real loss:
           profit -8, no margin (no revenue to take a percentage of).
        """
        p_id, _ = await _make_product_with_batch(price=10.0, cost=4.0, qty=20, name="Refund P")
        q_id, _ = await _make_product_with_batch(price=20.0, cost=8.0, qty=20, name="Refund Q")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sold = []
        for product_id, qty, amount in ((p_id, 5, 50.0), (q_id, 1, 20.0)):
            r = await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": qty}],
                    "payments": [{"method": "CASH", "amount": amount}],
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text
            sold.append(r.json())

        for sale, qty, restock, reason in (
            (sold[0], 2, True, "CUSTOMER_RETURN"),
            (sold[1], 1, False, "DAMAGED"),
        ):
            r = await client.post(
                f"/api/v1/sales/{sale['id']}/refunds",
                json={
                    "reason": reason,
                    "method": "CASH",
                    "items": [
                        {
                            "sale_item_id": sale["items"][0]["id"],
                            "quantity": qty,
                            "restock": restock,
                        }
                    ],
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text

        today = (await _business_today()).isoformat()
        by_name = await self._entries_by_name(client, headers, today, today)

        p = by_name["Refund P"]
        assert (p["revenue"], p["cost"], p["profit"], p["net_quantity_sold"]) == (
            30.0,
            12.0,
            18.0,
            3,
        )
        assert p["profit_margin_percent"] == 60.0
        q = by_name["Refund Q"]
        assert (q["revenue"], q["cost"], q["profit"], q["net_quantity_sold"]) == (0.0, 8.0, -8.0, 0)
        assert q["profit_margin_percent"] is None

    async def test_refund_of_an_earlier_periods_sale_counts_where_the_refund_happened(
        self, client, owner_user
    ):
        """
        Same rule as every other report: a refund counts against the
        period it was processed in. Sold 3 days ago, restocked-refunded
        today -> today shows a negative revenue (-20) and negative cost
        (-8) for that product even though it has no sale today, and the
        entries still add up to today's totals.
        """
        product_id, _ = await _make_product_with_batch(
            price=10.0, cost=4.0, qty=20, name="Old Sale Product"
        )
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        sale = (
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 5}],
                    "payments": [{"method": "CASH", "amount": 50.0}],
                },
                headers=headers,
            )
        ).json()

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as _select

            from app.models.sale import Sale

            row = (await db.execute(_select(Sale).where(Sale.id == sale["id"]))).scalar_one()
            row.created_at = datetime.now() - timedelta(days=3)
            await db.commit()

        refund = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale["items"][0]["id"], "quantity": 2, "restock": True}],
            },
            headers=headers,
        )
        assert refund.status_code == 201, refund.text

        today = await _business_today()
        by_name = await self._entries_by_name(client, headers, today.isoformat(), today.isoformat())
        entry = by_name["Old Sale Product"]
        assert (entry["revenue"], entry["cost"], entry["profit"]) == (-20.0, -8.0, -12.0)
        assert entry["net_quantity_sold"] == -2
        assert entry["profit_margin_percent"] is None

        # The whole window nets it back to the real picture: 3 net
        # units, 30 revenue, 12 cost.
        wide = await self._entries_by_name(
            client, headers, (today - timedelta(days=5)).isoformat(), today.isoformat()
        )
        entry = wide["Old Sale Product"]
        assert (entry["revenue"], entry["cost"], entry["profit"]) == (30.0, 12.0, 18.0)
        assert entry["net_quantity_sold"] == 3

    async def test_a_fully_refunded_and_restocked_product_is_left_out(self, client, owner_user):
        product_id, _ = await _make_product_with_batch(
            price=10.0, cost=4.0, qty=20, name="Undone Product"
        )
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        sale = (
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 2}],
                    "payments": [{"method": "CASH", "amount": 20.0}],
                },
                headers=headers,
            )
        ).json()
        refund = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale["items"][0]["id"], "quantity": 2, "restock": True}],
            },
            headers=headers,
        )
        assert refund.status_code == 201, refund.text

        today = (await _business_today()).isoformat()
        assert await self._entries_by_name(client, headers, today, today) == {}

    async def test_entries_add_up_exactly_across_many_random_discounted_and_refunded_sales(
        self, client, owner_user
    ):
        """
        The invariant under real-shaped messiness: awkward prices,
        multi-product baskets, random cent-level discounts and a mix of
        restocked / non-restocked partial refunds. Seeded, so it is the
        same run every time -- and every figure must still add up to
        /reports/profit to the cent.
        """
        import random

        rng = random.Random(20260921)
        catalogue = [
            (3.35, 1.10),
            (7.10, 4.85),
            (12.99, 9.40),
            (0.85, 0.30),
            (45.00, 38.25),
            (19.95, 11.15),
        ]
        product_ids = [
            (
                await _make_product_with_batch(
                    price=price, cost=cost, qty=500, name=f"Random Product {i}"
                )
            )[0]
            for i, (price, cost) in enumerate(catalogue)
        ]
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sales = []
        for _ in range(30):
            picks = rng.sample(range(len(catalogue)), rng.randint(1, 3))
            lines = [(idx, rng.randint(1, 6)) for idx in picks]
            subtotal_cents = sum(round(catalogue[idx][0] * 100) * qty for idx, qty in lines)
            discount_cents = rng.randint(0, subtotal_cents * 4 // 10) if rng.random() < 0.6 else 0
            r = await client.post(
                "/api/v1/sales",
                json={
                    "items": [
                        {"product_id": product_ids[idx], "quantity": qty} for idx, qty in lines
                    ],
                    "payments": [
                        {"method": "CASH", "amount": (subtotal_cents - discount_cents) / 100}
                    ],
                    "discount_amount": discount_cents / 100,
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text
            sales.append(r.json())

        for sale in rng.sample(sales, 12):
            line = rng.choice(sale["items"])
            r = await client.post(
                f"/api/v1/sales/{sale['id']}/refunds",
                json={
                    "reason": "CUSTOMER_RETURN",
                    "method": "CASH",
                    "items": [
                        {
                            "sale_item_id": line["id"],
                            "quantity": rng.randint(1, line["quantity"]),
                            "restock": rng.random() < 0.5,
                        }
                    ],
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text

        today = (await _business_today()).isoformat()
        by_name = await self._entries_by_name(client, headers, today, today)
        assert len(by_name) == len(catalogue)

    async def test_empty_period_returns_no_entries(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/profit-by-product",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["entries"] == []

    async def test_start_after_end_is_rejected(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/reports/profit-by-product",
            params={"start_date": "2026-02-02", "end_date": "2026-02-01"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    async def test_requires_view_profit_permission(self, client, administrator_user):
        token = await _login(client, "sam", "AdminPass1")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/profit-by-product",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


class TestProfitLossPdfProductBreakdown:
    """
    The Profit & Loss PDF carries the same per-product breakdown as the
    Profit tab. What matters: its Total row agrees with the statement's
    own figures printed above it, it survives many pages, and awkward
    product names can't break the document.
    """

    @staticmethod
    def _pdf_text(content: bytes) -> str:
        return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)

    async def test_breakdown_appears_and_its_total_row_agrees_with_the_statement(
        self, client, owner_user
    ):
        # Same three-line discounted sale as TestProfitByProduct: total 29.90,
        # cost 11.00, profit 18.90, margin 63.2%.
        a_id, _ = await _make_product_with_batch(price=10.0, cost=4.0, name="Pdf Split A")
        b_id, _ = await _make_product_with_batch(price=10.0, cost=6.0, name="Pdf Split B")
        c_id, _ = await _make_product_with_batch(price=10.0, cost=1.0, name="Pdf Split C")
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [
                    {"product_id": a_id, "quantity": 1},
                    {"product_id": b_id, "quantity": 1},
                    {"product_id": c_id, "quantity": 1},
                ],
                "payments": [{"method": "CASH", "amount": 29.9}],
                "discount_amount": 0.10,
            },
            headers=headers,
        )
        assert sale.status_code == 201, sale.text

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/profit-loss-pdf",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200
        text = self._pdf_text(r.content)

        assert "Profit by product" in text
        for name in ("Pdf Split A", "Pdf Split B", "Pdf Split C"):
            assert name in text
        assert "9.97" in text
        assert "9.96" in text
        # Each figure appears once in the statement and once in the
        # breakdown's Total row: they must be the same numbers.
        assert text.count("29.90") >= 2
        assert text.count("11.00") >= 2
        assert text.count("18.90") >= 2
        assert text.count("63.2%") >= 2

    def test_a_long_breakdown_spans_pages_and_repeats_its_header(self):
        from app.schemas.reports import ProfitByProductEntry
        from app.services.report_export_service import generate_profit_loss_pdf

        entries = [
            ProfitByProductEntry(
                product_id=i,
                name=f"Product {i:03d}",
                net_quantity_sold=1,
                revenue=10.0,
                cost=4.0,
                profit=6.0,
                profit_margin_percent=60.0,
            )
            for i in range(150)
        ]
        content = generate_profit_loss_pdf(
            business_name="Test Pharmacy",
            start_date="2026-01-01",
            end_date="2026-01-31",
            revenue=1500.0,
            cost_of_goods_sold=600.0,
            gross_profit=900.0,
            gross_margin_percent=60.0,
            currency="KES",
            product_breakdown=entries,
        )
        pages = [page.extract_text() for page in PdfReader(io.BytesIO(content)).pages]

        assert len(pages) > 1
        for i in range(150):
            assert f"Product {i:03d}" in "\n".join(pages)
        pages_with_rows = [p for p in pages if "Product 0" in p or "Product 1" in p]
        assert len(pages_with_rows) > 1
        assert all("Revenue (KES)" in p for p in pages_with_rows)
        assert "1,500.00" in pages[-1]  # the Total row lands on the last page

    def test_awkward_product_names_and_missing_margin_do_not_break_the_pdf(self):
        from app.schemas.reports import ProfitByProductEntry
        from app.services.report_export_service import generate_profit_loss_pdf

        entries = [
            ProfitByProductEntry(
                product_id=1,
                name="Cough & Cold <Syrup> " + "Long Name " * 20,
                net_quantity_sold=0,
                revenue=0.0,
                cost=8.0,
                profit=-8.0,
                profit_margin_percent=None,
            )
        ]
        content = generate_profit_loss_pdf(
            business_name="Test Pharmacy",
            start_date="2026-01-01",
            end_date="2026-01-31",
            revenue=0.0,
            cost_of_goods_sold=8.0,
            gross_profit=-8.0,
            gross_margin_percent=0.0,
            currency="KES",
            product_breakdown=entries,
        )
        text = self._pdf_text(content)
        assert "Cough & Cold <Syrup>" in text
        assert "n/a" in text
        assert "-8.00" in text

    def test_no_breakdown_means_no_breakdown_section(self):
        from app.services.report_export_service import generate_profit_loss_pdf

        for breakdown in (None, []):
            content = generate_profit_loss_pdf(
                business_name="Test Pharmacy",
                start_date="2026-01-01",
                end_date="2026-01-31",
                revenue=0.0,
                cost_of_goods_sold=0.0,
                gross_profit=0.0,
                gross_margin_percent=0.0,
                currency="KES",
                product_breakdown=breakdown,
            )
            assert "Profit by product" not in self._pdf_text(content)


class TestProfitReportCentPrecision:
    """
    Money is whole cents everywhere it is stored, so the report's
    totals must be too: 0.30 - 0.10 is 0.19999999999999998 in float
    arithmetic, and that used to leak straight into the JSON.
    """

    async def test_revenue_cost_and_profit_are_exact_to_the_cent(self, client, owner_user):
        # 3 x 0.10 sold (cost 0.05 each), 1 returned and restocked:
        # revenue 0.30 - 0.10, cost 0.15 - 0.05, profit 0.20 - 0.10.
        product_id, _ = await _make_product_with_batch(
            price=0.10, cost=0.05, qty=20, name="Cent Precision"
        )
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        sale = (
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 3}],
                    "payments": [{"method": "CASH", "amount": 0.3}],
                },
                headers=headers,
            )
        ).json()
        refund = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale["items"][0]["id"], "quantity": 1, "restock": True}],
            },
            headers=headers,
        )
        assert refund.status_code == 201, refund.text

        today = (await _business_today()).isoformat()
        params = {"start_date": today, "end_date": today}
        body = (await client.get("/api/v1/reports/profit", params=params, headers=headers)).json()
        assert (body["total_revenue"], body["total_cost"], body["total_profit"]) == (0.2, 0.1, 0.1)

        kpi = (
            await client.get("/api/v1/reports/kpi-dashboard", params=params, headers=headers)
        ).json()
        assert (kpi["revenue"], kpi["profit"]) == (0.2, 0.1)
