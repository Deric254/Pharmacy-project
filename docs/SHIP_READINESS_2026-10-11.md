# Ship-readiness check - 2026-10-11

Every result in the first two sections was produced by running the command against the exact
package that is being pushed, unpacked fresh (Ubuntu, 1 CPU core, virtual display, real Redis,
real Python backend, real Electron window). Anything not re-run today is listed separately below.

| Check | Result |
|---|---|
| Backend: `ruff check`, `ruff format --check`, `mypy` (119 files) | clean |
| Backend: `pytest`, real Redis, coverage gate 80% | 828 passed, 0 failed, 92.5% coverage |
| Frontend: `oxlint` | 0 warnings, 0 errors |
| Frontend: `tsc -b && vite build` | builds |
| Frontend: `vitest` | 119 passed (14 files) |
| Electron GUI tests (Playwright, real window, real backend, virtual display) | 10 passed, 0 retries, 0 flaky |

## Fixed in this pass
1. **5 of 10 Electron GUI tests failed in CI run #164** (window title, never-blank, "Install now",
   "Later", untrusted update address). Cause: the app opens a small splash window before the real
   one, and the tests used `firstWindow()`, which returned the splash. The splash has no login
   text and closes as soon as the real window is ready, so tests failed with "session closed" /
   "page has been closed". Fix is in the tests only: new `electron/tests/helpers.ts`
   (`mainWindow()` waits for the window served from `http://127.0.0.1:<port>`), used in
   `app.spec.ts` and `update-install.spec.ts`. App behaviour is unchanged by this fix.
2. **Flaky test teardown.** `orphan-process.spec.ts` once failed in `afterEach` (not in its
   checks) because deleting the temp folder raced with the still-running orphan backend. All three
   spec files now retry the delete (`maxRetries: 5, retryDelay: 200`). 10 of 10 passed afterwards.
3. **Startup splash now shows the logo, "Pharmacy ERP", "Starting, please wait..." and a loading
   bar.** Icon is a 4.5 KB image embedded in the page; animation is CSS only; no file reads, no
   network, no scripts. It is still created first in `startApp()` and closes exactly as before
   (on `ready-to-show`, with the 10-second fallback). Respects "reduce motion". Seen and checked
   on screen in a real run: it appears about 1 s after launch and the bar moves between frames.
   Only the splash page and its window height changed in `electron/main.js`.
4. **Dead code removed:** `BUILT_IN_THEMES` in `backend/app/schemas/business_config.py` (unreferenced
   duplicate of the `ThemeName` type); `frontend/public/icons.svg` (unused Vite starter sprite, zero
   references). Every other vulture/export-scan finding was checked against all references and is
   a false positive (route handlers, library hooks, types used in their own file), so it was left.
   A stray local `electron/test-results/.last-run.json` was also removed (already git-ignored).

## Splash: measured effect on startup (needs your confirmation)
Original app vs this package, 3 interleaved runs each, same machine, virtual display, no GPU:

| | Splash visible after launch | Real window content visible |
|---|---|---|
| Original | about 0.95 s | about 4.69 s |
| This package | about 0.98 s | about 4.99 s |

- The real window finished loading about 0.3 s (about 6%) later in every pair. Turning the splash
  animation off recovered only about half of that, within run-to-run noise, so the cause is not isolated.
- Handoff: the splash closes within about 10 ms of the real window's page loading; app content
  follows about 0.2 s later (the window is the same cream colour as the splash in between).
- This machine has one core and no graphics card, so it may overstate the cost. **Confirm on Windows**
  by comparing the `ready-to-show` and `page-load-finished` lines in `desktop.log` before and after.
  If there is a real slowdown, the splash change in `electron/main.js` can be dropped on its own;
  items 1, 2 and 4 do not depend on it.

## Scale test (2026-10-11, synthetic data, 1 CPU core / 4 GB RAM machine)
Real desktop backend, real owner login, data generated directly in the database: 8,000 products,
100,000 customers, and up to 1,500,000 sales (3.6 million line items, 1.5 million audit rows) =
1.1 GB. In this data 100,000 sales took about 86 MB. Real data may be larger. Absolute times are
pessimistic (1 core); the way they grow is what matters.

**Stays fast at 1.1 GB:** startup 6 s; checkout p50 61 ms / p95 78 ms, 0 errors in 150; 60 checkouts
during a 45 s report: 0 errors, slowest 192 ms; sales page, sale detail, product search, barcode
lookup, customer lookup, low stock, audit log all 0.01-0.2 s; copy made before an update 3.3 s;
database integrity check OK with 0 foreign-key problems (also after the crash below).

**Backup runs out of memory.** Peak server memory during a backup: 86 MB database -> 1.1 GB;
196 MB -> 2.3 GB; 379 MB -> the operating system killed the app (needed over 4 GB); 1.1 GB -> killed.
Roughly 12 times the database size. The data was not damaged. Restore and the migration export use
the same all-in-memory approach and were not tested at scale. On a 4 GB PC this fails somewhere
around 300 MB (about 350,000 sales in this data).

**All-history reports slow down with data (seconds, measured at 100k / 500k / 1.5M sales):**
dashboard over all history 1.7 / 7.7 / 37; profit by product 1.3 / 7.4 / 36; revenue by category
0.9 / 5.7 / 30; revenue trend, all history 0.7 / 4.1 / 21; inventory reconcile 0.8 / 2.5 / 10.9.
The home dashboard for today / this month: 0.38 / 0.43 s at 100k, 0.62 / 1.05 s at 1.5M.
Selling stays responsive while these run.

**Large unpaginated responses at 1.5M sales:** customer lifetime value 11 MB, seasonal trends 8 MB,
inventory reconcile 4.6 MB. The all-customers list takes 3.3 s (100,000 customers); a deep sales
page (offset 750,000) takes 2.6 s.

**Not tested:** a click-through of every screen, refunds, stock-takes and purchase orders at scale,
restore at scale, Windows.

## Not re-run in this pass (carried from the 2026-10-09 report)
Backend `bandit -ll` and `pip-audit`, frontend `npm audit`, Electron URL-policy unit tests,
backup/restore at 100,000 sales, and the "every API path has a frontend caller" check were not
re-run against this tree. Nothing in this pass touches those areas; the GUI test that rejects an
update address outside this project's own release did pass.

## Still not verified - needs you
1. **Windows:** build, installer, packaged app, update-and-keep-data path, and the long blank-screen
   wait seen on Windows (not reproduced here; startup took about 5 s on Linux).
2. **GitHub Actions:** in run #164 the backend and frontend jobs passed on GitHub; the Electron
   job failed before fix 1. Push and confirm all three jobs are green.
3. **Real external services:** Google Drive backups and AI providers were only tested against stand-ins.
4. **Auto-updater does not verify the installer it downloads, and the installer is not code-signed.**
   Protect the GitHub account with two-factor authentication until this is fixed.

## Known limits (accepted, unchanged since 2026-10-09)
- **Backup needs about 12 times the database size in RAM** (measured, see Scale test below). This is NOT fine long-term and is the top open item.
- Restoring a backup from a *newer* app version into an older one silently ignores unknown columns.
- Loyalty points are awarded after the sale commits; a failure there is logged and the sale stands.
- Not read line by line: reports, the AI assistant, spreadsheet imports and the frontend pages
  (covered by their tests only). Read by hand: checkout, refunds, stock selection, money, login and
  reset, backup/restore, purchasing, stock-take approval, network binding and secrets.
