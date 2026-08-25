"""
Purchasing service.

Stock only ever enters through quick_purchase: physically-here stock,
receipted in one atomic step -- real batches, real stock movements,
real money owed to the supplier, no draft/sent/in-transit ceremony in
between. There used to be a full state machine here
(DRAFT -> SENT -> IN_TRANSIT -> RECEIVED -> RECONCILED) but nothing in
the app could ever put a PO into any state before RECEIVED (there was
no "create a draft PO" call wired anywhere in the frontend), so that
whole pipeline was unreachable dead code sitting alongside the one
path that actually worked. Removed rather than left as an inert trap
for the next person who might wire a button up to it.
"""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus
from app.models.stock_movement import MovementType, StockMovement
from app.models.supplier import Supplier, SupplierTransaction
from app.models.user import User
from app.schemas.purchase_order import PurchaseOrderOut, QuickPurchaseRequest


class PurchasingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def quick_purchase(self, payload: QuickPurchaseRequest, user: User) -> PurchaseOrderOut:
        """
        The direct path: stock that's already physically here, no
        advance order, no draft/send/in-transit ceremony. Goes
        straight to a PO that's already RECEIVED -- real batches, real
        stock movements, real money owed to the supplier -- in one
        atomic step. Internally this is still a real purchase order
        (so it shows up in the same history, still contributes to the
        same supplier balance), it just never sits in an intermediate
        state along the way.
        """
        supplier_result = await self.db.execute(
            select(Supplier).where(Supplier.id == payload.supplier_id)
        )
        if supplier_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        now = datetime.now(UTC)
        po = PurchaseOrder(
            supplier_id=payload.supplier_id,
            created_by_user_id=user.id,
            notes=payload.notes,
            status=PurchaseOrderStatus.RECEIVED,
            sent_at=now,
            in_transit_at=now,
            received_at=now,
        )
        self.db.add(po)
        await self.db.flush()

        total_owed = 0.0
        product_result = await self.db.execute(
            select(Product).where(Product.id.in_([line.product_id for line in payload.lines]))
        )
        products_by_id = {product.id: product for product in product_result.scalars().all()}
        for line in payload.lines:
            product = products_by_id.get(line.product_id)
            if product is None:
                raise HTTPException(status_code=404, detail="Product not found")
            selling_price = (
                line.selling_price
                if line.selling_price is not None
                else product.default_selling_price
            )
            # Receiving the same physical batch again -- same product,
            # same batch number, same expiry -- merges into the
            # existing record via standard weighted-average cost,
            # rather than creating a second, separate batch row for
            # what is physically the identical batch. This is exactly
            # what re-uploading the same (or an updated) purchase list
            # should do: add to what's already there, never duplicate it.
            existing_result = await self.db.execute(
                select(MedicineBatch).where(
                    MedicineBatch.product_id == line.product_id,
                    MedicineBatch.batch_number == line.batch_number,
                    MedicineBatch.expiry_date == line.expiry_date,
                )
            )
            existing_batch = existing_result.scalar_one_or_none()

            if existing_batch is not None:
                combined_qty = existing_batch.qty_remaining + line.quantity
                existing_batch.cost_price = (
                    existing_batch.qty_remaining * existing_batch.cost_price
                    + line.quantity * line.unit_cost
                ) / combined_qty
                if (
                    existing_batch.selling_price is not None
                    and existing_batch.selling_price != selling_price
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This batch already has a different selling price. "
                            "Use a different batch number or edit the existing batch."
                        ),
                    )
                existing_batch.qty_received += line.quantity
                existing_batch.qty_remaining = combined_qty
                batch = existing_batch
            else:
                batch = MedicineBatch(
                    product_id=line.product_id,
                    batch_number=line.batch_number,
                    expiry_date=line.expiry_date,
                    qty_received=line.quantity,
                    qty_remaining=line.quantity,
                    cost_price=line.unit_cost,
                    selling_price=selling_price,
                )
                self.db.add(batch)
            await self.db.flush()

            self.db.add(
                StockMovement(
                    batch_id=batch.id,
                    movement_type=MovementType.PURCHASE,
                    quantity_delta=line.quantity,
                    created_by_user_id=user.id,
                    reference=f"po:{po.id}",
                )
            )

            self.db.add(
                PurchaseOrderItem(
                    purchase_order_id=po.id,
                    product_id=line.product_id,
                    quantity_ordered=line.quantity,
                    unit_cost_expected=line.unit_cost,
                    quantity_received=line.quantity,
                    unit_cost_actual=line.unit_cost,
                    batch_id=batch.id,
                )
            )
            total_owed += line.quantity * line.unit_cost

        if total_owed > 0:
            self.db.add(
                SupplierTransaction(
                    supplier_id=payload.supplier_id, amount=total_owed, reference=f"po:{po.id}"
                )
            )

        await self.db.commit()
        await self.db.refresh(po, attribute_names=["items", "created_at"])
        return PurchaseOrderOut.model_validate(po)

    async def get(self, po_id: int) -> PurchaseOrderOut:
        result = await self.db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
        po = result.scalar_one_or_none()
        if po is None:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return PurchaseOrderOut.model_validate(po)

    async def list_all(self) -> list[PurchaseOrderOut]:
        result = await self.db.execute(
            select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())
        )
        return [PurchaseOrderOut.model_validate(po) for po in result.scalars().all()]
