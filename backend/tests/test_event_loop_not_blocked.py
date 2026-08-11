"""
Concurrency regression test: does a CPU-bound PDF/Excel generation
call block this process's single asyncio event loop for every other
concurrent request while it runs?

generate_receipt_pdf (reportlab layout, PIL image decode) and the
report export functions (openpyxl/reportlab) are genuinely synchronous
CPU-bound work. Calling them directly inside an `async def` endpoint
does NOT make them non-blocking -- `async def` only means the
function CAN await something; it does nothing to code that never
does. Without `run_in_threadpool`, that work runs directly on the
same single thread the event loop uses for everything else, so one
person retrieving one receipt would freeze every other request the
whole app is handling at that moment -- another cashier's sale,
someone else's dashboard load -- until it finished.

This is proven directly rather than inferred: a fake "PDF generator"
sleeps for a fixed, known duration, and a concurrent lightweight
request (the real /health endpoint, which does a real but fast DB
query) is timed. If the heavy call blocks the event loop, the
lightweight request cannot even START until the heavy one finishes,
so its own completion time gets dragged out by (at least) the sleep
duration. If the heavy call is correctly offloaded via
run_in_threadpool, the lightweight request completes in roughly its
own normal time, independent of the sleep.
"""

import asyncio
import time
from datetime import date

from app.api.v1 import sales as sales_module
from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _make_product_with_batch(qty: int = 20) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name="Concurrency Test Product", default_selling_price=10.0)
        db.add(product)
        await db.flush()
        db.add(
            MedicineBatch(
                product_id=product.id,
                batch_number="CONC-1",
                expiry_date=date(2027, 1, 1),
                qty_received=qty,
                qty_remaining=qty,
                cost_price=5.0,
            )
        )
        await db.commit()
        return int(product.id)


class TestReceiptGenerationDoesNotBlockOtherRequests:
    async def test_health_check_stays_fast_while_a_slow_receipt_renders(
        self, client, owner_user, monkeypatch
    ):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        # A real sale so the receipt endpoint has something real to
        # fetch and reach the PDF-generation call at all.
        product_id = await _make_product_with_batch(qty=10)

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

        # Stand in for a genuinely slow render (a receipt with a large
        # logo, or a print job on a loaded machine) with a fixed,
        # known delay -- CPU-bound work and time.sleep() are both
        # "the thread is unavailable to do anything else for this
        # long" from the event loop's point of view, so this exercises
        # exactly the mechanism in question without depending on how
        # fast reportlab happens to be on whatever machine runs this
        # test.
        SLOW_SECONDS = 0.6

        def fake_slow_pdf_generator(*args, **kwargs):
            time.sleep(SLOW_SECONDS)
            return b"%PDF-1.4 fake receipt content"

        monkeypatch.setattr(sales_module, "generate_receipt_pdf", fake_slow_pdf_generator)

        health_timing: dict[str, float] = {}

        async def fetch_receipt():
            return await client.get(f"/api/v1/sales/{sale_id}/receipt", headers=headers)

        async def fetch_health_after_a_head_start():
            # The receipt endpoint does several of its own DB lookups
            # (sale, cashier, customer, business config) before it
            # ever reaches the PDF-generation call -- each of those is
            # a real await, so without this head start, /health could
            # race ahead and finish entirely during those gaps before
            # the receipt call even reaches its blocking work, which
            # would prove nothing either way. This delay places
            # /health's actual request-send squarely inside the
            # window where the receipt call is either deep inside its
            # blocking sleep (unfixed) or safely parked in a thread
            # pool worker (fixed).
            await asyncio.sleep(0.2)
            r = await client.get("/health")
            health_timing["completed_at"] = time.monotonic()
            return r

        overall_start = time.monotonic()
        receipt_resp, health_resp = await asyncio.gather(
            fetch_receipt(), fetch_health_after_a_head_start()
        )
        overall_elapsed = time.monotonic() - overall_start
        health_elapsed_from_shared_start = health_timing["completed_at"] - overall_start

        assert receipt_resp.status_code == 200, receipt_resp.text
        assert health_resp.status_code == 200, health_resp.text

        print(
            f"\n[CONCURRENCY] /health completed {health_elapsed_from_shared_start:.3f}s after "
            f"both requests were launched together, while a {SLOW_SECONDS}s receipt render "
            f"ran concurrently (total wall time for both: {overall_elapsed:.3f}s)"
        )

        # The actual proof: /health, sent 0.2s into the receipt's
        # SLOW_SECONDS-long render, must still complete quickly -- if
        # the event loop were blocked by the receipt call for that
        # whole duration, /health could not even send its request
        # until the receipt call released the thread, so its
        # completion time (from this shared start point) would be
        # dragged out to nearly SLOW_SECONDS too, not stay close to
        # its own 0.2s head start.
        assert health_elapsed_from_shared_start < SLOW_SECONDS * 0.75, (
            f"EVENT LOOP BLOCKED: /health only completed {health_elapsed_from_shared_start:.3f}s "
            f"after launch (0.2s head start + its own work), while a {SLOW_SECONDS}s receipt "
            "render ran concurrently -- it should complete well before the receipt render "
            "finishes. This means the receipt generation is blocking the event loop instead "
            "of running in a thread pool, so it stalls every other request the app is "
            "handling while any one receipt renders."
        )
