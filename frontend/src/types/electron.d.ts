export {}

declare global {
  interface Window {
    electronAPI?: {
      downloadUpdateInstaller: (url: string) => Promise<void>
      openLink: (url: string) => Promise<{ ok: boolean }>
    }
  }
}
