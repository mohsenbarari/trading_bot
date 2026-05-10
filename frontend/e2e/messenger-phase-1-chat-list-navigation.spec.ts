/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

test.describe.serial('Messenger Phase 1 - chat list, navigation, header, and new chat UI', () => {
  test('shows the empty-state messenger UI for a brand-new account', async ({ page }) => {
    const emptyUser = seedPrimarySession('phase1_empty_state')

    await test.step('prime auth storage for a new user session', async () => {
      await primeAuthStorage(page, emptyUser)
    })

    await test.step('open messenger and assert the stable empty state', async () => {
      await page.goto('/chat')
      await expect(page.getByTestId('messenger-chat-wrapper')).toBeVisible({ timeout: 15000 })
      await expect(page.getByTestId('conversation-list-empty-state')).toBeVisible()
      await expect(page.getByText('گفتگویی وجود ندارد')).toBeVisible()
    })
  })

  test('renders conversation rows, unread state, pinning, and direct-chat open/back navigation', async ({ page, request }) => {
    const actor = seedPrimarySession('phase1_actor_list')
    const unreadPeer = seedPrimarySession('phase1_peer_unread')
    const pinnedPeer = seedPrimarySession('phase1_peer_pin')
    const unreadMessage = `PW PHASE1 UNREAD ${Date.now()}`
    const pinnedMessage = `PW PHASE1 PIN ${Date.now()}`

    await sendTextChatMessage(request, unreadPeer, actor.userId, unreadMessage)
    await sendTextChatMessage(request, pinnedPeer, actor.userId, pinnedMessage)

    await test.step('log in and verify the seeded conversations render in the list with unread badge', async () => {
      await loginWithSeededSession(page, actor)
      await page.goto('/chat')

      await expect(page.getByTestId('conversation-list')).toBeVisible()
      await expect(page.getByTestId(`conversation-card-${unreadPeer.userId}`)).toContainText(unreadPeer.accountName)
      await expect(page.getByTestId(`conversation-card-${unreadPeer.userId}`)).toContainText(unreadMessage)
      await expect(page.getByTestId(`conversation-unread-badge-${unreadPeer.userId}`)).toBeVisible()
      await expect(page.getByTestId(`conversation-card-${pinnedPeer.userId}`)).toContainText(pinnedPeer.accountName)
      await expect(page.getByTestId(`conversation-card-${pinnedPeer.userId}`)).toContainText(pinnedMessage)
    })

    await test.step('toggle messenger search UI from the list header', async () => {
      await page.getByTestId('messenger-open-search-button').click()
      await expect(page.getByTestId('messenger-search-bar')).toBeVisible()
      await expect(page.getByTestId('messenger-search-input')).toBeVisible()
      await page.getByTestId('messenger-search-close-button').click()
      await expect(page.getByTestId('messenger-search-bar')).toBeHidden()
    })

    await test.step('open the conversation action menu, pin a conversation, and verify visual pinning/order', async () => {
      const pinnedRow = page.getByTestId(`conversation-card-${pinnedPeer.userId}`)
      await pinnedRow.click({ button: 'right' })
      await expect(page.getByTestId('conversation-actions-menu')).toBeVisible()
      await page.getByRole('button', { name: /سنجاق کردن/ }).click()

      await expect.poll(async () => page
        .locator('[data-testid="conversation-list"] [data-testid^="conversation-card-"]')
        .first()
        .getAttribute('data-testid')).toBe(`conversation-card-${pinnedPeer.userId}`)

      await page.getByTestId(`conversation-card-${pinnedPeer.userId}`).click({ button: 'right' })
      await expect(page.getByTestId('conversation-actions-menu')).toBeVisible()
      await expect(page.getByRole('button', { name: /برداشتن سنجاق/ })).toBeVisible()

      const viewport = page.viewportSize()
      if (!viewport) {
        throw new Error('Playwright viewport size is not available')
      }

      await page.mouse.click(Math.floor(viewport.width / 2), Math.max(10, viewport.height - 10))
      await expect(page.getByTestId('conversation-actions-menu')).toBeHidden()
    })

    await test.step('open an unread direct chat, verify header/route, and return to the list cleanly', async () => {
      const unreadRow = page.getByTestId(`conversation-card-${unreadPeer.userId}`)
      await unreadRow.click()

      await expect.poll(() => new URL(page.url()).searchParams.get('user_id')).toBe(String(unreadPeer.userId))
      await expect(page.getByTestId('messenger-header')).toBeVisible()
      await expect(page.getByTestId('messenger-header')).toContainText(unreadPeer.accountName)
      await expect(page.getByText(unreadMessage)).toBeVisible()

      await page.getByTestId('chat-header-back-button').click()
      await expect(page).toHaveURL(/\/chat$/)
      await expect(page.getByTestId('conversation-list')).toBeVisible()
      await expect(page.getByTestId(`conversation-unread-badge-${unreadPeer.userId}`)).toBeHidden()
    })
  })

  test('opens the self public profile from the messenger menu and returns without bouncing back to chat', async ({ page }) => {
    const actor = seedPrimarySession('phase1_self_profile')

    await test.step('log in and open the messenger list header menu', async () => {
      await loginWithSeededSession(page, actor)
      await page.goto('/chat')
      await expect(page.getByTestId('messenger-header-title')).toContainText('پیام‌ها')
      await page.getByTestId('messenger-header-menu-button').click()
      await expect(page.getByTestId('messenger-header-menu')).toBeVisible()
    })

    await test.step('navigate to self public profile and verify the profile route and visible identity', async () => {
      await page.getByTestId('messenger-menu-self-profile').click()
      await expect(page).toHaveURL(new RegExp(`/users/${actor.userId}$`))
      await expect(page.getByRole('heading', { name: actor.accountName, exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: /بازگشت/ })).toBeVisible()
    })

    await test.step('return from public profile and verify messenger remains the destination', async () => {
      await page.getByRole('button', { name: /بازگشت/ }).click()
      await expect(page).toHaveURL(/\/chat$/)
      await expect(page.getByTestId('conversation-list')).toBeVisible()
    })
  })

  test('opens the new conversation modal, searches users, and starts a direct chat from the result list', async ({ page }) => {
    const actor = seedPrimarySession('phase1_new_chat_actor')
    const searchableUser = seedPrimarySession('phase1_new_chat_target')

    await test.step('open the new conversation modal and verify its visual shell', async () => {
      await loginWithSeededSession(page, actor)
      await page.goto('/chat')
      await page.getByTestId('messenger-new-chat-button').click()

      await expect(page.getByTestId('new-chat-modal')).toBeVisible()
      await expect(page.getByTestId('new-chat-modal-header')).toContainText('شروع مکالمه جدید')
      await expect(page.getByTestId('new-chat-search-input')).toBeVisible()
      await expect(page.getByTestId('new-chat-user-list')).toBeVisible()
    })

    await test.step('search for a unique user and confirm the result row appears', async () => {
      await page.getByTestId('new-chat-search-input').fill(searchableUser.accountName)
      await expect(page.getByTestId(`new-chat-user-${searchableUser.userId}`)).toBeVisible()
      await expect(page.getByTestId(`new-chat-user-${searchableUser.userId}`)).toContainText(searchableUser.accountName)
    })

    await test.step('start a direct chat from the search result and verify the in-chat state', async () => {
      await page.getByTestId(`new-chat-user-${searchableUser.userId}`).click()
      await expect(page.getByTestId('new-chat-modal')).toBeHidden()
      await expect(page.getByTestId('messenger-header')).toContainText(searchableUser.accountName)
      await expect(page.getByText('شروع گفتگو...')).toBeVisible()
      await expect(page.getByRole('textbox', { name: 'پیام...' })).toBeVisible()
    })
  })
})

interface SeededSessionFixture {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

function runPythonInApp<T>(script: string): T {
  const stdout = execFileSync('docker', ['exec', '-i', 'trading_bot_app', 'python', '-'], {
    input: script,
    encoding: 'utf8',
  })

  const lastLine = stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
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
            address='Playwright Messenger Phase 1',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Messenger Phase 1 Device',
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

async function primeAuthStorage(page: Page, fixture: SeededSessionFixture) {
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

async function loginWithSeededSession(page: Page, fixture: SeededSessionFixture) {
  await primeAuthStorage(page, fixture)
  await page.goto('/')
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
