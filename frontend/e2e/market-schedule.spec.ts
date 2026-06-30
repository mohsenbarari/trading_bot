/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page } from '@playwright/test'

import { primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SeededMarketSession {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
  commodityId: number
  commodityName: string
}

interface MarketRuntimeSeedOptions {
  mode: 'open' | 'closed'
  noticeVisible: boolean
  offersSinceLastOpen: number
  disableSchedule?: boolean
}

interface MarketRuntimeResponse {
  is_open: boolean
  active_web_notice_visible: boolean
  offers_since_last_open: number
  last_transition_at: string | null
  next_transition_at: string | null
}

function runPythonInApp<T>(script: string): T {
  const stdout = execFileSync('docker', ['exec', '-i', 'trading_bot_app', 'python', '-'], {
    input: script,
    encoding: 'utf8',
  })

  const lastLine = stdout
    .split(/\r?\n/)
    .map((line: string) => line.trim())
    .filter(Boolean)
    .at(-1)

  if (!lastLine) {
    throw new Error('No JSON output returned from trading_bot_app market schedule helper')
  }

  return JSON.parse(lastLine) as T
}

function seedIsolatedMarketSession(label: string, role: 'standard' | 'super_admin' = 'standard'): SeededMarketSession {
  return runPythonInApp<SeededMarketSession>(`
import asyncio
import json
import uuid
from datetime import timedelta

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from core.cache import invalidate_commodities_cache
from bot.utils.redis_helpers import invalidate_commodity_cache
from models.commodity import Commodity
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}
role_name = ${JSON.stringify(role)}

async def main():
    suffix = uuid.uuid4().hex[:10]
    name_suffix = uuid.uuid4().hex[:10].translate(str.maketrans('0123456789abcdef', 'ابپتثجچحخدذرزسشص'))
    account_name = f"pw_market_schedule_{label}_{suffix}"
    commodity_name = f"کالای زمان‌بندی {name_suffix}"
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000

    async with AsyncSessionLocal() as db:
        commodity = Commodity(name=commodity_name)
        db.add(commodity)
        await db.flush()

        actor = User(
            account_name=account_name,
            mobile_number=f"09{mobile_seed:09d}",
            full_name=account_name,
            address='Playwright Market Schedule',
            role=UserRole.SUPER_ADMIN if role_name == 'super_admin' else UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(actor)
        await db.flush()

        refresh_token = create_refresh_token(subject=actor.id)
        session = UserSession(
            user_id=actor.id,
            device_name='Playwright Market Schedule Device',
            device_ip='127.0.0.1',
            platform=Platform.WEB,
            refresh_token_hash=hash_token(refresh_token),
            is_primary=True,
            is_active=True,
            expires_at=None,
        )
        db.add(session)
        await db.flush()

        access_token = create_access_token(
            subject=actor.id,
            expires_delta=timedelta(minutes=60),
            session_id=str(session.id),
        )

        await db.commit()

    await invalidate_commodities_cache()
    await invalidate_commodity_cache()

    print(json.dumps({
        'userId': actor.id,
        'accountName': account_name,
        'accessToken': access_token,
        'refreshToken': refresh_token,
        'commodityId': commodity.id,
        'commodityName': commodity_name,
    }))

asyncio.run(main())
`)
}

function toPythonBool(value: boolean) {
  return value ? 'True' : 'False'
}

function authHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
}

function configureMarketRuntime(options: MarketRuntimeSeedOptions) {
  runPythonInApp<{ ok: boolean }>(`
import asyncio
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete

from core.db import AsyncSessionLocal
from core.services.market_transition_service import get_market_runtime_view
from core.trading_settings import save_trading_settings_async
from models.market_runtime_state import MarketRuntimeState
from models.market_schedule_override import MarketScheduleOverride

mode = ${JSON.stringify(options.mode)}
notice_visible = ${toPythonBool(options.noticeVisible)}
offers_since_last_open = ${options.offersSinceLastOpen}
disable_schedule = ${toPythonBool(!!options.disableSchedule)}

async def main():
    tehran_now = datetime.now(ZoneInfo('Asia/Tehran'))
    tehran_today = tehran_now.date()

    settings_payload = {
        'market_schedule_enabled': not disable_schedule,
        'market_timezone': 'Asia/Tehran',
        'market_open_time_local': '00:00' if not disable_schedule else '09:00',
        'market_close_time_local': '23:59' if not disable_schedule else '18:00',
        'market_closed_weekdays': [tehran_today.weekday()] if (not disable_schedule and mode == 'closed') else [],
    }
    await save_trading_settings_async(settings_payload)

    async with AsyncSessionLocal() as db:
        await db.execute(delete(MarketScheduleOverride))

        state = await db.get(MarketRuntimeState, 1)
        if state is None:
            state = MarketRuntimeState(id=1)
            db.add(state)

        state.is_open = mode == 'open'
        state.active_web_notice_visible = notice_visible
        state.offers_since_last_open = offers_since_last_open
        state.last_transition_at = datetime.now(timezone.utc)

        await db.commit()

        deadline = datetime.now(timezone.utc).timestamp() + 10
        while datetime.now(timezone.utc).timestamp() < deadline:
            runtime_view = await get_market_runtime_view(db)
            if runtime_view.is_open == (mode == 'open' or disable_schedule):
                break
            await asyncio.sleep(0.25)
            await db.rollback()
        else:
            runtime_view = await get_market_runtime_view(db)
            raise RuntimeError(
                f"Market runtime seed did not converge: mode={mode} disable_schedule={disable_schedule} "
                f"view_open={runtime_view.is_open} notice={runtime_view.active_web_notice_visible} "
                f"offers={runtime_view.offers_since_last_open}"
            )

    print(json.dumps({'ok': True}))

asyncio.run(main())
`)
}

async function refreshMarketScheduleSettingsInApp(
  request: APIRequestContext,
  accessToken: string,
  disableSchedule: boolean,
) {
  const response = await request.put(`${BACKEND_BASE_URL}/api/trading-settings/`, {
    headers: authHeaders(accessToken),
    data: {
      market_schedule_enabled: !disableSchedule,
      market_timezone: 'Asia/Tehran',
      market_open_time_local: disableSchedule ? '09:00' : '00:00',
      market_close_time_local: disableSchedule ? '18:00' : '23:59',
      market_closed_weekdays: [],
    },
  })
  expect(response.ok()).toBeTruthy()
}

async function setSeededSession(page: Page, fixture: SeededMarketSession) {
  await primeAuthSession(page, fixture.accessToken, fixture.refreshToken)
}

async function gotoMarketAndWaitForSocket(page: Page) {
  await page.goto('/market')
  await expect(page.locator('.market-page')).toBeVisible()
}

async function openFreshMarketPage(context: BrowserContext, fixture: SeededMarketSession) {
  const freshPage = await context.newPage()
  await setSeededSession(freshPage, fixture)
  await gotoMarketAndWaitForSocket(freshPage)
  return freshPage
}

function readAuthoritativeMarketState(): MarketRuntimeResponse {
  return runPythonInApp<MarketRuntimeResponse>(`
import asyncio
import json

from core.db import AsyncSessionLocal
from core.services.market_transition_service import get_market_runtime_view

async def main():
    async with AsyncSessionLocal() as db:
        state = await get_market_runtime_view(db)
        print(json.dumps({
            'is_open': state.is_open,
            'active_web_notice_visible': state.active_web_notice_visible,
            'offers_since_last_open': state.offers_since_last_open,
            'last_transition_at': None if state.last_transition_at is None else state.last_transition_at.isoformat(),
            'next_transition_at': None if state.next_transition_at is None else state.next_transition_at.isoformat(),
        }))

asyncio.run(main())
`)
}

async function waitForAuthoritativeMarketState(
  predicate: (state: MarketRuntimeResponse) => boolean,
  description: string,
) {
  const deadline = Date.now() + 15000
  let lastState: MarketRuntimeResponse | null = null

  while (Date.now() < deadline) {
    lastState = readAuthoritativeMarketState()
    if (predicate(lastState)) {
      return lastState
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }

  throw new Error(`Timed out waiting for market state: ${description}. Last state: ${JSON.stringify(lastState)}`)
}

async function openFreshMarketContext(browser: Browser, fixture: SeededMarketSession, marketState?: MarketRuntimeResponse) {
  const freshContext = await browser.newContext()
  if (marketState) {
    await freshContext.route('**/api/trading-settings/market-state', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(marketState),
      })
    })
  }
  const freshPage = await openFreshMarketPage(freshContext, fixture)
  return { freshContext, freshPage }
}

async function openOfferPreview(page: Page, text: string) {
  const offerInput = page.locator('.text-offer-input')
  const sendButton = page.locator('.send-btn')
  await expect(offerInput).toBeEnabled({ timeout: 15000 })
  await offerInput.fill(text)
  await expect(offerInput).toHaveValue(text)
  await expect(sendButton).toBeEnabled({ timeout: 15000 })
  const parseRequest = page.waitForRequest((request) =>
    request.url().includes('/api/offers/parse') && request.method() === 'POST',
  )
  await sendButton.click()
  await parseRequest
  await expect(page.locator('.offer-preview-card')).toBeVisible()
}

async function confirmOfferPreview(page: Page) {
  const card = page.locator('.offer-preview-card')
  const confirmButton = card.locator('.offer-preview-confirm')
  const warning = card.locator('.offer-preview-warning')
  const error = card.locator('.offer-preview-error')
  const waitForPreviewOutcome = async () => Promise.race([
    card.waitFor({ state: 'detached', timeout: 30000 }).then(() => 'closed' as const),
    warning.waitFor({ state: 'visible', timeout: 30000 }).then(() => 'warning' as const),
    error.waitFor({ state: 'visible', timeout: 30000 }).then(() => 'error' as const),
  ]).catch(() => 'timeout' as const)

  await expect(confirmButton).toBeEnabled({ timeout: 15000 })
  await confirmButton.click()

  const firstOutcome = await waitForPreviewOutcome()
  if (firstOutcome === 'error') {
    throw new Error(`Offer preview error after confirm: ${(await error.textContent())?.trim() || 'unknown error'}`)
  }
  if (firstOutcome === 'warning') {
    await expect(confirmButton).toBeEnabled({ timeout: 15000 })
    await confirmButton.click()

    const secondOutcome = await waitForPreviewOutcome()
    if (secondOutcome === 'error') {
      throw new Error(`Offer preview error after warning acknowledgement: ${(await error.textContent())?.trim() || 'unknown error'}`)
    }
    if (secondOutcome === 'warning') {
      throw new Error(`Offer preview warning persisted after acknowledgement: ${(await warning.textContent())?.trim() || 'unknown warning'}`)
    }
  }

  await expect(card).toHaveCount(0, { timeout: 30000 })
}

test.describe('Market schedule browser regressions', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeEach(() => {
    configureMarketRuntime({
      mode: 'open',
      noticeVisible: false,
      offersSinceLastOpen: 2,
      disableSchedule: true,
    })
  })

  test.afterAll(() => {
    configureMarketRuntime({
      mode: 'open',
      noticeVisible: false,
      offersSinceLastOpen: 2,
      disableSchedule: true,
    })
  })

  test('market page reflects closed/open runtime transitions and disables or enables the composer', async ({ browser, browserName }) => {
    test.setTimeout(120000)
    if (browserName === 'webkit') {
      test.slow()
    }

    const actor = seedIsolatedMarketSession('transition_runtime')
    const initialMarketState = await waitForAuthoritativeMarketState(
      (state) => state.is_open && !state.active_web_notice_visible,
      'initial open state without notice',
    )
    const { freshContext: initialContext, freshPage: initialPage } = await openFreshMarketContext(browser, actor, initialMarketState)

    const input = initialPage.locator('.text-offer-input')
    const sendButton = initialPage.locator('.send-btn')
    await expect(input).toBeEnabled()
    await expect(sendButton).toBeDisabled()
    await expect(initialPage.locator('.market-runtime-notice')).toHaveCount(0)

    await input.fill(`خرید ${actor.commodityName} 10 عدد 122000`)
    await expect(sendButton).toBeEnabled()

    configureMarketRuntime({
      mode: 'closed',
      noticeVisible: true,
      offersSinceLastOpen: 0,
      disableSchedule: false,
    })
    const closedMarketState = await waitForAuthoritativeMarketState(
      (state) => !state.is_open && state.active_web_notice_visible,
      'closed state with visible end notice',
    )
    const { freshContext: closedContext, freshPage: closedPage } = await openFreshMarketContext(browser, actor, closedMarketState)

    await expect(closedPage.locator('.market-runtime-notice')).toHaveText('پایان فعالیت بازار')
    await expect(closedPage.locator('.text-offer-input')).toBeDisabled()
    await expect(closedPage.locator('.send-btn')).toBeDisabled()

    configureMarketRuntime({
      mode: 'open',
      noticeVisible: true,
      offersSinceLastOpen: 0,
      disableSchedule: false,
    })
    const reopenedMarketState = await waitForAuthoritativeMarketState(
      (state) => state.is_open && state.active_web_notice_visible,
      'reopened state with visible start notice',
    )
    const { freshContext: reopenedContext, freshPage: reopenedPage } = await openFreshMarketContext(browser, actor, reopenedMarketState)

    await expect(reopenedPage.locator('.market-runtime-notice')).toHaveText('شروع فعالیت بازار')
    const reopenedInput = reopenedPage.locator('.text-offer-input')
    const reopenedSendButton = reopenedPage.locator('.send-btn')
    await expect(reopenedInput).toBeEnabled()
    await reopenedInput.fill(`فروش ${actor.commodityName} 11 عدد 122100`)
    await expect(reopenedSendButton).toBeEnabled()

    await initialPage.close()
    await closedPage.close()
    await reopenedPage.close()
    await initialContext.close()
    await closedContext.close()
    await reopenedContext.close()
  })

  test('start notice hides after the second accepted offer after market open', async ({ browser, browserName, request }) => {
    test.setTimeout(120000)
    if (browserName === 'webkit') {
      test.slow()
    }

    configureMarketRuntime({
      mode: 'open',
      noticeVisible: true,
      offersSinceLastOpen: 0,
      disableSchedule: true,
    })

    const actor = seedIsolatedMarketSession('second_offer_notice', 'super_admin')
    await refreshMarketScheduleSettingsInApp(request, actor.accessToken, true)
    const openedMarketState = await waitForAuthoritativeMarketState(
      (state) => state.is_open && state.active_web_notice_visible && state.offers_since_last_open === 0,
      'opened state with visible start notice before offers',
    )
    const { freshContext: activeContext, freshPage: activePage } = await openFreshMarketContext(browser, actor, openedMarketState)

    await expect(activePage.locator('.market-runtime-notice')).toHaveText('شروع فعالیت بازار')

    await openOfferPreview(activePage, `خرید ${actor.commodityName} 10 عدد 121111`)
    configureMarketRuntime({
      mode: 'open',
      noticeVisible: true,
      offersSinceLastOpen: 0,
      disableSchedule: true,
    })
    await refreshMarketScheduleSettingsInApp(request, actor.accessToken, true)
    await confirmOfferPreview(activePage)
    await expect(activePage.locator('.offer-preview-card')).toHaveCount(0)
    const afterFirstOfferState = await waitForAuthoritativeMarketState(
      (state) => state.is_open && state.active_web_notice_visible && state.offers_since_last_open === 1,
      'start notice still visible after first accepted offer',
    )
    const { freshContext: afterFirstOfferContext, freshPage: afterFirstOfferPage } = await openFreshMarketContext(browser, actor, afterFirstOfferState)
    await expect(afterFirstOfferPage.locator('.market-runtime-notice')).toHaveText('شروع فعالیت بازار')

    await openOfferPreview(activePage, `فروش ${actor.commodityName} 12 عدد 121222`)
    configureMarketRuntime({
      mode: 'open',
      noticeVisible: true,
      offersSinceLastOpen: 1,
      disableSchedule: true,
    })
    await refreshMarketScheduleSettingsInApp(request, actor.accessToken, true)
    await confirmOfferPreview(activePage)
    await expect(activePage.locator('.offer-preview-card')).toHaveCount(0)
    const afterSecondOfferState = await waitForAuthoritativeMarketState(
      (state) => state.is_open && !state.active_web_notice_visible && state.offers_since_last_open >= 2,
      'start notice hidden after second accepted offer',
    )
    const { freshContext: afterSecondOfferContext, freshPage: afterSecondOfferPage } = await openFreshMarketContext(browser, actor, afterSecondOfferState)
    await expect(afterSecondOfferPage.locator('.market-runtime-notice')).toHaveCount(0)

    await activePage.close()
    await afterFirstOfferPage.close()
    await afterSecondOfferPage.close()
    await activeContext.close()
    await afterFirstOfferContext.close()
    await afterSecondOfferContext.close()
  })
})
