"""
Tests for app.main._frontend_dist_dir -- the function that decides
whether this backend should also serve a built frontend directly
(the bundled desktop .exe case) or leave that to Vite's dev server
(normal local development). This is unit-tested as a pure function
rather than through a live app instance, since the app object mounts
routes based on this function's result at import time -- re-importing
the whole app per test case to exercise different filesystem states
would be disproportionate to what this function actually does.
"""

from app.main import _frontend_dist_dir, _frontend_shell_response


class TestFrontendDistDir:
    def test_returns_none_when_nothing_is_bundled_or_built(self, monkeypatch, tmp_path):
        # Not frozen, and no frontend/dist sitting next to the backend
        # in this temp location -- the normal local-dev case.
        fake_main = tmp_path / "backend" / "app" / "main.py"
        fake_main.parent.mkdir(parents=True)
        fake_main.touch()

        monkeypatch.setattr("app.main.sys.frozen", False, raising=False)
        monkeypatch.setattr("app.main.__file__", str(fake_main))
        assert _frontend_dist_dir() is None

    def test_finds_dev_build_next_to_backend(self, monkeypatch, tmp_path):
        # ../frontend/dist relative to app/main.py, exactly what `npm
        # run build` produces and what a real dev checkout looks like.
        fake_main = tmp_path / "backend" / "app" / "main.py"
        fake_main.parent.mkdir(parents=True)
        fake_main.touch()
        dist_dir = tmp_path / "frontend" / "dist"
        dist_dir.mkdir(parents=True)

        monkeypatch.setattr("app.main.__file__", str(fake_main))
        monkeypatch.setattr("app.main.sys.frozen", False, raising=False)

        result = _frontend_dist_dir()
        assert result == dist_dir

    def test_looks_under_meipass_when_frozen(self, monkeypatch, tmp_path):
        # PyInstaller onefile sets sys.frozen=True and extracts data
        # files under sys._MEIPASS at runtime -- the packaged .exe case.
        meipass = tmp_path / "extracted"
        dist_dir = meipass / "frontend_dist"
        dist_dir.mkdir(parents=True)

        monkeypatch.setattr("app.main.sys.frozen", True, raising=False)
        monkeypatch.setattr("app.main.sys._MEIPASS", str(meipass), raising=False)

        result = _frontend_dist_dir()
        assert result == dist_dir

    def test_frozen_but_no_bundled_frontend_returns_none(self, monkeypatch, tmp_path):
        meipass = tmp_path / "extracted"
        meipass.mkdir()
        # frontend_dist deliberately not created here.

        monkeypatch.setattr("app.main.sys.frozen", True, raising=False)
        monkeypatch.setattr("app.main.sys._MEIPASS", str(meipass), raising=False)

        assert _frontend_dist_dir() is None

    def test_a_file_named_dist_is_not_mistaken_for_a_directory(self, monkeypatch, tmp_path):
        fake_main = tmp_path / "backend" / "app" / "main.py"
        fake_main.parent.mkdir(parents=True)
        fake_main.touch()
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        # A stray file called "dist" instead of a real build directory.
        (frontend_dir / "dist").touch()

        monkeypatch.setattr("app.main.__file__", str(fake_main))
        monkeypatch.setattr("app.main.sys.frozen", False, raising=False)

        assert _frontend_dist_dir() is None


class TestFrontendShellNeverHeuristicallyCached:
    """
    The real bug behind a genuine "sometimes starts blank, login never
    comes" report: Starlette's FileResponse sets no Cache-Control by
    default, so a browser (including the one inside the Electron
    wrapper) is free to serve a heuristically-cached, stale copy of a
    same-URL file like index.html without even asking the server --
    and after an app update replaces the files on disk, that stale
    copy can reference a since-deleted hashed JS filename, which 404s
    with nothing on screen and nothing for any retry/health-check
    logic to catch, since Chromium never makes the network request
    those watch. Every file _frontend_shell_response can return must
    carry an explicit Cache-Control: no-cache so the browser always
    revalidates -- cheap on this app's actual traffic pattern (a
    same-machine loopback request), and the only thing that actually
    closes this gap.
    """

    def test_the_spa_shell_fallback_is_marked_no_cache(self, tmp_path):
        (tmp_path / "index.html").write_text("<html>shell</html>")

        response = _frontend_shell_response("", tmp_path)

        assert response.path == tmp_path / "index.html"
        assert response.headers["cache-control"] == "no-cache"

    def test_a_deep_router_path_falls_back_to_the_shell_and_is_still_no_cache(self, tmp_path):
        (tmp_path / "index.html").write_text("<html>shell</html>")

        # /inventory isn't a real file on disk -- React Router handles
        # it client-side, so this must fall back to index.html exactly
        # like the root path does, with the same no-cache guarantee.
        response = _frontend_shell_response("inventory", tmp_path)

        assert response.path == tmp_path / "index.html"
        assert response.headers["cache-control"] == "no-cache"

    def test_a_real_non_hashed_file_is_also_marked_no_cache(self, tmp_path):
        # manifest.webmanifest, sw.js, registerSW.js, favicon.svg all
        # keep the exact same URL across every build, unlike the
        # content-hashed files under /assets/ -- same staleness risk
        # as index.html itself, so the same header is required here.
        (tmp_path / "index.html").write_text("<html>shell</html>")
        (tmp_path / "manifest.webmanifest").write_text("{}")

        response = _frontend_shell_response("manifest.webmanifest", tmp_path)

        assert response.path == tmp_path / "manifest.webmanifest"
        assert response.headers["cache-control"] == "no-cache"
