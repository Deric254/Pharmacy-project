# -*- mode: python ; coding: utf-8 -*-
"""
Bundles backend/desktop_main.py plus everything it needs at runtime
into a folder containing Pharmacy-ERP.exe and its dependencies
(onedir mode, not onefile) -- onefile re-extracts its entire bundle to
a temp directory on every single launch, a well-documented source of
inconsistent startup delay (especially with antivirus scanning the
freshly-extracted files each time). Onedir extracts once, at install
time, so every subsequent launch is genuinely instant:
  - backend/alembic/ and backend/alembic.ini, because desktop_main.py
    runs migrations via the Alembic Config API against these actual
    files on disk (not something static analysis can discover).
  - frontend/dist, so the single running process can serve the whole
    app on one port -- see app/main.py's _frontend_dist_dir(), which
    specifically looks for a "frontend_dist" folder next to a frozen
    executable.

Run from the repo root:
    pyinstaller pyinstaller/pharmacy-erp.spec

Requires frontend/dist to already exist (`cd frontend && npm run
build` first) -- the release workflow does this before invoking
PyInstaller; it is not done here.

Output: dist/Pharmacy-ERP/Pharmacy-ERP.exe (a folder, not a single
file) -- electron/main.js and electron/package.json's extraResources
both reference this nested path.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

REPO_ROOT = Path(SPECPATH).resolve().parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

datas = [
    (str(BACKEND_DIR / "alembic"), "alembic"),
    (str(BACKEND_DIR / "alembic.ini"), "."),
    # tzdata is pure IANA timezone data, no importable code -- PyInstaller's
    # static analysis never discovers it on its own. Windows has no OS-level
    # tzdata to fall back on (unlike Linux/macOS), so without this every
    # zoneinfo.ZoneInfo(...) call -- including the code's own fallback to
    # ZoneInfo("UTC") -- fails with ModuleNotFoundError on every install.
    *collect_data_files("tzdata"),
]
if FRONTEND_DIST.is_dir():
    datas.append((str(FRONTEND_DIST), "frontend_dist"))
else:
    print(
        f"[WARNING] {FRONTEND_DIST} does not exist -- building without a "
        "bundled frontend. Run `npm run build` in frontend/ first for a "
        "real release build.",
        file=sys.stderr,
    )

a = Analysis(
    [str(BACKEND_DIR / "desktop_main.py")],
    pathex=[str(BACKEND_DIR)],
    binaries=[],
    datas=datas,
    # PyInstaller's static import scanner can't see routers/services
    # that are only ever imported indirectly through app.main's own
    # imports, alembic's dynamic env.py loading, or argon2's backend
    # selection -- listed explicitly rather than discovered.
    hiddenimports=[
        "app.main",
        "app.models",
        "alembic",
        "alembic.op",
        "aiosqlite",
        "argon2",
        "jose",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "tzdata",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Pharmacy-ERP",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # deliberate: first-run setup and status messages
    # need somewhere to show up; see desktop_main.py's docstring.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Pharmacy-ERP",
)
