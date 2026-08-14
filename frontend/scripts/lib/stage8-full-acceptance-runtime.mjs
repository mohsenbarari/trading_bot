import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { createServer } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import fs from 'node:fs'
import path from 'node:path'

export const STAGE = '8'
export const RUN_AUTHORIZATION = 'STAGE8 FULL ACCEPTANCE — RUN'
export const FIXED_TIME = '2026-08-14T12:00:00.000Z'
export const INVITE_CODE = 'Stg8Inv1'
export const CUSTOMER_RELATION_ID = 9001
export const ACCOUNTANT_RELATION_ID = 9002
export const PUBLIC_USER_ID = 9101
export const ADMIN_USER_ID = 9102
export const VIEWPORTS = Object.freeze([
  { width: 360, height: 740 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 414, height: 896 },
  { width: 430, height: 932 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 900 },
])
export const ACCESS_VIEWPORT = VIEWPORTS[2]
export const ALL_STATES = Object.freeze([
  'loading',
  'empty',
  'normal',
  'dense',
  'error',
  'slow',
  'offline',
  'stale',
])
export const INTERACTIONS = Object.freeze(['touch', 'keyboard', 'zoom-200', 'reduced-motion'])
export const ENVIRONMENTS = Object.freeze([
  'mobile-browser',
  'pwa',
  'telegram-webview-non-messenger',
])
export const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
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

const MESSENGER_FAMILY = new Set(['messenger', 'share-receive', 'admin-channels'])
const ADMIN_DENIED_FOR_MIDDLE = new Set([
  'admin-channels',
  'admin-commodities',
  'admin-messages',
  'admin-system',
])

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
    diffCheck: gitText(repo, ['diff', '--check']).trim(),
  }
}

export function loadMatrix(matrixPath) {
  const matrix = JSON.parse(fs.readFileSync(matrixPath, 'utf8'))
  if (matrix.schemaVersion !== 3) throw new Error('ACCEPTANCE_MATRIX schemaVersion must be 3')
  if (!Array.isArray(matrix.routes) || matrix.routes.length !== 30) {
    throw new Error('ACCEPTANCE_MATRIX must enumerate exactly 30 routes')
  }
  if (!Array.isArray(matrix.accessProfiles) || matrix.accessProfiles.length !== 9) {
    throw new Error('ACCEPTANCE_MATRIX must enumerate exactly 9 access profiles')
  }
  let cells = 0
  for (const route of matrix.routes) {
    for (const profile of matrix.accessProfiles) {
      if (!route.expectedAccess?.[profile.id]) {
        throw new Error(`Missing expectedAccess for ${route.name}/${profile.id}`)
      }
      cells += 1
    }
  }
  if (cells !== 270) throw new Error(`Expected 270 access cells, found ${cells}`)
  return matrix
}

export function visitPathFor(route) {
  if (route.name === 'operations-customers-detail') return `/operations/customers/${CUSTOMER_RELATION_ID}`
  if (route.name === 'operations-accountants-detail') {
    return `/operations/accountants/${ACCOUNTANT_RELATION_ID}`
  }
  if (route.name === 'public-profile') return `/users/${PUBLIC_USER_ID}`
  if (route.name === 'admin-user-profile') return `/admin/users/${ADMIN_USER_ID}`
  if (route.name === 'invite-landing') return `/i/${INVITE_CODE}`
  if (route.name === 'system-recovery') return '/__system/recovery'
  return route.path
}

export function deriveExpectedOutcome(route, profile) {
  const matrixOutcome = route.expectedAccess[profile.id]
  if (route.name === 'settings') {
    const derived = profile.authenticated
      ? { kind: 'render-route', evidenceRefs: ['router-runtime', 'auth-guard-runtime'] }
      : { kind: 'redirect-login', targetName: 'login', evidenceRefs: ['router-runtime', 'auth-guard-runtime'] }
    return {
      ...derived,
      matrixKind: matrixOutcome.kind,
      sourceDrift: matrixOutcome.kind !== derived.kind,
      driftReason:
        matrixOutcome.kind !== derived.kind
          ? 'Current router registers /settings as SettingsView, not a redirect to account-security.'
          : null,
    }
  }
  return { ...matrixOutcome, matrixKind: matrixOutcome.kind, sourceDrift: false, driftReason: null }
}

export function componentCanonical(route, profile) {
  if (profile.id !== 'middle-admin') return null
  if (!ADMIN_DENIED_FOR_MIDDLE.has(route.name)) return null
  return {
    kind: 'redirect-component-canonical',
    targetName: 'admin',
    targetPath: '/admin',
    component: 'AdminView',
  }
}

export function finalRouteExpectation(route, profile) {
  const expected = deriveExpectedOutcome(route, profile)
  const canonical = componentCanonical(route, profile)
  if (expected.kind === 'redirect-login') {
    return { ...expected, finalName: 'login', finalPath: '/login' }
  }
  if (expected.kind === 'redirect-home') {
    return { ...expected, finalName: 'home', finalPath: '/' }
  }
  if (expected.kind === 'redirect-canonical') {
    if (expected.targetName === 'account-notifications') {
      return { ...expected, finalName: 'account-notifications', finalPath: '/account/notifications' }
    }
    if (expected.targetName === 'account-security') {
      return { ...expected, finalName: 'account-security', finalPath: '/account/security' }
    }
    return { ...expected, finalName: expected.targetName, finalPath: null }
  }
  if (expected.kind === 'redirect-forbidden-recovery') {
    return {
      ...expected,
      finalName: 'system-recovery',
      finalPath: '/__system/recovery',
      recoveryOutcome: 'forbidden',
    }
  }
  if (canonical) {
    return {
      ...expected,
      canonical,
      finalName: 'admin',
      finalPath: '/admin',
    }
  }
  return {
    ...expected,
    finalName: route.name,
    finalPath: visitPathFor(route).split('?')[0],
  }
}

export function userPayload(profile) {
  if (!profile.authenticated) {
    throw new Error('Guest must not receive an authenticated payload')
  }
  const id = 1000 + (profile.userIdOffset || 0)
  return {
    id,
    role: profile.role,
    account_status: profile.accountStatus || 'active',
    is_customer: profile.isCustomer === true,
    is_accountant: profile.isAccountant === true,
    customer_tier: profile.isCustomer === true ? 'tier1' : null,
    customer_management_name: profile.isCustomer === true ? 'مشتری نمونه' : null,
    customer_owner_user_id: profile.isCustomer === true ? 800 : null,
    customer_owner_account_name: profile.isCustomer === true ? 'مالک نمونه' : null,
    accountant_owner_user_id: profile.isAccountant === true ? 800 : null,
    accountant_owner_account_name: profile.isAccountant === true ? 'مالک نمونه' : null,
    full_name: `کاربر نمونه ${profile.label}`,
    account_name: `stage8_${profile.id}`,
    telegram_linked: false,
    can_connect_telegram: false,
    telegram_link_denial_reason: null,
    global_lock_grace_expires_at: null,
    global_web_locked_at: null,
    trading_restricted_until: null,
    offer_overtime_minutes: profile.isAccountant === true ? 0 : 3,
  }
}

export function attachProfileRuntime(profiles) {
  return profiles.map((profile, index) => ({
    ...profile,
    userIdOffset: index + 1,
  }))
}

export function makeJwt(userId) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64url')
  const now = Math.floor(Date.now() / 1000)
  return `${encode({ alg: 'none', typ: 'JWT' })}.${encode({
    sub: String(userId || 1001),
    exp: now + 3600,
    session_id: 'synthetic-stage8-session',
  })}.synthetic`
}

function known(body, status = 200) {
  return { known: true, body, status }
}

function customerRelation(id = CUSTOMER_RELATION_ID, denseIndex = 0) {
  return {
    id: id + denseIndex,
    owner_user_id: 1001,
    customer_user_id: 9200 + denseIndex,
    customer_account_name: denseIndex ? `مشتری_${denseIndex}` : 'مشتری_نمونه',
    invitation_account_name: null,
    mobile_number: null,
    management_name: denseIndex ? `مشتری نمونه ${denseIndex}` : 'مشتری نمونه',
    customer_tier: 'tier1',
    commission_rate: null,
    min_trade_quantity: null,
    max_trade_quantity: null,
    max_daily_trades: null,
    max_daily_commodity_volume: null,
    status: 'active',
    expires_at: null,
    activated_at: FIXED_TIME,
    deleted_at: null,
    created_at: FIXED_TIME,
  }
}

function accountantRelation(id = ACCOUNTANT_RELATION_ID, denseIndex = 0) {
  return {
    id: id + denseIndex,
    owner_user_id: 1001,
    accountant_user_id: 9300 + denseIndex,
    accountant_account_name: denseIndex ? `حسابدار_${denseIndex}` : 'حسابدار_نمونه',
    invitation_account_name: null,
    mobile_number: null,
    management_name: denseIndex ? `حسابدار نمونه ${denseIndex}` : 'حسابدار نمونه',
    status: 'active',
    expires_at: null,
    activated_at: FIXED_TIME,
    deleted_at: null,
    created_at: FIXED_TIME,
  }
}

function directoryUser(id, index = 0) {
  return {
    id,
    full_name: index ? `کاربر نمونه ${index}` : 'کاربر نمونه',
    account_name: `user_${id}`,
    role: 'عادی',
    account_status: 'active',
    is_customer: false,
    is_accountant: false,
    telegram_linked: false,
  }
}

function fixtureOffer(index = 0) {
  return {
    id: 501 + index,
    offer_public_id: `stage8-offer-${501 + index}`,
    offer_type: 'sell',
    settlement_type: 'cash',
    commodity_id: 1,
    commodity_name: 'طلای ۱۸ عیار',
    quantity: 10,
    remaining_quantity: 10,
    raw_price: 7450000,
    price: 7450000,
    is_wholesale: false,
    lot_sizes: null,
    original_lot_sizes: null,
    notes: 'داده مصنوعی پذیرش',
    status: 'active',
    created_at: FIXED_TIME,
    expires_at_ts: Math.floor(Date.parse(FIXED_TIME) / 1000) + 3600,
  }
}

function listForMode(mode, factory) {
  if (mode === 'empty') return []
  if (mode === 'dense') return Array.from({ length: 24 }, (_, index) => factory(index))
  return [factory(0)]
}

function isSessionVerify(pathname, method) {
  return pathname === '/api/sessions/verify' && method === 'POST'
}

function isRefresh(pathname, method) {
  return pathname === '/api/auth/refresh' && method === 'POST'
}

export function isAllowedMutation(pathname, method) {
  if (isSessionVerify(pathname, method) || isRefresh(pathname, method)) return true
  if (method === 'POST' && pathname.startsWith('/api/auth/registration-context')) return true
  if (method === 'PATCH' && /^\/api\/notifications\/\d+\/read$/u.test(pathname)) return true
  return false
}

export function isIdentityBootstrapPath(pathname, method) {
  if (isSessionVerify(pathname, method) || isRefresh(pathname, method)) return true
  if (pathname === '/api/auth/me' || pathname === '/api/auth/me/') return true
  if (pathname === '/api/auth/switchable-users') return true
  return false
}

export function isInvitationLookupPath(pathname) {
  return /^\/api\/invitations\/lookup\/[^/]+$/u.test(pathname)
}

export function isErrorInjectablePath(pathname) {
  if (isInvitationLookupPath(pathname)) return false
  return (
    /\/api\/(users|customers|accountants|invitations|notifications|offers|commodities)(\/|$)/.test(pathname) ||
    pathname === '/api/notifications' ||
    pathname === '/api/offers' ||
    pathname === '/api/commodities' ||
    pathname === '/api/invitations'
  )
}

export function apiFixture(pathname, method, profile, mode = 'normal') {
  if (MUTATING_METHODS.has(method) && !isAllowedMutation(pathname, method)) {
    return { known: false, status: 405, body: { detail: 'mutating method blocked' }, mutating: true }
  }
  const identityBootstrap = isIdentityBootstrapPath(pathname, method)
  if (!identityBootstrap && mode === 'offline') {
    return { known: true, status: 503, body: { detail: 'synthetic offline' }, offline: true }
  }
  if (!identityBootstrap && mode === 'error' && isInvitationLookupPath(pathname)) {
    return { known: true, status: 410, body: { detail: 'invitation gone' }, injectedError: true }
  }
  if (!identityBootstrap && mode === 'error' && isErrorInjectablePath(pathname)) {
    return { known: true, status: 500, body: { detail: 'synthetic error fixture' }, injectedError: true }
  }

  if (pathname === '/api/auth/me' || pathname === '/api/auth/me/') {
    if (!profile?.authenticated) return { known: true, status: 401, body: { detail: 'unauthenticated' } }
    return known(userPayload(profile))
  }
  if (pathname === '/api/auth/refresh') {
    return known({
      access_token: makeJwt(profile?.authenticated ? 1000 + (profile.userIdOffset || 1) : 0),
      refresh_token: makeJwt(profile?.authenticated ? 1000 + (profile.userIdOffset || 1) : 0),
    })
  }
  if (pathname === '/api/auth/switchable-users') return known([])
  if (pathname === '/api/auth/me/offer-overtime') {
    return known({ offer_overtime_minutes: profile?.isAccountant === true ? 0 : 3 })
  }
  if (pathname.startsWith('/api/auth/registration-context')) {
    if (pathname.endsWith('/clear')) return known({}, 204)
    if (pathname.endsWith('/exchange') || pathname.endsWith('/otp/request') || pathname.endsWith('/otp/verify') || pathname.endsWith('/complete')) {
      return { known: true, status: 410, body: { detail: 'expired' } }
    }
    return known({
      account_name: 'invitee_sample',
      mobile_number: 'synthetic-mobile',
      role: 'عادی',
      expires_at: FIXED_TIME,
      kind: 'invitation',
      progress: 'context_ready',
      requires_otp: true,
    })
  }
  if (pathname === '/api/blocks/status') {
    return known({
      can_block: false,
      can_block_now: false,
      max_blocked: 0,
      current_blocked: 0,
      remaining: 0,
      reason_code: null,
      reason_message: null,
    })
  }
  if (/^\/api\/blocks\/check\/\d+$/u.test(pathname)) {
    return known({ is_blocked_by_me: false })
  }
  if (pathname.startsWith('/api/users-public/')) {
    const id = Number(pathname.split('/')[3]) || PUBLIC_USER_ID
    if (pathname.includes('/project-users') || pathname.includes('/search')) return known([])
    return known({
      id,
      account_name: `user_${id}`,
      avatar_file_id: null,
      mobile_number: null,
      address: null,
      last_seen_at: FIXED_TIME,
      created_at_jalali: '۱۴۰۵/۰۵/۲۳',
      trades_count: 0,
      accountant_relations: [],
      customer_relations: [],
    })
  }
  if (method === 'PATCH' && /^\/api\/notifications\/\d+\/read$/u.test(pathname)) {
    return known({ ok: true })
  }
  if (pathname === '/api/config') {
    return known({ bot_username: 'synthetic_bot', telegram_bot_username: 'synthetic_bot' })
  }
  if (pathname === '/api/sessions/verify') return known({ ok: true })
  if (pathname === '/api/sessions/recovery/pending' || pathname === '/api/sessions/login-requests/pending') {
    return known([])
  }
  if (pathname === '/api/sessions/active') {
    return known([
      {
        id: 'synthetic-stage8-session',
        is_current: true,
        is_primary: true,
        deviceName: 'مرورگر آزمایشی',
        platform: 'web',
        lastActiveAt: FIXED_TIME,
      },
    ])
  }
  if (pathname === '/api/chat/poll') {
    return known({
      conversations_with_unread: [],
      muted_conversation_ids: [],
      unread_chats_count: 0,
      total_unread_mentions: 0,
    })
  }
  if (pathname === '/api/notifications/unread-count') return known(0)
  if (pathname === '/api/notifications/' || pathname === '/api/notifications') {
    return known(listForMode(mode, (index) => ({
      id: 7000 + index,
      title: `اعلان نمونه ${index + 1}`,
      body: 'متن مصنوعی اعلان',
      is_read: false,
      created_at: FIXED_TIME,
      kind: 'system',
    })))
  }
  if (pathname === '/api/notifications/preferences') return known({ market_offer_push_enabled: true })
  if (pathname === '/api/notifications/push/public-key') {
    return known({ enabled: false, public_key: null, missing: [] })
  }
  if (pathname === '/api/web-push/status') return known({ enabled: false, supported: false })
  if (pathname === '/api/trading-settings/market-overrides') return known([])
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
      is_open: true,
      active_web_notice_visible: false,
      offers_since_last_open: 1,
      last_transition_at: FIXED_TIME,
      next_transition_at: null,
    })
  }
  if (pathname === '/api/offers/page') {
    return known({ items: listForMode(mode, fixtureOffer), next_cursor: null, has_more: false })
  }
  if (pathname === '/api/offers/market-history' || pathname === '/api/offers/my/repeatable') return known([])
  if (pathname === '/api/offers/' || pathname === '/api/offers') return known(listForMode(mode, fixtureOffer))
  if (pathname === '/api/commodities/' || pathname === '/api/commodities') {
    return known(listForMode(mode, (index) => ({ id: 1 + index, name: index ? `کالای ${index}` : 'طلای ۱۸ عیار' })))
  }
  if (pathname === '/api/admin-messages/market/current') return known(null)
  if (pathname === '/api/admin-messages/market/history' || pathname === '/api/admin-messages/broadcasts/history') {
    return known([])
  }
  if (pathname === '/api/invitations/pending' || pathname === '/api/invitations/' || pathname === '/api/invitations') {
    return known([])
  }
  if (isInvitationLookupPath(pathname)) {
    return known({
      token: 'synthInv',
      valid: true,
      state: 'pending',
      bot_available: false,
      web_available: true,
      web_short_link: `/i/${INVITE_CODE}`,
      expires_at: FIXED_TIME,
    })
  }
  if (pathname === '/api/customers/owner-relations') {
    return known(listForMode(mode, (index) => customerRelation(CUSTOMER_RELATION_ID, index)))
  }
  if (pathname.startsWith('/api/customers/owner-relations/')) {
    if (pathname.endsWith('/sessions')) return known([])
    if (pathname.includes('/trade-stats')) {
      return known({
        relation_id: CUSTOMER_RELATION_ID,
        customer_user_id: 9200,
        period_days: 7,
        from_date: FIXED_TIME,
        to_date: FIXED_TIME,
        trade_count: 0,
        total_quantity: 0,
        commission_profit_toman: 0,
        commodities: [],
      })
    }
    return known(customerRelation())
  }
  if (pathname === '/api/accountants/owner-relations') {
    return known(listForMode(mode, (index) => accountantRelation(ACCOUNTANT_RELATION_ID, index)))
  }
  if (pathname.startsWith('/api/accountants/owner-relations/')) {
    if (pathname.endsWith('/sessions')) return known([])
    return known(accountantRelation())
  }
  if (pathname === '/api/users/' || pathname.startsWith('/api/users/?')) {
    return known(listForMode(mode, (index) => directoryUser(ADMIN_USER_ID + index, index)))
  }
  if (pathname === `/api/users/${PUBLIC_USER_ID}` || pathname === `/api/users/${ADMIN_USER_ID}`) {
    const id = pathname.endsWith(String(PUBLIC_USER_ID)) ? PUBLIC_USER_ID : ADMIN_USER_ID
    return known(directoryUser(id))
  }
  if (pathname.startsWith('/api/users/')) return known(directoryUser(Number(pathname.split('/')[3]) || 9100))
  if (pathname.startsWith('/api/trades/overtime-requests/')) return known([])
  if (pathname.startsWith('/api/trades/')) return known([])
  if (pathname.startsWith('/api/chat/')) return known([])
  if (pathname.startsWith('/api/admin-messages/')) return known([])
  if (pathname.startsWith('/api/notifications/')) return known([])
  if (pathname.startsWith('/api/offers/')) return known([])
  if (pathname.startsWith('/api/commodities/')) return known([])
  if (pathname.startsWith('/api/invitations/')) return known([])
  if (pathname.startsWith('/api/sessions/')) return known([])
  if (pathname.startsWith('/api/web-push/')) return known({ enabled: false, supported: false })
  if (pathname.startsWith('/api/config')) return known({ bot_username: 'synthetic_bot' })
  return { known: false, status: 599, body: { detail: 'unexpected synthetic fixture request' } }
}

export function createFixtureServer(dist, controller) {
  const state = {
    apiRequests: 0,
    expectedApiRequests: 0,
    unknownApiRequests: 0,
    unknownApiPaths: [],
    mutatingApiRequests: 0,
    mutatingApiPaths: [],
    staticServerErrors: [],
    injectedErrorResponses: 0,
  }
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url || '/', 'http://127.0.0.1')
      const pathname = decodeURIComponent(url.pathname)
      const method = request.method || 'GET'
      if (pathname.startsWith('/api/')) {
        const delayMs = controller.mode === 'slow' || controller.mode === 'loading' ? controller.delayMs : 0
        if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs))
        const fixture = apiFixture(pathname, method, controller.profile, controller.mode)
        state.apiRequests += 1
        if (fixture.mutating) {
          state.mutatingApiRequests += 1
          state.mutatingApiPaths.push(`${method} ${pathname}`)
        } else if (fixture.known) {
          state.expectedApiRequests += 1
          if (fixture.injectedError) state.injectedErrorResponses += 1
        } else {
          state.unknownApiRequests += 1
          state.unknownApiPaths.push(`${method} ${pathname}`)
        }
        response.writeHead(fixture.status, {
          'content-type': 'application/json; charset=utf-8',
          'cache-control': 'no-store',
        })
        response.end(JSON.stringify(fixture.body))
        return
      }
      if (pathname === '/__synthetic_telegram.js') {
        response.writeHead(200, {
          'content-type': 'application/javascript; charset=utf-8',
          'cache-control': 'no-store',
        })
        response.end(
          'window.Telegram=window.Telegram||{WebApp:{ready(){},expand(){},onEvent(){},offEvent(){}}};',
        )
        return
      }
      const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '')
      const requested = path.resolve(dist, relative)
      const insideDist = requested === dist || requested.startsWith(dist + path.sep)
      let file = insideDist ? requested : path.join(dist, 'index.html')
      try {
        if (!(await stat(file)).isFile()) throw new Error('not-a-file')
      } catch {
        file = path.join(dist, 'index.html')
      }
      let body = await readFile(file)
      if (path.basename(file) === 'index.html') {
        body = Buffer.from(
          body
            .toString('utf8')
            .replaceAll('https://telegram.org/js/telegram-web-app.js', '/__synthetic_telegram.js'),
        )
      }
      response.writeHead(200, {
        'content-type': MIME_TYPES.get(path.extname(file)) || 'application/octet-stream',
        'cache-control': 'no-store',
      })
      response.end(body)
    } catch (error) {
      state.staticServerErrors.push(error instanceof Error ? error.message : String(error))
      response.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' })
      response.end('synthetic static server failure')
    }
  })
  return { server, state }
}

export async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject)
      resolve()
    })
  })
  const address = server.address()
  if (!address || typeof address !== 'object') throw new Error('fixture server did not bind')
  return `http://127.0.0.1:${address.port}`
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
    mutatingRequests: [],
  }
}

export function classifyConsole(item) {
  const text = item.text || ''
  if (text.includes('Browserslist')) return 'fixture-ignored'
  if (text.includes('WebSocket') || text.includes('websocket')) return 'fixture-websocket'
  if (
    text.includes('synthetic error fixture') ||
    text.includes('synthetic offline') ||
    text.includes('[apiFetch] Connection lost') ||
    /Failed to load resource.*\b(500|503|410)\b/.test(text) ||
    /the server responded with a status of (500|503|410)/.test(text) ||
    /Request failed with status code (500|503|410)/.test(text)
  ) {
    return 'fixture-injected-state'
  }
  if (text.includes('410 (Gone)') && text.includes('Failed to load resource')) {
    return 'fixture-expected-410'
  }
  if (
    /Failed to load (commodities|settings|market|current user|history|offers|admin market|notification|commodity)/i.test(
      text,
    ) ||
    text.includes('Fetch offers error') ||
    text.includes('Failed to load market runtime')
  ) {
    return 'fixture-injected-companion'
  }
  if (item.type === 'error' || item.type === 'warning') return 'unexpected'
  return 'info'
}

export function diagnosticCounts(diagnostics, options = {}) {
  const allowInjected = options.allowInjected === true
  const unexpectedConsole = diagnostics.console.filter((item) => {
    const classification = classifyConsole(item)
    if (classification === 'fixture-ignored' || classification === 'fixture-websocket') return false
    if (
      allowInjected &&
      (classification === 'fixture-injected-state' ||
        classification === 'fixture-injected-companion' ||
        classification === 'fixture-expected-410')
    ) {
      return false
    }
    return classification === 'unexpected'
  })
  return {
    unexpectedConsole: unexpectedConsole.length,
    pageErrors: diagnostics.pageErrors.length,
    requestFailures: diagnostics.requestFailures.length,
    externalRequests: diagnostics.externalRequests.length,
    mutatingRequests: diagnostics.mutatingRequests.length,
    classifiedConsole: diagnostics.console.map((item) => ({
      type: item.type,
      classification: classifyConsole(item),
    })),
  }
}

export async function instrumentPage(page, baseUrl, diagnostics, profile, environment) {
  const token = profile.authenticated ? makeJwt(1000 + (profile.userIdOffset || 1)) : null
  const user = profile.authenticated ? userPayload(profile) : null
  await page.addInitScript(
    ({ authToken, userValue, environmentName }) => {
      window.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
      localStorage.clear()
      sessionStorage.clear()
      if (authToken && userValue) {
        localStorage.setItem('auth_token', authToken)
        localStorage.setItem('refresh_token', authToken)
        localStorage.setItem('current_user_summary', JSON.stringify(userValue))
        localStorage.setItem('current_user_role', userValue.role)
        localStorage.setItem('current_user_account_status', userValue.account_status)
        localStorage.setItem('current_user_is_accountant', String(userValue.is_accountant))
        localStorage.setItem('current_user_is_customer', String(userValue.is_customer))
      }
      window.Telegram = { WebApp: { ready() {}, expand() {}, onEvent() {}, offEvent() {} } }
      try {
        navigator.serviceWorker.getRegistration = async () => undefined
        navigator.serviceWorker.getRegistrations = async () => []
      } catch {}
      if (environmentName === 'pwa') {
        const original = window.matchMedia.bind(window)
        window.matchMedia = (query) => {
          if (String(query).includes('display-mode: standalone')) {
            return {
              matches: true,
              media: query,
              onchange: null,
              addListener() {},
              removeListener() {},
              addEventListener() {},
              removeEventListener() {},
              dispatchEvent() {
                return false
              },
            }
          }
          return original(query)
        }
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
    { authToken: token, userValue: user, environmentName: environment },
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
    const url = new URL(request.url())
    const method = request.method()
    if (MUTATING_METHODS.has(method) && !isAllowedMutation(url.pathname, method)) {
      diagnostics.mutatingRequests.push({ method, path: url.pathname })
      return route.abort('blockedbyclient')
    }
    if (url.origin === baseUrl) return route.continue()
    diagnostics.externalRequests.push({ method, origin: url.origin })
    return route.abort('blockedbyclient')
  })
}

export async function newPage(browser, baseUrl, viewport, diagnostics, profile, options = {}) {
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 1,
    locale: 'fa-IR',
    timezoneId: 'Asia/Tehran',
    serviceWorkers: 'block',
    reducedMotion: options.reducedMotion ? 'reduce' : 'no-preference',
    userAgent:
      options.environment === 'telegram-webview-non-messenger'
        ? 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Telegram WebView'
        : undefined,
  })
  const page = await context.newPage()
  await instrumentPage(page, baseUrl, diagnostics, profile, options.environment || 'mobile-browser')
  return { context, page }
}

export async function waitForApp(page, timeout = 20000) {
  await page.locator('#app').waitFor({ state: 'visible', timeout })
  await page.locator('html[data-app-mounted="1"]').waitFor({ state: 'attached', timeout })
  const boot = page.locator('#boot-loader')
  if ((await boot.count()) > 0) {
    await boot.waitFor({ state: 'hidden', timeout })
  }
  await page.evaluate(async () => {
    await document.fonts?.ready
  })
  await page.waitForTimeout(140)
}

export async function readRuntimeRoute(page) {
  return page.evaluate(() => {
    const app = document.querySelector('#app')?.__vue_app__
    const router = app?.config?.globalProperties?.$router
    const current = router?.currentRoute?.value
    return {
      name: typeof current?.name === 'string' ? current.name : null,
      path: current?.path || location.pathname,
      fullPath: current?.fullPath || `${location.pathname}${location.search}`,
      queryOutcome: current?.query?.outcome || null,
    }
  })
}

export async function collectUiProbe(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      if (!(element instanceof HTMLElement)) return false
      const style = getComputedStyle(element)
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false
      const rect = element.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0
    }
    const landmarkPresent = (element) => {
      if (!(element instanceof HTMLElement)) return false
      const style = getComputedStyle(element)
      return style.display !== 'none' && style.visibility !== 'hidden'
    }
    const mains = [...document.querySelectorAll('main')].filter(landmarkPresent)
    const roots = [...document.querySelectorAll('.app-route-v2-scope, .app-route-scroll > *')].filter(visible)
    const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
      .filter(visible)
      .map((element) => Number(element.tagName.slice(1)))
    let headingSkip = false
    for (let index = 1; index < headings.length; index += 1) {
      if (headings[index] - headings[index - 1] > 1) headingSkip = true
    }
    const docOverflow = document.documentElement.scrollWidth - window.innerWidth > 1
    const app = document.querySelector('#app')
    const appOverflow = app instanceof HTMLElement ? app.scrollWidth - window.innerWidth > 1 : false
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
      if (element.id) {
        const explicit = document.querySelector(`label[for="${CSS.escape(element.id)}"]`)
        if (explicit?.textContent?.trim()) return explicit.textContent.trim()
      }
      const wrapping = element.closest('label')
      if (wrapping?.textContent?.trim()) return wrapping.textContent.trim()
      if (element.getAttribute('title')?.trim()) return element.getAttribute('title').trim()
      return (element.textContent || '').trim()
    }
    const interactives = [...document.querySelectorAll('a,button,input,select,textarea,[role="button"],[role="tab"]')]
      .filter(visible)
    const unnamed = interactives.filter((element) => {
      return accessibleName(element).length === 0 && element.getAttribute('aria-hidden') !== 'true'
    })
    const nested = interactives.filter((element) =>
      interactives.some((other) => other !== element && other.contains(element)),
    )
    const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusedRect = focused ? focused.getBoundingClientRect() : null
    const focusInViewport = !focusedRect
      ? true
      : focusedRect.left >= -1 &&
        focusedRect.top >= -1 &&
        focusedRect.right <= window.innerWidth + 1 &&
        focusedRect.bottom <= window.innerHeight + 1
    const routeRoot =
      document.querySelector('.app-route--persian-typography') ||
      document.querySelector('.app-route-v2-scope') ||
      document.querySelector('.app-route-scroll')
    const font = routeRoot ? getComputedStyle(routeRoot).fontFamily : getComputedStyle(document.body).fontFamily
    const dir = document.documentElement.getAttribute('dir') || document.documentElement.dir
    const scroll = document.querySelector('.app-route-scroll')
    if (scroll instanceof HTMLElement) scroll.scrollTop = scroll.scrollHeight
    const nav = document.querySelector('.bottom-nav-wrapper, .ui-v2-bottom-nav, .bottom-nav-bar')
    const ctas = [...document.querySelectorAll('.app-route-scroll button, .app-route-scroll [role="button"], .app-route-scroll a.ui-button, .app-route-scroll .ui-button')]
      .filter(visible)
    const lastCta = ctas.at(-1)
    const lastCtaRect = lastCta instanceof HTMLElement ? lastCta.getBoundingClientRect() : null
    const navRect = nav instanceof HTMLElement && visible(nav) ? nav.getBoundingClientRect() : null
    const ctaAboveNav = !lastCtaRect || !navRect ? true : lastCtaRect.bottom <= navRect.top + 2
    return {
      visibleMainCount: mains.length,
      visibleRootCount: roots.length,
      headingCount: headings.length,
      headingSkip,
      documentOverflow: docOverflow,
      appOverflow,
      unnamedInteractive: unnamed.length,
      nestedInteractive: nested.length,
      focusInViewport,
      dir,
      fontFamily: font,
      vazirmatn: /Vazirmatn/i.test(font),
      ctaAboveNav,
      reducedMotion: window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true,
    }
  })
}

export function stateApplicability(routeName) {
  const na = (state, reason) => ({ applicable: false, state, reason })
  const yes = (state) => ({ applicable: true, state, reason: null })
  if (routeName === 'notifications') {
    return ALL_STATES.map((state) =>
      na(state, 'Router redirect record; states execute on canonical account-notifications.'),
    )
  }
  if (routeName === 'system-recovery') {
    return ALL_STATES.map((state) =>
      state === 'normal' ? yes(state) : na(state, 'Recovery is a terminal status page without list/data modes.'),
    )
  }
  if (['login', 'web-register', 'setup-password'].includes(routeName)) {
    return ALL_STATES.map((state) =>
      ['normal', 'error', 'slow', 'offline'].includes(state)
        ? yes(state)
        : na(state, 'Public/focused auth form has no list empty/dense/stale/loading inventory.'),
    )
  }
  if (routeName === 'invite-landing') {
    return ALL_STATES.map((state) =>
      ['loading', 'normal', 'error', 'slow', 'offline'].includes(state)
        ? yes(state)
        : na(state, 'Invitation lookup has no dense/empty/stale list inventory.'),
    )
  }
  if (['operations', 'admin'].includes(routeName)) {
    return ALL_STATES.map((state) =>
      ['normal', 'loading', 'error', 'slow', 'offline', 'stale'].includes(state)
        ? yes(state)
        : na(state, 'Hub/menu has no list empty/dense inventory.'),
    )
  }
  if (
    [
      'account',
      'account-storage',
      'profile',
      'public-profile',
      'admin-user-profile',
      'operations-customers-detail',
      'operations-accountants-detail',
    ].includes(routeName)
  ) {
    return ALL_STATES.map((state) =>
      ['empty', 'dense'].includes(state)
        ? na(state, 'Detail/hub family has no list empty/dense inventory.')
        : yes(state),
    )
  }
  return ALL_STATES.map(yes)
}

export function environmentApplicability(routeName, environment) {
  if (environment === 'telegram-webview-non-messenger' && MESSENGER_FAMILY.has(routeName)) {
    return {
      applicable: false,
      reason: 'Telegram WebView simulation is defined only for non-messenger routes.',
    }
  }
  return { applicable: true, reason: null }
}

export function interactionApplicability(routeName) {
  if (routeName === 'notifications') {
    return INTERACTIONS.map((interaction) => ({
      interaction,
      applicable: false,
      reason: 'Router redirect record; interactions execute on canonical account-notifications.',
    }))
  }
  return INTERACTIONS.map((interaction) => ({ interaction, applicable: true, reason: null }))
}

export function allowedProfileForRoute(route, profiles) {
  const render = profiles.find((profile) => deriveExpectedOutcome(route, profile).kind === 'render-route')
  if (render) return render
  const redirected = profiles.find((profile) =>
    ['redirect-canonical', 'redirect-home'].includes(deriveExpectedOutcome(route, profile).kind),
  )
  return redirected || profiles[0]
}

export function assertOutcome(actual, expected) {
  const failures = []
  if (expected.finalName && actual.name !== expected.finalName) {
    failures.push(`route name ${actual.name} != ${expected.finalName}`)
  }
  if (expected.finalPath && actual.path !== expected.finalPath) {
    failures.push(`path ${actual.path} != ${expected.finalPath}`)
  }
  if (expected.recoveryOutcome && actual.queryOutcome !== expected.recoveryOutcome) {
    failures.push(`recovery ${actual.queryOutcome} != ${expected.recoveryOutcome}`)
  }
  return failures
}

export function assertCommonUi(probe, expected, route, landedRoute = route) {
  const failures = []
  if (
    expected.kind === 'render-route' ||
    expected.kind === 'redirect-canonical' ||
    expected.kind === 'redirect-home' ||
    expected.kind === 'redirect-login' ||
    expected.kind === 'redirect-forbidden-recovery' ||
    expected.canonical
  ) {
    if (probe.documentOverflow) failures.push('document horizontal overflow')
    if (probe.appOverflow) failures.push('app horizontal overflow')
    if (probe.dir && probe.dir !== 'rtl') failures.push(`dir ${probe.dir}`)
    const landedProtection = landedRoute?.uiContract?.protection || route.uiContract?.protection
    if (landedProtection === 'full') {
      return failures
    }
    if (probe.visibleMainCount !== 1) failures.push(`visible main count ${probe.visibleMainCount}`)
    if (probe.visibleRootCount < 1) failures.push('no visible route root')
    if (probe.headingCount < 1) failures.push('no visible heading')
    if (probe.headingSkip) failures.push('heading hierarchy skip')
    if (probe.unnamedInteractive > 0) failures.push(`unnamed interactive ${probe.unnamedInteractive}`)
    if (probe.nestedInteractive > 0) failures.push(`nested interactive ${probe.nestedInteractive}`)
    if (!probe.focusInViewport) failures.push('focus outside viewport')
    if (landedProtection === 'none' && !probe.vazirmatn) {
      failures.push('NONE route missing Vazirmatn')
    }
    if (!probe.ctaAboveNav) failures.push('final CTA obscured by BottomNav')
  }
  return failures
}

export function redactScenario(scenario) {
  const clone = JSON.parse(JSON.stringify(scenario))
  delete clone.rawConsole
  delete clone.screenshotPath
  if (clone.unknownApiPaths) {
    clone.unknownApiPaths = clone.unknownApiPaths.map((item) => String(item).split('?')[0])
  }
  return clone
}

export function distFingerprint(dist) {
  const files = []
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else files.push(full)
    }
  }
  walk(dist)
  files.sort()
  const hash = createHash('sha256')
  for (const file of files) {
    hash.update(path.relative(dist, file))
    hash.update('\0')
    hash.update(fs.readFileSync(file))
  }
  return { fileCount: files.length, sha256: hash.digest('hex') }
}
