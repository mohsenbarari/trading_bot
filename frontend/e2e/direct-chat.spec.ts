/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SeededSessionFixture {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

interface DirectMessageReaction {
  emoji?: string
  user_id?: number
}

interface DirectMessageRecord {
  id?: number
  content?: string
  is_deleted?: boolean
  updated_at?: string | null
  sender_id?: number
  reactions?: DirectMessageReaction[]
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
            address='Playwright Direct Chat',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Direct Chat Device',
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

function authHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
}

async function loginWithSeededSession(page: Page, fixture: SeededSessionFixture) {
  await primeAuthSession(page, fixture.accessToken, fixture.refreshToken)
}

function activeComposerTextbox(page: Page) {
  return page.locator('.chat-view .input-area .input-container:visible').last().locator('textarea[placeholder="پیام..."]').first()
}

async function sendActiveComposerMessage(page: Page, content: string) {
  const container = page.locator('.chat-view .input-area .input-container:visible').last()
  const composer = container.locator('textarea[placeholder="پیام..."]').first()
  await expect(composer).toBeVisible({ timeout: 30000 })
  await composer.click()
  await composer.fill('')
  await composer.pressSequentially(content)
  await expect(composer).toHaveValue(content, { timeout: 30000 })

  const sendButton = container.locator('.send-btn-inline')
  await expect(sendButton).toBeVisible({ timeout: 30000 })
  await sendButton.click()
}

async function openDirectChat(page: Page, otherUserId: number) {
  await page.goto(`/chat?user_id=${otherUserId}`)
  await expect(activeComposerTextbox(page)).toBeVisible({ timeout: 30000 })
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

async function fetchDirectMessages(
  request: APIRequestContext,
  fixture: SeededSessionFixture,
  otherUserId: number,
): Promise<DirectMessageRecord[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/messages/${otherUserId}?limit=20`, {
    headers: authHeaders(fixture.accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = await response.json() as DirectMessageRecord[]
  return Array.isArray(body) ? body : []
}

test.describe('Direct chat regressions', () => {
  test('direct chat composer sends and edits an own text message', async ({ page, request }) => {
    const actor = seedPrimarySession('direct_actor_edit')
    const peer = seedPrimarySession('direct_peer_edit')
    const bootstrapContent = `PW DIRECT BOOTSTRAP ${Date.now()}`
    const initialContent = `PW DIRECT SEND ${Date.now()}`
    const editedContent = `PW DIRECT EDIT ${Date.now()}`

    await sendTextChatMessage(request, peer, actor.userId, bootstrapContent)

    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId)
    await expect(page.getByText(bootstrapContent)).toBeVisible()

    await sendActiveComposerMessage(page, initialContent)

    let createdMessageId: number | null = null
    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        createdMessageId = messages.find((message) => message.content === initialContent)?.id ?? null
        return createdMessageId
      }, { timeout: 30000 })
      .not.toBeNull()

    const sentBubble = page.locator('.message-bubble.sent').filter({ hasText: initialContent }).first()
    await expect(sentBubble).toBeVisible({ timeout: 30000 })

    await sentBubble.click()
    await page.locator('.context-menu .menu-item').filter({ hasText: 'ویرایش' }).click()

    const editComposer = page.locator('textarea[placeholder="ویرایش پیام..."]')
    await expect(editComposer).toHaveValue(initialContent)
    await editComposer.fill(editedContent)
    await editComposer.press('Enter')

    const editedBubble = page.locator('.message-bubble.sent').filter({ hasText: editedContent }).first()
    await expect(editedBubble).toBeVisible({ timeout: 30000 })
    await expect(editedBubble.locator('.edited-label')).toBeVisible()

    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        return messages.find((message) => message.id === createdMessageId) ?? null
      }, { timeout: 30000 })
      .toMatchObject({
        id: createdMessageId,
        content: editedContent,
      })

    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        return messages.find((message) => message.id === createdMessageId)?.updated_at ?? null
      }, { timeout: 30000 })
      .not.toBeNull()
  })

  test('direct chat supports reacting to an incoming message and deleting an own message', async ({ page, request }) => {
    const actor = seedPrimarySession('direct_actor_mutations')
    const peer = seedPrimarySession('direct_peer_mutations')
    const incomingContent = `PW DIRECT INCOMING ${Date.now()}`
    const deleteContent = `PW DIRECT DELETE ${Date.now()}`

    await sendTextChatMessage(request, peer, actor.userId, incomingContent)

    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId)
    await expect(page.getByText(incomingContent)).toBeVisible()

    const incomingBubble = page.locator('.message-bubble.received').filter({ hasText: incomingContent })
    await incomingBubble.click()
    await page.locator('.context-menu .reaction-btn').filter({ hasText: '👍' }).first().click()

    await expect(page.locator('.reaction-chip').filter({ hasText: '👍' })).toHaveCount(1)

    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        return messages.find((message) => message.content === incomingContent)?.reactions ?? []
      }, { timeout: 30000 })
      .toEqual(expect.arrayContaining([
        expect.objectContaining({ emoji: '👍', user_id: actor.userId }),
      ]))

    await sendActiveComposerMessage(page, deleteContent)

    const deleteBubble = page.locator('.message-bubble.sent').filter({ hasText: deleteContent })
    await expect(deleteBubble).toBeVisible()

    let deleteMessageId: number | null = null
    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        deleteMessageId = messages.find((message) => message.content === deleteContent)?.id ?? null
        return deleteMessageId
      }, { timeout: 30000 })
      .not.toBeNull()

    page.once('dialog', (dialog) => {
      void dialog.accept()
    })

    await deleteBubble.click()
    await page.locator('.context-menu [role="menuitem"]').filter({ hasText: 'حذف' }).click()
    await expect(page.getByText(deleteContent)).toHaveCount(0)

    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        return messages.some((message) => message.id === deleteMessageId)
      }, { timeout: 30000 })
      .toBeFalsy()
  })
})
