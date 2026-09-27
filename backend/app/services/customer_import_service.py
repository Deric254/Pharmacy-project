"""
Customer bulk import. Same shape and same guarantees as product bulk
import: the template constrains what can be typed in, but the real
authoritative guarantee is server-side -- every row fully validated,
and if anything is wrong, nothing is imported at all.
"""

import io
from typing import Any

from fastapi import HTTPException
from openpyxl import Workbook
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.user import User
from app.schemas.customer import CustomerCreate
from app.schemas.product import BulkImportResult, ImportRowError
from app.services.import_common import (
    check_duplicate_in_file,
    clean_str,
    raise_if_errors,
    write_example_row,
    write_header_row,
    write_instructions,
)
from app.services.spreadsheet_reader import read_data_rows

_HEADERS = ["Name", "Phone", "Email"]
_EXAMPLE_ROW = ["EXAMPLE - Jane Mwangi", "0712345678", "jane@example.com"]
_MAX_ROWS = 2000


def generate_customer_import_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Customers"

    write_header_row(ws, _HEADERS)
    write_example_row(ws, _EXAMPLE_ROW)

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 28

    write_instructions(ws, row=1, column=5, text="Delete the EXAMPLE row before importing.")

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _parse_and_validate(
    db: AsyncSession, file_bytes: bytes
) -> tuple[list[CustomerCreate], list[ImportRowError]]:
    rows = await read_data_rows(file_bytes, columns=len(_HEADERS), max_rows=_MAX_ROWS)

    errors: list[ImportRowError] = []
    candidates: list[CustomerCreate] = []
    seen_names: dict[str, int] = {}
    seen_phones: dict[str, int] = {}

    for offset, row in enumerate(rows):
        row_num = offset + 2
        row_values: list[Any] = list(row)
        name_raw, phone_raw, email_raw = row_values
        name = clean_str(name_raw)

        if not name:
            continue
        if name.upper().startswith("EXAMPLE"):
            continue

        phone = clean_str(phone_raw) or None
        email = clean_str(email_raw) or None

        row_already_invalid = False
        if len(name) > 150:
            errors.append(
                ImportRowError(
                    row=row_num, field="Name", message="Must be 150 characters or fewer."
                )
            )
            row_already_invalid = True
        if phone and len(phone) > 30:
            errors.append(
                ImportRowError(
                    row=row_num, field="Phone", message="Must be 30 characters or fewer."
                )
            )
            row_already_invalid = True
        if email and len(email) > 120:
            errors.append(
                ImportRowError(
                    row=row_num, field="Email", message="Must be 120 characters or fewer."
                )
            )
            row_already_invalid = True

        check_duplicate_in_file(seen_names, name.lower(), row_num, "Name", errors)

        if phone:
            check_duplicate_in_file(seen_phones, phone, row_num, "Phone", errors)

        if row_already_invalid:
            continue

        try:
            candidates.append(CustomerCreate(name=name, phone=phone, email=email))
        except ValidationError as exc:
            errors.append(
                ImportRowError(
                    row=row_num, field="Row", message=f"Invalid data: {exc.errors()[0]['msg']}"
                )
            )

    if not candidates:
        errors.append(
            ImportRowError(row=0, field="File", message="No customer rows found in this file.")
        )
        return candidates, errors

    phones_to_check = [c.phone for c in candidates if c.phone]
    existing_phone_set: set[str] = set()
    if phones_to_check:
        existing_phones = await db.execute(
            select(Customer.phone).where(Customer.phone.in_(phones_to_check))
        )
        existing_phone_set = {row[0] for row in existing_phones.all()}

    for candidate in candidates:
        if candidate.phone and candidate.phone in existing_phone_set:
            errors.append(
                ImportRowError(
                    row=seen_phones[candidate.phone],
                    field="Phone",
                    message=f'Phone "{candidate.phone}" already belongs to an existing customer.',
                )
            )

    return candidates, errors


async def bulk_import_customers(
    db: AsyncSession, file_bytes: bytes, user: User
) -> BulkImportResult:
    candidates, errors = await _parse_and_validate(db, file_bytes)

    raise_if_errors(errors, "imported")

    for candidate in candidates:
        db.add(Customer(**candidate.model_dump()))
    # One entry for the whole batch -- same reasoning as the product
    # importer's audit entry: a single real-world event, not one row
    # each.
    db.add(
        AuditLog(
            user_id=user.id,
            user_name_snapshot=user.full_name,
            action="customer.bulk_imported",
            entity_type="customer",
            entity_id="bulk",
            new_value=f"{len(candidates)} customer(s) imported",
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        # Same race CustomerService.create documents for a single
        # customer, here for a whole batch: the pre-commit duplicate-
        # phone check above isn't atomic with this INSERT. Without
        # this handler, the loser of that race got an unhandled 500
        # across the whole batch instead of a clean, actionable error.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "One or more of these phone numbers were just registered by someone else. "
                "Please re-check the file and try again."
            ),
        ) from exc

    return BulkImportResult(created=len(candidates))
