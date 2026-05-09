/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type Page } from '@playwright/test'

interface MandatoryChannelFixture {
  accountName: string
  accessToken: string
  refreshToken: string
  channelId: number
  channelTitle: string
  seedMessage: string
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
    throw new Error('No JSON output returned from trading_bot_app mandatory channel seed helper')
  }

  return JSON.parse(lastLine) as T
}

function seedMandatoryChannelSession(label: string): MandatoryChannelFixture {
  return runPythonInApp<MandatoryChannelFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from core.db import AsyncSessionLocal
from core.enums import MessageType, UserRole
from core.security import create_access_token, create_refresh_token
from core.services.chat_room_service import ensure_mandatory_channel_rollout, get_mandatory_channel
from core.services.session_service import hash_token
from models.message import Message
from models.session import Platform, UserSession
from models.user import User

label = ${JSON.stringify(label)}

async def main():
    suffix = uuid.uuid4().hex[:10]
    now = datetime.utcnow()
    seed_message = f"PW mandatory room {suffix}"

    async with AsyncSessionLocal() as db:
        admin = User(
            account_name=f"pw_mandatory_admin_{suffix}",
            mobile_number=f"09{int(uuid.uuid4().hex[:9], 16) % 1000000000:09d}",
            full_name=f"pw_mandatory_admin_{suffix}",
            address="System Default",
            role=UserRole.SUPER_ADMIN,
            has_bot_access=True,
            max_sessions=1,
        )
        member = User(
            account_name=f"pw_mandatory_member_{suffix}",
            mobile_number=f"09{int(uuid.uuid4().hex[9:18], 16) % 1000000000:09d}",
            full_name=f"pw_mandatory_member_{suffix}",
            address="Playwright Mandatory Channel",
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        db.add(admin)
        db.add(member)
        await db.flush()

        await ensure_mandatory_channel_rollout(db, users=[admin, member])
        chat = await get_mandatory_channel(db)

        message = Message(
            chat_id=chat.id,
            sender_id=admin.id,
            receiver_id=admin.id,
            content=seed_message,
            message_type=MessageType.TEXT,
            is_read=True,
        )
        db.add(message)
        await db.flush()
        chat.last_message_id = message.id
        chat.last_message_at = now

        refresh_token = create_refresh_token(subject=member.id)
        session = UserSession(
            user_id=member.id,
            device_name='Playwright Mandatory Channel Device',
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
            subject=member.id,
            expires_delta=timedelta(minutes=60),
            session_id=str(session.id),
        )

        await db.commit()

    print(json.dumps({
        'accountName': member.account_name,
        'accessToken': access_token,
        'refreshToken': refresh_token,
        'channelId': chat.id,
        'channelTitle': chat.title,
        'seedMessage': seed_message,
    }))

asyncio.run(main())
`)
}

async function loginWithSeededSession(page: Page, fixture: MandatoryChannelFixture) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, {
    accessToken: fixture.accessToken,
    refreshToken: fixture.refreshToken,
  })
  await page.goto('/')
  await expect(page.getByText(fixture.accountName)).toBeVisible()
}

test.describe('Mandatory channel smoke', () => {
  test('mandatory room is visible in messenger and opens normally', async ({ page }) => {
    const fixture = seedMandatoryChannelSession('mandatory_channel_smoke')

    await loginWithSeededSession(page, fixture)
    await page.goto('/chat')

    const conversationRow = page.locator('.conversation-item').filter({ hasText: fixture.channelTitle })
    await expect(conversationRow).toBeVisible()
    await expect(conversationRow).toContainText(fixture.seedMessage)

    await conversationRow.click()

    await expect.poll(() => page.url(), { timeout: 30000 }).toContain(`/chat?user_id=-${fixture.channelId}`)
    await expect(page.locator('.chat-header').getByText(fixture.channelTitle)).toBeVisible()
    await expect(page.getByText(fixture.seedMessage)).toBeVisible()
  })
})