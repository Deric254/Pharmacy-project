from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import get_current_user, require_permission
from app.models.user import User
from app.schemas.category import CategoryCreate, CategoryOut
from app.services.category_service import CategoryService

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    _current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[CategoryOut]:
    # Login-gated only, same as products.py's browse endpoint -- every
    # screen that assigns or displays a category (product form,
    # purchasing) needs this list regardless of the user's specific
    # permissions.
    return await CategoryService(db).list_all()


@router.post("", response_model=CategoryOut, status_code=201)
async def create_category(
    payload: CategoryCreate,
    _user: Annotated[User, Depends(require_permission("products.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CategoryOut:
    return await CategoryService(db).create(payload)
