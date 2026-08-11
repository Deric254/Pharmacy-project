"""
MoneyCents: the fix for money being stored as float/SQLite REAL.

The problem this closes: floats cannot exactly represent most decimal
fractions (0.1 + 0.2 != 0.3 in binary floating point). That's not a
theoretical concern for a system that runs the same weighted-average
cost recalculation, the same loyalty accrual, and the same running
supplier balance thousands of times over years -- small per-operation
errors compound. This type makes that entire class of error
structurally impossible for money, the same way `Integer` already
makes it impossible for drug quantities (see the models -- every
qty/quantity column was already an exact Integer; this brings money up
to the same standard).

Why integer cents, specifically, and not Decimal/NUMERIC: SQLite has
no native fixed-point decimal storage class. A Decimal stored as TEXT
would silently break every SQL-level SUM()/AVG() this app's reports,
KPI dashboard, and supplier-balance queries depend on -- SQLite cannot
do arithmetic on TEXT. Integer cents keeps every existing aggregate
query exact, because SQLite's INTEGER arithmetic is exact by
construction. No floating-point drift is possible after any number of
transactions, which is the entire point.

Why this is safe to drop into existing models with zero code changes
above the model layer: TypeDecorator's conversion happens at exactly
one choke point -- the moment a Python value crosses into or out of
the database. Every existing service method, every Pydantic schema,
every `SaleItem.quantity * SaleItem.unit_price` computation in Python
continues to work completely unchanged, because Python code still
only ever sees ordinary floats (150.0, not 15000). SQL-level
func.sum()/func.avg() also continue to return correct results,
verified live (see tests/test_money_precision.py) rather than assumed
-- SQLAlchemy applies this type's result processing to aggregate
expressions over a typed column exactly the same as it does to a
plain column read.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Integer
from sqlalchemy.types import TypeDecorator


class MoneyCents(TypeDecorator[float]):
    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: float | None, dialect: object) -> int | None:
        if value is None:
            return None
        # Decimal(str(value)) -- not Decimal(value) -- deliberately:
        # Decimal's float constructor reproduces the float's exact
        # binary value (Decimal(0.1) is 0.1000000000000000055511151...),
        # which is precisely the bug this type exists to avoid
        # re-introducing at the conversion boundary. str(float) gives
        # the shortest decimal that round-trips back to the same
        # float, which is what a human actually typed or expects.
        cents = (Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(cents)

    def process_result_value(self, value: int | None, dialect: object) -> float | None:
        if value is None:
            return None
        return float(Decimal(value) / 100)

    @property
    def python_type(self) -> type[float]:
        # SQLAlchemy's base TypeEngine.python_type is a property that
        # RAISES NotImplementedError when not overridden -- it does
        # not return None or omit the attribute. That distinction
        # matters: code elsewhere that does
        # `getattr(column.type, "python_type", None)`, expecting a
        # missing implementation to look like a missing attribute,
        # gets the exception anyway, because getattr's default only
        # covers AttributeError. Confirmed by hitting exactly that
        # crash in the backup/restore path the first time a
        # TypeDecorator (this one) was introduced anywhere in this
        # codebase -- every column before this was a plain built-in
        # SQLAlchemy type. Explicitly overriding this is the correct,
        # SQLAlchemy-documented fix, not a workaround.
        return float
