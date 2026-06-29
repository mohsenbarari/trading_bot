/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'

import { primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SessionUser {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
  roleLabel: string
}

interface OwnerCustomerRelationPayload {
  id: number
  owner_user_id?: number | null
  customer_user_id?: number | null
  invitation_account_name?: string | null
  customer_account_name?: string | null
  mobile_number?: string | null
  management_name: string
  customer_tier: 'tier1' | 'tier2'
  commission_rate?: number | null
  min_trade_quantity?: number | null
  max_trade_quantity?: number | null
  max_daily_trades?: number | null
  max_daily_commodity_volume?: number | null
  status: 'pending' | 'active' | 'expired' | 'revoked' | 'deleted'
  invitation_token?: string | null
  registration_link?: string | null
}

type PendingOwnerCustomerRelation = OwnerCustomerRelationPayload & {
  status: 'pending'
  invitation_token: string
  registration_link: string
}

type ActiveOwnerCustomerRelation = OwnerCustomerRelationPayload & {
  status: 'active'
  customer_user_id: number
}

interface DeletedCustomerPayload {
  relationStatus: string | null
  relationDeletedAt: string | null
  userDeleted: boolean | null
}

interface AdminHistoryFixture {
  superAdmin: SessionUser
  ownerUserId: number
  ownerAccountName: string
  customerUserId: number
  customerAccountName: string
  customerManagementName: string
}

interface MutualHistoryFixture {
  owner: SessionUser
  outsider: SessionUser
  ownerUserId: number
  outsiderUserId: number
  customerManagementName: string
  tradeQuantity: number
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
    throw new Error('No JSON output returned from trading_bot_app customer flow helper')
  }

  return JSON.parse(lastLine) as T
}

function seedSessionUser(label: string, roleTag: 'standard' | 'super_admin' = 'standard'): SessionUser {
  return runPythonInApp<SessionUser>(`
import asyncio
import json
import uuid
from datetime import timedelta

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}
role_tag = ${JSON.stringify(roleTag)}

async def main():
    suffix = uuid.uuid4().hex[:10]
    account_name = f"pw_{label}_{suffix}"
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    role = UserRole.SUPER_ADMIN if role_tag == 'super_admin' else UserRole.STANDARD

    async with AsyncSessionLocal() as db:
        user = User(
            account_name=account_name,
            mobile_number=f"09{mobile_seed:09d}",
            full_name=account_name,
            address='Playwright Customer Owner Flow',
            role=role,
            has_bot_access=True,
            max_sessions=1,
            max_customers=5,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Customer Owner Flow Device',
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

        await db.commit()

    print(json.dumps({
        'userId': user.id,
        'accountName': user.account_name,
        'accessToken': access_token,
        'refreshToken': refresh_token,
        'roleLabel': role.value,
    }))

asyncio.run(main())
`)
}

function inspectDeletedCustomer(customerUserId: number): DeletedCustomerPayload {
  return runPythonInApp<DeletedCustomerPayload>(`
import asyncio
import json

from sqlalchemy import select

from core.db import AsyncSessionLocal
from models.customer_relation import CustomerRelation
from models.user import User

customer_user_id = ${JSON.stringify(customerUserId)}

async def main():
    async with AsyncSessionLocal() as db:
        relation_stmt = select(CustomerRelation).where(CustomerRelation.customer_user_id == customer_user_id)
        relation = (await db.execute(relation_stmt)).scalar_one_or_none()
        user_stmt = select(User).where(User.id == customer_user_id)
        user = (await db.execute(user_stmt)).scalar_one_or_none()

    print(json.dumps({
        'relationStatus': getattr(relation.status, 'value', None) if relation else None,
        'relationDeletedAt': relation.deleted_at.isoformat() if relation and relation.deleted_at else None,
        'userDeleted': getattr(user, 'is_deleted', None),
    }))

asyncio.run(main())
`)
}

function seedAdminHistoryFixture(label: string): AdminHistoryFixture {
  return runPythonInApp<AdminHistoryFixture>(`
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
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.session import Platform, UserSession
from models.trade import Trade, TradeStatus, TradeType
from models.user import User

label = ${JSON.stringify(label)}

def random_mobile():
  mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  return f"09{mobile_seed:09d}"

async def main():
  suffix = uuid.uuid4().hex[:10]
  customer_management_name = 'مشتری تاریخچه‌ای'

  async with AsyncSessionLocal() as db:
    super_admin = User(
      account_name=f"pw_customer_hist_admin_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Customer History Admin',
      address='Playwright Customer History Admin',
      role=UserRole.SUPER_ADMIN,
      has_bot_access=True,
      max_sessions=1,
    )
    owner = User(
      account_name=f"pw_customer_hist_owner_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Customer History Owner',
      address='Playwright Customer History Owner',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
      max_customers=5,
    )
    customer = User(
      account_name=f"pw_customer_hist_customer_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name=customer_management_name,
      address='Playwright Customer History Customer',
      role=UserRole.STANDARD,
      has_bot_access=False,
      max_sessions=1,
    )
    outsider = User(
      account_name=f"pw_customer_hist_outsider_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Customer History Outsider',
      address='Playwright Customer History Outsider',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    commodity = Commodity(name=f"PW Customer History Commodity {suffix}")

    db.add_all([super_admin, owner, customer, outsider, commodity])
    await db.flush()

    relation = CustomerRelation(
      owner_user_id=owner.id,
      customer_user_id=customer.id,
      created_by_user_id=owner.id,
      invitation_token=uuid.uuid4().hex,
      management_name=customer_management_name,
      customer_tier=CustomerTier.TIER_2,
      commission_rate=1.25,
      status=CustomerRelationStatus.ACTIVE,
      activated_at=datetime.now(timezone.utc),
      expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(relation)

    max_trade_number = (await db.scalar(select(func.max(Trade.trade_number)))) or 9999
    now = datetime.now(timezone.utc)

    owner_history_trade = Trade(
      trade_number=max_trade_number + 1,
      offer_id=None,
      offer_user_id=outsider.id,
      offer_user_mobile=outsider.mobile_number,
      responder_user_id=owner.id,
      responder_user_mobile=owner.mobile_number,
      actor_user_id=customer.id,
      commodity_id=commodity.id,
      trade_type=TradeType.BUY,
      quantity=2,
      price=501000,
      status=TradeStatus.COMPLETED,
      created_at=now - timedelta(minutes=2),
    )
    customer_history_trade = Trade(
      trade_number=max_trade_number + 2,
      offer_id=None,
      offer_user_id=outsider.id,
      offer_user_mobile=outsider.mobile_number,
      responder_user_id=customer.id,
      responder_user_mobile=customer.mobile_number,
      actor_user_id=customer.id,
      commodity_id=commodity.id,
      trade_type=TradeType.BUY,
      quantity=5,
      price=602200,
      status=TradeStatus.COMPLETED,
      created_at=now - timedelta(minutes=1),
    )
    db.add_all([owner_history_trade, customer_history_trade])

    refresh_token = create_refresh_token(subject=super_admin.id)
    session = UserSession(
      user_id=super_admin.id,
      device_name='Playwright Customer History Admin Device',
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
      subject=super_admin.id,
      expires_delta=timedelta(minutes=60),
      session_id=str(session.id),
    )

    await db.commit()

  print(json.dumps({
    'superAdmin': {
      'userId': super_admin.id,
      'accountName': super_admin.account_name,
      'accessToken': access_token,
      'refreshToken': refresh_token,
      'roleLabel': super_admin.role.value,
    },
    'ownerUserId': owner.id,
    'ownerAccountName': owner.account_name,
    'customerUserId': customer.id,
    'customerAccountName': customer.account_name,
    'customerManagementName': customer_management_name,
  }))

asyncio.run(main())
`)
}

function seedMutualHistoryFixture(label: string): MutualHistoryFixture {
  return runPythonInApp<MutualHistoryFixture>(`
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
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.session import Platform, UserSession
from models.trade import Trade, TradeStatus, TradeType
from models.user import User

label = ${JSON.stringify(label)}

def random_mobile():
  mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  return f"09{mobile_seed:09d}"

def build_session_payload(user, session_id, refresh_token, access_token):
  return {
    'userId': user.id,
    'accountName': user.account_name,
    'accessToken': access_token,
    'refreshToken': refresh_token,
    'roleLabel': user.role.value,
  }

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
  return build_session_payload(user, session.id, refresh_token, access_token)

async def main():
  suffix = uuid.uuid4().hex[:10]
  customer_management_name = f"مشتری تاریخچه مشترک {suffix[:4]}"
  trade_quantity = 7

  async with AsyncSessionLocal() as db:
    owner = User(
      account_name=f"pw_customer_mutual_owner_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Customer Mutual Owner',
      address='Playwright Customer Mutual Owner',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
      max_customers=5,
    )
    outsider = User(
      account_name=f"pw_customer_mutual_outsider_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Customer Mutual Outsider',
      address='Playwright Customer Mutual Outsider',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    customer = User(
      account_name=f"pw_customer_mutual_customer_{label}_{suffix}",
      mobile_number=random_mobile(),
      full_name=customer_management_name,
      address='Playwright Customer Mutual Customer',
      role=UserRole.STANDARD,
      has_bot_access=False,
      max_sessions=1,
    )
    commodity = Commodity(name=f"PW Customer Mutual Commodity {suffix}")

    db.add_all([owner, outsider, customer, commodity])
    await db.flush()

    relation = CustomerRelation(
      owner_user_id=owner.id,
      customer_user_id=customer.id,
      created_by_user_id=owner.id,
      invitation_token=uuid.uuid4().hex,
      management_name=customer_management_name,
      customer_tier=CustomerTier.TIER_2,
      commission_rate=1.5,
      status=CustomerRelationStatus.ACTIVE,
      activated_at=datetime.now(timezone.utc),
      expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(relation)

    max_trade_number = (await db.scalar(select(func.max(Trade.trade_number)))) or 9999
    trade = Trade(
      trade_number=max_trade_number + 1,
      offer_id=None,
      offer_user_id=outsider.id,
      offer_user_mobile=outsider.mobile_number,
      responder_user_id=owner.id,
      responder_user_mobile=owner.mobile_number,
      actor_user_id=customer.id,
      commodity_id=commodity.id,
      trade_type=TradeType.BUY,
      quantity=trade_quantity,
      price=713500,
      status=TradeStatus.COMPLETED,
      created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(trade)

    owner_session = await create_session_bundle(db, owner, 'Playwright Customer Mutual Owner Device')
    outsider_session = await create_session_bundle(db, outsider, 'Playwright Customer Mutual Outsider Device')

    await db.commit()

  print(json.dumps({
    'owner': owner_session,
    'outsider': outsider_session,
    'ownerUserId': owner.id,
    'outsiderUserId': outsider.id,
    'customerManagementName': customer_management_name,
    'tradeQuantity': trade_quantity,
  }))

asyncio.run(main())
`)
}

function authHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
}

function requireValue<T>(value: T | null | undefined, message: string): T {
  if (value == null) {
    throw new Error(message)
  }
  return value
}

function toRelativeRegistrationPath(registrationLink: string): string {
  try {
    const url = new URL(registrationLink)
    return `${url.pathname}${url.search}`
  } catch {
    return registrationLink
  }
}

function seedRegistrationOtp(token: string, code = '12345') {
  execFileSync('docker', [
    'exec',
    'trading_bot_redis',
    'redis-cli',
    'SETEX',
    `reg_otp:${token}`,
    '300',
    code,
  ], { encoding: 'utf8' })
}

function seedRegistrationVerified(token: string) {
  execFileSync('docker', [
    'exec',
    'trading_bot_redis',
    'redis-cli',
    'SETEX',
    `reg_verified:${token}`,
    '600',
    '1',
  ], { encoding: 'utf8' })
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

async function completeRegistrationViaApi(
  request: APIRequestContext,
  token: string,
  address: string,
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/auth/register-complete`, {
    data: {
      token,
      address,
    },
  })

  expect(response.ok()).toBeTruthy()
}

async function setAuthTokens(page: Page, session: SessionUser) {
  await primeAuthSession(page, session.accessToken, session.refreshToken, {
    currentUserSummary: { role: session.roleLabel },
  })
}

async function fetchOwnerCustomerRelations(request: APIRequestContext, accessToken: string): Promise<OwnerCustomerRelationPayload[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/customers/owner-relations`, {
    headers: authHeaders(accessToken),
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<OwnerCustomerRelationPayload[]>
}

async function findOwnerCustomerRelation(
  request: APIRequestContext,
  accessToken: string,
  predicate: (relation: OwnerCustomerRelationPayload) => boolean,
): Promise<OwnerCustomerRelationPayload | undefined> {
  return (await fetchOwnerCustomerRelations(request, accessToken)).find(predicate)
}

function isPendingOwnerCustomerRelation(
  relation: OwnerCustomerRelationPayload | null | undefined,
): relation is PendingOwnerCustomerRelation {
  return Boolean(
    relation
      && relation.status === 'pending'
      && typeof relation.invitation_token === 'string'
      && relation.invitation_token.length > 0
      && typeof relation.registration_link === 'string'
      && relation.registration_link.length > 0,
  )
}

function isActiveOwnerCustomerRelation(
  relation: OwnerCustomerRelationPayload | null | undefined,
): relation is ActiveOwnerCustomerRelation {
  return Boolean(
    relation
      && relation.status === 'active'
      && typeof relation.customer_user_id === 'number',
  )
}

async function waitForPendingOwnerCustomerRelation(
  request: APIRequestContext,
  accessToken: string,
  predicate: (relation: OwnerCustomerRelationPayload) => boolean,
): Promise<PendingOwnerCustomerRelation> {
  await expect
    .poll(async () => isPendingOwnerCustomerRelation(await findOwnerCustomerRelation(request, accessToken, predicate)), { timeout: 30000 })
    .toBe(true)

  const relation = await findOwnerCustomerRelation(request, accessToken, predicate)
  if (!isPendingOwnerCustomerRelation(relation)) {
    throw new Error('Pending customer relation was not found')
  }

  return relation
}

async function waitForActiveOwnerCustomerRelation(
  request: APIRequestContext,
  accessToken: string,
  relationId: number,
): Promise<ActiveOwnerCustomerRelation> {
  await expect
    .poll(async () => isActiveOwnerCustomerRelation(
      await findOwnerCustomerRelation(request, accessToken, (relation) => relation.id === relationId),
    ), { timeout: 30000 })
    .toBe(true)

  const relation = await findOwnerCustomerRelation(request, accessToken, (item) => item.id === relationId)
  if (!isActiveOwnerCustomerRelation(relation)) {
    throw new Error('Activated customer relation was not found')
  }

  return relation
}

async function ensureAccordionOpen(root: Locator, sectionSelector: string) {
  const section = root.locator(sectionSelector).first()
  await expect(section).toBeVisible({ timeout: 30000 })

  const accordion = section.locator('.ds-accordion').first()
  if (await accordion.count() === 0) {
    return
  }

  const className = await accordion.getAttribute('class')
  if (!className?.includes('open')) {
    await accordion.locator('.ds-accordion-header').first().click()
    await expect(accordion).toHaveClass(/open/)
  }
}

async function loadPublicProfileTradeHistory(root: Locator, title: string) {
  const legacyHeader = root.locator('.ds-accordion-header').filter({ hasText: title }).first()
  if (await legacyHeader.count()) {
    await legacyHeader.click()
    return
  }

  const historySection = root.locator('.history-section-card').filter({ hasText: title }).first()
  await expect(historySection).toBeVisible({ timeout: 30000 })
  await historySection.getByRole('button', { name: 'اعمال فیلتر' }).click()
}

test.describe('customer owner lifecycle', () => {
  test('owner can create update and unlink a customer, and super-admin can follow the customer handoff', async ({ page, request }) => {
    test.setTimeout(180000)

    const owner = seedSessionUser('customer_owner_flow_owner')
    const superAdmin = seedSessionUser('customer_owner_flow_admin', 'super_admin')
    const suffix = Date.now()
    const managementName = `مشتری تست ${suffix}`
    const mobileNumber = `09${String(suffix).slice(-9)}`
    const customerAccountName = `customer_${mobileNumber}`

    await waitForBackendReady(request)
    await setAuthTokens(page, owner)

    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })

    await page.locator('.owner-profile-section .settings-btn').filter({ hasText: 'مشتریان' }).click()
    await page.waitForURL(/\/operations\/customers(?:\?.*)?$/)
    const workspace = page.locator('.customer-workspace-view')
    await expect(workspace).toBeVisible({ timeout: 30000 })

    await workspace.getByRole('button', { name: 'افزودن مشتری' }).first().click()
    const createPanel = page.locator('.customer-create-panel')
    await expect(createPanel).toBeVisible({ timeout: 30000 })

    await createPanel.getByLabel('نام مدیریتی').fill(managementName)
    await createPanel.getByLabel('شماره موبایل').fill(mobileNumber)
    await createPanel.getByLabel('سطح مشتری').selectOption('tier2')
    await createPanel.locator('input[aria-label="درصد کمیسیون مشتری"]').fill('1.25')
    await createPanel.getByLabel('حداقل مقدار معامله').fill('2')
    await createPanel.getByLabel('حداکثر مقدار معامله').fill('8')
    await createPanel.getByLabel('حداکثر تعداد روزانه').fill('3')
    await createPanel.getByLabel('حداکثر حجم روزانه').fill('40')
    await page.getByRole('button', { name: 'ثبت دعوت مشتری' }).click()

    const confirmedPendingRelation = await waitForPendingOwnerCustomerRelation(
      request,
      owner.accessToken,
      (relation) => relation.management_name === managementName,
    )
    const pendingRelationId = confirmedPendingRelation.id
    const registrationToken = confirmedPendingRelation.invitation_token
    const registrationLink = confirmedPendingRelation.registration_link

    expect(registrationLink).toContain('/register')
    await expect(workspace.locator('.customer-pending-card').filter({ hasText: managementName })).toContainText('دعوت')

    seedRegistrationVerified(registrationToken)

    await page.route('**/api/auth/register-otp-request', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'کد تایید ارسال شد', expires_in: 120 }),
      })
    })

    await page.goto(toRelativeRegistrationPath(registrationLink))
    await expect(page).toHaveURL(new RegExp(`/register\\?token=${registrationToken}$`))
    await expect(page.getByText(customerAccountName)).toBeVisible()
    await expect(page.getByText(mobileNumber).first()).toBeVisible()

    await page.getByRole('button', { name: 'ارسال کد تایید' }).click()
    await expect(page.getByText('کد تایید ۵ رقمی را وارد کنید:')).toBeVisible()

    await completeRegistrationViaApi(
      request,
      registrationToken,
      'تهران، خیابان مشتری تست، پلاک ۱۲، واحد ۴',
    )

    await setAuthTokens(page, owner)
    await page.goto('/operations/customers')
    await expect(workspace).toBeVisible({ timeout: 30000 })
    const confirmedActivatedCustomer = await waitForActiveOwnerCustomerRelation(
      request,
      owner.accessToken,
      pendingRelationId,
    )
    const activatedCustomerUserId = confirmedActivatedCustomer.customer_user_id

    await page.reload()
    await expect(workspace).toBeVisible({ timeout: 30000 })
    const activeCard = workspace.locator('.ui-list-item').filter({ hasText: managementName }).first()
    await expect(activeCard).toContainText('فعال')
    await expect(activeCard).toContainText('سطح ۲')

    await activeCard.click()
    await page.waitForURL(new RegExp(`/operations/customers/${pendingRelationId}(?:\\?.*)?$`))
    await workspace.getByRole('tab', { name: 'محدودیت‌ها' }).click()
    await workspace.locator('.customer-detail-shell input[aria-label="درصد کمیسیون مشتری"]').fill('2.50')
    await workspace.getByLabel('حداکثر مقدار معامله').fill('12')
    await workspace.getByLabel('حداکثر تعداد روزانه').fill('5')
    await workspace.getByRole('button', { name: 'ذخیره تغییرات' }).click()

    await expect
      .poll(async () => {
        const relations = await fetchOwnerCustomerRelations(request, owner.accessToken)
        const updated = relations.find((relation) => relation.id === pendingRelationId)
        return JSON.stringify({
          commission_rate: updated?.commission_rate,
          max_trade_quantity: updated?.max_trade_quantity,
          max_daily_trades: updated?.max_daily_trades,
        })
      }, { timeout: 30000 })
      .toBe(JSON.stringify({
        commission_rate: 2.5,
        max_trade_quantity: 12,
        max_daily_trades: 5,
      }))

    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.customer-relations-section')).toContainText(managementName)

    await ensureAccordionOpen(page.locator('.public-profile-view:visible').last(), '.customer-relations-section')
    await page.locator('.customer-relations-section .customer-profile-link-btn').filter({ hasText: managementName }).click()
    await page.waitForURL(new RegExp(`/users/${activatedCustomerUserId}(?:\\?.*)?$`))
    await expect(page.locator('.public-profile-view:visible').last().getByRole('heading', { name: new RegExp(managementName) })).toBeVisible({ timeout: 30000 })

    await setAuthTokens(page, superAdmin)
    await page.goto(`/users/${owner.userId}`)
    const superAdminOwnerProfileView = page.locator('.public-profile-view:visible').last()
    await expect(superAdminOwnerProfileView.locator('.profile-content')).toBeVisible({ timeout: 30000 })
    await ensureAccordionOpen(superAdminOwnerProfileView, '.customer-relations-section')
    await superAdminOwnerProfileView.locator('.customer-relations-section .customer-profile-link-btn').filter({ hasText: managementName }).click()
    await page.waitForURL(new RegExp(`/users/${activatedCustomerUserId}(?:\\?.*)?$`))

    const superAdminCustomerProfileView = page.locator('.public-profile-view:visible').last()
    await expect(superAdminCustomerProfileView.locator('.profile-content')).toBeVisible({ timeout: 30000 })
    await superAdminCustomerProfileView.locator('.owner-profile-section .settings-btn:visible').filter({ hasText: 'تنظیمات کاربر' }).click()
    const adminModal = page.locator('.admin-user-modal:visible')
    await expect(adminModal).toBeVisible({ timeout: 30000 })
    await expect(adminModal.locator('.customer-context-box')).toContainText(managementName)
    await expect(adminModal.locator('.customer-context-box')).toContainText(owner.accountName)
    await expect(adminModal.locator('.customer-context-box')).toContainText('سطح 2')
    await page.locator('.admin-user-modal-close').click()
    await expect(adminModal).toBeHidden({ timeout: 30000 })

    await setAuthTokens(page, owner)
    await page.goto(`/operations/customers/${pendingRelationId}?tab=danger`)
    await expect(workspace).toBeVisible({ timeout: 30000 })

    await workspace.getByRole('button', { name: 'قطع ارتباط مشتری' }).click()
    await page.getByRole('button', { name: 'قطع ارتباط' }).click()

    await expect
      .poll(async () => (await fetchOwnerCustomerRelations(request, owner.accessToken)).length, { timeout: 30000 })
      .toBe(0)

    await expect
      .poll(() => inspectDeletedCustomer(activatedCustomerUserId), { timeout: 30000 })
      .toMatchObject({
        relationStatus: 'deleted',
        userDeleted: true,
      })

    await page.goto('/operations/customers')
    await expect(workspace).toContainText('هنوز مشتری ثبت نشده است')
  })

  test('super-admin sees target trade history from the viewed public-profile perspective', async ({ page, request }) => {
    test.setTimeout(180000)

    const fixture = seedAdminHistoryFixture('public_profile_history')

    await waitForBackendReady(request)
    await setAuthTokens(page, fixture.superAdmin)

    await page.goto(`/users/${fixture.ownerUserId}`)
    const ownerProfileView = page.locator('.public-profile-view:visible').last()
    await expect(ownerProfileView.locator('.profile-content')).toBeVisible({ timeout: 30000 })
    await ensureAccordionOpen(ownerProfileView, '.customer-relations-section')
    await expect(ownerProfileView.locator('.customer-relations-section .customer-profile-link-btn').filter({ hasText: fixture.customerManagementName })).toBeVisible({ timeout: 30000 })
    await expect(ownerProfileView.getByRole('heading', { name: 'تاریخچه معاملات مشترک' })).toHaveCount(0)

    await loadPublicProfileTradeHistory(ownerProfileView, 'تاریخچه معاملات این کاربر')

    const ownerHistoryCard = ownerProfileView.locator('.history-list .mini-trade-card').filter({ hasText: '2 عدد' }).first()
    await expect(ownerHistoryCard).toContainText('خرید')
    await expect(ownerHistoryCard).toContainText(fixture.customerManagementName)
    await expect(ownerHistoryCard).toContainText('سطح 2')

    await ownerProfileView.locator('.customer-relations-section .customer-profile-link-btn').filter({ hasText: fixture.customerManagementName }).click()
    await page.waitForURL(new RegExp(`/users/${fixture.customerUserId}(?:\\?.*)?$`))
    const customerProfileView = page.locator('.public-profile-view:visible').last()
    await expect(customerProfileView.locator('.profile-content')).toBeVisible({ timeout: 30000 })
    await expect(customerProfileView.getByRole('heading', { name: /مشتری تاریخچه‌ای/ })).toBeVisible({ timeout: 30000 })

    await loadPublicProfileTradeHistory(customerProfileView, 'تاریخچه معاملات این کاربر')

    const customerHistoryCard = customerProfileView.locator('.history-list .mini-trade-card').filter({ hasText: '5 عدد' }).first()
    await expect(customerHistoryCard).toContainText('خرید')
    await expect(customerHistoryCard).toContainText(`سرگروه ${fixture.ownerAccountName}`)
    await expect(customerHistoryCard).toContainText('سطح 2')
  })

  test('owner mutual history with third-party shows customer badge and management name', async ({ page, request }) => {
    test.setTimeout(180000)

    const fixture = seedMutualHistoryFixture('owner_mutual_history')

    await waitForBackendReady(request)
    await setAuthTokens(page, fixture.owner)

    await page.goto(`/users/${fixture.outsiderUserId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })

    await loadPublicProfileTradeHistory(page.locator('.public-profile-view:visible').last(), 'تاریخچه معاملات مشترک')

    const tradeCard = page.locator('.history-list .mini-trade-card').filter({ hasText: `${fixture.tradeQuantity} عدد` }).first()
    await expect(tradeCard).toContainText('رابطه:')
    await expect(tradeCard).toContainText('مشتری')
    await expect(tradeCard).toContainText(fixture.customerManagementName)
    await expect(tradeCard).toContainText('سطح 2')
  })

  test('third-party mutual history with owner hides customer context', async ({ page, request }) => {
    test.setTimeout(180000)

    const fixture = seedMutualHistoryFixture('third_party_mutual_history')

    await waitForBackendReady(request)
    await setAuthTokens(page, fixture.outsider)

    await page.goto(`/users/${fixture.ownerUserId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })

    await loadPublicProfileTradeHistory(page.locator('.public-profile-view:visible').last(), 'تاریخچه معاملات مشترک')

    const tradeCard = page.locator('.history-list .mini-trade-card').filter({ hasText: `${fixture.tradeQuantity} عدد` }).first()
    await expect(tradeCard).toBeVisible({ timeout: 30000 })
    await expect(tradeCard.locator('.customer-context-badge')).toHaveCount(0)
    await expect(tradeCard.locator('.trade-customer-context-value')).toHaveCount(0)
    await expect(tradeCard).not.toContainText(fixture.customerManagementName)
    await expect(tradeCard).not.toContainText('رابطه:')
  })
})
