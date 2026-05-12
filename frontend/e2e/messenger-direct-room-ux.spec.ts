/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'
const TINY_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ6kAAAAASUVORK5CYII='
const GENERATED_WEBM_BASE64 = 'GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAMpEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHYTbuMU6uEElTDZ1OsggEeTbuMU6uEHFO7a1OsggMT7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsirXsYMPQkBNgI1MYXZmNjAuMTYuMTAwV0GNTGF2ZjYwLjE2LjEwMESJiECCwAAAAAAAFlSua8GuAQAAAAAAADjXgQFzxYgcOxjQCFYmc5yBACK1nIN1bmSIgQCGhVZfVlA5g4EBI+ODhAJiWgDgibCBYLqBYJqBAhJUw2dAgHNzoGPAgGfImkWjh0VOQ09ERVJEh41MYXZmNjAuMTYuMTAwc3PaY8CLY8WIHDsY0AhWJnNnyKVFo4dFTkNPREVSRIeYTGF2YzYwLjMxLjEwMiBsaWJ2cHgtdnA5Z8ihRaOIRFVSQVRJT05Eh5MwMDowMDowMC42MDAwMDAwMDAAH0O2dUFp54EAo7CBAACAgkmDQgAF8AX2ADgkHBhKAAAwYAAAfKn//1zBn///25IP//6uxlgyUsRrFACjlIEAKACGAECSnABQAAADIAAAWTDgo5SBAFAAhgBAkpwATuAAAyAAAFkw4KOUgQB4AIYAQJKcAFAAAAMgAABZMOCjlIEAoACGAECSnABNQAADIAAAWTDgo5SBAMgAhgBAkpwAUAAAAyAAAFkw4KOUgQDwAIYAQJKcAE7gAAMgAABZMOCjlIEBGACGAECSnABQAAADIAAAWTDgo5SBAUAAhgBAkpwASiAAAyAAAFkw4KOUgQFoAIYAQJKcAFAAAAMgAABZMOCjlIEBkACGAMCSnABKIAADIAAAWTDgo5SBAbgAhgBAkpwAUAAAAyAAAFkw4KOUgQHgAIYAQJKcAE1AAAMgAABZMOCjlIECCACGAECSnABQAAADIAAAWTDgo5SBAjAAhgBAkpwATuAAAyAAAFkw4BxTu2uRu4+zgQC3iveBAfGCAaTwgQM='

interface SessionFixture {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

interface DirectMessageRecord {
  id: number
  content: string
  message_type: string
  is_deleted?: boolean
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
    throw new Error('No JSON output returned from trading bot app seed helper')
  }

  return JSON.parse(lastLine) as T
}

function seedPrimarySession(label: string): SessionFixture {
  return runPythonInApp<SessionFixture>(`
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
            address='Playwright Direct Room',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Direct Room Device',
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

function createPlaywrightBinaryFile(name: string, mimeType: string, bodyBase64: string) {
  return {
    name,
    mimeType,
    buffer: Buffer.from(bodyBase64, 'base64'),
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

async function loginWithSeededSession(page: Page, fixture: SessionFixture) {
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

async function openDirectChat(page: Page, otherUserId: number, userName: string) {
  await page.goto(`/chat?user_id=${otherUserId}&user_name=${encodeURIComponent(userName)}`)
  await expect(page.locator('.chat-header .header-name')).toContainText(userName, { timeout: 30000 })
}

async function sendDirectTextMessage(
  request: APIRequestContext,
  sender: SessionFixture,
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
  return response.json() as Promise<{ id: number; content: string }>
}

async function fetchDirectMessages(
  request: APIRequestContext,
  fixture: SessionFixture,
  otherUserId: number,
): Promise<DirectMessageRecord[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/messages/${otherUserId}?limit=60`, {
    headers: authHeaders(fixture.accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = await response.json() as DirectMessageRecord[]
  return Array.isArray(body) ? body : []
}

async function injectGalleryAlbum(page: Page) {
  const suffix = Date.now()
  await page.locator('input[type="file"][accept="image/*,video/*"]').setInputFiles([
    createPlaywrightBinaryFile(`pw-direct-${suffix}.png`, 'image/png', TINY_PNG_BASE64),
    createPlaywrightBinaryFile(`pw-direct-${suffix}.webm`, 'video/webm', GENERATED_WEBM_BASE64),
  ])
}

async function injectDocument(page: Page) {
  const suffix = Date.now()
  await page.locator('input[type="file"][accept="*"]').setInputFiles([
    {
      name: `pw-direct-${suffix}.txt`,
      mimeType: 'text/plain',
      buffer: Buffer.from(`Playwright document ${suffix}`, 'utf8'),
    },
  ])
}

async function openDirectHeaderSearch(page: Page) {
  await page.locator('.chat-header .header-menu-container .header-btn').last().click()
  await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'جستجو' }).click()
  await expect(page.locator('#search-input')).toBeVisible({ timeout: 30000 })
}

test.describe('Messenger direct-room media/search/viewer regressions', () => {
  test('new conversation modal can start a direct chat and file tab sends a document', async ({ page, request }) => {
    test.setTimeout(90000)
    const actor = seedPrimarySession('direct_room_modal_actor')
    const peer = seedPrimarySession('direct_room_modal_peer')

    await waitForBackendReady(request)
    await loginWithSeededSession(page, actor)

    await page.goto('/chat')
    await expect(page.locator('.fab-new-chat')).toBeVisible({ timeout: 30000 })
    await page.locator('.fab-new-chat').click()
    await expect(page.locator('.new-chat-search-input')).toBeVisible({ timeout: 30000 })

    await page.locator('.new-chat-search-input').fill(peer.accountName)
    await expect(page.locator('.users-list').getByText(peer.accountName)).toBeVisible({ timeout: 30000 })
    await page.locator('.users-list').getByText(peer.accountName).click()

    await expect(page.locator('.chat-header .header-name')).toContainText(peer.accountName, { timeout: 30000 })
    await expect(page).toHaveURL(/\/chat$/)

    await page.locator('button.attach-btn').click()
    await expect(page.locator('.attachment-sheet')).toBeVisible({ timeout: 30000 })
    await page.getByRole('button', { name: 'فایل' }).first().click()
    await injectDocument(page)

    await expect(page.locator('.messages-container .msg-document')).toBeVisible({ timeout: 30000 })
    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        return messages.some((message) => message.message_type === 'document')
      }, { timeout: 30000 })
      .toBe(true)
  })

  test('in-chat search can jump to results, switch to list mode, and close overlays via browser back', async ({ page, request }) => {
    test.setTimeout(90000)
    const actor = seedPrimarySession('direct_room_search_actor')
    const peer = seedPrimarySession('direct_room_search_peer')
    const suffix = Date.now()
    const targetContent = `PW DIRECT SEARCH TARGET ${suffix}`

    await waitForBackendReady(request)
    for (let index = 0; index < 5; index += 1) {
      await sendDirectTextMessage(request, peer, actor.userId, `PW DIRECT SEARCH FILL ${suffix} #${index}`)
    }
    await sendDirectTextMessage(request, peer, actor.userId, targetContent)
    for (let index = 5; index < 10; index += 1) {
      await sendDirectTextMessage(request, peer, actor.userId, `PW DIRECT SEARCH FILL ${suffix} #${index}`)
    }

    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId, peer.accountName)
    await openDirectHeaderSearch(page)

    await page.locator('#search-input').fill(targetContent)
    await expect(page.locator('.search-bottom-bar')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.message-bubble').filter({ hasText: targetContent }).first()).toHaveClass(/highlight-message/, { timeout: 30000 })

    await page.locator('.toggle-list-btn').click()
    await expect(page.locator('.search-global-list .search-result-item').filter({ hasText: targetContent })).toBeVisible({ timeout: 30000 })

    await page.evaluate(() => window.history.back())
    await expect(page.locator('.search-global-list')).toHaveCount(0, { timeout: 30000 })
    await expect(page.locator('#search-input')).toBeVisible({ timeout: 30000 })

    await page.locator('.mobile-back-btn').click()
    await expect(page.locator('#search-input')).toHaveCount(0, { timeout: 30000 })
    await expect(page.locator('.chat-header .header-name')).toContainText(peer.accountName)
  })

  test('gallery album send opens the lightbox with toolbar actions and strip navigation', async ({ page, request }) => {
    test.setTimeout(120000)
    const actor = seedPrimarySession('direct_room_media_actor')
    const peer = seedPrimarySession('direct_room_media_peer')

    await waitForBackendReady(request)
    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId, peer.accountName)

    await page.locator('button.attach-btn').click()
    await expect(page.locator('.attachment-sheet')).toBeVisible({ timeout: 30000 })
    await injectGalleryAlbum(page)

    await expect(page.locator('.gp-title')).toHaveText('2 مورد', { timeout: 30000 })
    await page.getByRole('button', { name: 'ارسال 2 مورد' }).click()
    await expect(page.locator('.gp-title')).toHaveCount(0, { timeout: 30000 })

    await expect(page.locator('.messages-container [data-media-msg-id]')).toHaveCount(1, { timeout: 30000 })
    await expect(page.locator('.messages-container video')).toHaveCount(1, { timeout: 30000 })

    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        return messages.map((message) => message.message_type)
      }, { timeout: 30000 })
      .toEqual(expect.arrayContaining(['image', 'video']))

    await page.locator('.messages-container img[data-media-msg-id]').first().click()
    await expect(page.locator('.lightbox-overlay')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.lightbox-toolbar')).toContainText('پاسخ')
    await expect(page.locator('.lightbox-toolbar')).toContainText('هدایت')
    await expect(page.locator('.lightbox-toolbar')).toContainText('اشتراک')
    await expect(page.locator('.lightbox-stage-counter')).toContainText(/1\s*\/\s*2/)
    await expect(page.locator('.lightbox-thumb')).toHaveCount(2)

    await page.locator('.lightbox-thumb').nth(1).click()
    await expect(page.locator('.lightbox-stage-counter')).toContainText(/2\s*\/\s*2/)

    await page.locator('.lightbox-btn.close').click()
    await expect(page.locator('.lightbox-overlay')).toHaveCount(0, { timeout: 30000 })
  })
})