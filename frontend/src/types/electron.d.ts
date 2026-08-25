// window.electronAPI only exists when this app is actually running
// inside the Electron shell (see electron/preload.js) -- undefined
// everywhere else, e.g. the plain-browser dev server. Every call site
// must treat it as optional for exactly that reason.
export {}

declare global {
  interface Window {
    electronAPI?: {
      downloadUpdateInstaller: (url: string) => Promise<void>
    }
  }
}
