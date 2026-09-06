import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // jsdom, not the previous 'node': component tests need a real DOM
    // to render into. Pure-logic tests (lib/*.test.ts) run fine under
    // jsdom too, so this is one environment for both rather than a
    // second config to keep in sync.
    environment: 'jsdom',
    // .tsx added alongside the existing .ts pattern -- component
    // tests are .tsx (they contain JSX), plain logic tests stay .ts.
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    setupFiles: ['./src/test/setup.ts'],
  },
})
