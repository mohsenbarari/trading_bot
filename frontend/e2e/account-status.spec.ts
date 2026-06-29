/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface AuthTokens {
  access_token: string
  refresh_token: string
}

interface SessionUser {
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
    throw new Error('No JSON output returned from trading_bot_app account-status helper')
  }

  return JSON.parse(lastLine) as T
}

async function fetchDevLoginTokens(request: APIRequestContext): Promise<AuthTokens> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/auth/dev-login`, {
    headers: { 'Content-Type': 'application/json' },
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<AuthTokens>
}

function seedSessionUser(label: string): SessionUser {
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

async def main():
    suffix = uuid.uuid4().hex[:10]
    account_name = f"pw_account_status_{label}_{suffix}"
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000

    async with AsyncSessionLocal() as db:
        user = User(
            account_name=account_name,
            mobile_number=f"09{mobile_seed:09d}",
            full_name=account_name,
            address='Playwright Account Status',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Account Status Device',
            device_ip='127.0.0.1',
            platform=Platform.WEB,
            refresh_token_hash=hash_token(refresh_token),
            is_primary=True,
            is_active=True,
            expires_at=None,
            home_server=user.home_server,
        )
        db.add(session)
        await db.flush()

        access_token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=60),
            session_id=str(session.id),
            server_id=user.home_server,
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

function forceGlobalLock(userId: number) {
  return runPythonInApp<{ blocked: number }>(`
import asyncio
import json
from datetime import timedelta

from core.db import AsyncSessionLocal
from core.services.user_account_status_service import mark_due_users_globally_locked
from core.utils import utc_now
from models.user import User

user_id = ${JSON.stringify(userId)}

async def main():
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise RuntimeError('User not found')

        now = utc_now().replace(tzinfo=None)
        user.messenger_grace_expires_at = now - timedelta(minutes=5)
        user.messenger_blocked_at = None
        await db.commit()

        blocked = await mark_due_users_globally_locked(db, limit=10)
        await db.commit()

    print(json.dumps({'blocked': blocked}))

asyncio.run(main())
`)
}

function mintFreshSession(userId: number) {
  return runPythonInApp<AuthTokens>(`
import asyncio
import json
from datetime import timedelta

from core.db import AsyncSessionLocal
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.session import Platform, UserSession
from models.user import User

user_id = ${JSON.stringify(userId)}

async def main():
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise RuntimeError('User not found')

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Reactivated Device',
            device_ip='127.0.0.1',
            platform=Platform.WEB,
            refresh_token_hash=hash_token(refresh_token),
            is_primary=False,
            is_active=True,
            expires_at=None,
            home_server=user.home_server,
        )
        db.add(session)
        await db.flush()

        access_token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=60),
            session_id=str(session.id),
            server_id=user.home_server,
        )

        await db.commit()

    print(json.dumps({
        'access_token': access_token,
        'refresh_token': refresh_token,
    }))

asyncio.run(main())
`)
}

async function setAuthTokens(page: Page, tokens: AuthTokens) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
    localStorage.removeItem('current_user_summary')
  }, {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
  })
}

async function openAdminUserProfile(page: Page, user: SessionUser) {
  await page.goto(`/admin/users/${user.userId}`)
  await expect(page.getByRole('heading', { name: 'پروفایل کاربر' })).toBeVisible({ timeout: 30000 })
  await expect(page.getByText(user.accountName)).toBeVisible({ timeout: 30000 })
}

async function toggleAccountStatusFromAdminProfile(page: any) {
  const settingsHeading = page.getByRole('heading', { name: 'مدیریت کاربر' })
  if (!(await settingsHeading.isVisible())) {
    await page.getByRole('button', { name: /تنظیمات کاربر/ }).click()
    await expect(settingsHeading).toBeVisible()
  }
  const playwrightPage = page as any
  const dialogHandler: any = async (dialog: any) => {
    await dialog.accept()
  }
  // @ts-expect-error Workspace diagnostics misresolve Playwright's dialog event overload here.
  playwrightPage.on('dialog', dialogHandler)
  const updateResponsePromise = page.waitForResponse((response) => {
    return response.request().method() === 'PUT' && /\/api\/users\/\d+$/.test(response.url())
  })
  try {
    await page.getByRole('button', { name: /تغییر وضعیت حساب/ }).click()
    const response = await updateResponsePromise
    expect(response.ok()).toBeTruthy()
  } finally {
    // @ts-expect-error Workspace diagnostics misresolve Playwright's dialog event overload here.
    playwrightPage.off('dialog', dialogHandler)
  }
}

async function createIsolatedPage(browser: any, tokens: AuthTokens) {
  const context = await browser.newContext()
  const page = await context.newPage()
  await setAuthTokens(page, tokens)
  return { context, page }
}

async function readCurrentUserPayload(page: Page) {
  return page.evaluate(async () => {
    const token = localStorage.getItem('auth_token')
    const response = await fetch('/api/auth/me', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    const rawText = await response.text()
    let body: unknown = null
    try {
      body = rawText ? JSON.parse(rawText) : null
    } catch {
      body = rawText
    }
    return {
      status: response.status,
      body,
    }
  })
}

test.describe('Account status browser regression', () => {
  test('admin UI deactivation drives inactive UX, global-lock login revocation, and reactivation recovery', async ({ browser, request }) => {
    test.setTimeout(90000)

    const adminTokens = await fetchDevLoginTokens(request)
    const targetUser = seedSessionUser('target')

    let adminContext: BrowserContext | null = null
    let userContext: BrowserContext | null = null

    try {
      const adminSession = await createIsolatedPage(browser, adminTokens)
      adminContext = adminSession.context
      const adminPage = adminSession.page

      const userSession = await createIsolatedPage(browser, {
        access_token: targetUser.accessToken,
        refresh_token: targetUser.refreshToken,
      })
      userContext = userSession.context
      const userPage = userSession.page

      await userPage.goto('/')
      await expect(userPage.getByRole('button', { name: /ورود به بازار/ })).toBeVisible()
      await expect(userPage.getByText('حساب کاربری غیرفعال شده است')).toHaveCount(0)

      await openAdminUserProfile(adminPage, targetUser)
      await toggleAccountStatusFromAdminProfile(adminPage)

      await expect.poll(async () => {
        const payload = await readCurrentUserPayload(userPage)
        if (payload.status !== 200) {
          return `${payload.status}:${String(payload.body)}`
        }
        return (payload.body as { account_status?: string | null })?.account_status
      }).toBe('inactive')

      await userPage.reload({ waitUntil: 'domcontentloaded' })
      const blockedAlert = userPage.locator('.alert-blocked')
      await expect(blockedAlert).toContainText('حساب کاربری غیرفعال شده است')
      await expect(blockedAlert).toContainText(/اگر حساب تا/)
      await expect(userPage.locator('.hero-btn')).toBeDisabled()

      const lockResult = forceGlobalLock(targetUser.userId)
      expect(lockResult.blocked).toBeGreaterThanOrEqual(1)

      await userPage.reload({ waitUntil: 'domcontentloaded' })
      await userPage.waitForURL(/\/login$/)
      await expect(userPage.getByRole('heading', { name: 'ورود به بازار' })).toBeVisible()

      await toggleAccountStatusFromAdminProfile(adminPage)

      const reactivatedTokens = mintFreshSession(targetUser.userId)
      await setAuthTokens(userPage, reactivatedTokens)
      await userPage.goto('/')

      await expect(userPage.getByText('حساب کاربری غیرفعال شده است')).toHaveCount(0)
      await userPage.getByRole('button', { name: /ورود به بازار/ }).click()
      await userPage.waitForURL(/\/market$/)
    } finally {
      await adminContext?.close()
      await userContext?.close()
    }
  })
})
