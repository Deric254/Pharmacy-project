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

        today = date.today().isoformat()
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
        today = date.today().isoformat()
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

        today = date.today().isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.json()["profit"] == 30.0  # (10-4) * 5

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

        today = date.today().isoformat()
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
            expensive = Product(name="Expensive Product", default_selling_price=100.0)
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

        today = date.today().isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        top_products = r.json()["top_products"]
        assert top_products[0]["product_id"] == expensive_id
        assert top_products[0]["revenue"] == 100.0

    async def test_low_stock_and_expiring_counts_reflect_real_inventory(self, client, owner_user):
        # Reorder point 10, stock only 3 -- genuinely low.
        async with AsyncSessionLocal() as db:
            product = Product(
                name="Low Stock KPI Product", default_selling_price=5.0, reorder_point=10
            )
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
                )
            )
            await db.commit()

        token = await _login(client, "lucy", "S3curePass!")
        today = date.today().isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.json()["low_stock_count"] >= 1

    async def test_requires_reports_view_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        today = date.today().isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


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

        today = date.today().isoformat()
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

        today = date.today().isoformat()
        r = await client.get(
            "/api/v1/reports/top-customers",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
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
            json={"name": "Revenue Potential Product", "default_selling_price": 20.0},
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
            json={"name": "No Stock Product", "default_selling_price": 10.0},
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
            json={"name": "Slow Moving Runway Product", "default_selling_price": 10.0},
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
