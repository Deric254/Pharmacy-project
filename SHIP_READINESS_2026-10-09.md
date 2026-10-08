# Ship-readiness check - 2026-10-09

Every result below was produced by running the command against this exact tree.

| Check | Result |
|---|---|
| Backend: `ruff check`, `ruff format --check`, `mypy` (111 files) | clean |
| Backend: `bandit -ll`, `pip-audit` (shipped deps) | no findings |
| Backend: `pytest`, all 44 files, real Redis | 825 passed, 0 failed |
| Frontend: `oxlint`, `tsc -b`, `vitest`, `vite build` | clean, 119 passed, builds |
| Frontend: `npm audit --audit-level=high` | 0 vulnerabilities |
| Electron: URL-policy unit tests | pass |
| Backup/restore at 100,000 sales (301,471 rows) | backup 2.6 s, restore 4 s, sales count and revenue identical to the cent, `integrity_check` ok, peak memory ~510 MB |

## Fixed in this pass
1. **Tests failed daily 21:00-24:00 UTC** (`test_fefo.py`, `test_audit_accuracy.py`): "expires tomorrow"
   was computed in UTC, which for a Nairobi pharmacy is already today. App was right, tests wrong.
2. `test_config_validation.py` no longer depends on `REDIS_MODE` in the shell.
3. **Restore now refuses an internally inconsistent backup** (rows pointing at records that do not
   exist). Foreign keys are off during restore, so nothing used to check this. The whole restore
   rolls back and nothing is changed. New test, mutation-checked (fails with the check off).
4. **Receive stock now asks before accepting an expiry date that is today or past.** A year typo
   used to create unsellable stock owed to the supplier. The API still allows it deliberately
   (an admin can correct a batch date later); the screen now confirms first. New unit tests.
5. **Dead code removed:** backend `/updates/*` endpoints, service, schema and tests (the app asks
   GitHub directly; nothing called them) plus the `github_update_token` setting; `wait_for_db.py`
   (Docker/MySQL leftover); `download-redis.ps1` (unused); `generate_mega_test_data.py` (crashed on
   the current schema); Vite template images `hero.png`, `vite.svg`. Of 98 API paths, every
   remaining one has a caller in the frontend. There is no Android code anywhere in this project.
6. Removed `dump.rdb` (root, `electron/`) and `electron/test-results/.last-run.json`.

## Still not verified - needs you
1. **Windows:** build, installer, packaged app, Electron GUI tests, and the update-and-keep-data path.
2. **GitHub Actions workflows** have never run on GitHub (their commands pass locally).
3. **Real external services:** Google Drive backups and AI providers were only tested against stand-ins.
4. **Auto-updater does not verify the installer it downloads, and the installer is not code-signed.**
   Protect the GitHub account with two-factor authentication until this is fixed.

## Known limits (accepted)
- Backup holds the whole database in memory (~510 MB at 100k sales); fine for a single pharmacy for years.
- Restoring a backup from a *newer* app version into an older one silently ignores unknown columns.
- Loyalty points are awarded after the sale commits; a failure there is logged and the sale stands.
- Not read line by line: reports, the AI assistant, spreadsheet imports and the frontend pages
  (covered by their tests only). Read by hand: checkout, refunds, stock selection, money, login and
  reset, backup/restore, purchasing, stock-take approval, network binding and secrets.
