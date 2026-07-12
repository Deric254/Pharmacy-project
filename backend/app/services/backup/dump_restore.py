"""
Whole-database dump/restore.

Deliberately implemented at the SQLAlchemy Core level (not by shelling
out to mysqldump) so the exact same code path works identically on
MySQL and SQLite -- consistent with this project's established
preference for portable, dialect-agnostic mechanisms over DB-specific
tooling wherever one reasonably exists.

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

        python_type = getattr(column.type, "python_type", None)
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
    operation. This is necessary, not just convenient: excluded tables
    (backup_logs) still hold live foreign keys into tables being wiped
    and reinserted here (e.g. users), and MySQL correctly refuses a
    DELETE that would leave those references dangling mid-transaction,
    even though every row ultimately comes back with the same ID by
    the time the transaction commits. SQLite does not enforce FKs by
    default and would not have caught this -- confirmed by this
    exact scenario failing only against real MySQL, not SQLite, during
    development.
    """
    dialect_name = db.bind.dialect.name if db.bind is not None else ""
    restorable = _restorable_tables()
    tables_by_name = {table.name: table for table in restorable}

    if dialect_name == "mysql":
        await db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    elif dialect_name == "sqlite":
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
        if dialect_name == "mysql":
            await db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        elif dialect_name == "sqlite":
            await db.execute(text("PRAGMA foreign_keys=ON"))

    await db.commit()
    return total_rows
