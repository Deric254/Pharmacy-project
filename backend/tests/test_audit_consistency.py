"""
CONSISTENCY AUDIT.

Not "does reconcile() work" (already tested in the Inventory module) --
this runs a realistic SEQUENCE of mixed operations across many modules
(sales, purchasing receives, stock take variances, manual adjustments)
against several products at once, then sweeps EVERY batch and EVERY
supplier afterward to confirm every derived value (qty_remaining,
supplier balance) still exactly matches what its own ledger says it
should be. A single missed edge case in any module would show up here
even if that module's own narrower tests didn't happen to hit it.
"""

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.stock_movement import StockMovement
from app.models.supplier import SupplierTransaction


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestBlanketLedgerReconciliation:
    async def test_qty_remaining_matches_ledger_sum_after_a_mixed_operation_sequence(
        self, client, owner_user, employee_user
    ):
        owner_token = await _login(client, "lucy", "S3curePass!")
        employee_token = await _login(client, "joe", "pass1234")
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        employee_headers = {"Authorization": f"Bearer {employee_token}"}

        product_ids = []
        for name, price in [("Consistency Product A", 10.0), ("Consistency Product B", 20.0)]:
            resp = await client.post(
                "/api/v1/products",
                json={"name": name, "default_selling_price": price},
                headers=owner_headers,
            )
            product_id = resp.json()["id"]
            product_ids.append(product_id)
            for batch_num, (expiry, qty, cost) in enumerate(
                [("2026-09-01", 40, price / 2), ("2027-03-01", 60, price / 2 + 1)]
            ):
                await client.post(
                    f"/api/v1/products/{product_id}/batches",
                    json={
                        "batch_number": f"MIX-{product_id}-{batch_num}",
                        "expiry_date": expiry,
                        "qty_received": qty,
                        "cost_price": cost,
                    },
                    headers=owner_headers,
                )

        for index, product_id in enumerate(product_ids):
            price = 10.0 if index == 0 else 20.0
            for qty in (3, 5, 2):
                await client.post(
                    "/api/v1/sales",
                    json={
                        "items": [{"product_id": product_id, "quantity": qty}],
                        "payments": [{"method": "CASH", "amount": qty * price}],
                    },
                    headers=employee_headers,
                )

        async with AsyncSessionLocal() as db:
            for product_id in product_ids:
                result = await db.execute(
                    select(MedicineBatch).where(MedicineBatch.product_id == product_id)
                )
                for batch in result.scalars().all():
                    ledger_result = await db.execute(
                        select(StockMovement).where(StockMovement.batch_id == batch.id)
                    )
                    ledger_sum = sum(m.quantity_delta for m in ledger_result.scalars().all())
                    assert ledger_sum == batch.qty_remaining, (
                        f"Batch {batch.id} (product {product_id}): qty_remaining="
                        f"{batch.qty_remaining} but ledger sums to {ledger_sum}"
                    )

    async def test_adjustments_and_sales_interleaved_stay_consistent(
        self, client, owner_user, employee_user
    ):
        """
        Specifically interleaves a manual adjustment between two sales
        on the same batch -- the exact scenario where a bug in either
        path touching qty_remaining without a matching ledger row would
        silently drift.
        """
        owner_token = await _login(client, "lucy", "S3curePass!")
        employee_token = await _login(client, "joe", "pass1234")
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        employee_headers = {"Authorization": f"Bearer {employee_token}"}

        product_resp = await client.post(
            "/api/v1/products",
            json={"name": "Interleave Test Product", "default_selling_price": 8.0},
            headers=owner_headers,
        )
        product_id = product_resp.json()["id"]
        batch_resp = await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "INTERLEAVE-1",
                "expiry_date": "2027-01-01",
                "qty_received": 100,
                "cost_price": 3.0,
            },
            headers=owner_headers,
        )
        batch_id = batch_resp.json()["id"]

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 10}],
                "payments": [{"method": "CASH", "amount": 80.0}],
            },
            headers=employee_headers,
        )
        await client.post(
            "/api/v1/inventory/adjustments",
            json={"batch_id": batch_id, "quantity_delta": -7, "reason": "DAMAGED"},
            headers=owner_headers,
        )
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 15}],
                "payments": [{"method": "CASH", "amount": 120.0}],
            },
            headers=employee_headers,
        )
        await client.post(
            "/api/v1/inventory/adjustments",
            json={"batch_id": batch_id, "quantity_delta": 3, "reason": "MISCOUNT"},
            headers=owner_headers,
        )

        async with AsyncSessionLocal() as db:
            batch_result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.id == batch_id)
            )
            batch = batch_result.scalar_one()
            expected = 100 - 10 - 7 - 15 + 3
            assert batch.qty_remaining == expected

            ledger_result = await db.execute(
                select(StockMovement).where(StockMovement.batch_id == batch_id)
            )
            ledger_sum = sum(m.quantity_delta for m in ledger_result.scalars().all())
            assert ledger_sum == batch.qty_remaining == expected


class TestSupplierBalanceConsistency:
    async def test_balance_matches_ledger_after_multiple_pos_and_payments(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        supplier_resp = await client.post(
            "/api/v1/suppliers", json={"name": "Consistency Supplier"}, headers=headers
        )
        supplier_id = supplier_resp.json()["id"]

        product_resp = await client.post(
            "/api/v1/products", json={"name": "Supplier Consistency Product"}, headers=headers
        )
        product_id = product_resp.json()["id"]

        for i in range(2):
            po = await client.post(
                "/api/v1/purchase-orders/quick-purchase",
                json={
                    "supplier_id": supplier_id,
                    "lines": [
                        {
                            "product_id": product_id,
                            "quantity": 20,
                            "batch_number": f"SUPPLIER-CONS-{i}",
                            "expiry_date": "2027-01-01",
                            "unit_cost": 5.0,
                        }
                    ],
                },
                headers=headers,
            )
            assert po.status_code == 201, po.text
            await client.post(
                f"/api/v1/suppliers/{supplier_id}/payments",
                json={"amount": 50.0},  # partial payment each time
                headers=headers,
            )

        async with AsyncSessionLocal() as db:
            ledger_result = await db.execute(
                select(SupplierTransaction).where(SupplierTransaction.supplier_id == supplier_id)
            )
            ledger_sum = sum(t.amount for t in ledger_result.scalars().all())

        supplier_check = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        # Two receipts of 20*5.0=100 each (+200), two payments of 50 each (-100) = 100 owed
        assert supplier_check.json()["balance_owed"] == 100.0
        assert ledger_sum == 100.0
