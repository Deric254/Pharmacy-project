from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import business_today
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate


class ProductService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, payload: ProductCreate) -> ProductOut:
        if payload.barcode:
            existing = await self.db.execute(
                select(Product).where(
                    Product.barcode == payload.barcode, Product.deleted_at.is_(None)
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(status_code=409, detail="Barcode already in use")

        existing_name = await self.db.execute(
            select(Product).where(
                func.lower(Product.name) == payload.name.lower(), Product.deleted_at.is_(None)
            )
        )
        if existing_name.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f'A product named "{payload.name}" already exists. Search for it and add '
                    "stock through Purchasing rather than creating a duplicate."
                ),
            )

        product = Product(**payload.model_dump())
        self.db.add(product)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            # The real safety net against two concurrent creates racing
            # past the checks above -- both name and barcode uniqueness
            # are also enforced as real database constraints, not just
            # this pre-check.
            await self.db.rollback()
            raise HTTPException(
                status_code=409,
                detail="A product with that name or barcode was just created. Please refresh.",
            ) from exc
        await self.db.refresh(product)
        return await self._to_schema(product)

    async def update(self, product_id: int, payload: ProductUpdate) -> ProductOut:
        product = await self._get_or_404(product_id)

        update_data = payload.model_dump(exclude_unset=True)
        if "barcode" in update_data and update_data["barcode"]:
            existing = await self.db.execute(
                select(Product).where(
                    Product.barcode == update_data["barcode"],
                    Product.deleted_at.is_(None),
                    Product.id != product_id,
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(status_code=409, detail="Barcode already in use")
        if "name" in update_data and update_data["name"]:
            existing_name = await self.db.execute(
                select(Product).where(
                    func.lower(Product.name) == update_data["name"].lower(),
                    Product.deleted_at.is_(None),
                    Product.id != product_id,
                )
            )
            if existing_name.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f'A product named "{update_data["name"]}" already exists.',
                )

        for field, value in update_data.items():
            setattr(product, field, value)

        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=409,
                detail="A product with that name or barcode was just created. Please refresh.",
            ) from exc
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

        # "Available" means actually sellable -- matching exactly what
        # select_batches_fefo allows: expired batches excluded, and
        # batches currently locked by an open stock take excluded too.
        # Before this, a product could show as available here while
        # every real sale attempt correctly failed against it -- a
        # genuine, confusing inconsistency.
        product_ids = [p.id for p in products]
        today = await business_today(self.db)
        qty_result = await self.db.execute(
            select(
                MedicineBatch.product_id, func.coalesce(func.sum(MedicineBatch.qty_remaining), 0)
            )
            .where(
                MedicineBatch.product_id.in_(product_ids),
                MedicineBatch.expiry_date >= today,
                MedicineBatch.locked_by_stock_take_id.is_(None),
            )
            .group_by(MedicineBatch.product_id)
        )
        qty_by_product: dict[int, int] = dict(qty_result.tuples().all())
        next_price_by_product = await self._next_fefo_price_by_product(product_ids)

        outputs = []
        for product in products:
            out = ProductOut.model_validate(product)
            out.total_qty_available = int(qty_by_product.get(product.id, 0))
            cost, selling_price = next_price_by_product.get(product.id, (None, None))
            self._apply_margin(out, cost, selling_price)
            outputs.append(out)

        # Most-stocked first -- name is only a tie-breaker for equal
        # quantities, so the order is always deterministic, never
        # arbitrary or dependent on insertion order.
        outputs.sort(key=lambda o: (-o.total_qty_available, o.name.lower()))
        return outputs

    async def _next_fefo_price_by_product(
        self, product_ids: list[int]
    ) -> dict[int, tuple[float, float | None]]:
        """
        The cost of whichever batch would actually be sold next for
        each product -- same FEFO ordering select_batches_fefo uses
        for real sales, so "margin on this product" means "margin on
        the sale that would actually happen next", not some average
        that could be batch-specific stock is nowhere near reflecting.
        One query for every product (ordered so each product's
        earliest-expiring batch comes first), grouped in Python --
        avoids N+1 queries on what's a list endpoint.
        """
        result = await self.db.execute(
            select(
                MedicineBatch.product_id,
                MedicineBatch.cost_price,
                MedicineBatch.selling_price,
            )
            .where(
                MedicineBatch.product_id.in_(product_ids),
                MedicineBatch.qty_remaining > 0,
                MedicineBatch.expiry_date >= await business_today(self.db),
                MedicineBatch.locked_by_stock_take_id.is_(None),
            )
            .order_by(MedicineBatch.product_id, MedicineBatch.expiry_date.asc())
        )
        next_price_by_product: dict[int, tuple[float, float | None]] = {}
        for product_id, cost_price, selling_price in result.tuples().all():
            next_price_by_product.setdefault(product_id, (cost_price, selling_price))
        return next_price_by_product

    @staticmethod
    def _apply_margin(out: ProductOut, cost: float | None, selling_price: float | None) -> None:
        """
        Margin (profit as a % of selling price) and markup (profit as
        a % of cost) are genuinely different numbers people confuse --
        both are computed and exposed distinctly rather than picking
        one. None when there's no stock to compute a real cost from,
        never a fabricated number.
        """
        if cost is None:
            return
        selling_price = selling_price if selling_price is not None else out.default_selling_price
        out.current_cost = cost
        out.current_selling_price = selling_price
        profit = selling_price - cost
        out.margin_amount = profit
        if selling_price > 0:
            out.margin_percent = (profit / selling_price) * 100
        if cost > 0:
            out.markup_percent = (profit / cost) * 100

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
                MedicineBatch.product_id == product.id,
                MedicineBatch.expiry_date >= await business_today(self.db),
                MedicineBatch.locked_by_stock_take_id.is_(None),
            )
        )
        total_qty = qty_result.scalar_one()
        out = ProductOut.model_validate(product)
        out.total_qty_available = int(total_qty)
        next_price_by_product = await self._next_fefo_price_by_product([product.id])
        cost, selling_price = next_price_by_product.get(product.id, (None, None))
        self._apply_margin(out, cost, selling_price)
        return out
