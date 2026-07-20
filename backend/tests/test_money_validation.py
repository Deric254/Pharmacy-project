"""
Money validation tests. A real bug was found by actually sending
Infinity as a price: Pydantic's `ge=0` alone does not reject positive
infinity (inf >= 0 is mathematically true), Python's json module
accepts the non-standard Infinity/NaN literals, and the row was
committed to the database before the response even finished
serializing -- a 500 error that was hiding a successful garbage write,
not preventing one. These tests prove every money-accepting endpoint
rejects it before anything is written, not just that it responds
somehow.
"""

import math

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas._money import Money, PositiveMoney


class _MoneyModel(BaseModel):
    amount: Money


class _PositiveMoneyModel(BaseModel):
    amount: PositiveMoney


class TestMoneyTypeRejectsNonFiniteValues:
    @pytest.mark.parametrize("bad_value", [math.inf, -math.inf, math.nan])
    def test_money_rejects_non_finite(self, bad_value):
        with pytest.raises(ValidationError):
            _MoneyModel(amount=bad_value)

    @pytest.mark.parametrize("bad_value", [math.inf, -math.inf, math.nan])
    def test_positive_money_rejects_non_finite(self, bad_value):
        with pytest.raises(ValidationError):
            _PositiveMoneyModel(amount=bad_value)

    def test_money_rejects_absurdly_large_values(self):
        with pytest.raises(ValidationError):
            _MoneyModel(amount=1e27)

    def test_money_rejects_negative(self):
        with pytest.raises(ValidationError):
            _MoneyModel(amount=-1.0)

    def test_positive_money_rejects_zero(self):
        with pytest.raises(ValidationError):
            _PositiveMoneyModel(amount=0.0)

    def test_money_accepts_zero(self):
        assert _MoneyModel(amount=0.0).amount == 0.0

    def test_money_accepts_a_normal_price(self):
        assert _MoneyModel(amount=12.50).amount == 12.50

    def test_money_accepts_the_maximum_allowed_value(self):
        assert _MoneyModel(amount=999_999_999.99).amount == 999_999_999.99


class TestProductCreationRejectsGarbageMoney:
    """
    The exact live scenario that was reproduced: POST /products with a
    non-standard-JSON Infinity literal as the price. Before the fix,
    this returned a 500 -- AFTER the row was already committed. Now it
    must be rejected before any database write happens at all.
    """

    async def test_infinity_price_is_rejected_not_committed(self, client, owner_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        # httpx's json= parameter serializes via the standard library,
        # which (matching what a raw client actually sent when this
        # bug was found) emits the non-standard `Infinity` token that
        # Python's json module accepts on both ends by default.
        r = await client.post(
            "/api/v1/products",
            content='{"name":"Bad","unit":"box","reorder_point":10,"default_selling_price":Infinity}',
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        assert r.status_code == 422

        # And confirm nothing was actually written -- the whole point
        # is that a 500 here previously meant "already too late."
        list_r = await client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
        assert list_r.json() == []

    async def test_absurdly_large_price_is_rejected(self, client, owner_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.post(
            "/api/v1/products",
            json={
                "name": "Bad",
                "unit": "box",
                "reorder_point": 10,
                "default_selling_price": 999999999999999999999999999.99,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422
