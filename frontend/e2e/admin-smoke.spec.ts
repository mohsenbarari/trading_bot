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
  await page.evaluate(
    ({ accessToken, refreshToken }) => {
      localStorage.setItem('auth_token', accessToken)
      localStorage.setItem('refresh_token', refreshToken)
      localStorage.removeItem('suspended_refresh_token')
    },
    {
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
    },
  )
}

async function openAdmin(page: Page) {
  await page.goto('/admin', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: 'مرکز مدیریت' })).toBeVisible({ timeout: 30000 })
}

test.describe('Admin smoke regressions', () => {
  test('admin can open the invitation management panel', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)

    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page
      .locator('button:visible')
      .filter({ hasText: /ارسال لینک دعوت/ })
      .first()
      .click()
    const invitationManager = page.locator('.invitation-manager:visible')
    await expect(invitationManager).toHaveCount(1)
    await expect(invitationManager).toBeVisible()
    const accountNameInput = invitationManager.locator('#account_name')
    const mobileInput = invitationManager.locator('#mobile_number')
    const roleSelect = invitationManager.locator('#role')
    await expect(accountNameInput).toBeVisible()
    await expect(mobileInput).toBeVisible()
    await expect(roleSelect).toBeVisible()
    await expect(
      invitationManager.getByRole('region', { name: 'دعوت‌نامه‌های در انتظار', exact: true }),
    ).toBeVisible()
  })

  test('admin can open the user management panel', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page
      .locator('button:visible')
      .filter({ hasText: /مدیریت کاربران/ })
      .first()
      .click()
    const userDirectorySearch = page.locator('.user-search-form:visible').first()
    await expect(userDirectorySearch).toBeVisible()
    await expect(userDirectorySearch.locator('#user-directory-search')).toBeVisible()
    await expect(page.locator('.users-list:visible').first()).toBeVisible()
  })

  test('admin can open the system settings panel', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page
      .locator('button:visible')
      .filter({ hasText: /تنظیمات سیستم/ })
      .first()
      .click()
    const invitationAccordionHeader = page
      .locator('#trading-settings-invitation-header:visible')
      .first()
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
    await page
      .locator('button:visible')
      .filter({ hasText: /ساخت کانال/ })
      .first()
      .click()
    const channelShell = page.locator('.channel-admin-shell:visible').last()
    await expect(channelShell).toBeVisible()
    await expect(
      channelShell.locator('.ui-inset-group__title').filter({ hasText: /^ساخت کانال جدید$/ }),
    ).toBeVisible()
    await expect(channelShell.getByRole('button', { name: /کانال جدید/ }).first()).toBeVisible()
  })
})
