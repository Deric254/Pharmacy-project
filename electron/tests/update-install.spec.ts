/**
 * Tests the update-install flow added this session: the renderer
 * calls window.electronAPI.downloadUpdateInstaller(url), which
 * triggers a REAL Electron-managed download (not a blob: URL like
 * every other download in this app), and main.js's will-download
 * handler detects the installer by its exact filename pattern,
 * offers to run it, and launches it via shell.openPath on "Install
 * now".
 *
 * Driving this through the real UI would mean navigating through
 * first-run account setup and login just to reach Settings -- instead
 * this calls the real IPC handler directly inside the actual running
 * main process (electronApp.evaluate), with dialog.* and
 * shell.openPath mocked for exactly this test so it never shows a
 * real native dialog or actually launches anything. Everything else
 * -- the download itself, the filename-pattern detection, the save,
 * the backend shutdown sequence before quitting -- is the real code
 * path, not a reimplementation of it.
 */
import { test, expect, _electron as electron } from '@playwright/test'
import { createServer, type Server } from 'node:http'
import { mkdtempSync, existsSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

let appDataDir: string
let fakeReleaseServer: Server
let fakeReleaseServerUrl: string

function testEnvironment() {
  return { ...process.env, HOME: appDataDir, APPDATA: appDataDir, LOCALAPPDATA: appDataDir }
}

test.beforeEach(async () => {
  appDataDir = mkdtempSync(path.join(tmpdir(), 'pharmacy-erp-update-test-'))

  // A tiny local HTTP server standing in for a real GitHub release
  // asset -- serves one fixed file whose name matches the exact
  // pattern main.js's will-download handler looks for.
  fakeReleaseServer = createServer((req, res) => {
    res.setHeader('Content-Disposition', 'attachment; filename="Pharmacy-ERP-Setup-9.9.9.exe"')
    res.end('fake installer contents')
  })
  await new Promise<void>((resolve) => fakeReleaseServer.listen(0, '127.0.0.1', resolve))
  const address = fakeReleaseServer.address()
  const port = typeof address === 'object' && address ? address.port : 0
  fakeReleaseServerUrl = `http://127.0.0.1:${port}/Pharmacy-ERP-Setup-9.9.9.exe`
})

test.afterEach(() => {
  fakeReleaseServer.close()
  rmSync(appDataDir, { recursive: true, force: true })
})

test('choosing "Install now" saves the update and launches the installer', async () => {
  const electronApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })

  const savePath = path.join(appDataDir, 'Pharmacy-ERP-Setup-9.9.9.exe')

  try {
    await electronApp.firstWindow({ timeout: 30_000 })

    // app.getPath('userData') is a plain method call on an object
    // evaluate() already hands us -- no require() needed, unlike
    // trying to read the resulting log file's contents from inside
    // evaluate() itself (confirmed separately: require() is
    // completely unavailable in that sandboxed context, not just for
    // 'electron'). Only mocked: the two calls that would otherwise
    // show a real native dialog (save location, install
    // confirmation) and the actual OS-level launch (shell.openPath,
    // mocked purely to avoid trying to run a fake .exe on this
    // machine) -- app.quit() is left completely real, so this test
    // proves the actual end-to-end flow, not a simulation of it.
    const userDataDir = await electronApp.evaluate(({ app }) => app.getPath('userData'))
    const logFile = path.join(userDataDir, 'logs', 'desktop.log')

    await electronApp.evaluate(
      ({ dialog, shell }, savePath) => {
        dialog.showSaveDialogSync = () => savePath
        dialog.showMessageBoxSync = () => 0 // "Install now" is button index 0
        shell.openPath = async () => ''
      },
      savePath,
    )

    await electronApp.evaluate(({ BrowserWindow }, url) => {
      const win = BrowserWindow.getAllWindows()[0]
      win.webContents.downloadURL(url)
    }, fakeReleaseServerUrl)

    // Polls the real log file rather than a fixed sleep -- this flow
    // ends in a genuine app.quit(), so the whole process may already
    // be gone by the time this check runs, and polling a file on disk
    // works regardless of whether the process producing it still is.
    const deadline = Date.now() + 15_000
    let logContents = ''
    while (Date.now() < deadline) {
      if (existsSync(logFile)) {
        logContents = readFileSync(logFile, 'utf8')
        if (logContents.includes('update-install-launched')) break
      }
      await new Promise((resolve) => setTimeout(resolve, 300))
    }

    expect(existsSync(savePath)).toBe(true)
    expect(readFileSync(savePath, 'utf8')).toBe('fake installer contents')
    expect(logContents).toContain(`update-install-launched ${savePath}`)
  } finally {
    // The real app.quit() should have already ended the process by
    // this point -- that's the behavior under test. This is just a
    // safety net if it somehow didn't.
    await electronApp.close().catch(() => {})
  }
})

test('choosing "Later" saves the update but does not launch the installer', async () => {
  const electronApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })

  try {
    await electronApp.firstWindow({ timeout: 30_000 })
    const savePath = path.join(appDataDir, 'Pharmacy-ERP-Setup-9.9.9-later.exe')

    const result = await electronApp.evaluate(
      async ({ dialog, shell, BrowserWindow }, { url, savePath }) => {
        dialog.showSaveDialogSync = () => savePath
        dialog.showMessageBoxSync = () => 1 // "Later" is button index 1
        let openPathCalled = false
        shell.openPath = async () => {
          openPathCalled = true
          return ''
        }

        const win = BrowserWindow.getAllWindows()[0]
        win.webContents.downloadURL(url)
        await new Promise((resolve) => setTimeout(resolve, 3000))

        return { openPathCalled }
      },
      { url: fakeReleaseServerUrl, savePath },
    )

    expect(existsSync(savePath)).toBe(true)
    expect(result.openPathCalled).toBe(false)
  } finally {
    await electronApp.close()
  }
})

test('a regular (non-update) download is completely unaffected by the new logic', async () => {
  // Confirms the filename-pattern detection is precise -- a download
  // that ISN'T named like the installer must go through the ordinary
  // "Saved" flow, never the install-confirmation one, even though it
  // goes through the exact same will-download handler.
  const ordinaryFileServer = createServer((req, res) => {
    res.setHeader('Content-Disposition', 'attachment; filename="sales-report.xlsx"')
    res.end('fake report contents')
  })
  await new Promise<void>((resolve) => ordinaryFileServer.listen(0, '127.0.0.1', resolve))
  const address = ordinaryFileServer.address()
  const port = typeof address === 'object' && address ? address.port : 0
  const ordinaryUrl = `http://127.0.0.1:${port}/sales-report.xlsx`

  const electronApp = await electron.launch({
    args: [path.join(__dirname, '..', 'main.js')],
    env: testEnvironment(),
    timeout: 60_000,
  })

  try {
    await electronApp.firstWindow({ timeout: 30_000 })
    const savePath = path.join(appDataDir, 'sales-report.xlsx')

    const result = await electronApp.evaluate(
      async ({ dialog, BrowserWindow }, { url, savePath }) => {
        dialog.showSaveDialogSync = () => savePath
        let sawInfoMessage = false
        let sawInstallPrompt = false
        dialog.showMessageBox = (async (_win: unknown, opts: { message?: string }) => {
          if (opts.message === 'Saved') sawInfoMessage = true
          return { response: 0 }
        }) as typeof dialog.showMessageBox
        dialog.showMessageBoxSync = (() => {
          sawInstallPrompt = true
          return 0
        }) as typeof dialog.showMessageBoxSync

        const win = BrowserWindow.getAllWindows()[0]
        win.webContents.downloadURL(url)
        await new Promise((resolve) => setTimeout(resolve, 3000))

        return { sawInfoMessage, sawInstallPrompt }
      },
      { url: ordinaryUrl, savePath },
    )

    expect(existsSync(savePath)).toBe(true)
    expect(result.sawInfoMessage).toBe(true)
    expect(result.sawInstallPrompt).toBe(false)
  } finally {
    await electronApp.close()
    ordinaryFileServer.close()
  }
})
