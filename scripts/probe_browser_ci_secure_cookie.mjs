// Synthetic loopback observation only. It neither reads App state nor relaxes
// the staging cookie policy, and a valid observation need not accept the cookie.
import http from 'node:http'
import https from 'node:https'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { previewHttps } from '../frontend/scripts/browser-ci-https.mjs'

const BROWSERS = new Set(['chromium', 'firefox', 'webkit'])
const CONTROL = 'ci_cookie_control'
const SECURE = '__Host-ci_cookie_probe'
const VALUE = 'public-synthetic-cookie'
const FAILURE = 'browser_cookie_probe_failed'

export async function bounded(promise, milliseconds = 5000) {
  let timer
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(FAILURE)), milliseconds) }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

export async function loopback(tls) {
  let requests = 0
  const handler = (request, response) => {
    response.setHeader('Connection', 'close')
    response.setHeader('Cache-Control', 'no-store')
    if (++requests > 16 || request.method !== 'GET' || !['/set', '/check', '/favicon.ico'].includes(request.url)) {
      response.writeHead(404).end()
    } else if (request.url === '/set') {
      response.setHeader('Set-Cookie', [
        `${CONTROL}=${VALUE}; Path=/; HttpOnly; SameSite=Strict`,
        `${SECURE}=${VALUE}; Path=/; Secure; HttpOnly; SameSite=Strict`,
      ])
      response.setHeader('Content-Type', 'text/html; charset=utf-8')
      response.end('<!doctype html><title>Synthetic cookie observation</title>')
    } else if (request.url === '/check') {
      const cookies = new Set((request.headers.cookie || '').split(';').map(value => value.trim()))
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({
        control: cookies.has(`${CONTROL}=${VALUE}`),
        secure: cookies.has(`${SECURE}=${VALUE}`),
      }))
    } else response.writeHead(204).end()
  }
  const server = tls ? https.createServer({ maxHeaderSize: 8192, ...tls }, handler)
    : http.createServer({ maxHeaderSize: 8192 }, handler)
  server.maxConnections = 4
  server.requestTimeout = 5000
  server.headersTimeout = 5000
  try {
    await bounded(new Promise((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    }))
    return {
      origin: `${tls ? 'https' : 'http'}://127.0.0.1:${server.address().port}`,
      async close() {
        server.closeAllConnections()
        await bounded(new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve())))
      },
    }
  } catch {
    server.closeAllConnections()
    server.close()
    throw new Error(FAILURE)
  }
}

export async function observe(browserLabel, browserType, tls) {
  if (!BROWSERS.has(browserLabel)) throw new Error(FAILURE)
  let site, ownedServer, browser, context, result
  let failed = false
  try {
    site = await loopback(tls)
    // launchServer owns its timeout; once it returns, its process is retained
    // even if connecting or creating the context fails.
    ownedServer = await browserType.launchServer({ host: '127.0.0.1', headless: true, timeout: 10000 })
    browser = await browserType.connect(ownedServer.wsEndpoint(), { timeout: 5000 })
    context = await bounded(browser.newContext({ serviceWorkers: 'block', acceptDownloads: false }))
    await bounded(context.route('**/*', route => {
      const url = new URL(route.request().url())
      return url.origin === site.origin ? route.continue() : route.abort()
    }))
    const page = await bounded(context.newPage())
    await page.goto(`${site.origin}/set`, { waitUntil: 'domcontentloaded', timeout: 5000 })
    // No URL filter: a stored Secure cookie may not apply to an HTTP URL.
    // https://playwright.dev/docs/api/class-browsercontext#browser-context-cookies
    const rows = await bounded(context.cookies())
    if (!Array.isArray(rows) || rows.length > 2 || rows.some(row => ![CONTROL, SECURE].includes(row.name))
        || new Set(rows.map(row => row.name)).size !== rows.length) {
      throw new Error(FAILURE)
    }
    const control = rows.find(row => row.name === CONTROL)
    const secure = rows.find(row => row.name === SECURE)
    const correct = row => row.value === VALUE && row.domain === '127.0.0.1'
      && row.path === '/' && row.httpOnly === true && row.sameSite === 'Strict'
    if (!control || !correct(control) || control.secure !== false || (secure && (!correct(secure) || secure.secure !== true))) {
      throw new Error(FAILURE)
    }
    const sent = await bounded(page.evaluate(async () => {
      const response = await fetch('/check', { credentials: 'same-origin', signal: AbortSignal.timeout(3000) })
      if (!response.ok) throw new Error('cookie_probe_request_failed')
      return response.json()
    }))
    if (!sent || Object.keys(sent).sort().join(',') !== 'control,secure'
        || sent.control !== true || typeof sent.secure !== 'boolean' || (sent.secure && !secure)) {
      throw new Error(FAILURE)
    }
    result = { browser: browserLabel, accepted: Boolean(secure), retransmitted: sent.secure }
  } catch {
    failed = true
  } finally {
    for (const cleanup of [
      () => context?.close(),
      () => browser?.close(),
      () => ownedServer?.close(),
    ]) {
      try { await bounded(Promise.resolve().then(cleanup), 2000) } catch { failed = true }
    }
    if (ownedServer) {
      try {
        const child = ownedServer.process()
        if (child.exitCode === null && child.signalCode === null) await bounded(ownedServer.kill(), 2000)
        if (child.exitCode === null && child.signalCode === null) throw new Error(FAILURE)
      } catch { failed = true }
    }
    try { await site?.close() } catch { failed = true }
  }
  if (failed || !result) throw new Error(FAILURE)
  return result
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const mode = process.argv[3] || 'http'
    if (![3, 4].includes(process.argv.length) || !BROWSERS.has(process.argv[2])
        || !['http', 'https'].includes(mode)) throw new Error(FAILURE)
    const tls = mode === 'https' ? previewHttps(process.env) : undefined
    if (mode === 'https' && !tls) throw new Error(FAILURE)
    const require = createRequire(new URL('../frontend/package.json', import.meta.url))
    const browserLabel = process.argv[2]
    const playwright = require('playwright')
    process.stdout.write(`${JSON.stringify(await observe(browserLabel, playwright[browserLabel], tls))}\n`)
  } catch {
    process.stderr.write(`${FAILURE}\n`)
    process.exitCode = 1
  }
}
