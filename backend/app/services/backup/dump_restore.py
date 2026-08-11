"""
Whole-database dump/restore.

Implemented at the SQLAlchemy Core level (not by shelling out to a
database-specific dump tool), consistent with this project's
preference for portable, dialect-agnostic mechanisms.

Restore always deletes in reverse foreign-key order and inserts in
forward order, so foreign key constraints are never violated mid-restore.
"""

import enum
import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

# The backup system's own bookkeeping tables are deliberately excluded
# from the dump/restore cycle. Restoring a backup should not rewrite
# the backup system's own audit trail -- and including backup_logs
# specifically causes a genuine self-referential bug: restore_backup()
# holds an ORM reference to the very backup_logs row it's mid-transaction
# updating (to record restored_at), and if that table were wiped and
# reinserted from the older snapshot as part of the same restore, that
# reference goes stale before the update can be applied.
EXCLUDED_TABLES = frozenset({"backup_logs", "backup_oauth_tokens"})


def _restorable_tables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name not in EXCLUDED_TABLES]


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes):
        return value.decode("latin-1")  # round-trippable for arbitrary bytes
    raise TypeError(f"Cannot serialize {type(value)} for backup")


async def dump_all_tables(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    dump: dict[str, list[dict[str, Any]]] = {}
    for table in _restorable_tables():
        result = await db.execute(table.select())
        dump[table.name] = [dict(row._mapping) for row in result.all()]
    return dump


def serialize_dump(dump: dict[str, list[dict[str, Any]]]) -> bytes:
    return json.dumps(dump, default=_json_default).encode()


def deserialize_dump(data: bytes) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = json.loads(data)
    return result


def compute_manifest(dump: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {table_name: len(rows) for table_name, rows in dump.items()}


def _coerce_row_for_table(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    """Converts JSON-safe values (ISO date strings, enum .value strings)
    back into the Python types each column actually expects."""
    coerced: dict[str, Any] = {}
    for column in table.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        if value is None:
            coerced[column.name] = None
            continue

        # column.type.python_type can raise NotImplementedError (not
        # AttributeError) for a type that doesn't override it -- every
        # built-in SQLAlchemy type does, but a custom TypeDecorator
        # doesn't get that for free (see MoneyCents.python_type's own
        # docstring for how this exact crash was first found). A bare
        # `getattr(..., "python_type", None)` does NOT catch that,
        # since getattr's default only covers a genuinely missing
        # attribute, not a property that exists and raises when
        # accessed. Guarding this explicitly means any future custom
        # column type that forgets to override python_type degrades to
        # "value passed through as-is" here, not a hard crash that
        # takes down the entire restore.
        try:
            python_type = getattr(column.type, "python_type", None)
        except NotImplementedError:
            python_type = None
        enum_class = getattr(column.type, "enum_class", None)

        if enum_class is not None:
            coerced[column.name] = enum_class(value)
        elif python_type is datetime and isinstance(value, str):
            coerced[column.name] = datetime.fromisoformat(value)
        elif python_type is date and isinstance(value, str):
            coerced[column.name] = date.fromisoformat(value)
        elif python_type is bytes and isinstance(value, str):
            coerced[column.name] = value.encode("latin-1")
        else:
            coerced[column.name] = value
    return coerced


async def restore_all_tables(db: AsyncSession, dump: dict[str, list[dict[str, Any]]]) -> int:
    """
    Returns total rows restored across all tables.

    FK checks are temporarily disabled for the duration of this
    operation, even though SQLite doesn't enforce them by default --
    excluded tables (backup_logs) still hold live foreign keys into
    tables being wiped and reinserted here (e.g. users), and this stays
    correct regardless of whether FK enforcement is ever turned on for
    SQLite elsewhere in the app later.
    """
    restorable = _restorable_tables()
    tables_by_name = {table.name: table for table in restorable}

    await db.execute(text("PRAGMA foreign_keys=OFF"))

    try:
        for table in reversed(restorable):
            await db.execute(table.delete())

        total_rows = 0
        for table in restorable:
            rows = dump.get(table.name, [])
            if not rows:
                continue
            coerced_rows = [_coerce_row_for_table(table, row) for row in rows]
            await db.execute(tables_by_name[table.name].insert(), coerced_rows)
            total_rows += len(coerced_rows)
    finally:
        await db.execute(text("PRAGMA foreign_keys=ON"))

    await db.commit()
    return total_rows
