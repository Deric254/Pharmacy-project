"""
Owner-only licence desk: type the activation code a customer sent, get their
licence back, keep a ledger of every customer and which months they've paid.

Three locks, all enforced here on the server (the hidden menu item is only a
convenience):
  1. a signed-in user with the roles.manage permission (the top "owner" role, not ordinary admins),
  2. the copy of the app must be the OWNER's own computer (it proved ownership
     once with the owner key, see licence_gate / claim-owner). On any customer
     copy every route here answers 403,
  3. issuing a licence also needs the owner key itself, sent with that request
     and used only in memory -- it is never stored or logged.
"""

import asyncio
import csv
import io
import re
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.core.licence_gate import get_licensing
from app.core.licence_ledger import (
    LedgerError,
    get_ledger,
    month_of,
    months_between,
    valid_month,
)
from app.core.rbac import require_permission
from app.models.user import User
from licensing.licensing import LicenceError, norm_code

router = APIRouter(prefix="/owner/licences", tags=["owner-licences"])

EXPIRING_SOON_DAYS = 14


async def require_owner_copy(
    user: Annotated[User, Depends(require_permission("roles.manage"))],
) -> User:
    state = await asyncio.to_thread(get_licensing().state)
    if state.get("kind") != "owner":
        raise HTTPException(
            status_code=403,
            detail="This is only available on the owner's own computer.",
        )
    return user


OwnerOnly = Depends(require_owner_copy)


# ---------------------------------------------------------------------- schemas
class IssueIn(BaseModel):
    owner_key: str = Field(min_length=1, max_length=512)
    code: str = Field(min_length=1, max_length=40)
    user: str = Field(min_length=1, max_length=60)
    days: int = Field(ge=1, le=365)
    paid_this_month: bool = False
    phone: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=500)
    monthly_fee: float | None = Field(default=None, ge=0, le=1_000_000_000)


class PaymentIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    paid: bool


class DetailsIn(BaseModel):
    phone: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=500)
    monthly_fee: float | None = Field(default=None, ge=0, le=1_000_000_000)
    clear_fee: bool = False


# ---------------------------------------------------------------------- views
def _today() -> date:
    return date.today()


def _customer_view(c: dict[str, Any], today: date) -> dict[str, Any]:
    licences = c.get("licences", [])
    latest = max(licences, key=lambda lic: (lic["expires"], lic["issued"])) if licences else None
    expires = latest["expires"] if latest else None
    days_left = (date.fromisoformat(expires) - today).days if expires else None
    if days_left is None or days_left < 0:
        status = "expired"
    elif days_left <= EXPIRING_SOON_DAYS:
        status = "expiring"
    else:
        status = "active"

    payments = c.get("payments", {})
    paid_months = sorted(m for m, p in payments.items() if p.get("paid"))
    first = month_of(date.fromisoformat(c["created"]))
    last = max([month_of(today), *paid_months])
    months = months_between(first, last)
    unpaid = [m for m in months if m not in paid_months]
    fee = c.get("monthly_fee")
    return {
        "id": c["id"],
        "name": c["name"],
        "code": c["code"],
        "phone": c.get("phone", ""),
        "notes": c.get("notes", ""),
        "monthly_fee": fee,
        "created": c["created"],
        "last_issued": latest["issued"] if latest else None,
        "expires": expires,
        "days_left": days_left,
        "status": status,
        "licence_count": len(licences),
        "latest_licence": latest["token"] if latest else None,
        "months": [{"month": m, "paid": m in paid_months} for m in months],
        "paid_count": len([m for m in months if m in paid_months]),
        "unpaid_months": unpaid,
        "amount_outstanding": round(fee * len(unpaid), 2) if fee is not None else None,
    }


def _build_list(today: date) -> dict[str, Any]:
    views = [_customer_view(c, today) for c in get_ledger().customers()]
    views.sort(key=lambda v: v["name"].casefold())
    return {
        "summary": {
            "total_customers": len(views),
            "active": sum(v["status"] == "active" for v in views),
            "expiring": sum(v["status"] == "expiring" for v in views),
            "expired": sum(v["status"] == "expired" for v in views),
            "unpaid_months": sum(len(v["unpaid_months"]) for v in views),
            "amount_outstanding": round(sum(v["amount_outstanding"] or 0 for v in views), 2),
        },
        "customers": views,
        "current_month": month_of(today),
    }


def _ledger_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------- routes
@router.get("", dependencies=[OwnerOnly])
async def list_customers() -> dict[str, Any]:
    return await asyncio.to_thread(lambda: _ledger_call(_build_list, _today()))


@router.post("/issue", dependencies=[OwnerOnly])
async def issue_licence(body: IssueIn) -> dict[str, Any]:
    """Sign a licence for the activation code and record it. The same code again is a renewal."""

    def run() -> dict[str, Any]:
        today = _today()
        lic = get_licensing()
        code = norm_code(body.code)
        name = re.sub(r"\s+", " ", body.user.strip())
        ledger = get_ledger()
        existing = next((c for c in ledger.customers() if c["code"] == code), None)
        if existing is not None:
            if existing["name"].casefold() != name.casefold():
                # A customer's copy only accepts a renewal for the name it was activated with.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This activation code already belongs to {existing['name']}. "
                        "Renewals must use the same name."
                    ),
                )
            name = existing["name"]
        try:
            seed = lic.parse_owner_key(body.owner_key)
            out = lic.issue(seed, code, name, body.days)
        except (LicenceError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        customer = _ledger_call(
            ledger.record_issue,
            code=code,
            name=out["user"],
            token=out["licence"],
            issued=today.isoformat(),
            expires=out["expires"],
            days=body.days,
            phone=(body.phone or "").strip() or None,
            notes=(body.notes or "").strip() or None,
            monthly_fee=body.monthly_fee,
            paid_month=month_of(today) if body.paid_this_month else None,
        )
        return {
            "licence": out["licence"],
            "user": out["user"],
            "code": code,
            "expires": out["expires"],
            "renewal": existing is not None,
            "customer": _customer_view(customer, today),
        }

    return await asyncio.to_thread(run)


@router.put("/{customer_id}/payments", dependencies=[OwnerOnly])
async def set_payment(customer_id: str, body: PaymentIn) -> dict[str, Any]:
    if not valid_month(body.month):
        raise HTTPException(status_code=422, detail="The month must look like 2026-10.")
    today = _today()
    customer = await asyncio.to_thread(
        lambda: _ledger_call(get_ledger().set_payment, customer_id, body.month, body.paid, today)
    )
    return _customer_view(customer, today)


@router.patch("/{customer_id}", dependencies=[OwnerOnly])
async def update_customer(customer_id: str, body: DetailsIn) -> dict[str, Any]:
    today = _today()
    customer = await asyncio.to_thread(
        lambda: _ledger_call(
            get_ledger().update_details,
            customer_id,
            phone=body.phone,
            notes=body.notes,
            monthly_fee=body.monthly_fee,
            clear_fee=body.clear_fee,
        )
    )
    return _customer_view(customer, today)


@router.delete("/{customer_id}", status_code=204, dependencies=[OwnerOnly])
async def delete_customer(customer_id: str) -> Response:
    """Removes the ledger row only. A licence already sent keeps working until it expires."""
    await asyncio.to_thread(lambda: _ledger_call(get_ledger().delete, customer_id))
    return Response(status_code=204)


def _safe_cell(value: Any) -> Any:
    """Stop spreadsheet apps treating typed text as a formula (names and notes are free text)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@router.get("/export.csv", dependencies=[OwnerOnly])
async def export_csv() -> Response:
    def build() -> str:
        today = _today()
        views = _build_list(today)["customers"]
        all_months: list[str] = []
        if views:
            first = min(month_of(date.fromisoformat(v["created"])) for v in views)
            last = max([v["months"][-1]["month"] for v in views if v["months"]] or [first])
            all_months = months_between(first, last)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(
            [
                "Name",
                "Activation code",
                "Phone",
                "Monthly fee",
                "First licensed",
                "Last issued",
                "Expires",
                "Status",
                "Months paid",
                "Months unpaid",
                "Outstanding",
                "Notes",
                *all_months,
            ]
        )
        for v in views:
            paid = {m["month"]: m["paid"] for m in v["months"]}
            w.writerow(
                [
                    _safe_cell(v["name"]),
                    v["code"],
                    _safe_cell(v["phone"]),
                    "" if v["monthly_fee"] is None else v["monthly_fee"],
                    v["created"],
                    v["last_issued"] or "",
                    v["expires"] or "",
                    v["status"],
                    v["paid_count"],
                    len(v["unpaid_months"]),
                    "" if v["amount_outstanding"] is None else v["amount_outstanding"],
                    _safe_cell(v["notes"]),
                    *[("Paid" if paid[m] else "UNPAID") if m in paid else "" for m in all_months],
                ]
            )
        # utf-8-sig so Excel opens names with accents correctly.
        return out.getvalue()

    text = await asyncio.to_thread(lambda: _ledger_call(build))
    return Response(
        content=("﻿" + text).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="licence-ledger-{_today().isoformat()}.csv"'
            )
        },
    )
