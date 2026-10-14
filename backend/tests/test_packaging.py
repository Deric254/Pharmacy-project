"""
Alembic loads migrations at runtime, so PyInstaller's import scanner cannot see
the app modules they import. Each one has to be listed by hand in the build
spec's hiddenimports, or an installed copy fails at startup while every test
here still passes. This keeps the list and the migrations from drifting apart.
"""

import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
SPEC = BACKEND_DIR.parent / "pyinstaller" / "pharmacy-erp.spec"


def _app_modules_imported_by_migrations() -> set[str]:
    found: set[str] = set()
    for path in sorted((BACKEND_DIR / "alembic" / "versions").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                for alias in node.names:
                    submodule = f"{node.module}.{alias.name}"
                    is_module = (BACKEND_DIR / Path(*submodule.split("."))).with_suffix(".py")
                    found.add(submodule if is_module.exists() else node.module)
            elif isinstance(node, ast.Import):
                found.update(a.name for a in node.names if a.name.startswith("app"))
    return found


def test_every_app_module_a_migration_imports_is_in_the_build_spec():
    imported = _app_modules_imported_by_migrations()
    spec = SPEC.read_text()

    assert "app.core.integrity_rules" in imported  # the scan itself is working
    assert sorted(m for m in imported if f'"{m}"' not in spec) == []
