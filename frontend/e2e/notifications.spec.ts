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
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, fixture)
  await page.goto('/')
  await expect(page.getByText(fixture.accountName)).toBeVisible()
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
    await expect(page.getByText(content)).toBeVisible()

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
})