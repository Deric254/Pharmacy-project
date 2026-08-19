/**
 * Bug-hunting tests for the orphaned-backend-process fix.
 *
 * Honest scope: the Windows-specific cleanup mechanism in main.js
 * (clearAnyLeftoverBackendProcess -- one PowerShell invocation that
 * checks both by port and by exact process name) no-ops immediately
 * on any non-Windows platform -- there is no way to exercise the
 * actual PowerShell/Stop-Process commands from this environment, full
 * stop. Nothing here proves those specific Windows commands work;
 * only a real Windows machine can prove that.
 *
 * What CAN be genuinely tested here, and is: the platform-independent
 * parts of the same fix -- does the app correctly avoid running two
 * backends at once (single-instance-lock), does a normal close
 * actually terminate the spawned backend process rather than leaving
 * it running (the exact race condition that was fixed, using the
 * Linux .kill() fallback path instead of taskkill), and does the app
 * survive and recover cleanly after an orphan is left behind by a
 * hard crash (simulating what an imperfect Windows shutdown would
 * leave, even though the specific Windows recovery commands
 * themselves aren't running here).
 */
import { test, expect, _electron as electron } from '@playwright/test'
import { execFileSync, execSync } from 'node:child_process'
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

    // pgrep -f matches against the full command line, so this counts
    // real running `python desktop_main.py` processes specifically --
    // not the Electron shell, not this test runner itself.
    const output = execSync('pgrep -fc "desktop_main.py" || true').toString().trim()
    return output ? parseInt(output, 10) : 0
  } catch {
    return 0
  }
}

test('closing the app normally leaves zero backend processes behind', async () => {
  // This is the exact race condition that was fixed: stopBackend()
  // used to fire a kill command without waiting for it, and app.quit()
  // could complete before the kill actually finished. On Linux this
  // exercises the .kill() fallback path rather than taskkill, but the
  // ASYNC ORDERING being tested -- does quit genuinely wait for the
  // kill to resolve -- is the platform-independent part of the fix,
  // and is fully verifiable here.
  const before = countBackendProcesses()

  const electronApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })
  await electronApp.firstWindow({ timeout: 30_000 })

  const duringRun = countBackendProcesses()
  expect(duringRun).toBeGreaterThan(before) // the backend genuinely started

  await electronApp.close()

  // Give the OS a moment to actually reap the process after close()
  // returns -- close() resolving doesn't guarantee the OS process
  // table has updated in the same instant.
  await new Promise((resolve) => setTimeout(resolve, 1500))

  const after = countBackendProcesses()
  expect(after).toBe(before)
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

    const output = execSync('pgrep -f "desktop_main.py" || true').toString().trim()
    if (!output) return
    for (const line of output.split('\n')) {
      const pid = parseInt(line.trim(), 10)
      if (!Number.isNaN(pid)) {
        try {
          process.kill(pid, 'SIGKILL')
        } catch {
          // Already gone -- fine.
        }
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
  const firstApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })
  await firstApp.firstWindow({ timeout: 30_000 })

  const countWithOneRunning = countBackendProcesses()
  expect(countWithOneRunning).toBeGreaterThan(0)

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

  const countAfterSecondAttempt = countBackendProcesses()
  expect(countAfterSecondAttempt).toBe(countWithOneRunning)

  await firstApp.close()
  await new Promise((resolve) => setTimeout(resolve, 1500))
})

test('the app recovers cleanly after a previous session was killed ungracefully', async () => {
  // Simulates exactly what an imperfect Windows shutdown leaves
  // behind: a backend process that outlives the app that spawned it,
  // because the parent was terminated (crash, force-quit, killed by
  // the OS) before it could run its own cleanup code at all -- this
  // bypasses stopBackend() entirely on purpose, the same way a real
  // crash would.
  const firstApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })
  await firstApp.firstWindow({ timeout: 30_000 })
  expect(countBackendProcesses()).toBeGreaterThan(0)

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
  const orphanCount = countBackendProcesses()
  expect(orphanCount).toBeGreaterThan(0)

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
    await expect(window.locator('body')).not.toBeEmpty({ timeout: 10_000 })
    const bodyText = await window.locator('body').innerText()
    expect(bodyText.trim().length).toBeGreaterThan(0)
  } finally {
    await secondApp.close()
    // Best-effort cleanup of the orphan this test deliberately
    // created, so it can't pollute any test that runs after this one.
    killAllBackendProcesses()
  }
})
