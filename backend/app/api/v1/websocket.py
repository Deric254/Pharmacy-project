"""
WebSocket route.

Browsers can't attach a custom Authorization header to a WebSocket
handshake, so the access token is passed as a query parameter instead
-- the standard, widely-used pattern for this exact constraint. The
token is validated with the same decode_token used everywhere else;
only the transport differs.

The token is checked once, at the handshake, and the connection's
permissions are fixed from then on -- so the connection is closed
(TOKEN_EXPIRED_CLOSE_CODE) the moment that access token expires. A user
who has since been deactivated, demoted or sent to reset their password
therefore cannot stay subscribed indefinitely; a client that is still
entitled to the feed refreshes its token and reconnects.
"""

import asyncio
import contextlib
import time
from typing import NamedTuple

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.security import JWTError, decode_token
from app.core.websocket_manager import manager
from app.models.role import Role
from app.models.user import User

router = APIRouter()

# Sent both when the handshake token is rejected and when an accepted
# connection's token later expires; the client refreshes and reconnects.
TOKEN_EXPIRED_CLOSE_CODE = 4001


class _AuthenticatedSocket(NamedTuple):
    user: User
    expires_at: float  # epoch seconds, from the access token's exp claim


@router.websocket("/ws/notifications")
async def notifications_websocket(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    authenticated = await _authenticate(token)
    if authenticated is None:
        await websocket.close(code=TOKEN_EXPIRED_CLOSE_CODE)
        return
    user = authenticated.user

    await websocket.accept()
    manager.connect(websocket, user)
    expiry_watchdog = asyncio.create_task(
        _close_when_token_expires(websocket, authenticated.expires_at)
    )
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
        expiry_watchdog.cancel()
        manager.disconnect(websocket, user.id)


async def _close_when_token_expires(websocket: WebSocket, expires_at: float) -> None:
    await asyncio.sleep(max(expires_at - time.time(), 0))
    with contextlib.suppress(RuntimeError):  # the client already went away
        await websocket.close(code=TOKEN_EXPIRED_CLOSE_CODE)


async def _authenticate(token: str) -> _AuthenticatedSocket | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
        if user_id is None:
            return None
        expires_at = float(payload["exp"])
    except JWTError:
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User)
            .options(selectinload(User.role).selectinload(Role.permissions))
            .where(
                User.id == int(user_id),
                User.is_active.is_(True),
                User.must_change_password.is_(False),
            )
        )
        user = result.scalar_one_or_none()
    return None if user is None else _AuthenticatedSocket(user, expires_at)
