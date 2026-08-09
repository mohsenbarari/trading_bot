#!/usr/bin/env node

import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

const RUN_AUTHORIZATION = 'STAGE4 SOURCE FINAL POSTFIX — RUN'
if (process.env.STAGE4_BROWSER_AUTHORIZATION !== RUN_AUTHORIZATION) {
  throw new Error(
    `Browser execution is locked. Set STAGE4_BROWSER_AUTHORIZATION exactly to ${JSON.stringify(RUN_AUTHORIZATION)} only after source-final authorization.`,
  )
}

const WORKTREE = '/tmp/trading-bot-webapp-uiux-redesign-v2'
const FRONTEND = path.join(WORKTREE, 'frontend')
const FRONTEND_NODE_MODULES = fs.realpathSync(path.join(FRONTEND, 'node_modules'))
const PLAN_DIR = '/tmp/uiux-stage4-browser-prep-20260809'
const SOURCE_PLAN_PATH = path.join(PLAN_DIR, 'stage4-source-binding-plan.json')
const HARNESS_PATH = fileURLToPath(import.meta.url)
const RUN_ID = `uiux-stage4-browser-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join('/tmp', RUN_ID)
const METRICS_PATH = path.join(OUTPUT_DIR, 'stage4-browser-acceptance-metrics.json')
const ONLY_SUITE = process.env.STAGE4_BROWSER_ONLY?.trim() || ''
const SECURITY_360_DIAGNOSTIC = process.env.STAGE4_SECURITY_360_DIAGNOSTIC === '1'
const HOME_FOCUS_DIAGNOSTIC = process.env.STAGE4_HOME_FOCUS_DIAGNOSTIC === '1'
const HOME_KEYBOARD_DIAGNOSTIC = process.env.STAGE4_HOME_KEYBOARD_DIAGNOSTIC === '1'
const NOTIFICATIONS_CONTRAST_DIAGNOSTIC =
  process.env.STAGE4_NOTIFICATIONS_CONTRAST_DIAGNOSTIC === '1'
const HOME_OFFLINE_PWA_DIAGNOSTIC = process.env.STAGE4_HOME_OFFLINE_PWA_DIAGNOSTIC === '1'
const HOME_ACCOUNTANT_PWA_DIAGNOSTIC =
  process.env.STAGE4_HOME_ACCOUNTANT_PWA_DIAGNOSTIC === '1'
const FOCUSED_DIAGNOSTIC_ACTIVE = Boolean(
  SECURITY_360_DIAGNOSTIC ||
    HOME_FOCUS_DIAGNOSTIC ||
    HOME_KEYBOARD_DIAGNOSTIC ||
    NOTIFICATIONS_CONTRAST_DIAGNOSTIC ||
    HOME_OFFLINE_PWA_DIAGNOSTIC ||
    HOME_ACCOUNTANT_PWA_DIAGNOSTIC,
)
const VAPID_PUBLIC_KEY = `B${'A'.repeat(86)}`

const VIEWPORTS = Object.freeze([
  { label: 'mobile-360', width: 360, height: 740 },
  { label: 'mobile-375', width: 375, height: 812 },
  { label: 'mobile-390', width: 390, height: 844 },
  { label: 'mobile-414', width: 414, height: 896 },
  { label: 'mobile-430', width: 430, height: 932 },
  { label: 'tablet-768', width: 768, height: 1024 },
  { label: 'tablet-landscape-1024', width: 1024, height: 768 },
  { label: 'desktop-1440', width: 1440, height: 900 },
])

const USERS = Object.freeze({
  owner: Object.freeze({
    id: 9401,
    account_name: 'stage4_owner',
    full_name: 'مالک مرحله چهار',
    role: 'کاربر',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
  }),
  middleAdmin: Object.freeze({
    id: 9402,
    account_name: 'stage4_middle_admin',
    full_name: 'مدیر میانی مرحله چهار',
    role: 'مدیر میانی',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
  }),
  superAdmin: Object.freeze({
    id: 9403,
    account_name: 'stage4_super_admin',
    full_name: 'مدیر ارشد مرحله چهار',
    role: 'مدیر ارشد',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: true,
    telegram_linked: false,
  }),
  customer: Object.freeze({
    id: 9404,
    account_name: 'stage4_customer',
    full_name: 'مشتری مرحله چهار',
    customer_management_name: 'مشتری مرورگر',
    role: 'کاربر',
    account_status: 'active',
    is_accountant: false,
    is_customer: true,
    customer_tier: 'tier1',
    can_connect_telegram: false,
    telegram_linked: false,
  }),
  accountant: Object.freeze({
    id: 9405,
    account_name: 'stage4_accountant',
    full_name: 'حسابدار مرحله چهار',
    role: 'حسابدار',
    account_status: 'active',
    is_accountant: true,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
  }),
  inactive: Object.freeze({
    id: 9406,
    account_name: 'stage4_inactive',
    full_name: 'کاربر غیرفعال مرحله چهار',
    role: 'کاربر',
    account_status: 'inactive',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
  }),
  restricted: Object.freeze({
    id: 9407,
    account_name: 'stage4_restricted',
    full_name: 'کاربر محدود مرحله چهار',
    role: 'کاربر',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
    trading_restricted_until: '2027-08-09T12:00:00.000Z',
  }),
})

const HOSTILE_NOTIFICATION_MESSAGE = [
  '📦 کالا: طلای آب‌شده',
  'route: /raw-route-must-not-render',
  'backend: hidden-backend',
  'server: hidden-server',
  'route=/raw-equals-must-not-render',
  'backend=hidden-equals-backend',
  'مسیر：/raw-fullwidth-must-not-render',
  'server=hidden-cr\rbackend=hidden-after-cr\u2028route=/raw-after-line-separator\u2029مسیر﹕/raw-after-paragraph-separator',
  '⚖️ مقدار: ۱۲ گرم',
].join('\n')

const DEFAULT_NOTIFICATIONS = Object.freeze([
  Object.freeze({
    id: 4401,
    title: 'معامله جدید',
    message: HOSTILE_NOTIFICATION_MESSAGE,
    content: HOSTILE_NOTIFICATION_MESSAGE,
    category: 'trade',
    level: 'info',
    route: '/account/storage',
    is_read: false,
    created_at: '2026-08-09T08:30:00.000Z',
  }),
  Object.freeze({
    id: 4402,
    title: 'یادآوری حساب',
    message: 'برای مرور تنظیمات حساب آماده است.',
    content: 'برای مرور تنظیمات حساب آماده است.',
    category: 'system',
    level: 'warning',
    route: null,
    is_read: false,
    created_at: '2026-08-09T08:29:00.000Z',
  }),
  Object.freeze({
    id: 4403,
    title: 'مقصد نامعتبر',
    message: 'این اعلان فقط برای اطلاع است.',
    content: 'این اعلان فقط برای اطلاع است.',
    category: 'management',
    level: 'info',
    route: 'https://evil.example.invalid/escape',
    is_read: true,
    created_at: '2026-08-09T08:28:00.000Z',
  }),
])

const DEFAULT_SESSIONS = Object.freeze([
  Object.freeze({
    id: 'current-primary',
    device_name: 'Chrome فعلی',
    platform: 'Linux',
    is_primary: true,
    is_current: true,
    last_active_at: '2026-08-09T08:30:00.000Z',
    ip_address: '198.51.100.10',
    home_server: 'hidden-primary-origin',
  }),
  Object.freeze({
    id: 'other-phone',
    device_name: 'گوشی دیگر',
    platform: 'Android',
    is_primary: false,
    is_current: false,
    last_active_at: '2026-08-09T08:20:00.000Z',
    ip_address: '203.0.113.20',
    home_server: 'hidden-secondary-origin',
  }),
])

const SOURCE_PLAN_INITIAL_BYTES = fs.readFileSync(SOURCE_PLAN_PATH)
const HARNESS_INITIAL_BYTES = fs.readFileSync(HARNESS_PATH)
const CATALOG_SOURCE_PATH = path.join(
  FRONTEND,
  'src/components/ui/AppDesignSystemCatalog.vue',
)
const DESIGN_TOKEN_SOURCE_PATH = path.join(
  FRONTEND,
  'src/styles/design-system-v2.tokens.css',
)
const catalogSource = fs.readFileSync(CATALOG_SOURCE_PATH, 'utf8')
const semanticTokenArrayMatch = catalogSource.match(
  /const semanticTokens\s*=\s*\[([\s\S]*?)\]\s*as const/u,
)
assert.ok(semanticTokenArrayMatch, 'catalog semanticTokens source contract is missing')
const CATALOG_SEMANTIC_TOKEN_CONTRACT = Object.freeze(
  [...semanticTokenArrayMatch[1].matchAll(/name:\s*'([^']+)'/gu)].map((match) => match[1]),
)
assert.ok(CATALOG_SEMANTIC_TOKEN_CONTRACT.length > 0, 'catalog semantic token contract is empty')
assert.equal(
  new Set(CATALOG_SEMANTIC_TOKEN_CONTRACT).size,
  CATALOG_SEMANTIC_TOKEN_CONTRACT.length,
  'catalog semantic token contract contains duplicates',
)
const designTokenSource = fs.readFileSync(DESIGN_TOKEN_SOURCE_PATH, 'utf8')
for (const token of CATALOG_SEMANTIC_TOKEN_CONTRACT) {
  assert.ok(
    designTokenSource.includes(`${token}:`),
    `catalog semantic token is absent from the canonical token source: ${token}`,
  )
}
const sourcePlan = JSON.parse(SOURCE_PLAN_INITIAL_BYTES.toString('utf8'))
assert.equal(sourcePlan.schemaVersion, 1, 'unexpected source plan schema')
assert.equal(sourcePlan.stage, 4, 'source plan stage mismatch')
assert.equal(sourcePlan.worktree, WORKTREE, 'source plan worktree mismatch')
assert.equal(sourcePlan.runAuthorization, RUN_AUTHORIZATION, 'source plan authorization mismatch')
assert.deepEqual(
  sourcePlan.requiredViewports,
  VIEWPORTS.map((viewport) => viewport.width),
  'source plan viewport contract mismatch',
)
assert.equal(
  sourcePlan.status,
  'source_final_frozen',
  'source plan is not frozen; freeze it only after SOURCE FINAL authorization',
)
assert.ok(
  sourcePlan.finalBinding && typeof sourcePlan.finalBinding === 'object',
  'source plan is missing its source-final binding',
)
assert.ok(sourcePlan.sourceGroups && typeof sourcePlan.sourceGroups === 'object')
assert.ok(Array.isArray(sourcePlan.sourceTrees), 'source plan is missing sourceTrees')
const DIRECT_SOURCE_FILES = Object.values(sourcePlan.sourceGroups).flat()
assert.ok(DIRECT_SOURCE_FILES.length > 0, 'source plan contains no paths')
assert.ok(DIRECT_SOURCE_FILES.every((entry) => typeof entry === 'string' && entry.length > 0))
assert.equal(
  new Set(DIRECT_SOURCE_FILES).size,
  DIRECT_SOURCE_FILES.length,
  'source plan contains duplicate direct paths',
)
assert.ok(sourcePlan.sourceTrees.every((entry) => typeof entry === 'string' && entry.length > 0))
assert.equal(
  new Set(sourcePlan.sourceTrees).size,
  sourcePlan.sourceTrees.length,
  'source plan contains duplicate source trees',
)

function walkSourceTree(relativeRoot) {
  const absoluteRoot = path.join(WORKTREE, relativeRoot)
  assert.ok(fs.statSync(absoluteRoot).isDirectory(), `source tree is not a directory: ${relativeRoot}`)
  const files = []
  const pending = [absoluteRoot]
  while (pending.length > 0) {
    const directory = pending.pop()
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolutePath = path.join(directory, entry.name)
      if (entry.isDirectory()) {
        pending.push(absolutePath)
      } else if (entry.isFile()) {
        files.push(path.relative(WORKTREE, absolutePath).split(path.sep).join('/'))
      } else {
        throw new Error(`unsupported source tree entry: ${absolutePath}`)
      }
    }
  }
  return files
}

function resolveSourceFiles() {
  return [...new Set([
    ...DIRECT_SOURCE_FILES,
    ...sourcePlan.sourceTrees.flatMap(walkSourceTree),
  ])].sort()
}

const BOOTSTRAP_SOURCE_FILES = resolveSourceFiles()
assert.ok(BOOTSTRAP_SOURCE_FILES.length > 0, 'resolved source binding is empty')

const SANITIZED_ENV_EXACT_KEYS = Object.freeze([
  'E2E_BACKEND_BASE_URL',
  'FRONTEND_BUILD_OUT_DIR',
  'NODE_ENV',
  'VITEST',
])
const SANITIZED_ENV_KEYS = Object.keys(process.env)
  .filter((key) => key.startsWith('VITE_') || SANITIZED_ENV_EXACT_KEYS.includes(key))
  .sort()
for (const key of SANITIZED_ENV_KEYS) delete process.env[key]
process.env.NODE_ENV = 'development'

const VITE_ENV_FILES = Object.freeze([
  '.env',
  '.env.local',
  '.env.development',
  '.env.development.local',
])
for (const envFile of VITE_ENV_FILES) {
  assert.equal(
    fs.existsSync(path.join(FRONTEND, envFile)),
    false,
    `unbound Vite environment file is not allowed: frontend/${envFile}`,
  )
}

const require = createRequire(path.join(FRONTEND, 'package.json'))
const RUNTIME_VERSIONS = Object.freeze({
  node: process.versions.node,
  playwright: require('playwright/package.json').version,
  vite: require('vite/package.json').version,
  sharp: require('sharp/package.json').version,
  vazirmatn: require('vazirmatn/package.json').version,
})
const RUNTIME_ENVIRONMENT = Object.freeze({
  nodeEnv: process.env.NODE_ENV,
  sanitizedKeyPolicy: ['VITE_*', ...SANITIZED_ENV_EXACT_KEYS],
  forbiddenEnvFiles: VITE_ENV_FILES,
})
assert.deepEqual(
  sourcePlan.finalBinding.runtimeVersions,
  RUNTIME_VERSIONS,
  'source-final runtime version binding mismatch',
)
assert.equal(
  sourcePlan.finalBinding.harnessSha256,
  sha256(HARNESS_INITIAL_BYTES),
  'source-final harness hash mismatch',
)
assert.deepEqual(
  sourcePlan.finalBinding.runtimeEnvironment,
  RUNTIME_ENVIRONMENT,
  'source-final runtime environment binding mismatch',
)
const { chromium } = require('playwright')
const sharp = require('sharp')
const viteEntry = require.resolve('vite')
const { createServer } = await import(pathToFileURL(viteEntry).href)

fs.mkdirSync(OUTPUT_DIR, { recursive: true })

const assertions = []
const screenshots = []
const browserDiagnostics = {
  consoleErrors: [],
  pageErrors: [],
  httpFailures: [],
  unexpectedRequestFailures: [],
  blockedExternalRequests: [],
  unexpectedApiRequests: [],
  webSockets: [],
  unexpectedWebSockets: [],
}
const pageStates = new WeakMap()

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}

function sha256File(filePath) {
  return sha256(fs.readFileSync(filePath))
}

function fileSnapshot(filePath) {
  const stat = fs.statSync(filePath, { bigint: true })
  return {
    bytes: Number(stat.size),
    mtimeNs: stat.mtimeNs.toString(),
    sha256: sha256File(filePath),
  }
}

function sourcePlanSnapshot() {
  return fileSnapshot(SOURCE_PLAN_PATH)
}

function harnessSnapshot() {
  return fileSnapshot(HARNESS_PATH)
}

function environmentSnapshot() {
  return VITE_ENV_FILES.map((file) => {
    const absolutePath = path.join(FRONTEND, file)
    return {
      file: `frontend/${file}`,
      exists: fs.existsSync(absolutePath),
      ...(fs.existsSync(absolutePath) ? fileSnapshot(absolutePath) : {}),
    }
  })
}

function sourceSnapshot() {
  return resolveSourceFiles().map((relativePath) => {
    const absolutePath = path.join(WORKTREE, relativePath)
    const stat = fs.statSync(absolutePath, { bigint: true })
    return {
      path: relativePath,
      bytes: Number(stat.size),
      mtimeNs: stat.mtimeNs.toString(),
      sha256: sha256File(absolutePath),
    }
  })
}

function record(id, details = {}) {
  assertions.push({ id, passed: true, ...details })
}

function progress(stage, details = {}) {
  process.stdout.write(
    `${JSON.stringify({ event: 'stage4-browser-progress', runId: RUN_ID, stage, ...details })}\n`,
  )
}

function createJwt(payload) {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url')
  return `${header}.${body}.stage4-browser`
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function newRuntimeState(overrides = {}) {
  return {
    user: { ...USERS.owner },
    cachedUser: undefined,
    meMode: 'success',
    meGate: deferred(),
    meSequence: null,
    meSequenceCursor: 0,
    verifyMode: 'success',
    chatPollMode: 'success',
    notificationMode: 'success',
    notificationRows: DEFAULT_NOTIFICATIONS.map((row) => ({ ...row })),
    serverUnreadCount: 0,
    notificationGate: deferred(),
    pushScenario: 'permission-default',
    pushGate: deferred(),
    pushPermissionResult: 'granted',
    pushPostFailuresRemaining: 0,
    serviceWorkerReadyMode: 'ready',
    sessions: DEFAULT_SESSIONS.map((row) => ({ ...row })),
    sessionsMode: 'success',
    sessionsFailuresRemaining: 0,
    terminateMode: 'success',
    logoutOthersMode: 'success',
    localLogoutMode: 'success',
    storageFailure: null,
    telegramUrl: 'https://t.me/stage4_browser_bot?start=stage4-safe-fixture',
    telegramBotUsername: 'stage4_browser_bot',
    telegramStartParameter: 'stage4-safe-fixture',
    telegramDetail: null,
    marketOfferPushEnabled: false,
    requestLog: [],
    authToken: null,
    websocketEvents: [],
    intentionalNavigation: false,
    intentionalClose: false,
    ...overrides,
  }
}

function json(route, body, status = 200, headers = {}) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    headers: { 'Cache-Control': 'no-store', Pragma: 'no-cache', ...headers },
    body: body === null ? 'null' : JSON.stringify(body),
  })
}

function requestCount(state, pathname, method = null) {
  return state.requestLog.filter(
    (entry) => entry.pathname === pathname && (!method || entry.method === method),
  ).length
}

function apiMethodContract(pathname) {
  const contracts = {
    '/api/auth/me': ['GET'],
    '/api/auth/refresh': ['POST'],
    '/api/auth/switchable-users': ['GET'],
    '/api/auth/telegram-link-token': ['POST'],
    '/api/sessions/verify': ['POST'],
    '/api/sessions/recovery/pending': ['GET'],
    '/api/sessions/login-requests/pending': ['GET'],
    '/api/sessions/active': ['GET'],
    '/api/sessions/logout-all': ['POST'],
    '/api/chat/poll': ['GET'],
    '/api/chat/conversations': ['GET'],
    '/api/notifications/': ['GET'],
    '/api/notifications/unread-count': ['GET'],
    '/api/notifications/mark-all-read': ['POST'],
    '/api/notifications/push/public-key': ['GET'],
    '/api/notifications/push/subscription': ['POST', 'DELETE'],
    '/api/notifications/push/test': ['POST'],
    '/api/notifications/preferences': ['GET', 'PATCH'],
    '/api/config': ['GET'],
    '/api/offers/page': ['GET'],
    '/api/offers/my': ['GET'],
    '/api/offers/my/repeatable': ['GET'],
    '/api/offers/market-history': ['GET'],
    '/api/trades/my': ['GET'],
    '/api/commodities/': ['GET'],
    '/api/trading-settings/': ['GET'],
    '/api/trading-settings/market-state': ['GET'],
    '/api/trading-settings/market-overrides': ['GET'],
    '/api/admin-messages/market/current': ['GET'],
    '/api/invitations/pending': ['GET'],
    '/api/customers/owner-relations': ['GET'],
    '/api/accountants/owner-relations': ['GET'],
  }
  if (contracts[pathname]) return contracts[pathname]
  if (/^\/api\/sessions\/login-requests\/[^/]+\/(?:approve|reject)$/u.test(pathname)) {
    return ['POST']
  }
  if (/^\/api\/sessions\/[^/]+$/u.test(pathname)) return ['DELETE']
  if (/^\/api\/notifications\/[^/]+\/read$/u.test(pathname)) return ['PATCH']
  if (pathname.startsWith('/api/users-public/')) return ['GET']
  return null
}

function parseJsonBody(postData, label) {
  assert.ok(postData, `${label}: request body is required`)
  let payload
  try {
    payload = JSON.parse(postData)
  } catch {
    assert.fail(`${label}: request body must be JSON`)
  }
  assert.ok(payload && typeof payload === 'object' && !Array.isArray(payload), `${label}: JSON object required`)
  return payload
}

async function handleApiRoute(route, state) {
  const request = route.request()
  const url = new URL(request.url())
  const pathname = url.pathname
  const method = request.method()
  const postData = request.postData() || ''
  state.requestLog.push({ pathname, search: url.search, method, postData })

  const allowedMethods = apiMethodContract(pathname)
  if (allowedMethods && !allowedMethods.includes(method)) {
    browserDiagnostics.unexpectedApiRequests.push({
      pathname,
      method,
      expectedMethods: allowedMethods,
      suite: state.suite,
    })
    return json(route, { detail: 'stage4_method_contract_violation' }, 405, {
      Allow: allowedMethods.join(', '),
    })
  }

  if (pathname === '/api/auth/me') {
    if (Array.isArray(state.meSequence)) {
      const sequenceIndex = state.meSequenceCursor
      state.meSequenceCursor += 1
      const sequenceEntry = state.meSequence[sequenceIndex]
      assert.ok(sequenceEntry, `meSequence exhausted at request ${sequenceIndex + 1}`)
      if (sequenceEntry.gate) await sequenceEntry.gate.promise
      if (sequenceEntry.mode === 'error') {
        return json(route, { detail: 'opaque-current-user-sequence-failure' }, 400)
      }
      return json(route, sequenceEntry.user)
    }
    if (state.meMode === 'hold') await state.meGate.promise
    if (state.meMode === 'error') return json(route, { detail: 'opaque-current-user-failure' }, 400)
    if (state.meMode === 'reconnecting') return json(route, { detail: 'temporary' }, 503)
    return json(route, state.user)
  }
  if (pathname === '/api/auth/refresh') {
    return json(route, {
      access_token: createJwt({ sub: String(state.user.id), exp: Math.floor(Date.now() / 1000) + 3600 }),
      refresh_token: createJwt({ sub: String(state.user.id), exp: Math.floor(Date.now() / 1000) + 86400 }),
    })
  }
  if (pathname === '/api/auth/switchable-users') return json(route, [])
  if (pathname === '/api/sessions/verify') {
    const payload = parseJsonBody(postData, 'session verification')
    assert.deepEqual(Object.keys(payload).sort(), ['refresh_token'])
    assert.equal(typeof payload.refresh_token, 'string')
    assert.ok(payload.refresh_token.length > 0, 'session verification refresh token is empty')
    if (state.verifyMode === 'abort') return route.abort('failed')
    return json(route, { ok: true })
  }
  if (pathname === '/api/sessions/recovery/pending') return json(route, [])
  if (pathname === '/api/sessions/login-requests/pending') return json(route, [])
  if (/^\/api\/sessions\/login-requests\/[^/]+\/(?:approve|reject)$/u.test(pathname)) {
    return json(route, { detail: 'درخواست بررسی شد' })
  }
  if (pathname === '/api/sessions/active') {
    if (state.sessionsFailuresRemaining > 0) {
      state.sessionsFailuresRemaining -= 1
      return json(route, { detail: 'opaque-session-refresh-failure' }, 400)
    }
    if (state.sessionsMode === 'error') return json(route, { detail: 'opaque-session-failure' }, 400)
    return json(route, state.sessions)
  }
  if (pathname === '/api/sessions/logout-all') {
    if (state.logoutOthersMode === 'error') return json(route, { detail: 'opaque' }, 400)
    if (state.logoutOthersMode === 'invalid-receipt') return json(route, {})
    state.sessions = state.sessions.filter((session) => session.is_current)
    return json(route, { detail: 'نشست‌های دیگر بسته شدند.' })
  }
  const deleteSessionMatch = pathname.match(/^\/api\/sessions\/([^/]+)$/u)
  if (deleteSessionMatch && method === 'DELETE') {
    const sessionId = decodeURIComponent(deleteSessionMatch[1])
    const mode = sessionId === 'current-primary' ? state.localLogoutMode : state.terminateMode
    if (mode === 'error') return json(route, { detail: 'opaque' }, 400)
    if (mode === 'invalid-receipt') return json(route, {})
    state.sessions = state.sessions.filter((session) => String(session.id) !== sessionId)
    return json(route, { detail: 'نشست انتخاب‌شده بسته شد.' })
  }
  if (pathname === '/api/chat/poll') {
    if (state.chatPollMode === 'reconnecting') {
      return json(route, { detail: 'temporary-chat-poll-failure' }, 503)
    }
    return json(route, {
      conversations_with_unread: [],
      muted_conversation_ids: [],
      unread_chats_count: 0,
      total_unread_mentions: 0,
    })
  }
  if (pathname === '/api/chat/conversations') return json(route, [])
  if (pathname === '/api/notifications/' && method === 'GET') {
    assert.equal(url.searchParams.get('limit'), '50', 'notification history limit must be 50')
    assert.equal(url.searchParams.get('offset'), '0', 'notification history offset must be 0')
    const notificationSnapshot = state.notificationRows.map((row) => ({ ...row }))
    if (state.notificationMode === 'hold') await state.notificationGate.promise
    if (state.notificationMode === 'error') {
      return json(route, { detail: 'opaque-notification-history-failure' }, 400)
    }
    return json(route, notificationSnapshot)
  }
  if (pathname === '/api/notifications/unread-count') {
    assert.equal(url.search, '', 'notification unread count must not send raw query metadata')
    assert.ok(
      Number.isInteger(state.serverUnreadCount) && state.serverUnreadCount >= 0,
      'server unread count fixture must be a non-negative integer',
    )
    return json(route, state.serverUnreadCount)
  }
  if (pathname === '/api/notifications/mark-all-read') {
    let payload = {}
    try {
      payload = postData ? JSON.parse(postData) : {}
    } catch {
      payload = {}
    }
    const ids = Array.isArray(payload.notification_ids)
      ? payload.notification_ids
      : Array.isArray(payload.ids)
        ? payload.ids
        : null
    const normalizedIds = ids ? new Set(ids.map((id) => String(id))) : null
    state.notificationRows = state.notificationRows.map((row) => ({
      ...row,
      is_read:
        normalizedIds === null || normalizedIds.has(String(row.id)) ? true : row.is_read,
    }))
    return route.fulfill({ status: 204 })
  }
  const notificationReadMatch = pathname.match(/^\/api\/notifications\/([^/]+)\/read$/u)
  if (notificationReadMatch && method === 'PATCH') {
    const notificationId = decodeURIComponent(notificationReadMatch[1])
    state.notificationRows = state.notificationRows.map((row) =>
      String(row.id) === notificationId ? { ...row, is_read: true } : row,
    )
    return route.fulfill({ status: 204 })
  }
  if (pathname === '/api/notifications/push/public-key') {
    if (state.pushScenario === 'checking') await state.pushGate.promise
    if (state.pushScenario === 'error') return json(route, { detail: 'opaque-push-failure' }, 400)
    if (state.pushScenario === 'server-disabled') {
      return json(route, { enabled: false, public_key: null, missing: ['VAPID_PUBLIC_KEY'] })
    }
    return json(route, { enabled: true, public_key: VAPID_PUBLIC_KEY, missing: [] })
  }
  if (pathname === '/api/notifications/push/subscription') {
    const payload = parseJsonBody(postData, 'push subscription')
    assert.equal(
      request.headers().authorization,
      `Bearer ${state.authToken}`,
      'push subscription must use the current authenticated account',
    )
    if (method === 'POST') {
      assert.deepEqual(Object.keys(payload).sort(), ['endpoint', 'keys', 'platform'])
      assert.equal(payload.endpoint, 'https://push.invalid/stage4-browser')
      assert.deepEqual(payload.keys, { p256dh: 'stage4-p256dh', auth: 'stage4-auth' })
      assert.equal(typeof payload.platform, 'string')
      assert.ok(payload.platform.length > 0, 'push subscription platform is empty')
    } else {
      assert.deepEqual(payload, { endpoint: 'https://push.invalid/stage4-browser' })
    }
    if (method === 'POST' && state.pushPostFailuresRemaining > 0) {
      state.pushPostFailuresRemaining -= 1
      return json(route, { detail: 'opaque-push-registration-failure' }, 400)
    }
    return json(route, { ok: true })
  }
  if (pathname === '/api/notifications/push/test') {
    return json(route, { total: 1, sent: 1, failed: 0, disabled: 0 })
  }
  if (pathname === '/api/auth/telegram-link-token') {
    return json(route, {
      telegram_linked: false,
      can_connect_telegram: true,
      telegram_url: state.telegramUrl,
      bot_username: state.telegramBotUsername,
      start_parameter: state.telegramStartParameter,
      detail: state.telegramDetail,
    })
  }
  if (pathname === '/api/config') return json(route, { bot_username: 'stage4_browser_bot' })

  if (pathname === '/api/offers/page') {
    return json(route, { items: [], next_cursor: null, has_more: false, page_size: 0 })
  }
  if (
    pathname === '/api/offers/my'
    || pathname === '/api/offers/my/repeatable'
    || pathname === '/api/offers/market-history'
    || pathname === '/api/trades/my'
  ) return json(route, [])
  if (pathname === '/api/commodities/') {
    return json(route, [{ id: 1, name: 'طلای آب‌شده', aliases: [] }])
  }
  if (pathname === '/api/trading-settings/') {
    return json(route, {
      offer_min_quantity: 1,
      offer_max_quantity: 1000,
      lot_min_size: 5,
      lot_max_count: 5,
      offer_expiry_minutes: 60,
      market_schedule_enabled: true,
      market_timezone: 'Asia/Tehran',
      market_open_time_local: '10:00',
      market_close_time_local: '18:00',
      market_closed_weekdays: [4],
    })
  }
  if (pathname === '/api/trading-settings/market-state') {
    return json(route, {
      is_open: true,
      active_web_notice_visible: false,
      offers_since_last_open: 0,
      last_transition_at: null,
      next_transition_at: null,
    })
  }
  if (pathname === '/api/trading-settings/market-overrides') return json(route, [])
  if (pathname === '/api/admin-messages/market/current') return json(route, null)
  if (pathname === '/api/notifications/preferences') {
    if (method === 'PATCH') {
      const payload = parseJsonBody(postData, 'notification preferences')
      assert.deepEqual(Object.keys(payload), ['market_offer_push_enabled'])
      assert.equal(typeof payload.market_offer_push_enabled, 'boolean')
      state.marketOfferPushEnabled = payload.market_offer_push_enabled
    }
    return json(route, { market_offer_push_enabled: state.marketOfferPushEnabled })
  }
  if (pathname === '/api/invitations/pending') return json(route, [])
  if (pathname === '/api/customers/owner-relations') return json(route, [])
  if (pathname === '/api/accountants/owner-relations') return json(route, [])
  if (pathname.startsWith('/api/users-public/')) return json(route, [])

  browserDiagnostics.unexpectedApiRequests.push({ pathname, method, suite: state.suite })
  return json(route, { detail: 'stage4_unexpected_api_request' }, 501)
}

async function createPage(browser, baseUrl, options = {}) {
  const state = options.state || newRuntimeState()
  state.suite = options.suite || 'unassigned'
  const context = await browser.newContext({
    baseURL: baseUrl,
    locale: 'fa-IR',
    timezoneId: 'Asia/Tehran',
    serviceWorkers: 'block',
    reducedMotion: options.reducedMotion || 'no-preference',
  })
  const page = await context.newPage()
  const successfulNoContentRequests = new WeakSet()
  pageStates.set(page, state)
  await context.exposeBinding('__stage4RecordWebSocket', (_source, event) => {
    state.websocketEvents.push(event)
    browserDiagnostics.webSockets.push({ ...event, suite: state.suite })
    if (!event.valid) {
      browserDiagnostics.unexpectedWebSockets.push({ ...event, suite: state.suite })
    }
  })
  const token = createJwt({
    sub: String(state.user.id || 9401),
    exp: Math.floor(Date.now() / 1000) + 3600,
    session_id: `stage4-${state.suite}`,
  })
  state.authToken = token
  const cachedUser = Object.prototype.hasOwnProperty.call(options, 'cachedUser')
    ? options.cachedUser
    : Object.prototype.hasOwnProperty.call(state, 'cachedUser') && state.cachedUser !== undefined
      ? state.cachedUser
      : state.user

  await page.addInitScript(
    ({
      authenticated,
      accessToken,
      userSummary,
      pushScenario,
      pushPermissionResult,
      serviceWorkerReadyMode,
      storageFailure,
    }) => {
      window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
      delete window.__PLAYWRIGHT_ENABLE_PWA_REGISTRATION__
      const seedKey = '__stage4_browser_auth_seeded'
      if (sessionStorage.getItem(seedKey) !== '1') {
        sessionStorage.setItem(seedKey, '1')
        if (authenticated) {
          localStorage.setItem('auth_token', accessToken)
          localStorage.setItem('refresh_token', accessToken)
          if (userSummary) localStorage.setItem('current_user_summary', JSON.stringify(userSummary))
          else localStorage.removeItem('current_user_summary')
          localStorage.removeItem('suspended_refresh_token')
        } else {
          localStorage.removeItem('auth_token')
          localStorage.removeItem('refresh_token')
          localStorage.removeItem('current_user_summary')
          localStorage.removeItem('suspended_refresh_token')
        }
      }

      window.__stage4PermissionRequests = 0
      window.__stage4PushSubscriptions = 0
      window.__stage4StorageFailure = storageFailure
      const serviceWorkerMessageKey = '__stage4_service_worker_messages'
      const persistedServiceWorkerMessages = (() => {
        try {
          const value = JSON.parse(sessionStorage.getItem(serviceWorkerMessageKey) || '[]')
          return Array.isArray(value) ? value : []
        } catch {
          return []
        }
      })()
      window.__stage4ServiceWorkerMessages = persistedServiceWorkerMessages
      const recordServiceWorkerMessage = (message) => {
        const safeMessage = {
          type: typeof message?.type === 'string' ? message.type : null,
          enabled: typeof message?.enabled === 'boolean' ? message.enabled : null,
          authTokenPresent: typeof message?.authToken === 'string' && message.authToken.length > 0,
        }
        window.__stage4ServiceWorkerMessages.push(safeMessage)
        sessionStorage.setItem(
          serviceWorkerMessageKey,
          JSON.stringify(window.__stage4ServiceWorkerMessages),
        )
      }

      let activeSubscription = null
      const subscription = {
        endpoint: 'https://push.invalid/stage4-browser',
        toJSON: () => ({
          endpoint: 'https://push.invalid/stage4-browser',
          keys: { p256dh: 'stage4-p256dh', auth: 'stage4-auth' },
        }),
        unsubscribe: async () => {
          activeSubscription = null
          return true
        },
      }
      if (pushScenario === 'subscribed') activeSubscription = subscription
      const pushManager = {
        getSubscription: async () => activeSubscription,
        subscribe: async () => {
          window.__stage4PushSubscriptions += 1
          activeSubscription = subscription
          return subscription
        },
      }
      const ready = serviceWorkerReadyMode === 'hold'
        ? new Promise(() => undefined)
        : Promise.resolve({ pushManager })

      if (pushScenario === 'unsupported') {
        Reflect.deleteProperty(window, 'Notification')
        Reflect.deleteProperty(window, 'PushManager')
      } else {
        let permission =
          pushScenario === 'permission-blocked'
            ? 'denied'
            : pushScenario === 'subscribed' || pushScenario === 'unsubscribed'
              ? 'granted'
              : 'default'
        class FakeNotification {
          static get permission() {
            return permission
          }
          static async requestPermission() {
            window.__stage4PermissionRequests += 1
            permission = pushPermissionResult
            return permission
          }
          constructor() {
            this.onclick = null
          }
          close() {}
        }
        Object.defineProperty(window, 'Notification', {
          configurable: true,
          value: FakeNotification,
        })
        Object.defineProperty(window, 'PushManager', {
          configurable: true,
          value: class FakePushManager {},
        })
        Object.defineProperty(navigator, 'serviceWorker', {
          configurable: true,
          value: {
            ready,
            register: async () => ready,
            controller: { postMessage: recordServiceWorkerMessage },
            getRegistration: async () => ({
              active: { postMessage: recordServiceWorkerMessage },
              pushManager,
            }),
          },
        })
      }
      if (pushScenario === 'insecure') {
        Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
      }

      class FakeWebSocket {
        static CONNECTING = 0
        static OPEN = 1
        static CLOSING = 2
        static CLOSED = 3
        constructor(url, protocols) {
          this.url = String(url)
          let socketEvidence
          try {
            const parsed = new URL(this.url, location.href)
            const sameHost = parsed.host === location.host
            const expectedProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
            const protocolList = Array.isArray(protocols)
              ? protocols.map(String)
              : protocols
                ? [String(protocols)]
                : []
            const queryKeys = [...new Set(parsed.searchParams.keys())].sort()
            const isViteHmr =
              sameHost &&
              parsed.protocol === expectedProtocol &&
              parsed.pathname === '/' &&
              protocolList.includes('vite-hmr')
            const isAppRealtime =
              sameHost &&
              parsed.protocol === expectedProtocol &&
              parsed.pathname === '/api/realtime/ws' &&
              queryKeys.length === 1 &&
              queryKeys[0] === 'token' &&
              parsed.searchParams.get('token') === localStorage.getItem('auth_token')
            socketEvidence = {
              kind: isAppRealtime ? 'app-realtime' : isViteHmr ? 'vite-hmr' : 'unexpected',
              valid: isAppRealtime || isViteHmr,
              sameHost,
              protocol: parsed.protocol,
              pathname: parsed.pathname,
              queryKeys,
              tokenMatchesCurrentAuth: isAppRealtime,
              protocols: protocolList,
            }
          } catch {
            socketEvidence = {
              kind: 'unexpected',
              valid: false,
              sameHost: false,
              protocol: null,
              pathname: null,
              queryKeys: [],
              tokenMatchesCurrentAuth: false,
              protocols: [],
            }
          }
          void window.__stage4RecordWebSocket(socketEvidence)
          this.readyState = FakeWebSocket.CONNECTING
          this.sent = []
          this.listeners = new Map()
          window.__stage4Sockets = window.__stage4Sockets || []
          window.__stage4Sockets.push(this)
          queueMicrotask(() => {
            if (this.readyState !== FakeWebSocket.CONNECTING) return
            this.readyState = FakeWebSocket.OPEN
            this.dispatch('open', new Event('open'))
          })
        }
        addEventListener(type, listener) {
          const listeners = this.listeners.get(type) || new Set()
          listeners.add(listener)
          this.listeners.set(type, listeners)
        }
        removeEventListener(type, listener) {
          this.listeners.get(type)?.delete(listener)
        }
        dispatch(type, event) {
          this[`on${type}`]?.(event)
          for (const listener of this.listeners.get(type) || []) listener.call(this, event)
        }
        send(value) {
          this.sent.push(value)
        }
        close() {
          this.readyState = FakeWebSocket.CLOSED
          this.dispatch('close', new CloseEvent('close'))
        }
        emit(type, data) {
          this.dispatch('message', new MessageEvent('message', {
            data: JSON.stringify({ type, data }),
          }))
        }
      }
      window.WebSocket = FakeWebSocket
      window.open = () => null

      if (storageFailure && typeof IDBObjectStore !== 'undefined') {
        if (storageFailure === 'size') {
          IDBObjectStore.prototype.openCursor = function stage4FailSizeScan() {
            throw new DOMException('opaque-stage4-size-failure', 'UnknownError')
          }
        }
        if (storageFailure === 'clear') {
          IDBObjectStore.prototype.clear = function stage4FailClear() {
            throw new DOMException('opaque-stage4-clear-failure', 'UnknownError')
          }
        }
      }
    },
    {
      authenticated: options.authenticated !== false,
      accessToken: token,
      userSummary: cachedUser,
      pushScenario: state.pushScenario,
      pushPermissionResult: state.pushPermissionResult,
      serviceWorkerReadyMode: state.serviceWorkerReadyMode,
      storageFailure: state.storageFailure,
    },
  )

  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin === new URL(baseUrl).origin) return route.continue()
    if (url.origin === 'https://telegram.org' && url.pathname.endsWith('.js')) {
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' })
    }
    if (url.origin === 'https://t.me') {
      return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html>' })
    }
    browserDiagnostics.blockedExternalRequests.push({ url: route.request().url(), suite: state.suite })
    return route.abort('blockedbyclient')
  })
  await context.route('**/api/**', (route) => {
    const requestUrl = new URL(route.request().url())
    if (requestUrl.origin !== new URL(baseUrl).origin) {
      browserDiagnostics.blockedExternalRequests.push({
        url: route.request().url(),
        suite: state.suite,
        reason: 'external-api-origin',
      })
      return route.abort('blockedbyclient')
    }
    return handleApiRoute(route, state)
  })

  page.on('console', (message) => {
    if (message.type() === 'error') {
      browserDiagnostics.consoleErrors.push({
        text: message.text(),
        location: message.location(),
        suite: state.suite,
      })
    }
  })
  page.on('pageerror', (error) => {
    browserDiagnostics.pageErrors.push({ text: error.message, suite: state.suite })
  })
  page.on('response', (response) => {
    if (response.status() === 204) successfulNoContentRequests.add(response.request())
    if (response.status() < 400) return
    const url = new URL(response.url())
    if (url.origin !== new URL(baseUrl).origin) return
    browserDiagnostics.httpFailures.push({
      url: response.url(),
      method: response.request().method(),
      status: response.status(),
      suite: state.suite,
    })
  })
  page.on('requestfailed', (request) => {
    const url = new URL(request.url())
    const failure = request.failure()?.errorText || ''
    if (url.origin !== new URL(baseUrl).origin) return
    if (state.verifyMode === 'abort' && url.pathname === '/api/sessions/verify') return
    if (failure === 'net::ERR_ABORTED' && successfulNoContentRequests.has(request)) return
    if (
      failure === 'net::ERR_ABORTED' &&
      (state.intentionalNavigation === true || state.intentionalClose === true)
    ) {
      return
    }
    browserDiagnostics.unexpectedRequestFailures.push({
      url: request.url(),
      failure,
      suite: state.suite,
    })
  })
  return { context, page, state }
}

async function closeRuntime(runtime) {
  if (Array.isArray(runtime.state.meSequence)) {
    for (const sequenceEntry of runtime.state.meSequence) sequenceEntry.gate?.resolve()
  }
  if (runtime.state.meMode === 'hold') {
    runtime.state.meMode = 'success'
    runtime.state.meGate.resolve()
  }
  if (runtime.state.notificationMode === 'hold') {
    runtime.state.notificationMode = 'success'
    runtime.state.notificationGate.resolve()
  }
  if (runtime.state.pushScenario === 'checking') {
    runtime.state.pushScenario = 'permission-default'
    runtime.state.pushGate.resolve()
  }
  runtime.state.intentionalClose = true
  await runtime.context.close()
}

async function waitForSettledPage(page) {
  await page.waitForLoadState('domcontentloaded')
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready
  })
  await page.waitForTimeout(180)
}

async function waitForRequestCount(page, state, pathname, expected, method = null) {
  const deadline = Date.now() + 15_000
  while (requestCount(state, pathname, method) < expected) {
    assert.ok(Date.now() < deadline, `${method || '*'} ${pathname}: request count < ${expected}`)
    await page.waitForTimeout(20)
  }
}

async function waitForRequestCountToSettle(
  page,
  state,
  pathname,
  method,
  { minimum = 1, quietMs = 300, timeoutMs = 15_000 } = {},
) {
  await waitForRequestCount(page, state, pathname, minimum, method)
  const deadline = Date.now() + timeoutMs
  let settledCount = requestCount(state, pathname, method)
  let quietSince = Date.now()
  while (Date.now() - quietSince < quietMs) {
    assert.ok(Date.now() < deadline, `${method || '*'} ${pathname}: request count did not settle`)
    await page.waitForTimeout(25)
    const currentCount = requestCount(state, pathname, method)
    if (currentCount !== settledCount) {
      settledCount = currentCount
      quietSince = Date.now()
    }
  }
  return settledCount
}

async function gotoPath(page, routePath, ready) {
  const state = pageStates.get(page)
  if (state) state.intentionalNavigation = true
  try {
    await page.goto(routePath, { waitUntil: 'domcontentloaded' })
    if (typeof ready === 'string') {
      await page.getByText(ready, { exact: false }).first().waitFor({ timeout: 30_000 })
    } else if (ready?.selector) {
      await page.locator(ready.selector).first().waitFor({ timeout: 30_000 })
    } else if (ready?.role === 'heading') {
      await page.getByRole('heading', { name: ready.name, exact: ready.exact ?? true }).waitFor({
        timeout: 30_000,
      })
    }
    await waitForSettledPage(page)
  } finally {
    if (state) state.intentionalNavigation = false
  }
}

async function emitSocket(page, type, data) {
  await page.waitForFunction(
    () =>
      Array.isArray(window.__stage4Sockets) &&
      window.__stage4Sockets.some((socket) => {
        try {
          return new URL(socket.url, location.href).pathname === '/api/realtime/ws'
        } catch {
          return false
        }
      }),
  )
  await page.evaluate(
    ({ eventType, payload }) => {
      const socket = window.__stage4Sockets.find((candidate) => {
        try {
          return new URL(candidate.url, location.href).pathname === '/api/realtime/ws'
        } catch {
          return false
        }
      })
      if (!socket) throw new Error('Stage4 realtime WebSocket not found')
      socket.emit(eventType, payload)
    },
    { eventType: type, payload: data },
  )
  await page.waitForTimeout(0)
}

async function dispatchInstallPrompt(page, outcome = 'dismissed') {
  await page.evaluate((nextOutcome) => {
    const event = new Event('beforeinstallprompt', { cancelable: true })
    Object.defineProperties(event, {
      prompt: { value: async () => undefined },
      userChoice: { value: Promise.resolve({ outcome: nextOutcome, platform: 'web' }) },
    })
    window.dispatchEvent(event)
  }, outcome)
}

async function dispatchInstallPromptWithDismissAge(page, dismissAgeMs, outcome = 'dismissed') {
  await page.evaluate(
    ({ ageMs, nextOutcome }) => {
      localStorage.setItem(
        'pwa_install_prompt_dismissed_at_v2',
        String(Date.now() - ageMs),
      )
      const event = new Event('beforeinstallprompt', { cancelable: true })
      Object.defineProperties(event, {
        prompt: { value: async () => undefined },
        userChoice: { value: Promise.resolve({ outcome: nextOutcome, platform: 'web' }) },
      })
      window.dispatchEvent(event)
    },
    { ageMs: dismissAgeMs, nextOutcome: outcome },
  )
}

async function assertPwaPromptIneligible(page, label, clock = null) {
  if (clock) await clock.fastForward(300)
  else await page.waitForTimeout(300)
  const prompt = page.locator('.ui-v2-pwa-install')
  await prompt.waitFor({ state: 'hidden', timeout: 2_000 })
  assert.equal(await prompt.count(), 0, `${label}: PWA prompt did not detach after bounded motion`)
}

async function takeScreenshot(page, fileName) {
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready
  })
  const scrollEvidence = await page.evaluate(() => {
    const scroller = document.querySelector('.app-route-scroll')
    if (!(scroller instanceof HTMLElement)) {
      return { selector: null, initial: 0, positions: [0] }
    }
    const maximum = Math.max(0, scroller.scrollHeight - scroller.clientHeight)
    const step = Math.max(1, scroller.clientHeight - 64)
    const positions = []
    for (let position = 0; position < maximum; position += step) positions.push(position)
    positions.push(maximum)
    return {
      selector: '.app-route-scroll',
      initial: scroller.scrollTop,
      positions: [...new Set(positions.map((position) => Math.round(position)))],
    }
  })
  const extension = path.extname(fileName)
  const stem = fileName.slice(0, -extension.length)
  for (const [index, position] of scrollEvidence.positions.entries()) {
    if (scrollEvidence.selector) {
      await page.locator(scrollEvidence.selector).evaluate((element, scrollTop) => {
        element.scrollTop = scrollTop
      }, position)
      await page.waitForTimeout(30)
    }
    const artifactName = index === 0
      ? fileName
      : `${stem}-scroll-${String(index).padStart(3, '0')}${extension}`
    const filePath = path.join(OUTPUT_DIR, artifactName)
    await page.screenshot({ path: filePath, fullPage: false, animations: 'disabled' })
    const stat = fs.statSync(filePath)
    screenshots.push({
      file: artifactName,
      bytes: stat.size,
      sha256: sha256File(filePath),
      scrollContainer: scrollEvidence.selector,
      scrollTop: position,
    })
  }
  if (scrollEvidence.selector) {
    await page.locator(scrollEvidence.selector).evaluate((element, scrollTop) => {
      element.scrollTop = scrollTop
    }, scrollEvidence.initial)
  }
}

async function visibleText(page) {
  return page.locator('body').innerText()
}

async function navLabels(page) {
  return page.locator('.ui-v2-bottom-nav-label').allTextContents().then((rows) =>
    rows.map((row) => row.trim()).filter(Boolean),
  )
}

async function measureLayout(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number(style.opacity || '1') > 0 &&
        rect.width > 0 &&
        rect.height > 0
      )
    }
    const rectOf = (element) => {
      const rect = element.getBoundingClientRect()
      const intersectionLeft = Math.max(0, rect.left)
      const intersectionRight = Math.min(innerWidth, rect.right)
      const intersectionTop = Math.max(0, rect.top)
      const intersectionBottom = Math.min(innerHeight, rect.bottom)
      const inViewport =
        intersectionRight > intersectionLeft && intersectionBottom > intersectionTop
      const hit = inViewport
        ? document.elementFromPoint(
            (intersectionLeft + intersectionRight) / 2,
            (intersectionTop + intersectionBottom) / 2,
          )
        : null
      return {
        selector: element.className?.toString?.() || element.tagName,
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
        inViewport,
        centerUnoccluded: !inViewport || Boolean(hit && (element === hit || element.contains(hit))),
      }
    }
    const documentElement = document.documentElement
    const app = document.querySelector('#app')
    const routeScroller = document.querySelector('.app-route-scroll')
    const interactive = [...document.querySelectorAll(
      '[data-ui-system="v2"] :is(button:not([disabled]), a[href], [role="button"]:not([aria-disabled="true"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]))',
    )]
      .filter((element) => element instanceof HTMLElement && visible(element))
      .map((element, domIndex) => ({ ...rectOf(element), domIndex }))
    const fixed = [...document.querySelectorAll('body *')]
      .filter((element) => {
        if (!(element instanceof HTMLElement) || !visible(element)) return false
        const style = getComputedStyle(element)
        return style.position === 'fixed' || style.position === 'sticky'
      })
      .map(rectOf)
    const critical = [...document.querySelectorAll(
      '.dashboard-content, .main-section, .ui-v2-daily-page, .ui-v2-daily-page__content, .ui-page, .ui-workspace, .ui-section-card, .ui-v2-pwa-section, .bottom-nav-bar',
    )]
      .filter((element) => element instanceof HTMLElement && visible(element))
      .map((element) => ({
        ...rectOf(element),
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }))
    return {
      viewport: { width: innerWidth, height: innerHeight },
      documentScrollWidth: Math.max(
        documentElement.scrollWidth,
        document.body.scrollWidth,
        app?.scrollWidth || 0,
      ),
      routeScroller: routeScroller
        ? { clientWidth: routeScroller.clientWidth, scrollWidth: routeScroller.scrollWidth }
        : null,
      interactive,
      fixed,
      critical,
    }
  })
}

async function resolveOccludedInteractiveTargets(page, metrics, label) {
  const selector =
    '[data-ui-system="v2"] :is(button:not([disabled]), a[href], [role="button"]:not([aria-disabled="true"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]))'
  const describe = (element) => {
    const rect = element.getBoundingClientRect()
    const center = {
      x: Math.max(0, Math.min(innerWidth - 1, (rect.left + rect.right) / 2)),
      y: Math.max(0, Math.min(innerHeight - 1, (rect.top + rect.bottom) / 2)),
    }
    const stack = document.elementsFromPoint(center.x, center.y).slice(0, 8).map((node) => {
      const style = getComputedStyle(node)
      return {
        tag: node.tagName,
        id: node.id,
        className: node.className?.toString?.() || '',
        position: style.position,
        zIndex: style.zIndex,
        pointerEvents: style.pointerEvents,
      }
    })
    const hit = document.elementFromPoint(center.x, center.y)
    const scroller = document.querySelector('.app-route-scroll')
    const scrollerRect = scroller?.getBoundingClientRect()
    return {
      rect: {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      },
      viewport: { width: innerWidth, height: innerHeight },
      windowScroll: { x: scrollX, y: scrollY },
      routeScroller: scroller
        ? {
            scrollTop: scroller.scrollTop,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
            rect: scrollerRect
              ? {
                  top: scrollerRect.top,
                  bottom: scrollerRect.bottom,
                  height: scrollerRect.height,
                }
              : null,
          }
        : null,
      stack,
      centerUnoccluded: Boolean(hit && (element === hit || element.contains(hit))),
    }
  }
  const resolved = []
  for (const item of metrics.interactive.filter((entry) => entry.inViewport && !entry.centerUnoccluded)) {
    const target = page.locator(selector).nth(item.domIndex)
    const before = await target.evaluate(describe)
    await target.scrollIntoViewIfNeeded()
    await waitForSettledPage(page)
    const after = await target.evaluate(describe)
    assert.equal(
      after.centerUnoccluded,
      true,
      `${label}: target remains occluded after scrollIntoViewIfNeeded ${item.selector} ${JSON.stringify({ before, after })}`,
    )
    item.centerUnoccluded = true
    item.resolvedAfterScroll = after
    resolved.push({ selector: item.selector, before, after })
  }
  return resolved
}

function assertLayout(metrics, label) {
  assert.ok(
    metrics.documentScrollWidth <= metrics.viewport.width + 1,
    `${label}: horizontal overflow ${metrics.documentScrollWidth}/${metrics.viewport.width}`,
  )
  if (metrics.routeScroller) {
    assert.ok(
      metrics.routeScroller.scrollWidth <= metrics.routeScroller.clientWidth + 1,
      `${label}: route scroller horizontal overflow ${metrics.routeScroller.scrollWidth}/${metrics.routeScroller.clientWidth}`,
    )
  }
  for (const item of metrics.fixed) {
    assert.ok(item.left >= -1, `${label}: fixed left clipping ${item.selector}`)
    assert.ok(
      item.right <= metrics.viewport.width + 1,
      `${label}: fixed right clipping ${item.selector}`,
    )
  }
  for (const item of metrics.critical) {
    assert.ok(item.left >= -1, `${label}: critical left clipping ${item.selector}`)
    assert.ok(
      item.right <= metrics.viewport.width + 1,
      `${label}: critical right clipping ${item.selector}`,
    )
    assert.ok(
      item.scrollWidth <= item.clientWidth + 1,
      `${label}: critical internal overflow ${item.selector}`,
    )
  }
  assert.ok(metrics.interactive.length > 0, `${label}: no V2 interactive target measured`)
  for (const item of metrics.interactive) {
    assert.ok(
      item.width >= 43.5 && item.height >= 43.5,
      `${label}: target below 44x44 (${item.width}x${item.height}) ${item.selector}`,
    )
    assert.equal(
      item.centerUnoccluded,
      true,
      `${label}: viewport target is occluded ${item.selector}`,
    )
  }
}

async function focusProof(page, selector, label, options = {}) {
  const target = page.locator(selector).first()
  await target.waitFor({ state: 'visible' })
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  })
  let reachedByTab = false
  for (let index = 0; index < 100; index += 1) {
    await page.keyboard.press('Tab')
    reachedByTab = await target.evaluate((element) => document.activeElement === element)
    if (reachedByTab) break
  }
  assert.equal(reachedByTab, true, `${label}: target was not reachable by Tab`)
  await waitForSettledPage(page)
  const result = await target.evaluate((element) => {
    const style = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    const colorProbe = document.createElement('span')
    colorProbe.style.color = 'var(--ui-v2-color-border-focus)'
    colorProbe.style.display = 'none'
    element.appendChild(colorProbe)
    const canonicalFocusColor = getComputedStyle(colorProbe).color
    colorProbe.remove()
    const matchedFocusRules = []
    const visitRules = (rules, source) => {
      for (const rule of rules) {
        if (rule instanceof CSSStyleRule) {
          if (!/(?:outline|box-shadow|border(?:-color)?)/u.test(rule.style.cssText)) continue
          try {
            if (element.matches(rule.selectorText)) {
              matchedFocusRules.push({ source, selector: rule.selectorText, cssText: rule.style.cssText })
            }
          } catch {
            // Ignore selectors unsupported by Element.matches in this browser.
          }
          continue
        }
        if (rule instanceof CSSMediaRule && !matchMedia(rule.conditionText).matches) continue
        if ('cssRules' in rule) visitRules(rule.cssRules, source)
      }
    }
    for (const sheet of document.styleSheets) {
      try {
        visitRules(sheet.cssRules, sheet.href || sheet.ownerNode?.getAttribute?.('data-vite-dev-id') || 'inline')
      } catch {
        // All Stage 4 styles are local; ignore browser-owned inaccessible sheets.
      }
    }
    const scopeChain = []
    for (let node = element; node instanceof HTMLElement; node = node.parentElement) {
      if (node.hasAttribute('data-ui-system') || node === element) {
        scopeChain.push({
          tag: node.tagName,
          className: node.className?.toString?.() || '',
          role: node.getAttribute('role'),
          dataUiSystem: node.getAttribute('data-ui-system'),
        })
      }
    }
    return {
      element: {
        tag: element.tagName,
        className: element.className?.toString?.() || '',
        role: element.getAttribute('role'),
        ariaLabel: element.getAttribute('aria-label'),
      },
      active: document.activeElement === element,
      focusVisible: element.matches(':focus-visible'),
      outline: style.outline,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      outlineStyle: style.outlineStyle,
      outlineColor: style.outlineColor,
      outlineOffsetRaw: style.outlineOffset,
      outlineOffset: Number.parseFloat(style.outlineOffset),
      boxShadow: style.boxShadow,
      borderColor: style.borderColor,
      canonicalTokens: {
        stroke: style.getPropertyValue('--ui-v2-stroke-focus').trim(),
        offset: style.getPropertyValue('--ui-v2-spacing-2').trim(),
        color: style.getPropertyValue('--ui-v2-color-border-focus').trim(),
        resolvedColor: canonicalFocusColor,
      },
      inV2Scope: Boolean(element.closest('[data-ui-system="v2"], [data-ui-system="v2-portal"]')),
      scopeChain,
      matchedFocusRules,
      rect: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
      viewport: { width: innerWidth, height: innerHeight },
      centerUnoccluded: (() => {
        const x = Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2))
        const y = Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2))
        const hit = document.elementFromPoint(x, y)
        return Boolean(hit && (element === hit || element.contains(hit)))
      })(),
    }
  })
  assert.equal(result.active, true, `${label}: target is not active`)
  assert.equal(result.focusVisible, true, `${label}: keyboard target is not :focus-visible`)
  assert.equal(result.centerUnoccluded, true, `${label}: focused target is occluded`)
  assert.ok(Math.abs(result.outlineWidth - 3) <= 0.05, `${label}: focus width`)
  assert.equal(result.outlineStyle, 'solid', `${label}: focus style`)
  if (options.enforceOffset !== false) {
    assert.ok(result.outlineOffset >= 1.9, `${label}: focus offset ${JSON.stringify(result)}`)
  }
  if (result.inV2Scope && options.enforceCanonicalV2 !== false) {
    assert.ok(
      Math.abs(result.outlineWidth - Number.parseFloat(result.canonicalTokens.stroke)) <= 0.05,
      `${label}: canonical V2 focus stroke`,
    )
    assert.ok(
      Math.abs(result.outlineOffset - Number.parseFloat(result.canonicalTokens.offset)) <= 0.05,
      `${label}: canonical V2 focus offset ${JSON.stringify(result)}`,
    )
    assert.equal(
      result.outlineColor,
      result.canonicalTokens.resolvedColor,
      `${label}: canonical V2 focus color`,
    )
    assert.equal(result.boxShadow, 'none', `${label}: canonical V2 focus shadow`)
    assert.equal(
      result.borderColor,
      result.canonicalTokens.resolvedColor,
      `${label}: canonical V2 focus border`,
    )
  }
  assert.ok(result.rect.left >= -1 && result.rect.right <= result.viewport.width + 1)
  assert.ok(result.rect.top >= -1 && result.rect.bottom <= result.viewport.height + 1)
  return result
}

async function waitForActiveSelector(page, selector, label) {
  await page.waitForFunction(
    (requestedSelector) => document.activeElement?.matches(requestedSelector) === true,
    selector,
    { timeout: 5_000 },
  )
  assert.equal(
    await page.evaluate((requestedSelector) => document.activeElement?.matches(requestedSelector), selector),
    true,
    `${label}: focus did not reach ${selector}`,
  )
}

function parseRgb(value) {
  const numbers = value.match(/[\d.]+/gu)?.map(Number)
  const channels = value.trim().startsWith('color(srgb')
    ? numbers?.slice(0, 3).map((channel) => channel * 255)
    : numbers?.slice(0, 3)
  assert.equal(channels?.length, 3, `could not parse color ${value}`)
  return channels
}

function parseRgba(value) {
  const numbers = value.match(/[\d.]+/gu)?.map(Number)
  const channels = value.trim().startsWith('color(srgb')
    ? [
        (numbers?.[0] ?? 0) * 255,
        (numbers?.[1] ?? 0) * 255,
        (numbers?.[2] ?? 0) * 255,
        numbers?.[3] ?? 1,
      ]
    : numbers
  assert.ok(channels && channels.length >= 3, `could not parse color ${value}`)
  return [channels[0], channels[1], channels[2], channels[3] ?? 1]
}

function compositeRgba(foreground, background) {
  const alpha = foreground[3]
  return [
    foreground[0] * alpha + background[0] * (1 - alpha),
    foreground[1] * alpha + background[1] * (1 - alpha),
    foreground[2] * alpha + background[2] * (1 - alpha),
  ]
}

function contrastRatio(foreground, background) {
  const luminance = (rgb) => {
    const channels = rgb
      .map((value) => value / 255)
      .map((value) =>
        value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
      )
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
  }
  const first = luminance(foreground)
  const second = luminance(background)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

async function contrastProof(page, selectors, label) {
  const samples = await page.evaluate((requestedSelectors) => {
    const parseColor = (value) => {
      const numbers = value.match(/[\d.]+/gu)?.map(Number) || []
      if (numbers.length < 3) return null
      if (value.trim().startsWith('color(srgb')) {
        return [numbers[0] * 255, numbers[1] * 255, numbers[2] * 255, numbers[3] ?? 1]
      }
      return [numbers[0], numbers[1], numbers[2], numbers[3] ?? 1]
    }
    const composite = (foreground, background) => {
      const alpha = foreground[3] + background[3] * (1 - foreground[3])
      if (alpha <= 0) return [0, 0, 0, 0]
      return [
        (foreground[0] * foreground[3] + background[0] * background[3] * (1 - foreground[3])) / alpha,
        (foreground[1] * foreground[3] + background[1] * background[3] * (1 - foreground[3])) / alpha,
        (foreground[2] * foreground[3] + background[2] * background[3] * (1 - foreground[3])) / alpha,
        alpha,
      ]
    }
    const paintedColors = (element) => {
      const layers = []
      let candidate = element
      while (candidate) {
        const style = getComputedStyle(candidate)
        if (style.backgroundImage !== 'none') {
          return { unsupported: `non-solid background on ${candidate.className || candidate.tagName}` }
        }
        const color = parseColor(style.backgroundColor)
        if (!color) return { unsupported: `unparseable background ${style.backgroundColor}` }
        layers.push(color)
        if (color[3] >= 0.999) break
        candidate = candidate.parentElement
      }
      let background = [255, 255, 255, 1]
      for (const layer of layers.reverse()) background = composite(layer, background)
      const foreground = parseColor(getComputedStyle(element).color)
      if (!foreground) return { unsupported: 'unparseable foreground' }
      return {
        foreground: composite(foreground, background).slice(0, 3),
        background: background.slice(0, 3),
      }
    }
    return requestedSelectors.map((selector) => {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) return { selector, missing: true }
      const style = getComputedStyle(element)
      const painted = paintedColors(element)
      return {
        selector,
        missing: false,
        ...painted,
        foregroundCss: style.color,
        opacity: Number.parseFloat(style.opacity || '1'),
        fontSize: Number.parseFloat(style.fontSize),
        fontWeight: Number.parseFloat(style.fontWeight) || 400,
      }
    })
  }, selectors)
  for (const sample of samples) {
    assert.equal(sample.missing, false, `${label}: missing contrast selector ${sample.selector}`)
    if (sample.unsupported?.startsWith('non-solid background')) {
      const target = page.locator(sample.selector).first()
      await target.scrollIntoViewIfNeeded()
      const box = await target.boundingBox()
      assert.ok(box && box.width > 0 && box.height > 0, `${label}: no pixel box ${sample.selector}`)
      const priorVisibility = await target.evaluate((element) => ({
        value: element.style.getPropertyValue('visibility'),
        priority: element.style.getPropertyPriority('visibility'),
      }))
      let pixelBuffer
      try {
        await target.evaluate((element) => {
          element.style.setProperty('visibility', 'hidden', 'important')
        })
        pixelBuffer = await page.screenshot({
          animations: 'disabled',
          clip: {
            x: Math.max(0, Math.floor(box.x + box.width / 2)),
            y: Math.max(0, Math.floor(box.y + box.height / 2)),
            width: 1,
            height: 1,
          },
        })
      } finally {
        await target.evaluate((element, prior) => {
          if (prior.value) {
            element.style.setProperty('visibility', prior.value, prior.priority)
          } else {
            element.style.removeProperty('visibility')
          }
        }, priorVisibility)
      }
      const pixel = await sharp(pixelBuffer).ensureAlpha().raw().toBuffer()
      sample.background = [pixel[0], pixel[1], pixel[2]]
      const foreground = parseRgba(sample.foregroundCss)
      foreground[3] *= sample.opacity
      sample.foreground = compositeRgba(foreground, sample.background)
      sample.pixelComposited = true
      delete sample.unsupported
    }
    assert.equal(sample.unsupported, undefined, `${label}: ${sample.unsupported}`)
    const ratio = contrastRatio(sample.foreground, sample.background)
    const large = sample.fontSize >= 24 || (sample.fontSize >= 18.66 && sample.fontWeight >= 700)
    assert.ok(ratio >= (large ? 3 : 4.5), `${label}: contrast ${ratio} ${sample.selector}`)
    sample.ratio = ratio
  }
  return samples
}

async function keyboardWalk(page, label, steps = 6, options = {}) {
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  })
  const rows = []
  for (let index = 0; index < steps; index += 1) {
    await page.keyboard.press('Tab')
    await waitForSettledPage(page)
    const row = await page.evaluate(() => {
      const active = document.activeElement
      if (!(active instanceof HTMLElement)) return null
      const rect = active.getBoundingClientRect()
      const inertOwner = active.closest('[inert]')
      return {
        tag: active.tagName,
        id: active.id,
        className: active.className?.toString?.() || '',
        role: active.getAttribute('role'),
        text: active.getAttribute('aria-label') || active.textContent?.trim().slice(0, 80) || '',
        tabIndex: active.tabIndex,
        disabled: 'disabled' in active ? Boolean(active.disabled) : false,
        ariaDisabled: active.getAttribute('aria-disabled'),
        inert: Boolean(inertOwner),
        inertOwner: inertOwner?.className?.toString?.() || inertOwner?.tagName || null,
        interactiveTarget: active.matches(
          'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [role="button"]:not([aria-disabled="true"]), [tabindex]:not([tabindex="-1"])',
        ),
        route: location.pathname + location.search + location.hash,
        rect: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
        visible:
          rect.width > 0 &&
          rect.height > 0 &&
          getComputedStyle(active).visibility !== 'hidden' &&
          getComputedStyle(active).display !== 'none' &&
          Number(getComputedStyle(active).opacity || '1') > 0,
        focusVisible: active.matches(':focus-visible'),
        centerUnoccluded: (() => {
          const x = Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2))
          const y = Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2))
          const hit = document.elementFromPoint(x, y)
          return Boolean(hit && (active === hit || active.contains(hit)))
        })(),
      }
    })
    if (options.enforce !== false) {
      assert.ok(row?.visible, `${label}: Tab ${index + 1} is not visible ${JSON.stringify(row)}`)
      if (row.interactiveTarget && !row.disabled && !row.inert) {
        assert.equal(
          row.focusVisible,
          true,
          `${label}: Tab ${index + 1} interactive target lacks :focus-visible ${JSON.stringify(row)}`,
        )
        assert.equal(
          row.centerUnoccluded,
          true,
          `${label}: Tab ${index + 1} interactive target is occluded ${JSON.stringify(row)}`,
        )
      }
      assert.ok(
        row.rect.left >= -1 && row.rect.right <= (await page.evaluate(() => innerWidth)) + 1,
        `${label}: Tab ${index + 1} clips horizontally ${JSON.stringify(row)}`,
      )
    }
    rows.push(row)
  }
  assert.ok(new Set(rows.map((row) => `${row.tag}:${row.text}`)).size >= 2, `${label}: Tab loop stuck`)
  return rows
}

async function protectedEvidence({ recordAssertion = true } = {}) {
  const stage4 = await import(
    pathToFileURL(path.join(FRONTEND, 'scripts/lib/stage4-protected-surface-guard.mjs')).href
  )
  const stage3 = await import(
    pathToFileURL(path.join(FRONTEND, 'scripts/lib/stage3-protected-region-guard.mjs')).href
  )
  const ownedPaths = stage4.discoverStage4OwnedRuntimePaths(WORKTREE)
  const market = stage4.assertProtectedFileSetEvidence(
    'Market runtime',
    stage4.protectedFileSetEvidence(
      stage4.readFileEntries(WORKTREE, ownedPaths.market),
      stage4.MARKET_RUNTIME_CONTRACT,
    ),
    stage4.MARKET_RUNTIME_BASELINE,
  )
  const messenger = stage4.assertProtectedFileSetEvidence(
    'Messenger runtime',
    stage4.protectedFileSetEvidence(
      stage4.readFileEntries(WORKTREE, ownedPaths.messenger),
      stage4.MESSENGER_RUNTIME_CONTRACT,
    ),
    stage4.MESSENGER_RUNTIME_BASELINE,
  )
  const dashboard = stage3.dashboardMarketRegionEvidence(
    fs.readFileSync(path.join(WORKTREE, stage3.DASHBOARD_MARKET_REGION_PATH), 'utf8'),
  )
  assert.equal(dashboard.sha256, stage3.DASHBOARD_MARKET_REGION_SHA256)
  assert.equal(dashboard.sha256, sourcePlan.protectedBaselines.homeMarketRegionSha256)
  assert.deepEqual(
    {
      count: market.count,
      contentBytes: market.contentBytes,
      pathSetSha256: market.pathSetSha256,
      sha256: market.sha256,
    },
    sourcePlan.protectedBaselines.marketRuntime,
  )
  assert.deepEqual(
    {
      count: messenger.count,
      contentBytes: messenger.contentBytes,
      pathSetSha256: messenger.pathSetSha256,
      sha256: messenger.sha256,
    },
    sourcePlan.protectedBaselines.messengerRuntime,
  )
  const manifest = JSON.parse(
    fs.readFileSync(path.join(WORKTREE, stage4.STAGE4_SCOPE_MANIFEST_PATH), 'utf8'),
  )
  const manifestRoutes = stage4.assertStage4RouteProtection(manifest.routes)
  const runtimeRoutes = stage4.assertStage4RuntimeRouteProtection(
    fs.readFileSync(path.join(WORKTREE, stage4.STAGE4_ROUTE_CONTRACT_PATH), 'utf8'),
  )
  if (recordAssertion) {
    record('protected-market-messenger-home-source-baselines-exact', {
      dashboard,
      market,
      messenger,
      manifestRoutes,
      runtimeRoutes,
    })
  }
  return { dashboard, market, messenger, manifestRoutes, runtimeRoutes }
}

async function warmVite(browser, baseUrl) {
  const runtime = await createPage(browser, baseUrl, {
    authenticated: false,
    state: newRuntimeState(),
    suite: 'warm-vite',
  })
  try {
    await gotoPath(runtime.page, '/login', 'ورود به سامانه')
  } finally {
    await closeRuntime(runtime)
  }
}

const DAILY_ROUTES = Object.freeze([
  { key: 'home', path: '/', ready: { role: 'heading', name: 'خانه' }, routeScope: false },
  {
    key: 'operations',
    path: '/operations',
    ready: { role: 'heading', name: 'عملیات' },
    routeScope: true,
  },
  {
    key: 'account',
    path: '/account',
    ready: { selector: '.ui-v2-account-header strong' },
    routeScope: true,
  },
  {
    key: 'security',
    path: '/account/security',
    ready: { role: 'heading', name: 'امنیت حساب' },
    routeScope: true,
  },
  {
    key: 'storage',
    path: '/account/storage',
    ready: { role: 'heading', name: 'حافظه و داده‌ها' },
    routeScope: true,
  },
  {
    key: 'notifications',
    path: '/account/notifications',
    ready: { role: 'heading', name: 'اعلان‌ها' },
    routeScope: true,
  },
])

async function assertDailyRouteScope(page, spec, label) {
  if (spec.routeScope) {
    assert.equal(
      await page.locator('.app-route-v2-scope[data-ui-system="v2"]').count(),
      1,
      `${label}: route V2 scope missing`,
    )
    return
  }
  assert.equal(await page.locator('.app-route-v2-scope').count(), 0, `${label}: Home route-scoped`)
  assert.equal(
    await page.locator('.ui-v2-home-top[data-ui-system="v2"]').count(),
    1,
    `${label}: Home top section scope`,
  )
  assert.equal(
    await page.locator('.ui-v2-pwa-section[data-ui-system="v2"]').count(),
    1,
    `${label}: Home PWA section scope`,
  )
  assert.equal(
    await page.locator('[data-ui-system="v2"] .hero-btn').count(),
    0,
    `${label}: protected Market hero entered V2 scope`,
  )
}

async function runResponsiveDailyMatrix(browser, baseUrl) {
  const runtime = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.owner } }),
    suite: 'responsive-daily-matrix',
  })
  const rows = []
  try {
    if (NOTIFICATIONS_CONTRAST_DIAGNOSTIC) {
      await runtime.page.setViewportSize({ width: 390, height: 844 })
      await gotoPath(runtime.page, '/account/notifications', {
        role: 'heading',
        name: 'اعلان‌ها',
      })
      await runtime.page.locator('.ui-v2-notifications-item .notif-line-value').first().waitFor()
      const audit = await runtime.page.evaluate(() => {
        const scope = document.querySelector('[data-ui-system="v2"]')
        if (!(scope instanceof HTMLElement)) throw new Error('Notifications V2 scope missing')
        const resolveToken = (token, property) => {
          const probe = document.createElement('span')
          probe.style.setProperty(property, `var(${token})`)
          probe.style.display = 'none'
          scope.appendChild(probe)
          const value = getComputedStyle(probe).getPropertyValue(property)
          probe.remove()
          return value
        }
        const tokens = {
          secondary: resolveToken('--ui-v2-color-text-secondary', 'color'),
          card: resolveToken('--ui-v2-color-surface-card', 'background-color'),
          brandSoft: resolveToken('--ui-v2-color-surface-brand-soft', 'background-color'),
          subtle: resolveToken('--ui-v2-color-surface-subtle', 'background-color'),
        }
        const rows = [...document.querySelectorAll(
          '.ui-v2-notifications-item .notif-line-value, .ui-v2-notifications-item .notif-line-text',
        )].map((element) => {
          const style = getComputedStyle(element)
          const card = element.closest('.ui-v2-notifications-item')
          const field = element.closest('.notif-line')
          let effectiveOpacity = 1
          const opacityChain = []
          for (let node = element; node instanceof HTMLElement; node = node.parentElement) {
            const opacity = Number.parseFloat(getComputedStyle(node).opacity || '1')
            if (opacity !== 1) opacityChain.push({ className: node.className.toString(), opacity })
            effectiveOpacity *= opacity
            if (node === card) break
          }
          return {
            selectorClass: element.className.toString(),
            text: element.textContent?.trim() || '',
            color: style.color,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            ownOpacity: style.opacity,
            effectiveOpacity,
            opacityChain,
            fieldClass: field?.className?.toString?.() || null,
            fieldBackground: field ? getComputedStyle(field).backgroundColor : null,
            cardClass: card?.className?.toString?.() || null,
            cardBackground: card ? getComputedStyle(card).backgroundColor : null,
            unread: Boolean(card?.classList.contains('ui-v2-notifications-item--unread')),
          }
        })
        return { tokens, rows }
      })
      const canonicalForeground = parseRgb(audit.tokens.secondary)
      const canonicalRatios = Object.fromEntries(
        ['card', 'brandSoft', 'subtle'].map((surface) => [
          surface,
          contrastRatio(canonicalForeground, parseRgb(audit.tokens[surface])),
        ]),
      )
      for (const row of audit.rows) {
        const background = parseRgba(row.fieldBackground || row.cardBackground)
        const foreground = parseRgba(row.color)
        foreground[3] *= row.effectiveOpacity
        row.ratio = contrastRatio(
          compositeRgba(foreground, background.slice(0, 3)),
          background.slice(0, 3),
        )
      }
      const firstValue = runtime.page.locator('.ui-v2-notifications-item .notif-line-value').first()
      await firstValue.scrollIntoViewIfNeeded()
      const firstValueBox = await firstValue.boundingBox()
      assert.ok(firstValueBox, 'Notifications contrast pixel target has no box')
      const priorVisibility = await firstValue.evaluate((element) => ({
        value: element.style.getPropertyValue('visibility'),
        priority: element.style.getPropertyPriority('visibility'),
      }))
      let backgroundPixelBuffer
      try {
        await firstValue.evaluate((element) => {
          element.style.setProperty('visibility', 'hidden', 'important')
        })
        backgroundPixelBuffer = await runtime.page.screenshot({
          animations: 'disabled',
          clip: {
            x: Math.max(0, Math.floor(firstValueBox.x + firstValueBox.width / 2)),
            y: Math.max(0, Math.floor(firstValueBox.y + firstValueBox.height / 2)),
            width: 1,
            height: 1,
          },
        })
      } finally {
        await firstValue.evaluate((element, prior) => {
          if (prior.value) element.style.setProperty('visibility', prior.value, prior.priority)
          else element.style.removeProperty('visibility')
        }, priorVisibility)
      }
      const backgroundPixelRaw = await sharp(backgroundPixelBuffer).removeAlpha().raw().toBuffer()
      const backgroundPixel = [backgroundPixelRaw[0], backgroundPixelRaw[1], backgroundPixelRaw[2]]
      const firstForeground = parseRgba(audit.rows[0].color)
      firstForeground[3] *= audit.rows[0].effectiveOpacity
      const pixelRatio = contrastRatio(
        compositeRgba(firstForeground, backgroundPixel),
        backgroundPixel,
      )
      assert.ok(pixelRatio >= 4.5, `Notifications pixel contrast ${pixelRatio.toFixed(2)}:1`)
      for (const [surface, ratio] of Object.entries(canonicalRatios)) {
        assert.ok(ratio >= 4.5, `Canonical secondary contrast on ${surface}: ${ratio.toFixed(2)}:1`)
      }
      await waitForRequestCount(
        runtime.page,
        runtime.state,
        '/api/notifications/4401/read',
        1,
        'PATCH',
      )
      await waitForRequestCount(
        runtime.page,
        runtime.state,
        '/api/notifications/4402/read',
        1,
        'PATCH',
      )
      await runtime.page.waitForTimeout(300)
      const pixelProof = { backgroundPixel, pixelRatio }
      record('notifications-text-contrast-focused-diagnostic', {
        audit,
        canonicalRatios,
        pixelProof,
      })
      return { audit, canonicalRatios, pixelProof, focusedDiagnostic: true }
    }
    if (HOME_KEYBOARD_DIAGNOSTIC) {
      await runtime.page.setViewportSize({ width: 390, height: 844 })
      await gotoPath(runtime.page, '/', { role: 'heading', name: 'خانه' })
      const homeFocus = await focusProof(
        runtime.page,
        '.ui-v2-home-notifications',
        'Home notification pre-keyboard diagnostic',
      )
      const heroFocus = await focusProof(
        runtime.page,
        '.hero-btn',
        'protected Market hero pre-keyboard diagnostic',
      )
      const keyboard = await keyboardWalk(
        runtime.page,
        'Home keyboard focused diagnostic',
        7,
        { enforce: false },
      )
      record('home-keyboard-walk-focused-diagnostic', { homeFocus, heroFocus, keyboard })
      return { homeFocus, heroFocus, keyboard, focusedDiagnostic: true }
    }
    if (HOME_FOCUS_DIAGNOSTIC) {
      await runtime.page.setViewportSize({ width: 390, height: 844 })
      await gotoPath(runtime.page, '/', { role: 'heading', name: 'خانه' })
      const focus = await focusProof(
        runtime.page,
        '.ui-v2-home-notifications',
        'Home notification focus diagnostic',
      )
      const targetBox = await runtime.page.locator('.ui-v2-home-notifications').boundingBox()
      assert.ok(targetBox, 'Home focus diagnostic target has no bounding box')
      const margin = 16
      const clip = {
        x: Math.max(0, targetBox.x - margin),
        y: Math.max(0, targetBox.y - margin),
        width: Math.min(390, targetBox.x + targetBox.width + margin) - Math.max(0, targetBox.x - margin),
        height: Math.min(844, targetBox.y + targetBox.height + margin) - Math.max(0, targetBox.y - margin),
      }
      const screenshotPath = path.join(OUTPUT_DIR, 'stage4-home-focus-ring-diagnostic.png')
      await runtime.page.screenshot({ path: screenshotPath, clip })
      const { data: focusPixels, info: focusPixelInfo } = await sharp(screenshotPath)
        .removeAlpha()
        .raw()
        .toBuffer({ resolveWithObject: true })
      const samplePixel = (x, y) => {
        const offset = (y * focusPixelInfo.width + x) * focusPixelInfo.channels
        return [focusPixels[offset], focusPixels[offset + 1], focusPixels[offset + 2]]
      }
      const relativeTargetTop = Math.round(targetBox.y - clip.y)
      const relativeTargetCenterX = Math.round(targetBox.x + targetBox.width / 2 - clip.x)
      const ringY = Math.round(
        relativeTargetTop - focus.outlineOffset - focus.outlineWidth / 2,
      )
      const backgroundY = Math.max(
        0,
        Math.floor(relativeTargetTop - focus.outlineOffset - focus.outlineWidth - 3),
      )
      const ringPixel = samplePixel(relativeTargetCenterX, ringY)
      const adjacentBackgroundPixel = samplePixel(relativeTargetCenterX, backgroundY)
      const ringContrast = contrastRatio(ringPixel, adjacentBackgroundPixel)
      assert.deepEqual(ringPixel, parseRgb(focus.canonicalTokens.resolvedColor))
      assert.ok(ringContrast >= 3, `Home focus ring contrast ${ringContrast.toFixed(2)}:1`)
      const screenshot = {
        file: path.basename(screenshotPath),
        bytes: fs.statSync(screenshotPath).size,
        sha256: sha256File(screenshotPath),
        clip,
        ringPixel,
        adjacentBackgroundPixel,
        ringContrast,
      }
      screenshots.push(screenshot)
      record('home-keyboard-focus-ring-focused-diagnostic', { focus, screenshot })
      return { focus, screenshot, focusedDiagnostic: true }
    }
    const responsiveViewports = SECURITY_360_DIAGNOSTIC ? [VIEWPORTS[0]] : VIEWPORTS
    const responsiveRoutes = SECURITY_360_DIAGNOSTIC
      ? DAILY_ROUTES.filter((route) => route.key === 'security')
      : DAILY_ROUTES
    for (const viewport of responsiveViewports) {
      await runtime.page.setViewportSize({ width: viewport.width, height: viewport.height })
      const routeRows = []
      for (const spec of responsiveRoutes) {
        await gotoPath(runtime.page, spec.path, spec.ready)
        await assertDailyRouteScope(runtime.page, spec, `${viewport.label}:${spec.key}`)
        const layout = await measureLayout(runtime.page)
        const occlusionProofs = await resolveOccludedInteractiveTargets(
          runtime.page,
          layout,
          `${viewport.label}:${spec.key}`,
        )
        assertLayout(layout, `${viewport.label}:${spec.key}`)
        const body = await visibleText(runtime.page)
        for (const forbidden of [
          '/raw-route-must-not-render',
          'hidden-backend',
          'hidden-server',
          'hidden-primary-origin',
          'hidden-secondary-origin',
          '198.51.100.10',
          '203.0.113.20',
        ]) {
          assert.equal(body.includes(forbidden), false, `${viewport.label}:${spec.key}: leaked ${forbidden}`)
        }
        routeRows.push({ key: spec.key, path: runtime.page.url(), layout, occlusionProofs })
      }
      assert.deepEqual(await navLabels(runtime.page), ['خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'])
      rows.push({ viewport, routes: routeRows })
      progress('responsive-daily-viewport-complete', {
        viewport: viewport.label,
        completed: rows.length,
        total: VIEWPORTS.length,
      })
    }

    if (SECURITY_360_DIAGNOSTIC) {
      record('security-360-scroll-occlusion-focused-diagnostic', { rows })
      return { rows, focusedDiagnostic: true }
    }

    await runtime.page.setViewportSize({ width: 390, height: 844 })
    for (const spec of DAILY_ROUTES) {
      await gotoPath(runtime.page, spec.path, spec.ready)
      await takeScreenshot(runtime.page, `stage4-${spec.key}-mobile-390.png`)
    }
    await runtime.page.setViewportSize({ width: 1440, height: 900 })
    for (const key of ['home', 'operations', 'notifications']) {
      const spec = DAILY_ROUTES.find((row) => row.key === key)
      await gotoPath(runtime.page, spec.path, spec.ready)
      await takeScreenshot(runtime.page, `stage4-${spec.key}-desktop-1440.png`)
    }

    await runtime.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(runtime.page, '/', { role: 'heading', name: 'خانه' })
    const homeFocus = await focusProof(
      runtime.page,
      '.ui-v2-home-notifications',
      'Home notification focus',
    )
    const heroFocus = await focusProof(runtime.page, '.hero-btn', 'protected Market hero focus')
    const keyboard = await keyboardWalk(runtime.page, 'Home keyboard walk', 7)
    const homeContrast = await contrastProof(
      runtime.page,
      ['.ui-v2-home-title', '.ui-v2-home-name'],
      'Home',
    )

    await gotoPath(runtime.page, '/operations', { role: 'heading', name: 'عملیات' })
    const operationsFocus = await focusProof(
      runtime.page,
      '.operations-action-tile',
      'Operations action focus',
    )
    const operationsContrast = await contrastProof(
      runtime.page,
      ['.ds-workspace-heading h1', '.operations-action-tile'],
      'Operations',
    )

    await gotoPath(runtime.page, '/account', { selector: '.ui-v2-account-header strong' })
    const accountFocus = await focusProof(runtime.page, '.hub-action', 'Account action focus')
    const accountContrast = await contrastProof(
      runtime.page,
      ['.ui-v2-account-header strong', '.hub-action strong'],
      'Account',
    )

    await gotoPath(runtime.page, '/account/notifications', { role: 'heading', name: 'اعلان‌ها' })
    const notificationsFocus = await focusProof(
      runtime.page,
      '.notification-category-tabs button',
      'Notifications filter focus',
    )
    const notificationsContrast = await contrastProof(
      runtime.page,
      ['.ui-page-header h1', '.ui-v2-notifications-item .notif-line-value'],
      'Notifications',
    )

    await runtime.page.setViewportSize({ width: 360, height: 430 })
    await runtime.page.locator('.notifications-return').focus()
    const compactKeyboard = await keyboardWalk(runtime.page, 'compact-height keyboard walk', 5)

    record('responsive-daily-core-matrix-8-widths-6-routes', { rows: rows.length })
    record('daily-core-no-horizontal-overflow-and-44px-targets')
    record('daily-core-focus-and-keyboard-visible', {
      homeFocus,
      heroFocus,
      operationsFocus,
      accountFocus,
      notificationsFocus,
      keyboard,
      compactKeyboard,
    })
    record('daily-core-representative-contrast', {
      homeContrast,
      operationsContrast,
      accountContrast,
      notificationsContrast,
    })
    return rows
  } finally {
    await closeRuntime(runtime)
  }
}

async function runHomeStateMatrix(browser, baseUrl) {
  const results = {}

  if (HOME_ACCOUNTANT_PWA_DIAGNOSTIC) {
    const diagnostic = await createPage(browser, baseUrl, {
      state: newRuntimeState({ user: { ...USERS.accountant } }),
      suite: 'home-accountant-pwa-diagnostic',
    })
    try {
      await diagnostic.page.clock.install()
      await diagnostic.page.setViewportSize({ width: 390, height: 844 })
      await gotoPath(diagnostic.page, '/', { role: 'heading', name: 'خانه' })
      await dispatchInstallPrompt(diagnostic.page)
      await diagnostic.page.clock.fastForward(4_100)
      const prompt = diagnostic.page.locator('.ui-v2-pwa-install')
      await prompt.waitFor({ state: 'visible' })
      assert.equal(await prompt.count(), 1)
      assert.equal(await diagnostic.page.locator('.hero-btn').count(), 0)
      assert.deepEqual(await navLabels(diagnostic.page), ['خانه', 'پیام‌رسان', 'حساب'])
      assert.equal((await visibleText(diagnostic.page)).includes('مشتریان'), false)
      const positiveEligibility = await diagnostic.page.evaluate(() => {
        const element = document.querySelector('.ui-v2-pwa-install')
        if (!(element instanceof HTMLElement)) return { count: 0 }
        const style = getComputedStyle(element)
        return {
          count: 1,
          className: element.className,
          opacity: style.opacity,
          visibility: style.visibility,
          transitionDuration: style.transitionDuration,
          pointerEvents: style.pointerEvents,
        }
      })
      record('home-accountant-independent-pwa-eligibility-focused-diagnostic', {
        positiveEligibility,
      })
      return { positiveEligibility, focusedDiagnostic: true }
    } finally {
      await closeRuntime(diagnostic)
    }
  }

  if (HOME_OFFLINE_PWA_DIAGNOSTIC) {
    const diagnostic = await createPage(browser, baseUrl, {
      state: newRuntimeState({ user: { ...USERS.owner } }),
      suite: 'home-offline-pwa-diagnostic',
    })
    try {
      await diagnostic.page.clock.install()
      await diagnostic.page.setViewportSize({ width: 390, height: 844 })
      await gotoPath(diagnostic.page, '/', { role: 'heading', name: 'خانه' })
      await dispatchInstallPrompt(diagnostic.page)
      await diagnostic.page.clock.fastForward(4_100)
      const overlay = diagnostic.page.locator('.ui-v2-pwa-install')
      await overlay.waitFor({ state: 'visible' })
      await diagnostic.context.setOffline(true)
      await diagnostic.page.evaluate(() => window.dispatchEvent(new Event('offline')))
      await diagnostic.page
        .getByText('اتصال اینترنت در دسترس نیست', { exact: false })
        .first()
        .waitFor()
      const snapshot = () =>
        diagnostic.page.evaluate(() => {
          const element = document.querySelector('.ui-v2-pwa-install')
          if (!(element instanceof HTMLElement)) {
            return { count: 0, online: navigator.onLine }
          }
          const style = getComputedStyle(element)
          return {
            count: 1,
            online: navigator.onLine,
            className: element.className,
            opacity: style.opacity,
            visibility: style.visibility,
            pointerEvents: style.pointerEvents,
            transitionDuration: style.transitionDuration,
            ariaHidden: element.getAttribute('aria-hidden'),
          }
        })
      const duringLeave = await snapshot()
      await diagnostic.page.clock.fastForward(300)
      await overlay.waitFor({ state: 'hidden' })
      const afterMotion = await snapshot()
      record('home-offline-pwa-leave-transition-focused-diagnostic', {
        duringLeave,
        afterMotion,
      })
      return { duringLeave, afterMotion, focusedDiagnostic: true }
    } finally {
      await diagnostic.context.setOffline(false).catch(() => {})
      await closeRuntime(diagnostic)
    }
  }

  const ready = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.owner }, serverUnreadCount: 3 }),
    suite: 'home-ready',
  })
  try {
    await ready.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(ready.page, '/', { role: 'heading', name: 'خانه' })
    await waitForRequestCount(
      ready.page,
      ready.state,
      '/api/notifications/unread-count',
      1,
      'GET',
    )
    await ready.page
      .getByRole('button', { name: 'اعلان‌های خوانده‌نشده', exact: true })
      .waitFor()
    assert.equal(await ready.page.locator('.ui-v2-home-notifications .notif-dot').count(), 1)
    assert.equal(await ready.page.locator('.hero-btn').count(), 1)
    assert.equal(await ready.page.locator('[data-ui-system="v2"] .hero-btn').count(), 0)
    const text = await visibleText(ready.page)
    for (const removed of [
      'معاملات امروز',
      'همکاران پروژه',
      'وضعیت سالم',
      'اتصال تلگرام',
      'نقش کاربری',
    ]) {
      assert.equal(text.includes(removed), false, `ready Home retained ${removed}`)
    }
    results.ready = { nav: await navLabels(ready.page) }
  } finally {
    await closeRuntime(ready)
  }

  const loadingState = newRuntimeState({ meMode: 'hold', cachedUser: null })
  const loading = await createPage(browser, baseUrl, {
    state: loadingState,
    cachedUser: null,
    suite: 'home-loading',
  })
  try {
    await loading.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(loading.page, '/', 'در حال دریافت خانه')
    assert.equal(await loading.page.locator('.hero-btn').count(), 0)
    assert.deepEqual(await navLabels(loading.page), ['خانه', 'پیام‌رسان', 'حساب'])
    results.loading = true
  } finally {
    await closeRuntime(loading)
  }

  const error = await createPage(browser, baseUrl, {
    state: newRuntimeState({ meMode: 'error', cachedUser: null }),
    cachedUser: null,
    suite: 'home-error',
  })
  try {
    await error.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(error.page, '/', 'دریافت اطلاعات خانه انجام نشد')
    assert.equal(await error.page.locator('.hero-btn').count(), 0)
    assert.equal(await error.page.getByRole('button', { name: 'تلاش دوباره' }).count(), 1)
    results.error = true
  } finally {
    await closeRuntime(error)
  }

  const stale = await createPage(browser, baseUrl, {
    state: newRuntimeState({ meMode: 'error', user: { ...USERS.owner } }),
    suite: 'home-stale',
  })
  try {
    await stale.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(stale.page, '/', 'اطلاعات خانه به‌روز نشد')
    assert.equal(await stale.page.locator('.hero-btn').count(), 1)
    assert.equal(await stale.page.getByRole('button', { name: 'به‌روزرسانی' }).count(), 1)
    results.stale = true
  } finally {
    await closeRuntime(stale)
  }

  const offline = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.owner } }),
    suite: 'home-offline',
  })
  try {
    await offline.page.clock.install()
    await offline.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(offline.page, '/', { role: 'heading', name: 'خانه' })
    await dispatchInstallPrompt(offline.page)
    await offline.page.clock.fastForward(4_100)
    await offline.page.locator('.ui-v2-pwa-install').waitFor({ state: 'visible' })
    await offline.context.setOffline(true)
    await offline.page.evaluate(() => window.dispatchEvent(new Event('offline')))
    await offline.page.getByText('اتصال اینترنت در دسترس نیست', { exact: false }).first().waitFor()
    assert.equal(await offline.page.locator('.hero-btn').count(), 1)
    await assertPwaPromptIneligible(offline.page, 'offline Home', offline.page.clock)
    results.offline = true
    await offline.context.setOffline(false)
    await offline.page.evaluate(() => window.dispatchEvent(new Event('online')))
  } finally {
    await closeRuntime(offline)
  }

  const reconnectState = newRuntimeState({
    user: { ...USERS.owner },
    chatPollMode: 'reconnecting',
  })
  const reconnecting = await createPage(browser, baseUrl, {
    state: reconnectState,
    suite: 'home-reconnecting',
  })
  try {
    await reconnecting.page.clock.install()
    await reconnecting.page.setViewportSize({ width: 390, height: 844 })
    await reconnecting.page.goto('/', { waitUntil: 'domcontentloaded' })
    await reconnecting.page.locator('.ui-v2-connection-banner').waitFor({ timeout: 15_000 })
    await dispatchInstallPrompt(reconnecting.page)
    await reconnecting.page.clock.fastForward(4_100)
    assert.equal(await reconnecting.page.locator('.dashboard-connectivity-notice').count(), 0)
    assert.equal(await reconnecting.page.locator('.ui-v2-connection-banner').count(), 1)
    assert.equal(
      await reconnecting.page.getByText('ارتباط در حال بازیابی است…', { exact: true }).count(),
      1,
    )
    await assertPwaPromptIneligible(
      reconnecting.page,
      'reconnecting Home',
      reconnecting.page.clock,
    )
    const reconnectRequestsBeforeRecovery = requestCount(
      reconnectState,
      '/api/chat/poll',
      'GET',
    )
    reconnectState.chatPollMode = 'success'
    await reconnecting.page.clock.fastForward(3_100)
    await waitForRequestCount(
      reconnecting.page,
      reconnectState,
      '/api/chat/poll',
      reconnectRequestsBeforeRecovery + 1,
      'GET',
    )
    await reconnecting.page.locator('.ui-v2-connection-banner').waitFor({ state: 'hidden', timeout: 15_000 })
    results.reconnecting = true
  } finally {
    await closeRuntime(reconnecting)
  }

  for (const [key, user] of [
    ['inactive', USERS.inactive],
    ['accountant', USERS.accountant],
    ['restricted', USERS.restricted],
  ]) {
    const runtime = await createPage(browser, baseUrl, {
      state: newRuntimeState({ user: { ...user } }),
      suite: `home-${key}`,
    })
    try {
      await runtime.page.clock.install()
      await runtime.page.setViewportSize({ width: 390, height: 844 })
      await gotoPath(runtime.page, '/', { role: 'heading', name: 'خانه' })
      await dispatchInstallPrompt(runtime.page)
      await runtime.page.clock.fastForward(4_100)
      if (key === 'inactive') {
        assert.equal(await runtime.page.locator('.hero-btn').count(), 0)
        assert.equal(await runtime.page.getByRole('button', { name: 'پیگیری در حساب' }).count(), 1)
        assert.equal((await navLabels(runtime.page)).includes('بازار'), false)
      }
      if (key === 'accountant') {
        assert.equal(await runtime.page.locator('.hero-btn').count(), 0)
        assert.equal((await visibleText(runtime.page)).includes('مشتریان'), false)
        assert.deepEqual(await navLabels(runtime.page), ['خانه', 'پیام‌رسان', 'حساب'])
      }
      if (key === 'restricted') {
        assert.equal(await runtime.page.getByText('معاملات موقتاً محدود است').count(), 1)
      }
      if (key === 'accountant') {
        const prompt = runtime.page.locator('.ui-v2-pwa-install')
        await prompt.waitFor({ state: 'visible' })
        assert.equal(await prompt.count(), 1, 'accountant: healthy Home PWA eligibility missing')
      } else {
        await assertPwaPromptIneligible(
          runtime.page,
          `${key}: install prompt escaped Home eligibility`,
          runtime.page.clock,
        )
      }
      results[key] = true
    } finally {
      await closeRuntime(runtime)
    }
  }

  record('home-ready-loading-error-offline-stale-reconnecting-inactive-accountant-restricted',
    results,
  )
  record('home-protected-market-hero-outside-v2-runtime')
  record('home-role-aware-bottom-nav')
  record('home-reconnecting-has-exactly-one-global-connection-surface')
  return results
}

async function operationsActionTitles(page) {
  return page
    .locator('.operations-action-tile .ui-action-card__title-row > strong')
    .allTextContents()
    .then((rows) =>
    rows.map((row) => row.replace(/\s+/gu, ' ').trim()),
  )
}

async function runRoleAndIdentityAuthorityMatrix(browser, baseUrl) {
  const matrix = [
    {
      key: 'owner',
      user: USERS.owner,
      actions: ['مشتریان', 'حسابداران'],
      nav: ['خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
    },
    {
      key: 'middle-admin',
      user: USERS.middleAdmin,
      actions: ['مشتریان', 'حسابداران', 'ارسال دعوت‌نامه', 'مدیریت کاربران'],
      nav: ['خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
    },
    {
      key: 'super-admin',
      user: USERS.superAdmin,
      actions: [
        'مشتریان',
        'حسابداران',
        'ارسال دعوت‌نامه',
        'مدیریت کاربران',
        'مدیریت کالاها',
        'پیام‌های مدیریت',
        'تنظیمات سیستم',
      ],
      nav: ['خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
    },
    {
      key: 'customer',
      user: USERS.customer,
      actions: [],
      nav: ['خانه', 'بازار', 'پیام‌رسان', 'حساب'],
    },
    {
      key: 'accountant',
      user: USERS.accountant,
      actions: [],
      nav: ['خانه', 'پیام‌رسان', 'حساب'],
    },
  ]
  const results = []
  for (const row of matrix) {
    const runtime = await createPage(browser, baseUrl, {
      state: newRuntimeState({ user: { ...row.user } }),
      suite: `operations-${row.key}`,
    })
    try {
      await runtime.page.setViewportSize({ width: 390, height: 844 })
      await gotoPath(runtime.page, '/operations', { role: 'heading', name: 'عملیات' })
      assert.deepEqual(await operationsActionTitles(runtime.page), row.actions)
      assert.deepEqual(
        await runtime.page
          .locator('.operations-action-tile .ui-action-card__arrow')
          .evaluateAll((arrows) => arrows.map((arrow) => arrow.getAttribute('aria-hidden'))),
        row.actions.map(() => 'true'),
        `${row.key}: decorative Operations arrows`,
      )
      for (const title of row.actions) {
        assert.equal(
          await runtime.page.getByRole('button', { name: title, exact: true }).count(),
          1,
          `${row.key}: accessible Operations action name ${title}`,
        )
      }
      assert.deepEqual(await navLabels(runtime.page), row.nav)
      const text = await visibleText(runtime.page)
      assert.equal(text.includes('بر اساس دسترسی شما'), false)
      assert.equal(text.includes('تعداد ابزار'), false)
      assert.equal(text.includes(row.user.role), false, `${row.key}: role chip/copy leaked`)
      if (row.actions.length === 0) {
        assert.equal(await runtime.page.getByRole('button', { name: 'رفتن به حساب' }).count(), 1)
      }
      results.push({ key: row.key, actions: row.actions, nav: row.nav })
    } finally {
      await closeRuntime(runtime)
    }
  }

  for (const routePath of ['/operations', '/account', '/account/security']) {
    const state = newRuntimeState({
      cachedUser: { role: 'کاربر' },
      user: { ...USERS.owner },
      meMode: 'hold',
    })
    const runtime = await createPage(browser, baseUrl, {
      state,
      cachedUser: { role: 'کاربر' },
      suite: `partial-identity-${routePath}`,
    })
    try {
      await runtime.page.setViewportSize({ width: 390, height: 844 })
      await runtime.page.goto(routePath, { waitUntil: 'domcontentloaded' })
      await runtime.page.waitForTimeout(200)
      const text = await visibleText(runtime.page)
      for (const forbidden of [
        'مشتریان',
        'حسابداران',
        'نشست‌های فعال',
        'پایان نشست',
        'خروج از نشست‌های دیگر',
      ]) {
        assert.equal(text.includes(forbidden), false, `${routePath}: partial identity exposed ${forbidden}`)
      }
      assert.equal(requestCount(state, '/api/sessions/active'), 0)
      const nav = await navLabels(runtime.page)
      assert.equal(nav.includes('بازار'), false)
      assert.equal(nav.includes('عملیات'), false)
    } finally {
      await closeRuntime(runtime)
    }
  }

  const olderAuthorityGate = deferred()
  const raceState = newRuntimeState({ user: { ...USERS.owner }, notificationRows: [] })
  const race = await createPage(browser, baseUrl, {
    state: raceState,
    suite: 'identity-authority-prime-structured-race',
  })
  try {
    await race.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(race.page, '/account/notifications', { role: 'heading', name: 'اعلان‌ها' })
    const authMeBefore = requestCount(raceState, '/api/auth/me', 'GET')
    raceState.meSequence = [
      {
        label: 'older-held-prime',
        gate: olderAuthorityGate,
        mode: 'success',
        user: { ...USERS.superAdmin },
      },
      { label: 'newer-structured-load', mode: 'success', user: { ...USERS.accountant } },
      { label: 'operations-route-refresh', mode: 'success', user: { ...USERS.accountant } },
    ]
    raceState.meSequenceCursor = 0
    await race.page.evaluate(async () => {
      const currentUser = await import('/src/utils/currentUser.ts')
      window.__stage4CurrentUserModule = currentUser
      window.__stage4OlderPrime = currentUser.primeCurrentUserSummary(true)
    })
    await waitForRequestCount(
      race.page,
      raceState,
      '/api/auth/me',
      authMeBefore + 1,
      'GET',
    )
    await race.page.evaluate(() => {
      window.__stage4LatestStructured =
        window.__stage4CurrentUserModule.loadCurrentUserSummary({ force: true })
    })
    await waitForRequestCount(
      race.page,
      raceState,
      '/api/auth/me',
      authMeBefore + 2,
      'GET',
    )
    await race.page.evaluate(() => window.__stage4LatestStructured)
    await race.page.evaluate(async () => {
      const router = (await import('/src/router/index.ts')).default
      await router.push('/operations')
    })
    await race.page.getByRole('heading', { name: 'عملیات' }).waitFor()
    await waitForRequestCount(
      race.page,
      raceState,
      '/api/auth/me',
      authMeBefore + 3,
      'GET',
    )
    assert.deepEqual(await operationsActionTitles(race.page), [])
    assert.deepEqual(await navLabels(race.page), ['خانه', 'پیام‌رسان', 'حساب'])
    olderAuthorityGate.resolve()
    await race.page.evaluate(() => window.__stage4OlderPrime)
    assert.equal(requestCount(raceState, '/api/auth/me', 'GET'), authMeBefore + 3)
    assert.deepEqual(await operationsActionTitles(race.page), [])
    assert.deepEqual(await navLabels(race.page), ['خانه', 'پیام‌رسان', 'حساب'])
    assert.equal(
      await race.page.evaluate(() => JSON.parse(localStorage.getItem('current_user_summary')).role),
      USERS.accountant.role,
    )
  } finally {
    await closeRuntime(race)
  }

  const deepLinkState = newRuntimeState({
    cachedUser: { role: 'مدیر ارشد' },
    user: { ...USERS.accountant },
    meMode: 'hold',
  })
  const deepLink = await createPage(browser, baseUrl, {
    state: deepLinkState,
    cachedUser: { role: 'مدیر ارشد' },
    suite: 'partial-cache-market-deep-link',
  })
  try {
    await deepLink.page.setViewportSize({ width: 390, height: 844 })
    const navigation = deepLink.page.goto('/market', { waitUntil: 'domcontentloaded' })
    await waitForRequestCount(deepLink.page, deepLinkState, '/api/auth/me', 1)
    assert.equal(await deepLink.page.locator('.market-page').count(), 0)
    deepLinkState.meMode = 'success'
    deepLinkState.meGate.resolve()
    await navigation
    await deepLink.page.getByRole('heading', { name: 'دسترسی به این بخش مجاز نیست' }).waitFor()
    assert.equal(await deepLink.page.locator('.market-page').count(), 0)
  } finally {
    await closeRuntime(deepLink)
  }

  const ownerOnlyOperationRoutes = [
    '/operations/customers',
    '/operations/customers/77',
    '/operations/accountants',
    '/operations/accountants/88',
  ]
  for (const deniedUser of [USERS.customer, USERS.accountant, USERS.inactive]) {
    const denied = await createPage(browser, baseUrl, {
      state: newRuntimeState({ user: { ...deniedUser } }),
      suite: `operations-owner-deep-links-${deniedUser.account_name}`,
    })
    try {
      await denied.page.setViewportSize({ width: 390, height: 844 })
      for (const routePath of ownerOnlyOperationRoutes) {
        await gotoPath(denied.page, routePath, {
          role: 'heading',
          name: 'دسترسی به این بخش مجاز نیست',
        })
        assert.equal(new URL(denied.page.url()).pathname, '/__system/recovery')
        assert.equal((await visibleText(denied.page)).includes('ویرایش مشتری'), false)
        assert.equal((await visibleText(denied.page)).includes('ویرایش حسابدار'), false)
      }
    } finally {
      await closeRuntime(denied)
    }
  }

  record('operations-role-action-matrix-owner-admin-customer-accountant', { results })
  record('operations-owner-only-child-deep-links-fail-closed-for-customer-accountant-inactive')
  record('partial-legacy-identity-permission-neutral-across-nav-operations-account-security')
  record('current-user-late-prime-cannot-overwrite-newer-structured-authority')
  record('partial-cache-cannot-authorize-protected-market-deep-link')
  return results
}

async function accountActionTitles(page) {
  return page.locator('.hub-action strong').allTextContents().then((rows) =>
    rows.map((row) => row.replace(/\s+/gu, ' ').trim()),
  )
}

async function runAccountSecurityStorageAndRedirects(browser, baseUrl) {
  const account = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.owner } }),
    suite: 'account-standard',
  })
  try {
    await account.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(account.page, '/account', { selector: '.ui-v2-account-header strong' })
    assert.deepEqual(await accountActionTitles(account.page), [
      'پروفایل من',
      'نشست‌های فعال',
      'حافظه و داده‌ها',
      'اعلان‌ها',
    ])
    for (const title of ['پروفایل من', 'نشست‌های فعال', 'حافظه و داده‌ها', 'اعلان‌ها']) {
      assert.equal(
        await account.page.locator('.hub-action strong', { hasText: title }).count(),
        1,
        `Account action must be unique: ${title}`,
      )
    }
    const text = await visibleText(account.page)
    assert.equal(text.includes('فعال است'), false, 'positive active badge/copy leaked')
    assert.equal(text.includes('نامشخص'), false, 'neutral unknown identity badge leaked')
  } finally {
    await closeRuntime(account)
  }

  const accountant = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.accountant } }),
    suite: 'account-accountant',
  })
  try {
    await accountant.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(accountant.page, '/account', { selector: '.ui-v2-account-header strong' })
    assert.deepEqual(await accountActionTitles(accountant.page), [
      'پروفایل من',
      'حافظه و داده‌ها',
      'اعلان‌ها',
    ])
    const text = await visibleText(accountant.page)
    for (const forbidden of [
      'نشست‌های فعال',
      'مدیریت نشست برای حسابدار',
      'خروج از این دستگاه',
      'اتصال تلگرام',
    ]) {
      assert.equal(text.includes(forbidden), false, `accountant Account leaked ${forbidden}`)
    }
    await gotoPath(accountant.page, '/account/security', { role: 'heading', name: 'امنیت حساب' })
    const securityText = await visibleText(accountant.page)
    assert.equal(securityText.includes('نشست‌های فعال'), false)
    assert.equal(securityText.includes('خروج از این دستگاه'), false)
    assert.equal(requestCount(accountant.state, '/api/sessions/active'), 0)
  } finally {
    await closeRuntime(accountant)
  }

  const inactive = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.inactive } }),
    suite: 'account-inactive',
  })
  try {
    await inactive.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(inactive.page, '/account', { selector: '.ui-v2-account-header strong' })
    assert.equal(await inactive.page.getByText('حساب غیرفعال', { exact: true }).count(), 1)
  } finally {
    await closeRuntime(inactive)
  }

  const securityState = newRuntimeState({ user: { ...USERS.owner } })
  const security = await createPage(browser, baseUrl, {
    state: securityState,
    suite: 'security-confirmations',
  })
  try {
    await security.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(security.page, '/account/security', { role: 'heading', name: 'امنیت حساب' })
    await security.page.getByText('گوشی دیگر', { exact: true }).waitFor()
    assert.equal(await security.page.locator('.settings-storage-card').count(), 0)
    assert.equal(requestCount(securityState, '/api/sessions/active') >= 1, true)
    const body = await visibleText(security.page)
    for (const forbidden of [
      'hidden-primary-origin',
      'hidden-secondary-origin',
      '198.51.100.10',
      '203.0.113.20',
      'home_server',
    ]) {
      assert.equal(body.includes(forbidden), false, `Security leaked ${forbidden}`)
    }

    const logoutOthersBefore = requestCount(securityState, '/api/sessions/logout-all', 'POST')
    await security.page.getByRole('button', { name: 'خروج از نشست‌های دیگر', exact: true }).click()
    await security.page.getByRole('group', { name: 'تأیید خروج از نشست‌های دیگر' }).waitFor()
    await waitForActiveSelector(security.page, '.logout-others-confirm', 'logout others confirm')
    assert.equal(requestCount(securityState, '/api/sessions/logout-all', 'POST'), logoutOthersBefore)
    await security.page.getByRole('button', { name: 'انصراف', exact: true }).last().click()
    assert.equal(await security.page.getByRole('group', { name: 'تأیید خروج از نشست‌های دیگر' }).count(), 0)
    await waitForActiveSelector(security.page, '.logout-all-btn', 'logout others cancel restore')

    await security.page.getByRole('button', { name: 'خروج از این دستگاه', exact: true }).click()
    await security.page.getByRole('group', { name: 'تأیید خروج از این دستگاه' }).waitFor()
    await waitForActiveSelector(security.page, '.local-logout-confirm', 'local logout confirm')
    await security.page.getByRole('button', { name: 'انصراف', exact: true }).last().click()
    await waitForActiveSelector(security.page, '.logout-btn', 'local logout cancel restore')

    const terminateBefore = requestCount(securityState, '/api/sessions/other-phone', 'DELETE')
    await security.page.getByRole('button', { name: 'پایان نشست', exact: true }).click()
    await security.page.getByRole('group', { name: 'تأیید پایان نشست' }).waitFor()
    await waitForActiveSelector(security.page, '.session-terminate-confirm', 'terminate session confirm')
    assert.equal(requestCount(securityState, '/api/sessions/other-phone', 'DELETE'), terminateBefore)
    securityState.terminateMode = 'error'
    await security.page.getByRole('button', { name: 'تأیید پایان نشست', exact: true }).click()
    await security.page.getByText('پایان نشست انجام نشد', { exact: true }).waitFor()
    assert.equal(await security.page.getByText('گوشی دیگر', { exact: true }).count(), 1)
    securityState.terminateMode = 'success'
    await security.page.getByRole('button', { name: 'تأیید پایان نشست', exact: true }).click()
    await security.page.getByText('نشست پایان یافت', { exact: true }).waitFor()
    assert.equal(await security.page.getByText('گوشی دیگر', { exact: true }).count(), 0)
  } finally {
    await closeRuntime(security)
  }

  const logoutOthersState = newRuntimeState({ user: { ...USERS.owner } })
  const logoutOthers = await createPage(browser, baseUrl, {
    state: logoutOthersState,
    suite: 'security-logout-others-receipt',
  })
  try {
    await logoutOthers.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(logoutOthers.page, '/account/security', { role: 'heading', name: 'امنیت حساب' })
    await logoutOthers.page.getByText('گوشی دیگر', { exact: true }).waitFor()
    await logoutOthers.page.getByRole('button', { name: 'خروج از نشست‌های دیگر', exact: true }).click()
    logoutOthersState.sessionsFailuresRemaining = 1
    await logoutOthers.page.getByRole('button', { name: 'تأیید خروج دیگران', exact: true }).click()
    await logoutOthers.page.getByText('نشست‌های دیگر پایان یافتند', { exact: true }).waitFor()
    assert.match(await visibleText(logoutOthers.page), /نشست فعلی این دستگاه حفظ شد/u)
    assert.equal(await logoutOthers.page.getByText('Chrome فعلی', { exact: true }).count(), 1)
    assert.equal(await logoutOthers.page.getByText('گوشی دیگر', { exact: true }).count(), 0)
    assert.equal(await logoutOthers.page.locator('.sessions-refresh-error').count(), 1)
  } finally {
    await closeRuntime(logoutOthers)
  }

  const localLogoutState = newRuntimeState({ user: { ...USERS.owner } })
  const localLogout = await createPage(browser, baseUrl, {
    state: localLogoutState,
    suite: 'security-local-logout-one-shot-receipt',
  })
  try {
    await localLogout.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(localLogout.page, '/account/security', { role: 'heading', name: 'امنیت حساب' })
    await localLogout.page.getByRole('button', { name: 'خروج از این دستگاه', exact: true }).click()
    await localLogout.page.getByRole('group', { name: 'تأیید خروج از این دستگاه' }).waitFor()
    localLogoutState.intentionalNavigation = true
    try {
      await localLogout.page.getByRole('button', { name: 'تأیید خروج', exact: true }).click()
      await localLogout.page.waitForURL((url) => url.pathname === '/login')
      await localLogout.page.locator('[data-local-logout-notice]').waitFor()
      let loginText = await visibleText(localLogout.page)
      assert.match(loginText, /خروج این دستگاه ثبت شد/u)
      assert.match(loginText, /نشست این دستگاه روی سرور پایان یافت/u)
      assert.equal(loginText.includes('نشست انتخاب‌شده بسته شد'), false)
      assert.equal(await localLogout.page.evaluate(() => localStorage.getItem('auth_token')), null)
      assert.equal(await localLogout.page.evaluate(() => localStorage.getItem('current_user_summary')), null)
      assert.deepEqual(
        await localLogout.page.evaluate(() =>
          window.__stage4ServiceWorkerMessages.filter(
            (message) => message.type === 'web-push:cleanup-session',
          ),
        ),
        [{ type: 'web-push:cleanup-session', enabled: null, authTokenPresent: true }],
      )
      await localLogout.page.reload({ waitUntil: 'domcontentloaded' })
      await localLogout.page.getByRole('heading', { name: 'ورود به سامانه' }).waitFor()
      assert.equal(await localLogout.page.locator('[data-local-logout-notice]').count(), 0)
    } finally {
      localLogoutState.intentionalNavigation = false
    }
  } finally {
    await closeRuntime(localLogout)
  }

  const localOnlyState = newRuntimeState({
    user: { ...USERS.owner },
    localLogoutMode: 'error',
  })
  const localOnly = await createPage(browser, baseUrl, {
    state: localOnlyState,
    suite: 'security-local-only-logout-receipt',
  })
  try {
    await localOnly.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(localOnly.page, '/account/security', { role: 'heading', name: 'امنیت حساب' })
    await localOnly.page.getByRole('button', { name: 'خروج از این دستگاه', exact: true }).click()
    localOnlyState.intentionalNavigation = true
    try {
      await localOnly.page.getByRole('button', { name: 'تأیید خروج', exact: true }).click()
      await localOnly.page.waitForURL((url) => url.pathname === '/login')
      await localOnly.page.locator('[data-local-logout-notice]').waitFor()
      const loginText = await visibleText(localOnly.page)
      assert.match(loginText, /اطلاعات ورود این دستگاه پاک شد/u)
      assert.match(loginText, /تأیید سرور دریافت نشد/u)
      assert.equal(loginText.includes('opaque'), false)
      assert.deepEqual(
        await localOnly.page.evaluate(() =>
          window.__stage4ServiceWorkerMessages.filter(
            (message) => message.type === 'web-push:cleanup-session',
          ),
        ),
        [{ type: 'web-push:cleanup-session', enabled: null, authTokenPresent: true }],
      )
    } finally {
      localOnlyState.intentionalNavigation = false
    }
  } finally {
    await closeRuntime(localOnly)
  }

  const telegramState = newRuntimeState({
    user: { ...USERS.superAdmin },
    telegramUrl: 'javascript:alert(1)',
    telegramBotUsername: 'stage4_browser_bot',
    telegramStartParameter: 'link_stage4-hostile',
    telegramDetail: 'server=iran route=/api/internal/telegram/link',
  })
  const telegram = await createPage(browser, baseUrl, {
    state: telegramState,
    suite: 'account-hostile-telegram-receipt',
  })
  try {
    await telegram.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(telegram.page, '/account', { selector: '.telegram-connect-panel' })
    await telegram.page.locator('.telegram-connect-panel').click()
    await telegram.page.getByText('لینک اتصال تلگرام آماده نشد.', { exact: true }).waitFor()
    assert.equal(new URL(telegram.page.url()).pathname, '/account')
    const text = await visibleText(telegram.page)
    for (const forbidden of [
      'javascript:alert(1)',
      'server=iran',
      '/api/internal/telegram/link',
    ]) {
      assert.equal(text.includes(forbidden), false, `Telegram receipt leaked ${forbidden}`)
    }
    assert.equal(requestCount(telegramState, '/api/auth/telegram-link-token', 'POST'), 1)
  } finally {
    await closeRuntime(telegram)
  }

  const storageState = newRuntimeState({ user: { ...USERS.owner } })
  const storage = await createPage(browser, baseUrl, {
    state: storageState,
    suite: 'storage-confirmation-success',
  })
  try {
    await storage.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(storage.page, '/account/storage', { role: 'heading', name: 'حافظه و داده‌ها' })
    assert.equal(await storage.page.locator('.settings-security-card').count(), 0)
    assert.equal(requestCount(storageState, '/api/sessions/active'), 0)
    await storage.page.getByRole('button', { name: 'پاک‌کردن فایل‌های محلی', exact: true }).click()
    await storage.page.getByRole('group', { name: 'تأیید پاک‌سازی فایل‌های محلی' }).waitFor()
    await waitForActiveSelector(storage.page, '.storage-clear-confirm', 'storage clear confirm')
    assert.match(await visibleText(storage.page), /فقط فایل‌های ذخیره‌شده پیام‌رسان روی همین دستگاه/u)
    await storage.page.getByRole('button', { name: 'انصراف', exact: true }).click()
    await waitForActiveSelector(storage.page, '.storage-clear-btn', 'storage clear cancel restore')
    await storage.page.getByRole('button', { name: 'پاک‌کردن فایل‌های محلی', exact: true }).click()
    await waitForActiveSelector(storage.page, '.storage-clear-confirm', 'storage clear reopen confirm')
    storageState.intentionalNavigation = true
    try {
      await storage.page.getByRole('button', { name: 'تأیید پاک‌سازی', exact: true }).click()
      await storage.page.getByText('فایل‌های محلی پاک شدند', { exact: true }).waitFor()
      assert.match(
        await visibleText(storage.page),
        /فقط فایل‌های ذخیره‌شده پیام‌رسان روی همین دستگاه حذف شدند/u,
      )
    } finally {
      storageState.intentionalNavigation = false
    }
  } finally {
    await closeRuntime(storage)
  }

  const sizeFailure = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.owner }, storageFailure: 'size' }),
    suite: 'storage-size-failure',
  })
  try {
    await sizeFailure.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(sizeFailure.page, '/account/storage', 'اندازه حافظه مشخص نشد')
    assert.equal(await sizeFailure.page.locator('.storage-size-error').count(), 1)
    assert.equal((await visibleText(sizeFailure.page)).includes('0.00 MB'), false)
  } finally {
    await closeRuntime(sizeFailure)
  }

  const clearFailure = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.owner }, storageFailure: 'clear' }),
    suite: 'storage-clear-failure',
  })
  try {
    await clearFailure.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(clearFailure.page, '/account/storage', { role: 'heading', name: 'حافظه و داده‌ها' })
    await clearFailure.page.getByRole('button', { name: 'پاک‌کردن فایل‌های محلی', exact: true }).click()
    await clearFailure.page.getByRole('button', { name: 'تأیید پاک‌سازی', exact: true }).click()
    await clearFailure.page.getByText('پاک‌سازی انجام نشد', { exact: true }).waitFor()
    assert.match(await visibleText(clearFailure.page), /فایل‌های محلی این دستگاه تغییر نکردند/u)
  } finally {
    await closeRuntime(clearFailure)
  }

  const redirects = await createPage(browser, baseUrl, {
    state: newRuntimeState({ user: { ...USERS.owner } }),
    suite: 'legacy-daily-redirects',
  })
  try {
    await redirects.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(redirects.page, '/settings?tab=storage#legacy', { role: 'heading', name: 'امنیت حساب' })
    assert.equal(new URL(redirects.page.url()).pathname, '/account/security')
    assert.equal(new URL(redirects.page.url()).search, '')
    assert.equal(new URL(redirects.page.url()).hash, '')
    await redirects.page.getByRole('button', { name: 'حساب', exact: true }).click()
    await redirects.page.locator('.ui-v2-account-header').waitFor()
    assert.equal(new URL(redirects.page.url()).pathname, '/account')

    await gotoPath(
      redirects.page,
      '/notifications?category=management#legacy',
      { role: 'heading', name: 'اعلان‌ها' },
    )
    assert.equal(new URL(redirects.page.url()).pathname, '/account/notifications')
    assert.equal(new URL(redirects.page.url()).search, '')
    assert.equal(new URL(redirects.page.url()).hash, '')
    await redirects.page.getByRole('button', { name: 'بازگشت به حساب', exact: true }).click()
    await redirects.page.locator('.ui-v2-account-header').waitFor()
    assert.equal(new URL(redirects.page.url()).pathname, '/account')
  } finally {
    await closeRuntime(redirects)
  }

  record('account-destinations-unique-and-accountant-exact')
  record('security-storage-route-exclusive-and-local-inline-confirmations')
  record('security-mutations-receipt-bound-and-failure-preserves-context')
  record('security-logout-others-stays-reconciled-after-follow-up-refresh-failure')
  record('local-logout-hard-redirect-fixed-one-shot-server-and-local-receipts')
  record('account-telegram-hostile-receipt-fails-closed-with-fixed-copy')
  record('storage-size-error-not-zero-clear-success-failure-truthful')
  record('legacy-settings-notifications-canonical-redirects-and-account-back')
}

const PUSH_STATE_EXPECTATIONS = Object.freeze([
  { state: 'checking', label: 'در حال بررسی', control: null },
  { state: 'unsupported', label: 'پشتیبانی نمی‌شود', control: null },
  { state: 'insecure', label: 'نیازمند HTTPS', control: null },
  { state: 'server-disabled', label: 'غیرفعال در سرور', control: null },
  { state: 'permission-blocked', label: 'مسدود در مرورگر', control: null },
  { state: 'permission-default', label: 'آماده فعال‌سازی', control: 'enable' },
  { state: 'subscribed', label: 'فعال', control: null },
  { state: 'unsubscribed', label: 'غیرفعال', control: 'enable' },
  { state: 'error', label: 'خطا', control: 'retry' },
])

async function runServiceWorkerPayloadContract(browser, baseUrl) {
  const runtime = await createPage(browser, baseUrl, {
    authenticated: false,
    state: newRuntimeState(),
    suite: 'push-service-worker-payload-contract',
  })
  try {
    await gotoPath(runtime.page, '/login', { role: 'heading', name: 'ورود به سامانه' })
    const source = fs.readFileSync(
      path.join(FRONTEND, 'public/push-notifications-sw.js'),
      'utf8',
    )
    const evidence = await runtime.page.evaluate(async (serviceWorkerSource) => {
      const listeners = new Map()
      const shown = []
      const opened = []
      const cleanupRequests = []
      let unsubscribeCount = 0
      const cacheEntries = new Map()
      const cachesFixture = {
        open: async () => ({
          put: async (key, response) => {
            cacheEntries.set(String(key), await response.text())
          },
          match: async (key) => {
            const value = cacheEntries.get(String(key))
            return value === undefined ? undefined : new Response(value)
          },
        }),
      }
      const subscription = {
        endpoint: 'https://push.invalid/stage4-browser',
        unsubscribe: async () => {
          unsubscribeCount += 1
          return true
        },
      }
      const fetchFixture = async (url, options = {}) => {
        cleanupRequests.push({
          url: String(url),
          method: options.method,
          headers: options.headers,
          body: options.body,
          credentials: options.credentials,
          redirect: options.redirect,
          cache: options.cache,
        })
        return new Response(null, { status: 204 })
      }
      const scope = {
        location: { origin: location.origin },
        registration: {
          pushManager: { getSubscription: async () => subscription },
          showNotification: async (title, options) => {
            shown.push({ title, options })
          },
        },
        clients: {
          matchAll: async () => [],
          openWindow: async (url) => {
            opened.push(url)
          },
        },
        addEventListener: (name, listener) => listeners.set(name, listener),
      }
      Function('self', 'fetch', 'caches', serviceWorkerSource)(
        scope,
        fetchFixture,
        cachesFixture,
      )

      const dispatchMessage = async (data) => {
        let pending = Promise.resolve()
        listeners.get('message')({
          data,
          waitUntil: (value) => { pending = Promise.resolve(value) },
        })
        await pending
      }

      const dispatchPush = async (payload) => {
        let pending = Promise.resolve()
        listeners.get('push')({
          data: { json: () => payload, text: () => '' },
          waitUntil: (value) => { pending = Promise.resolve(value) },
        })
        await pending
      }
      await dispatchMessage({ type: 'web-push:delivery-gate', enabled: true })
      await dispatchPush(null)
      await dispatchPush({
        title: 'route=/admin',
        body: [
          'backend=iran',
          'route=/admin',
          'مسیر：/market',
          'server=hidden-cr\rbackend=hidden-after-cr\u2028route=/hidden-line\u2029مسیر﹕/hidden-paragraph',
          '📝 توضیحات: سالم',
        ].join('\n'),
        route: '/api/private?token=raw-secret',
        icon: 'https://tracker.invalid/icon.png',
        badge: 'https://tracker.invalid/badge.png',
        data: { route: '/api/private?token=raw-secret', secret: 'raw-secret' },
      })

      let clickPending = Promise.resolve()
      listeners.get('notificationclick')({
        notification: {
          data: { route: '/account?token=raw-secret' },
          close: () => undefined,
        },
        waitUntil: (value) => { clickPending = Promise.resolve(value) },
      })
      await clickPending
      await dispatchMessage({
        type: 'web-push:cleanup-session',
        authToken: 'stage4-old-auth-token',
      })
      await dispatchPush({ title: 'must not deliver after cleanup' })
      return { shown, opened, cleanupRequests, unsubscribeCount }
    }, source)

    assert.equal(evidence.shown.length, 2)
    assert.deepEqual(evidence.shown[0], {
      title: 'اعلان جدید',
      options: {
        body: '',
        icon: '/pwa-192x192.png',
        badge: '/pwa-192x192.png',
        data: { route: '/account/notifications' },
        dir: 'rtl',
        lang: 'fa-IR',
        vibrate: [200, 100, 200],
      },
    })
    assert.equal(evidence.shown[1].title, 'اعلان جدید')
    assert.equal(evidence.shown[1].options.body, '📝 توضیحات: سالم')
    assert.deepEqual(evidence.shown[1].options.data, { route: '/account/notifications' })
    const serialized = JSON.stringify(evidence)
    for (const forbidden of [
      'backend=iran',
      'route=/admin',
      'مسیر：/market',
      'hidden-cr',
      'hidden-after-cr',
      '/hidden-line',
      '/hidden-paragraph',
      'raw-secret',
      'tracker.invalid',
    ]) {
      assert.equal(serialized.includes(forbidden), false, `service worker leaked ${forbidden}`)
    }
    assert.deepEqual(evidence.opened, [`${baseUrl}/account/notifications`])
    assert.equal(evidence.unsubscribeCount, 1)
    assert.deepEqual(evidence.cleanupRequests, [
      {
        url: '/api/notifications/push/subscription',
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer stage4-old-auth-token',
        },
        body: JSON.stringify({ endpoint: 'https://push.invalid/stage4-browser' }),
        credentials: 'same-origin',
        redirect: 'error',
        cache: 'no-store',
      },
    ])
    record('push-service-worker-null-hostile-payload-safe-canonical-contract', {
      shown: evidence.shown,
      opened: evidence.opened,
      cleanupRequestCount: evidence.cleanupRequests.length,
      unsubscribeCount: evidence.unsubscribeCount,
    })
    record('push-service-worker-session-cleanup-delete-unsubscribe-and-fail-closed-gate')
    return evidence
  } finally {
    await closeRuntime(runtime)
  }
}

async function runPushStateMatrix(browser, baseUrl) {
  const results = []
  for (const expected of PUSH_STATE_EXPECTATIONS) {
    const state = newRuntimeState({
      user: { ...USERS.owner },
      pushScenario: expected.state,
      notificationRows: [],
    })
    const runtime = await createPage(browser, baseUrl, {
      state,
      suite: `push-${expected.state}`,
    })
    try {
      await runtime.page.setViewportSize({ width: 390, height: 844 })
      await runtime.page.goto('/account/notifications', { waitUntil: 'domcontentloaded' })
      await runtime.page.getByText(expected.label, { exact: true }).first().waitFor()
      if (expected.state === 'checking') {
        await waitForRequestCount(
          runtime.page,
          state,
          '/api/notifications/push/public-key',
          1,
          'GET',
        )
        assert.equal(
          await runtime.page.getByText('در حال بررسی', { exact: true }).first().isVisible(),
          true,
          'checking state was asserted before the held request began',
        )
      }
      const enableCount = await runtime.page.locator('.push-enable-btn').count()
      const retryCount = await runtime.page.locator('.push-status-retry').count()
      assert.equal(enableCount, expected.control === 'enable' ? 1 : 0, `${expected.state}: enable`)
      assert.equal(retryCount, expected.control === 'retry' ? 1 : 0, `${expected.state}: retry`)
      assert.equal(
        await runtime.page.evaluate(() => window.__stage4PermissionRequests),
        0,
        `${expected.state}: permission prompted on mount`,
      )
      if (expected.state === 'subscribed') {
        await waitForRequestCount(
          runtime.page,
          state,
          '/api/sessions/verify',
          1,
          'POST',
        )
        const authoritativeRebindCount = await waitForRequestCountToSettle(
          runtime.page,
          state,
          '/api/notifications/push/subscription',
          'POST',
        )
        assert.ok(
          authoritativeRebindCount >= 1 && authoritativeRebindCount <= 2,
          `existing local subscription rebind count must remain bounded at 1..2; received ${authoritativeRebindCount}`,
        )
        const authoritativeRebindBodies = state.requestLog
          .filter(
            (entry) =>
              entry.pathname === '/api/notifications/push/subscription' &&
              entry.method === 'POST',
          )
          .map((entry) => JSON.parse(entry.postData))
        assert.ok(
          authoritativeRebindBodies.every(
            (payload) => JSON.stringify(payload) === JSON.stringify(authoritativeRebindBodies[0]),
          ),
          'sequential current-account push rebind payloads must be identical',
        )
      }
      assert.match(await visibleText(runtime.page), /فقط برای همین مرورگر و دستگاه/u)
      results.push({ ...expected, enableCount, retryCount })
    } finally {
      await closeRuntime(runtime)
    }
  }

  const explicitState = newRuntimeState({
    user: { ...USERS.owner },
    pushScenario: 'permission-default',
    pushPermissionResult: 'granted',
    notificationRows: [],
  })
  const explicit = await createPage(browser, baseUrl, {
    state: explicitState,
    suite: 'push-explicit-permission',
  })
  try {
    await explicit.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(explicit.page, '/account/notifications', 'آماده فعال‌سازی')
    await explicit.page.evaluate(() => {
      window.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      window.dispatchEvent(new Event('touchstart', { bubbles: true }))
    })
    assert.equal(await explicit.page.evaluate(() => window.__stage4PermissionRequests), 0)
    await explicit.page.getByRole('button', { name: 'فعال‌سازی اعلان مرورگر', exact: true }).click()
    await explicit.page.getByText('فعال شد', { exact: true }).waitFor()
    assert.equal(await explicit.page.evaluate(() => window.__stage4PermissionRequests), 1)
    assert.equal(await explicit.page.evaluate(() => window.__stage4PushSubscriptions), 1)
    assert.equal(requestCount(explicitState, '/api/notifications/push/subscription', 'POST'), 1)
  } finally {
    await closeRuntime(explicit)
  }

  const rollbackState = newRuntimeState({
    user: { ...USERS.owner },
    pushScenario: 'permission-default',
    pushPermissionResult: 'granted',
    pushPostFailuresRemaining: 2,
    notificationRows: [],
  })
  const rollback = await createPage(browser, baseUrl, {
    state: rollbackState,
    suite: 'push-double-registration-failure-rollback',
  })
  try {
    await rollback.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(rollback.page, '/account/notifications', 'آماده فعال‌سازی')
    await rollback.page.getByRole('button', { name: 'فعال‌سازی اعلان مرورگر', exact: true }).click()
    await rollback.page.getByText('فعال‌سازی ناموفق بود', { exact: true }).waitFor()
    assert.equal(requestCount(rollbackState, '/api/notifications/push/subscription', 'POST'), 2)
    assert.equal(await rollback.page.evaluate(() => window.__stage4PushSubscriptions), 2)
    await rollback.page.getByRole('button', { name: 'بررسی دوباره', exact: true }).click()
    await rollback.page.getByText('غیرفعال', { exact: true }).first().waitFor()
    assert.equal(await rollback.page.locator('.push-enable-btn').count(), 1)
    await rollback.page.getByRole('button', { name: 'فعال‌سازی اعلان مرورگر', exact: true }).click()
    await rollback.page.getByText('فعال شد', { exact: true }).waitFor()
    assert.equal(requestCount(rollbackState, '/api/notifications/push/subscription', 'POST'), 3)
    assert.equal(await rollback.page.evaluate(() => window.__stage4PushSubscriptions), 3)
  } finally {
    await closeRuntime(rollback)
  }

  const readyTimeoutState = newRuntimeState({
    user: { ...USERS.owner },
    pushScenario: 'subscribed',
    serviceWorkerReadyMode: 'hold',
    notificationRows: [],
  })
  const readyTimeout = await createPage(browser, baseUrl, {
    state: readyTimeoutState,
    suite: 'push-service-worker-ready-timeout',
  })
  try {
    await readyTimeout.page.clock.install()
    await readyTimeout.page.setViewportSize({ width: 390, height: 844 })
    await readyTimeout.page.goto('/account/notifications', { waitUntil: 'domcontentloaded' })
    await readyTimeout.page.getByText('در حال بررسی', { exact: true }).first().waitFor()
    await readyTimeout.page.clock.fastForward(20_000)
    await readyTimeout.page.getByText('خطا', { exact: true }).first().waitFor()
    assert.equal(await readyTimeout.page.locator('.push-status-retry').count(), 1)
  } finally {
    await closeRuntime(readyTimeout)
  }

  await runServiceWorkerPayloadContract(browser, baseUrl)

  record('notifications-push-nine-state-matrix', { results })
  record('notifications-push-permission-only-after-explicit-enable')
  record('notifications-push-existing-subscription-current-account-authority')
  record('notifications-push-double-post-failure-rolls-back-before-retry')
  record('notifications-push-service-worker-ready-timeout-is-retryable')
  return results
}

async function runNotificationHistoryAndNavigation(browser, baseUrl) {
  const standardState = newRuntimeState({
    user: { ...USERS.owner },
    notificationRows: DEFAULT_NOTIFICATIONS.map((row) => ({ ...row })),
  })
  const standard = await createPage(browser, baseUrl, {
    state: standardState,
    suite: 'notification-standard',
  })
  try {
    await standard.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(standard.page, '/account/notifications', { role: 'heading', name: 'اعلان‌ها' })
    await standard.page.getByText('طلای آب‌شده', { exact: false }).waitFor()
    await waitForRequestCount(standard.page, standardState, '/api/sessions/verify', 1, 'POST')
    const historySnapshotCount = await waitForRequestCountToSettle(
      standard.page,
      standardState,
      '/api/notifications/',
      'GET',
    )
    assert.ok(
      historySnapshotCount >= 1 && historySnapshotCount <= 2,
      `notification center mount plus initial socket synchronization must remain bounded at 1..2 history snapshots; received ${historySnapshotCount}`,
    )
    await waitForRequestCount(standard.page, standardState, '/api/notifications/4401/read', 1, 'PATCH')
    await waitForRequestCount(standard.page, standardState, '/api/notifications/4402/read', 1, 'PATCH')
    await standard.page.waitForTimeout(100)
    assert.equal(requestCount(standardState, '/api/notifications/4401/read', 'PATCH'), 1)
    assert.equal(requestCount(standardState, '/api/notifications/4402/read', 'PATCH'), 1)
    assert.equal(requestCount(standardState, '/api/notifications/mark-all-read', 'POST'), 0)
    assert.deepEqual(
      await standard.page.locator('.notification-category-tabs button').allTextContents().then((rows) =>
        rows.map((row) => row.trim()),
      ),
      ['معاملات', 'سایر'],
    )
    let text = await visibleText(standard.page)
    for (const forbidden of [
      '/raw-route-must-not-render',
      'hidden-backend',
      'hidden-server',
      'route:',
      'backend:',
      'server:',
      '/raw-equals-must-not-render',
      'hidden-equals-backend',
      '/raw-fullwidth-must-not-render',
      'hidden-cr',
      'hidden-after-cr',
      '/raw-after-line-separator',
      '/raw-after-paragraph-separator',
      'route=',
    ]) {
      assert.equal(text.toLowerCase().includes(forbidden), false, `notification leaked ${forbidden}`)
    }
    assert.equal(await standard.page.locator('.notif-item:is(button)').count(), 1)
    await standard.page.getByRole('button', { name: 'باز کردن اعلان معامله جدید' }).click()
    await standard.page.getByRole('heading', { name: 'حافظه و داده‌ها' }).waitFor()
    assert.equal(new URL(standard.page.url()).pathname, '/account/storage')

    await gotoPath(standard.page, '/account/notifications', { role: 'heading', name: 'اعلان‌ها' })
    await standard.page.getByRole('tab', { name: 'سایر', exact: true }).click()
    await standard.page.getByText('یادآوری حساب', { exact: true }).waitFor()
    assert.equal(await standard.page.locator('.notifications-list article').count(), 2)
    assert.equal(await standard.page.locator('.notifications-list button').count(), 0)
    text = await visibleText(standard.page)
    assert.equal(text.includes('اعلان‌های سایر ۲'), false)
  } finally {
    await closeRuntime(standard)
  }

  const loadingState = newRuntimeState({
    notificationMode: 'hold',
    notificationRows: [],
  })
  const loading = await createPage(browser, baseUrl, {
    state: loadingState,
    suite: 'notification-initial-loading',
  })
  try {
    await loading.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(loading.page, '/account/notifications', 'در حال دریافت اعلان‌ها')
    assert.equal(await loading.page.locator('.notifications-list').count(), 0)
  } finally {
    await closeRuntime(loading)
  }

  const initialError = await createPage(browser, baseUrl, {
    state: newRuntimeState({ notificationMode: 'error', notificationRows: [] }),
    suite: 'notification-initial-error',
  })
  try {
    await initialError.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(initialError.page, '/account/notifications', 'اعلان‌ها دریافت نشدند')
    assert.equal(await initialError.page.locator('.notification-history-error').count(), 1)
    assert.equal(await initialError.page.getByRole('button', { name: 'تلاش دوباره' }).count(), 1)
  } finally {
    await closeRuntime(initialError)
  }

  const empty = await createPage(browser, baseUrl, {
    state: newRuntimeState({ notificationRows: [] }),
    suite: 'notification-true-empty',
  })
  try {
    await empty.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(empty.page, '/account/notifications', 'هیچ اعلانی یافت نشد')
    assert.equal(await empty.page.locator('.notification-history-error').count(), 0)
  } finally {
    await closeRuntime(empty)
  }

  const categoryEmpty = await createPage(browser, baseUrl, {
    state: newRuntimeState({ notificationRows: [{ ...DEFAULT_NOTIFICATIONS[0] }] }),
    suite: 'notification-category-empty',
  })
  try {
    await categoryEmpty.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(categoryEmpty.page, '/account/notifications', 'طلای آب‌شده')
    await categoryEmpty.page.getByRole('tab', { name: 'سایر', exact: true }).click()
    await categoryEmpty.page.getByText('اعلانی در این فیلتر وجود ندارد', { exact: true }).waitFor()
    assert.equal(await categoryEmpty.page.getByText('هیچ اعلانی یافت نشد').count(), 0)
  } finally {
    await closeRuntime(categoryEmpty)
  }

  const retainedState = newRuntimeState({ notificationMode: 'error', notificationRows: [] })
  const retained = await createPage(browser, baseUrl, {
    state: retainedState,
    suite: 'notification-retained-error',
  })
  try {
    await retained.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(retained.page, '/', { role: 'heading', name: 'خانه' })
    await emitSocket(retained.page, 'message', {
      id: 4490,
      title: 'اعلان حفظ‌شده',
      body: 'این مورد باید پس از خطای refresh بماند.',
      category: 'trade',
      is_read: false,
      created_at: '2026-08-09T08:40:00.000Z',
    })
    await retained.page.getByText('اعلان حفظ‌شده', { exact: true }).waitFor()
    await retained.page.evaluate(async () => {
      const router = (await import('/src/router/index.ts')).default
      await router.push('/account/notifications')
    })
    await retained.page
      .getByText('به‌روزرسانی اعلان‌ها انجام نشد', { exact: false })
      .first()
      .waitFor({ timeout: 30_000 })
    await waitForSettledPage(retained.page)
    assert.equal(
      await retained.page
        .locator('.notifications-list')
        .getByText('این مورد باید پس از خطای refresh بماند.')
        .count(),
      1,
    )
    assert.equal(
      retainedState.requestLog.some((entry) => /\/api\/notifications\/[^/]+\/read$/u.test(entry.pathname)),
      false,
    )
  } finally {
    await closeRuntime(retained)
  }

  const concurrentState = newRuntimeState({
    notificationMode: 'hold',
    notificationRows: [{ ...DEFAULT_NOTIFICATIONS[0], id: 4480, is_read: false }],
  })
  const concurrent = await createPage(browser, baseUrl, {
    state: concurrentState,
    suite: 'notification-concurrent-realtime',
  })
  try {
    await concurrent.page.setViewportSize({ width: 390, height: 844 })
    await concurrent.page.goto('/account/notifications', { waitUntil: 'domcontentloaded' })
    await concurrent.page.getByText('در حال دریافت اعلان‌ها', { exact: false }).waitFor()
    await emitSocket(concurrent.page, 'message', {
      id: 4491,
      title: 'هم‌زمان تازه',
      body: 'این اعلان پس از بازشدن مرکز رسید.',
      category: 'trade',
      is_read: false,
      created_at: '2026-08-09T08:41:00.000Z',
    })
    concurrentState.notificationRows.push({
      id: 4491,
      title: 'هم‌زمان تازه',
      body: 'این اعلان پس از بازشدن مرکز رسید.',
      content: 'این اعلان پس از بازشدن مرکز رسید.',
      message: 'این اعلان پس از بازشدن مرکز رسید.',
      category: 'trade',
      level: 'info',
      is_read: false,
      created_at: '2026-08-09T08:41:00.000Z',
    })
    const concurrentReadResponse = concurrent.page.waitForResponse((response) => {
      const url = new URL(response.url())
      return (
        url.pathname === '/api/notifications/4480/read' &&
        response.request().method() === 'PATCH' &&
        response.status() === 204
      )
    })
    concurrentState.notificationMode = 'success'
    concurrentState.notificationGate.resolve()
    const freshItem = concurrent.page.locator('.notif-item', { hasText: 'این اعلان پس از بازشدن مرکز رسید.' })
    await freshItem.waitFor()
    const concurrentReadCount = await waitForRequestCountToSettle(
      concurrent.page,
      concurrentState,
      '/api/notifications/4480/read',
      'PATCH',
    )
    assert.equal(concurrentReadCount, 1)
    assert.equal((await concurrentReadResponse).status(), 204)
    assert.equal(await freshItem.evaluate((element) => element.classList.contains('is-unread')), true)
    const originalHistoryItem = concurrent.page.locator('.notif-item', { hasText: 'طلای آب‌شده' })
    await originalHistoryItem.waitFor()
    await concurrent.page.waitForFunction(
      (element) => element instanceof HTMLElement && !element.classList.contains('is-unread'),
      await originalHistoryItem.elementHandle(),
    )
    assert.equal(
      concurrentState.notificationRows.find((row) => row.id === 4491)?.is_read,
      false,
    )
    assert.equal(
      concurrentState.notificationRows.find((row) => row.id === 4480)?.is_read,
      true,
    )
  } finally {
    await closeRuntime(concurrent)
  }

  const missingIdState = newRuntimeState({
    notificationMode: 'hold',
    notificationRows: [],
  })
  const missingId = await createPage(browser, baseUrl, {
    state: missingIdState,
    suite: 'notification-concurrent-missing-id',
  })
  try {
    await missingId.page.setViewportSize({ width: 390, height: 844 })
    await missingId.page.goto('/account/notifications', { waitUntil: 'domcontentloaded' })
    await missingId.page.getByText('در حال دریافت اعلان‌ها', { exact: false }).waitFor()
    await emitSocket(missingId.page, 'message', {
      title: 'هم‌زمان بدون شناسه',
      body: 'این اعلان fallback باید حفظ شود.',
      category: 'trade',
      is_read: false,
      created_at: '2026-08-09T08:42:00.000Z',
    })
    missingIdState.notificationMode = 'success'
    missingIdState.notificationGate.resolve()
    const item = missingId.page.locator('.notif-item', { hasText: 'این اعلان fallback باید حفظ شود.' })
    await item.waitFor()
    assert.equal(await item.evaluate((element) => element.classList.contains('is-unread')), true)
  } finally {
    await closeRuntime(missingId)
  }

  const toastState = newRuntimeState({ notificationRows: [] })
  const toast = await createPage(browser, baseUrl, {
    state: toastState,
    suite: 'notification-toast-keyboard-canonical',
  })
  try {
    await toast.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(toast.page, '/', { role: 'heading', name: 'خانه' })
    await emitSocket(toast.page, 'message', {
      id: 4493,
      title: 'اعلان صفحه‌کلید',
      body: 'route=/admin\nbackend=hidden-toast-backend\nمتن سالم اعلان',
      category: 'system',
      is_read: false,
    })
    const action = toast.page.getByRole('button', { name: 'باز کردن اعلان «اعلان صفحه‌کلید»' })
    await action.waitFor()
    const toastText = await toast.page.locator('.ui-v2-toast-item').innerText()
    assert.equal(toastText.includes('route='), false)
    assert.equal(toastText.includes('hidden-toast-backend'), false)
    assert.match(toastText, /متن سالم اعلان/u)
    await action.focus()
    await toast.page.keyboard.press('Enter')
    await toast.page.getByRole('heading', { name: 'اعلان‌ها' }).waitFor()
    assert.equal(new URL(toast.page.url()).pathname, '/account/notifications')
  } finally {
    await closeRuntime(toast)
  }

  const redirectState = newRuntimeState({
    user: { ...USERS.accountant },
    notificationRows: [
      {
        ...DEFAULT_NOTIFICATIONS[0],
        id: 4492,
        title: 'بازار نامجاز',
        content: 'مقصد برای این نقش مجاز نیست.',
        message: 'مقصد برای این نقش مجاز نیست.',
        route: '/market',
      },
    ],
  })
  const redirect = await createPage(browser, baseUrl, {
    state: redirectState,
    suite: 'notification-guard-redirect-recovery',
  })
  try {
    await redirect.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(redirect.page, '/account/notifications', {
      role: 'heading',
      name: 'اعلان‌ها',
    })
    const guardedNotification = redirect.page.getByRole('button', {
      name: 'باز کردن اعلان بازار نامجاز',
    })
    await guardedNotification.waitFor()
    await guardedNotification.click()
    await redirect.page.getByRole('heading', { name: 'اعلان‌ها' }).waitFor()
    assert.equal(new URL(redirect.page.url()).pathname, '/account/notifications')
    assert.equal(await guardedNotification.count(), 1)
    const recoveryHeading = redirect.page.getByText('دسترسی به این بخش مجاز نیست')
    await recoveryHeading.waitFor({ state: 'hidden', timeout: 5_000 })
    await redirect.page.waitForTimeout(250)
    assert.equal(await recoveryHeading.count(), 0)
  } finally {
    await closeRuntime(redirect)
  }

  record('notification-history-limit50-no-category-counts-no-raw-metadata')
  record('notification-loading-error-empty-retained-error-category-empty-distinct')
  record('notification-route-less-and-invalid-items-noninteractive')
  record('notification-concurrent-realtime-preserved-and-unread-at-center-open-cutoff')
  record('notification-concurrent-missing-id-normalized-once-and-preserved')
  record('notification-guard-redirect-restores-canonical-center-context')
  record('notification-toast-keyboard-opens-canonical-center-with-sanitized-copy')
}

async function runPwaLayersMotionAndKeyboard(browser, baseUrl) {
  const state = newRuntimeState({ user: { ...USERS.owner } })
  const runtime = await createPage(browser, baseUrl, {
    state,
    suite: 'pwa-layers-motion',
  })
  const { page, context } = runtime
  try {
    await page.clock.install()
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(page, '/', { role: 'heading', name: 'خانه' })
    await dispatchInstallPrompt(page)
    await page.clock.fastForward(4100)
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'visible' })
    assert.equal(await page.locator('.ui-v2-pwa-actions .ui-button').count(), 2)
    const actionSizes = await page.locator('.ui-v2-pwa-actions .ui-button').evaluateAll((buttons) =>
      buttons.map((button) => {
        const rect = button.getBoundingClientRect()
        return { width: rect.width, height: rect.height }
      }),
    )
    assert.ok(actionSizes.every((size) => size.width >= 43.5 && size.height >= 47.5))
    await takeScreenshot(page, 'stage4-home-pwa-ready-mobile-390.png')

    const navRestoreTarget = page.locator('.ui-v2-bottom-nav-item').first()
    await navRestoreTarget.focus()
    await emitSocket(page, 'message', {
      id: 4590,
      title: 'لایه آزمون',
      body: 'پیام آزمون ترتیب لایه‌ها',
      level: 'info',
      category: 'system',
    })
    await page.locator('.ui-v2-toast-item').waitFor({ state: 'visible' })
    await emitSocket(page, 'session:login_request', {
      request_id: 'stage4-layer-request',
      device_name: 'Stage 4 Browser',
      device_ip: '127.0.0.1',
      expires_at: new Date(Date.now() + 120_000).toISOString(),
    })
    const dialog = page.locator('.ui-v2-session-card[role="dialog"]')
    await dialog.waitFor({ state: 'visible' })
    await assertPwaPromptIneligible(page, 'security-layer Home', page.clock)
    assert.equal(await page.locator('.ui-v2-toast-layer[aria-hidden="true"][inert]').count(), 1)
    await page.clock.fastForward(6_000)
    assert.equal(
      await page.locator('.ui-v2-toast-item', { hasText: 'لایه آزمون' }).count(),
      1,
      'toast expired while its layer was security-blocked',
    )

    state.verifyMode = 'abort'
    await emitSocket(page, 'session:revoked', {})
    await page.locator('.ui-v2-connection-banner').waitFor({ state: 'visible', timeout: 15_000 })
    const layerMetrics = await page.evaluate(() => {
      const z = (selector) =>
        Number.parseInt(getComputedStyle(document.querySelector(selector)).zIndex, 10)
      const header = document.querySelector('.ui-v2-session-header')
      const title = header.querySelector('h2')
      const icon = header.querySelector('.ui-v2-session-icon')
      return {
        z: {
          security: z('.ui-v2-session-layer'),
          connection: z('.ui-v2-connection-banner'),
          toast: z('.ui-v2-toast-layer'),
          nav: z('.bottom-nav-wrapper'),
        },
        header: {
          background: getComputedStyle(header).backgroundColor,
          backgroundImage: getComputedStyle(header).backgroundImage,
          title: getComputedStyle(title).color,
          icon: getComputedStyle(icon).color,
        },
      }
    })
    assert.deepEqual(layerMetrics.z, {
      security: 10030,
      connection: 10020,
      toast: 10010,
      nav: 50,
    })
    assert.equal(layerMetrics.header.backgroundImage, 'none')
    const titleContrast = contrastRatio(
      parseRgb(layerMetrics.header.title),
      parseRgb(layerMetrics.header.background),
    )
    const iconContrast = contrastRatio(
      parseRgb(layerMetrics.header.icon),
      parseRgb(layerMetrics.header.background),
    )
    assert.ok(titleContrast >= 4.5)
    assert.ok(iconContrast >= 3)

    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('role')), 'dialog')
    await page.keyboard.press('Escape')
    assert.equal(await dialog.count(), 1, 'required security dialog dismissed by Escape')
    const actions = dialog.locator('button')
    const firstAction = actions.first()
    const lastAction = actions.last()
    await firstAction.focus()
    await page.keyboard.press('Shift+Tab')
    assert.equal(
      await page.evaluate(() => document.activeElement?.textContent?.trim()),
      await lastAction.textContent().then((text) => text?.trim()),
    )
    await lastAction.focus()
    await page.keyboard.press('Tab')
    assert.equal(
      await page.evaluate(() => document.activeElement?.textContent?.trim()),
      await firstAction.textContent().then((text) => text?.trim()),
    )
    await page.evaluate(() => document.querySelector('.ui-v2-bottom-nav-item')?.focus())
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('role')), 'dialog')
    await takeScreenshot(page, 'stage4-layer-coexistence-mobile-390.png')

    state.verifyMode = 'success'
    await firstAction.click()
    await dialog.waitFor({ state: 'hidden' })
    assert.equal(
      await page.evaluate(() => document.activeElement?.classList.contains('ui-v2-bottom-nav-item')),
      true,
    )

    const toastAction = page.getByRole('button', { name: 'باز کردن اعلان «لایه آزمون»' })
    await toastAction.waitFor({ state: 'visible' })
    await toastAction.focus()
    await page.clock.fastForward(6_000)
    assert.equal(await toastAction.count(), 1, 'focused toast expired')
    assert.equal(await toastAction.evaluate((element) => document.activeElement === element), true)
    await page.getByRole('button', { name: 'بستن اعلان', exact: true }).click()
    await page.locator('.ui-v2-toast-item', { hasText: 'لایه آزمون' }).waitFor({ state: 'hidden' })

    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'visible' })
    await context.setOffline(true)
    await page.evaluate(() => window.dispatchEvent(new Event('offline')))
    await assertPwaPromptIneligible(page, 'offline PWA layer proof', page.clock)
    await context.setOffline(false)
    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    await assertPwaPromptIneligible(page, 'online-but-stale Home', page.clock)
    const identityRefreshBefore = requestCount(state, '/api/auth/me', 'GET')
    await page.locator('.dashboard-identity-retry').click()
    await waitForRequestCount(
      page,
      state,
      '/api/auth/me',
      identityRefreshBefore + 1,
      'GET',
    )
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'visible' })
    await page.getByRole('button', { name: 'بعداً', exact: true }).click()
    assert.ok(
      await page.evaluate(() => Number(localStorage.getItem('pwa_install_prompt_dismissed_at_v2')) > 0),
    )
    await assertPwaPromptIneligible(page, 'dismissed Home prompt', page.clock)
    await dispatchInstallPrompt(page)
    assert.equal(await page.locator('.ui-v2-pwa-install').count(), 0)
    const dismissTtlMs = 24 * 60 * 60 * 1000
    await dispatchInstallPromptWithDismissAge(page, dismissTtlMs - 1)
    assert.equal(
      await page.locator('.ui-v2-pwa-install').count(),
      0,
      'PWA prompt reappeared one millisecond before dismiss TTL expiry',
    )
    await dispatchInstallPromptWithDismissAge(page, dismissTtlMs + 1)
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'visible' })

    await page.emulateMedia({ reducedMotion: 'reduce' })
    const motion = await page.evaluate(() => {
      const scope = document.querySelector('[data-ui-system="v2"]')
      const durations = [...document.querySelectorAll(
        '[data-ui-system="v2"] [data-ui-v2-motion], [data-ui-system="v2"] .ui-v2-bottom-nav-item, [data-ui-system="v2"] .ui-action-card, [data-ui-system="v2"] .ui-v2-notifications-item',
      )]
        .filter((element) => {
          const rect = element.getBoundingClientRect()
          return rect.width > 0 && rect.height > 0 && getComputedStyle(element).display !== 'none'
        })
        .map((element) => ({
          className: element.className?.toString?.() || element.tagName,
          transition: getComputedStyle(element).transitionDuration,
          animation: getComputedStyle(element).animationDuration,
        }))
      const style = getComputedStyle(scope)
      return {
        micro: style.getPropertyValue('--ui-v2-motion-micro').trim(),
        state: style.getPropertyValue('--ui-v2-motion-state').trim(),
        durations,
      }
    })
    assert.equal(motion.micro, '1ms')
    assert.equal(motion.state, '1ms')
    const seconds = (value) =>
      value.split(',').map((entry) => {
        const trimmed = entry.trim()
        return trimmed.endsWith('ms')
          ? Number.parseFloat(trimmed) / 1000
          : Number.parseFloat(trimmed)
      })
    for (const item of motion.durations) {
      for (const duration of [...seconds(item.transition), ...seconds(item.animation)]) {
        assert.ok(duration === 0 || duration <= 0.001, `${item.className}: motion ${duration}s`)
      }
    }

    await gotoPath(page, '/operations', { role: 'heading', name: 'عملیات' })
    await assertPwaPromptIneligible(page, 'non-Home route', page.clock)

    record('pwa-home-ready-only-offline-security-and-dismiss-ttl-gating', { actionSizes })
    record('layer-computed-order-security-connection-toast-nav', layerMetrics)
    record('session-modal-focus-trap-escape-and-restore')
    record('toast-lifetime-pauses-while-security-inert-or-keyboard-focused')
    record('session-modal-contrast', { titleContrast, iconContrast })
    record('reduced-motion-runtime-max-1ms', motion)
    return { layerMetrics, motion }
  } finally {
    await closeRuntime(runtime)
  }
}

async function runPrivateDesignSystemCatalog(browser, baseUrl) {
  const runtime = await createPage(browser, baseUrl, {
    authenticated: false,
    state: newRuntimeState(),
    suite: 'private-design-system-catalog',
    reducedMotion: 'reduce',
  })
  const { page } = runtime
  try {
    await gotoPath(page, '/login', { role: 'heading', name: 'ورود به سامانه' })
    await page.evaluate(async () => {
      const fixture = await import('/__stage4-private-catalog.js')
      fixture.mountStage4PrivateCatalog()
    })
    const catalog = page.locator('[data-test="ui-v2-catalog"]')
    await catalog.waitFor({ state: 'visible' })
    assert.equal(await catalog.getAttribute('data-private-catalog'), 'true')
    assert.equal(await catalog.getAttribute('data-ui-system'), 'v2')
    assert.equal(new URL(page.url()).pathname, '/login')
    assert.deepEqual(
      await catalog.locator('[data-catalog-state]').evaluateAll((rows) =>
        rows.map((row) => row.getAttribute('data-catalog-state')),
      ),
      ['normal', 'loading', 'disabled', 'error', 'destructive'],
    )
    assert.deepEqual(
      await catalog.locator('[data-test="semantic-token-grid"] [data-token]').evaluateAll((rows) =>
        rows.map((row) => row.getAttribute('data-token')),
      ),
      CATALOG_SEMANTIC_TOKEN_CONTRACT,
    )
    assert.equal(await catalog.locator('[data-test="typography-role-list"] [data-type-role]').count(), 10)
    assert.equal(await catalog.locator('[data-test="radius-role-grid"] [data-radius-role]').count(), 6)
    assert.equal(await catalog.locator('[data-test="icon-scale-contract"] [data-icon-size]').count(), 3)
    assert.equal(
      await catalog.locator('[data-catalog-state="loading"] button').isDisabled(),
      true,
    )
    assert.equal(
      await catalog.locator('[data-catalog-state="disabled"] button').isDisabled(),
      true,
    )
    const invalidInput = catalog.locator('[data-catalog-state="error"] input')
    assert.equal(await invalidInput.getAttribute('aria-invalid'), 'true')
    assert.ok(await invalidInput.getAttribute('aria-describedby'))

    const responsive = []
    for (const viewport of VIEWPORTS) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await waitForSettledPage(page)
      const metrics = await page.evaluate(() => {
        const root = document.querySelector('[data-test="ui-v2-catalog"]')
        const contracts = [...document.querySelectorAll('[data-overflow-contract]')].map(
          (element) => ({
            contract: element.getAttribute('data-overflow-contract'),
            clientWidth: element.clientWidth,
            scrollWidth: element.scrollWidth,
          }),
        )
        return {
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: innerWidth,
          rootClientWidth: root?.clientWidth || 0,
          rootScrollWidth: root?.scrollWidth || 0,
          contracts,
        }
      })
      assert.ok(metrics.documentWidth <= metrics.viewportWidth + 1, `${viewport.label}: catalog document overflow`)
      assert.ok(metrics.rootScrollWidth <= metrics.rootClientWidth + 1, `${viewport.label}: catalog root overflow`)
      assert.ok(
        metrics.contracts.every((entry) => entry.scrollWidth <= entry.clientWidth + 1),
        `${viewport.label}: catalog contract overflow`,
      )
      responsive.push({ viewport: viewport.label, metrics })
    }

    await page.setViewportSize({ width: 390, height: 844 })
    const focus = await focusProof(page, '.ui-v2-catalog__focus-proof', 'private catalog focus')
    const disclosure = catalog.locator('[data-test="reduced-motion-disclosure"]')
    await disclosure.locator('summary').focus()
    await page.keyboard.press('Enter')
    assert.equal(await disclosure.getAttribute('open'), '')
    const portalToggle = catalog.locator('[data-test="portal-proof-toggle"]')
    await portalToggle.click()
    const portal = page.locator('[data-test="catalog-portal-scope"]')
    await portal.waitFor({ state: 'visible' })
    assert.equal(await portal.getAttribute('data-ui-system'), 'v2-portal')
    assert.equal(await portal.getByRole('dialog').getAttribute('aria-modal'), 'false')
    await portal.getByRole('button', { name: 'انصراف', exact: true }).click()
    await portal.waitFor({ state: 'hidden' })
    const motion = await catalog.locator('[data-ui-v2-motion]').evaluateAll((elements) =>
      elements.map((element) => ({
        transition: getComputedStyle(element).transitionDuration,
        animation: getComputedStyle(element).animationDuration,
      })),
    )
    const durationSeconds = (value) =>
      value.split(',').map((entry) => {
        const trimmed = entry.trim()
        return trimmed.endsWith('ms')
          ? Number.parseFloat(trimmed) / 1000
          : Number.parseFloat(trimmed)
      })
    for (const row of motion) {
      for (const duration of [...durationSeconds(row.transition), ...durationSeconds(row.animation)]) {
        assert.ok(duration === 0 || duration <= 0.001, `catalog reduced motion ${duration}s`)
      }
    }
    const catalogScreenshot = path.join(OUTPUT_DIR, 'stage4-private-catalog-mobile-390.png')
    await page.screenshot({ path: catalogScreenshot, fullPage: true, animations: 'disabled' })
    screenshots.push({
      file: path.basename(catalogScreenshot),
      bytes: fs.statSync(catalogScreenshot).size,
      sha256: sha256File(catalogScreenshot),
      scrollContainer: 'document',
      scrollTop: 0,
    })
    assert.equal(runtime.state.requestLog.length, 0, 'private catalog issued a product API request')
    record('private-catalog-not-product-routed-and-state-contract-complete')
    record('private-catalog-8-width-overflow-focus-portal-and-reduced-motion', {
      responsive,
      focus,
      motion,
    })
    return { responsive, focus, motion }
  } finally {
    await closeRuntime(runtime)
  }
}

async function runProtectedRuntimeNoDrift(browser, baseUrl) {
  const state = newRuntimeState({
    user: { ...USERS.owner },
  })
  const runtime = await createPage(browser, baseUrl, {
    state,
    suite: 'protected-runtime-no-drift',
  })
  try {
    await runtime.page.setViewportSize({ width: 390, height: 844 })
    await gotoPath(runtime.page, '/market', { selector: '.market-page' })
    const protectedFab = runtime.page.locator('.fab-container')
    await protectedFab.waitFor({ state: 'visible', timeout: 30_000 })
    assert.equal(await runtime.page.locator('.app-route-v2-scope').count(), 0)
    assert.equal(await runtime.page.locator('.app-authenticated-shell-v2').count(), 0)
    assert.equal(await runtime.page.locator('.ui-v2-bottom-nav').count(), 0)
    assert.equal(await protectedFab.count(), 1)
    const pushToggle = runtime.page.locator('.market-notification-toggle')
    await pushToggle.waitFor({ state: 'visible' })
    assert.equal(await pushToggle.getAttribute('aria-pressed'), 'false')
    assert.equal(await pushToggle.getAttribute('aria-label'), 'روشن کردن اعلان آفرهای بازار')
    await pushToggle.click()
    await runtime.page.waitForFunction(() =>
      document.querySelector('.market-notification-toggle')?.getAttribute('aria-pressed') === 'true',
    )
    assert.equal(
      requestCount(state, '/api/notifications/preferences', 'PATCH'),
      1,
      'Market preference mutation drifted',
    )
    assert.equal(
      state.requestLog.find(
        (entry) => entry.pathname === '/api/notifications/preferences' && entry.method === 'PATCH',
      )?.postData,
      JSON.stringify({ market_offer_push_enabled: true }),
    )

    const marketTabs = runtime.page.locator('.market-filter-chips [role="tab"]')
    await marketTabs.first().waitFor({ state: 'visible' })
    assert.deepEqual(
      await marketTabs.allTextContents().then((rows) => rows.map((row) => row.trim())),
      ['همه', 'خریدار', 'فروشنده', 'لفظ‌های شما'],
    )
    const firstMarketTab = marketTabs.first()
    await firstMarketTab.focus()
    await firstMarketTab.press('ArrowLeft')
    assert.equal(await marketTabs.nth(1).getAttribute('aria-selected'), 'true')
    assert.equal(
      await firstMarketTab.evaluate((element) => document.activeElement === element),
      true,
      'protected Market filter focus behavior drifted',
    )
    await firstMarketTab.press('End')
    assert.equal(await marketTabs.nth(3).getAttribute('aria-selected'), 'true')
    assert.equal(await firstMarketTab.evaluate((element) => document.activeElement === element), true)
    assert.equal(await runtime.page.locator('.market-page [data-ui-system="v2"]').count(), 0)
    await takeScreenshot(runtime.page, 'stage4-protected-market-mobile-390.png')

    await gotoPath(runtime.page, '/chat', {
      selector: '.conversation-list-wrapper .empty-state',
    })
    await runtime.page.locator('.messenger-page').waitFor({ timeout: 30_000 })
    await runtime.page.locator('.chat-wrapper').waitFor({ timeout: 30_000 })
    await runtime.page.locator('.loading-state').waitFor({ state: 'hidden', timeout: 30_000 })
    assert.equal(await runtime.page.locator('.error-state').count(), 0)
    await waitForRequestCount(runtime.page, state, '/api/chat/conversations', 1, 'GET')
    await runtime.page.waitForTimeout(120)
    assert.equal(
      requestCount(state, '/api/chat/conversations', 'GET'),
      1,
      'protected Messenger initial conversation request drifted',
    )
    assert.equal(await runtime.page.locator('.app-route-v2-scope').count(), 0)
    assert.equal(await runtime.page.locator('.app-authenticated-shell-v2').count(), 0)
    assert.equal(await runtime.page.locator('.ui-v2-bottom-nav').count(), 0)
    assert.equal(await runtime.page.locator('.messenger-page [data-ui-system="v2"]').count(), 0)
    await takeScreenshot(runtime.page, 'stage4-protected-messenger-mobile-390.png')

    record('protected-market-messenger-runtime-remain-off-v2')
    record('protected-market-preference-and-filter-behavior-no-drift')
  } finally {
    await closeRuntime(runtime)
  }
}

const EXPECTED_HTTP_FAILURE_RULES = Object.freeze([
  { runSuite: 'home', suite: 'home-error', method: 'GET', pathname: '/api/auth/me', status: 400, min: 1, max: 4 },
  { runSuite: 'home', suite: 'home-stale', method: 'GET', pathname: '/api/auth/me', status: 400, min: 1, max: 4 },
  { runSuite: 'home', suite: 'home-reconnecting', method: 'GET', pathname: '/api/chat/poll', status: 503, min: 1, max: 50 },
  { runSuite: 'account', suite: 'security-confirmations', method: 'DELETE', pathname: '/api/sessions/other-phone', status: 400, min: 1, max: 1 },
  {
    runSuite: 'account',
    suite: 'security-logout-others-receipt',
    method: 'GET',
    pathname: '/api/sessions/active',
    status: 400,
    min: 1,
    max: 1,
  },
  {
    runSuite: 'account',
    suite: 'security-local-only-logout-receipt',
    method: 'DELETE',
    pathname: '/api/sessions/current-primary',
    status: 400,
    min: 1,
    max: 1,
  },
  {
    runSuite: 'push',
    suite: 'push-error',
    method: 'GET',
    pathname: '/api/notifications/push/public-key',
    status: 400,
    min: 1,
    max: 3,
  },
  {
    runSuite: 'push',
    suite: 'push-double-registration-failure-rollback',
    method: 'POST',
    pathname: '/api/notifications/push/subscription',
    status: 400,
    min: 2,
    max: 2,
  },
  {
    runSuite: 'notifications',
    suite: 'notification-initial-error',
    method: 'GET',
    pathname: '/api/notifications/',
    status: 400,
    min: 1,
    max: 2,
  },
  {
    runSuite: 'notifications',
    suite: 'notification-retained-error',
    method: 'GET',
    pathname: '/api/notifications/',
    status: 400,
    min: 1,
    max: 1,
  },
])

const EXPECTED_NON_HTTP_CONSOLE_RULES = Object.freeze([
  {
    runSuite: 'pwa',
    suite: 'pwa-layers-motion',
    pathname: '/api/sessions/verify',
    pattern: /net::ERR_FAILED/u,
    min: 0,
    max: 1,
  },
])

function matchingHttpFailureRule(row) {
  const pathname = new URL(row.url).pathname
  return EXPECTED_HTTP_FAILURE_RULES.find(
    (rule) =>
      rule.suite === row.suite &&
      rule.method === row.method &&
      rule.pathname === pathname &&
      rule.status === row.status,
  )
}

function consoleLocationPathname(row) {
  const value = row.location?.url
  if (!value) return null
  try {
    return new URL(value).pathname
  } catch {
    return null
  }
}

function classifyDiagnostics(selectedSuites = Object.keys(SUITES)) {
  const expectedHttpFailures = browserDiagnostics.httpFailures.filter(matchingHttpFailureRule)
  const unexpectedHttpFailures = browserDiagnostics.httpFailures.filter(
    (row) => !matchingHttpFailureRule(row),
  )
  const expectedHttpKeys = new Set()
  for (const row of expectedHttpFailures) {
    expectedHttpKeys.add(
      `${row.suite}\u0000${new URL(row.url).pathname}\u0000${row.status}`,
    )
  }
  const expectedConsoleErrors = []
  const unexpectedConsoleErrors = []
  const nonHttpConsoleCounts = new Map()
  for (const row of browserDiagnostics.consoleErrors) {
    const statusMatch = row.text.match(
      /^Failed to load resource: the server responded with a status of (\d+)/u,
    )
    const status = statusMatch ? Number(statusMatch[1]) : null
    const pathname = consoleLocationPathname(row)
    const httpKey = `${row.suite}\u0000${pathname}\u0000${status}`
    const nonHttpRule = EXPECTED_NON_HTTP_CONSOLE_RULES.find(
      (rule) =>
        rule.suite === row.suite &&
        rule.pathname === pathname &&
        rule.pattern.test(row.text),
    )
    if (status !== null && pathname && expectedHttpKeys.has(httpKey)) {
      expectedConsoleErrors.push(row)
    } else if (nonHttpRule) {
      expectedConsoleErrors.push(row)
      nonHttpConsoleCounts.set(nonHttpRule, (nonHttpConsoleCounts.get(nonHttpRule) || 0) + 1)
    } else {
      unexpectedConsoleErrors.push(row)
    }
  }
  const expectedRuleViolations = []
  for (const rule of EXPECTED_HTTP_FAILURE_RULES) {
    if (FOCUSED_DIAGNOSTIC_ACTIVE) continue
    if (!selectedSuites.includes(rule.runSuite)) continue
    const count = expectedHttpFailures.filter((row) => matchingHttpFailureRule(row) === rule).length
    if (count < rule.min || count > rule.max) {
      expectedRuleViolations.push({ type: 'http', rule, count })
    }
  }
  for (const rule of EXPECTED_NON_HTTP_CONSOLE_RULES) {
    if (FOCUSED_DIAGNOSTIC_ACTIVE) continue
    if (!selectedSuites.includes(rule.runSuite)) continue
    const count = nonHttpConsoleCounts.get(rule) || 0
    if (count < rule.min || count > rule.max) {
      expectedRuleViolations.push({ type: 'console', rule: { ...rule, pattern: String(rule.pattern) }, count })
    }
  }
  return {
    expectedHttpFailures,
    unexpectedHttpFailures,
    expectedConsoleErrors,
    unexpectedConsoleErrors,
    expectedRuleViolations,
  }
}

const SUITES = Object.freeze({
  catalog: runPrivateDesignSystemCatalog,
  responsive: runResponsiveDailyMatrix,
  home: runHomeStateMatrix,
  authority: runRoleAndIdentityAuthorityMatrix,
  account: runAccountSecurityStorageAndRedirects,
  push: runPushStateMatrix,
  notifications: runNotificationHistoryAndNavigation,
  pwa: runPwaLayersMotionAndKeyboard,
  protected: runProtectedRuntimeNoDrift,
})

async function startIsolatedVite() {
  const catalogVirtualId = '\0stage4-private-catalog'
  const vite = await createServer({
    root: FRONTEND,
    cacheDir: path.join(OUTPUT_DIR, 'vite-cache'),
    clearScreen: false,
    logLevel: 'error',
    plugins: [
      {
        name: 'stage4-private-catalog-fixture',
        resolveId(id) {
          return id === '/__stage4-private-catalog.js' ? catalogVirtualId : null
        },
        load(id) {
          if (id !== catalogVirtualId) return null
          return `
            import { createApp } from 'vue'
            import Catalog from '/src/components/ui/AppDesignSystemCatalog.vue'
            export function mountStage4PrivateCatalog() {
              const productRoot = document.querySelector('#app')
              if (productRoot instanceof HTMLElement) productRoot.style.display = 'none'
              document.documentElement.style.overflow = 'auto'
              document.body.style.overflow = 'auto'
              document.body.style.height = 'auto'
              const host = document.createElement('div')
              host.id = 'stage4-private-catalog-host'
              document.body.appendChild(host)
              createApp(Catalog).mount(host)
            }
          `
        },
      },
    ],
    server: {
      host: '127.0.0.1',
      port: 0,
      strictPort: false,
      fs: {
        allow: [FRONTEND, FRONTEND_NODE_MODULES],
      },
    },
  })
  await vite.listen()
  const address = vite.httpServer?.address()
  assert.ok(address && typeof address === 'object', 'Vite did not expose an ephemeral port')
  return { vite, baseUrl: `http://127.0.0.1:${address.port}` }
}

async function main() {
  const startedAt = new Date().toISOString()
  const allSuiteNames = Object.keys(SUITES)
  const selectedSuites = ONLY_SUITE ? [ONLY_SUITE] : allSuiteNames
  assert.ok(
    (!SECURITY_360_DIAGNOSTIC &&
      !HOME_FOCUS_DIAGNOSTIC &&
      !HOME_KEYBOARD_DIAGNOSTIC &&
      !NOTIFICATIONS_CONTRAST_DIAGNOSTIC &&
      !HOME_OFFLINE_PWA_DIAGNOSTIC &&
      !HOME_ACCOUNTANT_PWA_DIAGNOSTIC) ||
      (HOME_OFFLINE_PWA_DIAGNOSTIC || HOME_ACCOUNTANT_PWA_DIAGNOSTIC
        ? ONLY_SUITE === 'home'
        : ONLY_SUITE === 'responsive'),
    'Stage 4 focused diagnostic suite selection mismatch',
  )
  assert.equal(
    [
      SECURITY_360_DIAGNOSTIC,
      HOME_FOCUS_DIAGNOSTIC,
      HOME_KEYBOARD_DIAGNOSTIC,
      NOTIFICATIONS_CONTRAST_DIAGNOSTIC,
      HOME_OFFLINE_PWA_DIAGNOSTIC,
      HOME_ACCOUNTANT_PWA_DIAGNOSTIC,
    ].filter(Boolean).length > 1,
    false,
    'Stage 4 focused diagnostics are mutually exclusive',
  )
  const isFullRun =
    !ONLY_SUITE &&
    selectedSuites.length === allSuiteNames.length &&
    selectedSuites.every((suite, index) => suite === allSuiteNames[index])
  for (const suite of selectedSuites) {
    assert.ok(Object.hasOwn(SUITES, suite), `Unknown STAGE4_BROWSER_ONLY suite: ${suite}`)
  }

  const planBefore = sourcePlanSnapshot()
  assert.equal(
    planBefore.sha256,
    sha256(SOURCE_PLAN_INITIAL_BYTES),
    'source plan changed between parse and run bootstrap',
  )
  const harnessBefore = harnessSnapshot()
  assert.equal(
    harnessBefore.sha256,
    sha256(HARNESS_INITIAL_BYTES),
    'Stage 4 harness changed between parse and run bootstrap',
  )
  const environmentBefore = environmentSnapshot()
  assert.equal(
    environmentBefore.some((entry) => entry.exists),
    false,
    'an unbound Vite environment file appeared before browser launch',
  )
  const sourceBefore = sourceSnapshot()
  const sourceBindingSha256 = sha256(JSON.stringify(sourceBefore))
  assert.equal(
    sourcePlan.finalBinding.sourceFileCount,
    sourceBefore.length,
    'source-final plan file count mismatch',
  )
  assert.equal(
    sourcePlan.finalBinding.sourceBindingSha256,
    sourceBindingSha256,
    'source-final plan hash does not match current source',
  )
  assert.equal(
    sourcePlan.finalBinding.protectedBaselinesSha256,
    sha256(JSON.stringify(sourcePlan.protectedBaselines)),
    'source-final protected baseline binding mismatch',
  )
  const protectedBefore = await protectedEvidence({ recordAssertion: true })
  let browser = null
  let vite = null
  let baseUrl = null
  let browserVersion = null
  let sourceAfter = null
  let protectedAfter = null
  let planAfter = null
  let harnessAfter = null
  let environmentAfter = null
  let diagnostics = null
  let failure = null
  const suiteResults = {}

  try {
    const local = await startIsolatedVite()
    vite = local.vite
    baseUrl = local.baseUrl
    progress('vite-ready', { baseUrl, selectedSuites })

    browser = await chromium.launch({
      headless: true,
      args: ['--disable-dev-shm-usage'],
    })
    browserVersion = browser.version()
    await warmVite(browser, baseUrl)

    for (const suite of selectedSuites) {
      progress('suite-start', { suite })
      suiteResults[suite] = await SUITES[suite](browser, baseUrl)
      progress('suite-complete', { suite })
    }

    const protectedBeforeClose = await protectedEvidence({ recordAssertion: false })
    assert.deepEqual(
      protectedBeforeClose,
      protectedBefore,
      'protected Market/Messenger/Home evidence changed during browser run',
    )
    const sourceBeforeClose = sourceSnapshot()
    assert.deepEqual(
      sourceBeforeClose,
      sourceBefore,
      'source bytes, mtime, or hash changed during browser run',
    )

    diagnostics = classifyDiagnostics(selectedSuites)
    assert.deepEqual(diagnostics.unexpectedHttpFailures, [])
    assert.deepEqual(diagnostics.unexpectedConsoleErrors, [])
    assert.deepEqual(diagnostics.expectedRuleViolations, [])
    assert.deepEqual(browserDiagnostics.pageErrors, [])
    assert.deepEqual(browserDiagnostics.unexpectedRequestFailures, [])
    assert.deepEqual(browserDiagnostics.blockedExternalRequests, [])
    assert.deepEqual(browserDiagnostics.unexpectedApiRequests, [])
    assert.deepEqual(browserDiagnostics.unexpectedWebSockets, [])
    assert.ok(
      browserDiagnostics.webSockets.some((entry) => entry.kind === 'app-realtime' && entry.valid),
      'no valid application realtime WebSocket was observed',
    )
    record('browser-unexpected-diagnostics-zero', {
      expectedHttpFailures: diagnostics.expectedHttpFailures.length,
      expectedConsoleErrors: diagnostics.expectedConsoleErrors.length,
    })
    record('source-hash-mtime-pre-post-identical', { files: sourceBefore.length })
    record('protected-evidence-pre-post-identical')
  } catch (error) {
    failure = {
      name: error instanceof Error ? error.name : 'Error',
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : null,
    }
    throw error
  } finally {
    const teardownErrors = []
    if (browser) {
      try {
        await browser.close()
      } catch (error) {
        teardownErrors.push({
          target: 'chromium',
          message: error instanceof Error ? error.message : String(error),
        })
      }
    }
    if (vite) {
      try {
        await vite.close()
      } catch (error) {
        teardownErrors.push({
          target: 'vite',
          message: error instanceof Error ? error.message : String(error),
        })
      }
    }
    if (teardownErrors.length > 0) {
      if (failure) {
        failure.teardownErrors = teardownErrors
      } else {
        failure = {
          name: 'Stage4TeardownError',
          message: 'Chromium or Vite did not close cleanly before the final binding snapshot.',
          stack: null,
          teardownErrors,
        }
      }
    }
    try {
      sourceAfter = sourceSnapshot()
    } catch (error) {
      sourceAfter = { error: error instanceof Error ? error.message : String(error) }
    }
    try {
      protectedAfter = await protectedEvidence({ recordAssertion: false })
    } catch (error) {
      protectedAfter = { error: error instanceof Error ? error.message : String(error) }
    }
    try {
      planAfter = sourcePlanSnapshot()
    } catch (error) {
      planAfter = { error: error instanceof Error ? error.message : String(error) }
    }
    try {
      harnessAfter = harnessSnapshot()
    } catch (error) {
      harnessAfter = { error: error instanceof Error ? error.message : String(error) }
    }
    try {
      environmentAfter = environmentSnapshot()
    } catch (error) {
      environmentAfter = { error: error instanceof Error ? error.message : String(error) }
    }
    if (!diagnostics) diagnostics = classifyDiagnostics(selectedSuites)

    const sourceIdentical = JSON.stringify(sourceAfter) === JSON.stringify(sourceBefore)
    const protectedIdentical = JSON.stringify(protectedAfter) === JSON.stringify(protectedBefore)
    const planIdentical = JSON.stringify(planAfter) === JSON.stringify(planBefore)
    const harnessIdentical = JSON.stringify(harnessAfter) === JSON.stringify(harnessBefore)
    const environmentIdentical =
      JSON.stringify(environmentAfter) === JSON.stringify(environmentBefore) &&
      Array.isArray(environmentAfter) &&
      !environmentAfter.some((entry) => entry.exists)
    if (
      !failure &&
      (!sourceIdentical ||
        !protectedIdentical ||
        !planIdentical ||
        !harnessIdentical ||
        !environmentIdentical)
    ) {
      failure = {
        name: 'Stage4BindingIntegrityError',
        message: 'Source, protected evidence, source plan, harness, or Vite environment changed before final post-close snapshot.',
        stack: null,
      }
    }
    const promotable = Boolean(
      !failure &&
      isFullRun &&
      sourceIdentical &&
      protectedIdentical &&
      planIdentical &&
      harnessIdentical &&
      environmentIdentical,
    )
    const completedAt = new Date().toISOString()
    const metrics = {
      schemaVersion: 1,
      stage: 4,
      status: failure ? 'failed' : isFullRun ? 'passed' : 'partial-passed',
      promotable,
      runAuthorization: RUN_AUTHORIZATION,
      runId: RUN_ID,
      startedAt,
      completedAt,
      browser: {
        name: 'chromium',
        version: browserVersion,
        headless: true,
      },
      runtimeVersions: RUNTIME_VERSIONS,
      runtimeEnvironment: RUNTIME_ENVIRONMENT,
      sanitizedEnvironmentKeys: SANITIZED_ENV_KEYS,
      baseUrl,
      selectedSuites,
      requiredViewports: VIEWPORTS,
      sourceBinding: {
        plan: SOURCE_PLAN_PATH,
        planPre: planBefore,
        planPost: planAfter,
        planIdentical,
        harness: HARNESS_PATH,
        harnessPre: harnessBefore,
        harnessPost: harnessAfter,
        harnessIdentical,
        environmentPre: environmentBefore,
        environmentPost: environmentAfter,
        environmentIdentical,
        pre: sourceBefore,
        post: sourceAfter,
        sha256: sourceBindingSha256,
        identical: sourceIdentical,
      },
      protectedBinding: {
        pre: protectedBefore,
        post: protectedAfter,
        identical: protectedIdentical,
      },
      assertionSummary: {
        total: assertions.length,
        passed: assertions.filter((entry) => entry.passed).length,
        failed: failure ? 1 : 0,
      },
      assertions,
      suiteResults,
      screenshots,
      diagnostics: {
        ...browserDiagnostics,
        ...diagnostics,
      },
      failure,
    }
    fs.writeFileSync(METRICS_PATH, `${JSON.stringify(metrics, null, 2)}\n`)
    const bindingArtifact = {
      schemaVersion: 1,
      stage: 4,
      status: metrics.status,
      promotable,
      runId: RUN_ID,
      selectedSuites,
      sourceBindingSha256,
      sourceIdentical,
      protectedIdentical,
      planIdentical,
      harnessIdentical,
      harnessSha256: harnessBefore.sha256,
      runtimeVersions: RUNTIME_VERSIONS,
      runtimeEnvironment: RUNTIME_ENVIRONMENT,
      environmentIdentical,
      failure,
      source: sourceBefore,
      protected: protectedBefore,
    }
    fs.writeFileSync(
      path.join(
        OUTPUT_DIR,
        promotable
          ? 'stage4-final-source-binding.json'
          : 'stage4-non-promotable-source-binding.json',
      ),
      `${JSON.stringify(bindingArtifact, null, 2)}\n`,
    )
    progress('run-finished', {
      status: metrics.status,
      promotable,
      metrics: METRICS_PATH,
      assertions: assertions.length,
      screenshots: screenshots.length,
    })
    if (failure) process.exitCode = 1
  }
}

await main()
