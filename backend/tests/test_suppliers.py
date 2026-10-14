"""
Supplier tests. Covers the pre-existing create/get endpoints (no test
file existed for these before) and the new time-sliced KPI endpoint,
whose properties that matter are:
  1. "Purchased" and "paid" are flows scoped strictly to the selected
     date range -- a transaction outside the range must not leak in.
  2. "net_due" is a balance as of the end of that range, not a flow --
     it includes everything up to end_date regardless of when it
     started, and is exactly what SupplierService._to_schema's
     balance_owed already computes when end_date is today.
  3. Uses the same signed SupplierTransaction ledger as balance_owed,
     so it can never disagree with the number shown on the supplier's
     own record.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.business_time import business_today
from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.supplier import SupplierTransaction


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _owner_headers(client) -> dict[str, str]:
    token = await _login(client, "lucy", "S3curePass!")
    return {"Authorization": f"Bearer {token}"}


async def _business_today() -> date:
    async with AsyncSessionLocal() as db:
        return await business_today(db)


async def _make_supplier(client, headers, name: str) -> int:
    r = await client.post("/api/v1/suppliers", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _make_transaction(supplier_id: int, amount: float, days_ago: int = 0) -> None:
    """A transaction dated in the past, bypassing the API (which always
    timestamps 'now') so range-boundary behaviour is actually testable."""
    async with AsyncSessionLocal() as db:
        db.add(
            SupplierTransaction(
                supplier_id=supplier_id,
                amount=amount,
                created_at=datetime.now(UTC) - timedelta(days=days_ago),
                reference="test",
            )
        )
        await db.commit()


class TestSupplierCreateAndGet:
    async def test_requires_purchasing_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/suppliers",
            json={"name": "Should fail"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_create_and_fetch(self, client, owner_user):
        headers = await _owner_headers(client)
        r = await client.post(
            "/api/v1/suppliers",
            json={"name": "MedSupply Ltd", "contact_phone": "0700000000"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        supplier_id = r.json()["id"]
        assert r.json()["balance_owed"] == 0.0

        r2 = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "MedSupply Ltd"


class TestSupplierKpis:
    async def test_requires_purchasing_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_zero_activity_gives_zeroed_kpis(self, client, owner_user):
        headers = await _owner_headers(client)
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json() == {
            "start_date": today,
            "end_date": today,
            "total_purchased": 0.0,
            "total_paid": 0.0,
            "net_due": 0.0,
            "active_supplier_count": 0,
        }

    async def test_purchased_and_paid_within_the_period(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "KPI Supplier")
        await _make_transaction(supplier_id, 500.0)  # charge: goods received
        await _make_transaction(supplier_id, -200.0)  # payment

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["total_purchased"] == 500.0
        assert body["total_paid"] == 200.0
        assert body["net_due"] == 300.0
        assert body["active_supplier_count"] == 1

    async def test_transactions_outside_the_range_are_excluded_from_the_flows(
        self, client, owner_user
    ):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Old Activity Supplier")
        await _make_transaction(supplier_id, 500.0, days_ago=30)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["total_purchased"] == 0.0
        assert body["active_supplier_count"] == 0
        # But the balance from that old charge is still owed today.
        assert body["net_due"] == 500.0

    async def test_net_due_is_cumulative_across_multiple_suppliers(self, client, owner_user):
        headers = await _owner_headers(client)
        a = await _make_supplier(client, headers, "Supplier A")
        b = await _make_supplier(client, headers, "Supplier B")
        await _make_transaction(a, 300.0)
        await _make_transaction(b, 700.0)
        await _make_transaction(b, -100.0)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["total_purchased"] == 1000.0
        assert body["total_paid"] == 100.0
        assert body["net_due"] == 900.0
        assert body["active_supplier_count"] == 2

    async def test_a_real_purchase_creates_a_matching_charge(self, client, owner_user):
        """End-to-end: quick_purchase's own SupplierTransaction write
        (not a hand-inserted test row) is what the KPI reads."""
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Real PO Supplier")
        product = await client.post(
            "/api/v1/products", json={"name": "KPI Test Product"}, headers=headers
        )
        product_id = product.json()["id"]

        await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 10,
                        "batch_number": "KPI-001",
                        "expiry_date": "2097-06-30",
                        "unit_cost": 5.0,
                        "selling_price": 9.0,
                    }
                ],
            },
            headers=headers,
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["total_purchased"] == 50.0
        assert body["net_due"] == 50.0


class TestSupplierPayments:
    """A payment may settle a debt, never exceed it: the ledger is
    append-only, so an overpayment could not be undone afterwards and
    would sit as a permanent negative balance."""

    async def test_payment_up_to_the_balance_is_accepted(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Settle Supplier")
        await _make_transaction(supplier_id, 500.0)

        partial = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments", json={"amount": 200.0}, headers=headers
        )
        assert partial.status_code == 200, partial.text
        assert partial.json()["balance_owed"] == 300.0

        exact = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments", json={"amount": 300.0}, headers=headers
        )
        assert exact.status_code == 200, exact.text
        assert exact.json()["balance_owed"] == 0.0

    async def test_overpayment_is_rejected_and_ledger_untouched(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Overpay Supplier")
        await _make_transaction(supplier_id, 500.0)

        r = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments", json={"amount": 500.01}, headers=headers
        )
        assert r.status_code == 409, r.text

        check = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert check.json()["balance_owed"] == 500.0

    async def test_payment_to_a_supplier_owed_nothing_is_rejected(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Zero Balance Supplier")

        r = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments", json={"amount": 1.0}, headers=headers
        )
        assert r.status_code == 409, r.text


async def _admin_headers(client) -> dict[str, str]:
    token = await _login(client, "sam", "AdminPass1")
    return {"Authorization": f"Bearer {token}"}


async def _pay(client, headers, supplier_id: int, amount, key: str | None = None, notes=None):
    body: dict[str, object] = {"amount": amount}
    if key is not None:
        body["idempotency_key"] = key
    if notes is not None:
        body["notes"] = notes
    return await client.post(
        f"/api/v1/suppliers/{supplier_id}/payments", json=body, headers=headers
    )


async def _ledger(supplier_id: int) -> list[SupplierTransaction]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(SupplierTransaction)
            .where(SupplierTransaction.supplier_id == supplier_id)
            .order_by(SupplierTransaction.id)
        )
        return list(rows.scalars().all())


async def _audit_actions() -> list[str]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(AuditLog.action).order_by(AuditLog.id))
        return [r for (r,) in rows.all()]


class TestPaymentAmountValidation:
    async def test_more_than_two_decimals_is_rejected_not_rounded(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Decimal Supplier")
        await _make_transaction(supplier_id, 500.0)

        r = await _pay(client, headers, supplier_id, 100.005)
        assert r.status_code == 422, r.text
        assert len(await _ledger(supplier_id)) == 1  # only the seeded charge

    async def test_two_decimals_is_accepted_exactly(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Cents Supplier")
        await _make_transaction(supplier_id, 500.0)

        r = await _pay(client, headers, supplier_id, 100.10)
        assert r.status_code == 200, r.text
        assert r.json()["balance_owed"] == 399.9

    async def test_junk_amounts_are_rejected(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Junk Supplier")
        await _make_transaction(supplier_id, 500.0)

        for bad in (0, -5, 1_000_000_000.0):
            r = await _pay(client, headers, supplier_id, bad)
            assert r.status_code == 422, (bad, r.text)
        raw = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments",
            content='{"amount": Infinity}',
            headers={**headers, "Content-Type": "application/json"},
        )
        assert raw.status_code == 422, raw.text
        assert len(await _ledger(supplier_id)) == 1

    async def test_blank_notes_and_bad_keys_are_handled(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Key Shape Supplier")
        await _make_transaction(supplier_id, 500.0)

        short = await _pay(client, headers, supplier_id, 10.0, key="abc")
        assert short.status_code == 422
        weird = await _pay(client, headers, supplier_id, 10.0, key="has spaces in it!!")
        assert weird.status_code == 422


class TestPaymentIdempotency:
    async def test_same_key_twice_records_one_payment(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Double Click Supplier")
        await _make_transaction(supplier_id, 1000.0)

        first = await _pay(client, headers, supplier_id, 100.0, key="pay-attempt-0001")
        second = await _pay(client, headers, supplier_id, 100.0, key="pay-attempt-0001")
        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["balance_owed"] == 900.0  # paid once, not twice

        payments = [t for t in await _ledger(supplier_id) if t.amount < 0]
        assert len(payments) == 1
        actions = await _audit_actions()
        assert actions.count("supplier.payment_recorded") == 1

    async def test_same_key_with_a_different_amount_is_refused(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Key Reuse Supplier")
        await _make_transaction(supplier_id, 1000.0)

        await _pay(client, headers, supplier_id, 100.0, key="pay-attempt-0002")
        reused = await _pay(client, headers, supplier_id, 250.0, key="pay-attempt-0002")
        assert reused.status_code == 409, reused.text
        assert len([t for t in await _ledger(supplier_id) if t.amount < 0]) == 1

    async def test_different_keys_are_separate_payments(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Two Payments Supplier")
        await _make_transaction(supplier_id, 1000.0)

        await _pay(client, headers, supplier_id, 100.0, key="pay-attempt-0003")
        r = await _pay(client, headers, supplier_id, 100.0, key="pay-attempt-0004")
        assert r.json()["balance_owed"] == 800.0

    async def test_simultaneous_requests_with_one_key_pay_once(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Racing Key Supplier")
        await _make_transaction(supplier_id, 1000.0)

        results = await asyncio.gather(
            *[_pay(client, headers, supplier_id, 100.0, key="pay-attempt-race") for _ in range(6)]
        )
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        assert len([t for t in await _ledger(supplier_id) if t.amount < 0]) == 1


class TestPaymentConcurrencyNeverOverpays:
    async def test_simultaneous_payments_cannot_exceed_the_balance(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Race Supplier")
        await _make_transaction(supplier_id, 100.0)

        # Eight distinct 30.00 payments racing against 100.00 owed:
        # exactly three can fit (90.00), the rest must be refused.
        results = await asyncio.gather(
            *[
                _pay(client, headers, supplier_id, 30.0, key=f"race-payment-{i:04d}")
                for i in range(8)
            ]
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses == [200, 200, 200, 409, 409, 409, 409, 409], statuses

        check = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert check.json()["balance_owed"] == 10.0


class TestPaymentReversal:
    async def _owed_and_paid(self, client, headers, name: str, owed=1000.0, paid=400.0):
        supplier_id = await _make_supplier(client, headers, name)
        await _make_transaction(supplier_id, owed)
        r = await _pay(client, headers, supplier_id, paid, key=f"seed-{name[:20].replace(' ', '')}")
        assert r.status_code == 200, r.text
        listing = await client.get(f"/api/v1/suppliers/{supplier_id}/payments", headers=headers)
        return supplier_id, listing.json()[0]["id"]

    async def test_reversal_restores_the_balance_and_keeps_history(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id, payment_id = await self._owed_and_paid(client, headers, "Reverse Supplier")

        r = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments/{payment_id}/reverse",
            json={"reason": "Typed 400 instead of 40"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["balance_owed"] == 1000.0

        ledger = await _ledger(supplier_id)
        assert [t.amount for t in ledger] == [1000.0, -400.0, 400.0]  # nothing edited or deleted
        assert ledger[2].reverses_transaction_id == payment_id
        assert ledger[2].notes == "Typed 400 instead of 40"

        history = (
            await client.get(f"/api/v1/suppliers/{supplier_id}/payments", headers=headers)
        ).json()
        assert history[0]["reversed"] is True
        assert history[0]["reversal_reason"] == "Typed 400 instead of 40"
        assert "supplier.payment_reversed" in await _audit_actions()

    async def test_a_payment_can_only_be_reversed_once(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id, payment_id = await self._owed_and_paid(client, headers, "Once Supplier")
        url = f"/api/v1/suppliers/{supplier_id}/payments/{payment_id}/reverse"

        assert (
            await client.post(url, json={"reason": "wrong amount"}, headers=headers)
        ).status_code == 200
        again = await client.post(url, json={"reason": "wrong amount"}, headers=headers)
        assert again.status_code == 409, again.text
        assert len(await _ledger(supplier_id)) == 3

    async def test_simultaneous_reversals_apply_once(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id, payment_id = await self._owed_and_paid(client, headers, "Race Reverse")
        url = f"/api/v1/suppliers/{supplier_id}/payments/{payment_id}/reverse"

        results = await asyncio.gather(
            *[client.post(url, json={"reason": "wrong amount"}, headers=headers) for _ in range(5)]
        )
        assert sorted(r.status_code for r in results) == [200, 409, 409, 409, 409]
        check = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert check.json()["balance_owed"] == 1000.0

    async def test_reason_is_required(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id, payment_id = await self._owed_and_paid(client, headers, "Reason Supplier")
        url = f"/api/v1/suppliers/{supplier_id}/payments/{payment_id}/reverse"

        for body in ({}, {"reason": ""}, {"reason": "   "}, {"reason": "ab"}):
            r = await client.post(url, json=body, headers=headers)
            assert r.status_code == 422, (body, r.text)
        assert len(await _ledger(supplier_id)) == 2

    async def test_only_the_owner_may_reverse(self, client, owner_user, administrator_user):
        owner = await _owner_headers(client)
        supplier_id, payment_id = await self._owed_and_paid(client, owner, "Permission Supplier")
        url = f"/api/v1/suppliers/{supplier_id}/payments/{payment_id}/reverse"

        # An Administrator may record payments but not undo them.
        admin = await _admin_headers(client)
        denied = await client.post(url, json={"reason": "wrong amount"}, headers=admin)
        assert denied.status_code == 403, denied.text
        assert len(await _ledger(supplier_id)) == 2

        anonymous = await client.post(url, json={"reason": "wrong amount"})
        assert anonymous.status_code == 401

    async def test_cannot_reverse_a_charge_or_another_suppliers_payment(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id, payment_id = await self._owed_and_paid(client, headers, "Scope Supplier A")
        other_id = await _make_supplier(client, headers, "Scope Supplier B")
        charge_id = (await _ledger(supplier_id))[0].id

        wrong_supplier = await client.post(
            f"/api/v1/suppliers/{other_id}/payments/{payment_id}/reverse",
            json={"reason": "wrong amount"},
            headers=headers,
        )
        assert wrong_supplier.status_code == 404
        a_charge = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments/{charge_id}/reverse",
            json={"reason": "wrong amount"},
            headers=headers,
        )
        assert a_charge.status_code == 404
        missing = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments/999999/reverse",
            json={"reason": "wrong amount"},
            headers=headers,
        )
        assert missing.status_code == 404

    async def test_reversal_repairs_an_existing_overpayment(self, client, owner_user):
        """The pre-fix situation: a payment already on the books that
        exceeds what was owed (negative balance). Reversing it is the
        audited way back to a correct balance."""
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Legacy Overpaid")
        await _make_transaction(supplier_id, 468_122.0)
        await _make_transaction(supplier_id, -2_574_122.0)

        before = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert before.json()["balance_owed"] == -2_106_000.0
        payment_id = (
            await client.get(f"/api/v1/suppliers/{supplier_id}/payments", headers=headers)
        ).json()[0]["id"]

        r = await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments/{payment_id}/reverse",
            json={"reason": "Typo: extra digits entered"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["balance_owed"] == 468_122.0

    async def test_kpis_do_not_count_a_reversal_as_a_purchase(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id, payment_id = await self._owed_and_paid(client, headers, "KPI Reverse")
        today = (await _business_today()).isoformat()
        params = {"start_date": today, "end_date": today}

        before = (await client.get("/api/v1/suppliers/kpis", params=params, headers=headers)).json()
        assert (before["total_purchased"], before["total_paid"], before["net_due"]) == (
            1000.0,
            400.0,
            600.0,
        )

        await client.post(
            f"/api/v1/suppliers/{supplier_id}/payments/{payment_id}/reverse",
            json={"reason": "wrong amount"},
            headers=headers,
        )
        after = (await client.get("/api/v1/suppliers/kpis", params=params, headers=headers)).json()
        assert (after["total_purchased"], after["total_paid"], after["net_due"]) == (
            1000.0,  # still only the real purchase
            0.0,  # the payment no longer counts as paid
            1000.0,
        )


class TestPaymentHistoryAccess:
    async def test_history_requires_payment_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.get(
            "/api/v1/suppliers/1/payments", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403
