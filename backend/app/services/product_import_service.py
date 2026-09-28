"""
Product bulk import.

Two layers of defense, not one. The Excel template itself constrains
what can be typed into it (a dropdown for unit, a numeric-only cell for
reorder point) -- that's what makes a bad row hard to create in
the first place. But Excel-level validation can be bypassed (pasting
values, editing with a different tool, a formula that evaluates past
the constraint), so it is never trusted as the real guarantee. Every
row is fully re-validated here, server-side, and if ANY row has ANY
problem, NOTHING is imported -- no partial import, no "47 succeeded, 3
failed" leaving the catalog in a half-imported state. Either the whole
file is clean and all of it lands in one transaction, or none of it
does, and the response says exactly which rows and fields need fixing
so the file can be corrected and re-uploaded whole.
"""

import io
from typing import Any

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.category import Category
from app.models.product import Product
from app.models.user import User
from app.schemas.product import BulkImportResult, ImportRowError, ProductCreate
from app.services.import_common import (
    check_duplicate_in_file,
    clean_str,
    raise_if_errors,
    write_example_row,
    write_header_row,
    write_instructions,
)
from app.services.spreadsheet_reader import read_data_rows

_COMMON_UNITS = [
    "unit",
    "tablet",
    "capsule",
    "bottle",
    "box",
    "syrup",
    "injection",
    "vial",
    "tube",
    "sachet",
    "strip",
    "pack",
]

_HEADERS = ["Name", "Barcode", "Unit", "Reorder point", "Category"]
_EXAMPLE_ROW: list[str | int | float] = [
    "EXAMPLE - Paracetamol 500mg",
    "",
    "tablet",
    20,
    "Painkillers",
]
_MAX_ROWS = 2000  # generous for a small pharmacy's catalog; guards against an accidental huge file
_MAX_CATEGORY_NAME_LENGTH = 80  # matches Category.name's column length exactly


def generate_import_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None  # a freshly created Workbook always has an active sheet
    ws.title = "Products"

    write_header_row(ws, _HEADERS)
    write_example_row(ws, _EXAMPLE_ROW)

    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 20

    # Unit: a dropdown, not free text -- the actual mechanism that makes
    # "Tabs" / "tabs " / "Tablet " typo variants structurally impossible
    # instead of merely discouraged.
    unit_validation = DataValidation(
        type="list",
        formula1=f'"{",".join(_COMMON_UNITS)}"',
        allow_blank=False,
        showErrorMessage=True,
        errorTitle="Invalid unit",
        error="Choose a unit from the dropdown list.",
    )
    ws.add_data_validation(unit_validation)
    unit_validation.add(f"C2:C{_MAX_ROWS}")

    reorder_validation = DataValidation(
        type="whole",
        operator="greaterThanOrEqual",
        formula1=0,
        allow_blank=False,
        showErrorMessage=True,
        errorTitle="Invalid reorder point",
        error="Reorder point must be a whole number, 0 or greater.",
    )
    ws.add_data_validation(reorder_validation)
    reorder_validation.add(f"D2:D{_MAX_ROWS}")

    # Category is deliberately free text, not a dropdown like Unit above:
    # the category list is open-ended and grows as a pharmacy's owner
    # defines new ones, unlike Unit's small fixed vocabulary. A dropdown
    # built from the live category list would also risk silently
    # breaking -- Excel's inline list-validation formula has an ~255
    # character limit, easy to exceed once a real pharmacy has more than
    # a handful of categories. Leaving it blank is fine (product stays
    # uncategorised); typing a name that doesn't exist yet creates it,
    # same "it just works" behaviour as the product form's own
    # "+ New category" control.
    write_instructions(
        ws, row=1, column=7, text="Category is optional. A new name creates that category."
    )
    write_instructions(ws, row=2, column=7, text="Delete the EXAMPLE row before importing.")

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _resolve_categories(
    db: AsyncSession, category_names: list[str | None]
) -> tuple[dict[str, int], list[str]]:
    """
    Maps each distinct category name referenced in the file (lowercased)
    to a real category id, creating any that don't exist yet -- same
    "type a new name, it just works" behaviour as the product form's
    inline "+ New category" control, so a bulk import never requires a
    separate trip to set categories up first. Matching is case-
    insensitive, same rule CategoryService.create enforces, so "Antibiotics"
    and "antibiotics" in the same file resolve to one category, not two.
    Also returns the names actually created, so the caller can report
    that back to whoever ran the import.
    """
    first_original_by_lower: dict[str, str] = {}
    for name in category_names:
        if name is not None and name.lower() not in first_original_by_lower:
            first_original_by_lower[name.lower()] = name
    if not first_original_by_lower:
        return {}, []

    existing = await db.execute(
        select(Category.id, Category.name).where(
            func.lower(Category.name).in_(first_original_by_lower.keys())
        )
    )
    category_id_by_lower: dict[str, int] = {
        name.lower(): category_id for category_id, name in existing.all()
    }

    new_names = [
        original
        for lower, original in first_original_by_lower.items()
        if lower not in category_id_by_lower
    ]
    if new_names:
        new_categories = [Category(name=name) for name in new_names]
        for category in new_categories:
            db.add(category)
        try:
            await db.flush()
        except IntegrityError as exc:
            # Same theoretical race as the whole-file IntegrityError
            # handler in bulk_import below: two overlapping imports (or
            # an import racing a single category creation) could both
            # pass the pre-flush SELECT above before either commits.
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail=(
                    "One or more categories in this file were just created by "
                    "someone else. Please re-check the file and try again."
                ),
            ) from exc
        for category in new_categories:
            category_id_by_lower[category.name.lower()] = category.id

    return category_id_by_lower, new_names


async def _parse_and_validate(
    db: AsyncSession, file_bytes: bytes
) -> tuple[list[ProductCreate], list[ImportRowError], list[str]]:
    rows = await read_data_rows(file_bytes, columns=len(_HEADERS), max_rows=_MAX_ROWS)

    errors: list[ImportRowError] = []
    candidates: list[ProductCreate] = []
    category_names: list[str | None] = []  # aligned by index with candidates
    seen_names: dict[str, int] = {}  # lowercased name -> first row it appeared on
    seen_barcodes: dict[str, int] = {}

    for offset, row in enumerate(rows):
        row_num = offset + 2  # 1-indexed, header is row 1
        row_values: list[Any] = list(row)
        name_raw, barcode_raw, unit_raw, reorder_raw, category_raw = row_values
        name = clean_str(name_raw)

        if not name:
            continue  # a genuinely blank row (trailing empty rows are common) -- not an error
        if name.upper().startswith("EXAMPLE"):
            continue  # the template's own example row, left in by mistake -- silently skip

        barcode = clean_str(barcode_raw) or None
        unit = clean_str(unit_raw) or "unit"
        category_name = clean_str(category_raw) or None

        if unit not in _COMMON_UNITS:
            errors.append(
                ImportRowError(
                    row=row_num,
                    field="Unit",
                    message=f'"{unit}" is not one of the allowed units. Use the dropdown.',
                )
            )

        try:
            reorder_point = int(reorder_raw) if reorder_raw is not None else 10
            if reorder_point < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(
                ImportRowError(
                    row=row_num, field="Reorder point", message="Must be a whole number, 0 or more."
                )
            )
            reorder_point = 0

        check_duplicate_in_file(seen_names, name.lower(), row_num, "Name", errors)

        row_already_invalid = False
        if len(name) > 150:
            errors.append(
                ImportRowError(
                    row=row_num, field="Name", message="Must be 150 characters or fewer."
                )
            )
            row_already_invalid = True
        if barcode and len(barcode) > 64:
            errors.append(
                ImportRowError(
                    row=row_num, field="Barcode", message="Must be 64 characters or fewer."
                )
            )
            row_already_invalid = True
        if category_name and len(category_name) > _MAX_CATEGORY_NAME_LENGTH:
            errors.append(
                ImportRowError(
                    row=row_num,
                    field="Category",
                    message=f"Must be {_MAX_CATEGORY_NAME_LENGTH} characters or fewer.",
                )
            )
            row_already_invalid = True

        if barcode:
            check_duplicate_in_file(seen_barcodes, barcode, row_num, "Barcode", errors)

        # Defensive backstop: even with every check above, construct
        # via try/except rather than trust that this list of checks is
        # exhaustive against every constraint the schema could ever
        # gain. A row that somehow still fails becomes a normal,
        # reported error -- never an unhandled crash.
        if row_already_invalid:
            continue  # already reported above; avoid a duplicate message for the same row

        try:
            candidates.append(
                ProductCreate(
                    name=name,
                    barcode=barcode,
                    unit=unit,
                    reorder_point=reorder_point,
                )
            )
            category_names.append(category_name)
        except ValidationError as exc:
            errors.append(
                ImportRowError(
                    row=row_num, field="Row", message=f"Invalid data: {exc.errors()[0]['msg']}"
                )
            )

    if not candidates:
        errors.append(
            ImportRowError(row=0, field="File", message="No product rows found in this file.")
        )
        return candidates, errors, []

    # Duplicate check against the EXISTING catalog -- same case-
    # insensitive, active-only rule as single product creation.
    names_to_check = [c.name.lower() for c in candidates]
    existing_names = await db.execute(
        select(func.lower(Product.name)).where(
            func.lower(Product.name).in_(names_to_check), Product.deleted_at.is_(None)
        )
    )
    existing_name_set = {row[0] for row in existing_names.all()}

    barcodes_to_check = [c.barcode for c in candidates if c.barcode]
    existing_barcode_set: set[str] = set()
    if barcodes_to_check:
        existing_barcodes = await db.execute(
            select(Product.barcode).where(
                Product.barcode.in_(barcodes_to_check), Product.deleted_at.is_(None)
            )
        )
        existing_barcode_set = {row[0] for row in existing_barcodes.all()}

    for idx, candidate in enumerate(candidates):
        row_num = idx + 2  # approximate for reporting; exact row tracked above for in-file dupes
        if candidate.name.lower() in existing_name_set:
            errors.append(
                ImportRowError(
                    row=seen_names[candidate.name.lower()],
                    field="Name",
                    message=f'"{candidate.name}" already exists in the catalog.',
                )
            )
        if candidate.barcode and candidate.barcode in existing_barcode_set:
            errors.append(
                ImportRowError(
                    row=seen_barcodes[candidate.barcode],
                    field="Barcode",
                    message=f'Barcode "{candidate.barcode}" already exists in the catalog.',
                )
            )

    if errors:
        return candidates, errors, []

    # Only resolved once every other check has passed -- no point
    # creating a brand new category for a row that's about to be
    # rejected anyway (raise_if_errors below still imports nothing in
    # that case, but this avoids an uncommitted, orphaned Category
    # object with no product ever pointing at it in the common case of
    # a file needing one more correction pass).
    category_id_by_lower, categories_created = await _resolve_categories(db, category_names)
    for candidate, category_name in zip(candidates, category_names, strict=True):
        if category_name is not None:
            candidate.category_id = category_id_by_lower[category_name.lower()]

    return candidates, errors, categories_created


async def bulk_import(db: AsyncSession, file_bytes: bytes, user: User) -> BulkImportResult:
    candidates, errors, categories_created = await _parse_and_validate(db, file_bytes)

    # All-or-nothing: a rejected file imports exactly zero rows,
    # regardless of how many were individually clean. Reporting every
    # problem at once (not just the first) is what lets one correction
    # pass fix the whole file instead of a slow back-and-forth
    # discovering one bad row per re-upload.
    raise_if_errors(errors, "imported")

    for candidate in candidates:
        db.add(Product(**candidate.model_dump()))
    # One entry for the whole batch, not per row -- same reasoning as
    # purchase_order.received: a bulk import is a single real-world
    # event (a spreadsheet handed to someone to load), and per-row
    # entries here would drown the audit trail in noise without
    # adding any who/what/when it doesn't already have at this grain.
    db.add(
        AuditLog(
            user_id=user.id,
            user_name_snapshot=user.full_name,
            action="product.bulk_imported",
            entity_type="product",
            entity_id="bulk",
            new_value=f"{len(candidates)} product(s) imported",
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        # Same theoretical race ProductService.create documents for a
        # single product, here for a whole batch at once: the
        # existing-catalog duplicate checks above are a pre-commit
        # SELECT, not atomic with this INSERT, so two overlapping
        # imports (or an import racing a single product creation)
        # could both pass those checks before either commits. Real
        # name/barcode UNIQUE constraints at the database level are
        # what actually stop a duplicate from landing; without this
        # handler, the loser of that race got an unhandled 500 across
        # its entire batch instead of a clean, actionable error.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "One or more of these products were just created by someone else. "
                "Please re-check the file and try again."
            ),
        ) from exc

    return BulkImportResult(created=len(candidates), categories_created=categories_created)
