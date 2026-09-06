"""
ACCURACY AUDIT.

Not "is the calculation approximately right" -- exact expected values,
computed independently by hand in each test's comment, checked against
the system's actual output to the cent/unit. Extends beyond the
module-level accuracy tests (e.g. Reports' two-batch profit test) with
messier, more realistic multi-batch/discount/boundary scenarios.
"""

from datetime import date, datetime, timedelta

from app.core.business_time import business_today
from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestMultiBatchProfitAccuracy:
    async def test_profit_across_three_batches_with_a_discount(
        self, client, owner_user, employee_user
    ):
        """
        Three batches at three different costs, one sale spanning all
        three via FEFO, plus a discount applied at the sale level.

        Hand-computed expected values:
          Batches (nearest expiry first):
            A(qty=4, cost=2.0), B(qty=6, cost=3.0), C(qty=20, cost=4.0)
          Sale of 15 units at price 10.0/unit:
            - subtotal = 15 * 10.0 = 150.0
            - discount = 20.0 -> total_amount = 130.0
            - FEFO draws: 4 from A, 6 from B, 5 from C
            - cost = 4*2.0 + 6*3.0 + 5*4.0 = 8 + 18 + 20 = 46.0
            - profit = total_amount - cost = 130.0 - 46.0 = 84.0
        """
        async with AsyncSessionLocal() as db:
            product = Product(name="Triple Batch Product", default_selling_price=10.0)
            db.add(product)
            await db.flush()
            for batch_number, expiry, qty, cost in [
                ("A", (date.today() + timedelta(days=1)), 4, 2.0),
                ("B", date(2026, 10, 1), 6, 3.0),
                ("C", date(2027, 3, 1), 20, 4.0),
            ]:
                db.add(
                    MedicineBatch(
                        product_id=product.id,
                        batch_number=batch_number,
                        expiry_date=expiry,
                        qty_received=qty,
                        qty_remaining=qty,
                        cost_price=cost,
                    )
                )
            await db.commit()
            product_id = int(product.id)

        employee_token = await _login(client, "joe", "pass1234")
        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 15}],
                "payments": [{"method": "CASH", "amount": 130.0}],
                "discount_amount": 20.0,
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert sale.status_code == 201
        assert sale.json()["subtotal"] == 150.0
        assert sale.json()["total_amount"] == 130.0

        owner_token = await _login(client, "lucy", "S3curePass!")
        # business_today(), not date.today() -- the sale was just
        # committed via the real API at the true current instant, so
        # the report window must be computed the same way production
        # does, not via this process's own possibly-different-day
        # guess at "today".
        async with AsyncSessionLocal() as db:
            today = (await business_today(db)).isoformat()
        report = await client.get(
            f"/api/v1/reports/profit?start_date={today}&end_date={today}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert report.json()["total_cost"] == 46.0
        assert report.json()["total_revenue"] == 130.0
        assert report.json()["total_profit"] == 84.0

    async def test_fefo_exact_boundary_quantity_matches_batch_exactly(
        self, client, employee_user, owner_user
    ):
        """
        Requesting EXACTLY the quantity in the nearest-expiry batch --
        the boundary case between 'fits in one batch' and 'spills over'.
        Must draw fully from that batch and touch nothing else.
        """
        async with AsyncSessionLocal() as db:
            product = Product(name="Boundary Product", default_selling_price=5.0)
            db.add(product)
            await db.flush()
            near = MedicineBatch(
                product_id=product.id,
                batch_number="NEAR",
                expiry_date=(date.today() + timedelta(days=1)),
                qty_received=10,
                qty_remaining=10,
                cost_price=1.0,
            )
            far = MedicineBatch(
                product_id=product.id,
                batch_number="FAR",
                expiry_date=date(2027, 1, 1),
                qty_received=50,
                qty_remaining=50,
                cost_price=1.5,
            )
            db.add_all([near, far])
            await db.commit()
            product_id = int(product.id)

        employee_token = await _login(client, "joe", "pass1234")
        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 10}],  # exactly the NEAR batch
                "payments": [{"method": "CASH", "amount": 50.0}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert sale.status_code == 201
        assert len(sale.json()["items"]) == 1  # single allocation, no spillover
        assert sale.json()["items"][0]["quantity"] == 10

        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batches = {b.batch_number: b.qty_remaining for b in result.scalars().all()}
            assert batches["NEAR"] == 0
            assert batches["FAR"] == 50  # completely untouched

    async def test_single_unit_batch_accuracy(self, client, employee_user, owner_user):
        """A batch with exactly 1 unit remaining -- the smallest
        possible non-empty allocation, must work exactly, not off-by-one."""
        async with AsyncSessionLocal() as db:
            product = Product(name="Single Unit Product", default_selling_price=99.99)
            db.add(product)
            await db.flush()
            db.add(
                MedicineBatch(
                    product_id=product.id,
                    batch_number="LAST-ONE",
                    expiry_date=date(2027, 1, 1),
                    qty_received=1,
                    qty_remaining=1,
                    cost_price=50.0,
                )
            )
            await db.commit()
            product_id = int(product.id)

        employee_token = await _login(client, "joe", "pass1234")
        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 99.99}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert sale.status_code == 201
        assert sale.json()["total_amount"] == 99.99

        # And the very next unit must correctly fail -- no phantom stock.
        oversell = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 99.99}],
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert oversell.status_code == 409


class TestValuationAccuracy:
    async def test_stock_valuation_matches_hand_computed_sum(self, client, owner_user):
        """
        Three products, each with a batch of known qty*cost. Valuation
        total must be the exact sum, not an approximation.
          P1: 30 * 2.5 = 75.0
          P2: 12 * 9.99 = 119.88
          P3: 100 * 0.75 = 75.0
          total = 269.88
        """
        async with AsyncSessionLocal() as db:
            specs = [("Val P1", 30, 2.5), ("Val P2", 12, 9.99), ("Val P3", 100, 0.75)]
            for name, qty, cost in specs:
                product = Product(name=name)
                db.add(product)
                await db.flush()
                db.add(
                    MedicineBatch(
                        product_id=product.id,
                        batch_number="V1",
                        expiry_date=date(2027, 1, 1),
                        qty_received=qty,
                        qty_remaining=qty,
                        cost_price=cost,
                    )
                )
            await db.commit()

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/valuation", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert round(r.json()["total_value"], 2) == 269.88


class TestAuditLogDateFilterTimezoneAccuracy:
    """
    audit_log_service.py used to compare AuditLog.created_at (stored
    in UTC) directly against datetime.combine(local_date, time.min/
    max) -- a naive local-date-as-if-UTC comparison. For a business
    ahead of UTC (Africa/Nairobi, UTC+3 -- this app's primary market),
    an entry that happened in the first few hours of true local
    "today" is stored with a UTC timestamp still on "yesterday", and a
    filter for local "today" silently excluded it. sale_service.py's
    own listing already handles this correctly via
    local_day_bounds_utc; this proves audit log filtering now matches.
    """

    async def test_entry_just_after_local_midnight_is_found_under_the_correct_local_date(
        self, client, owner_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.patch("/api/v1/config", json={"timezone": "Africa/Nairobi"}, headers=headers)

        # 2027-01-14 22:00 UTC = 2027-01-15 01:00 in Africa/Nairobi
        # (UTC+3) -- truly local-Jan-15, even though the raw UTC
        # timestamp is still on Jan 14. This is exactly the boundary
        # case a naive comparison gets wrong.
        entry_utc = datetime(2027, 1, 14, 22, 0, 0)
        async with AsyncSessionLocal() as db:
            db.add(
                AuditLog(
                    action="config.updated",
                    entity_type="business_config",
                    entity_id="1",
                )
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select, update

            latest = (
                await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
            ).scalar_one()
            await db.execute(
                update(AuditLog).where(AuditLog.id == latest.id).values(created_at=entry_utc)
            )
            await db.commit()

        # Filtering for 2027-01-14 (the raw UTC date) must NOT find it --
        r_wrong_day = await client.get(
            "/api/v1/audit-logs?start_date=2027-01-14&end_date=2027-01-14",
            headers=headers,
        )
        ids_wrong_day = [e["id"] for e in r_wrong_day.json()["entries"]]
        assert latest.id not in ids_wrong_day, (
            "Entry stored at 2027-01-14 22:00 UTC (= local Jan 15 01:00 in "
            "Africa/Nairobi) should not appear under the UTC calendar date."
        )

        # -- filtering for 2027-01-15 (the true local date) must find it.
        r_right_day = await client.get(
            "/api/v1/audit-logs?start_date=2027-01-15&end_date=2027-01-15",
            headers=headers,
        )
        ids_right_day = [e["id"] for e in r_right_day.json()["entries"]]
        assert latest.id in ids_right_day, (
            "TIMEZONE BUG: entry at 2027-01-14 22:00 UTC is local Jan 15 01:00 "
            "in Africa/Nairobi (UTC+3) but was not found under that local date."
        )
