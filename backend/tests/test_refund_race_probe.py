"""
Standalone probe (not part of the permanent suite) -- checking whether
the over-refund guard in RefundService._already_refunded_quantity is
actually race-safe, or whether it's the same "SELECT, check in Python,
then INSERT" pattern that was already found and fixed for stock
decrement, batch restock, and PO-transition elsewhere in this
session's work.

Unlike apply_allocations() and _restock_batch(), which enforce their
invariant with an atomic `UPDATE ... WHERE <condition>` evaluated
against the row's real state at write time, the over-refund check
reads prior RefundItem rows with a plain SELECT, computes `remaining`
in Python, and only then inserts. If two refund requests against the
SAME sale_item run concurrently, both can complete that SELECT before
either commits its INSERT -- each sees zero (or partial) prior
refunds, both pass the `line.quantity > remaining` check, and both
commit. Nothing at the database level stops it: no unique constraint,
no CHECK, no atomic conditional update on refund_items.
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


class TestRefundRaceProbe:
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

        print(f"\n[PROBE] status codes: {status_codes}  exceptions: {exceptions}")

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
            print(f"[PROBE] total refund_items quantity for this sale_item: {total_refunded}")

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
        print(f"\n[PROBE-5x] status codes: {status_codes}  exceptions: {exceptions}")

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
            print(f"[PROBE-5x] total refund_items quantity: {total_refunded}")

        assert total_refunded <= 5, (
            f"OVER-REFUND CONFIRMED under 5-way concurrency: {total_refunded} units refunded "
            f"against a sale_item that only sold 5. status_codes={status_codes}"
        )
        assert not exceptions, f"Unhandled exceptions under concurrency: {exceptions}"
