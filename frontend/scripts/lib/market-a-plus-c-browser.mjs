import { createHash } from 'node:crypto'
import { createServer } from 'node:http'
import { execFileSync } from 'node:child_process'
import { readFile, stat } from 'node:fs/promises'
import fs from 'node:fs'
import path from 'node:path'

export const PHASES = Object.freeze(['baseline', 'candidate'])
export const FIXED_TIME = '2026-08-14T12:00:00.000Z'
export const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
export const VIEWPORTS = Object.freeze([
  { id: '360x740', width: 360, height: 740 },
  { id: '375x812', width: 375, height: 812 },
  { id: '390x844', width: 390, height: 844 },
  { id: '414x896', width: 414, height: 896 },
  { id: '430x932', width: 430, height: 932 },
  { id: '768x1024', width: 768, height: 1024 },
  { id: '1024x768', width: 1024, height: 768 },
  { id: '1440x900', width: 1440, height: 900 },
])
export const MIME_TYPES = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.ico', 'image/x-icon'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.webmanifest', 'application/manifest+json; charset=utf-8'],
  ['.woff2', 'font/woff2'],
])

const OWNER_ID = 1001

export function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

export function sha256File(filePath) {
  return sha256(fs.readFileSync(filePath))
}

export function gitText(repo, args) {
  return execFileSync('git', args, {
    cwd: repo,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).replace(/\n$/u, '')
}

export function gitSnapshot(repo) {
  const branch = gitText(repo, ['branch', '--show-current']).trim()
  const status = gitText(repo, ['status', '--porcelain=v1', '--untracked-files=all'])
  return {
    branch: branch || null,
    commit: gitText(repo, ['rev-parse', 'HEAD']).trim(),
    tree: gitText(repo, ['rev-parse', 'HEAD^{tree}']).trim(),
    parents: gitText(repo, ['rev-parse', 'HEAD^@'])
      .trim()
      .split('\n')
      .filter(Boolean),
    status,
    clean: status.trim() === '',
  }
}

export function makeJwt(userId) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64url')
  const now = Math.floor(Date.now() / 1000)
  return `${encode({ alg: 'none', typ: 'JWT' })}.${encode({
    sub: String(userId || OWNER_ID),
    exp: now + 3600,
    session_id: 'synthetic-market-a-plus-c-session',
  })}.synthetic`
}

export function ownerUser(overrides = {}) {
  return {
    id: OWNER_ID,
    role: 'کاربر',
    account_status: 'active',
    is_customer: false,
    is_accountant: false,
    customer_tier: null,
    full_name: 'کاربر بازار',
    account_name: 'market_owner',
    telegram_linked: false,
    can_connect_telegram: false,
    telegram_link_denial_reason: null,
    global_lock_grace_expires_at: null,
    global_web_locked_at: null,
    trading_restricted_until: null,
    offer_overtime_minutes: 3,
    ...overrides,
  }
}

export function fixtureOffer(index = 0, overrides = {}) {
  const nowSec = Math.floor(Date.now() / 1000)
  const sides = ['buy', 'sell']
  const settlements = ['cash', 'tomorrow']
  const names = ['سکه امامی', 'نیم سکه', 'ربع سکه', 'طلای آب‌شده']
  return {
    id: 501 + index,
    offer_public_id: `market-ac-offer-${501 + index}`,
    user_id: index === 2 ? OWNER_ID : 15 + index,
    is_own_offer: index === 2,
    offer_type: sides[index % 2],
    settlement_type: settlements[index % 2],
    commodity_id: 1 + (index % 3),
    commodity_name: names[index % names.length],
    quantity: index === 3 ? 8 : 4,
    remaining_quantity: index === 3 ? 3 : 4,
    raw_price: 78450000 - index * 100000,
    price: 78450000 - index * 100000,
    viewer_effective_price: 78450000 - index * 100000,
    is_wholesale: false,
    lot_sizes: index === 3 ? [1, 2, 3] : [1, 2, 4],
    original_lot_sizes: index === 3 ? [1, 2, 3] : [1, 2, 4],
    notes: index ? `یادداشت مصنوعی ${index}` : null,
    status: 'active',
    created_at: '۲ دقیقه پیش',
    lifecycle_phase: 'normal',
    normal_deadline_ts: nowSec + 1800,
    expires_at_ts: nowSec + 1800,
    timer_total_seconds: 3600,
    customer_badge_visible: false,
    accepts_new_public_interaction: true,
    ...overrides,
  }
}

function historyForMode(mode) {
  if (mode === 'expired') {
    return [fixtureOffer(5, { history_state: 'expired', status: 'expired', is_read_only: true, lifecycle_phase: 'expired' })]
  }
  if (mode === 'traded') {
    return [fixtureOffer(4, { history_state: 'traded', status: 'completed', is_read_only: true, traded_quantity: 4 })]
  }
  if (mode === 'partially-traded') {
    return [fixtureOffer(4, { history_state: 'traded', status: 'completed', is_read_only: true, traded_quantity: 2, is_partially_traded: true, quantity: 6 })]
  }
  if (mode === 'traded-overtime') {
    return [fixtureOffer(4, { history_state: 'traded', status: 'completed', is_read_only: true, traded_quantity: 3, overtime_trade_committed: true })]
  }
  if (mode === 'terminal-mix') {
    return [
      fixtureOffer(5, { history_state: 'expired', status: 'expired', is_read_only: true, lifecycle_phase: 'expired' }),
      fixtureOffer(4, { history_state: 'traded', status: 'completed', is_read_only: true, traded_quantity: 2, is_partially_traded: true }),
    ]
  }
  if (mode === 'normal' || mode === 'dense') {
    return [
      fixtureOffer(4, {
        history_state: 'traded',
        status: 'completed',
        is_read_only: true,
        traded_quantity: 2,
        is_partially_traded: true,
      }),
      fixtureOffer(5, {
        history_state: 'expired',
        status: 'expired',
        is_read_only: true,
      }),
    ]
  }
  return []
}

function listForMode(mode) {
  const nowSec = Math.floor(Date.now() / 1000)
  if (mode === 'empty' || mode === 'loading') return []
  if (mode === 'dense') return Array.from({ length: 18 }, (_, index) => fixtureOffer(index))
  if (mode === 'stale-old') return [fixtureOffer(0, { notes: 'کهنه-بازار' })]
  if (mode === 'stale-new') return [fixtureOffer(0, { notes: 'تازه-بازار' })]
  if (mode === 'normal-buy') {
    return [fixtureOffer(0, { offer_type: 'buy', lifecycle_phase: 'normal', normal_deadline_ts: nowSec + 1800, expires_at_ts: nowSec + 1800, timer_total_seconds: 3600 })]
  }
  if (mode === 'normal-sell') {
    return [fixtureOffer(1, { offer_type: 'sell', lifecycle_phase: 'normal', normal_deadline_ts: nowSec + 1800, expires_at_ts: nowSec + 1800, timer_total_seconds: 3600 })]
  }
  if (mode === 'critical-normal') {
    return [fixtureOffer(0, { offer_type: 'sell', lifecycle_phase: 'normal', normal_deadline_ts: nowSec + 200, expires_at_ts: nowSec + 200, timer_total_seconds: 3600 })]
  }
  if (mode === 'overtime-buy') {
    return [fixtureOffer(0, {
      offer_type: 'buy',
      lifecycle_phase: 'overtime',
      normal_deadline_ts: nowSec - 60,
      final_deadline_ts: nowSec + 240,
      expires_at_ts: nowSec + 240,
      timer_total_seconds: 300,
      accepts_overtime_request: true,
    })]
  }
  if (mode === 'overtime-sell') {
    return [fixtureOffer(1, {
      offer_type: 'sell',
      lifecycle_phase: 'overtime',
      normal_deadline_ts: nowSec - 60,
      final_deadline_ts: nowSec + 240,
      expires_at_ts: nowSec + 240,
      timer_total_seconds: 300,
      accepts_overtime_request: true,
    })]
  }
  if (mode === 'critical-overtime') {
    return [fixtureOffer(1, {
      offer_type: 'sell',
      lifecycle_phase: 'overtime',
      normal_deadline_ts: nowSec - 60,
      final_deadline_ts: nowSec + 20,
      expires_at_ts: nowSec + 20,
      timer_total_seconds: 300,
      accepts_overtime_request: true,
    })]
  }
  if (mode === 'final-tail') {
    return [fixtureOffer(0, {
      lifecycle_phase: 'final_tail',
      normal_deadline_ts: nowSec - 10,
      final_deadline_ts: nowSec - 5,
      expires_at_ts: nowSec - 5,
      timer_total_seconds: 0,
      accepts_new_public_interaction: false,
      accepts_overtime_request: false,
      accepts_automatic_trade: false,
    })]
  }
  if (['expired', 'traded', 'partially-traded', 'traded-overtime', 'terminal-mix'].includes(mode)) {
    return []
  }
  if (mode === 'own-offer') {
    return [fixtureOffer(2, { user_id: OWNER_ID, is_own_offer: true })]
  }
  return [
    fixtureOffer(0),
    fixtureOffer(1),
    fixtureOffer(2),
    fixtureOffer(3),
    fixtureOffer(4, {
      history_state: 'traded',
      status: 'completed',
      is_read_only: true,
      traded_quantity: 2,
      is_partially_traded: true,
    }),
    fixtureOffer(5, {
      history_state: 'expired',
      status: 'expired',
      is_read_only: true,
    }),
  ]
}

function known(body, status = 200) {
  return { known: true, status, body }
}

export function isAllowedMutation(pathname, method) {
  if (pathname === '/api/sessions/verify' && method === 'POST') return true
  if (pathname === '/api/auth/refresh' && method === 'POST') return true
  if (pathname === '/api/offers/parse' && method === 'POST') return true
  if ((pathname === '/api/trades/' || pathname === '/api/trades') && method === 'POST') return true
  return false
}

export function apiFixture(pathname, method, controller) {
  const mode = controller.mode || 'normal'
  if (MUTATING_METHODS.has(method) && !isAllowedMutation(pathname, method)) {
    return { known: false, status: 405, body: { detail: 'mutating method blocked' }, mutating: true }
  }
  const identity =
    pathname === '/api/auth/me' ||
    pathname === '/api/auth/me/' ||
    pathname === '/api/auth/refresh' ||
    pathname === '/api/sessions/verify'
  if (!identity && mode === 'offline') {
    return { known: true, status: 503, body: { detail: 'synthetic offline' }, offline: true }
  }
  if (!identity && mode === 'error' && pathname.startsWith('/api/offers')) {
    return { known: true, status: 500, body: { detail: 'synthetic error fixture' }, injectedError: true }
  }

  if (pathname === '/api/auth/me' || pathname === '/api/auth/me/') {
    return known(ownerUser(controller.userOverrides || {}))
  }
  if (pathname === '/api/auth/refresh') {
    return known({ access_token: makeJwt(OWNER_ID), refresh_token: makeJwt(OWNER_ID) })
  }
  if (pathname === '/api/sessions/verify') return known({ ok: true })
  if (pathname === '/api/sessions/recovery/pending' || pathname === '/api/sessions/login-requests/pending') {
    return known([])
  }
  if (
    pathname === '/api/trades/overtime-requests/pending-owner' ||
    pathname === '/api/trades/overtime-requests/pending-requester'
  ) {
    return known([])
  }
  if (pathname === '/api/auth/switchable-users') return known([])
  if (pathname === '/api/config') {
    return known({ bot_username: 'synthetic_bot', telegram_bot_username: 'synthetic_bot' })
  }
  if (pathname === '/api/notifications/preferences') {
    return known({ market_offer_push_enabled: controller.notificationEnabled !== false })
  }
  if (pathname === '/api/notifications/push/public-key') {
    return known({ enabled: false, public_key: null, missing: [] })
  }
  if (pathname === '/api/notifications/unread-count') return known(0)
  if (pathname === '/api/trading-settings/') {
    return known({
      offer_min_quantity: 1,
      offer_max_quantity: 1000,
      lot_min_size: 1,
      lot_max_count: 5,
      offer_expiry_minutes: 60,
    })
  }
  if (pathname === '/api/trading-settings/market-state') {
    return known({
      is_open: controller.marketOpen !== false,
      active_web_notice_visible: controller.noticeVisible === true,
      offers_since_last_open: 2,
      last_transition_at: FIXED_TIME,
      next_transition_at: null,
    })
  }
  if (pathname === '/api/admin-messages/market/current') {
    if (controller.adminMessage === true) {
      return known({
        id: 77,
        content: 'پیام مدیریت مصنوعی · قیمت و تسویه را بررسی کنید.',
        is_active: true,
        published_at: FIXED_TIME,
      })
    }
    return known(null)
  }
  if (pathname === '/api/commodities/' || pathname === '/api/commodities') {
    return known([
      { id: 1, name: 'سکه امامی' },
      { id: 2, name: 'نیم سکه' },
      { id: 3, name: 'طلای آب‌شده' },
    ])
  }
  if (pathname.startsWith('/api/offers/page')) {
    return known({ items: listForMode(mode), next_cursor: null, has_more: false })
  }
  if (pathname.startsWith('/api/offers/market-history')) return known(historyForMode(mode))
  if (pathname.startsWith('/api/offers/my/repeatable')) {
    return known([
      {
        id: 9001,
        offer_public_id: 'recent-9001',
        offer_type: 'buy',
        settlement_type: 'cash',
        commodity_id: 1,
        commodity_name: 'سکه امامی',
        quantity: 2,
        remaining_quantity: 2,
        raw_price: 78450000,
        price: 78450000,
        is_wholesale: false,
        lot_sizes: [1, 2],
        original_lot_sizes: [1, 2],
        notes: null,
        status: 'completed',
        created_at: FIXED_TIME,
      },
    ])
  }
  if ((pathname === '/api/trades/' || pathname === '/api/trades') && method === 'POST') {
    return known({
      id: 8801,
      status: 'completed',
      quantity: 1,
      fixture_bound: true,
    })
  }
  if (pathname === '/api/offers/parse' && method === 'POST') {
    return known({
      success: true,
      data: {
        trade_type: 'buy',
        settlement_type: 'cash',
        commodity_id: 1,
        commodity_name: 'سکه امامی',
        quantity: 2,
        price: 78450000,
        is_wholesale: false,
        lot_sizes: [1, 2],
        notes: null,
      },
    })
  }
  if (pathname === '/api/offers/' || pathname === '/api/offers') {
    return known(listForMode(mode))
  }
  if (pathname === '/api/chat/poll') {
    return known({
      conversations_with_unread: [],
      muted_conversation_ids: [],
      unread_chats_count: 0,
      total_unread_mentions: 0,
    })
  }
  return { known: false, status: 599, body: { detail: 'unexpected synthetic fixture request' } }
}

export async function createFixtureServer(distDir, controller, serverState) {
  const server = createServer(async (request, response) => {
    const url = new URL(request.url || '/', 'http://127.0.0.1')
    const method = String(request.method || 'GET').toUpperCase()
    const pathname = url.pathname
    if (pathname.startsWith('/api/')) {
      serverState.apiRequests += 1
      const fixture = apiFixture(pathname, method, controller)
      if (!fixture.known && !fixture.mutating) {
        if (!Array.isArray(serverState.unknownPaths)) serverState.unknownPaths = []
        serverState.unknownPaths.push(`${method} ${pathname}`)
      }
      if (controller.delayMs > 0 && pathname.startsWith('/api/offers/page')) {
        await new Promise((resolve) => setTimeout(resolve, controller.delayMs))
      }
      if (fixture.mutating) {
        serverState.mutatingApiRequests += 1
      } else if (fixture.known) {
        serverState.knownApiRequests += 1
        if (fixture.injectedError) serverState.injectedErrorResponses += 1
      } else {
        serverState.unknownApiRequests += 1
      }
      response.writeHead(fixture.status, { 'content-type': 'application/json; charset=utf-8' })
      response.end(JSON.stringify(fixture.body))
      return
    }
    let filePath = path.join(distDir, pathname === '/' ? 'index.html' : pathname)
    try {
      const fileStat = await stat(filePath)
      if (fileStat.isDirectory()) filePath = path.join(filePath, 'index.html')
    } catch {
      filePath = path.join(distDir, 'index.html')
    }
    const ext = path.extname(filePath)
    const body = await readFile(filePath)
    response.writeHead(200, { 'content-type': MIME_TYPES.get(ext) || 'application/octet-stream' })
    response.end(body)
  })
  return server
}

export function listen(server) {
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address !== 'object') {
        reject(new Error('fixture server did not bind'))
        return
      }
      resolve(`http://127.0.0.1:${address.port}`)
    })
    server.on('error', reject)
  })
}

export function closeServer(server) {
  return new Promise((resolve) => server.close(() => resolve()))
}

export function newDiagnostics() {
  return {
    console: [],
    pageErrors: [],
    requestFailures: [],
    externalRequests: [],
    companionRequests: [],
    mutatingRequests: [],
  }
}

function classifyConsole(item) {
  const text = String(item.text || '')
  if (text.includes('Browserslist')) return 'fixture-ignored'
  if (text.includes('WebSocket') || text.includes('websocket')) return 'fixture-websocket'
  if (text.includes('synthetic error fixture') || text.includes('synthetic offline')) {
    return 'fixture-injected-state'
  }
  return 'unexpected'
}

export function diagnosticCounts(diagnostics) {
  const unexpectedConsole = diagnostics.console.filter((item) => classifyConsole(item) === 'unexpected')
  return {
    unexpectedConsole: unexpectedConsole.length,
    pageErrors: diagnostics.pageErrors.length,
    requestFailures: diagnostics.requestFailures.length,
    externalRequests: diagnostics.externalRequests.length,
    mutatingRequests: diagnostics.mutatingRequests.length,
  }
}

export async function instrumentPage(page, baseUrl, diagnostics, user) {
  const token = makeJwt(user.id)
  await page.addInitScript(
    ({ authToken, userValue }) => {
      window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
      localStorage.clear()
      sessionStorage.clear()
      localStorage.setItem('auth_token', authToken)
      localStorage.setItem('refresh_token', authToken)
      localStorage.setItem('current_user_summary', JSON.stringify(userValue))
      localStorage.setItem('current_user_role', userValue.role)
      localStorage.setItem('current_user_account_status', userValue.account_status)
      localStorage.setItem('current_user_is_accountant', String(userValue.is_accountant))
      localStorage.setItem('current_user_is_customer', String(userValue.is_customer))
      try {
        navigator.serviceWorker.getRegistration = async () => undefined
        navigator.serviceWorker.getRegistrations = async () => []
      } catch {
        // Ignore storage-less workers.
      }
      class LocalWebSocket extends EventTarget {
        static CONNECTING = 0
        static OPEN = 1
        static CLOSED = 3
        readyState = LocalWebSocket.CONNECTING
        onopen = null
        onclose = null
        onerror = null
        onmessage = null
        constructor(url) {
          super()
          this.url = String(url)
          queueMicrotask(() => {
            this.readyState = LocalWebSocket.OPEN
            const event = new Event('open')
            this.onopen?.(event)
            this.dispatchEvent(event)
          })
        }
        send() {}
        close() {
          if (this.readyState === LocalWebSocket.CLOSED) return
          this.readyState = LocalWebSocket.CLOSED
          const event = new CloseEvent('close')
          this.onclose?.(event)
          this.dispatchEvent(event)
        }
      }
      globalThis.WebSocket = LocalWebSocket
    },
    { authToken: token, userValue: user },
  )
  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      diagnostics.console.push({ type: message.type(), text: message.text().slice(0, 180) })
    }
  })
  page.on('pageerror', (error) => diagnostics.pageErrors.push(String(error).slice(0, 180)))
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText || 'unknown'
    if (failure === 'net::ERR_ABORTED' || failure === 'blockedbyclient') return
    diagnostics.requestFailures.push({
      method: request.method(),
      path: new URL(request.url()).pathname,
      failure,
    })
  })
  await page.route('**/*', async (route) => {
    const request = route.request()
    let url
    try {
      url = new URL(request.url())
    } catch {
      return route.continue()
    }
    const method = request.method()
    if (['chrome-extension:', 'devtools:', 'data:', 'blob:', 'about:'].includes(url.protocol)) {
      return route.continue()
    }
    if (MUTATING_METHODS.has(method) && !isAllowedMutation(url.pathname, method)) {
      diagnostics.mutatingRequests.push({ method, path: url.pathname })
      return route.abort('blockedbyclient')
    }
    if (url.origin === baseUrl) return route.continue()
    if (url.origin === 'https://telegram.org' && url.pathname === '/js/telegram-web-app.js') {
      diagnostics.companionRequests = diagnostics.companionRequests || []
      diagnostics.companionRequests.push({ method, origin: url.origin, path: url.pathname })
      return route.fulfill({
        status: 200,
        contentType: 'application/javascript',
        body: 'window.Telegram = window.Telegram || { WebApp: { ready() {}, expand() {}, onEvent() {}, offEvent() {} } };',
      })
    }
    diagnostics.externalRequests.push({ method, origin: url.origin, path: url.pathname })
    return route.abort('blockedbyclient')
  })
}

export async function newPage(browser, baseUrl, viewport, diagnostics, user, options = {}) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: options.deviceScaleFactor || 1,
    locale: 'fa-IR',
    timezoneId: 'Asia/Tehran',
    serviceWorkers: 'block',
    reducedMotion: options.reducedMotion ? 'reduce' : 'no-preference',
  })
  const page = await context.newPage()
  await instrumentPage(page, baseUrl, diagnostics, user)
  return { context, page }
}

export async function waitForApp(page, timeout = 20000) {
  await page.locator('#app').waitFor({ state: 'visible', timeout })
  const boot = page.locator('#boot-loader')
  if ((await boot.count()) > 0) {
    await boot.waitFor({ state: 'hidden', timeout }).catch(() => {})
  }
  await page.waitForTimeout(160)
}

export async function collectMarketProbe(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      if (!(element instanceof HTMLElement)) return false
      const style = getComputedStyle(element)
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
        return false
      }
      const rect = element.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0
    }
    const accessibleName = (element) => {
      const ariaLabel = element.getAttribute('aria-label')
      if (ariaLabel?.trim()) return ariaLabel.trim()
      const labelledBy = element.getAttribute('aria-labelledby')
      if (labelledBy) {
        const text = labelledBy
          .split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent || '')
          .join(' ')
          .trim()
        if (text) return text
      }
      if (element.getAttribute('title')?.trim()) return element.getAttribute('title').trim()
      return (element.textContent || '').trim()
    }
    const interactives = [...document.querySelectorAll('a,button,input,select,textarea,[role="button"],[role="tab"]')]
      .filter(visible)
    const unnamed = interactives.filter((element) => accessibleName(element).length === 0)
    const nested = interactives.filter((element) =>
      interactives.some((other) => other !== element && other.contains(element)),
    )
    const smallTargets = interactives.filter((element) => {
      const rect = element.getBoundingClientRect()
      return rect.width < 44 || rect.height < 44
    })
    const tradeButtons = [...document.querySelectorAll('[data-test="trade-action-button"]')].filter(visible)
    const smallTradeTargets = tradeButtons.filter((element) => {
      const rect = element.getBoundingClientRect()
      return rect.width < 44 || rect.height < 44
    })
    const docOverflow = document.documentElement.scrollWidth - window.innerWidth > 1
    const marketPage = document.querySelector('.market-page')
    const marketOverflow = marketPage instanceof HTMLElement
      ? marketPage.scrollWidth - marketPage.clientWidth > 1
      : false
    const filterStrip = document.querySelector('.market-filter-strip')
    const filterStripOverflow =
      filterStrip instanceof HTMLElement && filterStrip.scrollWidth - filterStrip.clientWidth > 1
    const tradeStrip = document.querySelector('.trade-buttons')
    const tradeStripOverflow =
      tradeStrip instanceof HTMLElement && tradeStrip.scrollWidth - tradeStrip.clientWidth > 1
    const fab = document.querySelector('.bottom-nav-wrapper, .ui-v2-bottom-nav, .bottom-nav-bar, [data-test="nav-fab"]')
    const composer = document.querySelector('.market-action-bar')
    const lastOffer = [...document.querySelectorAll('[data-test="offer-card"]')].filter(visible).at(-1)
    const lastOfferRect = lastOffer instanceof HTMLElement ? lastOffer.getBoundingClientRect() : null
    const composerRect = composer instanceof HTMLElement && visible(composer)
      ? composer.getBoundingClientRect()
      : null
    const lastOfferAction = lastOffer instanceof HTMLElement
      ? [...lastOffer.querySelectorAll('[data-test="trade-action-button"], .cancel-own-offer-btn, [data-test="offer-decision-cancel"]')].filter(visible).at(-1)
      : null
    const lastOfferActionRect = lastOfferAction instanceof HTMLElement ? lastOfferAction.getBoundingClientRect() : lastOfferRect
    const lastActionAboveComposer = !lastOfferActionRect || !composerRect
      ? true
      : lastOfferActionRect.bottom <= composerRect.top + 8
    const headings = [...document.querySelectorAll('h1,h2,h3')].filter(visible).map((el) => el.textContent?.trim())
    const overtimeInMarket = Boolean(
      document.querySelector('.market-page .market-overtime-pref, .market-page [data-test="offer-overtime"]'),
    )
    const persianMarker = Boolean(document.querySelector('.app-route--persian-typography'))
    const v2Scope = Boolean(document.querySelector('[data-ui-system="v2"]'))
    const dir = document.documentElement.getAttribute('dir') || document.documentElement.dir
    const font = getComputedStyle(document.body).fontFamily
    const decisionPanel = document.querySelector('[data-test="offer-decision-panel"]')
    const pendingButtons = [...document.querySelectorAll('[data-test="trade-action-button"][data-state="pending"]')]
    const rectOf = (selector) => {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement) || !visible(element)) return null
      const rect = element.getBoundingClientRect()
      return {
        width: Number(rect.width.toFixed(2)),
        height: Number(rect.height.toFixed(2)),
        left: Number(rect.left.toFixed(2)),
        right: Number(rect.right.toFixed(2)),
        top: Number(rect.top.toFixed(2)),
        bottom: Number(rect.bottom.toFixed(2)),
      }
    }
    const parseRgb = (value) => {
      const match = String(value || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/)
      if (!match) return null
      return [Number(match[1]), Number(match[2]), Number(match[3])]
    }
    const luminance = (rgb) => {
      const linear = rgb.map((channel) => {
        const value = channel / 255
        return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    }
    const contrastRatio = (a, b) => {
      if (!a || !b) return null
      const first = luminance(a)
      const second = luminance(b)
      const lighter = Math.max(first, second)
      const darker = Math.min(first, second)
      return Number(((lighter + 0.05) / (darker + 0.05)).toFixed(2))
    }
    const focused = document.activeElement
    let focusContrast = null
    if (focused instanceof HTMLElement) {
      const style = getComputedStyle(focused)
      const surface = focused.closest('[data-test="offer-card"], .offer-preview-card, .trade-suggestion-card, .market-page, body')
      const surfaceStyle = getComputedStyle(surface instanceof HTMLElement ? surface : document.body)
      const outline = parseRgb(style.outlineColor)
      const adjacent = parseRgb(surfaceStyle.backgroundColor) || parseRgb('rgb(255, 255, 255)')
      focusContrast = {
        outline: style.outlineColor,
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        background: surfaceStyle.backgroundColor,
        ratio: contrastRatio(outline, adjacent),
      }
    }
    const deadlinePerimeter = document.querySelector('[data-test="offer-deadline-perimeter"]')
    const deadlineLabel = document.querySelector('[data-test="offer-deadline-label"]')
    const deadlineCard = deadlinePerimeter?.closest('[data-test="offer-card"]')
    const timerPct = deadlineCard instanceof HTMLElement
      ? Number(getComputedStyle(deadlineCard).getPropertyValue('--t-pct').trim() || 'NaN')
      : null
    const deadlineRect = deadlinePerimeter instanceof SVGElement
      ? deadlinePerimeter.getBoundingClientRect()
      : null
    const deadlineCardRect = deadlineCard instanceof HTMLElement
      ? deadlineCard.getBoundingClientRect()
      : null
    // The absolutely positioned SVG sits inside the card's 1px border box.
    // CSS page zoom scales that physical inset, so scale only this tolerance.
    const documentZoom = Number(getComputedStyle(document.documentElement).zoom) || 1
    const perimeterTolerance = 2.5 * Math.max(1, documentZoom)
    const perimeterMatchesCard = Boolean(
      deadlineRect
      && deadlineCardRect
      && Math.abs(deadlineRect.left - deadlineCardRect.left) <= perimeterTolerance
      && Math.abs(deadlineRect.top - deadlineCardRect.top) <= perimeterTolerance
      && Math.abs(deadlineRect.width - deadlineCardRect.width) <= perimeterTolerance
      && Math.abs(deadlineRect.height - deadlineCardRect.height) <= perimeterTolerance,
    )
    const perimeterValue = document.querySelector('.offer-deadline-perimeter__value')
    const lastAction = [...document.querySelectorAll('[data-test="trade-action-button"], .cancel-own-offer-btn, [data-test="offer-decision-cancel"]')]
      .filter(visible)
      .at(-1)
    let lastActionHit = null
    if (lastAction instanceof HTMLElement) {
      const rect = lastAction.getBoundingClientRect()
      const x = rect.left + rect.width / 2
      const y = rect.top + rect.height / 2
      const topNode = document.elementFromPoint(x, y)
      lastActionHit = {
        x,
        y,
        hit: Boolean(topNode && (topNode === lastAction || lastAction.contains(topNode))),
        topTag: topNode instanceof HTMLElement ? topNode.tagName : null,
      }
    }
    return {
      route: location.pathname,
      dir,
      font,
      docOverflow,
      marketOverflow,
      filterStripOverflow,
      tradeStripOverflow,
      lastActionAboveComposer,
      headingCount: headings.length,
      headings,
      unnamedCount: unnamed.length,
      nestedInteractiveCount: nested.length,
      smallTargetCount: smallTargets.length,
      tradeButtonCount: tradeButtons.length,
      smallTradeTargetCount: smallTradeTargets.length,
      offerCount: document.querySelectorAll('[data-test="offer-card"]').length,
      buyBadgeCount: [...document.querySelectorAll('.role-badge.buy')].filter(visible).length,
      sellBadgeCount: [...document.querySelectorAll('.role-badge.sell')].filter(visible).length,
      remainingCount: document.querySelectorAll('[data-test="offer-remaining"], .quantity-badge').length,
      decisionPanelVisible: decisionPanel instanceof HTMLElement && visible(decisionPanel),
      pendingCount: pendingButtons.length,
      previewVisible: Boolean(document.querySelector('[data-test="offer-preview-card"]')),
      previewRecapVisible: Boolean(document.querySelector('[data-test="offer-preview-recap"]')),
      marketTitleVisible: Boolean(
        [...document.querySelectorAll('.market-page-title')].some((el) => el.textContent?.trim() === 'بازار'),
      ),
      recentMenuVisible: Boolean(document.querySelector('[data-test="recent-offers-dropdown"]')),
      loadingVisible: Boolean(document.querySelector('[data-test="offers-loading-skeleton"]')),
      emptyVisible: Boolean(document.querySelector('[data-test="offers-empty-state"]')),
      noticeText: document.querySelector('.market-runtime-notice')?.textContent?.trim() || '',
      adminMessageVisible: Boolean(document.querySelector('.admin-market-message')),
      overtimeInMarket,
      persianMarker,
      v2Scope,
      fabPresent: fab instanceof HTMLElement && visible(fab),
      composerPresent: composer instanceof HTMLElement && visible(composer),
      geometry: {
        viewport: { width: window.innerWidth, height: window.innerHeight },
        title: rectOf('.market-title-row'),
        header: rectOf('.header-controls'),
        content: rectOf('.content-inner'),
        composer: rectOf('.action-bar-inner'),
        offers: rectOf('.offers-list'),
        firstCard: rectOf('[data-test="offer-card"]'),
      },
      decisionText: decisionPanel instanceof HTMLElement ? decisionPanel.textContent?.replace(/\s+/g, ' ').trim() : '',
      previewText: document.querySelector('[data-test="offer-preview-recap"]')?.textContent?.replace(/\s+/g, ' ').trim() || '',
      overtimeStickerVisible: Boolean(document.querySelector('[data-test="offer-overtime-sticker"]')),
      finalTailBadgeVisible: Boolean(document.querySelector('[data-test="offer-final-tail-badge"]')),
      overtimeTradeBadgeVisible: Boolean(document.querySelector('[data-test="offer-overtime-trade-badge"]')),
      deadline: {
        present: deadlinePerimeter instanceof SVGElement,
        phase: deadlinePerimeter instanceof SVGElement ? deadlinePerimeter.getAttribute('data-phase') : null,
        critical: deadlinePerimeter instanceof SVGElement ? deadlinePerimeter.getAttribute('data-critical') : null,
        label: deadlineLabel?.textContent?.trim() || '',
        pct: Number.isFinite(timerPct) ? Number(timerPct.toFixed(2)) : null,
        perimeterMatchesCard,
        strokeDasharray: perimeterValue instanceof SVGElement
          ? getComputedStyle(perimeterValue).strokeDasharray
          : '',
      },
      focusContrast,
      lastActionHit,
      animationNames: [...document.querySelectorAll('.offer-overtime-sticker__icon, .offer-deadline-perimeter__value')].map((element) =>
        getComputedStyle(element).animationName,
      ),
    }
  })
}

export function distFingerprint(distDir) {
  const files = []
  const walk = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const entryPath = path.join(directory, entry.name)
      if (entry.isDirectory()) walk(entryPath)
      else files.push(entryPath)
    }
  }
  walk(distDir)
  files.sort()
  const hash = createHash('sha256')
  for (const filePath of files) {
    hash.update(path.relative(distDir, filePath))
    hash.update(fs.readFileSync(filePath))
  }
  return { fileCount: files.length, sha256: hash.digest('hex') }
}
