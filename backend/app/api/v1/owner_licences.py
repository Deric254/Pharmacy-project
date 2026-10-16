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
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.core.licence_gate import get_licensing
from app.core.licence_ledger import (
    LedgerError,
    get_ledger,
    month_of,
    months_between,
    previous_month,
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
    # Needed only when this NEW code is for a name that already has an active computer:
    # "migration" = it replaces one of them (that one is retired), "extra" = one more computer.
    relation: Literal["migration", "extra"] | None = None
    replaces: str | None = Field(default=None, max_length=40)


class PaymentIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    paid: bool


class RetiredIn(BaseModel):
    retired: bool


class DetailsIn(BaseModel):
    phone: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=500)
    monthly_fee: float | None = Field(default=None, ge=0, le=1_000_000_000)
    clear_fee: bool = False


# ---------------------------------------------------------------------- views
def _today() -> date:
    return date.today()


def _name_key(name: str) -> str:
    """How two spellings of one customer name are recognised as the same."""
    return re.sub(r"\s+", " ", name.strip()).casefold()


def _seat_counts(customers: list[dict[str, Any]]) -> dict[str, int]:
    """Active (not retired) computers per customer name."""
    counts: dict[str, int] = {}
    for c in customers:
        if not c.get("retired"):
            key = _name_key(c["name"])
            counts[key] = counts.get(key, 0) + 1
    return counts


def _customer_view(c: dict[str, Any], today: date, seats: dict[str, int]) -> dict[str, Any]:
    licences = c.get("licences", [])
    latest = max(licences, key=lambda lic: (lic["expires"], lic["issued"])) if licences else None
    expires = latest["expires"] if latest else None
    days_left = (date.fromisoformat(expires) - today).days if expires else None
    retired = bool(c.get("retired"))
    retired_on = c.get("retired_on") if retired else None
    if retired:
        status = "retired"
    elif days_left is None or days_left < 0:
        status = "expired"
    elif days_left <= EXPIRING_SOON_DAYS:
        status = "expiring"
    else:
        status = "active"

    payments = c.get("payments", {})
    paid_months = sorted(m for m, p in payments.items() if p.get("paid"))
    first = month_of(date.fromisoformat(c["created"]))
    if retired and retired_on:
        # A retired computer is owed for the months before it was replaced; the month it
        # was replaced belongs to the new computer (otherwise one payment is counted twice).
        last = max([previous_month(date.fromisoformat(retired_on)), *paid_months])
    else:
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
        "retired": retired,
        "retired_on": retired_on,
        "active_computers": seats.get(_name_key(c["name"]), 0),
        "licence_count": len(licences),
        "latest_licence": latest["token"] if latest else None,
        "months": [{"month": m, "paid": m in paid_months} for m in months],
        "paid_count": len([m for m in months if m in paid_months]),
        "unpaid_months": unpaid,
        "amount_outstanding": round(fee * len(unpaid), 2) if fee is not None else None,
    }


def _view_of(customer: dict[str, Any], today: date) -> dict[str, Any]:
    return _customer_view(customer, today, _seat_counts(get_ledger().customers()))


def _build_list(today: date) -> dict[str, Any]:
    customers = get_ledger().customers()
    seats = _seat_counts(customers)
    views = [_customer_view(c, today, seats) for c in customers]
    views.sort(key=lambda v: (_name_key(v["name"]), v["retired"], v["created"]))
    return {
        "summary": {
            # Computers that still count: a retired one is history, not a user.
            "total_customers": sum(not v["retired"] for v in views),
            "retired": sum(v["retired"] for v in views),
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


def _resolve_new_computer(body: IssueIn, same_name: list[dict[str, Any]], name: str) -> str | None:
    """A new activation code for a name that already has active computers: the owner must say
    whether it is a migration or an extra computer. Returns the code to retire, if any."""
    have = len(same_name)
    if body.relation is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{name} already has {have} active computer{'s' if have != 1 else ''}. "
                "Say whether this new code is a migration (replaces one) or an extra computer."
            ),
        )
    if body.relation == "extra":
        return None
    if body.replaces:
        target = next((c for c in same_name if c["code"] == norm_code(body.replaces)), None)
        if target is None:
            raise HTTPException(
                status_code=400,
                detail="The computer being replaced doesn't belong to this customer.",
            )
    elif have == 1:
        target = same_name[0]
    else:
        raise HTTPException(status_code=400, detail="Choose which computer is being replaced.")
    return str(target["code"])


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
        all_customers = ledger.customers()
        existing = next((c for c in all_customers if c["code"] == code), None)
        retire_code: str | None = None
        if existing is not None:
            if _name_key(existing["name"]) != _name_key(name):
                # A customer's copy only accepts a renewal for the name it was activated with.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This activation code already belongs to {existing['name']}. "
                        "Renewals must use the same name."
                    ),
                )
            name = existing["name"]
            if existing.get("retired"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This computer was retired on {existing.get('retired_on')}. Renewing it "
                        "would keep two computers licensed. Restore it first if that is intended."
                    ),
                )
        else:
            same_name = [
                c
                for c in all_customers
                if not c.get("retired") and _name_key(c["name"]) == _name_key(name)
            ]
            if same_name:
                retire_code = _resolve_new_computer(body, same_name, name)
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
            retire_code=retire_code,
        )
        return {
            "licence": out["licence"],
            "user": out["user"],
            "code": code,
            "expires": out["expires"],
            "renewal": existing is not None,
            "retired_code": retire_code,
            "customer": _view_of(customer, today),
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
    return _view_of(customer, today)


@router.put("/{customer_id}/retired", dependencies=[OwnerOnly])
async def set_retired(customer_id: str, body: RetiredIn) -> dict[str, Any]:
    """Retire a computer (it can no longer be renewed here) or restore it."""
    today = _today()
    customer = await asyncio.to_thread(
        lambda: _ledger_call(get_ledger().set_retired, customer_id, body.retired, today)
    )
    return _view_of(customer, today)


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
    return _view_of(customer, today)


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
                "Retired on",
                "Active computers",
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
                    v["retired_on"] or "",
                    v["active_computers"],
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
