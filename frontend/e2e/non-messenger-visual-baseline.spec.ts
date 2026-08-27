import { expect, test, type Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import {
  attachDiagnostics,
  createDiagnostics,
  expectCleanDiagnostics,
  installFailClosedApi,
} from './helpers/nativeAppV2Api'

const shouldRunBaseline = process.env.UI_UX_BASELINE === '1'
const shouldRunA11ySmoke = process.env.UI_UX_A11Y === '1'
const externalArtifactDir = process.env.UI_UX_ARTIFACT_DIR?.trim() || ''
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
  readyText: string
}

const VIEWPORTS: ViewportCase[] = [
  { width: 390, height: 844, label: 'mobile-390' },
  { width: 1440, height: 900, label: 'desktop-1440' },
]

const ROUTES: RouteCase[] = [
  { path: '/', label: 'dashboard', authenticated: true, readyText: 'ورود به بازار' },
  { path: '/market', label: 'market', authenticated: true, readyText: 'هیچ لفظ فعالی یافت نشد' },
  { path: '/operations', label: 'operations', authenticated: true, readyText: 'عملیات' },
  { path: '/operations/customers', label: 'customers', authenticated: true, readyText: 'مشتریان' },
  { path: '/operations/accountants', label: 'accountants', authenticated: true, readyText: 'حسابداران' },
  { path: '/account', label: 'account', authenticated: true, readyText: 'حساب' },
  { path: '/profile', label: 'profile', authenticated: true, readyText: 'اطلاعات شخصی' },
  { path: '/account/notifications', label: 'notifications', authenticated: true, readyText: 'هیچ اعلانی یافت نشد' },
  { path: '/admin/users', label: 'admin-users', authenticated: true, readyText: 'مدیریت کاربران' },
  { path: '/admin/commodities', label: 'admin-commodities', authenticated: true, readyText: 'مدیریت کالاها' },
  { path: '/login', label: 'login', authenticated: false, readyText: 'ورود به سامانه' },
  { path: '/register', label: 'register', authenticated: false, readyText: 'تکمیل ثبت‌نام' },
  { path: '/i/uiux-baseline', label: 'invite-landing', authenticated: false, readyText: 'دعوت‌نامه اختصاصی' },
]

const CURRENT_USER = {
  id: 9001,
  account_name: 'uiux_visual_user',
  full_name: 'کاربر تست UI',
  role: 'مدیر ارشد',
  account_status: 'active',
  is_accountant: false,
  is_customer: false,
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

async function installVisualBaselineHarness(page: Page) {
  const diagnostics = createDiagnostics()
  await attachDiagnostics(page, diagnostics)
  await installFailClosedApi(page, diagnostics, {
    mode: 'empty',
    viewer: CURRENT_USER,
    extraKnown: (method, pathname) => {
      if (pathname === '/api/auth/me' && method === 'GET') {
        return { status: 200, body: CURRENT_USER }
      }
      if (pathname === `/api/users-public/${CURRENT_USER.id}` && method === 'GET') {
        return {
          status: 200,
          body: {
            id: CURRENT_USER.id,
            account_name: CURRENT_USER.account_name,
            mobile_number: '09120000000',
            address: 'تهران، خیابان نمونه، پلاک ۱۲',
            created_at: fixedNow,
            trades_count: 0,
            last_seen_at: fixedNow,
          },
        }
      }
      if (pathname === '/api/invitations/lookup/uiux-baseline' && method === 'GET') {
        return {
          status: 200,
          body: {
            token: 'uiux-baseline-token',
            valid: true,
            state: 'pending',
            bot_available: true,
            web_available: true,
            web_short_link: '/i/uiux-baseline',
            expires_at: null,
          },
        }
      }
      if (pathname === '/api/config' && method === 'GET') {
        return { status: 200, body: { bot_username: 'uiux_baseline_bot', telegram_bot_username: 'uiux_baseline_bot' } }
      }
      if (pathname === '/api/notifications/mark-all-read' && method === 'POST') {
        return { status: 200, body: { ok: true } }
      }
      return null
    },
    extraAllowedMutation: (pathname, method) => (
      method === 'POST' && pathname === '/api/notifications/mark-all-read'
    ),
  })
  return diagnostics
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

    const nameFor = (element: HTMLElement) => {
      const labelledBy = (element.getAttribute('aria-labelledby') || '')
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => document.getElementById(id)?.textContent || '')
        .join(' ')
      const nativeLabels = 'labels' in element
        ? Array.from((element as HTMLInputElement).labels || [])
            .map((item) => item.textContent || '')
            .join(' ')
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
        const diagnostics = await installVisualBaselineHarness(page)
        if (route.authenticated) {
          await primeAuthenticatedLayout(page)
        } else {
          await clearAuthenticatedLayout(page)
        }

        await page.setViewportSize({ width: viewport.width, height: viewport.height })
        await gotoRouteWithNavigationRetry(page, route.path)
        await expect(page.locator('#app')).toBeVisible({ timeout: 10_000 })
        await expect(page.locator('html')).toHaveAttribute('data-app-mounted', '1', { timeout: 10_000 })
        await expect(page.locator('#boot-loader')).toBeHidden({ timeout: 10_000 })
        await expect(page.getByText(route.readyText, { exact: false }).first()).toBeVisible({ timeout: 10_000 })
        await expect(page.getByText('در حال بارگذاری', { exact: false }).first()).toBeHidden({ timeout: 10_000 })
        await page.evaluate(async () => {
          await document.fonts?.ready
        })

        if (shouldRunA11ySmoke) {
          await expectCriticalA11yBasics(page, `${viewport.label}:${route.label}`)
        }

        if (externalArtifactDir) {
          await mkdir(externalArtifactDir, { recursive: true })
          await page.screenshot({
            path: path.join(externalArtifactDir, `${route.label}-${viewport.label}.png`),
            animations: 'disabled',
            fullPage: true,
          })
        }

        await expect(page).toHaveScreenshot(`${route.label}-${viewport.label}.png`, {
          animations: 'disabled',
          fullPage: true,
          maxDiffPixelRatio: 0.005,
        })
        expectCleanDiagnostics(diagnostics, `${viewport.label}:${route.label}`)
      })
    }
  }
})
