"""
Read-only data-integrity check for a Pharmacy ERP database.

Scans for rows that break the rules the database enforces from migration
0039 onward (no negative stock or prices, nothing refunded more than sold,
...). Run it against a copy of a customer's database or backup BEFORE
shipping an update that adds those rules, or any time the app refuses to
start and mentions migration 0039.

    python scripts/check_data_integrity.py path/to/pharmacy.db

The database is opened read-only; this script cannot change it.
Exit code: 0 = clean, 1 = rows break a rule, 2 = could not run the check.
"""

import sqlite3
import sys
from pathlib import Path

# Allow running as `python scripts/check_data_integrity.py` from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.integrity_rules import (  # noqa: E402
    INVARIANTS,
    describe_violations,
    find_violations,
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/check_data_integrity.py <path to pharmacy.db>")
        return 2
    db_path = Path(argv[1])
    if not db_path.is_file():
        print(f"error: no such file: {db_path}")
        return 2

    try:
        connection = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"error: cannot open {db_path} read-only: {exc}")
        return 2
    try:
        try:
            violations = find_violations(connection)
        except sqlite3.Error as exc:
            print(f"error: this does not look like a Pharmacy ERP database ({exc})")
            return 2

        rules_checked = sum(len(rules) for rules in INVARIANTS.values())
        if not violations:
            print(f"OK: no rows break any of the {rules_checked} data-integrity rules.")
            return 0

        print(f"PROBLEM: {len(violations)} of {rules_checked} rules are broken by existing rows.\n")
        print(describe_violations(connection, violations))
        print("\nNothing was changed. These rows need to be reviewed and corrected by a person.")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
