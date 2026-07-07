/// <reference types="node" />

import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { getE2EBackendBaseUrl, runPythonInApp as runPythonInConfiguredApp } from './helpers/mutationRuntime'

const BACKEND_BASE_URL = getE2EBackendBaseUrl()

interface SessionUser {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
}

interface OwnerRelationPayload {
  id: number
  global_account_name: string
  relation_display_name: string
  duty_description?: string | null
  mobile_number: string
  status: 'pending' | 'active' | 'expired' | 'revoked' | 'deleted'
  registration_link?: string | null
  accountant_user_id?: number | null
  accountant_account_name?: string | null
}

interface ActivatedAccountantPayload {
  accountantUserId: number
  accountantAccountName: string
}

interface DeletedAccountantPayload {
  relationStatus: string | null
  relationDeletedAt: string | null
  userDeleted: boolean | null
}

function runPythonInApp<T>(script: string): T {
  return runPythonInConfiguredApp<T>(script, 'accountant flow helper')
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
    account_name = f"pw_{label}_{suffix}"
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000

    async with AsyncSessionLocal() as db:
        user = User(
            account_name=account_name,
            mobile_number=f"09{mobile_seed:09d}",
            full_name=account_name,
            address='Playwright Accountant Owner Flow',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
            max_accountants=3,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Accountant Owner Flow Device',
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

function activatePendingRelation(ownerUserId: number, globalAccountName: string): ActivatedAccountantPayload {
  return runPythonInApp<ActivatedAccountantPayload>(`
import asyncio
import json
from datetime import datetime

from sqlalchemy import select

from core.db import AsyncSessionLocal
from core.enums import UserRole
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.user import User

owner_user_id = ${JSON.stringify(ownerUserId)}
global_account_name = ${JSON.stringify(globalAccountName)}

async def main():
    async with AsyncSessionLocal() as db:
        relation_stmt = select(AccountantRelation).where(
            AccountantRelation.owner_user_id == owner_user_id,
            AccountantRelation.global_account_name == global_account_name,
            AccountantRelation.deleted_at.is_(None),
        )
        relation = (await db.execute(relation_stmt)).scalar_one()

        user = User(
            account_name=relation.global_account_name,
            mobile_number=relation.mobile_number,
            full_name=relation.relation_display_name,
            address='Playwright Accountant Activated User',
            role=UserRole.STANDARD,
            has_bot_access=False,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        relation.accountant_user_id = user.id
        relation.status = AccountantRelationStatus.ACTIVE
        relation.activated_at = datetime.utcnow()

        await db.commit()

    print(json.dumps({
        'accountantUserId': user.id,
        'accountantAccountName': user.account_name,
    }))

asyncio.run(main())
`)
}

function inspectDeletedAccountant(accountantUserId: number): DeletedAccountantPayload {
  return runPythonInApp<DeletedAccountantPayload>(`
import asyncio
import json

from sqlalchemy import select

from core.db import AsyncSessionLocal
from models.accountant_relation import AccountantRelation
from models.user import User

accountant_user_id = ${JSON.stringify(accountantUserId)}

async def main():
    async with AsyncSessionLocal() as db:
        relation_stmt = select(AccountantRelation).where(AccountantRelation.accountant_user_id == accountant_user_id)
        relation = (await db.execute(relation_stmt)).scalar_one_or_none()
        user_stmt = select(User).where(User.id == accountant_user_id)
        user = (await db.execute(user_stmt)).scalar_one_or_none()

    print(json.dumps({
        'relationStatus': getattr(relation.status, 'value', None) if relation else None,
        'relationDeletedAt': relation.deleted_at.isoformat() if relation and relation.deleted_at else None,
        'userDeleted': getattr(user, 'is_deleted', None),
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

async function setAuthTokens(page: Page, session: SessionUser) {
  await page.goto('/login')
  await page.evaluate(({ accessToken, refreshToken }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.removeItem('suspended_refresh_token')
  }, {
    accessToken: session.accessToken,
    refreshToken: session.refreshToken,
  })
}

async function fetchOwnerRelations(request: APIRequestContext, accessToken: string): Promise<OwnerRelationPayload[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/accountants/owner-relations`, {
    headers: authHeaders(accessToken),
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<OwnerRelationPayload[]>
}

test.describe('accountant owner lifecycle', () => {
  test('owner can create pending accountant, observe activation, view it, and unlink it', async ({ page, request }) => {
    test.setTimeout(120000)

    const owner = seedSessionUser('accountant_owner_flow')
    const suffix = Date.now()
    const globalAccountName = `pwacct_${suffix}`
    const relationDisplayName = `حسابدار تست ${suffix}`
    const dutyDescription = `پیگیری تستی ${suffix}`
    const mobileNumber = `09${String(suffix).slice(-9)}`

    await waitForBackendReady(request)
    await setAuthTokens(page, owner)

    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })

    await page.locator('.owner-profile-section .settings-btn').filter({ hasText: 'حسابداران' }).click()
    const workspace = page.locator('.accountant-workspace-view')
    await expect(workspace).toBeVisible({ timeout: 30000 })

    await workspace.getByRole('button', { name: /افزودن حسابدار/ }).first().click()
    const createPanel = page.locator('.accountant-create-panel')
    await expect(createPanel).toBeVisible({ timeout: 30000 })
    await page.getByPlaceholder('مثلاً accountant_01').fill(globalAccountName)
    await page.getByPlaceholder('مثلاً حسابدار فروش').fill(relationDisplayName)
    await page.getByPlaceholder('0912xxxxxxx').fill(mobileNumber)
    await page.getByPlaceholder('مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه').fill(dutyDescription)
    await page.getByRole('button', { name: 'ثبت دعوت حسابدار' }).click()

    let pendingRelation: OwnerRelationPayload | null = null
    await expect
      .poll(async () => {
        const relations = await fetchOwnerRelations(request, owner.accessToken)
        pendingRelation = relations.find((relation) => relation.global_account_name === globalAccountName) ?? null
        return pendingRelation?.status ?? null
      }, { timeout: 30000 })
      .toBe('pending')

    expect(pendingRelation?.registration_link).toContain('/register')
    await expect(workspace).toContainText(relationDisplayName, { timeout: 30000 })
    await expect(workspace).toContainText('دعوت', { timeout: 30000 })

    const activatedAccountant = activatePendingRelation(owner.userId, globalAccountName)

    await page.reload()
    await expect(workspace).toBeVisible({ timeout: 30000 })
    await expect
      .poll(async () => {
        const relations = await fetchOwnerRelations(request, owner.accessToken)
        return relations.find((relation) => relation.id === pendingRelation?.id)?.status ?? null
      }, { timeout: 30000 })
      .toBe('active')

    await expect(workspace).toContainText('فعال', { timeout: 30000 })
    await expect(workspace).toContainText(relationDisplayName, { timeout: 30000 })
    await expect(workspace).toContainText(dutyDescription, { timeout: 30000 })

    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.accountant-relations-section')).toContainText(relationDisplayName)
    await expect(page.locator('.accountant-relations-section')).toContainText(dutyDescription)

    await page.locator('.owner-profile-section .settings-btn').filter({ hasText: 'حسابداران' }).click()
    await expect(workspace).toBeVisible({ timeout: 30000 })

    await workspace.locator('.ui-list-item').filter({ hasText: relationDisplayName }).first().click()
    await expect(page).toHaveURL(new RegExp(`/operations/accountants/${pendingRelation?.id}(?:\\?.*)?$`))
    await page.getByRole('tab', { name: 'حساس' }).click()
    await page.getByRole('button', { name: 'قطع ارتباط حسابدار' }).click()
    await page.locator('.ui-confirm-dialog:visible').getByRole('button', { name: 'قطع ارتباط', exact: true }).click()

    await expect
      .poll(async () => (await fetchOwnerRelations(request, owner.accessToken)).length, { timeout: 30000 })
      .toBe(0)

    await expect
      .poll(() => inspectDeletedAccountant(activatedAccountant.accountantUserId), { timeout: 30000 })
      .toMatchObject({
        relationStatus: 'deleted',
        userDeleted: true,
      })

    await page.goto('/operations/accountants')
    await expect(workspace).toBeVisible({ timeout: 30000 })
    await expect(workspace).toContainText('هنوز حسابداری ثبت نشده است', { timeout: 30000 })
  })
})
