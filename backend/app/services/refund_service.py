"""
Refund service.

A refund is always against specific SaleItem rows from a specific
sale -- never a free-floating "refund $X to this customer" -- so every
refunded cent and every unit put back on the shelf traces to exactly
what was originally sold. Two invariants this enforces that a naive
implementation would miss:

1. Over-refund prevention: the sum of quantities refunded against a
   given SaleItem across ALL refunds (not just this one) can never
   exceed what was originally sold on that line. Without summing
   prior refunds, the same 3 units could be "returned" five times.

2. Stock-take lock respect: restocking a batch currently locked for a
   physical count would change the count out from under the counter,
   exactly like a sale against a locked batch would. Refunds against
   a locked batch are rejected (409) rather than silently skipping the
   restock -- the cashier should retry once the count closes, not have
   money quietly leave the till with no matching stock movement.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.medicine_batch import MedicineBatch
from app.models.refund import Refund, RefundItem
from app.models.sale import Sale
from app.models.stock_movement import MovementType, StockMovement
from app.models.user import User
from app.schemas.refund import RefundOut, RefundRequest


class RefundService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_refund(
        self, sale_id: int, payload: RefundRequest, processed_by: User
    ) -> RefundOut:
        sale = await self._get_sale_or_404(sale_id)
        sale_items_by_id = {item.id: item for item in sale.items}

        refund = Refund(
            sale_id=sale.id,
            processed_by_user_id=processed_by.id,
            reason=payload.reason,
            method=payload.method,
            notes=payload.notes,
            total_amount=0.0,  # filled in after validating every line
        )
        self.db.add(refund)
        await self.db.flush()

        total_amount = 0.0
        for line in payload.items:
            sale_item = sale_items_by_id.get(line.sale_item_id)
            if sale_item is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Sale item {line.sale_item_id} does not belong to sale {sale_id}",
                )

            already_refunded = await self._already_refunded_quantity(sale_item.id)
            remaining = sale_item.quantity - already_refunded
            if line.quantity > remaining:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot refund {line.quantity} of sale item {sale_item.id}: "
                        f"only {remaining} unit(s) remain refundable "
                        f"({already_refunded} already refunded of {sale_item.quantity} sold)"
                    ),
                )

            line_total = sale_item.unit_price * line.quantity
            total_amount += line_total

            if line.restock:
                await self._restock_batch(
                    sale_item.batch_id, line.quantity, refund.id, processed_by.id
                )

            self.db.add(
                RefundItem(
                    refund_id=refund.id,
                    sale_item_id=sale_item.id,
                    product_id=sale_item.product_id,
                    batch_id=sale_item.batch_id,
                    quantity=line.quantity,
                    unit_price=sale_item.unit_price,
                    line_total=line_total,
                    restocked=line.restock,
                )
            )

        refund.total_amount = total_amount
        self.db.add(
            AuditLog(
                user_id=processed_by.id,
                action="sale.refunded",
                entity_type="refund",
                entity_id=str(refund.id),
                new_value=f"sale_id={sale.id} amount={total_amount:.2f} reason={payload.reason}",
            )
        )
        await self.db.commit()
        await self.db.refresh(refund, attribute_names=["items", "created_at"])
        return RefundOut.model_validate(refund)

    async def list_refunds_for_sale(self, sale_id: int) -> list[RefundOut]:
        await self._get_sale_or_404(sale_id)
        result = await self.db.execute(
            select(Refund).where(Refund.sale_id == sale_id).order_by(Refund.created_at)
        )
        return [RefundOut.model_validate(r) for r in result.scalars().all()]

    async def _get_sale_or_404(self, sale_id: int) -> Sale:
        result = await self.db.execute(select(Sale).where(Sale.id == sale_id))
        sale = result.scalar_one_or_none()
        if sale is None:
            raise HTTPException(status_code=404, detail="Sale not found")
        return sale

    async def _already_refunded_quantity(self, sale_item_id: int) -> int:
        result = await self.db.execute(
            select(RefundItem).where(RefundItem.sale_item_id == sale_item_id)
        )
        return sum(item.quantity for item in result.scalars().all())

    async def _restock_batch(
        self, batch_id: int, quantity: int, refund_id: int, created_by_user_id: int
    ) -> None:
        result = await self.db.execute(
            select(MedicineBatch).where(MedicineBatch.id == batch_id).with_for_update()
        )
        batch = result.scalar_one_or_none()
        if batch is None:
            raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
        if batch.locked_by_stock_take_id is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Batch {batch_id} is locked by an open stock take and cannot be "
                    "restocked right now. Retry once the count closes, or refund without "
                    "restocking this line."
                ),
            )

        batch.qty_remaining += quantity
        self.db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=MovementType.RETURN,
                quantity_delta=quantity,
                reference=f"refund:{refund_id}",
                created_by_user_id=created_by_user_id,
            )
        )
