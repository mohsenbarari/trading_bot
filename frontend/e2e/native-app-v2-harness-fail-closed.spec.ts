import { expect, test } from '@playwright/test'
import {
  attachDiagnostics,
  createDiagnostics,
  expectCleanDiagnostics,
  installFailClosedApi,
  isAllowedMutation,
  resolveKnownApi,
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
})
