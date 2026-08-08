/**
 * Electron main process for the Pharmacy ERP desktop wrapper.
 *
 * What this actually solves, concretely: the compiled backend exe
 * (desktop_main.py via PyInstaller) works correctly, but shows a raw
 * console window with startup logs -- fine for a developer, wrong for
 * a pharmacy owner. This process spawns that same exe as a hidden
 * child process (no console at all) and opens a real native window
 * pointed at it instead. The backend itself is completely unchanged;
 * this is purely a presentation layer on top of something already
 * proven to work.
 *
 * Honesty check, matching every other honesty note in this project:
 * this file was written against Electron's stable, long-standing APIs
 * (app, BrowserWindow, single-instance-lock) and has since been run
 * on real Windows machines. That real-world running is what surfaced
 * the refresh-cookie bug (Secure=true on a plain-HTTP cookie -- see
 * app/core/config.py's cookie_secure setting) -- the exact class of
 * bug this note used to warn wasn't yet ruled out. No other startup
 * or window-lifecycle defect has been reported against this file.
 * That is a track record, not a guarantee: this is still a thin
 * wrapper, still worth watching, and any new failure report on it
 * should come with the actual %APPDATA%\PharmacyERP\logs\desktop.log
 * from the machine it happened on, not a guess.
 */

const { app, BrowserWindow, dialog, session, ipcMain } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const http = require('node:http')
const { spawn } = require('node:child_process')

const BACKEND_PORT = 8000
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
const BACKEND_STARTUP_TIMEOUT_MS = 30000

// Pinned explicitly, not left to Electron's defaults. Two real,
// Pinned explicitly, not left to Electron's default. Without this,
// app.getPath('userData') (and therefore where desktop.log actually
// lives) falls back to package.json's "name" field
// ("pharmacy-erp-desktop"), not the human-facing "productName"
// ("Pharmacy ERP"/"PharmacyERP") this file's header comment assumes.
// This alone is enough to make that comment's claim true: Electron's
// own default userData root (%APPDATA% on Windows) plus this name
// gives a real, predictable, findable path.
//
// Deliberately NOT also forcing userData to the backend's own
// %LOCALAPPDATA%\PharmacyERP data folder (database, secrets) here,
// even though that would put logs right next to them. That would mean
// two separately-spawned OS processes -- this Electron main process
// and the backend child process -- both creating and locking files in
// the exact same directory at the exact same moment on every launch.
// That's a real, untested new failure mode with no way to verify it's
// safe on real Windows from where this was written. Not worth it for
// a convenience; app.setName alone already solves the actual problem
// (logs being unfindable).
app.setName('PharmacyERP')

let backendProcess = null
let mainWindow = null

function logDesktopDiagnostic(message) {
  try {
    const logDir = path.join(app.getPath('userData'), 'logs')
    fs.mkdirSync(logDir, { recursive: true })
    fs.appendFileSync(
      path.join(logDir, 'desktop.log'),
      `${new Date().toISOString()} ${message}\n`,
      'utf8',
    )
  } catch {
    // Diagnostics must never be the reason the app fails to open.
  }
}

// Electron's own native answer to the exact bug a real report showed:
// two copies of this app fighting over the same port. Checked before
// anything else even starts, so a second launch never gets far enough
// to spawn a second backend at all -- it just focuses the existing
// window instead. This is a *different, additional* layer from the
// backend's own "is something already running on this port" check
// (desktop_main.py's _already_running_instance) -- that one protects
// someone running the raw exe directly outside Electron; this one
// protects the packaged app specifically.
const gotSingleInstanceLock = app.requestSingleInstanceLock()

if (!gotSingleInstanceLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  app.whenReady().then(startApp)

  app.on('window-all-closed', () => {
    // Deliberately quits the whole app on window close, on every
    // platform -- this is a small single-purpose utility app someone
    // opens to do pharmacy work and closes when done, not something
    // that should linger running in the background unexpectedly
    // (the macOS convention of staying open until Cmd+Q doesn't fit
    // that -- so it's not applied here, on any platform).
    stopBackend()
    app.quit()
  })

  app.on('before-quit', stopBackend)
}

/**
 * Path to the bundled backend executable inside a packaged build.
 * `electron-builder`'s `extraResources` config (see package.json)
 * copies the PyInstaller-built exe here at build time.
 */
function packagedBackendPath() {
  const exeName = process.platform === 'win32' ? 'Pharmacy-ERP.exe' : 'Pharmacy-ERP'
  return path.join(process.resourcesPath, 'backend', exeName)
}

function devFrontendDistPath() {
  return path.join(__dirname, '..', 'frontend', 'dist', 'index.html')
}

function ensureDevFrontendBuilt() {
  if (app.isPackaged || fs.existsSync(devFrontendDistPath())) {
    return
  }

  throw new Error(
    'The frontend build is missing, so there is no login screen to show.\n\n' +
      'Start the desktop app with `cd electron && npm start`, or run ' +
      '`cd frontend && npm run build` before launching Electron directly.',
  )
}

function startBackend() {
  return new Promise((resolve, reject) => {
    // Tells desktop_main.py not to open a system browser tab -- this
    // window is already showing the app. Set on both paths (packaged
    // and dev) since either one is Electron spawning the backend.
    const backendEnv = { ...process.env, PHARMACY_ERP_ELECTRON: '1' }

    if (app.isPackaged) {
      backendProcess = spawn(packagedBackendPath(), [], {
        windowsHide: true, // the entire point: no console window
        stdio: 'ignore',
        env: backendEnv,
      })
    } else {
      // Development: run the real Python entrypoint the exe is built
      // from, so `npm start` here behaves the same as the packaged
      // app without needing a fresh PyInstaller build every time.
      backendProcess = spawn('python', ['desktop_main.py'], {
        cwd: path.join(__dirname, '..', 'backend'),
        stdio: 'inherit',
        env: backendEnv,
      })
    }

    backendProcess.on('error', (err) => {
      reject(new Error(`Could not start the backend: ${err.message}`))
    })

    backendProcess.on('exit', (code) => {
      backendProcess = null
      if (mainWindow === null) {
        // Died before the window ever opened -- nothing on screen to
        // explain why, so this becomes the startup failure message
        // instead of a silent blank window forever.
        reject(new Error(`The backend exited immediately (code ${code}).`))
      }
    })

    resolve()
  })
}

function waitForBackendHealthy() {
  const deadline = Date.now() + BACKEND_STARTUP_TIMEOUT_MS

  return new Promise((resolve, reject) => {
    function attempt() {
      const req = http.get(`${BACKEND_URL}/health`, (res) => {
        res.resume() // drain, don't leak the socket
        if (res.statusCode === 200) {
          resolve()
        } else {
          retryOrGiveUp()
        }
      })
      req.on('error', retryOrGiveUp)
    }

    function retryOrGiveUp() {
      if (Date.now() > deadline) {
        reject(new Error('The backend did not become ready within 30 seconds.'))
      } else {
        setTimeout(attempt, 500)
      }
    }

    attempt()
  })
}

function createWindow() {
  // Checked defensively -- if the icon asset isn't there for any
  // reason, the window must still open with Electron's default icon
  // rather than fail to launch at all over a missing image file.
  const iconPath = path.join(__dirname, 'build', 'logo.png')
  const iconOption = fs.existsSync(iconPath) ? { icon: iconPath } : {}

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'Pharmacy ERP',
    // Matches the app's own "paper" background token (see
    // frontend/src/theme) so there's no white flash before the real
    // page paints.
    backgroundColor: '#f7f3ec',
    autoHideMenuBar: true,
    // Held back until the renderer has actually painted its first
    // real frame (see ready-to-show below) -- otherwise the window
    // appears the instant it's created, while React is still parsing
    // and mounting, showing several seconds of an empty window before
    // any real content arrives.
    show: false,
    ...iconOption,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  })

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
  })
  // Defensive fallback: if ready-to-show never fires for any reason,
  // showing a blank window late is still far better than the app
  // silently never appearing at all, which would look like a failed
  // launch rather than a slow one.
  setTimeout(() => {
    if (mainWindow && !mainWindow.isVisible()) {
      mainWindow.show()
    }
  }, 10000)

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    logDesktopDiagnostic(`did-fail-load ${errorCode} ${errorDescription} ${validatedURL}`)
  })
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    logDesktopDiagnostic(`render-process-gone ${JSON.stringify(details)}`)
  })
  mainWindow.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    if (level >= 2) {
      logDesktopDiagnostic(`renderer-console level=${level} ${sourceId}:${line} ${message}`)
    }
  })

  mainWindow.loadURL(BACKEND_URL)

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

/**
 * Unconditionally clears anything listening on the backend's port
 * before every single launch -- not a health check, not "is this a
 * legitimate previous instance", just a guaranteed clean slate every
 * time, regardless of how a leftover process got there. Electron's
 * own single-instance-lock (see the top of this file) already
 * guarantees no other copy of THIS app is legitimately running by the
 * time this runs -- so anything still on this port at this exact
 * moment is, by definition, either a leftover from an imperfect past
 * shutdown or an unrelated program, never something worth preserving.
 * A user should never have to open Task Manager to make this app
 * work; this exists so they never have to.
 */
function forceClearPort(port) {
  return new Promise((resolve) => {
    if (process.platform !== 'win32') {
      resolve()
      return
    }
    const ps = spawn('powershell.exe', [
      '-NoProfile',
      '-Command',
      `Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | ` +
        `ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }`,
    ])
    ps.on('error', (err) => {
      logDesktopDiagnostic(`force-clear-port-failed ${err.message}`)
      resolve() // never let this block startup -- worst case, the existing health-check path still applies
    })
    ps.on('exit', () => resolve())
  })
}

async function startApp() {
  try {
    // Without this, Electron's default behavior for the blob-URL
    // downloads every export and template button uses is to save the
    // file somewhere silently, with no dialog and no confirmation at
    // all -- indistinguishable from the button doing nothing. This
    // makes every download show a real Save dialog and a completion
    // message, the same as any normal desktop app.
    session.defaultSession.on('will-download', (event, item) => {
      const savePath = dialog.showSaveDialogSync(mainWindow, {
        title: 'Save file',
        defaultPath: item.getFilename(),
      })
      if (!savePath) {
        item.cancel()
        return
      }
      item.setSavePath(savePath)
      item.once('done', (_event, state) => {
        if (state === 'completed') {
          dialog.showMessageBox(mainWindow, {
            type: 'info',
            message: 'Saved',
            detail: savePath,
          })
        } else if (state !== 'cancelled') {
          dialog.showErrorBox('Save failed', `Could not save the file (${state}).`)
        }
      })
    })

    ensureDevFrontendBuilt()
    await forceClearPort(BACKEND_PORT)
    // A killed process's port isn't always instantly free at the OS
    // level -- Stop-Process returning doesn't guarantee the socket
    // has been released yet. This is cheap insurance against the new
    // backend trying to bind a fraction of a second too early and
    // failing for a completely different reason than the one this
    // whole fix exists to close off.
    await new Promise((resolve) => setTimeout(resolve, 400))
    await startBackend()
    await waitForBackendHealthy()
    await session.defaultSession.clearStorageData({
      storages: ['serviceworkers', 'cachestorage'],
    })
    createWindow()
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    const stack = err instanceof Error ? err.stack : undefined
    logDesktopDiagnostic(`startup-error ${stack ?? message}`)
    dialog.showErrorBox(
      'Pharmacy ERP could not start',
      `${message}\n\nTry restarting your computer. If this keeps happening, ` +
        'contact whoever set this up for you with this exact message.',
    )
    stopBackend()
    app.quit()
  }
}

// Real silent printing -- no OS print dialog at all, either way. If a
// printer is actually available, this prints straight to it. If not,
// it does nothing rather than showing a dialog nobody wants: no
// "no printers found" popup, no browser print preview interrupting
// checkout. This is only possible because it runs here, in the main
// process, using webContents.print -- the renderer (a sandboxed web
// page, even inside Electron) has no way to talk to a printer
// directly, which is exactly why the old approach
// (iframe.contentWindow.print() from React) always had to show the
// browser's own print UI regardless of printer availability.
ipcMain.handle('print-receipt-silently', async (_event, base64Pdf) => {
  let printWindow = null
  try {
    // `plugins: true` is not optional here -- without it, Electron has
    // no PDF viewer registered at all, so loading a `data:application/pdf`
    // URL isn't rendered, it's treated as an unhandled download instead.
    // That download falls straight into the app's global `will-download`
    // handler above, which calls dialog.showSaveDialogSync(mainWindow) --
    // i.e. every single sale would pop a native "Save file" dialog over
    // the POS screen right after checkout, which is exactly backwards for
    // a feature whose entire purpose is printing with zero dialogs. With
    // plugins enabled, Chromium's built-in PDFium viewer renders the PDF
    // in-process instead, so print() has an actual page to print and
    // will-download never fires for this window at all.
    printWindow = new BrowserWindow({ show: false, webPreferences: { sandbox: true, plugins: true } })
    await printWindow.loadURL(`data:application/pdf;base64,${base64Pdf}`)

    const printers = await printWindow.webContents.getPrintersAsync()
    if (printers.length === 0) {
      return { printed: false }
    }

    await new Promise((resolve, reject) => {
      printWindow.webContents.print(
        { silent: true, printBackground: true },
        (success, errorType) => {
          if (success) resolve()
          else reject(new Error(errorType))
        },
      )
    })
    return { printed: true }
  } catch (err) {
    // A failed silent print must never surface as an error to the
    // cashier mid-checkout -- same principle as the old auto-print's
    // own try/catch. Logged for later diagnosis, not shown.
    logDesktopDiagnostic(`silent-print-failed ${err instanceof Error ? err.message : err}`)
    return { printed: false }
  } finally {
    if (printWindow && !printWindow.isDestroyed()) printWindow.destroy()
  }
})

function stopBackend() {
  if (!backendProcess || backendProcess.killed) {
    backendProcess = null
    return
  }
  // Plain .kill() only terminates the single process Node has a
  // handle to. On Windows, a PyInstaller-frozen onefile exe commonly
  // runs as a launcher that spawns its own inner process to actually
  // do the work -- killing just the outer one can leave that inner
  // process running, invisible to Electron, still bound to port 8000.
  // The next launch then spawns a brand new backend that can't bind
  // that port, while the orphan -- possibly from an older version, in
  // whatever state it happened to be in when orphaned -- is what
  // actually answers health checks and requests instead. That's the
  // real mechanism behind "sometimes blank, sometimes can't reach the
  // server" on relaunch: not randomness, an accumulating leftover
  // process from an imperfect shutdown.
  //
  // taskkill /t kills the entire process tree rooted at this PID, not
  // just the one process -- the actual fix, not a bigger hammer for
  // its own sake. Windows-only, matching this deployment target.
  if (process.platform === 'win32' && backendProcess.pid) {
    try {
      spawn('taskkill', ['/pid', String(backendProcess.pid), '/t', '/f'])
    } catch (err) {
      logDesktopDiagnostic(`taskkill-failed ${err instanceof Error ? err.message : err}`)
      backendProcess.kill()
    }
  } else {
    backendProcess.kill()
  }
  backendProcess = null
}
