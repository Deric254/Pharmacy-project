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

from fastapi import HTTPException
from sqlalchemy import select
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
        query = select(MedicineBatch).where(
            MedicineBatch.qty_remaining > 0, MedicineBatch.locked_by_stock_take_id.is_(None)
        )
        if payload.product_ids:
            query = query.where(MedicineBatch.product_id.in_(payload.product_ids))

        result = await self.db.execute(query)
        batches = result.scalars().all()
        if not batches:
            raise HTTPException(
                status_code=400,
                detail="No eligible (unlocked, in-stock) batches found for this scope",
            )

        stock_take = StockTake(initiated_by_user_id=user.id, notes=payload.notes)
        self.db.add(stock_take)
        await self.db.flush()

        for batch in batches:
            batch.locked_by_stock_take_id = stock_take.id
            self.db.add(
                StockTakeItem(
                    stock_take_id=stock_take.id,
                    batch_id=batch.id,
                    product_id=batch.product_id,
                    expected_qty=batch.qty_remaining,
                )
            )

        await self.db.commit()
        await self.db.refresh(stock_take, attribute_names=["items"])
        return StockTakeOut.model_validate(stock_take)

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
            item.approved_by_user_id = user.id
            item.approved_at = datetime.now(UTC)
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
        if item.approved_at is not None:
            raise HTTPException(status_code=400, detail="Item is already approved")

        variance = item.physical_qty - item.expected_qty
        item.approved_by_user_id = user.id
        item.approved_at = datetime.now(UTC)
        await self._apply_variance(item, variance, user)

        await self.db.commit()
        await self.db.refresh(item)
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

        stock_take.status = StockTakeStatus.CLOSED
        stock_take.closed_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(stock_take, attribute_names=["items"])

        shrinkage_percent = (shrinkage_value / expected_value * 100) if expected_value > 0 else 0.0
        await publish(
            StockTakeClosedEvent(
                stock_take_id=stock_take.id,
                shrinkage_value=f"{shrinkage_value:.2f}",
                shrinkage_percent=round(shrinkage_percent, 2),
            )
        )

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
