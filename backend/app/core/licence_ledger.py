"""
Owner-only ledger of the customers the owner has issued licences to, and which
months each one has paid for.

It lives in its own small JSON file (licence-ledger.json) in the same data
folder as the database, exactly like licence-state.json: it needs no
migration and never touches the pharmacy's business tables.

Only the owner's own computer ever uses this (see app/api/v1/owner_licences.py).
Writes are atomic and the previous version is kept as licence-ledger.json.bak.
"""

import json
import os
import re
import secrets
import shutil
import threading
from datetime import date
from pathlib import Path
from typing import Any

from app.core import licence_gate

LEDGER_FILENAME = "licence-ledger.json"
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class LedgerError(Exception):
    """The ledger can't be read or a request is invalid; the message is shown to the owner."""


def valid_month(value: str) -> bool:
    return bool(_MONTH_RE.match(value))


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def previous_month(d: date) -> str:
    """The calendar month before the one `d` falls in, as YYYY-MM."""
    year, month = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return f"{year:04d}-{month:02d}"


def months_between(first: str, last: str) -> list[str]:
    """Every month from `first` to `last` inclusive, as YYYY-MM (empty if last < first)."""
    y, m = int(first[:4]), int(first[5:])
    ly, lm = int(last[:4]), int(last[5:])
    out: list[str] = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


class LicenceLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ storage
    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"customers": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Never start a fresh ledger over a file we couldn't read: that would lose the records.
            raise LedgerError(
                f"The licence ledger file could not be read ({self.path.name}). "
                f"Nothing was changed. Restore {self.path.name}.bak over it if needed."
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("customers"), list):
            raise LedgerError(
                f"The licence ledger file ({self.path.name}) has an unexpected format."
            )
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ reads
    def customers(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._load()["customers"])

    # ------------------------------------------------------------------ writes
    def record_issue(
        self,
        *,
        code: str,
        name: str,
        token: str,
        issued: str,
        expires: str,
        days: int,
        phone: str | None,
        notes: str | None,
        monthly_fee: float | None,
        paid_month: str | None,
        retire_code: str | None = None,
    ) -> dict[str, Any]:
        """Add the customer (first licence) or add a licence to the existing one (renewal).

        `retire_code` marks another computer as retired in the same save: a customer
        who moves to a new computer gets the new licence and loses the old one's
        standing in one step, never one without the other.
        """
        with self._lock:
            data = self._load()
            if retire_code is not None:
                old = next((c for c in data["customers"] if c["code"] == retire_code), None)
                if old is None:
                    raise LedgerError("The computer being replaced isn't in the ledger.")
                old["retired"] = True
                old["retired_on"] = issued
            customer = next((c for c in data["customers"] if c["code"] == code), None)
            if customer is None:
                customer = {
                    "id": secrets.token_hex(6),
                    "name": name,
                    "code": code,
                    "phone": phone or "",
                    "notes": notes or "",
                    "monthly_fee": monthly_fee,
                    "created": issued,
                    "licences": [],
                    "payments": {},
                }
                data["customers"].append(customer)
            else:
                # Renewal: only fill in details typed this time; never blank existing ones.
                if phone:
                    customer["phone"] = phone
                if notes:
                    customer["notes"] = notes
                if monthly_fee is not None:
                    customer["monthly_fee"] = monthly_fee
            customer["licences"].append(
                {"issued": issued, "expires": expires, "days": days, "token": token}
            )
            if paid_month:
                customer["payments"][paid_month] = {"paid": True, "on": issued}
            self._save(data)
            return customer

    def _find(self, data: dict[str, Any], customer_id: str) -> dict[str, Any]:
        for customer in data["customers"]:
            if customer["id"] == customer_id:
                found: dict[str, Any] = customer
                return found
        raise LedgerError("That customer isn't in the ledger.")

    def set_payment(self, customer_id: str, month: str, paid: bool, today: date) -> dict[str, Any]:
        if not valid_month(month):
            raise LedgerError("The month must look like 2026-10.")
        with self._lock:
            data = self._load()
            customer = self._find(data, customer_id)
            if paid:
                customer["payments"][month] = {"paid": True, "on": today.isoformat()}
            else:
                customer["payments"].pop(month, None)
            self._save(data)
            return customer

    def set_retired(self, customer_id: str, retired: bool, today: date) -> dict[str, Any]:
        """Retire a computer (it is never renewed again) or restore it."""
        with self._lock:
            data = self._load()
            customer = self._find(data, customer_id)
            if retired:
                # Already retired: keep the original date, which months-owed are counted from.
                if not customer.get("retired"):
                    customer["retired"] = True
                    customer["retired_on"] = today.isoformat()
            else:
                customer.pop("retired", None)
                customer.pop("retired_on", None)
            self._save(data)
            return customer

    def update_details(
        self,
        customer_id: str,
        *,
        phone: str | None,
        notes: str | None,
        monthly_fee: float | None,
        clear_fee: bool,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            customer = self._find(data, customer_id)
            if phone is not None:
                customer["phone"] = phone
            if notes is not None:
                customer["notes"] = notes
            if clear_fee:
                customer["monthly_fee"] = None
            elif monthly_fee is not None:
                customer["monthly_fee"] = monthly_fee
            self._save(data)
            return customer

    def delete(self, customer_id: str) -> None:
        with self._lock:
            data = self._load()
            customer = self._find(data, customer_id)
            data["customers"].remove(customer)
            self._save(data)


_ledger: LicenceLedger | None = None
_ledger_lock = threading.Lock()


def get_ledger() -> LicenceLedger:
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = LicenceLedger(licence_gate._data_dir() / LEDGER_FILENAME)  # noqa: SLF001
        return _ledger


def use_ledger(ledger: LicenceLedger | None) -> None:
    """Swap the ledger (tests use a temp folder); None restores the real one."""
    global _ledger
    with _ledger_lock:
        _ledger = ledger
