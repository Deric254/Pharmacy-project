# Pharmacy ERP

Full pharmacy POS/ERP — sales, batch/expiry-aware inventory, purchasing with
Kanban, stock takes, customers, reports, AI assistant, backups, real-time
cross-module sync via an event bus. Runs on LAN or over the internet, same
codebase. Installable on Android and Windows as a PWA.

## Stack
FastAPI (async) · MySQL 8 (InnoDB, ACID) · Redis (cache + event bus) ·
SQLAlchemy 2 (async) · Alembic (migrations) · React (frontend, PWA).

## Local development

```bash
cp backend/.env.example backend/.env   # then fill in real secrets
docker compose up -d mysql redis
cd backend
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Run the quality gate locally before pushing (this is exactly what CI runs):

```bash
ruff check . && ruff format --check . && mypy app && pytest --cov=app --cov-fail-under=80
```

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
