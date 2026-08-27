import { expect, test, type Page, type Route } from '@playwright/test'

type ViewportCase = {
  width: number
  height: number
  label: string
}

const VIEWPORTS: ViewportCase[] = [
  { width: 360, height: 740, label: 'mobile-360' },
  { width: 390, height: 844, label: 'mobile-390' },
  { width: 430, height: 932, label: 'mobile-430' },
  { width: 768, height: 1024, label: 'tablet-768' },
  { width: 1440, height: 900, label: 'desktop-1440' },
]

const CONTRACT_VIEWPORTS: ViewportCase[] = [
  { width: 390, height: 844, label: 'mobile-390' },
  { width: 1440, height: 900, label: 'desktop-1440' },
]

const CUSTOMER_RELATION = {
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

const ACCOUNTANT_RELATION = {
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

type RouteCase = {
  path: string
  readyText: string
  readyBy?: 'text' | 'accessible-name'
}

const AUTHENTICATED_ROUTES: RouteCase[] = [
  { path: '/', readyText: 'خانه' },
  { path: '/setup-password', readyText: 'تنظیم رمز عبور' },
  { path: '/operations', readyText: 'عملیات' },
  { path: '/operations/customers', readyText: 'مشتریان' },
  { path: '/operations/customers/13', readyText: 'مشتری پذیرش' },
  { path: '/operations/accountants', readyText: 'حسابداران' },
  { path: '/operations/accountants/13', readyText: 'حسابدار پذیرش' },
  { path: '/account', readyText: 'حساب' },
  { path: '/account/security', readyText: 'امنیت حساب' },
  { path: '/account/storage', readyText: 'حافظه و داده‌ها' },
  { path: '/account/notifications', readyText: 'اعلان‌ها' },
  { path: '/chat', readyText: 'جستجو', readyBy: 'accessible-name' },
  { path: '/users/9001', readyText: 'اطلاعات شخصی' },
  { path: '/profile', readyText: 'اطلاعات شخصی' },
  { path: '/settings', readyText: 'تنظیمات حساب' },
  { path: '/admin', readyText: 'مرکز مدیریت' },
  { path: '/admin/invitations', readyText: 'ارسال دعوت‌نامه' },
  { path: '/admin/channels', readyText: 'ساخت کانال' },
  { path: '/admin/users', readyText: 'مدیریت کاربران' },
  { path: '/admin/users/9001', readyText: 'native_app_v2_user' },
  { path: '/admin/commodities', readyText: 'مدیریت کالاها' },
  { path: '/admin/messages', readyText: 'پیام‌های مدیریت' },
  { path: '/admin/system', readyText: 'تنظیمات سیستم' },
  { path: '/notifications', readyText: 'اعلان‌ها' },
  { path: '/share-receive', readyText: 'اشتراک‌گذاری آماده نشد' },
]

const PUBLIC_ROUTES: RouteCase[] = [
  { path: '/login', readyText: 'ورود به سامانه' },
  { path: '/register', readyText: 'تکمیل ثبت‌نام' },
  { path: '/i/uiux-baseline', readyText: 'دعوت‌نامه اختصاصی' },
  { path: '/this-route-does-not-exist', readyText: 'این صفحه پیدا نشد' },
]

const CURRENT_USER = {
  id: 9001,
  account_name: 'native_app_v2_user',
  full_name: 'کاربر تست UI',
  role: 'مدیر ارشد',
  account_status: 'active',
  is_accountant: false,
  is_customer: false,
  customer_tier: null,
  has_bot_access: true,
}

function createJwt() {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify({
    sub: String(CURRENT_USER.id),
    exp: Math.floor(Date.now() / 1000) + 60 * 60,
    session_id: 'native-app-v2-acceptance',
  })).toString('base64url')
  return `${header}.${body}.native-v2`
}

async function primeAuthenticatedLayout(page: Page) {
  const token = createJwt()
  await page.addInitScript(({ accessToken, userSummary }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', accessToken)
    localStorage.setItem('current_user_summary', JSON.stringify(userSummary))
    localStorage.removeItem('suspended_refresh_token')
  }, {
    accessToken: token,
    userSummary: CURRENT_USER,
  })
}

async function clearAuthenticatedLayout(page: Page) {
  await page.addInitScript(() => {
    localStorage.removeItem('auth_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('current_user_summary')
  })
}

async function installApiMocks(page: Page) {
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const json = (body: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })

    if (path === '/api/auth/me') return json(CURRENT_USER)
    if (path === '/api/sessions/verify') return json({ ok: true })
    if (path === '/api/chat/poll') {
      return json({
        conversations_with_unread: [],
        muted_conversation_ids: [],
        unread_chats_count: 0,
        total_unread_mentions: 0,
      })
    }
    if (path === '/api/notifications/' && method === 'GET') return json([])
    if (path === '/api/sessions/active') {
      return json([{
        id: 'native-v2-session',
        device_name: 'Acceptance Browser',
        platform: 'web',
        is_current: true,
        created_at: new Date().toISOString(),
        last_seen_at: new Date().toISOString(),
      }])
    }
    if (path === '/api/trades/my') return json([])
    if (path === '/api/trades/my/page') return json({ items: [], next_cursor: null, has_more: false })
    if (/^\/api\/users-public\/\d+$/.test(path)) {
      return json({
        id: CURRENT_USER.id,
        account_name: CURRENT_USER.account_name,
        full_name: CURRENT_USER.full_name,
        role: CURRENT_USER.role,
        account_status: CURRENT_USER.account_status,
        mobile_number: '09120000000',
        address: 'تهران',
      })
    }
    if (/^\/api\/users-public\/\d+\/project-users$/.test(path)) {
      return json({ items: [], total: 0, limit: 25, offset: 0 })
    }
    if (path === '/api/auth/switchable-users') return json([])
    if (path === '/api/offers/page' && method === 'GET') {
      return json({ items: [], next_cursor: null, has_more: false, page_size: 0 })
    }
    if (path === '/api/offers/my') return json([])
    if (path === '/api/commodities/') return json([{ id: 1, name: 'طلای آب‌شده' }])
    if (path === '/api/trading-settings/') {
      return json({
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
    if (path === '/api/trading-settings/market-state') {
      return json({
        is_open: true,
        active_web_notice_visible: false,
        offers_since_last_open: 0,
        last_transition_at: null,
        next_transition_at: null,
      })
    }
    if (path === '/api/trading-settings/market-overrides') return json([])
    if (path === '/api/admin-messages/market/current') return json(null)
    if (path === '/api/invitations/pending') return json([])
    if (path === '/api/customers/owner-relations') return json([CUSTOMER_RELATION])
    if (path === '/api/customers/owner-relations/13') return json(CUSTOMER_RELATION)
    if (path === '/api/accountants/owner-relations') return json([ACCOUNTANT_RELATION])
    if (path === '/api/accountants/owner-relations/13') return json(ACCOUNTANT_RELATION)
    if (path === '/api/users/' && method === 'GET') return json([CURRENT_USER])
    if (path === '/api/users/9001' && method === 'GET') return json(CURRENT_USER)
    if (path.startsWith('/api/invitations/') || path.startsWith('/api/register/')) return json({ ok: true })
    if (path.startsWith('/api/chat/')) return json({ conversations: [], items: [] })
    return json({})
  })
}

async function expectRouteReady(page: Page, route: RouteCase) {
  if (route.readyBy === 'accessible-name') {
    await expect(page.getByRole('button', { name: route.readyText }).first()).toBeVisible({ timeout: 10_000 })
    return
  }
  await expect(page.getByText(route.readyText, { exact: false }).first()).toBeVisible({ timeout: 10_000 })
}

async function gotoRouteWithNavigationRetry(page: Page, path: string) {
  let lastError: unknown = null
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await page.goto(path, { waitUntil: 'domcontentloaded' })
      return
    } catch (error) {
      lastError = error
      const message = error instanceof Error ? error.message : String(error)
      if (!/interrupted by another navigation|NS_BINDING_ABORTED|NS_ERROR_FAILURE/i.test(message)) {
        throw error
      }
      await page.waitForTimeout(250)
    }
  }
  throw lastError
}

async function expectNoHorizontalOverflow(page: Page, label: string) {
  const metrics = await page.evaluate(() => {
    const doc = document.documentElement
    const body = document.body
    const app = document.querySelector('#app') as HTMLElement | null
    return {
      viewportWidth: window.innerWidth,
      maxScrollWidth: Math.max(doc.scrollWidth, body.scrollWidth, app?.scrollWidth || 0),
    }
  })
  expect(metrics.maxScrollWidth, `${label}: horizontal overflow`).toBeLessThanOrEqual(metrics.viewportWidth + 1)
}

async function expectSingleMain(page: Page, label: string) {
  const count = await page.locator('main, [role="main"]').count()
  expect(count, `${label}: exactly one main`).toBe(1)
}

async function expectSingleH1(page: Page, label: string) {
  const count = await page.locator('h1').count()
  expect(count, `${label}: exactly one h1`).toBe(1)
}

async function expectNoPageError(page: Page, label: string) {
  const pageError = await page.evaluate(() => {
    const app = document.querySelector('#app')
    return app?.textContent?.includes('Something went wrong') ? 'app crashed' : ''
  })
  expect(pageError, `${label}: page error`).toBe('')
}

async function expectNamedControls(page: Page, label: string) {
  const issues = await page.evaluate(() => {
    const isVisible = (element: HTMLElement) => {
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number(style.opacity || '1') > 0
        && rect.width > 0
        && rect.height > 0
    }
    const nameFor = (element: HTMLElement) => {
      const labelledBy = (element.getAttribute('aria-labelledby') || '')
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => document.getElementById(id)?.textContent || '')
        .join(' ')
      const nativeLabels = 'labels' in element
        ? Array.from((element as HTMLInputElement).labels || []).map((item) => item.textContent || '').join(' ')
        : ''
      return [
        element.getAttribute('aria-label'),
        labelledBy,
        nativeLabels,
        element.getAttribute('title'),
        element.textContent,
      ].join(' ').trim()
    }
    return Array.from(document.querySelectorAll<HTMLElement>('button, a[href], input, textarea, select'))
      .filter(isVisible)
      .filter((element) => !nameFor(element) && element.getAttribute('aria-hidden') !== 'true')
      .slice(0, 8)
      .map((element) => `${element.tagName}.${element.className}`)
  })
  expect(issues, `${label}: unnamed controls`).toEqual([])
}

test.describe('Native App V2 remaining-route acceptance', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  for (const viewport of VIEWPORTS) {
    test(`${viewport.label} keeps remaining authenticated routes inside the viewport`, async ({ page }) => {
      test.setTimeout(90_000)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await primeAuthenticatedLayout(page)
      await installApiMocks(page)
      await page.setViewportSize({ width: viewport.width, height: viewport.height })

      for (const route of AUTHENTICATED_ROUTES.filter((item) => [
        '/settings',
        '/account/storage',
        '/profile',
        '/admin/invitations',
        '/admin/users',
        '/admin/commodities',
        '/admin/messages',
        '/admin/system',
        '/admin/channels',
        '/share-receive',
      ].includes(item.path))) {
        await gotoRouteWithNavigationRetry(page, route.path)
        await expectRouteReady(page, route)
        await expectNoHorizontalOverflow(page, `${viewport.label}:${route.path}`)
        await expectNamedControls(page, `${viewport.label}:${route.path}`)
      }
    })

    test(`${viewport.label} keeps remaining public routes inside the viewport`, async ({ page }) => {
      test.setTimeout(90_000)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await clearAuthenticatedLayout(page)
      await installApiMocks(page)
      await page.setViewportSize({ width: viewport.width, height: viewport.height })

      for (const route of PUBLIC_ROUTES) {
        await gotoRouteWithNavigationRetry(page, route.path)
        await expectRouteReady(page, route)
        await expectNoHorizontalOverflow(page, `${viewport.label}:${route.path}`)
        await expectNamedControls(page, `${viewport.label}:${route.path}`)
      }
    })
  }

  test('login stays keyboard-usable and returns focus after tabbing fields', async ({ page }) => {
    await clearAuthenticatedLayout(page)
    await installApiMocks(page)
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/login')
    await expect(page.getByText('ورود به سامانه').first()).toBeVisible({ timeout: 10_000 })

    const mobile = page.getByLabel('شماره موبایل')
    await mobile.focus()
    await expect(mobile).toBeFocused()
    await page.keyboard.type('09120000000')
    await page.keyboard.press('Tab')
    const submit = page.getByRole('button', { name: 'دریافت کد تأیید' })
    await expect(page.getByRole('button', { name: 'ورود سریع ۱ ساله' })).toHaveCount(0)
    await expect(submit).toBeFocused()
    await expectNamedControls(page, 'login-keyboard')
    await expectNoHorizontalOverflow(page, 'login-keyboard')
  })

  test('account remains usable at 200% page zoom', async ({ page, browserName }) => {
    test.skip(browserName !== 'chromium', 'CDP page-scale verification is Chromium-specific.')
    await primeAuthenticatedLayout(page)
    await installApiMocks(page)
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/account')
    await expect(page.getByText('حساب').first()).toBeVisible({ timeout: 10_000 })

    const session = await page.context().newCDPSession(page)
    await session.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 })
    await page.waitForTimeout(120)

    const zoom = await page.evaluate(() => ({
      scale: window.visualViewport?.scale ?? 1,
      width: window.visualViewport?.width ?? window.innerWidth,
    }))
    expect(zoom.scale).toBeCloseTo(2, 1)
    expect(zoom.width).toBeGreaterThanOrEqual(194)
    await expect(page.getByRole('heading', { name: 'حساب' })).toBeVisible()
    await expectNoHorizontalOverflow(page, 'account-zoom-200')
    await expectNamedControls(page, 'account-zoom-200')
  })
})

test.describe('Native App V2 29-route contract matrix', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  for (const viewport of CONTRACT_VIEWPORTS) {
    test(`${viewport.label} keeps all 29 non-market routes inside the viewport`, async ({ page, browserName }) => {
      test.skip(
        browserName !== 'chromium',
        'BR-MATRIX-001: full 29-route contract is Chromium-representative; Firefox/WebKit cover sensitive families.',
      )
      test.setTimeout(180_000)
      const pageErrors: string[] = []
      const unknownHosts: string[] = []
      page.on('pageerror', (error) => pageErrors.push(error.message))
      page.on('request', (request) => {
        const url = new URL(request.url())
        const host = url.hostname
        if (!host || host === '127.0.0.1' || host === 'localhost') return
        if (host === 'telegram.org' && url.pathname === '/js/telegram-web-app.js') {
          test.info().annotations.push({
            type: 'note',
            description: 'EXT-001 Mini App bootstrap script telegram.org/js/telegram-web-app.js is a pre-existing host dependency, not a candidate fetch.',
          })
          return
        }
        unknownHosts.push(request.url())
      })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await primeAuthenticatedLayout(page)
      await installApiMocks(page)
      await page.setViewportSize({ width: viewport.width, height: viewport.height })

      for (const route of AUTHENTICATED_ROUTES) {
        const label = `${viewport.label}:${route.path}`
        await gotoRouteWithNavigationRetry(page, route.path)
        await expectRouteReady(page, route)
        await expectSingleMain(page, label)
        await expectSingleH1(page, label)
        await expectNoHorizontalOverflow(page, label)
        await expectNamedControls(page, label)
        await expectNoPageError(page, label)
      }

      await clearAuthenticatedLayout(page)
      await page.reload({ waitUntil: 'domcontentloaded' })
      for (const route of PUBLIC_ROUTES) {
        const label = `${viewport.label}:${route.path}`
        await gotoRouteWithNavigationRetry(page, route.path)
        await expectRouteReady(page, route)
        await expectSingleMain(page, label)
        await expectSingleH1(page, label)
        await expectNoHorizontalOverflow(page, label)
        await expectNamedControls(page, label)
        await expectNoPageError(page, label)
      }

      expect(pageErrors, `${viewport.label}: page errors`).toEqual([])
      expect(unknownHosts, `${viewport.label}: external requests`).toEqual([])
    })
  }

  test('sensitive families stay usable for keyboard, modal, empty, and long Persian copy', async ({ page }) => {
    test.setTimeout(90_000)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await primeAuthenticatedLayout(page)
    await installApiMocks(page)
    await page.setViewportSize({ width: 390, height: 844 })

    await gotoRouteWithNavigationRetry(page, '/')
    await expect(page.getByRole('heading', { name: 'خانه' })).toBeVisible()
    await page.getByLabel(/باز کردن منوی حساب/).click()
    await expect(page.getByRole('dialog', { name: 'حساب' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'حساب' })).toHaveCount(0)

    await gotoRouteWithNavigationRetry(page, '/operations')
    await expect(page.getByText('مشتریان')).toBeVisible()
    await gotoRouteWithNavigationRetry(page, '/operations/customers')
    await expect(page.getByText('مشتری پذیرش')).toBeVisible()
    await gotoRouteWithNavigationRetry(page, '/operations/accountants')
    await expect(page.getByText('حسابدار پذیرش')).toBeVisible()

    await gotoRouteWithNavigationRetry(page, '/profile')
    await expect(page.getByText('09120000000')).toBeVisible()
    await expect(page.getByText('تهران')).toBeVisible()

    await gotoRouteWithNavigationRetry(page, '/account/notifications')
    await expect(page.getByText('هیچ اعلانی یافت نشد')).toBeVisible()

    await gotoRouteWithNavigationRetry(page, '/chat')
    await expectNoHorizontalOverflow(page, 'chat-empty')
    await expectNamedControls(page, 'chat-empty')
  })

  test('login hides developer shortcut and keeps mobile keyboard fields named', async ({ page }) => {
    await clearAuthenticatedLayout(page)
    await installApiMocks(page)
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/login')
    await expect(page.getByLabel('شماره موبایل')).toHaveAttribute('autocomplete', /tel|username/)
    await expect(page.getByRole('button', { name: 'ورود سریع ۱ ساله' })).toHaveCount(0)
    await expectNoHorizontalOverflow(page, 'login-no-dev-shortcut')
  })
})
