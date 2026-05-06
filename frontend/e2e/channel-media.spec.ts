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
      recorder.onerror = (event) => reject((event as MediaRecorderErrorEvent).error || new Error('MediaRecorder error'))
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
})