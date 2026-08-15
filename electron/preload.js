/**
 * Sandboxed preload -- runs with contextIsolation on, no direct Node
 * access in the renderer. contextBridge is the one narrow, explicit
 * doorway between the two: the renderer gets exactly this one
 * function and nothing else, never raw ipcRenderer, never fs, never
 * child_process. Adding this file is the only way real silent
 * printing (no OS print dialog at all) is possible -- that's an
 * Electron main-process capability (webContents.print), not
 * something any web page, even one running inside Electron, can do
 * on its own.
 */
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  // base64Pdf: the receipt PDF, base64-encoded. Resolves to
  // { printed: boolean } -- printed=false means "no printer was
  // available, nothing happened" (by design: no dialog either way),
  // never an error the caller needs to handle specially.
  printReceiptSilently: (base64Pdf) => ipcRenderer.invoke('print-receipt-silently', base64Pdf),
  // url: a GitHub release asset URL from useUpdateCheck(), never
  // arbitrary input. Starts a real Electron-managed download; the
  // save dialog, "install now?" confirmation, and launching the
  // installer all happen in main.js once it completes -- this call
  // itself resolves as soon as the download starts, not when it
  // finishes, so it has nothing meaningful to return.
  downloadUpdateInstaller: (url) => ipcRenderer.invoke('download-update-installer', url),
})
