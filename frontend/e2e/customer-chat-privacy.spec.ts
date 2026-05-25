/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SessionUser {
  userId: number
  accountName: string
  fullName: string
  mobileNumber: string
  accessToken: string
  refreshToken: string
}

interface CustomerChatPrivacyFixture {
  owner: SessionUser
  customer: SessionUser
  unrelated: SessionUser
  superAdmin: SessionUser
}

interface DirectMessageRecord {
  id?: number
  content?: string
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
    throw new Error('No JSON output returned from trading_bot_app customer chat privacy seed helper')
  }

  return JSON.parse(lastLine) as T
}

function seedCustomerChatPrivacyFixture(label: string): CustomerChatPrivacyFixture {
  return runPythonInApp<CustomerChatPrivacyFixture>(`
import asyncio
import json
import uuid
from datetime import timedelta
from decimal import Decimal

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
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
      'fullName': user.full_name,
      'mobileNumber': user.mobile_number,
        'accessToken': access_token,
        'refreshToken': refresh_token,
    }

async def main():
    suffix = uuid.uuid4().hex[:10]

    async with AsyncSessionLocal() as db:
        owner = User(
            account_name=f"pw_customer_owner_{label}_{suffix}",
            mobile_number=build_mobile(),
            full_name='Playwright Customer Owner',
            address='Playwright Customer Owner',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        customer = User(
            account_name=f"pw_customer_actor_{label}_{suffix}",
            mobile_number=build_mobile(),
            full_name='Playwright Customer Actor',
            address='Playwright Customer Actor',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        unrelated = User(
            account_name=f"pw_customer_unrelated_{label}_{suffix}",
            mobile_number=build_mobile(),
            full_name='Playwright Customer Unrelated',
            address='Playwright Customer Unrelated',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        super_admin = User(
            account_name=f"pw_customer_superadmin_{label}_{suffix}",
            mobile_number=build_mobile(),
            full_name='Playwright Customer Super Admin',
            address='Playwright Customer Super Admin',
            role=UserRole.SUPER_ADMIN,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add_all([owner, customer, unrelated, super_admin])
        await db.flush()

        db.add(CustomerRelation(
            owner_user_id=owner.id,
            customer_user_id=customer.id,
            created_by_user_id=owner.id,
            invitation_token=f"CUST-{uuid.uuid4().hex}",
            management_name=f"مشتری چت {suffix[:4]}",
            customer_tier=CustomerTier.TIER_2,
            commission_rate=Decimal('0.5'),
            status=CustomerRelationStatus.ACTIVE,
            activated_at=None,
            deleted_at=None,
        ))

        owner_bundle = await create_session_bundle(db, owner, 'Playwright Customer Chat Owner Device')
        customer_bundle = await create_session_bundle(db, customer, 'Playwright Customer Chat Customer Device')
        unrelated_bundle = await create_session_bundle(db, unrelated, 'Playwright Customer Chat Unrelated Device')
        super_admin_bundle = await create_session_bundle(db, super_admin, 'Playwright Customer Chat Super Admin Device')

        await db.commit()

    print(json.dumps({
        'owner': owner_bundle,
        'customer': customer_bundle,
        'unrelated': unrelated_bundle,
        'superAdmin': super_admin_bundle,
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

async function setAuthTokens(page: Page, session: SessionUser) {
  await primeAuthSession(page, session.accessToken, session.refreshToken)
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

async function openMessenger(page: Page) {
  await page.goto('/chat')
  await expect(page.locator('.chat-header')).toBeVisible({ timeout: 30000 })
}

async function openConversationListMenu(page: Page) {
  await page.locator('.chat-header .header-menu-container .header-btn').click()
  await expect(page.locator('.header-dropdown-menu')).toBeVisible({ timeout: 15000 })
}

async function sendComposerMessage(page: Page, text: string) {
  const composerContainers = page.locator('.chat-view .input-area .input-container')
  const containerCount = await composerContainers.count()

  for (let index = containerCount - 1; index >= 0; index -= 1) {
    const composerContainer = composerContainers.nth(index)
    const composer = composerContainer.locator('textarea[placeholder="پیام..."]')
    if (!(await composer.isVisible().catch(() => false))) {
      continue
    }

    await composer.click()
    await composer.fill('')
    await composer.pressSequentially(text)

    const sendButton = composerContainer.locator('.send-btn-inline')
    const hasReactiveSend = await sendButton.isVisible({ timeout: 1500 }).catch(() => false)
    if (!hasReactiveSend) {
      continue
    }

    await expect(composer).toHaveValue(text)
    await sendButton.click()
    return
  }

  throw new Error('No reactive composer container became send-ready for the customer privacy message flow')
}

async function fetchDirectMessages(
  request: APIRequestContext,
  fixture: SessionUser,
  otherUserId: number,
): Promise<DirectMessageRecord[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/messages/${otherUserId}?limit=20`, {
    headers: authHeaders(fixture.accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = await response.json() as DirectMessageRecord[]
  return Array.isArray(body) ? body : []
}

test.describe('Customer chat privacy regressions', () => {
  test('customer can start a direct chat with owner but unrelated direct initiation is denied and group creation is hidden', async ({ page, request }) => {
    test.setTimeout(90000)
    const fixture = seedCustomerChatPrivacyFixture('direct_privacy')
    const message = `PW CUSTOMER DIRECT ${Date.now()}`
    const blockedMessage = `PW CUSTOMER BLOCKED ${Date.now()}`

    await waitForBackendReady(request)
    await setAuthTokens(page, fixture.customer)
    await openMessenger(page)

    await expect(page.locator('.fab-new-chat')).toBeVisible({ timeout: 30000 })
    await page.locator('.fab-new-chat').click()
    await expect(page.locator('.new-chat-search-input')).toBeVisible({ timeout: 30000 })

    await page.locator('.new-chat-search-input').fill(fixture.owner.accountName)
    const ownerRow = page.locator('.users-list').getByRole('button', {
      name: new RegExp(`${fixture.owner.fullName}.*${fixture.owner.mobileNumber}`),
    })
    await expect(ownerRow).toBeVisible({ timeout: 30000 })
    await ownerRow.click()

    await expect(page.locator('.chat-header .header-name')).toContainText(fixture.owner.fullName, { timeout: 30000 })
    await sendComposerMessage(page, message)
    await expect(page.locator('.message-bubble.sent').filter({ hasText: message })).toBeVisible({ timeout: 30000 })

    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, fixture.customer, fixture.owner.userId)
        return messages.some((entry) => entry.content === message)
      }, { timeout: 30000 })
      .toBe(true)

    await page.goto('/chat')
    await expect(page.locator('.fab-new-chat')).toBeVisible({ timeout: 30000 })
    await page.locator('.fab-new-chat').click()
    await expect(page.locator('.new-chat-search-input')).toBeVisible({ timeout: 30000 })

    await page.locator('.new-chat-search-input').fill(fixture.unrelated.accountName)
    const unrelatedRow = page.locator('.users-list').getByRole('button', {
      name: new RegExp(`${fixture.unrelated.fullName}.*${fixture.unrelated.mobileNumber}`),
    })
    await expect(unrelatedRow).toBeVisible({ timeout: 30000 })
    await unrelatedRow.click()

    await expect(page.locator('.chat-header .header-name')).toContainText(fixture.unrelated.fullName, { timeout: 30000 })
    await sendComposerMessage(page, blockedMessage)

    await expect(page.locator('.message-bubble.sent.error').filter({ hasText: blockedMessage })).toBeVisible({ timeout: 30000 })
    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, fixture.customer, fixture.unrelated.userId)
        return messages.some((entry) => entry.content === blockedMessage)
      }, { timeout: 15000 })
      .toBe(false)

    const deniedResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/send`, {
      headers: authHeaders(fixture.customer.accessToken),
      data: {
        receiver_id: fixture.unrelated.userId,
        content: `PW CUSTOMER DENIED API ${Date.now()}`,
        message_type: 'text',
      },
    })
    expect(deniedResponse.status()).toBe(403)
    const deniedPayload = await deniedResponse.json() as { detail?: string }
    expect(deniedPayload.detail).toBe('کاربر مشتری در این فاز اجازه شروع گفتگوی مستقیم با این کاربر را ندارد')

    await page.goto('/chat')
    await openConversationListMenu(page)
    await expect(page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'ساخت گروه جدید' })).toHaveCount(0)
  })

  test('active customers are excluded from optional channel member invite candidates', async ({ page, request }) => {
    test.setTimeout(90000)
    const fixture = seedCustomerChatPrivacyFixture('channel_privacy')
    const suffix = Date.now()

    await waitForBackendReady(request)
    await setAuthTokens(page, fixture.superAdmin)
    await openMessenger(page)

    await openConversationListMenu(page)
    await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'ساخت کانال' }).click()

    const channelManager = page.locator('.channel-manager-root')
    await expect(channelManager).toBeVisible({ timeout: 30000 })
    await channelManager.getByRole('button', { name: 'کانال جدید' }).click()

    await channelManager.locator('#channel-title').fill(`Playwright Customer Channel ${suffix}`)
    await channelManager.locator('#channel-description').fill('Customer channel restriction browser flow')
    await channelManager.getByRole('button', { name: 'ساخت کانال' }).click()

    await expect(channelManager.getByText('کانال ساخته شد. حالا اعضا و ادمین‌ها را مدیریت کنید.')).toBeVisible({ timeout: 30000 })

    const searchInput = channelManager.getByPlaceholder('جستجو با نام، اکانت یا موبایل...')
    await searchInput.fill(fixture.unrelated.accountName)
    await expect(channelManager.locator('button').filter({ hasText: 'Playwright Customer Unrelated' }).first()).toBeVisible({ timeout: 30000 })

    await searchInput.fill(fixture.customer.accountName)
    await expect(channelManager.locator('button').filter({ hasText: 'Playwright Customer Unrelated' })).toHaveCount(0)
    await expect(channelManager.locator('button').filter({ hasText: 'Playwright Customer Actor' })).toHaveCount(0)
  })
})