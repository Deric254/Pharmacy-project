from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.models.user import User
from app.schemas.batch import (
    BatchCostCorrection,
    BatchCreate,
    BatchExpiryCorrection,
    BatchOut,
    BatchUpdate,
)


class BatchService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_batch(
        self, product_id: int, payload: BatchCreate, created_by: User
    ) -> BatchOut:
        """
        Manual stock entry (e.g. initial stocking before the Purchasing
        module exists, or an ad-hoc delivery). Always inserts a NEW
        batch row -- never merges into an existing one, even for the
        same product, because expiry dates differ between deliveries.
        The batch row and its ledger entry are written in the same
        transaction: either both succeed or neither does.
        """
        product_result = await self.db.execute(
            select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
        )
        product = product_result.scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")

        batch = MedicineBatch(
            product_id=product_id,
            batch_number=payload.batch_number,
            expiry_date=payload.expiry_date,
            qty_received=payload.qty_received,
            qty_remaining=payload.qty_received,
            cost_price=payload.cost_price,
            selling_price=(
                payload.selling_price
                if payload.selling_price is not None
                else product.default_selling_price
            ),
        )
        self.db.add(batch)
        await self.db.flush()  # assigns batch.id without ending the transaction

        self.db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=MovementType.PURCHASE,
                quantity_delta=payload.qty_received,
                reason="Manual stock entry",
                created_by_user_id=created_by.id,
            )
        )

        await self.db.commit()
        await self.db.refresh(batch)
        return BatchOut.model_validate(batch)

    async def update_selling_price(
        self, product_id: int, batch_id: int, payload: BatchUpdate, changed_by: User
    ) -> BatchOut:
        """
        Free to call at any time, on any batch, regardless of FEFO
        order or whether it's the batch currently selling -- that's
        the point (see InventoryPage: editing a non-FEFO batch is
        allowed, it just won't be reflected at the register until
        that batch is the one being drawn from). What's not optional
        is the audit trail: every change is logged with who, when,
        old price, and new price, in the same transaction as the
        price change itself, so a reprice can never happen silently.
        """
        batch = await self.db.get(MedicineBatch, batch_id)
        if batch is None or batch.product_id != product_id:
            raise HTTPException(status_code=404, detail="Batch not found")

        old_price = batch.selling_price
        new_price = payload.selling_price
        if old_price != new_price:
            batch.selling_price = new_price
            self.db.add(
                AuditLog(
                    user_id=changed_by.id,
                    user_name_snapshot=changed_by.full_name,
                    action="batch.price_changed",
                    entity_type="medicine_batch",
                    entity_id=str(batch.id),
                    old_value=f"{old_price:.2f}" if old_price is not None else "null",
                    new_value=(
                        f"{new_price:.2f} (product_id={batch.product_id}, "
                        f"batch={batch.batch_number}, exp={batch.expiry_date.isoformat()})"
                    ),
                )
            )
        await self.db.commit()
        await self.db.refresh(batch)
        return BatchOut.model_validate(batch)

    async def list_for_product(self, product_id: int) -> list[BatchOut]:
        result = await self.db.execute(
            select(MedicineBatch)
            .where(MedicineBatch.product_id == product_id)
            .order_by(MedicineBatch.expiry_date)
        )
        return [BatchOut.model_validate(b) for b in result.scalars().all()]

    async def correct_cost_price(
        self, product_id: int, batch_id: int, payload: BatchCostCorrection, changed_by: User
    ) -> BatchOut:
        """
        Free to call at any time, on any batch, regardless of whether
        anything has already sold from it -- mirrors
        update_selling_price above exactly, and is only safe to do
        because of a change made alongside this one: SaleItem.unit_cost
        now freezes the batch's cost at the exact moment each sale
        happened (see that column's own comment), and profit/COGS
        reports read from that frozen value, never from this batch's
        live cost_price. So a correction here can only ever change two
        things: this batch's REMAINING valuation, and the cost basis
        of whatever sells from it from this point forward. It can
        never reach back and change a number a past, already-closed
        report showed -- that's what makes an unconditional correction
        safe rather than a loophole.

        A reason is required and every correction is audit-logged --
        old price, new price, who, when, why -- in the same
        transaction as the change itself, exactly like
        update_selling_price. The audit trail is not optional just
        because the eligibility check is gone: a cost correction is
        still a real financial change worth a permanent record of who
        made it and why.
        """
        batch = await self.db.get(MedicineBatch, batch_id)
        if batch is None or batch.product_id != product_id:
            raise HTTPException(status_code=404, detail="Batch not found")

        old_price = batch.cost_price
        new_price = payload.cost_price
        if old_price != new_price:
            batch.cost_price = new_price
            self.db.add(
                AuditLog(
                    user_id=changed_by.id,
                    user_name_snapshot=changed_by.full_name,
                    action="batch.cost_corrected",
                    entity_type="medicine_batch",
                    entity_id=str(batch.id),
                    old_value=f"{old_price:.2f}",
                    new_value=(
                        f"{new_price:.2f} (product_id={batch.product_id}, "
                        f"batch={batch.batch_number}, exp={batch.expiry_date.isoformat()}, "
                        f"reason={payload.reason})"
                    ),
                )
            )
        await self.db.commit()
        await self.db.refresh(batch)
        return BatchOut.model_validate(batch)

    async def correct_expiry_date(
        self, product_id: int, batch_id: int, payload: BatchExpiryCorrection, changed_by: User
    ) -> BatchOut:
        """
        Gated by its own `batches.correct_expiry` permission -- not
        `batches.correct_cost`, not general `inventory.adjust` -- for
        a reason distinct from the usual "keep scopes narrow"
        principle: this one can specifically make an already-expired
        batch look valid again, letting it back into FEFO sale
        selection (see stock_selection_service.py's `expiry_date >=
        today` filter). A cost correction can only ever misstate
        money; this one, misused, is a route to selling expired
        medicine. See migration 0035's own docstring for the full
        reasoning. Every correction is still unconditionally allowed
        once granted -- the permission boundary and the audit trail
        are the safeguards here, not a business-rule restriction on
        when this can be called (mirrors correct_cost_price above).
        """
        batch = await self.db.get(MedicineBatch, batch_id)
        if batch is None or batch.product_id != product_id:
            raise HTTPException(status_code=404, detail="Batch not found")

        old_date = batch.expiry_date
        new_date = payload.new_expiry_date
        if old_date != new_date:
            batch.expiry_date = new_date
            self.db.add(
                AuditLog(
                    user_id=changed_by.id,
                    user_name_snapshot=changed_by.full_name,
                    action="batch.expiry_corrected",
                    entity_type="medicine_batch",
                    entity_id=str(batch.id),
                    old_value=old_date.isoformat(),
                    new_value=(
                        f"{new_date.isoformat()} (product_id={batch.product_id}, "
                        f"batch={batch.batch_number}, reason={payload.reason})"
                    ),
                )
            )
        await self.db.commit()
        await self.db.refresh(batch)
        return BatchOut.model_validate(batch)
