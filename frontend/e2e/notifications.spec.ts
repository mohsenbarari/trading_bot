/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SeededSessionFixture {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

interface RelationAwareNotificationFixture {
  owner: SeededSessionFixture
  accountant: SeededSessionFixture
  relationDisplayName: string
}

interface RelationAwareTradeNotificationFixture {
  viewer: SeededSessionFixture
  ownerUserId: number
  ownerAccountName: string
  accountantUserId: number
  accountantAccountName: string
  relationDisplayName: string
  offerId: number
  offerQuantity: number
}

interface RelationAwareRoomActivityFixture {
  owner: SeededSessionFixture
  accountant: SeededSessionFixture
  relationDisplayName: string
  groupChatId: number
  groupTitle: string
  channelChatId: number
  channelTitle: string
}

interface PendingLoginRequestFixture {
  requestId: string
  deviceName: string
  deviceIp: string
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

function isRetriableWebKitGotoError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error)
  return /WebKit encountered an internal error/i.test(message)
}

async function gotoWithWebKitRetry(page: Page, url: string) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded' })
      return
    } catch (error) {
      if (!isRetriableWebKitGotoError(error) || attempt === 2) {
        throw error
      }
    }
  }
}

function seedPrimarySession(label: string): SeededSessionFixture {
  return runPythonInApp<SeededSessionFixture>(`
import asyncio
import json
import uuid
from datetime import timedelta

from core.db import AsyncSessionLocal
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.session import Platform, UserSession
from models.user import User, UserRole

label = ${JSON.stringify(label)}

async def main():
    suffix = uuid.uuid4().hex[:10]
    account_name = f"pw_{label}_{suffix}"
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    mobile_number = f"09{mobile_seed:09d}"

    async with AsyncSessionLocal() as db:
        user = User(
            account_name=account_name,
            mobile_number=mobile_number,
            full_name=account_name,
            address='Playwright Test',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Primary Device',
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
        'accountName': account_name,
        'accessToken': access_token,
        'refreshToken': refresh_token,
    }))

asyncio.run(main())
`)
}

function seedRelationAwareNotificationPair(label: string): RelationAwareNotificationFixture {
  return runPythonInApp<RelationAwareNotificationFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

def build_session_payload(user, device_name):
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
  return refresh_token, session

async def main():
  suffix = uuid.uuid4().hex[:10]
  relation_display_name = f"دفتر {suffix[:6]}"
  owner_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  accountant_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000

  async with AsyncSessionLocal() as db:
    owner = User(
      account_name=f"pw_{label}_owner_{suffix}",
      mobile_number=f"09{owner_mobile_seed:09d}",
      full_name='Playwright Owner',
      address='Playwright Notification Owner',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
      max_accountants=3,
    )
    accountant = User(
      account_name=f"pw_{label}_acct_{suffix}",
      mobile_number=f"09{accountant_mobile_seed:09d}",
      full_name='Playwright Accountant',
      address='Playwright Notification Accountant',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    db.add(owner)
    db.add(accountant)
    await db.flush()

    relation = AccountantRelation(
      owner_user_id=owner.id,
      accountant_user_id=accountant.id,
      created_by_user_id=owner.id,
      invitation_token=f"pw_inv_{uuid.uuid4().hex}",
      global_account_name=accountant.account_name,
      relation_display_name=relation_display_name,
      duty_description='Playwright Notification Relation',
      mobile_number=accountant.mobile_number,
      status=AccountantRelationStatus.ACTIVE,
      expires_at=datetime.utcnow() + timedelta(days=30),
      activated_at=datetime.utcnow(),
    )
    db.add(relation)

    owner_refresh_token, owner_session = build_session_payload(owner, 'Playwright Relation Owner Device')
    accountant_refresh_token, accountant_session = build_session_payload(accountant, 'Playwright Relation Accountant Device')
    db.add(owner_session)
    db.add(accountant_session)
    await db.flush()

    owner_access_token = create_access_token(
      subject=owner.id,
      expires_delta=timedelta(minutes=60),
      session_id=str(owner_session.id),
    )
    accountant_access_token = create_access_token(
      subject=accountant.id,
      expires_delta=timedelta(minutes=60),
      session_id=str(accountant_session.id),
    )

    await db.commit()

  print(json.dumps({
    'owner': {
      'userId': owner.id,
      'accountName': owner.account_name,
      'accessToken': owner_access_token,
      'refreshToken': owner_refresh_token,
    },
    'accountant': {
      'userId': accountant.id,
      'accountName': accountant.account_name,
      'accessToken': accountant_access_token,
      'refreshToken': accountant_refresh_token,
    },
    'relationDisplayName': relation_display_name,
  }))

asyncio.run(main())
`)
}

function seedRelationAwareRoomActivityFixture(label: string): RelationAwareRoomActivityFixture {
  return runPythonInApp<RelationAwareRoomActivityFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from core.db import AsyncSessionLocal
from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.chat import Chat
from models.chat_member import ChatMember
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

def build_session_payload(user, device_name):
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
  return refresh_token, session

async def main():
  suffix = uuid.uuid4().hex[:10]
  relation_display_name = f"دفتر {suffix[:6]}"
  owner_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  accountant_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000

  async with AsyncSessionLocal() as db:
    owner = User(
      account_name=f"pw_{label}_owner_{suffix}",
      mobile_number=f"09{owner_mobile_seed:09d}",
      full_name='Playwright Room Owner',
      address='Playwright Room Owner',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
      max_accountants=3,
    )
    accountant = User(
      account_name=f"pw_{label}_acct_{suffix}",
      mobile_number=f"09{accountant_mobile_seed:09d}",
      full_name='Playwright Room Accountant',
      address='Playwright Room Accountant',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    db.add(owner)
    db.add(accountant)
    await db.flush()

    relation = AccountantRelation(
      owner_user_id=owner.id,
      accountant_user_id=accountant.id,
      created_by_user_id=owner.id,
      invitation_token=f"pw_room_inv_{uuid.uuid4().hex}",
      global_account_name=accountant.account_name,
      relation_display_name=relation_display_name,
      duty_description='Playwright Room Activity Relation',
      mobile_number=accountant.mobile_number,
      status=AccountantRelationStatus.ACTIVE,
      expires_at=datetime.utcnow() + timedelta(days=30),
      activated_at=datetime.utcnow(),
    )
    db.add(relation)

    group_chat = Chat(
      type=ChatType.GROUP,
      title=f"Playwright Relation Group {suffix[:6]}",
      description='Playwright relation-aware room activity',
      created_by_id=owner.id,
    )
    channel_chat = Chat(
      type=ChatType.CHANNEL,
      title=f"Playwright Relation Channel {suffix[:6]}",
      description='Playwright relation-aware channel activity',
      created_by_id=owner.id,
    )
    db.add(group_chat)
    db.add(channel_chat)
    await db.flush()

    db.add_all([
      ChatMember(
        chat_id=group_chat.id,
        user_id=owner.id,
        role=ChatMemberRole.ADMIN,
        membership_status=ChatMembershipStatus.ACTIVE,
        joined_at=datetime.utcnow(),
      ),
      ChatMember(
        chat_id=group_chat.id,
        user_id=accountant.id,
        role=ChatMemberRole.MEMBER,
        membership_status=ChatMembershipStatus.ACTIVE,
        joined_at=datetime.utcnow(),
      ),
      ChatMember(
        chat_id=channel_chat.id,
        user_id=owner.id,
        role=ChatMemberRole.ADMIN,
        membership_status=ChatMembershipStatus.ACTIVE,
        joined_at=datetime.utcnow(),
      ),
      ChatMember(
        chat_id=channel_chat.id,
        user_id=accountant.id,
        role=ChatMemberRole.MEMBER,
        membership_status=ChatMembershipStatus.ACTIVE,
        joined_at=datetime.utcnow(),
      ),
    ])

    owner_refresh_token, owner_session = build_session_payload(owner, 'Playwright Relation Room Owner Device')
    accountant_refresh_token, accountant_session = build_session_payload(accountant, 'Playwright Relation Room Accountant Device')
    db.add(owner_session)
    db.add(accountant_session)
    await db.flush()

    owner_access_token = create_access_token(
      subject=owner.id,
      expires_delta=timedelta(minutes=60),
      session_id=str(owner_session.id),
    )
    accountant_access_token = create_access_token(
      subject=accountant.id,
      expires_delta=timedelta(minutes=60),
      session_id=str(accountant_session.id),
    )

    await db.commit()

  print(json.dumps({
    'owner': {
      'userId': owner.id,
      'accountName': owner.account_name,
      'accessToken': owner_access_token,
      'refreshToken': owner_refresh_token,
    },
    'accountant': {
      'userId': accountant.id,
      'accountName': accountant.account_name,
      'accessToken': accountant_access_token,
      'refreshToken': accountant_refresh_token,
    },
    'relationDisplayName': relation_display_name,
    'groupChatId': group_chat.id,
    'groupTitle': group_chat.title,
    'channelChatId': channel_chat.id,
    'channelTitle': channel_chat.title,
  }))

asyncio.run(main())
`)
}

function seedRelationAwareTradeNotificationFixture(label: string): RelationAwareTradeNotificationFixture {
  return runPythonInApp<RelationAwareTradeNotificationFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta

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

def build_session_payload(user, device_name):
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
  return refresh_token, session

def random_mobile():
  mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
  return f"09{mobile_seed:09d}"

async def main():
  suffix = uuid.uuid4().hex[:10]
  relation_display_name = f"دفتر {suffix[:6]}"

  async with AsyncSessionLocal() as db:
    viewer = User(
      account_name=f"pw_{label}_viewer_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Trade Viewer',
      address='Playwright Trade Viewer',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
    )
    owner = User(
      account_name=f"pw_{label}_owner_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Trade Owner',
      address='Playwright Trade Owner',
      role=UserRole.STANDARD,
      has_bot_access=True,
      max_sessions=1,
      max_accountants=3,
    )
    accountant = User(
      account_name=f"pw_{label}_acct_{suffix}",
      mobile_number=random_mobile(),
      full_name='Playwright Trade Accountant',
      address='Playwright Trade Accountant',
      role=UserRole.STANDARD,
      has_bot_access=False,
      max_sessions=1,
    )
    commodity = Commodity(name=f"PW Notification Trade Commodity {suffix[:6]}")
    db.add_all([viewer, owner, accountant, commodity])
    await db.flush()

    db.add(AccountantRelation(
      owner_user_id=owner.id,
      accountant_user_id=accountant.id,
      created_by_user_id=owner.id,
      invitation_token=f"pw_trade_inv_{uuid.uuid4().hex}",
      global_account_name=accountant.account_name,
      relation_display_name=relation_display_name,
      duty_description='Playwright trade notification relation',
      mobile_number=accountant.mobile_number,
      status=AccountantRelationStatus.ACTIVE,
      expires_at=datetime.utcnow() + timedelta(days=30),
      activated_at=datetime.utcnow(),
    ))

    offer = Offer(
      user_id=accountant.id,
      actor_user_id=accountant.id,
      home_server='foreign',
      offer_type=OfferType.SELL,
      commodity_id=commodity.id,
      quantity=2,
      remaining_quantity=2,
      price=654321,
      is_wholesale=True,
      lot_sizes=None,
      original_lot_sizes=None,
      status=OfferStatus.ACTIVE,
      notes='Playwright trade notification offer',
    )
    db.add(offer)

    viewer_refresh_token, viewer_session = build_session_payload(viewer, 'Playwright Trade Notification Viewer Device')
    db.add(viewer_session)
    await db.flush()

    viewer_access_token = create_access_token(
      subject=viewer.id,
      expires_delta=timedelta(minutes=60),
      session_id=str(viewer_session.id),
    )

    await db.commit()

  print(json.dumps({
    'viewer': {
      'userId': viewer.id,
      'accountName': viewer.account_name,
      'accessToken': viewer_access_token,
      'refreshToken': viewer_refresh_token,
    },
    'ownerUserId': owner.id,
    'ownerAccountName': owner.account_name,
    'accountantUserId': accountant.id,
    'accountantAccountName': accountant.account_name,
    'relationDisplayName': relation_display_name,
    'offerId': offer.id,
    'offerQuantity': 2,
  }))

asyncio.run(main())
`)
}

function createPendingLoginRequest(userId: number, deviceName: string): PendingLoginRequestFixture {
  return runPythonInApp<PendingLoginRequestFixture>(`
import asyncio
import json
from datetime import datetime, timedelta
from uuid import uuid4

from core.db import AsyncSessionLocal
from core.utils import publish_user_event
from models.session import LoginRequestStatus, SessionLoginRequest

user_id = ${userId}
device_name = ${JSON.stringify(deviceName)}
device_ip = '10.10.10.10'

async def main():
    request_id = uuid4()
    expires_at = datetime.utcnow() + timedelta(seconds=120)

    async with AsyncSessionLocal() as db:
        request = SessionLoginRequest(
            id=request_id,
            user_id=user_id,
            requester_device_name=device_name,
            requester_ip=device_ip,
            status=LoginRequestStatus.PENDING,
            expires_at=expires_at,
        )
        db.add(request)
        await db.commit()

    await publish_user_event(user_id, 'session:login_request', {
        'request_id': str(request_id),
        'device_name': device_name,
        'device_ip': device_ip,
        'expires_at': expires_at.isoformat() + 'Z',
    })

    print(json.dumps({
        'requestId': str(request_id),
        'deviceName': device_name,
        'deviceIp': device_ip,
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

async function loginAsDev(page: Page) {
  await page.goto('/login')
  await page.getByRole('button', { name: 'ورود سریع ۱ ساله (توسعه‌دهنده)' }).click()
  await page.waitForURL('**/')
}

async function loginWithSeededSession(page: Page, fixture: SeededSessionFixture) {
  await gotoWithWebKitRetry(page, '/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, fixture)
  await gotoWithWebKitRetry(page, '/')
  await expect(page).not.toHaveURL(/\/login$/)
  await expect(page.getByRole('button', { name: 'اعلان‌ها' })).toBeVisible()
}

function chatMessageContent(page: Page, content: string) {
  return page.locator('.messages-container').getByText(content).first()
}

async function sendTextChatMessage(
  request: APIRequestContext,
  sender: SeededSessionFixture,
  receiverId: number,
  content: string,
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/send`, {
    headers: authHeaders(sender.accessToken),
    data: {
      receiver_id: receiverId,
      content,
      message_type: 'text',
    },
  })

  expect(response.ok()).toBeTruthy()
  return response.json()
}

async function sendRoomActivitySignal(
  request: APIRequestContext,
  actor: SeededSessionFixture,
  roomChatId: number,
  activity: 'typing' | 'uploading_file',
  active = true,
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/rooms/${roomChatId}/activity`, {
    headers: authHeaders(actor.accessToken),
    data: {
      activity,
      active,
    },
  })

  expect(response.status()).toBe(204)
}

async function executeTrade(
  request: APIRequestContext,
  viewer: SeededSessionFixture,
  offerId: number,
  quantity: number,
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/trades/`, {
    headers: authHeaders(viewer.accessToken),
    data: {
      offer_id: offerId,
      quantity,
    },
  })

  expect(response.status()).toBe(201)
  return response.json()
}

test.describe('Notification regressions', () => {
  test('dashboard notifications button opens the notification center', async ({ page }) => {
    await loginAsDev(page)

    await page.getByRole('button', { name: 'اعلان‌ها' }).click()

    await expect(page).toHaveURL(/\/notifications$/)
    await expect(page.getByRole('heading', { name: 'مرکز اعلان‌ها' })).toBeVisible()
  })

  test('websocket heartbeat pong does not emit JSON parse errors', async ({ page }) => {
    const consoleErrors: string[] = []

    await page.addInitScript(() => {
      const originalSetInterval = window.setInterval.bind(window)
      window.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
        if (timeout === 25000) {
          return originalSetInterval(handler, 50, ...args)
        }
        return originalSetInterval(handler, timeout, ...args)
      }) as typeof window.setInterval
    })

    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text())
      }
    })

    await loginAsDev(page)
    await page.waitForTimeout(500)

    expect(
      consoleErrors.some((entry) => entry.includes('pong') && entry.includes('not valid JSON')),
    ).toBeFalsy()
  })

  test('session approval modal shows a pending request and reject clears it', async ({ page, request }) => {
    const receiver = seedPrimarySession('session_modal')
    const pendingRequest = createPendingLoginRequest(receiver.userId, 'Playwright Secondary Device')

    await loginWithSeededSession(page, receiver)

    await expect(page.getByRole('heading', { name: 'درخواست ورود جدید' })).toBeVisible()
    await expect(page.getByText(pendingRequest.deviceName)).toBeVisible()
    await expect(page.getByText(pendingRequest.deviceIp)).toBeVisible()

    await page.getByRole('button', { name: 'رد' }).click()
    await expect(page.getByRole('heading', { name: 'درخواست ورود جدید' })).toBeHidden()

    await expect
      .poll(async () => {
        const response = await request.get(`${BACKEND_BASE_URL}/api/sessions/login-requests/pending`, {
          headers: authHeaders(receiver.accessToken),
        })
        const body = await response.json()
        return Array.isArray(body) ? body.length : -1
      })
      .toBe(0)
  })

  test('chat notification toast increments unread count and deep-links to the sender chat', async ({
    page,
    request,
  }) => {
    const receiver = seedPrimarySession('chat_receiver')
    const sender = seedPrimarySession('chat_sender')
    const content = `PLAYWRIGHT CHAT ${Date.now()}`

    await loginWithSeededSession(page, receiver)
    await page.waitForTimeout(1200)

    await sendTextChatMessage(request, sender, receiver.userId, content)

    const toast = page.locator('.toast-card-floating').filter({ hasText: content })
    await expect(toast).toBeVisible()
    await expect(page.locator('.nav-unread-badge')).toHaveText('1')

    await toast.click()

    await expect.poll(() => page.url()).toContain(`/chat?user_id=${sender.userId}`)
    await expect(chatMessageContent(page, content)).toBeVisible()

    await expect
      .poll(async () => {
        const response = await request.get(`${BACKEND_BASE_URL}/api/chat/poll`, {
          headers: {
            Authorization: `Bearer ${receiver.accessToken}`,
          },
        })
        const body = await response.json()
        return body.unread_chats_count ?? -1
      })
      .toBe(0)
  })

  test('relation-aware direct chat notifications keep the accountant display label in toast and route', async ({
    page,
    request,
  }) => {
    const fixture = seedRelationAwareNotificationPair('chat_relation_label')
    const warmupContent = `PLAYWRIGHT RELATION WARMUP ${Date.now()}`
    const content = `PLAYWRIGHT RELATION CHAT ${Date.now()}`

    await loginWithSeededSession(page, fixture.owner)
    await sendTextChatMessage(request, fixture.owner, fixture.accountant.userId, warmupContent)
    await page.waitForTimeout(1200)

    await sendTextChatMessage(request, fixture.accountant, fixture.owner.userId, content)

    const toast = page.locator('.toast-card-floating').filter({ hasText: content })
    await expect(toast).toBeVisible()
    await expect(toast).toContainText(fixture.relationDisplayName)
    await expect(toast).not.toContainText(fixture.accountant.accountName)

    await toast.click()

    await expect
      .poll(() => page.evaluate(() => {
        const url = new URL(window.location.href)
        return {
          userId: url.searchParams.get('user_id'),
          userName: url.searchParams.get('user_name'),
        }
      }))
      .toEqual({
        userId: String(fixture.accountant.userId),
        userName: fixture.relationDisplayName,
      })

    await expect(chatMessageContent(page, content)).toBeVisible()
  })

  test('group room activity status shows relation-aware accountant label in chat header', async ({ page, request }) => {
    const fixture = seedRelationAwareRoomActivityFixture('room_relation_activity')

    await loginWithSeededSession(page, fixture.owner)
    await page.goto(`/chat?user_id=-${fixture.groupChatId}&user_name=${encodeURIComponent(fixture.groupTitle)}`)
    await expect(page.locator('.chat-header .header-name')).toContainText(fixture.groupTitle, { timeout: 30000 })
    await page.waitForTimeout(1200)

    await sendRoomActivitySignal(request, fixture.accountant, fixture.groupChatId, 'typing', true)

    const headerStatus = page.locator('.chat-header .header-status')
    await expect(headerStatus).toContainText(fixture.relationDisplayName, { timeout: 30000 })
    await expect(headerStatus).toContainText('در حال نوشتن', { timeout: 30000 })
    await expect(headerStatus).not.toContainText(fixture.accountant.accountName)

    await sendRoomActivitySignal(request, fixture.accountant, fixture.groupChatId, 'typing', false)
    await sendRoomActivitySignal(request, fixture.accountant, fixture.groupChatId, 'uploading_file', true)

    await expect(headerStatus).toContainText(fixture.relationDisplayName, { timeout: 30000 })
    await expect(headerStatus).toContainText('در حال ارسال فایل', { timeout: 30000 })
    await expect(headerStatus).not.toContainText(fixture.accountant.accountName)
  })

  test('channel room activity status shows relation-aware accountant label in chat header', async ({ page, request }) => {
    const fixture = seedRelationAwareRoomActivityFixture('channel_relation_activity')

    await loginWithSeededSession(page, fixture.owner)
    await page.goto(`/chat?user_id=-${fixture.channelChatId}&user_name=${encodeURIComponent(fixture.channelTitle)}`)
    await expect(page.locator('.chat-header .header-name')).toContainText(fixture.channelTitle, { timeout: 30000 })
    await page.waitForTimeout(1200)

    await sendRoomActivitySignal(request, fixture.accountant, fixture.channelChatId, 'typing', true)

    const headerStatus = page.locator('.chat-header .header-status')
    await expect(headerStatus).toContainText(fixture.relationDisplayName, { timeout: 30000 })
    await expect(headerStatus).toContainText('در حال نوشتن', { timeout: 30000 })
    await expect(headerStatus).not.toContainText(fixture.accountant.accountName)

    await sendRoomActivitySignal(request, fixture.accountant, fixture.channelChatId, 'typing', false)
    await sendRoomActivitySignal(request, fixture.accountant, fixture.channelChatId, 'uploading_file', true)

    await expect(headerStatus).toContainText(fixture.relationDisplayName, { timeout: 30000 })
    await expect(headerStatus).toContainText('در حال ارسال فایل', { timeout: 30000 })
    await expect(headerStatus).not.toContainText(fixture.accountant.accountName)
  })

  test('trade notifications opened from the notification center keep the owner-resolved route and highlight', async ({
    page,
    request,
  }) => {
    const fixture = seedRelationAwareTradeNotificationFixture('trade_notification_center')

    await loginWithSeededSession(page, fixture.viewer)
    await page.waitForTimeout(1200)

    await executeTrade(request, fixture.viewer, fixture.offerId, fixture.offerQuantity)

    const toast = page.locator('.toast-card-floating').filter({ hasText: fixture.relationDisplayName })
    await expect(toast).toBeVisible({ timeout: 30000 })
    await expect(toast).not.toContainText(fixture.accountantAccountName)

    await page.getByRole('button', { name: 'اعلان‌ها' }).click()
    await expect(page).toHaveURL(/\/notifications$/)

    const notificationRow = page.locator('.notif-item').filter({ hasText: fixture.relationDisplayName }).first()
    await expect(notificationRow).toBeVisible({ timeout: 30000 })
    await expect(notificationRow).not.toContainText(fixture.accountantAccountName)

    await notificationRow.click()

    await expect(page).toHaveURL(new RegExp(`/users/${fixture.ownerUserId}`))
    await expect(page).toHaveURL(new RegExp(`highlight_accountant_user_id=${fixture.accountantUserId}`))
    await expect(page.getByText('نمایش پروفایل مالک اصلی')).toBeVisible()
    await expect(page.locator('.accountant-resolution-banner')).toContainText(fixture.relationDisplayName)
    await expect(page.locator('.profile-hero-copy h3')).toHaveText(fixture.ownerAccountName)
  })
})