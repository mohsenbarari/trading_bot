import { expect, test } from '@playwright/test'
import {
  attachDiagnostics,
  createDiagnosticContext,
  createDiagnostics,
  expectCleanDiagnostics,
  allowExpectedOfflineRequestFailed,
  allowHarnessHoldAbort,
  installFailClosedApi,
  isAllowedMutation,
  resolveKnownApi,
  pathEquals,
  TERMINAL_LIST_HTTP_STATUS,
  withControlledNavigation,
} from './helpers/nativeAppV2Api'
import { AUTH_SHELL_OFFLINE_GETS, authOfflineGetPaths } from './helpers/nativeAppV2Matrix'

test.describe('Native App V2 fail-closed harness contract', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  test('unknown API is recorded and fails the clean-diagnostics contract', async ({ page }) => {
    const diagnostics = createDiagnostics()
    const context = createDiagnosticContext()
    await attachDiagnostics(page, diagnostics, {}, context)
    await installFailClosedApi(page, diagnostics)
    await withControlledNavigation(context, async () => {
      await page.goto('/login', { waitUntil: 'domcontentloaded' })
      await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })
    })

    const status = await page.evaluate(async () => {
      const response = await fetch('/api/this-endpoint-does-not-exist-native-v2')
      return response.status
    })

    expect(status).toBe(599)
    expect(diagnostics.unknownApis).toContain('GET /api/this-endpoint-does-not-exist-native-v2')
    expect(() => expectCleanDiagnostics(diagnostics, 'unknown-api-self-test')).toThrow(/unknown API/)
  })

  test('unexpected mutation is recorded, answered 405, and fails the contract', async ({ page }) => {
    const diagnostics = createDiagnostics()
    const context = createDiagnosticContext()
    await attachDiagnostics(page, diagnostics, {}, context)
    await installFailClosedApi(page, diagnostics)
    await withControlledNavigation(context, async () => {
      await page.goto('/login', { waitUntil: 'domcontentloaded' })
      await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })
    })

    const status = await page.evaluate(async () => {
      const response = await fetch('/api/users/9001', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
      })
      return response.status
    })

    expect(status).toBe(405)
    expect(diagnostics.unexpectedMutations).toContain('DELETE /api/users/9001')
    expect(() => expectCleanDiagnostics(diagnostics, 'unexpected-mutation-self-test')).toThrow(/unexpected mutation/)
  })

  test('unauthorized external request is recorded and fails the contract', async ({ page }) => {
    const diagnostics = createDiagnostics()
    const context = createDiagnosticContext()
    await attachDiagnostics(page, diagnostics, {}, context)
    await installFailClosedApi(page, diagnostics)
    await withControlledNavigation(context, async () => {
      await page.goto('/login', { waitUntil: 'domcontentloaded' })
      await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })
    })

    await page.evaluate(() => {
      void fetch('https://example.invalid/native-v2-probe')
    })
    await expect.poll(() => diagnostics.externalRequests.length).toBeGreaterThan(0)
    expect(diagnostics.externalRequests.some((item) => item.includes('example.invalid'))).toBe(true)
    expect(() => expectCleanDiagnostics(diagnostics, 'external-request-self-test')).toThrow(/external request/)
  })

  test('helper remains fail-closed for unknown paths and mutations without a page', () => {
    expect(resolveKnownApi('GET', '/api/this-endpoint-does-not-exist-native-v2')).toBeNull()
    expect(isAllowedMutation('/api/users/9001', 'DELETE')).toBe(false)
    expect(isAllowedMutation('/api/sessions/verify', 'POST')).toBe(true)
    expect(isAllowedMutation('/api/chat/activity', 'POST')).toBe(true)
  })

  test('error mode fails list resources with terminal 422 and keeps identity alive', () => {
    expect(resolveKnownApi('GET', '/api/auth/me', 'error')?.status).toBe(200)
    expect(resolveKnownApi('GET', '/api/trades/my/page', 'error')?.status).toBe(TERMINAL_LIST_HTTP_STATUS)
    expect(resolveKnownApi('GET', '/api/chat/channels', 'error')?.status).toBe(TERMINAL_LIST_HTTP_STATUS)
    expect(resolveKnownApi('GET', '/api/chat/conversations', 'error')?.status).toBe(TERMINAL_LIST_HTTP_STATUS)
    expect(resolveKnownApi('GET', '/api/commodities', 'error')?.status).toBe(TERMINAL_LIST_HTTP_STATUS)
    expect(pathEquals('/api/chat/channels/', '/api/chat/channels')).toBe(true)
    expect(pathEquals('/api/chat/channels/21', '/api/chat/channels')).toBe(false)
  })

  test('long-copy mode stamps the viewer account name on identity and admin profile', () => {
    const me = resolveKnownApi('GET', '/api/auth/me', 'long-copy')?.body as { account_name: string }
    const admin = resolveKnownApi('GET', '/api/users/9001', 'long-copy')?.body as { account_name: string; mobile_number: string }
    expect(me.account_name).toBe('unbroken_ltr_accountnamewithoutspaces_9001')
    expect(admin.account_name).toBe('unbroken_ltr_accountnamewithoutspaces_9001')
    expect(admin.mobile_number).toBe('09120000000')
  })

  test('copy modes stamp distinct observable fixture fields', () => {
    const longName = resolveKnownApi('GET', '/api/auth/me', 'long-persian')?.body as { full_name: string; account_name: string }
    const unbroken = resolveKnownApi('GET', '/api/auth/me', 'unbroken')?.body as { account_name: string }
    const ltr = resolveKnownApi('GET', '/api/auth/me', 'ltr')?.body as { account_name: string }
    const full = resolveKnownApi('GET', '/api/chat/conversations', 'full')?.body as Array<{ other_user_name: string }>
    expect(longName.full_name).toContain('بسیار بلند فارسی')
    expect(longName.account_name).toBe('native_app_v2_user')
    expect(unbroken.account_name).toBe('unbroken_ltr_accountnamewithoutspaces_9001')
    expect(ltr.account_name).toBe('ltr_account_9001')
    expect(full).toHaveLength(2)
    expect(full[1]?.other_user_name).toBe('گفتگوی دوم پذیرش')
  })

  test('offline request-failed allow-list is descriptor GET/HEAD only', () => {
    const allowed = ['/api/chat/conversations']
    expect(allowExpectedOfflineRequestFailed('GET /api/chat/conversations net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(true)
    expect(allowExpectedOfflineRequestFailed('HEAD /api/chat/conversations net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(true)
    expect(allowExpectedOfflineRequestFailed('GET /api/users net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(false)
    expect(allowExpectedOfflineRequestFailed('GET /api/auth/me net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(false)
    expect(allowExpectedOfflineRequestFailed('GET /assets/index.js net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(false)
    expect(allowExpectedOfflineRequestFailed('POST /api/chat/conversations net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(false)
    expect(allowExpectedOfflineRequestFailed('PATCH /api/users/9001 net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(false)
    expect(allowExpectedOfflineRequestFailed('DELETE /api/users/9001 net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(false)
    expect(allowExpectedOfflineRequestFailed('GET /api/chat/conversations some-other-error', allowed)).toBe(false)
    expect(AUTH_SHELL_OFFLINE_GETS).toEqual(expect.arrayContaining([
      '/api/chat/poll',
      '/api/notifications/unread-count',
      '/api/notifications/push/public-key',
      '/api/sessions/login-requests/pending',
      '/api/sessions/recovery/pending',
      '/api/trading-settings/market-state',
      '/api/trades/overtime-requests/pending-owner',
      '/api/trades/overtime-requests/pending-requester',
    ]))
    expect(AUTH_SHELL_OFFLINE_GETS.some((path) => path === '/api/*' || path.includes('*'))).toBe(false)
    const homeOffline = authOfflineGetPaths('/api/trades/my/page', ['/api/sessions/active'])
    expect(allowExpectedOfflineRequestFailed('GET /api/trading-settings/market-state net::ERR_INTERNET_DISCONNECTED', homeOffline)).toBe(true)
    expect(allowExpectedOfflineRequestFailed('GET /api/users net::ERR_INTERNET_DISCONNECTED', homeOffline)).toBe(false)
    expect(allowHarnessHoldAbort('GET /api/trades/my/page net::ERR_ABORTED', '/api/trades/my/page')).toBe(true)
    expect(allowHarnessHoldAbort('GET /api/users net::ERR_ABORTED', '/api/trades/my/page')).toBe(false)
    expect(allowHarnessHoldAbort('POST /api/trades/my/page net::ERR_ABORTED', '/api/trades/my/page')).toBe(false)
  })

  test('offline classifies mutations before abort and records unexpected writes', async ({ page }) => {
    const diagnostics = createDiagnostics()
    const context = createDiagnosticContext()
    const allowed = ['/api/chat/conversations']
    await attachDiagnostics(page, diagnostics, {
      allowRequestFailed: (text) => allowExpectedOfflineRequestFailed(text, allowed),
    }, context)
    const controller = await installFailClosedApi(page, diagnostics, {
      expectedOfflineGetPaths: allowed,
    })
    await withControlledNavigation(context, async () => {
      await page.goto('/login', { waitUntil: 'domcontentloaded' })
      await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })
    })
    controller.setNetworkOffline(true)

    const result = await page.evaluate(async () => {
      const expectedGet = await fetch('/api/chat/conversations').then((response) => response.status).catch((error) => String(error))
      const unrelatedGet = await fetch('/api/users').then((response) => response.status).catch((error) => String(error))
      const asset = await fetch('/assets/native-v2-offline-probe.js').then((response) => response.status).catch((error) => String(error))
      const post = await fetch('/api/users/9001', { method: 'POST' }).then((response) => response.status)
      const patch = await fetch('/api/users/9001', { method: 'PATCH' }).then((response) => response.status)
      const del = await fetch('/api/users/9001', { method: 'DELETE' }).then((response) => response.status)
      return { expectedGet, unrelatedGet, asset, post, patch, del }
    })

    expect(result.expectedGet).not.toBe(200)
    expect(result.post).toBe(405)
    expect(result.patch).toBe(405)
    expect(result.del).toBe(405)
    expect(diagnostics.unexpectedMutations).toEqual(expect.arrayContaining([
      'POST /api/users/9001',
      'PATCH /api/users/9001',
      'DELETE /api/users/9001',
    ]))
    expect(allowExpectedOfflineRequestFailed('GET /api/chat/conversations net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(true)
    expect(allowExpectedOfflineRequestFailed('GET /api/users net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(false)
    expect(allowExpectedOfflineRequestFailed('GET /assets/native-v2-offline-probe.js net::ERR_INTERNET_DISCONNECTED', allowed)).toBe(false)
    expect(() => expectCleanDiagnostics(diagnostics, 'offline-mutation-self-test')).toThrow(/unexpected mutation/)
  })

  test('aborted ordinary API GET is not environmental', async ({ page }) => {
    const diagnostics = createDiagnostics()
    const context = createDiagnosticContext()
    await attachDiagnostics(page, diagnostics, {}, context)
    await installFailClosedApi(page, diagnostics)
    await withControlledNavigation(context, async () => {
      await page.goto('/login', { waitUntil: 'domcontentloaded' })
      await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })
    })

    await page.route('**/api/chat/conversations', async (route) => {
      await route.abort('aborted')
    })
    await page.evaluate(() => fetch('/api/chat/conversations').catch(() => undefined))
    await expect.poll(() => diagnostics.requestFailed.length + diagnostics.environmentalRequestFailed.length).toBeGreaterThan(0)
    expect(diagnostics.environmentalRequestFailed.some((line) => line.includes('/api/chat/conversations'))).toBe(false)
    expect(diagnostics.requestFailed.some((line) => line.includes('/api/chat/conversations'))).toBe(true)
    expect(() => expectCleanDiagnostics(diagnostics, 'aborted-get-self-test')).toThrow(/requestfailed/)
  })
})
