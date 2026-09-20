"""
Bounded reading of an uploaded .xlsx, shared by the four bulk importers.

An .xlsx is a zip of XML that openpyxl parses entirely into memory, so an
upload has to be bounded by what it will *become*, not by its size on the
wire (the routes already cap that at 10 MB):

* a few kilobytes can expand to gigabytes (a zip is happy to compress a run of
  zeros a thousand-fold), so the uncompressed size is checked first;
* a sheet is as tall and as wide as its furthest cell, so two cells -- A1 and
  XFD1048576 -- describe a million rows of sixteen thousand columns each; the
  declared height is checked before any row is built, and only the columns
  the importer actually reads are ever materialised.
"""

import io
import zipfile
from typing import Any

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from openpyxl import load_workbook

# A 2,000-row import is well under a megabyte of XML; this leaves ample room
# for styled or annotated workbooks while still refusing a decompression bomb.
_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024

_UNREADABLE = "Could not read this file as an Excel spreadsheet."


async def read_data_rows(
    file_bytes: bytes, *, columns: int, max_rows: int
) -> list[tuple[Any, ...]]:
    """
    Every row below the header of the first sheet, each exactly `columns`
    cells wide (short rows are padded with None), refusing a file that would
    need more than `max_rows` of them.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            uncompressed_bytes = sum(member.file_size for member in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=_UNREADABLE) from exc
    if uncompressed_bytes > _MAX_UNCOMPRESSED_BYTES:
        raise HTTPException(
            status_code=400,
            detail="This file is too large to import. Split it into smaller batches.",
        )

    try:
        # load_workbook is synchronous, CPU-bound XML parsing -- calling it
        # directly here would block this process's single event loop for the
        # whole parse, freezing every other request the app is handling (any
        # cashier's sale, any other page load) until a large file finishes
        # reading. run_in_threadpool moves that work to a separate OS thread.
        workbook = await run_in_threadpool(load_workbook, io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_UNREADABLE) from exc

    sheet = workbook.active
    if sheet is None:
        raise HTTPException(status_code=400, detail="This file has no worksheet to read.")

    if sheet.max_row - 1 > max_rows:
        raise HTTPException(
            status_code=400,
            detail=f"This file has more than {max_rows} rows. Split it into smaller batches.",
        )
    return list(sheet.iter_rows(min_row=2, max_col=columns, values_only=True))
