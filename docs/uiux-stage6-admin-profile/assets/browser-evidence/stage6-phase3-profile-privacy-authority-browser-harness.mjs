#!/usr/bin/env node

/*
 * Stage 6 / Phase 3 browser evidence harness
 *
 * This is an ignored, evidence-only local harness. It binds an exact, clean
 * implementation commit before it starts Vite or Chromium; all backend data
 * is synthetic and every browser request outside the local Vite origin is
 * intercepted before it can leave the browser.
 *
 * Scope:
 * - public-profile PII projection and ID-only route canonicalization;
 * - self contact/address affordance;
 * - admin self / super-peer read-only authority UI;
 * - bounded public-profile 403/404 recovery;
 * - notification-center, toast and browser-notification route entries;
 * - 360/390/430/1440 reflow, focus and reduced-motion checks.
 *
 * It does not deploy, publish Sites, contact a real backend, change product
 * source, or claim Messenger/Forward discovery coverage.
 */

import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'

const WORKTREE = '/tmp/trading-bot-webapp-uiux-redesign-v2'
const EXPECTED_BRANCH = 'condidate/webapp-ui-ux-redesign-v2'
const FRONTEND = path.join(WORKTREE, 'frontend')
const HARNESS_PATH = fileURLToPath(import.meta.url)
const EVIDENCE_DIR = path.dirname(HARNESS_PATH)
const RUN_AUTHORIZATION = 'STAGE6 PHASE3 PROFILE PRIVACY & AUTHORITY — RUN'
const FIXED_TIME = '2026-08-11T18:00:00.000Z'
const FIXED_EPOCH_SECONDS = Math.floor(Date.parse(FIXED_TIME) / 1000)
const LEGACY_PROFILE_QUERY_SENTINEL = 'stage6-phase3-legacy-profile-identity'
const PRIVATE_ERROR_DETAIL_SENTINEL = 'stage6-phase3-private-error-detail'
const RUN_ID = `uiux-stage6-phase3-browser-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join(EVIDENCE_DIR, 'runs', RUN_ID)
const METRICS_PATH = path.join(OUTPUT_DIR, 'stage6-phase3-profile-privacy-authority-metrics.json')
const BINDING_PATH = path.join(OUTPUT_DIR, 'stage6-phase3-source-binding.json')
const VITE_CACHE_DIR = path.join(tmpdir(), `${RUN_ID}-vite-cache`)

const ORDINARY_OWNER_ID = 9101
const PEER_ID = 9202
const SUPER_SELF_ID = 9301
const SUPER_PEER_ID = 9302
const PROFILE_403_ID = 9403
const PROFILE_404_ID = 9404

const VIEWPORTS = Object.freeze([
  { label: 'mobile-360', width: 360, height: 740 },
  { label: 'mobile-390', width: 390, height: 844 },
  { label: 'mobile-430', width: 430, height: 932 },
  { label: 'desktop-1440', width: 1440, height: 900 },
])

// The whole worktree must be clean. This explicit fingerprint additionally
// binds the browser surface and the Phase 3 server projection/authority code
// to one source SHA without copying source or fixture values into evidence.
const SOURCE_DIRECT_FILES = Object.freeze([
  'frontend/index.html',
  'frontend/package.json',
  'frontend/package-lock.json',
  'frontend/vite.config.ts',
  'api/routers/trades.py',
  'api/routers/users.py',
  'api/routers/users_public.py',
  'core/services/registration_notification_service.py',
  'core/services/trade_notification_audience_service.py',
  'schemas.py',
])
const SOURCE_TREES = Object.freeze(['frontend/src'])
const VITE_ENV_FILES = Object.freeze([
  '.env',
  '.env.local',
  '.env.development',
  '.env.development.local',
])
const SANITIZED_ENV_EXACT_KEYS = Object.freeze([
  'E2E_BACKEND_BASE_URL',
  'FRONTEND_BUILD_OUT_DIR',
  'NODE_ENV',
  'VITEST',
])

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}

function sha256File(filePath) {
  return sha256(fs.readFileSync(filePath))
}

function fileSnapshot(filePath) {
  const stat = fs.statSync(filePath)
  return { bytes: stat.size, sha256: sha256File(filePath) }
}

function gitText(args) {
  return execFileSync('git', args, {
    cwd: WORKTREE,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim()
}

function walk(relativeRoot) {
  const absoluteRoot = path.join(WORKTREE, relativeRoot)
  assert.ok(fs.statSync(absoluteRoot).isDirectory(), `Missing source directory: ${relativeRoot}`)
  const pending = [absoluteRoot]
  const files = []
  while (pending.length > 0) {
    const directory = pending.pop()
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolutePath = path.join(directory, entry.name)
      if (entry.isDirectory()) pending.push(absolutePath)
      else if (entry.isFile()) files.push(path.relative(WORKTREE, absolutePath).split(path.sep).join('/'))
      else throw new Error(`Unsupported source entry: ${absolutePath}`)
    }
  }
  return files
}

function sourceSnapshot() {
  const files = [...new Set([...SOURCE_DIRECT_FILES, ...SOURCE_TREES.flatMap(walk)])].sort()
  return files.map((relativePath) => {
    const absolutePath = path.join(WORKTREE, relativePath)
    const stat = fs.statSync(absolutePath)
    return { path: relativePath, bytes: stat.size, sha256: sha256File(absolutePath) }
  })
}

function gitSnapshot() {
  const branch = gitText(['branch', '--show-current'])
  const status = gitText(['status', '--porcelain=v1', '--untracked-files=all'])
  return {
    branch: branch || null,
    detached: branch === '',
    commit: gitText(['rev-parse', 'HEAD']),
    tree: gitText(['rev-parse', 'HEAD^{tree}']),
    parent: gitText(['rev-parse', 'HEAD^']),
    trackedClean: status === '',
    trackedStatus: status,
  }
}

function viteEnvironmentSnapshot() {
  return VITE_ENV_FILES.map((file) => {
    const absolutePath = path.join(FRONTEND, file)
    return {
      path: `frontend/${file}`,
      exists: fs.existsSync(absolutePath),
      ...(fs.existsSync(absolutePath) ? fileSnapshot(absolutePath) : {}),
    }
  })
}

function redact(value) {
  return String(value)
    .split(LEGACY_PROFILE_QUERY_SENTINEL).join('[redacted-legacy-query]')
    .split(PRIVATE_ERROR_DETAIL_SENTINEL).join('[redacted-private-detail]')
}

function safeError(error) {
  return {
    name: error instanceof Error ? error.name : 'Error',
    message: redact(error instanceof Error ? error.message : String(error)),
    stack: error instanceof Error ? redact(error.stack || '') : null,
  }
}

function safeUrlDescriptor(value) {
  const url = new URL(value)
  return {
    origin: url.origin,
    pathname: url.pathname,
    method: null,
    hasQuery: Boolean(url.search),
    queryKeys: [...url.searchParams.keys()].sort(),
  }
}

const SOURCE_INITIAL = sourceSnapshot()
const SOURCE_BINDING_SHA256 = sha256(JSON.stringify(SOURCE_INITIAL))
const GIT_INITIAL = gitSnapshot()
const HARNESS_INITIAL = fileSnapshot(HARNESS_PATH)

if (process.argv.includes('--print-source-binding')) {
  process.stdout.write(`${JSON.stringify({
    schemaVersion: 1,
    stage: 6,
    phase: 3,
    scope: 'profile-privacy-authority-browser-acceptance',
    branch: GIT_INITIAL.branch,
    commit: GIT_INITIAL.commit,
    tree: GIT_INITIAL.tree,
    parent: GIT_INITIAL.parent,
    trackedClean: GIT_INITIAL.trackedClean,
    sourceFileCount: SOURCE_INITIAL.length,
    sourceBindingSha256: SOURCE_BINDING_SHA256,
    harness: HARNESS_INITIAL,
    runnable: (
      GIT_INITIAL.branch === EXPECTED_BRANCH &&
      GIT_INITIAL.trackedClean
    ),
    execution: 'requires the exact authorization, expected commit, expected source SHA-256, expected branch and a Git-clean worktree; partial or diagnostic runs are disabled',
  }, null, 2)}\n`)
  process.exit(0)
}

assert.equal(
  process.env.STAGE6_PHASE3_BROWSER_AUTHORIZATION,
  RUN_AUTHORIZATION,
  `Browser execution is locked. Set STAGE6_PHASE3_BROWSER_AUTHORIZATION to ${JSON.stringify(RUN_AUTHORIZATION)}.`,
)
assert.equal(
  process.env.STAGE6_PHASE3_EXPECTED_SOURCE_SHA256,
  SOURCE_BINDING_SHA256,
  'STAGE6_PHASE3_EXPECTED_SOURCE_SHA256 does not match the bounded Phase 3 source snapshot.',
)
assert.equal(
  process.env.STAGE6_PHASE3_EXPECTED_COMMIT,
  GIT_INITIAL.commit,
  'STAGE6_PHASE3_EXPECTED_COMMIT does not match the current implementation commit.',
)
assert.equal(GIT_INITIAL.branch, EXPECTED_BRANCH, 'Stage 6 Phase 3 evidence is on the wrong branch.')
assert.equal(GIT_INITIAL.trackedClean, true, 'Phase 3 capture requires a Git-clean implementation worktree.')
assert.equal(
  (process.env.STAGE6_PHASE3_BROWSER_ONLY || '').trim(),
  '',
  'Partial Phase 3 browser runs are disabled; capture the complete suite only.',
)
assert.notEqual(
  process.env.STAGE6_PHASE3_BROWSER_DIAGNOSTIC,
  '1',
  'Diagnostic mode is intentionally disabled; this harness only captures a clean committed source.',
)

const SANITIZED_ENV_KEYS = Object.keys(process.env)
  .filter((key) => key.startsWith('VITE_') || SANITIZED_ENV_EXACT_KEYS.includes(key))
  .sort()
for (const key of SANITIZED_ENV_KEYS) delete process.env[key]
process.env.NODE_ENV = 'development'

const ENVIRONMENT_INITIAL = viteEnvironmentSnapshot()
assert.equal(
  ENVIRONMENT_INITIAL.some((entry) => entry.exists),
  false,
  'Unbound Vite environment files are forbidden during browser evidence capture.',
)

const require = createRequire(path.join(FRONTEND, 'package.json'))
const FRONTEND_NODE_MODULES = fs.realpathSync(path.join(FRONTEND, 'node_modules'))
const { chromium } = require('playwright')
const viteEntry = require.resolve('vite')
const { createServer } = await import(pathToFileURL(viteEntry).href)

const diagnostics = {
  consoleErrors: [],
  pageErrors: [],
  unexpectedApiRequests: [],
  sameOriginRequestFailures: [],
  nonApiRequestFailures: [],
  unexpectedTransports: [],
  externalTrafficIntercepted: [],
  websocketEvents: [],
  eventSourceEvents: [],
  expectedHttpErrors: [],
  benignAbortedDevResources: [],
}
const assertions = []
const screenshots = []
const runtimeSummaries = []

function record(id, details = {}) {
  assertions.push({ id, passed: true, ...details })
}

function progress(stage, details = {}) {
  process.stdout.write(`${JSON.stringify({ event: 'stage6-phase3-browser-progress', runId: RUN_ID, stage, ...details })}\n`)
}

function base64Url(value) {
  return Buffer.from(JSON.stringify(value)).toString('base64url')
}

function createJwt(subject) {
  return `${base64Url({ alg: 'none', typ: 'JWT' })}.${base64Url({ sub: String(subject), exp: FIXED_EPOCH_SECONDS + 3600 })}.synthetic`
}

function ordinaryOwner() {
  return {
    id: ORDINARY_OWNER_ID,
    account_name: 'stage6_synthetic_owner',
    full_name: 'مالک آزمایشی مرحله شش',
    role: 'عادی',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
  }
}

function superOwner() {
  return {
    id: SUPER_SELF_ID,
    account_name: 'stage6_synthetic_super',
    full_name: 'مدیر ارشد آزمایشی مرحله شش',
    role: 'مدیر ارشد',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
  }
}

const PEER_PUBLIC_PROFILE = Object.freeze({
  id: PEER_ID,
  account_name: 'stage6_synthetic_peer',
  avatar_file_id: null,
  mobile_number: '0912****345',
})

const SELF_PUBLIC_PROFILE = Object.freeze({
  id: ORDINARY_OWNER_ID,
  account_name: 'stage6_synthetic_owner',
  avatar_file_id: null,
  mobile_number: '09120000000',
  address: 'نشانی مصنوعی فقط برای پذیرش محلی مرحله شش',
  last_seen_at: '2026-08-11T17:59:00.000Z',
  created_at_jalali: '۱۴۰۵/۰۵/۲۰',
  trades_count: 7,
  accountant_relations: [],
  customer_relations: [],
})

function adminUserFixture(id) {
  const self = id === SUPER_SELF_ID
  const peer = id === SUPER_PEER_ID
  if (!self && !peer) return null
  return {
    id,
    full_name: self ? 'مدیر ارشد خودِ آزمایشی' : 'مدیر ارشد هم‌سطح آزمایشی',
    account_name: self ? 'stage6_synthetic_super' : 'stage6_synthetic_super_peer',
    mobile_number: self ? '09120000001' : '09120000002',
    role: 'مدیر ارشد',
    account_status: 'active',
    has_bot_access: true,
    created_at: '2026-08-01T00:00:00.000Z',
    max_sessions: 1,
    max_accountants: 3,
    max_customers: 5,
    can_block_users: true,
    max_blocked_users: 10,
    trading_restricted_until: null,
    trading_restricted_until_jalali: null,
    max_daily_trades: null,
    max_active_commodities: null,
    max_daily_requests: null,
    limitations_expire_at: null,
    limitations_expire_at_jalali: null,
    trades_count: 0,
    commodities_traded_count: 0,
    channel_messages_count: 0,
    global_lock_grace_expires_at: null,
    global_web_locked_at: null,
    is_accountant: false,
    is_customer: false,
    customer_management_name: null,
    customer_owner_account_name: null,
    customer_tier: null,
  }
}

function legacyProfileRoute(id = PEER_ID) {
  const params = new URLSearchParams({
    account_name: LEGACY_PROFILE_QUERY_SENTINEL,
    highlight_accountant_user_id: '9299',
    highlight_accountant_relation_display_name: LEGACY_PROFILE_QUERY_SENTINEL,
  })
  return `/users/${id}?${params.toString()}`
}

function notificationHistoryFixture() {
  return [{
    id: 9801,
    title: 'اعلان آزمایشی مسیر پروفایل',
    content: 'این متن فقط دادهٔ ساختگی پذیرش محلی است.',
    level: 'info',
    category: 'trade',
    is_read: false,
    created_at: FIXED_TIME,
    route: legacyProfileRoute(),
  }]
}

const SCENARIOS = Object.freeze({
  ordinary: {
    key: 'ordinary-peer',
    owner: ordinaryOwner(),
    documentHidden: false,
    notificationPermission: 'denied',
    notificationHistory: false,
  },
  self: {
    key: 'self-profile',
    owner: ordinaryOwner(),
    documentHidden: false,
    notificationPermission: 'denied',
    notificationHistory: false,
  },
  super: {
    key: 'super-authority',
    owner: superOwner(),
    documentHidden: false,
    notificationPermission: 'denied',
    notificationHistory: false,
  },
  notificationCenter: {
    key: 'notification-center-route',
    owner: ordinaryOwner(),
    documentHidden: false,
    notificationPermission: 'denied',
    notificationHistory: true,
  },
  toast: {
    key: 'toast-route',
    owner: ordinaryOwner(),
    documentHidden: false,
    notificationPermission: 'denied',
    notificationHistory: false,
  },
  browserNotification: {
    key: 'browser-notification-route',
    owner: ordinaryOwner(),
    documentHidden: true,
    notificationPermission: 'granted',
    notificationHistory: false,
  },
})

function newState(scenario, viewport) {
  return {
    scenario: scenario.key,
    viewport,
    suite: `${scenario.key}-${viewport.label}`,
    owner: scenario.owner,
    token: createJwt(scenario.owner.id),
    notificationHistory: scenario.notificationHistory,
    apiRequests: [],
    publicProfileContracts: [],
    profileRequestCounts: new Map(),
    adminProfileContracts: [],
    sensitiveUserMutations: [],
    notificationHistoryRequests: 0,
    websocketPayloadsInjected: 0,
  }
}

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify(body),
  })
}

function recordApi(state, request, url) {
  const queryKeys = [...url.searchParams.keys()].sort()
  state.apiRequests.push({
    pathname: url.pathname,
    method: request.method(),
    hasQuery: queryKeys.length > 0,
    queryKeys,
  })
  if (/^\/api\/users\/\d+$/u.test(url.pathname) && ['PUT', 'POST', 'DELETE'].includes(request.method())) {
    state.sensitiveUserMutations.push({ pathname: url.pathname, method: request.method() })
  }
}

function publicProfileResponse(state, id) {
  const count = (state.profileRequestCounts.get(id) || 0) + 1
  state.profileRequestCounts.set(id, count)

  if (id === PROFILE_403_ID) {
    state.publicProfileContracts.push({ id, status: 403, fields: ['detail'] })
    return { status: 403, body: { detail: PRIVATE_ERROR_DETAIL_SENTINEL } }
  }
  if (id === PROFILE_404_ID) {
    state.publicProfileContracts.push({ id, status: 404, fields: ['detail'] })
    return { status: 404, body: { detail: PRIVATE_ERROR_DETAIL_SENTINEL } }
  }
  if (id === PEER_ID) {
    state.publicProfileContracts.push({ id, status: 200, fields: Object.keys(PEER_PUBLIC_PROFILE).sort() })
    return { status: 200, body: PEER_PUBLIC_PROFILE }
  }
  if (id === ORDINARY_OWNER_ID) {
    state.publicProfileContracts.push({ id, status: 200, fields: Object.keys(SELF_PUBLIC_PROFILE).sort() })
    return { status: 200, body: SELF_PUBLIC_PROFILE }
  }
  state.publicProfileContracts.push({ id, status: 404, fields: ['detail'] })
  return { status: 404, body: { detail: PRIVATE_ERROR_DETAIL_SENTINEL } }
}

function adminProfileResponse(state, id) {
  const body = adminUserFixture(id)
  if (!body) {
    state.adminProfileContracts.push({ id, status: 404, fields: ['detail'] })
    return { status: 404, body: { detail: PRIVATE_ERROR_DETAIL_SENTINEL } }
  }
  state.adminProfileContracts.push({ id, status: 200, fields: Object.keys(body).sort() })
  return { status: 200, body }
}

async function handleApi(route, state) {
  const request = route.request()
  const url = new URL(request.url())
  const pathname = url.pathname
  const method = request.method()
  recordApi(state, request, url)

  if (pathname === '/api/auth/me' && method === 'GET') return json(route, state.owner)
  if (pathname === '/api/auth/refresh' && method === 'POST') {
    return json(route, { access_token: state.token, refresh_token: state.token })
  }
  if (/^\/api\/users-public\/\d+$/u.test(pathname) && method === 'GET') {
    const id = Number(pathname.split('/').pop())
    const response = publicProfileResponse(state, id)
    return json(route, response.body, response.status)
  }
  if (/^\/api\/users\/\d+$/u.test(pathname) && method === 'GET') {
    const id = Number(pathname.split('/').pop())
    const response = adminProfileResponse(state, id)
    return json(route, response.body, response.status)
  }
  if (pathname === `/api/blocks/check/${PEER_ID}` && method === 'GET') {
    return json(route, { is_blocked_by_me: false })
  }
  if (pathname === '/api/blocks/status' && method === 'GET') {
    return json(route, { can_block: true, can_block_now: true, max_blocked: 5, current_blocked: 0, remaining: 5 })
  }
  if ((pathname === '/api/notifications/' || pathname === '/api/notifications') && method === 'GET') {
    state.notificationHistoryRequests += 1
    return json(route, state.notificationHistory ? notificationHistoryFixture() : [])
  }
  if (/^\/api\/notifications\/\d+\/read$/u.test(pathname) && method === 'PATCH') return json(route, {})
  if (pathname === '/api/notifications/unread-count' && method === 'GET') return json(route, 0)
  if (pathname === '/api/notifications/push/public-key' && method === 'GET') {
    return json(route, { enabled: false, public_key: null })
  }
  if (pathname === '/api/chat/poll' && method === 'GET') {
    return json(route, {
      conversations_with_unread: [],
      unread_chats_count: 0,
      total_unread_mentions: 0,
      muted_conversation_ids: [],
    })
  }
  if (pathname === '/api/sessions/verify' && method === 'POST') return json(route, {})
  if (pathname === '/api/sessions/recovery/pending' && method === 'GET') return json(route, [])
  if (pathname === '/api/sessions/login-requests/pending' && method === 'GET') return json(route, [])
  if (pathname === '/api/trading-settings/market-state' && method === 'GET') {
    return json(route, {
      is_open: false,
      active_web_notice_visible: false,
      offers_since_last_open: 0,
      last_transition_at: null,
      next_transition_at: null,
    })
  }
  if (pathname === '/api/config' && method === 'GET') return json(route, {})
  if (pathname === '/api/health' && method === 'GET') return json(route, { status: 'ok' })
  if (pathname === '/api/ping' && method === 'GET') return json(route, { status: 'ok' })

  diagnostics.unexpectedApiRequests.push({
    suite: state.suite,
    pathname,
    method,
    queryKeys: [...url.searchParams.keys()].sort(),
  })
  return json(route, { detail: 'stage6_phase3_unexpected_api_request' }, 501)
}

function installBrowserFakes(page, state, scenario) {
  return page.addInitScript(({ token, owner, documentHidden, notificationPermission, sentinel }) => {
    window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
    localStorage.setItem('auth_token', token)
    localStorage.setItem('refresh_token', token)
    localStorage.setItem('current_user_summary', JSON.stringify(owner))
    localStorage.removeItem('suspended_refresh_token')
    sessionStorage.clear()

    Object.defineProperty(document, 'hidden', { configurable: true, get: () => Boolean(documentHidden) })
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => documentHidden ? 'hidden' : 'visible',
    })

    const containsSentinel = (value) => {
      try {
        return typeof value === 'string'
          ? value.includes(sentinel)
          : JSON.stringify(value).includes(sentinel)
      } catch {
        return false
      }
    }
    const safeRoute = (value) => {
      try {
        const url = new URL(value == null ? location.href : String(value), location.href)
        return {
          pathname: url.pathname,
          hasQuery: Boolean(url.search),
          queryKeys: [...url.searchParams.keys()].sort(),
          containsLegacySentinel: containsSentinel(url.href),
        }
      } catch {
        return { pathname: null, hasQuery: false, queryKeys: [], containsLegacySentinel: false }
      }
    }
    const historyAudit = { calls: [] }
    for (const method of ['pushState', 'replaceState']) {
      const original = history[method].bind(history)
      history[method] = (data, unused, url) => {
        historyAudit.calls.push({
          method,
          stateContainsLegacySentinel: containsSentinel(data),
          route: safeRoute(url),
        })
        return original(data, unused, url)
      }
    }
    window.__stage6Phase3HistoryAudit = historyAudit

    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        controller: { postMessage() {} },
        ready: Promise.resolve({ active: { postMessage() {} }, pushManager: { getSubscription: async () => null } }),
        getRegistration: async () => null,
        getRegistrations: async () => [],
        register: async () => ({ active: { postMessage() {} } }),
      },
    })

    const notifications = []
    class FakeNotification {
      static permission = notificationPermission
      static async requestPermission() { return notificationPermission }
      constructor(title, options = {}) {
        this.title = String(title)
        this.options = options
        this.onclick = null
        notifications.push(this)
      }
      close() {}
    }
    Object.defineProperty(window, 'Notification', { configurable: true, value: FakeNotification })
    window.__stage6Phase3Notifications = notifications

    const sockets = []
    class FakeSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      constructor(url) {
        this.url = String(url)
        this.readyState = FakeSocket.CONNECTING
        this.listeners = new Map()
        sockets.push(this)
        const parsed = new URL(this.url, location.href)
        const expectedProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        // Vite's local HMR transport uses the root websocket path. It remains
        // same-origin and does not represent product network traffic; the app
        // realtime transport must use the explicit API endpoint.
        const valid = parsed.host === location.host && parsed.protocol === expectedProtocol && (
          parsed.pathname === '/' || parsed.pathname === '/api/realtime/ws'
        )
        void window.__stage6RecordTransport({
          transport: 'websocket',
          valid,
          protocol: parsed.protocol,
          pathname: parsed.pathname,
          sameHost: parsed.host === location.host,
        })
        queueMicrotask(() => {
          this.readyState = FakeSocket.OPEN
          this.dispatch('open', new Event('open'))
        })
      }
      addEventListener(type, listener) {
        const listeners = this.listeners.get(type) || new Set()
        listeners.add(listener)
        this.listeners.set(type, listeners)
      }
      removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener) }
      dispatch(type, event) {
        this[`on${type}`]?.(event)
        for (const listener of this.listeners.get(type) || []) listener.call(this, event)
      }
      emitMessage(payload) { this.dispatch('message', { data: payload }) }
      send() {}
      close() { this.readyState = FakeSocket.CLOSED; this.dispatch('close', new Event('close')) }
    }
    class FakeEventSource {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSED = 2
      constructor(url) {
        this.url = String(url)
        this.readyState = FakeEventSource.CONNECTING
        this.listeners = new Map()
        const parsed = new URL(this.url, location.href)
        const valid = parsed.origin === location.origin && parsed.pathname === '/api/realtime/events'
        void window.__stage6RecordTransport({
          transport: 'eventsource',
          valid,
          protocol: parsed.protocol,
          pathname: parsed.pathname,
          sameOrigin: parsed.origin === location.origin,
        })
        queueMicrotask(() => {
          this.readyState = FakeEventSource.OPEN
          this.dispatch('open', new Event('open'))
        })
      }
      addEventListener(type, listener) {
        const listeners = this.listeners.get(type) || new Set()
        listeners.add(listener)
        this.listeners.set(type, listeners)
      }
      removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener) }
      dispatch(type, event) {
        this[`on${type}`]?.(event)
        for (const listener of this.listeners.get(type) || []) listener.call(this, event)
      }
      close() { this.readyState = FakeEventSource.CLOSED }
    }
    window.__stage6Phase3Sockets = sockets
    window.WebSocket = FakeSocket
    window.EventSource = FakeEventSource
    window.confirm = () => false
    window.alert = () => undefined
    window.open = () => null
  }, {
    token: state.token,
    owner: state.owner,
    documentHidden: scenario.documentHidden,
    notificationPermission: scenario.notificationPermission,
    sentinel: LEGACY_PROFILE_QUERY_SENTINEL,
  })
}

function expectedHttpError(message) {
  const text = message.text()
  const location = message.location()
  let pathname = ''
  try {
    pathname = new URL(location.url).pathname
  } catch {
    return null
  }
  const expected = [
    [PROFILE_403_ID, 403, 'Forbidden'],
    [PROFILE_404_ID, 404, 'Not Found'],
  ]
  for (const [id, status, label] of expected) {
    if (
      pathname === `/api/users-public/${id}` &&
      text === `Failed to load resource: the server responded with a status of ${status} (${label})`
    ) return { id, status }
  }
  return null
}

async function createRuntime(browser, baseUrl, scenario, viewport) {
  const state = newState(scenario, viewport)
  const context = await browser.newContext({
    baseURL: baseUrl,
    locale: 'fa-IR',
    timezoneId: 'Asia/Tehran',
    serviceWorkers: 'block',
    reducedMotion: 'reduce',
    viewport: { width: viewport.width, height: viewport.height },
  })
  const page = await context.newPage()
  await page.clock.setFixedTime(new Date(FIXED_TIME))
  await context.exposeBinding('__stage6RecordTransport', (_source, event) => {
    const row = { suite: state.suite, ...event }
    if (event.transport === 'websocket') diagnostics.websocketEvents.push(row)
    else diagnostics.eventSourceEvents.push(row)
    if (!event.valid) diagnostics.unexpectedTransports.push(row)
  })
  await installBrowserFakes(page, state, scenario)

  const origin = new URL(baseUrl).origin
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) {
      diagnostics.externalTrafficIntercepted.push({
        suite: state.suite,
        origin: url.origin,
        pathname: url.pathname,
        method: route.request().method(),
      })
      // index.html contains this third-party loader. Return an inert local
      // response before a network connection is made, rather than allowing it.
      if (url.origin === 'https://telegram.org' && url.pathname.endsWith('.js')) {
        return route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: '' })
      }
      return route.abort('blockedbyclient')
    }
    if (url.pathname.startsWith('/api/')) return handleApi(route, state)
    return route.continue()
  })
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const expected = expectedHttpError(message)
    if (expected) {
      diagnostics.expectedHttpErrors.push({ suite: state.suite, ...expected })
      return
    }
    diagnostics.consoleErrors.push({
      suite: state.suite,
      text: redact(message.text()),
      location: {
        url: redact(message.location().url),
        lineNumber: message.location().lineNumber,
      },
    })
  })
  page.on('pageerror', (error) => diagnostics.pageErrors.push({ suite: state.suite, text: redact(error.message) }))
  page.on('requestfailed', (request) => {
    const url = new URL(request.url())
    if (url.origin !== origin) return
    const failure = redact(request.failure()?.errorText || '')
    const row = { suite: state.suite, ...safeUrlDescriptor(request.url()), method: request.method(), failure }
    if (url.pathname.startsWith('/api/')) diagnostics.sameOriginRequestFailures.push(row)
    else if (failure === 'net::ERR_ABORTED') diagnostics.benignAbortedDevResources.push(row)
    else diagnostics.nonApiRequestFailures.push(row)
  })
  return { context, page, state }
}

async function settle(page) {
  await page.waitForLoadState('domcontentloaded')
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
}

async function openPublicProfile(page, id, { legacy = false } = {}) {
  await page.goto(legacy ? legacyProfileRoute(id) : `/users/${id}`, { waitUntil: 'domcontentloaded' })
  if (legacy) {
    // The product removes legacy query data through an async router.replace().
    // Do not bind a strict locator to its outgoing/entering transition; first
    // require the canonical URL, then require that the transition has left one
    // stable profile root. A persistent duplicate remains a hard failure.
    await page.waitForURL(new RegExp(`/users/${id}$`, 'u'), { timeout: 30_000 })
  }
  await page.waitForFunction(
    () => document.querySelectorAll('.public-profile-view').length === 1,
    undefined,
    { timeout: 30_000 },
  )
  const profileRoot = page.locator('.public-profile-view')
  await profileRoot.waitFor({ state: 'visible', timeout: 30_000 })
  await profileRoot.locator('.profile-content, .error-state').first().waitFor({ state: 'visible', timeout: 30_000 })
  await settle(page)
}

async function openAdminProfile(page, id) {
  await page.goto(`/admin/users/${id}`, { waitUntil: 'domcontentloaded' })
  await page.locator('.admin-view .profile-details').waitFor({ state: 'visible', timeout: 30_000 })
  await settle(page)
}

async function capture(page, label) {
  const file = `stage6-phase3-profile-privacy-authority-${label}.png`
  const filePath = path.join(OUTPUT_DIR, file)
  await page.screenshot({ path: filePath, fullPage: false, animations: 'disabled' })
  const stat = fs.statSync(filePath)
  const entry = { file, bytes: stat.size, sha256: sha256File(filePath) }
  screenshots.push(entry)
  return entry
}

async function routeState(page) {
  return page.evaluate(() => {
    const url = new URL(location.href)
    return {
      pathname: url.pathname,
      queryKeys: [...url.searchParams.keys()].sort(),
      hasQuery: Boolean(url.search),
      historyState: history.state,
    }
  })
}

async function assertIdOnlyPublicProfileRoute(page, id) {
  const current = await routeState(page)
  assert.equal(current.pathname, `/users/${id}`, 'Public profile has an unexpected path.')
  assert.equal(current.hasQuery, false, 'Public profile must not retain query parameters.')
  assert.deepEqual(current.queryKeys, [], 'Public profile query keys must be empty.')
  return current
}

async function assertNoLegacyRoutePersistence(page) {
  const result = await page.evaluate((sentinel) => {
    const contains = (value) => {
      try {
        return typeof value === 'string'
          ? value.includes(sentinel)
          : JSON.stringify(value).includes(sentinel)
      } catch {
        return false
      }
    }
    const storageContains = (storage) => Array.from({ length: storage.length }, (_value, index) => {
      const key = storage.key(index)
      return key ? `${key}:${storage.getItem(key) || ''}` : ''
    }).some(contains)
    const historyAudit = window.__stage6Phase3HistoryAudit || { calls: [] }
    const current = new URL(location.href)
    const profileHistoryCalls = historyAudit.calls
      .filter((entry) => typeof entry?.route?.pathname === 'string' && entry.route.pathname.startsWith('/users/'))
      .map((entry) => ({
        pathname: entry.route.pathname,
        hasQuery: Boolean(entry.route.hasQuery),
        queryKeys: Array.isArray(entry.route.queryKeys) ? entry.route.queryKeys : [],
        containsLegacySentinel: Boolean(entry.route.containsLegacySentinel || entry.stateContainsLegacySentinel),
      }))
    return {
      locationContainsLegacySentinel: contains(location.href),
      historyStateContainsLegacySentinel: contains(history.state),
      localStorageContainsLegacySentinel: storageContains(localStorage),
      sessionStorageContainsLegacySentinel: storageContains(sessionStorage),
      currentProfileHasQuery: current.pathname.startsWith('/users/') && Boolean(current.search),
      terminalProfileHistoryCall: profileHistoryCalls.at(-1) || null,
    }
  }, LEGACY_PROFILE_QUERY_SENTINEL)
  assert.equal(result.locationContainsLegacySentinel, false, 'Legacy identity query remained in location.')
  assert.equal(result.historyStateContainsLegacySentinel, false, 'Legacy identity query remained in current history state.')
  assert.equal(result.localStorageContainsLegacySentinel, false, 'Legacy identity query reached localStorage.')
  assert.equal(result.sessionStorageContainsLegacySentinel, false, 'Legacy identity query reached sessionStorage.')
  assert.equal(result.currentProfileHasQuery, false, 'Current public profile still has a query.')
  if (result.terminalProfileHistoryCall) {
    assert.equal(result.terminalProfileHistoryCall.hasQuery, false, 'Terminal profile history entry retained query parameters.')
    assert.deepEqual(result.terminalProfileHistoryCall.queryKeys, [], 'Terminal profile history entry retained query keys.')
    assert.equal(result.terminalProfileHistoryCall.containsLegacySentinel, false, 'Terminal profile history entry retained legacy identity data.')
  }
  return result
}

async function assertOrdinaryPeerProjection(page, state) {
  await page.locator('.public-profile-view .profile-content').waitFor({ state: 'visible' })
  const contract = await page.evaluate(({ fullMobile, address }) => {
    const text = document.querySelector('.public-profile-view')?.textContent || ''
    const selectors = [
      '.profile-presence-status',
      '.profile-stats-grid',
      '.address-row',
      '.address-edit-trigger',
      '.project-users-section',
      '.accountant-relations-section',
      '.customer-relations-section',
      '.history-section-card',
    ]
    return {
      textContainsFullMobile: text.includes(fullMobile),
      textContainsAddress: text.includes(address),
      textContainsMaskedMobile: text.includes('0912****345'),
      absentProtectedSections: selectors.every((selector) => document.querySelector(selector) === null),
      privacyNoteVisible: document.querySelector('.profile-privacy-note') instanceof HTMLElement,
      readonlyAvatarVisible: document.querySelector('[data-test="profile-avatar-readonly"]') instanceof HTMLElement,
    }
  }, { fullMobile: SELF_PUBLIC_PROFILE.mobile_number, address: SELF_PUBLIC_PROFILE.address })
  assert.deepEqual(contract, {
    textContainsFullMobile: false,
    textContainsAddress: false,
    textContainsMaskedMobile: true,
    absentProtectedSections: true,
    privacyNoteVisible: true,
    readonlyAvatarVisible: true,
  }, 'Ordinary peer public profile exposed a protected surface or omitted the masked projection.')
  const successful = state.publicProfileContracts.filter((entry) => entry.id === PEER_ID && entry.status === 200)
  assert.ok(successful.length >= 1, 'Ordinary peer public profile was not requested.')
  for (const response of successful) {
    assert.deepEqual(response.fields, ['account_name', 'avatar_file_id', 'id', 'mobile_number'], 'Peer response was not minimal.')
  }
}

async function assertLayoutFocusAndMotion(page, selector, label) {
  const target = page.locator(selector).first()
  await target.waitFor({ state: 'visible', timeout: 15_000 })

  // locator.focus() is programmatic and does not prove the browser's keyboard
  // focus-visible modality. Start at the exact control, then traverse away and
  // back using real Tab / Shift+Tab input so the final focus state is keyboard
  // derived without relying on an incidental page tab order.
  const baselineFocusStyle = await target.evaluate((element) => {
    const style = getComputedStyle(element)
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
      outlineColor: style.outlineColor,
      boxShadow: style.boxShadow,
      borderColor: style.borderColor,
    }
  })
  await target.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab')
  const focusCueSettled = await page.waitForFunction(
    ({ targetSelector, baseline }) => {
      const targetElement = document.querySelector(targetSelector)
      if (!(targetElement instanceof HTMLElement)) return false
      if (document.activeElement !== targetElement || !targetElement.matches(':focus-visible')) return false
      const style = getComputedStyle(targetElement)
      return (
        (style.outlineStyle !== 'none' && style.outlineWidth !== '0px')
        || (style.boxShadow !== 'none' && style.boxShadow !== baseline.boxShadow)
        || style.borderColor !== baseline.borderColor
      )
    },
    { targetSelector: selector, baseline: baselineFocusStyle },
    { timeout: 1_000 },
  ).then(() => true).catch(() => false)
  const contract = await target.evaluate((element, baseline) => {
    const targetElement = element instanceof HTMLElement ? element : null
    const rect = targetElement?.getBoundingClientRect()
    const center = rect ? document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) : null
    const route = document.querySelector('.app-route-scroll')
    const scope = document.querySelector('[data-ui-system="v2"]')
    const style = targetElement ? getComputedStyle(targetElement) : null
    const scopeStyle = scope ? getComputedStyle(scope) : null
    const overflowOffenders = [...document.querySelectorAll('*')]
      .filter((candidate) => candidate instanceof HTMLElement)
      .map((candidate) => {
        const candidateStyle = getComputedStyle(candidate)
        const candidateRect = candidate.getBoundingClientRect()
        return {
          tag: candidate.tagName.toLowerCase(),
          id: candidate.id || null,
          classes: [...candidate.classList].slice(0, 5),
          scrollWidth: candidate.scrollWidth,
          clientWidth: candidate.clientWidth,
          left: Math.round(candidateRect.left),
          right: Math.round(candidateRect.right),
          overflowX: candidateStyle.overflowX,
        }
      })
      .filter((candidate) => (
        candidate.scrollWidth > candidate.clientWidth + 1 ||
        candidate.left < -1 ||
        candidate.right > window.innerWidth + 1
      ))
      .slice(0, 24)
    const headerChildren = [...(document.querySelector('.profile-header-row')?.children || [])]
      .filter((candidate) => candidate instanceof HTMLElement)
      .map((candidate) => {
        const candidateRect = candidate.getBoundingClientRect()
        return {
          tag: candidate.tagName.toLowerCase(),
          classes: [...candidate.classList].slice(0, 5),
          left: Math.round(candidateRect.left),
          right: Math.round(candidateRect.right),
          width: Math.round(candidateRect.width),
        }
      })
    return {
      targetIsFocused: document.activeElement === targetElement,
      focusVisible: Boolean(targetElement?.matches(':focus-visible')),
      focusOutlineVisible: Boolean(style && style.outlineStyle !== 'none' && style.outlineWidth !== '0px'),
      focusBoxShadowVisible: Boolean(style && style.boxShadow !== 'none' && style.boxShadow !== baseline.boxShadow),
      focusBorderVisible: Boolean(style && style.borderColor !== baseline.borderColor),
      focusStyle: style ? {
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        outlineColor: style.outlineColor,
        boxShadow: style.boxShadow,
        borderColor: style.borderColor,
      } : null,
      baselineFocusStyle: baseline,
      effectiveMotion: style ? {
        transitionDuration: style.transitionDuration,
        transitionDelay: style.transitionDelay,
        animationDuration: style.animationDuration,
        animationDelay: style.animationDelay,
      } : null,
      targetCenterClickable: Boolean(targetElement && center && (center === targetElement || center.closest('button, [role="button"]') === targetElement)),
      targetVisible: Boolean(rect && rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= window.innerHeight),
      overflow: {
        document: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        body: document.body.scrollWidth > document.body.clientWidth,
        route: route instanceof HTMLElement && route.scrollWidth > route.clientWidth,
      },
      overflowOffenders,
      headerChildren,
      reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      v2MotionMicro: scopeStyle?.getPropertyValue('--ui-v2-motion-micro').trim() || null,
      v2MotionState: scopeStyle?.getPropertyValue('--ui-v2-motion-state').trim() || null,
    }
  }, baselineFocusStyle)
  assert.equal(contract.targetIsFocused, true, `${label}: keyboard-focus target was not focused.`)
  assert.equal(contract.focusVisible, true, `${label}: real keyboard traversal did not produce :focus-visible.`)
  assert.equal(focusCueSettled, true, `${label}: keyboard focus did not produce a visible cue within 1s.`)
  assert.equal(
    contract.focusOutlineVisible || contract.focusBoxShadowVisible || contract.focusBorderVisible,
    true,
    `${label}: focused control has no visible focus treatment: ${JSON.stringify({ baseline: contract.baselineFocusStyle, focused: contract.focusStyle })}`,
  )
  assert.equal(contract.targetCenterClickable, true, `${label}: focused control center is obscured.`)
  assert.equal(contract.targetVisible, true, `${label}: focused control is not fully visible.`)
  assert.deepEqual(
    contract.overflow,
    { document: false, body: false, route: false },
    `${label}: horizontal overflow detected: ${JSON.stringify({ offenders: contract.overflowOffenders, headerChildren: contract.headerChildren })}`,
  )
  assert.equal(contract.reducedMotion, true, `${label}: reduced-motion media emulation was not active.`)
  assert.equal(contract.v2MotionMicro, '1ms', `${label}: micro motion was not reduced to 1ms.`)
  assert.equal(contract.v2MotionState, '1ms', `${label}: state motion was not reduced to 1ms.`)
  const effectiveMotionMs = maxEffectiveMotionMilliseconds(contract.effectiveMotion)
  assert.equal(
    effectiveMotionMs <= 1,
    true,
    `${label}: focused control does not honor reduced motion (effective transition/animation ${effectiveMotionMs}ms, ${JSON.stringify(contract.effectiveMotion)}).`,
  )
  return contract
}

function maxEffectiveMotionMilliseconds(motion) {
  if (!motion) return Number.POSITIVE_INFINITY
  const parseTime = (value) => String(value)
    .split(',')
    .map((token) => token.trim())
    .map((token) => {
      const match = /^(?<value>-?(?:\d+|\d*\.\d+))(?<unit>ms|s)$/u.exec(token)
      if (!match?.groups) return Number.POSITIVE_INFINITY
      const parsed = Number(match.groups.value)
      if (!Number.isFinite(parsed)) return Number.POSITIVE_INFINITY
      return match.groups.unit === 's' ? parsed * 1_000 : parsed
    })
  return Math.max(
    0,
    ...Object.values(motion).flatMap(parseTime),
  )
}

async function assertSelfProjection(page, state) {
  await page.locator('.public-profile-view .profile-content').waitFor({ state: 'visible' })
  await page.getByText(SELF_PUBLIC_PROFILE.mobile_number, { exact: true }).waitFor({ state: 'visible' })
  await page.getByText(SELF_PUBLIC_PROFILE.address, { exact: true }).waitFor({ state: 'visible' })
  await page.locator('.address-edit-trigger').waitFor({ state: 'visible' })
  await page.locator('.profile-stats-grid').waitFor({ state: 'visible' })
  await page.locator('.profile-presence-status').waitFor({ state: 'visible' })
  await page.locator('.address-edit-trigger').click()
  await page.locator('textarea.ui-textarea').waitFor({ state: 'visible' })
  await page.getByRole('button', { name: 'ذخیره آدرس', exact: true }).waitFor({ state: 'visible' })
  const successful = state.publicProfileContracts.filter((entry) => entry.id === ORDINARY_OWNER_ID && entry.status === 200)
  assert.ok(successful.length >= 1, 'Self profile was not requested.')
  assert.ok(successful.every((entry) => entry.fields.includes('address') && entry.fields.includes('trades_count')), 'Self response lost allowed self-only fields.')
}

async function assertAdminReadOnly(page, state, kind) {
  await page.locator('.admin-sensitive-readonly').waitFor({ state: 'visible', timeout: 15_000 })
  const contract = await page.evaluate(() => ({
    readonlyText: document.querySelector('.admin-sensitive-readonly')?.textContent?.trim() || '',
    sensitiveControls: [
      '.sessions-config-box',
      '.terminate-sessions-btn',
      '.delete-btn',
      '.profile-control.settings-btn',
      '.edit-section',
      '.modal-overlay',
    ].map((selector) => ({ selector, count: document.querySelectorAll(selector).length })),
    hasBackControl: document.querySelector('.profile-control.back-btn') instanceof HTMLButtonElement,
  }))
  assert.match(contract.readonlyText, /فقط برای مشاهده|فقط‌خواندنی/u, `${kind}: read-only authority message missing.`)
  assert.deepEqual(
    contract.sensitiveControls.map((entry) => entry.count),
    [0, 0, 0, 0, 0, 0],
    `${kind}: sensitive admin controls rendered for a protected target.`,
  )
  assert.equal(contract.hasBackControl, true, `${kind}: safe return control is missing.`)
  assert.deepEqual(state.sensitiveUserMutations, [], `${kind}: UI emitted a sensitive user mutation.`)
}

async function assertSafePublicProfileError(page, state, id, status) {
  const error = page.locator('.public-profile-view .error-state')
  await error.waitFor({ state: 'visible', timeout: 30_000 })
  const errorText = await error.innerText()
  assert.match(errorText, /دریافت پروفایل انجام نشد/u, `${status}: generic public-profile recovery title missing.`)
  assert.equal(errorText.includes(PRIVATE_ERROR_DETAIL_SENTINEL), false, `${status}: server detail leaked into public error UI.`)
  assert.equal(await page.locator('.public-profile-view .profile-content').count(), 0, `${status}: stale profile data remained visible.`)
  await assertIdOnlyPublicProfileRoute(page, id)
  await page.getByRole('button', { name: 'تلاش دوباره', exact: true }).click()
  await error.waitFor({ state: 'visible', timeout: 15_000 })
  assert.ok((state.profileRequestCounts.get(id) || 0) >= 2, `${status}: retry did not issue a new bounded request.`)
}

function injectAppNotification(page, id) {
  return page.evaluate(({ eventId, notificationId, legacyRoute }) => {
    const socket = [...(window.__stage6Phase3Sockets || [])]
      .reverse()
      .find((candidate) => {
        if (!candidate || typeof candidate.emitMessage !== 'function') return false
        try {
          const url = new URL(candidate.url, location.href)
          const expectedProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
          return (
            url.host === location.host
            && url.protocol === expectedProtocol
            && url.pathname === '/api/realtime/ws'
            && candidate.readyState === window.WebSocket.OPEN
          )
        } catch {
          return false
        }
      })
    if (!socket) throw new Error('Synthetic product realtime socket is unavailable.')
    socket.emitMessage(JSON.stringify({
      type: 'message',
      event_id: eventId,
      data: {
        id: notificationId,
        title: 'اعلان آزمایشی محلی',
        body: 'بدنهٔ ساختگی بدون اطلاعات شخصی',
        level: 'info',
        category: 'trade',
        route: legacyRoute,
        created_at: '2026-08-11T18:00:00.000Z',
      },
    }))
  }, {
    eventId: `stage6-phase3-app-${id}`,
    notificationId: id,
    legacyRoute: legacyProfileRoute(),
  })
}

async function waitForProductRealtimeSocket(page) {
  await page.waitForFunction(() => (
    (window.__stage6Phase3Sockets || []).some((candidate) => {
      try {
        const url = new URL(candidate.url, location.href)
        const expectedProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        return (
          url.host === location.host
          && url.protocol === expectedProtocol
          && url.pathname === '/api/realtime/ws'
          && candidate.readyState === window.WebSocket.OPEN
        )
      } catch {
        return false
      }
    })
  ), undefined, { timeout: 15_000 })
}

async function runPeerLayoutMatrix(browser, baseUrl) {
  for (const viewport of VIEWPORTS) {
    const runtime = await createRuntime(browser, baseUrl, SCENARIOS.ordinary, viewport)
    try {
      await openPublicProfile(runtime.page, PEER_ID)
      await assertIdOnlyPublicProfileRoute(runtime.page, PEER_ID)
      await assertOrdinaryPeerProjection(runtime.page, runtime.state)
      await assertLayoutFocusAndMotion(runtime.page, '.visitor-profile-section .message-menu-btn', `ordinary peer / ${viewport.label}`)
      await capture(runtime.page, `ordinary-peer-${viewport.label}`)
      record(`stage6-phase3-ordinary-peer-${viewport.label}-masked-projection-reflow-focus-reduced-motion`, { viewport })
    } finally {
      runtimeSummaries.push(summarizeRuntime(runtime.state))
      await runtime.context.close()
    }
  }
}

async function runSelfProfile(browser, baseUrl) {
  const viewport = VIEWPORTS.find((candidate) => candidate.label === 'mobile-390')
  assert.ok(viewport, 'Missing mobile-390 viewport.')
  const runtime = await createRuntime(browser, baseUrl, SCENARIOS.self, viewport)
  try {
    await openPublicProfile(runtime.page, ORDINARY_OWNER_ID)
    await assertIdOnlyPublicProfileRoute(runtime.page, ORDINARY_OWNER_ID)
    await assertSelfProjection(runtime.page, runtime.state)
    await assertLayoutFocusAndMotion(runtime.page, '.address-edit-actions .ui-button', 'self profile / mobile-390')
    await capture(runtime.page, 'self-mobile-390-address-edit')
    record('stage6-phase3-self-full-contact-address-edit-affordance-mobile-390')
  } finally {
    runtimeSummaries.push(summarizeRuntime(runtime.state))
    await runtime.context.close()
  }
}

async function runAdminAuthority(browser, baseUrl) {
  const cases = [
    { id: SUPER_SELF_ID, label: 'super-self-mobile-390', viewport: VIEWPORTS.find((candidate) => candidate.label === 'mobile-390') },
    { id: SUPER_PEER_ID, label: 'super-peer-desktop-1440', viewport: VIEWPORTS.find((candidate) => candidate.label === 'desktop-1440') },
  ]
  for (const entry of cases) {
    assert.ok(entry.viewport, `Missing viewport for ${entry.label}.`)
    const runtime = await createRuntime(browser, baseUrl, SCENARIOS.super, entry.viewport)
    try {
      await openAdminProfile(runtime.page, entry.id)
      await assertAdminReadOnly(runtime.page, runtime.state, entry.label)
      await assertLayoutFocusAndMotion(runtime.page, '.profile-control.back-btn', entry.label)
      await capture(runtime.page, entry.label)
      record(`stage6-phase3-${entry.label}-sensitive-admin-actions-read-only`, { viewport: entry.viewport, targetId: entry.id })
    } finally {
      runtimeSummaries.push(summarizeRuntime(runtime.state))
      await runtime.context.close()
    }
  }
}

async function runPublicProfileErrorRecovery(browser, baseUrl) {
  const viewport = VIEWPORTS.find((candidate) => candidate.label === 'mobile-390')
  assert.ok(viewport, 'Missing mobile-390 viewport.')
  for (const [id, status] of [[PROFILE_403_ID, 403], [PROFILE_404_ID, 404]]) {
    const runtime = await createRuntime(browser, baseUrl, SCENARIOS.ordinary, viewport)
    try {
      await openPublicProfile(runtime.page, id)
      await assertSafePublicProfileError(runtime.page, runtime.state, id, status)
      await capture(runtime.page, `public-profile-${status}-recovery-mobile-390`)
      record(`stage6-phase3-public-profile-${status}-generic-bounded-retry-no-detail-leak`, { targetId: id })
    } finally {
      runtimeSummaries.push(summarizeRuntime(runtime.state))
      await runtime.context.close()
    }
  }
}

async function runLegacyCanonicalization(browser, baseUrl) {
  const viewport = VIEWPORTS.find((candidate) => candidate.label === 'mobile-390')
  assert.ok(viewport, 'Missing mobile-390 viewport.')
  const runtime = await createRuntime(browser, baseUrl, SCENARIOS.ordinary, viewport)
  try {
    await openPublicProfile(runtime.page, PEER_ID, { legacy: true })
    await assertIdOnlyPublicProfileRoute(runtime.page, PEER_ID)
    await assertNoLegacyRoutePersistence(runtime.page)
    await assertOrdinaryPeerProjection(runtime.page, runtime.state)
    await capture(runtime.page, 'legacy-profile-query-canonicalized-mobile-390')
    record('stage6-phase3-incoming-public-profile-legacy-query-canonicalized-to-id-only')
  } finally {
    runtimeSummaries.push(summarizeRuntime(runtime.state))
    await runtime.context.close()
  }
}

async function runNotificationCenterEntry(browser, baseUrl) {
  const viewport = VIEWPORTS.find((candidate) => candidate.label === 'mobile-390')
  assert.ok(viewport, 'Missing mobile-390 viewport.')
  const runtime = await createRuntime(browser, baseUrl, SCENARIOS.notificationCenter, viewport)
  try {
    const { page, state } = runtime
    await page.goto('/account/notifications', { waitUntil: 'domcontentloaded' })
    await page.locator('.notifications-list .notif-item').waitFor({ state: 'visible', timeout: 30_000 })
    await page.getByRole('button', { name: /باز کردن اعلان/u }).first().click()
    await page.waitForURL(new RegExp(`/users/${PEER_ID}$`, 'u'), { timeout: 15_000 })
    await page.locator('.public-profile-view .profile-content').waitFor({ state: 'visible' })
    await assertIdOnlyPublicProfileRoute(page, PEER_ID)
    await assertNoLegacyRoutePersistence(page)
    await assertOrdinaryPeerProjection(page, state)
    assert.ok(state.notificationHistoryRequests >= 1, 'Notification history route fixture was not read.')
    await capture(page, 'notification-center-id-only-profile-mobile-390')
    record('stage6-phase3-notification-center-profile-entry-canonicalizes-legacy-query')
  } finally {
    runtimeSummaries.push(summarizeRuntime(runtime.state))
    await runtime.context.close()
  }
}

async function runToastEntry(browser, baseUrl) {
  const viewport = VIEWPORTS.find((candidate) => candidate.label === 'mobile-390')
  assert.ok(viewport, 'Missing mobile-390 viewport.')
  const runtime = await createRuntime(browser, baseUrl, SCENARIOS.toast, viewport)
  try {
    const { page, state } = runtime
    await openPublicProfile(page, PEER_ID)
    await waitForProductRealtimeSocket(page)
    await injectAppNotification(page, 9811)
    state.websocketPayloadsInjected += 1
    await page.locator('.toast-card-floating__action').waitFor({ state: 'visible', timeout: 15_000 })
    await page.locator('.toast-card-floating__action').click()
    await page.waitForURL(/\/account\/notifications$/u, { timeout: 15_000 })
    const current = await routeState(page)
    assert.equal(current.hasQuery, false, 'Toast navigation retained a route query.')
    await assertNoLegacyRoutePersistence(page)
    record('stage6-phase3-app-toast-enters-canonical-notification-center-without-query')
  } finally {
    runtimeSummaries.push(summarizeRuntime(runtime.state))
    await runtime.context.close()
  }
}

async function runBrowserNotificationEntry(browser, baseUrl) {
  const viewport = VIEWPORTS.find((candidate) => candidate.label === 'mobile-390')
  assert.ok(viewport, 'Missing mobile-390 viewport.')
  const runtime = await createRuntime(browser, baseUrl, SCENARIOS.browserNotification, viewport)
  try {
    const { page, state } = runtime
    await openPublicProfile(page, PEER_ID)
    await waitForProductRealtimeSocket(page)
    await injectAppNotification(page, 9812)
    state.websocketPayloadsInjected += 1
    await page.waitForFunction(() => window.__stage6Phase3Notifications?.length > 0, undefined, { timeout: 15_000 })
    const notificationContract = await page.evaluate(() => ({
      count: window.__stage6Phase3Notifications?.length || 0,
      lastHasClick: typeof window.__stage6Phase3Notifications?.at(-1)?.onclick === 'function',
    }))
    assert.equal(notificationContract.count >= 1, true, 'Synthetic browser notification was not created.')
    assert.equal(notificationContract.lastHasClick, true, 'Synthetic browser notification has no click handler.')
    await page.evaluate(() => window.__stage6Phase3Notifications.at(-1).onclick())
    await page.waitForURL(/\/account\/notifications$/u, { timeout: 15_000 })
    let current = await routeState(page)
    assert.equal(current.hasQuery, false, 'Browser-notification center entry retained a route query.')
    await assertNoLegacyRoutePersistence(page)

    // A previously persisted browser notification can carry an old profile
    // link. The real AppAuthenticatedShell listener must canonicalize it too.
    await page.evaluate((legacyRoute) => {
      window.dispatchEvent(new CustomEvent('app-browser-notification-click', { detail: { route: legacyRoute } }))
    }, legacyProfileRoute())
    await page.waitForURL(new RegExp(`/users/${PEER_ID}$`, 'u'), { timeout: 15_000 })
    await page.locator('.public-profile-view .profile-content').waitFor({ state: 'visible' })
    current = await routeState(page)
    assert.equal(current.hasQuery, false, 'Legacy browser-notification profile entry retained a query.')
    await assertNoLegacyRoutePersistence(page)
    await capture(page, 'browser-notification-id-only-profile-mobile-390')
    record('stage6-phase3-browser-notification-center-and-legacy-profile-entry-never-persist-query')
  } finally {
    runtimeSummaries.push(summarizeRuntime(runtime.state))
    await runtime.context.close()
  }
}

function summarizeRuntime(state) {
  return {
    suite: state.suite,
    scenario: state.scenario,
    viewport: state.viewport,
    apiRequests: state.apiRequests,
    publicProfileContracts: state.publicProfileContracts,
    profileRequestCounts: [...state.profileRequestCounts.entries()].map(([id, count]) => ({ id, count })),
    adminProfileContracts: state.adminProfileContracts,
    sensitiveUserMutations: state.sensitiveUserMutations,
    notificationHistoryRequests: state.notificationHistoryRequests,
    websocketPayloadsInjected: state.websocketPayloadsInjected,
  }
}

async function startVite() {
  const port = await new Promise((resolve, reject) => {
    const probe = createNetServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address()
      probe.close((error) => {
        if (error) return reject(error)
        if (!address || typeof address !== 'object') return reject(new Error('No ephemeral Vite port was allocated.'))
        resolve(address.port)
      })
    })
  })
  const vite = await createServer({
    root: FRONTEND,
    cacheDir: VITE_CACHE_DIR,
    clearScreen: false,
    logLevel: 'error',
    server: {
      host: '127.0.0.1',
      port,
      strictPort: true,
      fs: { allow: [FRONTEND, FRONTEND_NODE_MODULES] },
    },
  })
  await vite.listen()
  const address = vite.httpServer?.address()
  assert.ok(address && typeof address === 'object', 'Vite did not listen.')
  return { vite, baseUrl: `http://127.0.0.1:${address.port}` }
}

function exactDiagnostics() {
  return {
    consoleErrors: diagnostics.consoleErrors,
    pageErrors: diagnostics.pageErrors,
    unexpectedApiRequests: diagnostics.unexpectedApiRequests,
    sameOriginRequestFailures: diagnostics.sameOriginRequestFailures,
    nonApiRequestFailures: diagnostics.nonApiRequestFailures,
    unexpectedTransports: diagnostics.unexpectedTransports,
  }
}

function assertExternalTrafficWasBlocked() {
  for (const request of diagnostics.externalTrafficIntercepted) {
    assert.equal(request.origin, 'https://telegram.org', 'Unexpected external browser request was attempted.')
    assert.equal(request.pathname.endsWith('.js'), true, 'Unexpected external browser resource was attempted.')
  }
}

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true })
  const startedAt = new Date().toISOString()
  let vite = null
  let browser = null
  let failure = null
  try {
    const started = await startVite()
    vite = started.vite
    progress('vite-ready', { captureMode: 'clean-commit-only' })
    browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] })
    await runPeerLayoutMatrix(browser, started.baseUrl)
    await runSelfProfile(browser, started.baseUrl)
    await runAdminAuthority(browser, started.baseUrl)
    await runPublicProfileErrorRecovery(browser, started.baseUrl)
    await runLegacyCanonicalization(browser, started.baseUrl)
    await runNotificationCenterEntry(browser, started.baseUrl)
    await runToastEntry(browser, started.baseUrl)
    await runBrowserNotificationEntry(browser, started.baseUrl)
    assert.deepEqual(exactDiagnostics(), {
      consoleErrors: [],
      pageErrors: [],
      unexpectedApiRequests: [],
      sameOriginRequestFailures: [],
      nonApiRequestFailures: [],
      unexpectedTransports: [],
    }, 'Unexpected browser diagnostics occurred.')
    assertExternalTrafficWasBlocked()
    record('stage6-phase3-browser-diagnostics-clean-and-external-traffic-intercepted', {
      externalTrafficIntercepted: diagnostics.externalTrafficIntercepted.length,
      expectedHttpErrors: diagnostics.expectedHttpErrors.length,
    })
  } catch (error) {
    failure = safeError(error)
    process.exitCode = 1
  } finally {
    if (browser) await browser.close().catch((error) => { failure ||= safeError(error) })
    if (vite) await vite.close().catch((error) => { failure ||= safeError(error) })
    fs.rmSync(VITE_CACHE_DIR, { recursive: true, force: true })
  }

  const sourceFinal = sourceSnapshot()
  const gitFinal = gitSnapshot()
  const harnessFinal = fileSnapshot(HARNESS_PATH)
  const environmentFinal = viteEnvironmentSnapshot()
  const sourceIdentical = JSON.stringify(SOURCE_INITIAL) === JSON.stringify(sourceFinal)
  const gitIdentical = JSON.stringify(GIT_INITIAL) === JSON.stringify(gitFinal)
  const harnessIdentical = JSON.stringify(HARNESS_INITIAL) === JSON.stringify(harnessFinal)
  const environmentIdentical = JSON.stringify(ENVIRONMENT_INITIAL) === JSON.stringify(environmentFinal)
  if (!failure) {
    try {
      assert.equal(sourceIdentical, true, 'Bound source changed during browser capture.')
      assert.equal(gitIdentical, true, 'Git state changed during browser capture.')
      assert.equal(harnessIdentical, true, 'Harness changed during browser capture.')
      assert.equal(environmentIdentical, true, 'Vite environment changed during browser capture.')
    } catch (error) {
      failure = safeError(error)
      process.exitCode = 1
    }
  }

  const completedAt = new Date().toISOString()
  const metrics = {
    schemaVersion: 1,
    stage: 6,
    phase: 3,
    scope: 'profile-privacy-authority-browser-acceptance',
    status: failure ? 'failed' : 'passed',
    promotable: !failure,
    runId: RUN_ID,
    startedAt,
    completedAt,
    source: {
      expectedSha256: process.env.STAGE6_PHASE3_EXPECTED_SOURCE_SHA256,
      pre: SOURCE_INITIAL,
      post: sourceFinal,
      sha256: SOURCE_BINDING_SHA256,
      identical: sourceIdentical,
    },
    git: { pre: GIT_INITIAL, post: gitFinal, identical: gitIdentical },
    harness: { pre: HARNESS_INITIAL, post: harnessFinal, identical: harnessIdentical },
    environment: { pre: ENVIRONMENT_INITIAL, post: environmentFinal, identical: environmentIdentical },
    runtime: {
      node: process.versions.node,
      playwright: require('playwright/package.json').version,
      vite: require('vite/package.json').version,
      fixedTime: FIXED_TIME,
      fixturePolicy: 'synthetic identities only; no real backend, credentials, customer data, or external traffic',
      executionPolicy: 'clean committed source only; no diagnostic or partial capture',
    },
    assertions,
    screenshots,
    runtimeSummaries,
    diagnostics,
    failure,
    claimBoundary: 'This local harness proves only the bounded Phase 3 browser behaviors exercised with synthetic API and realtime fixtures. It does not prove backend enforcement independently, alter Messenger/Forward discovery, publish Figma/Sites, deploy, or close Stage 6.',
  }
  const serializedMetrics = `${JSON.stringify(metrics, null, 2)}\n`
  assert.equal(serializedMetrics.includes(LEGACY_PROFILE_QUERY_SENTINEL), false, 'Evidence metrics retained legacy identity query data.')
  assert.equal(serializedMetrics.includes(PRIVATE_ERROR_DETAIL_SENTINEL), false, 'Evidence metrics retained private error detail.')
  fs.writeFileSync(METRICS_PATH, serializedMetrics)
  fs.writeFileSync(BINDING_PATH, `${JSON.stringify({
    schemaVersion: 1,
    stage: 6,
    phase: 3,
    runId: RUN_ID,
    status: metrics.status,
    promotable: metrics.promotable,
    sourceBindingSha256: SOURCE_BINDING_SHA256,
    sourceFileCount: SOURCE_INITIAL.length,
    branch: GIT_INITIAL.branch,
    commit: GIT_INITIAL.commit,
    tree: GIT_INITIAL.tree,
    parent: GIT_INITIAL.parent,
    sourceIdentical,
    gitIdentical,
    harnessIdentical,
    environmentIdentical,
  }, null, 2)}\n`)
  progress('complete', {
    status: metrics.status,
    assertionCount: assertions.length,
    screenshotCount: screenshots.length,
    failure: failure ? { name: failure.name, message: failure.message } : null,
  })
}

await main()
