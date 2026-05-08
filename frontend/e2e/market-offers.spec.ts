import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface AuthTokens {
  access_token: string
  refresh_token: string
}

interface Commodity {
  id: number
  name: string
}

interface OfferSummary {
  offer_type: 'buy' | 'sell'
  commodity_name: string
  quantity: number
  price: number
}

async function fetchDevLoginTokens(request: APIRequestContext): Promise<AuthTokens> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/auth/dev-login`, {
    headers: { 'Content-Type': 'application/json' },
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<AuthTokens>
}

function authHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
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

async function fetchFirstCommodity(request: APIRequestContext): Promise<Commodity> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/commodities/`)
  expect(response.ok()).toBeTruthy()
  const commodities = await response.json() as Commodity[]
  const commodity = commodities.find((entry) => entry?.id && entry?.name)
  expect(commodity).toBeTruthy()
  return commodity as Commodity
}

async function fetchMyOffers(request: APIRequestContext, accessToken: string): Promise<OfferSummary[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/offers/my?since_hours=2`, {
    headers: authHeaders(accessToken),
  })
  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<OfferSummary[]>
}

function uniquePrice(seed: number) {
  return 120000 + (seed % 1000)
}

test.describe('Market offer creation regressions', () => {
  test('manual wizard create flow persists a new buy offer', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    const commodity = await fetchFirstCommodity(request)
    const price = uniquePrice(Date.now())

    await setAuthTokens(page, tokens)
    await page.goto('/market')

    await page.getByRole('button', { name: /ثبت خرید/ }).click()
    await page.getByRole('button', { name: commodity.name }).click()
    await page.getByRole('button', { name: /^10$/ }).click()
    await page.getByRole('button', { name: /فروش یکجا/ }).click()
    await page.locator('input[type="number"][placeholder="0"]').fill(String(price))
    await page.getByRole('button', { name: /ثبت نهایی لفظ خرید/ }).click()

    await expect(page.getByText('لفظ ثبت شد')).toBeVisible()

    const offers = await fetchMyOffers(request, tokens.access_token)
    expect(
      offers.some((offer) =>
        offer.offer_type === 'buy'
        && offer.commodity_name === commodity.name
        && Number(offer.quantity) === 10
        && Number(offer.price) === price,
      ),
    ).toBeTruthy()
  })

  test('text offer submission parses and persists a new sell offer', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    const commodity = await fetchFirstCommodity(request)
    const price = uniquePrice(Date.now() + 111)

    await setAuthTokens(page, tokens)
    await page.goto('/market')

    const offerInput = page.locator('input[type="text"]').first()
    await offerInput.fill(`فروش ${commodity.name} 12 عدد ${price}`)
    await offerInput.press('Enter')

    await expect(page.getByText('لفظ متنی ثبت شد')).toBeVisible()

    const offers = await fetchMyOffers(request, tokens.access_token)
    expect(
      offers.some((offer) =>
        offer.offer_type === 'sell'
        && offer.commodity_name === commodity.name
        && Number(offer.quantity) === 12
        && Number(offer.price) === price,
      ),
    ).toBeTruthy()
  })
})