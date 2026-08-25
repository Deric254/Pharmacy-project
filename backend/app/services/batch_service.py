from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement
from app.models.user import User
from app.schemas.batch import BatchCreate, BatchOut


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
        if product_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Product not found")

        batch = MedicineBatch(
            product_id=product_id,
            batch_number=payload.batch_number,
            expiry_date=payload.expiry_date,
            qty_received=payload.qty_received,
            qty_remaining=payload.qty_received,
            cost_price=payload.cost_price,
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

    async def list_for_product(self, product_id: int) -> list[BatchOut]:
        result = await self.db.execute(
            select(MedicineBatch)
            .where(MedicineBatch.product_id == product_id)
            .order_by(MedicineBatch.expiry_date)
        )
        return [BatchOut.model_validate(b) for b in result.scalars().all()]
