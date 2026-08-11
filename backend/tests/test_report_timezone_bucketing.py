"""
Regression test for local-timezone bucketing in the reports/dashboard
layer.

Sale.created_at is written via `func.now()`, which on SQLite means
UTC. The default business timezone (BusinessConfig.timezone) is
"Africa/Nairobi", UTC+3. Every report/dashboard query correctly
converts local calendar dates to UTC bounds via
business_time.local_day_bounds_utc before filtering -- this test
proves that end to end, by placing a sale at 01:00 local Nairobi time
(which is 22:00 UTC the PREVIOUS calendar day) and confirming the
dashboard still counts it under "today" by local date. Without that
conversion, a sale in the first few hours of the local business day
would silently be counted under the wrong day's revenue.
"""

from datetime import UTC, date, datetime, timedelta

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


class TestReportTimezoneBucketing:
    async def test_early_morning_local_sale_counted_in_correct_local_day(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id = await _make_product_with_batch(qty=20)

        # Confirm the business timezone actually in effect for this test.
        cfg = await client.get("/api/v1/config", headers=headers)
        print(f"\n[report-timezone] business_config timezone: {cfg.json().get('timezone')}")

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
            print(f"[report-timezone] sale.created_at set to (UTC, naive): {row.created_at}")
            print(f"[report-timezone] local business calendar date this represents: {local_today}")

        # Debug: what does SQLite's own date() function extract from
        # the stored value, and via SQLAlchemy's func.date()?
        from sqlalchemy import text as _text

        async with AsyncSessionLocal() as db:
            raw = await db.execute(
                _text("SELECT id, created_at, date(created_at) FROM sales WHERE id = :sid"),
                {"sid": sale_id},
            )
            print(f"[report-timezone] raw row (id, created_at, date(created_at)): {raw.fetchone()}")

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
        print(
            f"[report-timezone] kpi-dashboard for local 'today': revenue={body['revenue']} "
            f"transaction_count={body['transaction_count']}"
        )

        if body["transaction_count"] == 0:
            raise AssertionError(
                "TIMEZONE DAY-BOUNDARY BUG CONFIRMED: a sale made at 01:00 local "
                "(Africa/Nairobi) time -- squarely 'today' for the pharmacy owner -- "
                "is invisible in today's KPI dashboard because the report layer "
                "buckets by func.date(Sale.created_at), which is the UTC calendar "
                "date, not the business's local calendar date. It is being counted "
                "under yesterday's report instead."
            )

    async def test_dst_boundary_uses_the_sale_dates_own_offset_not_todays(self, client, owner_user):
        """
        The harder case Africa/Nairobi's fixed UTC+3 can never expose:
        a DST-observing business timezone, where the correct UTC
        offset for a given local date depends on THAT date's own DST
        status, not on whatever offset happens to apply on the day the
        report is actually run.

        America/New_York is EST (UTC-5) in January, EDT (UTC-4) in
        August. This test runs for real on whatever the actual current
        date is (verified independently to be nowhere near January
        2026 -- see the assertion below), which is exactly the
        scenario that broke under the old "offset as of now" logic: a
        sale genuinely made at 23:30 EST on January 14th was, under
        that logic, wrongly counted as January 15th revenue whenever
        the report ran in a differently-offset month. Proven with
        real numbers before this fix existed:
          - correct (January's own -5h offst) window for local Jan 15:
            [2026-01-15 05:00 UTC, 2026-01-16 05:00 UTC) -- excludes it.
          - old buggy (August's -4h offset misapplied) window:
            [2026-01-15 04:00 UTC, 2026-01-16 04:00 UTC) -- wrongly
            includes it, because 04:30 UTC falls inside that shifted
            window even though the sale never happened on Jan 15th at
            all, local or UTC.
        """
        assert date.today().month != 1, (
            "This test's whole point is running in a month with a DIFFERENT "
            "DST offset than the January date under test -- it would not "
            "catch a regression if it happened to run in January itself."
        )

        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        cfg_resp = await client.patch(
            "/api/v1/config", json={"timezone": "America/New_York"}, headers=headers
        )
        assert cfg_resp.status_code == 200, cfg_resp.text

        product_id = await _make_product_with_batch(qty=20)

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

        # A sale genuinely made at 23:30 EST on January 14th, 2026 --
        # stored in UTC exactly as the app itself would store it for a
        # real sale at that real local moment.
        sale_utc_timestamp = datetime(2026, 1, 15, 4, 30, 0)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Sale).where(Sale.id == sale_id))
            row = result.scalar_one()
            row.created_at = sale_utc_timestamp
            await db.commit()

        # January 14th: this sale DID happen locally on this date --
        # it must be counted here.
        r_jan14 = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": "2026-01-14", "end_date": "2026-01-14"},
            headers=headers,
        )
        assert r_jan14.status_code == 200, r_jan14.text
        jan14_body = r_jan14.json()

        # January 15th: this sale did NOT happen locally on this date
        # -- 04:30 UTC on the 15th is still 23:30 on the 14th in
        # America/New_York in January. Counting it here is the exact
        # bug this test exists to catch.
        r_jan15 = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": "2026-01-15", "end_date": "2026-01-15"},
            headers=headers,
        )
        assert r_jan15.status_code == 200, r_jan15.text
        jan15_body = r_jan15.json()

        assert jan14_body["transaction_count"] == 1, (
            f"Sale genuinely made at 23:30 EST Jan 14 is missing from Jan 14's "
            f"report: {jan14_body}"
        )
        assert jan15_body["transaction_count"] == 0, (
            "DST BUG CONFIRMED: a sale made at 23:30 EST on January 14th was "
            f"counted in January 15th's report instead: {jan15_body}. This is "
            "exactly what happens when the report uses today's UTC offset "
            "instead of the offset that actually applied on the queried date."
        )

    async def test_revenue_trend_buckets_correctly_across_a_dst_transition(
        self, client, owner_user
    ):
        """
        revenue_trend does its bucketing entirely in SQL for
        performance (see its own docstring: "correct regardless of
        how many years of sales have accumulated"). A single offset
        computed once cannot correctly bucket rows that fall on
        opposite sides of a real DST transition -- this test spans
        America/New_York's actual 2026 spring-forward date (March 8)
        with one sale on each side, and checks each lands in its own
        correct calendar day, not shifted onto the wrong one.
        """
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.patch("/api/v1/config", json={"timezone": "America/New_York"}, headers=headers)
        product_id = await _make_product_with_batch(qty=20)

        async def _sell_and_set_created_at(utc_timestamp: datetime) -> None:
            resp = await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "payments": [{"method": "CASH", "amount": 10.0}],
                    "discount_amount": 0,
                    "customer_id": None,
                },
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            sale_id = resp.json()["id"]
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Sale).where(Sale.id == sale_id))
                row = result.scalar_one()
                row.created_at = utc_timestamp
                await db.commit()

        # 23:30 EST, March 7th (the day BEFORE the transition)
        await _sell_and_set_created_at(datetime(2026, 3, 8, 4, 30, 0))
        # 12:00 EDT, March 9th (the day AFTER the transition)
        await _sell_and_set_created_at(datetime(2026, 3, 9, 16, 0, 0))

        r = await client.get(
            "/api/v1/reports/revenue-trend",
            params={"start_date": "2026-03-05", "end_date": "2026-03-12"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        points_by_date = {p["period_label"]: p for p in r.json()["points"]}

        assert points_by_date.get("2026-03-07", {}).get("revenue") == 10.0, (
            f"Sale genuinely made 23:30 EST March 7 missing from March 7's bucket: "
            f"{points_by_date}"
        )
        assert points_by_date.get("2026-03-09", {}).get("revenue") == 10.0, (
            f"Sale genuinely made 12:00 EDT March 9 missing from March 9's bucket: "
            f"{points_by_date}"
        )
        # Neither sale should have leaked into an adjacent day due to a
        # wrong single offset being applied across the transition.
        assert points_by_date.get("2026-03-08", {}).get("revenue", 0.0) == 0.0
        assert points_by_date.get("2026-03-06", {}).get("revenue", 0.0) == 0.0
        assert points_by_date.get("2026-03-10", {}).get("revenue", 0.0) == 0.0

    async def test_dst_transition_does_not_shift_a_report_run_in_a_different_season(
        self, client, owner_user
    ):
        """
        Africa/Nairobi never observes DST, so the test above can't
        catch this class of bug at all -- it needs a timezone that
        actually has two different UTC offsets across the year.

        The scenario: a sale genuinely made at 23:30 local time on
        Jan 14th (EST, UTC-5) -- squarely "Jan 14th" to the person who
        made it. A report layer that computes "the business's current
        UTC offset" once and applies it everywhere would use whatever
        offset is in effect on the day the report happens to be RUN,
        not the offset that applied on Jan 14th. This test suite runs
        in August (EDT, UTC-4) real time, so this reproduces the exact
        failure mode without needing to fake the calendar: under the
        old logic, August's -4h offset applied to a "Jan 15" query
        would wrongly pull in this Jan 14th sale.
        """
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id = await _make_product_with_batch(qty=20)

        cfg_resp = await client.patch(
            "/api/v1/config", json={"timezone": "America/New_York"}, headers=headers
        )
        assert cfg_resp.status_code == 200, cfg_resp.text

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

        # 23:30 EST on Jan 14, 2026 -- computed via real DST-aware
        # conversion (not a guess), matching exactly what a sale made
        # at that real local moment would be stored as.
        from zoneinfo import ZoneInfo

        ny = ZoneInfo("America/New_York")
        sale_local = datetime(2026, 1, 14, 23, 30, tzinfo=ny)
        sale_utc_naive = sale_local.astimezone(UTC).replace(tzinfo=None)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Sale).where(Sale.id == sale_id))
            row = result.scalar_one()
            row.created_at = sale_utc_naive
            await db.commit()

        # Querying "Jan 15" must NOT include a sale that was really
        # made on Jan 14th local time -- this is the exact case the
        # old "offset as of now" logic got wrong when run in a
        # different DST season than the date being queried.
        r_jan15 = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": "2026-01-15", "end_date": "2026-01-15"},
            headers=headers,
        )
        assert r_jan15.status_code == 200, r_jan15.text
        jan15_body = r_jan15.json()
        if jan15_body["transaction_count"] != 0:
            raise AssertionError(
                "DST BUG CONFIRMED: a sale genuinely made at 23:30 EST on Jan 14th "
                "is being counted in the Jan 15th report. This happens when the "
                "report layer uses the UTC offset in effect 'right now' (this test "
                "suite runs in August, EDT, UTC-4) instead of the offset that "
                "actually applied on the historical date being queried (January, "
                "EST, UTC-5) -- a report run in winter would show this sale "
                "correctly, but the exact same report run in summer would not, "
                "which is the whole bug: the same historical data giving a "
                "different answer depending on when you happen to ask."
            )

        # And it MUST show up correctly under Jan 14th, where it
        # genuinely belongs.
        r_jan14 = await client.get(
            "/api/v1/reports/kpi-dashboard",
            params={"start_date": "2026-01-14", "end_date": "2026-01-14"},
            headers=headers,
        )
        assert r_jan14.status_code == 200, r_jan14.text
        jan14_body = r_jan14.json()
        assert jan14_body["transaction_count"] == 1, (
            f"Sale genuinely made on Jan 14th local time is missing from the "
            f"Jan 14th report: {jan14_body}"
        )
        assert jan14_body["revenue"] == 10.0
