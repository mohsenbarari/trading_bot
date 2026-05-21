/// <reference types="node" />

import { execFileSync } from 'child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_BASE_URL = 'http://127.0.0.1:8000'

interface SessionUser {
  userId: number
  accountName: string
  accessToken: string
  refreshToken: string
  roleLabel: string
}

interface OwnerCustomerRelationPayload {
  id: number
  invitation_account_name?: string | null
  customer_account_name?: string | null
  management_name: string
  customer_tier: 'tier1' | 'tier2'
  commission_rate?: number | null
  min_trade_quantity?: number | null
  max_trade_quantity?: number | null
  max_daily_trades?: number | null
  max_daily_commodity_volume?: number | null
  status: 'pending' | 'active' | 'expired' | 'revoked' | 'deleted'
  registration_link?: string | null
}

interface ActivatedCustomerPayload {
  customerUserId: number
  customerAccountName: string
}

interface DeletedCustomerPayload {
  relationStatus: string | null
  relationDeletedAt: string | null
  userDeleted: boolean | null
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
    throw new Error('No JSON output returned from trading_bot_app customer flow helper')
  }

  return JSON.parse(lastLine) as T
}

function seedSessionUser(label: string, roleTag: 'standard' | 'super_admin' = 'standard'): SessionUser {
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
role_tag = ${JSON.stringify(roleTag)}

async def main():
    suffix = uuid.uuid4().hex[:10]
    account_name = f"pw_{label}_{suffix}"
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    role = UserRole.SUPER_ADMIN if role_tag == 'super_admin' else UserRole.STANDARD

    async with AsyncSessionLocal() as db:
        user = User(
            account_name=account_name,
            mobile_number=f"09{mobile_seed:09d}",
            full_name=account_name,
            address='Playwright Customer Owner Flow',
            role=role,
            has_bot_access=True,
            max_sessions=1,
            max_customers=5,
        )
        db.add(user)
        await db.flush()

        refresh_token = create_refresh_token(subject=user.id)
        session = UserSession(
            user_id=user.id,
            device_name='Playwright Customer Owner Flow Device',
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
        'roleLabel': role.value,
    }))

asyncio.run(main())
`)
}

function activatePendingCustomer(relationId: number): ActivatedCustomerPayload {
  return runPythonInApp<ActivatedCustomerPayload>(`
import asyncio
import json
from datetime import datetime, UTC

from sqlalchemy import select

from core.db import AsyncSessionLocal
from core.enums import UserRole
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.invitation import Invitation
from models.user import User

relation_id = ${JSON.stringify(relationId)}

async def main():
    async with AsyncSessionLocal() as db:
        relation_stmt = select(CustomerRelation).where(CustomerRelation.id == relation_id)
        relation = (await db.execute(relation_stmt)).scalar_one()
        invitation_stmt = select(Invitation).where(Invitation.token == relation.invitation_token)
        invitation = (await db.execute(invitation_stmt)).scalar_one()

        user = User(
            account_name=invitation.account_name,
            mobile_number=invitation.mobile_number,
            full_name=relation.management_name,
            address='Playwright Activated Customer',
            role=UserRole.STANDARD,
            has_bot_access=False,
            max_sessions=1,
        )
        db.add(user)
        await db.flush()

        relation.customer_user_id = user.id
        relation.status = CustomerRelationStatus.ACTIVE
        relation.activated_at = datetime.now(UTC).replace(tzinfo=None)

        invitation.is_used = True

        await db.commit()

    print(json.dumps({
        'customerUserId': user.id,
        'customerAccountName': user.account_name,
    }))

asyncio.run(main())
`)
}

function inspectDeletedCustomer(customerUserId: number): DeletedCustomerPayload {
  return runPythonInApp<DeletedCustomerPayload>(`
import asyncio
import json

from sqlalchemy import select

from core.db import AsyncSessionLocal
from models.customer_relation import CustomerRelation
from models.user import User

customer_user_id = ${JSON.stringify(customerUserId)}

async def main():
    async with AsyncSessionLocal() as db:
        relation_stmt = select(CustomerRelation).where(CustomerRelation.customer_user_id == customer_user_id)
        relation = (await db.execute(relation_stmt)).scalar_one_or_none()
        user_stmt = select(User).where(User.id == customer_user_id)
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
  await page.evaluate(({ accessToken, refreshToken, roleLabel }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.setItem('current_user_summary', JSON.stringify({ role: roleLabel }))
    localStorage.removeItem('suspended_refresh_token')
  }, {
    accessToken: session.accessToken,
    refreshToken: session.refreshToken,
    roleLabel: session.roleLabel,
  })
}

async function fetchOwnerCustomerRelations(request: APIRequestContext, accessToken: string): Promise<OwnerCustomerRelationPayload[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/customers/owner-relations`, {
    headers: authHeaders(accessToken),
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<OwnerCustomerRelationPayload[]>
}

test.describe('customer owner lifecycle', () => {
  test('owner can create update and unlink a customer, and super-admin can follow the customer handoff', async ({ page, request }) => {
    test.setTimeout(180000)

    const owner = seedSessionUser('customer_owner_flow_owner')
    const superAdmin = seedSessionUser('customer_owner_flow_admin', 'super_admin')
    const suffix = Date.now()
    const customerAccountName = `pwcust_${suffix}`
    const managementName = `مشتری تست ${suffix}`
    const mobileNumber = `09${String(suffix).slice(-9)}`

    await waitForBackendReady(request)
    await setAuthTokens(page, owner)

    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })

    await page.locator('.owner-profile-section .settings-btn').filter({ hasText: 'مشتریان' }).click()
    const modal = page.locator('.customer-manager-shell')
    await expect(modal).toBeVisible({ timeout: 30000 })

    await modal.locator('input.create-account-name').fill(customerAccountName)
    await modal.locator('input.create-management-name').fill(managementName)
    await modal.locator('input.create-mobile-number').fill(mobileNumber)
    await modal.locator('select.create-tier-select').selectOption('tier2')
    await modal.locator('input.create-commission-rate').fill('1.25')
    await modal.locator('input.create-min-trade').fill('2')
    await modal.locator('input.create-max-trade').fill('8')
    await modal.locator('input.create-max-daily-trades').fill('3')
    await modal.locator('input.create-max-daily-volume').fill('40')
    await modal.locator('button.submit-create').click()

    let pendingRelation: OwnerCustomerRelationPayload | null = null
    await expect
      .poll(async () => {
        const relations = await fetchOwnerCustomerRelations(request, owner.accessToken)
        pendingRelation = relations.find((relation) => relation.management_name === managementName) ?? null
        return pendingRelation?.status ?? null
      }, { timeout: 30000 })
      .toBe('pending')

    expect(pendingRelation?.registration_link).toContain('/register')
    await expect(modal.locator('.customer-card').filter({ hasText: managementName })).toContainText('در انتظار ثبت‌نام')

    const activatedCustomer = activatePendingCustomer(Number(pendingRelation?.id))

    await modal.locator('button').filter({ hasText: 'بروزرسانی لیست' }).click()
    await expect
      .poll(async () => {
        const relations = await fetchOwnerCustomerRelations(request, owner.accessToken)
        return relations.find((relation) => relation.id === pendingRelation?.id)?.status ?? null
      }, { timeout: 30000 })
      .toBe('active')

    const activeCard = modal.locator('.customer-card').filter({ hasText: managementName }).first()
    await expect(activeCard).toContainText('فعال')
    await expect(activeCard).toContainText(activatedCustomer.customerAccountName)

    await activeCard.locator('button.start-edit').click()
    await activeCard.locator('input.edit-commission-rate').fill('2.50')
    await activeCard.locator('input.edit-max-trade').fill('12')
    await activeCard.locator('input.edit-max-daily-trades').fill('5')
    await activeCard.locator('button.save-edit').click()

    await expect
      .poll(async () => {
        const relations = await fetchOwnerCustomerRelations(request, owner.accessToken)
        const updated = relations.find((relation) => relation.id === pendingRelation?.id)
        return JSON.stringify({
          commission_rate: updated?.commission_rate,
          max_trade_quantity: updated?.max_trade_quantity,
          max_daily_trades: updated?.max_daily_trades,
        })
      }, { timeout: 30000 })
      .toBe(JSON.stringify({
        commission_rate: 2.5,
        max_trade_quantity: 12,
        max_daily_trades: 5,
      }))

    await modal.getByRole('button', { name: 'بستن' }).click()
    await expect(modal).toBeHidden({ timeout: 30000 })
    await expect(page.locator('.customer-relations-section')).toContainText(managementName)

    await page.locator('.customer-relations-section .customer-profile-link-btn').filter({ hasText: managementName }).click()
    await page.waitForURL(new RegExp(`/users/${activatedCustomer.customerUserId}(?:\\?.*)?$`))
    await expect(page.locator('.customer-context-banner')).toContainText(managementName)
    await expect(page.locator('.customer-context-banner')).toContainText(owner.accountName)

    await setAuthTokens(page, superAdmin)
    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })
    await page.locator('.customer-relations-section .customer-profile-link-btn').filter({ hasText: managementName }).click()
    await page.waitForURL(new RegExp(`/users/${activatedCustomer.customerUserId}(?:\\?.*)?$`))

    await page.locator('.owner-profile-section .settings-btn').filter({ hasText: 'تنظیمات کاربر' }).click()
    const adminModal = page.locator('.admin-user-modal')
    await expect(adminModal).toBeVisible({ timeout: 30000 })
    await expect(adminModal.locator('.customer-context-box')).toContainText(managementName)
    await expect(adminModal.locator('.customer-context-box')).toContainText(owner.accountName)
    await expect(adminModal.locator('.customer-context-box')).toContainText('سطح 2')
    await page.locator('.admin-user-modal-close').click()
    await expect(adminModal).toBeHidden({ timeout: 30000 })

    await setAuthTokens(page, owner)
    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({ timeout: 30000 })
    await page.locator('.owner-profile-section .settings-btn').filter({ hasText: 'مشتریان' }).click()
    await expect(modal).toBeVisible({ timeout: 30000 })

    page.once('dialog', (dialog) => dialog.accept())
    await modal.locator('.customer-card').filter({ hasText: managementName }).locator('button.unlink-active').click()

    await expect
      .poll(async () => (await fetchOwnerCustomerRelations(request, owner.accessToken)).length, { timeout: 30000 })
      .toBe(0)

    await expect
      .poll(() => inspectDeletedCustomer(activatedCustomer.customerUserId), { timeout: 30000 })
      .toMatchObject({
        relationStatus: 'deleted',
        userDeleted: true,
      })

    await expect(modal).toContainText('ارتباط مشتری قطع شد')
  })
})