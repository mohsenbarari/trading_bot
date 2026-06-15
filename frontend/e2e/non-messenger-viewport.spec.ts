import { expect, test, type Page, type Route } from '@playwright/test'

type ViewportCase = {
  width: number
  height: number
  label: string
}

const VIEWPORTS: ViewportCase[] = [
  { width: 360, height: 740, label: 'mobile-360' },
  { width: 375, height: 812, label: 'mobile-375' },
  { width: 390, height: 844, label: 'mobile-390' },
  { width: 414, height: 896, label: 'mobile-414' },
  { width: 430, height: 932, label: 'mobile-430' },
  { width: 768, height: 1024, label: 'tablet-768' },
  { width: 1024, height: 768, label: 'tablet-landscape-1024' },
  { width: 1440, height: 900, label: 'desktop-1440' },
]

const ROUTES = [
  { path: '/', label: 'dashboard', expectedText: 'ورود به بازار' },
  { path: '/operations', label: 'operations', expectedText: 'عملیات' },
  { path: '/operations/customers', label: 'customers', expectedText: 'مشتریان' },
  { path: '/operations/accountants', label: 'accountants', expectedText: 'حسابداران' },
  { path: '/account', label: 'account', expectedText: 'حساب' },
  { path: '/account/security', label: 'security', expectedText: 'امنیت حساب' },
  { path: '/account/notifications', label: 'notifications', expectedText: 'مرکز اعلان‌ها' },
  { path: '/market', label: 'market', expectedText: 'بازار' },
  { path: '/admin', label: 'admin', expectedText: 'پنل مدیریت' },
]

const CURRENT_USER = {
  id: 9001,
  account_name: 'stage10_visual_user',
  full_name: 'کاربر تست Stage 10',
  role: 'مدیر ارشد',
  account_status: 'active',
  is_accountant: false,
  customer_tier: null,
  has_bot_access: true,
}

function createJwt(payload: Record<string, unknown>) {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url')
  return `${header}.${body}.stage10`
}

async function primeAuthenticatedLayout(page: Page) {
  const token = createJwt({
    sub: String(CURRENT_USER.id),
    exp: Math.floor(Date.now() / 1000) + 60 * 60,
    session_id: 'stage10-viewport-session',
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
          id: 'stage10-session',
          device_name: 'Viewport Browser',
          platform: 'web',
          is_current: true,
          created_at: new Date().toISOString(),
          last_seen_at: new Date().toISOString(),
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
      return json([{ id: 1, name: 'طلای آب‌شده' }])
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

    return json({})
  })
}

async function expectNoHorizontalOverflow(page: Page, label: string) {
  const metrics = await page.evaluate(() => {
    const doc = document.documentElement
    const body = document.body
    const app = document.querySelector('#app') as HTMLElement | null
    const maxScrollWidth = Math.max(
      doc.scrollWidth,
      body.scrollWidth,
      app?.scrollWidth || 0,
    )
    const fixedElements = Array.from(document.querySelectorAll<HTMLElement>('.bottom-nav-bar, .market-action-bar'))
      .filter((element) => {
        const style = window.getComputedStyle(element)
        return style.display !== 'none' && style.visibility !== 'hidden'
      })
      .map((element) => {
        const rect = element.getBoundingClientRect()
        return {
          className: element.className,
          left: rect.left,
          right: rect.right,
          bottom: rect.bottom,
          width: rect.width,
        }
      })

    return {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      maxScrollWidth,
      fixedElements,
    }
  })

  expect(metrics.maxScrollWidth, `${label}: horizontal overflow`).toBeLessThanOrEqual(metrics.viewportWidth + 1)
  for (const element of metrics.fixedElements) {
    expect(element.left, `${label}: fixed element left bound ${element.className}`).toBeGreaterThanOrEqual(-1)
    expect(element.right, `${label}: fixed element right bound ${element.className}`).toBeLessThanOrEqual(metrics.viewportWidth + 1)
    expect(element.bottom, `${label}: fixed element bottom bound ${element.className}`).toBeLessThanOrEqual(metrics.viewportHeight + 1)
  }
}

async function expectLastControlClearOfBottomChrome(page: Page, label: string) {
  const metrics = await page.evaluate(() => {
    const focusableSelector = [
      'button:not([disabled])',
      'a[href]',
      'input:not([disabled]):not([type="hidden"])',
      'textarea:not([disabled])',
      'select:not([disabled])',
      '[role="button"]',
      '[tabindex]:not([tabindex="-1"])',
    ].join(',')

    const isVisible = (element: HTMLElement) => {
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number(style.opacity || '1') > 0
        && rect.width > 0
        && rect.height > 0
    }

    const scrollables = Array.from(document.querySelectorAll<HTMLElement>('body, #app, main, section, div'))
      .filter((element) => {
        const style = window.getComputedStyle(element)
        const overflowY = `${style.overflowY} ${style.overflow}`
        return /(auto|scroll)/.test(overflowY) && element.scrollHeight > element.clientHeight + 2
      })

    for (const element of scrollables) {
      element.scrollTop = element.scrollHeight
    }
    window.scrollTo(0, document.documentElement.scrollHeight)

    const fixedBottomElements = Array.from(document.querySelectorAll<HTMLElement>('body *'))
      .filter((element) => {
        if (!isVisible(element)) return false
        const style = window.getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        const isBottomFixed = (style.position === 'fixed' || style.position === 'sticky')
          && rect.bottom >= window.innerHeight - 2
          && rect.top < window.innerHeight
          && rect.height > 8
        const isKnownBottomChrome = element.matches('.bottom-nav-bar, .market-action-bar')
        return isBottomFixed || isKnownBottomChrome
      })
      .map((element) => {
        const rect = element.getBoundingClientRect()
        return {
          className: element.className.toString(),
          top: rect.top,
          bottom: rect.bottom,
          left: rect.left,
          right: rect.right,
          height: rect.height,
        }
      })
      .sort((a, b) => a.top - b.top)

    const bottomChromeTop = fixedBottomElements.length
      ? Math.min(...fixedBottomElements.map(element => element.top))
      : window.innerHeight

    const focusables = Array.from(document.querySelectorAll<HTMLElement>(focusableSelector))
      .filter((element) => {
        if (!isVisible(element)) return false
        const isInsideBottomChrome = fixedBottomElements.some((chrome) => {
          const rect = element.getBoundingClientRect()
          const overlapsX = rect.right > chrome.left && rect.left < chrome.right
          const overlapsY = rect.bottom > chrome.top && rect.top < chrome.bottom
          return overlapsX && overlapsY
        })
        return !isInsideBottomChrome
      })

    const lastControl = focusables.at(-1) ?? null
    if (!lastControl) {
      return {
        viewportHeight: window.innerHeight,
        bottomChromeTop,
        fixedBottomElements,
        lastControl: null,
        gap: null,
      }
    }

    const rect = lastControl.getBoundingClientRect()

    return {
      viewportHeight: window.innerHeight,
      bottomChromeTop,
      fixedBottomElements,
      lastControl: {
        tagName: lastControl.tagName,
        text: (lastControl.textContent || lastControl.getAttribute('aria-label') || '').trim().slice(0, 80),
        top: rect.top,
        bottom: rect.bottom,
        left: rect.left,
        right: rect.right,
        height: rect.height,
      },
      gap: bottomChromeTop - rect.bottom,
    }
  })

  if (!metrics.lastControl) return

  expect(
    metrics.gap,
    `${label}: last control "${metrics.lastControl.text}" must stay at least 12px above bottom nav/fixed bar`,
  ).toBeGreaterThanOrEqual(12)
}

test.describe('Non-messenger responsive viewport matrix', () => {
  test.beforeEach(async ({ page }) => {
    await primeAuthenticatedLayout(page)
    await installApiMocks(page)
  })

  for (const viewport of VIEWPORTS) {
    test(`${viewport.label} keeps core non-messenger routes inside viewport`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })

      for (const route of ROUTES) {
        await page.goto(route.path, { waitUntil: 'domcontentloaded' })
        await expect(page.getByText(route.expectedText).first()).toBeVisible({ timeout: 10_000 })
        await expectNoHorizontalOverflow(page, `${viewport.label}:${route.label}`)
        if (viewport.width <= 430) {
          await expectLastControlClearOfBottomChrome(page, `${viewport.label}:${route.label}`)
        }
      }
    })
  }
})
