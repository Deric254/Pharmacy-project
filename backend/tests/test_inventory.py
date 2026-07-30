"""
Inventory tests. The properties that matter:
  1. Low-stock and expiry detection reflect real ledger state, not a
     stale cached number.
  2. Adjustments can never take a batch negative, and always require
     a reason - there's no path to a silent quantity edit.
  3. Reconciliation flags real mismatches without "fixing" anything
     itself (detection only).
  4. A sale that drops a product below its reorder point actually
     publishes stock.low - proving the event hook, not just the query.
"""

import asyncio
import json
from datetime import date, timedelta

from app.core.database import AsyncSessionLocal
from app.core.events import CHANNEL
from app.core.redis_client import redis_client
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _make_product(name: str, reorder_point: int = 10, price: float = 5.0) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name=name, reorder_point=reorder_point, default_selling_price=price)
        db.add(product)
        await db.commit()
        return int(product.id)


async def _add_batch(
    product_id: int, qty: int, expiry: str = "2027-01-01", batch_number: str = "B1"
) -> int:
    async with AsyncSessionLocal() as db:
        batch = MedicineBatch(
            product_id=product_id,
            batch_number=batch_number,
            expiry_date=date.fromisoformat(expiry),
            qty_received=qty,
            qty_remaining=qty,
            cost_price=2.0,
        )
        db.add(batch)
        await db.flush()
        db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=MovementType.PURCHASE,
                quantity_delta=qty,
                created_by_user_id=None,
            )
        )
        await db.commit()
        return int(batch.id)


class TestLowStock:
    async def test_product_below_reorder_point_is_flagged(self, client, owner_user):
        product_id = await _make_product("Amoxicillin", reorder_point=20)
        await _add_batch(product_id, qty=5)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/low-stock", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        flagged_ids = {p["product_id"] for p in r.json()}
        assert product_id in flagged_ids

    async def test_product_above_reorder_point_not_flagged(self, client, owner_user):
        product_id = await _make_product("Paracetamol", reorder_point=5)
        await _add_batch(product_id, qty=50)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/low-stock", headers={"Authorization": f"Bearer {token}"}
        )
        flagged_ids = {p["product_id"] for p in r.json()}
        assert product_id not in flagged_ids

    async def test_product_with_zero_batches_counts_as_zero_stock(self, client, owner_user):
        product_id = await _make_product("Never Stocked", reorder_point=1)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/low-stock", headers={"Authorization": f"Bearer {token}"}
        )
        flagged = {p["product_id"]: p for p in r.json()}
        assert product_id in flagged
        assert flagged[product_id]["total_qty_available"] == 0

    async def test_requires_permission(self, client, employee_user):
        # Employee fixture only has sales.create + inventory.view in
        # this suite's seeded_roles -- inventory.view IS granted, so
        # this should succeed; verifies view access works for the
        # role that actually needs to check stock while selling.
        token = await _login(client, "joe", "pass1234")
        r = await client.get(
            "/api/v1/inventory/low-stock", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200


class TestExpiringBatches:
    async def test_batch_within_default_window_is_flagged(self, client, owner_user):
        product_id = await _make_product("Insulin")
        near_expiry = (date.today() + timedelta(days=10)).isoformat()
        await _add_batch(product_id, qty=20, expiry=near_expiry)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/expiring", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        product_ids = {b["product_id"] for b in r.json()}
        assert product_id in product_ids

    async def test_batch_far_in_future_not_flagged_by_default_window(self, client, owner_user):
        product_id = await _make_product("Vitamin C")
        far_expiry = (date.today() + timedelta(days=365)).isoformat()
        await _add_batch(product_id, qty=20, expiry=far_expiry)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/expiring", headers={"Authorization": f"Bearer {token}"}
        )
        product_ids = {b["product_id"] for b in r.json()}
        assert product_id not in product_ids

    async def test_custom_window_overrides_default(self, client, owner_user):
        product_id = await _make_product("Vitamin D")
        expiry_in_200_days = (date.today() + timedelta(days=200)).isoformat()
        await _add_batch(product_id, qty=20, expiry=expiry_in_200_days)

        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        default_window = await client.get("/api/v1/inventory/expiring", headers=headers)
        assert product_id not in {b["product_id"] for b in default_window.json()}

        wide_window = await client.get(
            "/api/v1/inventory/expiring?within_days=365", headers=headers
        )
        assert product_id in {b["product_id"] for b in wide_window.json()}

    async def test_fully_depleted_batch_not_flagged_even_if_expiring(self, client, owner_user):
        product_id = await _make_product("Depleted Product")
        near_expiry = (date.today() + timedelta(days=5)).isoformat()
        await _add_batch(product_id, qty=0, expiry=near_expiry)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/expiring", headers={"Authorization": f"Bearer {token}"}
        )
        product_ids = {b["product_id"] for b in r.json()}
        assert product_id not in product_ids


class TestValuation:
    async def test_valuation_matches_qty_times_cost(self, client, owner_user):
        product_id = await _make_product("Bandages")
        await _add_batch(product_id, qty=100)  # cost_price=2.0 in helper

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/valuation", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        product_row = next(p for p in r.json()["by_product"] if p["product_id"] == product_id)
        assert product_row["qty_on_hand"] == 100
        assert product_row["value"] == 200.0  # 100 * 2.0
        assert r.json()["total_value"] >= 200.0


class TestAdjustments:
    async def test_adjustment_requires_permission(self, client, employee_user):
        product_id = await _make_product("Gauze")
        batch_id = await _add_batch(product_id, qty=50)

        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/inventory/adjustments",
            json={"batch_id": batch_id, "quantity_delta": -5, "reason": "DAMAGED"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_adjustment_writes_ledger_and_updates_qty(self, client, owner_user):
        product_id = await _make_product("Cotton Wool")
        batch_id = await _add_batch(product_id, qty=50)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/inventory/adjustments",
            json={
                "batch_id": batch_id,
                "quantity_delta": -8,
                "reason": "DAMAGED",
                "notes": "Water damage in storage",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["qty_remaining_after"] == 42

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.id == batch_id)
            )
            assert batch_result.scalar_one().qty_remaining == 42

            ledger_result = await db.execute(
                select(StockMovement).where(
                    StockMovement.batch_id == batch_id,
                    StockMovement.movement_type == MovementType.ADJUSTMENT,
                )
            )
            adjustment_rows = ledger_result.scalars().all()
            assert len(adjustment_rows) == 1
            assert adjustment_rows[0].quantity_delta == -8
            assert "DAMAGED" in adjustment_rows[0].reason
            assert "Water damage" in adjustment_rows[0].reason

    async def test_adjustment_cannot_take_batch_negative(self, client, owner_user):
        product_id = await _make_product("Syringes")
        batch_id = await _add_batch(product_id, qty=5)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/inventory/adjustments",
            json={"batch_id": batch_id, "quantity_delta": -10, "reason": "THEFT_OR_LOSS"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.id == batch_id)
            )
            assert batch_result.scalar_one().qty_remaining == 5  # unchanged

    async def test_zero_quantity_delta_rejected_by_schema(self, client, owner_user):
        product_id = await _make_product("Masks")
        batch_id = await _add_batch(product_id, qty=20)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/inventory/adjustments",
            json={"batch_id": batch_id, "quantity_delta": 0, "reason": "MISCOUNT"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

    async def test_positive_adjustment_can_correct_an_undercount(self, client, owner_user):
        product_id = await _make_product("Thermometers")
        batch_id = await _add_batch(product_id, qty=10)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/inventory/adjustments",
            json={"batch_id": batch_id, "quantity_delta": 3, "reason": "MISCOUNT"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["qty_remaining_after"] == 13

    async def test_two_concurrent_adjustments_both_apply(self, client, owner_user):
        """
        The actual bug this closes: adjustment used
        SELECT...FOR UPDATE, which SQLite silently drops entirely (the
        same false-safety pattern already found and fixed for stock
        decrements, PO transitions, refund restocks, stock-take
        closes, and loyalty points this session). Two adjustments to
        the same batch landing at once -- two managers independently
        correcting the same miscount, say -- could see the same
        starting quantity and the second commit would silently
        overwrite the first's correct result.
        """
        product_id = await _make_product("Concurrent Adjustment Product")
        batch_id = await _add_batch(product_id, qty=50)
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        async def adjust(delta: int):
            return await client.post(
                "/api/v1/inventory/adjustments",
                json={"batch_id": batch_id, "quantity_delta": delta, "reason": "MISCOUNT"},
                headers=headers,
            )

        results = await asyncio.gather(adjust(5), adjust(3), return_exceptions=True)
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        assert status_codes.count(201) == 2  # both adjustments genuinely succeed

        async with AsyncSessionLocal() as db:
            batch = await db.get(MedicineBatch, batch_id)
            # Started at 50, +5 and +3 both must land -- not 53 or 55
            # (one adjustment silently lost), exactly 58.
            assert batch.qty_remaining == 58


class TestReconciliation:
    async def test_correctly_maintained_batch_has_no_issues(self, client, owner_user):
        product_id = await _make_product("Reconciled Product")
        await _add_batch(product_id, qty=30)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/reconcile", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert r.json() == []

    async def test_detects_a_deliberately_corrupted_batch(self, client, owner_user):
        product_id = await _make_product("Corrupted Product")
        batch_id = await _add_batch(product_id, qty=30)

        # Simulate a direct DB edit bypassing the application layer --
        # qty_remaining changed without a matching ledger row.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.id == batch_id)
            )
            batch = batch_result.scalar_one()
            batch.qty_remaining = 999
            await db.commit()

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/inventory/reconcile", headers={"Authorization": f"Bearer {token}"}
        )
        issues = {i["batch_id"]: i for i in r.json()}
        assert batch_id in issues
        assert issues[batch_id]["qty_remaining"] == 999
        assert issues[batch_id]["ledger_sum"] == 30
        assert issues[batch_id]["discrepancy"] == 969


class TestSaleTriggeredLowStockEvent:
    async def test_sale_dropping_below_reorder_point_publishes_stock_low(
        self, client, employee_user
    ):
        product_id = await _make_product("Event Test Product", reorder_point=10, price=5.0)
        await _add_batch(product_id, qty=12)

        pubsub = redis_client.pubsub()
        await pubsub.subscribe(CHANNEL)
        await pubsub.get_message(timeout=1)  # discard the subscribe confirmation

        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 25.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201  # 12 - 5 = 7, below reorder_point of 10

        found_stock_low = False
        for _ in range(10):
            message = await pubsub.get_message(timeout=1)
            if message and message["type"] == "message":
                envelope = json.loads(message["data"])
                if envelope["event_type"] == "stock.low":
                    assert envelope["payload"]["product_id"] == product_id
                    assert envelope["payload"]["qty_remaining"] == 7
                    found_stock_low = True
                    break

        await pubsub.unsubscribe(CHANNEL)
        assert (
            found_stock_low
        ), "Expected a stock.low event after the sale dropped below reorder point"
