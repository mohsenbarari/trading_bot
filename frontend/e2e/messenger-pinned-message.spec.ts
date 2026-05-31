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

interface DirectPinnedState {
  room_kind: string
  chat_id?: number | null
  message?: { id: number; content?: string } | null
}

interface GroupRoom {
  id: number
  title: string
  description?: string | null
}

interface ChannelRoom {
  id: number
  title: string
  description?: string | null
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
    throw new Error('No JSON output returned from trading_bot_app pinned-message seed helper')
  }

  return JSON.parse(lastLine) as T
}

function seedSessionUser(label: string, role: 'standard' | 'super_admin'): SessionUser {
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
role_name = ${JSON.stringify(role)}

async def main():
    suffix = uuid.uuid4().hex[:10]
    account_name = f"pw_{label}_{suffix}"
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    role = UserRole.SUPER_ADMIN if role_name == 'super_admin' else UserRole.STANDARD

    async with AsyncSessionLocal() as db:
        user = User(
            account_name=account_name,
            mobile_number=f"09{mobile_seed:09d}",
            full_name=account_name,
            address='Playwright Pinned Message',
            role=role,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Pinned Message Device',
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

async function loginWithSeededSession(page: Page, fixture: SessionUser) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, {
    accessToken: fixture.accessToken,
    refreshToken: fixture.refreshToken,
  })
}

async function openChat(page: Page, conversationKey: number, userName: string) {
  const encodedName = encodeURIComponent(userName)
  await page.goto(`/chat?user_id=${conversationKey}&user_name=${encodedName}`)
  await expect(page.locator('.chat-header .header-name')).toContainText(userName, { timeout: 30000 })
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
  return response.json() as Promise<{ id: number; content: string }>
}

async function sendRoomTextMessage(
  request: APIRequestContext,
  accessToken: string,
  chatId: number,
  content: string,
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/rooms/${chatId}/send`, {
    headers: authHeaders(accessToken),
    data: {
      content,
      message_type: 'text',
    },
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<{ id: number; content: string }>
}

async function createGroup(
  request: APIRequestContext,
  accessToken: string,
  title: string,
  description: string,
): Promise<GroupRoom> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/groups`, {
    headers: authHeaders(accessToken),
    data: {
      title,
      description,
      member_ids: [],
    },
  })

  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as { group: GroupRoom }
  return payload.group
}

async function createChannel(
  request: APIRequestContext,
  accessToken: string,
  title: string,
  description: string,
): Promise<ChannelRoom> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/channels`, {
    headers: authHeaders(accessToken),
    data: {
      title,
      description,
    },
  })

  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as { channel: ChannelRoom }
  return payload.channel
}

function isTransientPinnedStateError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error)
  return /socket hang up|ECONNRESET|ETIMEDOUT|EAI_AGAIN/i.test(message)
}

async function getPinnedStateWithRetry(
  request: APIRequestContext,
  url: string,
  accessToken: string,
) {
  let lastError: unknown = null
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await request.get(url, {
        headers: authHeaders(accessToken),
      })

      if (response.ok()) {
        return response
      }
      if ((response.status() >= 500 || response.status() === 408) && attempt < 2) {
        await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)))
        continue
      }
      expect(response.ok()).toBeTruthy()
      return response
    } catch (error) {
      lastError = error
      if (!isTransientPinnedStateError(error) || attempt === 2) {
        throw error
      }
      await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)))
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Failed to fetch pinned state after retries')
}

async function fetchDirectPinnedState(
  request: APIRequestContext,
  accessToken: string,
  otherUserId: number,
): Promise<DirectPinnedState> {
  const response = await getPinnedStateWithRetry(
    request,
    `${BACKEND_BASE_URL}/api/chat/direct/${otherUserId}/pinned-message`,
    accessToken,
  )
  return response.json() as Promise<DirectPinnedState>
}

async function fetchRoomPinnedState(
  request: APIRequestContext,
  accessToken: string,
  chatId: number,
): Promise<DirectPinnedState> {
  const response = await getPinnedStateWithRetry(
    request,
    `${BACKEND_BASE_URL}/api/chat/rooms/${chatId}/pinned-message`,
    accessToken,
  )
  return response.json() as Promise<DirectPinnedState>
}

async function pinMessageFromBubble(page: Page, content: string) {
  const bubble = page.locator('.message-bubble').filter({ hasText: content }).first()
  await expect(bubble).toBeVisible({ timeout: 30000 })
  await bubble.click()
  await page.locator('.context-menu [role="menuitem"]').filter({ hasText: 'سنجاق کردن پیام' }).click()
  return bubble
}

async function scrollMessagesToBottom(page: Page) {
  await page.locator('.messages-container').evaluate((node) => {
    node.scrollTop = node.scrollHeight
  })
}

test.describe('Messenger pinned-message regressions', () => {
  test('direct rooms support pin banner render, jump, persistence, and unpin', async ({ page, request }) => {
    test.setTimeout(90000)
    const actor = seedSessionUser('pinned_direct_actor', 'standard')
    const peer = seedSessionUser('pinned_direct_peer', 'standard')
    const suffix = Date.now()
    const pinnedContent = `PW PINNED DIRECT TARGET ${suffix}`

    await waitForBackendReady(request)
    await sendDirectTextMessage(request, actor.accessToken, peer.userId, pinnedContent)
    for (let index = 0; index < 18; index += 1) {
      await sendDirectTextMessage(request, actor.accessToken, peer.userId, `PW DIRECT FILL ${suffix} #${index} ${'متن تست '.repeat(10)}`)
    }

    await loginWithSeededSession(page, actor)
    await openChat(page, peer.userId, peer.accountName)

    const targetBubble = await pinMessageFromBubble(page, pinnedContent)
    const pinnedBanner = page.locator('.pinned-message-banner')
    await expect(pinnedBanner).toBeVisible({ timeout: 30000 })
    await expect(pinnedBanner).toContainText('پیام سنجاق‌شده')
    await expect(pinnedBanner.locator('.pinned-message-meta')).toHaveText('برای رفتن به پیام ضربه بزنید')
    await expect(pinnedBanner.locator('.pinned-message-preview')).toContainText(pinnedContent)

    const pinnedState = await fetchDirectPinnedState(request, actor.accessToken, peer.userId)
    expect(pinnedState.message?.content).toBe(pinnedContent)

    await scrollMessagesToBottom(page)
    await pinnedBanner.click()
    await expect(targetBubble).toHaveClass(/highlight-message/, { timeout: 30000 })

    await page.reload()
    await expect(page.locator('.pinned-message-banner')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.pinned-message-banner .pinned-message-preview')).toContainText(pinnedContent)

    await page.locator('.pinned-message-dismiss').click()
    await expect(page.locator('.pinned-message-banner')).toHaveCount(0)
    await expect
      .poll(async () => (await fetchDirectPinnedState(request, actor.accessToken, peer.userId)).message ?? null, { timeout: 30000 })
      .toBeNull()
  })

  test('group rooms support pin banner render, jump, persistence, and unpin', async ({ page, request }) => {
    test.setTimeout(90000)
    const owner = seedSessionUser('pinned_group_owner', 'standard')
    const suffix = Date.now()
    const title = `Playwright Pinned Group ${suffix}`
    const group = await createGroup(request, owner.accessToken, title, 'Pinned group flow')
    const pinnedContent = `PW PINNED GROUP TARGET ${suffix}`

    await waitForBackendReady(request)
    await sendRoomTextMessage(request, owner.accessToken, group.id, pinnedContent)
    for (let index = 0; index < 18; index += 1) {
      await sendRoomTextMessage(request, owner.accessToken, group.id, `PW GROUP FILL ${suffix} #${index} ${'متن تست '.repeat(10)}`)
    }

    await loginWithSeededSession(page, owner)
    await openChat(page, -group.id, group.title)

    const targetBubble = await pinMessageFromBubble(page, pinnedContent)
    const pinnedBanner = page.locator('.pinned-message-banner')
    await expect(pinnedBanner).toBeVisible({ timeout: 30000 })
    await expect(pinnedBanner.locator('.pinned-message-meta')).toContainText(owner.accountName)
    await expect(pinnedBanner.locator('.pinned-message-preview')).toContainText(pinnedContent)

    await expect
      .poll(async () => (await fetchRoomPinnedState(request, owner.accessToken, group.id)).message?.content ?? null, { timeout: 30000 })
      .toBe(pinnedContent)

    await scrollMessagesToBottom(page)
    await pinnedBanner.click()
    await expect(targetBubble).toHaveClass(/highlight-message/, { timeout: 30000 })

    await page.reload()
    await expect(page.locator('.pinned-message-banner')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.pinned-message-banner .pinned-message-preview')).toContainText(pinnedContent)

    await page.locator('.pinned-message-dismiss').click()
    await expect(page.locator('.pinned-message-banner')).toHaveCount(0)
    await expect
      .poll(async () => (await fetchRoomPinnedState(request, owner.accessToken, group.id)).message ?? null, { timeout: 30000 })
      .toBeNull()
  })

  test('channel rooms support pin banner render, jump, persistence, and unpin', async ({ page, request }) => {
    test.setTimeout(90000)
    const owner = seedSessionUser('pinned_channel_owner', 'super_admin')
    const suffix = Date.now()
    const title = `Playwright Pinned Channel ${suffix}`
    const channel = await createChannel(request, owner.accessToken, title, 'Pinned channel flow')
    const pinnedContent = `PW PINNED CHANNEL TARGET ${suffix}`

    await waitForBackendReady(request)
    await sendRoomTextMessage(request, owner.accessToken, channel.id, pinnedContent)
    for (let index = 0; index < 18; index += 1) {
      await sendRoomTextMessage(request, owner.accessToken, channel.id, `PW CHANNEL FILL ${suffix} #${index} ${'متن تست '.repeat(10)}`)
    }

    await loginWithSeededSession(page, owner)
    await openChat(page, -channel.id, channel.title)

    const targetBubble = await pinMessageFromBubble(page, pinnedContent)
    const pinnedBanner = page.locator('.pinned-message-banner')
    await expect(pinnedBanner).toBeVisible({ timeout: 30000 })
    await expect(pinnedBanner.locator('.pinned-message-meta')).toContainText(owner.accountName)
    await expect(pinnedBanner.locator('.pinned-message-preview')).toContainText(pinnedContent)

    await expect
      .poll(async () => (await fetchRoomPinnedState(request, owner.accessToken, channel.id)).message?.content ?? null, { timeout: 30000 })
      .toBe(pinnedContent)

    await scrollMessagesToBottom(page)
    await pinnedBanner.click()
    await expect(targetBubble).toHaveClass(/highlight-message/, { timeout: 30000 })

    await page.reload()
    await expect(page.locator('.pinned-message-banner')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.pinned-message-banner .pinned-message-preview')).toContainText(pinnedContent)

    await page.locator('.pinned-message-dismiss').click()
    await expect(page.locator('.pinned-message-banner')).toHaveCount(0)
    await expect
      .poll(async () => (await fetchRoomPinnedState(request, owner.accessToken, channel.id)).message ?? null, { timeout: 30000 })
      .toBeNull()
  })
})