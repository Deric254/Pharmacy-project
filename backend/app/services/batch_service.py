from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.models.user import User
from app.schemas.batch import BatchCreate, BatchOut, BatchUpdate


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
