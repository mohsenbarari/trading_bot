#!/usr/bin/env node

/*
 * Stage 6 aggregate browser acceptance evidence
 *
 * This ignored, evidence-only runner binds one clean implementation commit,
 * then captures the current delivered Stage 6 browser surface with synthetic
 * fixtures only:
 *   - Phase 1: Admin landing role matrix, semantic keyboard path and reflow;
 *   - Phase 2: private directory query, scroll/list-detail/recovery;
 *   - Phase 3: public-profile privacy, authority, recovery and motion.
 *
 * It never changes product source, uses no real backend data, blocks browser
 * traffic outside the local Vite origin, does not touch Figma or Sites, and
 * only marks a run promotable after all three complete with stable pre/post
 * source, Git, harness and Vite-environment fingerprints.
 */

import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync, spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'

const WORKTREE = '/tmp/trading-bot-webapp-uiux-redesign-v2'
const EXPECTED_BRANCH = 'condidate/webapp-ui-ux-redesign-v2'
const FRONTEND = path.join(WORKTREE, 'frontend')
const HARNESS_PATH = fileURLToPath(import.meta.url)
const EVIDENCE_DIR = path.dirname(HARNESS_PATH)
const PHASE2_HARNESS = path.join(EVIDENCE_DIR, 'stage6-phase2-admin-directory-browser-harness.mjs')
const PHASE3_HARNESS = path.join(EVIDENCE_DIR, 'stage6-phase3-profile-privacy-authority-browser-harness.mjs')
const RUN_AUTHORIZATION = 'STAGE6 AGGREGATE BROWSER ACCEPTANCE — RUN'
const FIXED_TIME = '2026-08-11T21:00:00.000Z'
const FIXED_EPOCH_SECONDS = Math.floor(Date.parse(FIXED_TIME) / 1000)
const RUN_ID = 'uiux-stage6-aggregate-browser-' + new Date().toISOString().replace(/[-:.]/gu, '')
const OUTPUT_DIR = path.join(EVIDENCE_DIR, 'runs', RUN_ID)
const METRICS_PATH = path.join(OUTPUT_DIR, 'stage6-aggregate-browser-metrics.json')
const BINDING_PATH = path.join(OUTPUT_DIR, 'stage6-aggregate-source-binding.json')
const VITE_CACHE_DIR = path.join(tmpdir(), RUN_ID + '-vite-cache')

const VIEWPORTS = Object.freeze([
  { label: 'mobile-360', width: 360, height: 740 },
  { label: 'mobile-375', width: 375, height: 812 },
  { label: 'mobile-390', width: 390, height: 844 },
  { label: 'mobile-414', width: 414, height: 896 },
  { label: 'mobile-430', width: 430, height: 932 },
  { label: 'desktop-1440', width: 1440, height: 900 },
])

const ROLE_CASES = Object.freeze([
  {
    key: 'middle',
    role: 'مدیر میانی',
    expectedLabels: ['ارسال لینک دعوت', 'مدیریت کاربران'],
    forbiddenLabels: ['مدیریت کالاها', 'ساخت کانال', 'پیام‌های مدیریت', 'تنظیمات سیستم'],
  },
  {
    key: 'super',
    role: 'مدیر ارشد',
    expectedLabels: ['ارسال لینک دعوت', 'مدیریت کاربران', 'مدیریت کالاها', 'ساخت کانال', 'پیام‌های مدیریت', 'تنظیمات سیستم'],
    forbiddenLabels: [],
  },
])

const SOURCE_SCOPE = Object.freeze([
  'api',
  'core',
  'schemas.py',
  'frontend/index.html',
  'frontend/package.json',
  'frontend/package-lock.json',
  'frontend/vite.config.ts',
  'frontend/src',
])
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
  return { path: path.relative(WORKTREE, filePath).split(path.sep).join('/'), bytes: stat.size, sha256: sha256File(filePath) }
}

function gitText(args) {
  return execFileSync('git', args, {
    cwd: WORKTREE,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim()
}

function sourceSnapshot() {
  const output = gitText(['ls-files', '--', ...SOURCE_SCOPE])
  const files = output ? output.split('\n').filter(Boolean).sort() : []
  assert.ok(files.length > 0, 'No tracked Stage 6 source files were found.')
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
      path: 'frontend/' + file,
      exists: fs.existsSync(absolutePath),
      ...(fs.existsSync(absolutePath) ? {
        bytes: fs.statSync(absolutePath).size,
        sha256: sha256File(absolutePath),
      } : {}),
    }
  })
}

function sanitizeViteEnvironment() {
  const keys = Object.keys(process.env)
    .filter((key) => key.startsWith('VITE_') || SANITIZED_ENV_EXACT_KEYS.includes(key))
  for (const key of keys) delete process.env[key]
  process.env.NODE_ENV = 'development'
}

function safeError(error) {
  return {
    name: error instanceof Error ? error.name : 'Error',
    message: error instanceof Error ? error.message : String(error),
  }
}

function base64Url(value) {
  return Buffer.from(JSON.stringify(value)).toString('base64url')
}

function createJwt(subject) {
  return base64Url({ alg: 'none', typ: 'JWT' }) + '.' +
    base64Url({ sub: String(subject), exp: FIXED_EPOCH_SECONDS + 3600 }) +
    '.synthetic'
}

function phase1Owner(roleCase) {
  const superAdmin = roleCase.key === 'super'
  return {
    id: superAdmin ? 9611 : 9612,
    account_name: superAdmin ? 'stage6_aggregate_super' : 'stage6_aggregate_middle',
    full_name: superAdmin ? 'مدیر ارشد آزمایشی' : 'مدیر میانی آزمایشی',
    role: roleCase.role,
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    customer_tier: null,
    can_connect_telegram: false,
    telegram_linked: false,
  }
}

function directoryUser(index) {
  return {
    id: 9701 + index,
    full_name: 'کاربر آزمایشی ' + String(index + 1),
    account_name: 'stage6_aggregate_user_' + String(index + 1).padStart(2, '0'),
    mobile_number: 'ثبت\u200cنشده',
    telegram_id: 0,
    role: index % 4 === 0 ? 'عادی' : 'تماشا',
    account_status: 'active',
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

const DIRECTORY_USERS = Object.freeze(Array.from({ length: 32 }, (_value, index) => directoryUser(index)))

const diagnostics = {
  phase1ConsoleErrors: [],
  phase1PageErrors: [],
  phase1UnexpectedApiRequests: [],
  phase1SameOriginRequestFailures: [],
  phase1NonApiRequestFailures: [],
  phase1UnexpectedTransports: [],
  phase1ExternalRequestsBlocked: [],
}
const assertions = []
const screenshots = []
const phase1RuntimeSummaries = []
const childRuns = []

function record(id, details = {}) {
  assertions.push({ id, passed: true, ...details })
}

function progress(stage, details = {}) {
  process.stdout.write(JSON.stringify({
    event: 'stage6-aggregate-browser-progress',
    runId: RUN_ID,
    stage,
    ...details,
  }) + '\n')
}

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify(body),
  })
}

async function handlePhase1Api(route, state) {
  const request = route.request()
  const url = new URL(request.url())
  const pathname = url.pathname
  const method = request.method()
  state.apiRequests.push({
    pathname,
    method,
    queryKeys: [...url.searchParams.keys()].sort(),
  })

  if (pathname === '/api/auth/me' && method === 'GET') return json(route, state.owner)
  if (pathname === '/api/auth/refresh' && method === 'POST') {
    return json(route, { access_token: state.token, refresh_token: state.token })
  }
  if (pathname === '/api/users/' && method === 'GET') return json(route, DIRECTORY_USERS)
  if (/^\/api\/users\/\d+$/u.test(pathname) && method === 'GET') {
    const id = Number(pathname.split('/').pop())
    return json(route, DIRECTORY_USERS.find((candidate) => candidate.id === id) || { detail: 'not-found' }, DIRECTORY_USERS.some((candidate) => candidate.id === id) ? 200 : 404)
  }
  if ((pathname === '/api/notifications' || pathname === '/api/notifications/') && method === 'GET') return json(route, [])
  if (pathname === '/api/notifications/unread-count' && method === 'GET') return json(route, 0)
  if (pathname === '/api/notifications/push/public-key' && method === 'GET') return json(route, { enabled: false, public_key: null })
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

  diagnostics.phase1UnexpectedApiRequests.push({ suite: state.suite, pathname, method, queryKeys: [...url.searchParams.keys()].sort() })
  return json(route, { detail: 'stage6_aggregate_unexpected_api_request' }, 501)
}

function installPhase1BrowserFakes(page, state) {
  return page.addInitScript(({ token, owner }) => {
    window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
    localStorage.setItem('auth_token', token)
    localStorage.setItem('refresh_token', token)
    localStorage.setItem('current_user_summary', JSON.stringify(owner))
    localStorage.removeItem('suspended_refresh_token')
    sessionStorage.clear()

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
        void window.__stage6AggregateRecordTransport({
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
        this['on' + type]?.(event)
        for (const listener of this.listeners.get(type) || []) listener.call(this, event)
      }
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
        void window.__stage6AggregateRecordTransport({
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
        this['on' + type]?.(event)
        for (const listener of this.listeners.get(type) || []) listener.call(this, event)
      }
      close() { this.readyState = FakeEventSource.CLOSED }
    }

    window.WebSocket = FakeSocket
    window.EventSource = FakeEventSource
    window.confirm = () => false
    window.alert = () => undefined
    window.open = () => null
  }, { token: state.token, owner: state.owner })
}

async function createPhase1Runtime(browser, baseUrl, roleCase, viewport) {
  const state = {
    suite: 'phase1-' + roleCase.key + '-' + viewport.label,
    role: roleCase.role,
    viewport,
    owner: phase1Owner(roleCase),
    token: createJwt(roleCase.key === 'super' ? 9611 : 9612),
    apiRequests: [],
  }
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
  await context.exposeBinding('__stage6AggregateRecordTransport', (_source, event) => {
    if (!event.valid) diagnostics.phase1UnexpectedTransports.push({ suite: state.suite, ...event })
  })
  await installPhase1BrowserFakes(page, state)

  const origin = new URL(baseUrl).origin
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) {
      diagnostics.phase1ExternalRequestsBlocked.push({
        suite: state.suite,
        origin: url.origin,
        pathname: url.pathname,
        method: route.request().method(),
      })
      if (url.origin === 'https://telegram.org' && url.pathname.endsWith('.js')) {
        return route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: '' })
      }
      return route.abort('blockedbyclient')
    }
    if (url.pathname.startsWith('/api/')) return handlePhase1Api(route, state)
    return route.continue()
  })
  page.on('console', (message) => {
    if (message.type() === 'error') {
      diagnostics.phase1ConsoleErrors.push({
        suite: state.suite,
        text: message.text(),
        location: { url: message.location().url, lineNumber: message.location().lineNumber },
      })
    }
  })
  page.on('pageerror', (error) => diagnostics.phase1PageErrors.push({ suite: state.suite, text: error.message }))
  page.on('requestfailed', (request) => {
    const url = new URL(request.url())
    if (url.origin !== origin) return
    const failure = request.failure()?.errorText || ''
    const row = {
      suite: state.suite,
      pathname: url.pathname,
      method: request.method(),
      failure,
    }
    if (url.pathname.startsWith('/api/')) diagnostics.phase1SameOriginRequestFailures.push(row)
    else if (failure !== 'net::ERR_ABORTED') diagnostics.phase1NonApiRequestFailures.push(row)
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

async function openAdminLanding(page) {
  await page.goto('/admin', { waitUntil: 'domcontentloaded' })
  await page.locator('.admin-panel-container').waitFor({ state: 'visible', timeout: 30_000 })
  await page.getByRole('heading', { name: 'مرکز مدیریت', exact: true }).waitFor({ state: 'visible', timeout: 30_000 })
  await settle(page)
}

async function assertAdminLanding(page, roleCase, viewport) {
  const contract = await page.evaluate(() => {
    const nav = document.querySelector('.admin-panel-container')
    const list = nav?.querySelector(':scope > ul.admin-action-list')
    const items = list ? [...list.querySelectorAll(':scope > li.admin-action-list__item')] : []
    const actions = items.map((item) => item.querySelector(':scope > button.admin-panel-action'))
    const routeScroll = document.querySelector('.app-route-scroll')
    const actionContract = actions.map((element) => {
      if (!(element instanceof HTMLElement)) return { valid: false, visible: false, width: 0, height: 0, centerClickable: false, text: '' }
      const rect = element.getBoundingClientRect()
      const visible = rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= window.innerHeight
      const center = visible ? document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) : null
      return {
        valid: element.tagName === 'BUTTON' && element.getAttribute('type') === 'button',
        visible,
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        centerClickable: !visible || center === element || Boolean(center?.closest('button') === element),
        text: (element.textContent || '').replace(/\s+/gu, ' ').trim(),
      }
    })
    return {
      navTag: nav?.tagName,
      navLabel: nav?.getAttribute('aria-label'),
      listTag: list?.tagName,
      itemsAreLi: items.every((item) => item.tagName === 'LI'),
      actionContract,
      legacyAccordionCount: document.querySelectorAll('.admin-accordion').length,
      overflow: {
        document: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        body: document.body.scrollWidth > document.body.clientWidth,
        route: routeScroll instanceof HTMLElement ? routeScroll.scrollWidth > routeScroll.clientWidth : false,
      },
    }
  })

  assert.equal(contract.navTag, 'NAV', roleCase.key + '/' + viewport.label + ': landing navigation is not semantic')
  assert.equal(contract.navLabel, 'ابزارهای مدیریت', roleCase.key + '/' + viewport.label + ': landing navigation label changed')
  assert.equal(contract.listTag, 'UL', roleCase.key + '/' + viewport.label + ': action collection is not a list')
  assert.equal(contract.itemsAreLi, true, roleCase.key + '/' + viewport.label + ': action item is not a list item')
  assert.equal(contract.legacyAccordionCount, 0, roleCase.key + '/' + viewport.label + ': legacy accordion returned')
  assert.equal(contract.actionContract.length, roleCase.expectedLabels.length, roleCase.key + '/' + viewport.label + ': role action count is wrong')
  const actionText = contract.actionContract.map((action) => action.text).join('\n')
  for (const label of roleCase.expectedLabels) {
    assert.equal(actionText.includes(label), true, roleCase.key + '/' + viewport.label + ': expected action missing: ' + label)
  }
  for (const label of roleCase.forbiddenLabels) {
    assert.equal(actionText.includes(label), false, roleCase.key + '/' + viewport.label + ': restricted action leaked: ' + label)
  }
  for (const action of contract.actionContract) {
    assert.equal(action.valid, true, roleCase.key + '/' + viewport.label + ': action is not a native button')
    assert.ok(action.width >= 44 && action.height >= 44, roleCase.key + '/' + viewport.label + ': action target is below 44px')
    assert.equal(action.centerClickable, true, roleCase.key + '/' + viewport.label + ': action target center is obscured')
  }
  assert.deepEqual(contract.overflow, { document: false, body: false, route: false }, roleCase.key + '/' + viewport.label + ': landing has horizontal overflow')
}

async function assertAdminKeyboardPath(page, roleCase) {
  const action = page.locator('button.admin-panel-action').filter({ hasText: 'مدیریت کاربران' }).first()
  await action.waitFor({ state: 'visible', timeout: 10_000 })
  await action.focus()
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Tab')
  const focus = await page.waitForFunction(() => {
    const target = [...document.querySelectorAll('button.admin-panel-action')]
      .find((element) => (element.textContent || '').includes('مدیریت کاربران'))
    if (!(target instanceof HTMLElement)) return false
    return document.activeElement === target && target.matches(':focus-visible')
  }, undefined, { timeout: 1_000 })
  assert.equal(Boolean(focus), true, roleCase.key + ': keyboard traversal did not restore focus-visible on the users action')

  const focusContract = await action.evaluate((element) => {
    const style = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    const center = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
    return {
      focused: document.activeElement === element,
      focusVisible: element.matches(':focus-visible'),
      visibleCue: style.outlineStyle !== 'none' || style.boxShadow !== 'none' || style.borderColor !== '',
      centerClickable: center === element || Boolean(center?.closest('button') === element),
    }
  })
  assert.equal(focusContract.focused, true, roleCase.key + ': keyboard target lost focus')
  assert.equal(focusContract.focusVisible, true, roleCase.key + ': keyboard target has no focus-visible state')
  assert.equal(focusContract.visibleCue, true, roleCase.key + ': keyboard target has no visible focus cue')
  assert.equal(focusContract.centerClickable, true, roleCase.key + ': keyboard target center is obscured')

  await page.keyboard.press('Enter')
  await page.waitForURL(/\/admin\/users(?:\?[^#]*)?$/u, { timeout: 15_000 })
  // The application intentionally overlaps route roots during its fade
  // transition. The incoming directory is appended last; bind the keyboard
  // path to that concrete incoming form instead of requiring a transient
  // duplicate root count of one.
  await page.locator('.user-search-form').last().waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForFunction(
    () => document.querySelectorAll('.user-search-form').length === 1,
    undefined,
    { timeout: 10_000 },
  )
  await page.locator('.admin-subview-return').last().click()
  await page.waitForURL(/\/admin$/u, { timeout: 15_000 })
  // Bind to the incoming menu while the outgoing subview fades, then require
  // its transition to settle to a single semantic landing navigation.
  await page.locator('.admin-panel-container').last().waitFor({ state: 'visible', timeout: 15_000 })
  await page.waitForFunction(
    () => document.querySelectorAll('.admin-panel-container').length === 1,
    undefined,
    { timeout: 10_000 },
  )
}

async function capturePhase1(page, label) {
  const file = 'stage6-aggregate-phase1-admin-landing-' + label + '.png'
  const filePath = path.join(OUTPUT_DIR, file)
  await page.screenshot({ path: filePath, fullPage: false, animations: 'disabled' })
  const stat = fs.statSync(filePath)
  screenshots.push({ phase: 1, file, bytes: stat.size, sha256: sha256File(filePath) })
}

function summarizePhase1Runtime(state) {
  return {
    suite: state.suite,
    role: state.role,
    viewport: state.viewport,
    apiRequests: state.apiRequests,
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
  const require = createRequire(path.join(FRONTEND, 'package.json'))
  const viteEntry = require.resolve('vite')
  const { createServer } = await import(pathToFileURL(viteEntry).href)
  const frontendNodeModules = fs.realpathSync(path.join(FRONTEND, 'node_modules'))
  const vite = await createServer({
    root: FRONTEND,
    cacheDir: VITE_CACHE_DIR,
    clearScreen: false,
    logLevel: 'error',
    server: {
      host: '127.0.0.1',
      port,
      strictPort: true,
      fs: { allow: [FRONTEND, frontendNodeModules] },
    },
  })
  await vite.listen()
  const address = vite.httpServer?.address()
  assert.ok(address && typeof address === 'object', 'Vite did not listen.')
  return { vite, baseUrl: 'http://127.0.0.1:' + String(address.port) }
}

async function runPhase1(browser, baseUrl) {
  for (const roleCase of ROLE_CASES) {
    for (const viewport of VIEWPORTS) {
      const runtime = await createPhase1Runtime(browser, baseUrl, roleCase, viewport)
      try {
        await openAdminLanding(runtime.page)
        await assertAdminLanding(runtime.page, roleCase, viewport)
        if (viewport.label === 'mobile-390') {
          await assertAdminKeyboardPath(runtime.page, roleCase)
          record('stage6-phase1-' + roleCase.key + '-keyboard-users-route-and-return-mobile-390', { role: roleCase.role, viewport })
        }
        if (viewport.label === 'mobile-360' || viewport.label === 'desktop-1440') {
          await capturePhase1(runtime.page, roleCase.key + '-' + viewport.label)
        }
        record('stage6-phase1-' + roleCase.key + '-' + viewport.label + '-role-semantic-targets-reflow', { role: roleCase.role, viewport })
      } finally {
        phase1RuntimeSummaries.push(summarizePhase1Runtime(runtime.state))
        await runtime.context.close()
      }
    }
  }
  assert.deepEqual({
    consoleErrors: diagnostics.phase1ConsoleErrors,
    pageErrors: diagnostics.phase1PageErrors,
    unexpectedApiRequests: diagnostics.phase1UnexpectedApiRequests,
    sameOriginRequestFailures: diagnostics.phase1SameOriginRequestFailures,
    nonApiRequestFailures: diagnostics.phase1NonApiRequestFailures,
    unexpectedTransports: diagnostics.phase1UnexpectedTransports,
  }, {
    consoleErrors: [],
    pageErrors: [],
    unexpectedApiRequests: [],
    sameOriginRequestFailures: [],
    nonApiRequestFailures: [],
    unexpectedTransports: [],
  }, 'Phase 1 landing browser diagnostics are not clean.')
  record('stage6-phase1-browser-diagnostics-clean-and-external-traffic-intercepted', {
    externalRequestsBlocked: diagnostics.phase1ExternalRequestsBlocked.length,
  })
}

function readChildBinding(harnessPath) {
  const stdout = execFileSync(process.execPath, [harnessPath, '--print-source-binding'], {
    cwd: WORKTREE,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  return JSON.parse(stdout)
}

function runChildPhase(phase, harnessPath, expectedHarness) {
  const binding = readChildBinding(harnessPath)
  assert.equal(binding.branch, GIT_INITIAL.branch, 'Phase ' + phase + ' child binding has the wrong branch.')
  assert.equal(binding.commit, GIT_INITIAL.commit, 'Phase ' + phase + ' child binding has the wrong commit.')
  assert.equal(binding.tree, GIT_INITIAL.tree, 'Phase ' + phase + ' child binding has the wrong tree.')
  assert.equal(binding.trackedClean, true, 'Phase ' + phase + ' child binding is not clean.')
  assert.deepEqual(binding.harness, {
    bytes: expectedHarness.bytes,
    sha256: expectedHarness.sha256,
  }, 'Phase ' + phase + ' child harness changed before its execution.')

  const childEnv = { ...process.env }
  delete childEnv.STAGE6_PHASE2_BROWSER_DIAGNOSTIC
  delete childEnv.STAGE6_PHASE2_BROWSER_ONLY
  delete childEnv.STAGE6_PHASE3_BROWSER_DIAGNOSTIC
  delete childEnv.STAGE6_PHASE3_BROWSER_ONLY

  if (phase === 2) {
    childEnv.STAGE6_PHASE2_BROWSER_AUTHORIZATION = 'STAGE6 PHASE2 ADMIN DIRECTORY — RUN'
    childEnv.STAGE6_PHASE2_EXPECTED_SOURCE_SHA256 = binding.sourceBindingSha256
    childEnv.STAGE6_PHASE2_EXPECTED_COMMIT = binding.commit
  } else {
    childEnv.STAGE6_PHASE3_BROWSER_AUTHORIZATION = 'STAGE6 PHASE3 PROFILE PRIVACY & AUTHORITY — RUN'
    childEnv.STAGE6_PHASE3_EXPECTED_SOURCE_SHA256 = binding.sourceBindingSha256
    childEnv.STAGE6_PHASE3_EXPECTED_COMMIT = binding.commit
  }

  progress('phase-' + phase + '-child-start', { sourceBindingSha256: binding.sourceBindingSha256 })
  const result = spawnSync(process.execPath, [harnessPath], {
    cwd: WORKTREE,
    env: childEnv,
    encoding: 'utf8',
    timeout: 10 * 60 * 1000,
    maxBuffer: 8 * 1024 * 1024,
  })
  const lines = (result.stdout || '').split('\n').filter(Boolean)
  const events = lines.flatMap((line) => {
    try { return [JSON.parse(line)] } catch { return [] }
  })
  const completion = events.find((event) => (
    event.stage === 'complete' &&
    (event.event === 'stage6-phase2-browser-progress' || event.event === 'stage6-phase3-browser-progress')
  ))
  assert.equal(result.status, 0, 'Phase ' + phase + ' child harness failed; inspect its local run artifacts.')
  assert.ok(completion?.runId, 'Phase ' + phase + ' child harness did not emit a complete run ID.')

  const runDir = path.join(EVIDENCE_DIR, 'runs', completion.runId)
  const metricsFile = phase === 2
    ? path.join(runDir, 'stage6-phase2-admin-directory-metrics.json')
    : path.join(runDir, 'stage6-phase3-profile-privacy-authority-metrics.json')
  const bindingFile = phase === 2
    ? path.join(runDir, 'stage6-phase2-source-binding.json')
    : path.join(runDir, 'stage6-phase3-source-binding.json')
  assert.equal(fs.existsSync(metricsFile), true, 'Phase ' + phase + ' metrics artifact is missing.')
  assert.equal(fs.existsSync(bindingFile), true, 'Phase ' + phase + ' binding artifact is missing.')
  const metrics = JSON.parse(fs.readFileSync(metricsFile, 'utf8'))
  const outputBinding = JSON.parse(fs.readFileSync(bindingFile, 'utf8'))
  assert.equal(metrics.status, 'passed', 'Phase ' + phase + ' did not pass.')
  assert.equal(metrics.promotable, true, 'Phase ' + phase + ' did not produce promotable evidence.')
  assert.equal(outputBinding.commit, GIT_INITIAL.commit, 'Phase ' + phase + ' output bound the wrong commit.')
  assert.equal(outputBinding.tree, GIT_INITIAL.tree, 'Phase ' + phase + ' output bound the wrong tree.')
  assert.equal(outputBinding.sourceIdentical, true, 'Phase ' + phase + ' source changed during capture.')
  assert.equal(outputBinding.gitIdentical, true, 'Phase ' + phase + ' Git state changed during capture.')
  assert.equal(outputBinding.harnessIdentical, true, 'Phase ' + phase + ' harness changed during capture.')
  assert.equal(outputBinding.environmentIdentical, true, 'Phase ' + phase + ' Vite environment changed during capture.')

  const summary = {
    phase,
    runId: completion.runId,
    status: metrics.status,
    promotable: metrics.promotable,
    assertionCount: Array.isArray(metrics.assertions) ? metrics.assertions.length : null,
    screenshotCount: Array.isArray(metrics.screenshots) ? metrics.screenshots.length : null,
    metrics: {
      path: path.relative(OUTPUT_DIR, metricsFile).split(path.sep).join('/'),
      bytes: fs.statSync(metricsFile).size,
      sha256: sha256File(metricsFile),
    },
    binding: {
      path: path.relative(OUTPUT_DIR, bindingFile).split(path.sep).join('/'),
      bytes: fs.statSync(bindingFile).size,
      sha256: sha256File(bindingFile),
    },
    stdoutSha256: sha256(result.stdout || ''),
    stderrSha256: sha256(result.stderr || ''),
  }
  childRuns.push(summary)
  record('stage6-phase' + phase + '-child-promotable-source-bound-acceptance', {
    runId: completion.runId,
    assertionCount: summary.assertionCount,
    screenshotCount: summary.screenshotCount,
  })
  progress('phase-' + phase + '-child-complete', {
    runId: completion.runId,
    assertionCount: summary.assertionCount,
    screenshotCount: summary.screenshotCount,
  })
}

const SOURCE_INITIAL = sourceSnapshot()
const SOURCE_BINDING_SHA256 = sha256(JSON.stringify(SOURCE_INITIAL))
const GIT_INITIAL = gitSnapshot()
const HARNESSES_INITIAL = {
  aggregate: fileSnapshot(HARNESS_PATH),
  phase2: fileSnapshot(PHASE2_HARNESS),
  phase3: fileSnapshot(PHASE3_HARNESS),
}

if (process.argv.includes('--print-source-binding')) {
  process.stdout.write(JSON.stringify({
    schemaVersion: 1,
    stage: 6,
    phase: 'aggregate',
    scope: 'phase1-admin-landing-phase2-directory-phase3-profile-browser-acceptance',
    branch: GIT_INITIAL.branch,
    commit: GIT_INITIAL.commit,
    tree: GIT_INITIAL.tree,
    parent: GIT_INITIAL.parent,
    trackedClean: GIT_INITIAL.trackedClean,
    sourceFileCount: SOURCE_INITIAL.length,
    sourceBindingSha256: SOURCE_BINDING_SHA256,
    harnesses: HARNESSES_INITIAL,
    execution: 'requires exact authorization, commit, source SHA-256, expected branch and a Git-clean worktree; partial or diagnostic runs are disabled',
  }, null, 2) + '\n')
  process.exit(0)
}

assert.equal(
  process.env.STAGE6_AGGREGATE_BROWSER_AUTHORIZATION,
  RUN_AUTHORIZATION,
  'Browser execution is locked. Set STAGE6_AGGREGATE_BROWSER_AUTHORIZATION to the documented authorization value.',
)
assert.equal(
  process.env.STAGE6_AGGREGATE_EXPECTED_SOURCE_SHA256,
  SOURCE_BINDING_SHA256,
  'STAGE6_AGGREGATE_EXPECTED_SOURCE_SHA256 does not match the bounded source snapshot.',
)
assert.equal(
  process.env.STAGE6_AGGREGATE_EXPECTED_COMMIT,
  GIT_INITIAL.commit,
  'STAGE6_AGGREGATE_EXPECTED_COMMIT does not match the current implementation commit.',
)
assert.equal(GIT_INITIAL.branch, EXPECTED_BRANCH, 'Aggregate Stage 6 evidence is on the wrong branch.')
assert.equal(GIT_INITIAL.trackedClean, true, 'Aggregate Stage 6 evidence requires a Git-clean implementation worktree.')
assert.equal((process.env.STAGE6_AGGREGATE_BROWSER_ONLY || '').trim(), '', 'Partial aggregate runs are disabled.')
assert.notEqual(process.env.STAGE6_AGGREGATE_BROWSER_DIAGNOSTIC, '1', 'Diagnostic mode is intentionally disabled.')

sanitizeViteEnvironment()
const ENVIRONMENT_INITIAL = viteEnvironmentSnapshot()
assert.equal(
  ENVIRONMENT_INITIAL.some((entry) => entry.exists),
  false,
  'Unbound Vite environment files are forbidden during aggregate browser evidence capture.',
)

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true })
  const startedAt = new Date().toISOString()
  let failure = null
  let vite = null
  let browser = null
  try {
    const started = await startVite()
    vite = started.vite
    const require = createRequire(path.join(FRONTEND, 'package.json'))
    const { chromium } = require('playwright')
    progress('phase-1-vite-ready')
    browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] })
    await runPhase1(browser, started.baseUrl)
    progress('phase-1-complete', { assertionCount: assertions.length, screenshotCount: screenshots.length })
  } catch (error) {
    failure = safeError(error)
    process.exitCode = 1
  } finally {
    if (browser) await browser.close().catch((error) => { failure ||= safeError(error) })
    if (vite) await vite.close().catch((error) => { failure ||= safeError(error) })
    fs.rmSync(VITE_CACHE_DIR, { recursive: true, force: true })
  }

  if (!failure) {
    try {
      runChildPhase(2, PHASE2_HARNESS, HARNESSES_INITIAL.phase2)
      runChildPhase(3, PHASE3_HARNESS, HARNESSES_INITIAL.phase3)
    } catch (error) {
      failure = safeError(error)
      process.exitCode = 1
    }
  }

  const sourceFinal = sourceSnapshot()
  const gitFinal = gitSnapshot()
  const harnessesFinal = {
    aggregate: fileSnapshot(HARNESS_PATH),
    phase2: fileSnapshot(PHASE2_HARNESS),
    phase3: fileSnapshot(PHASE3_HARNESS),
  }
  const environmentFinal = viteEnvironmentSnapshot()
  const sourceIdentical = JSON.stringify(SOURCE_INITIAL) === JSON.stringify(sourceFinal)
  const gitIdentical = JSON.stringify(GIT_INITIAL) === JSON.stringify(gitFinal)
  const harnessesIdentical = JSON.stringify(HARNESSES_INITIAL) === JSON.stringify(harnessesFinal)
  const environmentIdentical = JSON.stringify(ENVIRONMENT_INITIAL) === JSON.stringify(environmentFinal)

  if (!failure) {
    try {
      assert.equal(sourceIdentical, true, 'Bound source changed during aggregate browser capture.')
      assert.equal(gitIdentical, true, 'Git state changed during aggregate browser capture.')
      assert.equal(harnessesIdentical, true, 'Aggregate or child harness changed during capture.')
      assert.equal(environmentIdentical, true, 'Vite environment changed during aggregate browser capture.')
    } catch (error) {
      failure = safeError(error)
      process.exitCode = 1
    }
  }

  const completedAt = new Date().toISOString()
  const metrics = {
    schemaVersion: 1,
    stage: 6,
    phase: 'aggregate',
    scope: 'phase1-admin-landing-phase2-directory-phase3-profile-browser-acceptance',
    status: failure ? 'failed' : 'passed',
    promotable: !failure,
    runId: RUN_ID,
    startedAt,
    completedAt,
    source: {
      expectedSha256: process.env.STAGE6_AGGREGATE_EXPECTED_SOURCE_SHA256,
      pre: SOURCE_INITIAL,
      post: sourceFinal,
      sha256: SOURCE_BINDING_SHA256,
      identical: sourceIdentical,
    },
    git: { pre: GIT_INITIAL, post: gitFinal, identical: gitIdentical },
    harnesses: { pre: HARNESSES_INITIAL, post: harnessesFinal, identical: harnessesIdentical },
    environment: { pre: ENVIRONMENT_INITIAL, post: environmentFinal, identical: environmentIdentical },
    runtime: {
      node: process.versions.node,
      fixedTime: FIXED_TIME,
      fixturePolicy: 'synthetic identities only; no real backend, no personal data, all external browser traffic intercepted before connection',
      childPolicy: 'Phase 2 and Phase 3 run complete, promotable child harnesses with their own exact source/Git/harness/environment checks',
    },
    assertions,
    screenshots,
    phase1RuntimeSummaries,
    childRuns,
    diagnostics,
    failure,
    claimBoundary: 'This local acceptance covers only the delivered Stage 6 Admin/Profile browser scope with synthetic fixtures. It does not deploy, publish Sites, mutate production or staging, prove live backend availability, or cover protected Messenger/Forward behavior beyond the existing guarded disposition.',
  }
  fs.writeFileSync(METRICS_PATH, JSON.stringify(metrics, null, 2) + '\n')
  fs.writeFileSync(BINDING_PATH, JSON.stringify({
    schemaVersion: 1,
    stage: 6,
    phase: 'aggregate',
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
    harnessesIdentical,
    environmentIdentical,
    childRuns: childRuns.map((entry) => ({
      phase: entry.phase,
      runId: entry.runId,
      status: entry.status,
      promotable: entry.promotable,
      metricsSha256: entry.metrics.sha256,
      bindingSha256: entry.binding.sha256,
    })),
  }, null, 2) + '\n')
  progress('complete', {
    status: metrics.status,
    assertionCount: assertions.length,
    screenshotCount: screenshots.length,
    childRunCount: childRuns.length,
    failure: failure ? { name: failure.name, message: failure.message } : null,
  })
}

await main()
