"""
Purchase reversal. The properties that matter:
  1. A reversal is all-or-nothing: stock out, supplier debt cancelled, purchase
     marked REVERSED, audit written -- or nothing at all changes.
  2. Only a batch still exactly as the purchase left it can be taken back --
     and then it goes back to exactly its earlier state, cost price included.
     Anything that moved since (a sale, an adjustment, a later delivery, a
     changed cost, an open stock take) is refused; nothing is guessed.
  3. It happens at most once, even when requested simultaneously.
  4. Nothing is edited or deleted -- opposite entries are appended -- and the
     stock ledger still reconciles.
  5. Only the owner may do it; a reason is mandatory.
  6. The supplier's figures treat a reversed purchase as never bought, without
     disturbing payments.
"""

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select, text

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus
from app.models.stock_movement import MovementType, StockMovement
from app.models.stock_take import StockTake
from app.models.supplier import Supplier, SupplierTransaction

BACKEND_DIR = Path(__file__).resolve().parent.parent


async def _login(client, username: str, password: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _owner(client) -> dict[str, str]:
    return await _login(client, "lucy", "S3curePass!")


async def _make_supplier(name: str = "Reversal Supplier") -> int:
    async with AsyncSessionLocal() as db:
        supplier = Supplier(name=name)
        db.add(supplier)
        await db.commit()
        return int(supplier.id)


async def _make_product(name: str) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name=name)
        db.add(product)
        await db.commit()
        return int(product.id)


def _line(product_id: int, qty: int, batch: str, cost: float = 8.0, price: float = 15.0) -> dict:
    return {
        "product_id": product_id,
        "quantity": qty,
        "batch_number": batch,
        "expiry_date": "2097-06-30",
        "unit_cost": cost,
        "selling_price": price,
    }


async def _purchase(client, headers, supplier_id: int, lines: list[dict]) -> int:
    r = await client.post(
        "/api/v1/purchase-orders/quick-purchase",
        json={"supplier_id": supplier_id, "lines": lines},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _reverse(client, headers, po_id: int, reason: str = "Entered by mistake"):
    return await client.post(
        f"/api/v1/purchase-orders/{po_id}/reverse", json={"reason": reason}, headers=headers
    )


async def _batch_ids(po_id: int) -> list[int]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(PurchaseOrderItem.batch_id)
            .where(PurchaseOrderItem.purchase_order_id == po_id)
            .order_by(PurchaseOrderItem.id)
        )
        return [int(b) for (b,) in rows.all()]


async def _qty_remaining(batch_id: int) -> int:
    async with AsyncSessionLocal() as db:
        return int((await db.get(MedicineBatch, batch_id)).qty_remaining)  # type: ignore[union-attr]


async def _batch(batch_id: int) -> tuple[int, int, float]:
    """(qty_remaining, qty_received, cost_price) as stored."""
    async with AsyncSessionLocal() as db:
        batch = await db.get(MedicineBatch, batch_id)
        assert batch is not None
        return int(batch.qty_remaining), int(batch.qty_received), float(batch.cost_price)


async def _sell(batch_id: int, qty: int) -> None:
    """What a sale does to the books: take stock down and log it."""
    async with AsyncSessionLocal() as db:
        batch = await db.get(MedicineBatch, batch_id)
        assert batch is not None
        batch.qty_remaining -= qty
        db.add(
            StockMovement(batch_id=batch_id, movement_type=MovementType.SALE, quantity_delta=-qty)
        )
        await db.commit()


async def _movements(batch_id: int) -> list[tuple[str, int, str | None]]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(
                StockMovement.movement_type, StockMovement.quantity_delta, StockMovement.reference
            )
            .where(StockMovement.batch_id == batch_id)
            .order_by(StockMovement.id)
        )
        return [(str(t), int(d), r) for t, d, r in rows.all()]


async def _ledger(supplier_id: int) -> list[SupplierTransaction]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(SupplierTransaction)
            .where(SupplierTransaction.supplier_id == supplier_id)
            .order_by(SupplierTransaction.id)
        )
        return list(rows.scalars().all())


async def _po(po_id: int) -> PurchaseOrder:
    async with AsyncSessionLocal() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        return po


async def _audit_actions() -> list[str]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(AuditLog.action).order_by(AuditLog.id))
        return [a for (a,) in rows.all()]


async def _balance(client, headers, supplier_id: int) -> float:
    r = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
    return float(r.json()["balance_owed"])


async def _assert_nothing_changed(supplier_id: int, po_id: int, ledger_rows: int) -> None:
    assert (await _po(po_id)).status == PurchaseOrderStatus.RECEIVED
    assert len(await _ledger(supplier_id)) == ledger_rows
    actions = await _audit_actions()
    assert "purchase_order.reversed" not in actions
    async with AsyncSessionLocal() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.reference == f"po-reversal:{po_id}")
            )
        ).scalar_one()
    assert count == 0


class TestReversalHappyPath:
    async def test_stock_leaves_debt_is_cancelled_and_history_is_kept(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Amoxicillin")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "REV-1")])
        (batch_id,) = await _batch_ids(po_id)
        assert await _balance(client, headers, supplier_id) == 80.0

        r = await _reverse(client, headers, po_id, "Wrong supplier")

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "REVERSED"
        assert body["reversal_reason"] == "Wrong supplier"
        assert body["reversed_at"] is not None
        assert len(body["items"]) == 1  # the purchase's own record is untouched

        assert await _batch(batch_id) == (0, 0, 8.0)  # the receipt is cancelled, not just emptied
        assert await _movements(batch_id) == [
            ("PURCHASE", 10, f"po:{po_id}"),
            ("ADJUSTMENT", -10, f"po-reversal:{po_id}"),
        ]
        assert await _balance(client, headers, supplier_id) == 0.0
        ledger = await _ledger(supplier_id)
        assert [t.amount for t in ledger] == [80.0, -80.0]
        assert ledger[1].reverses_transaction_id == ledger[0].id
        assert ledger[1].notes == "Wrong supplier"
        assert "purchase_order.reversed" in await _audit_actions()

        # The stock ledger still agrees with every batch's quantity.
        recon = await client.get("/api/v1/inventory/reconcile", headers=headers)
        assert recon.json() == []

    async def test_a_purchase_with_several_lines_is_reversed_as_a_whole(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        a = await _make_product("Product A")
        b = await _make_product("Product B")
        po_id = await _purchase(
            client,
            headers,
            supplier_id,
            [_line(a, 10, "ML-A"), _line(b, 4, "ML-B", cost=25.0, price=40.0)],
        )
        batches = await _batch_ids(po_id)
        assert await _balance(client, headers, supplier_id) == 180.0

        assert (await _reverse(client, headers, po_id)).status_code == 200

        assert [await _qty_remaining(x) for x in batches] == [0, 0]
        assert await _balance(client, headers, supplier_id) == 0.0
        assert len(await _ledger(supplier_id)) == 2  # one charge, one cancellation

    async def test_the_same_batch_on_two_lines_of_one_purchase_reverses(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Twice On One Receipt")
        po_id = await _purchase(
            client,
            headers,
            supplier_id,
            [_line(product_id, 5, "SAME-1"), _line(product_id, 7, "SAME-1")],
        )
        batch_ids = await _batch_ids(po_id)
        assert batch_ids[0] == batch_ids[1]
        assert await _qty_remaining(batch_ids[0]) == 12

        r = await _reverse(client, headers, po_id)

        assert r.status_code == 200, r.text
        assert await _qty_remaining(batch_ids[0]) == 0
        deltas = [d for _, d, _ in await _movements(batch_ids[0])]
        assert sum(deltas) == 0
        assert (await client.get("/api/v1/inventory/reconcile", headers=headers)).json() == []

    async def test_a_free_purchase_has_no_debt_to_cancel(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Free Sample")
        po_id = await _purchase(
            client, headers, supplier_id, [_line(product_id, 3, "FREE-1", cost=0.0)]
        )
        assert await _ledger(supplier_id) == []

        assert (await _reverse(client, headers, po_id)).status_code == 200
        assert await _ledger(supplier_id) == []

    async def test_the_batch_can_be_received_again_afterwards(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Received Twice")
        first = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "AGAIN-1")])
        (batch_id,) = await _batch_ids(first)
        await _reverse(client, headers, first)

        second = await _purchase(
            client, headers, supplier_id, [_line(product_id, 6, "AGAIN-1", cost=9.0)]
        )

        assert await _batch_ids(second) == [batch_id]
        # Nothing left of the old receipt or the old cost.
        assert await _batch(batch_id) == (6, 6, 9.0)
        assert await _balance(client, headers, supplier_id) == 54.0
        assert (await client.get("/api/v1/inventory/reconcile", headers=headers)).json() == []


class TestRestockReversal:
    async def test_a_restock_goes_back_to_exactly_its_earlier_cost_and_quantity(
        self, client, owner_user
    ):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Restocked")
        first = await _purchase(
            client, headers, supplier_id, [_line(product_id, 1, "RS-1", cost=0.01)]
        )
        (batch_id,) = await _batch_ids(first)
        second = await _purchase(
            client, headers, supplier_id, [_line(product_id, 3, "RS-1", cost=0.02)]
        )
        # (1*1 + 3*2) / 4 = 1.75 cents, stored as 2: a weighted average that
        # cannot be turned back by arithmetic -- only from what was recorded.
        assert await _batch(batch_id) == (4, 4, 0.02)

        assert (await _reverse(client, headers, second)).status_code == 200

        assert await _batch(batch_id) == (1, 1, 0.01)
        assert (await client.get("/api/v1/inventory/reconcile", headers=headers)).json() == []
        assert await _balance(client, headers, supplier_id) == 0.01  # only the first purchase

        assert (await _reverse(client, headers, first)).status_code == 200
        assert await _batch(batch_id) == (0, 0, 0.01)

    async def test_deliveries_into_one_batch_are_undone_latest_first(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Layered")
        first = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "LAY-1")])
        second = await _purchase(
            client, headers, supplier_id, [_line(product_id, 5, "LAY-1", cost=12.0)]
        )
        (batch_id,) = await _batch_ids(first)

        too_early = await _reverse(client, headers, first)
        assert too_early.status_code == 409
        assert "Reverse later deliveries first" in too_early.json()["detail"]
        await _assert_nothing_changed(supplier_id, first, ledger_rows=2)

        assert (await _reverse(client, headers, second)).status_code == 200
        assert await _batch(batch_id) == (10, 10, 8.0)
        assert (await _reverse(client, headers, first)).status_code == 200
        assert await _batch(batch_id) == (0, 0, 8.0)
        assert await _balance(client, headers, supplier_id) == 0.0

    async def test_stock_sold_before_the_purchase_does_not_block_it(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Sold First")
        first = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "SF-1")])
        (batch_id,) = await _batch_ids(first)
        await _sell(batch_id, 6)
        second = await _purchase(
            client, headers, supplier_id, [_line(product_id, 10, "SF-1", cost=12.0)]
        )
        assert (await _batch(batch_id))[0] == 14

        assert (await _reverse(client, headers, second)).status_code == 200

        assert await _batch(batch_id) == (4, 10, 8.0)  # the 6 sold stay sold
        assert (await client.get("/api/v1/inventory/reconcile", headers=headers)).json() == []

    async def test_several_lines_into_one_existing_batch_are_all_undone(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Two Lines Into Existing")
        first = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "TL-1")])
        (batch_id,) = await _batch_ids(first)
        second = await _purchase(
            client,
            headers,
            supplier_id,
            [_line(product_id, 3, "TL-1", cost=12.0), _line(product_id, 2, "TL-1", cost=16.0)],
        )
        assert (await _batch(batch_id))[0] == 15

        assert (await _reverse(client, headers, second)).status_code == 200

        assert await _batch(batch_id) == (10, 10, 8.0)
        deltas = [d for _, d, _ in await _movements(batch_id)]
        assert sum(deltas) == 10
        assert (await client.get("/api/v1/inventory/reconcile", headers=headers)).json() == []

    async def test_each_restock_line_records_the_cost_it_blended_into(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Snapshot")
        first = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "SNAP-1")])
        second = await _purchase(
            client, headers, supplier_id, [_line(product_id, 10, "SNAP-1", cost=12.0)]
        )

        async with AsyncSessionLocal() as db:
            rows = {
                po_id: (item.batch_cost_before, item.batch_cost_after)
                for po_id, item in (
                    await db.execute(select(PurchaseOrderItem.purchase_order_id, PurchaseOrderItem))
                ).tuples()
            }
        assert rows[first] == (None, None)  # created its batch: nothing to restore
        assert rows[second] == (8.0, 10.0)

    async def test_the_recorded_costs_chain_under_simultaneous_restocks(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Concurrent Restocks")
        await _purchase(
            client, headers, supplier_id, [_line(product_id, 10, "CR-1", cost=8.0, price=50.0)]
        )

        await asyncio.gather(
            *[
                _purchase(
                    client,
                    headers,
                    supplier_id,
                    [_line(product_id, 3 + i, "CR-1", cost=9.0 + i, price=50.0)],
                )
                for i in range(8)
            ]
        )

        async with AsyncSessionLocal() as db:
            items = list(
                (
                    await db.execute(
                        select(PurchaseOrderItem)
                        .where(PurchaseOrderItem.batch_cost_before.is_not(None))
                        .order_by(PurchaseOrderItem.id)
                    )
                ).scalars()
            )
            batch_cost = (await db.execute(select(MedicineBatch.cost_price))).scalar_one()
        assert len(items) == 8
        assert items[0].batch_cost_before == 8.0
        for earlier, later in zip(items, items[1:], strict=False):
            # Each restock saw exactly what the previous one left -- no gap, no overlap.
            assert later.batch_cost_before == earlier.batch_cost_after
        assert items[-1].batch_cost_after == batch_cost


class TestReversalRefusedForTamperedOrOldRecords:
    async def test_a_cost_changed_after_the_purchase_blocks_it(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Cost Corrected")
        first = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "CC-1")])
        second = await _purchase(
            client, headers, supplier_id, [_line(product_id, 10, "CC-1", cost=12.0)]
        )
        (batch_id,) = await _batch_ids(first)
        async with AsyncSessionLocal() as db:
            await db.execute(text("UPDATE medicine_batches SET cost_price = 999"))
            await db.commit()

        r = await _reverse(client, headers, second)

        assert r.status_code == 409
        assert "cost price" in r.json()["detail"]
        assert await _batch(batch_id) == (20, 20, 9.99)  # untouched, correction preserved
        await _assert_nothing_changed(supplier_id, second, ledger_rows=2)

    async def test_a_restock_from_before_restocks_were_recorded_is_refused(
        self, client, owner_user
    ):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Old Restock")
        await _purchase(client, headers, supplier_id, [_line(product_id, 10, "OLD-1")])
        second = await _purchase(
            client, headers, supplier_id, [_line(product_id, 10, "OLD-1", cost=12.0)]
        )
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "UPDATE purchase_order_items SET batch_cost_before = NULL, "
                    "batch_cost_after = NULL WHERE purchase_order_id = :po"
                ),
                {"po": second},
            )
            await db.commit()

        r = await _reverse(client, headers, second)

        assert r.status_code == 409
        assert "before the system recorded" in r.json()["detail"]
        await _assert_nothing_changed(supplier_id, second, ledger_rows=2)

    async def test_an_old_purchase_that_created_its_batch_can_still_be_reversed(
        self, client, owner_user
    ):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Old Created")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "OLDC-1")])
        (batch_id,) = await _batch_ids(po_id)

        # Such a purchase never had a snapshot, and never needed one.
        r = await _reverse(client, headers, po_id)

        assert r.status_code == 200, r.text
        assert await _batch(batch_id) == (0, 0, 8.0)

    async def test_a_purchase_whose_stock_ledger_entry_is_missing_is_refused(
        self, client, owner_user
    ):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Missing Entry")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "MISS-1")])
        async with AsyncSessionLocal() as db:
            await db.execute(text("UPDATE stock_movements SET reference = 'tampered'"))
            await db.commit()

        r = await _reverse(client, headers, po_id)

        assert r.status_code == 409
        assert "incomplete" in r.json()["detail"]
        assert (await _po(po_id)).status == PurchaseOrderStatus.RECEIVED

    async def test_a_batch_that_no_longer_holds_the_stock_is_refused(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Short Batch")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "SHORT-1")])
        (batch_id,) = await _batch_ids(po_id)
        async with AsyncSessionLocal() as db:
            await db.execute(text("UPDATE medicine_batches SET qty_remaining = 3"))
            await db.commit()

        r = await _reverse(client, headers, po_id)

        assert r.status_code == 409
        assert "no longer holds" in r.json()["detail"]
        assert (await _batch(batch_id))[0] == 3
        await _assert_nothing_changed(supplier_id, po_id, ledger_rows=1)


class TestReversalIsRefusedWhenStockHasMoved:
    async def test_sold_stock_blocks_it_and_nothing_changes(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Part Sold")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "SOLD-1")])
        (batch_id,) = await _batch_ids(po_id)
        await _sell(batch_id, 3)

        r = await _reverse(client, headers, po_id)

        assert r.status_code == 409, r.text
        assert "SOLD-1" in r.json()["detail"]
        assert await _qty_remaining(batch_id) == 7
        await _assert_nothing_changed(supplier_id, po_id, ledger_rows=1)

    async def test_it_is_all_or_nothing_across_batches(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        a = await _make_product("Intact")
        b = await _make_product("Sold Into")
        po_id = await _purchase(
            client, headers, supplier_id, [_line(a, 10, "AON-A"), _line(b, 10, "AON-B")]
        )
        batch_a, batch_b = await _batch_ids(po_id)
        await _sell(batch_b, 1)

        r = await _reverse(client, headers, po_id)

        assert r.status_code == 409
        assert await _qty_remaining(batch_a) == 10  # the intact batch was not touched either
        assert await _qty_remaining(batch_b) == 9
        await _assert_nothing_changed(supplier_id, po_id, ledger_rows=1)

    async def test_a_manual_adjustment_blocks_it(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Adjusted")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "ADJ-1")])
        (batch_id,) = await _batch_ids(po_id)
        adj = await client.post(
            "/api/v1/inventory/adjustments",
            json={"batch_id": batch_id, "quantity_delta": -1, "reason": "DAMAGED"},
            headers=headers,
        )
        assert adj.status_code == 201, adj.text

        r = await _reverse(client, headers, po_id)

        assert r.status_code == 409
        await _assert_nothing_changed(supplier_id, po_id, ledger_rows=1)

    async def test_a_batch_in_an_open_stock_take_blocks_it_until_the_count_closes(
        self, client, owner_user
    ):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Being Counted")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "LOCK-1")])
        (batch_id,) = await _batch_ids(po_id)
        async with AsyncSessionLocal() as db:
            stock_take = StockTake(initiated_by_user_id=owner_user.id)
            db.add(stock_take)
            await db.flush()
            batch = await db.get(MedicineBatch, batch_id)
            assert batch is not None
            batch.locked_by_stock_take_id = stock_take.id
            await db.commit()

        blocked = await _reverse(client, headers, po_id)

        assert blocked.status_code == 409
        assert "stock take" in blocked.json()["detail"]
        await _assert_nothing_changed(supplier_id, po_id, ledger_rows=1)

        async with AsyncSessionLocal() as db:
            await db.execute(text("UPDATE medicine_batches SET locked_by_stock_take_id = NULL"))
            await db.commit()
        assert (await _reverse(client, headers, po_id)).status_code == 200

    async def test_an_incomplete_purchase_record_is_refused(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Broken Record")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "INC-1")])
        async with AsyncSessionLocal() as db:
            await db.execute(text("UPDATE purchase_order_items SET batch_id = NULL"))
            await db.commit()

        r = await _reverse(client, headers, po_id)

        assert r.status_code == 409
        assert "incomplete" in r.json()["detail"]
        await _assert_nothing_changed(supplier_id, po_id, ledger_rows=1)


class TestReversalHappensOnce:
    async def test_reversing_twice_is_refused(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Once")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "ONCE-1")])

        assert (await _reverse(client, headers, po_id)).status_code == 200
        again = await _reverse(client, headers, po_id)

        assert again.status_code == 409, again.text
        assert len(await _ledger(supplier_id)) == 2
        assert await _balance(client, headers, supplier_id) == 0.0

    async def test_simultaneous_reversals_apply_exactly_once(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Race")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "RACE-1")])
        (batch_id,) = await _batch_ids(po_id)

        results = await asyncio.gather(*[_reverse(client, headers, po_id) for _ in range(6)])

        assert sorted(r.status_code for r in results) == [200, 409, 409, 409, 409, 409]
        assert len(await _ledger(supplier_id)) == 2
        assert len(await _movements(batch_id)) == 2
        assert (await client.get("/api/v1/inventory/reconcile", headers=headers)).json() == []

    async def test_an_adjustment_racing_a_reversal_never_both_succeed(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Adjustment Race")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "AR-1")])
        (batch_id,) = await _batch_ids(po_id)

        reversal, adjustment = await asyncio.gather(
            _reverse(client, headers, po_id),
            client.post(
                "/api/v1/inventory/adjustments",
                json={"batch_id": batch_id, "quantity_delta": -1, "reason": "DAMAGED"},
                headers=headers,
            ),
        )

        # Whichever got there first wins; the other must be refused, because
        # removing 10 from a batch that no longer holds 10 would invent stock.
        assert not (reversal.status_code == 200 and adjustment.status_code == 201)
        assert reversal.status_code in (200, 409)
        assert (await client.get("/api/v1/inventory/reconcile", headers=headers)).json() == []
        assert await _qty_remaining(batch_id) >= 0


class TestReversalInputAndAccess:
    async def test_a_reason_is_required(self, client, owner_user):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Reason")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "RSN-1")])

        for body in (
            {},
            {"reason": ""},
            {"reason": "   "},
            {"reason": "ab"},
            {"reason": "x" * 256},
        ):
            r = await client.post(
                f"/api/v1/purchase-orders/{po_id}/reverse", json=body, headers=headers
            )
            assert r.status_code == 422, (body, r.text)
        await _assert_nothing_changed(supplier_id, po_id, ledger_rows=1)

    async def test_only_the_owner_may_reverse(self, client, owner_user, administrator_user):
        owner = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Permission")
        po_id = await _purchase(client, owner, supplier_id, [_line(product_id, 10, "PERM-1")])

        admin = await _login(client, "sam", "AdminPass1")
        denied = await _reverse(client, admin, po_id)
        anonymous = await client.post(
            f"/api/v1/purchase-orders/{po_id}/reverse", json={"reason": "wrong amount"}
        )

        assert denied.status_code == 403
        assert anonymous.status_code == 401
        await _assert_nothing_changed(supplier_id, po_id, ledger_rows=1)

    async def test_an_unknown_purchase_is_not_found(self, client, owner_user):
        r = await _reverse(client, await _owner(client), 999_999)
        assert r.status_code == 404


class TestSupplierFiguresAfterReversal:
    async def test_a_reversed_purchase_counts_as_never_bought_and_payments_are_untouched(
        self, client, owner_user
    ):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("Figures")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "FIG-1")])
        paid = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments", json={"amount": 30.0}, headers=headers
        )
        assert paid.status_code == 200, paid.text

        async with AsyncSessionLocal() as db:
            today = (await db.execute(select(func.date(func.current_timestamp())))).scalar_one()
        params = {"start_date": today, "end_date": today}
        before = (await client.get("/api/v1/suppliers/kpis", params=params, headers=headers)).json()
        assert (before["total_purchased"], before["total_paid"], before["net_due"]) == (
            80.0,
            30.0,
            50.0,
        )

        assert (await _reverse(client, headers, po_id)).status_code == 200

        after = (await client.get("/api/v1/suppliers/kpis", params=params, headers=headers)).json()
        # Purchased drops to nothing; the 30 really was paid; the supplier now
        # owes the business that 30 back (a credit), shown as a negative balance.
        assert (after["total_purchased"], after["total_paid"], after["net_due"]) == (
            0.0,
            30.0,
            -30.0,
        )
        assert await _balance(client, headers, supplier_id) == -30.0

        history = (
            await client.get(f"/api/v1/suppliers/{supplier_id}/payments", headers=headers)
        ).json()
        assert [p["amount"] for p in history] == [30.0]  # the cancellation is not a payment
        assert history[0]["reversed"] is False

    async def test_a_purchase_cancellation_cannot_itself_be_reversed_as_a_payment(
        self, client, owner_user
    ):
        headers = await _owner(client)
        supplier_id = await _make_supplier()
        product_id = await _make_product("No Undo Of Undo")
        po_id = await _purchase(client, headers, supplier_id, [_line(product_id, 10, "NUU-1")])
        await _reverse(client, headers, po_id)
        cancellation_id = (await _ledger(supplier_id))[1].id

        r = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments/{cancellation_id}/reverse",
            json={"reason": "trying to undo it"},
            headers=headers,
        )

        assert r.status_code == 404
        assert len(await _ledger(supplier_id)) == 2


def _run_alembic(db_path: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
    )


class TestMigration:
    def test_downgrade_is_refused_while_reversed_purchases_exist(self, tmp_path):
        db_path = tmp_path / "shop.db"
        assert _run_alembic(db_path, "upgrade", "head").returncode == 0
        connection = sqlite3.connect(db_path)
        connection.execute("INSERT INTO suppliers (name) VALUES ('S')")
        connection.execute(
            "INSERT INTO users (id, full_name, username, hashed_password, role_id) "
            "SELECT 1, 'U', 'u', 'x', id FROM roles LIMIT 1"
        )
        connection.execute(
            "INSERT INTO purchase_orders (supplier_id, status, created_by_user_id, reversed_at) "
            "VALUES (1, 'REVERSED', 1, CURRENT_TIMESTAMP)"
        )
        connection.commit()
        connection.close()

        refused = _run_alembic(db_path, "downgrade", "0041_supplier_payment_safety")

        assert refused.returncode != 0
        assert b"reversed purchase" in refused.stderr
        columns = {
            row[1] for row in sqlite3.connect(db_path).execute("PRAGMA table_info(purchase_orders)")
        }
        assert {"reversed_at", "reversal_reason"} <= columns  # nothing was dropped

    def test_downgrade_and_upgrade_round_trip_when_nothing_is_reversed(self, tmp_path):
        db_path = tmp_path / "shop.db"
        assert _run_alembic(db_path, "upgrade", "head").returncode == 0

        assert _run_alembic(db_path, "downgrade", "0041_supplier_payment_safety").returncode == 0
        connection = sqlite3.connect(db_path)
        orders = {row[1] for row in connection.execute("PRAGMA table_info(purchase_orders)")}
        items = {row[1] for row in connection.execute("PRAGMA table_info(purchase_order_items)")}
        connection.close()
        assert "reversed_at" not in orders
        assert "batch_cost_before" not in items
        assert _run_alembic(db_path, "upgrade", "head").returncode == 0
