/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SessionFixture {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
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
  if (exactAppName) return exactAppName

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
    .map((line) => line.trim())
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

    async with AsyncSessionLocal() as db:
        user = User(
            account_name=account_name,
            mobile_number=f"09{mobile_seed:09d}",
            full_name=account_name,
            address='Playwright Virtual Timeline',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Virtual Timeline Device',
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
  await primeAuthSession(page, fixture.accessToken, fixture.refreshToken)
}

function activeHeaderName(page: Page) {
  return page.locator('.chat-header .header-name:visible').first()
}

async function openDirectChat(page: Page, otherUserId: number, userName: string) {
  await page.goto(`/chat?user_id=${otherUserId}&user_name=${encodeURIComponent(userName)}`)
  await expect(activeHeaderName(page)).toContainText(userName, { timeout: 30000 })
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

async function openDirectHeaderSearch(page: Page) {
  await page.locator('.chat-header .header-menu-container .header-btn').last().evaluate((node: HTMLButtonElement) => {
    node.click()
  })
  await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'جستجو' }).click()
  await expect(page.locator('#search-input')).toBeVisible({ timeout: 30000 })
}

test.describe('Messenger virtual timeline flag regressions', () => {
  test.skip(process.env.VITE_MESSENGER_VIRTUAL_TIMELINE !== 'true', 'requires VITE_MESSENGER_VIRTUAL_TIMELINE=true')

  test('heavy direct-room search jump renders and highlights an offscreen virtual row', async ({ page, request }) => {
    test.setTimeout(120000)
    const actor = seedPrimarySession('virtual_actor')
    const peer = seedPrimarySession('virtual_peer')
    const suffix = Date.now()
    const targetContent = `PW VIRTUAL SEARCH TARGET ${suffix}`

    await waitForBackendReady(request)

    for (let index = 0; index < 95; index += 1) {
      await sendDirectTextMessage(request, peer, actor.userId, `PW VIRTUAL OLDER FILL ${suffix} #${index}`)
    }
    await sendDirectTextMessage(request, peer, actor.userId, targetContent)
    for (let index = 0; index < 95; index += 1) {
      await sendDirectTextMessage(request, peer, actor.userId, `PW VIRTUAL NEWER FILL ${suffix} #${index}`)
    }

    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId, peer.accountName)

    await expect(page.locator('.virtual-timeline')).toBeVisible({ timeout: 45000 })

    const renderedBubbleCount = await page.locator('.message-bubble').count()
    expect(renderedBubbleCount).toBeLessThan(80)

    await openDirectHeaderSearch(page)
    await page.locator('#search-input').fill(targetContent)

    const targetBubble = page.locator('.message-bubble').filter({ hasText: targetContent }).first()
    await expect(targetBubble).toHaveClass(/highlight-message/, { timeout: 45000 })
    await expect(page.locator('.virtual-timeline')).toBeVisible()
  })
})
