/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

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

interface ConversationStateFixture {
  actorUserId: number
  actorAccountName: string
  accessToken: string
  refreshToken: string
  peerAId: number
  peerAName: string
  peerBId: number
  peerBName: string
  peerCId: number
  peerCName: string
  optionalChannelId: number
  optionalChannelTitle: string
  mandatoryChannelId: number
  mandatoryChannelTitle: string
}

interface ConversationRecord {
  chat_id?: number | null
  other_user_id: number
  other_user_name: string
  unread_count: number
  room_kind?: 'direct' | 'channel' | 'group'
  is_muted?: boolean
  is_pinned?: boolean
  pin_order?: number | null
}

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
    throw new Error('No JSON output returned from trading_bot_app conversation-state seed helper')
  }

  return JSON.parse(lastLine) as T
}

function seedConversationStateFixture(label: string): ConversationStateFixture {
  return runPythonInApp<ConversationStateFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from core.db import AsyncSessionLocal
from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType, UserRole
from core.security import create_access_token, create_refresh_token
from core.services.chat_room_service import ensure_mandatory_channel_rollout, get_mandatory_channel
from core.services.session_service import hash_token
from models.chat import Chat
from models.chat_member import ChatMember
from models.message import Message
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

def make_user(prefix: str, suffix: str, role=UserRole.STANDARD):
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    account_name = f"pw_{prefix}_{suffix}"
    return User(
        account_name=account_name,
        mobile_number=f"09{mobile_seed:09d}",
        full_name=account_name,
        address='Playwright Messenger Conversation State',
        role=role,
        has_bot_access=True,
        max_sessions=1,
    )

async def main():
    suffix = uuid.uuid4().hex[:10]
    now = datetime.utcnow()

    async with AsyncSessionLocal() as db:
        admin = make_user(f"{label}_mandatory_admin", suffix, UserRole.SUPER_ADMIN)
        actor = make_user(f"{label}_actor", suffix)
        peer_a = make_user(f"{label}_peer_a", suffix)
        peer_b = make_user(f"{label}_peer_b", suffix)
        peer_c = make_user(f"{label}_peer_c", suffix)

        db.add_all([admin, actor, peer_a, peer_b, peer_c])
        await db.flush()

        await ensure_mandatory_channel_rollout(db, users=[admin, actor])
        mandatory_channel = await get_mandatory_channel(db)

        mandatory_message = Message(
            chat_id=mandatory_channel.id,
            sender_id=admin.id,
            receiver_id=admin.id,
            content=f"PW mandatory state {suffix}",
            message_type=MessageType.TEXT,
            is_read=True,
        )
        db.add(mandatory_message)
        await db.flush()
        mandatory_channel.last_message_id = mandatory_message.id
        mandatory_channel.last_message_at = now

        optional_channel = Chat(
            type=ChatType.CHANNEL,
            title=f"Playwright Optional Channel {suffix}",
            description='Playwright optional channel for conversation state coverage',
            created_by_id=actor.id,
            is_system=False,
            is_mandatory=False,
            created_at=now,
            updated_at=now,
        )
        db.add(optional_channel)
        await db.flush()

        db.add(ChatMember(
            chat_id=optional_channel.id,
            user_id=actor.id,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
            joined_at=now,
            updated_at=now,
        ))

        optional_message = Message(
            chat_id=optional_channel.id,
            sender_id=actor.id,
            receiver_id=actor.id,
            content=f"PW optional state {suffix}",
            message_type=MessageType.TEXT,
            is_read=True,
        )
        db.add(optional_message)
        await db.flush()
        optional_channel.last_message_id = optional_message.id
        optional_channel.last_message_at = now

        refresh_token = create_refresh_token(subject=actor.id)
        session = UserSession(
            user_id=actor.id,
            device_name='Playwright Conversation State Device',
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

    print(json.dumps({
        'actorUserId': actor.id,
        'actorAccountName': actor.account_name,
        'accessToken': access_token,
        'refreshToken': refresh_token,
        'peerAId': peer_a.id,
        'peerAName': peer_a.account_name,
        'peerBId': peer_b.id,
        'peerBName': peer_b.account_name,
        'peerCId': peer_c.id,
        'peerCName': peer_c.account_name,
        'optionalChannelId': optional_channel.id,
        'optionalChannelTitle': optional_channel.title,
        'mandatoryChannelId': mandatory_channel.id,
        'mandatoryChannelTitle': mandatory_channel.title,
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

async function loginWithSeededSession(page: Page, fixture: ConversationStateFixture) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, fixture)
  await page.goto('/')
  await expect(page.getByText(fixture.actorAccountName)).toBeVisible({ timeout: 30000 })
}

async function sendDirectTextMessage(
  request: APIRequestContext,
  accessToken: string,
  receiverId: number,
  content: string,
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/send`, {
    headers: authHeaders(accessToken),
    data: {
      receiver_id: receiverId,
      content,
      message_type: 'text',
    },
  })

  expect(response.ok()).toBeTruthy()
}

async function fetchConversations(request: APIRequestContext, accessToken: string): Promise<ConversationRecord[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/conversations`, {
    headers: authHeaders(accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = await response.json() as ConversationRecord[]
  return Array.isArray(body) ? body : []
}

function findConversationByDirectUser(conversations: ConversationRecord[], userId: number) {
  return conversations.find((item) => item.room_kind === 'direct' && Number(item.other_user_id) === userId) ?? null
}

function findConversationByRoom(conversations: ConversationRecord[], roomKind: 'channel' | 'group', chatId: number) {
  return conversations.find((item) => item.room_kind === roomKind && Number(item.chat_id) === chatId) ?? null
}

function conversationRow(page: Page, title: string) {
  return page.locator('.conversation-item').filter({ hasText: title }).first()
}

async function openConversationMenuByLongPress(page: Page, row: Locator) {
  await row.scrollIntoViewIfNeeded()
  const box = await row.boundingBox()
  if (!box) {
    throw new Error('Conversation row has no bounding box')
  }

  const popover = page.locator('.conversation-menu-popover')
  try {
    await row.dispatchEvent('contextmenu', {
      bubbles: true,
      cancelable: true,
      clientX: box.x + box.width / 2,
      clientY: box.y + box.height / 2,
      button: 2,
    })
    await expect(popover).toBeVisible({ timeout: 2000 })
  } catch {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.down()
    await page.waitForTimeout(520)
    await page.mouse.up()
    await expect(popover).toBeVisible({ timeout: 2000 })
  }
}

async function clickConversationMenuAction(page: Page, label: string) {
  const action = page.locator('.conversation-menu-popover .menu-action').filter({ hasText: label }).first()
  await expect(action).toBeVisible({ timeout: 15000 })
  const browserName = page.context().browser()?.browserType().name() || ''
  if (browserName === 'webkit') {
    await action.evaluate((node: HTMLElement) => node.click())
    const popover = page.locator('.conversation-menu-popover')
    await popover.waitFor({ state: 'hidden', timeout: 1500 }).catch(() => {})
    if (await popover.isVisible().catch(() => false)) {
      await closeConversationMenu(page)
    }
    return
  }

  await action.click()
  await expect(page.locator('.conversation-menu-popover')).toBeHidden({ timeout: 15000 })
}

async function closeConversationMenu(page: Page) {
  const overlay = page.locator('.conversation-menu-overlay')
  if (await overlay.count() > 0) {
    const browserName = page.context().browser()?.browserType().name() || ''
    await overlay.click({ position: { x: 8, y: 8 }, force: true, timeout: 1000 }).catch(async () => {
      await overlay.evaluate((node) => {
        if (node instanceof HTMLElement) {
          node.click()
        }
      }).catch(() => {})
    })
    if (browserName === 'webkit') {
      await page.locator('.conversation-menu-popover').waitFor({ state: 'hidden', timeout: 1500 }).catch(() => {})
      return
    }

    await expect(page.locator('.conversation-menu-popover')).toBeHidden({ timeout: 15000 })
  }
}

async function getConversationNameOrder(page: Page) {
  return page.locator('.conversation-item .conv-name').evaluateAll((nodes) => {
    return nodes.map((node) => (node.textContent || '').trim()).filter(Boolean)
  })
}

test.describe('Messenger conversation list state actions', () => {
  test('direct conversation menu supports pin reorder mute unread and hide flows', async ({ page, request, browserName }) => {
    test.setTimeout(browserName === 'webkit' ? 240000 : 150000)
    const fixture = seedConversationStateFixture('conversation_actions_direct')
    const suffix = Date.now()

    await sendDirectTextMessage(request, fixture.accessToken, fixture.peerAId, `PW DIRECT A ${suffix}`)
    await sendDirectTextMessage(request, fixture.accessToken, fixture.peerBId, `PW DIRECT B ${suffix}`)
    await sendDirectTextMessage(request, fixture.accessToken, fixture.peerCId, `PW DIRECT C ${suffix}`)

    await loginWithSeededSession(page, fixture)
    await page.goto('/chat')

    const rowA = conversationRow(page, fixture.peerAName)
    const rowB = conversationRow(page, fixture.peerBName)
    const rowC = conversationRow(page, fixture.peerCName)

    await expect(rowA).toBeVisible({ timeout: 30000 })
    await expect(rowB).toBeVisible({ timeout: 30000 })
    await expect(rowC).toBeVisible({ timeout: 30000 })

    await openConversationMenuByLongPress(page, rowA)
    await clickConversationMenuAction(page, 'سنجاق کردن')

    await expect(rowA).toHaveClass(/conversation-card--pinned/, { timeout: 30000 })

    await openConversationMenuByLongPress(page, rowB)
    await clickConversationMenuAction(page, 'سنجاق کردن')

    await expect(rowB).toHaveClass(/conversation-card--pinned/, { timeout: 30000 })

    await expect
      .poll(async () => {
        const conversations = await fetchConversations(request, fixture.accessToken)
        return {
          rowA: findConversationByDirectUser(conversations, fixture.peerAId),
          rowB: findConversationByDirectUser(conversations, fixture.peerBId),
        }
      }, { timeout: 30000 })
      .toMatchObject({
        rowA: expect.objectContaining({ is_pinned: true }),
        rowB: expect.objectContaining({ is_pinned: true }),
      })

    await openConversationMenuByLongPress(page, rowA)
    await clickConversationMenuAction(page, 'جابجایی به بالا')

    await expect
      .poll(async () => {
        const order = await getConversationNameOrder(page)
        return order.indexOf(fixture.peerAName) < order.indexOf(fixture.peerBName)
      }, { timeout: 30000 })
      .toBe(true)

    await openConversationMenuByLongPress(page, rowB)
    await clickConversationMenuAction(page, 'برداشتن سنجاق')

    await expect
      .poll(async () => {
        const conversations = await fetchConversations(request, fixture.accessToken)
        return findConversationByDirectUser(conversations, fixture.peerBId)?.is_pinned ?? null
      }, { timeout: 30000 })
      .toBe(false)

    await openConversationMenuByLongPress(page, rowB)
    await clickConversationMenuAction(page, 'بی‌صدا کردن گفتگو')

    await expect(rowB.locator('.side-muted-indicator')).toBeVisible({ timeout: 30000 })
    await expect
      .poll(async () => {
        const conversations = await fetchConversations(request, fixture.accessToken)
        return findConversationByDirectUser(conversations, fixture.peerBId)?.is_muted ?? null
      }, { timeout: 30000 })
      .toBe(true)

    await openConversationMenuByLongPress(page, rowB)
    await clickConversationMenuAction(page, 'خروج از حالت بی‌صدا')

    await expect(rowB.locator('.side-muted-indicator')).toHaveCount(0)
    await expect
      .poll(async () => {
        const conversations = await fetchConversations(request, fixture.accessToken)
        return findConversationByDirectUser(conversations, fixture.peerBId)?.is_muted ?? null
      }, { timeout: 30000 })
      .toBe(false)

    await openConversationMenuByLongPress(page, rowA)
    await clickConversationMenuAction(page, 'علامت‌گذاری به‌عنوان خوانده‌نشده')

    await expect(conversationRow(page, fixture.peerAName).locator('.unread-badge')).toHaveText(/[1۱]/, { timeout: 30000 })
    await expect
      .poll(async () => {
        const conversations = await fetchConversations(request, fixture.accessToken)
        return findConversationByDirectUser(conversations, fixture.peerAId)?.unread_count ?? null
      }, { timeout: 30000 })
      .toBe(1)

    await openConversationMenuByLongPress(page, rowC)
    await clickConversationMenuAction(page, 'حذف گفتگو')

    await expect(rowC).toHaveCount(0)
    await expect
      .poll(async () => {
        const conversations = await fetchConversations(request, fixture.accessToken)
        return findConversationByDirectUser(conversations, fixture.peerCId)
      }, { timeout: 30000 })
      .toBeNull()
  })

  test('mandatory and optional room menu actions preserve mandatory ordering and allow unfollow', async ({ page, request }) => {
    test.setTimeout(90000)
    const fixture = seedConversationStateFixture('conversation_actions_rooms')

    await loginWithSeededSession(page, fixture)
    await page.goto('/chat')

    const mandatoryRow = conversationRow(page, fixture.mandatoryChannelTitle)
    const optionalRow = conversationRow(page, fixture.optionalChannelTitle)

    await expect(mandatoryRow).toBeVisible({ timeout: 30000 })
    await expect(optionalRow).toBeVisible({ timeout: 30000 })

    await expect
      .poll(async () => {
        const order = await getConversationNameOrder(page)
        return order.indexOf(fixture.mandatoryChannelTitle) < order.indexOf(fixture.optionalChannelTitle)
      }, { timeout: 30000 })
      .toBe(true)

    await openConversationMenuByLongPress(page, mandatoryRow)
    await expect(page.locator('.conversation-menu-popover .menu-action').filter({ hasText: 'سنجاق کردن' })).toHaveCount(0)
    await expect(page.locator('.conversation-menu-popover .menu-action').filter({ hasText: 'برداشتن سنجاق' })).toHaveCount(0)
    await expect(page.locator('.conversation-menu-popover .menu-action').filter({ hasText: 'بی‌صدا کردن گفتگو' })).toHaveCount(0)
    await closeConversationMenu(page)

    await openConversationMenuByLongPress(page, optionalRow)
    await clickConversationMenuAction(page, 'سنجاق کردن')

    await expect
      .poll(async () => {
        const order = await getConversationNameOrder(page)
        return order.indexOf(fixture.mandatoryChannelTitle) < order.indexOf(fixture.optionalChannelTitle)
      }, { timeout: 30000 })
      .toBe(true)

    await expect
      .poll(async () => {
        const conversations = await fetchConversations(request, fixture.accessToken)
        return findConversationByRoom(conversations, 'channel', fixture.optionalChannelId)?.is_pinned ?? null
      }, { timeout: 30000 })
      .toBe(true)

    await openConversationMenuByLongPress(page, optionalRow)
    await clickConversationMenuAction(page, 'لغو دنبال‌کردن')

    await expect(optionalRow).toHaveCount(0)
    await expect
      .poll(async () => {
        const conversations = await fetchConversations(request, fixture.accessToken)
        return findConversationByRoom(conversations, 'channel', fixture.optionalChannelId)
      }, { timeout: 30000 })
      .toBeNull()
  })
})
