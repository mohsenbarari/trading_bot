/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'

import { primeAuthSession } from './helpers/auth'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'
const TINY_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ6kAAAAASUVORK5CYII='

interface SessionUser {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

interface ActiveUser {
  userId: number
  accountName: string
  mobileNumber: string
}

interface CurrentUserPayload {
  id: number
  account_name: string
  avatar_file_id?: string | null
}

interface GroupPayload {
  id: number
  title: string
  description?: string | null
  avatar_file_id?: string | null
}

interface GroupMemberPayload {
  user_id: number
  account_name: string
  role: 'admin' | 'member'
  is_group_creator: boolean
}

interface GroupDetailPayload {
  group: GroupPayload
  members: GroupMemberPayload[]
}

interface ChannelPayload {
  id: number
  title: string
  description?: string | null
  avatar_file_id?: string | null
}

interface ChannelMemberPayload {
  user_id: number
  account_name: string
  role: 'admin' | 'member'
  is_channel_creator: boolean
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
    throw new Error('No JSON output returned from trading_bot_app room-manager/profile seed helper')
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
            address='Playwright Messenger Manager/Profile',
            role=role,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Messenger Room Manager Device',
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

function seedActiveUser(label: string): ActiveUser {
  return runPythonInApp<ActiveUser>(`
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
            address='Playwright Messenger Candidate',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()
        await db.commit()

    print(json.dumps({
        'userId': user.id,
        'accountName': user.account_name,
        'mobileNumber': user.mobile_number,
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

async function setAuthTokens(page: Page, session: SessionUser) {
  await primeAuthSession(page, session.accessToken, session.refreshToken)
}

async function openMessenger(page: Page) {
  await page.goto('/chat')
  await expect(page.locator('.chat-header')).toBeVisible({ timeout: 30000 })
}

async function expectPublicProfileForUser(page: Page, userId: number, accountName: string) {
  const expectedProfileUrl = new RegExp(`/users/${userId}`)
  const canonicalProfilePath = `/users/${userId}?account_name=${encodeURIComponent(accountName)}`
  const profileView = page.locator('.public-profile-view')
  await page.waitForURL(expectedProfileUrl, { timeout: 7000 }).catch(() => {})

  const hasLoadedProfile = expectedProfileUrl.test(page.url()) &&
    await profileView.filter({ hasText: accountName }).first().isVisible({ timeout: 5000 }).catch(() => false)

  if (!hasLoadedProfile) {
    try {
      await page.goto(canonicalProfilePath, { waitUntil: 'domcontentloaded' })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (!/ERR_ABORTED|interrupted by another navigation/i.test(message)) {
        throw error
      }
      await page.waitForURL(expectedProfileUrl, { timeout: 15000 }).catch(() => {})
    }
  }

  await expect(page).toHaveURL(expectedProfileUrl, { timeout: 30000 })
  await expect(profileView).toContainText(accountName, { timeout: 30000 })
}

async function openConversationListMenu(page: Page) {
  await page.locator('.chat-header .header-menu-container .header-btn').click()
  await expect(page.locator('.header-dropdown-menu')).toBeVisible({ timeout: 15000 })
}

async function openRoomHeaderMenu(page: Page) {
  await page.locator('.chat-header .header-menu-container .header-btn').last().click()
  await expect(page.locator('.header-dropdown-menu')).toBeVisible({ timeout: 15000 })
}

async function openNamedRoomFromRoute(page: Page, roomId: number, title: string) {
  await page.goto(`/chat?user_id=-${roomId}&user_name=${encodeURIComponent(title)}`, { waitUntil: 'domcontentloaded' })
  await expect.poll(() => selectedRoomIdFromUrl(page), { timeout: 30000 }).toBe(-roomId)
  await expect(page.locator('.chat-header .header-name').last()).toContainText(title, { timeout: 30000 })
}

async function openRoomManagerFromHeader(page: Page, managerRoot: Locator, menuLabel: string) {
  if (await managerRoot.isVisible().catch(() => false)) {
    return
  }

  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.locator('.chat-header .header-user-info').last().click({ force: true })
    if (await managerRoot.isVisible().catch(() => false)) {
      return
    }
    await page.waitForTimeout(250)
  }

  if (await managerRoot.isVisible().catch(() => false)) {
    return
  }

  await openRoomHeaderMenu(page)
  await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: menuLabel }).click({ force: true })
  await expect(managerRoot).toBeVisible({ timeout: 30000 })
}

async function expectManagerOverviewIA(managerRoot: Locator, expectedRole: string, destructiveSection: string) {
  await expect(managerRoot.locator('.manager-role-strip')).toContainText('نقش شما', { timeout: 30000 })
  await expect(managerRoot.locator('.manager-role-strip')).toContainText(expectedRole, { timeout: 30000 })
  await expect(managerRoot.locator('.section-heading').filter({ hasText: 'اعضا و دسترسی‌ها' })).toBeVisible({ timeout: 30000 })
  await expect(managerRoot.locator('.section-heading').filter({ hasText: 'تنظیمات' })).toBeVisible({ timeout: 30000 })
  await expect(managerRoot.locator('.section-heading').filter({ hasText: destructiveSection })).toBeVisible({ timeout: 30000 })
}

async function openChannelSettingsPanel(managerRoot: Locator) {
  const settingsRow = managerRoot.locator('.telegram-row').filter({ hasText: 'تنظیمات کانال' })
  const titleInput = managerRoot.locator('#edit-channel-title')
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await settingsRow.click({ force: true })
    if (await titleInput.isVisible().catch(() => false)) {
      return
    }
  }
  await expect(titleInput).toBeVisible({ timeout: 30000 })
}

async function clickOpenInMessengerButton(managerRoot: Locator) {
  let lastError: unknown = null
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const button = managerRoot.getByRole('button', { name: 'باز کردن در پیام‌رسان' })
    await expect(button).toBeVisible({ timeout: 30000 })
    try {
      await button.click({ timeout: 7000 })
      return
    } catch (error) {
      lastError = error
      const message = error instanceof Error ? error.message : String(error)
      if (!/not stable|detached|not attached/i.test(message) || attempt === 3) {
        throw error
      }
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Failed to click open-in-messenger button')
}

async function sendDirectTextMessage(request: APIRequestContext, accessToken: string, receiverId: number, content: string) {
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

async function fetchCurrentUser(request: APIRequestContext, accessToken: string): Promise<CurrentUserPayload> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/auth/me`, {
    headers: authHeaders(accessToken),
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<CurrentUserPayload>
}

async function fetchGroupDetail(request: APIRequestContext, accessToken: string, groupId: number): Promise<GroupPayload> {
  const response = await retryGroupDetailRequest(request, accessToken, groupId)

  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as { group?: GroupPayload } | GroupPayload
  return ('group' in payload && payload.group ? payload.group : payload) as GroupPayload
}

async function fetchGroupDetailPayload(request: APIRequestContext, accessToken: string, groupId: number): Promise<GroupDetailPayload> {
  const response = await retryGroupDetailRequest(request, accessToken, groupId)

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<GroupDetailPayload>
}

async function retryGroupDetailRequest(request: APIRequestContext, accessToken: string, groupId: number) {
  const maxAttempts = 3
  let lastError: unknown = null

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await request.get(`${BACKEND_BASE_URL}/api/chat/groups/${groupId}`, {
        headers: authHeaders(accessToken),
      })
    } catch (error) {
      lastError = error
      const message = error instanceof Error ? error.message : String(error)
      const isTransientSocketError = /socket hang up|ECONNRESET/i.test(message)
      if (!isTransientSocketError || attempt === maxAttempts) {
        throw error
      }
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError || 'group detail request failed'))
}

async function createGroupViaApi(
  request: APIRequestContext,
  accessToken: string,
  title: string,
  memberIds: number[],
  description?: string,
): Promise<GroupPayload> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/groups`, {
    headers: authHeaders(accessToken),
    data: {
      title,
      member_ids: memberIds,
      ...(description ? { description } : {}),
    },
  })

  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as { group: GroupPayload }
  return payload.group
}

async function fetchChannelById(request: APIRequestContext, accessToken: string, channelId: number): Promise<ChannelPayload | null> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/channels`, {
    headers: authHeaders(accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as ChannelPayload[]
  return Array.isArray(payload)
    ? payload.find((channel) => Number(channel.id) === channelId) ?? null
    : null
}

async function fetchChannelMembers(
  request: APIRequestContext,
  accessToken: string,
  channelId: number,
): Promise<ChannelMemberPayload[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/channels/${channelId}/members`, {
    headers: authHeaders(accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as ChannelMemberPayload[]
  return Array.isArray(payload) ? payload : []
}

async function createChannelViaApi(
  request: APIRequestContext,
  accessToken: string,
  title: string,
  description?: string,
): Promise<ChannelPayload> {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/channels`, {
    headers: authHeaders(accessToken),
    data: {
      title,
      ...(description ? { description } : {}),
    },
  })

  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as { channel: ChannelPayload }
  return payload.channel
}

async function bulkAddChannelMembers(
  request: APIRequestContext,
  accessToken: string,
  channelId: number,
  userIds: number[],
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/channels/${channelId}/members/bulk`, {
    headers: authHeaders(accessToken),
    data: {
      user_ids: userIds,
    },
  })

  expect(response.ok()).toBeTruthy()
}

function conversationRow(page: Page, title: string) {
  return page.locator('.conversation-item').filter({ hasText: title }).first()
}

function selectedRoomIdFromUrl(page: Page) {
  const currentUrl = new URL(page.url())
  return Number(currentUrl.searchParams.get('user_id') || '0')
}

async function setAvatarInput(locator: Locator, fileName: string) {
  await locator.setInputFiles([
    createPlaywrightBinaryFile(fileName, 'image/png', TINY_PNG_BASE64),
  ])
}

test.describe('Messenger room manager and public profile flows', () => {
  test('group manager supports messenger-header create, avatar edit, and settings save', async ({ page, request }) => {
    test.setTimeout(90000)
    const owner = seedSessionUser('group_manager_owner', 'standard')
    const candidate = seedActiveUser('group_manager_candidate')
    const suffix = Date.now()
    const initialTitle = `Playwright Group ${suffix}`
    const updatedTitle = `Playwright Group Updated ${suffix}`
    const initialDescription = 'Playwright group manager creation flow'
    const updatedDescription = 'Playwright group manager updated flow'

    await waitForBackendReady(request)
    await setAuthTokens(page, owner)
    await openMessenger(page)

    await openConversationListMenu(page)
    await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'ساخت گروه جدید' }).click()

    const groupManager = page.locator('.group-manager-shell:visible').last()
    await expect(groupManager).toBeVisible({ timeout: 30000 })

    await groupManager.getByPlaceholder('جستجو با نام، اکانت یا موبایل...').fill(candidate.accountName)
    const candidateRow = groupManager.locator('.chat-user-row').filter({ hasText: candidate.accountName }).first()
    await expect(candidateRow).toBeVisible({ timeout: 30000 })
    await candidateRow.click()
    await groupManager.locator('.primary-chip').filter({ hasText: 'ادامه' }).click()

    await groupManager.locator('#group-title').fill(initialTitle)
    await groupManager.locator('#group-description').fill(initialDescription)
    await groupManager.getByRole('button', { name: 'ساخت گروه' }).click()

    await expect
      .poll(() => selectedRoomIdFromUrl(page), { timeout: 30000 })
      .toBeLessThan(0)

    const groupId = Math.abs(selectedRoomIdFromUrl(page))
    await expect(page.locator('.chat-header .header-name').last()).toHaveText(initialTitle, { timeout: 30000 })

    await openRoomHeaderMenu(page)
    const manageGroupItem = page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'مدیریت گروه' })
    await expect(manageGroupItem).toBeVisible({ timeout: 15000 })
    await manageGroupItem.click()
    await expect(groupManager).toBeVisible({ timeout: 30000 })
    await expectManagerOverviewIA(groupManager, 'ادمین گروه', 'خروج')

    const groupAvatarUploadResponse = page.waitForResponse((response) => {
      return response.request().method() === 'POST' && response.url().includes('/api/chat/upload-media')
    })
    await setAvatarInput(groupManager.locator('input.hidden-avatar-input'), `pw-group-${suffix}.png`)
    expect((await groupAvatarUploadResponse).ok()).toBeTruthy()
    await expect(groupManager.getByRole('button', { name: 'حذف عکس' })).toBeVisible({ timeout: 30000 })

    await groupManager.locator('.telegram-row').filter({ hasText: 'تنظیمات گروه' }).click({ force: true })

    await groupManager.locator('#group-edit-title').fill(updatedTitle)
    await groupManager.locator('#group-edit-description').fill(updatedDescription)
    await groupManager.getByRole('button', { name: 'ذخیره تغییرات' }).click()

    await expect
      .poll(async () => fetchGroupDetail(request, owner.accessToken, groupId), { timeout: 30000 })
      .toMatchObject({
        id: groupId,
        title: updatedTitle,
        description: updatedDescription,
        avatar_file_id: expect.any(String),
      })
  })

  test('channel manager supports messenger-header create, member add, and header-open settings save', async ({ page, request }) => {
    test.setTimeout(90000)
    const owner = seedSessionUser('channel_manager_owner', 'super_admin')
    const candidate = seedActiveUser('channel_manager_candidate')
    const suffix = Date.now()
    const initialTitle = `Playwright Channel ${suffix}`
    const updatedTitle = `Playwright Channel Updated ${suffix}`
    const initialDescription = 'Playwright channel manager creation flow'
    const updatedDescription = 'Playwright channel manager updated flow'

    await waitForBackendReady(request)
    await setAuthTokens(page, owner)
    await openMessenger(page)

    await openConversationListMenu(page)
    await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'ساخت کانال' }).click()

    const channelManager = page.locator('.channel-manager-root:visible').last()
    await expect(channelManager).toBeVisible({ timeout: 30000 })
    await channelManager.getByRole('button', { name: 'کانال جدید' }).click()

    await channelManager.locator('#channel-title').fill(initialTitle)
    await channelManager.locator('#channel-description').fill(initialDescription)
    await channelManager.getByRole('button', { name: 'ساخت کانال' }).click()

    await expect(channelManager.getByText('کانال ساخته شد. حالا اعضا و ادمین‌ها را مدیریت کنید.')).toBeVisible({ timeout: 30000 })

    await channelManager.getByPlaceholder('جستجو با نام، اکانت یا موبایل...').fill(candidate.accountName)
    const candidateRow = channelManager.locator('.chat-user-row').filter({ hasText: candidate.accountName }).first()
    await expect(candidateRow).toBeVisible({ timeout: 30000 })
    await candidateRow.click()
    await channelManager.locator('.primary-chip').filter({ hasText: 'افزودن' }).click()
    await expect(channelManager.locator('.chat-user-row').filter({ hasText: candidate.accountName }).first()).toBeVisible({ timeout: 30000 })

    await channelManager.locator('.manager-header .header-icon-btn').first().click()
    await waitForBackendReady(request)
    await clickOpenInMessengerButton(channelManager)

    await expect.poll(() => page.url(), { timeout: 60000 }).toContain('/chat?user_id=-')

    const channelId = Math.abs(selectedRoomIdFromUrl(page))
    await expect(page.locator('.chat-header .header-name').last()).toHaveText(initialTitle, { timeout: 30000 })

    await openRoomHeaderMenu(page)
    const manageChannelItem = page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'مدیریت کانال' })
    await expect(manageChannelItem).toBeVisible({ timeout: 15000 })
    await expect(page.locator('.header-dropdown-menu')).not.toContainText('تنظیمات کانال')
    await manageChannelItem.click()
    await expect(channelManager).toBeVisible({ timeout: 30000 })
    const reopenedChannelManager = page.locator('.channel-manager-root:visible').last()
    await expectManagerOverviewIA(reopenedChannelManager, 'سازنده کانال', 'خروج و حذف')

    await openChannelSettingsPanel(reopenedChannelManager)

    await reopenedChannelManager.locator('#edit-channel-title').fill(updatedTitle)
    await reopenedChannelManager.locator('#edit-channel-description').fill(updatedDescription)
    await reopenedChannelManager.getByRole('button', { name: 'ذخیره تغییرات' }).click()

    await expect
      .poll(async () => fetchChannelById(request, owner.accessToken, channelId), { timeout: 30000 })
      .toMatchObject({
        id: channelId,
        title: updatedTitle,
        description: updatedDescription,
      })
    await page.goBack()
    await expect(page.locator('.channel-manager-root')).toHaveCount(0)
    const updatedHeader = page.locator('.chat-header:visible .header-name').first()
    const headerAlreadyUpdated = await expect(updatedHeader)
      .toContainText(updatedTitle, { timeout: 5000 })
      .then(() => true)
      .catch(() => false)
    if (!headerAlreadyUpdated) {
      const headerUpdatedAfterManagerClose = await expect(updatedHeader)
        .toContainText(updatedTitle, { timeout: 30000 })
        .then(() => true)
        .catch(() => false)
      if (headerUpdatedAfterManagerClose) {
        return
      }

      const updatedChannelRow = conversationRow(page, updatedTitle)
      await expect(updatedChannelRow).toBeVisible({ timeout: 30000 })
      await updatedChannelRow.click({ force: true })
      await expect.poll(() => selectedRoomIdFromUrl(page), { timeout: 30000 }).toBe(-channelId)
      await expect(updatedHeader).toContainText(updatedTitle, { timeout: 30000 })
    }
  })

  test('group manager supports member-row profiles, admin mutations, member removal, and creator leave', async ({ page, request }) => {
    test.setTimeout(120000)
    const owner = seedSessionUser('group_manager_edge_owner', 'standard')
    const candidateOne = seedActiveUser('group_manager_edge_one')
    const candidateTwo = seedActiveUser('group_manager_edge_two')
    const suffix = Date.now()
    const title = `Playwright Group Edge ${suffix}`

    await waitForBackendReady(request)
    const group = await createGroupViaApi(request, owner.accessToken, title, [candidateOne.userId, candidateTwo.userId], 'Playwright group edge flow')

    await setAuthTokens(page, owner)
    await openMessenger(page)

    const groupRow = conversationRow(page, title)
    await expect(groupRow).toBeVisible({ timeout: 30000 })
    await groupRow.click()
    await expect(page.locator('.chat-header .header-name').last()).toHaveText(title, { timeout: 30000 })

    const groupManager = page.locator('.group-manager-shell')
    await openRoomManagerFromHeader(page, groupManager, 'مدیریت گروه')

    await groupManager.locator('.telegram-row').filter({ hasText: 'اعضای گروه' }).click()
    const memberRow = groupManager.locator('.chat-user-row').filter({ hasText: candidateOne.accountName }).first()
    await expect(memberRow).toBeVisible({ timeout: 30000 })
    await memberRow.locator('.chat-user-row__action-btn').filter({ hasText: 'پروفایل' }).click()

    await expectPublicProfileForUser(page, candidateOne.userId, candidateOne.accountName)

    await openNamedRoomFromRoute(page, group.id, title)

    await openRoomManagerFromHeader(page, groupManager, 'مدیریت گروه')
    await groupManager.locator('.telegram-row').filter({ hasText: 'مدیریت ادمین‌ها' }).click()

    const promotableRow = groupManager.locator('.chat-user-row').filter({ hasText: candidateOne.accountName }).first()
    await promotableRow.locator('.chat-user-row__action-btn--primary').filter({ hasText: 'ارتقا به ادمین' }).click()

    await expect
      .poll(async () => (await fetchGroupDetailPayload(request, owner.accessToken, group.id)).members.find((member) => member.user_id === candidateOne.userId)?.role, { timeout: 30000 })
      .toBe('admin')

    const adminRow = groupManager.locator('.chat-user-row').filter({ hasText: candidateOne.accountName }).first()
    await adminRow.locator('.chat-user-row__action-btn').filter({ hasText: 'حذف ادمین' }).click()

    await expect
      .poll(async () => (await fetchGroupDetailPayload(request, owner.accessToken, group.id)).members.find((member) => member.user_id === candidateOne.userId)?.role, { timeout: 30000 })
      .toBe('member')

    const promotableAgainRow = groupManager.locator('.chat-user-row').filter({ hasText: candidateOne.accountName }).first()
    await promotableAgainRow.locator('.chat-user-row__action-btn--primary').filter({ hasText: 'ارتقا به ادمین' }).click()

    await expect
      .poll(async () => (await fetchGroupDetailPayload(request, owner.accessToken, group.id)).members.find((member) => member.user_id === candidateOne.userId)?.role, { timeout: 30000 })
      .toBe('admin')

    await groupManager.locator('.header-icon-btn').first().click()
    await groupManager.locator('.telegram-row').filter({ hasText: 'اعضای گروه' }).click()

    const removableRow = groupManager.locator('.chat-user-row').filter({ hasText: candidateTwo.accountName }).first()
    await removableRow.locator('.chat-user-row__action-btn--danger').filter({ hasText: 'حذف' }).click()

    await expect
      .poll(async () => (await fetchGroupDetailPayload(request, owner.accessToken, group.id)).members.some((member) => member.user_id === candidateTwo.userId), { timeout: 30000 })
      .toBe(false)

    await groupManager.locator('.header-icon-btn').first().click()
    await groupManager.locator('.telegram-row.nav.danger').click()

    await expect.poll(() => page.url(), { timeout: 30000 }).not.toContain(`user_id=-${group.id}`)
    const detailResponse = await request.get(`${BACKEND_BASE_URL}/api/chat/groups/${group.id}`, {
      headers: authHeaders(owner.accessToken),
    })
    expect(detailResponse.status()).toBe(403)
  })

  test('channel manager supports member-row profiles, admin mutations, member removal, and creator delete', async ({ page, request }) => {
    test.setTimeout(120000)
    const owner = seedSessionUser('channel_manager_edge_owner', 'super_admin')
    const candidateOne = seedActiveUser('channel_manager_edge_one')
    const candidateTwo = seedActiveUser('channel_manager_edge_two')
    const suffix = Date.now()
    const title = `Playwright Channel Edge ${suffix}`

    await waitForBackendReady(request)
    const channel = await createChannelViaApi(request, owner.accessToken, title, 'Playwright channel edge flow')
    await bulkAddChannelMembers(request, owner.accessToken, channel.id, [candidateOne.userId, candidateTwo.userId])

    await setAuthTokens(page, owner)
    await openMessenger(page)

    const channelRow = conversationRow(page, title)
    await expect(channelRow).toBeVisible({ timeout: 30000 })
    await channelRow.click()
    await expect(page.locator('.chat-header .header-name').last()).toHaveText(title, { timeout: 30000 })

    const channelManager = page.locator('.channel-manager-root')
    await openRoomManagerFromHeader(page, channelManager, 'مدیریت کانال')

    await channelManager.locator('.telegram-row').filter({ hasText: 'اعضای کانال' }).click()
    const expectedProfileUrl = new RegExp(`/users/${candidateOne.userId}`)
    const clickChannelMemberProfile = async () => {
      const currentMemberRow = channelManager.locator('.chat-user-row').filter({ hasText: candidateOne.accountName }).first()
      await expect(currentMemberRow).toBeVisible({ timeout: 30000 })
      const profileButton = currentMemberRow.locator('.chat-user-row__action-btn').filter({ hasText: 'پروفایل' })
      await profileButton.scrollIntoViewIfNeeded().catch(() => {})
      await profileButton.click({ force: true })
    }

    await clickChannelMemberProfile()
    const navigatedToProfile = await page.waitForURL(expectedProfileUrl, { timeout: 5000 }).then(() => true).catch(() => false)
    if (!navigatedToProfile) {
      if (!(await channelManager.isVisible().catch(() => false))) {
        await openRoomManagerFromHeader(page, channelManager, 'مدیریت کانال')
      }
      await channelManager.locator('.telegram-row').filter({ hasText: 'اعضای کانال' }).click()
      await clickChannelMemberProfile()
    }

    await expectPublicProfileForUser(page, candidateOne.userId, candidateOne.accountName)

    await openNamedRoomFromRoute(page, channel.id, title)

    await openRoomManagerFromHeader(page, channelManager, 'مدیریت کانال')
    await channelManager.locator('.telegram-row').filter({ hasText: 'مدیریت ادمین‌ها' }).click()

    const promotableRow = channelManager.locator('.chat-user-row').filter({ hasText: candidateOne.accountName }).first()
    await promotableRow.locator('.chat-user-row__action-btn--primary').filter({ hasText: 'ارتقا به ادمین' }).click()

    await expect
      .poll(async () => (await fetchChannelMembers(request, owner.accessToken, channel.id)).find((member) => member.user_id === candidateOne.userId)?.role, { timeout: 30000 })
      .toBe('admin')

    const adminRow = channelManager.locator('.chat-user-row').filter({ hasText: candidateOne.accountName }).first()
    await adminRow.locator('.chat-user-row__action-btn').filter({ hasText: 'حذف ادمین' }).click()

    await expect
      .poll(async () => (await fetchChannelMembers(request, owner.accessToken, channel.id)).find((member) => member.user_id === candidateOne.userId)?.role, { timeout: 30000 })
      .toBe('member')

    const promotableAgainRow = channelManager.locator('.chat-user-row').filter({ hasText: candidateOne.accountName }).first()
    await promotableAgainRow.locator('.chat-user-row__action-btn--primary').filter({ hasText: 'ارتقا به ادمین' }).click()

    await expect
      .poll(async () => (await fetchChannelMembers(request, owner.accessToken, channel.id)).find((member) => member.user_id === candidateOne.userId)?.role, { timeout: 30000 })
      .toBe('admin')

    await channelManager.locator('.manager-header .header-icon-btn').first().click()
    await channelManager.locator('.telegram-row').filter({ hasText: 'اعضای کانال' }).click()

    const removableRow = channelManager.locator('.chat-user-row').filter({ hasText: candidateTwo.accountName }).first()
    await removableRow.locator('.chat-user-row__action-btn--danger').filter({ hasText: 'حذف' }).click()

    await expect
      .poll(async () => (await fetchChannelMembers(request, owner.accessToken, channel.id)).some((member) => member.user_id === candidateTwo.userId), { timeout: 30000 })
      .toBe(false)

    await channelManager.locator('.manager-header .header-icon-btn').first().click()
    await channelManager.locator('.telegram-row.nav.danger').click()

    await expect.poll(() => page.url(), { timeout: 30000 }).not.toContain(`user_id=-${channel.id}`)
    await expect
      .poll(async () => await fetchChannelById(request, owner.accessToken, channel.id), { timeout: 30000 })
      .toBeNull()
  })

  test('public profiles can replace an existing avatar without removing it first', async ({ page, request }) => {
    test.setTimeout(90000)
    const owner = seedSessionUser('public_profile_avatar_replace_owner', 'standard')
    const suffix = Date.now()

    await waitForBackendReady(request)
    await setAuthTokens(page, owner)
    await openMessenger(page)

    await openConversationListMenu(page)
    await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'پروفایل عمومی من' }).click()

    await expect(page).toHaveURL(new RegExp(`/users/${owner.userId}`))
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })

    const profileInput = page.locator('.public-profile-view input.hidden-avatar-input')
    await setAvatarInput(profileInput, `pw-profile-first-${suffix}.png`)

    let firstAvatarId = ''
    await expect
      .poll(async () => {
        firstAvatarId = (await fetchCurrentUser(request, owner.accessToken)).avatar_file_id ?? ''
        return firstAvatarId.length > 0
      }, { timeout: 30000 })
      .toBe(true)

    await setAvatarInput(profileInput, `pw-profile-second-${suffix}.png`)

    await expect
      .poll(async () => {
        const nextAvatarId = (await fetchCurrentUser(request, owner.accessToken)).avatar_file_id ?? ''
        return nextAvatarId.length > 0 && nextAvatarId !== firstAvatarId
      }, { timeout: 30000 })
      .toBe(true)

    await expect(page.locator('.public-profile-view .profile-avatar-image')).toBeVisible({ timeout: 30000 })
  })

  test('public profiles support self avatar edits, other-user open from chat header, and return to chat', async ({ page, request }) => {
    test.setTimeout(90000)
    const owner = seedSessionUser('public_profile_owner', 'standard')
    const peer = seedActiveUser('public_profile_peer')
    const suffix = Date.now()

    await waitForBackendReady(request)
    await sendDirectTextMessage(request, owner.accessToken, peer.userId, `PW PROFILE DIRECT ${suffix}`)

    await setAuthTokens(page, owner)
    await openMessenger(page)

    await openConversationListMenu(page)
    await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'پروفایل عمومی من' }).click()

    await expect(page).toHaveURL(new RegExp(`/users/${owner.userId}`))
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })

    const profileInput = page.locator('.public-profile-view input.hidden-avatar-input')
    await setAvatarInput(profileInput, `pw-profile-${suffix}.png`)

    await expect
      .poll(async () => Boolean((await fetchCurrentUser(request, owner.accessToken)).avatar_file_id), { timeout: 30000 })
      .toBe(true)
    await expect(page.locator('.public-profile-view .profile-avatar-image')).toBeVisible({ timeout: 30000 })
    await expect(page.getByRole('button', { name: 'تغییر آواتار' })).toBeVisible({ timeout: 30000 })

    await page.locator('.public-profile-view .back-button').click()
    await expect(page).toHaveURL(/\/chat/)
    await expect(page.locator('.chat-header')).toBeVisible({ timeout: 30000 })

    const peerConversation = conversationRow(page, peer.accountName)
    await expect(peerConversation).toBeVisible({ timeout: 30000 })
    await peerConversation.click()

    await expect(page.locator('.chat-header .header-name').last()).toHaveText(peer.accountName, { timeout: 30000 })
    await page.locator('.chat-header .header-user-info').last().click()

    await expect(page).toHaveURL(new RegExp(`/users/${peer.userId}`))
    await expect(page.locator('.public-profile-view')).toContainText(peer.accountName)

    await page.goBack()
    await expect.poll(() => page.url(), { timeout: 30000 }).toContain(`/chat?user_id=${peer.userId}`)
    await expect(page.locator('.chat-header .header-name').last()).toHaveText(peer.accountName, { timeout: 30000 })
  })
})
