import { expect, test, type Page } from '@playwright/test'
import {
  ACCOUNTANT_RELATION,
  CURRENT_USER,
  CUSTOMER_RELATION,
  REGULAR_USER,
  allowExpectedOfflineRequestFailed,
  allowHarnessHoldAbort,
  allowIntentionalFixtureConsole,
  allowOfflineConsole,
  attachDiagnostics,
  countEnvironmentalDiagnostics,
  createDiagnosticContext,
  createDiagnostics,
  expectCleanDiagnostics,
  installFailClosedApi,
  waitForLocalIdle,
  withControlledNavigation,
  type DiagnosticContext,
  type FixtureController,
  type FixtureMode,
  type RouteDiagnostics,
} from './helpers/nativeAppV2Api'
import {
  applyControlledSafeArea,
  applyMeasurableZoom,
  expectRouteContract,
  simulateSoftKeyboard,
  waitForVisualStability,
} from './helpers/nativeAppV2Contract'
import {
  KEYBOARD_FORM_ROUTES,
  ROUTE_DESCRIPTORS,
  SENSITIVE_FAMILIES,
  SENSITIVE_VIEWPORTS,
  VIEWPORTS,
  ZOOM_FAMILY_REPRESENTATIVES,
  assertMatrixCoverage,
  naCells,
  observationForState,
  offlineGetPathsFor,
  type RouteDescriptor,
  type StateId,
} from './helpers/nativeAppV2Matrix'

assertMatrixCoverage()

const AUTH_DISABLE_KEY = 'native-v2-auth-disabled'

function createJwt(userId = CURRENT_USER.id) {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify({
    sub: String(userId),
    exp: Math.floor(Date.now() / 1000) + 60 * 60,
    session_id: 'native-app-v2-acceptance',
  })).toString('base64url')
  return `${header}.${body}.native-v2`
}

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

function modeForState(state: StateId): FixtureMode {
  if (state === 'empty') return 'empty'
  if (state === 'error') return 'error'
  if (state === 'full') return 'full'
  if (state === 'long-persian') return 'long-persian'
  if (state === 'unbroken') return 'unbroken'
  if (state === 'ltr') return 'ltr'
  if (state === 'stale') return 'stale'
  return 'normal'
}

function viewerForState(route: RouteDescriptor, state: StateId) {
  if (state !== 'unauthorized') return CURRENT_USER
  if (route.family === 'admin') return REGULAR_USER
  if (route.family === 'operations') return { ...REGULAR_USER, is_customer: true, id: 33 }
  return CURRENT_USER
}

async function preparePage(
  page: Page,
  route: RouteDescriptor,
  state: StateId,
) {
  const diagnostics = createDiagnostics()
  const viewer = viewerForState(route, state)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  if (!route.auth || state === 'unauthorized' && route.family === 'admin') {
    if (!route.auth) await clearAuthenticatedLayout(page)
    else await primeAuthenticatedLayout(page, viewer)
  } else if (state === 'unauthorized' && route.family === 'operations') {
    await primeAuthenticatedLayout(page, viewer)
  } else {
    await primeAuthenticatedLayout(page, viewer)
  }
  const allowErrorConsole = state === 'error' || state === 'retry' || state === 'loading' || state === 'slow'
  const expectedOfflineGetPaths = offlineGetPathsFor(route)
  const diagnosticContext = createDiagnosticContext()
  await attachDiagnostics(page, diagnostics, {
    allowConsole: (text) => {
      if (state === 'offline' && allowOfflineConsole(text)) return true
      if (allowErrorConsole && allowIntentionalFixtureConsole(text)) return true
      return false
    },
    allowRequestFailed: (text) => {
      if (state === 'offline') return allowExpectedOfflineRequestFailed(text, expectedOfflineGetPaths)
      if ((state === 'loading' || state === 'slow') && route.holdPath) {
        return allowHarnessHoldAbort(text, route.holdPath)
      }
      if (state === 'retry' && route.errorPath) {
        return allowHarnessHoldAbort(text, route.errorPath)
      }
      return false
    },
  }, diagnosticContext)
  const controller = await installFailClosedApi(page, diagnostics, {
    mode: modeForState(state),
    viewer,
    expectedOfflineGetPaths,
  })
  return { diagnostics, controller, viewer, diagnosticContext, expectedOfflineGetPaths }
}

async function gotoRouteWithNavigationRetry(page: Page, path: string, context?: DiagnosticContext) {
  const mountTimeout = path === '/chat' || path.startsWith('/chat?') ? 45_000 : 15_000
  const navigate = async () => {
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
  if (context) return withControlledNavigation(context, navigate)
  return navigate()
}

async function settleAuthenticatedShell(context: DiagnosticContext) {
  await expect.poll(
    () => context.seenPaths.has('GET /api/sessions/recovery/pending')
      || context.seenPaths.has('GET /api/sessions/login-requests/pending')
      || context.seenPaths.has('POST /api/sessions/verify') === false,
    { timeout: 2500 },
  ).toBeTruthy()
  await waitForLocalIdle(context)
}

async function expectRouteReady(page: Page, route: RouteDescriptor) {
  const timeout = route.family === 'messenger' ? 45_000 : 15_000
  if (route.readyBy === 'accessible-name') {
    await expect(page.getByRole('button', { name: route.readyText }).first()).toBeVisible({ timeout })
  } else {
    await expect(page.getByText(route.readyText, { exact: false }).first()).toBeVisible({ timeout })
  }
  await expect(page.locator('.fade-enter-active, .fade-leave-active, .ui-v2-route-fade-enter-active, .ui-v2-route-fade-leave-active')).toHaveCount(0, { timeout: 8_000 })
}

async function expectNoPageCrash(page: Page, label: string) {
  const pageError = await page.evaluate(() => {
    const app = document.querySelector('#app')
    return app?.textContent?.includes('Something went wrong') ? 'app crashed' : ''
  })
  expect(pageError, `${label}: page error`).toBe('')
}

async function runSettledContract(
  page: Page,
  route: RouteDescriptor,
  diagnostics: RouteDiagnostics,
  label: string,
  state?: StateId,
  controller?: FixtureController,
) {
  const contractRoute = resolveContractRoute(route, state)
  await expectRouteReady(page, contractRoute)
  await waitForVisualStability(page, { controller })
  await expectNoPageCrash(page, label)
  await expectRouteContract(page, contractRoute, label)
  expectCleanDiagnostics(diagnostics, label)
  const counts = countEnvironmentalDiagnostics(diagnostics)
  test.info().annotations.push({
    type: 'environmental-count',
    description: JSON.stringify(counts),
  })
  if (counts.console + counts.pageErrors + counts.requestFailed > 0) {
    test.info().annotations.push({
      type: 'environmental',
      description: [
        ...diagnostics.environmentalConsole,
        ...diagnostics.environmentalPageErrors,
        ...diagnostics.environmentalRequestFailed,
      ].join(' | '),
    })
  }
}

function resolveContractRoute(route: RouteDescriptor, state?: StateId): RouteDescriptor {
  if (!state) return route
  if (state === 'unbroken') {
    if (route.id === 'profile' || route.id === 'public-profile') {
      return { ...route, h1: 'unbroken_ltr_accountnamewithoutspaces_9001' }
    }
    if (route.id === 'admin-user-profile') {
      return { ...route, readyText: 'unbroken_ltr_accountnamewithoutspaces_9001' }
    }
  }
  if (state === 'ltr') {
    if (route.id === 'profile' || route.id === 'public-profile') {
      return { ...route, h1: 'ltr_account_9001' }
    }
    if (route.id === 'admin-user-profile') {
      return { ...route, readyText: 'ltr_account_9001' }
    }
  }
  return route
}

async function runCell(
  page: Page,
  route: RouteDescriptor,
  state: StateId,
  controller: FixtureController,
  diagnostics: RouteDiagnostics,
  label: string,
  diagnosticContext: DiagnosticContext,
) {
  if (state === 'loading' || state === 'slow') {
    if (!route.holdPath) throw new Error(`${route.id} missing holdPath`)
    controller.hold(route.holdPath)
    const pending = gotoRouteWithNavigationRetry(page, route.path, diagnosticContext)
    const loading = page.locator(
      '.ui-loading-state, [aria-busy="true"], .dashboard-daily-state, .messenger-loader, .loading-state-skeleton',
    ).or(page.getByText(route.loadingText || /در حال بارگذاری/i)).first()
    await expect(loading).toBeVisible({ timeout: 10_000 })
    if (state === 'slow') await page.waitForTimeout(1200)
    controller.release(route.holdPath)
    await pending
    await runSettledContract(page, route, diagnostics, label, state, controller)
    await expect(page.locator('.ui-loading-state:visible, [aria-busy="true"]:visible, .dashboard-daily-state:visible, .messenger-loader:visible, .loading-state-skeleton:visible')).toHaveCount(0)
    return
  }

  if (state === 'retry') {
    if (!route.errorPath) throw new Error(`${route.id} missing errorPath`)
    controller.failUntil(route.errorPath)
    await gotoRouteWithNavigationRetry(page, route.path, diagnosticContext)
    await expect(page.locator('.ui-error-state, [role="alert"], .error-state, .channel-status-banner.error, .admin-messages-load-error').first()).toBeVisible({ timeout: 10_000 })
    const retryName = /تلاش مجدد|تلاش دوباره|بررسی دوباره/i
    await expect(page.getByRole('button', { name: retryName }).first()).toBeVisible()
    controller.clearFail(route.errorPath)
    try {
      await page.getByRole('button', { name: retryName }).first().click({ force: true, timeout: 4_000 })
    } catch {
      // Auto-refetch after releasing the sticky 422 may recover without a second press.
    }
    await runSettledContract(page, route, diagnostics, label, state, controller)
    await expect(page.locator('.ui-error-state:visible')).toHaveCount(0)
    return
  }

  if (state === 'offline') {
    await gotoRouteWithNavigationRetry(page, route.path, diagnosticContext)
    await expectRouteReady(page, route)
    await settleAuthenticatedShell(diagnosticContext)
    controller.setNetworkOffline(true)
    await page.evaluate(() => window.dispatchEvent(new Event('offline')))
    await withControlledNavigation(diagnosticContext, async () => {
      await page.reload({ waitUntil: 'domcontentloaded' })
      await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })
    })
    await expect.poll(
      () => diagnosticContext.seenPaths.has('GET /api/sessions/recovery/pending')
        || diagnosticContext.seenPaths.has('GET /api/sessions/login-requests/pending'),
      { timeout: 2500 },
    ).toBeTruthy()
    await waitForLocalIdle(diagnosticContext)
    await expect(page.getByText(/آفلاین|باز کردن این صفحه ممکن نشد|اتصال|ارتباط|شبکه|ناموفق|اکنون ممکن نشد|خطا|دریافت نشد|ممکن نشد|برقرار نشد|انجام نشد/i).first()).toBeVisible({ timeout: 10_000 })
    controller.setNetworkOffline(false)
    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    await waitForLocalIdle(diagnosticContext)
    await gotoRouteWithNavigationRetry(page, route.path, diagnosticContext)
    await runSettledContract(page, route, diagnostics, label, state, controller)
    return
  }

  await gotoRouteWithNavigationRetry(page, route.path, diagnosticContext)

  if (state === 'empty' && route.emptyText) {
    await expect(page.getByText(route.emptyText, { exact: false }).first()).toBeVisible({ timeout: 10_000 })
  }
  if (state === 'error') {
    await expect(page.locator('.ui-error-state, [role="alert"], .error-state, .channel-status-banner.error, .admin-messages-load-error').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(route.errorText || /ناموفق|خطا|دوباره|دریافت نشد|ممکن نشد/i).first()).toBeVisible({ timeout: 10_000 })
    await expectNoPageCrash(page, label)
    expectCleanDiagnostics(diagnostics, label)
    return
  }
  if (state === 'unauthorized') {
    await expect(page.getByText(/دسترسی|مجاز|پیدا نشد|بازیابی/i).first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(route.readyText, { exact: false }).first()).toHaveCount(0)
    expectCleanDiagnostics(diagnostics, label)
    return
  }
  if (state === 'full' || state === 'long-persian' || state === 'unbroken' || state === 'ltr') {
    const observation = observationForState(route.id, state)
    if (!observation) {
      throw new Error(`${route.id}:${state} is applicable but has no distinct observation`)
    }
    if (route.id === 'admin-messages') {
      await page.locator('[data-test="message-mode-chat"]').click()
    }
    await expect(page.getByText(observation, { exact: false }).first()).toBeVisible({ timeout: 10_000 })
  }
  if (state === 'stale') {
    await expect(page.getByText(/۱۴۰۲|2024|قدیمی|تازه‌سازی|به‌روزرسانی/i).first()).toBeVisible({ timeout: 10_000 })
  }

  await runSettledContract(page, route, diagnostics, label, state, controller)
}

const GEOMETRY_STATES = new Set<StateId>(['initial', 'normal'])
const STATE_VIEWPORTS = SENSITIVE_VIEWPORTS

test.describe('Native App V2 matrix coverage', () => {
  test('29 routes and every N/A cell have a product reason', () => {
    expect(ROUTE_DESCRIPTORS).toHaveLength(29)
    const nas = naCells(VIEWPORTS)
    expect(nas.length).toBeGreaterThan(0)
    for (const cell of nas) {
      expect(cell.naCode, cell.id).toBeTruthy()
      expect(cell.naReason, cell.id).toBeTruthy()
    }
  })
})

test.describe('Native App V2 Chromium geometry matrix', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  for (const viewport of VIEWPORTS) {
    for (const route of ROUTE_DESCRIPTORS) {
      for (const state of GEOMETRY_STATES) {
        if (!route.states[state].applicable) continue
        test(`v2:${route.id}:${viewport.label}:${state}`, async ({ page, browserName }) => {
          test.skip(browserName !== 'chromium', 'full geometry matrix is Chromium')
          test.setTimeout(45_000)
          const { diagnostics, controller, diagnosticContext } = await preparePage(page, route, state)
          await page.setViewportSize({ width: viewport.width, height: viewport.height })
          await runCell(page, route, state, controller, diagnostics, `v2:${route.id}:${viewport.label}:${state}`, diagnosticContext)
        })
      }
    }
  }
})

test.describe('Native App V2 Chromium state matrix', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  for (const viewport of STATE_VIEWPORTS) {
    for (const route of ROUTE_DESCRIPTORS) {
      for (const state of Object.keys(route.states) as StateId[]) {
        if (GEOMETRY_STATES.has(state)) continue
        if (!route.states[state].applicable) continue
        test(`v2:${route.id}:${viewport.label}:${state}`, async ({ page, browserName }) => {
          test.skip(browserName !== 'chromium', 'state matrix is Chromium; Firefox/WebKit cover sensitive families')
          test.setTimeout(60_000)
          const { diagnostics, controller, diagnosticContext } = await preparePage(page, route, state)
          await page.setViewportSize({ width: viewport.width, height: viewport.height })
          await runCell(page, route, state, controller, diagnostics, `v2:${route.id}:${viewport.label}:${state}`, diagnosticContext)
        })
      }
    }
  }
})

test.describe('Native App V2 Firefox/WebKit sensitive families', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  for (const viewport of SENSITIVE_VIEWPORTS) {
    for (const route of ROUTE_DESCRIPTORS.filter((item) => SENSITIVE_FAMILIES.has(item.family))) {
      test(`v2:${route.id}:${viewport.label}:normal`, async ({ page, browserName }) => {
        test.skip(browserName === 'chromium', 'Chromium already covers the full matrix')
        test.setTimeout(45_000)
        const { diagnostics, controller, diagnosticContext } = await preparePage(page, route, 'normal')
        await page.setViewportSize({ width: viewport.width, height: viewport.height })
        await runCell(page, route, 'normal', controller, diagnostics, `${browserName}:v2:${route.id}:${viewport.label}:normal`, diagnosticContext)
      })
    }
  }
})

test.describe('Native App V2 keyboard, zoom, motion, overlays', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  test('login stays keyboard-usable and hides developer shortcut', async ({ page }) => {
    const { diagnostics, diagnosticContext } = await preparePage(page, ROUTE_DESCRIPTORS.find((item) => item.id === 'login')!, 'normal')
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/login', diagnosticContext)
    const route = ROUTE_DESCRIPTORS.find((item) => item.id === 'login')!
    await expectRouteReady(page, route)
    await expectRouteContract(page, route, 'login-keyboard')
    const mobile = page.getByLabel('شماره موبایل')
    await mobile.focus()
    await expect(mobile).toBeFocused()
    await page.keyboard.type('09120000000')
    await page.keyboard.press('Tab')
    const submit = page.getByRole('button', { name: 'دریافت کد تأیید' })
    await expect(page.getByRole('button', { name: 'ورود سریع ۱ ساله' })).toHaveCount(0)
    await expect(submit).toBeFocused()
    expectCleanDiagnostics(diagnostics, 'login-keyboard')
  })

  for (const form of KEYBOARD_FORM_ROUTES) {
    test(`soft-keyboard:${form.id}`, async ({ page }) => {
      const route = ROUTE_DESCRIPTORS.find((item) => item.path === form.path)!
      const { diagnostics, diagnosticContext } = await preparePage(page, route, 'normal')
      await page.setViewportSize({ width: 390, height: 844 })
      await gotoRouteWithNavigationRetry(page, form.path, diagnosticContext)
      await expectRouteReady(page, route)
      if ('openName' in form && form.openName) {
        await page.getByRole('button', { name: form.openName }).first().click()
      }
      if ('tabName' in form && form.tabName) {
        await page.getByRole('tab', { name: form.tabName }).click()
      }
      if (form.field) {
        const field = page.getByLabel(form.field).first()
        await field.focus()
        if ('typeIntoField' in form && form.typeIntoField) {
          await field.fill(form.typeIntoField)
        }
      }
      if ('confirmField' in form && form.confirmField && 'confirmValue' in form && form.confirmValue) {
        await page.getByLabel(form.confirmField).first().fill(form.confirmValue)
      }
      const keyboard = await simulateSoftKeyboard(page, 336)
      expect(keyboard.after.visual).toBeLessThan(keyboard.before.visual)
      expect(keyboard.after.inner).toBeLessThan(keyboard.before.inner)
      const submit = page.getByRole('button', { name: form.submit }).first()
      await expect(submit).toBeVisible()
      await submit.focus()
      await expect(submit).toBeFocused()
      const geometry = await submit.evaluate((element) => {
        const rect = element.getBoundingClientRect()
        const visual = window.visualViewport?.height ?? window.innerHeight
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
        return {
          bottom: rect.bottom,
          visual,
          hit: Boolean(hit && (hit === element || element.contains(hit))),
        }
      })
      expect(geometry.bottom).toBeLessThanOrEqual(geometry.visual + 1)
      expect(geometry.hit).toBe(true)
      await page.setViewportSize(keyboard.restore)
      expectCleanDiagnostics(diagnostics, `soft-keyboard:${form.id}`)
    })
  }

  test('safe-area:login', async ({ page }) => {
    test.setTimeout(45_000)
    const loginRoute = ROUTE_DESCRIPTORS.find((item) => item.id === 'login')!
    const { diagnostics, diagnosticContext } = await preparePage(page, loginRoute, 'normal')
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/login', diagnosticContext)
    const shell = page.locator('.auth-shell')
    const content = page.locator('.auth-shell__content')
    const loginCta = page.getByRole('button', { name: 'دریافت کد تأیید' })
    await expect(loginCta).toBeVisible()
    const before = await page.evaluate(() => {
      const shellNode = document.querySelector('.auth-shell') as HTMLElement | null
      const contentNode = document.querySelector('.auth-shell__content') as HTMLElement | null
      return {
        shellPadTop: shellNode ? getComputedStyle(shellNode).paddingTop : '',
        contentPadBottom: contentNode ? getComputedStyle(contentNode).paddingBottom : '',
        tokenTop: getComputedStyle(document.documentElement).getPropertyValue('--ds-safe-area-top').trim(),
      }
    })
    const inset = await applyControlledSafeArea(page)
    if (inset.bottom !== '34px' || inset.top !== '47px') {
      test.info().annotations.push({ type: 'naCode', description: 'safe-area-not-applied' })
      test.info().annotations.push({
        type: 'naReason',
        description: 'مرورگر توکن safe-area مصنوعی را روی ورود اعمال نکرد.',
      })
      test.skip(true, 'synthetic safe-area tokens were not applied on login')
    }
    const after = await page.evaluate(() => {
      const shellNode = document.querySelector('.auth-shell') as HTMLElement | null
      const contentNode = document.querySelector('.auth-shell__content') as HTMLElement | null
      const cta = document.querySelector('button')
      const buttons = Array.from(document.querySelectorAll('button')).filter((node) => node.textContent?.includes('دریافت کد تأیید'))
      const target = buttons[0] || cta
      const rect = target?.getBoundingClientRect()
      const hit = rect
        ? document.elementFromPoint(rect.left + Math.min(rect.width / 2, 12), rect.top + Math.min(rect.height / 2, 12))
        : null
      return {
        shellPadTop: shellNode ? getComputedStyle(shellNode).paddingTop : '',
        contentPadBottom: contentNode ? getComputedStyle(contentNode).paddingBottom : '',
        tokenTop: getComputedStyle(document.documentElement).getPropertyValue('--ds-safe-area-top').trim(),
        tokenBottom: getComputedStyle(document.documentElement).getPropertyValue('--ds-safe-area-bottom').trim(),
        bottom: rect?.bottom ?? 0,
        overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        hit: Boolean(target && hit && (hit === target || target.contains(hit))),
      }
    })
    expect(Number.parseFloat(before.shellPadTop)).toBeLessThan(Number.parseFloat(after.shellPadTop))
    expect(Number.parseFloat(after.shellPadTop)).toBeGreaterThanOrEqual(47)
    expect(Number.parseFloat(after.contentPadBottom)).toBeGreaterThanOrEqual(58)
    expect(after.tokenTop).toBe('47px')
    expect(after.tokenBottom).toBe('34px')
    expect(after.bottom).toBeLessThanOrEqual(844 - 33)
    expect(after.hit).toBe(true)
    expect(after.overflowX).toBe(false)
    await loginCta.focus()
    await expect(loginCta).toBeFocused()
    await expect(shell).toBeVisible()
    await expect(content).toBeVisible()
    expectCleanDiagnostics(diagnostics, 'safe-area:login')
  })

  test('safe-area:messenger', async ({ page }) => {
    test.setTimeout(45_000)
    const messengerRoute = ROUTE_DESCRIPTORS.find((item) => item.id === 'messenger')!
    const { diagnostics, diagnosticContext } = await preparePage(page, messengerRoute, 'normal')
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/chat?user_id=33&user_name=گفتگوی نمونه', diagnosticContext)
    await expect(page.locator('.messenger-page')).toBeVisible({ timeout: 20_000 })
    const attach = page.getByRole('button', { name: 'افزودن پیوست' })
    const textarea = page.getByLabel('متن پیام')
    await expect(attach).toBeVisible({ timeout: 15_000 })
    await expect(textarea).toBeVisible()
    const composer = page.locator('.messenger-page .input-area')
    const beforePad = await composer.evaluate((node) => getComputedStyle(node).paddingBottom)
    const inset = await applyControlledSafeArea(page)
    if (inset.bottom !== '34px' || inset.top !== '47px') {
      test.info().annotations.push({ type: 'naCode', description: 'safe-area-not-applied' })
      test.info().annotations.push({
        type: 'naReason',
        description: 'مرورگر توکن safe-area مصنوعی را روی پیام‌رسان اعمال نکرد.',
      })
      test.skip(true, 'synthetic safe-area tokens were not applied on messenger')
    }
    await expect.poll(async () => {
      return Number.parseFloat(await composer.evaluate((node) => getComputedStyle(node).paddingBottom))
    }, { timeout: 3_000 }).toBeGreaterThanOrEqual(46)
    const after = await page.evaluate(() => {
      const composer = document.querySelector('.messenger-page .input-area') as HTMLElement | null
      const attachBtn = Array.from(document.querySelectorAll('button')).find((node) => node.getAttribute('aria-label') === 'افزودن پیوست')
      const field = document.querySelector('textarea, [aria-label="متن پیام"]') as HTMLElement | null
      const attachRect = attachBtn?.getBoundingClientRect()
      const fieldRect = field?.getBoundingClientRect()
      const attachHit = attachRect
        ? document.elementFromPoint(attachRect.left + Math.min(attachRect.width / 2, 12), attachRect.top + Math.min(attachRect.height / 2, 12))
        : null
      return {
        composerPad: composer ? getComputedStyle(composer).paddingBottom : '',
        attachBottom: attachRect?.bottom ?? 0,
        fieldBottom: fieldRect?.bottom ?? 0,
        overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        attachHit: Boolean(attachBtn && attachHit && (attachHit === attachBtn || attachBtn.contains(attachHit))),
      }
    })
    expect(Number.parseFloat(beforePad)).toBeLessThan(Number.parseFloat(after.composerPad))
    expect(Number.parseFloat(after.composerPad)).toBeGreaterThanOrEqual(46)
    expect(after.attachBottom).toBeLessThanOrEqual(844 - 33)
    expect(after.fieldBottom).toBeLessThanOrEqual(844 - 33)
    expect(after.attachHit).toBe(true)
    expect(after.overflowX).toBe(false)
    await attach.focus()
    await expect(attach).toBeFocused()
    await textarea.focus()
    await expect(textarea).toBeFocused()
    expectCleanDiagnostics(diagnostics, 'safe-area:messenger')
  })

  for (const path of ZOOM_FAMILY_REPRESENTATIVES) {
    test(`zoom-200:${path}`, async ({ page, browserName }) => {
      const route = ROUTE_DESCRIPTORS.find((item) => item.path === path)!
      const { diagnostics, diagnosticContext } = await preparePage(page, route, 'normal')
      await page.setViewportSize({ width: 390, height: 844 })
      await gotoRouteWithNavigationRetry(page, path, diagnosticContext)
      await expectRouteReady(page, route)
      const zoom = await applyMeasurableZoom(page, browserName)
      if (zoom.method === 'none') {
        test.info().annotations.push({ type: 'naCode', description: 'browser-has-no-measurable-page-scale-api' })
        test.info().annotations.push({
          type: 'naReason',
          description: 'این مرورگر زوم ۲۰۰٪ را اعمال و اندازه‌گیری نکرد.',
        })
        test.skip(true, 'zoom was not applied or measured')
      }
      if (zoom.method === 'cdp-page-scale') expect(zoom.scale).toBeCloseTo(2, 1)
      if (zoom.method === 'css-zoom') {
        expect(zoom.applied === '2' || zoom.applied === '2.0').toBe(true)
      }
      await expectRouteContract(page, route, `zoom-200:${path}`)
      expectCleanDiagnostics(diagnostics, `zoom-200:${path}`)
    })
  }

  test('home sheet Escape restores focus and keeps one live surface', async ({ page }) => {
    const route = ROUTE_DESCRIPTORS.find((item) => item.id === 'home')!
    const { diagnostics, diagnosticContext } = await preparePage(page, route, 'normal')
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/', diagnosticContext)
    await expect(page.getByRole('heading', { name: 'خانه' })).toBeVisible()
    const accountTrigger = page.getByLabel(/باز کردن منوی حساب/)
    await accountTrigger.click()
    await expect(page.getByRole('dialog', { name: 'حساب' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'حساب' })).toHaveCount(0)
    await expect(accountTrigger).toBeFocused()
    await expectRouteContract(page, route, 'home-sheet')
    expectCleanDiagnostics(diagnostics, 'home-sheet')
  })

  test('operations and profile keep phone, address, and relation names', async ({ page }) => {
    const route = ROUTE_DESCRIPTORS.find((item) => item.id === 'profile')!
    const { diagnostics, diagnosticContext } = await preparePage(page, route, 'normal')
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRouteWithNavigationRetry(page, '/operations/customers', diagnosticContext)
    await settleAuthenticatedShell(diagnosticContext)
    await expect(page.getByText(CUSTOMER_RELATION.management_name)).toBeVisible()
    await gotoRouteWithNavigationRetry(page, '/operations/accountants', diagnosticContext)
    await settleAuthenticatedShell(diagnosticContext)
    await expect(page.getByText(ACCOUNTANT_RELATION.relation_display_name)).toBeVisible()
    await gotoRouteWithNavigationRetry(page, '/profile', diagnosticContext)
    await settleAuthenticatedShell(diagnosticContext)
    await expect(page.getByText('09120000000')).toBeVisible()
    await expect(page.getByText('تهران')).toBeVisible()
    expectCleanDiagnostics(diagnostics, 'operations-profile-copy')
  })
})

if (process.env.NATIVE_APP_V2_VISUAL === '1') {
  const visualDir = process.env.NATIVE_APP_V2_VISUAL_DIR || '/tmp/native-app-v2-visual'
  test.describe('Native App V2 visual contact sheet', () => {
    test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })
    const shotViewports = [
      ...VIEWPORTS.filter((item) => item.width === 390 || item.width === 1440),
      ...VIEWPORTS.filter((item) => item.width === 360 || item.width === 430 || item.width === 768),
    ]
    for (const viewport of shotViewports) {
      for (const route of ROUTE_DESCRIPTORS) {
        const sensitiveExtra = viewport.width === 360 || viewport.width === 430 || viewport.width === 768
        if (sensitiveExtra && !SENSITIVE_FAMILIES.has(route.family) && route.family !== 'account' && route.family !== 'home') continue
        test(`shot:${route.id}:${viewport.label}:normal`, async ({ page, browserName }) => {
          test.skip(browserName !== 'chromium')
          const { diagnostics, diagnosticContext, controller } = await preparePage(page, route, 'normal')
          await page.setViewportSize({ width: viewport.width, height: viewport.height })
          await gotoRouteWithNavigationRetry(page, route.path, diagnosticContext)
          await expectRouteReady(page, route)
          await waitForVisualStability(page, { controller })
          await page.screenshot({
            path: `${visualDir}/${route.id}-${viewport.label}-normal.png`,
            fullPage: true,
          })
          expectCleanDiagnostics(diagnostics, `shot:${route.id}:${viewport.label}`)
        })
      }
    }
  })
}
