"""
ACCURACY AUDIT.

Not "is the calculation approximately right" -- exact expected values,
computed independently by hand in each test's comment, checked against
the system's actual output to the cent/unit. Extends beyond the
module-level accuracy tests (e.g. Reports' two-batch profit test) with
messier, more realistic multi-batch/discount/boundary scenarios.
"""

from datetime import date, timedelta

from app.core.database import AsyncSessionLocal
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
        today = date.today().isoformat()
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
