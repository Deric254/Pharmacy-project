"""
The backend must not outlive the desktop window that started it. An orphaned
backend is what used to pile up in Task Manager and block the next launch.
"""

import subprocess
import sys
import threading
import time

import desktop_main


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_watchdog_fires_when_parent_dies() -> None:
    parent = _sleeper()
    fired = threading.Event()
    threading.Thread(
        target=desktop_main._watch_parent, args=(parent.pid, fired.set), daemon=True
    ).start()
    time.sleep(0.5)
    assert not fired.is_set(), "must not fire while the parent is alive"
    parent.kill()
    parent.wait()
    assert fired.wait(timeout=5), "must fire after the parent is gone"


def test_watchdog_does_not_fire_for_a_live_parent() -> None:
    parent = _sleeper()
    fired = threading.Event()
    try:
        threading.Thread(
            target=desktop_main._watch_parent, args=(parent.pid, fired.set), daemon=True
        ).start()
        assert not fired.wait(timeout=2.5)
    finally:
        parent.kill()
        parent.wait()


def test_watchdog_is_off_outside_electron(monkeypatch) -> None:
    monkeypatch.delenv("PHARMACY_ERP_ELECTRON", raising=False)
    monkeypatch.setenv("PHARMACY_ERP_PARENT_PID", "1")

    class Server:
        should_exit = False

    server = Server()
    desktop_main._start_parent_watchdog(server)
    time.sleep(0.2)
    assert server.should_exit is False
