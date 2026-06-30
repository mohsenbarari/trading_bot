import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

test.use({ serviceWorkers: 'block' })

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

test.describe('Login/auth regressions', () => {
  test('unauthenticated protected routes redirect to the login page', async ({ page }) => {
    await page.goto('/profile')

    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('heading', { name: 'ورود به بازار' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'دریافت کد تایید' })).toBeVisible()
  })

  test('developer quick login reaches the dashboard and stores tokens', async ({ page }) => {
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

    await page.goto('/profile')
    await expect(page).toHaveURL(/\/profile$/)
    await expect(page.getByRole('heading', { name: 'پروفایل' }).first()).toBeVisible()
    await page.goto('/settings')
    await expect(page).toHaveURL(/\/settings$/)
    await expect(page.getByRole('heading', { name: 'تنظیمات' }).first()).toBeVisible()
  })
})
