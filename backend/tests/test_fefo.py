"""
FEFO selection tests. This logic is what stands between "the system
tracks expiry" and "the system actually sells the soon-to-expire stock
first" -- the difference between a nice report and real loss
prevention for the pharmacy. Tested directly against the service layer
(not through HTTP) since there's no Sales endpoint yet to exercise it
through.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.services.stock_selection_service import (
    InsufficientStockError,
    apply_allocations,
    select_batches_fefo,
)


async def _make_product_with_batches(db, batches: list[tuple[str, str, int]]) -> int:
    """batches: list of (batch_number, expiry_date_iso, qty)"""
    product = Product(name="Test Product")
    db.add(product)
    await db.flush()

    for batch_number, expiry, qty in batches:
        db.add(
            MedicineBatch(
                product_id=product.id,
                batch_number=batch_number,
                expiry_date=date.fromisoformat(expiry),
                qty_received=qty,
                qty_remaining=qty,
                cost_price=1.0,
            )
        )
    await db.commit()
    return int(product.id)


class TestFEFOSelection:
    async def test_selects_nearest_expiry_batch_first(self):
        async with AsyncSessionLocal() as db:
            product_id = await _make_product_with_batches(
                db,
                [
                    ("FAR", "2028-01-01", 50),
                    ("NEAR", "2026-08-01", 50),
                    ("MID", "2027-01-01", 50),
                ],
            )

            allocations = await select_batches_fefo(db, product_id, qty_needed=10, lock=False)

            assert len(allocations) == 1
            batch, qty = allocations[0]
            assert batch.batch_number == "NEAR"  # earliest expiry, regardless of insertion order
            assert qty == 10

    async def test_spills_into_next_batch_when_first_is_insufficient(self):
        async with AsyncSessionLocal() as db:
            product_id = await _make_product_with_batches(
                db,
                [
                    ("NEAR", "2026-08-01", 5),
                    ("MID", "2027-01-01", 20),
                ],
            )

            allocations = await select_batches_fefo(db, product_id, qty_needed=12, lock=False)

            assert len(allocations) == 2
            (batch1, qty1), (batch2, qty2) = allocations
            assert batch1.batch_number == "NEAR"
            assert qty1 == 5  # takes everything from the nearer-expiry batch first
            assert batch2.batch_number == "MID"
            assert qty2 == 7  # remainder from the next batch

    async def test_raises_when_total_stock_insufficient(self):
        async with AsyncSessionLocal() as db:
            product_id = await _make_product_with_batches(db, [("ONLY", "2027-01-01", 5)])

            with pytest.raises(InsufficientStockError) as exc_info:
                await select_batches_fefo(db, product_id, qty_needed=10, lock=False)

            assert exc_info.value.requested == 10
            assert exc_info.value.available == 5

    async def test_zero_quantity_batches_are_skipped(self):
        async with AsyncSessionLocal() as db:
            product_id = await _make_product_with_batches(
                db,
                [
                    ("EMPTY", "2026-08-01", 0),
                    ("HAS_STOCK", "2027-01-01", 20),
                ],
            )

            allocations = await select_batches_fefo(db, product_id, qty_needed=5, lock=False)

            assert len(allocations) == 1
            assert allocations[0][0].batch_number == "HAS_STOCK"

    async def test_rejects_non_positive_quantity(self):
        async with AsyncSessionLocal() as db:
            product_id = await _make_product_with_batches(db, [("A", "2027-01-01", 10)])
            with pytest.raises(ValueError):
                await select_batches_fefo(db, product_id, qty_needed=0, lock=False)


class TestApplyAllocations:
    async def test_decrements_batches_and_writes_ledger_rows(self):
        async with AsyncSessionLocal() as db:
            product_id = await _make_product_with_batches(
                db,
                [
                    ("NEAR", "2026-08-01", 5),
                    ("MID", "2027-01-01", 20),
                ],
            )

            allocations = await select_batches_fefo(db, product_id, qty_needed=12, lock=False)
            await apply_allocations(
                db,
                allocations,
                movement_type=MovementType.SALE,
                created_by_user_id=None,
                reference="test-sale-1",
            )
            await db.commit()

            result = await db.execute(
                select(MedicineBatch)
                .where(MedicineBatch.product_id == product_id)
                .order_by(MedicineBatch.expiry_date)
            )
            batches = result.scalars().all()
            assert batches[0].qty_remaining == 0  # NEAR: 5 - 5
            assert batches[1].qty_remaining == 13  # MID: 20 - 7

            ledger_result = await db.execute(
                select(StockMovement).where(StockMovement.reference == "test-sale-1")
            )
            ledger_rows = ledger_result.scalars().all()
            assert len(ledger_rows) == 2
            assert {row.quantity_delta for row in ledger_rows} == {-5, -7}
            assert all(row.movement_type == MovementType.SALE for row in ledger_rows)

    async def test_never_allocates_more_than_a_batch_actually_has(self):
        """
        Guards against a regression where a bad allocation could drive
        qty_remaining negative -- which would silently corrupt the
        ledger's meaning (a batch can't have negative physical stock).
        """
        async with AsyncSessionLocal() as db:
            product_id = await _make_product_with_batches(db, [("A", "2027-01-01", 10)])
            allocations = await select_batches_fefo(db, product_id, qty_needed=10, lock=False)
            await apply_allocations(
                db, allocations, movement_type=MovementType.SALE, created_by_user_id=None
            )
            await db.commit()

            result = await db.execute(
                select(MedicineBatch).where(MedicineBatch.product_id == product_id)
            )
            batch = result.scalar_one()
            assert batch.qty_remaining == 0
            assert batch.qty_remaining >= 0
