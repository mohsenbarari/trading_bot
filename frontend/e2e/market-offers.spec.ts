/// <reference types="node" />

import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'

import { getE2EBackendBaseUrl, runPythonInApp as runPythonInConfiguredApp } from './helpers/mutationRuntime'
import { primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = getE2EBackendBaseUrl()

interface AuthTokens {
  access_token: string
  refresh_token: string
}

interface SeededSessionFixture {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

interface SeededCommoditySessionFixture extends SeededSessionFixture {
  commodityId: number
  commodityName: string
}

interface CustomerPricingMarketFixture {
  owner: SeededSessionFixture
  tier1Customer: SeededSessionFixture
  tier2Customer: SeededSessionFixture
  outsider: SeededSessionFixture
  commodityId: number
  commodityName: string
  sourceManagementName: string
}

interface CustomerExecutionMarketFixture {
  owner: SeededSessionFixture
  tier2Customer: SeededSessionFixture
  outsider: SeededSessionFixture
  commodityId: number
  commodityName: string
  ownerOfferId: number
  ownerOfferNote: string
  outsiderOfferId: number
  outsiderOfferNote: string
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

interface PersistedOfferRecord {
  id: number
  userId: number
  offerType: 'buy' | 'sell'
  price: number
  quantity: number
  notes: string | null
  excludeFromCompetitivePrice: boolean
  priceWarningType: string | null
}

interface PersistedTradeRecord {
  id: number
  offerId: number | null
  offerUserId: number
  responderUserId: number
  actorUserId: number | null
  price: number
  quantity: number
  tradeType: 'buy' | 'sell'
}

function runPythonInApp<T>(script: string): T {
  return runPythonInConfiguredApp<T>(script, 'market offers test seed helper')
}

function seedWarningActorWithComparableOffers(
  label: string,
  commodityId: number,
  comparableOfferType: 'buy' | 'sell',
  prices: number[],
  quantity = 10,
): SeededSessionFixture {
  return runPythonInApp<SeededSessionFixture>(`
import asyncio
import json
import uuid
from datetime import timedelta

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from core.server_routing import current_server
from models.offer import Offer, OfferStatus, OfferType
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}
commodity_id = ${commodityId}
comparable_offer_type = ${JSON.stringify(comparableOfferType)}
prices = ${JSON.stringify(prices)}
quantity = ${quantity}

async def main():
    suffix = uuid.uuid4().hex[:10]
    actor_account_name = f"pw_warning_actor_{label}_{suffix}"
    owner_account_name = f"pw_warning_owner_{label}_{suffix}"

    actor_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    owner_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000

    async with AsyncSessionLocal() as db:
        actor = User(
            account_name=actor_account_name,
            mobile_number=f"09{actor_mobile_seed:09d}",
            full_name=actor_account_name,
            address='Playwright Market Warning Actor',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        owner = User(
            account_name=owner_account_name,
            mobile_number=f"09{owner_mobile_seed:09d}",
            full_name=owner_account_name,
            address='Playwright Market Warning Owner',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add_all([actor, owner])
        await db.flush()

        refresh_token = create_refresh_token(subject=actor.id)
        session = UserSession(
            user_id=actor.id,
            device_name='Playwright Market Warning Device',
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

        offers = []
        for index, price in enumerate(prices):
            offers.append(Offer(
                user_id=owner.id,
                actor_user_id=owner.id,
                offer_type=OfferType.BUY if comparable_offer_type == 'buy' else OfferType.SELL,
                commodity_id=commodity_id,
                quantity=quantity,
                remaining_quantity=quantity,
                price=price,
                home_server=current_server(),
                is_wholesale=True,
                lot_sizes=None,
                original_lot_sizes=None,
                notes=f"pw-warning-{label}-{index}",
                status=OfferStatus.ACTIVE,
                exclude_from_competitive_price=False,
            ))
        db.add_all(offers)
        await db.commit()

    print(json.dumps({
        'userId': actor.id,
        'accountName': actor_account_name,
        'accessToken': access_token,
        'refreshToken': refresh_token,
    }))

asyncio.run(main())
`)
}

function seedIsolatedMarketSession(label: string): SeededCommoditySessionFixture {
  return runPythonInApp<SeededCommoditySessionFixture>(`
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

async def main():
  suffix = uuid.uuid4().hex[:10]
  name_suffix = uuid.uuid4().hex[:10].translate(str.maketrans('0123456789abcdef', 'ابپتثجچحخدذرزسشص'))
  account_name = f"pw_market_actor_{label}_{suffix}"
  commodity_name = f"کالای تست {name_suffix}"
  mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000

  async with AsyncSessionLocal() as db:
    commodity = Commodity(name=commodity_name)
    db.add(commodity)
    await db.flush()

    actor = User(
      account_name=account_name,
      mobile_number=f"09{mobile_seed:09d}",
      full_name=account_name,
      address='Playwright Market Isolated Actor',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    db.add(actor)
    await db.flush()

    refresh_token = create_refresh_token(subject=actor.id)
    session = UserSession(
      user_id=actor.id,
      device_name='Playwright Market Isolated Device',
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

function seedCustomerPricingMarketFixture(label: string): CustomerPricingMarketFixture {
  return runPythonInApp<CustomerPricingMarketFixture>(`
import asyncio
import json
import uuid
from datetime import timedelta
from decimal import Decimal

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from core.cache import invalidate_commodities_cache
from core.server_routing import current_server
from bot.utils.redis_helpers import invalidate_commodity_cache
from models.commodity import Commodity
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer import Offer, OfferStatus, OfferType
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

def build_mobile() -> str:
  return f"09{int(uuid.uuid4().hex[:9], 16) % 1000000000:09d}"

async def create_session_bundle(db, user, device_name):
  refresh_token = create_refresh_token(subject=user.id)
  session = UserSession(
    user_id=user.id,
    device_name=device_name,
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
    subject=user.id,
    expires_delta=timedelta(minutes=60),
    session_id=str(session.id),
  )
  return {
    'userId': user.id,
    'accountName': user.account_name,
    'accessToken': access_token,
    'refreshToken': refresh_token,
  }

async def main():
  suffix = uuid.uuid4().hex[:10]
  name_suffix = uuid.uuid4().hex[:10].translate(str.maketrans('0123456789abcdef', 'ابپتثجچحخدذرزسشص'))
  commodity_name = f"کالای قیمت مشتری {name_suffix}"
  source_management_name = f"مشتری سطح ۱ {suffix[:4]}"

  async with AsyncSessionLocal() as db:
    commodity = Commodity(name=commodity_name)
    owner = User(
      account_name=f"pw_customer_owner_{label}_{suffix}",
      mobile_number=build_mobile(),
      full_name='Playwright Customer Owner',
      address='Playwright Customer Owner',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    source_customer = User(
      account_name=f"pw_customer_source_{label}_{suffix}",
      mobile_number=build_mobile(),
      full_name='Playwright Tier1 Source',
      address='Playwright Tier1 Source',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    tier2_customer = User(
      account_name=f"pw_customer_tier2_{label}_{suffix}",
      mobile_number=build_mobile(),
      full_name='Playwright Tier2 Viewer',
      address='Playwright Tier2 Viewer',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    outsider = User(
      account_name=f"pw_customer_outsider_{label}_{suffix}",
      mobile_number=build_mobile(),
      full_name='Playwright Outsider Viewer',
      address='Playwright Outsider Viewer',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    db.add_all([commodity, owner, source_customer, tier2_customer, outsider])
    await db.flush()

    db.add_all([
      CustomerRelation(
        owner_user_id=owner.id,
        customer_user_id=source_customer.id,
        created_by_user_id=owner.id,
        invitation_token=f"CUST-{uuid.uuid4().hex}",
        management_name=source_management_name,
        customer_tier=CustomerTier.TIER_1,
        commission_rate=None,
        status=CustomerRelationStatus.ACTIVE,
        activated_at=None,
        deleted_at=None,
      ),
      CustomerRelation(
        owner_user_id=owner.id,
        customer_user_id=tier2_customer.id,
        created_by_user_id=owner.id,
        invitation_token=f"CUST-{uuid.uuid4().hex}",
        management_name=f"مشتری سطح ۲ {suffix[:4]}",
        customer_tier=CustomerTier.TIER_2,
        commission_rate=Decimal('0.5'),
        status=CustomerRelationStatus.ACTIVE,
        activated_at=None,
        deleted_at=None,
      ),
    ])

    db.add(Offer(
      user_id=source_customer.id,
      actor_user_id=source_customer.id,
      offer_type=OfferType.BUY,
      commodity_id=commodity.id,
      quantity=10,
      remaining_quantity=10,
      price=50000,
      home_server=current_server(),
      is_wholesale=True,
      lot_sizes=None,
      original_lot_sizes=None,
      notes=f"pw-customer-market-{label}",
      status=OfferStatus.ACTIVE,
      exclude_from_competitive_price=False,
    ))

    owner_bundle = await create_session_bundle(db, owner, 'Playwright Customer Owner Device')
    tier1_bundle = await create_session_bundle(db, source_customer, 'Playwright Tier1 Source Device')
    tier2_bundle = await create_session_bundle(db, tier2_customer, 'Playwright Tier2 Viewer Device')
    outsider_bundle = await create_session_bundle(db, outsider, 'Playwright Outsider Viewer Device')

    await db.commit()

  await invalidate_commodities_cache()
  await invalidate_commodity_cache()

  print(json.dumps({
    'owner': owner_bundle,
    'tier1Customer': tier1_bundle,
    'tier2Customer': tier2_bundle,
    'outsider': outsider_bundle,
    'commodityId': commodity.id,
    'commodityName': commodity_name,
    'sourceManagementName': source_management_name,
  }))

asyncio.run(main())
`)
}

function seedCustomerExecutionMarketFixture(label: string): CustomerExecutionMarketFixture {
  return runPythonInApp<CustomerExecutionMarketFixture>(`
import asyncio
import json
import uuid
from datetime import timedelta
from decimal import Decimal

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from core.cache import invalidate_commodities_cache
from core.server_routing import current_server
from bot.utils.redis_helpers import invalidate_commodity_cache
from models.commodity import Commodity
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer import Offer, OfferStatus, OfferType
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

def build_mobile() -> str:
  return f"09{int(uuid.uuid4().hex[:9], 16) % 1000000000:09d}"

async def create_session_bundle(db, user, device_name):
  refresh_token = create_refresh_token(subject=user.id)
  session = UserSession(
    user_id=user.id,
    device_name=device_name,
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
    subject=user.id,
    expires_delta=timedelta(minutes=60),
    session_id=str(session.id),
  )
  return {
    'userId': user.id,
    'accountName': user.account_name,
    'accessToken': access_token,
    'refreshToken': refresh_token,
  }

async def main():
  suffix = uuid.uuid4().hex[:10]
  owner_offer_note = f"pw-owner-offer-{label}-{suffix[:6]}"
  outsider_offer_note = f"pw-outsider-offer-{label}-{suffix[:6]}"

  async with AsyncSessionLocal() as db:
    commodity = Commodity(name=f"کالای اجرای مشتری {int(uuid.uuid4().hex[:8], 16) % 10000}")
    owner = User(
      account_name=f"pw_exec_owner_{label}_{suffix}",
      mobile_number=build_mobile(),
      full_name='Playwright Execution Owner',
      address='Playwright Execution Owner',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    tier2_customer = User(
      account_name=f"pw_exec_tier2_{label}_{suffix}",
      mobile_number=build_mobile(),
      full_name='Playwright Execution Tier2',
      address='Playwright Execution Tier2',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    outsider = User(
      account_name=f"pw_exec_outsider_{label}_{suffix}",
      mobile_number=build_mobile(),
      full_name='Playwright Execution Outsider',
      address='Playwright Execution Outsider',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    db.add_all([commodity, owner, tier2_customer, outsider])
    await db.flush()

    db.add(CustomerRelation(
      owner_user_id=owner.id,
      customer_user_id=tier2_customer.id,
      created_by_user_id=owner.id,
      invitation_token=f"CUST-{uuid.uuid4().hex}",
      management_name=f"مشتری اجرای سطح ۲ {suffix[:4]}",
      customer_tier=CustomerTier.TIER_2,
      commission_rate=Decimal('0.5'),
      status=CustomerRelationStatus.ACTIVE,
      activated_at=None,
      deleted_at=None,
    ))

    owner_offer = Offer(
      user_id=owner.id,
      actor_user_id=owner.id,
      offer_type=OfferType.BUY,
      commodity_id=commodity.id,
      quantity=4,
      remaining_quantity=4,
      price=50000,
      home_server=current_server(),
      is_wholesale=True,
      lot_sizes=None,
      original_lot_sizes=None,
      notes=owner_offer_note,
      status=OfferStatus.ACTIVE,
      exclude_from_competitive_price=False,
    )
    outsider_offer = Offer(
      user_id=outsider.id,
      actor_user_id=outsider.id,
      offer_type=OfferType.SELL,
      commodity_id=commodity.id,
      quantity=4,
      remaining_quantity=4,
      price=100000,
      home_server=current_server(),
      is_wholesale=True,
      lot_sizes=None,
      original_lot_sizes=None,
      notes=outsider_offer_note,
      status=OfferStatus.ACTIVE,
      exclude_from_competitive_price=False,
    )
    db.add_all([owner_offer, outsider_offer])
    await db.flush()

    owner_bundle = await create_session_bundle(db, owner, 'Playwright Execution Owner Device')
    tier2_bundle = await create_session_bundle(db, tier2_customer, 'Playwright Execution Tier2 Device')
    outsider_bundle = await create_session_bundle(db, outsider, 'Playwright Execution Outsider Device')

    await db.commit()

  await invalidate_commodities_cache()
  await invalidate_commodity_cache()

  print(json.dumps({
    'owner': owner_bundle,
    'tier2Customer': tier2_bundle,
    'outsider': outsider_bundle,
    'commodityId': commodity.id,
    'commodityName': commodity.name,
    'ownerOfferId': owner_offer.id,
    'ownerOfferNote': owner_offer_note,
    'outsiderOfferId': outsider_offer.id,
    'outsiderOfferNote': outsider_offer_note,
  }))

asyncio.run(main())
`)
}

function fetchPersistedOfferByNotes(note: string): PersistedOfferRecord | null {
  return runPythonInApp<PersistedOfferRecord | null>(`
import asyncio
import json

from sqlalchemy import select

from core.db import AsyncSessionLocal
from models.offer import Offer, OfferType

target_note = ${JSON.stringify(note)}

async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Offer)
            .where(Offer.notes == target_note)
            .order_by(Offer.id.desc())
        )
        offer = result.scalars().first()

        if offer is None:
            print('null')
            return

        print(json.dumps({
            'id': offer.id,
            'userId': offer.user_id,
            'offerType': 'buy' if offer.offer_type == OfferType.BUY else 'sell',
            'price': offer.price,
            'quantity': offer.quantity,
            'notes': offer.notes,
            'excludeFromCompetitivePrice': bool(offer.exclude_from_competitive_price),
            'priceWarningType': offer.price_warning_type,
        }))

asyncio.run(main())
`)
}

function fetchPersistedTradesForCommodity(commodityId: number, userIds: number[]): PersistedTradeRecord[] {
  return runPythonInApp<PersistedTradeRecord[]>(`
import asyncio
import json

from sqlalchemy import or_, select

from core.db import AsyncSessionLocal
from models.trade import Trade, TradeType

commodity_id = ${commodityId}
user_ids = ${JSON.stringify(userIds)}

async def main():
  async with AsyncSessionLocal() as db:
    result = await db.execute(
      select(Trade)
      .where(
        Trade.commodity_id == commodity_id,
        or_(
          Trade.offer_user_id.in_(user_ids),
          Trade.responder_user_id.in_(user_ids),
        ),
      )
      .order_by(Trade.id.asc())
    )
    trades = result.scalars().all()
    print(json.dumps([
      {
        'id': trade.id,
        'offerId': trade.offer_id,
        'offerUserId': trade.offer_user_id,
        'responderUserId': trade.responder_user_id,
        'actorUserId': trade.actor_user_id,
        'price': trade.price,
        'quantity': trade.quantity,
        'tradeType': 'buy' if trade.trade_type == TradeType.BUY else 'sell',
      }
      for trade in trades
    ]))

asyncio.run(main())
`)
}

async function fetchDevLoginTokens(request: APIRequestContext): Promise<AuthTokens> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/auth/dev-login`, {
    headers: { 'Content-Type': 'application/json' },
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<AuthTokens>
}

async function executeTradeFromCard(page: Page, offerCard: Locator, quantityLabel: string) {
  const tradeButton = offerCard.locator('[data-test="trade-action-button"]').filter({ hasText: quantityLabel }).first()
  await expect(tradeButton).toBeVisible({ timeout: 30000 })
  await tradeButton.evaluate((node: HTMLElement) => node.click())
  await expect(tradeButton).toHaveAttribute('data-state', 'pending')
  const tradeRequest = page.waitForRequest((request) =>
    request.url().includes('/api/trades/') && request.method() === 'POST',
  )
  await tradeButton.evaluate((node: HTMLElement) => node.click())
  await tradeRequest
}

function authHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
}

async function setAuthTokens(page: Page, tokens: AuthTokens) {
  await primeAuthSession(page, tokens.access_token, tokens.refresh_token)
}

async function setSeededSession(page: Page, fixture: SeededSessionFixture) {
  await primeAuthSession(page, fixture.accessToken, fixture.refreshToken)
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

async function openOfferPreview(page: Page, text: string) {
  const offerInput = page.locator('[data-test="market-text-offer-input"]').first()
  await offerInput.fill(text)
  const sendButton = page.locator('[data-test="market-send-button"]')
  await expect(sendButton).toBeEnabled({ timeout: 30000 })
  await sendButton.click()
  await expect(page.locator('[data-test="offer-preview-card"]')).toBeVisible({ timeout: 30000 })
}

async function confirmOfferPreview(page: Page) {
  await page.locator('[data-test="offer-preview-confirm"]').click()
}

function uniquePrice(seed: number) {
  return 120000 + (seed % 1000)
}

test.describe('Market offer creation regressions', () => {
  test('market page stays text-only and persists a new buy offer from text input', async ({ page, request }) => {
    const actor = seedIsolatedMarketSession('buy_text_offer')
    const price = uniquePrice(Date.now())

    await setSeededSession(page, actor)
    await page.goto('/market')

    await expect(page.getByRole('button', { name: /ثبت خرید/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /ثبت فروش/ })).toHaveCount(0)

    await openOfferPreview(page, `خرید ${actor.commodityName} 10 عدد ${price}`)
    await expect(page.getByRole('heading', { name: 'پیش‌نمایش لفظ' })).toBeVisible()
    await confirmOfferPreview(page)

    await expect(page.locator('[data-test="offer-preview-card"]')).toHaveCount(0)

    const offers = await fetchMyOffers(request, actor.accessToken)
    expect(
      offers.some((offer) =>
        offer.offer_type === 'buy'
        && offer.commodity_name === actor.commodityName
        && Number(offer.quantity) === 10
        && Number(offer.price) === price,
      ),
    ).toBeTruthy()
  })

  test('text offer submission parses and persists a new sell offer', async ({ page, request }) => {
    const actor = seedIsolatedMarketSession('sell_text_offer')
    const price = uniquePrice(Date.now() + 111)

    await setSeededSession(page, actor)
    await page.goto('/market')

    await openOfferPreview(page, `فروش ${actor.commodityName} 12 عدد ${price}`)
    await expect(page.getByRole('heading', { name: 'پیش‌نمایش لفظ' })).toBeVisible()
    await confirmOfferPreview(page)

    await expect(page.locator('[data-test="offer-preview-card"]')).toHaveCount(0)

    const offers = await fetchMyOffers(request, actor.accessToken)
    expect(
      offers.some((offer) =>
        offer.offer_type === 'sell'
        && offer.commodity_name === actor.commodityName
        && Number(offer.quantity) === 12
        && Number(offer.price) === price,
      ),
    ).toBeTruthy()
  })

  test('market cards keep raw owner/public pricing but show adjusted Tier2 pricing', async ({ page }) => {
    const fixture = seedCustomerPricingMarketFixture('pricing_matrix')

    await setSeededSession(page, fixture.owner)
    await page.goto('/market')

    const ownerCard = page.locator('[data-test="offer-card"]', { hasText: fixture.commodityName }).first()
    await expect(ownerCard).toBeVisible()
    await expect(ownerCard.locator('[data-test="offer-price"]')).toHaveText('50,000')
    await expect(ownerCard.locator('[data-test="customer-context-row"]')).toContainText(fixture.sourceManagementName)
    await expect(ownerCard.locator('[data-test="customer-context-row"]')).toContainText('سطح 1')

    await setSeededSession(page, fixture.tier2Customer)
    await page.goto('/market')

    const tier2Card = page.locator('[data-test="offer-card"]', { hasText: fixture.commodityName }).first()
    await expect(tier2Card).toBeVisible()
    await expect(tier2Card.locator('[data-test="offer-price"]')).toHaveText('49,700')
    await expect(tier2Card.locator('[data-test="customer-context-row"]')).toHaveCount(0)

    await setSeededSession(page, fixture.outsider)
    await page.goto('/market')

    const outsiderCard = page.locator('[data-test="offer-card"]', { hasText: fixture.commodityName }).first()
    await expect(outsiderCard).toBeVisible()
    await expect(outsiderCard.locator('[data-test="offer-price"]')).toHaveText('50,000')
    await expect(outsiderCard.locator('[data-test="customer-context-row"]')).toHaveCount(0)
  })

  test('tier1 customer can publish a sell offer while owner and tier2 viewers see raw versus projected prices', async ({ page }) => {
    const fixture = seedCustomerPricingMarketFixture('tier1_sell_matrix')
    const uniqueNote = `pw_tier1_sell_${Date.now()}`
    const sellPrice = 50000

    await setSeededSession(page, fixture.tier1Customer)
    await page.goto('/market')

    await openOfferPreview(page, `فروش ${fixture.commodityName} 7 عدد ${sellPrice}: ${uniqueNote}`)
    await expect(page.getByRole('heading', { name: 'پیش‌نمایش لفظ' })).toBeVisible()
    await confirmOfferPreview(page)
    await expect(page.locator('[data-test="offer-preview-card"]')).toHaveCount(0)

    await expect
      .poll(() => fetchPersistedOfferByNotes(uniqueNote), { timeout: 30000 })
      .toMatchObject({
        userId: fixture.tier1Customer.userId,
        offerType: 'sell',
        price: sellPrice,
        quantity: 7,
      })

    await setSeededSession(page, fixture.owner)
    await page.goto('/market')

    const ownerCard = page.locator('[data-test="offer-card"]', { hasText: uniqueNote }).first()
    await expect(ownerCard).toBeVisible()
    await expect(ownerCard.locator('[data-test="offer-price"]')).toHaveText('50,000')
    await expect(ownerCard.locator('[data-test="customer-context-row"]')).toContainText(fixture.sourceManagementName)
    await expect(ownerCard.locator('[data-test="customer-context-row"]')).toContainText('سطح 1')

    await setSeededSession(page, fixture.tier2Customer)
    await page.goto('/market')

    const tier2Card = page.locator('[data-test="offer-card"]', { hasText: uniqueNote }).first()
    await expect(tier2Card).toBeVisible()
    await expect(tier2Card.locator('[data-test="offer-price"]')).toHaveText('50,300')
    await expect(tier2Card.locator('[data-test="customer-context-row"]')).toHaveCount(0)
  })

  test('tier2 customer executes own-owner and outsider offers through the browser with direct and mediated trade legs', async ({ page, request }) => {
    const fixture = seedCustomerExecutionMarketFixture('execution_matrix')

    await setSeededSession(page, fixture.tier2Customer)
    await page.goto('/market')

    const ownerCard = page.locator('[data-test="offer-card"]', { hasText: fixture.ownerOfferNote }).first()
    await expect(ownerCard).toBeVisible()
    await expect(ownerCard.locator('[data-test="offer-price"]')).toHaveText('49,700')

    await executeTradeFromCard(page, ownerCard, '4 عدد')
    await expect
      .poll(() => fetchPersistedTradesForCommodity(fixture.commodityId, [fixture.owner.userId, fixture.tier2Customer.userId, fixture.outsider.userId]), { timeout: 30000 })
      .toEqual([
        {
          id: expect.any(Number),
          offerId: fixture.ownerOfferId,
          offerUserId: fixture.owner.userId,
          responderUserId: fixture.tier2Customer.userId,
          actorUserId: expect.any(Number),
          price: 49700,
          quantity: 4,
          tradeType: 'sell',
        },
      ])

    const outsiderCard = page.locator('[data-test="offer-card"]', { hasText: fixture.outsiderOfferNote }).first()
    await expect(outsiderCard).toBeVisible()
    await expect(outsiderCard.locator('[data-test="offer-price"]')).toHaveText('100,500')

    await executeTradeFromCard(page, outsiderCard, '4 عدد')

    await expect
      .poll(() => fetchPersistedTradesForCommodity(fixture.commodityId, [fixture.owner.userId, fixture.tier2Customer.userId, fixture.outsider.userId]), { timeout: 30000 })
      .toEqual([
        {
          id: expect.any(Number),
          offerId: fixture.ownerOfferId,
          offerUserId: fixture.owner.userId,
          responderUserId: fixture.tier2Customer.userId,
          actorUserId: expect.any(Number),
          price: 49700,
          quantity: 4,
          tradeType: 'sell',
        },
        {
          id: expect.any(Number),
          offerId: fixture.outsiderOfferId,
          offerUserId: fixture.outsider.userId,
          responderUserId: fixture.owner.userId,
          actorUserId: expect.any(Number),
          price: 100000,
          quantity: 4,
          tradeType: 'buy',
        },
        {
          id: expect.any(Number),
          offerId: null,
          offerUserId: fixture.owner.userId,
          responderUserId: fixture.tier2Customer.userId,
          actorUserId: expect.any(Number),
          price: 100500,
          quantity: 4,
          tradeType: 'buy',
        },
      ])

    const myTradesResponse = await request.get(`${BACKEND_BASE_URL}/api/trades/my`, {
      headers: authHeaders(fixture.tier2Customer.accessToken),
    })
    expect(myTradesResponse.ok()).toBeTruthy()
    const myTrades = await myTradesResponse.json() as Array<{ price: number; trade_path_summary?: string | null }>
    expect(myTrades.some((trade) => Number(trade.price) === 49700)).toBeTruthy()
    expect(myTrades.some((trade) => Number(trade.price) === 100500 && typeof trade.trade_path_summary === 'string' && trade.trade_path_summary.length > 0)).toBeTruthy()
  })

  test('sell outlier warning requires a second confirmation and persists exclusion flags', async ({ page, request }) => {
    const commodity = await fetchFirstCommodity(request)
    const actor = seedWarningActorWithComparableOffers('sell_warning', commodity.id, 'sell', [100000, 100500, 101000])
    const uniqueNote = `pw_warn_sell_${Date.now()}`

    await setSeededSession(page, actor)
    await page.goto('/market')

    await openOfferPreview(page, `فروش ${commodity.name} 10 عدد 99900: ${uniqueNote}`)
    await expect(page.getByRole('heading', { name: 'پیش‌نمایش لفظ' })).toBeVisible()
    await confirmOfferPreview(page)

    await expect(page.getByRole('heading', { name: 'هشدار قیمت فروش' })).toBeVisible()
    await expect(page.getByText('پایین‌ترین قیمت فروش فعال')).toBeVisible()
    await expect(page.getByText('در نرخ منصفانه لحاظ نخواهد شد.')).toBeVisible()
    await expect(page.locator('[data-test="offer-preview-confirm"]')).toHaveText('با وجود هشدار منتشر کن')

    await confirmOfferPreview(page)
    await expect(page.locator('[data-test="offer-preview-card"]')).toHaveCount(0)

    await expect
      .poll(() => fetchPersistedOfferByNotes(uniqueNote), { timeout: 30000 })
      .toMatchObject({
        userId: actor.userId,
        offerType: 'sell',
        price: 99900,
        quantity: 10,
        excludeFromCompetitivePrice: true,
        priceWarningType: 'sell_below_lowest_active',
      })
  })

  test('buy outlier warning requires a second confirmation and persists exclusion flags', async ({ page, request }) => {
    const commodity = await fetchFirstCommodity(request)
    const actor = seedWarningActorWithComparableOffers('buy_warning', commodity.id, 'buy', [100000, 100500, 101000])
    const uniqueNote = `pw_warn_buy_${Date.now()}`
    const warningPrice = 999999

    await setSeededSession(page, actor)
    await page.goto('/market')

    await openOfferPreview(page, `خرید ${commodity.name} 10 عدد ${warningPrice}: ${uniqueNote}`)
    await expect(page.getByRole('heading', { name: 'پیش‌نمایش لفظ' })).toBeVisible()
    await confirmOfferPreview(page)

    await expect(page.getByRole('heading', { name: 'هشدار قیمت خرید' })).toBeVisible()
    await expect(page.getByText('بالاترین قیمت خرید فعال')).toBeVisible()
    await expect(page.getByText('در نرخ منصفانه لحاظ نخواهد شد.')).toBeVisible()
    await expect(page.locator('[data-test="offer-preview-confirm"]')).toHaveText('با وجود هشدار منتشر کن')

    await confirmOfferPreview(page)
    await expect(page.locator('[data-test="offer-preview-card"]')).toHaveCount(0)

    await expect
      .poll(() => fetchPersistedOfferByNotes(uniqueNote), { timeout: 30000 })
      .toMatchObject({
        userId: actor.userId,
        offerType: 'buy',
        price: warningPrice,
        quantity: 10,
        excludeFromCompetitivePrice: true,
        priceWarningType: 'buy_above_highest_active',
      })
  })
})
