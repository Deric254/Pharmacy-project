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


# ---------------------------------------------------------------- new computers
CODE2 = "WXYZ-2345-6789"
CODE3 = "QRST-JKLM-NPQR"


async def _issue(client, headers, key, **over):
    return await client.post(f"{BASE}/issue", json=_body(key, **over), headers=headers)


async def _list(client, headers):
    return (await client.get(BASE, headers=headers)).json()


async def test_new_code_for_a_known_name_must_say_migration_or_extra(
    client, admin_headers, ledger, owner_key
):
    await _issue(client, admin_headers, owner_key)
    r = await _issue(client, admin_headers, owner_key, code=CODE2, user="jane  DOE")
    assert r.status_code == 409
    assert "already has 1 active computer" in r.json()["detail"]
    # Nothing was recorded and nothing was retired.
    out = await _list(client, admin_headers)
    assert out["summary"]["total_customers"] == 1 and out["summary"]["retired"] == 0


async def test_migration_retires_the_old_computer_in_one_step(
    client, admin_headers, ledger, owner_key
):
    await _issue(client, admin_headers, owner_key, paid_this_month=True)
    r = await _issue(client, admin_headers, owner_key, code=CODE2, relation="migration")
    assert r.status_code == 200, r.text
    assert r.json()["retired_code"] == "ABCDEFGHJKLM"
    assert r.json()["renewal"] is False
    out = await _list(client, admin_headers)
    assert out["summary"]["total_customers"] == 1  # one live computer, not two
    assert out["summary"]["retired"] == 1
    by_code = {c["code"]: c for c in out["customers"]}
    assert by_code["ABCDEFGHJKLM"]["status"] == "retired"
    assert by_code["ABCDEFGHJKLM"]["retired_on"] == date.today().isoformat()
    assert by_code["WXYZ23456789"]["status"] == "active"
    assert by_code["WXYZ23456789"]["active_computers"] == 1
    # Active computer sorts above its retired predecessor.
    assert [c["code"] for c in out["customers"]] == ["WXYZ23456789", "ABCDEFGHJKLM"]


async def test_a_retired_computer_is_never_renewed_until_restored(
    client, admin_headers, ledger, owner_key
):
    await _issue(client, admin_headers, owner_key)
    await _issue(client, admin_headers, owner_key, code=CODE2, relation="migration")
    r = await _issue(client, admin_headers, owner_key)  # old code again
    assert r.status_code == 400 and "retired" in r.json()["detail"]
    old = next(c for c in (await _list(client, admin_headers))["customers"] if c["retired"])
    assert old["licence_count"] == 1  # nothing was signed or recorded
    restored = await client.put(
        f"{BASE}/{old['id']}/retired", json={"retired": False}, headers=admin_headers
    )
    assert restored.status_code == 200 and restored.json()["status"] != "retired"
    assert (await _issue(client, admin_headers, owner_key)).status_code == 200


async def test_extra_computer_keeps_both_and_counts_two(client, admin_headers, ledger, owner_key):
    await _issue(client, admin_headers, owner_key)
    r = await _issue(client, admin_headers, owner_key, code=CODE2, relation="extra")
    assert r.status_code == 200 and r.json()["retired_code"] is None
    out = await _list(client, admin_headers)
    assert out["summary"]["total_customers"] == 2 and out["summary"]["retired"] == 0
    assert {c["active_computers"] for c in out["customers"]} == {2}


async def test_migration_with_several_computers_must_name_the_one_replaced(
    client, admin_headers, ledger, owner_key
):
    await _issue(client, admin_headers, owner_key)
    await _issue(client, admin_headers, owner_key, code=CODE2, relation="extra")
    vague = await _issue(client, admin_headers, owner_key, code=CODE3, relation="migration")
    assert vague.status_code == 400
    stranger = await _issue(
        client,
        admin_headers,
        owner_key,
        code=CODE3,
        relation="migration",
        replaces="NOPE-NOPE-NOPE",
    )
    assert stranger.status_code == 400
    assert (await _list(client, admin_headers))["summary"]["total_customers"] == 2
    ok = await _issue(
        client,
        admin_headers,
        owner_key,
        code=CODE3,
        relation="migration",
        replaces="wxyz-2345-6789",
    )
    assert ok.status_code == 200 and ok.json()["retired_code"] == "WXYZ23456789"
    out = await _list(client, admin_headers)
    assert out["summary"]["total_customers"] == 2 and out["summary"]["retired"] == 1


async def test_a_different_name_needs_no_choice(client, admin_headers, ledger, owner_key):
    await _issue(client, admin_headers, owner_key)
    r = await _issue(client, admin_headers, owner_key, code=CODE2, user="John Smith")
    assert r.status_code == 200 and r.json()["retired_code"] is None


async def test_retired_computer_money_and_manual_retire(client, admin_headers, ledger, owner_key):
    """The month of the move belongs to the new computer, so it is never counted twice."""
    out = await _issue(client, admin_headers, owner_key, monthly_fee=500)
    cid = out.json()["customer"]["id"]
    # Retired the same month it was created: nothing is owed on it.
    r = await client.put(f"{BASE}/{cid}/retired", json={"retired": True}, headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "retired" and r.json()["unpaid_months"] == []
    assert r.json()["amount_outstanding"] == 0
    summary = (await _list(client, admin_headers))["summary"]
    assert summary["total_customers"] == 0 and summary["retired"] == 1
    assert summary["unpaid_months"] == 0
    # A name whose only computers are retired can get a fresh code without a choice.
    fresh = await _issue(client, admin_headers, owner_key, code=CODE2)
    assert fresh.status_code == 200
    missing = await client.put(
        f"{BASE}/nope/retired", json={"retired": True}, headers=admin_headers
    )
    assert missing.status_code == 400


async def test_csv_has_the_new_columns_and_months_stay_last(
    client, admin_headers, ledger, owner_key
):
    await _issue(client, admin_headers, owner_key)
    await _issue(client, admin_headers, owner_key, code=CODE2, relation="migration")
    r = await client.get(f"{BASE}/export.csv", headers=admin_headers)
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    header = rows[0]
    assert "Retired on" in header and "Active computers" in header
    assert header.index("Notes") < header.index(f"{date.today().year:04d}-{date.today().month:02d}")
    status_col = header.index("Status")
    assert sorted(row[status_col] for row in rows[1:]) == ["active", "retired"]


def test_previous_month_wraps_the_year():
    from app.core.licence_ledger import previous_month

    assert previous_month(date(2026, 1, 15)) == "2025-12"
    assert previous_month(date(2026, 10, 31)) == "2026-09"
