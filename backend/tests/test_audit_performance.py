"""
SPEED AUDIT.

Two different kinds of check, deliberately not a full load-testing
suite (out of scope for a unit/integration test run):
  1. Query-count regression guard: counts the ACTUAL number of SQL
     statements a list endpoint issues (via SQLAlchemy engine events),
     proving the N+1 fix found earlier in this audit actually holds and
     stays O(1) as the dataset grows, not just "looks fixed by reading
     the code."
  2. Basic latency sanity: a moderate-scale sequence of real operations
     completes within a generous wall-clock bound, catching a gross
     performance regression (e.g. an accidental N+1 reintroduced
     elsewhere) without pretending to be a real load test.
"""

import time
from datetime import date

from sqlalchemy import event

from app.core.database import AsyncSessionLocal, engine
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class _QueryCounter:
    def __init__(self) -> None:
        self.count = 0

    def __enter__(self) -> "_QueryCounter":
        event.listen(engine.sync_engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info: object) -> None:
        event.remove(engine.sync_engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, *args: object, **kwargs: object) -> None:
        self.count += 1


class TestQueryCountRegressionGuard:
    async def test_product_list_query_count_does_not_scale_with_row_count(self, client, owner_user):
        """
        This is the direct regression test for the N+1 bug found and
        fixed at the start of this audit: list 5 products, then list 20
        products, and confirm the query count for the LIST call itself
        stays constant rather than growing with the number of rows.
        """
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        async def _create_products(n: int, prefix: str) -> None:
            for i in range(n):
                await client.post(
                    "/api/v1/products", json={"name": f"{prefix}-{i}"}, headers=headers
                )

        await _create_products(5, "QueryCountSmall")
        with _QueryCounter() as counter_small:
            r = await client.get("/api/v1/products", headers=headers)
        assert r.status_code == 200
        small_query_count = counter_small.count

        await _create_products(20, "QueryCountLarge")
        with _QueryCounter() as counter_large:
            r2 = await client.get("/api/v1/products", headers=headers)
        assert r2.status_code == 200
        large_query_count = counter_large.count

        # An O(n) implementation would show large_query_count growing
        # roughly linearly with the extra 20 rows (e.g. 20+ more
        # queries). An O(1) implementation issues the same handful of
        # queries regardless of row count -- allow small variance for
        # the permission-check/auth queries every request also makes.
        assert large_query_count <= small_query_count + 2, (
            f"Query count grew from {small_query_count} to {large_query_count} "
            f"when the product count grew by 20 -- looks like an N+1 regression"
        )

    async def test_low_stock_report_query_count_does_not_scale_with_row_count(
        self, client, owner_user
    ):
        """Same regression-guard pattern applied to the Inventory
        module's low-stock aggregation query."""
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncSessionLocal() as db:
            for i in range(15):
                product = Product(name=f"LowStockQueryTest-{i}", reorder_point=100)
                db.add(product)
                await db.flush()
                db.add(
                    MedicineBatch(
                        product_id=product.id,
                        batch_number="B1",
                        expiry_date=date(2027, 1, 1),
                        qty_received=1,
                        qty_remaining=1,
                        cost_price=1.0,
                    )
                )
            await db.commit()

        with _QueryCounter() as counter:
            r = await client.get("/api/v1/inventory/low-stock", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) >= 15
        # One aggregated GROUP BY query (plus a couple auth queries),
        # not one per flagged product.
        assert counter.count <= 5, (
            f"low-stock report issued {counter.count} queries for 15 flagged "
            f"products -- expected a small constant number, not one per row"
        )


class TestBasicLatencySanity:
    async def test_fifty_sequential_sales_complete_within_a_generous_bound(
        self, client, owner_user, employee_user
    ):
        """
        Not a load test -- a sanity bound generous enough that it only
        fails on a genuine gross regression (e.g. an N+1 reintroduced
        into the sale path), run against SQLite in-process which is
        itself slower than production MySQL for this pattern, making
        the bound conservative in the right direction.
        """
        owner_token = await _login(client, "lucy", "S3curePass!")
        employee_token = await _login(client, "joe", "pass1234")

        product_resp = await client.post(
            "/api/v1/products",
            json={"name": "Latency Test Product", "default_selling_price": 5.0},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        product_id = product_resp.json()["id"]
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "LATENCY-1",
                "expiry_date": "2027-01-01",
                "qty_received": 1000,
                "cost_price": 2.0,
            },
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        start = time.perf_counter()
        for _ in range(50):
            r = await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "payments": [{"method": "CASH", "amount": 5.0}],
                },
                headers={"Authorization": f"Bearer {employee_token}"},
            )
            assert r.status_code == 201
        elapsed = time.perf_counter() - start

        # Generous bound: 50 full checkout transactions (FEFO lookup +
        # locking + ledger write + commit) in under 15 seconds even on
        # SQLite in a shared CI runner. Real MySQL in production is
        # faster for this exact pattern (real row-level locking is
        # cheaper than SQLite's coarser database-level locking under
        # contention, and connection pooling amortizes better).
        assert elapsed < 15.0, f"50 sequential sales took {elapsed:.2f}s -- investigate regression"
