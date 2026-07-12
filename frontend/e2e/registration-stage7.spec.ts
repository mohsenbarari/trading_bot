import { expect, test, type Page } from '@playwright/test'

const viewports = [
  { name: 'mobile-390', width: 390, height: 844 },
  { name: 'desktop-1440', width: 1440, height: 1000 },
]

async function expectNoHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }))
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport)
}

for (const viewport of viewports) {
  test(`role-aware invitation and completed-login routing fit ${viewport.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    await page.route('**/api/invitations/lookup/stage7-tier1', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          token: 'CUST-stage7-tier1',
          valid: true,
          state: 'pending',
          kind: 'customer',
          bot_available: true,
          web_available: true,
          expires_at: '2026-07-13T12:00:00Z',
        }),
      })
    })
    await page.route('**/api/config', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ bot_username: 'stage7_test_bot' }),
      })
    })

    await page.goto('/i/stage7-tier1')
    await expect(page.getByText('ثبت‌نام با تلگرام')).toBeVisible()
    await expect(page.getByRole('button', { name: 'ثبت‌نام از طریق وب' })).toBeVisible()
    await expect(page.getByText(/مهلت ثبت‌نام:/)).toBeVisible()
    await expectNoHorizontalOverflow(page)
    await page.screenshot({
      path: `../tmp/stage7-responsive/invitation-${viewport.name}-${testInfo.project.name}.png`,
      fullPage: true,
    })

    await page.unroute('**/api/invitations/lookup/stage7-tier1')
    await page.route('**/api/invitations/lookup/stage7-completed', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          valid: false,
          state: 'completed',
          kind: 'standard',
          bot_available: false,
          web_available: false,
          expires_at: '2026-07-11T12:00:00Z',
        }),
      })
    })

    await page.goto('/i/stage7-completed')
    await expect(page).toHaveURL(/\/login\?registration=complete$/)
    await expect(page.getByText('ثبت‌نام قبلاً تکمیل شده است')).toBeVisible()
    await expect(page.getByLabel('شماره موبایل')).toBeVisible()
    await expectNoHorizontalOverflow(page)
  })

  test(`Telegram-first OTP status fits ${viewport.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    await page.route('**/api/auth/request-otp', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ method: 'telegram', sms_fallback_in: 40 }),
      })
    })

    await page.goto('/login')
    await page.getByLabel('شماره موبایل').fill('09123456789')
    await page.getByRole('button', { name: 'دریافت کد تایید' }).click()
    await expect(page.getByText(/کد ابتدا در تلگرام ارسال شد؛ 00:(39|40) تا ارسال خودکار پیامک/)).toBeVisible()
    await expect(page.getByRole('button', { name: 'ارسال مجدد کد' })).toHaveCount(0)
    await expectNoHorizontalOverflow(page)
    await page.screenshot({
      path: `../tmp/stage7-responsive/otp-${viewport.name}-${testInfo.project.name}.png`,
      fullPage: true,
    })
  })
}

test('Web-only invitation never renders a Telegram action', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/invitations/lookup/stage7-web-only', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        token: 'ACCT-stage7',
        valid: true,
        state: 'pending',
        kind: 'accountant',
        bot_available: false,
        web_available: true,
        expires_at: '2026-07-13T12:00:00Z',
      }),
    })
  })

  await page.goto('/i/stage7-web-only')
  await expect(page.getByText('ثبت‌نام با تلگرام')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'ثبت‌نام از طریق وب' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
})
