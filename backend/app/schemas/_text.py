"""
Shared field type for "name"-like text fields.

Plain `str = Field(min_length=1)` does NOT reject a string that's
nothing but whitespace -- a single space has length 1, so it satisfies
the constraint and sails straight through to the database. This was
found and fixed for product names (see the chaos-testing note this
type used to live next to in product.py), but the same gap existed
unfixed in every other "name" field across the app: customers,
suppliers, roles, users, and the business's own name in Setup/Config.
A pharmacy owner typing a space instead of a name (fat-fingering the
Tab key, an autofill mishap, etc.) would previously get a customer or
supplier record with no usable name at all -- not a crash, just a
silently broken record sitting in the list forever.

NonBlankName closes this for every field that uses it: Pydantic's own
`min_length=1` still runs on the raw input first (so a request with a
literal empty string still gets the standard field-length error), and
this AfterValidator then strips whitespace and rejects what's left if
it's empty -- while also returning the *stripped* value, so " Jane "
is stored as "Jane" rather than with stray leading/trailing spaces
that would silently break exact-match lookups (e.g. the POS phone/name
lookup in customers.py's get_by_phone).
"""

from typing import Annotated

from pydantic import AfterValidator


def _must_have_real_content(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("This field cannot be empty or just whitespace.")
    return stripped


NonBlankName = Annotated[str, AfterValidator(_must_have_real_content)]


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# For optional fields like phone/email where an exact-match lookup
# happens later (e.g. customers.py's get_by_phone does
# Customer.phone == phone). An unstripped " 0722000000" registered at
# checkout would never match a later lookup for "0722000000" typed
# cleanly -- same customer, two different POS visits, treated as
# strangers. Blank-after-strip collapses to None rather than storing
# an empty string, consistent with the field already being optional.
OptionalStrippedText = Annotated[str | None, AfterValidator(_strip_or_none)]
