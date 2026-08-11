"""
Refund tests. sales.refund existed as a grantable permission with
zero implementation before this module -- these tests cover the two
invariants that actually matter for "every coin and every drug
accounted for":
  1. The same sold unit can never be refunded twice, even across
     multiple separate refund requests against the same sale.
  2. Restocking writes an actual StockMovement ledger row and updates
     the batch, exactly like a sale does -- a refund is a stock event,
     not just a money event.
"""

import asyncio
from datetime import date

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.role import Role
from app.models.stock_movement import MovementType, StockMovement
from app.models.user import User


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


async def _make_sale(client, token: str, product_id: int, quantity: int, unit_price: float):
    r = await client.post(
        "/api/v1/sales",
        json={
            "items": [{"product_id": product_id, "quantity": quantity}],
            "payments": [{"method": "CASH", "amount": unit_price * quantity}],
            "discount_amount": 0,
            "customer_id": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestCreateRefund:
    async def test_requires_sales_refund_permission(self, client, seeded_roles):
        # Employee role in seeded_roles only has sales.create, not
        # sales.refund -- confirm the two are genuinely separate gates.
        async with AsyncSessionLocal() as db:
            role_result = await db.execute(select(Role).where(Role.name == "Employee"))
            role = role_result.scalar_one()
            u = User(
                full_name="Cashier Only",
                username="cashieronly",
                hashed_password=hash_password("pass1234"),
                role_id=role.id,
            )
            db.add(u)
            await db.commit()

        token = await _login(client, "cashieronly", "pass1234")
        product_id = await _make_product_with_batch()
        sale = await _make_sale(client, token, product_id, 2, 10.0)

        r = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale["items"][0]["batch_id"], "quantity": 1}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_full_refund_with_restock_reverses_stock_and_pays_back_full_amount(
        self, client, owner_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        sale = await _make_sale(client, token, product_id, 5, 10.0)
        sale_item = sale["items"][0]

        r = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [
                    {
                        "sale_item_id": sale_item["id"],
                        "quantity": 5,
                        "restock": True,
                    }
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["total_amount"] == 50.0
        assert body["items"][0]["restocked"] is True

        async with AsyncSessionLocal() as db:
            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.id == sale_item["batch_id"])
            )
            batch = batch_result.scalar_one()
            assert batch.qty_remaining == 20  # 20 - 5 sold + 5 returned

            movement_result = await db.execute(
                select(StockMovement).where(
                    StockMovement.batch_id == sale_item["batch_id"],
                    StockMovement.movement_type == MovementType.RETURN,
                )
            )
            movements = movement_result.scalars().all()
            assert len(movements) == 1
            assert movements[0].quantity_delta == 5

    async def test_two_concurrent_refunds_against_the_same_batch_both_restock(
        self, client, owner_user
    ):
        """
        The actual bug this closes: restocking used to be a Python-level
        `batch.qty_remaining += quantity` guarded only by
        SELECT...FOR UPDATE, which SQLite silently drops entirely (the
        same false-safety pattern already found and fixed for the
        stock-decrement and PO-transition races this session). Two
        refunds landing on the same batch at once could see the same
        starting quantity and the second commit would silently
        overwrite the first's correct increment, losing a real restock
        with no error at all.
        """
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sale_a = await _make_sale(client, token, product_id, 5, 10.0)
        sale_b = await _make_sale(client, token, product_id, 5, 10.0)

        async def refund(sale: dict) -> object:
            sale_item = sale["items"][0]
            return await client.post(
                f"/api/v1/sales/{sale['id']}/refunds",
                json={
                    "reason": "CUSTOMER_RETURN",
                    "method": "CASH",
                    "items": [{"sale_item_id": sale_item["id"], "quantity": 5}],
                },
                headers=headers,
            )

        results = await asyncio.gather(refund(sale_a), refund(sale_b), return_exceptions=True)
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        assert status_codes.count(201) == 2  # both refunds genuinely succeed, no lost update

        async with AsyncSessionLocal() as db:
            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batch = batch_result.scalar_one()
            # Started at 20, sold 10 total (two sales of 5), refunded
            # both back -- must be 20 again, not 15 (one restock lost).
            assert batch.qty_remaining == 20

    async def test_refund_without_restock_pays_back_but_leaves_stock_alone(
        self, client, owner_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        sale = await _make_sale(client, token, product_id, 5, 10.0)
        sale_item = sale["items"][0]

        r = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "DAMAGED",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 5, "restock": False}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["items"][0]["restocked"] is False

        async with AsyncSessionLocal() as db:
            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.id == sale_item["batch_id"])
            )
            batch = batch_result.scalar_one()
            assert batch.qty_remaining == 15  # 20 - 5 sold, refund did NOT restock

    async def test_cannot_refund_more_than_was_sold(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        sale = await _make_sale(client, token, product_id, 3, 10.0)
        sale_item = sale["items"][0]

        r = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 4}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409

    async def test_cannot_refund_the_same_units_twice_across_two_refunds(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        sale = await _make_sale(client, token, product_id, 5, 10.0)
        sale_item = sale["items"][0]

        first = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 3}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 201

        # Only 2 units remain refundable (5 sold - 3 already refunded).
        second_too_many = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 3}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second_too_many.status_code == 409

        second_ok = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 2}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second_ok.status_code == 201

    async def test_refunding_a_sale_item_from_a_different_sale_is_rejected(
        self, client, owner_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        sale_one = await _make_sale(client, token, product_id, 2, 10.0)
        sale_two = await _make_sale(client, token, product_id, 2, 10.0)

        r = await client.post(
            f"/api/v1/sales/{sale_two['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_one["items"][0]["id"], "quantity": 1}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    async def test_refunding_an_unknown_sale_returns_404(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/sales/999999/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": 1, "quantity": 1}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


class TestListRefunds:
    async def test_list_refunds_for_a_sale(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        product_id = await _make_product_with_batch(price=10.0, qty=20)
        sale = await _make_sale(client, token, product_id, 5, 10.0)
        sale_item = sale["items"][0]

        await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN",
                "method": "CASH",
                "items": [{"sale_item_id": sale_item["id"], "quantity": 2}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        r = await client.get(
            f"/api/v1/sales/{sale['id']}/refunds",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["total_amount"] == 20.0
