import { expect, test } from '@playwright/test'
import {
  attachDiagnostics,
  createDiagnostics,
  expectCleanDiagnostics,
  allowExpectedOfflineRequestFailed,
  installFailClosedApi,
  isAllowedMutation,
  resolveKnownApi,
  pathEquals,
} from './helpers/nativeAppV2Api'

test.describe('Native App V2 fail-closed harness contract', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  test('unknown API is recorded and fails the clean-diagnostics contract', async ({ page }) => {
    const diagnostics = createDiagnostics()
    await attachDiagnostics(page, diagnostics)
    await installFailClosedApi(page, diagnostics)
    await page.goto('/login', { waitUntil: 'domcontentloaded' })
    await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })

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
    await attachDiagnostics(page, diagnostics)
    await installFailClosedApi(page, diagnostics)
    await page.goto('/login', { waitUntil: 'domcontentloaded' })
    await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })

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
    await attachDiagnostics(page, diagnostics)
    await installFailClosedApi(page, diagnostics)
    await page.goto('/login', { waitUntil: 'domcontentloaded' })
    await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })

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

  test('error mode fails list resources and keeps identity alive', () => {
    expect(resolveKnownApi('GET', '/api/auth/me', 'error')?.status).toBe(200)
    expect(resolveKnownApi('GET', '/api/trades/my/page', 'error')?.status).toBe(425)
    expect(resolveKnownApi('GET', '/api/chat/channels', 'error')?.status).toBe(425)
    expect(resolveKnownApi('GET', '/api/chat/conversations', 'error')?.status).toBe(425)
    expect(resolveKnownApi('GET', '/api/commodities', 'error')?.status).toBe(425)
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

  test('offline request-failed allow-list is path-scoped', () => {
    expect(allowExpectedOfflineRequestFailed('GET /api/chat/conversations net::ERR_INTERNET_DISCONNECTED')).toBe(true)
    expect(allowExpectedOfflineRequestFailed('GET /api/auth/me net::ERR_INTERNET_DISCONNECTED')).toBe(false)
    expect(allowExpectedOfflineRequestFailed('GET /assets/index.js net::ERR_INTERNET_DISCONNECTED')).toBe(false)
    expect(allowExpectedOfflineRequestFailed('GET /api/chat/conversations some-other-error')).toBe(false)
  })
})
