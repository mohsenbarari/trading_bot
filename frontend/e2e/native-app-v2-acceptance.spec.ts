import { expect, test, type Page } from '@playwright/test'
import {
  ACCOUNTANT_RELATION,
  CURRENT_USER,
  CUSTOMER_RELATION,
  REGULAR_USER,
  attachDiagnostics,
  createDiagnostics,
  expectCleanDiagnostics,
  installFailClosedApi,
  type FixtureMode,
  type RouteDiagnostics,
} from './helpers/nativeAppV2Api'

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

type RouteCase = {
  path: string
  readyText: string
  readyBy?: 'text' | 'accessible-name'
  family: 'auth' | 'profile' | 'operations' | 'admin' | 'messenger' | 'share-receive' | 'account' | 'home' | 'recovery'
  auth: boolean
}

const AUTHENTICATED_ROUTES: RouteCase[] = [
  { path: '/', readyText: 'خانه', family: 'home', auth: true },
  { path: '/setup-password', readyText: 'تنظیم رمز عبور', family: 'auth', auth: true },
  { path: '/operations', readyText: 'عملیات', family: 'operations', auth: true },
  { path: '/operations/customers', readyText: 'مشتریان', family: 'operations', auth: true },
  { path: '/operations/customers/13', readyText: 'مشتری پذیرش', family: 'operations', auth: true },
  { path: '/operations/accountants', readyText: 'حسابداران', family: 'operations', auth: true },
  { path: '/operations/accountants/13', readyText: 'حسابدار پذیرش', family: 'operations', auth: true },
  { path: '/account', readyText: 'حساب', family: 'account', auth: true },
  { path: '/account/security', readyText: 'امنیت حساب', family: 'account', auth: true },
  { path: '/account/storage', readyText: 'حافظه و داده‌ها', family: 'account', auth: true },
  { path: '/account/notifications', readyText: 'اعلان‌ها', family: 'account', auth: true },
  { path: '/chat', readyText: 'جستجو', readyBy: 'accessible-name', family: 'messenger', auth: true },
  { path: '/users/9001', readyText: 'اطلاعات شخصی', family: 'profile', auth: true },
  { path: '/profile', readyText: 'اطلاعات شخصی', family: 'profile', auth: true },
  { path: '/settings', readyText: 'تنظیمات حساب', family: 'account', auth: true },
  { path: '/admin', readyText: 'مرکز مدیریت', family: 'admin', auth: true },
  { path: '/admin/invitations', readyText: 'ارسال دعوت‌نامه', family: 'admin', auth: true },
  { path: '/admin/channels', readyText: 'ساخت کانال', family: 'admin', auth: true },
  { path: '/admin/users', readyText: 'مدیریت کاربران', family: 'admin', auth: true },
  { path: '/admin/users/9001', readyText: 'native_app_v2_user', family: 'admin', auth: true },
  { path: '/admin/commodities', readyText: 'مدیریت کالاها', family: 'admin', auth: true },
  { path: '/admin/messages', readyText: 'پیام‌های مدیریت', family: 'admin', auth: true },
  { path: '/admin/system', readyText: 'تنظیمات سیستم', family: 'admin', auth: true },
  { path: '/notifications', readyText: 'اعلان‌ها', family: 'account', auth: true },
  { path: '/share-receive', readyText: 'اشتراک‌گذاری آماده نشد', family: 'share-receive', auth: true },
]

const PUBLIC_ROUTES: RouteCase[] = [
  { path: '/login', readyText: 'ورود به سامانه', family: 'auth', auth: false },
  { path: '/register', readyText: 'تکمیل ثبت‌نام', family: 'auth', auth: false },
  { path: '/i/uiux-baseline', readyText: 'ثبت‌نام در وب‌اپ', family: 'auth', auth: false },
  { path: '/this-route-does-not-exist', readyText: 'این صفحه پیدا نشد', family: 'recovery', auth: false },
]

const ALL_LIVE_ROUTES = [...AUTHENTICATED_ROUTES, ...PUBLIC_ROUTES]
const SENSITIVE_FAMILIES = new Set([
  'auth',
  'profile',
  'operations',
  'admin',
  'messenger',
  'share-receive',
  'recovery',
])

function createJwt(userId = CURRENT_USER.id) {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify({
    sub: String(userId),
    exp: Math.floor(Date.now() / 1000) + 60 * 60,
    session_id: 'native-app-v2-acceptance',
  })).toString('base64url')
  return `${header}.${body}.native-v2`
}

const AUTH_DISABLE_KEY = 'native-v2-auth-disabled'

async function primeAuthenticatedLayout(page: Page, user = CURRENT_USER) {
  const token = createJwt(user.id)
  await page.addInitScript(({ accessToken, userSummary, disableKey }) => {
    if (window.sessionStorage.getItem(disableKey) === '1') {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('current_user_summary')
      return
    }
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', accessToken)
    localStorage.setItem('current_user_summary', JSON.stringify(userSummary))
    localStorage.removeItem('suspended_refresh_token')
  }, {
    accessToken: token,
    userSummary: user,
    disableKey: AUTH_DISABLE_KEY,
  })
}

async function clearAuthenticatedLayout(page: Page) {
  await page.addInitScript((disableKey) => {
    window.sessionStorage.setItem(disableKey, '1')
    localStorage.removeItem('auth_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('current_user_summary')
  }, AUTH_DISABLE_KEY)
}

async function disableAuthForPublicRoutes(page: Page) {
  await page.evaluate((disableKey) => {
    window.sessionStorage.setItem(disableKey, '1')
    localStorage.removeItem('auth_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('current_user_summary')
  }, AUTH_DISABLE_KEY)
}

async function preparePage(
  page: Page,
  options: { auth?: boolean; mode?: FixtureMode; user?: typeof CURRENT_USER } = {},
) {
  const diagnostics = createDiagnostics()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  if (options.auth === false) {
    await clearAuthenticatedLayout(page)
  } else {
    await primeAuthenticatedLayout(page, options.user)
  }
  await attachDiagnostics(page, diagnostics)
  const controller = await installFailClosedApi(page, diagnostics, { mode: options.mode, viewer: options.user })
  return { diagnostics, controller }
}

function routeByPath(path: string) {
  return ALL_LIVE_ROUTES.find((route) => route.path === path)
}

async function expectRouteReady(page: Page, route: RouteCase) {
  const timeout = route.family === 'messenger' ? 45_000 : 15_000
  if (route.readyBy === 'accessible-name') {
    await expect(page.getByRole('button', { name: route.readyText }).first()).toBeVisible({ timeout })
    return
  }
  await expect(page.getByText(route.readyText, { exact: false }).first()).toBeVisible({ timeout })
}

async function gotoRouteWithNavigationRetry(page: Page, path: string) {
  const mountTimeout = path === '/chat' || path.startsWith('/chat?') ? 45_000 : 15_000
  let lastError: unknown = null
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await page.goto(path, { waitUntil: 'domcontentloaded' })
      await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: mountTimeout })
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

async function collectRouteContract(page: Page) {
  return page.evaluate(() => {
    const isVisible = (element: Element) => {
      if (!(element instanceof HTMLElement)) return false
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
    const interactives = Array.from(document.querySelectorAll<HTMLElement>(
      'a[href], button, input, textarea, select, [role="button"], [role="tab"], [role="menuitem"]',
    )).filter(isVisible)
    const unnamed = interactives.filter((element) => (
      !nameFor(element) && element.getAttribute('aria-hidden') !== 'true'
    ))
    const nested = interactives.filter((element) => (
      interactives.some((other) => other !== element && other.contains(element))
    ))
    const undersized = interactives.flatMap((element) => {
      const rect = element.getBoundingClientRect()
      if (rect.height >= 48 && rect.width >= 44) return []
      const input = element as HTMLInputElement
      const label = element.closest('label')
      if (label && (input.type === 'checkbox' || input.type === 'radio')) {
        const labelRect = label.getBoundingClientRect()
        if (labelRect.height >= 48 && labelRect.width >= 44) return []
      }
      if (element.getAttribute('aria-hidden') === 'true') return []
      return [`${element.tagName.toLowerCase()}.${element.className.split(' ').slice(0, 3).join('.')}:${Math.round(rect.width)}x${Math.round(rect.height)}`]
    })
    const doc = document.documentElement
    const body = document.body
    const app = document.querySelector('#app') as HTMLElement | null
    const routeScroll = document.querySelector('.app-route-scroll') as HTMLElement | null
    const nav = document.querySelector('.bottom-nav-wrapper, .ui-v2-bottom-nav, .bottom-nav-bar') as HTMLElement | null
    const ctas = Array.from(document.querySelectorAll<HTMLElement>(
      '.app-route-scroll button, .app-route-scroll [role="button"], .app-route-scroll a[href]',
    )).filter(isVisible)
    const navRect = nav && isVisible(nav) ? nav.getBoundingClientRect() : null
    if (routeScroll) routeScroll.scrollTop = routeScroll.scrollHeight
    const viewportCtas = ctas.filter((element) => {
      const rect = element.getBoundingClientRect()
      return rect.bottom > 8
        && rect.top < window.innerHeight - 8
        && rect.left < window.innerWidth - 8
        && rect.right > 8
    })
    const lastCta = viewportCtas.at(-1) || ctas.at(-1)
    const lastCtaAfterScroll = lastCta?.getBoundingClientRect()
    const visibleLeft = lastCtaAfterScroll ? Math.max(lastCtaAfterScroll.left, 0) : 0
    const visibleRight = lastCtaAfterScroll ? Math.min(lastCtaAfterScroll.right, window.innerWidth) : 0
    const visibleTop = lastCtaAfterScroll ? Math.max(lastCtaAfterScroll.top, 0) : 0
    const visibleBottom = lastCtaAfterScroll ? Math.min(lastCtaAfterScroll.bottom, window.innerHeight) : 0
    const hitX = visibleLeft + Math.max(visibleRight - visibleLeft, 0) / 2
    const hitY = visibleTop + Math.max(visibleBottom - visibleTop, 0) / 2
    const hitNode = lastCtaAfterScroll && visibleRight - visibleLeft >= 8 && visibleBottom - visibleTop >= 8
      ? document.elementFromPoint(hitX, hitY)
      : lastCta || null
    return {
      mainCount: Array.from(document.querySelectorAll('main, [role="main"]')).filter((element) => {
        if (!(element instanceof HTMLElement)) return false
        const style = window.getComputedStyle(element)
        return style.display !== 'none' && style.visibility !== 'hidden'
      }).length,
      h1Count: document.querySelectorAll('h1').length,
      unnamed: unnamed.slice(0, 8).map((element) => `${element.tagName}.${element.className}`),
      nested: nested.slice(0, 8).map((element) => `${element.tagName}.${element.className}`),
      undersized: undersized.slice(0, 8),
      viewportWidth: window.innerWidth,
      maxScrollWidth: Math.max(doc.scrollWidth, body.scrollWidth, app?.scrollWidth || 0),
      routeScrollWidth: routeScroll?.scrollWidth || 0,
      routeClientWidth: routeScroll?.clientWidth || window.innerWidth,
      scrollerCount: [routeScroll, document.scrollingElement].filter((node) => {
        if (!(node instanceof HTMLElement)) return false
        return node.scrollHeight > node.clientHeight + 1
      }).length,
      ctaAboveNav: !lastCtaAfterScroll || !navRect ? true : lastCtaAfterScroll.bottom <= navRect.top + 2,
      lastCtaCenterHit: !lastCta || !lastCtaAfterScroll
        ? true
        : Boolean(hitNode && (hitNode === lastCta || lastCta.contains(hitNode) || hitNode.closest('button, a, [role="button"]') === lastCta)),
      reducedMotion: window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true,
    }
  })
}

async function expectRouteContract(page: Page, label: string) {
  const contract = await collectRouteContract(page)
  expect(contract.mainCount, `${label}: exactly one main`).toBe(1)
  expect(contract.h1Count, `${label}: exactly one h1`).toBe(1)
  expect(contract.unnamed, `${label}: unnamed controls`).toEqual([])
  expect(contract.nested, `${label}: nested controls`).toEqual([])
  expect(contract.undersized, `${label}: target < 48`).toEqual([])
  expect(contract.maxScrollWidth, `${label}: document overflow`).toBeLessThanOrEqual(contract.viewportWidth + 1)
  expect(contract.routeScrollWidth, `${label}: route overflow`).toBeLessThanOrEqual(contract.routeClientWidth + 1)
  expect(contract.ctaAboveNav, `${label}: CTA above BottomNav`).toBe(true)
  expect(contract.lastCtaCenterHit, `${label}: CTA hit-test`).toBe(true)
  expect(contract.reducedMotion, `${label}: reduced motion`).toBe(true)
}

async function expectNoPageCrash(page: Page, label: string) {
  const pageError = await page.evaluate(() => {
    const app = document.querySelector('#app')
    return app?.textContent?.includes('Something went wrong') ? 'app crashed' : ''
  })
  expect(pageError, `${label}: page error`).toBe('')
}

async function visitAndAssert(page: Page, route: RouteCase, label: string) {
  await gotoRouteWithNavigationRetry(page, route.path)
  await expectRouteReady(page, route)
  await expectNoPageCrash(page, label)
  await expectRouteContract(page, label)
}

test.describe('Native App V2 fail-closed 29-route matrix', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  for (const viewport of VIEWPORTS) {
    test(`${viewport.label} Chromium contract for all 29 non-market routes`, async ({ page, browserName }) => {
      test.skip(browserName !== 'chromium', 'full 29-route production matrix is Chromium; Firefox/WebKit cover sensitive families below.')
      test.setTimeout(240_000)
      const { diagnostics } = await preparePage(page, { auth: true })
      await page.setViewportSize({ width: viewport.width, height: viewport.height })

      for (const route of AUTHENTICATED_ROUTES) {
        await visitAndAssert(page, route, `${viewport.label}:${route.path}`)
      }

      await disableAuthForPublicRoutes(page)
      for (const route of PUBLIC_ROUTES) {
        await visitAndAssert(page, route, `${viewport.label}:${route.path}`)
      }

      expectCleanDiagnostics(diagnostics, viewport.label)
    })
  }
})

test.describe('Native App V2 sensitive families', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  for (const viewport of [
    { width: 390, height: 844, label: 'mobile-390' },
    { width: 1440, height: 900, label: 'desktop-1440' },
  ]) {
    test(`${viewport.label} Firefox/WebKit sensitive families stay inside contract`, async ({ page, browserName }) => {
      test.skip(browserName === 'chromium', 'Chromium already covers the full 29-route matrix.')
      test.setTimeout(180_000)
      const { diagnostics } = await preparePage(page, { auth: true })
      await page.setViewportSize({ width: viewport.width, height: viewport.height })

      for (const route of ALL_LIVE_ROUTES.filter((item) => SENSITIVE_FAMILIES.has(item.family))) {
        if (route.auth) {
          await visitAndAssert(page, route, `${browserName}:${viewport.label}:${route.path}`)
        }
      }

      await disableAuthForPublicRoutes(page)
      for (const route of PUBLIC_ROUTES.filter((item) => SENSITIVE_FAMILIES.has(item.family))) {
        await visitAndAssert(page, route, `${browserName}:${viewport.label}:${route.path}`)
      }

      expectCleanDiagnostics(diagnostics, `${browserName}:${viewport.label}`)
    })
  }
})

test.describe('Native App V2 states and interactions', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  test('login stays keyboard-usable and hides developer shortcut', async ({ page }) => {
    const { diagnostics } = await preparePage(page, { auth: false })
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
    await expect(mobile).toHaveAttribute('autocomplete', /tel|username/)
    await expectRouteContract(page, 'login-keyboard')
    expectCleanDiagnostics(diagnostics, 'login-keyboard')
  })

  test('account remains usable at 200% zoom when the browser exposes page scale', async ({ page, browserName }) => {
    const { diagnostics } = await preparePage(page, { auth: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/account')
    await expect(page.getByText('حساب').first()).toBeVisible({ timeout: 10_000 })

    if (browserName === 'chromium') {
      const session = await page.context().newCDPSession(page)
      await session.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 })
      await page.waitForTimeout(120)
      const zoom = await page.evaluate(() => ({
        scale: window.visualViewport?.scale ?? 1,
        width: window.visualViewport?.width ?? window.innerWidth,
      }))
      expect(zoom.scale).toBeCloseTo(2, 1)
      expect(zoom.width).toBeGreaterThanOrEqual(194)
    } else {
      await page.evaluate(() => {
        document.documentElement.style.setProperty('zoom', '2')
      })
      const zoomSupport = await page.evaluate(() => {
        const applied = getComputedStyle(document.documentElement).zoom
        return applied === '2' || applied === '2.0'
      })
      test.info().annotations.push({
        type: 'zoom.page-scale',
        description: zoomSupport
          ? 'CSS zoom applied; browser has no CDP page-scale API.'
          : 'zoom.naReason=browser-has-no-page-scale-api',
      })
    }

    await expect(page.getByRole('heading', { name: 'حساب' })).toBeVisible()
    await expectRouteContract(page, 'account-zoom-200')
    expectCleanDiagnostics(diagnostics, 'account-zoom-200')
  })

  test('empty, error, long Persian, LTR, and offline states stay named and unoverflowed', async ({ page }) => {
    test.setTimeout(90_000)
    const { diagnostics, controller } = await preparePage(page, { auth: true, mode: 'empty' })
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/account/notifications')
    await expect(page.getByText('هیچ اعلانی یافت نشد')).toBeVisible({ timeout: 10_000 })
    await expectRouteContract(page, 'notifications-empty')

    await gotoRouteWithNavigationRetry(page, '/chat')
    await expectRouteReady(page, routeByPath('/chat')!)
    await expectRouteContract(page, 'chat-empty')

    controller.mode = 'long-copy'
    await gotoRouteWithNavigationRetry(page, '/profile')
    await expect(page.getByText('09120000000')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('تهران')).toBeVisible()
    await expect(page.getByText(/نام بسیار بلند فارسی/)).toBeVisible()
    await expect(page.getByText('unbroken_ltr_accountnamewithoutspaces_9001')).toBeVisible()
    await expectRouteContract(page, 'profile-long-copy')

    controller.mode = 'error'
    await gotoRouteWithNavigationRetry(page, '/operations/customers')
    await expect(page.getByText(/ناموفق|خطا|دوباره|دریافت/i).first()).toBeVisible({ timeout: 10_000 })
    await expectRouteContract(page, 'customers-error')
    expectCleanDiagnostics(diagnostics, 'state-matrix')
  })

  test('home sheet, operations, profile, and messenger keep Escape restoration and one live surface', async ({ page }) => {
    test.setTimeout(90_000)
    const { diagnostics } = await preparePage(page, { auth: true })
    await page.setViewportSize({ width: 390, height: 844 })

    await gotoRouteWithNavigationRetry(page, '/')
    await expect(page.getByRole('heading', { name: 'خانه' })).toBeVisible()
    const accountTrigger = page.getByLabel(/باز کردن منوی حساب/)
    await accountTrigger.click()
    await expect(page.getByRole('dialog', { name: 'حساب' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'حساب' })).toHaveCount(0)
    await expect(accountTrigger).toBeFocused()

    await gotoRouteWithNavigationRetry(page, '/operations')
    await expect(page.getByText('مشتریان')).toBeVisible()
    await gotoRouteWithNavigationRetry(page, '/operations/customers')
    await expect(page.getByText(CUSTOMER_RELATION.management_name)).toBeVisible()
    await gotoRouteWithNavigationRetry(page, '/operations/accountants')
    await expect(page.getByText(ACCOUNTANT_RELATION.relation_display_name)).toBeVisible()

    await gotoRouteWithNavigationRetry(page, '/profile')
    await expect(page.getByText('09120000000')).toBeVisible()
    await expect(page.getByText('تهران')).toBeVisible()

    await gotoRouteWithNavigationRetry(page, '/chat')
    await expectRouteReady(page, routeByPath('/chat')!)
    await expectRouteContract(page, 'chat-normal')
    expectCleanDiagnostics(diagnostics, 'sensitive-interactions')
  })

  test('soft keyboard and safe-area keep the login submit reachable', async ({ page }) => {
    const { diagnostics } = await preparePage(page, { auth: false })
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/login')
    await expect(page.getByText('ورود به سامانه').first()).toBeVisible({ timeout: 10_000 })
    await page.getByLabel('شماره موبایل').focus()
    await page.evaluate(() => {
      window.visualViewport?.dispatchEvent(new Event('resize'))
    })
    const submit = page.getByRole('button', { name: 'دریافت کد تأیید' })
    await expect(submit).toBeVisible()
    const geometry = await page.evaluate(() => {
      const button = document.querySelector('button')
      const rect = button?.getBoundingClientRect()
      return {
        bottom: rect?.bottom ?? 0,
        viewport: window.innerHeight,
        safeArea: getComputedStyle(document.documentElement).getPropertyValue('env(safe-area-inset-bottom)') || '0',
      }
    })
    expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewport + 1)
    await expectRouteContract(page, 'login-soft-keyboard')
    expectCleanDiagnostics(diagnostics, 'login-soft-keyboard')
  })

  test('forbidden probe keeps unauthorized admin and owner routes off-limits', async ({ page }) => {
    const { diagnostics, controller } = await preparePage(page, { auth: true, user: REGULAR_USER })
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/admin')
    await expect(page.getByText(/دسترسی|مجاز|پیدا نشد|بازیابی/i).first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('مرکز مدیریت')).toHaveCount(0)

    const customerViewer = { ...REGULAR_USER, is_customer: true, id: 33 }
    controller.viewer = customerViewer
    await page.evaluate((userSummary) => {
      localStorage.setItem('current_user_summary', JSON.stringify(userSummary))
    }, customerViewer)
    await gotoRouteWithNavigationRetry(page, '/operations/customers')
    await expect(page.getByText(/دسترسی|مجاز|پیدا نشد|بازیابی/i).first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(CUSTOMER_RELATION.management_name)).toHaveCount(0)
    expectCleanDiagnostics(diagnostics, 'forbidden-probe')
  })
})
