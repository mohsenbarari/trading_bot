/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SeededChannelAdminFixture {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
  bootstrapAccessToken: string
  creatorUserId: number
  creatorAccountName: string
  creatorAccessToken: string
  channelId: number
  channelTitle: string
}

type SeededChannelRole = 'admin' | 'member'

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

function seedChannelSession(label: string, role: SeededChannelRole): SeededChannelAdminFixture {
  return runPythonInApp<SeededChannelAdminFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from core.db import AsyncSessionLocal
from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, UserRole
from core.security import create_access_token, create_refresh_token
from core.services.session_service import hash_token
from models.chat import Chat
from models.chat_member import ChatMember
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}
role = ${JSON.stringify(role)}

async def main():
    suffix = uuid.uuid4().hex[:10]
    member_account_name = f"pw_{label}_{suffix}"
    member_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    member_mobile_number = f"09{member_mobile_seed:09d}"
    channel_title = f"Playwright Channel {suffix}"
    now = datetime.utcnow()

    async with AsyncSessionLocal() as db:
        creator_account_name = f"pw_creator_{label}_{suffix}"
        creator_mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
        creator_mobile_number = f"09{creator_mobile_seed:09d}"

        creator = User(
            account_name=creator_account_name,
            mobile_number=creator_mobile_number,
            full_name=creator_account_name,
            address='Playwright Channel Creator',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(creator)
        await db.flush()

        creator_refresh_token = create_refresh_token(subject=creator.id)
        creator_session = UserSession(
            user_id=creator.id,
            device_name='Playwright Channel Creator Device',
            device_ip='127.0.0.1',
            platform=Platform.WEB,
            refresh_token_hash=hash_token(creator_refresh_token),
            is_primary=True,
            is_active=True,
            expires_at=None,
        )
        db.add(creator_session)
        await db.flush()

        creator_access_token = create_access_token(
            subject=creator.id,
            expires_delta=timedelta(minutes=60),
            session_id=str(creator_session.id),
        )

        user = User(
            account_name=member_account_name,
            mobile_number=member_mobile_number,
            full_name=member_account_name,
            address='Playwright Channel Test',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Channel Admin Device',
            device_ip='127.0.0.1',
            platform=Platform.WEB,
            refresh_token_hash=hash_token(refresh_token),
            is_primary=True,
            is_active=True,
            expires_at=None,
        )
        db.add(session)
        await db.flush()

        channel = Chat(
            type=ChatType.CHANNEL,
            title=channel_title,
            description='Playwright seeded optional channel',
            created_by_id=creator.id,
            is_system=False,
            is_mandatory=False,
            created_at=now,
            updated_at=now,
        )
        db.add(channel)
        await db.flush()

        db.add(ChatMember(
            chat_id=channel.id,
            user_id=creator.id,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
            joined_at=now,
            updated_at=now,
        ))

        member_role = ChatMemberRole.ADMIN if role == 'admin' else ChatMemberRole.MEMBER
        if user.id != creator.id:
          db.add(ChatMember(
            chat_id=channel.id,
            user_id=user.id,
            role=member_role,
            membership_status=ChatMembershipStatus.ACTIVE,
            joined_at=now,
            updated_at=now,
          ))

        access_token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=60),
            session_id=str(session.id),
        )

        await db.commit()

    print(json.dumps({
        'userId': user.id,
        'accountName': member_account_name,
        'accessToken': access_token,
        'refreshToken': refresh_token,
        'bootstrapAccessToken': creator_access_token if role == 'member' else access_token,
        'creatorUserId': creator.id,
        'creatorAccountName': creator_account_name,
        'creatorAccessToken': creator_access_token,
        'channelId': channel.id,
        'channelTitle': channel_title,
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

async function loginWithSeededSession(page: Page, fixture: SeededChannelAdminFixture) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, fixture)
  await page.goto('/')
  await expect(page.getByText(fixture.accountName)).toBeVisible()
}

async function seedBootstrapChannelMessage(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
  content: string,
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/rooms/${fixture.channelId}/send`, {
    headers: authHeaders(fixture.bootstrapAccessToken),
    data: {
      content,
      message_type: 'text',
    },
  })

  expect(response.ok()).toBeTruthy()
  return response.json()
}

async function seedDirectTextMessage(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
  content: string,
) {
  const response = await request.post(`${BACKEND_BASE_URL}/api/chat/send`, {
    headers: authHeaders(fixture.creatorAccessToken),
    data: {
      receiver_id: fixture.userId,
      content,
      message_type: 'text',
    },
  })

  expect(response.ok()).toBeTruthy()
  return response.json()
}

async function seedDirectDocumentMessage(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
  fileName: string,
  fileBody: string,
) {
  const uploadResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/upload-media`, {
    headers: authOnlyHeaders(fixture.creatorAccessToken),
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
    headers: authHeaders(fixture.creatorAccessToken),
    data: {
      receiver_id: fixture.userId,
      content: JSON.stringify(uploadPayload),
      message_type: 'document',
    },
  })

  expect(sendResponse.ok()).toBeTruthy()
  return sendResponse.json()
}

async function seedDirectImageMessage(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
  fileName: string,
) {
  const uploadResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/upload-media`, {
    headers: authOnlyHeaders(fixture.creatorAccessToken),
    multipart: {
      file: {
        name: fileName,
        mimeType: 'image/png',
        buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ6kAAAAASUVORK5CYII=', 'base64'),
      },
    },
  })

  expect(uploadResponse.ok()).toBeTruthy()
  const uploadPayload = await uploadResponse.json() as {
    file_id: string
    file_name: string
    mime_type: string
    size: number
    width?: number
    height?: number
    thumbnail?: string | null
  }

  const sendResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/send`, {
    headers: authHeaders(fixture.creatorAccessToken),
    data: {
      receiver_id: fixture.userId,
      content: JSON.stringify(uploadPayload),
      message_type: 'image',
    },
  })

  expect(sendResponse.ok()).toBeTruthy()
  return sendResponse.json()
}

async function fetchLatestRoomMessageTypes(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
): Promise<string[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/rooms/${fixture.channelId}/messages?limit=12`, {
    headers: authHeaders(fixture.accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = (await response.json()) as Array<{ message_type?: string }>
  return Array.isArray(body) ? body.map((item) => item.message_type || '') : []
}

async function fetchLatestRoomContents(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
): Promise<string[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/rooms/${fixture.channelId}/messages?limit=12`, {
    headers: authHeaders(fixture.accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = (await response.json()) as Array<{ content?: string }>
  return Array.isArray(body) ? body.map((item) => item.content || '') : []
}

async function fetchLatestDirectContents(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
  otherUserId: number,
): Promise<string[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/messages/${otherUserId}?limit=12`, {
    headers: authHeaders(fixture.accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = (await response.json()) as Array<{ content?: string }>
  return Array.isArray(body) ? body.map((item) => item.content || '') : []
}

function extractSharedFilePayload(
  contents: string[],
  expectedFileName: string,
): { file_id?: string; file_name?: string; mime_type?: string; size?: number } | null {
  for (const content of contents) {
    try {
      const parsed = JSON.parse(content) as { file_id?: string; file_name?: string; mime_type?: string; size?: number }
      if (parsed?.file_name === expectedFileName) {
        return parsed
      }
    } catch {
      // Ignore plain text messages in mixed threads.
    }
  }

  return null
}

async function fetchChannelConversationUnread(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
): Promise<number> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/conversations`, {
    headers: authHeaders(fixture.accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = (await response.json()) as Array<{ chat_id?: number; unread_count?: number }>
  const channel = Array.isArray(body)
    ? body.find((item) => Number(item?.chat_id) === fixture.channelId)
    : undefined

  return Number(channel?.unread_count || 0)
}

async function fetchRoomMessageReactionEmojis(
  request: APIRequestContext,
  fixture: SeededChannelAdminFixture,
  messageId: number,
): Promise<string[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/chat/rooms/${fixture.channelId}/messages?limit=20`, {
    headers: authHeaders(fixture.accessToken),
  })

  expect(response.ok()).toBeTruthy()
  const body = (await response.json()) as Array<{ id?: number; reactions?: Array<{ emoji?: string }> }>
  const message = Array.isArray(body)
    ? body.find((item) => Number(item?.id) === messageId)
    : undefined

  return Array.isArray(message?.reactions)
    ? message.reactions.map((reaction) => reaction?.emoji || '').filter(Boolean)
    : []
}

async function seedShareReceivePayload(page: Page, payload: {
  key: string
  title?: string
  text?: string
  url?: string
  files?: Array<{
    name: string
    type: string
    bodyBase64?: string
    generator?: 'video' | 'audio'
  }>
}) {
  await page.evaluate(async (entry) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open('trading-bot-share-target', 1)
      req.onupgradeneeded = () => {
        const nextDb = req.result
        if (!nextDb.objectStoreNames.contains('pending')) {
          nextDb.createObjectStore('pending', { keyPath: 'key' })
        }
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })

    const createGeneratedVideoBlob = async () => {
      const canvas = document.createElement('canvas')
      canvas.width = 96
      canvas.height = 96
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        throw new Error('Share-receive video context unavailable')
      }
      if (typeof canvas.captureStream !== 'function' || typeof MediaRecorder === 'undefined') {
        throw new Error('Share-receive MediaRecorder unavailable')
      }

      const mimeType = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((type) => MediaRecorder.isTypeSupported(type))
      if (!mimeType) {
        throw new Error('No supported share-receive MediaRecorder mime type')
      }

      return await new Promise<Blob>(async (resolveVideo, rejectVideo) => {
        const stream = canvas.captureStream(8)
        const recorder = new MediaRecorder(stream, { mimeType })
        const chunks: BlobPart[] = []

        recorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) {
            chunks.push(event.data)
          }
        }
        recorder.onerror = () => rejectVideo(new Error('Share-receive MediaRecorder error'))
        recorder.onstop = () => resolveVideo(new Blob(chunks, { type: mimeType }))

        const drawFrame = (frame: number) => {
          ctx.fillStyle = frame % 2 === 0 ? '#dc2626' : '#7c3aed'
          ctx.fillRect(0, 0, 96, 96)
          ctx.fillStyle = '#ffffff'
          ctx.beginPath()
          ctx.arc(28 + frame * 5, 48, 16, 0, Math.PI * 2)
          ctx.fill()
          ctx.fillStyle = '#e2e8f0'
          ctx.font = 'bold 15px sans-serif'
          ctx.fillText('SHR', 42, 54)
        }

        recorder.start()
        for (let frame = 0; frame < 6; frame += 1) {
          drawFrame(frame)
          await new Promise((resolveFrame) => window.setTimeout(resolveFrame, 90))
        }
        recorder.stop()
      })
    }

    const createGeneratedAudioBlob = async () => {
      const sampleRate = 16000
      const durationSeconds = 0.45
      const frameCount = Math.floor(sampleRate * durationSeconds)
      const channelCount = 1
      const bitsPerSample = 16
      const blockAlign = channelCount * (bitsPerSample / 8)
      const byteRate = sampleRate * blockAlign
      const pcmBytes = frameCount * blockAlign
      const totalLength = 44 + pcmBytes
      const buffer = new ArrayBuffer(totalLength)
      const view = new DataView(buffer)

      const writeAscii = (offset: number, value: string) => {
        for (let index = 0; index < value.length; index += 1) {
          view.setUint8(offset + index, value.charCodeAt(index))
        }
      }

      writeAscii(0, 'RIFF')
      view.setUint32(4, 36 + pcmBytes, true)
      writeAscii(8, 'WAVE')
      writeAscii(12, 'fmt ')
      view.setUint32(16, 16, true)
      view.setUint16(20, 1, true)
      view.setUint16(22, channelCount, true)
      view.setUint32(24, sampleRate, true)
      view.setUint32(28, byteRate, true)
      view.setUint16(32, blockAlign, true)
      view.setUint16(34, bitsPerSample, true)
      writeAscii(36, 'data')
      view.setUint32(40, pcmBytes, true)

      let writeOffset = 44
      for (let sampleIndex = 0; sampleIndex < frameCount; sampleIndex += 1) {
        const time = sampleIndex / sampleRate
        const amplitude = Math.sin(2 * Math.PI * 660 * time) * 0.35
        const value = Math.max(-1, Math.min(1, amplitude))
        view.setInt16(writeOffset, value * 0x7fff, true)
        writeOffset += 2
      }

      return new Blob([buffer], { type: 'audio/wav' })
    }

    const buildPendingShareFile = async (file: { name: string; type: string; bodyBase64?: string; generator?: 'video' | 'audio' }) => {
      if (file.generator === 'video') {
        const blob = await createGeneratedVideoBlob()
        return {
          name: file.name,
          type: blob.type || file.type,
          size: blob.size,
          blob,
        }
      }

      if (file.generator === 'audio') {
        const blob = await createGeneratedAudioBlob()
        return {
          name: file.name,
          type: blob.type || file.type,
          size: blob.size,
          blob,
        }
      }

      if (!file.bodyBase64) {
        throw new Error(`Missing share payload body for ${file.name}`)
      }

      const binary = atob(file.bodyBase64)
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
      return {
        name: file.name,
        type: file.type,
        size: bytes.byteLength,
        blob: new Blob([bytes], { type: file.type }),
      }
    }

    const files = await Promise.all(Array.isArray(entry.files) ? entry.files.map((file) => buildPendingShareFile(file)) : [])

    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('pending', 'readwrite')
      tx.objectStore('pending').put({
        key: entry.key,
        createdAt: Date.now(),
        title: entry.title || '',
        text: entry.text || '',
        url: entry.url || '',
        files,
      })

      tx.oncomplete = () => {
        db.close()
        resolve()
      }
      tx.onerror = () => {
        db.close()
        reject(tx.error)
      }
    })
  }, payload)
}

async function injectGalleryVideo(page: Page, fileName: string) {
  await page.evaluate(async ({ nextFileName }) => {
    const input = document.querySelector('input[type="file"][accept="image/*,video/*"]')
    if (!(input instanceof HTMLInputElement)) {
      throw new Error('Gallery input not found')
    }

    const videoFile = await new Promise<File>(async (resolve, reject) => {
      const canvas = document.createElement('canvas')
      canvas.width = 96
      canvas.height = 96
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('Video context unavailable'))
        return
      }
      if (typeof canvas.captureStream !== 'function' || typeof MediaRecorder === 'undefined') {
        reject(new Error('MediaRecorder captureStream unavailable'))
        return
      }

      const mimeType = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((type) => MediaRecorder.isTypeSupported(type))
      if (!mimeType) {
        reject(new Error('No supported MediaRecorder mime type'))
        return
      }

      const stream = canvas.captureStream(8)
      const recorder = new MediaRecorder(stream, { mimeType })
      const chunks: BlobPart[] = []

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          chunks.push(event.data)
        }
      }
      recorder.onerror = () => reject(new Error('MediaRecorder error'))
      recorder.onstop = () => {
        const blob = new Blob(chunks, { type: mimeType })
        resolve(new File([blob], nextFileName, { type: mimeType }))
      }

      const drawFrame = (frame: number) => {
        ctx.fillStyle = frame % 2 === 0 ? '#16a34a' : '#2563eb'
        ctx.fillRect(0, 0, 96, 96)
        ctx.fillStyle = '#ffffff'
        ctx.beginPath()
        ctx.arc(28 + frame * 6, 48, 16, 0, Math.PI * 2)
        ctx.fill()
        ctx.fillStyle = '#e2e8f0'
        ctx.font = 'bold 16px sans-serif'
        ctx.fillText('FWD', 44, 54)
      }

      recorder.start()
      for (let frame = 0; frame < 6; frame += 1) {
        drawFrame(frame)
        await new Promise((resolveFrame) => window.setTimeout(resolveFrame, 90))
      }
      recorder.stop()
    })

    const dataTransfer = new DataTransfer()
    dataTransfer.items.add(videoFile)
    input.files = dataTransfer.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }, { nextFileName: fileName })
}

async function injectGalleryImageAndVideo(page: Page) {
  await page.evaluate(async () => {
    const input = document.querySelector('input[type="file"][accept="image/*,video/*"]')
    if (!(input instanceof HTMLInputElement)) {
      throw new Error('Gallery input not found')
    }

    const imageFile = await new Promise<File>((resolve, reject) => {
      const canvas = document.createElement('canvas')
      canvas.width = 72
      canvas.height = 72
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('2D context unavailable'))
        return
      }
      ctx.fillStyle = '#f59e0b'
      ctx.fillRect(0, 0, 72, 72)
      ctx.fillStyle = '#0f172a'
      ctx.fillRect(10, 10, 52, 52)
      ctx.fillStyle = '#ffffff'
      ctx.font = 'bold 18px sans-serif'
      ctx.fillText('IMG', 14, 42)
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error('Failed to create image blob'))
          return
        }
        resolve(new File([blob], `pw-channel-${Date.now()}.png`, { type: 'image/png' }))
      }, 'image/png')
    })

    const videoFile = await new Promise<File>(async (resolve, reject) => {
      const canvas = document.createElement('canvas')
      canvas.width = 96
      canvas.height = 96
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('Video context unavailable'))
        return
      }
      if (typeof canvas.captureStream !== 'function' || typeof MediaRecorder === 'undefined') {
        reject(new Error('MediaRecorder captureStream unavailable'))
        return
      }

      const mimeType = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((type) => MediaRecorder.isTypeSupported(type))
      if (!mimeType) {
        reject(new Error('No supported MediaRecorder mime type'))
        return
      }

      const stream = canvas.captureStream(8)
      const recorder = new MediaRecorder(stream, { mimeType })
      const chunks: BlobPart[] = []

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          chunks.push(event.data)
        }
      }
      recorder.onerror = () => reject(new Error('MediaRecorder error'))
      recorder.onstop = () => {
        const blob = new Blob(chunks, { type: mimeType })
        resolve(new File([blob], `pw-channel-${Date.now()}.webm`, { type: mimeType }))
      }

      const drawFrame = (frame: number) => {
        ctx.fillStyle = frame % 2 === 0 ? '#0891b2' : '#4f46e5'
        ctx.fillRect(0, 0, 96, 96)
        ctx.fillStyle = '#ffffff'
        ctx.beginPath()
        ctx.arc(24 + frame * 6, 48, 16, 0, Math.PI * 2)
        ctx.fill()
        ctx.fillStyle = '#e2e8f0'
        ctx.font = 'bold 16px sans-serif'
        ctx.fillText('VID', 48, 54)
      }

      recorder.start()
      for (let frame = 0; frame < 6; frame += 1) {
        drawFrame(frame)
        await new Promise((resolveFrame) => window.setTimeout(resolveFrame, 90))
      }
      recorder.stop()
    })

    const dataTransfer = new DataTransfer()
    dataTransfer.items.add(imageFile)
    dataTransfer.items.add(videoFile)
    input.files = dataTransfer.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

test.describe('Channel media regressions', () => {
  test('channel admin can open attachments without voice/location and send image+video album', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('channel_media', 'admin')
    const bootstrapContent = `PLAYWRIGHT CHANNEL BOOTSTRAP ${Date.now()}`
    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)

    await loginWithSeededSession(page, fixture)

    await page.goto('/chat')
    await expect(page.getByText(fixture.channelTitle)).toBeVisible()
    await page.getByText(fixture.channelTitle).click()

    await expect(page.getByText('کانال • شما مدیر هستید')).toBeVisible()
    await expect(page.locator('button.attach-btn')).toBeVisible()
    await expect(page.locator('button.voice-btn')).toHaveCount(0)

    await page.locator('button.attach-btn').click()
    await expect(page.getByRole('button', { name: 'گالری' }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'فایل' }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'موقعیت' })).toHaveCount(0)

    await injectGalleryImageAndVideo(page)

    await expect(page.locator('.gp-title')).toHaveText('2 مورد')
    await page.getByRole('button', { name: 'ارسال 2 مورد' }).click()
    await expect(page.getByRole('button', { name: 'ارسال 2 مورد' })).toHaveCount(0)

    await expect(page.locator('.messages-container [data-media-msg-id]')).toHaveCount(1, { timeout: 30000 })
    await expect(page.locator('.messages-container video')).toHaveCount(1, { timeout: 30000 })

    await expect
      .poll(async () => fetchLatestRoomMessageTypes(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining(['image', 'video']))
  })

  test('channel member sees read-only composer and backend rejects posting', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('channel_member', 'member')
    const bootstrapContent = `PLAYWRIGHT CHANNEL MEMBER ${Date.now()}`
    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)

    await loginWithSeededSession(page, fixture)

    await page.goto('/chat')
    await expect(page.getByText(fixture.channelTitle)).toBeVisible()
    await page.getByText(fixture.channelTitle).click()

    await expect(page.getByText('کانال • فقط مدیران امکان ارسال دارند')).toBeVisible()
    await expect(page.getByText('فقط مدیران کانال امکان ارسال پیام دارند.')).toBeVisible()
    await expect(page.locator('button.attach-btn')).toHaveCount(0)
    await expect(page.locator('button.voice-btn')).toHaveCount(0)
    await expect(page.getByRole('textbox', { name: 'پیام...' })).toHaveCount(0)

    const response = await request.post(`${BACKEND_BASE_URL}/api/chat/rooms/${fixture.channelId}/send`, {
      headers: authHeaders(fixture.accessToken),
      data: {
        content: `PLAYWRIGHT MEMBER BLOCKED ${Date.now()}`,
        message_type: 'text',
      },
    })

    expect(response.status()).toBe(403)
    await expect(response.json()).resolves.toMatchObject({
      detail: 'Only channel admins can post messages',
    })
  })

  test('channel room selection survives reload via synced route query', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('channel_reload_restore', 'member')
    const bootstrapContent = `PW CHANNEL RELOAD ${Date.now()}`
    const conversationRow = page.locator('.conversation-item').filter({ hasText: fixture.channelTitle })

    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)
    await loginWithSeededSession(page, fixture)

    await page.goto('/chat')
    await expect(conversationRow).toBeVisible()
    await conversationRow.click()

    await expect.poll(() => page.url(), { timeout: 30000 }).toContain(`/chat?user_id=-${fixture.channelId}`)
    await expect(page.locator('.chat-header').getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.locator('.chat-header').getByText('کانال • فقط مدیران امکان ارسال دارند')).toBeVisible()
    await expect(page.locator('.messages-container').getByText(bootstrapContent)).toBeVisible()

    await page.reload()

    await expect.poll(() => page.url(), { timeout: 30000 }).toContain(`/chat?user_id=-${fixture.channelId}`)
    await expect(page.locator('.chat-header').getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.locator('.chat-header').getByText('کانال • فقط مدیران امکان ارسال دارند')).toBeVisible()
    await expect(page.locator('.messages-container').getByText(bootstrapContent)).toBeVisible()
  })

  test('channel admin can forward a document message into the channel', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('channel_forward_document', 'admin')
    const fileName = `pw-forward-${Date.now()}.txt`
    const fileBody = `PW FORWARD DOCUMENT ${Date.now()}`
    const directDocumentMessage = await seedDirectDocumentMessage(request, fixture, fileName, fileBody) as { id?: number }
    const sourceMessageId = Number(directDocumentMessage?.id)

    expect(Number.isFinite(sourceMessageId)).toBeTruthy()

    await loginWithSeededSession(page, fixture)

    await page.goto('/chat')
    await expect(page.getByText(fixture.creatorAccountName)).toBeVisible()
    await page.getByText(fixture.creatorAccountName).click()

    const sourceMessageBubble = page.locator(`#msg-${sourceMessageId}`)
    await expect(sourceMessageBubble.getByText(fileName)).toBeVisible()

    await sourceMessageBubble.dispatchEvent('click')
    await expect(page.locator('.context-menu')).toBeVisible()
    await page.locator('.context-menu .menu-item').filter({ hasText: 'هدایت پیام' }).click()

    await expect(page.locator('.forward-modal')).toBeVisible()
    await page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle }).click()
    await page.getByRole('button', { name: 'هدایت به 1 مقصد' }).click()

    await expect(page.locator('.forward-modal')).toHaveCount(0)
    await expect(page.getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.getByText('کانال • شما مدیر هستید')).toBeVisible()
    await expect(page.locator('.messages-container .forwarded-banner')).toContainText(`از ${fixture.creatorAccountName}`)
    await expect(page.locator('.messages-container .msg-document').getByText(fileName)).toBeVisible()

    await expect
      .poll(async () => fetchLatestRoomMessageTypes(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining(['document']))
    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expect.stringContaining(fileName)]))
  })

  test('channel admin can forward an image message into the channel', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('channel_forward_image', 'admin')
    const fileName = `pw-forward-${Date.now()}.png`
    const directImageMessage = await seedDirectImageMessage(request, fixture, fileName) as { id?: number }
    const sourceMessageId = Number(directImageMessage?.id)

    expect(Number.isFinite(sourceMessageId)).toBeTruthy()

    await loginWithSeededSession(page, fixture)

    await page.goto('/chat')
    await expect(page.getByText(fixture.creatorAccountName)).toBeVisible()
    await page.getByText(fixture.creatorAccountName).click()

    const sourceMessageBubble = page.locator(`#msg-${sourceMessageId}`)
    await expect(sourceMessageBubble.locator('.msg-media-link')).toBeVisible()

    await sourceMessageBubble.dispatchEvent('click')
    await expect(page.locator('.context-menu')).toBeVisible()
    await page.locator('.context-menu .menu-item').filter({ hasText: 'هدایت پیام' }).click()

    await expect(page.locator('.forward-modal')).toBeVisible()
    await page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle }).click()
    await page.getByRole('button', { name: 'هدایت به 1 مقصد' }).click()

    await expect(page.locator('.forward-modal')).toHaveCount(0)
    await expect(page.getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.getByText('کانال • شما مدیر هستید')).toBeVisible()
    await expect(page.locator('.messages-container .forwarded-banner')).toContainText(`از ${fixture.creatorAccountName}`)
    await expect(page.locator('.messages-container .msg-media-link')).toBeVisible()

    await expect
      .poll(async () => fetchLatestRoomMessageTypes(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining(['image']))
    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expect.stringContaining(fileName)]))
  })

  test('channel admin can forward a video message into the channel', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('channel_forward_video', 'admin')
    const directBootstrapContent = `PW DIRECT VIDEO SOURCE ${Date.now()}`
    const videoFileName = `pw-forward-video-${Date.now()}.webm`

    await seedDirectTextMessage(request, fixture, directBootstrapContent)
    await loginWithSeededSession(page, fixture)

    await page.goto('/chat')
    await expect(page.getByText(fixture.creatorAccountName)).toBeVisible()
    await page.getByText(fixture.creatorAccountName).click()

    await expect(page.locator('.messages-container').getByText(directBootstrapContent)).toBeVisible()
    await page.locator('button.attach-btn').click()
    await injectGalleryVideo(page, videoFileName)

    await expect(page.locator('.messages-container video')).toHaveCount(1, { timeout: 30000 })

    const sourceMessageBubble = page.locator('.messages-container [id^="msg-"]:not([id^="msg--"])').filter({ has: page.locator('video') }).last()
    await expect(sourceMessageBubble).toBeVisible()

    await sourceMessageBubble.dispatchEvent('click')
    await expect(page.locator('.context-menu')).toBeVisible()
    await page.locator('.context-menu .menu-item').filter({ hasText: 'هدایت پیام' }).click()

    await expect(page.locator('.forward-modal')).toBeVisible()
    await page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle }).click()
    await page.getByRole('button', { name: 'هدایت به 1 مقصد' }).click()

    await expect(page.locator('.forward-modal')).toHaveCount(0)
    await expect(page.locator('.chat-header').getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.locator('.chat-header').getByText('کانال • شما مدیر هستید')).toBeVisible()
    await expect(page.locator('.messages-container .forwarded-banner')).toContainText(`از ${fixture.accountName}`)
    await expect(page.locator('.messages-container video')).toHaveCount(1, { timeout: 30000 })

    await expect
      .poll(async () => fetchLatestRoomMessageTypes(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining(['video']))
    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expect.stringContaining('"width":96')]))
  })

  test('channel member gets realtime unread update and live read reset', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('channel_realtime_unread', 'member')
    const unreadContent = `PW CHANNEL UNREAD ${Date.now()}`
    const liveContent = `PW CHANNEL LIVE ${Date.now()}`
    const conversationRow = page.locator('.conversation-item').filter({ hasText: fixture.channelTitle })

    await loginWithSeededSession(page, fixture)

    await page.goto('/chat')
    await expect(conversationRow).toBeVisible()
    await page.waitForTimeout(1200)

    await expect(conversationRow.locator('.unread-badge')).toHaveCount(0)
    await expect
      .poll(async () => fetchChannelConversationUnread(request, fixture), { timeout: 30000 })
      .toBe(0)

    const unreadResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/rooms/${fixture.channelId}/send`, {
      headers: authHeaders(fixture.bootstrapAccessToken),
      data: {
        content: unreadContent,
        message_type: 'text',
      },
    })

    expect(unreadResponse.ok()).toBeTruthy()

    await expect(conversationRow.locator('.unread-badge')).toHaveText('1')
    await expect
      .poll(async () => fetchChannelConversationUnread(request, fixture), { timeout: 30000 })
      .toBe(1)

    await conversationRow.click()

    await expect(page.getByText('کانال • فقط مدیران امکان ارسال دارند')).toBeVisible()
    await expect(page.locator('.messages-container').getByText(unreadContent)).toBeVisible()
    await expect
      .poll(async () => fetchChannelConversationUnread(request, fixture), { timeout: 30000 })
      .toBe(0)

    const liveResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/rooms/${fixture.channelId}/send`, {
      headers: authHeaders(fixture.bootstrapAccessToken),
      data: {
        content: liveContent,
        message_type: 'text',
      },
    })

    expect(liveResponse.ok()).toBeTruthy()

    await expect(page.locator('.messages-container').getByText(liveContent)).toBeVisible()
    await expect
      .poll(async () => fetchChannelConversationUnread(request, fixture), { timeout: 30000 })
      .toBe(0)
  })

  test('channel reactions patch live into an open member room', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('channel_reaction_live', 'member')
    const bootstrapContent = `PW CHANNEL REACTION ${Date.now()}`
    const bootstrapMessage = await seedBootstrapChannelMessage(request, fixture, bootstrapContent) as { id?: number }
    const messageId = Number(bootstrapMessage?.id)

    expect(Number.isFinite(messageId)).toBeTruthy()

    await loginWithSeededSession(page, fixture)

    await page.goto('/chat')
    await expect(page.getByText(fixture.channelTitle)).toBeVisible()
    await page.getByText(fixture.channelTitle).click()

    const messageRoot = page.locator(`#msg-${messageId}`)
    const liveReactionChip = page.locator('.messages-container .reaction-chip').filter({ hasText: '🔥' })
    await expect(messageRoot.getByText(bootstrapContent)).toBeVisible()
    await expect(liveReactionChip).toHaveCount(0)

    const reactionResponse = await request.post(`${BACKEND_BASE_URL}/api/chat/messages/${messageId}/reaction`, {
      headers: authHeaders(fixture.bootstrapAccessToken),
      data: {
        emoji: '🔥',
      },
    })

    expect(reactionResponse.ok()).toBeTruthy()
    await expect(reactionResponse.json()).resolves.toMatchObject({
      id: messageId,
    })

    await expect(liveReactionChip).toHaveCount(1)
    await expect(liveReactionChip).toContainText('🔥')
    await expect
      .poll(async () => fetchRoomMessageReactionEmojis(request, fixture, messageId), { timeout: 30000 })
      .toEqual(['🔥'])
  })

  test('share receive can route shared text into a writable channel target', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('share_receive_channel', 'admin')
    const bootstrapContent = `PLAYWRIGHT SHARE RECEIVE BOOTSTRAP ${Date.now()}`
    const shareKey = `pw-share-${Date.now()}`
    const shareTitle = `Playwright Shared Title ${Date.now()}`
    const shareText = `Playwright Shared Body ${Date.now()}`
    const shareUrl = `https://example.test/share/${Date.now()}`
    const expectedMergedText = `${shareTitle}\n${shareText}\n${shareUrl}`

    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)
    await loginWithSeededSession(page, fixture)
    await seedShareReceivePayload(page, {
      key: shareKey,
      title: shareTitle,
      text: shareText,
      url: shareUrl,
    })

    await page.goto(`/share-receive?share_key=${shareKey}`)
    await expect(page.locator('.forward-modal')).toBeVisible()

    await page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle }).click()
    await page.getByRole('button', { name: 'هدایت به 1 مقصد' }).click()

    await expect.poll(() => page.url(), { timeout: 30000 }).toContain(`/chat?user_id=-${fixture.channelId}`)
    await expect(page.getByText(fixture.channelTitle)).toBeVisible()

    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expectedMergedText]))
  })

  test('share receive can fan out shared text to a channel and a direct chat target', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('share_receive_multi_target', 'admin')
    const bootstrapContent = `PLAYWRIGHT SHARE RECEIVE MULTI ${Date.now()}`
    const seedDirectContent = `PW DIRECT TARGET SEED ${Date.now()}`
    const shareKey = `pw-share-multi-${Date.now()}`
    const shareTitle = `Playwright Shared Multi Title ${Date.now()}`
    const shareText = `Playwright Shared Multi Body ${Date.now()}`
    const shareUrl = `https://example.test/share/multi/${Date.now()}`
    const expectedMergedText = `${shareTitle}\n${shareText}\n${shareUrl}`
    const channelTarget = page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle })
    const directTarget = page.locator('.forward-target-item').filter({ hasText: fixture.creatorAccountName })

    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)
    await seedDirectTextMessage(request, fixture, seedDirectContent)
    await loginWithSeededSession(page, fixture)
    await seedShareReceivePayload(page, {
      key: shareKey,
      title: shareTitle,
      text: shareText,
      url: shareUrl,
    })

    await page.goto(`/share-receive?share_key=${shareKey}`)
    await expect(page.locator('.forward-modal')).toBeVisible()
    await expect(channelTarget).toBeVisible()
    await expect(directTarget).toBeVisible()

    await channelTarget.click()
    await directTarget.click()
    await page.getByRole('button', { name: 'هدایت به 2 مقصد' }).click()

    await expect(page.locator('.forward-modal')).toHaveCount(0)
    await expect
      .poll(() => {
        const url = new URL(page.url())
        return `${url.pathname}${url.search}`
      }, { timeout: 30000 })
      .toBe('/chat')
    await expect(page.locator('.conversation-item').filter({ hasText: fixture.channelTitle })).toBeVisible()
    await expect(page.locator('.conversation-item').filter({ hasText: fixture.creatorAccountName })).toBeVisible()

    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expectedMergedText]))
    await expect
      .poll(async () => fetchLatestDirectContents(request, fixture, fixture.creatorUserId), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expectedMergedText]))
  })

  test('share receive can fan out one shared document upload to a channel and a direct chat target', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('share_receive_multi_file', 'admin')
    const bootstrapContent = `PLAYWRIGHT SHARE RECEIVE MULTI FILE ${Date.now()}`
    const seedDirectContent = `PW DIRECT FILE TARGET SEED ${Date.now()}`
    const shareKey = `pw-share-multi-file-${Date.now()}`
    const sharedDocumentName = `pw-share-multi-${Date.now()}.txt`
    const sharedDocumentBody = `PW SHARE MULTI FILE BODY ${Date.now()}`
    const channelTarget = page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle })
    const directTarget = page.locator('.forward-target-item').filter({ hasText: fixture.creatorAccountName })

    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)
    await seedDirectTextMessage(request, fixture, seedDirectContent)
    await loginWithSeededSession(page, fixture)
    await seedShareReceivePayload(page, {
      key: shareKey,
      files: [
        {
          name: sharedDocumentName,
          type: 'text/plain',
          bodyBase64: Buffer.from(sharedDocumentBody, 'utf8').toString('base64'),
        },
      ],
    })

    await page.goto(`/share-receive?share_key=${shareKey}`)
    await expect(page.locator('.forward-modal')).toBeVisible()
    await expect(channelTarget).toBeVisible()
    await expect(directTarget).toBeVisible()

    await channelTarget.click()
    await directTarget.click()
    await page.getByRole('button', { name: 'هدایت به 2 مقصد' }).click()

    await expect(page.locator('.forward-modal')).toHaveCount(0)
    await expect
      .poll(() => {
        const url = new URL(page.url())
        return `${url.pathname}${url.search}`
      }, { timeout: 30000 })
      .toBe('/chat')
    await expect(page.locator('.conversation-item').filter({ hasText: fixture.channelTitle })).toBeVisible()
    await expect(page.locator('.conversation-item').filter({ hasText: fixture.creatorAccountName })).toBeVisible()

    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expect.stringContaining(sharedDocumentName)]))
    await expect
      .poll(async () => fetchLatestDirectContents(request, fixture, fixture.creatorUserId), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expect.stringContaining(sharedDocumentName)]))

    const [channelContents, directContents] = await Promise.all([
      fetchLatestRoomContents(request, fixture),
      fetchLatestDirectContents(request, fixture, fixture.creatorUserId),
    ])

    const channelPayload = extractSharedFilePayload(channelContents, sharedDocumentName)
    const directPayload = extractSharedFilePayload(directContents, sharedDocumentName)

    expect(channelPayload?.file_id).toBeTruthy()
    expect(directPayload?.file_id).toBeTruthy()
    expect(channelPayload?.file_id).toBe(directPayload?.file_id)
  })

  test('share receive can route shared file and media payloads into a writable channel target', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('share_receive_channel_files', 'admin')
    const bootstrapContent = `PLAYWRIGHT SHARE RECEIVE FILES ${Date.now()}`
    const shareKey = `pw-share-files-${Date.now()}`
    const sharedDocumentName = `pw-share-${Date.now()}.txt`
    const sharedImageName = `pw-share-${Date.now()}.png`
    const sharedDocumentBody = `PW SHARE FILE BODY ${Date.now()}`

    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)
    await loginWithSeededSession(page, fixture)
    await seedShareReceivePayload(page, {
      key: shareKey,
      files: [
        {
          name: sharedDocumentName,
          type: 'text/plain',
          bodyBase64: Buffer.from(sharedDocumentBody, 'utf8').toString('base64'),
        },
        {
          name: sharedImageName,
          type: 'image/png',
          bodyBase64: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ6kAAAAASUVORK5CYII=',
        },
      ],
    })

    await page.goto(`/share-receive?share_key=${shareKey}`)
    await expect(page.locator('.forward-modal')).toBeVisible()

    await page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle }).click()
    await page.getByRole('button', { name: 'هدایت به 1 مقصد' }).click()

    await expect(page.locator('.forward-modal')).toHaveCount(0)
    await expect.poll(() => page.url(), { timeout: 30000 }).toContain(`/chat?user_id=-${fixture.channelId}`)
    await expect(page.locator('.chat-header').getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.locator('.messages-container .msg-document').getByText(sharedDocumentName)).toBeVisible()
    await expect(page.locator('.messages-container .msg-media-link')).toBeVisible()

    await expect
      .poll(async () => fetchLatestRoomMessageTypes(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining(['document', 'image']))
    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([
        expect.stringContaining(sharedDocumentName),
        expect.stringContaining(sharedImageName),
      ]))
  })

  test('share receive can route a shared video into a writable channel target', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('share_receive_channel_video', 'admin')
    const bootstrapContent = `PLAYWRIGHT SHARE RECEIVE VIDEO ${Date.now()}`
    const shareKey = `pw-share-video-${Date.now()}`
    const sharedVideoName = `pw-share-video-${Date.now()}.webm`

    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)
    await loginWithSeededSession(page, fixture)
    await seedShareReceivePayload(page, {
      key: shareKey,
      files: [
        {
          name: sharedVideoName,
          type: 'video/webm',
          generator: 'video',
        },
      ],
    })

    await page.goto(`/share-receive?share_key=${shareKey}`)
    await expect(page.locator('.forward-modal')).toBeVisible()

    await page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle }).click()
    await page.getByRole('button', { name: 'هدایت به 1 مقصد' }).click()

    await expect(page.locator('.forward-modal')).toHaveCount(0)
    await expect.poll(() => page.url(), { timeout: 30000 }).toContain(`/chat?user_id=-${fixture.channelId}`)
    await expect(page.locator('.chat-header').getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.locator('.messages-container .msg-media-link')).toBeVisible()
    await expect(page.locator('.messages-container .media-type-badge')).toContainText('ویدئو')

    await expect
      .poll(async () => fetchLatestRoomMessageTypes(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining(['video']))
    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expect.stringContaining(sharedVideoName)]))
  })

  test('share receive can route a shared voice clip into a writable channel target', async ({
    page,
    request,
  }) => {
    const fixture = seedChannelSession('share_receive_channel_voice', 'admin')
    const bootstrapContent = `PLAYWRIGHT SHARE RECEIVE VOICE ${Date.now()}`
    const shareKey = `pw-share-voice-${Date.now()}`
    const sharedVoiceName = `pw-share-voice-${Date.now()}.wav`

    await seedBootstrapChannelMessage(request, fixture, bootstrapContent)
    await loginWithSeededSession(page, fixture)
    await seedShareReceivePayload(page, {
      key: shareKey,
      files: [
        {
          name: sharedVoiceName,
          type: 'audio/wav',
          generator: 'audio',
        },
      ],
    })

    await page.goto(`/share-receive?share_key=${shareKey}`)
    await expect(page.locator('.forward-modal')).toBeVisible()

    await page.locator('.forward-target-item').filter({ hasText: fixture.channelTitle }).click()
    await page.getByRole('button', { name: 'هدایت به 1 مقصد' }).click()

    await expect(page.locator('.forward-modal')).toHaveCount(0)
    await expect.poll(() => page.url(), { timeout: 30000 }).toContain(`/chat?user_id=-${fixture.channelId}`)
    await expect(page.locator('.chat-header').getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.locator('.messages-container .msg-voice')).toBeVisible()
    await expect(page.locator('.messages-container .voice-play-btn')).toBeVisible()

    await expect
      .poll(async () => fetchLatestRoomMessageTypes(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining(['voice']))
    await expect
      .poll(async () => fetchLatestRoomContents(request, fixture), { timeout: 30000 })
      .toEqual(expect.arrayContaining([expect.stringContaining(sharedVoiceName)]))
  })
})