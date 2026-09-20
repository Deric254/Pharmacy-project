"""
The shared, bounded .xlsx reader behind all four bulk importers. An upload
must be limited by what it will expand to in memory, not by its size on the
wire: a few kilobytes can describe a million rows of sixteen thousand
columns, or a zip that inflates a thousand-fold.
"""

import io
import zipfile

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from app.services.spreadsheet_reader import read_data_rows


def _xlsx(cells: dict[str, object]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for reference, value in cells.items():
        sheet[reference] = value
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def _refused(file_bytes: bytes, *, max_rows: int = 2000) -> str:
    with pytest.raises(HTTPException) as refusal:
        await read_data_rows(file_bytes, columns=3, max_rows=max_rows)
    assert refusal.value.status_code == 400
    return str(refusal.value.detail)


async def test_rows_below_the_header_come_back_exactly_as_wide_as_asked():
    rows = await read_data_rows(
        _xlsx({"A1": "Name", "A2": "Aspirin", "C2": "x", "A3": "Ibuprofen"}),
        columns=3,
        max_rows=2000,
    )

    assert rows == [("Aspirin", None, "x"), ("Ibuprofen", None, None)]


async def test_a_sheet_with_only_a_header_has_no_rows():
    assert await read_data_rows(_xlsx({"A1": "Name"}), columns=3, max_rows=2000) == []


async def test_a_far_flung_cell_cannot_widen_the_rows():
    # XFD is column 16,384. The rows must still be 3 wide, not 16,384.
    rows = await read_data_rows(
        _xlsx({"A1": "h", "B2": "y", "XFD2": "far"}), columns=3, max_rows=2000
    )

    assert rows == [(None, "y", None)]


async def test_a_sheet_declaring_a_million_rows_is_refused_before_any_are_built():
    hostile = _xlsx({"A1": "h", "A2": "x", "XFD1048576": "x"})
    assert len(hostile) < 10_000  # a few KB on the wire

    assert "more than 2000 rows" in await _refused(hostile)


async def test_exactly_the_maximum_number_of_rows_is_allowed():
    cells = {"A1": "h", **{f"A{n}": f"item {n}" for n in range(2, 12)}}

    rows = await read_data_rows(_xlsx(cells), columns=1, max_rows=10)

    assert len(rows) == 10


async def test_one_row_over_the_maximum_is_refused():
    cells = {"A1": "h", **{f"A{n}": f"item {n}" for n in range(2, 13)}}

    assert "more than 10 rows" in await _refused(_xlsx(cells), max_rows=10)


async def test_a_file_that_inflates_far_beyond_its_size_is_refused():
    valid = _xlsx({"A1": "h", "A2": "x"})
    bomb = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(valid)) as source,
        zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for member in source.infolist():
            target.writestr(member, source.read(member))
        target.writestr("xl/padding.bin", b"\0" * (101 * 1024 * 1024))
    assert len(bomb.getvalue()) < 1_000_000  # under a megabyte on the wire

    assert "too large" in await _refused(bomb.getvalue())


async def test_something_that_is_not_a_spreadsheet_is_refused():
    assert "Could not read" in await _refused(b"just some text, not a workbook")


async def test_a_zip_that_is_not_a_workbook_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")

    assert "Could not read" in await _refused(buffer.getvalue())
