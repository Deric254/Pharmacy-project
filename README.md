# Pharmacy ERP

Full pharmacy POS/ERP — sales, batch/expiry-aware inventory, purchasing with
Kanban, stock takes, customers, reports, AI assistant, backups, real-time
cross-module sync via an event bus. Runs on LAN or over the internet, same
codebase. Installable on Android and Windows as a PWA. Every business-facing
detail — name, logo, slogan, currency, and choice of 4 built-in visual
themes — is configured live from Settings, not hardcoded to any one pharmacy.

## Stack
FastAPI (async) · MySQL 8 (InnoDB, ACID) · Redis (cache + event bus) ·
SQLAlchemy 2 (async) · Alembic (migrations) · React + Vite + Tailwind
(frontend, installable PWA).

## Running it for the first time

There are two ways to run this. **Path A is what's actually been run and
verified, end to end, real HTTP requests, real checkout, real refunds** —
start there if you just want to see it working. Path B is the intended
production setup (Docker + MySQL) but hasn't been run in this environment
(no Docker daemon available while building this) — the compose file and
Dockerfiles are new and correct by inspection, not yet proven by a live run.

### Path A — local dev (SQLite + Redis), fastest way to see it running

Needs: Python 3.12+, Node 20+, Redis, and `redis-server` reachable on
localhost:6379 (install via your OS package manager, e.g. `apt install
redis-server` / `brew install redis`, then `redis-server --daemonize yes`).

```bash
# 1. Backend
cd backend
pip install -r requirements.txt -r requirements-dev.txt
export DATABASE_URL="sqlite+aiosqlite:///./dev.db"
export JWT_SECRET_KEY="dev-secret-change-me"
export ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # dev only -- see note below
export REDIS_URL="redis://localhost:6379/0"
alembic upgrade head

# 2. Create the first user (interactive, prompts for a password)
python -m scripts.create_first_user --full-name "Your Name" --username admin --role ChemistOwner

# 3. Start the backend
uvicorn app.main:app --reload
# -> http://localhost:8000  (health check: http://localhost:8000/health)
```

Note: `requirements-mysql.txt` is deliberately **not** installed here. It's a
separate file containing only the MySQL driver (`asyncmy`), needed by
Docker/production but not by this SQLite-based local path or the desktop
`.exe` — kept out so a fresh machine doesn't need a C toolchain just to try
the app locally. `docker compose` (Path B below) installs it automatically.

In a second terminal:

```bash
cd frontend
npm install
npm run dev
# -> http://localhost:5173  (proxies /api to the backend automatically)
```

Open http://localhost:5173, log in with the username/password you just
created. That's a fully working system — real checkout, real stock
ledger, real refunds, real inventory adjustments.

**About `ENCRYPTION_KEY`:** it must be a base64-encoded 32-byte value.
The one above is all-zero and fine for trying the system locally, but
generate a real one for anything that matters:
`python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`

### Path B — Docker Compose (MySQL, production-shaped)

```bash
cp backend/.env.example backend/.env   # fill in real JWT_SECRET_KEY / ENCRYPTION_KEY
docker compose up -d
docker compose exec backend python -m scripts.create_first_user \
  --full-name "Your Name" --username admin --role ChemistOwner
```

- Frontend: http://localhost:8080 (nginx serves the built app and reverse-proxies `/api` to the backend — this is what makes it same-origin, which the frontend's relative `fetch('/api/...')` calls depend on)
- Backend directly: http://localhost:8000
- Migrations run automatically on container start (`docker-entrypoint.sh`) — no manual `alembic upgrade head` needed after the first `up`.

If this doesn't come up cleanly on your machine, that's exactly the kind
of thing worth reporting back rather than assuming — this path is new and
hasn't had a live run yet.

### Windows — three ways to run it, simplest first

**Option 1 — just download the `.exe`.** Every tagged release publishes
`Pharmacy-ERP-vX.Y.Z.exe` at
[github.com/Deric254/Pharmacy-project/releases](https://github.com/Deric254/Pharmacy-project/releases).
No Python, Node, or Redis to install — the executable is fully
self-contained, generates its own secrets on first run, sets up its own
database under `%LOCALAPPDATA%\PharmacyERP`, walks you through creating
the owner account the first time, then opens your browser automatically.
This is genuinely the least there is to do — one file, no setup steps.
The app also checks for newer releases itself and shows a banner when one
exists (see "Staying up to date" below).

**Option 2 — `Pharmacy-ERP.bat`**, at the repo root, if you're running
from a source checkout instead of a release download. That's the only
one you ever need to run, first time or every time after — it checks
whether setup has been done yet, does it automatically if not (installs
everything, generates a real `.env`, runs migrations, walks you through
creating the first user), then always finishes by starting the backend
and frontend and opening your browser. Nothing else to click, nothing to
remember to run first.

If Redis isn't already on your machine, the script downloads a real,
portable Redis-for-Windows build automatically (from the actively
maintained [redis-windows/redis-windows](https://github.com/redis-windows/redis-windows)
project) into `redis-portable\` next to the script — no install, no admin
rights, nothing added to PATH. That only fails if the machine has no
internet access, in which case it prints the manual Memurai fallback
instead of just stopping.

**Option 3 — `run-docker.bat`**, also at the repo root, if you'd rather
use Docker Desktop instead of installing Python/Node/Redis directly on
Windows. Same idea as Option 2, but everything runs in containers.

**Important (Options 2 & 3):** extract the `.zip` fully first (right-click
→ Extract All). Double-clicking a `.bat` file from inside Windows
Explorer's zip preview, without extracting, is the single most common way
these scripts fail — `Pharmacy-ERP.bat` checks for this specifically and
tells you if that's what happened, rather than failing with a confusing
wall of errors.

**Honesty check:** I do not have a Windows machine in the environment I
built this in, so **none of this has actually been double-clicked and run
on real Windows** — only reasoned through carefully, and where possible,
actually built and run on Linux as the closest available proxy (the exe
was genuinely compiled with PyInstaller and run end-to-end here — real
migrations, real first-user creation, real login — just producing a Linux
binary instead of the `.exe` Windows CI will produce). One real bug was
caught this way already: two of the internal helper scripts
(`backend\start-backend.bat`, `frontend\start-frontend.bat`) had no error
handling and no `pause`, so if either was ever run directly instead of
through the main script, it would fail instantly and the window would
close before anything was readable — "it blinks and closes" is exactly
what that produces. A second one was caught building the exe itself: it
crashed with a raw traceback instead of a readable message if launched
without a console attached. Both are now guarded the same way everything
else is: every failure path prints why, then pauses instead of vanishing.
The Redis auto-download (`windows\download-redis.ps1`) is the newest
piece and carries the same caveat most directly — real GitHub API calls,
real PowerShell `Expand-Archive`, checked carefully for correctness, but
never actually run on a Windows machine. If it fails, the script falls
back to printing the manual Memurai steps rather than leaving you stuck.

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
   does it get renamed with the version and uploaded as a release asset.

You never manually edit a version number. If you did, the next automated
release would immediately overwrite it based on commit history — so don't
fight the tool, write correct commit prefixes instead.

## Staying up to date (in-app)

Every screen checks `https://api.github.com/repos/Deric254/Pharmacy-project/releases/latest`
in the background (`frontend/src/lib/updateCheck.ts`) and compares it to
the running version reported by `/health`. If a newer release exists, a
banner appears with a direct link to the new `.exe` — informational only,
never forced, and any failure (offline, rate-limited, no releases yet)
just means the banner doesn't show, never an error the person has to deal
with.

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
