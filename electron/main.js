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
const {
  INSTALLER_FILENAME,
  isTrustedInstallerUrl,
  isSafeExternalUrl,
  isSameOrigin,
} = require('./urlPolicy')

// No longer a fixed constant -- see getFreePort(). Set once at the
// start of startApp(), before the backend is spawned, and read by
// every function below that needs to talk to the backend.
let backendPort = null
let backendUrl = null
// 90s, not 30s -- raised after a real report: backend.log showed
// migration duration alone (a fixed, small set of Alembic scripts,
// no reason to vary) ranging from 2s to 19s+ across back-to-back
// launches on the same machine, with the gap between
// migrations-complete and uvicorn-starting stretching further still on
// some attempts and never being reached at all on others. That
// signature -- the SAME code taking wildly different amounts of time
// on each run, not a consistently slow step -- is the known fingerprint
// of Windows Defender/SmartScreen real-time-scanning this build's
// freshly-written, unsigned DLLs (this .spec has no codesign_identity)
// on every access until the exe earns enough reputation/gets cached,
// which is worst immediately after a fresh install or update and
// tapers off on later launches. 30s was tuned for an already-trusted,
// warm exe; it left no margin for that real, observed variance and was
// killing genuinely-still-starting (not hung) launches. This does not
// paper over an actual hang -- a truly crashed/exited backend is still
// caught immediately via the 'exit' listener in waitForBackendHealthy,
// regardless of this deadline.
const BACKEND_STARTUP_TIMEOUT_MS = 90000

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

// Software rendering, on purpose. A real report showed the splash and then
// the main window painting as an empty white (later dark) rectangle on a
// real Windows PC while the page behind it was demonstrably alive (its title
// had been set by the app after loading its config). That is the signature
// of the GPU/compositor path failing to present frames, which this app has
// no use for: it is forms, tables and a receipt PDF, not video or 3D. Turning
// it off makes every PC use the same plain, predictable drawing path instead
// of whatever each graphics driver does. Must run before the app is ready.
// Every automated GUI test already runs this way (no GPU), so this also makes
// the real app match what the tests check.
app.disableHardwareAcceleration()

let backendProcess = null
let mainWindow = null
// The one installer URL the updater IPC has approved. A download counts as the
// update installer only if it began from exactly this URL (see will-download).
let approvedInstallerUrl = null

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

// Startup timing trace: one line per phase with milliseconds since this
// file was first loaded, written to the same desktop.log. Diagnostic only
// -- it never changes what the app does or when.
const STARTUP_T0 = Date.now()
function startupTrace(label) {
  logDesktopDiagnostic(`startup-trace ${label} +${Date.now() - STARTUP_T0}ms`)
}

// A crashed GPU, utility or network helper process is invisible to the user
// and to every other log line here; this is the only record of it.
app.on('child-process-gone', (_event, details) => {
  logDesktopDiagnostic(`child-process-gone ${JSON.stringify(details)}`)
})

// Electron's own native answer to the exact bug a real report showed:
// two copies of this app fighting over the same port. Checked before
// anything else even starts, so a second launch never gets far enough
// to spawn a second backend at all -- it just focuses the existing
// window instead. This is a *different, additional* layer from the
// backend's own "is something already running on this port" check
// (desktop_main.py's _already_running_instance) -- that one protects
// someone running the raw exe directly outside Electron; this one
// protects the packaged app specifically.
let splashWindow = null
let quitting = false

// How a second launch proves to itself that the first instance is alive
// and showing something: it passes a file path as additionalData, and the
// first instance writes that file once it has focused a visible window.
// No ack = the old instance is stuck or window-less, so the new launch
// offers to restart it instead of silently quitting (the old behaviour,
// which looked exactly like "I click the icon and nothing happens").
function ackFilePath() {
  return path.join(app.getPath('userData'), `launch-ack-${process.pid}.flag`)
}

function liveWindow() {
  for (const w of [mainWindow, splashWindow]) {
    if (w && !w.isDestroyed() && (w.isVisible() || w.isMinimized())) return w
  }
  return null
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function killOtherInstances() {
  // Packaged builds only: in development the executable is the shared
  // electron.exe, and sweeping by that name could close unrelated dev apps.
  if (process.platform !== 'win32' || !app.isPackaged) return Promise.resolve()
  const run = (args) =>
    new Promise((resolve) => {
      const p = spawn('taskkill', args, { windowsHide: true })
      p.on('error', resolve)
      p.on('exit', resolve)
    })
  return run([
    '/F', '/T', '/FI', `PID ne ${process.pid}`, '/IM', path.basename(process.execPath),
  ]).then(() => run(['/F', '/T', '/IM', 'Pharmacy-ERP.exe']))
}

async function handleDeniedLaunch(ackFile) {
  const deadline = Date.now() + 3000
  while (Date.now() < deadline) {
    if (fs.existsSync(ackFile)) {
      try { fs.rmSync(ackFile, { force: true }) } catch { /* ignore */ }
      logDesktopDiagnostic('second-launch-handed-to-running-instance')
      app.quit()
      return
    }
    await sleep(150)
  }
  await app.whenReady()
  logDesktopDiagnostic('second-launch-no-ack: existing instance has no visible window')
  const { response } = await dialog.showMessageBox({
    type: 'warning',
    buttons: ['Restart Pharmacy ERP', 'Cancel'],
    defaultId: 0,
    cancelId: 1,
    title: 'Pharmacy ERP',
    message: 'Pharmacy ERP is already running, but it has no window.',
    detail: 'A previous session did not close properly. Restart will close it and open the app fresh. No data is lost.',
  })
  if (response === 0) {
    await killOtherInstances()
    await sleep(1500)
    app.relaunch()
    app.exit(0)
  } else {
    app.quit()
  }
}

fs.mkdirSync(app.getPath('userData'), { recursive: true })
const myAckFile = ackFilePath()
const gotSingleInstanceLock = app.requestSingleInstanceLock({ ackFile: myAckFile })

if (!gotSingleInstanceLock) {
  handleDeniedLaunch(myAckFile)
} else {
  app.on('second-instance', (_event, _argv, _cwd, additionalData) => {
    const win = liveWindow()
    if (!win) return // no ack: the new launch will offer a restart
    if (win.isMinimized()) win.restore()
    win.focus()
    const ack = additionalData && additionalData.ackFile
    if (
      typeof ack === 'string' &&
      path.dirname(path.resolve(ack)) === path.resolve(app.getPath('userData'))
    ) {
      try { fs.writeFileSync(ack, '1') } catch { /* ignore */ }
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
    logDesktopDiagnostic('window-all-closed')
    beginQuit()
    stopBackend().then(() => {
      logDesktopDiagnostic('backend-stopped')
      app.quit()
    })
  })

  app.on('before-quit', (event) => {
    beginQuit()
    if (backendProcess === null) return
    // Delay Electron's own shutdown until the kill is confirmed, same
    // reasoning as window-all-closed above -- before-quit can fire
    // from paths that don't go through that handler (e.g. the OS
    // asking every app to close during a shutdown/logoff).
    event.preventDefault()
    stopBackend().then(() => app.quit())
  })
}

// Once quitting starts, a hard exit is guaranteed shortly after, so a hung
// taskkill or cleanup can never leave a window-less Pharmacy ERP process
// behind holding the single-instance lock.
function beginQuit() {
  if (quitting) return
  quitting = true
  logDesktopDiagnostic('quit-begin')
  setTimeout(() => {
    logDesktopDiagnostic('quit-hard-exit-fired')
    app.exit(0)
  }, 8000).unref()
}

function createSplash() {
  if (splashWindow) return
  splashWindow = new BrowserWindow({
    width: 380,
    height: 270,
    frame: false,
    resizable: false,
    center: true,
    show: true,
    backgroundColor: '#f7f3ec',
    title: 'Pharmacy ERP',
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  })
  // Everything the splash needs is inline: the icon is a ~4 KB embedded PNG and
  // the animation is pure CSS (transform/opacity only, so it keeps moving
  // smoothly even while the main process is busy starting the backend). No
  // file reads, no network, no scripts -- nothing that could delay or break it.
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Pharmacy ERP</title><style>
    html,body{margin:0;height:100%;overflow:hidden;background:#f7f3ec;color:#333;
      font-family:'Segoe UI',system-ui,sans-serif}
    body{display:flex;flex-direction:column;align-items:center;justify-content:center}
    img{width:88px;height:88px;animation:pulse 1.8s ease-in-out infinite}
    h1{margin:14px 0 0;font-size:22px;font-weight:600}
    #m{margin-top:8px;font-size:14px;color:#555}
    .track{margin-top:18px;width:220px;height:4px;border-radius:2px;background:#e4dccd;overflow:hidden}
    .bar{width:40%;height:100%;border-radius:2px;background:#c8942f;animation:slide 1.3s ease-in-out infinite}
    @keyframes slide{0%{transform:translateX(-100%)}100%{transform:translateX(260%)}}
    @keyframes pulse{0%,100%{transform:scale(1);opacity:.92}50%{transform:scale(1.05);opacity:1}}
    @media (prefers-reduced-motion:reduce){img,.bar{animation:none}.bar{width:100%}}
  </style></head><body>
    <img alt="" src="data:image/png;base64,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">
    <h1>Pharmacy ERP</h1>
    <div id="m">Starting, please wait...</div>
    <div class="track"><div class="bar"></div></div>
  </body></html>`
  splashWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
  splashWindow.webContents.on('will-navigate', (e) => e.preventDefault())
  splashWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  splashWindow.on('closed', () => { splashWindow = null })
  setTimeout(() => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.webContents
        .executeJavaScript(
          "document.getElementById('m').textContent='Still starting... this can take up to a minute after an update.'",
        )
        .catch(() => {})
    }
  }, 20000)
}

function closeSplash() {
  if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close()
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
      PHARMACY_ERP_PARENT_PID: String(process.pid),
    }

    startupTrace('spawning-backend')
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
      startupTrace('backend-process-spawned')
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
        finishReject(
          new Error(
            `The backend did not become ready within ${BACKEND_STARTUP_TIMEOUT_MS / 1000} seconds.`,
          ),
        )
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

// Five seconds after the page loads, looks at what is actually on screen. If
// the page has real content but every pixel is the same colour, the
// window is alive and drawing nothing -- the exact failure that was reported.
// It is logged, and the page is reloaded once (never more), because a window
// that is provably blank cannot be made worse by a reload. A page that is
// still legitimately showing its startup screen has varied pixels, so this
// does not fire on a slow start.
let paintCheckDone = false
function schedulePaintCheck() {
  if (paintCheckDone) return
  paintCheckDone = true
  setTimeout(async () => {
    const win = mainWindow
    if (!win || win.isDestroyed() || !win.isVisible() || win.isMinimized()) return
    try {
      const image = await win.webContents.capturePage()
      const { width, height } = image.getSize()
      if (width === 0 || height === 0) return
      const bitmap = image.toBitmap()
      // Every pixel, stopping at the first one that differs: exact (a fixed
      // stride can line up with the window width and only ever look at a few
      // columns), and a normal screen exits within the first few rows. Only a
      // truly flat screen scans it all, about 40 ms, once. The right and bottom
      // 24 px are skipped because a scrollbar there is drawn even over an
      // otherwise empty page.
      const first = bitmap.readUInt32LE(0)
      const scanWidth = width - 24
      const scanHeight = height - 24
      let distinct = 1
      for (let y = 0; y < scanHeight && distinct < 2; y += 1) {
        for (let x = 0; x < scanWidth; x += 1) {
          if (bitmap.readUInt32LE((y * width + x) * 4) !== first) {
            distinct += 1
            break
          }
        }
      }
      const textChars = await win.webContents.executeJavaScript(
        'document.body ? document.body.innerText.length : 0',
      )
      logDesktopDiagnostic(`paint-check distinct-colours=${distinct >= 2 ? 'many' : 'one'} dom-text-chars=${textChars}`)
      if (distinct < 2 && textChars > 0 && !win.isDestroyed()) {
        logDesktopDiagnostic('blank-paint-detected: reloading once')
        win.webContents.reloadIgnoringCache()
      }
    } catch (err) {
      logDesktopDiagnostic(`paint-check-failed ${err && err.message}`)
    }
  }, 5000).unref()
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
    startupTrace('ready-to-show')
    mainWindow.show()
    closeSplash()
  })
  mainWindow.webContents.once('did-start-loading', () => startupTrace('page-load-started'))
  mainWindow.webContents.once('did-finish-load', () => startupTrace('page-load-finished'))

  // The only thing in this app that ever calls window.open() is the
  // receipt view (see handleViewReceipt in SalesPage.tsx and PosPage.tsx)
  // -- always with a blob: URL for a PDF it just fetched, never an
  // outbound link. Left unhandled, Electron's raw default for
  // window.open() is an undecorated popup with none of the
  // customizations just above: a visible native menu bar, no app icon,
  // the wrong background color, and no ready-to-show hold-back -- so it
  // flashes white and looks like an entirely different, unrelated
  // program instead of part of this one, which is exactly the "not
  // part of this system" symptom this was reported as.
  //
  // The genuine target="_blank" anchors elsewhere in this app (a
  // GitHub release link, a Gmail compose link, the OAuth playground
  // link in BackupsPage) go through this exact same handler too --
  // those must never be captured into an in-app window sized and
  // titled for a receipt. Routed by URL scheme instead: only blob:
  // (which nothing but the receipt view ever produces) gets the
  // in-app popup; everything else is denied here and handed to the
  // system's own default browser instead, exactly where a real
  // outbound link belongs.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('blob:')) {
      return {
        action: 'allow',
        overrideBrowserWindowOptions: {
          width: 440,
          height: 720,
          title: 'Receipt',
          backgroundColor: '#f7f3ec',
          autoHideMenuBar: true,
          ...iconOption,
          webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
          },
        },
      }
    }
    // Only web and mail links go to the operating system: handing it any
    // other scheme (file:, ms-msdt:, search-ms: ...) can launch programs.
    if (isSafeExternalUrl(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  // Never let this window -- the one the preload's IPC bridge is exposed
  // to -- navigate away from the app's own origin. Something that tries to
  // is opened in the user's browser instead, where it has no bridge.
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isSameOrigin(url, backendUrl)) return
    event.preventDefault()
    if (isSafeExternalUrl(url)) shell.openExternal(url)
  })
  // Defensive fallback: if ready-to-show never fires for any reason,
  // showing a blank window late is still far better than the app
  // silently never appearing at all, which would look like a failed
  // launch rather than a slow one.
  setTimeout(() => {
    if (mainWindow && !mainWindow.isVisible()) {
      startupTrace('show-fallback-fired-after-10s')
      mainWindow.show()
      closeSplash()
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
          'contact dericmarangu@gmail.com you with this exact message.',
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
  mainWindow.webContents.on('unresponsive', () => logDesktopDiagnostic('window-unresponsive'))
  mainWindow.webContents.on('responsive', () => logDesktopDiagnostic('window-responsive'))
  mainWindow.webContents.once('did-finish-load', () => schedulePaintCheck())
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
 * Kills every leftover copy of THIS app's own packaged backend still
 * running from a previous launch -- not just the single PID this
 * session happens to have on file, and never anything found by
 * scanning for a port number.
 *
 * History worth keeping: an earlier version of this function matched
 * ANY process listening on the backend's port, OR named
 * 'Pharmacy-ERP.exe', OR running a command line containing
 * 'desktop_main.py'. The port half of that was the actual bug -- safe
 * only for as long as this exact port happened to be unique on the
 * machine, which it isn't: any other system built on the same
 * Electron+FastAPI architecture shares that pattern, so a port-based
 * match could -- and did -- kill a completely different app's
 * backend. That's why a port is never part of the match here, even
 * though the request that led to this rewrite specifically asked for
 * "clear everything on the port": there's also nothing to clear that
 * way anymore -- getFreePort() already guarantees the port this
 * launch uses was free before anything bound it, so a same-port
 * leftover isn't a real failure mode this app can have.
 *
 * What WAS a real problem: matching only one previously-recorded PID
 * misses every OTHER leftover -- several stacked-up crashes across
 * past launches, or a pid file that predates this exact recording
 * mechanism, all left unkilled. The fix that actually addresses "an
 * old copy is still holding on" is matching by this app's own image
 * name, not by widening to a port or a generic filename any other
 * program could equally use. 'Pharmacy-ERP.exe' is this app's own
 * productName (see electron/package.json's build config) -- nothing
 * else on the machine can share it by accident the way a port number
 * or 'python.exe' could, so an image-name sweep is exactly as safe as
 * the single-PID check it replaces, just no longer capped at finding
 * only one.
 *
 * taskkill.exe is also a small native Windows binary, not a hosted
 * scripting engine -- swapping the previous powershell.exe +
 * Get-CimInstance call for this is a straight speed win on every
 * launch that actually finds something to clean up, not a slower
 * path traded for a safer one.
 */
function killPreviousBackendIfAny() {
  if (process.platform !== 'win32') {
    return Promise.resolve()
  }

  if (app.isPackaged) {
    return new Promise((resolve) => {
      const kill = spawn('taskkill', ['/F', '/T', '/IM', 'Pharmacy-ERP.exe'])
      kill.on('error', (err) => {
        logDesktopDiagnostic(`kill-previous-backend-failed ${err.message}`)
        resolve() // never let this block startup
      })
      kill.on('exit', () => {
        clearBackendPidFile()
        resolve()
      })
    })
  }

  // Development only, below: the backend here is the machine's own
  // shared python.exe (or a venv copy of it), not a uniquely-named
  // exe -- an image-name sweep like the packaged one above would just
  // as happily kill an unrelated Python program running on the same
  // machine. Falls back to the narrower, previously-recorded-PID
  // check instead, confirmed against this exact process's own command
  // line before anything is killed -- this path only ever matters on
  // Deric's own dev machine, never on a pharmacy's real install.
  return new Promise((resolve) => {
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
        `Where-Object { $_.CommandLine -match 'desktop_main\\.py' } | ` +
        `ForEach-Object { Stop-Process -Id ${pid} -Force -ErrorAction SilentlyContinue }`,
    ])
    ps.on('error', (err) => {
      logDesktopDiagnostic(`kill-previous-backend-failed ${err.message}`)
      resolve()
    })
    ps.on('exit', () => {
      clearBackendPidFile()
      resolve()
    })
  })
}

async function startApp() {
  startupTrace('app-ready')
  try {
    startupTrace(`gpu-status ${JSON.stringify(app.getGPUFeatureStatus())}`)
  } catch {
    // Diagnostics must never be the reason the app fails to open.
  }
  createSplash()
  try {
    // Without this, Electron's default behavior for the blob-URL
    // downloads every export and template button uses is to save the
    // file somewhere silently, with no dialog and no confirmation at
    // all -- indistinguishable from the button doing nothing. This
    // makes every download show a real Save dialog and a completion
    // message, the same as any normal desktop app.
    //
    // The one exception is the update installer, detected below by
    // its exact filename pattern AND the URL the updater approved --
    // every other download in this app
    // (receipts, report exports) comes from a blob: URL created in
    // the page itself; only the update installer is ever a real
    // http(s) download routed through webContents.downloadURL(), so
    // this can never misfire on an unrelated file a person happens to
    // save with a similar name.
    session.defaultSession.on('will-download', (event, item) => {
      // A filename is something any page can give a file, so the name alone
      // is not enough: the download must also have begun from the exact URL
      // the update IPC approved.
      const isUpdateInstaller =
        approvedInstallerUrl !== null &&
        item.getURLChain()[0] === approvedInstallerUrl &&
        INSTALLER_FILENAME.test(item.getFilename())

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
    startupTrace('previous-backend-check-done')
    // A fresh, OS-assigned port for this launch -- see getFreePort().
    // Nothing else on the machine can already be bound to it, so
    // there is nothing to wait for here the way the old fixed-port
    // design had to wait for a just-killed process's port to clear.
    backendPort = await getFreePort()
    backendUrl = `http://127.0.0.1:${backendPort}`
    startupTrace('port-claimed')
    const spawnedBackend = await startBackend()
    startupTrace('backend-spawn-returned')
    // These three are independent of each other -- clearing session
    // storage never depends on the backend being up, it only needs
    // Electron's own session API, which is available immediately.
    // Running them concurrently instead of one after another saves
    // real time on every launch without changing what happens before
    // createWindow() runs: all three still fully complete first, so
    // the window still never shows stale cached content, exactly as
    // before -- only the ORDER of independent work changed, not what
    // work happens or when relative to the window appearing.
    //
    // clearStorageData({ storages: ['serviceworkers', 'cachestorage'] })
    // and clearCache() are NOT the same thing, despite the similar
    // names -- the former clears the service worker's own Cache
    // Storage API entries; the LATTER clears Chromium's separate,
    // ordinary HTTP disk cache, which is where a plain (non-service-
    // worker) heuristically-cached index.html would actually live.
    // Missing this second call was the real gap behind a genuine
    // "sometimes starts blank, login never comes" report that
    // persisted after the service-worker clearing above was already
    // in place: an old index.html surviving in the plain HTTP cache
    // across an app update, referencing a since-deleted hashed JS
    // filename -- a 404 that never touches did-fail-load at all,
    // because Chromium serves it from cache without a network
    // request. Backend/app/main.py's serve_frontend now also sends an
    // explicit Cache-Control: no-cache on this same file for exactly
    // this reason -- this call and that header are deliberately two
    // independent layers closing the same gap, not redundant with
    // each other: this one protects every launch regardless of what
    // any given backend build does; that header protects any future
    // client (a real browser hitting this backend directly) that
    // isn't going through this Electron startup path at all.
    await Promise.all([
      waitForBackendHealthy(spawnedBackend).then(() => startupTrace('backend-health-ok')),
      session.defaultSession
        .clearStorageData({
          storages: ['serviceworkers', 'cachestorage'],
        })
        .then(() => startupTrace('clear-storage-done')),
      session.defaultSession.clearCache().then(() => startupTrace('clear-cache-done')),
    ])
    startupTrace('creating-window')
    createWindow()
  } catch (err) {
    if (quitting) return
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.hide()
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
ipcMain.handle('download-update-installer', (event, url) => {
  // Whatever runs in the renderer can call this, so the URL is checked here
  // rather than trusted: only this project's own GitHub release installer
  // (or, in an unpackaged dev/e2e run, a fake update server on loopback),
  // and only when asked by the main window itself.
  if (
    event.sender !== mainWindow?.webContents ||
    !isTrustedInstallerUrl(url, { allowLoopback: !app.isPackaged })
  ) {
    logDesktopDiagnostic(`update-download-refused ${String(url)}`)
    throw new Error('Refusing to download an installer from an untrusted address.')
  }
  approvedInstallerUrl = url
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
