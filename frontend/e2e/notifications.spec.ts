import { expect, test } from '@playwright/test'

async function loginAsDev(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByRole('button', { name: 'ورود سریع ۱ ساله (توسعه‌دهنده)' }).click()
  await page.waitForURL('**/')
}

test.describe('Notification regressions', () => {
  test('dashboard notifications button opens the notification center', async ({ page }) => {
    await loginAsDev(page)

    await page.getByRole('button', { name: 'اعلان‌ها' }).click()

    await expect(page).toHaveURL(/\/notifications$/)
    await expect(page.getByRole('heading', { name: 'مرکز اعلان‌ها' })).toBeVisible()
  })

  test('websocket heartbeat pong does not emit JSON parse errors', async ({ page }) => {
    const consoleErrors: string[] = []

    await page.addInitScript(() => {
      const originalSetInterval = window.setInterval.bind(window)
      window.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
        if (timeout === 25000) {
          return originalSetInterval(handler, 50, ...args)
        }
        return originalSetInterval(handler, timeout, ...args)
      }) as typeof window.setInterval
    })

    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text())
      }
    })

    await loginAsDev(page)
    await page.waitForTimeout(500)

    expect(
      consoleErrors.some((entry) => entry.includes('pong') && entry.includes('not valid JSON')),
    ).toBeFalsy()
  })
})