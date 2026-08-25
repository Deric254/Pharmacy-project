/**
 * Sandboxed preload -- runs with contextIsolation on, no direct Node
 * access in the renderer. contextBridge is the one narrow, explicit
 * doorway between the two: the renderer gets exactly this one
 * function and nothing else, never raw ipcRenderer, never fs, never
 * child_process.
 */
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  // url: a GitHub release asset URL from useUpdateCheck(), never
  // arbitrary input. Starts a real Electron-managed download; the
  // save dialog, "install now?" confirmation, and launching the
  // installer all happen in main.js once it completes -- this call
  // itself resolves as soon as the download starts, not when it
  // finishes, so it has nothing meaningful to return.
  downloadUpdateInstaller: (url) => ipcRenderer.invoke('download-update-installer', url),
})
