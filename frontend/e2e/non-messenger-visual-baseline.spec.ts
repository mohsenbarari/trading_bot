import { expect, test, type Page, type Route } from '@playwright/test'

const shouldRunBaseline = process.env.UI_UX_BASELINE === '1'
const shouldRunA11ySmoke = process.env.UI_UX_A11Y === '1'
const fixedNow = '2026-07-07T08:30:00.000Z'

type ViewportCase = {
  width: number
  height: number
  label: string
}

type RouteCase = {
  path: string
  label: string
  authenticated: boolean
}

const VIEWPORTS: ViewportCase[] = [
  { width: 390, height: 844, label: 'mobile-390' },
  { width: 1440, height: 900, label: 'desktop-1440' },
]

const ROUTES: RouteCase[] = [
  { path: '/', label: 'dashboard', authenticated: true },
  { path: '/market', label: 'market', authenticated: true },
  { path: '/operations', label: 'operations', authenticated: true },
  { path: '/operations/customers', label: 'customers', authenticated: true },
  { path: '/operations/accountants', label: 'accountants', authenticated: true },
  { path: '/account', label: 'account', authenticated: true },
  { path: '/profile', label: 'profile', authenticated: true },
  { path: '/notifications', label: 'notifications', authenticated: true },
  { path: '/admin/users', label: 'admin-users', authenticated: true },
  { path: '/admin/commodities', label: 'admin-commodities', authenticated: true },
  { path: '/login', label: 'login', authenticated: false },
  { path: '/register', label: 'register', authenticated: false },
  { path: '/i/uiux-baseline', label: 'invite-landing', authenticated: false },
]

const CURRENT_USER = {
  id: 9001,
  account_name: 'uiux_visual_user',
  full_name: 'کاربر تست UI',
  role: 'مدیر ارشد',
  account_status: 'active',
  is_accountant: false,
  customer_tier: null,
  has_bot_access: true,
}

function createJwt(payload: Record<string, unknown>) {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url')
  return `${header}.${body}.uiux`
}

async function installDeterministicRuntime(page: Page) {
  await page.addInitScript((nowIso) => {
    const fixedTime = new Date(nowIso).valueOf()
    const NativeDate = Date

    class FixedDate extends NativeDate {
      constructor(...args: ConstructorParameters<typeof Date>) {
        if (args.length === 0) {
          super(fixedTime)
        } else {
          super(...args)
        }
      }

      static now() {
        return fixedTime
      }
    }

    Object.setPrototypeOf(FixedDate, NativeDate)
    globalThis.Date = FixedDate as DateConstructor

    const style = document.createElement('style')
    style.setAttribute('data-ui-ux-baseline', 'true')
    style.textContent = `
      *, *::before, *::after {
        animation-delay: 0s !important;
        animation-duration: 0s !important;
        animation-iteration-count: 1 !important;
        caret-color: transparent !important;
        scroll-behavior: auto !important;
        transition-delay: 0s !important;
        transition-duration: 0s !important;
      }

      .circle-timer,
      [class*="timer"],
      [data-testid*="timer"],
      [data-testid*="countdown"] {
        visibility: hidden !important;
      }
    `
    document.documentElement.appendChild(style)
  }, fixedNow)
}

async function primeAuthenticatedLayout(page: Page) {
  const token = createJwt({
    sub: String(CURRENT_USER.id),
    exp: Math.floor(new Date(fixedNow).valueOf() / 1000) + 60 * 60,
    session_id: 'uiux-visual-session',
  })

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
    localStorage.removeItem('suspended_refresh_token')
  })
}

async function installApiMocks(page: Page) {
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()

    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })

    if (path === '/api/auth/me') {
      return json(CURRENT_USER)
    }
    if (path === '/api/sessions/verify') {
      return json({ ok: true })
    }
    if (path === '/api/chat/poll') {
      return json({
        conversations_with_unread: [],
        muted_conversation_ids: [],
        unread_chats_count: 0,
        total_unread_mentions: 0,
      })
    }
    if (path === '/api/notifications/' && method === 'GET') {
      return json([])
    }
    if (path === '/api/notifications/mark-all-read') {
      return json({ ok: true })
    }
    if (path === '/api/sessions/active') {
      return json([
        {
          id: 'uiux-session',
          device_name: 'Baseline Browser',
          platform: 'web',
          is_current: true,
          created_at: fixedNow,
          last_seen_at: fixedNow,
        },
      ])
    }
    if (path === '/api/trades/my') {
      return json([])
    }
    if (path === '/api/auth/switchable-users') {
      return json([])
    }
    if (path === '/api/offers/' && method === 'GET') {
      return json([])
    }
    if (path === '/api/offers/my') {
      return json([])
    }
    if (path === '/api/commodities/') {
      return json([{ id: 1, name: 'طلای آب‌شده', aliases: ['آبشده'] }])
    }
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
    if (path === '/api/trading-settings/market-overrides') {
      return json([])
    }
    if (path === '/api/admin-messages/market/current') {
      return json(null)
    }
    if (path === '/api/invitations/pending') {
      return json([])
    }
    if (path === '/api/customers/owner-relations') {
      return json([])
    }
    if (path === '/api/accountants/owner-relations') {
      return json([])
    }
    if (path.startsWith('/api/invitations/') || path.startsWith('/api/register/')) {
      return json({ ok: true })
    }

    return json({})
  })
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

async function expectCriticalA11yBasics(page: Page, label: string) {
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

    const nameFor = (element: HTMLElement) =>
      [
        element.getAttribute('aria-label'),
        element.getAttribute('title'),
        element.textContent,
      ].join(' ').trim()

    return Array.from(document.querySelectorAll<HTMLElement>('button, a[href], input, textarea, select'))
      .filter(isVisible)
      .filter((element) => !nameFor(element) && element.getAttribute('aria-hidden') !== 'true')
      .slice(0, 10)
      .map((element) => ({
        tag: element.tagName,
        className: element.className.toString(),
      }))
  })

  expect(issues, `${label}: visible interactive controls need an accessible name`).toEqual([])
}

test.describe('Non-messenger visual baseline harness', () => {
  test.skip(!shouldRunBaseline, 'Set UI_UX_BASELINE=1 to capture or compare WebApp UI/UX screenshots.')
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  for (const viewport of VIEWPORTS) {
    for (const route of ROUTES) {
      test(`${viewport.label}:${route.label}`, async ({ page }) => {
        await installDeterministicRuntime(page)
        await installApiMocks(page)
        if (route.authenticated) {
          await primeAuthenticatedLayout(page)
        } else {
          await clearAuthenticatedLayout(page)
        }

        await page.setViewportSize({ width: viewport.width, height: viewport.height })
        await gotoRouteWithNavigationRetry(page, route.path)
        await expect(page.locator('#app')).toBeVisible({ timeout: 10_000 })
        await page.evaluate(async () => {
          await document.fonts?.ready
        })

        if (shouldRunA11ySmoke) {
          await expectCriticalA11yBasics(page, `${viewport.label}:${route.label}`)
        }

        await expect(page).toHaveScreenshot(`${route.label}-${viewport.label}.png`, {
          animations: 'disabled',
          fullPage: true,
          maxDiffPixelRatio: 0.02,
        })
      })
    }
  }
})
