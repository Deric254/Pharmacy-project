"""
Business intelligence tests. Records are real rows (products, batches, sales,
purchase orders, supplier ledger), dated explicitly into the three equal
windows the report compares, and every expected number below is worked out by
hand in a comment so the test fails if the logic is wrong, not just if it
crashes. With days=30 and today = T:

    current  T-29 .. T        previous  T-59 .. T-30        before  T-89 .. T-60
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.core.business_time import business_today
from app.core.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus
from app.models.sale import Payment, PaymentMethod, Sale, SaleItem
from app.models.supplier import Supplier, SupplierTransaction


async def _login(client: Any, username: str, password: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _owner(client: Any) -> dict[str, str]:
    return await _login(client, "lucy", "S3curePass!")


async def _today() -> date:
    async with AsyncSessionLocal() as db:
        return await business_today(db)


def _at(day: date) -> datetime:
    """Noon in the business's timezone, stored the way the app stores it (naive UTC)."""
    return datetime.combine(day, time(9, 0))


async def _product(
    name: str,
    qty: int = 1000,
    cost: float = 4.0,
    price: float = 10.0,
    expiry: date | None = None,
    batch_created: datetime | None = None,
) -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        p = Product(name=name)
        db.add(p)
        await db.flush()
        b = MedicineBatch(
            product_id=p.id,
            batch_number=f"B-{name[:6]}",
            expiry_date=expiry or date(2097, 1, 1),
            qty_received=max(qty, 1),
            qty_remaining=qty,
            cost_price=cost,
            selling_price=price,
        )
        if batch_created is not None:
            b.created_at = batch_created
        db.add(b)
        await db.commit()
        return int(p.id), int(b.id)


async def _sale(
    cashier_id: int,
    day: date,
    lines: list[tuple[int, int, int, float, float]],  # product, batch, qty, price, cost
    customer_id: int | None = None,
    discount: float = 0.0,
    method: PaymentMethod = PaymentMethod.CASH,
) -> None:
    subtotal = round(sum(q * price for _, _, q, price, _ in lines), 2)
    total = round(subtotal - discount, 2)
    async with AsyncSessionLocal() as db:
        sale = Sale(
            cashier_user_id=cashier_id,
            customer_id=customer_id,
            subtotal=subtotal,
            discount_amount=discount,
            total_amount=total,
            created_at=_at(day),
        )
        db.add(sale)
        await db.flush()
        for pid, bid, q, price, cost in lines:
            db.add(
                SaleItem(
                    sale_id=sale.id,
                    product_id=pid,
                    batch_id=bid,
                    quantity=q,
                    unit_price=price,
                    unit_cost=cost,
                    line_total=round(q * price, 2),
                )
            )
        db.add(Payment(sale_id=sale.id, method=method, amount=total))
        await db.commit()


async def _report(client: Any, headers: dict[str, str], days: int = 30) -> dict[str, Any]:
    r = await client.get(f"/api/v1/reports/intelligence?days={days}", headers=headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


def _area(rep: dict[str, Any], area_id: str) -> dict[str, Any]:
    return next(a for a in rep["areas"] if a["id"] == area_id)


def _find(rep: dict[str, Any], area_id: str, kind: str) -> dict[str, Any] | None:
    return next((i for i in _area(rep, area_id)["insights"] if i["kind"] == kind), None)


def _kpi(rep: dict[str, Any], area_id: str, label: str) -> float | None:
    k = next((k for k in _area(rep, area_id)["kpis"] if k["label"] == label), None)
    return None if k is None else k["value"]


class TestShapeAndPermissions:
    async def test_empty_business_has_all_eight_areas_and_invents_nothing(self, client, owner_user):
        rep = await _report(client, await _owner(client))
        ids = [a["id"] for a in rep["areas"]]
        assert ids == [
            "sales",
            "profitability",
            "inventory",
            "customers",
            "expenses",
            "cash_flow",
            "performance",
            "growth",
        ]
        for area_id in ("sales", "profitability", "customers", "performance", "growth"):
            assert _area(rep, area_id)["insights"] == [], area_id
        today = await _today()
        assert rep["period"]["end"] == today.isoformat()
        assert rep["period"]["start"] == (today - timedelta(days=29)).isoformat()
        assert rep["previous_period"]["end"] == (today - timedelta(days=30)).isoformat()
        assert _kpi(rep, "sales", "Revenue") == 0.0

    async def test_login_is_required_and_days_are_validated(
        self, client, owner_user, employee_user
    ):
        assert (await client.get("/api/v1/reports/intelligence")).status_code == 401
        emp = await _login(client, "joe", "pass1234")
        assert (await client.get("/api/v1/reports/intelligence", headers=emp)).status_code == 403
        owner = await _owner(client)
        assert (
            await client.get("/api/v1/reports/intelligence?days=3", headers=owner)
        ).status_code == 422
        assert (
            await client.get("/api/v1/reports/intelligence?days=400", headers=owner)
        ).status_code == 422

    async def test_profit_is_withheld_from_a_role_without_view_profit(
        self, client, owner_user, administrator_user
    ):
        p, b = await _product("Widget")
        today = await _today()
        for k in range(12):
            await _sale(owner_user.id, today - timedelta(days=59 - k), [(p, b, 1, 10.0, 4.0)])
        for k in range(12):
            await _sale(owner_user.id, today - timedelta(days=k), [(p, b, 1, 10.0, 8.0)])

        admin = await _login(client, "sam", "AdminPass1")
        rep = await _report(client, admin)
        prof = _area(rep, "profitability")
        assert prof["status"] == "unavailable" and prof["insights"] == []
        assert "View profit" in prof["missing"][0]
        card = _find(rep, "performance", "performance_scorecard")
        assert card is not None
        assert not any("profit" in e["label"].lower() for e in card["evidence"])
        assert not any("profit" in k["label"].lower() for a in rep["areas"] for k in a["kpis"])
        # the owner sees them
        full = await _report(client, await _owner(client))
        assert _area(full, "profitability")["status"] == "full"


class TestSalesAndProfitability:
    async def test_a_sales_drop_is_flagged_with_exact_impact_and_driver(self, client, owner_user):
        today = await _today()
        w, wb = await _product("Widget")
        g, gb = await _product("Gadget")
        for k in range(12):  # previous: 12 x 10.00 = 120.00 (first sale on the period's first day)
            await _sale(owner_user.id, today - timedelta(days=59 - k), [(w, wb, 1, 10.0, 4.0)])
        for k in range(5):  # current: Widget 5 x 10.00 = 50.00
            await _sale(owner_user.id, today - timedelta(days=k), [(w, wb, 1, 10.0, 4.0)])
        for k in range(2):  # current: Gadget 2 x 5.00 = 10.00
            await _sale(owner_user.id, today - timedelta(days=10 + k), [(g, gb, 1, 5.0, 2.0)])

        rep = await _report(client, await _owner(client))
        i = _find(rep, "sales", "sales_change")
        assert i is not None, "a 50% drop must be flagged"
        assert i["severity"] == "problem"
        assert i["impact"]["amount"] == -60.0  # 60 - 120
        assert i["comparison"]["current"] == 60.0 and i["comparison"]["previous"] == 120.0
        assert i["comparison"]["change_pct"] == -50.0
        assert any("Widget" in e["label"] for e in i["evidence"]), "the product behind the drop"
        assert "KES 60.00" in i["what_happened"] and "KES 120.00" in i["what_happened"]

    async def test_short_history_gives_no_comparison_and_says_why(self, client, owner_user):
        p, b = await _product("Widget")
        today = await _today()
        for k in range(11):
            await _sale(owner_user.id, today - timedelta(days=k), [(p, b, 1, 10.0, 4.0)])
        rep = await _report(client, await _owner(client))
        sales = _area(rep, "sales")
        assert _find(rep, "sales", "sales_change") is None
        assert sales["kpis"][0]["previous"] is None, "no previous figure may be invented"
        assert any("day(s) of sales history" in n for n in sales["notes"])
        assert _area(rep, "performance")["insights"] == []

    async def test_margin_shift_and_loss_making_products(self, client, owner_user):
        today = await _today()
        w, wb = await _product("Widget")
        for k in range(12):  # previous margin: (10 - 4) / 10 = 60%
            await _sale(owner_user.id, today - timedelta(days=59 - k), [(w, wb, 1, 10.0, 4.0)])
        for k in range(12):  # current margin: (10 - 8) / 10 = 20%
            await _sale(owner_user.id, today - timedelta(days=k), [(w, wb, 1, 10.0, 8.0)])
        ll, llb = await _product("Loss Leader")
        for k in range(3):  # sold at 10.00 costing 15.00: loses 5.00 each
            await _sale(owner_user.id, today - timedelta(days=20 + k), [(ll, llb, 1, 10.0, 15.0)])

        rep = await _report(client, await _owner(client))
        shift = _find(rep, "profitability", "margin_shift")
        assert shift is not None
        # current: 12 Widgets (profit 24) + 3 Loss Leaders (profit -15) on revenue 150 -> 9/150 = 6%
        assert shift["comparison"]["current"] == 6.0 and shift["comparison"]["previous"] == 60.0
        assert shift["severity"] == "problem"
        loss = _find(rep, "profitability", "loss_making_items")
        assert loss is not None and loss["severity"] == "problem"
        assert loss["impact"]["amount"] == -15.0
        assert [e["label"] for e in loss["evidence"]] == ["Loss Leader"]

    async def test_discounts_taking_a_bigger_share_are_flagged(self, client, owner_user):
        today = await _today()
        w, wb = await _product("Widget")
        for k in range(12):  # previous: no discounts
            await _sale(owner_user.id, today - timedelta(days=59 - k), [(w, wb, 1, 10.0, 4.0)])
        for k in range(12):  # current: 1.00 off every 10.00 sale = 10% of sales before discount
            await _sale(
                owner_user.id, today - timedelta(days=k), [(w, wb, 1, 10.0, 4.0)], discount=1.0
            )
        rep = await _report(client, await _owner(client))
        i = _find(rep, "profitability", "discount_leakage")
        assert i is not None
        assert i["comparison"]["current"] == 10.0 and i["comparison"]["previous"] == 0.0
        assert i["impact"]["amount"] == -12.0  # 10 points of 120.00 before discount


class TestInventory:
    async def test_expiry_dead_stock_stock_outs_and_write_off_risk(self, client, owner_user):
        today = await _today()
        now = datetime.now(UTC).replace(tzinfo=None)
        # sells 30 units in the last 30 days = 1 per day, but 100 units expire in 20 days
        a, ab = await _product(
            "Slow Syrup", qty=100, cost=4.0, price=8.0, expiry=today + timedelta(days=20)
        )
        for d in (5, 10, 15):
            await _sale(owner_user.id, today - timedelta(days=d), [(a, ab, 10, 8.0, 4.0)])
        # sold recently, nothing left
        b, bb = await _product("Sold Out Item", qty=0, cost=2.0, price=5.0)
        await _sale(owner_user.id, today - timedelta(days=3), [(b, bb, 5, 5.0, 2.0)])
        # expired 10 days ago, 7 units at 3.00 still on the shelf
        await _product("Old Expired", qty=7, cost=3.0, price=6.0, expiry=today - timedelta(days=10))
        # received 60 days ago and never sold: 10 units at 2.00
        await _product("Dusty", qty=10, cost=2.0, price=4.0, batch_created=now - timedelta(days=60))

        rep = await _report(client, await _owner(client))
        assert _kpi(rep, "inventory", "Stock value at cost") == 420.0  # 400 + 20; expired excluded

        expired = _find(rep, "inventory", "expired_stock")
        assert expired is not None and expired["impact"]["amount"] == 21.0
        dead = _find(rep, "inventory", "dead_stock")
        assert dead is not None and dead["impact"]["amount"] == 20.0
        assert [e["label"] for e in dead["evidence"]] == ["Dusty"]
        out = _find(rep, "inventory", "sold_out_sellers")
        assert out is not None and out["severity"] == "problem"
        assert out["impact"]["amount"] == 25.0  # 5 x 5.00
        risk = _find(rep, "inventory", "expiry_write_off_risk")
        assert risk is not None and risk["severity"] == "problem"  # expires within 30 days
        # 100 units, 1 sold per day for 20 days -> 80 expire unsold, at cost 4.00 = 320.00
        assert risk["impact"]["amount"] == 320.0
        assert any("80 unit" in e["value"] for e in risk["evidence"])
        assert any("pace" in lim for lim in risk["confidence"]["limitations"])

    async def test_batches_ahead_in_expiry_order_count_against_later_ones(self, client, owner_user):
        today = await _today()
        a, _ = await _product("Two Batches", qty=40, cost=1.0, expiry=today + timedelta(days=10))
        async with AsyncSessionLocal() as db:  # a second, later-expiring batch of the same product
            db.add(
                MedicineBatch(
                    product_id=a,
                    batch_number="B-LATER",
                    expiry_date=today + timedelta(days=40),
                    qty_received=60,
                    qty_remaining=60,
                    cost_price=1.0,
                    selling_price=2.0,
                )
            )
            await db.commit()
        # no sales at all: the pace is 0, so every unit expiring inside 90 days is at risk
        risk = _find(
            await _report(client, await _owner(client)), "inventory", "expiry_write_off_risk"
        )
        assert risk is not None and risk["impact"]["amount"] == 100.0  # 40 + 60 units at 1.00
        assert any("no recent sales" in lim for lim in risk["confidence"]["limitations"])


class TestCustomers:
    async def test_lapsed_customers_are_found_with_the_revenue_they_represented(
        self, client, owner_user
    ):
        today = await _today()
        p, b = await _product("Widget")
        ids = {}
        async with AsyncSessionLocal() as db:
            for n in ("Anna", "Ben", "Cara", "Dan"):
                c = Customer(name=n)
                db.add(c)
                await db.flush()
                ids[n] = c.id
            await db.commit()
        # previous period: each bought once, 10.00; current period: only Dan again
        for k, n in enumerate(("Anna", "Ben", "Cara", "Dan")):
            await _sale(
                owner_user.id, today - timedelta(days=59 - k * 5), [(p, b, 1, 10.0, 4.0)], ids[n]
            )
        await _sale(owner_user.id, today - timedelta(days=2), [(p, b, 1, 10.0, 4.0)], ids["Dan"])

        rep = await _report(client, await _owner(client))
        i = _find(rep, "customers", "lapsed_customers")
        assert i is not None and i["severity"] == "problem"  # 30 of 40 = 75% of last period
        assert i["impact"]["amount"] == -30.0
        assert {e["label"] for e in i["evidence"]} == {"Anna", "Ben", "Cara"}
        assert _kpi(rep, "customers", "Identified customers") == 1.0
        assert any("attached to a sale" in lim for lim in i["confidence"]["limitations"])


class TestExpensesAndCash:
    async def test_supplier_price_rises_are_found_from_received_purchases(self, client, owner_user):
        today = await _today()
        w, wb = await _product("Widget")
        z, zb = await _product("Zinc")
        async with AsyncSessionLocal() as db:
            s = Supplier(name="Acme Pharma")
            db.add(s)
            await db.flush()

            async def po(day: date, pid: int, qty: int, cost: float) -> None:
                order = PurchaseOrder(
                    supplier_id=s.id,
                    status=PurchaseOrderStatus.RECEIVED,
                    created_by_user_id=owner_user.id,
                    received_at=_at(day),
                )
                db.add(order)
                await db.flush()
                db.add(
                    PurchaseOrderItem(
                        purchase_order_id=order.id,
                        product_id=pid,
                        quantity_ordered=qty,
                        unit_cost_expected=cost,
                        quantity_received=qty,
                        unit_cost_actual=cost,
                        batch_id=None,
                    )
                )

            await po(
                today - timedelta(days=59), z, 5, 10.0
            )  # proves history reaches the period start
            await po(today - timedelta(days=45), w, 10, 10.0)  # previous: 10 x 10.00
            await po(today - timedelta(days=5), w, 10, 12.0)  # current: 10 x 12.00
            await db.commit()

        rep = await _report(client, await _owner(client))
        i = _find(rep, "expenses", "supplier_price_increase")
        assert i is not None
        assert i["impact"]["amount"] == 20.0  # (12.00 - 10.00) x 10 units
        assert i["evidence"][0]["label"] == "Acme Pharma - Widget"
        assert "+20.0%" in i["evidence"][0]["value"]
        assert _kpi(rep, "expenses", "Stock purchased (received)") == 120.0
        exp = _area(rep, "expenses")
        assert exp["status"] == "partial"
        assert any("Operating expenses" in m for m in exp["missing"])

    async def test_supplier_payments_reversals_and_balances_are_read_correctly(
        self, client, owner_user
    ):
        today = await _today()
        p, b = await _product("Widget")
        await _sale(owner_user.id, today - timedelta(days=2), [(p, b, 10, 10.0, 4.0)])  # CASH 100
        await _sale(
            owner_user.id,
            today - timedelta(days=1),
            [(p, b, 5, 10.0, 4.0)],
            method=PaymentMethod.MPESA,
        )  # MPESA 50
        async with AsyncSessionLocal() as db:
            s1, s2 = Supplier(name="Settled Co"), Supplier(name="Owed Co")
            db.add_all([s1, s2])
            await db.flush()
            when = _at(today - timedelta(days=3))

            def tx(sid: int, amount: float, reverses: int | None = None) -> SupplierTransaction:
                return SupplierTransaction(
                    supplier_id=sid,
                    amount=amount,
                    created_at=when,
                    reverses_transaction_id=reverses,
                )

            purchase = tx(s1.id, 100.0)
            pay1 = tx(s1.id, -40.0)
            db.add_all([purchase, pay1])
            await db.flush()
            db.add_all([tx(s1.id, 40.0, pay1.id), tx(s1.id, -30.0), tx(s1.id, -100.0, purchase.id)])
            db.add(tx(s2.id, 50.0))  # owed 50.00, nothing paid
            await db.commit()

        rep = await _report(client, await _owner(client))
        assert _kpi(rep, "cash_flow", "Cash received from sales") == 150.0
        # payments 40 + 30, minus the 40 reversed = 30 paid; the purchase reversal is not a payment
        assert _kpi(rep, "cash_flow", "Paid to suppliers") == 30.0
        assert _kpi(rep, "cash_flow", "Net cash movement") == 120.0
        assert (
            _kpi(rep, "cash_flow", "Owed to suppliers") == 50.0
        )  # the overpaid supplier is not owed
        cash = _area(rep, "cash_flow")
        assert cash["status"] == "partial"
        assert any("Opening cash" in m for m in cash["missing"])


class TestGrowth:
    async def test_growth_needs_three_covered_periods_and_reports_the_trend(
        self, client, owner_user
    ):
        today = await _today()
        p, b = await _product("Widget")
        for window, price in ((2, 10.0), (1, 20.0), (0, 40.0)):  # 100, 200, 400
            for k in range(10):
                day = today - timedelta(days=window * 30 + 29 - k)
                await _sale(owner_user.id, day, [(p, b, 1, price, 4.0)])
        rep = await _report(client, await _owner(client))
        g = _find(rep, "growth", "revenue_growth")
        assert g is not None and g["severity"] == "good"
        assert g["impact"]["amount"] == 200.0  # 400 - 200
        out = _find(rep, "growth", "revenue_outlook")
        assert out is not None and "KES 233.33" in out["what_happened"]  # (100 + 200 + 400) / 3
        assert any("seasonality" in lim for lim in out["confidence"]["limitations"])

        # seen from a day with too little history it must not claim a trend
        # (only the first ~40 days exist then)
        short = await client.get(
            "/api/v1/reports/intelligence?days=60", headers=await _owner(client)
        )
        assert not any(
            i["kind"] == "revenue_growth" for a in short.json()["areas"] for i in a["insights"]
        )


class TestEveryInsightCarriesTheFullStructure:
    async def test_structure(self, client, owner_user):
        today = await _today()
        p, b = await _product("Widget")
        ll, llb = await _product("Loss Leader")
        for k in range(12):
            await _sale(owner_user.id, today - timedelta(days=59 - k), [(p, b, 1, 10.0, 4.0)])
        for k in range(3):
            await _sale(owner_user.id, today - timedelta(days=k), [(ll, llb, 1, 10.0, 15.0)])
        rep = await _report(client, await _owner(client))
        seen = 0
        for a in rep["areas"]:
            for i in a["insights"]:
                seen += 1
                assert i["what_happened"] and i["why_it_matters"] and i["recommended_action"], i[
                    "kind"
                ]
                assert i["evidence"], f"{i['kind']} has no evidence"
                assert i["confidence"]["level"] in ("high", "medium", "low"), i["kind"]
                assert i["period"]["start"] and i["period"]["end"], i["kind"]
        assert seen >= 2, f"expected several findings, got {seen}"
