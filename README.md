# Pharmacy ERP

Full pharmacy POS/ERP — sales, batch/expiry-aware inventory, purchasing with
Kanban, stock takes, customers, reports, AI assistant, backups, real-time
cross-module sync via an event bus. Runs entirely on one computer, no
network or server setup required. Every business-facing detail — name,
logo, slogan, currency, and choice of 4 built-in visual themes — is
configured live from Settings, baked into the installer per business, not
hardcoded to any one pharmacy.

## Stack
FastAPI (async) · SQLite (single-file database, no separate server to
install or run) · Redis-compatible in-memory cache/event bus (no separate
Redis install needed either) · SQLAlchemy 2 (async) · Alembic (migrations)
· React + Vite + Tailwind (frontend) · Electron (the actual desktop app
that ships to a real user).

## Running it for the first time

There are two ways to run this, and they're for two different people.
**If you're a pharmacy owner or staff member, you almost certainly want
the Windows section below, Option 1 (the installer)** — nothing past this
paragraph is relevant to you. Everything under "Path A" is for working on
the source code itself.

### Path A — local dev, fastest way to see it running

Needs: Python 3.12+, Node 20+. That's it — no separate database server,
no separate Redis install. This app only ever uses SQLite (one file) and
an in-process in-memory stand-in for Redis, matching exactly what the
shipped desktop app uses.

```bash
# 1. Backend
cd backend
pip install -r requirements.txt -r requirements-dev.txt
export DATABASE_URL="sqlite+aiosqlite:///./dev.db"
export JWT_SECRET_KEY="dev-secret-change-me"
export ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # dev only -- see note below
export REDIS_MODE="memory"
alembic upgrade head
uvicorn app.main:app --reload
# -> http://localhost:8000  (health check: http://localhost:8000/health)
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
# -> http://localhost:5173  (proxies /api to the backend automatically)
```

Open http://localhost:5173 — since no user exists yet, you'll land on the
same setup screen the installer shows: create the owner account right
there. That's a fully working system after that — real checkout, real
stock ledger, real refunds, real inventory adjustments.

**About `ENCRYPTION_KEY`:** it must be a base64-encoded 32-byte value.
The one above is all-zero and fine for trying the system locally, but
generate a real one for anything that matters:
`python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`

### Windows — the two ways to run it, simplest first

**Option 1 — the installer, `Pharmacy-ERP-Setup-vX.Y.Z.exe`.** Every
tagged release publishes this at
[github.com/Deric254/Pharmacy-project/releases](https://github.com/Deric254/Pharmacy-project/releases).
Real Windows install — Start Menu entry, desktop shortcut, an actual
native window, no console/terminal visible at any point. Under the hood
it's the same backend proven throughout this README, wrapped in Electron
purely so double-clicking it feels like a real application instead of a
developer tool. First run opens straight into a setup screen (create the
owner account right there in the window — full name, username, password),
then the app itself. This is the one to hand to a pharmacy owner or staff
member — nothing else on this page is meant for them.

**Option 2 — `Pharmacy-ERP.bat`**, at the repo root, only if you're
working from a source checkout instead of a release download (building a
custom branded installer for a specific business, or developing the app
itself). That's the only script you ever need to run, first time or every
time after — it checks whether setup has been done yet, does it
automatically if not (installs everything, generates a real `.env`, runs
migrations), then always finishes by starting the backend and frontend
and opening your browser to the same setup screen the installer shows.
Nothing else to click, nothing to remember to run first.

If Redis isn't already on your machine, the script downloads a real,
portable Redis-for-Windows build automatically (from the actively
maintained [redis-windows/redis-windows](https://github.com/redis-windows/redis-windows)
project) into `redis-portable\` next to the script — no install, no admin
rights, nothing added to PATH. That only fails if the machine has no
internet access, in which case it prints the manual Memurai fallback
instead of just stopping.

**Important (Option 2):** extract the `.zip` fully first (right-click
→ Extract All). Double-clicking a `.bat` file from inside Windows
Explorer's zip preview, without extracting, is the single most common way
these scripts fail — `Pharmacy-ERP.bat` checks for this specifically and
tells you if that's what happened, rather than failing with a confusing
wall of errors.

**Honesty check:** I do not have a Windows machine in the environment I
built this in, so **none of this has actually been double-clicked and run
on real Windows** — only reasoned through carefully, and where possible,
actually built and run on Linux as the closest available proxy (the
backend the installer wraps was genuinely compiled with PyInstaller and
run end-to-end here — real migrations, real first-user creation, real
login — just producing a Linux binary, not the `.exe` Windows CI actually
builds). One real bug was caught this way already: two of the internal
helper scripts (`backend\start-backend.bat`, `frontend\start-frontend.bat`)
had no error handling and no `pause`, so if either was ever run directly
instead of through the main script, it would fail instantly and the
window would close before anything was readable — "it blinks and closes"
is exactly what that produces. A second one was caught building the exe
itself: it crashed with a raw traceback instead of a readable message if
launched without a console attached. Both are now guarded the same way
everything else is: every failure path prints why, then pauses instead of
vanishing. The Redis auto-download (`windows\download-redis.ps1`) carries
the same caveat most directly — real GitHub API calls, real PowerShell
`Expand-Archive`, checked carefully for correctness, but never actually
run on a Windows machine. If it fails, the script falls back to printing
the manual Memurai steps rather than leaving you stuck.

The Electron installer (Option 1) carries the biggest version of this
caveat: Electron needs a real display to open a window, which this
environment doesn't have. `electron/main.js` was written carefully
against Electron's stable, long-standing APIs (`app`, `BrowserWindow`,
single-instance-lock) and every piece it depends on — the backend it
spawns, the setup flow the window loads — has been tested thoroughly on
its own. But the wrapper itself has never actually opened a window.

The rest of `Pharmacy-ERP.bat` has been checked carefully for the same
class of batch-scripting pitfalls (unescaped nested quoting, delayed-vs-
immediate variable expansion inside conditional blocks), and every file
uses proper CRLF line endings. If it still breaks on your machine, that's
a real bug report, not user error — tell me exactly what happened
(a screenshot of the window before it closes helps a lot) and I'll fix it.

### Making it your own business

Once logged in as ChemistOwner/Administrator, go to **Settings** and set
the business name, slogan, logo, currency, and pick one of the 4 built-in
themes (Ledger, Clinical, Midnight, Sunrise). Takes effect immediately for
everyone, no rebuild, no restart. The only exception is the icon/name
shown when someone installs the app to a home screen — that's set once per
deployment via `frontend/.env` (`VITE_APP_NAME` etc., see
`frontend/.env.example`) and requires a rebuild, since it's baked into a
static PWA manifest file read by the OS before the app itself ever runs.

## Quality gate

Run this locally before pushing — it's exactly what CI runs:

```bash
cd backend
ruff check . && ruff format --check . && mypy app scripts desktop_main.py && pytest --cov=app --cov-fail-under=80
```

As of the last full run: 252 backend tests passing, 0 failing, 93.6%
coverage. Frontend: `cd frontend && npx tsc -b && npx oxlint && npm run build`.


## Commit convention (this drives versioning — required, not optional)

Every commit message must follow **Conventional Commits**:

| Prefix | Effect on next release |
|---|---|
| `fix: ...` | PATCH bump (1.2.3 → 1.2.4) |
| `feat: ...` | MINOR bump (1.2.3 → 1.3.0) |
| `feat!: ...` or a `BREAKING CHANGE:` footer | MAJOR bump (1.2.3 → 2.0.0) |
| `chore:`, `docs:`, `test:`, `refactor:` | no release triggered |

## Release process (automatic — do not hand-edit versions)

1. Push to `main`.
2. **CI workflow** runs lint, type-check, migrations-against-test-DB, and
   tests with coverage. If anything fails, the pipeline stops here —
   nothing broken can ever reach a release.
3. **Release workflow** runs only after CI succeeds. It reads commit
   messages since the last tag, bumps the version (`app/__init__.py` +
   `pyproject.toml`), updates `CHANGELOG.md`, creates a git tag (`vX.Y.Z`),
   and publishes a GitHub Release — all automatic, no manual version edits.
4. **Only if a release was actually cut** (i.e. there were release-worthy
   commits — a docs-only push cuts no release and this step is skipped
   entirely), a second job builds the Windows executable: checks out the
   exact tagged commit, builds the frontend, bundles everything via
   PyInstaller (`pyinstaller/pharmacy-erp.spec`) into one `Pharmacy-ERP.exe`.
5. **The built exe is actually run** on the Windows runner before it's
   trusted with anything — real first-run input piped in, waits for
   `/health`, performs a real login against the account it just created.
   If any of that fails, the release build fails loudly rather than
   silently attaching a broken exe to the release. Only after that passes
   does the build continue: the same exe gets wrapped into a real Windows
   installer via Electron, both get renamed with the version, and both
   are uploaded as release assets.

You never manually edit a version number. If you did, the next automated
release would immediately overwrite it based on commit history — so don't
fight the tool, write correct commit prefixes instead.

## Staying up to date (in-app)

Every screen checks `https://api.github.com/repos/Deric254/Pharmacy-project/releases/latest`
in the background (`frontend/src/lib/updateCheck.ts`) and compares it to
the running version reported by `/health`. If a newer release exists, a
banner appears with a direct link to the new installer specifically
(`Pharmacy-ERP-Setup-*.exe`, never the raw backend exe a release also
attaches internally) — informational only, never forced, and any failure
(offline, rate-limited, no releases yet) just means the banner doesn't
show, never an error the person has to deal with.

## Compatibility policy (forward/backward, non-negotiable)

**Database:**
- Migrations are additive-first. A column/table is never dropped in the
  same migration that stops using it — deprecate in code, ship, confirm
  stable in production, remove the column in a *later* migration.
- Every migration must have a working `downgrade()`.
- New non-nullable columns always ship with a `server_default` so existing
  rows don't break the migration.

**API:**
- All routes are versioned under `/api/v1`. A breaking change to a v1
  contract is never made in place — it ships as `/api/v2` alongside the
  existing `/api/v1`, which keeps working until clients have migrated.
- Additive changes (new optional field, new endpoint) don't require a
  version bump and are safe to add directly to v1.

**Events (internal event bus):**
- Event schemas (`app/core/events.py`) are Pydantic models. New fields are
  added as optional with defaults. A field is never removed or repurposed
  without a new event type — old and new subscribers must both be able to
  read events from a rolling deploy without crashing.

## Project structure

```
backend/app/
  core/        # config, database, security, rbac, event bus - shared by all modules
  models/      # SQLAlchemy ORM models
  schemas/     # Pydantic request/response contracts
  services/    # business logic, DB transactions
  api/v1/      # thin route handlers, versioned
frontend/      # React PWA
```

Module build order: Auth/RBAC (done) → Config Panel → Products/Batches →
Sales → Inventory → Stock Arrivals & Stock Takes → Purchasing/Kanban →
Customers → Reports → AI Assistant → Backups → Notifications — matching
the agreed scope checklist.
