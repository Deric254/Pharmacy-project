"""
Owner licence desk: only the owner's computer (and only with the owner key) can
issue licences; the ledger tracks customers and the months they've paid.
Throwaway keys only -- never the real owner key.
"""

import csv
import io
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from licence_helpers import make_licensing

from app.core import licence_gate
from app.core.licence_gate import use_licensing
from app.core.licence_ledger import LicenceLedger, use_ledger
from licensing.licensing import Licensing

CODE = "ABCD-EFGH-JKLM"
BASE = "/api/v1/owner/licences"


@pytest.fixture
def ledger(tmp_path: Path):
    lg = LicenceLedger(tmp_path / "licence-ledger.json")
    use_ledger(lg)
    yield lg
    use_ledger(None)


@pytest.fixture
def owner_key() -> str:
    """The autouse fixture made an owner copy; re-make it here so the test knows the key text."""
    lic, key_text, _ = make_licensing()
    lic.claim_owner(key_text)
    use_licensing(lic)
    return key_text


async def _login(client, username: str, password: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def admin_headers(client, owner_user):
    """Logged in as the top owner role (roles.manage)."""
    return await _login(client, "lucy", "S3curePass!")


def _body(key: str, **over):
    body = {"owner_key": key, "code": CODE, "user": "Jane Doe", "days": 30}
    body.update(over)
    return body


async def test_issue_creates_customer_and_a_licence_the_customer_can_activate(
    client, admin_headers, ledger, owner_key
):
    r = await client.post(
        f"{BASE}/issue", json=_body(owner_key, paid_this_month=True), headers=admin_headers
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["renewal"] is False
    assert out["customer"]["name"] == "Jane Doe"
    assert out["customer"]["unpaid_months"] == []

    # The licence is genuinely valid for that code (verified with the same public key).
    pub = licence_gate.get_licensing().public.hex()  # type: ignore[union-attr]
    customer_copy = Licensing(licence_gate.APP_ID, licence_gate.PREFIX, pub, store=None)
    data = customer_copy.read(out["licence"], for_install="ABCDEFGHJKLM")
    assert data["user"] == "Jane Doe"


async def test_wrong_owner_key_is_refused_and_nothing_is_recorded(
    client, admin_headers, ledger, owner_key
):
    other_key, _ = Licensing.make_owner_key("PHE")
    r = await client.post(f"{BASE}/issue", json=_body(other_key), headers=admin_headers)
    assert r.status_code == 400
    assert (await client.get(BASE, headers=admin_headers)).json()["summary"]["total_customers"] == 0


async def test_same_code_is_a_renewal_and_name_must_match(client, admin_headers, ledger, owner_key):
    await client.post(f"{BASE}/issue", json=_body(owner_key), headers=admin_headers)
    r = await client.post(
        f"{BASE}/issue", json=_body(owner_key, user="jane doe"), headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["renewal"] is True
    assert r.json()["customer"]["licence_count"] == 2
    assert r.json()["customer"]["name"] == "Jane Doe"  # keeps the original spelling

    bad = await client.post(
        f"{BASE}/issue", json=_body(owner_key, user="Someone Else"), headers=admin_headers
    )
    assert bad.status_code == 400
    assert "Jane Doe" in bad.json()["detail"]
    assert (await client.get(BASE, headers=admin_headers)).json()["summary"]["total_customers"] == 1


async def test_bad_code_and_days_are_rejected(client, admin_headers, ledger, owner_key):
    assert (
        await client.post(f"{BASE}/issue", json=_body(owner_key, code="ABC"), headers=admin_headers)
    ).status_code == 400
    assert (
        await client.post(f"{BASE}/issue", json=_body(owner_key, days=0), headers=admin_headers)
    ).status_code == 422
    assert (
        await client.post(f"{BASE}/issue", json=_body(owner_key, days=366), headers=admin_headers)
    ).status_code == 422


async def test_payments_tick_and_untick(client, admin_headers, ledger, owner_key):
    out = (
        await client.post(
            f"{BASE}/issue", json=_body(owner_key, monthly_fee=500), headers=admin_headers
        )
    ).json()
    cid = out["customer"]["id"]
    this_month = f"{date.today().year:04d}-{date.today().month:02d}"
    assert out["customer"]["unpaid_months"] == [this_month]
    assert out["customer"]["amount_outstanding"] == 500

    r = await client.put(
        f"{BASE}/{cid}/payments", json={"month": this_month, "paid": True}, headers=admin_headers
    )
    assert r.status_code == 200 and r.json()["unpaid_months"] == []
    r = await client.put(
        f"{BASE}/{cid}/payments", json={"month": this_month, "paid": False}, headers=admin_headers
    )
    assert r.json()["unpaid_months"] == [this_month]
    bad = await client.put(
        f"{BASE}/{cid}/payments", json={"month": "2026-13", "paid": True}, headers=admin_headers
    )
    assert bad.status_code == 422
    missing = await client.put(
        f"{BASE}/nope/payments", json={"month": this_month, "paid": True}, headers=admin_headers
    )
    assert missing.status_code == 400


async def test_update_details_and_delete(client, admin_headers, ledger, owner_key):
    cid = (
        await client.post(
            f"{BASE}/issue", json=_body(owner_key, monthly_fee=500), headers=admin_headers
        )
    ).json()["customer"]["id"]
    r = await client.patch(
        f"{BASE}/{cid}", json={"phone": "0700", "clear_fee": True}, headers=admin_headers
    )
    assert r.json()["phone"] == "0700" and r.json()["monthly_fee"] is None
    assert (await client.delete(f"{BASE}/{cid}", headers=admin_headers)).status_code == 204
    assert (await client.get(BASE, headers=admin_headers)).json()["customers"] == []


async def test_export_csv_is_formula_safe(client, admin_headers, ledger, owner_key):
    await client.post(
        f"{BASE}/issue", json=_body(owner_key, user="=HYPERLINK(1)"), headers=admin_headers
    )
    r = await client.get(f"{BASE}/export.csv", headers=admin_headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][0] == "Name"
    assert rows[1][0] == "'=HYPERLINK(1)"
    assert rows[1][-1] == "UNPAID"


async def test_customer_copy_cannot_use_any_of_it(client, admin_headers, ledger):
    """A licensed (non-owner) copy gets 403 on every route, even with a valid admin login."""
    lic, key_text, seed = make_licensing()
    lic.activate(lic.issue(seed, lic.install_id(), "Customer", 30)["licence"])
    use_licensing(lic)
    assert (await client.get(BASE, headers=admin_headers)).status_code == 403
    assert (await client.get(f"{BASE}/export.csv", headers=admin_headers)).status_code == 403
    r = await client.post(f"{BASE}/issue", json=_body(key_text), headers=admin_headers)
    assert r.status_code == 403


async def test_requires_login_and_the_top_role(client, administrator_user, ledger, owner_key):
    assert (await client.get(BASE)).status_code == 401
    headers = await _login(client, "sam", "AdminPass1")  # ordinary Administrator: no roles.manage
    assert (await client.get(BASE, headers=headers)).status_code == 403
    r = await client.post(f"{BASE}/issue", json=_body(owner_key), headers=headers)
    assert r.status_code == 403


def test_corrupt_ledger_is_not_overwritten(tmp_path: Path):
    from app.core.licence_ledger import LedgerError

    p = tmp_path / "licence-ledger.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(LedgerError):
        LicenceLedger(p).customers()
    assert p.read_text(encoding="utf-8") == "{not json"
