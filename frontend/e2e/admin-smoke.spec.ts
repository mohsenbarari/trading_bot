import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface AuthTokens {
  access_token: string
  refresh_token: string
}

async function fetchDevLoginTokens(request: APIRequestContext): Promise<AuthTokens> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/auth/dev-login`, {
    headers: { 'Content-Type': 'application/json' },
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<AuthTokens>
}

async function setAuthTokens(page: Page, tokens: AuthTokens) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
  })
}

async function openAdmin(page: Page) {
  await page.goto('/admin', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: 'پنل مدیریت' })).toBeVisible({ timeout: 30000 })
}

test.describe('Admin smoke regressions', () => {
  test('admin can open the invitation management panel', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)

    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page.locator('button:visible').filter({ hasText: /ارسال لینک دعوت/ }).first().click()
    const accountNameInput = page.locator('#account_name:visible').first()
    const mobileInput = page.locator('#mobile_number:visible').first()
    const roleSelect = page.locator('#role:visible').first()
    await expect(accountNameInput).toBeVisible()
    await expect(mobileInput).toBeVisible()
    await expect(roleSelect).toBeVisible()
    await expect(page.getByRole('region', { name: 'دعوت‌نامه‌های pending' }).first()).toBeVisible()
  })

  test('admin can open the user management panel', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page.locator('button:visible').filter({ hasText: /مدیریت کاربران/ }).first().click()
    await expect(page.locator('.search-toggle-btn:visible').first()).toBeVisible()
    await expect(page.locator('.users-list:visible').first()).toBeVisible()
  })

  test('admin can open the system settings panel', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page.locator('button:visible').filter({ hasText: /تنظیمات سیستم/ }).first().click()
    const invitationAccordionHeader = page.locator('#trading-settings-invitation-header:visible').first()
    await expect(invitationAccordionHeader).toBeVisible()
    await expect(invitationAccordionHeader).toHaveAttribute('aria-expanded', 'false')
  })

  test('admin can open the optional channel manager', async ({ page, request, browserName }) => {
    if (browserName === 'webkit') {
      test.slow()
    }

    const tokens = await fetchDevLoginTokens(request)

    await setAuthTokens(page, tokens)
    await openAdmin(page)
    await page.locator('button:visible').filter({ hasText: /ساخت کانال/ }).first().click()
    await expect(page.locator('.channel-admin-shell:visible').first()).toBeVisible()
    await expect(page.getByRole('heading', { name: 'ساخت کانال جدید' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'کانال جدید' }).first()).toBeVisible()
  })
})
