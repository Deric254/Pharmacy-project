"""
Inventory service.

Low-stock and expiry detection here are on-demand queries (called from
API routes, e.g. for a dashboard load). The architecture spec calls for
these to also fire automatically off the sales event stream -- that
hook lives in SaleService (checked after each sale commits) so the
detection logic itself stays reusable by both paths rather than
duplicated.

Adjustments never silently edit a quantity -- every adjustment writes
a StockMovement row with a mandatory reason, in the same transaction
as the qty_remaining change.

Reconciliation is detection-only: it reports where qty_remaining and
the ledger's own sum disagree, but never auto-corrects. A mismatch is
a signal for a human to investigate (possible bug, or someone edited
the DB directly), not something to silently paper over.
"""

from datetime import date, timedelta
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import business_today, local_day_bounds_utc
from app.core.events import BatchExpiringEvent, StockLowEvent, publish
from app.models.audit_log import AuditLog
from app.models.business_config import BusinessConfig
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.models.user import User
from app.schemas.inventory import (
    AdjustmentOut,
    AdjustmentRequest,
    BulkWriteOffResult,
    ExpiringBatchOut,
    LowStockProductOut,
    ProductValuationOut,
    ReconciliationIssueOut,
    StockMovementOut,
    StockMovementPage,
    StockValuationOut,
    WriteOffResult,
)


class InventoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_low_stock_products(self) -> list[LowStockProductOut]:
        today = await business_today(self.db)
        # Ordered by how urgent the shortage is -- lowest total_qty
        # (the most depleted, most at-risk-of-stocking-out products)
        # first. Without an explicit ORDER BY, SQL makes no promise
        # about row order at all; this used to come back in whatever
        # incidental order the GROUP BY happened to produce, which is
        # why the list didn't look sorted despite the underlying data
        # being perfectly fine. The order_by target is the same
        # coalesced SUM expression used in both SELECT and HAVING
        # above, not the "total_qty" label -- some backends don't
        # accept an aggregate's own output label inside ORDER BY, so
        # this repeats the expression itself to stay portable.
        total_qty_expr = func.coalesce(func.sum(MedicineBatch.qty_remaining), 0)
        result = await self.db.execute(
            select(
                Product.id,
                Product.name,
                Product.barcode,
                Product.reorder_point,
                total_qty_expr.label("total_qty"),
            )
            .outerjoin(
                MedicineBatch,
                and_(
                    MedicineBatch.product_id == Product.id,
                    MedicineBatch.expiry_date >= today,
                    MedicineBatch.locked_by_stock_take_id.is_(None),
                ),
            )
            .where(Product.deleted_at.is_(None))
            .group_by(Product.id)
            .having(total_qty_expr < Product.reorder_point)
            .order_by(total_qty_expr.asc())
        )
        return [
            LowStockProductOut(
                product_id=row.id,
                name=row.name,
                barcode=row.barcode,
                total_qty_available=int(row.total_qty),
                reorder_point=row.reorder_point,
            )
            for row in result.all()
        ]

    async def get_expiring_batches(self, within_days: int | None = None) -> list[ExpiringBatchOut]:
        threshold_days = within_days if within_days is not None else await self._max_alert_window()
        today = await business_today(self.db)
        cutoff = today + timedelta(days=threshold_days)

        result = await self.db.execute(
            select(MedicineBatch, Product.name)
            .join(Product, Product.id == MedicineBatch.product_id)
            .where(MedicineBatch.expiry_date <= cutoff, MedicineBatch.qty_remaining > 0)
            .order_by(MedicineBatch.expiry_date)
        )
        return [
            ExpiringBatchOut(
                batch_id=batch.id,
                product_id=batch.product_id,
                product_name=product_name,
                batch_number=batch.batch_number,
                expiry_date=batch.expiry_date,
                days_remaining=(batch.expiry_date - today).days,
                qty_remaining=batch.qty_remaining,
            )
            for batch, product_name in result.all()
        ]

    async def get_valuation(self) -> StockValuationOut:
        today = await business_today(self.db)
        result = await self.db.execute(
            select(
                Product.id,
                Product.name,
                func.coalesce(func.sum(MedicineBatch.qty_remaining), 0).label("qty"),
                func.coalesce(
                    func.sum(MedicineBatch.qty_remaining * MedicineBatch.cost_price), 0.0
                ).label("value"),
            )
            .outerjoin(
                MedicineBatch,
                and_(
                    MedicineBatch.product_id == Product.id,
                    MedicineBatch.expiry_date >= today,
                ),
            )
            .where(Product.deleted_at.is_(None))
            .group_by(Product.id)
        )
        rows = result.all()
        by_product = [
            ProductValuationOut(
                product_id=row.id, name=row.name, qty_on_hand=int(row.qty), value=float(row.value)
            )
            for row in rows
        ]
        return StockValuationOut(
            total_value=sum(p.value for p in by_product), by_product=by_product
        )

    async def adjust_stock(self, payload: AdjustmentRequest, user: User) -> AdjustmentOut:
        batch = await self.db.get(MedicineBatch, payload.batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found")

        reason_text = (
            payload.reason.value
            if payload.notes is None
            else f"{payload.reason.value}: {payload.notes}"
        )

        # The real guarantee against two concurrent adjustments to the
        # same batch silently losing one of them -- the WHERE clause
        # is checked against the row's actual state at the moment the
        # UPDATE runs, not whatever was read moments earlier. This is
        # also what correctly prevents a negative result even under a
        # race: the earlier plain read can no longer be trusted as the
        # true current value once concurrency is a possibility.
        result = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(MedicineBatch)
                .where(
                    MedicineBatch.id == payload.batch_id,
                    MedicineBatch.qty_remaining + payload.quantity_delta >= 0,
                    # A batch under an open stock take must not move mid-count:
                    # the count later overwrites qty_remaining with what was
                    # physically counted, so an adjustment made in between
                    # would be ledgered twice (here, and as that variance).
                    MedicineBatch.locked_by_stock_take_id.is_(None),
                )
                .values(qty_remaining=MedicineBatch.qty_remaining + payload.quantity_delta)
            ),
        )
        if result.rowcount == 0:
            refreshed = await self.db.get(MedicineBatch, payload.batch_id, populate_existing=True)
            if refreshed is not None and refreshed.locked_by_stock_take_id is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Batch {batch.batch_number} is locked by an open stock take "
                        "and cannot be adjusted right now."
                    ),
                )
            current_qty = refreshed.qty_remaining if refreshed is not None else 0
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Adjustment would take batch {payload.batch_id} negative "
                    f"({current_qty} + {payload.quantity_delta} < 0)"
                ),
            )

        self.db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=MovementType.ADJUSTMENT,
                quantity_delta=payload.quantity_delta,
                reason=reason_text,
                created_by_user_id=user.id,
            )
        )
        await self.db.commit()

        refreshed = await self.db.get(MedicineBatch, payload.batch_id)
        new_qty = refreshed.qty_remaining if refreshed is not None else 0

        return AdjustmentOut(
            batch_id=batch.id,
            quantity_delta=payload.quantity_delta,
            qty_remaining_after=new_qty,
            reason=payload.reason,
        )

    async def write_off_expired_batch(self, batch_id: int, user: User) -> WriteOffResult:
        """
        The one-click version of adjust_stock for the single most
        common write-off case: a batch has genuinely expired, and all
        of its remaining stock needs to go to zero, reason and
        quantity already implied rather than typed in by hand.

        "Genuinely expired" is re-verified here against
        business_today(), never trusted from whatever the caller
        claims -- a stale UI (someone left the page open across
        midnight) must not be able to write off a batch that's since
        become sellable again, or that was never expired to begin
        with. Same reasoning as _restock_batch's own lock check
        elsewhere in this codebase: server-side truth, not client
        state, decides what's actually true.

        The compare-and-swap on qty_remaining below (not just the
        `> 0` floor adjust_stock uses) is deliberate: because this
        writes a fixed value from a Python-side snapshot rather than
        adjust_stock's own relative column expression, that snapshot
        has to be provably still accurate at the instant of the write
        for the StockMovement/AuditLog delta to be honest -- otherwise
        a concurrent manual adjustment landing in the gap between the
        read and this write could make the logged "quantity written
        off" wrong, which would silently break reconcile()'s own
        invariant (ledger sum must equal qty_remaining exactly).
        """
        batch = await self.db.get(MedicineBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found")

        today = await business_today(self.db)
        if batch.expiry_date >= today:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Batch {batch.batch_number} has not expired yet "
                    f"(expires {batch.expiry_date.isoformat()}). Use a regular "
                    "stock adjustment if you need to remove it for another reason."
                ),
            )
        if batch.qty_remaining <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Batch {batch.batch_number} has no remaining stock to write off.",
            )

        original_qty = batch.qty_remaining
        result = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(MedicineBatch)
                .where(
                    MedicineBatch.id == batch_id,
                    MedicineBatch.expiry_date < today,
                    MedicineBatch.locked_by_stock_take_id.is_(None),
                    MedicineBatch.qty_remaining == original_qty,
                )
                .values(qty_remaining=0)
            ),
        )
        if result.rowcount == 0:
            refreshed = await self.db.get(MedicineBatch, batch_id)
            if refreshed is not None and refreshed.locked_by_stock_take_id is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Batch {batch.batch_number} is locked by an open stock take "
                        "and cannot be written off right now."
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Batch {batch.batch_number} changed just now (someone else adjusted "
                    "it). Please refresh and try again."
                ),
            )

        self._record_write_off(batch, original_qty, user)
        await self.db.commit()

        return WriteOffResult(
            batch_id=batch.id, quantity_written_off=original_qty, qty_remaining_after=0
        )

    async def write_off_all_expired(self, user: User) -> BulkWriteOffResult:
        """
        Same guarantees as write_off_expired_batch, applied to every
        currently-expired batch with stock left in one action. Each
        batch gets its own StockMovement (the per-batch ledger stays
        exactly as granular as every other adjustment path in this
        app), but one single AuditLog entry summarizes the whole
        action -- matching how one sale with several line items gets
        one audit entry, not one per line.
        """
        today = await business_today(self.db)
        candidates = (
            (
                await self.db.execute(
                    select(MedicineBatch).where(
                        MedicineBatch.expiry_date < today,
                        MedicineBatch.qty_remaining > 0,
                        MedicineBatch.locked_by_stock_take_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

        details: list[WriteOffResult] = []
        for batch in candidates:
            original_qty = batch.qty_remaining
            result = cast(
                "CursorResult[Any]",
                await self.db.execute(
                    update(MedicineBatch)
                    .where(
                        MedicineBatch.id == batch.id,
                        MedicineBatch.expiry_date < today,
                        MedicineBatch.locked_by_stock_take_id.is_(None),
                        MedicineBatch.qty_remaining == original_qty,
                    )
                    .values(qty_remaining=0)
                ),
            )
            if result.rowcount == 0:
                # Changed or got locked between the SELECT above and
                # this UPDATE (a stock take starting, or someone else
                # already acting on it) -- skip it rather than fail
                # the whole bulk action over one batch's bad luck.
                continue

            self.db.add(
                StockMovement(
                    batch_id=batch.id,
                    movement_type=MovementType.ADJUSTMENT,
                    quantity_delta=-original_qty,
                    reason="EXPIRED: written off (bulk expired-stock write-off)",
                    created_by_user_id=user.id,
                )
            )
            details.append(
                WriteOffResult(
                    batch_id=batch.id, quantity_written_off=original_qty, qty_remaining_after=0
                )
            )

        if details:
            total = sum(d.quantity_written_off for d in details)
            self.db.add(
                AuditLog(
                    user_id=user.id,
                    user_name_snapshot=user.full_name,
                    action="batch.bulk_written_off_expired",
                    entity_type="medicine_batch",
                    entity_id="bulk",
                    new_value=(
                        f"batches={len(details)} total_quantity={total} "
                        f"batch_ids={[d.batch_id for d in details]}"
                    ),
                )
            )
            await self.db.commit()

        return BulkWriteOffResult(
            batches_written_off=len(details),
            total_quantity_written_off=sum(d.quantity_written_off for d in details),
            details=details,
        )

    def _record_write_off(self, batch: MedicineBatch, original_qty: int, user: User) -> None:
        self.db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=MovementType.ADJUSTMENT,
                quantity_delta=-original_qty,
                reason="EXPIRED: written off (one-click expired-stock write-off)",
                created_by_user_id=user.id,
            )
        )
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="batch.written_off_expired",
                entity_type="medicine_batch",
                entity_id=str(batch.id),
                old_value=f"qty_remaining={original_qty}",
                new_value="qty_remaining=0",
            )
        )

    async def reconcile(self) -> list[ReconciliationIssueOut]:
        """
        Detection only. For each batch, the ledger's own sum of
        quantity_delta values should exactly equal qty_remaining --
        that's the definition of qty_remaining being a correctly
        derived cache of the ledger. A mismatch means either a bug or
        a direct DB edit bypassing the application layer.
        """
        result = await self.db.execute(
            select(
                MedicineBatch.id,
                MedicineBatch.batch_number,
                MedicineBatch.product_id,
                Product.name,
                MedicineBatch.qty_remaining,
                func.coalesce(func.sum(StockMovement.quantity_delta), 0).label("ledger_sum"),
            )
            .join(Product, Product.id == MedicineBatch.product_id)
            .outerjoin(StockMovement, StockMovement.batch_id == MedicineBatch.id)
            .group_by(MedicineBatch.id)
        )

        issues = []
        for row in result.all():
            ledger_sum = int(row.ledger_sum)
            if ledger_sum != row.qty_remaining:
                issues.append(
                    ReconciliationIssueOut(
                        batch_id=row.id,
                        batch_number=row.batch_number,
                        product_id=row.product_id,
                        product_name=row.name,
                        qty_remaining=row.qty_remaining,
                        ledger_sum=ledger_sum,
                        discrepancy=row.qty_remaining - ledger_sum,
                    )
                )
        return issues

    async def list_movements(
        self,
        product_id: int | None = None,
        batch_id: int | None = None,
        movement_type: MovementType | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> StockMovementPage:
        """
        The real, complete trace of every quantity change to every
        batch -- sales, refunds, adjustments, write-offs, corrections,
        and purchase receipts all write here (see StockMovement's own
        docstring: it is the append-only source of truth, not
        MedicineBatch.qty_remaining, which is a derived cache). This
        was always correct and complete in the data; this method is
        what finally makes it visible to a real audit review, which
        it never was until now.
        """
        query = (
            select(
                StockMovement.id,
                StockMovement.batch_id,
                MedicineBatch.batch_number,
                MedicineBatch.product_id,
                Product.name,
                StockMovement.movement_type,
                StockMovement.quantity_delta,
                StockMovement.reason,
                StockMovement.reference,
                StockMovement.created_by_user_id,
                User.full_name,
                StockMovement.created_at,
            )
            .join(MedicineBatch, MedicineBatch.id == StockMovement.batch_id)
            .join(Product, Product.id == MedicineBatch.product_id)
            .outerjoin(User, User.id == StockMovement.created_by_user_id)
        )
        count_query = (
            select(func.count())
            .select_from(StockMovement)
            .join(MedicineBatch, MedicineBatch.id == StockMovement.batch_id)
        )

        if product_id is not None:
            query = query.where(MedicineBatch.product_id == product_id)
            count_query = count_query.where(MedicineBatch.product_id == product_id)
        if batch_id is not None:
            query = query.where(StockMovement.batch_id == batch_id)
            count_query = count_query.where(StockMovement.batch_id == batch_id)
        if movement_type is not None:
            query = query.where(StockMovement.movement_type == movement_type)
            count_query = count_query.where(StockMovement.movement_type == movement_type)
        if start_date is not None:
            utc_start, _ = await local_day_bounds_utc(self.db, start_date)
            query = query.where(StockMovement.created_at >= utc_start)
            count_query = count_query.where(StockMovement.created_at >= utc_start)
        if end_date is not None:
            _, utc_end = await local_day_bounds_utc(self.db, end_date)
            query = query.where(StockMovement.created_at < utc_end)
            count_query = count_query.where(StockMovement.created_at < utc_end)

        total = (await self.db.execute(count_query)).scalar_one()
        query = query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        query = query.limit(limit).offset(offset)
        rows = (await self.db.execute(query)).all()

        entries = [
            StockMovementOut(
                id=row[0],
                batch_id=row[1],
                batch_number=row[2],
                product_id=row[3],
                product_name=row[4],
                movement_type=row[5],
                quantity_delta=row[6],
                reason=row[7],
                reference=row[8],
                created_by_user_id=row[9],
                created_by_name=row[10],
                created_at=row[11],
            )
            for row in rows
        ]
        return StockMovementPage(entries=entries, total=total, limit=limit, offset=offset)

    async def list_all_movements_for_export(
        self,
        product_id: int | None = None,
        batch_id: int | None = None,
        movement_type: MovementType | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[StockMovementOut]:
        """
        Every matching movement, not one page of them -- an export
        silently capped at the same page limit as the on-screen list
        would be a real accuracy gap for exactly the ledger this
        method exists to make auditable (see list_movements' own
        docstring). Same filters and shape as list_movements() above,
        just without limit/offset, mirroring SaleService and
        AuditLogService's own list_all_for_export methods.
        """
        query = (
            select(
                StockMovement.id,
                StockMovement.batch_id,
                MedicineBatch.batch_number,
                MedicineBatch.product_id,
                Product.name,
                StockMovement.movement_type,
                StockMovement.quantity_delta,
                StockMovement.reason,
                StockMovement.reference,
                StockMovement.created_by_user_id,
                User.full_name,
                StockMovement.created_at,
            )
            .join(MedicineBatch, MedicineBatch.id == StockMovement.batch_id)
            .join(Product, Product.id == MedicineBatch.product_id)
            .outerjoin(User, User.id == StockMovement.created_by_user_id)
        )

        if product_id is not None:
            query = query.where(MedicineBatch.product_id == product_id)
        if batch_id is not None:
            query = query.where(StockMovement.batch_id == batch_id)
        if movement_type is not None:
            query = query.where(StockMovement.movement_type == movement_type)
        if start_date is not None:
            utc_start, _ = await local_day_bounds_utc(self.db, start_date)
            query = query.where(StockMovement.created_at >= utc_start)
        if end_date is not None:
            _, utc_end = await local_day_bounds_utc(self.db, end_date)
            query = query.where(StockMovement.created_at < utc_end)

        query = query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        rows = (await self.db.execute(query)).all()

        return [
            StockMovementOut(
                id=row[0],
                batch_id=row[1],
                batch_number=row[2],
                product_id=row[3],
                product_name=row[4],
                movement_type=row[5],
                quantity_delta=row[6],
                reason=row[7],
                reference=row[8],
                created_by_user_id=row[9],
                created_by_name=row[10],
                created_at=row[11],
            )
            for row in rows
        ]

    async def _max_alert_window(self) -> int:
        result = await self.db.execute(select(BusinessConfig).where(BusinessConfig.id == 1))
        config = result.scalar_one_or_none()
        if config is None:
            return 90
        days = [int(d) for d in config.expiry_alert_days.split(",") if d]
        return max(days) if days else 90


async def check_and_publish_low_stock(db: AsyncSession, product_ids: list[int]) -> None:
    """
    Called after a sale commits (see SaleService) for the specific
    products involved, rather than scanning the whole catalog on every
    sale -- cheap enough to run inline without slowing checkout.
    """
    if not product_ids:
        return

    today = await business_today(db)
    result = await db.execute(
        select(
            Product.id,
            Product.reorder_point,
            func.coalesce(func.sum(MedicineBatch.qty_remaining), 0).label("total_qty"),
        )
        .outerjoin(
            MedicineBatch,
            and_(
                MedicineBatch.product_id == Product.id,
                MedicineBatch.expiry_date >= today,
                MedicineBatch.locked_by_stock_take_id.is_(None),
            ),
        )
        .where(Product.id.in_(product_ids))
        .group_by(Product.id)
    )
    for row in result.all():
        if int(row.total_qty) < row.reorder_point:
            # batch_id=0 is a placeholder -- the event's purpose is the
            # product-level signal, and a dashboard subscriber can
            # query current batches itself if it needs batch detail.
            await publish(
                StockLowEvent(
                    product_id=row.id,
                    batch_id=0,
                    qty_remaining=int(row.total_qty),
                    reorder_point=row.reorder_point,
                )
            )


async def check_and_publish_expiring(db: AsyncSession, within_days: int = 30) -> None:
    """Intended to be called by a scheduled job once one exists (Backup/Notifications module)."""
    service = InventoryService(db)
    for batch in await service.get_expiring_batches(within_days=within_days):
        await publish(
            BatchExpiringEvent(
                batch_id=batch.batch_id,
                product_id=batch.product_id,
                expiry_date=batch.expiry_date.isoformat(),
                days_remaining=batch.days_remaining,
            )
        )
