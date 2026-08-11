"""
Shared field types for money-like and quantity-like values.

Plain `float = Field(ge=0)` does NOT reject `Infinity`/`NaN` -- both are
mathematically >= 0 (or, for NaN, every comparison is simply False,
which `ge` silently treats as "constraint not violated"). Python's
`json` module accepts the non-standard `Infinity`/`-Infinity`/`NaN`
literals by default too, so a client can send them over the wire and
Pydantic's `ge=0` alone lets them straight through to the database --
confirmed by actually doing it: an `Infinity` price was committed to
the products table before the response even finished serializing.

The same hole exists for plain `int = Field(gt=0)` with no upper
bound, just reached differently: SQLite's INTEGER column is a signed
64-bit value, and a large enough int crashes with `OverflowError` at
the database layer instead of a clean validation error -- also
confirmed by actually doing it, with a 21-digit qty_received.

`Money`/`PositiveMoney` and `Quantity`/`PositiveQuantity` close both
gaps for every price/cost/amount and quantity field at once, with
ceilings generous enough for any real transaction size but small
enough that a stray extra digit or a deliberate overflow attempt can't
produce a value that breaks reports, receipts, arithmetic, or the
database itself.
"""

import math
from typing import Annotated

from pydantic import AfterValidator, Field


def _must_be_finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("must be a finite number (not infinity or NaN)")
    return value


MAX_MONEY = 999_999_999.99

Money = Annotated[float, AfterValidator(_must_be_finite), Field(ge=0, le=MAX_MONEY)]
PositiveMoney = Annotated[float, AfterValidator(_must_be_finite), Field(gt=0, le=MAX_MONEY)]

# A quantity field with only `gt=0`/`ge=0` and no upper bound has the
# same hole as Money did, just reached with an integer instead of a
# float: SQLite's INTEGER column is a signed 64-bit value, and a large
# enough int (confirmed with a 21-digit qty_received) raises
# `OverflowError: Python int too large to convert to SQLite INTEGER`
# at the database layer -- a 500, not a clean validation error, and
# on MySQL/production the failure mode would differ again rather than
# be a predictable 422 either way. No real pharmacy transaction is
# anywhere near a billion units; this ceiling exists purely to keep
# "clearly absurd input" from ever reaching the database at all.
MAX_QUANTITY = 1_000_000_000

Quantity = Annotated[int, Field(ge=0, le=MAX_QUANTITY)]
PositiveQuantity = Annotated[int, Field(gt=0, le=MAX_QUANTITY)]
# For fields that are legitimately negative (a stock adjustment
# removing units) -- same overflow risk in both directions, so the
# bound is symmetric rather than just an upper limit.
QuantityDelta = Annotated[int, Field(ge=-MAX_QUANTITY, le=MAX_QUANTITY)]
