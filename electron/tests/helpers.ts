import type { ElectronApplication, Page } from '@playwright/test'

// The app opens a small splash window (a small data: page, never the real app) before the
// real one, and closes it once the real window is ready. electronApp.firstWindow()
// returns whichever window opened first -- the splash -- so tests that need the
// real app must wait for the window the local backend serves instead.
export async function mainWindow(
  electronApp: ElectronApplication,
  timeout = 60_000,
): Promise<Page> {
  const isMain = (page: Page) => /^http:\/\/127\.0\.0\.1:\d+\//.test(page.url())
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const found = electronApp.windows().find(isMain)
    if (found) return found
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error('The main app window (served from 127.0.0.1) never appeared')
}
