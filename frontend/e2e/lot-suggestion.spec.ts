/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SeededSessionFixture {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

interface SeededOfferFixture {
  viewer: SeededSessionFixture
  ownerUserId: number
  ownerAccountName: string
  publicOfferId: number
  publicOfferNote: string
  ownOfferId: number
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
    throw new Error('No JSON output returned from trading_bot_app test seed helper')
  }

  return JSON.parse(lastLine) as T
}

function seedMarketOfferFixture(label: string): SeededOfferFixture {
  return runPythonInApp<SeededOfferFixture>(`
import asyncio
import json
import uuid
from datetime import timedelta

from core.db import AsyncSessionLocal
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.session import Platform, UserSession
from models.user import User, UserRole

label = ${JSON.stringify(label)}

async def main():
    suffix = uuid.uuid4().hex[:10]
    viewer_account_name = f"pw_viewer_{label}_{suffix}"
    owner_account_name = f"pw_owner_{label}_{suffix}"
    public_offer_note = f"pw-offer-{label}-{suffix}"

    viewer_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    owner_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000

    async with AsyncSessionLocal() as db:
        commodity = (await db.execute(select(Commodity).order_by(Commodity.id))).scalars().first()
        if commodity is None:
            commodity = Commodity(name='سکه امامی')
            db.add(commodity)
            await db.flush()

        viewer = User(
            account_name=viewer_account_name,
            mobile_number=f"09{viewer_mobile_seed:09d}",
            full_name=viewer_account_name,
            address='Playwright Lot Suggestion Viewer',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(viewer)
        await db.flush()

        owner = User(
            account_name=owner_account_name,
            mobile_number=f"09{owner_mobile_seed:09d}",
            full_name=owner_account_name,
            address='Playwright Lot Suggestion Owner',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(owner)
        await db.flush()

        refresh_token = create_refresh_token(subject=viewer.id)
        session = UserSession(
            user_id=viewer.id,
            device_name='Playwright Lot Suggestion Device',
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
            subject=viewer.id,
            expires_delta=timedelta(minutes=60),
            session_id=str(session.id),
        )

        public_offer = Offer(
            user_id=owner.id,
            offer_type=OfferType.SELL,
            commodity_id=commodity.id,
            quantity=34,
            remaining_quantity=34,
            price=76333,
            is_wholesale=False,
            lot_sizes=[16, 10, 8],
            original_lot_sizes=[16, 10, 8],
            notes=public_offer_note,
            status=OfferStatus.ACTIVE,
        )
        own_offer = Offer(
            user_id=viewer.id,
            offer_type=OfferType.BUY,
            commodity_id=commodity.id,
            quantity=12,
            remaining_quantity=12,
            price=75111,
            is_wholesale=True,
            lot_sizes=None,
            original_lot_sizes=None,
            notes=f"own-{public_offer_note}",
            status=OfferStatus.ACTIVE,
        )
        db.add_all([public_offer, own_offer])
        await db.commit()

    print(json.dumps({
        'viewer': {
            'userId': viewer.id,
            'accountName': viewer_account_name,
            'accessToken': access_token,
            'refreshToken': refresh_token,
        },
        'ownerUserId': owner.id,
        'ownerAccountName': owner_account_name,
        'publicOfferId': public_offer.id,
        'publicOfferNote': public_offer_note,
        'ownOfferId': own_offer.id,
    }))

from sqlalchemy import select
asyncio.run(main())
`)
}

function updateOfferState(offerId: number, remainingQuantity: number, lotSizes: number[], status: 'active' | 'expired' = 'active') {
  runPythonInApp<{ ok: boolean }>(`
import asyncio
import json

from core.db import AsyncSessionLocal
from models.offer import Offer, OfferStatus

offer_id = ${offerId}
remaining_quantity = ${remainingQuantity}
lot_sizes = ${JSON.stringify(lotSizes)}
status_value = ${JSON.stringify(status)}

async def main():
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, offer_id)
        offer.remaining_quantity = remaining_quantity
        offer.lot_sizes = lot_sizes or None
        offer.status = OfferStatus.ACTIVE if status_value == 'active' else OfferStatus.EXPIRED
        await db.commit()
    print(json.dumps({'ok': True}))

asyncio.run(main())
`)
}

function authHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
}

async function loginWithSeededSession(page: Page, fixture: SeededSessionFixture) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' })
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, fixture)
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByText(fixture.accountName)).toBeVisible({ timeout: 30000 })
}

test.describe('Lot suggestion regressions', () => {
  test('public offers API redacts owner identity and marks own offers without leaking ids', async ({ request }) => {
    const fixture = seedMarketOfferFixture('identity_audit')

    const response = await request.get(`${BACKEND_BASE_URL}/api/offers/`, {
      headers: authHeaders(fixture.viewer.accessToken),
    })

    expect(response.ok()).toBeTruthy()
    const offers = (await response.json()) as Array<Record<string, unknown>>

    const publicOffer = offers.find((offer) => Number(offer.id) === fixture.publicOfferId)
    const ownOffer = offers.find((offer) => Number(offer.id) === fixture.ownOfferId)

    expect(publicOffer).toBeTruthy()
    expect(publicOffer?.user_account_name).toBe('')
    expect(publicOffer?.user_id).toBeNull()
    expect(publicOffer?.is_own_offer).toBe(false)

    expect(ownOffer).toBeTruthy()
    expect(ownOffer?.user_account_name).toBe('')
    expect(ownOffer?.user_id).toBeNull()
    expect(ownOffer?.is_own_offer).toBe(true)
  })

  test('409 suggestion modal keeps server payload, updates from real offer changes, and never shows owner identity', async ({ page }) => {
    test.setTimeout(60000)
    const fixture = seedMarketOfferFixture('modal_regression')
    await loginWithSeededSession(page, fixture.viewer)
    await page.goto('/market', { waitUntil: 'domcontentloaded' })

    const publicOfferCard = page.locator('.offer-card-wrap').filter({
      has: page.locator('p').filter({ hasText: `توضیحات: ${fixture.publicOfferNote}` }),
    }).first()
    await expect(publicOfferCard).toBeVisible()
    await expect(page.getByText(fixture.ownerAccountName)).toHaveCount(0)

    await page.route('**/api/trades/', async (route) => {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          error_code: 'TRADE_LOT_UNAVAILABLE',
          title: 'پیشنهاد معامله',
          intro_text: 'لات 10 عددی که انتخاب کرده بودید لحظاتی قبل توسط کاربر دیگری انجام شد.',
          offer_id: fixture.publicOfferId,
          requested_amount: 10,
          offer_type: 'sell',
          offer_type_label: 'فروش',
          commodity_name: 'سکه امامی',
          price: 76333,
          remaining_quantity: 24,
          lot_summary: '16 + 8',
          available_lots: [16, 8],
        }),
      })
    })

    const lot10Button = publicOfferCard.getByRole('button', { name: '10 عدد' }).first()
    await expect(lot10Button).toBeVisible({ timeout: 30000 })
    await lot10Button.evaluate((node: HTMLElement) => node.click())

    const confirm10Button = publicOfferCard.getByRole('button', { name: 'تایید 10 عدد؟' }).first()
    await expect(confirm10Button).toBeVisible({ timeout: 30000 })
    await confirm10Button.evaluate((node: HTMLElement) => node.click())

    const dialog = page.getByRole('alertdialog', { name: 'پیشنهاد معامله' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('24 عدد')
    await expect(dialog).toContainText('🔢 خُرد: 16 + 8')
    await expect(dialog).not.toContainText('34 عدد')
    await expect(dialog).not.toContainText(fixture.ownerAccountName)
    await expect(dialog).not.toContainText('طرف معامله')
    await expect(dialog.getByRole('button', { name: 'رد کردن' })).toBeVisible()
    await expect(dialog.getByRole('button', { name: 'بستن' })).toHaveCount(0)

    await expect(dialog.getByRole('button', { name: '16 عدد' })).toBeVisible({ timeout: 30000 })
    await dialog.getByRole('button', { name: '16 عدد' }).evaluate((node: HTMLElement) => node.click())
    await expect(dialog.getByRole('button', { name: 'تایید 16 عدد؟' })).toBeVisible({ timeout: 30000 })

    updateOfferState(fixture.publicOfferId, 8, [8], 'active')
    await expect(dialog).toContainText('8 عدد')
    await expect(dialog).toContainText('🔢 خُرد: 8')
    await expect(dialog.getByRole('button', { name: '8 عدد' })).toBeVisible()
    await expect(dialog.getByRole('button', { name: '16 عدد' })).toHaveCount(0)

    updateOfferState(fixture.publicOfferId, 8, [8], 'expired')
    await expect(dialog).toBeHidden({ timeout: 5000 })
  })
})
