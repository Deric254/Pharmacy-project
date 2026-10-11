# Change - 2026-10-11 - blank / white window on some Windows PCs

**Report:** on one Windows PC the splash and then the main window appeared as an empty white
(later dark grey) rectangle, sometimes for 10+ minutes. It happened more often with the internet
connected, and occasionally after a restart even offline. The window title was already the
pharmacy name, which the app only sets after loading its config, so the backend and the page were
alive; nothing was being drawn.

**Cause (not proven, most likely):** the graphics (GPU) drawing path failing on that PC. All
automated GUI tests ran without a GPU, so the real Windows GPU path was never exercised.

## What changed (only `electron/main.js` + one new unit test)
1. `app.disableHardwareAcceleration()` at the top of the file, before the app is ready. Every PC
   now uses the same plain software drawing path the automated tests already use.
2. `desktop.log` now records: GPU status at startup (`gpu-status`), crashed helper processes
   (`child-process-gone`), a frozen window (`window-unresponsive` / `window-responsive`), and a
   check 5 s after the page loads (`paint-check`) of whether the screen really shows anything.
3. If that check finds a page with content but one flat colour on screen (every pixel is
   compared, ignoring the right/bottom 24 px where a scrollbar can appear), it logs
   `blank-paint-detected` and reloads the page once (never more).
4. New `electron/tests/unit/gpu-policy.unit.js` fails the build if (1) is removed, moved after
   `whenReady()`, or the crash logging is removed.

No screen, API, database, workflow or business rule changed. Splash code is unchanged.

## Verified (Ubuntu, 1 CPU, virtual display, real backend, real Electron window)
- Electron unit tests: 8 passed. Electron GUI tests: 10 passed, 0 retries.
- Startup, original vs this change, 3 interleaved runs each (ready-to-show):
  original 3.6 / 3.4 / 3.3 s, this change 3.3 / 3.2 / 3.4 s. No slowdown.
- `desktop.log` shows `gpu-status` with everything `disabled_software`.
- Paint check, tested both ways in a real window: normal launch -> varied pixels, no reload;
  a full-screen flat white layer forced over the page -> `blank-paint-detected`, one reload, page
  recovered, no second check. (An earlier draft sampled pixels on a fixed stride and a stricter
  "exactly one colour" rule; both were wrong - the first only looked at 5 columns, the second
  never matched because of the scrollbar - and were replaced before this package.)
- Lint scan for unused variables / unreachable code on `electron/main.js`: clean.
- Not measurable here: the real CPU cost of software drawing on Windows. This machine has no GPU,
  so the original already drew in software and the timings above cannot show that difference.

## NOT verified - needs the Windows PC that showed the problem
- That the blank window is actually gone there. If it still happens, send
  `%APPDATA%\PharmacyERP\logs\desktop.log`; the new lines say what went wrong.
- The splash appearing late: it is created as soon as Electron is ready, so the delay is
  Electron's own start-up (and, online, Windows Defender checking the unsigned program) before
  any of this code runs. This change does not shorten it.
