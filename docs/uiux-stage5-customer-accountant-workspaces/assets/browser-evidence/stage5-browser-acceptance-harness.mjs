#!/usr/bin/env node

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
const EVIDENCE_DIR = path.dirname(fileURLToPath(import.meta.url))
const HARNESS_PATH = fileURLToPath(import.meta.url)
const RUN_AUTHORIZATION = 'STAGE5 CUSTOMER ACCOUNTANT SOURCE FINAL — RUN'
const FIXED_BROWSER_TIME = '2026-08-11T08:00:00.000Z'
const FIXED_BROWSER_EPOCH_SECONDS = Math.floor(Date.parse(FIXED_BROWSER_TIME) / 1000)
const RUN_ID = `uiux-stage5-browser-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join(EVIDENCE_DIR, 'runs', RUN_ID)
const METRICS_PATH = path.join(OUTPUT_DIR, 'stage5-browser-acceptance-metrics.json')
const VITE_CACHE_DIR = path.join(tmpdir(), `${RUN_ID}-vite-cache`)
const ONLY_SUITE = process.env.STAGE5_BROWSER_ONLY?.trim() || ''
const DIAGNOSTIC_MODE = process.env.STAGE5_BROWSER_DIAGNOSTIC === '1'

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
const BREAKPOINT_VIEWPORTS = Object.freeze([
  { label: 'breakpoint-899', width: 899, height: 900 },
  { label: 'breakpoint-900', width: 900, height: 900 },
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

function walkSourceTree(relativeRoot) {
  const absoluteRoot = path.join(WORKTREE, relativeRoot)
  assert.ok(fs.statSync(absoluteRoot).isDirectory(), `Source tree missing: ${relativeRoot}`)
  const pending = [absoluteRoot]
  const files = []
  while (pending.length > 0) {
    const directory = pending.pop()
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolutePath = path.join(directory, entry.name)
      if (entry.isDirectory()) pending.push(absolutePath)
      else if (entry.isFile()) {
        files.push(path.relative(WORKTREE, absolutePath).split(path.sep).join('/'))
      } else {
        throw new Error(`Unsupported source-tree entry: ${absolutePath}`)
      }
    }
  }
  return files
}

function resolveSourceFiles() {
  return [...new Set([
    ...SOURCE_DIRECT_FILES,
    ...SOURCE_TREES.flatMap(walkSourceTree),
  ])].sort()
}

function sourceSnapshot() {
  return resolveSourceFiles().map((relativePath) => {
    const absolutePath = path.join(WORKTREE, relativePath)
    const stat = fs.statSync(absolutePath)
    return {
      path: relativePath,
      bytes: stat.size,
      sha256: sha256File(absolutePath),
    }
  })
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

function gitSnapshot() {
  // Ignored evidence stays outside Git status, while every nonignored source
  // file must belong to the bound implementation commit.
  const trackedStatus = gitText(['status', '--porcelain=v1', '--untracked-files=all'])
  return {
    branch: gitText(['branch', '--show-current']),
    commit: gitText(['rev-parse', 'HEAD']),
    tree: gitText(['rev-parse', 'HEAD^{tree}']),
    trackedClean: trackedStatus === '',
    trackedStatus,
  }
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

const SOURCE_INITIAL = sourceSnapshot()
const SOURCE_BINDING_SHA256 = sha256(JSON.stringify(SOURCE_INITIAL))
const GIT_INITIAL = gitSnapshot()
const HARNESS_INITIAL = fileSnapshot(HARNESS_PATH)

if (process.argv.includes('--print-source-binding')) {
  process.stdout.write(`${JSON.stringify({
    schemaVersion: 1,
    stage: 5,
    branch: GIT_INITIAL.branch,
    commit: GIT_INITIAL.commit,
    tree: GIT_INITIAL.tree,
    trackedClean: GIT_INITIAL.trackedClean,
    sourceFileCount: SOURCE_INITIAL.length,
    sourceBindingSha256: SOURCE_BINDING_SHA256,
    harnessSha256: HARNESS_INITIAL.sha256,
  }, null, 2)}\n`)
  process.exit(0)
}

assert.equal(
  process.env.STAGE5_BROWSER_AUTHORIZATION,
  RUN_AUTHORIZATION,
  `Browser execution is locked. STAGE5_BROWSER_AUTHORIZATION must exactly equal ${JSON.stringify(RUN_AUTHORIZATION)}.`,
)
assert.equal(
  process.env.STAGE5_EXPECTED_SOURCE_SHA256,
  SOURCE_BINDING_SHA256,
  'STAGE5_EXPECTED_SOURCE_SHA256 does not match the live Stage 5 frontend source binding.',
)
assert.equal(
  process.env.STAGE5_EXPECTED_COMMIT,
  GIT_INITIAL.commit,
  'STAGE5_EXPECTED_COMMIT does not match the current implementation commit.',
)
assert.equal(GIT_INITIAL.branch, EXPECTED_BRANCH, 'Stage 5 browser evidence is on the wrong branch.')
if (DIAGNOSTIC_MODE) {
  assert.ok(ONLY_SUITE, 'Dirty-tree diagnostic mode requires one STAGE5_BROWSER_ONLY suite.')
} else {
  assert.equal(GIT_INITIAL.trackedClean, true, 'Tracked worktree changes must be committed before capture.')
}

const SANITIZED_ENV_KEYS = Object.keys(process.env)
  .filter((key) => key.startsWith('VITE_') || SANITIZED_ENV_EXACT_KEYS.includes(key))
  .sort()
for (const key of SANITIZED_ENV_KEYS) delete process.env[key]
process.env.NODE_ENV = 'development'

const ENVIRONMENT_INITIAL = environmentSnapshot()
assert.equal(
  ENVIRONMENT_INITIAL.some((entry) => entry.exists),
  false,
  'Unbound Vite environment files are forbidden during browser evidence capture.',
)

const require = createRequire(path.join(FRONTEND, 'package.json'))
const FRONTEND_NODE_MODULES = fs.realpathSync(path.join(FRONTEND, 'node_modules'))
const RUNTIME_VERSIONS = Object.freeze({
  node: process.versions.node,
  playwright: require('playwright/package.json').version,
  vite: require('vite/package.json').version,
  vazirmatn: require('vazirmatn/package.json').version,
})
const { chromium } = require('playwright')
const viteEntry = require.resolve('vite')
const { createServer } = await import(pathToFileURL(viteEntry).href)

fs.mkdirSync(OUTPUT_DIR, { recursive: true })

const OWNER = Object.freeze({
  id: 9501,
  account_name: 'stage5_owner',
  full_name: 'مالک مرحله پنج',
  role: 'کاربر',
  account_status: 'active',
  is_accountant: false,
  is_customer: false,
  customer_tier: null,
  can_connect_telegram: false,
  telegram_linked: false,
})

function customerRelation(overrides) {
  return {
    id: 5102,
    owner_user_id: OWNER.id,
    customer_user_id: 6102,
    customer_account_name: 'customer_finance',
    invitation_account_name: null,
    mobile_number: '09121234567',
    management_name: 'مشتری مالی',
    customer_tier: 'tier2',
    commission_rate: 0.5,
    min_trade_quantity: 10,
    max_trade_quantity: 500,
    max_daily_trades: 4,
    max_daily_commodity_volume: 1000,
    status: 'active',
    registration_link: null,
    bot_registration_link: null,
    web_registration_link: null,
    web_short_link: null,
    sms_status: null,
    expires_at: null,
    activated_at: '2026-08-01T08:00:00.000Z',
    deleted_at: null,
    created_at: '2026-07-20T08:00:00.000Z',
    ...overrides,
  }
}

function accountantRelation(overrides) {
  return {
    id: 5202,
    owner_user_id: OWNER.id,
    accountant_user_id: 6202,
    accountant_account_name: 'accountant_ops',
    global_account_name: 'accountant_ops',
    relation_display_name: 'حسابدار عملیات',
    duty_description: 'ثبت و پیگیری معاملات روزانه',
    mobile_number: '09123334455',
    status: 'active',
    registration_link: null,
    bot_registration_link: null,
    web_registration_link: null,
    web_short_link: null,
    sms_status: null,
    expires_at: '2027-08-11T08:00:00.000Z',
    activated_at: '2026-08-01T08:00:00.000Z',
    deleted_at: null,
    created_at: '2026-07-20T08:00:00.000Z',
    ...overrides,
  }
}

function defaultCustomerRelations() {
  const rows = [
    customerRelation({
      id: 5101,
      customer_user_id: null,
      customer_account_name: null,
      invitation_account_name: 'customer_pending',
      management_name: 'دعوت مشتری آزمایشی',
      mobile_number: '09120001122',
      customer_tier: 'tier1',
      commission_rate: null,
      status: 'pending',
      web_registration_link: 'https://example.invalid/i/customer-stage5-web',
      bot_registration_link: 'https://t.me/stage5_bot?start=customer-stage5',
      sms_status: 'sent',
      expires_at: '2027-08-11T12:00:00.000Z',
      activated_at: null,
      created_at: '2026-08-10T09:00:00.000Z',
    }),
    customerRelation({}),
    customerRelation({
      id: 5103,
      customer_user_id: null,
      customer_account_name: null,
      management_name: 'رابطه مشتری بدون حساب',
      mobile_number: '09124445566',
      customer_tier: 'tier1',
      commission_rate: null,
      status: 'active',
      activated_at: '2026-08-02T08:00:00.000Z',
    }),
  ]
  for (let index = 0; index < 12; index += 1) {
    rows.push(customerRelation({
      id: 5110 + index,
      customer_user_id: 6110 + index,
      customer_account_name: `customer_${index + 1}`,
      management_name: `مشتری نمونه ${index + 1}`,
      mobile_number: `0912777${String(index).padStart(4, '0')}`,
      created_at: `2026-07-${String(19 - index).padStart(2, '0')}T08:00:00.000Z`,
    }))
  }
  return rows
}

function defaultAccountantRelations() {
  const rows = [
    accountantRelation({
      id: 5201,
      accountant_user_id: null,
      accountant_account_name: null,
      global_account_name: 'accountant_pending',
      relation_display_name: 'دعوت حسابدار آزمایشی',
      duty_description: 'آماده‌سازی گزارش روزانه',
      mobile_number: '09121112233',
      status: 'pending',
      web_registration_link: 'https://example.invalid/i/accountant-stage5-web',
      bot_registration_link: 'https://t.me/stage5_bot?start=accountant-stage5',
      sms_status: 'sent',
      expires_at: '2027-08-11T12:00:00.000Z',
      activated_at: null,
      created_at: '2026-08-10T09:00:00.000Z',
    }),
    accountantRelation({}),
    accountantRelation({
      id: 5203,
      accountant_user_id: null,
      accountant_account_name: null,
      global_account_name: 'accountant_orphan',
      relation_display_name: 'رابطه حسابدار بدون حساب',
      duty_description: 'ثبت قدیمی',
      status: 'active',
    }),
  ]
  for (let index = 0; index < 12; index += 1) {
    rows.push(accountantRelation({
      id: 5210 + index,
      accountant_user_id: 6210 + index,
      accountant_account_name: `accountant_${index + 1}`,
      global_account_name: `accountant_${index + 1}`,
      relation_display_name: `حسابدار نمونه ${index + 1}`,
      duty_description: `وظیفه نمونه ${index + 1}`,
      mobile_number: `0912888${String(index).padStart(4, '0')}`,
      created_at: `2026-07-${String(19 - index).padStart(2, '0')}T08:00:00.000Z`,
    }))
  }
  return rows
}

function session(id, name, platform, primary) {
  return {
    id,
    device_name: name,
    device_ip: '198.51.100.10',
    platform,
    home_server: `hidden-${id}-origin`,
    is_primary: primary,
    is_active: true,
    created_at: '2026-08-01T08:00:00.000Z',
    last_active_at: '2026-08-11T07:30:00.000Z',
  }
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

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function newRuntimeState(overrides = {}) {
  return {
    suite: 'unassigned',
    customerRelations: defaultCustomerRelations(),
    accountantRelations: defaultAccountantRelations(),
    customerDetailRelations: {
      5104: customerRelation({
        id: 5104,
        customer_user_id: 6104,
        customer_account_name: 'customer_archived',
        management_name: 'مشتری پایان‌یافته',
        mobile_number: '09125556677',
        status: 'expired',
        deleted_at: null,
      }),
    },
    accountantDetailRelations: {
      5204: accountantRelation({
        id: 5204,
        accountant_user_id: 6204,
        accountant_account_name: 'accountant_archived',
        global_account_name: 'accountant_archived',
        relation_display_name: 'حسابدار پایان‌یافته',
        status: 'revoked',
        deleted_at: '2026-08-08T08:00:00.000Z',
      }),
    },
    deletedCustomerRelationIds: new Set(),
    deletedAccountantRelationIds: new Set(),
    customerSessions: {
      5102: [
        session('customer-primary', 'Chrome اصلی مشتری', 'web', true),
        session('customer-phone', 'گوشی مشتری', 'mobile', false),
      ],
    },
    accountantSessions: {
      5202: [
        session('accountant-primary', 'Chrome اصلی حسابدار', 'web', true),
        session('accountant-phone', 'گوشی حسابدار', 'mobile', false),
      ],
    },
    customerSessionFailuresRemaining: 0,
    accountantSessionFailuresRemaining: 0,
    customerCreateGate: null,
    accountantCreateGate: null,
    customerDeleteGate: null,
    accountantDeleteGate: null,
    requestLog: [],
    expectedHttpFailures: [],
    websocketEvents: [],
    eventSourceEvents: [],
    intentionalNavigation: false,
    intentionalClose: false,
    authToken: null,
    ...overrides,
  }
}

function createJwt(payload) {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url')
  return `${header}.${body}.stage5-browser`
}

function parseJsonBody(request, label) {
  const postData = request.postData()
  assert.ok(postData, `${label}: JSON request body is required.`)
  let payload
  try {
    payload = JSON.parse(postData)
  } catch {
    assert.fail(`${label}: request body is not JSON.`)
  }
  assert.ok(payload && typeof payload === 'object' && !Array.isArray(payload), `${label}: object required.`)
  return payload
}

function fulfillJson(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    headers: { 'Cache-Control': 'no-store', Pragma: 'no-cache' },
    body: body === null ? 'null' : JSON.stringify(body),
  })
}

function expectedFailure(route, state, body, status) {
  const request = route.request()
  const pathname = new URL(request.url()).pathname
  state.expectedHttpFailures.push({ method: request.method(), pathname, status })
  return fulfillJson(route, body, status)
}

function relationById(rows, id) {
  return rows.find((row) => row.id === Number(id)) || null
}

function methodContract(pathname) {
  const exact = {
    '/api/auth/me': ['GET'],
    '/api/auth/refresh': ['POST'],
    '/api/auth/switchable-users': ['GET'],
    '/api/sessions/verify': ['POST'],
    '/api/sessions/recovery/pending': ['GET'],
    '/api/sessions/login-requests/pending': ['GET'],
    '/api/sessions/active': ['GET'],
    '/api/chat/poll': ['GET'],
    '/api/chat/conversations': ['GET'],
    '/api/notifications/': ['GET'],
    '/api/notifications/unread-count': ['GET'],
    '/api/notifications/preferences': ['GET', 'PATCH'],
    '/api/notifications/push/public-key': ['GET'],
    '/api/config': ['GET'],
    '/api/invitations/pending': ['GET'],
    '/api/customers/owner-relations': ['GET', 'POST'],
    '/api/accountants/owner-relations': ['GET', 'POST'],
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
  }
  if (exact[pathname]) return exact[pathname]
  if (/^\/api\/customers\/owner-relations\/\d+$/u.test(pathname)) return ['GET', 'PATCH', 'DELETE']
  if (/^\/api\/customers\/owner-relations\/\d+\/sessions$/u.test(pathname)) return ['GET']
  if (/^\/api\/customers\/owner-relations\/\d+\/sessions\/[^/]+$/u.test(pathname)) return ['DELETE']
  if (/^\/api\/customers\/owner-relations\/\d+\/trade-stats$/u.test(pathname)) return ['GET']
  if (/^\/api\/trades\/with\/\d+$/u.test(pathname)) return ['GET']
  if (/^\/api\/accountants\/owner-relations\/\d+$/u.test(pathname)) return ['GET', 'PATCH', 'DELETE']
  if (/^\/api\/accountants\/owner-relations\/\d+\/sessions$/u.test(pathname)) return ['GET']
  if (/^\/api\/accountants\/owner-relations\/\d+\/sessions\/[^/]+$/u.test(pathname)) return ['DELETE']
  if (/^\/api\/users-public\//u.test(pathname)) return ['GET']
  return null
}

const diagnostics = {
  consoleErrors: [],
  pageErrors: [],
  httpFailures: [],
  unexpectedRequestFailures: [],
  externalRequestsBlocked: [],
  externalRequestsAllowed: [],
  unexpectedApiRequests: [],
  webSockets: [],
  unexpectedWebSockets: [],
  eventSources: [],
  unexpectedEventSources: [],
}

async function handleApiRoute(route, state) {
  const request = route.request()
  const url = new URL(request.url())
  const pathname = url.pathname
  const method = request.method()
  state.requestLog.push({ pathname, search: url.search, method, postData: request.postData() || '' })

  const allowed = methodContract(pathname)
  if (allowed && !allowed.includes(method)) {
    diagnostics.unexpectedApiRequests.push({ suite: state.suite, pathname, method, allowed })
    return fulfillJson(route, { detail: 'stage5_method_contract_violation' }, 405)
  }

  if (pathname === '/api/auth/me') return fulfillJson(route, OWNER)
  if (pathname === '/api/auth/refresh') {
    return fulfillJson(route, {
      access_token: state.authToken,
      refresh_token: state.authToken,
    })
  }
  if (pathname === '/api/auth/switchable-users') return fulfillJson(route, [])
  if (pathname === '/api/sessions/verify') return fulfillJson(route, { ok: true })
  if (pathname === '/api/sessions/recovery/pending') return fulfillJson(route, [])
  if (pathname === '/api/sessions/login-requests/pending') return fulfillJson(route, [])
  if (pathname === '/api/sessions/active') return fulfillJson(route, [])
  if (pathname === '/api/chat/poll') {
    return fulfillJson(route, {
      conversations_with_unread: [],
      muted_conversation_ids: [],
      unread_chats_count: 0,
      total_unread_mentions: 0,
    })
  }
  if (pathname === '/api/chat/conversations') return fulfillJson(route, [])
  if (pathname === '/api/notifications/') return fulfillJson(route, [])
  if (pathname === '/api/notifications/unread-count') return fulfillJson(route, 0)
  if (pathname === '/api/notifications/preferences') {
    return fulfillJson(route, { market_offer_push_enabled: false })
  }
  if (pathname === '/api/notifications/push/public-key') {
    return fulfillJson(route, { enabled: false, public_key: null, missing: ['VAPID_PUBLIC_KEY'] })
  }
  if (pathname === '/api/config') return fulfillJson(route, { bot_username: 'stage5_fixture_bot' })
  if (pathname === '/api/invitations/pending') return fulfillJson(route, [])
  if (pathname === '/api/offers/page') {
    return fulfillJson(route, { items: [], next_cursor: null, has_more: false, page_size: 0 })
  }
  if (['/api/offers/my', '/api/offers/my/repeatable', '/api/offers/market-history', '/api/trades/my'].includes(pathname)) {
    return fulfillJson(route, [])
  }
  if (pathname === '/api/commodities/') {
    return fulfillJson(route, [{ id: 1, name: 'طلای آب‌شده', aliases: [] }])
  }
  if (pathname === '/api/trading-settings/') {
    return fulfillJson(route, {
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
    return fulfillJson(route, {
      is_open: true,
      active_web_notice_visible: false,
      offers_since_last_open: 0,
      last_transition_at: null,
      next_transition_at: null,
    })
  }
  if (pathname === '/api/trading-settings/market-overrides') return fulfillJson(route, [])
  if (pathname === '/api/admin-messages/market/current') return fulfillJson(route, null)
  if (pathname.startsWith('/api/users-public/')) return fulfillJson(route, [])

  if (pathname === '/api/customers/owner-relations' && method === 'GET') {
    return fulfillJson(route, clone(state.customerRelations))
  }
  if (pathname === '/api/customers/owner-relations' && method === 'POST') {
    const payload = parseJsonBody(request, 'create customer relation')
    if (state.customerCreateGate) await state.customerCreateGate.promise
    const created = customerRelation({
      id: 5199,
      customer_user_id: null,
      customer_account_name: null,
      invitation_account_name: String(payload.account_name || ''),
      management_name: String(payload.management_name || ''),
      mobile_number: String(payload.mobile_number || ''),
      customer_tier: payload.customer_tier === 'tier2' ? 'tier2' : 'tier1',
      commission_rate: payload.customer_tier === 'tier2' ? Number(payload.commission_rate) : null,
      min_trade_quantity: payload.min_trade_quantity ?? null,
      max_trade_quantity: payload.max_trade_quantity ?? null,
      max_daily_trades: payload.max_daily_trades ?? null,
      max_daily_commodity_volume: payload.max_daily_commodity_volume ?? null,
      status: 'pending',
      web_registration_link: 'https://example.invalid/i/new-customer',
      bot_registration_link: 'https://t.me/stage5_bot?start=new-customer',
      sms_status: 'sent',
      expires_at: '2027-08-12T12:00:00.000Z',
      activated_at: null,
      created_at: '2026-08-11T08:00:00.000Z',
    })
    state.customerRelations = [created, ...state.customerRelations]
    return fulfillJson(route, clone(created), 201)
  }
  const customerRelationMatch = pathname.match(/^\/api\/customers\/owner-relations\/(\d+)$/u)
  if (customerRelationMatch && method === 'GET') {
    const relation = relationById(state.customerRelations, customerRelationMatch[1])
      || state.customerDetailRelations[customerRelationMatch[1]]
    if (relation) return fulfillJson(route, clone(relation))
    if (state.deletedCustomerRelationIds.has(Number(customerRelationMatch[1]))) {
      return expectedFailure(route, state, { detail: 'stage5_customer_relation_deleted' }, 404)
    }
    return fulfillJson(route, { detail: 'stage5_customer_relation_not_found' }, 404)
  }
  if (customerRelationMatch && method === 'PATCH') {
    const relation = relationById(state.customerRelations, customerRelationMatch[1])
    assert.ok(relation, 'Customer PATCH fixture relation missing.')
    const payload = parseJsonBody(request, 'update customer relation')
    Object.assign(relation, payload)
    return fulfillJson(route, clone(relation))
  }
  if (customerRelationMatch && method === 'DELETE') {
    const relation = relationById(state.customerRelations, customerRelationMatch[1])
    assert.ok(relation, 'Customer DELETE fixture relation missing.')
    if (state.customerDeleteGate) await state.customerDeleteGate.promise
    const receipt = { ...relation, status: relation.status === 'pending' ? 'revoked' : 'deleted' }
    state.deletedCustomerRelationIds.add(relation.id)
    state.customerRelations = state.customerRelations.filter((row) => row.id !== relation.id)
    return fulfillJson(route, clone(receipt))
  }
  const customerSessionsMatch = pathname.match(/^\/api\/customers\/owner-relations\/(\d+)\/sessions$/u)
  if (customerSessionsMatch && method === 'GET') {
    if (state.customerSessionFailuresRemaining > 0) {
      state.customerSessionFailuresRemaining -= 1
      return expectedFailure(route, state, { detail: 'opaque-customer-session-refresh-failure' }, 503)
    }
    return fulfillJson(route, clone(state.customerSessions[customerSessionsMatch[1]] || []))
  }
  const customerSessionDeleteMatch = pathname.match(/^\/api\/customers\/owner-relations\/(\d+)\/sessions\/([^/]+)$/u)
  if (customerSessionDeleteMatch && method === 'DELETE') {
    const relationId = customerSessionDeleteMatch[1]
    const sessionId = decodeURIComponent(customerSessionDeleteMatch[2])
    const rows = state.customerSessions[relationId] || []
    assert.ok(rows.some((row) => row.id === sessionId), 'Customer session fixture missing.')
    const remaining = rows.filter((row) => row.id !== sessionId)
    const promoted = rows.find((row) => row.id === sessionId)?.is_primary && remaining.length
      ? remaining[0].id
      : null
    state.customerSessions[relationId] = remaining.map((row) => ({
      ...row,
      is_primary: promoted === row.id ? true : row.is_primary,
    }))
    return fulfillJson(route, {
      detail: 'نشست انتخاب‌شده پایان یافت.',
      terminated_session_id: sessionId,
      promoted_primary_session_id: promoted,
    })
  }
  const customerTradesMatch = pathname.match(/^\/api\/trades\/with\/(\d+)$/u)
  if (customerTradesMatch) {
    assert.equal(url.searchParams.get('limit'), '20', 'Customer history limit drifted.')
    return fulfillJson(route, [{
      id: 7101,
      trade_number: 4001,
      trade_type: 'خرید',
      settlement_type: 'cash',
      commodity_name: 'طلای آب‌شده',
      quantity: 12,
      price: 3500000,
      status: 'completed',
      counterparty_name: 'طرف معامله نمونه',
      created_at: '2026-08-10T11:00:00.000Z',
    }])
  }
  const customerStatsMatch = pathname.match(/^\/api\/customers\/owner-relations\/(\d+)\/trade-stats$/u)
  if (customerStatsMatch) {
    const days = Number(url.searchParams.get('days'))
    assert.ok([1, 3, 7, 30, 90, 180].includes(days), 'Customer stats period is not canonical.')
    return fulfillJson(route, {
      relation_id: Number(customerStatsMatch[1]),
      customer_user_id: 6102,
      period_days: days,
      from_date: '2026-08-04T00:00:00.000Z',
      to_date: '2026-08-11T00:00:00.000Z',
      trade_count: 4,
      total_quantity: 48,
      commission_profit_toman: 1750000,
      commodities: [{ commodity_id: 1, commodity_name: 'طلای آب‌شده', total_quantity: 48 }],
      profit_calculation_note: 'برآورد کمیسیون بر مبنای معاملات تکمیل‌شده',
    })
  }

  if (pathname === '/api/accountants/owner-relations' && method === 'GET') {
    return fulfillJson(route, clone(state.accountantRelations))
  }
  if (pathname === '/api/accountants/owner-relations' && method === 'POST') {
    const payload = parseJsonBody(request, 'create accountant relation')
    if (state.accountantCreateGate) await state.accountantCreateGate.promise
    const created = accountantRelation({
      id: 5299,
      accountant_user_id: null,
      accountant_account_name: null,
      global_account_name: String(payload.account_name || ''),
      relation_display_name: String(payload.relation_display_name || ''),
      mobile_number: String(payload.mobile_number || ''),
      duty_description: payload.duty_description == null ? null : String(payload.duty_description),
      status: 'pending',
      web_registration_link: 'https://example.invalid/i/new-accountant',
      bot_registration_link: 'https://t.me/stage5_bot?start=new-accountant',
      sms_status: 'sent',
      expires_at: '2027-08-12T12:00:00.000Z',
      activated_at: null,
      created_at: '2026-08-11T08:00:00.000Z',
    })
    state.accountantRelations = [created, ...state.accountantRelations]
    return fulfillJson(route, clone(created), 201)
  }
  const accountantRelationMatch = pathname.match(/^\/api\/accountants\/owner-relations\/(\d+)$/u)
  if (accountantRelationMatch && method === 'GET') {
    const relation = relationById(state.accountantRelations, accountantRelationMatch[1])
      || state.accountantDetailRelations[accountantRelationMatch[1]]
    if (relation) return fulfillJson(route, clone(relation))
    if (state.deletedAccountantRelationIds.has(Number(accountantRelationMatch[1]))) {
      return expectedFailure(route, state, { detail: 'stage5_accountant_relation_deleted' }, 404)
    }
    return fulfillJson(route, { detail: 'stage5_accountant_relation_not_found' }, 404)
  }
  if (accountantRelationMatch && method === 'PATCH') {
    const relation = relationById(state.accountantRelations, accountantRelationMatch[1])
    assert.ok(relation, 'Accountant PATCH fixture relation missing.')
    const payload = parseJsonBody(request, 'update accountant relation')
    Object.assign(relation, payload)
    return fulfillJson(route, clone(relation))
  }
  if (accountantRelationMatch && method === 'DELETE') {
    const relation = relationById(state.accountantRelations, accountantRelationMatch[1])
    assert.ok(relation, 'Accountant DELETE fixture relation missing.')
    if (state.accountantDeleteGate) await state.accountantDeleteGate.promise
    const receipt = { ...relation, status: relation.status === 'pending' ? 'revoked' : 'deleted' }
    state.deletedAccountantRelationIds.add(relation.id)
    state.accountantRelations = state.accountantRelations.filter((row) => row.id !== relation.id)
    return fulfillJson(route, clone(receipt))
  }
  const accountantSessionsMatch = pathname.match(/^\/api\/accountants\/owner-relations\/(\d+)\/sessions$/u)
  if (accountantSessionsMatch && method === 'GET') {
    if (state.accountantSessionFailuresRemaining > 0) {
      state.accountantSessionFailuresRemaining -= 1
      return expectedFailure(route, state, { detail: 'opaque-accountant-session-refresh-failure' }, 503)
    }
    return fulfillJson(route, clone(state.accountantSessions[accountantSessionsMatch[1]] || []))
  }
  const accountantSessionDeleteMatch = pathname.match(/^\/api\/accountants\/owner-relations\/(\d+)\/sessions\/([^/]+)$/u)
  if (accountantSessionDeleteMatch && method === 'DELETE') {
    const relationId = accountantSessionDeleteMatch[1]
    const sessionId = decodeURIComponent(accountantSessionDeleteMatch[2])
    const rows = state.accountantSessions[relationId] || []
    assert.ok(rows.some((row) => row.id === sessionId), 'Accountant session fixture missing.')
    const remaining = rows.filter((row) => row.id !== sessionId)
    const promoted = rows.find((row) => row.id === sessionId)?.is_primary && remaining.length
      ? remaining[0].id
      : null
    state.accountantSessions[relationId] = remaining.map((row) => ({
      ...row,
      is_primary: promoted === row.id ? true : row.is_primary,
    }))
    return fulfillJson(route, {
      detail: 'نشست انتخاب‌شده پایان یافت.',
      terminated_session_id: sessionId,
      promoted_primary_session_id: promoted,
    })
  }

  diagnostics.unexpectedApiRequests.push({ suite: state.suite, pathname, method })
  return fulfillJson(route, { detail: 'stage5_unexpected_api_request' }, 501)
}

const assertions = []
const screenshots = []
const pageStates = new WeakMap()
const runtimeStates = []

function record(id, details = {}) {
  assertions.push({ id, passed: true, ...details })
}

function progress(stage, details = {}) {
  process.stdout.write(`${JSON.stringify({
    event: 'stage5-browser-progress',
    runId: RUN_ID,
    stage,
    ...details,
  })}\n`)
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
    viewport: options.viewport || { width: 390, height: 844 },
  })
  const page = await context.newPage()
  await page.clock.setFixedTime(new Date(FIXED_BROWSER_TIME))
  pageStates.set(page, state)
  runtimeStates.push(state)

  await context.exposeBinding('__stage5RecordRealtime', (_source, event) => {
    const row = { ...event, suite: state.suite }
    if (event.transport === 'websocket') {
      state.websocketEvents.push(row)
      diagnostics.webSockets.push(row)
      if (!event.valid) diagnostics.unexpectedWebSockets.push(row)
    } else {
      state.eventSourceEvents.push(row)
      diagnostics.eventSources.push(row)
      if (!event.valid) diagnostics.unexpectedEventSources.push(row)
    }
  })

  const token = createJwt({
    sub: String(OWNER.id),
    exp: FIXED_BROWSER_EPOCH_SECONDS + 3600,
    session_id: `stage5-${state.suite}`,
  })
  state.authToken = token

  await page.addInitScript(({ accessToken, owner }) => {
    window.__stage5NavigationEvents = [{
      kind: 'document-init',
      at: performance.now(),
      href: location.href,
    }]
    for (const method of ['pushState', 'replaceState']) {
      const original = history[method]
      history[method] = function (...args) {
        const from = location.href
        const requested = args[2] == null ? null : new URL(String(args[2]), from).href
        const at = performance.now()
        try {
          const result = Reflect.apply(original, this, args)
          window.__stage5NavigationEvents.push({
            kind: `history.${method}`,
            at,
            from,
            requested,
            href: location.href,
          })
          return result
        } catch (error) {
          window.__stage5NavigationEvents.push({
            kind: `history.${method}:error`,
            at,
            from,
            requested,
            href: location.href,
            error: error instanceof Error ? error.message : String(error),
          })
          throw error
        }
      }
    }
    window.addEventListener('popstate', () => {
      window.__stage5NavigationEvents.push({
        kind: 'popstate',
        at: performance.now(),
        href: location.href,
      })
    })

    window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
    delete window.__PLAYWRIGHT_ENABLE_PWA_REGISTRATION__
    const seedKey = '__stage5_browser_auth_seeded'
    if (sessionStorage.getItem(seedKey) !== '1') {
      sessionStorage.setItem(seedKey, '1')
      localStorage.setItem('auth_token', accessToken)
      localStorage.setItem('refresh_token', accessToken)
      localStorage.setItem('current_user_summary', JSON.stringify(owner))
      localStorage.removeItem('suspended_refresh_token')
    }

    window.__stage5ClipboardWrites = []
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async (value) => {
          window.__stage5ClipboardWrites.push(String(value))
        },
      },
    })

    class FakeNotification {
      static permission = 'denied'
      static async requestPermission() { return 'denied' }
      close() {}
    }
    Object.defineProperty(window, 'Notification', { configurable: true, value: FakeNotification })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: Promise.resolve({
          active: { postMessage() {} },
          pushManager: { getSubscription: async () => null },
        }),
        register: async () => ({ active: { postMessage() {} } }),
        getRegistration: async () => null,
      },
    })

    class FakeWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      constructor(url, protocols) {
        this.url = String(url)
        this.readyState = FakeWebSocket.CONNECTING
        this.bufferedAmount = 0
        this.extensions = ''
        this.protocol = ''
        this.binaryType = 'blob'
        this.listeners = new Map()
        const parsed = new URL(this.url, location.href)
        const protocolList = Array.isArray(protocols)
          ? protocols.map(String)
          : protocols
            ? [String(protocols)]
            : []
        const expectedProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        const sameHost = parsed.host === location.host
        const queryKeys = [...new Set(parsed.searchParams.keys())].sort()
        const isViteHmr = sameHost && parsed.protocol === expectedProtocol
          && parsed.pathname === '/' && protocolList.includes('vite-hmr')
        const isAppRealtime = sameHost && parsed.protocol === expectedProtocol
          && parsed.pathname === '/api/realtime/ws'
          && queryKeys.length === 1 && queryKeys[0] === 'token'
          && parsed.searchParams.get('token') === localStorage.getItem('auth_token')
        void window.__stage5RecordRealtime({
          transport: 'websocket',
          kind: isAppRealtime ? 'app-realtime' : isViteHmr ? 'vite-hmr' : 'unexpected',
          valid: isAppRealtime || isViteHmr,
          sameHost,
          protocol: parsed.protocol,
          pathname: parsed.pathname,
          queryKeys,
          tokenMatchesCurrentAuth: isAppRealtime,
          protocols: protocolList,
        })
        window.__stage5Sockets = window.__stage5Sockets || []
        window.__stage5Sockets.push(this)
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

      send() {}

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

    class FakeEventSource {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSED = 2

      constructor(url) {
        this.url = String(url)
        this.readyState = FakeEventSource.CONNECTING
        this.withCredentials = false
        this.listeners = new Map()
        const parsed = new URL(this.url, location.href)
        const sameOrigin = parsed.origin === location.origin
        const valid = sameOrigin && parsed.pathname === '/api/realtime/events'
        void window.__stage5RecordRealtime({
          transport: 'eventsource',
          kind: valid ? 'app-realtime' : 'unexpected',
          valid,
          sameOrigin,
          pathname: parsed.pathname,
          queryKeys: [...new Set(parsed.searchParams.keys())].sort(),
        })
        queueMicrotask(() => {
          if (this.readyState !== FakeEventSource.CONNECTING) return
          this.readyState = FakeEventSource.OPEN
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

      close() { this.readyState = FakeEventSource.CLOSED }
    }
    window.EventSource = FakeEventSource
    window.open = () => null
  }, { accessToken: token, owner: OWNER })

  const baseOrigin = new URL(baseUrl).origin
  await context.route('**/*', async (route) => {
    const requestUrl = new URL(route.request().url())
    if (requestUrl.origin !== baseOrigin) {
      diagnostics.externalRequestsBlocked.push({
        suite: state.suite,
        method: route.request().method(),
        url: route.request().url(),
      })
      if (requestUrl.origin === 'https://telegram.org' && requestUrl.pathname.endsWith('.js')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          headers: { 'Cache-Control': 'no-store' },
          body: '',
        })
      }
      return route.abort('blockedbyclient')
    }
    if (requestUrl.pathname.startsWith('/api/')) return handleApiRoute(route, state)
    return route.continue()
  })

  page.on('console', (message) => {
    if (message.type() === 'error') {
      diagnostics.consoleErrors.push({
        suite: state.suite,
        text: message.text(),
        location: message.location(),
      })
    }
  })
  page.on('pageerror', (error) => {
    diagnostics.pageErrors.push({ suite: state.suite, text: error.message })
  })
  page.on('response', (response) => {
    if (response.status() < 400) return
    const url = new URL(response.url())
    if (url.origin !== baseOrigin) return
    diagnostics.httpFailures.push({
      suite: state.suite,
      method: response.request().method(),
      pathname: url.pathname,
      status: response.status(),
    })
  })
  page.on('requestfailed', (request) => {
    const url = new URL(request.url())
    if (url.origin !== baseOrigin) return
    const failure = request.failure()?.errorText || ''
    if (state.intentionalNavigation && failure === 'net::ERR_ABORTED') return
    diagnostics.unexpectedRequestFailures.push({
      suite: state.suite,
      method: request.method(),
      pathname: url.pathname,
      failure,
    })
  })
  return { context, page, state }
}

async function closeRuntime(runtime) {
  for (const gateName of [
    'customerCreateGate',
    'accountantCreateGate',
    'customerDeleteGate',
    'accountantDeleteGate',
  ]) {
    runtime.state[gateName]?.resolve()
  }
  runtime.state.intentionalClose = true
  await runtime.context.close()
}

async function settle(page) {
  await page.waitForLoadState('domcontentloaded')
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
}

async function gotoPath(page, routePath, readySelector) {
  const state = pageStates.get(page)
  if (state) state.intentionalNavigation = true
  try {
    await page.goto(routePath, { waitUntil: 'domcontentloaded' })
    await page.locator(readySelector).first().waitFor({ state: 'visible', timeout: 30_000 })
    await settle(page)
  } finally {
    if (state) state.intentionalNavigation = false
  }
}

async function capture(page, fileName) {
  await settle(page)
  const filePath = path.join(OUTPUT_DIR, fileName)
  await page.screenshot({ path: filePath, fullPage: false, animations: 'disabled' })
  const stat = fs.statSync(filePath)
  const artifact = { file: fileName, bytes: stat.size, sha256: sha256File(filePath) }
  screenshots.push(artifact)
  return artifact
}

async function visibleCount(locator) {
  let count = 0
  for (const candidate of await locator.all()) {
    if (await candidate.isVisible()) count += 1
  }
  return count
}

async function waitForSingleVisible(page, selector) {
  await page.waitForFunction((target) => {
    const visible = [...document.querySelectorAll(target)].filter((element) => {
      if (!(element instanceof HTMLElement)) return false
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number.parseFloat(style.opacity || '1') > 0
        && rect.width > 0
        && rect.height > 0
    })
    return visible.length === 1 && document.querySelectorAll(target).length === 1
  }, selector, { timeout: 5_000 }).catch(() => undefined)
  const snapshot = await page.evaluate((target) => {
    const ancestry = (element) => {
      const rows = []
      for (let current = element; current && rows.length < 8; current = current.parentElement) {
        rows.push({
          tag: current.tagName,
          id: current.id || '',
          className: current.className?.toString?.() || '',
          uiSystem: current.getAttribute('data-ui-system'),
        })
      }
      return rows
    }
    const routeScroller = document.querySelector('.app-route-scroll')
    const directRouteChild = (element) => {
      let current = element
      while (current?.parentElement && current.parentElement !== routeScroller) {
        current = current.parentElement
      }
      return current?.parentElement === routeScroller ? current : null
    }
    const targetElements = [...document.querySelectorAll(target)]
    const routeRootElements = [...new Set(targetElements.map(directRouteChild).filter(Boolean))]
    const routeRoots = routeRootElements.map((element, index) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return {
        index,
        className: element.className?.toString?.() || '',
        uiSystem: element.getAttribute('data-ui-system'),
        display: style.display,
        visibility: style.visibility,
        opacity: style.opacity,
        transitionProperty: style.transitionProperty,
        transitionDuration: style.transitionDuration,
        transitionDelay: style.transitionDelay,
        width: rect.width,
        height: rect.height,
        targetCount: element.querySelectorAll(target).length,
        firstChild: element.firstElementChild
          ? {
              tag: element.firstElementChild.tagName,
              className: element.firstElementChild.className?.toString?.() || '',
            }
          : null,
      }
    })
    const rows = targetElements.map((element) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      const routeRoot = element.closest('.app-route-v2-scope')
      return {
        display: style.display,
        visibility: style.visibility,
        opacity: style.opacity,
        width: rect.width,
        height: rect.height,
        routeRootIndex: routeRootElements.indexOf(routeRoot),
        ancestry: ancestry(element),
      }
    })
    return {
      href: location.href,
      total: rows.length,
      rows,
      routeRoots,
      routeScroller: routeScroller
        ? {
            scrollTop: routeScroller.scrollTop,
            scrollHeight: routeScroller.scrollHeight,
            clientHeight: routeScroller.clientHeight,
          }
        : null,
      navigationEvents: window.__stage5NavigationEvents || [],
    }
  }, selector)
  assert.equal(
    snapshot.total,
    1,
    `Expected one settled ${selector}; actual ${JSON.stringify(snapshot)}`,
  )
  assert.ok(
    snapshot.rows[0].display !== 'none'
      && snapshot.rows[0].visibility !== 'hidden'
      && Number.parseFloat(snapshot.rows[0].opacity || '1') > 0
      && snapshot.rows[0].width > 0
      && snapshot.rows[0].height > 0,
    `Settled ${selector} is not visible: ${JSON.stringify(snapshot)}`,
  )
}

async function visibleBodyText(page) {
  return page.locator('body').innerText()
}

async function layoutMetrics(page) {
  return page.evaluate(() => {
    const isVisible = (element) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden'
        && Number(style.opacity || '1') > 0 && rect.width > 0 && rect.height > 0
    }
    const rectData = (element) => {
      const rect = element.getBoundingClientRect()
      const x = rect.left + rect.width / 2
      const y = rect.top + rect.height / 2
      const inViewport = rect.right > 0 && rect.left < innerWidth && rect.bottom > 0 && rect.top < innerHeight
      const fullyInViewport = rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight
      const clip = { left: 0, right: innerWidth, top: 0, bottom: innerHeight }
      const clipsAxis = (value) => ['auto', 'scroll', 'hidden', 'clip'].includes(value)
      const scrollsAxis = (value) => ['auto', 'scroll'].includes(value)
      let recoverableByScroll = false
      for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
        const ancestorStyle = getComputedStyle(ancestor)
        const ancestorRect = ancestor.getBoundingClientRect()
        if (clipsAxis(ancestorStyle.overflowX)) {
          clip.left = Math.max(clip.left, ancestorRect.left)
          clip.right = Math.min(clip.right, ancestorRect.right)
        }
        if (clipsAxis(ancestorStyle.overflowY)) {
          clip.top = Math.max(clip.top, ancestorRect.top)
          clip.bottom = Math.min(clip.bottom, ancestorRect.bottom)
        }
        if (
          (scrollsAxis(ancestorStyle.overflowX) && ancestor.scrollWidth > ancestor.clientWidth + 1)
          || (scrollsAxis(ancestorStyle.overflowY) && ancestor.scrollHeight > ancestor.clientHeight + 1)
        ) {
          recoverableByScroll = true
        }
      }
      const fullyExposed = rect.left >= clip.left - 0.5 && rect.right <= clip.right + 0.5
        && rect.top >= clip.top - 0.5 && rect.bottom <= clip.bottom + 0.5
      const centerInViewport = x >= 0 && x < innerWidth && y >= 0 && y < innerHeight
      const hit = centerInViewport ? document.elementFromPoint(x, y) : null
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
        inViewport,
        fullyInViewport,
        fullyExposed,
        recoverableByScroll,
        clip,
        centerInViewport,
        centerUnoccluded: !centerInViewport || Boolean(hit && (element === hit || element.contains(hit))),
      }
    }
    const modalRoot = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')]
      .filter((element) => element instanceof HTMLElement && isVisible(element))
      .at(-1)
    // While an aria-modal surface is open, only its controls are active for
    // target-size/occlusion purposes; the obscured workspace remains rendered
    // underneath by design.
    const interactiveRoot = modalRoot || document
    const interactiveSelector = [
      '[data-ui-system="v2"] button:not(:disabled)',
      '[data-ui-system="v2"] a[href]',
      '[data-ui-system="v2"] input:not(:disabled)',
      '[data-ui-system="v2"] select:not(:disabled)',
      '[data-ui-system="v2"] textarea:not(:disabled)',
      '[data-ui-system="v2"] [role="button"]:not([aria-disabled="true"])',
    ].join(',')
    const interactive = [...interactiveRoot.querySelectorAll(interactiveSelector)]
      .filter((element) => element instanceof HTMLElement && isVisible(element))
      .map((element) => {
        const own = rectData(element)
        const type = element instanceof HTMLInputElement ? element.type : ''
        // A native control nested by its label deliberately shares the label's
        // pointer hit-area (for example AppSearchField's 48px shell). Measure
        // that semantic target instead of the input's internal text line-box.
        const effectiveTarget = element.matches('input, select, textarea')
          ? element.closest('label') || element
          : element
        const effective = rectData(effectiveTarget)
        return {
          tag: element.tagName,
          type,
          text: (element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 80),
          className: element.className?.toString?.() || '',
          own,
          effective,
        }
      })
    const critical = [...document.querySelectorAll([
      '.ui-v2-workspace-customer-root',
      '.ui-v2-workspace-customer-layout',
      '.ui-v2-workspace-customer-list-section',
      '.ui-v2-workspace-customer-detail-section',
      '.ui-v2-workspace-customer-financial-table',
      '.ui-v2-workspace-accountant-layout',
      '.ui-v2-workspace-accountant-list-section',
      '.ui-v2-workspace-accountant-detail-section',
      '.ui-v2-workspace-overlay-panel',
      '.ui-v2-workspace-account-deletion-dialog',
    ].join(','))]
      .filter((element) => element instanceof HTMLElement && isVisible(element))
      .map((element) => ({
        selector: element.className?.toString?.() || element.tagName,
        ...rectData(element),
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }))
    const fixed = [...document.querySelectorAll('body *')]
      .filter((element) => element instanceof HTMLElement && isVisible(element)
        && ['fixed', 'sticky'].includes(getComputedStyle(element).position))
      .map((element) => ({ selector: element.className?.toString?.() || element.tagName, ...rectData(element) }))
    const routeScroller = document.querySelector('.app-route-scroll')
    return {
      viewport: { width: innerWidth, height: innerHeight },
      documentScrollWidth: Math.max(
        document.documentElement.scrollWidth,
        document.body.scrollWidth,
        document.querySelector('#app')?.scrollWidth || 0,
      ),
      routeScroller: routeScroller
        ? { clientWidth: routeScroller.clientWidth, scrollWidth: routeScroller.scrollWidth }
        : null,
      interactive,
      critical,
      fixed,
    }
  })
}

async function auditLayout(page, label) {
  const metrics = await layoutMetrics(page)
  assert.ok(
    metrics.documentScrollWidth <= metrics.viewport.width + 1,
    `${label}: horizontal overflow ${metrics.documentScrollWidth}/${metrics.viewport.width}`,
  )
  if (metrics.routeScroller) {
    assert.ok(
      metrics.routeScroller.scrollWidth <= metrics.routeScroller.clientWidth + 1,
      `${label}: route scroller overflow.`,
    )
  }
  assert.ok(metrics.interactive.length > 0, `${label}: no V2 interactive targets found.`)
  for (const target of metrics.interactive) {
    assert.ok(
      target.effective.width >= 43.5 && target.effective.height >= 43.5,
      `${label}: effective target below 44x44 ${JSON.stringify(target)}`,
    )
    if (target.own.fullyExposed && !target.own.recoverableByScroll) {
      assert.equal(target.own.centerUnoccluded, true, `${label}: target is obscured ${JSON.stringify(target)}`)
    }
  }
  for (const item of metrics.critical) {
    assert.ok(item.left >= -1 && item.right <= metrics.viewport.width + 1, `${label}: clipped critical surface ${item.selector}`)
    assert.ok(item.scrollWidth <= item.clientWidth + 1, `${label}: internal overflow ${item.selector}`)
  }
  for (const item of metrics.fixed) {
    assert.ok(item.left >= -1 && item.right <= metrics.viewport.width + 1, `${label}: clipped fixed layer ${item.selector}`)
  }
  return metrics
}

async function assertTargetUnobscured(locator, label) {
  await locator.scrollIntoViewIfNeeded()
  await settle(locator.page())
  const result = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    const x = Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2))
    const y = Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2))
    const hit = document.elementFromPoint(x, y)
    return {
      rect: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom },
      viewport: { width: innerWidth, height: innerHeight },
      unoccluded: Boolean(hit && (element === hit || element.contains(hit))),
    }
  })
  assert.ok(result.rect.left >= -1 && result.rect.right <= result.viewport.width + 1, `${label}: CTA clips horizontally.`)
  assert.ok(result.rect.top >= -1 && result.rect.bottom <= result.viewport.height + 1, `${label}: CTA remains outside viewport.`)
  assert.equal(result.unoccluded, true, `${label}: CTA is obscured.`)
  return result
}

async function focusProof(page, locator, label) {
  await locator.scrollIntoViewIfNeeded()
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  })
  let reached = false
  for (let index = 0; index < 120; index += 1) {
    await page.keyboard.press('Tab')
    reached = await locator.evaluate((element) => document.activeElement === element)
    if (reached) break
  }
  assert.equal(reached, true, `${label}: not keyboard reachable.`)
  const result = await locator.evaluate((element) => {
    const style = getComputedStyle(element)
    return {
      focusVisible: element.matches(':focus-visible'),
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      outlineOffset: Number.parseFloat(style.outlineOffset),
    }
  })
  assert.equal(result.focusVisible, true, `${label}: keyboard focus is not visible.`)
  assert.equal(result.outlineStyle, 'solid', `${label}: focus outline style drifted.`)
  assert.ok(result.outlineWidth >= 2, `${label}: focus outline is too thin.`)
  assert.ok(result.outlineOffset >= 1, `${label}: focus outline offset is too small.`)
  return result
}

async function trapAndRestoreProof(page, dialog, trigger, label) {
  const focusableSelector = [
    'button:not([disabled])',
    'a[href]',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',')
  await dialog.waitFor({ state: 'visible' })
  await page.waitForFunction((selector) => document.querySelector(selector)?.contains(document.activeElement), await dialog.evaluate((element) => {
    if (!element.id) element.id = `stage5-dialog-${crypto.randomUUID()}`
    return `#${CSS.escape(element.id)}`
  }))
  const focusable = dialog.locator(focusableSelector)
  const count = await focusable.count()
  assert.ok(count >= 2, `${label}: dialog has fewer than two focusable controls.`)
  const first = focusable.first()
  const last = focusable.last()
  await last.focus()
  await page.keyboard.press('Tab')
  assert.equal(await first.evaluate((element) => document.activeElement === element), true, `${label}: forward trap failed.`)
  await first.focus()
  await page.keyboard.press('Shift+Tab')
  assert.equal(await last.evaluate((element) => document.activeElement === element), true, `${label}: reverse trap failed.`)
  await page.keyboard.press('Escape')
  await dialog.waitFor({ state: 'hidden' })
  assert.equal(await trigger.evaluate((element) => document.activeElement === element), true, `${label}: focus was not restored.`)
  return { focusableCount: count }
}

function parseCssRgb(value) {
  const channels = value.match(/[\d.]+/gu)?.map(Number) || []
  assert.ok(channels.length >= 3, `Unable to parse CSS color: ${value}`)
  return channels.slice(0, 3)
}

function luminance(rgb) {
  const normalized = rgb.map((channel) => {
    const value = channel / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return normalized[0] * 0.2126 + normalized[1] * 0.7152 + normalized[2] * 0.0722
}

function contrastRatio(foreground, background) {
  const lighter = Math.max(luminance(foreground), luminance(background))
  const darker = Math.min(luminance(foreground), luminance(background))
  return (lighter + 0.05) / (darker + 0.05)
}

async function contrastProof(page, selectors, label) {
  const samples = await page.evaluate((requestedSelectors) => requestedSelectors.map((selector) => {
    const element = document.querySelector(selector)
    if (!(element instanceof HTMLElement)) return { selector, missing: true }
    const style = getComputedStyle(element)
    let background = 'rgba(0, 0, 0, 0)'
    for (let node = element; node instanceof HTMLElement; node = node.parentElement) {
      const candidate = getComputedStyle(node).backgroundColor
      const alpha = Number(candidate.match(/[\d.]+/gu)?.[3] ?? 1)
      if (alpha > 0) {
        background = candidate
        break
      }
    }
    return {
      selector,
      missing: false,
      color: style.color,
      background,
      fontSize: Number.parseFloat(style.fontSize),
      fontWeight: Number(style.fontWeight) || 400,
    }
  }), selectors)
  for (const sample of samples) {
    assert.equal(sample.missing, false, `${label}: missing contrast selector ${sample.selector}`)
    const ratio = contrastRatio(parseCssRgb(sample.color), parseCssRgb(sample.background))
    sample.ratio = ratio
    const large = sample.fontSize >= 24 || (sample.fontSize >= 18.66 && sample.fontWeight >= 700)
    assert.ok(ratio >= (large ? 3 : 4.5), `${label}: contrast ${ratio.toFixed(2)}:1 at ${sample.selector}`)
  }
  return samples
}

async function textResizeProof(page, label) {
  const previous = await page.evaluate(() => document.documentElement.style.fontSize)
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  await settle(page)
  const metrics = await auditLayout(page, `${label} at 200% text`)
  await page.evaluate((value) => { document.documentElement.style.fontSize = value }, previous)
  await settle(page)
  return metrics
}

async function reducedMotionProof(page, selector, label) {
  const result = await page.evaluate((requestedSelector) => {
    const parseTime = (value) => value.split(',').map((part) => {
      const trimmed = part.trim()
      return trimmed.endsWith('ms') ? Number.parseFloat(trimmed) / 1000 : Number.parseFloat(trimmed)
    }).filter(Number.isFinite)
    const elements = [...document.querySelectorAll(requestedSelector)]
    const rows = elements.map((element) => {
      const style = getComputedStyle(element)
      const animationSeconds = parseTime(style.animationDuration)
      const transitionSeconds = parseTime(style.transitionDuration)
      const maximumSeconds = Math.max(0, ...animationSeconds, ...transitionSeconds)
      return {
        tag: element.tagName,
        text: (element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 60),
        className: element.className?.toString?.() || '',
        animationDuration: style.animationDuration,
        transitionDuration: style.transitionDuration,
        maximumSeconds,
      }
    })
    return {
      matches: matchMedia('(prefers-reduced-motion: reduce)').matches,
      elementCount: elements.length,
      maximumSeconds: Math.max(0, ...rows.map((row) => row.maximumSeconds)),
      offenders: rows.filter((row) => row.maximumSeconds > 0.001).slice(0, 20),
    }
  }, selector)
  assert.equal(result.matches, true, `${label}: reduced-motion media query not active.`)
  assert.ok(result.elementCount > 0, `${label}: no Stage 5 elements sampled.`)
  assert.ok(result.maximumSeconds <= 0.001, `${label}: motion exceeds 1ms ${JSON.stringify(result.offenders)}`)
  return result
}

async function assertCanonicalUrl(page, expected, label) {
  await page.waitForFunction(({ pathname, query }) => {
    if (location.pathname !== pathname) return false
    const actual = Object.fromEntries([...new URLSearchParams(location.search).entries()].sort())
    const normalizedExpected = Object.fromEntries(Object.entries(query).sort())
    return JSON.stringify(actual) === JSON.stringify(normalizedExpected)
  }, expected, { timeout: 5_000 }).catch(() => undefined)
  const actual = await page.evaluate(() => ({
    pathname: location.pathname,
    query: Object.fromEntries([...new URLSearchParams(location.search).entries()].sort()),
  }))
  assert.deepEqual(actual, {
    pathname: expected.pathname,
    query: Object.fromEntries(Object.entries(expected.query).sort()),
  }, `${label}: URL is not canonical.`)
  return actual
}

function requestRows(state, pathname, method) {
  return state.requestLog.filter((row) => row.pathname === pathname && (!method || row.method === method))
}

function assertDeleteAction(request, expectedAction, label) {
  const url = new URL(request.url())
  assert.equal(url.searchParams.get('expected_action'), expectedAction, `${label}: destructive semantic precondition drifted.`)
  assert.deepEqual([...url.searchParams.keys()], ['expected_action'], `${label}: destructive request has noncanonical query keys.`)
}

async function masterDetailProof(page, product, width) {
  const prefix = `.ui-v2-workspace-${product}`
  const result = await page.evaluate(({ listSelector, detailSelector }) => {
    const list = document.querySelector(listSelector)
    const detail = document.querySelector(detailSelector)
    assertElement(list, listSelector)
    assertElement(detail, detailSelector)
    function assertElement(value, label) {
      if (!(value instanceof HTMLElement)) throw new Error(`Missing ${label}`)
    }
    const listRect = list.getBoundingClientRect()
    const detailRect = detail.getBoundingClientRect()
    return {
      list: { left: listRect.left, right: listRect.right, top: listRect.top, width: listRect.width },
      detail: { left: detailRect.left, right: detailRect.right, top: detailRect.top, width: detailRect.width },
      direction: getComputedStyle(document.documentElement).direction,
    }
  }, {
    listSelector: `${prefix}-list-section`,
    detailSelector: `${prefix}-detail-section`,
  })
  assert.ok(Math.abs(result.list.top - result.detail.top) <= 2, `${product} ${width}: master/detail rows are not aligned.`)
  assert.ok(result.list.width >= 240, `${product} ${width}: master width is not usable.`)
  assert.ok(result.detail.width > result.list.width, `${product} ${width}: detail is not the dominant pane.`)
  const gap = result.direction === 'rtl'
    ? result.list.left - result.detail.right
    : result.detail.left - result.list.right
  assert.ok(gap >= 8, `${product} ${width}: master/detail panes overlap.`)
  return result
}

async function runResponsiveMatrix(browser, baseUrl) {
  const products = [
    {
      key: 'customer',
      listPath: '/operations/customers',
      detailPath: '/operations/customers/5102',
      listSelector: '.ui-v2-workspace-customer-list-section',
      detailSelector: '.ui-v2-workspace-customer-detail-section',
      listReady: '.ui-v2-workspace-customer-relation-list',
      detailReady: '.ui-v2-workspace-customer-detail-header',
      createName: 'افزودن مشتری',
    },
    {
      key: 'accountant',
      listPath: '/operations/accountants',
      detailPath: '/operations/accountants/5202',
      listSelector: '.ui-v2-workspace-accountant-list-section',
      detailSelector: '.ui-v2-workspace-accountant-detail-section',
      listReady: '.ui-v2-workspace-accountant-relation-list',
      detailReady: '.ui-v2-workspace-accountant-detail-header',
      createName: 'افزودن حسابدار',
    },
  ]
  const result = {}
  for (const product of products) {
    const runtime = await createPage(browser, baseUrl, {
      suite: `responsive-${product.key}`,
      state: newRuntimeState(),
    })
    const rows = []
    try {
      for (const viewport of [...VIEWPORTS, ...BREAKPOINT_VIEWPORTS]) {
        await runtime.page.setViewportSize({ width: viewport.width, height: viewport.height })
        await gotoPath(runtime.page, product.listPath, product.listReady)
        assert.equal(await visibleCount(runtime.page.locator(product.listSelector)), 1, `${product.key} ${viewport.label}: list missing.`)
        const emptyDesktopDetail = product.key === 'accountant' && viewport.width >= 900
        assert.equal(
          await visibleCount(runtime.page.locator(product.detailSelector)),
          emptyDesktopDetail ? 1 : 0,
          `${product.key} ${viewport.label}: list-route master/detail contract failed.`,
        )
        if (emptyDesktopDetail) {
          await runtime.page.getByText('حسابداری انتخاب نشده است', { exact: true }).waitFor({ state: 'visible' })
        }
        const listLayout = await auditLayout(runtime.page, `${product.key} list ${viewport.label}`)
        await assertTargetUnobscured(
          runtime.page.getByRole('button', { name: product.createName, exact: true }).first(),
          `${product.key} list ${viewport.label} create CTA`,
        )
        await capture(runtime.page, `stage5-${product.key}-list-${viewport.label}.png`)

        await gotoPath(runtime.page, product.detailPath, product.detailReady)
        const compact = viewport.width < 900
        assert.equal(await visibleCount(runtime.page.locator(product.detailSelector)), 1, `${product.key} ${viewport.label}: detail missing.`)
        assert.equal(
          await visibleCount(runtime.page.locator(product.listSelector)),
          compact ? 0 : 1,
          `${product.key} ${viewport.label}: list/detail XOR contract failed.`,
        )
        const detailLayout = await auditLayout(runtime.page, `${product.key} detail ${viewport.label}`)
        const masterDetail = compact ? null : await masterDetailProof(runtime.page, product.key, viewport.width)
        await capture(runtime.page, `stage5-${product.key}-detail-${viewport.label}.png`)
        rows.push({ viewport, compact, emptyDesktopDetail, listLayout, detailLayout, masterDetail })
      }
      record(`stage5-${product.key}-responsive-eight-widths-and-899-900-xor`, { rows: rows.length })
      result[product.key] = rows
    } finally {
      await closeRuntime(runtime)
    }
  }
  record('stage5-desktop-true-master-detail-at-900-1024-1440')
  return result
}

async function openStrongDeleteWithKeyboard(page, triggerName, dialogName, label) {
  const trigger = page.getByRole('button', { name: triggerName, exact: true })
  const focus = await focusProof(page, trigger, `${label} trigger focus`)
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: dialogName, exact: true })
  await dialog.waitFor({ state: 'visible' })
  const describedBy = (await dialog.getAttribute('aria-describedby') || '').split(/\s+/u).filter(Boolean)
  assert.ok(describedBy.length >= 2, `${label}: dialog description does not include consequences.`)
  for (const id of describedBy) {
    assert.equal(await page.locator(`[id=${JSON.stringify(id)}]`).count(), 1, `${label}: unresolved aria-describedby id ${id}`)
  }
  const consequences = [
    'دسترسی وب‌اپ و ربات قطع می‌شود.',
    'همه نشست‌های فعال پایان می‌یابند.',
    'آفرهای فعال منقضی می‌شوند.',
    'دعوت‌های در انتظار مرتبط لغو می‌شوند.',
    'همه روابط باز مشتری و حسابدارِ متعلق یا لینک‌شده بسته می‌شوند.',
    'حساب‌های فعال وابسته‌ای که این کاربر مالک آن‌هاست ممکن است به‌صورت بازگشتی حذف شوند.',
    'سوابق معاملات حذف نمی‌شوند.',
  ]
  for (const consequence of consequences) {
    await dialog.getByText(consequence, { exact: true }).waitFor({ state: 'visible' })
  }
  return { trigger, dialog, focus, consequences }
}

async function completeStrongDelete(page, state, options) {
  const { subjectName, endpoint, gateKey, label, expectedAction = 'delete-account' } = options
  const dialog = page.locator('.ui-v2-workspace-account-deletion-dialog')
  assert.equal(await dialog.getByRole('heading', { name: `حذف حساب ${subjectName}`, exact: true }).count(), 1)
  const confirm = dialog.getByRole('button', { name: 'حذف حساب و قطع ارتباط', exact: true })
  assert.equal(await confirm.isDisabled(), true, `${label}: delete enabled before confirmation.`)
  await dialog.getByRole('textbox').fill(subjectName)
  assert.equal(await confirm.isDisabled(), true, `${label}: acknowledgement was bypassed.`)
  await dialog.locator('input[type="checkbox"]').check()
  assert.equal(await confirm.isEnabled(), true, `${label}: exact confirmation did not enable deletion.`)
  await capture(page, `stage5-${label}-strong-delete.png`)
  state[gateKey] = deferred()
  const requestPromise = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return request.method() === 'DELETE' && url.pathname === endpoint
  })
  await confirm.click()
  const request = await requestPromise
  assertDeleteAction(request, expectedAction, `${label} strong delete`)
  await page.waitForFunction(() => document.querySelector('.ui-v2-workspace-account-deletion-dialog')?.getAttribute('aria-busy') === 'true')
  assert.equal(await confirm.isDisabled(), true, `${label}: busy delete can be submitted twice.`)
  await page.keyboard.press('Escape')
  assert.equal(await dialog.isVisible(), true, `${label}: Escape dismissed a busy destructive action.`)
  state[gateKey].resolve()
  state[gateKey] = null
  await dialog.waitFor({ state: 'hidden' })
}

async function runCustomerWorkflows(browser, baseUrl) {
  const result = {}

  const pending = await createPage(browser, baseUrl, { suite: 'customer-pending' })
  try {
    await gotoPath(pending.page, '/operations/customers/5101', '.ui-v2-workspace-customer-pending-detail')
    const body = await visibleBodyText(pending.page)
    assert.match(body, /دعوت در انتظار ثبت‌نام/u)
    assert.match(body, /مهلت ثبت‌نام/u)
    assert.equal(await pending.page.getByRole('tab').count(), 0, 'Pending customer exposes active-account tabs.')
    const cancel = pending.page.getByRole('button', { name: 'لغو دعوت', exact: true }).first()
    await cancel.click()
    const dialog = pending.page.locator('.ui-confirm-dialog')
    await dialog.waitFor({ state: 'visible' })
    assert.equal(await dialog.getByRole('heading', { name: 'لغو رابطه در انتظار و دعوت مشتری', exact: true }).count(), 1)
    const copy = await dialog.innerText()
    assert.match(copy, /رزرو هویت/u)
    assert.match(copy, /هیچ آبشار حذف حساب فعالی اجرا نمی‌شود/u)
    await capture(pending.page, 'stage5-customer-pending-mobile-390.png')
    await pending.page.keyboard.press('Escape')
    await auditLayout(pending.page, 'customer pending mobile 390')
    await cancel.click()
    await dialog.waitFor({ state: 'visible' })
    const cancellation = pending.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/customers/owner-relations/5101' && request.method() === 'DELETE')
    pending.state.intentionalNavigation = true
    await dialog.getByRole('button', { name: 'لغو رابطه و دعوت', exact: true }).click()
    assertDeleteAction(await cancellation, 'cancel-pending', 'customer pending cancellation')
    await waitForSingleVisible(pending.page, '.ui-v2-workspace-customer-list-section')
    pending.state.intentionalNavigation = false
    record('stage5-customer-pending-deadline-and-relation-only-cancel-copy')
    result.pending = { copy }
  } finally {
    await closeRuntime(pending)
  }

  const terminal = await createPage(browser, baseUrl, { suite: 'customer-terminal-detail' })
  try {
    const detailRequest = terminal.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/customers/owner-relations/5104' && request.method() === 'GET')
    await gotoPath(
      terminal.page,
      '/operations/customers/5104?panel=trades&listScroll=48&noise=1',
      '.ui-v2-workspace-customer-detail-header',
    )
    await detailRequest
    const canonical = await assertCanonicalUrl(terminal.page, {
      pathname: '/operations/customers/5104',
      query: { scroll: '48', tab: 'trades' },
    }, 'customer terminal legacy detail context')
    await terminal.page.getByText('رابطه فقط خواندنی است', { exact: true }).waitFor({ state: 'visible' })
    assert.deepEqual(
      await terminal.page.getByRole('tab').allTextContents(),
      ['مشخصات', 'معاملات', 'آمار'],
      'Terminal customer capabilities are not read-only.',
    )
    assert.equal(await terminal.page.locator('.ui-v2-workspace-customer-danger-card').count(), 0)
    assert.equal(await terminal.page.locator('.ui-v2-workspace-customer-edit-card').count(), 0)
    assert.equal(await terminal.page.locator('.ui-v2-workspace-customer-session-actions').count(), 0)
    await terminal.page.getByText(/طرف معامله نمونه/u).waitFor({ state: 'visible' })
    await auditLayout(terminal.page, 'customer terminal detail mobile 390')
    await capture(terminal.page, 'stage5-customer-terminal-detail-mobile-390.png')
    record('stage5-customer-terminal-detail-get-read-only-and-legacy-canonicalization')
    result.terminal = { canonical }
  } finally {
    await closeRuntime(terminal)
  }

  const financial = await createPage(browser, baseUrl, { suite: 'customer-financial' })
  try {
    await gotoPath(financial.page, '/operations/customers/5102?tab=limits', '.ui-v2-workspace-customer-edit-card')
    await financial.page.getByLabel('حداکثر تعداد روزانه').fill('۶')
    await financial.page.getByRole('button', { name: 'مرور تغییرات', exact: true }).click()
    const table = financial.page.locator('.ui-v2-workspace-customer-financial-table')
    await table.waitFor({ state: 'visible' })
    assert.deepEqual(await table.getByRole('columnheader').allTextContents(), ['مورد', 'قبل', 'بعد'])
    assert.match(await table.innerText(), /تعداد روزانه/u)
    assert.match(await visibleBodyText(financial.page), /فقط روی معاملات آینده اثر دارند/u)
    await auditLayout(financial.page, 'customer financial review mobile 390')
    await capture(financial.page, 'stage5-customer-financial-review-mobile-390.png')
    const patchPromise = financial.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/customers/owner-relations/5102' && request.method() === 'PATCH')
    await financial.page.getByRole('button', { name: 'ثبت تغییرات', exact: true }).click()
    const patchRequest = await patchPromise
    assert.deepEqual(JSON.parse(patchRequest.postData()), { max_daily_trades: 6 })
    await financial.page.getByText('تنظیمات مشتری ذخیره شد و فقط بر معاملات آینده اثر می‌گذارد.', { exact: true }).waitFor({ state: 'visible' })
    record('stage5-customer-financial-before-after-review-and-future-only-patch')
    result.financial = { payload: JSON.parse(patchRequest.postData()) }
  } finally {
    await closeRuntime(financial)
  }

  const sessions = await createPage(browser, baseUrl, { suite: 'customer-sessions' })
  try {
    await gotoPath(sessions.page, '/operations/customers/5102?tab=sessions', '.ui-v2-workspace-customer-session-actions')
    assert.match(await visibleBodyText(sessions.page), /Chrome اصلی مشتری/u)
    assert.match(await visibleBodyText(sessions.page), /گوشی مشتری/u)
    assert.doesNotMatch(await visibleBodyText(sessions.page), /home_server|hidden-customer/u)
    sessions.state.customerSessionFailuresRemaining = 1
    const failedRefresh = sessions.page.waitForResponse((response) => new URL(response.url()).pathname === '/api/customers/owner-relations/5102/sessions' && response.status() === 503)
    await sessions.page.getByRole('button', { name: 'نوسازی', exact: true }).click()
    await failedRefresh
    await sessions.page.getByRole('alert').waitFor({ state: 'visible' })
    assert.match(await visibleBodyText(sessions.page), /Chrome اصلی مشتری/u)
    assert.match(await visibleBodyText(sessions.page), /گوشی مشتری/u)
    const recovery = sessions.page.waitForResponse((response) => new URL(response.url()).pathname === '/api/customers/owner-relations/5102/sessions' && response.status() === 200)
    await sessions.page.getByRole('button', { name: 'تلاش دوباره', exact: true }).click()
    await recovery
    await sessions.page.getByRole('alert').waitFor({ state: 'hidden' })
    const primaryRow = sessions.page.locator('.ui-list-item').filter({ hasText: 'Chrome اصلی مشتری' })
    await primaryRow.getByRole('button', { name: 'پایان نشست', exact: true }).click()
    const confirm = sessions.page.locator('.ui-confirm-dialog')
    await confirm.waitFor({ state: 'visible' })
    assert.equal(await confirm.getByRole('heading', { name: 'پایان نشست', exact: true }).count(), 1)
    const confirmCopy = await confirm.innerText()
    assert.match(confirmCopy, /فقط نشست/u)
    assert.match(confirmCopy, /نشست‌های دیگر فعال می‌مانند/u)
    const terminate = sessions.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/customers/owner-relations/5102/sessions/customer-primary' && request.method() === 'DELETE')
    await confirm.getByRole('button', { name: 'پایان همین نشست', exact: true }).click()
    await terminate
    await sessions.page.getByText('نشست «Chrome اصلی مشتری» پایان یافت.', { exact: true }).waitFor({ state: 'visible' })
    assert.equal(await sessions.page.getByText('Chrome اصلی مشتری', { exact: true }).count(), 0)
    const remainingRow = sessions.page.locator('.ui-list-item').filter({ hasText: 'گوشی مشتری' })
    await remainingRow.getByText('اصلی', { exact: true }).waitFor({ state: 'visible' })
    await capture(sessions.page, 'stage5-customer-sessions-recovery-mobile-390.png')
    record('stage5-customer-session-retained-refresh-recovery-and-selected-termination')
    result.sessions = { confirmCopy }
  } finally {
    await closeRuntime(sessions)
  }

  const strong = await createPage(browser, baseUrl, { suite: 'customer-strong-delete' })
  try {
    await gotoPath(strong.page, '/operations/customers/5102?tab=danger', '.ui-v2-workspace-customer-danger-card')
    const opened = await openStrongDeleteWithKeyboard(
      strong.page,
      'بررسی و حذف حساب',
      'حذف حساب مشتری مالی',
      'customer strong delete',
    )
    const trap = await trapAndRestoreProof(strong.page, opened.dialog, opened.trigger, 'customer strong delete')
    await strong.page.waitForFunction(() => !document.querySelector('.ui-v2-workspace-account-deletion-dialog'))
    await opened.trigger.click()
    await waitForSingleVisible(strong.page, '.ui-v2-workspace-account-deletion-dialog')
    strong.state.intentionalNavigation = true
    await completeStrongDelete(strong.page, strong.state, {
      subjectName: 'مشتری مالی',
      endpoint: '/api/customers/owner-relations/5102',
      gateKey: 'customerDeleteGate',
      label: 'customer',
    })
    await waitForSingleVisible(strong.page, '.ui-v2-workspace-customer-list-section')
    strong.state.intentionalNavigation = false
    record('stage5-customer-strong-delete-exact-name-ack-busy-trap-and-focus-return', { trap })
    result.strongDelete = { consequences: opened.consequences, trap }
  } finally {
    await closeRuntime(strong)
  }

  const orphan = await createPage(browser, baseUrl, { suite: 'customer-orphan' })
  try {
    await gotoPath(orphan.page, '/operations/customers/5103?tab=danger', '.ui-v2-workspace-customer-danger-card')
    const body = await visibleBodyText(orphan.page)
    assert.match(body, /بستن رابطه بدون حذف حساب/u)
    assert.match(body, /آبشار حذف حساب فعال اجرا نمی‌شود/u)
    assert.equal(await orphan.page.locator('.ui-v2-workspace-account-deletion-dialog').count(), 0)
    await orphan.page.getByRole('button', { name: 'بررسی و بستن رابطه', exact: true }).click()
    const dialog = orphan.page.locator('.ui-confirm-dialog')
    await dialog.waitFor({ state: 'visible' })
    assert.equal(await dialog.getByRole('heading', { name: 'بستن رابطه مشتری', exact: true }).count(), 1)
    assert.match(await dialog.innerText(), /فقط رابطه/u)
    assert.match(await dialog.innerText(), /هیچ آبشار حذف حساب/u)
    await capture(orphan.page, 'stage5-customer-orphan-relation-only-mobile-390.png')
    const deletion = orphan.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/customers/owner-relations/5103' && request.method() === 'DELETE')
    orphan.state.intentionalNavigation = true
    await dialog.getByRole('button', { name: 'بستن همین رابطه', exact: true }).click()
    assertDeleteAction(await deletion, 'delete-relation', 'customer orphan relation-only close')
    await waitForSingleVisible(orphan.page, '.ui-v2-workspace-customer-list-section')
    orphan.state.intentionalNavigation = false
    record('stage5-customer-orphan-active-relation-closes-without-account-cascade')
    result.orphan = { body }
  } finally {
    await closeRuntime(orphan)
  }

  return result
}

async function runAccountantWorkflows(browser, baseUrl) {
  const result = {}

  const pending = await createPage(browser, baseUrl, { suite: 'accountant-pending' })
  try {
    await gotoPath(pending.page, '/operations/accountants', '.ui-v2-workspace-accountant-relation-list')
    const pendingCard = pending.page.locator('.accountant-pending-card').filter({ hasText: 'دعوت حسابدار آزمایشی' })
    await pendingCard.waitFor({ state: 'visible' })
    assert.match(await pendingCard.innerText(), /مهلت استفاده/u)
    await gotoPath(pending.page, '/operations/accountants/5201?tab=danger', '.ui-v2-workspace-accountant-danger-card')
    assert.equal(await pending.page.getByRole('tab', { name: 'نشست‌ها', exact: true }).count(), 0, 'Pending accountant exposes sessions.')
    assert.match(await visibleBodyText(pending.page), /تا پیش از ثبت‌نام فقط مشخصات دعوت/u)
    await pending.page.getByRole('button', { name: 'لغو رابطه و دعوت', exact: true }).click()
    const dialog = pending.page.locator('.ui-confirm-dialog')
    await dialog.waitFor({ state: 'visible' })
    assert.equal(await dialog.getByRole('heading', { name: 'لغو رابطه و دعوت حسابدار', exact: true }).count(), 1)
    const copy = await dialog.innerText()
    assert.match(copy, /رزرو هویت و نام کاربری آزاد می‌شود/u)
    assert.match(copy, /حذف زنجیره‌ای حساب، نشست، آفر یا روابط فعال اجرا نمی‌شود/u)
    await capture(pending.page, 'stage5-accountant-pending-mobile-390.png')
    await pending.page.keyboard.press('Escape')
    await auditLayout(pending.page, 'accountant pending mobile 390')
    await pending.page.getByRole('button', { name: 'لغو رابطه و دعوت', exact: true }).click()
    await dialog.waitFor({ state: 'visible' })
    const cancellation = pending.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/accountants/owner-relations/5201' && request.method() === 'DELETE')
    pending.state.intentionalNavigation = true
    await dialog.getByRole('button', { name: 'لغو رابطه و دعوت', exact: true }).click()
    assertDeleteAction(await cancellation, 'cancel-pending', 'accountant pending cancellation')
    await waitForSingleVisible(pending.page, '.ui-v2-workspace-accountant-list-section')
    pending.state.intentionalNavigation = false
    record('stage5-accountant-pending-deadline-capability-and-relation-only-cancel-copy')
    result.pending = { copy }
  } finally {
    await closeRuntime(pending)
  }

  const terminal = await createPage(browser, baseUrl, { suite: 'accountant-terminal-detail' })
  try {
    const detailRequest = terminal.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/accountants/owner-relations/5204' && request.method() === 'GET')
    await gotoPath(
      terminal.page,
      '/operations/accountants/5204?section=duty&listScroll=48&noise=1',
      '.ui-v2-workspace-accountant-detail-header',
    )
    await detailRequest
    const canonical = await assertCanonicalUrl(terminal.page, {
      pathname: '/operations/accountants/5204',
      query: { scroll: '48' },
    }, 'accountant terminal legacy detail context')
    await waitForSingleVisible(terminal.page, '.ui-v2-workspace-accountant-detail-section')
    await terminal.page.getByText('این رابطه پایان یافته است', { exact: true }).waitFor({ state: 'visible' })
    assert.deepEqual(
      await terminal.page.getByRole('tab').allTextContents(),
      ['مشخصات'],
      'Terminal accountant capabilities are not read-only.',
    )
    assert.equal(await terminal.page.locator('.ui-v2-workspace-accountant-danger-card').count(), 0)
    assert.equal(await terminal.page.locator('.ui-v2-workspace-accountant-edit-form-card').count(), 0)
    assert.equal(await terminal.page.locator('.ui-v2-workspace-accountant-session-actions').count(), 0)
    await auditLayout(terminal.page, 'accountant terminal detail mobile 390')
    await capture(terminal.page, 'stage5-accountant-terminal-detail-mobile-390.png')
    record('stage5-accountant-terminal-detail-get-read-only-and-legacy-canonicalization')
    result.terminal = { canonical }
  } finally {
    await closeRuntime(terminal)
  }

  const duty = await createPage(browser, baseUrl, { suite: 'accountant-duty' })
  try {
    await gotoPath(duty.page, '/operations/accountants/5202?tab=duty', '.ui-v2-workspace-accountant-edit-form-card')
    const textarea = duty.page.getByLabel('شرح وظیفه')
    assert.equal(await textarea.inputValue(), 'ثبت و پیگیری معاملات روزانه')
    await textarea.fill('کنترل تسویه و گزارش پایان روز')
    const patchPromise = duty.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/accountants/owner-relations/5202' && request.method() === 'PATCH')
    await duty.page.getByRole('button', { name: 'ذخیره تغییرات', exact: true }).click()
    const patchRequest = await patchPromise
    assert.deepEqual(JSON.parse(patchRequest.postData()), {
      duty_description: 'کنترل تسویه و گزارش پایان روز',
    })
    await duty.page.getByText('شرح وظیفه ذخیره شد.', { exact: true }).waitFor({ state: 'visible' })
    assert.equal(await duty.page.getByLabel('شرح وظیفه').count(), 1, 'Accountant duty field duplicated.')
    await auditLayout(duty.page, 'accountant duty mobile 390')
    await capture(duty.page, 'stage5-accountant-duty-mobile-390.png')
    record('stage5-accountant-duty-single-editor-and-local-patch-receipt')
    result.duty = { payload: JSON.parse(patchRequest.postData()) }
  } finally {
    await closeRuntime(duty)
  }

  const sessions = await createPage(browser, baseUrl, { suite: 'accountant-sessions' })
  try {
    await gotoPath(sessions.page, '/operations/accountants/5202?tab=sessions', '.ui-v2-workspace-accountant-session-actions')
    assert.match(await visibleBodyText(sessions.page), /Chrome اصلی حسابدار/u)
    assert.match(await visibleBodyText(sessions.page), /گوشی حسابدار/u)
    assert.doesNotMatch(await visibleBodyText(sessions.page), /home_server|hidden-accountant/u)
    sessions.state.accountantSessionFailuresRemaining = 1
    const failedRefresh = sessions.page.waitForResponse((response) => new URL(response.url()).pathname === '/api/accountants/owner-relations/5202/sessions' && response.status() === 503)
    await sessions.page.getByRole('button', { name: 'نوسازی', exact: true }).click()
    await failedRefresh
    await sessions.page.getByRole('alert').waitFor({ state: 'visible' })
    assert.match(await visibleBodyText(sessions.page), /Chrome اصلی حسابدار/u)
    assert.match(await visibleBodyText(sessions.page), /گوشی حسابدار/u)
    const recovery = sessions.page.waitForResponse((response) => new URL(response.url()).pathname === '/api/accountants/owner-relations/5202/sessions' && response.status() === 200)
    await sessions.page.getByRole('button', { name: 'تلاش دوباره', exact: true }).click()
    await recovery
    await sessions.page.getByRole('alert').waitFor({ state: 'hidden' })
    const primaryRow = sessions.page.locator('.ui-list-item').filter({ hasText: 'Chrome اصلی حسابدار' })
    await primaryRow.getByRole('button', { name: 'پایان نشست', exact: true }).click()
    const confirm = sessions.page.locator('.ui-confirm-dialog')
    await confirm.waitFor({ state: 'visible' })
    assert.equal(await confirm.getByRole('heading', { name: 'پایان نشست', exact: true }).count(), 1)
    const confirmCopy = await confirm.innerText()
    assert.match(confirmCopy, /فقط دسترسی همین نشست قطع می‌شود/u)
    assert.match(confirmCopy, /نشست‌های دیگر باقی می‌مانند/u)
    const terminate = sessions.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/accountants/owner-relations/5202/sessions/accountant-primary' && request.method() === 'DELETE')
    await confirm.getByRole('button', { name: 'پایان نشست', exact: true }).click()
    await terminate
    await sessions.page.getByText('نشست «Chrome اصلی حسابدار» پایان یافت.', { exact: true }).waitFor({ state: 'visible' })
    assert.equal(await sessions.page.getByText('Chrome اصلی حسابدار', { exact: true }).count(), 0)
    const remainingRow = sessions.page.locator('.ui-list-item').filter({ hasText: 'گوشی حسابدار' })
    await remainingRow.getByText('اصلی', { exact: true }).waitFor({ state: 'visible' })
    await capture(sessions.page, 'stage5-accountant-sessions-recovery-mobile-390.png')
    record('stage5-accountant-session-retained-refresh-recovery-and-selected-termination')
    result.sessions = { confirmCopy }
  } finally {
    await closeRuntime(sessions)
  }

  const strong = await createPage(browser, baseUrl, { suite: 'accountant-strong-delete' })
  try {
    await gotoPath(strong.page, '/operations/accountants/5202?tab=danger', '.ui-v2-workspace-accountant-danger-card')
    const opened = await openStrongDeleteWithKeyboard(
      strong.page,
      'حذف حساب',
      'حذف حساب حسابدار عملیات',
      'accountant strong delete',
    )
    const trap = await trapAndRestoreProof(strong.page, opened.dialog, opened.trigger, 'accountant strong delete')
    await strong.page.waitForFunction(() => !document.querySelector('.ui-v2-workspace-account-deletion-dialog'))
    await opened.trigger.click()
    await waitForSingleVisible(strong.page, '.ui-v2-workspace-account-deletion-dialog')
    strong.state.intentionalNavigation = true
    await completeStrongDelete(strong.page, strong.state, {
      subjectName: 'حسابدار عملیات',
      endpoint: '/api/accountants/owner-relations/5202',
      gateKey: 'accountantDeleteGate',
      label: 'accountant',
    })
    await waitForSingleVisible(strong.page, '.ui-v2-workspace-accountant-list-section')
    strong.state.intentionalNavigation = false
    record('stage5-accountant-strong-delete-exact-name-ack-busy-trap-and-focus-return', { trap })
    result.strongDelete = { consequences: opened.consequences, trap }
  } finally {
    await closeRuntime(strong)
  }

  const orphan = await createPage(browser, baseUrl, { suite: 'accountant-orphan' })
  try {
    await gotoPath(orphan.page, '/operations/accountants/5203?tab=danger', '.ui-v2-workspace-accountant-danger-card')
    const body = await visibleBodyText(orphan.page)
    assert.match(body, /حساب کاربری متصل در دسترس نیست/u)
    assert.match(body, /فقط همین رابطه حذف می‌شود/u)
    assert.match(body, /حذف زنجیره‌ای حساب/u)
    assert.equal(await orphan.page.getByRole('tab', { name: 'نشست‌ها', exact: true }).count(), 0)
    assert.equal(await orphan.page.locator('.ui-v2-workspace-account-deletion-dialog').count(), 0)
    await orphan.page.getByRole('button', { name: 'حذف رابطه', exact: true }).click()
    const dialog = orphan.page.locator('.ui-confirm-dialog')
    await dialog.waitFor({ state: 'visible' })
    assert.equal(await dialog.getByRole('heading', { name: /حذف رابطه/u }).count(), 1)
    assert.match(await dialog.innerText(), /فقط همین رابطه حذف می‌شود/u)
    assert.match(await dialog.innerText(), /حذف زنجیره‌ای حساب، نشست، آفر، دعوت یا سایر روابط اجرا نمی‌شود/u)
    await capture(orphan.page, 'stage5-accountant-orphan-relation-only-mobile-390.png')
    const deletion = orphan.page.waitForRequest((request) => new URL(request.url()).pathname === '/api/accountants/owner-relations/5203' && request.method() === 'DELETE')
    orphan.state.intentionalNavigation = true
    await dialog.getByRole('button', { name: 'حذف رابطه', exact: true }).click()
    assertDeleteAction(await deletion, 'delete-relation', 'accountant orphan relation-only close')
    await waitForSingleVisible(orphan.page, '.ui-v2-workspace-accountant-list-section')
    orphan.state.intentionalNavigation = false
    record('stage5-accountant-orphan-active-relation-closes-without-account-cascade')
    result.orphan = { body }
  } finally {
    await closeRuntime(orphan)
  }

  return result
}

async function routeScrollSnapshot(page, relationListSelector = null) {
  return page.evaluate((selector) => {
    const routeScroller = document.querySelector('.app-route-scroll')
    const relationList = selector ? document.querySelector(selector) : null
    return {
      window: Math.round(window.scrollY),
      document: Math.round(document.scrollingElement?.scrollTop || 0),
      route: Math.round(routeScroller?.scrollTop || 0),
      relationList: Math.round(relationList?.scrollTop || 0),
    }
  }, relationListSelector)
}

async function runCanonicalHistory(browser, baseUrl) {
  const result = {}
  const restorationFailures = []

  const customer = await createPage(browser, baseUrl, {
    suite: 'history-customer',
    viewport: { width: 1440, height: 900 },
  })
  try {
    await gotoPath(
      customer.page,
      '/operations/customers/5102?q=%D9%85%D8%B4%D8%AA%D8%B1%DB%8C&filter=active&scroll=96&tab=sessions&panel=noise&listScroll=12',
      '.ui-v2-workspace-customer-detail-header',
    )
    const canonicalSessions = await assertCanonicalUrl(customer.page, {
      pathname: '/operations/customers/5102',
      query: { q: 'مشتری', filter: 'active', scroll: '96', tab: 'sessions' },
    }, 'customer detail context')
    const customerRestored = await customer.page.waitForFunction(() => {
      const list = document.querySelector('.ui-v2-workspace-customer-relation-list')
      return list instanceof HTMLElement && list.scrollTop >= 80
    }, null, { timeout: 2_000 }).then(() => true).catch(() => false)
    const restoredDetailScroll = await routeScrollSnapshot(customer.page, '.ui-v2-workspace-customer-relation-list')
    if (!customerRestored) restorationFailures.push({ product: 'customer', expected: 96, actual: restoredDetailScroll })
    const dangerTab = customer.page.getByRole('tab', { name: 'حساس', exact: true })
    await dangerTab.click()
    const canonicalDanger = await assertCanonicalUrl(customer.page, {
      pathname: '/operations/customers/5102',
      query: { q: 'مشتری', filter: 'active', scroll: '96', tab: 'danger' },
    }, 'customer danger context')
    await customer.page.goBack({ waitUntil: 'domcontentloaded' })
    await customer.page.getByRole('tab', { name: 'نشست‌ها', exact: true }).waitFor({ state: 'visible' })
    await customer.page.waitForFunction(() => document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() === 'نشست‌ها')
    await customer.page.goForward({ waitUntil: 'domcontentloaded' })
    await customer.page.waitForFunction(() => document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() === 'حساس')
    await waitForSingleVisible(customer.page, '.ui-v2-workspace-customer-root')
    await customer.page.locator('.ui-v2-workspace-customer-root .ui-v2-workspace-adapter__back').click()
    const canonicalList = await assertCanonicalUrl(customer.page, {
      pathname: '/operations/customers',
      query: { q: 'مشتری', filter: 'active', scroll: '96' },
    }, 'customer back-to-list context')
    await waitForSingleVisible(customer.page, '.ui-v2-workspace-customer-relation-list')
    await customer.page.goBack({ waitUntil: 'domcontentloaded' })
    await customer.page.waitForFunction(() => location.pathname === '/operations/customers/5102' && new URLSearchParams(location.search).get('tab') === 'danger')
    record('stage5-customer-canonical-q-filter-scroll-tab-and-history', { restoredDetailScroll })
    result.customer = { canonicalSessions, canonicalDanger, canonicalList, restoredDetailScroll }
  } finally {
    await closeRuntime(customer)
  }

  const accountant = await createPage(browser, baseUrl, {
    suite: 'history-accountant',
    viewport: { width: 1440, height: 900 },
  })
  try {
    await gotoPath(
      accountant.page,
      '/operations/accountants/5202?q=%D8%AD%D8%B3%D8%A7%D8%A8%D8%AF%D8%A7%D8%B1&filter=active&scroll=96&tab=sessions&section=noise&listScroll=12',
      '.ui-v2-workspace-accountant-detail-header',
    )
    const canonicalSessions = await assertCanonicalUrl(accountant.page, {
      pathname: '/operations/accountants/5202',
      query: { q: 'حسابدار', filter: 'active', scroll: '96', tab: 'sessions' },
    }, 'accountant detail context')
    const accountantRestored = await accountant.page.waitForFunction(() => {
      const routeScroller = document.querySelector('.app-route-scroll')
      return (window.scrollY >= 80)
        || (document.scrollingElement?.scrollTop || 0) >= 80
        || (routeScroller instanceof HTMLElement && routeScroller.scrollTop >= 80)
    }, null, { timeout: 2_000 }).then(() => true).catch(() => false)
    const restoredDetailScroll = await routeScrollSnapshot(accountant.page)
    if (!accountantRestored) restorationFailures.push({ product: 'accountant', expected: 96, actual: restoredDetailScroll })
    await accountant.page.getByRole('tab', { name: 'حساس', exact: true }).click()
    const canonicalDanger = await assertCanonicalUrl(accountant.page, {
      pathname: '/operations/accountants/5202',
      query: { q: 'حسابدار', filter: 'active', scroll: '96', tab: 'danger' },
    }, 'accountant danger context')
    await accountant.page.goBack({ waitUntil: 'domcontentloaded' })
    await accountant.page.waitForFunction(() => document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() === 'نشست‌ها')
    await accountant.page.goForward({ waitUntil: 'domcontentloaded' })
    await accountant.page.waitForFunction(() => document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() === 'حساس')
    await waitForSingleVisible(accountant.page, '.ui-v2-workspace-accountant-root')
    await accountant.page.locator('.ui-v2-workspace-accountant-root .ui-v2-workspace-adapter__back').click()
    const canonicalList = await assertCanonicalUrl(accountant.page, {
      pathname: '/operations/accountants',
      query: { q: 'حسابدار', filter: 'active', scroll: '96' },
    }, 'accountant back-to-list context')
    await waitForSingleVisible(accountant.page, '.ui-v2-workspace-accountant-relation-list')
    await accountant.page.goBack({ waitUntil: 'domcontentloaded' })
    await accountant.page.waitForFunction(() => location.pathname === '/operations/accountants/5202' && new URLSearchParams(location.search).get('tab') === 'danger')
    await gotoPath(
      accountant.page,
      '/operations/accountants/5202?q=%20%D8%AD%D8%B3%D8%A7%D8%A8%D8%AF%D8%A7%D8%B1%20&filter=active&listScroll=48&section=duty&noise=1',
      '.ui-v2-workspace-accountant-edit-form-card',
    )
    const canonicalLegacyDuty = await assertCanonicalUrl(accountant.page, {
      pathname: '/operations/accountants/5202',
      query: { q: 'حسابدار', filter: 'active', scroll: '48', tab: 'duty' },
    }, 'accountant native legacy duty context')
    record('stage5-accountant-canonical-q-filter-scroll-tab-and-history', { restoredDetailScroll })
    result.accountant = {
      canonicalSessions,
      canonicalDanger,
      canonicalList,
      canonicalLegacyDuty,
      restoredDetailScroll,
    }
  } finally {
    await closeRuntime(accountant)
  }

  assert.deepEqual(restorationFailures, [], `Stage 5 canonical scroll restoration failed: ${JSON.stringify(restorationFailures)}`)
  return result
}

async function assertCreateBusyContract(runtime, options) {
  const { product, createButton, fields, submitName, endpoint, gateKey } = options
  const trigger = runtime.page.getByRole('button', { name: createButton, exact: true }).first()
  await focusProof(runtime.page, trigger, `${product} create trigger`)
  await runtime.page.keyboard.press('Enter')
  const dialog = runtime.page.getByRole('dialog', { name: createButton, exact: true })
  await dialog.waitFor({ state: 'visible' })
  for (const [label, value] of fields) {
    // AppFormField wraps its hint inside the native <label>, so the computed
    // accessible name can append the hint even though the visible label is
    // stable. Match the leading field label without assuming an exact name.
    await dialog.getByLabel(label).fill(value)
  }
  runtime.state[gateKey] = deferred()
  const requestPromise = runtime.page.waitForRequest((request) => new URL(request.url()).pathname === endpoint && request.method() === 'POST')
  await dialog.getByRole('button', { name: submitName, exact: true }).click()
  await requestPromise
  await runtime.page.waitForFunction((selector) => document.querySelector(selector)?.getAttribute('aria-busy') === 'true', `.ui-v2-workspace-${product}-create-${product === 'customer' ? 'fieldset' : 'panel'}`)
  // `:disabled` includes controls disabled transitively by a busy <fieldset>;
  // `[disabled]` would miss that native HTML state.
  const enabledVisibleControls = await dialog.locator('button:not(:disabled):visible, input:not(:disabled):visible, textarea:not(:disabled):visible, select:not(:disabled):visible').count()
  assert.equal(enabledVisibleControls, 0, `${product}: create dialog has enabled visible controls while busy.`)
  await runtime.page.keyboard.press('Escape')
  assert.equal(await dialog.isVisible(), true, `${product}: Escape dismissed busy create.`)
  await runtime.page.locator('.ui-v2-workspace-overlay-backdrop').dispatchEvent('click')
  assert.equal(await dialog.isVisible(), true, `${product}: backdrop dismissed busy create.`)
  await capture(runtime.page, `stage5-${product}-create-busy-mobile-390.png`)
  runtime.state[gateKey].resolve()
  runtime.state[gateKey] = null
  await dialog.waitFor({ state: 'hidden' })
  assert.equal(await trigger.evaluate((element) => document.activeElement === element), true, `${product}: create completion did not restore focus.`)
  return { enabledVisibleControls }
}

async function runCreateBusy(browser, baseUrl) {
  const customer = await createPage(browser, baseUrl, { suite: 'create-busy-customer' })
  let customerResult
  try {
    await gotoPath(customer.page, '/operations/customers', '.ui-v2-workspace-customer-relation-list')
    customerResult = await assertCreateBusyContract(customer, {
      product: 'customer',
      createButton: 'افزودن مشتری',
      fields: [
        ['نام مدیریتی', 'مشتری تازه'],
        ['شماره موبایل', '09129990011'],
      ],
      submitName: 'ثبت دعوت مشتری',
      endpoint: '/api/customers/owner-relations',
      gateKey: 'customerCreateGate',
    })
    await customer.page.getByText('دعوت مشتری ثبت شد', { exact: true }).waitFor({ state: 'visible' })
    record('stage5-customer-create-busy-cannot-dismiss-and-restores-focus')
  } finally {
    await closeRuntime(customer)
  }

  const accountant = await createPage(browser, baseUrl, { suite: 'create-busy-accountant' })
  let accountantResult
  try {
    await gotoPath(accountant.page, '/operations/accountants', '.ui-v2-workspace-accountant-relation-list')
    accountantResult = await assertCreateBusyContract(accountant, {
      product: 'accountant',
      createButton: 'افزودن حسابدار',
      fields: [
        ['نام کاربری جهانی', 'accountant_new'],
        ['نام نمایشی رابطه', 'حسابدار تازه'],
        ['شماره موبایل', '09129990022'],
        ['شرح وظیفه', 'کنترل گزارش روزانه'],
      ],
      submitName: 'ثبت دعوت حسابدار',
      endpoint: '/api/accountants/owner-relations',
      gateKey: 'accountantCreateGate',
    })
    await accountant.page.getByText('دعوت حسابدار ثبت شد', { exact: true }).waitFor({ state: 'visible' })
    record('stage5-accountant-create-busy-cannot-dismiss-and-restores-focus')
  } finally {
    await closeRuntime(accountant)
  }
  return { customer: customerResult, accountant: accountantResult }
}

async function runAccessibility(browser, baseUrl) {
  const result = {}
  const reduced = await createPage(browser, baseUrl, {
    suite: 'accessibility-reduced-motion',
    reducedMotion: 'reduce',
    viewport: { width: 360, height: 740 },
  })
  try {
    await gotoPath(reduced.page, '/operations/customers/5102?tab=danger', '.ui-v2-workspace-customer-danger-card')
    const rootMotion = await reducedMotionProof(
      reduced.page,
      '.ui-v2-workspace-customer-root, .ui-v2-workspace-customer-root *',
      'customer workspace reduced motion',
    )
    await reduced.page.getByRole('button', { name: 'بررسی و حذف حساب', exact: true }).click()
    const dialog = reduced.page.getByRole('dialog', { name: 'حذف حساب مشتری مالی', exact: true })
    await dialog.waitFor({ state: 'visible' })
    const overlayMotion = await reducedMotionProof(
      reduced.page,
      '.ui-v2-workspace-account-deletion-backdrop, .ui-v2-workspace-account-deletion-backdrop *',
      'strong-delete reduced motion',
    )
    const dialogContrast = await contrastProof(reduced.page, [
      '.ui-v2-workspace-account-deletion-dialog__header h2',
      '.ui-v2-workspace-account-deletion-dialog__consequences li',
      '.ui-v2-workspace-account-deletion-dialog__acknowledgement span',
    ], 'strong-delete representative text')
    await auditLayout(reduced.page, 'customer strong-delete reduced-motion mobile 360')
    await reduced.page.keyboard.press('Escape')
    result.reducedMotion = { rootMotion, overlayMotion, dialogContrast }
    record('stage5-reduced-motion-runtime-and-strong-delete-contrast')
  } finally {
    await closeRuntime(reduced)
  }

  const customer = await createPage(browser, baseUrl, {
    suite: 'accessibility-customer-reflow',
    viewport: { width: 360, height: 740 },
  })
  try {
    await gotoPath(customer.page, '/operations/customers/5102?tab=limits', '.ui-v2-workspace-customer-edit-card')
    await customer.page.getByLabel('حداکثر تعداد روزانه').fill('۷')
    await customer.page.getByRole('button', { name: 'مرور تغییرات', exact: true }).click()
    await customer.page.locator('.ui-v2-workspace-customer-financial-table').waitFor({ state: 'visible' })
    const contrast = await contrastProof(customer.page, [
      '.ui-v2-workspace-customer-detail-header h2',
      '.ui-v2-workspace-customer-financial-table tbody th',
      '.ui-v2-workspace-customer-financial-table tbody td:last-child',
    ], 'customer financial representative text')
    const reflow = await textResizeProof(customer.page, 'customer financial review mobile 360')
    const cta = await assertTargetUnobscured(
      customer.page.getByRole('button', { name: 'ثبت تغییرات', exact: true }),
      'customer financial submit at 200% recovery',
    )
    result.customer = { contrast, reflow, cta }
    record('stage5-customer-200-percent-text-reflow-contrast-target-and-cta')
  } finally {
    await closeRuntime(customer)
  }

  const accountant = await createPage(browser, baseUrl, {
    suite: 'accessibility-accountant-reflow',
    viewport: { width: 360, height: 740 },
  })
  try {
    await gotoPath(accountant.page, '/operations/accountants/5202?tab=duty', '.ui-v2-workspace-accountant-edit-form-card')
    const focus = await focusProof(
      accountant.page,
      accountant.page.getByLabel('شرح وظیفه'),
      'accountant duty keyboard focus',
    )
    const contrast = await contrastProof(accountant.page, [
      '.ui-v2-workspace-accountant-detail-header h2',
      '.ui-v2-workspace-accountant-detail-header p',
      '.ui-v2-workspace-accountant-edit-form-card .ui-form-field__label',
    ], 'accountant duty representative text')
    const reflow = await textResizeProof(accountant.page, 'accountant duty mobile 360')
    const cta = await assertTargetUnobscured(
      accountant.page.getByRole('button', { name: 'ذخیره تغییرات', exact: true }),
      'accountant duty submit after 200% recovery',
    )
    result.accountant = { focus, contrast, reflow, cta }
    record('stage5-accountant-200-percent-text-reflow-focus-contrast-target-and-cta')
  } finally {
    await closeRuntime(accountant)
  }
  return result
}

const SUITES = Object.freeze({
  responsive: runResponsiveMatrix,
  customer: runCustomerWorkflows,
  accountant: runAccountantWorkflows,
  history: runCanonicalHistory,
  'create-busy': runCreateBusy,
  accessibility: runAccessibility,
})

async function startIsolatedVite() {
  const port = await new Promise((resolve, reject) => {
    const probe = createNetServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address()
      if (!address || typeof address !== 'object') {
        probe.close(() => reject(new Error('Ephemeral-port probe returned no TCP address.')))
        return
      }
      probe.close((error) => {
        if (error) reject(error)
        else resolve(address.port)
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
  try {
    await vite.listen()
  } catch (error) {
    await vite.close().catch(() => undefined)
    throw error
  }
  const address = vite.httpServer?.address()
  assert.ok(address && typeof address === 'object', 'Vite did not allocate an ephemeral port.')
  return { vite, baseUrl: `http://127.0.0.1:${address.port}` }
}

function failureKey(row) {
  return `${row.suite}\u0000${row.method}\u0000${row.pathname}\u0000${row.status}`
}

function countRows(rows, keyBuilder) {
  const counts = new Map()
  for (const row of rows) {
    const key = keyBuilder(row)
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return counts
}

function classifyDiagnostics() {
  const expectedHttpFailures = runtimeStates.flatMap((state) => state.expectedHttpFailures.map((row) => ({
    ...row,
    suite: state.suite,
  })))
  const expectedCounts = countRows(expectedHttpFailures, failureKey)
  const actualCounts = countRows(diagnostics.httpFailures, failureKey)
  const httpViolations = []
  for (const key of new Set([...expectedCounts.keys(), ...actualCounts.keys()])) {
    const expected = expectedCounts.get(key) || 0
    const actual = actualCounts.get(key) || 0
    if (expected !== actual) httpViolations.push({ key, expected, actual })
  }

  const expectedPathStatus = new Set(expectedHttpFailures.map((row) => `${row.suite}\u0000${row.pathname}\u0000${row.status}`))
  const expectedConsoleErrors = []
  const unexpectedConsoleErrors = []
  for (const row of diagnostics.consoleErrors) {
    const statusMatch = row.text.match(/status of (\d+)/u)
    let pathname = null
    try {
      pathname = row.location?.url ? new URL(row.location.url).pathname : null
    } catch {
      pathname = null
    }
    const key = `${row.suite}\u0000${pathname}\u0000${statusMatch ? Number(statusMatch[1]) : null}`
    if (expectedPathStatus.has(key)) expectedConsoleErrors.push(row)
    else unexpectedConsoleErrors.push(row)
  }
  return {
    expectedHttpFailures,
    httpViolations,
    expectedConsoleErrors,
    unexpectedConsoleErrors,
  }
}

async function main() {
  const startedAt = new Date().toISOString()
  const allSuiteNames = Object.keys(SUITES)
  const selectedSuites = ONLY_SUITE ? [ONLY_SUITE] : allSuiteNames
  for (const suite of selectedSuites) {
    assert.ok(Object.hasOwn(SUITES, suite), `Unknown STAGE5_BROWSER_ONLY suite: ${suite}`)
  }
  const isFullRun = !ONLY_SUITE && !DIAGNOSTIC_MODE

  const sourcePre = SOURCE_INITIAL
  const gitPre = GIT_INITIAL
  const harnessPre = HARNESS_INITIAL
  const environmentPre = ENVIRONMENT_INITIAL
  let browser = null
  let vite = null
  let baseUrl = null
  let browserVersion = null
  let failure = null
  let classifiedDiagnostics = null
  const suiteResults = {}

  try {
    const local = await startIsolatedVite()
    vite = local.vite
    baseUrl = local.baseUrl
    progress('vite-ready', { baseUrl, selectedSuites, diagnosticMode: DIAGNOSTIC_MODE })
    browser = await chromium.launch({
      headless: true,
      args: ['--disable-dev-shm-usage'],
    })
    browserVersion = browser.version()

    for (const suite of selectedSuites) {
      progress('suite-start', { suite })
      suiteResults[suite] = await SUITES[suite](browser, baseUrl)
      progress('suite-complete', { suite })
    }

    classifiedDiagnostics = classifyDiagnostics()
    assert.deepEqual(classifiedDiagnostics.httpViolations, [], 'HTTP diagnostic failure contract drifted.')
    assert.deepEqual(classifiedDiagnostics.unexpectedConsoleErrors, [], 'Unexpected browser console errors occurred.')
    assert.deepEqual(diagnostics.pageErrors, [], 'Uncaught page errors occurred.')
    assert.deepEqual(diagnostics.unexpectedRequestFailures, [], 'Unexpected same-origin request failures occurred.')
    assert.deepEqual(diagnostics.externalRequestsAllowed, [], 'External traffic escaped interception.')
    assert.deepEqual(diagnostics.unexpectedApiRequests, [], 'Unexpected API calls occurred.')
    assert.deepEqual(diagnostics.unexpectedWebSockets, [], 'Unexpected WebSocket transport occurred.')
    assert.deepEqual(diagnostics.unexpectedEventSources, [], 'Unexpected EventSource transport occurred.')
    assert.ok(
      diagnostics.webSockets.some((row) => row.kind === 'app-realtime' && row.valid),
      'No authenticated same-origin application realtime socket was observed.',
    )
    record('stage5-browser-console-request-api-and-realtime-diagnostics-classified', {
      expectedHttpFailures: classifiedDiagnostics.expectedHttpFailures.length,
      externalRequestsBlocked: diagnostics.externalRequestsBlocked.length,
    })
  } catch (error) {
    failure = {
      name: error instanceof Error ? error.name : 'Error',
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : null,
    }
    process.exitCode = 1
  } finally {
    const teardownErrors = []
    if (browser) {
      try {
        await browser.close()
      } catch (error) {
        teardownErrors.push({ target: 'chromium', message: error instanceof Error ? error.message : String(error) })
      }
    }
    if (vite) {
      try {
        await vite.close()
      } catch (error) {
        teardownErrors.push({ target: 'vite', message: error instanceof Error ? error.message : String(error) })
      }
    }
    try {
      fs.rmSync(VITE_CACHE_DIR, { recursive: true, force: true })
    } catch (error) {
      teardownErrors.push({ target: 'vite-cache', message: error instanceof Error ? error.message : String(error) })
    }
    if (teardownErrors.length > 0) {
      failure = failure || {
        name: 'Stage5TeardownError',
        message: 'Chromium or Vite did not close cleanly.',
        stack: null,
      }
      failure.teardownErrors = teardownErrors
      process.exitCode = 1
    }

    let sourcePost
    let gitPost
    let harnessPost
    let environmentPost
    try { sourcePost = sourceSnapshot() } catch (error) { sourcePost = { error: String(error) } }
    try { gitPost = gitSnapshot() } catch (error) { gitPost = { error: String(error) } }
    try { harnessPost = fileSnapshot(HARNESS_PATH) } catch (error) { harnessPost = { error: String(error) } }
    try { environmentPost = environmentSnapshot() } catch (error) { environmentPost = { error: String(error) } }

    const sourceIdentical = JSON.stringify(sourcePost) === JSON.stringify(sourcePre)
    const gitIdentical = JSON.stringify(gitPost) === JSON.stringify(gitPre)
    const harnessIdentical = JSON.stringify(harnessPost) === JSON.stringify(harnessPre)
    const environmentIdentical = JSON.stringify(environmentPost) === JSON.stringify(environmentPre)
      && Array.isArray(environmentPost) && !environmentPost.some((entry) => entry.exists)
    if (!failure && (!sourceIdentical || !gitIdentical || !harnessIdentical || !environmentIdentical)) {
      failure = {
        name: 'Stage5BindingIntegrityError',
        message: 'Source, Git, harness, or Vite environment changed during browser capture.',
        stack: null,
      }
      process.exitCode = 1
    }
    if (!classifiedDiagnostics) classifiedDiagnostics = classifyDiagnostics()
    const promotable = Boolean(
      !failure
      && isFullRun
      && !DIAGNOSTIC_MODE
      && gitPre.trackedClean
      && sourceIdentical
      && gitIdentical
      && harnessIdentical
      && environmentIdentical,
    )
    const completedAt = new Date().toISOString()
    const metrics = {
      schemaVersion: 1,
      stage: 5,
      scope: 'customer-accountant-workspaces',
      status: failure ? 'failed' : isFullRun ? 'passed' : 'diagnostic-passed',
      promotable,
      diagnosticMode: DIAGNOSTIC_MODE,
      runAuthorization: RUN_AUTHORIZATION,
      runId: RUN_ID,
      startedAt,
      completedAt,
      browser: { name: 'chromium', version: browserVersion, headless: true },
      runtimeVersions: RUNTIME_VERSIONS,
      sanitizedEnvironmentKeys: SANITIZED_ENV_KEYS,
      baseUrl,
      selectedSuites,
      requiredViewports: VIEWPORTS,
      breakpointViewports: BREAKPOINT_VIEWPORTS,
      sourceBinding: {
        expectedSha256: process.env.STAGE5_EXPECTED_SOURCE_SHA256,
        sha256: SOURCE_BINDING_SHA256,
        pre: sourcePre,
        post: sourcePost,
        identical: sourceIdentical,
      },
      gitBinding: { pre: gitPre, post: gitPost, identical: gitIdentical },
      harnessBinding: { pre: harnessPre, post: harnessPost, identical: harnessIdentical },
      environmentBinding: { pre: environmentPre, post: environmentPost, identical: environmentIdentical },
      assertionSummary: {
        total: assertions.length + (failure ? 1 : 0),
        passed: assertions.filter((row) => row.passed).length,
        failed: failure ? 1 : 0,
      },
      assertions,
      suiteResults,
      screenshots,
      diagnostics: { ...diagnostics, ...classifiedDiagnostics },
      failure,
    }
    fs.writeFileSync(METRICS_PATH, `${JSON.stringify(metrics, null, 2)}\n`)
    const binding = {
      schemaVersion: 1,
      stage: 5,
      scope: 'customer-accountant-workspaces',
      status: metrics.status,
      promotable,
      diagnosticMode: DIAGNOSTIC_MODE,
      runId: RUN_ID,
      selectedSuites,
      sourceBindingSha256: SOURCE_BINDING_SHA256,
      sourceIdentical,
      gitIdentical,
      harnessIdentical,
      environmentIdentical,
      implementationCommit: gitPre.commit,
      implementationTree: gitPre.tree,
      harnessSha256: harnessPre.sha256,
      failure,
    }
    fs.writeFileSync(
      path.join(OUTPUT_DIR, promotable ? 'stage5-final-source-binding.json' : 'stage5-non-promotable-source-binding.json'),
      `${JSON.stringify(binding, null, 2)}\n`,
    )
    progress('run-finished', {
      status: metrics.status,
      promotable,
      metrics: METRICS_PATH,
      assertions: assertions.length,
      screenshots: screenshots.length,
    })
  }
}

await main()
