/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface AuthTokens {
  access_token: string
  refresh_token: string
}

interface SeededUser {
  userId: number
  accountName: string
  mobileNumber: string
  fullName: string
}

interface TradingSettingsPayload {
  invitation_expiry_days: number
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

function seedActiveUser(label: string): SeededUser {
  return runPythonInApp<SeededUser>(`
import asyncio
import json
import uuid

from core.db import AsyncSessionLocal
from core.enums import UserRole
from models.user import User

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
            address='Playwright Admin Smoke',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()
        await db.commit()

    print(json.dumps({
        'userId': user.id,
        'accountName': account_name,
        'mobileNumber': mobile_number,
        'fullName': account_name,
    }))

asyncio.run(main())
`)
}

async function fetchDevLoginTokens(request: APIRequestContext): Promise<AuthTokens> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/auth/dev-login`, {
    headers: { 'Content-Type': 'application/json' },
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<AuthTokens>
}

function authHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
}

async function setAuthTokens(page: Page, tokens: AuthTokens) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
  })
}

async function openAdmin(page: Page) {
  await page.goto('/admin')
  await expect(page.getByRole('heading', { name: 'پنل مدیریت' })).toBeVisible()
}

async function fetchTradingSettings(request: APIRequestContext, accessToken: string): Promise<TradingSettingsPayload> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/trading-settings/`, {
    headers: authHeaders(accessToken),
  })
  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<TradingSettingsPayload>
}

test.describe('Admin smoke regressions', () => {
  test('admin can create an invitation link from the management panel', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    const suffix = Date.now()

    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page.getByRole('button', { name: /ارسال لینک دعوت/ }).click()
    await expect(page.getByRole('heading', { name: 'ارسال لینک دعوت جدید' })).toBeVisible()

    await page.locator('#account_name').fill(`pw_invite_${suffix}`)
    await page.locator('#mobile_number').fill(`09${String(suffix).slice(-9)}`)
    await page.locator('#role').selectOption('عادی')
    await page.getByRole('button', { name: /^ارسال لینک دعوت$/ }).click()

    await expect(page.locator('.success-box .result-message')).toContainText('✅ لینک دعوت با موفقیت ایجاد شد')
    await expect(page.locator('.success-box input[readonly]').first()).toHaveValue(/.+/)
  })

  test('admin user search finds a seeded user and opens the profile view', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    const seededUser = seedActiveUser('admin_search')

    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page.getByRole('button', { name: /مدیریت کاربران/ }).click()
    await page.getByRole('button', { name: '🔍 جستجوی کاربر' }).click()
    await page.getByPlaceholder('نام، نام کاربری یا موبایل...').fill(seededUser.accountName)
    await page.getByRole('button', { name: /^جستجو$/ }).click()

    const userRow = page.locator('.user-item').filter({ hasText: seededUser.accountName })
    await expect(userRow).toBeVisible()
    await expect(userRow).toContainText(seededUser.mobileNumber)

    await userRow.click()
    await expect(page.getByRole('heading', { name: 'پروفایل کاربر' })).toBeVisible()
    await expect(page.getByText(seededUser.accountName)).toBeVisible()
  })

  test('admin can save trading settings from the system settings panel', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    const currentSettings = await fetchTradingSettings(request, tokens.access_token)

    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page.getByRole('button', { name: /تنظیمات سیستم/ }).click()
    await page.getByText('📨 دعوت‌نامه').click()

    const invitationExpiryInput = page.locator('input[placeholder="2"]').first()
    await invitationExpiryInput.fill(String(currentSettings.invitation_expiry_days))
    await page.getByRole('button', { name: /ذخیره تنظیمات/ }).click()

    await expect(page.getByText('تنظیمات با موفقیت ذخیره شد')).toBeVisible()
  })

  test('admin can create an optional channel and invite a seeded active member', async ({ page, request }) => {
    const tokens = await fetchDevLoginTokens(request)
    const seededCandidate = seedActiveUser('admin_channel')
    const suffix = Date.now()
    const channelTitle = `Playwright Channel ${suffix}`

    await setAuthTokens(page, tokens)
    await openAdmin(page)

    await page.getByRole('button', { name: /ساخت کانال اختیاری/ }).click()
    await expect(page.getByRole('heading', { name: 'ساخت کانال اختیاری' })).toBeVisible()

    await page.locator('#channel-title').fill(channelTitle)
    await page.locator('#channel-description').fill('Playwright admin smoke channel')
    await page.getByRole('button', { name: 'ساخت کانال و ادامه' }).click()

    await expect(page.getByText('✅ کانال ساخته شد. حالا اعضای اولیه را انتخاب کنید.')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'اعضای فعلی کانال' })).toBeVisible()

    await page.getByPlaceholder('جستجو با نام، اکانت یا موبایل...').fill(seededCandidate.accountName)
    const candidateRow = page.locator('.candidate-row').filter({ hasText: seededCandidate.accountName })
    await expect(candidateRow).toBeVisible()
    await candidateRow.click()
    await page.getByRole('button', { name: 'ثبت اعضای انتخاب‌شده' }).click()

    await expect(page.getByText(/✅ اعضا با موفقیت افزوده شدند\./)).toBeVisible()
    await expect(page.locator('.member-row').filter({ hasText: seededCandidate.accountName })).toBeVisible()
  })
})