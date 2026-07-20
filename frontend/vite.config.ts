import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// The PWA manifest (app name/icon as seen on a home screen after
// "Install") is baked in at build time -- unlike everything else in
// this app, it can't be changed at runtime from the business config
// database, because it's a static file the OS reads before the app
// (and therefore before any API call) ever runs. Each deployment sets
// these in its .env file and rebuilds; nothing here should ever be
// hardcoded to one specific pharmacy. See .env.example.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const appName = env.VITE_APP_NAME || 'Pharmacy System'
  const appShortName = env.VITE_APP_SHORT_NAME || appName
  const themeColor = env.VITE_APP_THEME_COLOR || '#1F2933'
  const backgroundColor = env.VITE_APP_BG_COLOR || '#EDE8DB'

  return {
    plugins: [
      react(),
      tailwindcss(),
      VitePWA({
        registerType: 'autoUpdate',
        includeAssets: ['favicon.svg'],
        manifest: {
          name: appName,
          short_name: appShortName,
          description: 'Point-of-sale, inventory, and back-office system',
          theme_color: themeColor,
          background_color: backgroundColor,
          display: 'standalone',
          start_url: '/',
          icons: [
            { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
            { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
            { src: 'pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          ],
        },
        workbox: {
          // Never cache API calls -- POS/inventory data must always be fresh
          // or explicitly handled offline, not silently served stale.
          navigateFallbackDenylist: [/^\/api\//],
          runtimeCaching: [
            {
              urlPattern: /^\/api\//,
              handler: 'NetworkOnly',
            },
          ],
        },
      }),
    ],
    server: {
      proxy: {
        '/api': { target: 'http://localhost:8000', changeOrigin: true },
        '/api/v1/ws': { target: 'ws://localhost:8000', ws: true },
        '/health': { target: 'http://localhost:8000', changeOrigin: true },
      },
    },
  }
})
