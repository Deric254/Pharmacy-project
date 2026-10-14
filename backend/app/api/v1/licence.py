"""
Licence endpoints: show the state and activation code, activate or renew a
licence, claim the owner's own computer. These are the only /api routes that
stay open while the app is locked (see app/core/licence_gate.py).
"""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.licence_gate import get_licensing, public_state, reset_cache
from licensing.licensing import LicenceError

router = APIRouter(prefix="/licence", tags=["licence"])


class ActivateIn(BaseModel):
    licence: str = Field(min_length=1, max_length=4096)


class ClaimOwnerIn(BaseModel):
    owner_key: str = Field(min_length=1, max_length=512)


@router.get("/state")
async def licence_state() -> dict[str, Any]:
    return await asyncio.to_thread(public_state)


@router.post("/activate")
async def activate_licence(body: ActivateIn) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        lic = get_licensing()
        current = lic.state()
        # A renewal must be for the same person who holds the current licence.
        holder = current.get("user") if current.get("kind") == "licensed" else None
        lic.activate(body.licence, expect_user=holder)
        reset_cache()
        return public_state()

    try:
        return await asyncio.to_thread(run)
    except LicenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/claim-owner")
async def claim_owner(body: ClaimOwnerIn) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        get_licensing().claim_owner(body.owner_key)
        reset_cache()
        return public_state()

    try:
        return await asyncio.to_thread(run)
    except LicenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
