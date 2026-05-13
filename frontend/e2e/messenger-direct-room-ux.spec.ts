/// <reference types="node" />

import { execFileSync } from 'child_process'
import { mkdtempSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'
const TINY_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ6kAAAAASUVORK5CYII='
const GENERATED_WEBM_BASE64 = 'GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAMpEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHYTbuMU6uEElTDZ1OsggEeTbuMU6uEHFO7a1OsggMT7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsirXsYMPQkBNgI1MYXZmNjAuMTYuMTAwV0GNTGF2ZjYwLjE2LjEwMESJiECCwAAAAAAAFlSua8GuAQAAAAAAADjXgQFzxYgcOxjQCFYmc5yBACK1nIN1bmSIgQCGhVZfVlA5g4EBI+ODhAJiWgDgibCBYLqBYJqBAhJUw2dAgHNzoGPAgGfImkWjh0VOQ09ERVJEh41MYXZmNjAuMTYuMTAwc3PaY8CLY8WIHDsY0AhWJnNnyKVFo4dFTkNPREVSRIeYTGF2YzYwLjMxLjEwMiBsaWJ2cHgtdnA5Z8ihRaOIRFVSQVRJT05Eh5MwMDowMDowMC42MDAwMDAwMDAAH0O2dUFp54EAo7CBAACAgkmDQgAF8AX2ADgkHBhKAAAwYAAAfKn//1zBn///25IP//6uxlgyUsRrFACjlIEAKACGAECSnABQAAADIAAAWTDgo5SBAFAAhgBAkpwATuAAAyAAAFkw4KOUgQB4AIYAQJKcAFAAAAMgAABZMOCjlIEAoACGAECSnABNQAADIAAAWTDgo5SBAMgAhgBAkpwAUAAAAyAAAFkw4KOUgQDwAIYAQJKcAE7gAAMgAABZMOCjlIEBGACGAECSnABQAAADIAAAWTDgo5SBAUAAhgBAkpwASiAAAyAAAFkw4KOUgQFoAIYAQJKcAFAAAAMgAABZMOCjlIEBkACGAMCSnABKIAADIAAAWTDgo5SBAbgAhgBAkpwAUAAAAyAAAFkw4KOUgQHgAIYAQJKcAE1AAAMgAABZMOCjlIECCACGAECSnABQAAADIAAAWTDgo5SBAjAAhgBAkpwATuAAAyAAAFkw4BxTu2uRu4+zgQC3iveBAfGCAaTwgQM='
const PLAYWRIGHT_TMP_DIR = mkdtempSync(join(tmpdir(), 'pw-direct-room-'))

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

function authOnlyHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
  }
}

function createPlaywrightBinaryFile(name: string, mimeType: string, bodyBase64: string) {
  return {
    name,
    mimeType,
    buffer: Buffer.from(bodyBase64, 'base64'),
  }
}

function createPlaywrightTextFilePath(name: string, contents: string) {
  const filePath = join(PLAYWRIGHT_TMP_DIR, name)
  writeFileSync(filePath, contents, 'utf8')
  return filePath
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

async function seedDirectDocumentMessage(
  request: APIRequestContext,
  sender: SessionFixture,
  receiverId: number,
  fileName: string,
  fileBody: string,
) {
  const uploadResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/upload-media`, {
    headers: authOnlyHeaders(sender.accessToken),
    multipart: {
      file: {
        name: fileName,
        mimeType: 'text/plain',
        buffer: Buffer.from(fileBody, 'utf8'),
      },
    },
  })

  expect(uploadResponse.ok()).toBeTruthy()
  const uploadPayload = await uploadResponse.json() as {
    file_id: string
    file_name: string
    mime_type: string
    size: number
  }

  const sendResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/send`, {
    headers: authHeaders(sender.accessToken),
    data: {
      receiver_id: receiverId,
      content: JSON.stringify(uploadPayload),
      message_type: 'document',
    },
  })

  expect(sendResponse.ok()).toBeTruthy()
  return sendResponse.json() as Promise<{ id: number; content: string; message_type: string }>
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

async function waitForPersistedPendingDocumentUpload(page: Page) {
  await expect
    .poll(async () => {
      return await page.evaluate(async () => {
        return await new Promise<boolean>((resolve) => {
          try {
            const openRequest = indexedDB.open('chat_upload_queue')
            openRequest.onerror = () => resolve(false)
            openRequest.onupgradeneeded = () => resolve(false)
            openRequest.onsuccess = () => {
              try {
                const db = openRequest.result
                if (!db.objectStoreNames.contains('pending')) {
                  resolve(false)
                  return
                }

                const tx = db.transaction('pending', 'readonly')
                const getAllRequest = tx.objectStore('pending').getAll()
                getAllRequest.onerror = () => resolve(false)
                getAllRequest.onsuccess = () => {
                  const rows = Array.isArray(getAllRequest.result) ? getAllRequest.result : []
                  resolve(
                    rows.some((row) => (
                      row?.msgType === 'document' &&
                      typeof row?.phase === 'string' &&
                      !['sent', 'cancelled', 'failed'].includes(row.phase)
                    )),
                  )
                }
              } catch {
                resolve(false)
              }
            }
          } catch {
            resolve(false)
          }
        })
      })
    }, { timeout: 15000 })
    .toBe(true)
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
  const filePath = createPlaywrightTextFilePath(`pw-direct-${suffix}.txt`, `Playwright document ${suffix}`)
  await page.locator('input[type="file"][accept="*"]').setInputFiles(filePath)
}

async function openDirectHeaderSearch(page: Page) {
  await page.locator('.chat-header .header-menu-container .header-btn').last().click()
  await page.locator('.header-dropdown-menu .header-menu-item').filter({ hasText: 'جستجو' }).click()
  await expect(page.locator('#search-input')).toBeVisible({ timeout: 30000 })
}

async function triggerLongPressSelectionOnMessage(page: Page, messageText: string) {
  const bubble = page.locator('.message-bubble').filter({ hasText: messageText }).first()
  await expect(bubble).toBeVisible({ timeout: 30000 })

  await bubble.evaluate((node) => {
    const createSyntheticTouchEvent = (
      type: 'touchstart' | 'touchend',
      target: HTMLElement,
      touchList: Array<Record<string, number | HTMLElement>>,
    ) => {
      const event = new Event(type, { bubbles: true, cancelable: true })
      Object.defineProperties(event, {
        touches: { value: type === 'touchstart' ? touchList : [], configurable: true },
        targetTouches: { value: type === 'touchstart' ? touchList : [], configurable: true },
        changedTouches: { value: touchList, configurable: true },
      })
      target.dispatchEvent(event)
    }

    const target = (node.closest('.message-wrapper') as HTMLElement | null) ?? (node as HTMLElement)
    const rect = target.getBoundingClientRect()
    const point = {
      identifier: 1,
      target,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
      pageX: rect.left + rect.width / 2,
      pageY: rect.top + rect.height / 2,
      screenX: rect.left + rect.width / 2,
      screenY: rect.top + rect.height / 2,
      radiusX: 2,
      radiusY: 2,
      rotationAngle: 0,
      force: 1,
    }
    createSyntheticTouchEvent('touchstart', target, [point])
  })

  await page.waitForTimeout(650)

  await bubble.evaluate((node) => {
    const createSyntheticTouchEvent = (
      type: 'touchstart' | 'touchend',
      target: HTMLElement,
      touchList: Array<Record<string, number | HTMLElement>>,
    ) => {
      const event = new Event(type, { bubbles: true, cancelable: true })
      Object.defineProperties(event, {
        touches: { value: type === 'touchstart' ? touchList : [], configurable: true },
        targetTouches: { value: type === 'touchstart' ? touchList : [], configurable: true },
        changedTouches: { value: touchList, configurable: true },
      })
      target.dispatchEvent(event)
    }

    const target = (node.closest('.message-wrapper') as HTMLElement | null) ?? (node as HTMLElement)
    const rect = target.getBoundingClientRect()
    const point = {
      identifier: 1,
      target,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
      pageX: rect.left + rect.width / 2,
      pageY: rect.top + rect.height / 2,
      screenX: rect.left + rect.width / 2,
      screenY: rect.top + rect.height / 2,
      radiusX: 2,
      radiusY: 2,
      rotationAngle: 0,
      force: 1,
    }
    createSyntheticTouchEvent('touchend', target, [point])
  })
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

  test('document upload resumes after reload and stays attached to the active direct room', async ({ page, request }) => {
    test.setTimeout(120000)
    const actor = seedPrimarySession('direct_room_upload_resume_actor')
    const peer = seedPrimarySession('direct_room_upload_resume_peer')
    let hasHeldInitialUpload = false
    let releaseInitialUploadAbort: (() => void) | null = null
    let resolveInitialUploadSeen: (() => void) | null = null
    const initialUploadSeen = new Promise<void>((resolve) => {
      resolveInitialUploadSeen = resolve
    })

    await waitForBackendReady(request)
    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId, peer.accountName)

    await page.route('**/api/chat/upload-media', async (route) => {
      if (!hasHeldInitialUpload) {
        hasHeldInitialUpload = true
        resolveInitialUploadSeen?.()
        await new Promise<void>((resolve) => {
          releaseInitialUploadAbort = resolve
        })
        try {
          await route.abort()
        } catch {
          // Page reload may have already disposed the intercepted request.
        }
        return
      }

      await route.continue()
    })

    await page.locator('button.attach-btn').click()
    await expect(page.locator('.attachment-sheet')).toBeVisible({ timeout: 30000 })
    await page.getByRole('button', { name: 'فایل' }).first().click()
    await injectDocument(page)

    await expect(page.locator('.messages-container .msg-document')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.messages-container .sending-status-wrapper')).toBeVisible({ timeout: 30000 })
    await initialUploadSeen
    await waitForPersistedPendingDocumentUpload(page)

    await page.reload()
    const releaseUploadAbort = releaseInitialUploadAbort ?? (() => {})
    releaseUploadAbort()

    await expect(page.locator('.chat-header .header-name')).toContainText(peer.accountName, { timeout: 30000 })

    await expect
      .poll(async () => {
        const messages = await fetchDirectMessages(request, actor, peer.userId)
        return messages.some((message) => message.message_type === 'document')
      }, { timeout: 60000 })
      .toBe(true)

    await expect(page.locator('.messages-container .msg-document').first()).toBeVisible({ timeout: 60000 })
    await expect(page.locator('.messages-container .sending-status-wrapper')).toHaveCount(0, { timeout: 60000 })
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

  test('search list clicks keep search active and next or prev controls move between matched messages', async ({ page, request }) => {
    test.setTimeout(90000)
    const actor = seedPrimarySession('direct_room_search_nav_actor')
    const peer = seedPrimarySession('direct_room_search_nav_peer')
    const suffix = Date.now()
    const query = `PW DIRECT SEARCH EDGE ${suffix}`
    const olderTarget = `${query} OLDER`
    const newerTarget = `${query} NEWER`

    await waitForBackendReady(request)
    await sendDirectTextMessage(request, peer, actor.userId, `PW DIRECT SEARCH EDGE FILL ${suffix} #1`)
    await sendDirectTextMessage(request, peer, actor.userId, olderTarget)
    await sendDirectTextMessage(request, peer, actor.userId, `PW DIRECT SEARCH EDGE FILL ${suffix} #2`)
    await sendDirectTextMessage(request, peer, actor.userId, newerTarget)

    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId, peer.accountName)
    await openDirectHeaderSearch(page)

    await page.locator('#search-input').fill(query)
    await expect(page.locator('.search-bottom-bar')).toContainText('1 از 2')

    await page.locator('.toggle-list-btn').click()
    const olderResult = page.locator('.search-global-list .search-result-item').filter({ hasText: olderTarget })
    await expect(olderResult).toBeVisible({ timeout: 30000 })
    await olderResult.click()

    await expect(page.locator('.search-global-list')).toHaveCount(0, { timeout: 30000 })
    await expect(page.locator('#search-input')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.message-bubble').filter({ hasText: olderTarget }).first()).toHaveClass(/highlight-message/, { timeout: 30000 })

    const navButtons = page.locator('.search-bottom-bar .right-navs .nav-btn')
    await navButtons.nth(1).click()
    await expect(page.locator('.message-bubble').filter({ hasText: newerTarget }).first()).toHaveClass(/highlight-message/, { timeout: 30000 })

    await navButtons.nth(0).click()
    await expect(page.locator('.message-bubble').filter({ hasText: olderTarget }).first()).toHaveClass(/highlight-message/, { timeout: 30000 })
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
      }, { timeout: 90000 })
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
    await expect(page.locator('.lightbox-stage-card.active video.lightbox-media')).toHaveCount(1)

    await page.locator('.lightbox-btn.close').click()
    await expect(page.locator('.lightbox-overlay')).toHaveCount(0, { timeout: 30000 })
  })

  test('document download stays attached across room switches and clears its busy state when the background queue finishes', async ({ page, request }) => {
    test.setTimeout(120000)
    const actor = seedPrimarySession('direct_room_download_resume_actor')
    const peer = seedPrimarySession('direct_room_download_resume_peer')
    const otherPeer = seedPrimarySession('direct_room_download_resume_other_peer')
    const suffix = Date.now()
    const fileName = `pw-direct-download-${suffix}.txt`

    await waitForBackendReady(request)
    await seedDirectDocumentMessage(request, peer, actor.userId, fileName, `Playwright document body ${suffix}`)
  await sendDirectTextMessage(request, otherPeer, actor.userId, `PW DIRECT DOWNLOAD SWITCH ${suffix}`)

    await loginWithSeededSession(page, actor)
    await page.goto('/chat')
    const initialPeerConversation = page.locator('.conversation-item').filter({ hasText: peer.accountName }).first()
    const initialOtherConversation = page.locator('.conversation-item').filter({ hasText: otherPeer.accountName }).first()
    await expect(initialPeerConversation).toBeVisible({ timeout: 30000 })
    await expect(initialOtherConversation).toBeVisible({ timeout: 30000 })
    await initialPeerConversation.click()
    await expect(page.locator('.chat-header .header-name')).toContainText(peer.accountName, { timeout: 30000 })

    let releaseDocumentDownload: (() => void) | null = null
    const holdDocumentDownload = new Promise<void>((resolve) => {
      releaseDocumentDownload = resolve
    })

    await page.route('**/api/chat/files/**', async (route) => {
      await holdDocumentDownload
      await route.continue()
    })

    const documentBubble = page.locator('.messages-container .msg-document').filter({ hasText: fileName }).first()
    await expect(documentBubble).toBeVisible({ timeout: 30000 })

    await documentBubble.click()
    await expect(documentBubble).toHaveClass(/is-busy/, { timeout: 30000 })
    await expect(documentBubble.locator('.doc-icon.doc-uploading')).toBeVisible({ timeout: 30000 })

    await page.locator('.chat-header .back-btn').click()
    const otherConversation = page.locator('.conversation-item').filter({ hasText: otherPeer.accountName }).first()
    await expect(otherConversation).toBeVisible({ timeout: 30000 })
    await otherConversation.click()
    await expect(page.locator('.chat-header .header-name')).toContainText(otherPeer.accountName, { timeout: 30000 })

    await page.locator('.chat-header .back-btn').click()
    const originalConversation = page.locator('.conversation-item').filter({ hasText: peer.accountName }).first()
    await expect(originalConversation).toBeVisible({ timeout: 30000 })
    await originalConversation.click()
    await expect(page.locator('.chat-header .header-name')).toContainText(peer.accountName, { timeout: 30000 })

    const resumedBubble = page.locator('.messages-container .msg-document').filter({ hasText: fileName }).first()
    await expect(resumedBubble).toBeVisible({ timeout: 30000 })
    await expect(resumedBubble).toHaveClass(/is-busy/, { timeout: 30000 })
    await expect(resumedBubble.locator('.doc-icon.doc-uploading')).toBeVisible({ timeout: 30000 })

    releaseDocumentDownload?.()
    releaseDocumentDownload = null

    await expect(resumedBubble).not.toHaveClass(/is-busy/, { timeout: 60000 })
    await expect(resumedBubble.locator('.doc-icon.doc-uploading')).toHaveCount(0, { timeout: 60000 })
    await expect(resumedBubble.locator('.doc-download-icon')).toHaveCount(0, { timeout: 60000 })
  })

  test('selection mode can reply to a long-pressed message and exit selection state', async ({ page, request }) => {
    test.setTimeout(90000)
    const actor = seedPrimarySession('direct_room_selection_reply_actor')
    const peer = seedPrimarySession('direct_room_selection_reply_peer')
    const targetContent = `PW DIRECT SELECT REPLY ${Date.now()}`

    await waitForBackendReady(request)
    await sendDirectTextMessage(request, peer, actor.userId, targetContent)

    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId, peer.accountName)

    await triggerLongPressSelectionOnMessage(page, targetContent)

    await expect(page.locator('.selection-bottom-bar')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.message-bubble.selected-message').filter({ hasText: targetContent })).toHaveCount(1)

    await page.locator('.selection-bottom-bar .selection-action-btn').filter({ hasText: 'پاسخ' }).click()

    await expect(page.locator('.reply-banner')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.reply-banner')).toContainText(targetContent)
    await expect(page.locator('.selection-bottom-bar')).toHaveCount(0, { timeout: 30000 })
    await expect(page.locator('.message-bubble.selected-message')).toHaveCount(0)
  })

  test('selection mode supports multi-select toggles and exits through browser back', async ({ page, request }) => {
    test.setTimeout(90000)
    const actor = seedPrimarySession('direct_room_selection_multi_actor')
    const peer = seedPrimarySession('direct_room_selection_multi_peer')
    const firstContent = `PW DIRECT SELECT FIRST ${Date.now()}`
    const secondContent = `PW DIRECT SELECT SECOND ${Date.now()}`

    await waitForBackendReady(request)
    await sendDirectTextMessage(request, peer, actor.userId, firstContent)
    await sendDirectTextMessage(request, peer, actor.userId, secondContent)

    await loginWithSeededSession(page, actor)
    await openDirectChat(page, peer.userId, peer.accountName)

    await triggerLongPressSelectionOnMessage(page, firstContent)
    await expect(page.locator('.selection-bottom-bar')).toBeVisible({ timeout: 30000 })

    await page.locator('.message-bubble').filter({ hasText: secondContent }).first().click()

    await expect(page.locator('.message-bubble.selected-message')).toHaveCount(2)
    await expect(page.locator('.selection-bottom-bar')).not.toContainText('پاسخ')
    await expect(page.locator('.selection-bottom-bar')).toContainText('هدایت')

    await page.evaluate(() => window.history.back())

    await expect(page.locator('.selection-bottom-bar')).toHaveCount(0, { timeout: 30000 })
    await expect(page.locator('.message-bubble.selected-message')).toHaveCount(0)
  })
})