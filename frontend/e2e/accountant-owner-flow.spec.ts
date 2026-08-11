/// <reference types="node" />

import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  getE2EBackendBaseUrl,
  runPythonInApp as runPythonInConfiguredApp,
} from './helpers/mutationRuntime'

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

interface CancelledAccountantRelationState {
  relationStatus: string | null
  relationDeleted: boolean
  invitationRevoked: boolean
  identityReservationReleased: boolean
}

interface AccountantDeletionCascadeFixture {
  sessionId: string
  offerId: number
  invitationId: number
  linkedRelationId: number
  tradeId: number
}

interface AccountantDeletionCascadeState {
  sessionActive: boolean | null
  offerStatus: string | null
  offerExpireReason: string | null
  invitationRevoked: boolean
  linkedRelationStatus: string | null
  linkedRelationDeleted: boolean
  tradeRetained: boolean
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

function activatePendingRelation(
  ownerUserId: number,
  globalAccountName: string,
): ActivatedAccountantPayload {
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

function inspectCancelledAccountantRelation(relationId: number): CancelledAccountantRelationState {
  return runPythonInApp<CancelledAccountantRelationState>(`
import asyncio
import json

from sqlalchemy import select

from core.db import AsyncSessionLocal
from models.accountant_relation import AccountantRelation
from models.invitation import Invitation
from models.invitation_identity_reservation import InvitationIdentityReservation

relation_id = ${JSON.stringify(relationId)}

async def main():
    async with AsyncSessionLocal() as db:
        relation = await db.get(AccountantRelation, relation_id)
        invitation = None
        identity_reservation = None
        if relation is not None:
            invitation = (await db.execute(
                select(Invitation).where(Invitation.token == relation.invitation_token)
            )).scalar_one_or_none()
        if invitation is not None:
            identity_reservation = (await db.execute(
                select(InvitationIdentityReservation.id).where(
                    InvitationIdentityReservation.invitation_id == invitation.id
                )
            )).scalar_one_or_none()

    print(json.dumps({
        'relationStatus': getattr(relation.status, 'value', None) if relation else None,
        'relationDeleted': bool(relation and relation.deleted_at),
        'invitationRevoked': bool(invitation and invitation.revoked_at),
        'identityReservationReleased': identity_reservation is None,
    }))

asyncio.run(main())
`)
}

function seedAccountantDeletionCascadeFixture(
  accountantUserId: number,
): AccountantDeletionCascadeFixture {
  return runPythonInApp<AccountantDeletionCascadeFixture>(`
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from core.db import AsyncSessionLocal
from core.enums import UserRole
from models.commodity import Commodity
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.invitation import Invitation, InvitationKind
from models.offer import Offer, OfferStatus, OfferType
from models.session import Platform, UserSession
from models.trade import Trade, TradeStatus, TradeType
from models.user import User

accountant_user_id = ${JSON.stringify(accountantUserId)}

def random_mobile():
    mobile_seed = int(uuid.uuid4().hex[:9], 16) % 1000000000
    return f"09{mobile_seed:09d}"

async def main():
    suffix = uuid.uuid4().hex[:10]
    async with AsyncSessionLocal() as db:
        accountant = await db.get(User, accountant_user_id)
        if accountant is None:
            raise RuntimeError('Accountant deletion target was not found')

        linked_owner = User(
            account_name=f"pw_accountant_delete_link_owner_{suffix}",
            mobile_number=random_mobile(),
            full_name='Playwright Accountant Deletion Link Owner',
            address='Playwright Accountant Deletion Link Owner',
            role=UserRole.STANDARD,
            has_bot_access=True,
            max_sessions=1,
        )
        commodity = Commodity(name=f"PW Accountant Deletion Commodity {suffix}")
        db.add_all([linked_owner, commodity])
        await db.flush()

        session = UserSession(
            user_id=accountant.id,
            device_name='Playwright Accountant Deletion Session',
            device_ip='127.0.0.1',
            platform=Platform.WEB,
            refresh_token_hash=f"pw-delete-{uuid.uuid4().hex}",
            is_primary=True,
            is_active=True,
            expires_at=None,
        )
        offer = Offer(
            user_id=accountant.id,
            actor_user_id=accountant.id,
            home_server='iran',
            offer_type=OfferType.SELL,
            commodity_id=commodity.id,
            quantity=3,
            remaining_quantity=3,
            price=543210,
            is_wholesale=True,
            lot_sizes=None,
            original_lot_sizes=None,
            status=OfferStatus.ACTIVE,
            notes='Playwright accountant deletion active offer',
        )
        invitation = Invitation(
            account_name=accountant.account_name,
            mobile_number=accountant.mobile_number,
            token=f"pw-accountant-delete-inv-{uuid.uuid4().hex}",
            short_code=uuid.uuid4().hex[:8],
            role=UserRole.STANDARD,
            kind=InvitationKind.STANDARD,
            created_by_id=linked_owner.id,
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(days=1),
        )
        linked_relation = CustomerRelation(
            owner_user_id=linked_owner.id,
            customer_user_id=accountant.id,
            created_by_user_id=linked_owner.id,
            invitation_token=f"pw-accountant-delete-customer-{uuid.uuid4().hex}",
            management_name=f"مشتری لینک‌شده {suffix[:4]}",
            customer_tier=CustomerTier.TIER_1,
            status=CustomerRelationStatus.ACTIVE,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            activated_at=datetime.now(timezone.utc),
        )
        trade = Trade(
            trade_number=1000000000 + accountant.id,
            offer_id=None,
            offer_user_id=accountant.id,
            offer_user_mobile=accountant.mobile_number,
            responder_user_id=linked_owner.id,
            responder_user_mobile=linked_owner.mobile_number,
            actor_user_id=accountant.id,
            commodity_id=commodity.id,
            trade_type=TradeType.BUY,
            quantity=2,
            price=654321,
            status=TradeStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
        )
        db.add_all([session, offer, invitation, linked_relation, trade])
        await db.flush()

        fixture = {
            'sessionId': str(session.id),
            'offerId': offer.id,
            'invitationId': invitation.id,
            'linkedRelationId': linked_relation.id,
            'tradeId': trade.id,
        }
        await db.commit()

    print(json.dumps(fixture))

asyncio.run(main())
`)
}

function inspectAccountantDeletionCascadeFixture(
  fixture: AccountantDeletionCascadeFixture,
): AccountantDeletionCascadeState {
  return runPythonInApp<AccountantDeletionCascadeState>(`
import asyncio
import json
import uuid

from core.db import AsyncSessionLocal
from models.customer_relation import CustomerRelation
from models.invitation import Invitation
from models.offer import Offer
from models.session import UserSession
from models.trade import Trade

session_id = ${JSON.stringify(fixture.sessionId)}
offer_id = ${JSON.stringify(fixture.offerId)}
invitation_id = ${JSON.stringify(fixture.invitationId)}
linked_relation_id = ${JSON.stringify(fixture.linkedRelationId)}
trade_id = ${JSON.stringify(fixture.tradeId)}

async def main():
    async with AsyncSessionLocal() as db:
        session = await db.get(UserSession, uuid.UUID(session_id))
        offer = await db.get(Offer, offer_id)
        invitation = await db.get(Invitation, invitation_id)
        linked_relation = await db.get(CustomerRelation, linked_relation_id)
        trade = await db.get(Trade, trade_id)

    print(json.dumps({
        'sessionActive': session.is_active if session else None,
        'offerStatus': getattr(offer.status, 'value', None) if offer else None,
        'offerExpireReason': offer.expire_reason if offer else None,
        'invitationRevoked': bool(invitation and invitation.revoked_at),
        'linkedRelationStatus': getattr(linked_relation.status, 'value', None) if linked_relation else None,
        'linkedRelationDeleted': bool(linked_relation and linked_relation.deleted_at),
        'tradeRetained': trade is not None,
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
    .poll(
      async () => {
        try {
          const response = await request.get(`${BACKEND_BASE_URL}/api/config`)
          return response.ok()
        } catch {
          return false
        }
      },
      { timeout: 60000 },
    )
    .toBe(true)
}

async function setAuthTokens(page: Page, session: SessionUser) {
  await page.goto('/login')
  await page.evaluate(
    ({ accessToken, refreshToken }) => {
      localStorage.setItem('auth_token', accessToken)
      localStorage.setItem('refresh_token', refreshToken)
      localStorage.removeItem('suspended_refresh_token')
    },
    {
      accessToken: session.accessToken,
      refreshToken: session.refreshToken,
    },
  )
}

async function fetchOwnerRelations(
  request: APIRequestContext,
  accessToken: string,
): Promise<OwnerRelationPayload[]> {
  const response = await request.get(`${BACKEND_BASE_URL}/api/accountants/owner-relations`, {
    headers: authHeaders(accessToken),
  })

  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<OwnerRelationPayload[]>
}

test.describe('accountant owner lifecycle', () => {
  test('owner can create pending accountant, observe activation, view it, and delete its active account', async ({
    page,
    request,
  }) => {
    test.setTimeout(180000)

    const owner = seedSessionUser('accountant_owner_flow')
    const suffix = Date.now()
    const globalAccountName = `pwacct_${suffix}`
    const relationDisplayName = `حسابدار تست ${suffix}`
    const dutyDescription = `پیگیری تستی ${suffix}`
    const mobileNumber = `09${String(suffix).slice(-9)}`
    const cancelledGlobalAccountName = `pwacct_cancel_${suffix}`
    const cancelledRelationDisplayName = `حسابدار لغوشونده ${suffix}`
    const cancelledMobileNumber = `09${String(suffix + 1).slice(-9)}`

    await waitForBackendReady(request)
    await setAuthTokens(page, owner)

    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({
      timeout: 30000,
    })

    await page
      .locator('.owner-profile-section .settings-btn')
      .filter({ hasText: 'حسابداران' })
      .click()
    const workspace = page.locator('.accountant-workspace-view')
    await expect(workspace).toBeVisible({ timeout: 30000 })

    await workspace
      .getByRole('button', { name: /افزودن حسابدار/ })
      .first()
      .click()
    const createPanel = page.locator('.accountant-create-panel')
    await expect(createPanel).toBeVisible({ timeout: 30000 })

    await createPanel.getByPlaceholder('مثلاً accountant_01').fill(cancelledGlobalAccountName)
    await createPanel.getByPlaceholder('مثلاً حسابدار فروش').fill(cancelledRelationDisplayName)
    await createPanel.getByPlaceholder('0912xxxxxxx').fill(cancelledMobileNumber)
    await createPanel
      .getByPlaceholder('مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه')
      .fill('دعوتی که باید لغو شود')
    await createPanel.getByRole('button', { name: 'ثبت دعوت حسابدار', exact: true }).click()

    let cancelledPendingRelation: OwnerRelationPayload | null = null
    await expect
      .poll(
        async () => {
          const relations = await fetchOwnerRelations(request, owner.accessToken)
          cancelledPendingRelation =
            relations.find(
              (relation) => relation.global_account_name === cancelledGlobalAccountName,
            ) ?? null
          return cancelledPendingRelation?.status ?? null
        },
        { timeout: 30000 },
      )
      .toBe('pending')

    const cancelledRelationId = Number(cancelledPendingRelation?.id)
    expect(Number.isInteger(cancelledRelationId)).toBe(true)
    expect(
      inspectCancelledAccountantRelation(cancelledRelationId).identityReservationReleased,
    ).toBe(false)
    const cancelledPendingCard = workspace
      .locator('.accountant-pending-card')
      .filter({ hasText: cancelledRelationDisplayName })
    await expect(cancelledPendingCard).toContainText('دعوت', { timeout: 30000 })
    await cancelledPendingCard.getByRole('button', { name: 'لغو دعوت', exact: true }).click()

    const cancellationDialog = page.getByRole('dialog', {
      name: 'لغو رابطه و دعوت حسابدار',
      exact: true,
    })
    await expect(cancellationDialog).toBeVisible()
    await expect(
      cancellationDialog.getByText(
        `رابطه و دعوت در انتظار «${cancelledRelationDisplayName}» لغو شود؟ لینک ثبت‌نام بی‌اعتبار و رزرو هویت و نام کاربری آزاد می‌شود. چون حسابی فعال نشده، حذف زنجیره‌ای حساب، نشست، آفر یا روابط فعال اجرا نمی‌شود.`,
        { exact: true },
      ),
    ).toBeVisible()
    await cancellationDialog.getByRole('button', { name: 'لغو رابطه و دعوت', exact: true }).click()

    await expect
      .poll(() => inspectCancelledAccountantRelation(cancelledRelationId), { timeout: 30000 })
      .toEqual({
        relationStatus: 'revoked',
        relationDeleted: true,
        invitationRevoked: true,
        identityReservationReleased: true,
      })
    await expect(cancelledPendingCard).toBeHidden({ timeout: 30000 })

    await workspace
      .getByRole('button', { name: /افزودن حسابدار/ })
      .first()
      .click()
    await expect(createPanel).toBeVisible({ timeout: 30000 })
    await createPanel.getByPlaceholder('مثلاً accountant_01').fill(globalAccountName)
    await createPanel.getByPlaceholder('مثلاً حسابدار فروش').fill(relationDisplayName)
    await createPanel.getByPlaceholder('0912xxxxxxx').fill(mobileNumber)
    await createPanel
      .getByPlaceholder('مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه')
      .fill(dutyDescription)
    await createPanel.getByRole('button', { name: 'ثبت دعوت حسابدار', exact: true }).click()

    let pendingRelation: OwnerRelationPayload | null = null
    await expect
      .poll(
        async () => {
          const relations = await fetchOwnerRelations(request, owner.accessToken)
          pendingRelation =
            relations.find((relation) => relation.global_account_name === globalAccountName) ?? null
          return pendingRelation?.status ?? null
        },
        { timeout: 30000 },
      )
      .toBe('pending')

    expect(pendingRelation?.registration_link).toContain('/register')
    await expect(workspace).toContainText(relationDisplayName, { timeout: 30000 })
    await expect(workspace).toContainText('دعوت', { timeout: 30000 })

    const activatedAccountant = activatePendingRelation(owner.userId, globalAccountName)

    await page.reload()
    await expect(workspace).toBeVisible({ timeout: 30000 })
    await expect
      .poll(
        async () => {
          const relations = await fetchOwnerRelations(request, owner.accessToken)
          return relations.find((relation) => relation.id === pendingRelation?.id)?.status ?? null
        },
        { timeout: 30000 },
      )
      .toBe('active')

    await expect(workspace).toContainText('فعال', { timeout: 30000 })
    await expect(workspace).toContainText(relationDisplayName, { timeout: 30000 })
    await expect(workspace).toContainText(dutyDescription, { timeout: 30000 })

    await page.goto(`/users/${owner.userId}`)
    await expect(page.locator('.public-profile-view .profile-content')).toBeVisible({
      timeout: 30000,
    })
    await expect(page.locator('.accountant-relations-section')).toContainText(relationDisplayName)
    await expect(page.locator('.accountant-relations-section')).toContainText(dutyDescription)

    await page
      .locator('.owner-profile-section .settings-btn')
      .filter({ hasText: 'حسابداران' })
      .click()
    await expect(workspace).toBeVisible({ timeout: 30000 })

    await workspace
      .locator('.ui-list-item')
      .filter({ hasText: relationDisplayName })
      .first()
      .click()
    await expect(page).toHaveURL(
      new RegExp(`/operations/accountants/${pendingRelation?.id}(?:\\?.*)?$`),
    )
    const deletionCascadeFixture = seedAccountantDeletionCascadeFixture(
      activatedAccountant.accountantUserId,
    )
    await workspace.getByRole('tab', { name: 'حساس' }).click()
    await workspace.getByRole('button', { name: 'حذف حساب', exact: true }).click()

    const deletionDialog = page.getByRole('dialog', {
      name: `حذف حساب ${relationDisplayName}`,
      exact: true,
    })
    await expect(deletionDialog).toBeVisible()
    await expect(
      deletionDialog.getByText('این اقدام فقط قطع یک رابطه نیست و بازگشت خودکار ندارد.', {
        exact: true,
      }),
    ).toBeVisible()

    const deletionConsequences = [
      'دسترسی وب‌اپ و ربات قطع می‌شود.',
      'همه نشست‌های فعال پایان می‌یابند.',
      'آفرهای فعال منقضی می‌شوند.',
      'دعوت‌های در انتظار مرتبط لغو می‌شوند.',
      'همه روابط باز مشتری و حسابدارِ متعلق یا لینک‌شده بسته می‌شوند.',
      'حساب‌های فعال وابسته‌ای که این کاربر مالک آن‌هاست ممکن است به‌صورت بازگشتی حذف شوند.',
      'سوابق معاملات حذف نمی‌شوند.',
    ]
    for (const consequence of deletionConsequences) {
      await expect(deletionDialog.getByText(consequence, { exact: true })).toBeVisible()
    }

    const confirmDeletionButton = deletionDialog.getByRole('button', {
      name: 'حذف حساب و قطع ارتباط',
      exact: true,
    })
    await expect(confirmDeletionButton).toBeDisabled()
    await deletionDialog
      .getByRole('textbox', {
        name: `برای تأیید، نام نمایش‌داده‌شده «${relationDisplayName}» را وارد کنید.`,
        exact: true,
      })
      .fill(relationDisplayName)
    await deletionDialog
      .getByRole('checkbox', { name: 'پیامدهای بالا را خواندم و تأیید می‌کنم.', exact: true })
      .check()
    await expect(confirmDeletionButton).toBeEnabled()
    await confirmDeletionButton.click()

    await expect
      .poll(async () => (await fetchOwnerRelations(request, owner.accessToken)).length, {
        timeout: 30000,
      })
      .toBe(0)

    await expect
      .poll(() => inspectDeletedAccountant(activatedAccountant.accountantUserId), {
        timeout: 30000,
      })
      .toMatchObject({
        relationStatus: 'deleted',
        userDeleted: true,
      })

    await expect
      .poll(() => inspectAccountantDeletionCascadeFixture(deletionCascadeFixture), {
        timeout: 30000,
      })
      .toEqual({
        sessionActive: false,
        offerStatus: 'expired',
        offerExpireReason: 'user_deleted',
        invitationRevoked: true,
        linkedRelationStatus: 'deleted',
        linkedRelationDeleted: true,
        tradeRetained: true,
      })

    await page.goto('/operations/accountants')
    await expect(workspace).toBeVisible({ timeout: 30000 })
    await expect(workspace).toContainText('هنوز حسابداری ثبت نشده است', { timeout: 30000 })
  })
})
