"""
Standalone probe (not part of the permanent suite).

Sale.created_at is written via `func.now()`, which on SQLite means
UTC. The default business timezone (BusinessConfig.timezone) is
"Africa/Nairobi", UTC+3. Every report/dashboard query in
report_service.py buckets a sale into a calendar day using
`func.date(Sale.created_at)` -- the UTC calendar date -- and compares
that directly against the start_date/end_date the pharmacy owner picks
in the UI, which they mean as local dates ("today", "this week").

receipt_service.py already does real timezone-aware conversion via
ZoneInfo(business_timezone) for the printed receipt timestamp. This
probe checks whether that same conversion exists anywhere in the
reporting path, by placing a sale at 01:00 local Nairobi time (which
is 22:00 UTC the PREVIOUS calendar day) and asking the dashboard for
"today" by local date.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.sale import Sale


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _make_product_with_batch(qty: int = 20) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name="Amoxicillin 500mg", default_selling_price=10.0)
        db.add(product)
        await db.flush()
        db.add(
            MedicineBatch(
                product_id=product.id,
                batch_number="B1",
                expiry_date=date.fromisoformat("2027-01-01"),
                qty_received=qty,
                qty_remaining=qty,
                cost_price=5.0,
            )
        )
        await db.commit()
        return int(product.id)


class TestTimezoneBucketingProbe:
    async def test_early_morning_local_sale_counted_in_correct_local_day(
        self, client, owner_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id = await _make_product_with_batch(qty=20)

        # Confirm the business timezone actually in effect for this test.
        cfg = await client.get("/api/v1/config", headers=headers)
        print(f"\n[PROBE-TZ] business_config timezone: {cfg.json().get('timezone')}")

        sale_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 10.0}],
                "discount_amount": 0,
                "customer_id": None,
            },
            headers=headers,
        )
        assert sale_resp.status_code == 201, sale_resp.text
        sale_id = sale_resp.json()["id"]

        # Nairobi is UTC+3. 01:00 local "today" == 22:00 UTC "yesterday".
        # Overwrite created_at directly, same column the app itself
        # writes, to simulate a real early-morning sale.
        local_today = date.today()
        utc_timestamp_for_1am_nairobi_today = datetime.combine(
            local_today, datetime.min.time()
        ) - timedelta(hours=2)  # 22:00 UTC on (local_today - 1 day)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Sale).where(Sale.id == sale_id))
            row = result.scalar_one()
            row.created_at = utc_timestamp_for_1am_nairobi_today
            await db.commit()
            print(f"[PROBE-TZ] sale.created_at set to (UTC, naive): {row.created_at}")
            print(f"[PROBE-TZ] local business calendar date this represents: {local_today}")

        # Debug: what does SQLite's own date() function extract from
        # the stored value, and via SQLAlchemy's func.date()?
        from sqlalchemy import text as _text

        async with AsyncSessionLocal() as db:
            raw = await db.execute(
                _text("SELECT id, created_at, date(created_at) FROM sales WHERE id = :sid"),
                {"sid": sale_id},
            )
            print(f"[PROBE-TZ] raw row (id, created_at, date(created_at)): {raw.fetchone()}")

        # Ask the dashboard for TODAY, by local calendar date -- what
        # the pharmacy owner actually means when they open the app in
        # the morning and look at "today's revenue".
        today = local_today.isoformat()
        r = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        print(f"[PROBE-TZ] kpi-dashboard for local 'today': revenue={body['revenue']} "
              f"transaction_count={body['transaction_count']}")

        if body["transaction_count"] == 0:
            raise AssertionError(
                "TIMEZONE DAY-BOUNDARY BUG CONFIRMED: a sale made at 01:00 local "
                "(Africa/Nairobi) time -- squarely 'today' for the pharmacy owner -- "
                "is invisible in today's KPI dashboard because the report layer "
                "buckets by func.date(Sale.created_at), which is the UTC calendar "
                "date, not the business's local calendar date. It is being counted "
                "under yesterday's report instead."
            )
