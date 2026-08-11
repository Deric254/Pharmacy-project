"""
Stock take service.

Lifecycle: initiate (snapshot + lock batches) -> count each item ->
non-zero variances need a reason, and either self-approve (small,
within SELF_APPROVE_THRESHOLD) or wait for a manager
(stocktake.approve_variance) -> close (requires every item counted and
resolved, unlocks batches, publishes a shrinkage event if losses are
significant).

The ledger write for an approved variance sets qty_remaining directly
to the physical count rather than adding the delta -- since the batch
is locked for the whole count, qty_remaining should still equal
expected_qty at approval time, so this is equivalent to expected+delta
but is more obviously correct as "trust what was physically counted."
"""

from datetime import UTC, datetime
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import StockTakeClosedEvent, publish
from app.models.medicine_batch import MedicineBatch
from app.models.stock_movement import MovementType, StockMovement
from app.models.stock_take import StockTake, StockTakeItem, StockTakeStatus
from app.models.user import User
from app.schemas.stock_take import CountSubmit, StockTakeCreate, StockTakeItemOut, StockTakeOut

SELF_APPROVE_THRESHOLD = 2  # |variance| at or below this, the counter can self-approve


class StockTakeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def initiate(self, payload: StockTakeCreate, user: User) -> StockTakeOut:
        stock_take = StockTake(initiated_by_user_id=user.id, notes=payload.notes)
        self.db.add(stock_take)
        await self.db.flush()

        # The real guarantee against two concurrent initiate() calls
        # racing on overlapping batches -- an atomic UPDATE claims only
        # whatever is genuinely still unlocked at the exact moment it
        # runs, not whatever a plain SELECT happened to see moments
        # earlier. A losing concurrent call simply claims fewer (or
        # zero) batches; it can never silently steal a batch another
        # stock take already locked.
        claim_conditions = [
            MedicineBatch.qty_remaining > 0,
            MedicineBatch.locked_by_stock_take_id.is_(None),
        ]
        if payload.product_ids:
            claim_conditions.append(MedicineBatch.product_id.in_(payload.product_ids))

        await self.db.execute(
            update(MedicineBatch)
            .where(*claim_conditions)
            .values(locked_by_stock_take_id=stock_take.id)
        )

        # The actual claimed set -- never assumed to match what was
        # requested, since a concurrent call may have already claimed
        # some of these batches first.
        claimed_result = await self.db.execute(
            select(MedicineBatch).where(MedicineBatch.locked_by_stock_take_id == stock_take.id)
        )
        batches = claimed_result.scalars().all()
        if not batches:
            await self.db.rollback()
            raise HTTPException(
                status_code=400,
                detail="No eligible (unlocked, in-stock) batches found for this scope",
            )

        for batch in batches:
            self.db.add(
                StockTakeItem(
                    stock_take_id=stock_take.id,
                    batch_id=batch.id,
                    product_id=batch.product_id,
                    expected_qty=batch.qty_remaining,
                )
            )

        await self.db.commit()
        await self.db.refresh(stock_take, attribute_names=["items", "started_at"])
        return StockTakeOut.model_validate(stock_take)

    async def _claim_approval(self, item_id: int, user_id: int) -> bool:
        """
        Atomically claims approval for one stock-take item. Returns
        False if it was already approved (by this same request's
        caller or a concurrent one) -- the caller must not apply the
        variance or write a StockMovement in that case.
        """
        result = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(StockTakeItem)
                .where(StockTakeItem.id == item_id, StockTakeItem.approved_at.is_(None))
                .values(approved_by_user_id=user_id, approved_at=datetime.now(UTC))
            ),
        )
        return result.rowcount > 0

    async def submit_count(
        self, stock_take_id: int, item_id: int, payload: CountSubmit, user: User
    ) -> StockTakeItemOut:
        _, item = await self._load_open_item(stock_take_id, item_id)

        variance = payload.physical_qty - item.expected_qty
        if variance != 0 and payload.reason is None:
            raise HTTPException(
                status_code=400, detail="A reason is required when the physical count differs"
            )

        item.physical_qty = payload.physical_qty
        item.reason = (
            None
            if payload.reason is None
            else (
                payload.reason.value
                if payload.notes is None
                else f"{payload.reason.value}: {payload.notes}"
            )
        )
        item.counted_by_user_id = user.id
        item.counted_at = datetime.now(UTC)

        if variance == 0:
            item.approved_by_user_id = user.id
            item.approved_at = datetime.now(UTC)
        elif abs(variance) <= SELF_APPROVE_THRESHOLD:
            if await self._claim_approval(item.id, user.id):
                await self._apply_variance(item, variance, user)
        # else: left pending -- approved_at stays null until a manager
        # calls approve_variance().

        await self.db.commit()
        await self.db.refresh(item)
        return StockTakeItemOut.model_validate(item)

    async def approve_variance(
        self, stock_take_id: int, item_id: int, user: User
    ) -> StockTakeItemOut:
        _, item = await self._load_open_item(stock_take_id, item_id)

        if item.physical_qty is None:
            raise HTTPException(status_code=400, detail="Item has not been counted yet")

        variance = item.physical_qty - item.expected_qty
        if not await self._claim_approval(item.id, user.id):
            raise HTTPException(status_code=400, detail="Item is already approved")
        await self._apply_variance(item, variance, user)

        await self.db.commit()
        await self.db.refresh(item, attribute_names=["approved_at", "approved_by_user_id"])
        return StockTakeItemOut.model_validate(item)

    async def close(self, stock_take_id: int, user: User) -> StockTakeOut:
        result = await self.db.execute(select(StockTake).where(StockTake.id == stock_take_id))
        stock_take = result.scalar_one_or_none()
        if stock_take is None:
            raise HTTPException(status_code=404, detail="Stock take not found")
        if stock_take.status == StockTakeStatus.CLOSED:
            raise HTTPException(status_code=400, detail="Stock take is already closed")

        uncounted = [i for i in stock_take.items if i.physical_qty is None]
        if uncounted:
            raise HTTPException(
                status_code=400, detail=f"{len(uncounted)} item(s) have not been counted yet"
            )
        unapproved = [i for i in stock_take.items if i.approved_at is None]
        if unapproved:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(unapproved)} item(s) have unapproved variances "
                    "requiring manager sign-off"
                ),
            )

        # The real guarantee against two concurrent close() calls both
        # succeeding -- claimed atomically before any of the
        # shrinkage/unlock work below, so a losing concurrent call
        # fails fast instead of redundantly doing that work for a
        # close that's about to be rejected. Not row-locking: this
        # service never used SELECT...FOR UPDATE to begin with, so
        # this was a genuinely unguarded race, not a false-safety one.
        claim_result = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(StockTake)
                .where(StockTake.id == stock_take_id, StockTake.status != StockTakeStatus.CLOSED)
                .values(status=StockTakeStatus.CLOSED, closed_at=datetime.now(UTC))
            ),
        )
        if claim_result.rowcount == 0:
            raise HTTPException(status_code=400, detail="Stock take is already closed")

        shrinkage_value = 0.0
        expected_value = 0.0
        for item in stock_take.items:
            batch_result = await self.db.execute(
                select(MedicineBatch).where(MedicineBatch.id == item.batch_id)
            )
            batch = batch_result.scalar_one()
            batch.locked_by_stock_take_id = None

            variance = (item.physical_qty or 0) - item.expected_qty
            expected_value += item.expected_qty * batch.cost_price
            if variance < 0:
                shrinkage_value += abs(variance) * batch.cost_price

        await self.db.commit()
        await self.db.refresh(stock_take, attribute_names=["items", "status", "closed_at"])

        shrinkage_percent = (shrinkage_value / expected_value * 100) if expected_value > 0 else 0.0
        await publish(
            StockTakeClosedEvent(
                stock_take_id=stock_take.id,
                shrinkage_value=f"{shrinkage_value:.2f}",
                shrinkage_percent=round(shrinkage_percent, 2),
            )
        )

        return StockTakeOut.model_validate(stock_take)

    async def cancel(self, stock_take_id: int, user: User) -> StockTakeOut:
        """
        Abandons an in-progress stock take and releases every batch it
        locked -- deliberately without requiring a completed count.
        Before this, the only way to release a stock take's locked
        batches was finishing the entire count and closing it; someone
        who started one and then walked away had no way out, and
        those batches would silently show as available everywhere
        while every real sale attempt against them kept failing.
        """
        result = await self.db.execute(select(StockTake).where(StockTake.id == stock_take_id))
        stock_take = result.scalar_one_or_none()
        if stock_take is None:
            raise HTTPException(status_code=404, detail="Stock take not found")
        if stock_take.status != StockTakeStatus.OPEN:
            raise HTTPException(status_code=400, detail="Only an open stock take can be cancelled")

        # Same atomic-claim guarantee as close(): a losing concurrent
        # call fails fast instead of redundantly releasing locks a
        # winning close()/cancel() has already handled.
        claim_result = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(StockTake)
                .where(StockTake.id == stock_take_id, StockTake.status == StockTakeStatus.OPEN)
                .values(status=StockTakeStatus.CANCELLED, closed_at=datetime.now(UTC))
            ),
        )
        if claim_result.rowcount == 0:
            raise HTTPException(status_code=400, detail="Only an open stock take can be cancelled")

        await self.db.execute(
            update(MedicineBatch)
            .where(MedicineBatch.locked_by_stock_take_id == stock_take_id)
            .values(locked_by_stock_take_id=None)
        )
        await self.db.commit()
        await self.db.refresh(stock_take, attribute_names=["items", "status", "closed_at"])

        return StockTakeOut.model_validate(stock_take)

    async def get(self, stock_take_id: int) -> StockTakeOut:
        result = await self.db.execute(select(StockTake).where(StockTake.id == stock_take_id))
        stock_take = result.scalar_one_or_none()
        if stock_take is None:
            raise HTTPException(status_code=404, detail="Stock take not found")
        return StockTakeOut.model_validate(stock_take)

    async def list_all(self) -> list[StockTakeOut]:
        result = await self.db.execute(select(StockTake).order_by(StockTake.started_at.desc()))
        return [StockTakeOut.model_validate(st) for st in result.scalars().all()]

    async def _load_open_item(
        self, stock_take_id: int, item_id: int
    ) -> tuple[StockTake, StockTakeItem]:
        st_result = await self.db.execute(select(StockTake).where(StockTake.id == stock_take_id))
        stock_take = st_result.scalar_one_or_none()
        if stock_take is None:
            raise HTTPException(status_code=404, detail="Stock take not found")
        if stock_take.status == StockTakeStatus.CLOSED:
            raise HTTPException(status_code=400, detail="Stock take is already closed")

        item_result = await self.db.execute(
            select(StockTakeItem).where(
                StockTakeItem.id == item_id, StockTakeItem.stock_take_id == stock_take_id
            )
        )
        item = item_result.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Stock take item not found")
        return stock_take, item

    async def _apply_variance(self, item: StockTakeItem, variance: int, user: User) -> None:
        if variance == 0:
            return
        assert item.physical_qty is not None  # guaranteed by callers before variance is non-zero
        batch_result = await self.db.execute(
            select(MedicineBatch).where(MedicineBatch.id == item.batch_id).with_for_update()
        )
        batch = batch_result.scalar_one()
        batch.qty_remaining = item.physical_qty  # ground truth: what was physically counted
        self.db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=MovementType.ADJUSTMENT,
                quantity_delta=variance,
                reason=item.reason,
                created_by_user_id=user.id,
                reference=f"stocktake:{item.stock_take_id}",
            )
        )
