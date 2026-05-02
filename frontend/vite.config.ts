import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      workbox: {
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
        importScripts: ['share-target-sw.js'],
        // Don't intercept POST navigations — let our share-target handler
        // (above) own POST /share-receive, and let normal API POSTs hit the
        // network without going through the navigation fallback.
        navigateFallbackDenylist: [/^\/api\//, /^\/share-receive/],
      },
      includeAssets: ['favicon.ico', 'pwa-192x192.png', 'pwa-512x512.png', 'share-target-sw.js'],
      manifest: {
        id: '/?source=pwa',
        name: 'Gold',
        short_name: 'Gold',
        description: 'بازار امن معاملات طلا و سکه',
        theme_color: '#ffffff',
        background_color: '#ffffff',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        orientation: 'portrait',
        icons: [
          {
            src: '/pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'any maskable'
          },
          {
            src: '/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable'
          }
        ],
        share_target: {
          action: '/share-receive',
          method: 'POST',
          enctype: 'multipart/form-data',
          params: {
            title: 'title',
            text: 'text',
            url: 'url',
            files: [
              {
                name: 'files',
                accept: [
                  'image/*',
                  'video/*',
                  'audio/*',
                  'application/pdf',
                  'application/zip',
                  'application/x-zip-compressed',
                  'application/msword',
                  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                  'application/vnd.ms-excel',
                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                  'application/vnd.ms-powerpoint',
                  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                  'text/plain',
                  'text/csv',
                ],
              },
            ],
          },
        },
      }
    })
  ],
  build: {
    outDir: '../mini_app_dist',
    emptyOutDir: true,
    target: 'es2020',
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
