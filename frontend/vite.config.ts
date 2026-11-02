import { defineConfig } from 'vite'
import { closeSync, constants, fstatSync, lstatSync, openSync, readSync, realpathSync, type BigIntStats } from 'node:fs'
import { resolve } from 'node:path'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

const frontendBuildOutDir = process.env.FRONTEND_BUILD_OUT_DIR?.trim() || '../mini_app_dist'
const devProxyTarget = process.env.VITE_DEV_PROXY_TARGET?.trim()
  || process.env.E2E_BACKEND_BASE_URL?.trim()
  || 'http://127.0.0.1:8000'

// Only the explicitly isolated NPM browser fixture preserves its incoming Host.
// Normal development, other CI and product builds retain the existing proxy behavior.
const isolatedNpmBrowserFixture = process.env.CI === 'true'
  && process.env.NPM_SECURITY_ISOLATED_BROWSER_FIXTURE === '1'

// The synthetic staging fixture needs real HTTPS for its Secure __Host cookie.
// Only the fixed, freshly owned RUNNER_TEMP files can be read; no product TLS input.
function isolatedBrowserTls() {
  if (!isolatedNpmBrowserFixture) return undefined
  const temporaryRoot = process.env.RUNNER_TEMP
  if (!temporaryRoot) throw new Error('isolated_browser_tls_refused')
  const directory = resolve(temporaryRoot, 'npm-isolated-browser-tls')
  const frame = (value: BigIntStats) => [value.dev, value.ino, value.mode,
    value.uid, value.gid, value.nlink, value.size, value.mtimeNs, value.ctimeNs]
  const same = (left: readonly bigint[], right: readonly bigint[]) =>
    left.length === right.length && left.every((value, index) => value === right[index])
  const dir = lstatSync(directory, { bigint: true })
  if (realpathSync(directory) !== directory || !dir.isDirectory()
      || (dir.mode & 0o777n) !== 0o700n || dir.uid !== BigInt(process.getuid!())) {
    throw new Error('isolated_browser_tls_refused')
  }
  const directoryFrame = frame(dir)
  const checkDirectory = () => {
    if (realpathSync(directory) !== directory
        || !same(frame(lstatSync(directory, { bigint: true })), directoryFrame)) {
      throw new Error('isolated_browser_tls_refused')
    }
  }

  const target = new URL(devProxyTarget)
  if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1'
      || target.username || target.password || target.pathname !== '/'
      || target.search || target.hash) throw new Error('isolated_browser_proxy_refused')
  const completed: Array<{ filename: string; identity: bigint[] }> = []
  const read = (name: 'key.pem' | 'cert.pem') => {
    try {
      checkDirectory()
      const filename = resolve(directory, name)
      const before = lstatSync(filename, { bigint: true })
      if (!before.isFile() || before.uid !== BigInt(process.getuid!()) || before.nlink !== 1n
          || (before.mode & 0o777n) !== 0o600n || before.size < 1n || before.size > 65536n) {
        throw new Error('isolated_browser_tls_refused')
      }
      const identity = frame(before)
      // NONBLOCK also covers replacement with a FIFO between lstat and open.
      const descriptor = openSync(filename, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK)
      let refused = false
      let output: Buffer | undefined
      const check = () => {
        checkDirectory()
        if (!same(frame(fstatSync(descriptor, { bigint: true })), identity)
            || !same(frame(lstatSync(filename, { bigint: true })), identity)) {
          throw new Error('isolated_browser_tls_refused')
        }
      }
      try {
        check()
        const buffer = Buffer.alloc(Number(before.size) + 1)
        const bytes = readSync(descriptor, buffer, 0, buffer.length, 0)
        check()
        if (bytes !== Number(before.size)) throw new Error('isolated_browser_tls_refused')
        output = buffer.subarray(0, bytes)
      } catch {
        refused = true
      } finally {
        try { check() } catch { refused = true }
        try { closeSync(descriptor) } catch { refused = true }
        try { fstatSync(descriptor); refused = true } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'EBADF') refused = true
        }
        try {
          checkDirectory()
          if (!same(frame(lstatSync(filename, { bigint: true })), identity)) refused = true
        } catch { refused = true }
      }
      if (refused || !output) throw new Error('isolated_browser_tls_refused')
      completed.push({ filename, identity })
      return output
    } catch {
      throw new Error('isolated_browser_tls_refused')
    }
  }
  const result = { key: read('key.pem'), cert: read('cert.pem') }
  checkDirectory()
  // The last close must not change an input whose original reader already closed.
  for (const row of completed) {
    if (!same(frame(lstatSync(row.filename, { bigint: true })), row.identity)) {
      throw new Error('isolated_browser_tls_refused')
    }
  }
  return result
}
const npmFixtureTls = isolatedBrowserTls()

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
        importScripts: ['share-target-sw.js', 'upload-background-sw.js', 'push-notifications-sw.js'],
        // Don't intercept POST navigations — let our share-target handler
        // (above) own POST /share-receive, and let normal API POSTs hit the
        // network without going through the navigation fallback.
        navigateFallbackDenylist: [/^\/api\//, /^\/share-receive/],
      },
      includeAssets: [
        'favicon.ico',
        'pwa-192x192.png',
        'pwa-512x512.png',
        'pwa-maskable-192x192.png',
        'pwa-maskable-512x512.png',
        'share-target-sw.js',
        'upload-background-sw.js',
        'push-notifications-sw.js',
      ],
      manifest: {
        id: '/?source=pwa',
        name: 'Gold',
        short_name: 'Gold',
        lang: 'fa',
        dir: 'rtl',
        description: 'بازار امن معاملات طلا و سکه',
        theme_color: '#f2f2f7',
        background_color: '#f2f2f7',
        display: 'standalone',
        display_override: ['standalone', 'minimal-ui'],
        start_url: '/',
        scope: '/',
        orientation: 'portrait',
        icons: [
          {
            src: '/pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'any',
          },
          {
            src: '/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any',
          },
          {
            src: '/pwa-maskable-192x192.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'maskable',
          },
          {
            src: '/pwa-maskable-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
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
    outDir: frontendBuildOutDir,
    emptyOutDir: true,
    target: 'es2018',
  },
  preview: { https: npmFixtureTls },
  server: {
    proxy: {
      '/api': {
        target: devProxyTarget,
        changeOrigin: !isolatedNpmBrowserFixture,
        ...(isolatedNpmBrowserFixture ? { headers: { 'X-Forwarded-Proto': 'https' } } : {}),
        ws: true,
      },
    },
  },
})
