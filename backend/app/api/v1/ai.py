from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.ai import (
    AIAskRequest,
    AIAskResponse,
    AIConversationDetailOut,
    AIConversationOut,
    AIProviderKeyCreate,
    AIProviderKeyOut,
)
from app.services.ai_assistant_service import AIAssistantService
from app.services.ai_conversation_service import AIConversationService, ConversationNotFound
from app.services.ai_key_service import AIKeyService

router = APIRouter(prefix="/ai", tags=["ai-assistant"])


@router.get("/keys", response_model=list[AIProviderKeyOut])
async def list_keys(
    user: Annotated[User, Depends(require_permission("users.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[AIProviderKeyOut]:
    return await AIKeyService(db).list_keys(user)


@router.post("/keys", response_model=AIProviderKeyOut, status_code=201)
async def add_key(
    payload: AIProviderKeyCreate,
    user: Annotated[User, Depends(require_permission("users.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AIProviderKeyOut:
    return await AIKeyService(db).add_key(user, payload)


@router.delete("/keys/{key_id}", status_code=204)
async def delete_key(
    key_id: int,
    user: Annotated[User, Depends(require_permission("users.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await AIKeyService(db).delete_key(user, key_id)


@router.get("/conversations", response_model=list[AIConversationOut])
async def list_conversations(
    user: Annotated[User, Depends(require_permission("ai.use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[AIConversationOut]:
    return await AIConversationService(db).list_conversations(user)


@router.get("/conversations/{conversation_id}", response_model=AIConversationDetailOut)
async def get_conversation(
    conversation_id: int,
    user: Annotated[User, Depends(require_permission("ai.use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AIConversationDetailOut:
    conversation = await AIConversationService(db).get_conversation_detail(user, conversation_id)
    if conversation is None:
        # Same message whether the id doesn't exist or belongs to
        # someone else -- never reveal which, to a user probing ids.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    user: Annotated[User, Depends(require_permission("ai.use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    deleted = await AIConversationService(db).delete_conversation(user, conversation_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


@router.post("/ask", response_model=AIAskResponse)
async def ask(
    payload: AIAskRequest,
    user: Annotated[User, Depends(require_permission("ai.use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AIAskResponse:
    try:
        return await AIAssistantService(db).ask(user, payload)
    except ConversationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found") from exc
