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
})