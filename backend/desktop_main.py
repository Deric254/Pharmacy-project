"""
Entrypoint for the bundled desktop .exe (built via pyinstaller/pharmacy-erp.spec).

This is deliberately NOT the same code path as `uvicorn app.main:app`
used for local dev / Docker / server deployments -- those have a real
Redis, a real .env, and a human running commands in a terminal who
already knows what's going on. A downloaded .exe has none of that: no
Python, no Node, no Redis, and the person running it just wants it to
work. Everything here exists to make "double-click, it works" true
without asking for a single prerequisite.

Where things live: %LOCALAPPDATA%\\PharmacyERP on Windows (falls back
to ~/.pharmacy-erp elsewhere, e.g. testing this on Linux/Mac). A
secrets file generated once on first run and reused on every
subsequent one -- regenerating JWT_SECRET_KEY/ENCRYPTION_KEY on every
launch would invalidate every session and make encrypted data
unreadable the moment the app restarts.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import cast

# Unbuffered stdout: without this, print() output can appear to lag
# behind what's actually happening (migrations already done, waiting
# on input()) when stdout isn't a real interactive terminal -- verified
# during a piped-input test where prompts didn't appear until the
# input they were waiting for had already been consumed from the pipe.
# The underlying flow was always correct; only the on-screen timing
# was confusing.
sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]


def _app_data_dir() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) if local_appdata else Path.home() / ".pharmacy-erp"
    data_dir = base / "PharmacyERP" if local_appdata else base
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _load_or_create_secrets(data_dir: Path) -> dict[str, str]:
    secrets_file = data_dir / "secrets.json"
    if secrets_file.exists():
        return cast("dict[str, str]", json.loads(secrets_file.read_text()))

    generated = {
        "jwt_secret_key": secrets.token_hex(32),
        "encryption_key": base64.b64encode(os.urandom(32)).decode(),
    }
    secrets_file.write_text(json.dumps(generated))
    # Windows has no umask concept the way POSIX does, but on any
    # POSIX system this at least keeps the secrets file from being
    # world-readable if someone ever runs this build there.
    with contextlib.suppress(OSError):
        secrets_file.chmod(0o600)
    return generated


def _configure_environment(data_dir: Path) -> None:
    secrets_values = _load_or_create_secrets(data_dir)
    db_path = data_dir / "pharmacy.db"

    os.environ.setdefault("ENVIRONMENT", "production")
    os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    os.environ.setdefault("REDIS_MODE", "memory")
    os.environ.setdefault("JWT_SECRET_KEY", secrets_values["jwt_secret_key"])
    os.environ.setdefault("ENCRYPTION_KEY", secrets_values["encryption_key"])
    os.environ.setdefault("CORS_ORIGINS", '["http://127.0.0.1:8000","http://localhost:8000"]')


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent

    alembic_cfg = Config(str(base / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(base / "alembic"))
    print("Setting up the database (first run may take a few seconds)...")
    command.upgrade(alembic_cfg, "head")


def _running_under_electron() -> bool:
    """
    Set by electron/main.js when it spawns this exe as its child
    process (see startBackend() there). Nothing else ever sets this --
    when it's absent, this is either the raw exe run directly, or the
    plain `python desktop_main.py` dev path, both of which genuinely
    need to open a browser themselves since nothing else will.
    """
    return os.environ.get("PHARMACY_ERP_ELECTRON") == "1"


def _wait_for_server_then_open_browser(port: int) -> None:
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    if not _running_under_electron():
                        webbrowser.open(url)
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    print(f"[WARNING] Server did not respond after 30s. Try opening {url} manually.")


def _already_running_instance(port: int) -> bool:
    """
    True if something on this port is already answering as THIS app
    (not just "something" -- a stray unrelated program on the same
    port should still produce a clear error, not be silently treated
    as us). Checked by hitting /health and confirming the JSON shape
    matches what app.main.health() actually returns.
    """
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            if response.status != 200:
                return False
            body = json.loads(response.read())
            return isinstance(body, dict) and body.get("status") == "ok"
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


def main() -> None:
    print("=" * 50)
    print(" Pharmacy ERP")
    print("=" * 50)
    print()

    port = 8000

    # The exact scenario a real bug report showed: a leftover backend
    # window from earlier testing still running on this port, then the
    # packaged exe launched on top of it. Failing loudly there is the
    # wrong response -- if it's genuinely already us and already
    # healthy, there's nothing to set up, just open the browser to it.
    if _already_running_instance(port):
        print(f"Pharmacy ERP is already running at http://127.0.0.1:{port}")
        if not _running_under_electron():
            print("Opening your browser...")
            webbrowser.open(f"http://127.0.0.1:{port}")
        print()
        print("This window can be closed -- it isn't the one running the app.")
        with contextlib.suppress(EOFError):
            input("Press Enter to close this window...")
        return

    data_dir = _app_data_dir()
    print(f"Data directory: {data_dir}")
    _configure_environment(data_dir)

    _run_migrations()

    import uvicorn

    threading.Thread(target=_wait_for_server_then_open_browser, args=(port,), daemon=True).start()

    print()
    print(f"Starting Pharmacy ERP at http://127.0.0.1:{port}")
    if _running_under_electron():
        print("The app window will appear in a moment.")
    else:
        print("Your browser should open automatically in a moment.")
    print("Close this window to stop the app.")
    print()

    # Deferred import so the app module (which populates SQLAlchemy's
    # mapper registry at import time -- see app/main.py) is only
    # loaded after migrations have already run against a schema it
    # matches.
    from app.main import app

    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    except OSError as exc:
        if exc.errno in (10048, 98):  # Windows / POSIX "address already in use"
            print()
            print("=" * 50)
            print(f" Something else on this computer is already using port {port},")
            print(" and it isn't another copy of Pharmacy ERP (already checked).")
            print(" Close whatever that is, or restart your computer, then try again.")
            print("=" * 50)
            input("Press Enter to close this window...")
            raise SystemExit(1) from exc
        raise


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:  # noqa: BLE001 -- last-resort handler, see comment below
        # Deliberately broad: this is the outermost boundary of the
        # whole program. Anything that reaches here would otherwise
        # print a raw traceback and immediately close the console
        # window on Windows, exactly the "blinks and closes" failure
        # already found and fixed in the .bat scripts -- an exe
        # deserves the same guarantee that a failure is readable.
        print()
        print("=" * 50)
        print(f" Something went wrong: {exc}")
        print("=" * 50)
        with contextlib.suppress(EOFError):
            input("Press Enter to close this window...")
        raise SystemExit(1) from exc
