/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SessionUser {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

interface TradeHistoryFixture {
  viewer: SessionUser
  counterpartyOwnerId: number
  counterpartyOwnerAccountName: string
  counterpartyAccountantId: number
  counterpartyAccountantAccountName: string
  relationDisplayName: string
}

interface TradeRealtimeFixture {
  viewer: SessionUser
  counterpartyOwnerId: number
  counterpartyOwnerAccountName: string
  counterpartyAccountantId: number
  counterpartyAccountantAccountName: string
  relationDisplayName: string
  offerId: number
  offerQuantity: number
}

interface PublicProfileHistoryFilterFixture {
  viewer: SessionUser
  targetUserId: number
  targetAccountName: string
  goldCommodityName: string
  silverCommodityName: string
  recentMutualTradeNumber: number
  mediumMutualTradeNumber: number
  oldMutualTradeNumber: number
  otherCounterpartyTradeNumber: number
  narrowFromDate: string
  narrowToDate: string
  wideFromDate: string
  wideToDate: string
}

interface PublicProfilePresenceFixture {
  viewer: SessionUser
  onlineTargetUserId: number
  onlineTargetAccountName: string
  offlineTargetUserId: number
  offlineTargetAccountName: string
}

interface PublicProfileBlockedMarketFixture {
  viewer: SessionUser
  targetUserId: number
  targetAccountName: string
  offerNote: string
  tradeAmount: number
}

function resolveAppContainerName() {
  const stdout = execFileSync('docker', ['ps', '--format', '{{.Names}}'], {
    encoding: 'utf8',
  })

  const names = stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

  const exactAppName = names.find((name) => name === 'trading_bot_app')
  if (exactAppName) {
    return exactAppName
  }

  const appName = names.find((name) => name.includes('trading_bot') && /(^|_)app($|_)/.test(name))
  if (!appName) {
    throw new Error('Could not find a running trading bot app container')
  }
  return appName
}

const APP_CONTAINER_NAME = resolveAppContainerName()

function runPythonInApp<T>(script: string): T {
  const stdout = execFileSync('docker', ['exec', '-i', APP_CONTAINER_NAME, 'python', '-'], {
    input: script,
    encoding: 'utf8',
  })

  const lastLine = stdout
    .split(/\r?\n/)
    .map((line: string) => line.trim())
    .filter(Boolean)
    .at(-1)

  if (!lastLine) {
    throw new Error('No JSON output returned from trade history accountant seed helper')
  }

  return JSON.parse(lastLine) as T
}

function seedTradeHistoryFixture(label: string): TradeHistoryFixture {
  return runPythonInApp<TradeHistoryFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.commodity import Commodity
from models.session import Platform, UserSession
from models.trade import Trade, TradeStatus, TradeType
from models.user import User

label = ${JSON.stringify(label)}

def random_mobile():
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    return f"09{mobile_seed:09d}"

async def main():
    suffix = uuid.uuid4().hex[:10]
    relation_display_name = 'حسابدار فروش'

    async with AsyncSessionLocal() as db:
        viewer = User(
            account_name=f"pw_trade_viewer_{label}_{suffix}",
            mobile_number=random_mobile(),
            full_name='Playwright Trade Viewer',
            address='Playwright Trade History Viewer',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        counterparty_owner = User(
            account_name=f"pw_trade_owner_{label}_{suffix}",
            mobile_number=random_mobile(),
            full_name='Playwright Counterparty Owner',
            address='Playwright Counterparty Owner',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        counterparty_accountant = User(
            account_name=f"pw_trade_acct_{label}_{suffix}",
            mobile_number=random_mobile(),
            full_name=relation_display_name,
            address='Playwright Counterparty Accountant',
            role=UserRole.STANDARD,
            has_bot_access=False,
            max_sessions=1,
        )
        commodity = Commodity(name=f"PW Trade Commodity {suffix}")

        db.add_all([viewer, counterparty_owner, counterparty_accountant, commodity])
        await db.flush()

        relation = AccountantRelation(
            owner_user_id=counterparty_owner.id,
            accountant_user_id=counterparty_accountant.id,
            created_by_user_id=counterparty_owner.id,
            invitation_token=uuid.uuid4().hex,
            global_account_name=counterparty_accountant.account_name,
            relation_display_name=relation_display_name,
            mobile_number=counterparty_accountant.mobile_number,
            status=AccountantRelationStatus.ACTIVE,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            activated_at=datetime.now(timezone.utc),
        )
        db.add(relation)

        max_trade_number = (await db.scalar(select(func.max(Trade.trade_number)))) or 9999
        trade = Trade(
            trade_number=max_trade_number + 1,
            offer_id=None,
            offer_user_id=counterparty_accountant.id,
            offer_user_mobile=counterparty_accountant.mobile_number,
            responder_user_id=viewer.id,
            responder_user_mobile=viewer.mobile_number,
            actor_user_id=viewer.id,
            commodity_id=commodity.id,
            trade_type=TradeType.BUY,
            quantity=3,
            price=543210,
            status=TradeStatus.COMPLETED,
        )
        db.add(trade)

        refresh_token = create_refresh_token(subject=viewer.id)
        session = UserSession(
            user_id=viewer.id,
            device_name='Playwright Trade History Device',
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

        await db.commit()

    print(json.dumps({
        'viewer': {
            'userId': viewer.id,
            'accountName': viewer.account_name,
            'accessToken': access_token,
            'refreshToken': refresh_token,
        },
        'counterpartyOwnerId': counterparty_owner.id,
        'counterpartyOwnerAccountName': counterparty_owner.account_name,
        'counterpartyAccountantId': counterparty_accountant.id,
        'counterpartyAccountantAccountName': counterparty_accountant.account_name,
        'relationDisplayName': relation_display_name,
    }))

asyncio.run(main())
`)
}

function seedTradeRealtimeFixture(label: string): TradeRealtimeFixture {
  return runPythonInApp<TradeRealtimeFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

def random_mobile():
  mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  return f"09{mobile_seed:09d}"

async def main():
  suffix = uuid.uuid4().hex[:10]
  relation_display_name = 'حسابدار فروش'

  async with AsyncSessionLocal() as db:
    viewer = User(
      account_name=f"pw_trade_rt_viewer_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Trade Realtime Viewer',
      address='Playwright Trade Realtime Viewer',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    counterparty_owner = User(
      account_name=f"pw_trade_rt_owner_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Trade Realtime Owner',
      address='Playwright Trade Realtime Owner',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    counterparty_accountant = User(
      account_name=f"pw_trade_rt_acct_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name=relation_display_name,
      address='Playwright Trade Realtime Accountant',
      role=UserRole.STANDARD,
      has_bot_access=False,
      max_sessions=1,
    )
    commodity = Commodity(name=f"PW Trade Realtime Commodity {suffix[:6]}")

    db.add_all([viewer, counterparty_owner, counterparty_accountant, commodity])
    await db.flush()

    relation = AccountantRelation(
      owner_user_id=counterparty_owner.id,
      accountant_user_id=counterparty_accountant.id,
      created_by_user_id=counterparty_owner.id,
      invitation_token=uuid.uuid4().hex,
      global_account_name=counterparty_accountant.account_name,
      relation_display_name=relation_display_name,
      mobile_number=counterparty_accountant.mobile_number,
      status=AccountantRelationStatus.ACTIVE,
      expires_at=datetime.now(timezone.utc) + timedelta(days=30),
      activated_at=datetime.now(timezone.utc),
    )
    db.add(relation)

    offer = Offer(
      user_id=counterparty_accountant.id,
      actor_user_id=counterparty_accountant.id,
      home_server='foreign',
      offer_type=OfferType.SELL,
      commodity_id=commodity.id,
      quantity=3,
      remaining_quantity=3,
      price=543210,
      is_wholesale=True,
      lot_sizes=None,
      original_lot_sizes=None,
      status=OfferStatus.ACTIVE,
      notes='Playwright trade realtime offer',
    )
    db.add(offer)

    refresh_token = create_refresh_token(subject=viewer.id)
    session = UserSession(
      user_id=viewer.id,
      device_name='Playwright Trade Realtime Device',
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

    await db.commit()

  print(json.dumps({
    'viewer': {
      'userId': viewer.id,
      'accountName': viewer.account_name,
      'accessToken': access_token,
      'refreshToken': refresh_token,
    },
    'counterpartyOwnerId': counterparty_owner.id,
    'counterpartyOwnerAccountName': counterparty_owner.account_name,
    'counterpartyAccountantId': counterparty_accountant.id,
    'counterpartyAccountantAccountName': counterparty_accountant.account_name,
    'relationDisplayName': relation_display_name,
    'offerId': offer.id,
    'offerQuantity': 3,
  }))

asyncio.run(main())
`)
}

function seedPublicProfileHistoryFilterFixture(label: string): PublicProfileHistoryFilterFixture {
  return runPythonInApp<PublicProfileHistoryFilterFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.commodity import Commodity
from models.session import Platform, UserSession
from models.trade import Trade, TradeStatus, TradeType
from models.user import User

label = ${JSON.stringify(label)}

def random_mobile():
  mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  return f"09{mobile_seed:09d}"

async def main():
  suffix = uuid.uuid4().hex[:10]
  now = datetime.utcnow()
  narrow_from = (now - timedelta(days=45)).date().isoformat()
  narrow_to = now.date().isoformat()
  wide_from = (now - timedelta(days=365)).date().isoformat()
  wide_to = now.date().isoformat()

  async with AsyncSessionLocal() as db:
    viewer = User(
      account_name=f"pw_pp_hist_viewer_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Public Profile History Viewer',
      address='Playwright Public Profile History Viewer',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    target = User(
      account_name=f"pw_pp_hist_target_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Public Profile History Target',
      address='Playwright Public Profile History Target',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    other = User(
      account_name=f"pw_pp_hist_other_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Public Profile History Other',
      address='Playwright Public Profile History Other',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    gold = Commodity(name=f"PW History Gold {suffix[:5]}")
    silver = Commodity(name=f"PW History Silver {suffix[:5]}")

    db.add_all([viewer, target, other, gold, silver])
    await db.flush()

    max_trade_number = (await db.scalar(select(func.max(Trade.trade_number)))) or 9999

    recent_mutual = Trade(
      trade_number=max_trade_number + 1,
      offer_id=None,
      offer_user_id=target.id,
      offer_user_mobile=target.mobile_number,
      responder_user_id=viewer.id,
      responder_user_mobile=viewer.mobile_number,
      actor_user_id=viewer.id,
      commodity_id=gold.id,
      trade_type=TradeType.BUY,
      quantity=1,
      price=111000,
      status=TradeStatus.COMPLETED,
      created_at=now - timedelta(days=10),
    )
    medium_mutual = Trade(
      trade_number=max_trade_number + 2,
      offer_id=None,
      offer_user_id=viewer.id,
      offer_user_mobile=viewer.mobile_number,
      responder_user_id=target.id,
      responder_user_mobile=target.mobile_number,
      actor_user_id=viewer.id,
      commodity_id=silver.id,
      trade_type=TradeType.SELL,
      quantity=2,
      price=222000,
      status=TradeStatus.COMPLETED,
      created_at=now - timedelta(days=70),
    )
    old_mutual = Trade(
      trade_number=max_trade_number + 3,
      offer_id=None,
      offer_user_id=target.id,
      offer_user_mobile=target.mobile_number,
      responder_user_id=viewer.id,
      responder_user_mobile=viewer.mobile_number,
      actor_user_id=viewer.id,
      commodity_id=gold.id,
      trade_type=TradeType.BUY,
      quantity=3,
      price=333000,
      status=TradeStatus.COMPLETED,
      created_at=now - timedelta(days=200),
    )
    self_other = Trade(
      trade_number=max_trade_number + 4,
      offer_id=None,
      offer_user_id=other.id,
      offer_user_mobile=other.mobile_number,
      responder_user_id=viewer.id,
      responder_user_mobile=viewer.mobile_number,
      actor_user_id=viewer.id,
      commodity_id=gold.id,
      trade_type=TradeType.BUY,
      quantity=4,
      price=444000,
      status=TradeStatus.COMPLETED,
      created_at=now - timedelta(days=15),
    )
    db.add_all([recent_mutual, medium_mutual, old_mutual, self_other])

    refresh_token = create_refresh_token(subject=viewer.id)
    session = UserSession(
      user_id=viewer.id,
      device_name='Playwright Public Profile History Device',
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

    await db.commit()

  print(json.dumps({
    'viewer': {
      'userId': viewer.id,
      'accountName': viewer.account_name,
      'accessToken': access_token,
      'refreshToken': refresh_token,
    },
    'targetUserId': target.id,
    'targetAccountName': target.account_name,
    'goldCommodityName': gold.name,
    'silverCommodityName': silver.name,
    'recentMutualTradeNumber': recent_mutual.trade_number,
    'mediumMutualTradeNumber': medium_mutual.trade_number,
    'oldMutualTradeNumber': old_mutual.trade_number,
    'otherCounterpartyTradeNumber': self_other.trade_number,
    'narrowFromDate': narrow_from,
    'narrowToDate': narrow_to,
    'wideFromDate': wide_from,
    'wideToDate': wide_to,
  }))

asyncio.run(main())
`)
}

function seedPublicProfilePresenceFixture(label: string): PublicProfilePresenceFixture {
  return runPythonInApp<PublicProfilePresenceFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

def random_mobile():
  mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  return f"09{mobile_seed:09d}"

async def main():
  suffix = uuid.uuid4().hex[:10]
  now = datetime.utcnow()

  async with AsyncSessionLocal() as db:
    viewer = User(
      account_name=f"pw_pp_presence_viewer_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Public Profile Presence Viewer',
      address='Playwright Public Profile Presence Viewer',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    online_target = User(
      account_name=f"pw_pp_presence_online_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Public Profile Online Target',
      address='Playwright Public Profile Online Target',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
      last_seen_at=now,
    )
    offline_target = User(
      account_name=f"pw_pp_presence_offline_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Public Profile Offline Target',
      address='Playwright Public Profile Offline Target',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
      last_seen_at=now - timedelta(minutes=10),
    )

    db.add_all([viewer, online_target, offline_target])
    await db.flush()

    refresh_token = create_refresh_token(subject=viewer.id)
    session = UserSession(
      user_id=viewer.id,
      device_name='Playwright Public Profile Presence Device',
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

    await db.commit()

  print(json.dumps({
    'viewer': {
      'userId': viewer.id,
      'accountName': viewer.account_name,
      'accessToken': access_token,
      'refreshToken': refresh_token,
    },
    'onlineTargetUserId': online_target.id,
    'onlineTargetAccountName': online_target.account_name,
    'offlineTargetUserId': offline_target.id,
    'offlineTargetAccountName': offline_target.account_name,
  }))

asyncio.run(main())
`)
}

function seedPublicProfileBlockedMarketFixture(label: string): PublicProfileBlockedMarketFixture {
  return runPythonInApp<PublicProfileBlockedMarketFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

def random_mobile():
  mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  return f"09{mobile_seed:09d}"

async def main():
  suffix = uuid.uuid4().hex[:10]
  offer_note = f"pw_block_market_{label}_{suffix}"

  async with AsyncSessionLocal() as db:
    viewer = User(
      account_name=f"pw_pp_block_viewer_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Public Profile Block Viewer',
      address='Playwright Public Profile Block Viewer',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
      can_block_users=True,
      max_blocked_users=5,
    )
    target = User(
      account_name=f"pw_pp_block_target_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Public Profile Block Target',
      address='Playwright Public Profile Block Target',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    commodity = Commodity(name=f"PW Block Commodity {suffix[:5]}")

    db.add_all([viewer, target, commodity])
    await db.flush()

    offer = Offer(
      user_id=target.id,
      actor_user_id=target.id,
      home_server='foreign',
      offer_type=OfferType.SELL,
      commodity_id=commodity.id,
      quantity=2,
      remaining_quantity=2,
      price=321000,
      is_wholesale=True,
      lot_sizes=None,
      original_lot_sizes=None,
      status=OfferStatus.ACTIVE,
      notes=offer_note,
      created_at=datetime.now(timezone.utc),
    )
    db.add(offer)

    refresh_token = create_refresh_token(subject=viewer.id)
    session = UserSession(
      user_id=viewer.id,
      device_name='Playwright Public Profile Block Device',
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

    await db.commit()

  print(json.dumps({
    'viewer': {
      'userId': viewer.id,
      'accountName': viewer.account_name,
      'accessToken': access_token,
      'refreshToken': refresh_token,
    },
    'targetUserId': target.id,
    'targetAccountName': target.account_name,
    'offerNote': offer_note,
    'tradeAmount': 2,
  }))

asyncio.run(main())
`)
}

async function waitForBackendReady(request: APIRequestContext) {
  await expect
    .poll(async () => {
      try {
        const response = await request.get(`${BACKEND_BASE_URL}/api/config`)
        return response.ok()
      } catch {
        return false
      }
    }, { timeout: 60000 })
    .toBe(true)
}

async function loginWithSeededSession(page: Page, session: SessionUser) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, session)
}

async function executeTrade(request: APIRequestContext, accessToken: string, offerId: number, quantity: number) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/trades/`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    data: {
      offer_id: offerId,
      quantity,
    },
  })

  expect(response.status()).toBe(201)
  return response.json()
}

test.describe('Trade history accountant context', () => {
  test('own public profile history resolves accountant counterpart rows to the owner profile', async ({ page, request }) => {
    await waitForBackendReady(request)
    const fixture = seedTradeHistoryFixture('public_profile_history')

    await loginWithSeededSession(page, fixture.viewer)
    await page.goto(`/users/${fixture.viewer.userId}?account_name=${encodeURIComponent(fixture.viewer.accountName)}`)

    const profileView = page.locator('.public-profile-view')
    await expect(profileView).toContainText(fixture.viewer.accountName)

    const historyHeader = page.locator('.ds-accordion-header').filter({ hasText: 'تاریخچه معاملات من' }).first()
    await historyHeader.click()

    const counterpartyLink = page.locator('.profile-link-btn').filter({ hasText: fixture.relationDisplayName }).first()
    await expect(counterpartyLink).toBeVisible()
    await expect(profileView).not.toContainText(fixture.counterpartyAccountantAccountName)

    await counterpartyLink.click()

    await expect(page).toHaveURL(new RegExp(`/users/${fixture.counterpartyOwnerId}`))
    await expect(page).toHaveURL(new RegExp(`highlight_accountant_user_id=${fixture.counterpartyAccountantId}`))
    await expect(profileView).toContainText('نمایش پروفایل مالک اصلی')
    await expect(profileView).toContainText(fixture.relationDisplayName)
    await expect(profileView).toContainText(fixture.counterpartyOwnerAccountName)
  })

  test('trade-created realtime keeps relation-aware toast, history rows, and owner profile targets stable', async ({ page, request }) => {
    await waitForBackendReady(request)
    const fixture = seedTradeRealtimeFixture('trade_created_realtime')

    await loginWithSeededSession(page, fixture.viewer)
    await page.goto(`/users/${fixture.viewer.userId}?account_name=${encodeURIComponent(fixture.viewer.accountName)}`)
    await page.waitForTimeout(1200)

    await executeTrade(request, fixture.viewer.accessToken, fixture.offerId, fixture.offerQuantity)

    const toast = page.locator('.toast-card-floating').filter({ hasText: fixture.relationDisplayName })
    await expect(toast).toBeVisible({ timeout: 30000 })
    await expect(toast).not.toContainText(fixture.counterpartyAccountantAccountName)

    await expect
      .poll(async () => {
        const response = await request.get(`${BACKEND_BASE_URL}/api/trades/my`, {
          headers: {
            Authorization: `Bearer ${fixture.viewer.accessToken}`,
          },
        })
        expect(response.ok()).toBeTruthy()
        const body = await response.json()
        const trade = Array.isArray(body)
          ? body.find((item: any) => Number(item?.offer_id) === fixture.offerId)
          : null
        if (!trade) {
          return null
        }
        return {
          offer_user_name: trade.offer_user_name,
          offer_user_profile_user_id: Number(trade.offer_user_profile_user_id),
          offer_user_highlight_accountant_user_id: Number(trade.offer_user_highlight_accountant_user_id),
          offer_user_highlight_accountant_relation_display_name: trade.offer_user_highlight_accountant_relation_display_name,
        }
      }, { timeout: 30000 })
      .toEqual({
        offer_user_name: fixture.relationDisplayName,
        offer_user_profile_user_id: fixture.counterpartyOwnerId,
        offer_user_highlight_accountant_user_id: fixture.counterpartyAccountantId,
        offer_user_highlight_accountant_relation_display_name: fixture.relationDisplayName,
      })

    const profileView = page.locator('.public-profile-view')
    const historyHeader = page.locator('.ds-accordion-header').filter({ hasText: 'تاریخچه معاملات من' }).first()
    await historyHeader.click()

    const counterpartyLink = page.locator('.profile-link-btn').filter({ hasText: fixture.relationDisplayName }).first()
    await expect(counterpartyLink).toBeVisible({ timeout: 30000 })
    await expect(profileView).not.toContainText(fixture.counterpartyAccountantAccountName)

    await counterpartyLink.click()

    await expect(page).toHaveURL(new RegExp(`/users/${fixture.counterpartyOwnerId}`))
    await expect(page).toHaveURL(new RegExp(`highlight_accountant_user_id=${fixture.counterpartyAccountantId}`))
    await expect(profileView).toContainText('نمایش پروفایل مالک اصلی')
    await expect(profileView).toContainText(fixture.relationDisplayName)
    await expect(profileView).toContainText(fixture.counterpartyOwnerAccountName)
  })

  test('mutual public-profile history applies date and commodity filters and reuses the same query state for Excel export', async ({ page, request }) => {
    await waitForBackendReady(request)
    const fixture = seedPublicProfileHistoryFilterFixture('mutual_filter_export')

    await loginWithSeededSession(page, fixture.viewer)
    await page.goto(`/users/${fixture.targetUserId}?account_name=${encodeURIComponent(fixture.targetAccountName)}`)

    const profileView = page.locator('.public-profile-view')
    await expect(profileView).toContainText(fixture.targetAccountName)

    const historyHeader = page.locator('.ds-accordion-header').filter({ hasText: 'تاریخچه معاملات مشترک' }).first()
    await historyHeader.click()

    await expect.poll(async () => page.locator('.mini-trade-card').count(), { timeout: 30000 }).toBe(3)
    await expect(profileView).toContainText(`#${fixture.recentMutualTradeNumber}`)
    await expect(profileView).toContainText(`#${fixture.mediumMutualTradeNumber}`)
    await expect(profileView).toContainText(`#${fixture.oldMutualTradeNumber}`)

    const fromInput = page.locator('.history-filter-field').filter({ hasText: 'از تاریخ' }).locator('input')
    const toInput = page.locator('.history-filter-field').filter({ hasText: 'تا تاریخ' }).locator('input')
    const commodityInput = page.locator('.history-filter-field').filter({ hasText: 'کالا' }).locator('input')

    await fromInput.fill(fixture.narrowFromDate)
    await toInput.fill(fixture.narrowToDate)
    await commodityInput.fill(fixture.goldCommodityName)

    const filteredResponsePromise = page.waitForResponse((response) => {
      if (!response.ok()) return false
      const url = new URL(response.url())
      return url.pathname === `/api/trades/with/${fixture.targetUserId}` && url.searchParams.get('commodity_query') === fixture.goldCommodityName
    })
    await page.locator('.history-action-btn.primary').filter({ hasText: 'اعمال فیلتر' }).click()
    const filteredResponse = await filteredResponsePromise
    const filteredUrl = new URL(filteredResponse.url())
    expect(filteredUrl.searchParams.get('from_date')).toBe(fixture.narrowFromDate)
    expect(filteredUrl.searchParams.get('to_date')).toBe(fixture.narrowToDate)
    expect(filteredUrl.searchParams.get('commodity_query')).toBe(fixture.goldCommodityName)

    await expect.poll(async () => page.locator('.mini-trade-card').count(), { timeout: 30000 }).toBe(1)
    await expect(profileView).toContainText(`#${fixture.recentMutualTradeNumber}`)
    await expect(profileView).not.toContainText(`#${fixture.mediumMutualTradeNumber}`)
    await expect(profileView).not.toContainText(`#${fixture.oldMutualTradeNumber}`)

    const exportResponsePromise = page.waitForResponse((response) => {
      if (!response.ok()) return false
      const url = new URL(response.url())
      return url.pathname === `/api/trades/with/${fixture.targetUserId}/export` && url.searchParams.get('format') === 'excel'
    })
    await page.locator('.history-action-btn').filter({ hasText: 'خروجی Excel' }).click()
    const exportResponse = await exportResponsePromise
    const exportUrl = new URL(exportResponse.url())
    expect(exportUrl.searchParams.get('from_date')).toBe(fixture.narrowFromDate)
    expect(exportUrl.searchParams.get('to_date')).toBe(fixture.narrowToDate)
    expect(exportUrl.searchParams.get('commodity_query')).toBe(fixture.goldCommodityName)
  })

  test('self public-profile history preset and custom date ranges drive the same export query state', async ({ page, request }) => {
    await waitForBackendReady(request)
    const fixture = seedPublicProfileHistoryFilterFixture('self_preset_export')

    await loginWithSeededSession(page, fixture.viewer)
    await page.goto(`/users/${fixture.viewer.userId}?account_name=${encodeURIComponent(fixture.viewer.accountName)}`)

    const profileView = page.locator('.public-profile-view')
    await expect(profileView).toContainText(fixture.viewer.accountName)

    const historyHeader = page.locator('.ds-accordion-header').filter({ hasText: 'تاریخچه معاملات من' }).first()
    await historyHeader.click()

    await expect.poll(async () => page.locator('.mini-trade-card').count(), { timeout: 30000 }).toBe(4)
    await expect(profileView).toContainText(`#${fixture.otherCounterpartyTradeNumber}`)

    const presetResponsePromise = page.waitForResponse((response) => {
      if (!response.ok()) return false
      const url = new URL(response.url())
      return url.pathname === '/api/trades/my' && Boolean(url.searchParams.get('from_date')) && Boolean(url.searchParams.get('to_date'))
    })
    await page.locator('.history-chip').filter({ hasText: '۳ ماه' }).click()
    await presetResponsePromise

    await expect.poll(async () => page.locator('.mini-trade-card').count(), { timeout: 30000 }).toBe(3)
    await expect(profileView).not.toContainText(`#${fixture.oldMutualTradeNumber}`)

    const fromInput = page.locator('.history-filter-field').filter({ hasText: 'از تاریخ' }).locator('input')
    const toInput = page.locator('.history-filter-field').filter({ hasText: 'تا تاریخ' }).locator('input')
    const presetFromDate = await fromInput.inputValue()
    const presetToDate = await toInput.inputValue()

    const presetExportPromise = page.waitForResponse((response) => {
      if (!response.ok()) return false
      const url = new URL(response.url())
      return url.pathname === '/api/trades/my/export' && url.searchParams.get('format') === 'excel'
    })
    await page.locator('.history-action-btn').filter({ hasText: 'خروجی Excel' }).click()
    const presetExportResponse = await presetExportPromise
    const presetExportUrl = new URL(presetExportResponse.url())
    expect(presetExportUrl.searchParams.get('from_date')).toBe(presetFromDate)
    expect(presetExportUrl.searchParams.get('to_date')).toBe(presetToDate)

    await fromInput.fill(fixture.wideFromDate)
    await toInput.fill(fixture.wideToDate)
    const customResponsePromise = page.waitForResponse((response) => {
      if (!response.ok()) return false
      const url = new URL(response.url())
      return url.pathname === '/api/trades/my' && url.searchParams.get('from_date') === fixture.wideFromDate && url.searchParams.get('to_date') === fixture.wideToDate
    })
    await page.locator('.history-action-btn.primary').filter({ hasText: 'اعمال فیلتر' }).click()
    await customResponsePromise

    await expect.poll(async () => page.locator('.mini-trade-card').count(), { timeout: 30000 }).toBe(4)
    await expect(profileView).toContainText(`#${fixture.oldMutualTradeNumber}`)

    const customExportPromise = page.waitForResponse((response) => {
      if (!response.ok()) return false
      const url = new URL(response.url())
      return url.pathname === '/api/trades/my/export' && url.searchParams.get('format') === 'pdf'
    })
    await page.locator('.history-action-btn').filter({ hasText: 'خروجی PDF' }).click()
    const customExportResponse = await customExportPromise
    const customExportUrl = new URL(customExportResponse.url())
    expect(customExportUrl.searchParams.get('from_date')).toBe(fixture.wideFromDate)
    expect(customExportUrl.searchParams.get('to_date')).toBe(fixture.wideToDate)
  })

  test('public profile presence renders online and last-seen states from the shared presence contract', async ({ page, request }) => {
    await waitForBackendReady(request)
    const fixture = seedPublicProfilePresenceFixture('presence_rendering')

    await loginWithSeededSession(page, fixture.viewer)

    await page.goto(`/users/${fixture.onlineTargetUserId}?account_name=${encodeURIComponent(fixture.onlineTargetAccountName)}`)
    await expect(page.locator('.public-profile-view')).toContainText(fixture.onlineTargetAccountName)

    const onlinePresence = page.locator('.profile-presence-status')
    await expect(onlinePresence).toHaveText('آنلاین')
    await expect(onlinePresence).toHaveClass(/online/)

    await page.goto(`/users/${fixture.offlineTargetUserId}?account_name=${encodeURIComponent(fixture.offlineTargetAccountName)}`)
    await expect(page.locator('.public-profile-view')).toContainText(fixture.offlineTargetAccountName)

    const offlinePresence = page.locator('.profile-presence-status')
    await expect(offlinePresence).toContainText('آخرین بازدید')
    await expect(offlinePresence).toContainText('دقیقه پیش')
    await expect(offlinePresence).not.toHaveClass(/online/)
  })

  test('blocking a user from public profile keeps market trade execution errors generic', async ({ page, request }) => {
    await waitForBackendReady(request)
    const fixture = seedPublicProfileBlockedMarketFixture('blocked_market_generic')

    await loginWithSeededSession(page, fixture.viewer)
    await page.goto(`/users/${fixture.targetUserId}?account_name=${encodeURIComponent(fixture.targetAccountName)}`)
    await expect(page.locator('.public-profile-view')).toContainText(fixture.targetAccountName)

    const blockButton = page.locator('.visitor-action-btn').filter({ hasText: 'بلاک کاربر' }).first()
    await expect(blockButton).toBeVisible()

    const dialogMessages: string[] = []
    const dialogHandler = async (dialog: any) => {
      dialogMessages.push(dialog.message())
      await dialog.accept()
    }
    page.on('dialog', dialogHandler)
    await blockButton.click()
    await expect.poll(() => dialogMessages.length, { timeout: 30000 }).toBeGreaterThanOrEqual(2)
    page.off('dialog', dialogHandler)

    expect(dialogMessages[0]).toContain(`بلاک کاربر ${fixture.targetAccountName}`)
    expect(dialogMessages[1]).toContain('مسدود شد')

    await expect(page.locator('.visitor-action-btn').filter({ hasText: 'رفع بلاک' }).first()).toBeVisible()

    await page.goto('/market')
    const offerCard = page.locator('.offer-card-wrap', { hasText: fixture.offerNote }).first()
    await expect(offerCard).toBeVisible()

    const executeButton = offerCard.locator('.trade-btn').filter({ hasText: `${fixture.tradeAmount} عدد` }).first()
    const tradeResponsePromise = page.waitForResponse((response) => {
      if (response.request().method() !== 'POST') return false
      const url = new URL(response.url())
      return url.pathname === '/api/trades/'
    })

    await executeButton.dblclick()
    const tradeResponse = await tradeResponsePromise
    expect(tradeResponse.status()).toBe(400)
    await expect(offerCard.locator('.trade-btn').filter({ hasText: `${fixture.tradeAmount} عدد` }).first()).toBeVisible()

    const payload = await tradeResponse.json()
    expect(payload?.detail).toBe('امکان انجام این معامله وجود ندارد.')

    const tradeErrorToast = page.locator('div').filter({ hasText: 'امکان انجام این معامله وجود ندارد.' }).last()
    await expect(tradeErrorToast).toBeVisible()
    await expect(tradeErrorToast).not.toContainText('بلاک')
    await expect(tradeErrorToast).not.toContainText(fixture.targetAccountName)
  })
})