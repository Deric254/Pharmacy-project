"""
Sales tests. The two properties that actually matter for a POS:
  1. A sale either fully succeeds (stock decremented, ledger written,
     payment recorded) or fully fails (nothing touched) - no partial
     sales, ever.
  2. Checkout draws from the nearest-expiry batch first, same as the
     dedicated FEFO tests prove at the service level - this file
     proves it end-to-end through the real HTTP + DB path.
"""

import asyncio
from datetime import date

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.role import Role
from app.models.sale import Sale
from app.models.stock_movement import StockMovement
from app.models.user import User
from tests.conftest import running_on_sqlite


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _make_product_with_batch(
    price: float = 10.0, qty: int = 20, expiry: str = "2027-01-01"
) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name="Amoxicillin 500mg", default_selling_price=price)
        db.add(product)
        await db.flush()
        db.add(
            MedicineBatch(
                product_id=product.id,
                batch_number="B1",
                expiry_date=date.fromisoformat(expiry),
                qty_received=qty,
                qty_remaining=qty,
                cost_price=price / 2,
            )
        )
        await db.commit()
        return int(product.id)


class TestCreateSale:
    async def test_requires_permission(self, client, seeded_roles):
        async with AsyncSessionLocal() as db:
            role_result = await db.execute(select(Role).where(Role.name == "Employee"))
            role = role_result.scalar_one()
            role.permissions = []  # strip sales.create for this one test
            await db.commit()

            u = User(
                full_name="No Permission",
                username="noperm",
                hashed_password=hash_password("pass1234"),
                role_id=role.id,
            )
            db.add(u)
            await db.commit()

        token = await _login(client, "noperm", "pass1234")
        r = await client.post(
            "/api/v1/sales",
            json={"items": [], "payments": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_successful_sale_decrements_stock_and_writes_ledger(self, client, employee_user):
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        token = await _login(client, "joe", "pass1234")

        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 50.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["subtotal"] == 50.0
        assert body["total_amount"] == 50.0
        assert len(body["items"]) == 1
        assert body["items"][0]["quantity"] == 5

        async with AsyncSessionLocal() as db:
            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batch = batch_result.scalar_one()
            assert batch.qty_remaining == 15  # 20 - 5

            ledger_result = await db.execute(
                select(StockMovement).where(StockMovement.reference == f"sale:{body['id']}")
            )
            ledger_rows = ledger_result.scalars().all()
            assert len(ledger_rows) == 1
            assert ledger_rows[0].quantity_delta == -5

    async def test_insufficient_stock_rolls_back_completely(self, client, employee_user):
        product_id = await _make_product_with_batch(price=10.0, qty=3)
        token = await _login(client, "joe", "pass1234")

        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 10}],
                "payments": [{"method": "CASH", "amount": 100.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409

        async with AsyncSessionLocal() as db:
            # Nothing touched: no sale row, batch quantity unchanged.
            sales_result = await db.execute(select(Sale))
            assert sales_result.scalars().all() == []

            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batch = batch_result.scalar_one()
            assert batch.qty_remaining == 3  # unchanged

    async def test_payment_mismatch_rejected_and_rolled_back(self, client, employee_user):
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        token = await _login(client, "joe", "pass1234")

        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 40.0}],  # should be 50
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

        async with AsyncSessionLocal() as db:
            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batch = batch_result.scalar_one()
            assert batch.qty_remaining == 20  # unchanged - nothing partially applied

    async def test_duplicate_product_lines_rejected_by_schema(self, client, employee_user):
        product_id = await _make_product_with_batch()
        token = await _login(client, "joe", "pass1234")

        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [
                    {"product_id": product_id, "quantity": 1},
                    {"product_id": product_id, "quantity": 2},
                ],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

    async def test_checkout_draws_nearest_expiry_batch_first(self, client, employee_user):
        async with AsyncSessionLocal() as db:
            product = Product(name="Paracetamol", default_selling_price=5.0)
            db.add(product)
            await db.flush()
            db.add(
                MedicineBatch(
                    product_id=product.id,
                    batch_number="FAR",
                    expiry_date=date(2028, 1, 1),
                    qty_received=50,
                    qty_remaining=50,
                    cost_price=2.0,
                )
            )
            db.add(
                MedicineBatch(
                    product_id=product.id,
                    batch_number="NEAR",
                    expiry_date=date(2026, 8, 1),
                    qty_received=10,
                    qty_remaining=10,
                    cost_price=2.0,
                )
            )
            await db.commit()
            product_id = int(product.id)

        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 4}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batches = {b.batch_number: b.qty_remaining for b in result.scalars().all()}
            assert batches["NEAR"] == 6  # 10 - 4, drawn first
            assert batches["FAR"] == 50  # untouched


class TestGetSale:
    async def test_get_sale_returns_full_detail(self, client, employee_user):
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 2}],
                "payments": [{"method": "MPESA", "amount": 20.0, "reference": "QWE123"}],
            },
            headers=headers,
        )
        sale_id = create_resp.json()["id"]

        r = await client.get(f"/api/v1/sales/{sale_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["payments"][0]["method"] == "MPESA"
        assert r.json()["payments"][0]["reference"] == "QWE123"

    async def test_get_unknown_sale_returns_404(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.get("/api/v1/sales/999999", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404


class TestConcurrentSales:
    async def test_two_concurrent_sales_cannot_oversell_the_same_stock(self, client, employee_user):
        """
        Two 'cashiers' try to sell from a batch with only 10 units at
        the same time, each requesting 8. Both cannot succeed - only
        one should, and total stock sold must never exceed what
        existed. This is the row-level-locking guarantee, exercised
        under real concurrency rather than asserted from the single-
        threaded FEFO unit tests alone.

        SQLite does not support SELECT...FOR UPDATE at all -- SQLAlchemy
        silently omits the clause for that dialect (confirmed by
        inspecting the compiled SQL), so this test cannot exercise the
        real locking mechanism there and is skipped. It has been run
        and passed against real MySQL/InnoDB (matching both production
        and the MySQL service in ci.yml): two concurrent 8-unit requests
        against 10 units in stock produced exactly one 201 and one 409,
        with the batch correctly left at 2 remaining.
        """
        if running_on_sqlite():
            import pytest

            pytest.skip(
                "SQLite has no row-level locking; SELECT...FOR UPDATE is silently "
                "dropped by SQLAlchemy's SQLite dialect. Verified against real "
                "MySQL/InnoDB instead - see docstring."
            )

        product_id = await _make_product_with_batch(price=10.0, qty=10)
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        async def attempt_sale():
            return await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 8}],
                    "payments": [{"method": "CASH", "amount": 80.0}],
                },
                headers=headers,
            )

        results = await asyncio.gather(attempt_sale(), attempt_sale(), return_exceptions=True)
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]

        assert status_codes.count(201) == 1
        assert status_codes.count(409) == 1

        async with AsyncSessionLocal() as db:
            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batch = batch_result.scalar_one()
            assert batch.qty_remaining == 2  # 10 - 8, never negative, never double-sold
