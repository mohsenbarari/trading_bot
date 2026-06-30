import { type Page } from '@playwright/test'

interface PrimeAuthSessionOptions {
  currentUserSummary?: Record<string, unknown>
}

function createPrimeAuthPayloadId() {
  return `pw-prime-auth:${Date.now()}:${Math.random().toString(36).slice(2)}`
}

export async function primeAuthSession(
  page: Page,
  accessToken: string,
  refreshToken: string,
  options: PrimeAuthSessionOptions = {},
) {
  const payloadId = createPrimeAuthPayloadId()

  await disablePwaRegistration(page)

  await page.addInitScript(({ nextAccessToken, nextRefreshToken, nextCurrentUserSummary, nextPayloadId }) => {
    const marker = `|${nextPayloadId}|`
    if (window.name.includes(marker)) {
      return
    }

    localStorage.setItem('auth_token', nextAccessToken)
    localStorage.setItem('refresh_token', nextRefreshToken)
    if (nextCurrentUserSummary) {
      localStorage.setItem('current_user_summary', JSON.stringify(nextCurrentUserSummary))
    }
    localStorage.removeItem('suspended_refresh_token')

    window.name = `${window.name}${marker}`
  }, {
    nextAccessToken: accessToken,
    nextRefreshToken: refreshToken,
    nextCurrentUserSummary: options.currentUserSummary ?? null,
    nextPayloadId: payloadId,
  })
}

export async function disablePwaRegistration(page: Page) {
  await page.addInitScript(() => {
    ;(window as any).__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__ = true
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations()
        .then((registrations) => Promise.all(registrations.map((registration) => registration.unregister())))
        .catch(() => {})
    }
  })
}
