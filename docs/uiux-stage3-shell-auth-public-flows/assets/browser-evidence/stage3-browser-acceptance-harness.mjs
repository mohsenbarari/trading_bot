#!/usr/bin/env node

import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'

const WORKTREE = '/tmp/trading-bot-webapp-uiux-redesign-v2'
const FRONTEND = path.join(WORKTREE, 'frontend')
const RUN_ID = `uiux-stage3-browser-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join('/tmp', RUN_ID)
const METRICS_PATH = path.join(OUTPUT_DIR, 'stage3-browser-acceptance-metrics.json')
const ONLY_SUITE = process.env.STAGE3_BROWSER_ONLY?.trim() || ''
const SOURCE_FILES = [
  'frontend/public/uiux-v2-brand-mark.svg',
  'frontend/src/App.vue',
  'frontend/src/components/AppAuthenticatedShell.vue',
  'frontend/src/components/AppToasts.vue',
  'frontend/src/components/BottomNav.vue',
  'frontend/src/components/PWAInstallOverlay.vue',
  'frontend/src/components/SessionApprovalModal.vue',
  'frontend/src/components/auth/AuthFlowShell.vue',
  'frontend/src/router/index.ts',
  'frontend/src/router/systemRecovery.ts',
  'frontend/src/router/systemRecovery.test.ts',
  'frontend/src/router/uiRouteContract.ts',
  'frontend/src/styles/design-system-v2.components.css',
  'frontend/src/styles/design-system-v2.tokens.css',
  'frontend/src/utils/auth.ts',
  'frontend/src/utils/auth.test.ts',
  'frontend/src/utils/authNavigation.ts',
  'frontend/src/utils/authNavigation.test.ts',
  'frontend/src/utils/invitationContract.ts',
  'frontend/src/utils/invitationContract.test.ts',
  'frontend/src/utils/navigationResult.ts',
  'frontend/src/utils/navigationResult.test.ts',
  'frontend/src/utils/registrationHandoff.ts',
  'frontend/src/utils/registrationHandoff.test.ts',
  'frontend/src/utils/securityLayerState.ts',
  'frontend/src/views/DashboardView.vue',
  'frontend/src/views/InviteLanding.vue',
  'frontend/src/views/InviteLanding.test.ts',
  'frontend/src/views/LoginView.vue',
  'frontend/src/views/LoginView.test.ts',
  'frontend/src/views/SetupPassword.vue',
  'frontend/src/views/SetupPassword.test.ts',
  'frontend/src/views/SystemRecoveryView.vue',
  'frontend/src/views/WebRegister.vue',
  'frontend/src/views/WebRegister.test.ts',
  'frontend/e2e/mandatory-channel.spec.ts',
  'api/routers/auth.py',
  'api/routers/accountants.py',
  'api/routers/customers.py',
  'api/routers/invitations.py',
  'bot/handlers/panel.py',
  'bot/handlers/start.py',
  'core/invitation_contract_service.py',
  'core/log_redaction.py',
  'core/logging_config.py',
  'core/metrics.py',
  'core/registration_contracts.py',
  'schemas.py',
  'tests/test_auth_router_login_otp_flows.py',
  'tests/test_auth_router_registration_flows.py',
  'tests/test_authoritative_registration_postgres.py',
  'tests/customer_live_auth_smoke.py',
  'tests/test_accountants_router.py',
  'tests/test_customers_router.py',
  'tests/test_invitations_router.py',
  'tests/test_invitation_public_access.py',
  'tests/test_logging_foundation.py',
  'tests/test_metrics.py',
  'tests/test_registration_stage1_contracts.py',
  'tests/test_request_logging.py',
  'tests/test_stage5_direct_telegram_registration.py',
  'tests/test_bot_start_profile_token_success.py',
  'tests/test_bot_start_registration_contact.py',
  'tests/test_bot_start_registration_address.py',
  'tests/test_bot_start_invitation_entry.py',
  'tests/test_bot_panel_standard_actions.py',
  'tests/test_bot_admin_role.py',
]
const VIEWPORTS = [
  { label: 'mobile-360', width: 360, height: 740 },
  { label: 'mobile-375', width: 375, height: 812 },
  { label: 'mobile-390', width: 390, height: 844 },
  { label: 'mobile-414', width: 414, height: 896 },
  { label: 'mobile-430', width: 430, height: 932 },
  { label: 'tablet-768', width: 768, height: 1024 },
  { label: 'tablet-landscape-1024', width: 1024, height: 768 },
  { label: 'desktop-1440', width: 1440, height: 900 },
]
const CURRENT_USER = {
  id: 9001,
  account_name: 'stage3_browser_user',
  full_name: 'کاربر مرورگر مرحله سه',
  role: 'مدیر ارشد',
  account_status: 'active',
  is_accountant: false,
  customer_tier: null,
  has_bot_access: true,
  can_connect_telegram: false,
  telegram_linked: false,
}

const require = createRequire(path.join(FRONTEND, 'package.json'))
const { chromium } = require('playwright')
const viteEntry = require.resolve('vite')
const { createServer } = await import(pathToFileURL(viteEntry).href)

fs.mkdirSync(OUTPUT_DIR, { recursive: true })

const assertions = []
const screenshots = []
const browserDiagnostics = {
  consoleErrors: [],
  expectedNegativeConsoleErrors: [],
  unexpectedConsoleErrors: [],
  expectedHarnessNavigationAborts: [],
  expectedLifecycleArtifacts: [],
  observedRequestFailures: [],
  pageErrors: [],
  unexpectedRequestFailures: [],
}
const pendingRequestFailures = []
const pageRuntimeStates = new WeakMap()

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex')
}

function sha256File(filePath) {
  return sha256Buffer(fs.readFileSync(filePath))
}

function sourceSnapshot() {
  return SOURCE_FILES.map((relativePath) => {
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
  process.stdout.write(`${JSON.stringify({ event: 'stage3-browser-progress', runId: RUN_ID, stage, ...details })}\n`)
}

function expectTrue(value, message) {
  assert.equal(Boolean(value), true, message)
}

function createJwt(payload) {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url')
  return `${header}.${body}.stage3-browser`
}

function newRuntimeState(overrides = {}) {
  return {
    user: { ...CURRENT_USER },
    verifyMode: 'success',
    tradesMode: 'success',
    releaseTrades: null,
    rawInvite: 'INV-stage3-browser-secret-6d9ef1b9',
    lookupBotAvailable: false,
    lookupWebAvailable: true,
    registrationHandle: 'opaque-stage3-context-1',
    registrationProgress: 'context_ready',
    registrationKind: 'invitation',
    registrationCompleted: false,
    contextMode: 'active',
    clearFailuresRemaining: 0,
    holdContextRead: false,
    releaseContextRead: null,
    holdExchange: false,
    releaseExchange: null,
    exchangeCount: 0,
    exchangeBodySecretCount: 0,
    contextReadCount: 0,
    otpRequestCount: 0,
    otpVerifyCount: 0,
    completionCount: 0,
    clearCount: 0,
    failOtpRequestOnce: false,
    loginMode: 'normal',
    requestLog: [],
    expectedRequestAborts: new WeakSet(),
    harnessClosing: false,
    lifecycleMarker: {
      suite: 'unassigned',
      viewport: null,
      navigationEpoch: 0,
      phase: 'idle',
      plannedNavigation: false,
      assertionComplete: false,
    },
    requestLifecycle: [],
    requestLifecycleByRequest: new WeakMap(),
    navigationEpoch: 0,
    activeSuite: 'unassigned',
    ...overrides,
  }
}

function registrationContextPayload(state) {
  return {
    account_name: 'stage3_invited_user',
    mobile_number: '0912****789',
    role: 'کاربر',
    expires_at: '2026-08-09T08:00:00.000Z',
    kind: state.registrationKind,
    progress: state.registrationProgress,
    requires_otp: state.registrationKind === 'invitation',
  }
}

function json(route, body, status = 200, headers = {}) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    headers: {
      'Cache-Control': 'no-store',
      Pragma: 'no-cache',
      ...headers,
    },
    body: body === null ? 'null' : JSON.stringify(body),
  })
}

async function handleApiRoute(route, state) {
  const request = route.request()
  const url = new URL(request.url())
  const requestPath = url.pathname
  const method = request.method()
  const postData = request.postData() || ''
  const rawSecret = state.rawInvite
  state.requestLog.push({
    path: requestPath,
    method,
    secretInUrl: request.url().includes(rawSecret),
    secretInHeaders: JSON.stringify(await request.allHeaders()).includes(rawSecret),
    secretInBody: postData.includes(rawSecret),
  })

  if (requestPath === '/api/invitations/lookup/stage3-browser') {
    return json(route, {
      token: rawSecret,
      valid: true,
      state: 'pending',
      kind: 'standard',
      bot_available: state.lookupBotAvailable,
      web_available: state.lookupWebAvailable,
      expires_at: '2026-08-09T08:00:00.000Z',
    })
  }
  if (requestPath === '/api/config') return json(route, { bot_username: 'stage3_browser_bot' })

  if (requestPath === '/api/auth/registration-context/exchange') {
    const parsed = JSON.parse(postData || '{}')
    assert.equal(method, 'POST')
    assert.equal(parsed.kind, state.registrationKind)
    assert.equal(parsed.token, rawSecret)
    assert.match(parsed.exchange_id || '', /^(?:exchange_[a-f0-9]{64}|[a-f0-9-]{32,36})$/u)
    state.exchangeCount += 1
    state.exchangeBodySecretCount += postData.includes(rawSecret) ? 1 : 0
    if (state.holdExchange) {
      await new Promise((resolve) => {
        state.releaseExchange = resolve
      })
      state.releaseExchange = null
    }
    if (state.registrationCompleted) {
      return json(route, { status: 'registration_complete' }, 200, {
        'set-cookie': `web_registration=${state.registrationHandle}; Max-Age=600; Path=/; HttpOnly; SameSite=Strict`,
      })
    }
    state.registrationProgress = 'context_ready'
    return json(route, registrationContextPayload(state), 200, {
      'set-cookie': `web_registration=${state.registrationHandle}; Max-Age=600; Path=/; HttpOnly; SameSite=Strict`,
    })
  }

  if (requestPath === '/api/auth/registration-context') {
    assert.equal(method, 'POST')
    state.contextReadCount += 1
    if (state.holdContextRead) {
      await new Promise((resolve) => {
        state.releaseContextRead = resolve
      })
      state.releaseContextRead = null
    }
    if (state.registrationCompleted) return json(route, { status: 'registration_complete' })
    if (state.contextMode === 'gone') return json(route, { detail: 'context cleared after completion' }, 410)
    return json(route, registrationContextPayload(state))
  }

  if (requestPath === '/api/auth/registration-context/otp/request') {
    assert.equal(method, 'POST')
    state.otpRequestCount += 1
    if (state.failOtpRequestOnce) {
      state.failOtpRequestOnce = false
      return json(route, { detail: 'اختلال موقت آزمون' }, 503)
    }
    state.registrationProgress = 'otp_requested'
    return json(route, { detail: 'کد تأیید ارسال شد' })
  }

  if (requestPath === '/api/auth/registration-context/otp/verify') {
    const parsed = JSON.parse(postData || '{}')
    assert.equal(method, 'POST')
    assert.equal(parsed.code, '12345')
    state.otpVerifyCount += 1
    state.registrationProgress = 'otp_verified'
    state.registrationHandle = 'opaque-stage3-context-rotated'
    return json(route, { detail: 'کد تأیید شد' }, 200, {
      'set-cookie': `web_registration=${state.registrationHandle}; Max-Age=540; Path=/; HttpOnly; SameSite=Strict`,
    })
  }

  if (requestPath === '/api/auth/registration-context/complete') {
    const parsed = JSON.parse(postData || '{}')
    assert.equal(method, 'POST')
    assert.match(parsed.address || '', /تهران/u)
    if (state.registrationCompleted) return json(route, { status: 'registration_complete' })
    state.completionCount += 1
    state.registrationCompleted = true
    return json(
      route,
      { access_token: createJwt({ sub: '9001', exp: Math.floor(Date.now() / 1000) + 3600 }), refresh_token: createJwt({ sub: '9001', exp: Math.floor(Date.now() / 1000) + 86400 }) },
      200,
      { 'set-cookie': `web_registration=${state.registrationHandle}; Max-Age=600; Path=/; HttpOnly; SameSite=Strict` },
    )
  }

  if (requestPath === '/api/auth/registration-context/clear') {
    state.clearCount += 1
    if (state.clearFailuresRemaining > 0) {
      state.clearFailuresRemaining -= 1
      return json(route, { detail: 'temporary clear failure' }, 503)
    }
    state.registrationCompleted = false
    state.contextMode = 'gone'
    return route.fulfill({
      status: 204,
      headers: {
        'Cache-Control': 'no-store',
        Pragma: 'no-cache',
        'set-cookie': 'web_registration=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict',
      },
    })
  }

  if (requestPath === '/api/auth/request-otp') {
    return json(route, {
      detail: 'کد ثبت شد',
      method: 'log',
      expires_in: 120,
    })
  }

  if (requestPath === '/api/auth/verify-otp') {
    if (state.loginMode === 'registration-required') {
      state.registrationKind = 'registration'
      state.registrationProgress = 'otp_verified'
      state.registrationHandle = 'opaque-stage3-direct-registration'
      return json(
        route,
        {
          status: 'registration_required',
          expires_in: 600,
          invitation: {
            account_name: 'stage3_direct_user',
            mobile_number: '0912****789',
            role: 'کاربر',
            expires_at: '2026-08-09T08:00:00.000Z',
          },
        },
        200,
        { 'set-cookie': `web_registration=${state.registrationHandle}; Max-Age=600; Path=/; HttpOnly; SameSite=Strict` },
      )
    }
    return json(route, {
      access_token: createJwt({ sub: '9001', exp: Math.floor(Date.now() / 1000) + 3600 }),
      refresh_token: createJwt({ sub: '9001', exp: Math.floor(Date.now() / 1000) + 86400 }),
    })
  }

  if (requestPath === '/api/auth/me') return json(route, state.user)
  if (requestPath === '/api/auth/switchable-users') return json(route, [])
  if (requestPath === '/api/auth/refresh') {
    return json(route, {
      access_token: createJwt({ sub: '9001', exp: Math.floor(Date.now() / 1000) + 3600 }),
      refresh_token: createJwt({ sub: '9001', exp: Math.floor(Date.now() / 1000) + 86400 }),
    })
  }
  if (requestPath === '/api/sessions/verify') {
    if (state.verifyMode === 'abort') {
      state.expectedRequestAborts.add(request)
      return route.abort('failed')
    }
    return json(route, { ok: true })
  }
  if (requestPath === '/api/sessions/active') {
    return json(route, [{ id: 'stage3-session', is_current: true, is_primary: true }])
  }
  if (requestPath === '/api/sessions/recovery/pending') return json(route, [])
  if (requestPath === '/api/sessions/login-requests/pending') return json(route, [])
  if (/^\/api\/sessions\/login-requests\/[^/]+\/(?:approve|reject)$/u.test(requestPath)) {
    return json(route, { ok: true })
  }
  if (requestPath === '/api/chat/poll') {
    return json(route, {
      conversations_with_unread: [],
      muted_conversation_ids: [],
      unread_chats_count: 0,
      total_unread_mentions: 0,
    })
  }
  if (requestPath === '/api/notifications/' && method === 'GET') return json(route, [])
  if (requestPath === '/api/notifications/mark-all-read') return json(route, { ok: true })

  if (requestPath === '/api/trades/my') {
    if (state.tradesMode === 'pending') {
      await new Promise((resolve) => {
        state.releaseTrades = async () => {
          state.tradesMode = 'success'
          await json(route, [])
          resolve()
        }
      })
      return
    }
    return json(route, [])
  }
  if (requestPath === '/api/offers/page' && method === 'GET') {
    return json(route, { items: [], next_cursor: null, has_more: false, page_size: 0 })
  }
  if (requestPath === '/api/offers/my') return json(route, [])
  if (requestPath === '/api/commodities/') {
    return json(route, [{ id: 1, name: 'طلای آب‌شده', aliases: [] }])
  }
  if (requestPath === '/api/trading-settings/') {
    return json(route, {
      offer_min_quantity: 1,
      offer_max_quantity: 1000,
      lot_min_size: 5,
      lot_max_count: 5,
      offer_expiry_minutes: 60,
      invitation_expiry_days: 7,
      market_schedule_enabled: true,
      market_timezone: 'Asia/Tehran',
      market_open_time_local: '10:00',
      market_close_time_local: '18:00',
      market_closed_weekdays: [4],
    })
  }
  if (requestPath === '/api/trading-settings/market-state') {
    return json(route, {
      is_open: true,
      active_web_notice_visible: false,
      offers_since_last_open: 0,
      last_transition_at: null,
      next_transition_at: null,
    })
  }
  if (requestPath === '/api/trading-settings/market-overrides') return json(route, [])
  if (requestPath === '/api/admin-messages/market/current') return json(route, null)
  if (requestPath === '/api/invitations/pending') return json(route, [])
  if (requestPath === '/api/customers/owner-relations') return json(route, [])
  if (requestPath === '/api/accountants/owner-relations') return json(route, [])
  if (requestPath.startsWith('/api/users-public/')) return json(route, [])
  return json(route, {})
}

async function createPage(browser, baseUrl, options = {}) {
  const state = options.state || newRuntimeState()
  state.activeSuite = options.suite || state.activeSuite
  const context = await browser.newContext({
    baseURL: baseUrl,
    locale: 'fa-IR',
    timezoneId: 'Asia/Tehran',
    serviceWorkers: 'block',
  })
  const page = await context.newPage()
  pageRuntimeStates.set(page, state)
  const token = createJwt({
    sub: String(CURRENT_USER.id),
    exp: Math.floor(Date.now() / 1000) + 3600,
    session_id: 'stage3-browser-session',
  })
  await page.addInitScript(
    ({ authenticated, preserveAuthOnReload, accessToken, userSummary }) => {
      window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
      delete window.__PLAYWRIGHT_ENABLE_PWA_REGISTRATION__
      if (authenticated) {
        localStorage.setItem('auth_token', accessToken)
        localStorage.setItem('refresh_token', accessToken)
        localStorage.setItem('current_user_summary', JSON.stringify(userSummary))
        localStorage.removeItem('suspended_refresh_token')
      } else if (!preserveAuthOnReload) {
        localStorage.removeItem('auth_token')
        localStorage.removeItem('refresh_token')
        localStorage.removeItem('current_user_summary')
        localStorage.removeItem('suspended_refresh_token')
      }

      class FakeWebSocket {
        static CONNECTING = 0
        static OPEN = 1
        static CLOSING = 2
        static CLOSED = 3
        constructor(url) {
          this.url = String(url)
          this.readyState = FakeWebSocket.CONNECTING
          this.sent = []
          window.__stage3Sockets = window.__stage3Sockets || []
          window.__stage3Sockets.push(this)
          queueMicrotask(() => {
            if (this.readyState !== FakeWebSocket.CONNECTING) return
            this.readyState = FakeWebSocket.OPEN
            this.onopen?.(new Event('open'))
          })
        }
        send(value) {
          this.sent.push(value)
        }
        close() {
          this.readyState = FakeWebSocket.CLOSED
          this.onclose?.(new CloseEvent('close'))
        }
        emit(type, data) {
          this.onmessage?.({ data: JSON.stringify({ type, data }) })
        }
      }
      window.WebSocket = FakeWebSocket
      window.open = () => null
    },
    {
      authenticated: Boolean(options.authenticated),
      preserveAuthOnReload: Boolean(options.preserveAuthOnReload),
      accessToken: token,
      userSummary: CURRENT_USER,
    },
  )

  await context.route('https://telegram.org/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }),
  )
  const handleMockApiRoute = async (route) => {
    const pathname = new URL(route.request().url()).pathname
    const successfulClear =
      pathname === '/api/auth/registration-context/clear' && state.clearFailuresRemaining === 0
    await handleApiRoute(route, state)
    const lifecycle = state.requestLifecycleByRequest.get(route.request())
    if (lifecycle) {
      lifecycle.routeHandlerSettledAt = Date.now()
      if (successfulClear) {
        lifecycle.clearSideEffectRecorded = true
        lifecycle.clearCountAfterSideEffect = state.clearCount
        lifecycle.clearSideEffectAt = Date.now()
      }
    }
  }
  await context.route(
    /\/api\/(?!auth\/registration-context\/clear(?:[?#]|$))/u,
    handleMockApiRoute,
  )
  await context.route('**/api/auth/registration-context/clear', handleMockApiRoute)

  const markerSnapshot = () => ({ ...state.lifecycleMarker })
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname
    if (!pathname.startsWith('/api/')) return
    const lifecycle = {
      id: state.requestLifecycle.length + 1,
      url: request.url(),
      pathname,
      method: request.method(),
      startedAt: Date.now(),
      startMarker: markerSnapshot(),
      responseAt: null,
      responseStatus: null,
      responseCacheControl: null,
      routeHandlerSettledAt: null,
      assertionCompletedAt: null,
      assertionLabel: null,
      clearSideEffectRecorded: false,
      clearCountAfterSideEffect: null,
      clearSideEffectAt: null,
      finishedAt: null,
      failedAt: null,
      failure: null,
      endMarker: null,
    }
    state.requestLifecycle.push(lifecycle)
    state.requestLifecycleByRequest.set(request, lifecycle)
  })
  page.on('response', (response) => {
    const lifecycle = state.requestLifecycleByRequest.get(response.request())
    if (!lifecycle) return
    lifecycle.responseAt = Date.now()
    lifecycle.responseStatus = response.status()
    lifecycle.responseCacheControl = response.headers()['cache-control'] || ''
  })
  page.on('requestfinished', (request) => {
    const lifecycle = state.requestLifecycleByRequest.get(request)
    if (!lifecycle) return
    lifecycle.finishedAt = Date.now()
    lifecycle.endMarker = markerSnapshot()
  })

  page.on('console', (message) => {
    if (message.type() === 'error') browserDiagnostics.consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => browserDiagnostics.pageErrors.push(error.message))
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText || ''
    const lifecycle = state.requestLifecycleByRequest.get(request)
    if (lifecycle) {
      lifecycle.failedAt = Date.now()
      lifecycle.failure = failure
      lifecycle.endMarker = markerSnapshot()
    }
    if (state.expectedRequestAborts.has(request)) return
    const observed = { url: request.url(), failure, lifecycle }
    browserDiagnostics.observedRequestFailures.push({ url: request.url(), failure })
    pendingRequestFailures.push(observed)
  })
  return { context, page, state }
}

function beginPlannedNavigation(page, phase) {
  const state = pageRuntimeStates.get(page)
  if (!state) return
  state.navigationEpoch += 1
  const viewport = page.viewportSize()
  state.lifecycleMarker = {
    suite: state.activeSuite,
    viewport: viewport ? `${viewport.width}x${viewport.height}` : null,
    navigationEpoch: state.navigationEpoch,
    phase,
    plannedNavigation: true,
    assertionComplete: false,
  }
}

function markPageAssertionComplete(page, label) {
  const state = pageRuntimeStates.get(page)
  if (!state) return
  state.lifecycleMarker = {
    ...state.lifecycleMarker,
    phase: label,
    plannedNavigation: false,
    assertionComplete: true,
  }
}

function markResponseAssertionComplete(response, label) {
  const request = response.request()
  let state = null
  try {
    state = pageRuntimeStates.get(request.frame().page()) || null
  } catch {
    return
  }
  const lifecycle = state?.requestLifecycleByRequest.get(request)
  if (!lifecycle) return
  lifecycle.assertionCompletedAt = Date.now()
  lifecycle.assertionLabel = label
}

async function plannedGoto(page, url, options, label = `page.goto:${url}`) {
  beginPlannedNavigation(page, label)
  return page.goto(url, options)
}

async function plannedReload(page, options, label = 'page.reload') {
  beginPlannedNavigation(page, label)
  return page.reload(options)
}

async function plannedGoBack(page, options, label = 'page.goBack') {
  beginPlannedNavigation(page, label)
  return page.goBack(options)
}

async function plannedGoForward(page, options, label = 'page.goForward') {
  beginPlannedNavigation(page, label)
  return page.goForward(options)
}

function classifyRequestLifecycleArtifacts() {
  const clearAssertionLabels = new Set([
    'registration-complete:clear-and-ready-asserted',
    'invitation-home-transition-and-clear-asserted',
    'retained-refresh-home-recovery-and-clear-asserted',
    'guest-completion-login-transition-and-clear-asserted',
  ])
  const expected = []
  const unexpected = []

  for (const observed of pendingRequestFailures) {
    const lifecycle = observed.lifecycle
    const isExactClear204Artifact = Boolean(
      lifecycle &&
        observed.failure === 'net::ERR_ABORTED' &&
        lifecycle.method === 'POST' &&
        lifecycle.pathname === '/api/auth/registration-context/clear' &&
        lifecycle.responseStatus === 204 &&
        lifecycle.responseCacheControl
          .split(',')
          .map((value) => value.trim().toLowerCase())
          .includes('no-store') &&
        lifecycle.clearSideEffectRecorded === true &&
        Number.isInteger(lifecycle.clearCountAfterSideEffect) &&
        lifecycle.clearCountAfterSideEffect > 0 &&
        lifecycle.assertionCompletedAt !== null &&
        clearAssertionLabels.has(lifecycle.assertionLabel),
    )

    const nextMarker = lifecycle?.endMarker
    const startMarker = lifecycle?.startMarker
    const isExactVerifyNavigationArtifact = Boolean(
      lifecycle &&
        observed.failure === 'net::ERR_ABORTED' &&
        lifecycle.method === 'POST' &&
        lifecycle.pathname === '/api/sessions/verify' &&
        lifecycle.responseStatus === 200 &&
        lifecycle.routeHandlerSettledAt !== null &&
        lifecycle.assertionCompletedAt !== null &&
        lifecycle.assertionCompletedAt < lifecycle.failedAt &&
        typeof lifecycle.assertionLabel === 'string' &&
        lifecycle.assertionLabel.includes('ready-asserted') &&
        startMarker &&
        nextMarker &&
        nextMarker.suite === startMarker.suite &&
        nextMarker.plannedNavigation === true &&
        nextMarker.navigationEpoch === startMarker.navigationEpoch + 1 &&
        /^(?:page\.(?:goto|reload|goBack|goForward)|terminal-context-close)/u.test(
          nextMarker.phase,
        ),
    )

    if (isExactClear204Artifact) {
      expected.push({
        kind: 'chromium-cdp-204-no-content',
        url: observed.url,
        failure: observed.failure,
        lifecycle,
      })
    } else if (isExactVerifyNavigationArtifact) {
      expected.push({
        kind: 'mock-unread-verify-next-planned-navigation',
        url: observed.url,
        failure: observed.failure,
        lifecycle,
      })
    } else {
      unexpected.push({ url: observed.url, failure: observed.failure, lifecycle })
    }
  }

  browserDiagnostics.expectedLifecycleArtifacts.push(...expected)
  browserDiagnostics.unexpectedRequestFailures.push(...unexpected)
  const counts = expected.reduce((accumulator, entry) => {
    accumulator[entry.kind] = (accumulator[entry.kind] || 0) + 1
    return accumulator
  }, {})
  record('request-lifecycle-artifacts-exactly-classified', {
    counts,
    expected: expected.length,
    unexpected: unexpected.length,
  })
  return counts
}

async function closeHarnessRuntime(runtime) {
  runtime.state.harnessClosing = true
  beginPlannedNavigation(runtime.page, 'terminal-context-close')
  await runtime.context.close()
}

async function warmViteDependencies(browser, baseUrl) {
  const context = await browser.newContext({ baseURL: baseUrl, serviceWorkers: 'block' })
  try {
    await context.route('https://telegram.org/**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }),
    )
    await context.route('**/api/**', (route) => json(route, {}))
    const page = await context.newPage()
    await page.addInitScript(() => {
      window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
      localStorage.removeItem('auth_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('current_user_summary')
    })
    await plannedGoto(page, '/login', { waitUntil: 'domcontentloaded' }, 'warm-vite:login')
    await page.getByText('ورود به سامانه', { exact: false }).first().waitFor({ timeout: 30_000 })
    await page.waitForLoadState('networkidle')
  } finally {
    await context.close()
  }
}

async function runRequestLifecycleDiagnostic(browser, baseUrl) {
  const state = newRuntimeState()
  const runtime = await createPage(browser, baseUrl, {
    authenticated: true,
    state,
    suite: 'request-lifecycle-diagnostic',
  })
  const { page } = runtime
  let closed = false
  const navigations = []

  const navigate = async (navigationEpoch, routePath, readyText, label) => {
    state.lifecycleMarker = {
      suite: 'request-lifecycle-diagnostic',
      viewport: 'mobile-390',
      navigationEpoch,
      phase: `${label}:planned-goto`,
      plannedNavigation: true,
      assertionComplete: false,
    }
    const verifyResponse = waitForApiResponse(page, '/api/sessions/verify')
    await gotoReady(page, routePath, readyText)
    const response = await verifyResponse
    assert.equal(response.status(), 200)
    markResponseAssertionComplete(response, `${label}:verify-and-ready-asserted`)
    state.lifecycleMarker = {
      ...state.lifecycleMarker,
      phase: `${label}:ready-asserted`,
      plannedNavigation: false,
      assertionComplete: true,
    }
    const bodyDrain = await Promise.race([
      response.body().then(() => 'resolved', (error) => `rejected:${error instanceof Error ? error.message : String(error)}`),
      new Promise((resolve) => setTimeout(() => resolve('timeout'), 3_000)),
    ])
    await new Promise((resolve) => setTimeout(resolve, 100))
    navigations.push({ navigationEpoch, routePath, label, bodyDrain })
  }

  try {
    await page.setViewportSize({ width: 390, height: 844 })
    await navigate(1, '/', 'ورود به بازار', 'home')
    await navigate(2, '/setup-password', 'تنظیم رمز عبور', 'focused')
    await navigate(3, '/', 'ورود به بازار', 'home-return')
    state.lifecycleMarker = {
      suite: 'request-lifecycle-diagnostic',
      viewport: 'mobile-390',
      navigationEpoch: 4,
      phase: 'terminal-context-close',
      plannedNavigation: true,
      assertionComplete: true,
    }
    await closeHarnessRuntime(runtime)
    closed = true
    await new Promise((resolve) => setTimeout(resolve, 100))
  } finally {
    if (!closed) await closeHarnessRuntime(runtime)
  }

  const relevant = state.requestLifecycle.filter((entry) => entry.pathname === '/api/sessions/verify')
  record('request-lifecycle-isolated-diagnostic', { navigations, relevant })
  return { navigations, relevant, allRequests: state.requestLifecycle }
}

async function runClearLifecycleDiagnostic(browser, baseUrl) {
  const state = newRuntimeState({ registrationCompleted: true })
  const runtime = await createPage(browser, baseUrl, {
    state,
    suite: 'clear-lifecycle-diagnostic',
  })
  const { page } = runtime
  let closed = false
  let bodyDrain = null
  try {
    await runtime.context.addCookies([
      {
        name: 'web_registration',
        value: state.registrationHandle,
        url: baseUrl,
        httpOnly: true,
        sameSite: 'Strict',
      },
    ])
    await page.setViewportSize({ width: 390, height: 844 })
    state.lifecycleMarker = {
      suite: 'clear-lifecycle-diagnostic',
      viewport: 'mobile-390',
      navigationEpoch: 1,
      phase: 'registration-complete:planned-goto',
      plannedNavigation: true,
      assertionComplete: false,
    }
    const clearResponse = waitForApiResponse(page, '/api/auth/registration-context/clear')
    await plannedGoto(page, '/register', { waitUntil: 'domcontentloaded' }, 'clear-diagnostic:register')
    await page.waitForURL((url) => url.pathname === '/login' && url.searchParams.get('registration') === 'complete')
    await page.getByText('ورود به سامانه', { exact: false }).first().waitFor()
    const response = await clearResponse
    assert.equal(response.status(), 204)
    markResponseAssertionComplete(response, 'registration-complete:clear-and-ready-asserted')
    state.lifecycleMarker = {
      ...state.lifecycleMarker,
      phase: 'registration-complete:ready-and-clear-asserted',
      plannedNavigation: false,
      assertionComplete: true,
    }
    bodyDrain = await Promise.race([
      response.body().then(() => 'resolved', (error) => `rejected:${error instanceof Error ? error.message : String(error)}`),
      new Promise((resolve) => setTimeout(() => resolve('timeout'), 3_000)),
    ])
    await new Promise((resolve) => setTimeout(resolve, 100))
    state.lifecycleMarker = {
      suite: 'clear-lifecycle-diagnostic',
      viewport: 'mobile-390',
      navigationEpoch: 2,
      phase: 'post-clear:planned-goto',
      plannedNavigation: true,
      assertionComplete: false,
    }
    await plannedGoto(page, '/login', { waitUntil: 'domcontentloaded' }, 'clear-diagnostic:next-login')
    await page.getByText('ورود به سامانه', { exact: false }).first().waitFor()
    await new Promise((resolve) => setTimeout(resolve, 100))
    state.lifecycleMarker = {
      ...state.lifecycleMarker,
      navigationEpoch: 3,
      phase: 'terminal-context-close',
      plannedNavigation: true,
      assertionComplete: true,
    }
    await closeHarnessRuntime(runtime)
    closed = true
    await new Promise((resolve) => setTimeout(resolve, 100))
  } finally {
    if (!closed) await closeHarnessRuntime(runtime)
  }
  const relevant = state.requestLifecycle.filter(
    (entry) => entry.pathname === '/api/auth/registration-context/clear',
  )
  record('clear-lifecycle-isolated-diagnostic', { bodyDrain, relevant })
  return { bodyDrain, relevant, allRequests: state.requestLifecycle }
}

async function gotoReady(page, routePath, readyText) {
  await plannedGoto(page, routePath, { waitUntil: 'domcontentloaded' })
  await page.locator('#app').waitFor({ state: 'visible', timeout: 15_000 })
  await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })
  await page.locator('#boot-loader').waitFor({ state: 'hidden', timeout: 15_000 })
  if (readyText) {
    await page.getByText(readyText, { exact: false }).first().waitFor({ state: 'visible', timeout: 15_000 })
  }
  await page.evaluate(async () => document.fonts?.ready)
  markPageAssertionComplete(page, `ready-asserted:${routePath}`)
}

async function gotoAuthenticatedReady(page, routePath, readyText) {
  const verified = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/sessions/verify' && response.request().method() === 'POST',
    { timeout: 15_000 },
  )
  await gotoReady(page, routePath, readyText)
  const response = await verified
  assert.equal(response.status(), 200, `${routePath}: session verification did not settle`)
  markResponseAssertionComplete(response, `session-ready-asserted:${routePath}`)
  await new Promise((resolve) => setTimeout(resolve, 50))
}

function waitForApiResponse(page, apiPath) {
  const response = page.waitForResponse((candidate) => new URL(candidate.url()).pathname === apiPath, { timeout: 15_000 })
  void response.catch(() => undefined)
  return response
}

async function settleApiResponse(responsePromise, expectedStatus, assertionLabel = null) {
  const response = await responsePromise
  assert.equal(response.status(), expectedStatus, `${new URL(response.url()).pathname}: unexpected status`)
  markResponseAssertionComplete(
    response,
    assertionLabel || `api-response-asserted:${new URL(response.url()).pathname}:${expectedStatus}`,
  )
  await new Promise((resolve) => setTimeout(resolve, 50))
  return response
}

async function waitForNodeCondition(predicate, label, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(`Timed out waiting for ${label}`)
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
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
      return {
        selector: element.className?.toString?.() || element.tagName,
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      }
    }
    const doc = document.documentElement
    const body = document.body
    const app = document.querySelector('#app')
    const fixed = [...document.querySelectorAll('body *')]
      .filter((element) => {
        if (!(element instanceof HTMLElement) || !visible(element)) return false
        const style = getComputedStyle(element)
        return style.position === 'fixed' || style.position === 'sticky'
      })
      .map(rectOf)
    const ctas = [...document.querySelectorAll('.ui-v2-auth-flow .ui-button, .ui-v2-pwa-actions .ui-button, .ui-v2-auth-invite-route, .hero-btn, .ui-v2-session-action')]
      .filter((element) => element instanceof HTMLElement && visible(element))
      .map(rectOf)
    const navItems = [...document.querySelectorAll('.ui-v2-bottom-nav-item')]
      .filter((element) => element instanceof HTMLElement && visible(element))
      .map(rectOf)
    const navLabels = [...document.querySelectorAll('.ui-v2-bottom-nav-label')]
      .filter((element) => element instanceof HTMLElement && visible(element))
      .map((element) => Number.parseFloat(getComputedStyle(element).fontSize))
    const critical = [...document.querySelectorAll('.ui-v2-auth-flow, .ui-v2-auth-flow__content, .main-section, .ui-v2-pwa-section, .bottom-nav-bar')]
      .filter((element) => element instanceof HTMLElement && visible(element))
      .map((element) => ({
        selector: element.className.toString(),
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        ...rectOf(element),
      }))
    return {
      viewport: { width: innerWidth, height: innerHeight },
      documentScrollWidth: Math.max(doc.scrollWidth, body.scrollWidth, app?.scrollWidth || 0),
      fixed,
      ctas,
      navItems,
      navLabels,
      critical,
    }
  })
}

function assertLayout(metrics, label) {
  assert.ok(
    metrics.documentScrollWidth <= metrics.viewport.width + 1,
    `${label}: document horizontal overflow ${metrics.documentScrollWidth}/${metrics.viewport.width}`,
  )
  for (const item of metrics.fixed) {
    assert.ok(item.left >= -1, `${label}: fixed left clipping ${item.selector}`)
    assert.ok(item.right <= metrics.viewport.width + 1, `${label}: fixed right clipping ${item.selector}`)
  }
  for (const item of metrics.critical) {
    assert.ok(item.scrollWidth <= item.clientWidth + 1, `${label}: critical overflow ${item.selector}`)
    assert.ok(item.left >= -1, `${label}: critical left clipping ${item.selector}`)
    assert.ok(item.right <= metrics.viewport.width + 1, `${label}: critical right clipping ${item.selector}`)
  }
  assert.ok(metrics.ctas.length > 0, `${label}: no visible CTA measured`)
  for (const cta of metrics.ctas) {
    assert.ok(cta.height >= 47.5, `${label}: CTA below 48px (${cta.height}) ${cta.selector}`)
  }
  for (const item of metrics.navItems) {
    assert.ok(item.width >= 43.5 && item.height >= 43.5, `${label}: nav target below 44x44`)
  }
  for (const fontSize of metrics.navLabels) {
    assert.ok(fontSize >= 10.9, `${label}: nav label below 11px (${fontSize})`)
  }
}

async function focusProof(page, selector, label) {
  const target = page.locator(selector).first()
  await target.focus()
  const result = await target.evaluate((element) => {
    const style = getComputedStyle(element)
    return {
      active: document.activeElement === element,
      width: Number.parseFloat(style.outlineWidth),
      style: style.outlineStyle,
      offset: Number.parseFloat(style.outlineOffset),
      color: style.outlineColor,
    }
  })
  assert.equal(result.active, true, `${label}: focus target not active`)
  assert.ok(Math.abs(result.width - 3) <= 0.02, `${label}: focus width ${result.width}`)
  assert.equal(result.style, 'solid', `${label}: focus style`)
  assert.ok(result.offset >= 1.9, `${label}: focus offset ${result.offset}`)
  return result
}

async function takeScreenshot(page, fileName) {
  const filePath = path.join(OUTPUT_DIR, fileName)
  await page.screenshot({ path: filePath, fullPage: true, animations: 'disabled' })
  const stat = fs.statSync(filePath)
  screenshots.push({ file: fileName, bytes: stat.size, sha256: sha256File(filePath) })
}

async function assertPublicShell(page, label) {
  await page.locator('.ui-v2-auth-flow').waitFor()
  assert.equal(await page.locator('.app-authenticated-shell-v2').count(), 0, `${label}: auth shell leaked`)
  assert.equal(await page.locator('.bottom-nav-wrapper, .fab-container').count(), 0, `${label}: daily nav leaked`)
  assert.equal(await page.locator('.ui-v2-pwa-install').count(), 0, `${label}: PWA leaked`)
  assert.equal(await page.locator('.app-route-scroll--no-daily-nav').count(), 1, `${label}: nav space reserved`)
  assert.equal(await page.locator('.app-route-v2-scope[data-ui-system="v2"]').count(), 1, `${label}: route V2 scope missing`)
}

async function runResponsiveShellMatrix(browser, baseUrl) {
  const guest = await createPage(browser, baseUrl, { suite: 'responsive-shell-matrix' })
  const authenticated = await createPage(browser, baseUrl, {
    authenticated: true,
    suite: 'responsive-shell-matrix',
  })
  const rows = []
  try {
    for (const viewport of VIEWPORTS) {
      await guest.page.setViewportSize({ width: viewport.width, height: viewport.height })
      await gotoReady(guest.page, '/login', 'ورود به سامانه')
      await assertPublicShell(guest.page, `${viewport.label}:login`)
      const loginLayout = await measureLayout(guest.page)
      assertLayout(loginLayout, `${viewport.label}:login`)
      const loginFocus = await focusProof(guest.page, 'input[autocomplete="tel"]', `${viewport.label}:login`)

      await gotoReady(guest.page, '/does-not-exist?outcome=forbidden', 'این صفحه پیدا نشد')
      await assertPublicShell(guest.page, `${viewport.label}:system-guest`)
      assert.equal(await guest.page.locator('[data-test="route-system-recovery"][data-outcome="not-found"]').count(), 1)
      const guestSystemLayout = await measureLayout(guest.page)
      assertLayout(guestSystemLayout, `${viewport.label}:system-guest`)

      await authenticated.page.setViewportSize({ width: viewport.width, height: viewport.height })
      await gotoAuthenticatedReady(authenticated.page, '/setup-password', 'تنظیم رمز عبور')
      assert.equal(await authenticated.page.locator('.app-authenticated-shell-v2[data-ui-system="v2"]').count(), 1)
      assert.equal(await authenticated.page.locator('.bottom-nav-wrapper, .fab-container').count(), 0)
      assert.equal(await authenticated.page.locator('.ui-v2-auth-flow--focused').count(), 1)
      assert.equal(await authenticated.page.locator('.app-route-scroll--no-daily-nav').count(), 1)
      const focusedLayout = await measureLayout(authenticated.page)
      assertLayout(focusedLayout, `${viewport.label}:focused`)
      const focusedFocus = await focusProof(authenticated.page, 'input[autocomplete="new-password"]', `${viewport.label}:focused`)

      await gotoAuthenticatedReady(authenticated.page, '/', 'ورود به بازار')
      assert.equal(await authenticated.page.locator('.app-authenticated-shell-v2[data-ui-system="v2"]').count(), 1)
      assert.equal(await authenticated.page.locator('.ui-v2-bottom-nav').count(), 1)
      assert.equal(await authenticated.page.locator('.ui-v2-pwa-install').count(), 0)
      const hiddenPwaSection = await authenticated.page.locator('.ui-v2-pwa-section').evaluateAll((sections) => ({
        count: sections.length,
        displays: sections.map((section) => getComputedStyle(section).display),
      }))
      assert.ok(
        hiddenPwaSection.count === 0 || hiddenPwaSection.displays.every((display) => display === 'none'),
        `${viewport.label}: hidden PWA wrapper remained a layout flex item (${JSON.stringify(hiddenPwaSection)})`,
      )
      const homeLayout = await measureLayout(authenticated.page)
      assertLayout(homeLayout, `${viewport.label}:standard-home`)
      const navFocus = await focusProof(authenticated.page, '.ui-v2-bottom-nav-item', `${viewport.label}:bottom-nav`)

      await gotoAuthenticatedReady(
        authenticated.page,
        '/__system/recovery?outcome=deep-link-failure',
        'باز کردن این صفحه ممکن نشد',
      )
      assert.equal(await authenticated.page.locator('.app-authenticated-shell-v2[data-ui-system="v2"]').count(), 1)
      await authenticated.page.locator('.ui-v2-bottom-nav').waitFor({ state: 'visible' })
      assert.equal(await authenticated.page.locator('[data-test="route-system-recovery"][data-outcome="deep-link-failure"]').count(), 1)
      const authSystemLayout = await measureLayout(authenticated.page)
      assertLayout(authSystemLayout, `${viewport.label}:system-authenticated`)

      rows.push({
        viewport,
        login: loginLayout,
        systemGuest: guestSystemLayout,
        focused: focusedLayout,
        home: homeLayout,
        hiddenPwaSection,
        systemAuthenticated: authSystemLayout,
        focus: { login: loginFocus, focused: focusedFocus, nav: navFocus },
      })
      progress('responsive-viewport-complete', { viewport: viewport.label, completed: rows.length, total: VIEWPORTS.length })
    }

    await guest.page.setViewportSize({ width: 390, height: 844 })
    await gotoReady(guest.page, '/__system/recovery?outcome=forbidden', 'دسترسی به این بخش مجاز نیست')
    assert.equal(await guest.page.locator('[data-test="route-system-recovery"][data-outcome="forbidden"]').count(), 1)

    await guest.page.setViewportSize({ width: 360, height: 740 })
    await gotoReady(guest.page, '/login', 'ورود به سامانه')
    const mobileInput = guest.page.locator('input[autocomplete="tel"]')
    await mobileInput.focus()
    await guest.page.setViewportSize({ width: 360, height: 430 })
    await guest.page.keyboard.press('Tab')
    const keyboardProof = await guest.page.evaluate(() => {
      const viewportMeta = document.querySelector('meta[name="viewport"]')?.getAttribute('content') || ''
      const active = document.activeElement
      const rect = active instanceof HTMLElement ? active.getBoundingClientRect() : null
      return {
        viewportMeta,
        activeTag: active?.tagName || null,
        activeRect: rect ? { top: rect.top, bottom: rect.bottom, height: rect.height } : null,
        viewportHeight: innerHeight,
      }
    })
    assert.match(keyboardProof.viewportMeta, /interactive-widget=resizes-content/u)
    assert.ok(keyboardProof.activeRect && keyboardProof.activeRect.top >= -1)
    assert.ok(keyboardProof.activeRect && keyboardProof.activeRect.bottom <= keyboardProof.viewportHeight + 1)

    await guest.page.setViewportSize({ width: 390, height: 844 })
    await gotoReady(guest.page, '/login', 'ورود به سامانه')
    await takeScreenshot(guest.page, 'login-public-mobile-390.png')
    await authenticated.page.setViewportSize({ width: 390, height: 844 })
    await gotoAuthenticatedReady(authenticated.page, '/setup-password', 'تنظیم رمز عبور')
    await takeScreenshot(authenticated.page, 'setup-password-focused-mobile-390.png')
    await authenticated.page.setViewportSize({ width: 1440, height: 900 })
    await gotoAuthenticatedReady(authenticated.page, '/', 'ورود به بازار')
    await takeScreenshot(authenticated.page, 'home-standard-desktop-1440.png')

    record('responsive-shell-matrix-8-widths', { rows: rows.length })
    record('public-focused-system-shell-isolation')
    record('home-hidden-pwa-wrapper-no-layout-item')
    record('focus-indicator-exact-3px')
    record('cta-48-nav-target-44-label-11')
    record('mobile-keyboard-resize-proxy', keyboardProof)
    return { rows, keyboardProof }
  } finally {
    await closeHarnessRuntime(guest)
    await closeHarnessRuntime(authenticated)
  }
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

async function emitSocket(page, type, data) {
  await page.waitForFunction(() => Array.isArray(window.__stage3Sockets) && window.__stage3Sockets.length > 0)
  await page.evaluate(({ eventType, payload }) => {
    const socket = window.__stage3Sockets.at(-1)
    socket.emit(eventType, payload)
  }, { eventType: type, payload: data })
}

function parseRgb(value) {
  const parts = value.match(/[\d.]+/gu)?.slice(0, 3).map(Number)
  assert.equal(parts?.length, 3, `Could not parse color: ${value}`)
  return parts
}

function contrastRatio(foreground, background) {
  const luminance = (rgb) => {
    const channels = rgb
      .map((value) => value / 255)
      .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
  }
  const first = luminance(foreground)
  const second = luminance(background)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

async function runPwaLayersAndMotion(browser, baseUrl) {
  const state = newRuntimeState({ tradesMode: 'pending' })
  const runtime = await createPage(browser, baseUrl, {
    authenticated: true,
    state,
    suite: 'pwa-layers-motion',
  })
  const { page, context } = runtime
  try {
    await page.clock.install()
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoAuthenticatedReady(page, '/', 'ورود به بازار')
    await dispatchInstallPrompt(page)
    await page.clock.fastForward(4100)
    assert.equal(await page.locator('.ui-v2-pwa-install').count(), 0, 'PWA shown during trades pending')
    assert.equal(typeof state.releaseTrades, 'function', 'trades pending route not captured')
    await state.releaseTrades()
    await page.getByText('امروز معامله‌ای ثبت نشده است', { exact: false }).waitFor()
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'visible' })
    assert.notEqual(await page.locator('.ui-v2-pwa-section').evaluate((section) => getComputedStyle(section).display), 'none')
    const pwaActionHeights = await page.locator('.ui-v2-pwa-actions .ui-button').evaluateAll((buttons) =>
      buttons.map((button) => button.getBoundingClientRect().height),
    )
    assert.ok(pwaActionHeights.length === 2 && pwaActionHeights.every((height) => height >= 47.5))
    await takeScreenshot(page, 'home-pwa-positive-mobile-390.png')

    const openingNav = page.locator('.ui-v2-bottom-nav-item').first()
    await openingNav.focus()
    await emitSocket(page, 'message', {
      id: 'stage3-toast',
      title: 'اعلان آزمون',
      body: 'پیام آزمون ترتیب لایه‌ها',
      level: 'info',
      category: 'system',
    })
    await page.locator('.ui-v2-toast-item').waitFor({ state: 'visible' })
    await emitSocket(page, 'session:login_request', {
      request_id: 'stage3-login-request',
      device_name: 'Stage 3 Browser',
      device_ip: '127.0.0.1',
      expires_at: new Date(Date.now() + 120_000).toISOString(),
    })
    const dialog = page.locator('.ui-v2-session-card[role="dialog"]')
    await dialog.waitFor({ state: 'visible' })
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'hidden' })
    assert.equal(await page.locator('.ui-v2-pwa-install').isVisible(), false, 'PWA remained visible under security layer')
    assert.equal(await page.locator('.ui-v2-toast-layer[aria-hidden="true"][inert]').count(), 1)

    state.verifyMode = 'abort'
    await emitSocket(page, 'session:revoked', {})
    await page.locator('.ui-v2-connection-banner').waitFor({ state: 'visible' })

    const layerMetrics = await page.evaluate(() => {
      const z = (selector) => Number.parseInt(getComputedStyle(document.querySelector(selector)).zIndex, 10)
      const header = document.querySelector('.ui-v2-session-header')
      const title = header?.querySelector('h2')
      const icon = header?.querySelector('.ui-v2-session-icon')
      const headerStyle = getComputedStyle(header)
      return {
        z: {
          security: z('.ui-v2-session-layer'),
          connection: z('.ui-v2-connection-banner'),
          toast: z('.ui-v2-toast-layer'),
          nav: z('.bottom-nav-wrapper'),
        },
        header: {
          backgroundColor: headerStyle.backgroundColor,
          backgroundImage: headerStyle.backgroundImage,
          titleColor: getComputedStyle(title).color,
          iconColor: getComputedStyle(icon).color,
        },
      }
    })
    assert.deepEqual(layerMetrics.z, { security: 10030, connection: 10020, toast: 10010, nav: 50 })
    assert.equal(layerMetrics.header.backgroundColor, 'rgb(232, 240, 255)')
    assert.equal(layerMetrics.header.backgroundImage, 'none')
    assert.equal(layerMetrics.header.titleColor, 'rgb(15, 35, 60)')
    assert.equal(layerMetrics.header.iconColor, 'rgb(49, 93, 168)')
    const titleContrast = contrastRatio(parseRgb(layerMetrics.header.titleColor), parseRgb(layerMetrics.header.backgroundColor))
    const iconContrast = contrastRatio(parseRgb(layerMetrics.header.iconColor), parseRgb(layerMetrics.header.backgroundColor))
    assert.ok(titleContrast >= 4.5, `V2 session title contrast ${titleContrast}`)
    assert.ok(iconContrast >= 3, `V2 session icon contrast ${iconContrast}`)

    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('role')), 'dialog')
    await page.keyboard.press('Escape')
    assert.equal(await dialog.count(), 1, 'Escape dismissed required security dialog')
    const actions = dialog.locator('button')
    const firstAction = actions.first()
    const lastAction = actions.last()
    await firstAction.focus()
    await page.keyboard.press('Shift+Tab')
    assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), await lastAction.textContent().then((text) => text?.trim()))
    await lastAction.focus()
    await page.keyboard.press('Tab')
    assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), await firstAction.textContent().then((text) => text?.trim()))
    await page.evaluate(() => document.querySelector('.ui-v2-bottom-nav-item')?.focus())
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('role')), 'dialog')
    await takeScreenshot(page, 'layer-coexistence-session-connection-toast-nav-mobile-390.png')

    state.verifyMode = 'success'
    await firstAction.click()
    await dialog.waitFor({ state: 'hidden' })
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('ui-v2-bottom-nav-item')), true)

    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'visible' })
    await context.setOffline(true)
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'hidden' })
    await context.setOffline(false)
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'visible' })
    await page.getByRole('button', { name: 'بعداً' }).click()
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'hidden' })
    const dismissTimestamp = await page.evaluate(() => Number(localStorage.getItem('pwa_install_prompt_dismissed_at_v2')))
    assert.ok(Number.isFinite(dismissTimestamp) && dismissTimestamp > 0, 'dismiss TTL timestamp missing')
    await dispatchInstallPrompt(page)
    await page.locator('.ui-v2-pwa-install').waitFor({ state: 'hidden' })
    assert.equal(await page.locator('.ui-v2-pwa-install').isVisible(), false, 'dismiss TTL ignored')

    await page.emulateMedia({ reducedMotion: 'reduce' })
    const motion = await page.evaluate(() => {
      const scope = document.querySelector('[data-ui-system="v2"]')
      const style = getComputedStyle(scope)
      const durations = [...document.querySelectorAll('[data-ui-system="v2"] [data-ui-v2-motion], [data-ui-system="v2"] .ui-v2-bottom-nav-item')]
        .filter((element) => {
          const rect = element.getBoundingClientRect()
          const computed = getComputedStyle(element)
          return computed.display !== 'none' && rect.width > 0 && rect.height > 0
        })
        .map((element) => ({
          className: element.className?.toString?.() || element.tagName,
          transitionDuration: getComputedStyle(element).transitionDuration,
          animationDuration: getComputedStyle(element).animationDuration,
        }))
      return {
        micro: style.getPropertyValue('--ui-v2-motion-micro').trim(),
        state: style.getPropertyValue('--ui-v2-motion-state').trim(),
        durations,
      }
    })
    assert.equal(motion.micro, '1ms')
    assert.equal(motion.state, '1ms')
    const seconds = (value) => value.split(',').map((item) => item.trim()).map((item) => item.endsWith('ms') ? Number.parseFloat(item) / 1000 : Number.parseFloat(item))
    for (const item of motion.durations) {
      for (const duration of [...seconds(item.transitionDuration), ...seconds(item.animationDuration)]) {
        assert.ok(duration <= 0.001 || duration === 0, `reduced motion exceeded 1ms: ${item.className} ${duration}`)
      }
    }

    record('pwa-positive-negative-runtime', { pwaActionHeights })
    record('layer-computed-z-order', layerMetrics.z)
    record('session-modal-focus-trap-escape-restore')
    record('session-modal-v2-color-contrast', { ...layerMetrics.header, titleContrast, iconContrast })
    record('reduced-motion-runtime-max-1ms', motion)
    return { pwaActionHeights, layerMetrics, titleContrast, iconContrast, motion }
  } finally {
    await closeHarnessRuntime(runtime)
  }
}

async function runLegacyModalProof(browser, baseUrl) {
  const runtime = await createPage(browser, baseUrl, {
    authenticated: true,
    suite: 'legacy-modal',
  })
  const { page, context } = runtime
  try {
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoAuthenticatedReady(page, '/market', null)
    assert.equal(await page.locator('.ui-v2-pwa-install').count(), 0)
    await emitSocket(page, 'session:login_request', {
      request_id: 'stage3-legacy-request',
      device_name: 'Legacy Browser',
      device_ip: '127.0.0.1',
      expires_at: new Date(Date.now() + 120_000).toISOString(),
    })
    const legacyLayer = page.locator('.fixed.inset-0.z-\\[9999\\]').first()
    await legacyLayer.waitFor({ state: 'visible' })
    const legacy = await legacyLayer.evaluate((layer) => {
      const card = layer.querySelector('.animate-scale-in')
      const header = card?.querySelector('.bg-gradient-to-r')
      const title = header?.querySelector('h3')
      const icon = header?.querySelector('svg')
      return {
        zIndex: getComputedStyle(layer).zIndex,
        dataUiSystem: layer.getAttribute('data-ui-system'),
        role: card?.getAttribute('role') || null,
        backgroundImage: getComputedStyle(header).backgroundImage,
        titleColor: getComputedStyle(title).color,
        iconColor: getComputedStyle(icon).color,
      }
    })
    assert.equal(legacy.zIndex, '9999')
    assert.equal(legacy.dataUiSystem, null)
    assert.equal(legacy.role, null)
    assert.match(legacy.backgroundImage, /linear-gradient/u)
    assert.equal(legacy.titleColor, 'rgb(255, 255, 255)')
    assert.equal(legacy.iconColor, 'rgb(255, 255, 255)')
    await takeScreenshot(page, 'legacy-session-modal-protected-market-mobile-390.png')
    record('legacy-modal-white-gradient-false-branch', legacy)
    return legacy
  } finally {
    await closeHarnessRuntime(runtime)
  }
}

async function secretScan(page, secrets) {
  const browserState = await page.evaluate(() => {
    const storage = (source) =>
      Array.from({ length: source.length }, (_, index) => {
        const key = source.key(index) || ''
        return `${key}:${source.getItem(key) || ''}`
      }).join('|')
    const vueRouter = document.querySelector('#app')?.__vue_app__?.config?.globalProperties?.$router
    const vueRoute = vueRouter?.currentRoute?.value
    return {
      url: location.href,
      historyState: JSON.stringify(history.state),
      vueRouterCurrentRoute: vueRoute
        ? JSON.stringify({
            path: vueRoute.path,
            fullPath: vueRoute.fullPath,
            query: vueRoute.query,
            hash: vueRoute.hash,
          })
        : '',
      html: document.documentElement.outerHTML,
      bodyText: document.body.innerText,
      localStorage: storage(localStorage),
      sessionStorage: storage(sessionStorage),
      documentCookie: document.cookie,
      resources: performance.getEntriesByType('resource').map((entry) => entry.name).join('|'),
    }
  })
  const failures = []
  for (const secret of secrets) {
    for (const [surface, value] of Object.entries(browserState)) {
      if (String(value).includes(secret)) failures.push({ surface })
    }
    if (browserDiagnostics.consoleErrors.some((message) => message.includes(secret))) failures.push({ surface: 'console-error' })
  }
  assert.deepEqual(failures, [], `registration secret leaked: ${JSON.stringify(failures)}`)
  return {
    surfaces: Object.keys(browserState),
    failures,
    opaqueCookieVisibleToDocument: browserState.documentCookie.includes('web_registration'),
  }
}

function assertCanonicalVueNavigationState(navigationState, expectedCurrent, expectedHash) {
  assert.deepEqual(Object.keys(navigationState.historyState).sort(), [
    'back',
    'current',
    'forward',
    'position',
    'replaced',
    'scroll',
  ])
  assert.deepEqual(
    {
      back: navigationState.historyState.back,
      current: navigationState.historyState.current,
      forward: navigationState.historyState.forward,
      replaced: navigationState.historyState.replaced,
      scroll: navigationState.historyState.scroll,
    },
    { back: null, current: expectedCurrent, forward: null, replaced: true, scroll: false },
  )
  assert.equal(Number.isInteger(navigationState.historyState.position), true)
  assert.ok(navigationState.historyState.position >= 0)
  assert.deepEqual(navigationState.vueRoute, {
    path: '/register',
    fullPath: expectedCurrent,
    query: {},
    hash: expectedHash,
  })
  assert.equal(
    /(?:REG-|INV-|registration[_-]?token)/iu.test(
      JSON.stringify({
        historyState: navigationState.historyState,
        vueRoute: navigationState.vueRoute,
      }),
    ),
    false,
  )
}

async function runBrowserUrlScrubProof(browser, baseUrl) {
  const fragmentCases = [
    { label: 'fragment-raw', path: '/register#registration_token=REG-fragment-secret', secret: 'REG-fragment-secret' },
    { label: 'fragment-double-encoded', path: '/register#registration_token%253DREG-double-secret', secret: 'REG-double-secret' },
    { label: 'fragment-path', path: '/register#/legacy/registration_token=REG-fragment-path-secret', secret: 'REG-fragment-path-secret' },
    { label: 'fragment-path-encoded', path: '/register#/legacy/registration_token%3DREG-fragment-path-encoded-secret', secret: 'REG-fragment-path-encoded-secret' },
  ]
  const fragments = []
  for (const fragmentCase of fragmentCases) {
    const state = newRuntimeState({
      rawInvite: fragmentCase.secret,
      contextMode: 'gone',
      holdContextRead: true,
    })
    const runtime = await createPage(browser, baseUrl, {
      state,
      suite: `browser-url-scrub:${fragmentCase.label}`,
    })
    try {
      await plannedGoto(runtime.page, fragmentCase.path, { waitUntil: 'domcontentloaded' })
      await waitForNodeCondition(() => typeof state.releaseContextRead === 'function', `${fragmentCase.label} held context read`)
      const urlState = await runtime.page.evaluate(() => ({
        pathname: location.pathname,
        search: location.search,
        hash: location.hash,
        historyState: history.state,
        vueRoute: (() => {
          const route = document.querySelector('#app')?.__vue_app__?.config?.globalProperties?.$router?.currentRoute?.value
          return route
            ? { path: route.path, fullPath: route.fullPath, query: route.query, hash: route.hash }
            : null
        })(),
      }))
      assert.deepEqual(
        { pathname: urlState.pathname, search: urlState.search, hash: urlState.hash },
        { pathname: '/register', search: '', hash: '' },
      )
      assertCanonicalVueNavigationState(urlState, '/register', '')
      const scan = await secretScan(runtime.page, [fragmentCase.secret])
      const contextResponse = waitForApiResponse(runtime.page, '/api/auth/registration-context')
      state.releaseContextRead()
      await settleApiResponse(contextResponse, 410)
      await runtime.page.getByText('جلسه ثبت‌نام نامعتبر یا منقضی شده است.', { exact: false }).waitFor()
      assert.equal(state.exchangeCount, 0)
      fragments.push({ label: fragmentCase.label, urlState, scan, contextReadCount: state.contextReadCount })
      progress('url-scrub-fragment-complete', { fragment: fragmentCase.label })
    } finally {
      state.releaseContextRead?.()
      await closeHarnessRuntime(runtime)
    }
  }

  const querySecret = 'REG-query-secret'
  const queryState = newRuntimeState({
    rawInvite: querySecret,
    registrationKind: 'registration',
    registrationProgress: 'otp_verified',
    holdExchange: true,
  })
  const queryRuntime = await createPage(browser, baseUrl, {
    state: queryState,
    suite: 'browser-url-scrub:query',
  })
  let queryResult
  try {
    progress('url-scrub-query-start')
    await plannedGoto(queryRuntime.page, `/register?registration_token=${querySecret}#safe`, { waitUntil: 'domcontentloaded' })
    progress('url-scrub-query-domcontentloaded')
    await waitForNodeCondition(() => typeof queryState.releaseExchange === 'function', 'query handoff held exchange')
    progress('url-scrub-query-exchange-held')
    const urlState = await queryRuntime.page.evaluate(() => ({
      pathname: location.pathname,
      search: location.search,
      hash: location.hash,
      historyState: history.state,
      vueRoute: (() => {
        const route = document.querySelector('#app')?.__vue_app__?.config?.globalProperties?.$router?.currentRoute?.value
        return route
          ? { path: route.path, fullPath: route.fullPath, query: route.query, hash: route.hash }
          : null
      })(),
    }))
    assert.equal(urlState.pathname, '/register')
    assert.equal(urlState.search, '')
    assert.equal(urlState.hash, '#safe')
    assertCanonicalVueNavigationState(urlState, '/register#safe', '#safe')
    assert.equal(JSON.stringify(urlState.historyState).includes(querySecret), false)
    const scanBeforeExchangeCompletion = await secretScan(queryRuntime.page, [querySecret])
    assert.equal(queryState.exchangeCount, 1)
    assert.equal(queryState.exchangeBodySecretCount, 1)
    assert.deepEqual(
      queryState.requestLog.filter((entry) => entry.secretInUrl || entry.secretInHeaders),
      [],
    )
    assert.equal(
      queryState.requestLog.filter((entry) => entry.secretInBody).every((entry) => entry.path === '/api/auth/registration-context/exchange'),
      true,
    )
    const exchangeResponse = waitForApiResponse(queryRuntime.page, '/api/auth/registration-context/exchange')
    queryState.releaseExchange()
    await settleApiResponse(exchangeResponse, 200)
    await queryRuntime.page.locator('textarea[autocomplete="street-address"]').waitFor()
    const scanAfterExchangeCompletion = await secretScan(queryRuntime.page, [querySecret])
    queryResult = {
      urlState,
      exchangeCount: queryState.exchangeCount,
      exchangeBodySecretCount: queryState.exchangeBodySecretCount,
      leakScans: [scanBeforeExchangeCompletion, scanAfterExchangeCompletion],
    }
  } finally {
    queryState.releaseExchange?.()
    await closeHarnessRuntime(queryRuntime)
  }

  const result = { fragments, query: queryResult }
  record('registration-browser-url-fragment-query-scrub', result)
  return result
}

async function runInviteAvailabilityFlagsProof(browser, baseUrl) {
  const modes = [
    { label: 'bot-only-no-links', bot: true, web: false, expectedWebLabel: null },
    { label: 'bot-and-web-no-links', bot: true, web: true, expectedWebLabel: 'ثبت‌نام از طریق وب' },
  ]
  const results = []
  for (const mode of modes) {
    const state = newRuntimeState({
      rawInvite: `INV-${mode.label}-secret`,
      lookupBotAvailable: mode.bot,
      lookupWebAvailable: mode.web,
    })
    const runtime = await createPage(browser, baseUrl, {
      state,
      suite: `invite-availability:${mode.label}`,
    })
    try {
      await runtime.page.setViewportSize({ width: 390, height: 844 })
      await gotoReady(runtime.page, '/i/stage3-browser', 'دعوت‌نامه معتبر است')
      await assertPublicShell(runtime.page, mode.label)
      const telegramVisible = await runtime.page.getByRole('button', { name: 'ثبت‌نام با تلگرام' }).isVisible()
      assert.equal(telegramVisible, true)
      const webButtons = runtime.page.locator('.ui-v2-auth-invite-route:not(.ui-v2-auth-invite-route--telegram)')
      assert.equal(await webButtons.count(), mode.web ? 1 : 0)
      if (mode.expectedWebLabel) {
        assert.equal(await runtime.page.getByRole('button', { name: mode.expectedWebLabel }).isVisible(), true)
      }
      const scan = await secretScan(runtime.page, [state.rawInvite])
      results.push({ ...mode, telegramVisible, webButtonCount: await webButtons.count(), scan })
    } finally {
      await closeHarnessRuntime(runtime)
    }
  }
  record('invite-availability-flags-without-link-fields', { modes: results })
  return results
}

async function runInvitationRegistration(browser, baseUrl) {
  const state = newRuntimeState({ failOtpRequestOnce: true })
  const runtime = await createPage(browser, baseUrl, {
    state,
    suite: 'invitation-registration',
  })
  const { page, context } = runtime
  try {
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoReady(page, '/i/stage3-browser', 'دعوت‌نامه معتبر است')
    await assertPublicShell(page, 'invite')
    await page.getByRole('button', { name: 'ادامه ثبت‌نام در وب‌اپ' }).click()
    await page.waitForURL('**/register')
    await page.getByRole('button', { name: 'دریافت کد تأیید' }).waitFor()
    assert.equal(state.exchangeCount, 1)
    assert.equal(state.exchangeBodySecretCount, 1)
    assert.equal(state.requestLog.filter((entry) => entry.secretInUrl || entry.secretInHeaders).length, 0)
    const afterExchangeScan = await secretScan(page, [state.rawInvite])
    const cookiesAfterExchange = await context.cookies()
    const firstCookie = cookiesAfterExchange.find((cookie) => cookie.name === 'web_registration')
    assert.equal(firstCookie?.httpOnly, true)
    assert.equal(firstCookie?.sameSite, 'Strict')
    assert.equal(firstCookie?.path, '/')
    assert.equal(await page.evaluate(() => document.cookie.includes('web_registration')), false)

    await plannedReload(page, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: 'دریافت کد تأیید' }).waitFor()
    assert.equal(state.exchangeCount, 1, 'refresh replayed raw exchange')
    assert.ok(state.contextReadCount >= 1)

    await page.getByRole('button', { name: 'دریافت کد تأیید' }).click()
    await page.locator('.ui-v2-auth-error[role="alert"]').waitFor()
    await page.getByRole('button', { name: 'تلاش مجدد' }).click()
    const otp = page.locator('input[autocomplete="one-time-code"]')
    await otp.waitFor()
    assert.equal(await otp.getAttribute('inputmode'), 'numeric')
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('autocomplete')), 'one-time-code')

    await plannedReload(page, { waitUntil: 'domcontentloaded' })
    await page.locator('input[autocomplete="one-time-code"]').waitFor()
    assert.equal(state.exchangeCount, 1)
    await page.locator('input[autocomplete="one-time-code"]').fill('12345')
    await page.getByRole('button', { name: 'تأیید و ادامه' }).click()
    const address = page.locator('textarea[autocomplete="street-address"]')
    await address.waitFor()
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('autocomplete')), 'street-address')
    const rotatedCookie = (await context.cookies()).find((cookie) => cookie.name === 'web_registration')
    assert.equal(rotatedCookie?.value, 'opaque-stage3-context-rotated')

    await plannedReload(page, { waitUntil: 'domcontentloaded' })
    await page.locator('textarea[autocomplete="street-address"]').waitFor()
    assert.equal(state.exchangeCount, 1)
    await plannedGoBack(page, { waitUntil: 'domcontentloaded' })
    await page.getByText('دعوت‌نامه معتبر است', { exact: false }).waitFor()
    await plannedGoForward(page, { waitUntil: 'domcontentloaded' })
    await page.locator('textarea[autocomplete="street-address"]').waitFor()
    assert.equal(state.exchangeCount, 1)
    const beforeCompletionScan = await secretScan(page, [state.rawInvite])
    await takeScreenshot(page, 'registration-resumed-step-3-mobile-390.png')

    await page.locator('textarea[autocomplete="street-address"]').fill('تهران، خیابان نمونه، پلاک دوازده')
    const completionClear = waitForApiResponse(page, '/api/auth/registration-context/clear')
    const completionVerify = waitForApiResponse(page, '/api/sessions/verify')
    await page.getByRole('button', { name: 'تکمیل ثبت‌نام' }).click()
    await page.waitForURL((url) => url.pathname === '/')
    await page.getByText('ورود به بازار', { exact: false }).first().waitFor()
    await settleApiResponse(completionClear, 204, 'invitation-home-transition-and-clear-asserted')
    await settleApiResponse(completionVerify, 200, 'invitation-home-session-ready-asserted')
    assert.equal(state.completionCount, 1)
    assert.equal((await context.cookies()).some((cookie) => cookie.name === 'web_registration'), false)
    const afterCompletionScan = await secretScan(page, [state.rawInvite])

    const registrationMetrics = {
      exchangeCount: state.exchangeCount,
      exchangeBodySecretCount: state.exchangeBodySecretCount,
      contextReadCount: state.contextReadCount,
      otpRequestCount: state.otpRequestCount,
      otpVerifyCount: state.otpVerifyCount,
      completionCount: state.completionCount,
      cookie: {
        name: firstCookie?.name,
        httpOnly: firstCookie?.httpOnly,
        sameSite: firstCookie?.sameSite,
        path: firstCookie?.path,
        rawSecretInCookie: firstCookie?.value.includes(state.rawInvite) || false,
      },
      leakScans: [afterExchangeScan, beforeCompletionScan, afterCompletionScan],
    }
    record('secure-invitation-registration-refresh-back-forward', registrationMetrics)
    record('registration-secret-browser-leak-scan-zero')
    return registrationMetrics
  } finally {
    await closeHarnessRuntime(runtime)
  }
}

async function runCompletionRecoveryEdges(browser, baseUrl) {
  const retainedState = newRuntimeState({
    registrationKind: 'registration',
    registrationProgress: 'otp_verified',
    clearFailuresRemaining: 1,
    user: { ...CURRENT_USER, can_connect_telegram: true, telegram_linked: false },
  })
  const retainedRuntime = await createPage(browser, baseUrl, {
    state: retainedState,
    preserveAuthOnReload: true,
    suite: 'completion-recovery:retained',
  })
  const retained = retainedRuntime.page
  try {
    await retainedRuntime.context.addCookies([{
      name: 'web_registration',
      value: retainedState.registrationHandle,
      url: baseUrl,
      httpOnly: true,
      sameSite: 'Strict',
    }])
    await retained.setViewportSize({ width: 390, height: 844 })
    await gotoReady(retained, '/register', 'تکمیل ثبت‌نام')
    const address = retained.locator('textarea[autocomplete="street-address"]')
    await address.waitFor()
    await address.fill('تهران، خیابان بازیابی، پلاک بیست')
    const failedClear = waitForApiResponse(retained, '/api/auth/registration-context/clear')
    await retained.getByRole('button', { name: 'تکمیل ثبت‌نام' }).click()
    const completionStatus = retained.locator('[role="status"][aria-labelledby="registration-complete-title"]')
    await completionStatus.waitFor({ state: 'visible' })
    await settleApiResponse(failedClear, 503)
    assert.equal(new URL(retained.url()).pathname, '/register')
    assert.equal(await completionStatus.evaluate((element) => document.activeElement === element), true)
    assert.equal(retainedState.clearCount, 1)
    assert.equal(retainedState.registrationCompleted, true)
    const retainedCookie = (await retainedRuntime.context.cookies()).find((cookie) => cookie.name === 'web_registration')
    assert.equal(retainedCookie?.httpOnly, true)
    assert.equal(retainedCookie?.sameSite, 'Strict')
    assert.equal(await retained.evaluate(() => Boolean(localStorage.getItem('auth_token') && localStorage.getItem('refresh_token'))), true)
    await takeScreenshot(retained, 'registration-step-4-retained-marker-mobile-390.png')

    const recoveryLogStart = retainedState.requestLog.length
    const recoveredClear = waitForApiResponse(retained, '/api/auth/registration-context/clear')
    const recoveredVerify = waitForApiResponse(retained, '/api/sessions/verify')
    await plannedReload(retained, { waitUntil: 'domcontentloaded' })
    await retained.waitForURL((url) => url.pathname === '/')
    await retained.getByText('ورود به بازار', { exact: false }).first().waitFor()
    await settleApiResponse(recoveredClear, 204, 'retained-refresh-home-recovery-and-clear-asserted')
    await settleApiResponse(recoveredVerify, 200, 'retained-refresh-home-session-ready-asserted')
    const recoveryPaths = retainedState.requestLog.slice(recoveryLogStart).map((entry) => entry.path)
    const contextIndex = recoveryPaths.indexOf('/api/auth/registration-context')
    const meIndex = recoveryPaths.indexOf('/api/auth/me')
    const clearIndex = recoveryPaths.indexOf('/api/auth/registration-context/clear')
    assert.ok(contextIndex >= 0 && meIndex > contextIndex && clearIndex > meIndex, `completion recovery order: ${recoveryPaths.join(',')}`)
    assert.equal(retainedState.clearCount, 2)
    assert.equal(retainedState.registrationCompleted, false)
    assert.equal((await retainedRuntime.context.cookies()).some((cookie) => cookie.name === 'web_registration'), false)
    assert.equal(await retained.evaluate(() => Boolean(localStorage.getItem('auth_token') && localStorage.getItem('refresh_token'))), true)

    const retainedResult = {
      step4Focused: true,
      firstClearFailedMarkerRetained: true,
      refreshRecoveryPaths: recoveryPaths,
      clearCount: retainedState.clearCount,
      cookieClearedAfterRecovery: true,
    }
    record('registration-step4-refresh-retained-marker-clear-retry', retainedResult)

    const goneState = newRuntimeState({ contextMode: 'gone' })
    const goneRuntime = await createPage(browser, baseUrl, {
      authenticated: true,
      state: goneState,
      suite: 'completion-recovery:authenticated-gone',
    })
    try {
      await goneRuntime.page.setViewportSize({ width: 390, height: 844 })
      const goneVerify = waitForApiResponse(goneRuntime.page, '/api/sessions/verify')
      await plannedGoto(goneRuntime.page, '/register', { waitUntil: 'domcontentloaded' })
      await goneRuntime.page.waitForURL((url) => url.pathname === '/')
      await goneRuntime.page.getByText('ورود به بازار', { exact: false }).first().waitFor()
      await settleApiResponse(goneVerify, 200, 'authenticated-context-gone-home-session-ready-asserted')
      const gonePaths = goneState.requestLog.map((entry) => entry.path)
      assert.ok(gonePaths.indexOf('/api/auth/me') > gonePaths.indexOf('/api/auth/registration-context'))
      assert.equal(goneState.clearCount, 0)
      record('registration-authenticated-context-410-home', { paths: gonePaths })
    } finally {
      await closeHarnessRuntime(goneRuntime)
    }

    const guestState = newRuntimeState({ registrationCompleted: true })
    const guestRuntime = await createPage(browser, baseUrl, {
      state: guestState,
      suite: 'completion-recovery:guest-complete',
    })
    try {
      await guestRuntime.context.addCookies([{
        name: 'web_registration',
        value: guestState.registrationHandle,
        url: baseUrl,
        httpOnly: true,
        sameSite: 'Strict',
      }])
      await guestRuntime.page.setViewportSize({ width: 390, height: 844 })
      const guestClear = waitForApiResponse(guestRuntime.page, '/api/auth/registration-context/clear')
      await plannedGoto(guestRuntime.page, '/register', { waitUntil: 'domcontentloaded' })
      await guestRuntime.page.waitForURL((url) => url.pathname === '/login' && url.searchParams.get('registration') === 'complete')
      await guestRuntime.page.getByText('ورود به سامانه', { exact: false }).first().waitFor()
      await settleApiResponse(guestClear, 204, 'guest-completion-login-transition-and-clear-asserted')
      assert.equal(guestState.clearCount, 1)
      assert.equal((await guestRuntime.context.cookies()).some((cookie) => cookie.name === 'web_registration'), false)
      record('registration-no-auth-completion-marker-login-clear', { clearCount: guestState.clearCount })
    } finally {
      await closeHarnessRuntime(guestRuntime)
    }

    return retainedResult
  } finally {
    await closeHarnessRuntime(retainedRuntime)
  }
}

async function runLoginRegistrationRequired(browser, baseUrl) {
  const state = newRuntimeState({ loginMode: 'registration-required', registrationKind: 'registration', registrationProgress: 'otp_verified' })
  const runtime = await createPage(browser, baseUrl, {
    state,
    suite: 'login-registration-required',
  })
  const { page, context } = runtime
  try {
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoReady(page, '/login', 'ورود به سامانه')
    const mobile = page.locator('input[autocomplete="tel"]')
    assert.equal(await mobile.getAttribute('inputmode'), 'tel')
    await mobile.fill('09123456789')
    await page.getByRole('button', { name: 'دریافت کد تأیید' }).click()
    const otp = page.locator('input[autocomplete="one-time-code"]')
    await otp.waitFor()
    const registerNavigation = page.waitForURL('**/register')
    await otp.fill('12345')
    await registerNavigation
    await page.locator('textarea[autocomplete="street-address"]').waitFor()
    assert.equal(state.exchangeCount, 0, 'direct registration unexpectedly used raw exchange')
    assert.ok(state.contextReadCount >= 1)
    await page.locator('.ui-v2-auth-progress').waitFor({ state: 'hidden' })
    assert.equal(await page.locator('.ui-v2-auth-progress').isVisible(), false, 'direct registration rendered visible duplicate progress')
    const cookie = (await context.cookies()).find((entry) => entry.name === 'web_registration')
    assert.equal(cookie?.httpOnly, true)
    assert.equal(cookie?.sameSite, 'Strict')
    await plannedReload(page, { waitUntil: 'domcontentloaded' })
    await page.locator('textarea[autocomplete="street-address"]').waitFor()
    const serialized = await page.evaluate(() => `${location.href}|${JSON.stringify(history.state)}|${document.documentElement.outerHTML}|${JSON.stringify(localStorage)}|${JSON.stringify(sessionStorage)}`)
    assert.equal(/(?:REG-|registration_token=)/u.test(serialized), false)
    const result = { exchangeCount: state.exchangeCount, contextReadCount: state.contextReadCount, cookieHttpOnly: cookie?.httpOnly, cookieSameSite: cookie?.sameSite }
    record('login-registration-required-cookie-direct-resume', result)
    return result
  } finally {
    await closeHarnessRuntime(runtime)
  }
}

const startedAt = new Date().toISOString()
const sourceBefore = sourceSnapshot()
let viteServer
let browser
let exitError = null
let result = null

try {
  viteServer = await createServer({
    root: FRONTEND,
    configFile: path.join(FRONTEND, 'vite.config.ts'),
    cacheDir: path.join(OUTPUT_DIR, 'vite-cache'),
    logLevel: 'error',
    server: {
      host: '127.0.0.1',
      port: 0,
      strictPort: false,
      fs: { allow: [FRONTEND, fs.realpathSync(path.join(FRONTEND, 'node_modules'))] },
    },
  })
  await viteServer.listen()
  const address = viteServer.httpServer?.address()
  assert.ok(address && typeof address !== 'string', 'Vite did not expose an ephemeral TCP port')
  const baseUrl = `http://127.0.0.1:${address.port}`
  browser = await chromium.launch({ headless: true })
  await warmViteDependencies(browser, baseUrl)
  progress('browser-warm-complete', { baseUrl })

  assert.ok(['', 'url-scrub', 'request-lifecycle', 'clear-lifecycle', 'completion-recovery', 'responsive-navigation'].includes(ONLY_SUITE), `unknown STAGE3_BROWSER_ONLY value: ${ONLY_SUITE}`)
  const scrubOnly = ONLY_SUITE === 'url-scrub'
  const requestLifecycleOnly = ONLY_SUITE === 'request-lifecycle'
  const clearLifecycleOnly = ONLY_SUITE === 'clear-lifecycle'
  const completionRecoveryOnly = ONLY_SUITE === 'completion-recovery'
  const responsiveNavigationOnly = ONLY_SUITE === 'responsive-navigation'
  const lifecycleOnly = requestLifecycleOnly || clearLifecycleOnly
  const diagnosticOnly = scrubOnly || lifecycleOnly || completionRecoveryOnly || responsiveNavigationOnly
  const responsive = responsiveNavigationOnly || !diagnosticOnly ? await runResponsiveShellMatrix(browser, baseUrl) : { rows: [], keyboardProof: null }
  if (responsiveNavigationOnly || !diagnosticOnly) progress('suite-complete', { suite: 'responsive-shell-matrix' })
  const pwaLayers = diagnosticOnly ? null : await runPwaLayersAndMotion(browser, baseUrl)
  if (!diagnosticOnly) progress('suite-complete', { suite: 'pwa-layers-motion' })
  const legacy = diagnosticOnly ? null : await runLegacyModalProof(browser, baseUrl)
  if (!diagnosticOnly) progress('suite-complete', { suite: 'legacy-modal' })
  const browserUrlScrub = lifecycleOnly || completionRecoveryOnly || responsiveNavigationOnly ? null : await runBrowserUrlScrubProof(browser, baseUrl)
  if (!lifecycleOnly && !completionRecoveryOnly && !responsiveNavigationOnly) progress('suite-complete', { suite: 'browser-url-scrub' })
  const requestLifecycle = requestLifecycleOnly ? await runRequestLifecycleDiagnostic(browser, baseUrl) : null
  if (requestLifecycleOnly) progress('suite-complete', { suite: 'request-lifecycle-diagnostic' })
  const clearLifecycle = clearLifecycleOnly ? await runClearLifecycleDiagnostic(browser, baseUrl) : null
  if (clearLifecycleOnly) progress('suite-complete', { suite: 'clear-lifecycle-diagnostic' })
  const inviteAvailability = diagnosticOnly ? null : await runInviteAvailabilityFlagsProof(browser, baseUrl)
  if (!diagnosticOnly) progress('suite-complete', { suite: 'invite-availability' })
  const invitationRegistration = diagnosticOnly ? null : await runInvitationRegistration(browser, baseUrl)
  if (!diagnosticOnly) progress('suite-complete', { suite: 'invitation-registration' })
  const completionRecovery = completionRecoveryOnly || !diagnosticOnly ? await runCompletionRecoveryEdges(browser, baseUrl) : null
  if (completionRecoveryOnly || !diagnosticOnly) progress('suite-complete', { suite: 'completion-recovery' })
  const loginRegistration = diagnosticOnly ? null : await runLoginRegistrationRequired(browser, baseUrl)
  if (!diagnosticOnly) progress('suite-complete', { suite: 'login-registration-required' })

  const lifecycleArtifactCounts = classifyRequestLifecycleArtifacts()
  if (requestLifecycleOnly) {
    assert.deepEqual(lifecycleArtifactCounts, {
      'mock-unread-verify-next-planned-navigation': 2,
    })
  } else if (clearLifecycleOnly) {
    assert.deepEqual(lifecycleArtifactCounts, { 'chromium-cdp-204-no-content': 1 })
  } else if (completionRecoveryOnly) {
    assert.deepEqual(lifecycleArtifactCounts, { 'chromium-cdp-204-no-content': 2 })
  } else if (responsiveNavigationOnly) {
    assert.deepEqual(lifecycleArtifactCounts, {
      'mock-unread-verify-next-planned-navigation': 25,
    })
  } else if (scrubOnly) {
    assert.deepEqual(lifecycleArtifactCounts, {})
  } else {
    assert.deepEqual(lifecycleArtifactCounts, {
      'mock-unread-verify-next-planned-navigation': 25,
      'chromium-cdp-204-no-content': 3,
    })
  }

  const expectedNegativeConsolePatterns = responsiveNavigationOnly
    ? []
    : completionRecoveryOnly
    ? [
        { label: 'retryable-http-503-negative', pattern: /Failed to load resource: the server responded with a status of 503 \(Service Unavailable\)/u, count: 1 },
        { label: 'terminal-http-410-negative', pattern: /Failed to load resource: the server responded with a status of 410 \(Gone\)/u, count: 1 },
      ]
    : lifecycleOnly
    ? []
    : scrubOnly
    ? [{ label: 'terminal-http-410-negative', pattern: /Failed to load resource: the server responded with a status of 410 \(Gone\)/u, count: 4 }]
    : [
        { label: 'connection-network-negative', pattern: /Failed to load resource: net::ERR_FAILED/u, count: 1 },
        { label: 'retryable-http-503-negative', pattern: /Failed to load resource: the server responded with a status of 503 \(Service Unavailable\)/u, count: 2 },
        { label: 'terminal-http-410-negative', pattern: /Failed to load resource: the server responded with a status of 410 \(Gone\)/u, count: 5 },
      ]
  for (const message of browserDiagnostics.consoleErrors) {
    const expected = expectedNegativeConsolePatterns.find((entry) => entry.pattern.test(message) && entry.count > 0)
    if (expected) {
      expected.count -= 1
      browserDiagnostics.expectedNegativeConsoleErrors.push({ label: expected.label, message })
    } else {
      browserDiagnostics.unexpectedConsoleErrors.push(message)
    }
  }
  assert.deepEqual(
    expectedNegativeConsolePatterns.map(({ label, count }) => ({ label, remaining: count })),
    expectedNegativeConsolePatterns.map(({ label }) => ({ label, remaining: 0 })),
    'expected negative-path console signals were not observed exactly',
  )
  assert.deepEqual(browserDiagnostics.pageErrors, [], 'page errors detected')
  assert.deepEqual(browserDiagnostics.unexpectedConsoleErrors, [], 'unexpected console errors detected')
  assert.deepEqual(browserDiagnostics.unexpectedRequestFailures, [], 'unexpected failed requests detected')
  record('browser-unexpected-diagnostics-zero', { expectedNegativeConsoleSignals: browserDiagnostics.expectedNegativeConsoleErrors.length })

  const sourceAfter = sourceSnapshot()
  assert.deepEqual(sourceAfter, sourceBefore, 'bound source hash/mtime changed during browser run')
  record('source-hash-mtime-pre-post-identical', { files: sourceBefore.length })

  result = {
    schemaVersion: 1,
    stage: '3',
    status: diagnosticOnly ? 'passed-diagnostic' : 'passed',
    mode: scrubOnly
      ? 'isolated-url-scrub-diagnostic'
      : requestLifecycleOnly
        ? 'isolated-request-lifecycle-diagnostic'
        : clearLifecycleOnly
          ? 'isolated-clear-lifecycle-diagnostic'
          : completionRecoveryOnly
            ? 'isolated-completion-recovery-diagnostic'
            : responsiveNavigationOnly
              ? 'isolated-responsive-navigation-diagnostic'
          : 'full-source-bound',
    runId: RUN_ID,
    startedAt,
    completedAt: new Date().toISOString(),
    browser: { name: 'chromium', version: await browser.version(), headless: true },
    viewports: VIEWPORTS,
    sourceBinding: { pre: sourceBefore, post: sourceAfter, identical: true },
    assertionSummary: { total: assertions.length, passed: assertions.length, failed: 0 },
    assertions,
    screenshots,
    diagnostics: browserDiagnostics,
    metrics: {
      responsiveRows: responsive.rows.length,
      keyboardProof: responsive.keyboardProof,
      pwaLayers,
      legacy,
      browserUrlScrub,
      requestLifecycle,
      clearLifecycle,
      lifecycleArtifactCounts,
      inviteAvailability,
      invitationRegistration,
      completionRecovery,
      loginRegistration,
    },
  }
  fs.writeFileSync(METRICS_PATH, `${JSON.stringify(result, null, 2)}\n`)
  const metricsSha256 = sha256File(METRICS_PATH)
  process.stdout.write(`${JSON.stringify({ status: result.status, mode: result.mode, runId: RUN_ID, outputDir: OUTPUT_DIR, metricsPath: METRICS_PATH, metricsSha256, assertions: result.assertionSummary, screenshots }, null, 2)}\n`)
} catch (error) {
  exitError = error
  const failure = {
    schemaVersion: 1,
    stage: '3',
    status: 'failed',
    runId: RUN_ID,
    startedAt,
    completedAt: new Date().toISOString(),
    message: error instanceof Error ? error.message : String(error),
    stack: error instanceof Error ? error.stack : null,
    assertions,
    screenshots,
    diagnostics: browserDiagnostics,
    sourceBinding: { pre: sourceBefore, post: sourceSnapshot() },
  }
  fs.writeFileSync(METRICS_PATH, `${JSON.stringify(failure, null, 2)}\n`)
  process.stderr.write(`${failure.message}\n${failure.stack || ''}\nFailure artifact: ${METRICS_PATH}\n`)
} finally {
  await browser?.close()
  await viteServer?.close()
}

if (exitError) process.exitCode = 1
