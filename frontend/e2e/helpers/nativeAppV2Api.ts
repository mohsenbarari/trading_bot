import { expect, type Page, type Request, type Route } from '@playwright/test'

export type FixtureMode =
  | 'normal'
  | 'empty'
  | 'error'
  | 'long-copy'
  | 'long-persian'
  | 'full'
  | 'unbroken'
  | 'ltr'
  | 'stale'

export type KnownApiResolver = (
  method: string,
  pathname: string,
  mode: FixtureMode,
  viewer: typeof CURRENT_USER,
) => { status: number; body: unknown } | null

export type DiagnosticPolicy = {
  allowConsole?: (text: string) => boolean
  allowPageError?: (text: string) => boolean
  allowRequestFailed?: (text: string) => boolean
}

export type RouteDiagnostics = {
  unknownApis: string[]
  unexpectedMutations: string[]
  externalRequests: string[]
  pageErrors: string[]
  requestFailed: string[]
  consoleErrors: string[]
  environmentalConsole: string[]
  environmentalPageErrors: string[]
  environmentalRequestFailed: string[]
}

export const CURRENT_USER = {
  id: 9001,
  account_name: 'native_app_v2_user',
  full_name: 'کاربر تست UI',
  role: 'مدیر ارشد',
  account_status: 'active',
  is_accountant: false,
  is_customer: false,
  customer_tier: null,
  has_bot_access: true,
  mobile_number: '09120000000',
  address: 'تهران',
}

export const REGULAR_USER = {
  ...CURRENT_USER,
  id: 9002,
  account_name: 'native_app_v2_regular',
  full_name: 'کاربر عادی تست',
  role: 'عادی',
}

export const CUSTOMER_RELATION = {
  id: 13,
  owner_user_id: 9001,
  customer_user_id: 33,
  customer_account_name: 'customer13',
  invitation_account_name: null,
  mobile_number: '09123333333',
  management_name: 'مشتری پذیرش',
  customer_tier: 'tier1',
  commission_rate: null,
  min_trade_quantity: null,
  max_trade_quantity: null,
  max_daily_trades: 2,
  max_daily_commodity_volume: null,
  status: 'active',
  registration_link: null,
  expires_at: null,
  activated_at: '2026-01-04T10:00:00Z',
  deleted_at: null,
  created_at: '2026-01-03T10:00:00Z',
}

export const ACCOUNTANT_RELATION = {
  id: 13,
  owner_user_id: 9001,
  accountant_user_id: 44,
  accountant_account_name: 'accountant13',
  global_account_name: 'accountant13',
  relation_display_name: 'حسابدار پذیرش',
  mobile_number: '09124444444',
  duty_description: 'ثبت اسناد',
  status: 'active',
  created_at: '2026-01-03T10:00:00Z',
}

const FIXED_TIME = '2026-08-14T12:00:00.000Z'
export const STALE_TIME = '2024-01-01T00:00:00.000Z'
const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
export const TERMINAL_LIST_HTTP_STATUS = 422
const FIXTURE_LIST_FAILURE = { detail: 'دریافت فهرست ناموفق بود' }

function failList() {
  // 425 is not a contractual status for these list/auth endpoints.
  // Register treats 425 as retryable; apiFetch infinite-retries 5xx.
  // 422 is a terminal unprocessable status in Register and Invite contracts
  // and is returned by apiFetch without the 5xx reconnect loop.
  return { status: TERMINAL_LIST_HTTP_STATUS, body: FIXTURE_LIST_FAILURE }
}

export type DiagnosticContext = {
  controlledNavigation: boolean
  localInFlight: number
  seenPaths: Set<string>
}

export function createDiagnosticContext(): DiagnosticContext {
  return {
    controlledNavigation: false,
    localInFlight: 0,
    seenPaths: new Set(),
  }
}

export async function waitForLocalIdle(context: DiagnosticContext, timeoutMs = 10_000) {
  const started = Date.now()
  let quietSince = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (context.localInFlight <= 0) {
      if (Date.now() - quietSince >= 250) return
    } else {
      quietSince = Date.now()
    }
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  throw new Error(`local requests still in flight: ${context.localInFlight}`)
}

export async function withControlledNavigation<T>(
  context: DiagnosticContext,
  run: () => Promise<T>,
): Promise<T> {
  context.controlledNavigation = true
  try {
    return await run()
  } finally {
    context.controlledNavigation = false
  }
}

export function isSessionKeepalivePath(pathname: string) {
  return (
    pathname === '/api/auth/me'
    || pathname === '/api/auth/me/'
    || pathname === '/api/sessions/verify'
    || pathname === '/api/auth/refresh'
  )
}

export function pathEquals(pathname: string, key: string) {
  if (pathname === key) return true
  if (key.endsWith('/')) return pathname === key.slice(0, -1)
  return pathname === `${key}/`
}

export function pathIsHeld(pathname: string, key: string) {
  if (pathEquals(pathname, key)) return true
  const prefix = key.endsWith('/') ? key : `${key}/`
  return pathname.startsWith(prefix)
}

const REGISTRATION_CONTEXT = {
  account_name: 'invitee_sample',
  mobile_number: 'synthetic-mobile',
  role: 'عادی',
  expires_at: FIXED_TIME,
  kind: 'invitation',
  progress: 'context_ready',
  requires_otp: true,
}

export function createDiagnostics(): RouteDiagnostics {
  return {
    unknownApis: [],
    unexpectedMutations: [],
    externalRequests: [],
    pageErrors: [],
    requestFailed: [],
    consoleErrors: [],
    environmentalConsole: [],
    environmentalPageErrors: [],
    environmentalRequestFailed: [],
  }
}

export function isAllowedMutation(
  pathname: string,
  method: string,
  extra?: (pathname: string, method: string) => boolean,
) {
  if (pathname === '/api/sessions/verify' && method === 'POST') return true
  if (pathname === '/api/auth/refresh' && method === 'POST') return true
  if (method === 'POST' && pathname.startsWith('/api/auth/registration-context')) return true
  if (method === 'POST' && /^\/api\/chat\/read\/\d+$/u.test(pathname)) return true
  if (method === 'POST' && pathname === '/api/chat/activity') return true
  if (method === 'PATCH' && /^\/api\/notifications\/\d+\/read$/u.test(pathname)) return true
  return extra?.(pathname, method) === true
}

function createDeferred() {
  let resolve = () => {}
  const promise = new Promise<void>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function isLocalHost(hostname: string) {
  return hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '[::1]'
}

function isKnownTelegramBootstrap(url: URL) {
  return url.hostname === 'telegram.org' && url.pathname === '/js/telegram-web-app.js'
}

function isRealtimeSocket(url: URL) {
  return url.pathname === '/api/realtime/ws'
}

export const UNBROKEN_ACCOUNT_NAME = 'unbroken_ltr_accountnamewithoutspaces_9001'
export const LTR_ACCOUNT_NAME = 'ltr_account_9001'
export const LONG_PERSIAN_FULL_NAME = 'کاربر تست با نام بسیار بلند فارسی برای شکست خط عنوان پروفایل'
export const LONG_PERSIAN_ADDRESS = 'تهران خیابان ولیعصر با نام بسیار بلند فارسی برای شکست خط نشانی'

function copyViewer(viewer: typeof CURRENT_USER, mode: FixtureMode) {
  const longPersian = mode === 'long-copy' || mode === 'long-persian'
  const unbroken = mode === 'unbroken' || mode === 'long-copy'
  const ltr = mode === 'ltr'
  return {
    ...viewer,
    full_name: longPersian ? LONG_PERSIAN_FULL_NAME : viewer.full_name,
    account_name: ltr ? LTR_ACCOUNT_NAME : unbroken ? UNBROKEN_ACCOUNT_NAME : viewer.account_name,
    address: longPersian ? LONG_PERSIAN_ADDRESS : viewer.address,
  }
}

export function resolveKnownApi(
  method: string,
  pathname: string,
  mode: FixtureMode = 'normal',
  viewer = CURRENT_USER,
): { status: number; body: unknown } | null {
  const empty = mode === 'empty'
  const error = mode === 'error'
  const longCopy = mode === 'long-copy' || mode === 'long-persian'
  const full = mode === 'full'
  const stale = mode === 'stale'
  const stamp = stale ? STALE_TIME : FIXED_TIME
  const viewerBody = copyViewer(viewer, mode)
  const list = <T>(item: T, extra?: T): T[] => {
    if (empty) return []
    if (full) return extra ? [item, extra] : [item, { ...item }]
    return [item]
  }

  if (MUTATING_METHODS.has(method) && !isAllowedMutation(pathname, method)) {
    return null
  }

  if (pathname === '/api/auth/me' || pathname === '/api/auth/me/') {
    if (error) return { status: 200, body: viewer }
    return {
      status: 200,
      body: viewerBody,
    }
  }
  if (pathname === '/api/sessions/verify') return { status: 200, body: { ok: true } }
  if (pathname === '/api/auth/refresh') return { status: 200, body: { access_token: 'native-v2-refresh', token_type: 'bearer' } }
  if (pathname === '/api/auth/switchable-users') return { status: 200, body: [] }
  if (pathname === '/api/auth/me/offer-overtime') {
    return { status: 200, body: { offer_overtime_minutes: viewer.is_accountant ? 0 : 3 } }
  }
  if (pathname.startsWith('/api/auth/registration-context')) {
    if (pathname.endsWith('/clear')) return { status: 204, body: {} }
    if (pathname.endsWith('/otp/request') || pathname.endsWith('/otp/verify') || pathname.endsWith('/complete')) {
      return { status: 410, body: { detail: 'expired' } }
    }
    if (error && (pathname === '/api/auth/registration-context' || pathname === '/api/auth/registration-context/')) {
      return failList()
    }
    return { status: 200, body: REGISTRATION_CONTEXT }
  }

  if (pathname === '/api/chat/poll') {
    return {
      status: 200,
      body: {
        conversations_with_unread: [],
        muted_conversation_ids: [],
        unread_chats_count: 0,
        total_unread_mentions: 0,
      },
    }
  }
  if (pathname === '/api/chat/conversations' || pathname === '/api/chat/conversations/') {
    if (error) return failList()
    return { status: 200, body: list({
      id: 6001,
      other_user_id: 33,
      other_user_name: longCopy ? 'گفتگوی بسیار بلند فارسی برای شکست خط عنوان' : 'گفتگوی نمونه',
      last_message_content: 'پیام مصنوعی پذیرش',
      last_message_type: 'text',
      last_message_at: stamp,
      unread_count: 0,
      room_kind: 'direct',
      can_send: true,
    }, {
      id: 6002,
      other_user_id: 34,
      other_user_name: 'گفتگوی دوم پذیرش',
      last_message_content: 'پیام مصنوعی پذیرش',
      last_message_type: 'text',
      last_message_at: stamp,
      unread_count: 0,
      room_kind: 'direct',
      can_send: true,
    }) }
  }
  if (pathname === '/api/chat/channels' || pathname === '/api/chat/channels/') {
    if (error) return failList()
    return { status: 200, body: list({
      id: 21,
      title: longCopy ? 'کانال بسیار بلند فارسی برای بررسی شکست خط' : 'کانال پذیرش',
      username: 'native_channel',
      is_owner: true,
      member_count: 0,
    }, {
      id: 22,
      title: 'کانال دوم پذیرش',
      username: 'native_channel_two',
      is_owner: true,
      member_count: 0,
    }) }
  }
  if (/^\/api\/chat\/channels\/\d+$/u.test(pathname)) {
    return { status: 200, body: { id: 21, title: 'کانال پذیرش', username: 'native_channel', is_owner: true, member_count: 0 } }
  }
  if (/^\/api\/chat\/channels\/\d+\/members$/u.test(pathname)) return { status: 200, body: [] }
  if (pathname === '/api/chat/channels/invite-candidates') return { status: 200, body: { items: [], total: 0 } }
  if (/^\/api\/chat\/messages\/\d+$/u.test(pathname)) return { status: 200, body: [] }
  if (method === 'POST' && /^\/api\/chat\/read\/\d+$/u.test(pathname)) return { status: 200, body: { ok: true } }
  if (method === 'POST' && pathname === '/api/chat/activity') return { status: 200, body: { ok: true } }
  if (/^\/api\/chat\/rooms\/\d+\/messages$/u.test(pathname)) return { status: 200, body: [] }
  if (/^\/api\/chat\/(direct|rooms)\/\d+\/pinned-message$/u.test(pathname)) return { status: 200, body: null }
  if (pathname === '/api/chat/search') return { status: 200, body: [] }
  if (/^\/api\/chat\/rooms\/\d+\/messages\/\d+\/seen$/u.test(pathname)) return { status: 200, body: [] }
  if (/^\/api\/chat\/groups\/\d+$/u.test(pathname)) {
    return { status: 200, body: { id: 9, title: 'گروه پذیرش', members: [] } }
  }
  if (pathname === '/api/chat/groups/member-candidates') return { status: 200, body: [] }

  if (pathname === '/api/notifications/unread-count') return { status: 200, body: 0 }
  if (pathname === '/api/notifications/' || pathname === '/api/notifications') {
    if (error) return failList()
    return { status: 200, body: list({
      id: 7001,
      title: longCopy ? 'اعلان با عنوان بسیار بلند فارسی برای شکست خط' : 'اعلان نمونه',
      body: longCopy ? 'اعلان با عنوان بسیار بلند فارسی برای شکست خط' : 'متن مصنوعی اعلان',
      content: longCopy ? 'اعلان با عنوان بسیار بلند فارسی برای شکست خط' : 'متن مصنوعی اعلان',
      is_read: false,
      created_at: stamp,
      kind: 'trade',
      category: 'trade',
    }, {
      id: 7002,
      title: 'اعلان دوم پذیرش',
      body: 'اعلان دوم پذیرش',
      content: 'اعلان دوم پذیرش',
      is_read: false,
      created_at: stamp,
      kind: 'trade',
      category: 'trade',
    }) }
  }
  if (pathname === '/api/notifications/preferences') {
    return { status: 200, body: { market_offer_push_enabled: true } }
  }
  if (pathname === '/api/notifications/push/public-key') {
    return { status: 200, body: { enabled: false, public_key: null, missing: [] } }
  }
  if (/^\/api\/notifications\/\d+\/read$/u.test(pathname) && method === 'PATCH') {
    return { status: 200, body: { ok: true } }
  }

  if (pathname === '/api/sessions/recovery/pending') return { status: 200, body: [] }
  if (pathname === '/api/sessions/login-requests/pending') return { status: 200, body: [] }
  if (pathname === '/api/sessions/active') {
    if (error) return failList()
    return {
      status: 200,
      body: list({
        id: 'native-v2-session',
        device_name: longCopy ? 'دستگاه مرورگر آزمایشی با نام بسیار بلند فارسی' : 'Acceptance Browser',
        platform: 'web',
        is_current: true,
        is_primary: true,
        created_at: stamp,
        last_active_at: stamp,
        last_seen_at: stamp,
      }, {
        id: 'native-v2-session-2',
        device_name: 'نشست دوم پذیرش',
        platform: 'web',
        is_current: false,
        is_primary: false,
        created_at: stamp,
        last_active_at: stamp,
        last_seen_at: stamp,
      }),
    }
  }

  if (pathname === '/api/trades/overtime-requests/pending-owner') return { status: 200, body: [] }
  if (pathname === '/api/trades/overtime-requests/pending-requester') return { status: 200, body: [] }
  if (pathname === '/api/trades/my') return { status: 200, body: [] }
  if (pathname === '/api/trades/my/page') {
    if (error) return failList()
    return { status: 200, body: { items: [], next_cursor: null, has_more: false } }
  }
  if (/^\/api\/trades\/with\/\d+$/u.test(pathname)) return { status: 200, body: [] }

  if (/^\/api\/users-public\/\d+$/u.test(pathname)) {
    if (error) return failList()
    const id = Number(pathname.split('/')[3])
    return {
      status: 200,
      body: {
        id,
        full_name: id === viewer.id ? viewerBody.full_name : `کاربر ${id}`,
        account_name: id === viewer.id ? viewerBody.account_name : `user_${id}`,
        role: id === viewer.id ? viewer.role : 'عادی',
        account_status: 'active',
        mobile_number: id === viewer.id ? viewerBody.mobile_number : '09123333333',
        address: id === viewer.id ? viewerBody.address : 'اصفهان',
        avatar_file_id: null,
        last_seen_at: stamp,
        created_at_jalali: stale ? '۱۴۰۲/۱۰/۱۱' : '۱۴۰۵/۰۵/۲۳',
        trades_count: 0,
        accountant_relations: [],
        customer_relations: [],
      },
    }
  }
  if (/^\/api\/users-public\/\d+\/project-users$/u.test(pathname)) {
    return { status: 200, body: { items: [], total: 0, limit: 25, offset: 0 } }
  }
  if (pathname === '/api/users-public/search') return { status: 200, body: [] }

  if (pathname === '/api/users/' || pathname === '/api/users') {
    if (error) return failList()
    return { status: 200, body: list({
      ...viewerBody,
      customer_management_name: longCopy ? LONG_PERSIAN_FULL_NAME : undefined,
    }, {
      ...REGULAR_USER,
      customer_management_name: 'کاربر دوم پذیرش',
      full_name: 'کاربر دوم پذیرش',
    }) }
  }
  if (/^\/api\/users\/\d+$/u.test(pathname)) {
    if (error) return failList()
    const id = Number(pathname.split('/')[3])
    if (id === viewer.id) {
      return { status: 200, body: viewerBody }
    }
    return { status: 200, body: { ...REGULAR_USER, id } }
  }

  if (pathname === '/api/blocks/status') {
    return {
      status: 200,
      body: {
        can_block: false,
        can_block_now: false,
        max_blocked: 0,
        current_blocked: 0,
        remaining: 0,
        reason_code: null,
        reason_message: null,
      },
    }
  }
  if (/^\/api\/blocks\/check\/\d+$/u.test(pathname)) {
    return { status: 200, body: { is_blocked_by_me: false } }
  }

  if (pathname === '/api/config') {
    return { status: 200, body: { bot_username: 'synthetic_bot', telegram_bot_username: 'synthetic_bot' } }
  }
  if (pathname === '/api/web-push/status') {
    return { status: 200, body: { enabled: false, supported: false } }
  }
  if (pathname === '/api/web-push/vapid-public-key') {
    return { status: 200, body: { enabled: false, public_key: null } }
  }

  if (pathname === '/api/offers/page') {
    return { status: 200, body: { items: [], next_cursor: null, has_more: false, page_size: 0 } }
  }
  if (pathname === '/api/offers/my' || pathname === '/api/offers/my/repeatable') return { status: 200, body: [] }
  if (pathname === '/api/offers/' || pathname === '/api/offers') return { status: 200, body: [] }
  if (pathname === '/api/offers/market-history') return { status: 200, body: [] }

  if (pathname === '/api/commodities/' || pathname === '/api/commodities') {
    if (error) return failList()
    return { status: 200, body: list(
      { id: 1, name: longCopy ? 'طلای آب‌شده با نام بسیار بلند فارسی' : 'طلای آب‌شده', aliases: [] },
      { id: 2, name: 'کالای دوم پذیرش', aliases: [] },
    ) }
  }

  if (pathname === '/api/trading-settings/' || pathname === '/api/trading-settings') {
    if (error) return failList()
    return {
      status: 200,
      body: {
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
      },
    }
  }
  if (pathname === '/api/trading-settings/market-state') {
    return {
      status: 200,
      body: {
        is_open: true,
        active_web_notice_visible: false,
        offers_since_last_open: 0,
        last_transition_at: null,
        next_transition_at: null,
      },
    }
  }
  if (pathname === '/api/trading-settings/market-overrides') return { status: 200, body: [] }

  if (pathname === '/api/admin-messages/market/current') return { status: 200, body: null }
  if (pathname === '/api/admin-messages/market/history') return { status: 200, body: [] }
  if (pathname === '/api/admin-messages/broadcasts/history') {
    if (error) return failList()
    return {
      status: 200,
      body: list({
        id: 2,
        content: longCopy ? 'پیام همگانی با متن بسیار بلند فارسی برای شکست خط' : 'پیام همگانی پذیرش',
        target_groups: ['users'],
        recipient_count: 4,
        published_at: stamp,
        created_at: stamp,
        created_by_id: 9001,
        created_by_name: viewerBody.full_name,
      }, {
        id: 3,
        content: 'پیام دوم همگانی',
        target_groups: ['users'],
        recipient_count: 2,
        published_at: stamp,
        created_at: stamp,
        created_by_id: 9001,
        created_by_name: viewer.full_name,
      }),
    }
  }

  if (pathname === '/api/invitations/pending' || pathname === '/api/invitations/' || pathname === '/api/invitations') {
    return { status: 200, body: list({
      id: 8001,
      account_name: 'invitee_sample',
      mobile_number: 'synthetic-mobile',
      role: 'عادی',
      web_link: '/i/uiux-baseline',
      web_short_link: '/i/uiux-baseline',
      bot_available: false,
      web_available: true,
      state: 'pending',
      expires_at: stamp,
      created_at: stamp,
    }) }
  }
  if (/^\/api\/invitations\/lookup\/[^/]+$/u.test(pathname)) {
    if (error) return failList()
    return {
      status: 200,
      body: {
        token: 'synthInv',
        valid: true,
        state: 'pending',
        bot_available: false,
        web_available: true,
        web_short_link: '/i/uiux-baseline',
        expires_at: stamp,
      },
    }
  }

  if (pathname === '/api/customers/owner-relations') {
    if (error) return failList()
    return { status: 200, body: list({
      ...CUSTOMER_RELATION,
      management_name: longCopy
        ? 'مشتری پذیرش با نام بسیار بلند فارسی برای شکست خط ردیف'
        : CUSTOMER_RELATION.management_name,
    }, {
      ...CUSTOMER_RELATION,
      id: 14,
      customer_user_id: 35,
      customer_account_name: 'customer14',
      management_name: 'مشتری دوم پذیرش',
      mobile_number: '09123555555',
    }) }
  }
  if (/^\/api\/customers\/owner-relations\/\d+$/u.test(pathname)) {
    if (error) return failList()
    return {
      status: 200,
      body: {
        ...CUSTOMER_RELATION,
        management_name: longCopy
          ? 'مشتری پذیرش با نام بسیار بلند فارسی برای شکست خط ردیف'
          : CUSTOMER_RELATION.management_name,
      },
    }
  }
  if (/^\/api\/customers\/owner-relations\/\d+\/sessions$/u.test(pathname)) return { status: 200, body: [] }
  if (/^\/api\/customers\/owner-relations\/\d+\/trade-stats$/u.test(pathname)) {
    return {
      status: 200,
      body: {
        relation_id: 13,
        customer_user_id: 33,
        period_days: 7,
        from_date: stamp,
        to_date: stamp,
        trade_count: 0,
        total_quantity: 0,
        commission_profit_toman: 0,
        commodities: [],
      },
    }
  }

  if (pathname === '/api/accountants/owner-relations') {
    if (error) return failList()
    return { status: 200, body: list({
      ...ACCOUNTANT_RELATION,
      relation_display_name: longCopy
        ? 'حسابدار پذیرش با نام بسیار بلند فارسی برای شکست خط ردیف'
        : ACCOUNTANT_RELATION.relation_display_name,
    }, {
      ...ACCOUNTANT_RELATION,
      id: 14,
      accountant_user_id: 45,
      accountant_account_name: 'accountant14',
      global_account_name: 'accountant14',
      relation_display_name: 'حسابدار دوم پذیرش',
      mobile_number: '09124555555',
    }) }
  }
  if (/^\/api\/accountants\/owner-relations\/\d+$/u.test(pathname)) {
    if (error) return failList()
    return {
      status: 200,
      body: {
        ...ACCOUNTANT_RELATION,
        relation_display_name: longCopy
          ? 'حسابدار پذیرش با نام بسیار بلند فارسی برای شکست خط ردیف'
          : ACCOUNTANT_RELATION.relation_display_name,
      },
    }
  }
  if (/^\/api\/accountants\/owner-relations\/\d+\/sessions$/u.test(pathname)) return { status: 200, body: [] }

  return null
}

function isAbortFailure(failure: string) {
  return /ERR_ABORTED|NS_BINDING_ABORTED|Load request cancelled/i.test(failure)
}

function isEnvironmentalConsole(text: string) {
  return /Viewport argument key .* not recognized/i.test(text)
}

export function countEnvironmentalDiagnostics(diagnostics: RouteDiagnostics) {
  return {
    console: diagnostics.environmentalConsole.length,
    pageErrors: diagnostics.environmentalPageErrors.length,
    requestFailed: diagnostics.environmentalRequestFailed.length,
    requestFailedByPath: diagnostics.environmentalRequestFailed.reduce<Record<string, number>>((acc, line) => {
      const path = line.split(' ')[1] || line
      acc[path] = (acc[path] || 0) + 1
      return acc
    }, {}),
  }
}

export async function attachDiagnostics(
  page: Page,
  diagnostics: RouteDiagnostics,
  policy: DiagnosticPolicy = {},
  context: DiagnosticContext = createDiagnosticContext(),
) {
  page.on('pageerror', (error) => {
    const text = error.message
    if (policy.allowPageError?.(text)) return
    diagnostics.pageErrors.push(text)
  })
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    if (isEnvironmentalConsole(text)) {
      diagnostics.environmentalConsole.push(text)
      return
    }
    if (policy.allowConsole?.(text)) return
    diagnostics.consoleErrors.push(text)
  })
  const trackLocal = (request: Request) => {
    const url = new URL(request.url())
    return isLocalHost(url.hostname) && !isRealtimeSocket(url)
  }
  page.on('request', (request: Request) => {
    const url = new URL(request.url())
    if (trackLocal(request)) {
      context.localInFlight += 1
      context.seenPaths.add(`${request.method()} ${url.pathname}`)
    }
    if (isLocalHost(url.hostname)) return
    if (isKnownTelegramBootstrap(url)) return
    diagnostics.externalRequests.push(`${request.method()} ${request.url()}`)
  })
  page.on('requestfinished', (request) => {
    if (trackLocal(request)) context.localInFlight = Math.max(0, context.localInFlight - 1)
  })
  page.on('requestfailed', (request) => {
    if (trackLocal(request)) context.localInFlight = Math.max(0, context.localInFlight - 1)
    const url = new URL(request.url())
    const failure = request.failure()?.errorText || ''
    const line = `${request.method()} ${url.pathname} ${failure}`
    const keepaliveAbort = isAbortFailure(failure)
      && !MUTATING_METHODS.has(request.method())
      && isSessionKeepalivePath(url.pathname)
    const controlledNavigationAbort = request.isNavigationRequest()
      && isAbortFailure(failure)
      && context.controlledNavigation
    if (keepaliveAbort || controlledNavigationAbort) {
      diagnostics.environmentalRequestFailed.push(line)
      return
    }
    if (policy.allowRequestFailed?.(line)) return
    diagnostics.requestFailed.push(line)
  })
}

export type WebSocketMockStatus = {
  ok: boolean
  endpoint: '/api/realtime/ws'
  reason?: string
}

export type FixtureController = {
  mode: FixtureMode
  viewer: typeof CURRENT_USER
  extraKnown?: KnownApiResolver
  extraAllowedMutation?: (pathname: string, method: string) => boolean
  expectedOfflineGetPaths: string[]
  holds: Map<string, ReturnType<typeof createDeferred>>
  failNext: Map<string, number>
  failSticky: Set<string>
  abortNetwork: boolean
  websocketMock: WebSocketMockStatus
  hold(pathname: string): ReturnType<typeof createDeferred>
  release(pathname: string): void
  failOnce(pathname: string): void
  failUntil(pathname: string): void
  clearFail(pathname: string): void
  setNetworkOffline(value: boolean): void
}

export type FailClosedOptions = {
  mode?: FixtureMode
  viewer?: typeof CURRENT_USER
  extraKnown?: KnownApiResolver
  extraAllowedMutation?: (pathname: string, method: string) => boolean
  expectedOfflineGetPaths?: string[]
}

export async function installRealtimeWebSocketMock(page: Page): Promise<WebSocketMockStatus> {
  if (typeof page.routeWebSocket !== 'function') {
    return {
      ok: false,
      endpoint: '/api/realtime/ws',
      reason: 'page.routeWebSocket is not available in this Playwright runtime',
    }
  }
  try {
    await page.routeWebSocket(/\/api\/realtime\/ws(?:\?|$)/, (ws) => {
      ws.onMessage((message) => {
        if (message === 'ping') ws.send('pong')
      })
    })
    return { ok: true, endpoint: '/api/realtime/ws' }
  } catch (error) {
    return {
      ok: false,
      endpoint: '/api/realtime/ws',
      reason: error instanceof Error ? error.message : String(error),
    }
  }
}

export async function installFailClosedApi(
  page: Page,
  diagnostics: RouteDiagnostics,
  options: FailClosedOptions = {},
): Promise<FixtureController> {
  const controller: FixtureController = {
    mode: options.mode ?? 'normal',
    viewer: options.viewer ?? CURRENT_USER,
    extraKnown: options.extraKnown,
    extraAllowedMutation: options.extraAllowedMutation,
    expectedOfflineGetPaths: options.expectedOfflineGetPaths ?? [],
    holds: new Map(),
    failNext: new Map(),
    failSticky: new Set(),
    abortNetwork: false,
    websocketMock: await installRealtimeWebSocketMock(page),
    hold(pathname: string) {
      const existing = this.holds.get(pathname)
      if (existing) return existing
      const deferred = createDeferred()
      this.holds.set(pathname, deferred)
      return deferred
    },
    release(pathname: string) {
      const deferred = this.holds.get(pathname)
      deferred?.resolve()
      this.holds.delete(pathname)
    },
    failOnce(pathname: string) {
      this.failNext.set(pathname, (this.failNext.get(pathname) || 0) + 1)
    },
    failUntil(pathname: string) {
      this.failSticky.add(pathname)
    },
    clearFail(pathname: string) {
      this.failSticky.delete(pathname)
      this.failNext.delete(pathname)
    },
    setNetworkOffline(value: boolean) {
      this.abortNetwork = value
    },
  }

  await page.route('https://telegram.org/js/telegram-web-app.js', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: 'window.Telegram=window.Telegram||{WebApp:{ready(){},expand(){},close(){}}};',
    })
  })

  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (isRealtimeSocket(url)) {
      await route.fallback()
      return
    }

    const pathname = url.pathname
    const method = request.method()
    if (MUTATING_METHODS.has(method) && !isAllowedMutation(pathname, method, controller.extraAllowedMutation)) {
      diagnostics.unexpectedMutations.push(`${method} ${pathname}`)
      await route.fulfill({
        status: 405,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'unexpected mutation blocked' }),
      })
      return
    }
    if (
      controller.abortNetwork
      && !MUTATING_METHODS.has(method)
      && !isSessionKeepalivePath(pathname)
    ) {
      await route.abort('internetdisconnected')
      return
    }
    const hold = [...controller.holds.entries()].find(([key]) => pathIsHeld(pathname, key))
    if (hold) await hold[1].promise

    const stickyFail = [...controller.failSticky].find((key) => pathEquals(pathname, key) || pathIsHeld(pathname, key))
    const failCount = [...controller.failNext.entries()].find(([key]) => pathEquals(pathname, key))
    if (stickyFail || (failCount && failCount[1] > 0)) {
      if (failCount && failCount[1] > 0) {
        controller.failNext.set(failCount[0], failCount[1] - 1)
      }
      await route.fulfill({
        status: TERMINAL_LIST_HTTP_STATUS,
        contentType: 'application/json',
        body: JSON.stringify(FIXTURE_LIST_FAILURE),
      })
      return
    }

    const extra = controller.extraKnown?.(method, pathname, controller.mode, controller.viewer)
    const known = extra ?? resolveKnownApi(method, pathname, controller.mode, controller.viewer)
    if (known) {
      await route.fulfill({
        status: known.status,
        contentType: 'application/json',
        body: JSON.stringify(known.body),
      })
      return
    }

    diagnostics.unknownApis.push(`${method} ${pathname}`)
    await route.fulfill({
      status: 599,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'unknown api' }),
    })
  })

  return controller
}

export function allowIntentionalFixtureConsole(text: string) {
  return /intentional fixture failure|دریافت فهرست ناموفق بود|Failed to load resource: the server responded with a status of 422/i.test(text)
}

export function allowOfflineConsole(text: string) {
  return /ERR_INTERNET_DISCONNECTED|NS_ERROR_NET_|net::ERR_INTERNET_DISCONNECTED|internetdisconnected|Failed to load market runtime state/i.test(text)
}

export function allowHarnessHoldAbort(text: string, holdPath?: string) {
  if (!holdPath) return false
  const match = /^(GET|HEAD) (\S+) (.+)$/.exec(text)
  if (!match) return false
  if (!isAbortFailure(match[3])) return false
  return pathEquals(match[2], holdPath) || pathIsHeld(match[2], holdPath)
}

export function isDisconnectFailure(failure: string) {
  return /ERR_INTERNET_DISCONNECTED|internetdisconnected|NS_ERROR_NET_|NS_ERROR_FAILURE/i.test(failure)
}

export function allowExpectedOfflineRequestFailed(
  text: string,
  allowedGetPaths: readonly string[],
) {
  const match = /^(GET|HEAD) (\S+) (.+)$/.exec(text)
  if (!match) return false
  const pathname = match[2]
  const failure = match[3]
  if (!isDisconnectFailure(failure)) return false
  return allowedGetPaths.some((key) => pathEquals(pathname, key) || pathIsHeld(pathname, key))
}

export function expectCleanDiagnostics(diagnostics: RouteDiagnostics, label: string) {
  expect(diagnostics.unknownApis, `${label}: unknown API`).toEqual([])
  expect(diagnostics.unexpectedMutations, `${label}: unexpected mutation`).toEqual([])
  expect(diagnostics.externalRequests, `${label}: external request`).toEqual([])
  expect(diagnostics.pageErrors, `${label}: pageerror`).toEqual([])
  expect(diagnostics.requestFailed, `${label}: requestfailed`).toEqual([])
  expect(diagnostics.consoleErrors, `${label}: console error`).toEqual([])
}
