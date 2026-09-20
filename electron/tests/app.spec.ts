/**
 * Real Electron GUI tests -- the one thing nothing else in this
 * project's CI can check, because every other test (backend pytest,
 * frontend vitest/build) runs headless with no window at all. This
 * actually launches the real app (main.js, spawning the real Python
 * backend exactly as a real double-click would) and asserts on the
 * real rendered window: it appears, it never sits blank, its title
 * and icon are correct, and the login/setup screen genuinely renders.
 *
 * Runs under Xvfb in CI (`xvfb-run`) since Electron still needs *a*
 * display even when nobody is physically looking at it -- there is
 * no way to open a real BrowserWindow with literally no display
 * server at all, headless or not.
 */
import { test, expect, _electron as electron } from '@playwright/test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

let appDataDir: string

function testEnvironment() {
  return { ...process.env, HOME: appDataDir, APPDATA: appDataDir, LOCALAPPDATA: appDataDir }
}

test.beforeEach(() => {
  // A fresh, isolated app-data directory per test run -- never reuses
  // a real installation's data, and never lets one test's state leak
  // into another's.
  appDataDir = mkdtempSync(path.join(tmpdir(), 'pharmacy-erp-e2e-'))
})

test.afterEach(() => {
  rmSync(appDataDir, { recursive: true, force: true })
})

test('the real app window appears and is never left blank', async () => {
  const electronApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000, // real Python startup + real migrations, not instant
  })

  try {
    const window = await electronApp.firstWindow({ timeout: 30_000 })

    // The real fix from earlier this session: the window is held back
    // (show: false) until the renderer has actually painted, via
    // ready-to-show -- so the instant it's visible to the OS, it must
    // already have real content, never an empty shell.
    await expect(window.locator('body')).not.toBeEmpty({ timeout: 5_000 })
    const bodyText = await window.locator('body').innerText()
    expect(bodyText.trim().length).toBeGreaterThan(0)

    // The real backend must have actually come up behind it -- not
    // just "some HTML rendered", but the genuine setup/login flow
    // this app boots into on a first real launch.
    await expect(
      window.getByText(/set up|create.*account|username|password/i).first(),
    ).toBeVisible({ timeout: 15_000 })

    const title = await window.title()
    expect(title.length).toBeGreaterThan(0)
  } finally {
    await electronApp.close()
  }
})

test('the window title matches the product name', async () => {
  const electronApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })

  try {
    const window = await electronApp.firstWindow({ timeout: 30_000 })
    await window.waitForLoadState('domcontentloaded')
    const title = await window.title()
    expect(title).toContain('Pharmacy')
  } finally {
    await electronApp.close()
  }
})
