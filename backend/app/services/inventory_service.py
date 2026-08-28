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

from datetime import timedelta
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import business_today
from app.core.events import BatchExpiringEvent, StockLowEvent, publish
from app.models.business_config import BusinessConfig
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.models.user import User
from app.schemas.inventory import (
    AdjustmentOut,
    AdjustmentRequest,
    ExpiringBatchOut,
    LowStockProductOut,
    ProductValuationOut,
    ReconciliationIssueOut,
    StockValuationOut,
)


class InventoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_low_stock_products(self) -> list[LowStockProductOut]:
        today = await business_today(self.db)
        result = await self.db.execute(
            select(
                Product.id,
                Product.name,
                Product.barcode,
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
            .where(Product.deleted_at.is_(None))
            .group_by(Product.id)
            .having(func.coalesce(func.sum(MedicineBatch.qty_remaining), 0) < Product.reorder_point)
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
                )
                .values(qty_remaining=MedicineBatch.qty_remaining + payload.quantity_delta)
            ),
        )
        if result.rowcount == 0:
            refreshed = await self.db.get(MedicineBatch, payload.batch_id)
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
