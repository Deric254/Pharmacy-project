import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  // These launch a real Electron process with a real spawned Python
  // backend each -- running them in parallel would mean multiple
  // Python processes competing for the same port. One at a time.
  workers: 1,
  timeout: 90_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
