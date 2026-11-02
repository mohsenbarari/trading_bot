import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { disablePwaRegistration, primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface AuthTokens {
  access_token: string
  refresh_token: string
}

async function fetchDevLoginTokens(request: APIRequestContext): Promise<AuthTokens> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/auth/dev-login`, {
    headers: {
      'Content-Type': 'application/json',
    },
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<AuthTokens>
}

async function setAuthTokens(page: Page, tokens: AuthTokens) {
  await primeAuthSession(page, tokens.access_token, tokens.refresh_token)
}

function selfProfileDisplayName(profile: { customer_management_name?: unknown; account_name?: unknown }): string {
  const managementName = typeof profile.customer_management_name === 'string'
    ? profile.customer_management_name.trim() : ''
  const accountName = typeof profile.account_name === 'string' ? profile.account_name.trim() : ''
  return managementName || accountName
}

test.describe('Login/auth regressions', () => {
  test('unauthenticated protected routes redirect to the login page', async ({ page }) => {
    await disablePwaRegistration(page)
    await page.goto('/profile')

    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('heading', { name: 'ورود به سامانه' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'دریافت کد تأیید' })).toBeVisible()
  })

  test('developer quick login reaches the dashboard and stores tokens', async ({ page }) => {
    await disablePwaRegistration(page)
    await page.goto('/login')
    await page.getByRole('button', { name: 'ورود سریع ۱ ساله' }).click()

    await page.waitForURL('**/')
    await expect(page.getByText('ورود به بازار')).toBeVisible()

    const tokens = await page.evaluate(() => ({
      authToken: localStorage.getItem('auth_token'),
      refreshToken: localStorage.getItem('refresh_token'),
    }))

    expect(tokens.authToken).toBeTruthy()
    expect(tokens.refreshToken).toBeTruthy()
  })

  test('an existing authenticated session bypasses /login and keeps protected routes open', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    await setAuthTokens(page, tokens)

    await page.goto('/login')
    await page.waitForURL('**/')
    await expect(page.getByText('ورود به بازار')).toBeVisible()

    const [currentUserResponse, selfProfileResponse] = await Promise.all([
      page.waitForResponse(response => response.request().method() === 'GET'
        && new URL(response.url()).pathname === '/api/auth/me'),
      page.waitForResponse(response => response.request().method() === 'GET'
        && /^\/api\/users-public\/[1-9]\d*$/.test(new URL(response.url()).pathname)),
      page.goto('/profile'),
    ])
    await expect(page).toHaveURL(/\/profile$/)
    expect(currentUserResponse.ok()).toBeTruthy()
    const currentUser = await currentUserResponse.json() as { id?: unknown }
    const currentUserId = Number(currentUser.id)
    expect(Number.isInteger(currentUserId)).toBeTruthy()
    expect(currentUserId).toBeGreaterThan(0)
    expect(new URL(selfProfileResponse.url()).pathname).toBe(`/api/users-public/${currentUserId}`)
    expect(selfProfileResponse.ok()).toBeTruthy()
    const selfProfile = await selfProfileResponse.json() as {
      customer_management_name?: unknown
      account_name?: unknown
    }
    const expectedProfileName = selfProfileDisplayName(selfProfile)
    expect(expectedProfileName).not.toBe('')
    const profileHeading = page.locator('[data-test="profile-identity-header"]').getByRole('heading', { level: 1 })
    await expect(profileHeading).toBeVisible()
    await expect(profileHeading).toHaveAttribute('title', expectedProfileName)
    await expect(profileHeading).toContainText(expectedProfileName)
    await page.goto('/account/security')
    await expect(page).toHaveURL(/\/account\/security$/)
    await expect(page.getByRole('heading', { name: 'امنیت حساب' }).first()).toBeVisible()
  })
})
