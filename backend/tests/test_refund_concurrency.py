"""
Concurrency regression test for refund over-refund protection.

The over-refund guard in RefundService._already_refunded_quantity
reads prior RefundItem rows with a plain SELECT, computes `remaining`
in Python, and only then inserts -- unlike apply_allocations() and
_restock_batch(), which enforce their invariant with an atomic
`UPDATE ... WHERE <condition>` evaluated against the row's real state
at write time. Two refund requests against the SAME sale_item running
concurrently could, in principle, both complete that SELECT before
either commits its INSERT -- each seeing zero (or partial) prior
refunds, both passing the `line.quantity > remaining` check, both
committing. Nothing at the database level stops that: no unique
constraint, no CHECK, no atomic conditional update on refund_items.

In practice this doesn't happen, because the refund header row's own
early `db.flush()` acquires SQLite's single-writer lock before the
over-refund check ever runs, serializing concurrent requests. But that
protection is incidental to timing, not an explicit guarantee -- a
future refactor that reorders these steps could silently remove it
with nothing here to catch that. This test exists to make sure any
such regression is caught immediately rather than surfacing as a real
over-refund in production.
"""

import asyncio
from datetime import date

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.refund import RefundItem


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


class TestRefundOverRefundConcurrency:
    async def test_two_concurrent_refunds_against_the_same_sale_item(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id = await _make_product_with_batch(qty=20)

        sale_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 50.0}],
                "discount_amount": 0,
                "customer_id": None,
            },
            headers=headers,
        )
        assert sale_resp.status_code == 201, sale_resp.text
        sale = sale_resp.json()
        sale_item_id = sale["items"][0]["id"]

        # Only 5 units were ever sold on this line. Two refund requests,
        # each asking for 4, fire at the same instant. At most ONE
        # should be able to succeed -- 4 + 4 = 8 > 5 sold.
        async def refund_4():
            return await client.post(
                f"/api/v1/sales/{sale['id']}/refunds",
                json={
                    "reason": "CUSTOMER_RETURN",
                    "method": "CASH",
                    "items": [{"sale_item_id": sale_item_id, "quantity": 4}],
                },
                headers=headers,
            )

        results = await asyncio.gather(refund_4(), refund_4(), return_exceptions=True)
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        exceptions = [r for r in results if isinstance(r, Exception)]

        print(f"\n[refund-concurrency] status codes: {status_codes}  exceptions: {exceptions}")

        async with AsyncSessionLocal() as db:
            total_refunded = await db.scalar(
                select(RefundItem.quantity).where(RefundItem.sale_item_id == sale_item_id)
            )
            all_items = (
                (
                    await db.execute(
                        select(RefundItem).where(RefundItem.sale_item_id == sale_item_id)
                    )
                )
                .scalars()
                .all()
            )
            total_refunded = sum(i.quantity for i in all_items)
            print(f"[refund-concurrency] total refund_items quantity for this sale_item: {total_refunded}")

        assert total_refunded <= 5, (
            f"OVER-REFUND CONFIRMED: {total_refunded} units refunded against a sale_item "
            f"that only sold 5. status_codes={status_codes}"
        )

    async def test_five_concurrent_refunds_against_the_same_sale_item(self, client, owner_user):
        """Higher fan-out than the 2-way case above -- 5 simultaneous
        requests for 2 units each against a sale_item that only sold 5
        (10 requested total). At most 2 requests can succeed (4 of 5
        units); the invariant is never selling/refunding past what
        exists, regardless of how many requests land at once."""
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id = await _make_product_with_batch(qty=20)

        sale_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 50.0}],
                "discount_amount": 0,
                "customer_id": None,
            },
            headers=headers,
        )
        assert sale_resp.status_code == 201, sale_resp.text
        sale = sale_resp.json()
        sale_item_id = sale["items"][0]["id"]

        async def refund_2():
            return await client.post(
                f"/api/v1/sales/{sale['id']}/refunds",
                json={
                    "reason": "CUSTOMER_RETURN",
                    "method": "CASH",
                    "items": [{"sale_item_id": sale_item_id, "quantity": 2}],
                },
                headers=headers,
            )

        results = await asyncio.gather(*[refund_2() for _ in range(5)], return_exceptions=True)
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        exceptions = [r for r in results if isinstance(r, Exception)]
        print(f"\n[refund-concurrency 5x] status codes: {status_codes}  exceptions: {exceptions}")

        async with AsyncSessionLocal() as db:
            all_items = (
                (
                    await db.execute(
                        select(RefundItem).where(RefundItem.sale_item_id == sale_item_id)
                    )
                )
                .scalars()
                .all()
            )
            total_refunded = sum(i.quantity for i in all_items)
            print(f"[refund-concurrency 5x] total refund_items quantity: {total_refunded}")

        assert total_refunded <= 5, (
            f"OVER-REFUND CONFIRMED under 5-way concurrency: {total_refunded} units refunded "
            f"against a sale_item that only sold 5. status_codes={status_codes}"
        )
        assert not exceptions, f"Unhandled exceptions under concurrency: {exceptions}"
