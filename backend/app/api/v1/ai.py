from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.ai import AIAskRequest, AIAskResponse, AIProviderKeyCreate, AIProviderKeyOut
from app.services.ai_assistant_service import AIAssistantService
from app.services.ai_key_service import AIKeyService

router = APIRouter(prefix="/ai", tags=["ai-assistant"])


@router.get("/keys", response_model=list[AIProviderKeyOut])
async def list_keys(
    user: Annotated[User, Depends(require_permission("ai.use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[AIProviderKeyOut]:
    return await AIKeyService(db).list_keys(user)


@router.post("/keys", response_model=AIProviderKeyOut, status_code=201)
async def add_key(
    payload: AIProviderKeyCreate,
    user: Annotated[User, Depends(require_permission("ai.use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AIProviderKeyOut:
    return await AIKeyService(db).add_key(user, payload)


@router.delete("/keys/{key_id}", status_code=204)
async def delete_key(
    key_id: int,
    user: Annotated[User, Depends(require_permission("ai.use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await AIKeyService(db).delete_key(user, key_id)


@router.post("/ask", response_model=AIAskResponse)
async def ask(
    payload: AIAskRequest,
    user: Annotated[User, Depends(require_permission("ai.use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AIAskResponse:
    return await AIAssistantService(db).ask(user, payload)
