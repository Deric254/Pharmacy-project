from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryOut


class CategoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_all(self) -> list[CategoryOut]:
        result = await self.db.execute(select(Category).order_by(Category.name))
        return [CategoryOut.model_validate(c) for c in result.scalars().all()]

    async def create(self, payload: CategoryCreate) -> CategoryOut:
        existing = await self.db.execute(
            select(Category).where(func.lower(Category.name) == payload.name.lower())
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="A category with that name already exists")

        category = Category(name=payload.name)
        self.db.add(category)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            # Same belt-and-braces pattern as ProductService.create: the
            # pre-check above handles the common case, this catches two
            # concurrent creates racing past it.
            await self.db.rollback()
            raise HTTPException(
                status_code=409,
                detail="A category with that name was just created. Please refresh.",
            ) from exc
        await self.db.commit()
        await self.db.refresh(category)
        return CategoryOut.model_validate(category)
