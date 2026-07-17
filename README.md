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

### Windows — double-click setup

Three `.bat` files at the repo root wrap the two paths above for Windows
users who'd rather not type commands:

- **`install.bat`** — checks Python/Node/Redis are installed (with clear
  links if not), sets up the backend virtual environment, generates a real
  `backend\.env` with a properly random encryption key, runs migrations,
  walks you through creating the first user, and installs frontend deps.
  Run this once.
- **`run.bat`** — starts Redis (if not already running), the backend, and
  the frontend, each in its own window, then opens your browser. Run this
  every time after `install.bat` has been run once.
- **`run-docker.bat`** — the Docker Compose path instead, if Docker
  Desktop is installed. Simpler, since it doesn't touch Redis/Python/Node
  on the Windows host at all — everything runs in containers.

**Honesty check:** I do not have a Windows machine in the environment I
built this in, so **these `.bat` files have never actually been
double-clicked and run** — only reasoned through carefully, line by line,
for the batch-scripting pitfalls I know about (unescaped parentheses
inside conditional blocks, delayed-vs-immediate variable expansion,
nested quoting). If one of them breaks, that's a real bug report, not
user error — tell me exactly what happened and I'll fix it.

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
ruff check . && ruff format --check . && mypy app && pytest --cov=app --cov-fail-under=80
```

As of the last full run: 214 backend tests passing, 0 failing, 93.7%
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

You never manually edit a version number. If you did, the next automated
release would immediately overwrite it based on commit history — so don't
fight the tool, write correct commit prefixes instead.

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
