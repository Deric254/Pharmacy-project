/**
 * Tests for the orphaned-backend-process protections.
 *
 * Honest scope: the Windows-specific cleanup in main.js
 * (killPreviousBackendIfAny -- taskkill / PowerShell) no-ops on any other
 * platform, so nothing here proves those commands work; only a real Windows
 * machine can. What IS verified here is the platform-independent behavior
 * around it:
 *   - a normal close, and a close while the backend is still starting, both
 *     leave no backend process behind (the second must also never block on an
 *     error dialog);
 *   - a second launch is refused by the single-instance lock instead of
 *     spawning a second backend;
 *   - the app still starts after a previous session was killed without
 *     running any cleanup, which leaves an orphaned backend behind.
 */
import { test, expect, _electron as electron, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

let appDataDir: string

function testEnvironment() {
  return { ...process.env, HOME: appDataDir, APPDATA: appDataDir, LOCALAPPDATA: appDataDir }
}

test.beforeEach(() => {
  appDataDir = mkdtempSync(path.join(tmpdir(), 'pharmacy-erp-bughunt-'))
})

test.afterEach(() => {
  rmSync(appDataDir, { recursive: true, force: true })
})

// PIDs of real running `python desktop_main.py` processes, matched on the
// full command line -- not the Electron shell, not this test runner. Run
// without a shell on purpose: a `sh -c 'pgrep -f ...'` wrapper carries the
// pattern in its own command line, gets counted, and makes the result
// never zero -- which turns every "a backend exists" assertion into one
// that cannot fail. pgrep exits 1 when nothing matches.
function posixBackendPids(): number[] {
  try {
    return execFileSync('pgrep', ['-f', 'desktop_main\\.py'], { encoding: 'utf8' })
      .split('\n')
      .filter(Boolean)
      .map(Number)
  } catch {
    return []
  }
}

// firstWindow() resolves at the splash screen, before the backend has even
// been spawned (see startApp() in main.js). The same window navigates to the
// real app only once the backend is healthy, so that navigation is the
// honest signal that the backend is up.
async function waitForBackendReady(page: Page): Promise<void> {
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 60_000 })
}

function countBackendProcesses(): number {
  try {
    if (process.platform === 'win32') {
      const command =
        "$items=@(Get-CimInstance Win32_Process | " +
        "Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and " +
        "$_.CommandLine -match 'desktop_main\\.py' }); $items.Count"
      const output = execFileSync('powershell.exe', ['-NoProfile', '-Command', command])
        .toString()
        .trim()
      return output ? parseInt(output, 10) : 0
    }

    return posixBackendPids().length
  } catch {
    return 0
  }
}

test('closing the app normally leaves zero backend processes behind', async () => {
  const before = countBackendProcesses()

  const electronApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })
  await waitForBackendReady(await electronApp.firstWindow({ timeout: 30_000 }))
  expect(countBackendProcesses()).toBeGreaterThan(before) // the backend genuinely started

  await electronApp.close()

  // Polled rather than a fixed sleep: close() resolving doesn't guarantee the
  // OS process table has caught up, and this fails only if it never does.
  await expect.poll(countBackendProcesses, { timeout: 5_000 }).toBe(before)
})

test('closing the app while it is still starting quits promptly and leaves zero backend processes behind', async () => {
  // Regression: closing the app kills the backend on purpose, and that used to
  // be reported as a startup failure -- whose modal error dialog then blocked
  // the quit forever. Nobody can dismiss it under Xvfb, so the failure shows up
  // here as a timeout on close().
  test.setTimeout(30_000)
  const before = countBackendProcesses()

  const electronApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })
  const page = await electronApp.firstWindow({ timeout: 30_000 })

  // Precondition, asserted rather than assumed: the backend has been spawned
  // but the window has not yet moved on from the splash screen. Otherwise this
  // test would silently be exercising an ordinary close.
  await expect.poll(countBackendProcesses).toBeGreaterThan(before)
  expect(page.url()).toMatch(/splash\.html$/)

  await electronApp.close()
  await expect.poll(countBackendProcesses, { timeout: 5_000 }).toBe(before)
})

function killAllBackendProcesses(): void {
  // Avoids pkill entirely -- confirmed independently that pkill
  // itself hangs indefinitely in this specific sandbox regardless of
  // arguments, which is an environment quirk unrelated to the actual
  // fix (main.js never uses pkill anywhere; Windows uses taskkill).
  // pgrep -l works reliably here (used successfully by
  // countBackendProcesses above), so this lists PIDs the same way and
  // kills each directly via Node instead of shelling out to pkill.
  try {
    if (process.platform === 'win32') {
      const command =
        "Get-CimInstance Win32_Process | " +
        "Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and " +
        " $_.CommandLine -match 'desktop_main\\.py' } | " +
        'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'
      execFileSync('powershell.exe', ['-NoProfile', '-Command', command])
      return
    }

    for (const pid of posixBackendPids()) {
      try {
        process.kill(pid, 'SIGKILL')
      } catch {
        // Already gone -- fine.
      }
    }
  } catch {
    // Best-effort only.
  }
}

test('a second launch while the first is still running does not spawn a second backend', async () => {
  // Tests the single-instance-lock directly -- this is standard
  // Electron API behavior, not a Windows-specific mechanism, so this
  // genuinely proves the real thing, not a platform-limited stand-in
  // for it.
  const before = countBackendProcesses()
  const firstApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })
  await waitForBackendReady(await firstApp.firstWindow({ timeout: 30_000 }))

  const countWithOneRunning = countBackendProcesses()
  expect(countWithOneRunning).toBeGreaterThan(before)

  // A second launch attempt, same appDataDir (same "installation"),
  // while the first is still fully alive. The CORRECT behavior is for
  // this second instance to lose the single-instance-lock and quit
  // almost immediately -- so immediately that Playwright's own
  // launch() handshake can legitimately fail to complete before the
  // process is already gone. Both a clean return AND a launch
  // rejection are consistent with the lock working correctly; only
  // the process-count assertion below actually distinguishes success
  // from failure.
  try {
    const secondApp = await electron.launch({
      args: [path.join(__dirname, '..', 'main.js')],
      env: testEnvironment(),
      timeout: 15_000,
    })
    await secondApp.close().catch(() => {})
  } catch {
    // Expected when the lock loser quits before Playwright can attach
    // -- not a failure on its own, see comment above.
  }

  // A lock loser that wrongly started up would spawn its backend within moments
  // of launching. Absence can't be polled for, so allow that time first.
  await new Promise((resolve) => setTimeout(resolve, 1500))
  expect(countBackendProcesses()).toBe(countWithOneRunning)

  await firstApp.close()
})

test('the app recovers cleanly after a previous session was killed ungracefully', async () => {
  // Simulates exactly what an imperfect Windows shutdown leaves
  // behind: a backend process that outlives the app that spawned it,
  // because the parent was terminated (crash, force-quit, killed by
  // the OS) before it could run its own cleanup code at all -- this
  // bypasses stopBackend() entirely on purpose, the same way a real
  // crash would.
  const before = countBackendProcesses()
  const firstApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })
  await waitForBackendReady(await firstApp.firstWindow({ timeout: 30_000 }))
  expect(countBackendProcesses()).toBeGreaterThan(before)

  // SIGKILL the Electron process directly -- not app.close(), which
  // goes through the app's own graceful-shutdown code (the very thing
  // being bypassed here). This is the actual PID Playwright spawned.
  const electronPid = firstApp.process().pid
  if (electronPid) {
    process.kill(electronPid, 'SIGKILL')
  }
  await new Promise((resolve) => setTimeout(resolve, 1000))

  // On Linux, a killed parent's un-detached child is re-parented to
  // init and keeps running -- the orphan this test needs to exist in
  // order to prove anything. If this assertion ever fails, it means
  // the orphan wasn't created and the test below isn't actually
  // testing recovery from anything.
  expect(countBackendProcesses()).toBeGreaterThan(before)

  // Now launch again, same appDataDir, with a genuine orphan already
  // holding whatever port the old backend was on. The app must still
  // come up successfully -- it should not hang forever behind the
  // orphan, and it should not silently fail.
  const secondApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })
  try {
    const window = await secondApp.firstWindow({ timeout: 45_000 })
    await waitForBackendReady(window)
    // The genuine first-run setup/login screen -- not just "some page".
    await expect(
      window.getByText(/set up|create.*account|username|password/i).first(),
    ).toBeVisible({ timeout: 30_000 })
  } finally {
    await secondApp.close()
    // Best-effort cleanup of the orphan this test deliberately
    // created, so it can't pollute any test that runs after this one.
    killAllBackendProcesses()
  }
})
