import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// `vite build --mode android` builds the bundle packed into the Android app
// (see capacitor.config.json). It gets no service worker: the assets ship
// inside the APK, and push goes through FCM instead of Web Push.
function checkAndroidEnv(mode) {
  if (mode !== 'android') return
  const env = loadEnv(mode, process.cwd())
  if (!/^https:\/\//.test(env.VITE_API_BASE || '')) {
    throw new Error('The Android build needs frontend/.env.android with VITE_API_BASE=https://<your server>/api ' +
      '(copy .env.android.example).')
  }
}

export default defineConfig(({ mode }) => {
  checkAndroidEnv(mode)
  return {
  plugins: [
    react(),
    mode !== 'android' && VitePWA({
      registerType: 'autoUpdate',
      // Only build/register the service worker for `vite build` — during
      // `vite dev` it would intercept requests to the Vite dev server itself
      // and make hot-reload/proxying behave unpredictably.
      // Set VITE_DEV_SW=true to exercise the service worker (and Web Push) under
      // `vite dev`; otherwise test push against `npm run build && npm run preview`.
      devOptions: { enabled: process.env.VITE_DEV_SW === 'true', type: 'classic' },
      manifest: {
        name: 'MUSAFIR',
        short_name: 'MUSAFIR',
        description: 'Digital tourist ID, safety score, geofencing, and one-tap SOS.',
        theme_color: '#0284c7',
        background_color: '#0284c7',
        display: 'standalone',
        start_url: '/app',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Push + notification-click handlers (public/push-sw.js), appended to the generated worker.
        importScripts: ['push-sw.js'],
        // The single app bundle passed Workbox's 2 MiB default (2.15 MB), which fails the
        // build. It must stay precached -- offline SOS/maps need the app shell with no
        // signal -- so the limit is raised. Follow-up: code-split the admin pages.
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
        // App shell (JS/CSS/HTML/icons) is precached automatically by the
        // plugin. Runtime caching below covers API calls specifically:
        runtimeCaching: [
          {
            // GET-only: safety score, zones, police units, itinerary --
            // reads a tourist needs while offline. Mutations (POST/PATCH/
            // DELETE) are never cached and go through the offline SOS queue
            // (src/lib/offlineQueue.js) instead of Workbox background sync,
            // since SOS needs bespoke UI feedback ("queued, will send when
            // back online") rather than a silent retry.
            urlPattern: ({ url, request }) =>
              url.pathname.startsWith('/api/') && request.method === 'GET',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-get-cache',
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 100, maxAgeSeconds: 60 * 60 * 24 }, // 1 day
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // Offline Maps & Safety Card: OSM map tiles the tourist has
            // already viewed stay visible with no signal at all. CacheFirst
            // (not NetworkFirst) -- a tile never changes, so there's no
            // reason to ever re-fetch one that's already cached.
            urlPattern: ({ url }) => /tile\.openstreetmap\.org/.test(url.hostname),
            handler: 'CacheFirst',
            options: {
              cacheName: 'map-tile-cache',
              expiration: { maxEntries: 2000, maxAgeSeconds: 60 * 60 * 24 * 30 }, // 30 days
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
  preview: {
    port: 4173,
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/ws': { target: 'ws://127.0.0.1:8001', ws: true },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/ws': { target: 'ws://127.0.0.1:8001', ws: true },
    },
  },
  test: {
    // The existing suites exercise the simulated-GPS path; the app default is real GPS.
    env: { VITE_SIMULATE_GPS: 'true' },
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{js,jsx}'],
      exclude: ['src/main.jsx', 'src/test/**'],
    },
  },
  }
})
