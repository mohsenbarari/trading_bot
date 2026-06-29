/// <reference types="node" />

import { expect, test, type Page, type Route } from '@playwright/test'

function base64Url(payload: Record<string, unknown>) {
  return Buffer.from(JSON.stringify(payload)).toString('base64url')
}

function fakeJwt() {
  return `${base64Url({ alg: 'none', typ: 'JWT' })}.${base64Url({ exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`
}

async function primeMockAuth(page: Page) {
  const accessToken = fakeJwt()
  await page.addInitScript((token) => {
    localStorage.setItem('auth_token', token)
    localStorage.setItem('refresh_token', 'pw-refresh-token')
    localStorage.setItem('current_user_summary', JSON.stringify({
      id: 77,
      role: 'عادی',
      account_name: 'pw_market_mutation_viewer',
      customer_tier: null,
    }))
    localStorage.removeItem('suspended_refresh_token')
  }, accessToken)
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test.describe('Market mutation UX', () => {
  test('recent expired offers toggle stays clickable above the market FAB on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await primeMockAuth(page)

    await page.route('**/api/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())

      if (url.pathname === '/api/auth/me') {
        return fulfillJson(route, 200, { id: 77, role: 'عادی', account_name: 'pw_market_mutation_viewer', customer_tier: null })
      }
      if (url.pathname === '/api/sessions/verify') {
        return fulfillJson(route, 200, { ok: true })
      }
      if (url.pathname === '/api/sessions/active') {
        return fulfillJson(route, 200, [{ id: 'pw-session', is_current: true, is_primary: true }])
      }
      if (url.pathname === '/api/sessions/login-requests/pending' || url.pathname === '/api/sessions/recovery/pending') {
        return fulfillJson(route, 200, [])
      }
      if (url.pathname === '/api/notifications/preferences') {
        return fulfillJson(route, 200, { market_offer_push_enabled: true })
      }
      if (url.pathname === '/api/commodities/') {
        return fulfillJson(route, 200, [{ id: 1, name: 'سکه' }])
      }
      if (url.pathname === '/api/trading-settings/') {
        return fulfillJson(route, 200, {
          offer_min_quantity: 1,
          offer_max_quantity: 1000,
          lot_min_size: 1,
          lot_max_count: 5,
          offer_expiry_minutes: 60,
        })
      }
      if (url.pathname === '/api/trading-settings/market-state') {
        return fulfillJson(route, 200, {
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (url.pathname === '/api/admin-messages/market/current') {
        return fulfillJson(route, 200, null)
      }
      if (url.pathname === '/api/offers/' && request.method() === 'GET') {
        return fulfillJson(route, 200, [])
      }
      if (url.pathname === '/api/offers/my' && url.searchParams.get('status_filter') === 'expired') {
        return fulfillJson(route, 200, [
          {
            id: 8001,
            user_id: 77,
            user_account_name: 'pw_market_mutation_viewer',
            is_own_offer: true,
            offer_type: 'buy',
            commodity_id: 1,
            commodity_name: 'سکه',
            quantity: 4,
            remaining_quantity: 4,
            price: 50000,
            raw_price: 50000,
            market_published_price: 50000,
            viewer_effective_price: 50000,
            is_wholesale: true,
            lot_sizes: null,
            original_lot_sizes: null,
            notes: 'recent expired',
            status: 'expired',
            expire_reason: 'time_limit',
            expired_at: 'امروز',
            created_at: 'امروز',
            expires_at_ts: null,
            channel_message_id: null,
            customer_badge_visible: false,
            customer_management_name: null,
            customer_tier: null,
          },
        ])
      }

      return fulfillJson(route, 200, null)
    })

    await page.goto('/market', { waitUntil: 'domcontentloaded' })

    await expect(page.locator('.recent-offers-toggle')).toBeVisible()
    await expect(page.locator('.text-offer-input')).toBeVisible()
    await expect(page.locator('.send-btn')).toBeVisible()
    const toggleBox = await page.locator('.recent-offers-toggle').boundingBox()
    const inputBox = await page.locator('.text-offer-input').boundingBox()
    const sendBox = await page.locator('.send-btn').boundingBox()
    expect(toggleBox).not.toBeNull()
    expect(inputBox).not.toBeNull()
    expect(sendBox).not.toBeNull()
    expect(toggleBox!.x - inputBox!.x).toBeGreaterThanOrEqual(0)
    expect(toggleBox!.x - inputBox!.x).toBeLessThanOrEqual(4)
    expect(toggleBox!.x + toggleBox!.width).toBeLessThanOrEqual(inputBox!.x + inputBox!.width)
    expect(Math.abs((sendBox!.x + sendBox!.width) - (inputBox!.x + inputBox!.width))).toBeLessThanOrEqual(4)
    await page.locator('.recent-offers-toggle').click()
    await expect(page.locator('.recent-offers-dropdown')).toHaveCSS('z-index', '1200')
    const dropdownBox = await page.locator('.recent-offers-dropdown').boundingBox()
    expect(dropdownBox).not.toBeNull()
    expect(dropdownBox!.y).toBeGreaterThanOrEqual(0)
    expect(dropdownBox!.y + dropdownBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height)
    await expect(page.locator('.recent-offer-item')).toContainText('سکه')
  })

  test('publish and execute conflict states are bounded, visible, and duplicate-safe', async ({ page }) => {
    await primeMockAuth(page)

    let offerPublishCount = 0
    let tradeExecuteCount = 0
    const offerBodies: Array<Record<string, unknown>> = []
    const tradeBodies: Array<Record<string, unknown>> = []

    await page.route('**/api/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const method = request.method()

      if (url.pathname === '/api/auth/me') {
        return fulfillJson(route, 200, { id: 77, role: 'عادی', account_name: 'pw_market_mutation_viewer', customer_tier: null })
      }
      if (url.pathname === '/api/sessions/verify') {
        return fulfillJson(route, 200, { ok: true })
      }
      if (url.pathname === '/api/sessions/active') {
        return fulfillJson(route, 200, [{ id: 'pw-session', is_current: true, is_primary: true }])
      }
      if (url.pathname === '/api/sessions/login-requests/pending' || url.pathname === '/api/sessions/recovery/pending') {
        return fulfillJson(route, 200, [])
      }
      if (url.pathname === '/api/notifications/preferences') {
        return fulfillJson(route, 200, { market_offer_push_enabled: true })
      }
      if (url.pathname === '/api/commodities/') {
        return fulfillJson(route, 200, [{ id: 1, name: 'سکه' }])
      }
      if (url.pathname === '/api/trading-settings/') {
        return fulfillJson(route, 200, {
          offer_min_quantity: 1,
          offer_max_quantity: 1000,
          lot_min_size: 1,
          lot_max_count: 5,
          offer_expiry_minutes: 60,
        })
      }
      if (url.pathname === '/api/trading-settings/market-state') {
        return fulfillJson(route, 200, {
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (url.pathname === '/api/admin-messages/market/current') {
        return fulfillJson(route, 200, null)
      }
      if (url.pathname === '/api/offers/parse' && method === 'POST') {
        return fulfillJson(route, 200, {
          success: true,
          data: {
            trade_type: 'buy',
            commodity_id: 1,
            commodity_name: 'سکه',
            quantity: 4,
            price: 50000,
            is_wholesale: true,
            lot_sizes: null,
            notes: 'pw mutation publish',
          },
        })
      }
      if (url.pathname === '/api/offers/' && method === 'POST') {
        offerPublishCount += 1
        offerBodies.push(request.postDataJSON() as Record<string, unknown>)
        await new Promise((resolve) => setTimeout(resolve, 150))
        return fulfillJson(route, 409, {
          detail: 'بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید.',
        })
      }
      if (url.pathname === '/api/offers/' && method === 'GET') {
        return fulfillJson(route, 200, [
          {
            id: 901,
            user_id: null,
            user_account_name: '',
            is_own_offer: false,
            offer_type: 'sell',
            commodity_id: 1,
            commodity_name: 'سکه',
            quantity: 4,
            remaining_quantity: 4,
            price: 50000,
            raw_price: 50000,
            market_published_price: 50000,
            viewer_effective_price: 50000,
            is_wholesale: true,
            lot_sizes: null,
            original_lot_sizes: null,
            notes: 'pw execute conflict',
            status: 'active',
            created_at: 'امروز',
            expires_at_ts: Math.floor(Date.now() / 1000) + 3600,
            customer_badge_visible: false,
            customer_management_name: null,
            customer_tier: null,
          },
        ])
      }
      if (url.pathname === '/api/trades/' && method === 'POST') {
        tradeExecuteCount += 1
        tradeBodies.push(request.postDataJSON() as Record<string, unknown>)
        await new Promise((resolve) => setTimeout(resolve, 150))
        return fulfillJson(route, 400, {
          detail: 'نمی‌توانید روی لفظ خودتان معامله کنید.',
        })
      }

      return fulfillJson(route, 200, null)
    })

    await page.goto('/market', { waitUntil: 'domcontentloaded' })

    await expect(page.locator('.text-offer-input')).toBeVisible()
    await page.locator('.text-offer-input').fill('خرید سکه 4 عدد 50000')
    await page.locator('.send-btn').click()
    await expect(page.locator('.offer-preview-card')).toBeVisible()

    const publishConfirm = page.locator('.offer-preview-confirm')
    await publishConfirm.evaluate((node: HTMLElement) => {
      node.click()
      node.click()
    })

    await expect(page.locator('.offer-preview-error')).toContainText('بازار در حال حاضر بسته است')
    expect(offerPublishCount).toBe(1)
    expect(offerBodies[0]?.idempotency_key).toEqual(expect.any(String))
    await page.locator('.offer-preview-close').evaluate((node: HTMLElement) => node.click())
    await expect(page.locator('.offer-preview-card')).toBeHidden()

    const offerCard = page.locator('.offer-card-wrap').filter({ hasText: 'pw execute conflict' }).first()
    await expect(offerCard).toBeVisible()
    const tradeButton = offerCard.getByRole('button', { name: '4 عدد' }).first()
    await tradeButton.click()
    await expect(tradeButton).toHaveClass(/pending/)
    await tradeButton.evaluate((node: HTMLElement) => {
      node.click()
      node.click()
    })

    await expect(page.getByText('نمی‌توانید روی لفظ خودتان معامله کنید.')).toBeVisible()
    expect(tradeExecuteCount).toBe(1)
    expect(tradeBodies[0]).toEqual(expect.objectContaining({
      offer_id: 901,
      quantity: 4,
      idempotency_key: expect.any(String),
    }))
  })
})
