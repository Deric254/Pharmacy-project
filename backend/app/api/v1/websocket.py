"""
WebSocket route.

Browsers can't attach a custom Authorization header to a WebSocket
handshake, so the access token is passed as a query parameter instead
-- the standard, widely-used pattern for this exact constraint. The
token is validated with the same decode_token used everywhere else;
only the transport differs.
"""

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.security import JWTError, decode_token
from app.core.websocket_manager import manager
from app.models.role import Role
from app.models.user import User

router = APIRouter()


@router.websocket("/ws/notifications")
async def notifications_websocket(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    user = await _authenticate(token)
    if user is None:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    manager.connect(websocket, user)
    try:
        while True:
            # This endpoint is push-only from the server's side; the
            # receive call exists purely to detect disconnects (a
            # closed socket raises WebSocketDisconnect here). Any
            # client message is intentionally ignored.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, user.id)


async def _authenticate(token: str) -> User | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User)
            .options(selectinload(User.role).selectinload(Role.permissions))
            .where(User.id == int(user_id), User.is_active.is_(True))
        )
        return result.scalar_one_or_none()
