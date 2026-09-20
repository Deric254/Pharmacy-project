"""
Exact-cent money helpers, and the claim money_types.py makes that SQL
aggregates over a MoneyCents column stay exact.
"""

import pytest
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.money_types import from_cents, prorate_cents, to_cents
from app.models.sale import Sale


class TestCentConversion:
    @pytest.mark.parametrize(
        ("amount", "cents"),
        [
            (0.0, 0),
            (0.1, 10),
            (0.30000000000000004, 30),  # 0.1 * 3 in binary floating point
            (12.35, 1235),
            (19.99, 1999),
            (0.005, 1),  # half rounds up, never to even
            (0.004, 0),
        ],
    )
    def test_to_cents_rounds_half_up_from_what_a_human_would_read(self, amount, cents):
        assert to_cents(amount) == cents

    @pytest.mark.parametrize("cents", [0, 1, 30, 1235, 99_999_999_999])
    def test_a_whole_number_of_cents_round_trips(self, cents):
        assert to_cents(from_cents(cents)) == cents


class TestProrateCents:
    def test_rounds_half_up(self):
        assert prorate_cents(1000, 2999, 3000) == 1000  # 999.67
        assert prorate_cents(1, 1, 2) == 1  # 0.5

    def test_a_ratio_of_one_changes_nothing(self):
        assert prorate_cents(1234, 500, 500) == 1234

    def test_an_exact_share_is_exact(self):
        assert prorate_cents(3000, 250, 300) == 2500


class TestAggregatesStayExact:
    async def test_sql_sum_over_a_money_column_is_exact(self, owner_user):
        async with AsyncSessionLocal() as db:
            for _ in range(3):
                db.add(
                    Sale(
                        cashier_user_id=owner_user.id,
                        subtotal=0.1,
                        discount_amount=0.0,
                        total_amount=0.1,
                    )
                )
            await db.commit()

            total = (await db.execute(select(func.sum(Sale.total_amount)))).scalar_one()

        assert total == 0.3  # 0.1 + 0.1 + 0.1 is 0.30000000000000004 as floats
