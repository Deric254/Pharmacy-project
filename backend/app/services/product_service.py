from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate


class ProductService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, payload: ProductCreate) -> ProductOut:
        if payload.barcode:
            existing = await self.db.execute(
                select(Product).where(Product.barcode == payload.barcode)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(status_code=409, detail="Barcode already in use")

        product = Product(**payload.model_dump())
        self.db.add(product)
        await self.db.commit()
        await self.db.refresh(product)
        return await self._to_schema(product)

    async def update(self, product_id: int, payload: ProductUpdate) -> ProductOut:
        product = await self._get_or_404(product_id)

        update_data = payload.model_dump(exclude_unset=True)
        if "barcode" in update_data and update_data["barcode"]:
            existing = await self.db.execute(
                select(Product).where(
                    Product.barcode == update_data["barcode"], Product.id != product_id
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(status_code=409, detail="Barcode already in use")

        for field, value in update_data.items():
            setattr(product, field, value)

        await self.db.commit()
        await self.db.refresh(product)
        return await self._to_schema(product)

    async def get(self, product_id: int) -> ProductOut:
        product = await self._get_or_404(product_id)
        return await self._to_schema(product)

    async def get_by_barcode(self, barcode: str) -> ProductOut:
        result = await self.db.execute(
            select(Product).where(Product.barcode == barcode, Product.deleted_at.is_(None))
        )
        product = result.scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found for that barcode")
        return await self._to_schema(product)

    async def list_all(self, search: str | None = None) -> list[ProductOut]:
        query = select(Product).where(Product.deleted_at.is_(None))
        if search:
            query = query.where(Product.name.ilike(f"%{search}%"))
        result = await self.db.execute(query.order_by(Product.name))
        products = result.scalars().all()
        if not products:
            return []

        # One aggregated query for every product's quantity, instead of
        # one query per product (the previous version called _to_schema
        # per row, each issuing its own SUM query -- O(n) round trips
        # for a list endpoint that should be O(1)).
        product_ids = [p.id for p in products]
        qty_result = await self.db.execute(
            select(
                MedicineBatch.product_id, func.coalesce(func.sum(MedicineBatch.qty_remaining), 0)
            )
            .where(MedicineBatch.product_id.in_(product_ids))
            .group_by(MedicineBatch.product_id)
        )
        qty_by_product: dict[int, int] = dict(qty_result.tuples().all())

        outputs = []
        for product in products:
            out = ProductOut.model_validate(product)
            out.total_qty_available = int(qty_by_product.get(product.id, 0))
            outputs.append(out)
        return outputs

    async def delete(self, product_id: int) -> None:
        """
        Soft delete only -- sets deleted_at, never a hard DELETE. This
        preserves referential integrity for historical sales/purchase
        order items that still reference this product; they must
        remain fully readable after the product is discontinued.
        """
        product = await self._get_or_404(product_id)
        product.deleted_at = datetime.now(UTC)
        await self.db.commit()

    async def _get_or_404(self, product_id: int) -> Product:
        result = await self.db.execute(
            select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
        )
        product = result.scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product

    async def _to_schema(self, product: Product) -> ProductOut:
        qty_result = await self.db.execute(
            select(func.coalesce(func.sum(MedicineBatch.qty_remaining), 0)).where(
                MedicineBatch.product_id == product.id
            )
        )
        total_qty = qty_result.scalar_one()
        out = ProductOut.model_validate(product)
        out.total_qty_available = int(total_qty)
        return out
