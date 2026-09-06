"""
Shared field type for IANA timezone name validation.

A saved timezone string was previously only ever checked for validity
when read back (see business_time.py's get_business_timezone, which
falls back to UTC on any bad saved name so a typo can never crash a
report) -- which meant a typo or garbage value was silently *accepted*
at write time, then silently became UTC on every subsequent "what day
is it for this business" calculation from that point on: FEFO expiry
checks, sales reports, audit log date filtering, all of it, with
nothing ever telling the person who saved it that anything was wrong.
ValidTimezone closes that at the one place it should be closed -- the
moment the value is written -- so a bad value gets a clear 422
immediately instead of silently corrupting every date calculation
downstream. get_business_timezone's own UTC fallback stays as-is
deliberately: it's still the right behavior for a value that was
somehow already invalid before this validator existed.
"""

from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AfterValidator


def _must_be_a_real_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except Exception as exc:  # noqa: BLE001 - any bad name (unknown key, wrong type reaching here, etc.) is the same user-facing error
        raise ValueError(
            f"'{value}' is not a recognized timezone name (e.g. 'Africa/Nairobi')."
        ) from exc
    return value


ValidTimezone = Annotated[str, AfterValidator(_must_be_a_real_timezone)]
