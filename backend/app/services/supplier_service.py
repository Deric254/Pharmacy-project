from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier import Supplier, SupplierTransaction
from app.schemas.supplier import PaymentRecordRequest, SupplierCreate, SupplierOut


class SupplierService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, payload: SupplierCreate) -> SupplierOut:
        supplier = Supplier(**payload.model_dump())
        self.db.add(supplier)
        await self.db.commit()
        await self.db.refresh(supplier)
        return await self._to_schema(supplier)

    async def list_all(self) -> list[SupplierOut]:
        result = await self.db.execute(select(Supplier).order_by(Supplier.name))
        return [await self._to_schema(s) for s in result.scalars().all()]

    async def get(self, supplier_id: int) -> SupplierOut:
        supplier = await self._get_or_404(supplier_id)
        return await self._to_schema(supplier)

    async def record_payment(self, supplier_id: int, payload: PaymentRecordRequest) -> SupplierOut:
        supplier = await self._get_or_404(supplier_id)
        self.db.add(
            SupplierTransaction(
                supplier_id=supplier.id,
                amount=-payload.amount,  # negative: reduces what's owed
                reference="manual-payment",
                notes=payload.notes,
            )
        )
        await self.db.commit()
        return await self._to_schema(supplier)

    async def _get_or_404(self, supplier_id: int) -> Supplier:
        result = await self.db.execute(select(Supplier).where(Supplier.id == supplier_id))
        supplier = result.scalar_one_or_none()
        if supplier is None:
            raise HTTPException(status_code=404, detail="Supplier not found")
        return supplier

    async def _to_schema(self, supplier: Supplier) -> SupplierOut:
        balance_result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierTransaction.amount), 0.0)).where(
                SupplierTransaction.supplier_id == supplier.id
            )
        )
        balance = float(balance_result.scalar_one())
        out = SupplierOut.model_validate(supplier)
        out.balance_owed = balance
        return out
