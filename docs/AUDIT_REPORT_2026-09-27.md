# Optimization / Performance / Security Audit — 2026-09-27

Scope: full backend (FastAPI/SQLAlchemy/SQLite), full frontend
(React/Vite/TS), Electron shell. Every check below was actually run
against this codebase in a clean environment — nothing in this report
is inferred or assumed.

## Summary

The codebase was already in genuinely excellent shape. Static analysis,
type-checking, security scanning, and the full test suite (741 backend
tests + 104 frontend tests) all passed cleanly *before* any changes were
made. The only real gaps found were outdated/vulnerable pinned
dependencies and missing route-level code-splitting on the frontend.
Both are now fixed and re-verified.

## Tools run (all against this exact codebase)

| Tool | Target | Result |
|---|---|---|
| `ruff check` (project's own strict config) | backend | clean |
| `mypy --strict` | backend, 113 files | clean |
| `bandit` | backend | no medium/high findings |
| `pip-audit` | backend deps | **45 known CVEs found → 0 after fixes** |
| `pytest` (full suite, real Redis) | backend | **741 passed** |
| `oxlint` | frontend | clean |
| `tsc -b` | frontend | clean |
| `npm audit` | frontend deps | 0 vulnerabilities (already clean) |
| `vitest run` | frontend | **104 passed** |
| `vite build` | frontend | verified real chunk-size improvement |
| Manual app boot smoke test (migrations + live requests) | backend | verified working on new dependency stack |

## Changes made

### 1. Dependency vulnerabilities fixed (backend)

`pip-audit` found 45 known CVEs across 5 packages. All are now resolved
to fully patched versions, re-verified with a second `pip-audit` run
(**0 vulnerabilities**) and a full test-suite re-run (**741/741 passed**)
against the new stack:

- **`python-jose` → `pyjwt==2.13.*`**: `python-jose` is unmaintained
  upstream with multiple *unpatched* CVEs (no fixed release exists).
  Migrated to PyJWT, which was already a test-only dependency in this
  project. The migration is contained entirely to
  `app/core/security.py`, per that file's own "one place to audit"
  design — every caller elsewhere only ever imports the re-exported
  `JWTError` name and `create_token`/`decode_token`, so nothing else
  changed. All auth, refresh-rotation, and JWT-tampering tests
  (`test_auth.py`, `test_audit_security.py`) still pass unmodified.
- **`fastapi==0.115.*` → `0.141.*`**: was pinned ~26 minor versions
  behind current, which is also what pulled in the two vulnerable
  transitive packages below. This is a large jump (Starlette itself
  moved from `0.46.x` to a new `1.x` line in that range), so it was
  validated by reinstalling clean and re-running the *entire* backend
  test suite plus a manual app-boot smoke test (migrations, `/docs`,
  `/openapi.json`, a real request through the full middleware stack) —
  all passed before this was considered safe to ship.
- **`cryptography==43.0.*` → `50.*`**: had several disclosed CVEs
  (PYSEC-2026-35, -1284, -2141, -3552/3553/3554, GHSA-537c-gmf6-5ccf).
- **`python-multipart==0.0.12` → `0.0.32`**: several disclosed DoS/
  parsing CVEs; pinned explicitly (not left to whatever FastAPI
  resolves) so a future FastAPI bump can't silently drag it back down.

  A useful *side effect* of the PyJWT migration: PyJWT is stricter than
  jose and now emits `InsecureKeyLengthWarning` for HMAC secrets under
  32 bytes. Every production/desktop deployment should confirm its
  `JWT_SECRET_KEY` is at least 32 bytes — the app's `Settings` already
  requires this value to be set explicitly (no insecure default), this
  is just a reminder to check its length specifically.

### 2. Frontend route-level code-splitting

All 17 authenticated pages (`SalesPage`, `ReportsPage`, `SettingsPage`,
`RolesPage`, `BackupsPage`, etc.) were imported eagerly in `App.tsx`,
so every page's code was parsed and evaluated on every single app
launch, before anything could render — even pages a given user might
never open (Roles admin, Backups, AI assistant). The codebase already
used `React.lazy()` for chart components, just not at the page/route
level.

Extended the same pattern to every page reachable only after
authentication (`LoginPage` and `SetupPage` stay eager, since
lazy-loading the very first screen a user sees only adds a guaranteed
loading flash for no benefit). Wrapped the authenticated route subtree
in a single `<Suspense>` boundary using the same loading-dots visual
already used for the app's own bootstrap screen, so it reads as one
continuous app rather than two different loading states.

**Measured, verified result** (`vite build`, before → after):

| | Before | After |
|---|---|---|
| Main entry chunk | 460.5 kB (120.5 kB gzip) | 278.2 kB (85.8 kB gzip) |
| Pages | bundled into entry chunk | 17 separate on-demand chunks (1.7–29 kB each) |

That's a ~40% cut in what has to be parsed and evaluated before the
app can render its first screen, on every launch.

## Findings that were investigated and turned out *not* to be problems

In the interest of not reporting speculative issues, these were things
that looked like they might be gaps and were checked directly rather
than assumed:

- **POS product search with an empty query** looked like it might
  render an unbounded product list for a large pharmacy catalog with
  no virtualization. Checked the backend endpoint directly: results
  are already capped at 200, sorted most-stocked-first. Not an issue.
- **FEFO batch selection running one query per cart line item** looked
  like a possible N+1 pattern. It's a deliberate, correct design
  (batch selection needs per-product locking semantics), and basket
  sizes make this a non-issue in practice.
- **Report aggregation** was checked for the classic "pull every row
  into Python and sum it there" anti-pattern. It already aggregates in
  SQL (`GROUP BY` + `func.sum`) — a comment in `report_service.py`
  documents that this was already fixed once before, for exactly the
  reason it would otherwise matter (unbounded `days` lookback).
- **Electron security config**: `contextIsolation: true`,
  `nodeIntegration: false`, `sandbox: true` are already set correctly
  on every `BrowserWindow`. Nothing to change.
- **Database indexing**: every foreign key and every column used for
  sorting/filtering in a hot path already has an index, including
  partial indexes for the soft-delete pattern on `products`.
- **SQLite configuration** (`WAL` mode, `busy_timeout`,
  `synchronous=FULL`, per-connection foreign-key enforcement) was
  already tuned correctly for this app's actual concurrency profile,
  with the reasoning documented in `database.py`.

## Files changed

- `backend/requirements.txt` — dependency version bumps (see above)
- `backend/requirements-dev.txt` — removed now-redundant
  `types-python-jose` and `pyjwt` (the latter is now pulled in via
  `requirements.txt`, avoiding the exact "listed in the wrong file"
  bug class this project's own comments already called out for
  `httpx`/`pillow`)
- `backend/app/core/security.py` — migrated JWT handling from
  `python-jose` to `pyjwt`, isolated to this one file
- `frontend/src/App.tsx` — route-level code-splitting via
  `React.lazy()` + `Suspense`

## To deploy

1. Backend: delete any existing venv, then
   `pip install -r requirements.txt -r requirements-dev.txt` (dev
   deps only needed for running tests/lint, not for running the app),
   then run `alembic upgrade head` as usual.
2. Frontend: `npm install && npm run build` as usual — no workflow
   changes, this is a drop-in source change.
3. Confirm your deployed `JWT_SECRET_KEY` is at least 32 bytes (see
   the PyJWT note above).
4. Everything else (Electron packaging, PyInstaller spec, install
   scripts) is unchanged.
