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

const { app, BrowserWindow, dialog, session, ipcMain, shell } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const { spawn } = require('node:child_process')

// No longer a fixed constant -- see getFreePort(). Set once at the
// start of startApp(), before the backend is spawned, and read by
// every function below that needs to talk to the backend.
let backendPort = null
let backendUrl = null
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
// setName() does not retroactively change Electron's default userData path
// on Windows. Set it explicitly so diagnostics are stored in the documented
// %APPDATA%\\PharmacyERP location instead of the package name directory.
app.setPath('userData', path.join(app.getPath('appData'), 'PharmacyERP'))

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
    //
    // Awaited, not fire-and-forget -- app.quit() must not run until
    // the backend's process tree is actually confirmed dead, or the
    // very next launch inherits an orphan holding the port. This is
    // the normal, everyday way this app closes, so this path matters
    // more than the startup-failure one below.
    stopBackend().then(() => app.quit())
  })

  app.on('before-quit', (event) => {
    if (backendProcess === null) return
    // Delay Electron's own shutdown until the kill is confirmed, same
    // reasoning as window-all-closed above -- before-quit can fire
    // from paths that don't go through that handler (e.g. the OS
    // asking every app to close during a shutdown/logoff).
    event.preventDefault()
    stopBackend().then(() => app.quit())
  })
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

function developmentPythonPath() {
  const venvPython = process.platform === 'win32'
    ? path.join(__dirname, '..', 'backend', '.venv', 'Scripts', 'python.exe')
    : path.join(__dirname, '..', 'backend', '.venv', 'bin', 'python')
  return fs.existsSync(venvPython) ? venvPython : 'python'
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
    // PHARMACY_ERP_BACKEND_PORT tells it which OS-assigned port
    // getFreePort() already claimed for this launch, so both sides
    // agree on the same port without either one hardcoding it.
    const backendEnv = {
      ...process.env,
      PHARMACY_ERP_ELECTRON: '1',
      PHARMACY_ERP_BACKEND_PORT: String(backendPort),
    }

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
        backendProcess = spawn(developmentPythonPath(), ['desktop_main.py'], {
          cwd: path.join(__dirname, '..', 'backend'),
          windowsHide: process.platform === 'win32',
          stdio: process.platform === 'win32' ? 'ignore' : 'inherit',
          env: backendEnv,
        })
    }

    let spawned = false
    backendProcess.once('spawn', () => {
      spawned = true
      writeBackendPidFile(backendProcess.pid)
      // Resolve WITH the process reference itself, not void -- the
      // caller must be able to watch this exact process for its exit
      // event directly, rather than re-reading the module-level
      // `backendProcess` variable later. That variable gets nulled
      // out by the 'exit' handler below, and on a backend that exits
      // within milliseconds of spawning (e.g. it detects a leftover
      // instance still on the port and exits immediately -- see
      // desktop_main.py's _already_running_instance check), the read
      // can lose the race and see null, silently skipping the exit
      // watch in waitForBackendHealthy() entirely.
      resolve(backendProcess)
    })
    backendProcess.on('error', (err) => {
      reject(new Error(`Could not start the backend: ${err.message}`))
    })

    backendProcess.on('exit', (code) => {
      backendProcess = null
      if (!spawned || mainWindow === null) {
        // Died before the window ever opened -- nothing on screen to
        // explain why, so this becomes the startup failure message
        // instead of a silent blank window forever.
        reject(new Error(`The backend exited immediately (code ${code}).`))
      }
    })

  })
}

/**
 * Asks the OS for a currently-unused TCP port instead of assuming
 * one. Binding to port 0 is the standard way to ask the OS to pick --
 * it will never hand back a port already bound by anything else on
 * the machine, which is what makes this immune to collisions with any
 * other system on the same computer, including ones built on this
 * exact same architecture (same entrypoint filename, same framework,
 * even the same port this app used to hardcode). The probe listener
 * only exists to learn the number; it's closed immediately afterward
 * so the real backend process can bind that same port itself.
 */
function getFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.on('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

function waitForBackendHealthy(processToWatch = backendProcess) {
  const deadline = Date.now() + BACKEND_STARTUP_TIMEOUT_MS

  return new Promise((resolve, reject) => {
    let settled = false
    const onBackendExit = (code) => {
      if (settled) return
      settled = true
      reject(new Error(`The backend exited before becoming ready (code ${code}).`))
    }
    processToWatch?.once('exit', onBackendExit)

    function finishResolve() {
      if (settled) return
      settled = true
      processToWatch?.removeListener('exit', onBackendExit)
      resolve()
    }

    function finishReject(error) {
      if (settled) return
      settled = true
      processToWatch?.removeListener('exit', onBackendExit)
      reject(error)
    }

    function attempt() {
      if (settled) return
      const req = http.get(`${backendUrl}/health`, (res) => {
        res.resume() // drain, don't leak the socket
        if (res.statusCode === 200) {
          finishResolve()
        } else {
          retryOrGiveUp()
        }
      })
      req.on('error', retryOrGiveUp)
    }

    function retryOrGiveUp() {
      if (Date.now() > deadline) {
        finishReject(new Error('The backend did not become ready within 30 seconds.'))
      } else {
        // 150ms, not 500ms -- this only controls how quickly a
        // ready backend gets NOTICED, not how long we're willing to
        // wait overall (the 30s deadline above is unchanged). Polling
        // more often costs nothing but a few extra fast, local,
        // connection-refused attempts while the backend is still
        // starting; polling less often means genuinely waiting
        // longer, on every single launch, for no reliability benefit
        // -- if the backend becomes ready partway between polls, the
        // old 500ms interval meant sitting on a real answer for up to
        // half a second before checking again.
        setTimeout(attempt, 150)
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

  // Previously logged only -- did-fail-load fires with no dialog and
  // no retry, so if the backend's own /health check had already passed
  // (waitForBackendHealthy in startApp()) but the actual page
  // navigation then failed for some separate reason, the result was a
  // window that shows itself (via ready-to-show or the 10s fallback
  // above) with nothing ever loaded into it -- a real, previously
  // unhandled gap between "backend is up" and "the page actually
  // loaded", not a hypothetical one.
  //
  // -3 (ERR_ABORTED) is deliberately excluded: it fires routinely for
  // benign, expected navigation (a redirect superseded by another
  // load, the page's own client-side routing) and is not evidence of
  // a real failure -- treating it as one would retry/alert on normal
  // operation, not just genuine faults.
  //
  // isMainFrame is checked because this event also fires for failed
  // sub-resource loads (a font, an image) inside an otherwise
  // perfectly working page; only a failed top-level navigation is
  // "the app never actually appeared" -- the failure this fix exists
  // to catch.
  let didFailLoadRetried = false
  mainWindow.webContents.on(
    'did-fail-load',
    (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
      logDesktopDiagnostic(
        `did-fail-load ${errorCode} ${errorDescription} ${validatedURL} isMainFrame=${isMainFrame}`,
      )
      if (!isMainFrame || errorCode === -3) return

      if (!didFailLoadRetried) {
        // One automatic retry first -- a transient failure (the
        // backend answering /health a moment before it's fully ready
        // to serve the actual page, a brief loopback hiccup) shouldn't
        // need a person to manually restart the app at all.
        didFailLoadRetried = true
        setTimeout(() => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.loadURL(backendUrl)
          }
        }, 500)
        return
      }

      // Retried once and it failed again -- this is exactly the
      // "double-click, blank window" report, made visible instead of
      // silent. A person seeing this dialog knows something is wrong
      // and can report it with the real error, instead of assuming the
      // app is simply broken or slow.
      dialog.showErrorBox(
        'Pharmacy ERP could not load',
        `The app window failed to load (${errorDescription}).\n\n` +
          'Try closing and reopening the app. If this keeps happening, ' +
          'contact whoever set this up for you with this exact message.',
      )
    },
  )
  // Resets the retry flag on a genuine success so a later, unrelated
  // failure (not expected in this single-page app today, but not
  // impossible either) gets its own fresh retry rather than going
  // straight to the dialog because of an earlier, already-resolved one.
  mainWindow.webContents.on('did-finish-load', () => {
    didFailLoadRetried = false
  })
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    logDesktopDiagnostic(`render-process-gone ${JSON.stringify(details)}`)
  })
  mainWindow.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    if (level >= 2) {
      logDesktopDiagnostic(`renderer-console level=${level} ${sourceId}:${line} ${message}`)
    }
  })

  mainWindow.loadURL(backendUrl)

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

/**
 * Path to the file recording the PID of the backend process THIS app
 * spawned last time it ran. Written immediately after every
 * successful spawn (see startBackend()) and cleared on every clean
 * shutdown (see stopBackend()) -- so if it's still present on the
 * NEXT launch, the previous session ended without running its own
 * cleanup at all (a crash, a force-kill, a Windows shutdown that
 * didn't give the app time to close normally), and whatever process
 * that exact PID pointed to may still be alive.
 */
function backendPidFilePath() {
  return path.join(app.getPath('userData'), 'backend.pid')
}

function writeBackendPidFile(pid) {
  try {
    fs.writeFileSync(backendPidFilePath(), String(pid), 'utf8')
  } catch (err) {
    logDesktopDiagnostic(`write-backend-pid-failed ${err.message}`)
  }
}

function clearBackendPidFile() {
  try {
    fs.rmSync(backendPidFilePath(), { force: true })
  } catch (err) {
    logDesktopDiagnostic(`clear-backend-pid-failed ${err.message}`)
  }
}

/**
 * Kills the exact backend process this app itself spawned and
 * recorded last time it ran -- and ONLY that one recorded PID. Never
 * anything found by scanning for a port number or an image name.
 *
 * This replaces the previous approach, which matched ANY process
 * listening on the backend's port, OR named 'Pharmacy-ERP.exe', OR
 * running a command line containing 'desktop_main.py'. That was safe
 * only for as long as this exact port/filename/entrypoint combination
 * was unique on the machine -- it is not: every system built on this
 * same Electron+FastAPI+desktop_main.py architecture shares that
 * pattern, so the old check could -- and did -- match and kill a
 * completely different app's backend, not just this app's own
 * leftovers, whenever both happened to be present on the same
 * machine.
 *
 * Tracking one exact, previously-recorded PID makes that structurally
 * impossible: this can never find a process it didn't itself spawn on
 * a previous run, regardless of what port or process name any other
 * app on the machine happens to use -- including apps built later,
 * after this fix, that nobody has thought of yet.
 *
 * The recorded PID is still confirmed by process name before being
 * killed, as a last identity check -- not because the number alone
 * usually isn't enough, but because Windows does eventually reuse
 * PIDs, and a stale recording pointing at a since-reused number must
 * never take down whatever unrelated process now happens to hold it.
 * This check runs only against the one already-selected PID, never as
 * a system-wide scan.
 */
function killPreviousBackendIfAny() {
  return new Promise((resolve) => {
    if (process.platform !== 'win32') {
      resolve()
      return
    }
    let pid
    try {
      pid = Number.parseInt(fs.readFileSync(backendPidFilePath(), 'utf8').trim(), 10)
    } catch {
      resolve() // no pid file recorded -- nothing to clean up
      return
    }
    if (!Number.isInteger(pid) || pid <= 0) {
      clearBackendPidFile()
      resolve()
      return
    }
    const ps = spawn('powershell.exe', [
      '-NoProfile',
      '-Command',
      `Get-CimInstance Win32_Process -Filter "ProcessId = ${pid}" | ` +
        `Where-Object { $_.Name -eq 'Pharmacy-ERP.exe' -or $_.CommandLine -match 'desktop_main\\.py' } | ` +
        `ForEach-Object { Stop-Process -Id ${pid} -Force -ErrorAction SilentlyContinue }`,
    ])
    ps.on('error', (err) => {
      logDesktopDiagnostic(`kill-previous-backend-failed ${err.message}`)
      resolve() // never let this block startup
    })
    ps.on('exit', () => {
      clearBackendPidFile()
      resolve()
    })
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
    //
    // The one exception is the update installer, detected below by
    // its exact filename pattern -- every other download in this app
    // (receipts, report exports) comes from a blob: URL created in
    // the page itself; only the update installer is ever a real
    // http(s) download routed through webContents.downloadURL(), so
    // this can never misfire on an unrelated file a person happens to
    // save with a similar name.
    session.defaultSession.on('will-download', (event, item) => {
      const isUpdateInstaller = /^Pharmacy-ERP-Setup-.*\.exe$/i.test(item.getFilename())

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
        if (state === 'completed' && isUpdateInstaller) {
          const choice = dialog.showMessageBoxSync(mainWindow, {
            type: 'info',
            buttons: ['Install now', 'Later'],
            defaultId: 0,
            message: 'Update downloaded',
            detail:
              'The installer has been saved. Install now? ' +
              'Pharmacy ERP will close so the update can complete.',
          })
          if (choice === 0) {
            // Same shutdown discipline as every other quit path in
            // this file -- the backend's process tree must be
            // confirmed dead before anything else, or the installer
            // could fail to replace files still locked by a running
            // backend, and the very next launch could inherit an
            // orphan exactly like the one this session's other fix
            // exists to prevent.
            stopBackend().then(() => {
              shell.openPath(savePath).then((openError) => {
                if (openError) {
                  logDesktopDiagnostic(`update-install-launch-failed ${openError}`)
                  dialog.showErrorBox(
                    'Could not start the installer',
                    `The update was downloaded to:\n${savePath}\n\n` +
                      'Please run it manually to complete the update.',
                  )
                } else {
                  // Symmetric with the failure branch above -- a real
                  // support case for "update didn't seem to apply"
                  // needs to distinguish "the installer never
                  // launched" from "it launched but something in the
                  // installer itself went wrong", and only this log
                  // line can tell those apart.
                  logDesktopDiagnostic(`update-install-launched ${savePath}`)
                }
                app.quit()
              })
            })
          }
        } else if (state === 'completed') {
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
    // Cleans up only the exact process this app itself spawned last
    // time it ran, if that session ended without cleaning up after
    // itself (crash, force-kill, abrupt shutdown) -- see
    // killPreviousBackendIfAny()'s own comment for why this is no
    // longer a port/name/commandline scan.
    await killPreviousBackendIfAny()
    // A fresh, OS-assigned port for this launch -- see getFreePort().
    // Nothing else on the machine can already be bound to it, so
    // there is nothing to wait for here the way the old fixed-port
    // design had to wait for a just-killed process's port to clear.
    backendPort = await getFreePort()
    backendUrl = `http://127.0.0.1:${backendPort}`
    const spawnedBackend = await startBackend()
    // These two are independent of each other -- clearing session
    // storage never depends on the backend being up, it only needs
    // Electron's own session API, which is available immediately.
    // Running them concurrently instead of one after the other saves
    // real time on every launch without changing what happens before
    // createWindow() runs: both still fully complete first, so the
    // window still never shows stale cached content, exactly as
    // before -- only the ORDER of independent work changed, not what
    // work happens or when relative to the window appearing.
    await Promise.all([
      waitForBackendHealthy(spawnedBackend),
      session.defaultSession.clearStorageData({
        storages: ['serviceworkers', 'cachestorage'],
      }),
    ])
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
    stopBackend().then(() => app.quit())
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
    // getPrintersAsync() on Windows always includes the built-in
    // virtual printers ("Microsoft Print to PDF", "Microsoft XPS
    // Document Writer", sometimes "OneNote" / "Fax") even on a
    // machine with zero physical printers attached -- so
    // `printers.length === 0` almost never actually happens. Worse:
    // if the system's DEFAULT printer happens to be one of these
    // (very common when no physical printer has ever been set up),
    // print({ silent: true }) still triggers a real, unsuppressable
    // native "Save Print Output As" dialog -- that comes from
    // Windows' own PDF/XPS driver needing a destination file path,
    // not from Chromium, so Electron's `silent` flag has no power
    // over it at all. Filtering these out, and only ever printing to
    // a REAL device, is what actually keeps this silent end to end.
    const VIRTUAL_PRINTER_NAME_PATTERN = /pdf|xps document writer|onenote|fax/i
    const realPrinters = printers.filter(
      (p) => !VIRTUAL_PRINTER_NAME_PATTERN.test(p.name) && !VIRTUAL_PRINTER_NAME_PATTERN.test(p.displayName ?? ''),
    )
    if (realPrinters.length === 0) {
      return { printed: false }
    }
    // Prefer whichever real printer is the OS default; otherwise just
    // take the first real one. Explicitly named via deviceName below
    // -- never left to "whatever the system default is", since that
    // default is exactly what might be the virtual PDF printer this
    // filtering just excluded.
    const targetPrinter = realPrinters.find((p) => p.isDefault) ?? realPrinters[0]

    await new Promise((resolve, reject) => {
      printWindow.webContents.print(
        { silent: true, printBackground: true, deviceName: targetPrinter.name },
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

// Triggered from the Settings page's "Download update" button. Only
// ever called with a URL the renderer got from useUpdateCheck(),
// which only ever returns a GitHub release asset URL -- never
// arbitrary renderer-controlled input reaching a filesystem or shell
// operation, which is what makes routing it through
// webContents.downloadURL() (rather than, say, exec-ing curl on a
// raw string) the safe way to do this. The actual save dialog, the
// "install now?" confirmation, and launching the installer all happen
// in the will-download handler above once this download completes --
// this handler's only job is to start it.
ipcMain.handle('download-update-installer', (_event, url) => {
  mainWindow.webContents.downloadURL(url)
})

function stopBackend() {
  if (!backendProcess || backendProcess.killed) {
    // The backend was already gone by the time this ran (it crashed,
    // or its own 'exit' handler already nulled backendProcess out
    // before this function got called) -- but the pid file recorded
    // at spawn time is still sitting there regardless of *how* the
    // process ended. Previously left uncleared on this exact path:
    // the next launch's killPreviousBackendIfAny() would then find
    // that stale pid, spawn powershell.exe to investigate a process
    // that's already gone, and pay that real startup cost for
    // nothing -- not a rare crash-recovery cost, but a routine one on
    // every ordinary close that happened to hit this branch.
    clearBackendPidFile()
    backendProcess = null
    return Promise.resolve()
  }
  const processToKill = backendProcess
  backendProcess = null
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
  //
  // Returning a Promise that only resolves once taskkill has actually
  // finished is not optional here -- every caller of this function
  // calls app.quit() immediately afterward, and a fire-and-forget
  // spawn() used to let that quit race ahead of the kill actually
  // completing. On a PyInstaller-frozen exe under any load (antivirus
  // scanning taskkill.exe itself, a slow shutdown), that race can be
  // lost, which is exactly how an orphan survives a normal app close,
  // not just a crash -- and normal closes happen every single day,
  // far more often than startup failures do.
  if (process.platform === 'win32' && processToKill.pid) {
    return new Promise((resolve) => {
      const kill = spawn('taskkill', ['/pid', String(processToKill.pid), '/t', '/f'])
      kill.on('exit', () => {
        clearBackendPidFile()
        resolve()
      })
      kill.on('error', (err) => {
        logDesktopDiagnostic(`taskkill-failed ${err.message}`)
        processToKill.kill()
        clearBackendPidFile()
        resolve()
      })
    })
  }
  processToKill.kill()
  clearBackendPidFile()
  return Promise.resolve()
}
