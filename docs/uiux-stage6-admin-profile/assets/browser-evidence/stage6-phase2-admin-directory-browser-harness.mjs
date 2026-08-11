#!/usr/bin/env node

/*
 * Stage 6 / Phase 2 browser evidence harness
 *
 * This is deliberately an ignored, local evidence tool.  It never changes
 * product source, never contacts a real backend, and refuses a promotable run
 * unless a separately-bound, clean source commit is supplied.  Diagnostic
 * runs can bind the in-progress worktree, but still fail if any bounded source,
 * Git state, harness, or Vite environment changes during the run.
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
const RUN_AUTHORIZATION = 'STAGE6 PHASE2 ADMIN DIRECTORY — RUN'
const FIXED_TIME = '2026-08-11T16:30:00.000Z'
const FIXED_EPOCH_SECONDS = Math.floor(Date.parse(FIXED_TIME) / 1000)
const RAW_QUERY_SENTINEL = 'stage6-raw-query-sentinel-9a8c7b6d'
const RUN_ID = `uiux-stage6-phase2-browser-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join(EVIDENCE_DIR, 'runs', RUN_ID)
const METRICS_PATH = path.join(OUTPUT_DIR, 'stage6-phase2-admin-directory-metrics.json')
const BINDING_PATH = path.join(OUTPUT_DIR, 'stage6-phase2-source-binding.json')
const VITE_CACHE_DIR = path.join(tmpdir(), `${RUN_ID}-vite-cache`)
const DIAGNOSTIC_MODE = process.env.STAGE6_PHASE2_BROWSER_DIAGNOSTIC === '1'
const ONLY_SUITE = process.env.STAGE6_PHASE2_BROWSER_ONLY?.trim() || ''

const VIEWPORTS = Object.freeze([
  { label: 'mobile-360', width: 360, height: 740 },
  { label: 'mobile-375', width: 375, height: 812 },
  { label: 'mobile-390', width: 390, height: 844 },
  { label: 'mobile-414', width: 414, height: 896 },
  { label: 'mobile-430', width: 430, height: 932 },
  { label: 'desktop-1440', width: 1440, height: 900 },
])

const SOURCE_DIRECT_FILES = Object.freeze([
  'frontend/index.html',
  'frontend/package.json',
  'frontend/package-lock.json',
  'frontend/vite.config.ts',
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
  const status = gitText(['status', '--porcelain=v1', '--untracked-files=all'])
  return {
    branch: gitText(['branch', '--show-current']) || null,
    detached: gitText(['branch', '--show-current']) === '',
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
  return String(value).split(RAW_QUERY_SENTINEL).join('[redacted-query]')
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
    phase: 2,
    scope: 'admin-directory-browser-acceptance',
    branch: GIT_INITIAL.branch,
    commit: GIT_INITIAL.commit,
    tree: GIT_INITIAL.tree,
    parent: GIT_INITIAL.parent,
    trackedClean: GIT_INITIAL.trackedClean,
    sourceFileCount: SOURCE_INITIAL.length,
    sourceBindingSha256: SOURCE_BINDING_SHA256,
    harness: HARNESS_INITIAL,
    runModes: {
      diagnostic: 'requires STAGE6_PHASE2_BROWSER_DIAGNOSTIC=1 plus the authorization and expected binding variables',
      promotable: 'requires a Git-clean bound source and omits diagnostic mode',
    },
  }, null, 2)}\n`)
  process.exit(0)
}

assert.equal(
  process.env.STAGE6_PHASE2_BROWSER_AUTHORIZATION,
  RUN_AUTHORIZATION,
  `Browser execution is locked. Set STAGE6_PHASE2_BROWSER_AUTHORIZATION to ${JSON.stringify(RUN_AUTHORIZATION)}.`,
)
assert.equal(
  process.env.STAGE6_PHASE2_EXPECTED_SOURCE_SHA256,
  SOURCE_BINDING_SHA256,
  'STAGE6_PHASE2_EXPECTED_SOURCE_SHA256 does not match the bounded frontend source snapshot.',
)
assert.equal(
  process.env.STAGE6_PHASE2_EXPECTED_COMMIT,
  GIT_INITIAL.commit,
  'STAGE6_PHASE2_EXPECTED_COMMIT does not match the current implementation commit.',
)
assert.equal(GIT_INITIAL.branch, EXPECTED_BRANCH, 'Stage 6 Phase 2 evidence is on the wrong branch.')
if (!DIAGNOSTIC_MODE) {
  assert.equal(GIT_INITIAL.trackedClean, true, 'Promotable capture requires a Git-clean implementation worktree.')
  assert.equal(ONLY_SUITE, '', 'Promotable capture may not skip any Phase 2 evidence suite.')
}

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
  expectedProfileResponseConsoleEvents: [],
  pageErrors: [],
  unexpectedApiRequests: [],
  sameOriginRequestFailures: [],
  nonApiRequestFailures: [],
  benignAbortedDevResources: [],
  externalRequestsBlocked: [],
  websocketEvents: [],
  eventSourceEvents: [],
  unexpectedTransports: [],
}
const assertions = []
const screenshots = []
const runtimeSummaries = []

function record(id, details = {}) {
  assertions.push({ id, passed: true, ...details })
}

function progress(stage, details = {}) {
  process.stdout.write(`${JSON.stringify({ event: 'stage6-phase2-browser-progress', runId: RUN_ID, stage, ...details })}\n`)
}

function base64Url(value) {
  return Buffer.from(JSON.stringify(value)).toString('base64url')
}

function createJwt(subject) {
  return `${base64Url({ alg: 'none', typ: 'JWT' })}.${base64Url({ sub: String(subject), exp: FIXED_EPOCH_SECONDS + 3600 })}.synthetic`
}

function currentUser(role) {
  const superAdmin = role === 'مدیر ارشد'
  return {
    id: superAdmin ? 9601 : 9602,
    account_name: superAdmin ? 'stage6_synthetic_super' : 'stage6_synthetic_middle',
    full_name: superAdmin ? 'مدیر ارشد آزمایشی' : 'مدیر میانی آزمایشی',
    role,
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
  }
}

function directoryUser(index) {
  const id = 9401 + index
  const role = index % 9 === 0 ? 'مدیر میانی' : index % 5 === 0 ? 'پلیس' : index % 3 === 0 ? 'تماشا' : 'عادی'
  return {
    id,
    full_name: `کاربر آزمایشی ${index + 1}`,
    account_name: `stage6_directory_user_${String(index + 1).padStart(2, '0')}`,
    mobile_number: 'ثبت‌نشده',
    telegram_id: 0,
    role,
    account_status: index % 11 === 0 ? 'inactive' : 'active',
    has_bot_access: true,
    created_at: '2026-08-01T00:00:00.000Z',
    max_sessions: 1,
    max_accountants: 3,
    max_customers: 5,
    can_block_users: true,
    max_blocked_users: 10,
    max_daily_trades: null,
    max_active_commodities: null,
    max_daily_requests: null,
    limitations_expire_at: null,
    limitations_expire_at_jalali: null,
    trading_restricted_until: null,
    trading_restricted_until_jalali: null,
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
    accountant_owner_account_name: null,
  }
}

const DIRECTORY_USERS = Object.freeze(Array.from({ length: 54 }, (_value, index) => directoryUser(index)))
const PROFILE_403_ID = 9793
const PROFILE_404_ID = 9794

function newState(role, viewport) {
  return {
    role,
    viewport,
    suite: `${role === 'مدیر ارشد' ? 'super' : 'middle'}-${viewport.label}`,
    owner: currentUser(role),
    token: createJwt(role === 'مدیر ارشد' ? 9601 : 9602),
    apiRequests: [],
    apiSearch: {
      requested: false,
      matchedSentinel: false,
      valuesPersisted: false,
    },
    scrollTrace: [],
    profileResponseContracts: [],
    profileRequestCounts: new Map(),
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
  const searchKeys = [...url.searchParams.keys()].sort()
  const isDirectorySearch = url.pathname === '/api/users/' && url.searchParams.has('search')
  if (isDirectorySearch) {
    const value = url.searchParams.get('search') || ''
    state.apiSearch.requested = true
    state.apiSearch.matchedSentinel ||= value === RAW_QUERY_SENTINEL
  }
  // Evidence must prove the API search occurred without ever retaining its raw
  // value.  Query keys are enough for transport-contract verification.
  state.apiRequests.push({
    pathname: url.pathname,
    method: request.method(),
    hasQuery: searchKeys.length > 0,
    queryKeys: searchKeys,
    directorySearch: isDirectorySearch,
  })
}

function profileResponse(state, id) {
  const count = (state.profileRequestCounts.get(id) || 0) + 1
  state.profileRequestCounts.set(id, count)
  if (id === PROFILE_403_ID) {
    state.profileResponseContracts.push({ id, status: 403, fields: ['detail'], containsDirectoryUser: false })
    return { status: 403, body: { detail: 'stage6_phase2_forbidden' } }
  }
  if (id === PROFILE_404_ID) {
    state.profileResponseContracts.push({ id, status: 404, fields: ['detail'], containsDirectoryUser: false })
    return { status: 404, body: { detail: 'stage6_phase2_missing' } }
  }
  const user = DIRECTORY_USERS.find((candidate) => candidate.id === id)
  if (!user) {
    state.profileResponseContracts.push({ id, status: 404, fields: ['detail'], containsDirectoryUser: false })
    return { status: 404, body: { detail: 'stage6_phase2_missing' } }
  }
  state.profileResponseContracts.push({ id, status: 200, fields: ['synthetic_user'], containsDirectoryUser: true })
  return { status: 200, body: user }
}

function expectedProfileResponseConsoleEvent(message) {
  const text = message.text()
  const location = message.location()
  let pathname = ''
  try {
    pathname = new URL(location.url).pathname
  } catch {
    return null
  }
  if (
    pathname === `/api/users/${PROFILE_403_ID}` &&
    text === 'Failed to load resource: the server responded with a status of 403 (Forbidden)'
  ) {
    return { status: 403, userId: PROFILE_403_ID }
  }
  if (
    pathname === `/api/users/${PROFILE_404_ID}` &&
    text === 'Failed to load resource: the server responded with a status of 404 (Not Found)'
  ) {
    return { status: 404, userId: PROFILE_404_ID }
  }
  return null
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
  if (pathname === '/api/users/' && method === 'GET') {
    const query = url.searchParams.get('search') || ''
    const payload = query === RAW_QUERY_SENTINEL
      ? DIRECTORY_USERS.slice(0, 1)
      : DIRECTORY_USERS
    return json(route, payload)
  }
  if (/^\/api\/users\/\d+$/u.test(pathname) && method === 'GET') {
    const id = Number(pathname.split('/').pop())
    const response = profileResponse(state, id)
    return json(route, response.body, response.status)
  }
  if ((pathname === '/api/notifications' || pathname === '/api/notifications/') && method === 'GET') {
    return json(route, [])
  }
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

  diagnostics.unexpectedApiRequests.push({ suite: state.suite, pathname, method, queryKeys: [...url.searchParams.keys()].sort() })
  return json(route, { detail: 'stage6_phase2_unexpected_api_request' }, 501)
}

function installBrowserFakes(page, state) {
  return page.addInitScript(({ token, owner, rawQuerySentinel }) => {
    window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
    localStorage.setItem('auth_token', token)
    localStorage.setItem('refresh_token', token)
    localStorage.setItem('current_user_summary', JSON.stringify(owner))
    localStorage.removeItem('suspended_refresh_token')

    const includesRaw = (value) => {
      try {
        return typeof value === 'string'
          ? value.includes(rawQuerySentinel)
          : JSON.stringify(value).includes(rawQuerySentinel)
      } catch {
        return false
      }
    }
    const safeRoute = (value) => {
      try {
        const url = new URL(value == null ? location.href : String(value), location.href)
        return {
          pathname: url.pathname,
          queryKeys: [...url.searchParams.keys()].sort(),
          scroll: url.searchParams.get('scroll'),
        }
      } catch {
        return { pathname: null, queryKeys: [], scroll: null }
      }
    }
    const historyAudit = { calls: [] }
    for (const method of ['pushState', 'replaceState']) {
      const original = history[method].bind(history)
      history[method] = (data, unused, url) => {
        historyAudit.calls.push({
          method,
          rawInState: includesRaw(data),
          rawInUrl: includesRaw(url == null ? '' : String(url)),
          route: safeRoute(url),
        })
        return original(data, unused, url)
      }
    }
    window.__stage6Phase2HistoryAudit = historyAudit

    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: Promise.resolve({ active: { postMessage() {} } }),
        getRegistration: async () => null,
        getRegistrations: async () => [],
        register: async () => ({ active: { postMessage() {} } }),
      },
    })
    class FakeNotification {
      static permission = 'denied'
      static async requestPermission() { return 'denied' }
      close() {}
    }
    Object.defineProperty(window, 'Notification', { configurable: true, value: FakeNotification })
    class FakeSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      constructor(url) {
        this.url = String(url)
        this.readyState = FakeSocket.CONNECTING
        this.listeners = new Map()
        const parsed = new URL(this.url, location.href)
        const expectedProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
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
      send() {}
      close() { this.readyState = FakeSocket.CLOSED; this.dispatch('close', new CloseEvent('close')) }
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
    window.WebSocket = FakeSocket
    window.EventSource = FakeEventSource
    window.open = () => null
  }, { token: state.token, owner: state.owner, rawQuerySentinel: RAW_QUERY_SENTINEL })
}

async function createRuntime(browser, baseUrl, role, viewport) {
  const state = newState(role, viewport)
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
  await installBrowserFakes(page, state)
  const origin = new URL(baseUrl).origin
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) {
      diagnostics.externalRequestsBlocked.push({
        suite: state.suite,
        origin: url.origin,
        pathname: url.pathname,
        method: route.request().method(),
      })
      // The static Telegram loader is intentionally replaced with an empty
      // local response; no remote code or network response is used.
      if (url.origin === 'https://telegram.org' && url.pathname.endsWith('.js')) {
        return route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: '' })
      }
      return route.abort('blockedbyclient')
    }
    if (url.pathname.startsWith('/api/')) return handleApi(route, state)
    return route.continue()
  })
  page.on('console', (message) => {
    if (message.type() === 'error') {
      const expectedProfileResponse = expectedProfileResponseConsoleEvent(message)
      if (expectedProfileResponse) {
        diagnostics.expectedProfileResponseConsoleEvents.push({ suite: state.suite, ...expectedProfileResponse })
        return
      }
      diagnostics.consoleErrors.push({
        suite: state.suite,
        text: redact(message.text()),
        location: { url: redact(message.location().url), lineNumber: message.location().lineNumber },
      })
    }
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

async function openDirectory(page, scroll = null) {
  const suffix = Number.isInteger(scroll) && scroll > 0 ? `?scroll=${scroll}` : ''
  await page.goto(`/admin/users${suffix}`, { waitUntil: 'domcontentloaded' })
  await page.locator('.user-search-form').waitFor({ state: 'visible', timeout: 30_000 })
  await page.locator('.users-list').waitFor({ state: 'visible', timeout: 30_000 })
  await settle(page)
}

async function capture(page, label) {
  const file = `stage6-phase2-admin-directory-${label}.png`
  const filePath = path.join(OUTPUT_DIR, file)
  await page.screenshot({ path: filePath, fullPage: false, animations: 'disabled' })
  const stat = fs.statSync(filePath)
  const entry = { file, bytes: stat.size, sha256: sha256File(filePath) }
  screenshots.push(entry)
  return entry
}

async function assertDirectoryStructure(page, viewport, role) {
  await page.locator('.user-search-form').waitFor({ state: 'visible' })
  await page.locator('#user-directory-search').waitFor({ state: 'visible' })
  const contract = await page.evaluate(() => {
    const form = document.querySelector('.user-search-form')
    const input = document.querySelector('#user-directory-search')
    const list = document.querySelector('.users-list')
    const rows = list ? [...list.querySelectorAll(':scope > li')] : []
    const rowButtons = rows.map((row) => row.querySelector(':scope > button.user-item'))
    const submit = form?.querySelector('button[type="submit"]')
    const visibleRowButtons = rowButtons.filter((element) => {
      if (!(element instanceof HTMLElement)) return false
      const rect = element.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= window.innerHeight
    }).slice(0, 3)
    const targetContract = [submit, ...visibleRowButtons].map((element) => {
      if (!(element instanceof HTMLElement)) return { valid: false, width: 0, height: 0, centerClickable: false }
      const rect = element.getBoundingClientRect()
      const center = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
      return {
        valid: element.tagName === 'BUTTON',
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        centerClickable: center === element || Boolean(center?.closest('button') === element),
      }
    })
    const route = document.querySelector('.app-route-scroll')
    return {
      persistentForm: form instanceof HTMLFormElement,
      inputTag: input?.tagName,
      hasLegacyToggle: document.querySelectorAll('.search-toggle-btn').length > 0,
      listTag: list?.tagName,
      rowCount: rows.length,
      allRowsAreListItems: rows.every((row) => row.tagName === 'LI'),
      allRowsHaveButtons: rowButtons.length === rows.length && rowButtons.every((button) => button instanceof HTMLButtonElement),
      visibleTargetCount: visibleRowButtons.length + (submit instanceof HTMLButtonElement ? 1 : 0),
      targetContract,
      overflow: {
        document: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        body: document.body.scrollWidth > document.body.clientWidth,
        route: route instanceof HTMLElement && route.scrollWidth > route.clientWidth,
      },
    }
  })
  assert.equal(contract.persistentForm, true, `${role}/${viewport.label}: permanent search form missing`)
  assert.equal(contract.inputTag, 'INPUT', `${role}/${viewport.label}: search input missing`)
  assert.equal(contract.hasLegacyToggle, false, `${role}/${viewport.label}: legacy search toggle returned`)
  assert.equal(contract.listTag, 'UL', `${role}/${viewport.label}: user collection is not semantic list`)
  assert.ok(contract.rowCount >= 20, `${role}/${viewport.label}: insufficient synthetic list depth for scroll evidence`)
  assert.equal(contract.allRowsAreListItems, true, `${role}/${viewport.label}: non-list row found`)
  assert.equal(contract.allRowsHaveButtons, true, `${role}/${viewport.label}: non-button row action found`)
  assert.ok(contract.visibleTargetCount >= 2, `${role}/${viewport.label}: insufficient visible button targets`)
  for (const target of contract.targetContract) {
    assert.equal(target.valid, true, `${role}/${viewport.label}: non-button target`)
    assert.ok(target.width >= 44 && target.height >= 44, `${role}/${viewport.label}: target below 44px`)
    assert.equal(target.centerClickable, true, `${role}/${viewport.label}: target center obscured`)
  }
  assert.deepEqual(contract.overflow, { document: false, body: false, route: false }, `${role}/${viewport.label}: horizontal overflow`)
}

async function assertScrollOnlyRoute(page, expectedPath = null) {
  const routeContract = await page.evaluate(() => {
    const url = new URL(location.href)
    return {
      pathname: url.pathname,
      queryKeys: [...url.searchParams.keys()].sort(),
      scroll: url.searchParams.get('scroll'),
      hasForbiddenKey: ['q', 'account_name', 'mobile', 'mobile_number', 'search'].some((key) => url.searchParams.has(key)),
    }
  })
  if (expectedPath) assert.equal(routeContract.pathname, expectedPath, 'Unexpected directory route path')
  assert.equal(routeContract.hasForbiddenKey, false, 'Sensitive directory context leaked into route query')
  assert.deepEqual(routeContract.queryKeys, routeContract.scroll ? ['scroll'] : [], 'Route context is not scroll-only')
  return routeContract
}

async function assertRawQueryPrivacy(page) {
  const result = await page.evaluate((rawQuerySentinel) => {
    const containsRaw = (value) => {
      try {
        return typeof value === 'string'
          ? value.includes(rawQuerySentinel)
          : JSON.stringify(value).includes(rawQuerySentinel)
      } catch {
        return false
      }
    }
    const storageContainsRaw = (storage) => Array.from({ length: storage.length }, (_value, index) => {
      const key = storage.key(index)
      return key ? `${key}:${storage.getItem(key) || ''}` : ''
    }).some(containsRaw)
    const historyAudit = window.__stage6Phase2HistoryAudit || { calls: [] }
    return {
      locationLeak: containsRaw(location.href),
      historyStateLeak: containsRaw(history.state),
      historyUrlLeak: historyAudit.calls.some((entry) => entry.rawInUrl),
      historyCallStateLeak: historyAudit.calls.some((entry) => entry.rawInState),
      localStorageLeak: storageContainsRaw(localStorage),
      sessionStorageLeak: storageContainsRaw(sessionStorage),
    }
  }, RAW_QUERY_SENTINEL)
  assert.deepEqual(result, {
    locationLeak: false,
    historyStateLeak: false,
    historyUrlLeak: false,
    historyCallStateLeak: false,
    localStorageLeak: false,
    sessionStorageLeak: false,
  }, 'Raw search query crossed the browser privacy boundary')
}

async function setDirectoryScroll(page, expectedMinimum) {
  const nextScroll = await page.evaluate((minimum) => {
    const target = document.querySelector('.app-route-scroll')
    if (!(target instanceof HTMLElement)) throw new Error('Missing route scroll target')
    target.scrollTop = minimum
    target.dispatchEvent(new Event('scroll'))
    return target.scrollTop
  }, expectedMinimum)
  assert.ok(nextScroll >= expectedMinimum, 'Directory list was not deep enough to set scroll evidence')
  await page.waitForFunction((value) => {
    const url = new URL(location.href)
    return url.searchParams.get('scroll') === String(value)
  }, nextScroll, { timeout: 10_000 })
  return nextScroll
}

async function recordScrollTrace(page, state, stage) {
  const trace = await page.evaluate(() => {
    const url = new URL(location.href)
    const target = document.querySelector('.app-route-scroll')
    const historyAudit = window.__stage6Phase2HistoryAudit || { calls: [] }
    return {
      pathname: url.pathname,
      queryKeys: [...url.searchParams.keys()].sort(),
      scroll: url.searchParams.get('scroll'),
      scrollTarget: target instanceof HTMLElement
        ? {
          scrollTop: target.scrollTop,
          scrollHeight: target.scrollHeight,
          clientHeight: target.clientHeight,
        }
        : null,
      adminViewCount: document.querySelectorAll('.admin-view').length,
      returnButtonCount: document.querySelectorAll('.admin-subview-return').length,
      historyCalls: historyAudit.calls.map((entry) => ({
        method: entry.method,
        rawInState: entry.rawInState,
        rawInUrl: entry.rawInUrl,
        route: entry.route,
      })),
    }
  })
  state.scrollTrace.push({ stage, ...trace })
}

async function runLayoutMatrix(browser, baseUrl) {
  const roleCases = ['مدیر ارشد', 'مدیر میانی']
  for (const role of roleCases) {
    for (const viewport of VIEWPORTS) {
      const runtime = await createRuntime(browser, baseUrl, role, viewport)
      try {
        await openDirectory(runtime.page)
        await assertDirectoryStructure(runtime.page, viewport, role)
        await assertScrollOnlyRoute(runtime.page, '/admin/users')
        await capture(runtime.page, `${role === 'مدیر ارشد' ? 'super' : 'middle'}-${viewport.label}`)
        record(`stage6-phase2-${role === 'مدیر ارشد' ? 'super' : 'middle'}-${viewport.label}-persistent-search-semantic-targets-reflow`, {
          viewport,
          role,
        })
      } finally {
        runtimeSummaries.push(summarizeRuntime(runtime.state))
        await runtime.context.close()
      }
    }
  }
}

async function runPrivacyAndScrollFlow(browser, baseUrl) {
  const viewport = VIEWPORTS.find((candidate) => candidate.label === 'mobile-390')
  assert.ok(viewport, 'Missing mobile-390 evidence viewport')
  const runtime = await createRuntime(browser, baseUrl, 'مدیر ارشد', viewport)
  try {
    const { page, state } = runtime
    await openDirectory(page)
    await page.locator('#user-directory-search').fill(RAW_QUERY_SENTINEL)
    await page.locator('.user-search-form button[type="submit"]').click()
    await page.waitForFunction((rawQuerySentinel) => {
      const input = document.querySelector('#user-directory-search')
      const rows = document.querySelectorAll('.users-list > li')
      return input instanceof HTMLInputElement && input.value === rawQuerySentinel && rows.length === 1
    }, RAW_QUERY_SENTINEL, { timeout: 10_000 })
    assert.equal(state.apiSearch.requested, true, 'Search did not reach the local intercepted API')
    assert.equal(state.apiSearch.matchedSentinel, true, 'Search API did not receive the synthetic sentinel')
    assert.equal(state.apiSearch.valuesPersisted, false, 'Raw search value was retained by evidence instrumentation')
    await assertRawQueryPrivacy(page)
    await assertScrollOnlyRoute(page, '/admin/users')
    record('stage6-phase2-raw-search-is-api-only-not-url-history-storage')

    // Do not take a screenshot while user-entered text is visible.  Clear the
    // field before visual capture or route navigation evidence continues.
    await page.getByRole('button', { name: 'پاک کردن', exact: true }).click()
    await page.waitForFunction(() => document.querySelectorAll('.users-list > li').length >= 20, undefined, { timeout: 10_000 })
    await assertRawQueryPrivacy(page)

    const scrollTop = await setDirectoryScroll(page, 640)
    await recordScrollTrace(page, state, 'list-scroll-captured')
    await assertRawQueryPrivacy(page)
    const visibleIndex = await page.evaluate(() => {
      const buttons = [...document.querySelectorAll('.users-list > li > button.user-item')]
      return buttons.findIndex((element) => {
        const rect = element.getBoundingClientRect()
        return rect.top >= 0 && rect.bottom <= window.innerHeight
      })
    })
    assert.ok(visibleIndex >= 0, 'No visible directory row was available at scroll context')
    const expectedUserId = DIRECTORY_USERS[visibleIndex]?.id
    assert.ok(expectedUserId, 'Synthetic detail fixture was missing')
    await page.locator('.users-list > li > button.user-item').nth(visibleIndex).click()
    await page.waitForURL(new RegExp(`/admin/users/${expectedUserId}(?:\\?scroll=${scrollTop})?$`, 'u'), { timeout: 15_000 })
    await recordScrollTrace(page, state, 'detail-route-loaded')
    // App-level route fading intentionally keeps an outgoing root in the DOM
    // briefly.  The incoming route is appended last, so bind actions to it.
    await page.locator('.admin-subview-return').last().waitFor({ state: 'visible' })
    await assertScrollOnlyRoute(page, `/admin/users/${expectedUserId}`)
    await assertRawQueryPrivacy(page)
    await page.locator('.admin-subview-return').last().click()
    await page.waitForTimeout(500)
    await recordScrollTrace(page, state, 'list-return-settled')
    await page.waitForURL(new RegExp(`/admin/users\\?scroll=${scrollTop}$`, 'u'), { timeout: 5_000 })
    await page.locator('#user-directory-search').waitFor({ state: 'visible' })
    await page.waitForFunction((expected) => {
      const target = document.querySelector('.app-route-scroll')
      return target instanceof HTMLElement && target.scrollTop >= expected - 2
    }, scrollTop, { timeout: 10_000 })
    await assertScrollOnlyRoute(page, '/admin/users')
    await assertRawQueryPrivacy(page)
    record('stage6-phase2-scroll-only-list-detail-return-preserves-context', { scrollTop })
  } finally {
    runtimeSummaries.push(summarizeRuntime(runtime.state))
    await runtime.context.close()
  }
}

async function runProfileErrorRecovery(browser, baseUrl, status) {
  const viewport = VIEWPORTS.find((candidate) => candidate.label === 'mobile-390')
  assert.ok(viewport, 'Missing mobile-390 evidence viewport')
  const userId = status === 403 ? PROFILE_403_ID : PROFILE_404_ID
  const heading = status === 403 ? 'دسترسی به پروفایل مجاز نیست' : 'کاربر پیدا نشد'
  const scrollTop = status === 403 ? 384 : 512
  const runtime = await createRuntime(browser, baseUrl, 'مدیر ارشد', viewport)
  try {
    const { page, state } = runtime
    await page.goto(`/admin/users/${userId}?scroll=${scrollTop}`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('heading', { name: heading, exact: true }).waitFor({ state: 'visible', timeout: 30_000 })
    await page.locator('.admin-route-profile-error').waitFor({ state: 'visible' })
    const errorBody = await page.locator('.admin-route-profile-error').innerText()
    assert.equal(errorBody.includes('stage6_directory_user_'), false, 'Profile error exposed directory identity data')
    assert.equal(errorBody.includes('ثبت‌نشده'), false, 'Profile error exposed synthetic mobile field')
    assert.equal(state.profileResponseContracts.every((entry) => (
      entry.status !== status || (entry.fields.length === 1 && entry.fields[0] === 'detail' && !entry.containsDirectoryUser)
    )), true, 'Profile error fixture contained identity fields')
    await assertScrollOnlyRoute(page, `/admin/users/${userId}`)
    await assertRawQueryPrivacy(page)

    await page.getByRole('button', { name: 'تلاش مجدد', exact: true }).click()
    await page.getByRole('heading', { name: heading, exact: true }).waitFor({ state: 'visible', timeout: 10_000 })
    assert.ok((state.profileRequestCounts.get(userId) || 0) >= 2, 'Profile retry did not re-request the local error fixture')

    await page.locator('.admin-subview-return').last().click()
    await page.waitForURL(new RegExp(`/admin/users\\?scroll=${scrollTop}$`, 'u'), { timeout: 15_000 })
    await page.locator('#user-directory-search').waitFor({ state: 'visible' })
    await page.waitForFunction((expected) => {
      const target = document.querySelector('.app-route-scroll')
      return target instanceof HTMLElement && target.scrollTop >= expected - 2
    }, scrollTop, { timeout: 10_000 })
    await assertScrollOnlyRoute(page, '/admin/users')
    await assertRawQueryPrivacy(page)
    await capture(page, `super-mobile-390-profile-${status}-recovered`)
    record(`stage6-phase2-profile-${status}-bounded-retry-and-list-recovery-no-pii`, { scrollTop })
  } finally {
    runtimeSummaries.push(summarizeRuntime(runtime.state))
    await runtime.context.close()
  }
}

function summarizeRuntime(state) {
  return {
    suite: state.suite,
    role: state.role,
    viewport: state.viewport,
    apiRequests: state.apiRequests,
    apiSearch: state.apiSearch,
    scrollTrace: state.scrollTrace,
    profileResponseContracts: state.profileResponseContracts,
    profileRequestCounts: [...state.profileRequestCounts.entries()].map(([id, count]) => ({ id, count })),
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

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true })
  const startedAt = new Date().toISOString()
  let vite = null
  let browser = null
  let baseUrl = null
  let failure = null
  try {
    const started = await startVite()
    vite = started.vite
    baseUrl = started.baseUrl
    progress('vite-ready', { diagnosticMode: DIAGNOSTIC_MODE })
    browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] })
    assert.ok(['', 'layout', 'privacy-scroll', 'profile-errors'].includes(ONLY_SUITE), 'Unknown STAGE6_PHASE2_BROWSER_ONLY suite.')
    if (!ONLY_SUITE || ONLY_SUITE === 'layout') await runLayoutMatrix(browser, baseUrl)
    if (!ONLY_SUITE || ONLY_SUITE === 'privacy-scroll') await runPrivacyAndScrollFlow(browser, baseUrl)
    if (!ONLY_SUITE || ONLY_SUITE === 'profile-errors') {
      await runProfileErrorRecovery(browser, baseUrl, 403)
      await runProfileErrorRecovery(browser, baseUrl, 404)
    }
    assert.deepEqual(exactDiagnostics(), {
      consoleErrors: [],
      pageErrors: [],
      unexpectedApiRequests: [],
      sameOriginRequestFailures: [],
      nonApiRequestFailures: [],
      unexpectedTransports: [],
    }, 'Unexpected browser diagnostics occurred.')
    record('stage6-phase2-browser-diagnostics-and-external-traffic-blocking', {
      externalRequestsBlocked: diagnostics.externalRequestsBlocked.length,
      expectedProfileResponseConsoleEvents: diagnostics.expectedProfileResponseConsoleEvents.length,
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
      assert.equal(sourceIdentical, true, 'Source changed during browser capture.')
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
    phase: 2,
    scope: 'admin-directory-browser-acceptance',
    status: failure ? 'failed' : DIAGNOSTIC_MODE ? 'diagnostic-passed' : 'passed',
    promotable: !failure && !DIAGNOSTIC_MODE,
    runId: RUN_ID,
    startedAt,
    completedAt,
    source: {
      expectedSha256: process.env.STAGE6_PHASE2_EXPECTED_SOURCE_SHA256,
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
      onlySuite: ONLY_SUITE || null,
      fixturePolicy: 'synthetic identities only; no personal data; raw search values are never retained',
    },
    assertions,
    screenshots,
    runtimeSummaries,
    diagnostics,
    failure,
    claimBoundary: 'This local harness proves only bounded Phase 2 Admin directory/profile-route browser behavior with synthetic fixtures and blocked external traffic. It does not prove backend authorization policy, mutate UserProfile authority, publish Sites, deploy, or close Stage 6.',
  }
  const serializedMetrics = `${JSON.stringify(metrics, null, 2)}\n`
  assert.equal(serializedMetrics.includes(RAW_QUERY_SENTINEL), false, 'Evidence metrics retained raw search input.')
  fs.writeFileSync(METRICS_PATH, serializedMetrics)
  fs.writeFileSync(BINDING_PATH, `${JSON.stringify({
    schemaVersion: 1,
    stage: 6,
    phase: 2,
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
