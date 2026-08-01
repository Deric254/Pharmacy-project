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
 * this was written carefully against Electron's stable, long-standing
 * APIs (app, BrowserWindow, single-instance-lock) and reasoned through
 * line by line, but has never actually been run -- Electron needs a
 * real display to launch a window, which the environment this was
 * built in does not have. The backend it wraps has been tested
 * extremely thoroughly; this wrapper has not been run once.
 */

const { app, BrowserWindow, dialog, session } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const http = require('node:http')
const { spawn } = require('node:child_process')

const BACKEND_PORT = 8000
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
const BACKEND_STARTUP_TIMEOUT_MS = 30000

let backendProcess = null
let mainWindow = null

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

  mainWindow.loadURL(BACKEND_URL)

  mainWindow.on('closed', () => {
    mainWindow = null
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

    await startBackend()
    await waitForBackendHealthy()
    createWindow()
  } catch (err) {
    dialog.showErrorBox(
      'Pharmacy ERP could not start',
      `${err.message}\n\nTry restarting your computer. If this keeps happening, ` +
        'contact whoever set this up for you with this exact message.',
    )
    stopBackend()
    app.quit()
  }
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill()
  }
  backendProcess = null
}
