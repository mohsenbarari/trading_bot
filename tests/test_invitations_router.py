import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.invitations import (
    InvitationCreate,
    create_invitation,
    delete_pending_invitation,
    generate_short_code,
    generate_token,
    list_pending_invitations,
    lookup_invitation,
    validate_invitation,
)
from models.invitation import InvitationKind
from models.user import UserRole
from core.services.canonical_invitation_creation_service import CanonicalInvitationCreationError


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = AsyncMock()
        self.added = []

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, item):
        self.added.append(item)


class InvitationsRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.public_url_patcher = patch(
            "api.routers.invitations.public_webapp_url_for_links",
            return_value="https://frontend.test",
        )
        self.public_url_patcher.start()
        self.addCleanup(self.public_url_patcher.stop)

    def test_generate_token_and_short_code_shapes(self):
        token = generate_token()
        short_code = generate_short_code()

        self.assertTrue(token.startswith("INV-"))
        self.assertEqual(len(token), 36)
        self.assertEqual(len(short_code), 8)
        self.assertTrue(short_code.isalnum())

    async def test_create_invitation_rejects_invalid_mobile_and_existing_user(self):
        admin = SimpleNamespace(id=1)

        with patch(
            "api.routers.invitations.create_or_reuse_canonical_invitation",
            new=AsyncMock(side_effect=CanonicalInvitationCreationError("invalid_identity")),
        ), self.assertRaises(HTTPException) as exc_info:
            await create_invitation(InvitationCreate(account_name="user1", mobile_number="123"), db=FakeDB(), admin=admin)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("نام کاربری", exc_info.exception.detail)

        with patch(
            "api.routers.invitations.create_or_reuse_canonical_invitation",
            new=AsyncMock(side_effect=CanonicalInvitationCreationError("user_identity_exists")),
        ), self.assertRaises(HTTPException) as exc_info:
            await create_invitation(
                InvitationCreate(account_name="user1", mobile_number="09120000000", role=UserRole.WATCH),
                db=FakeDB(),
                admin=admin,
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است")

    async def test_middle_manager_can_only_invite_watch_or_standard(self):
        admin = SimpleNamespace(id=1, role=UserRole.MIDDLE_MANAGER)

        with self.assertRaises(HTTPException) as exc_info:
            await create_invitation(
                InvitationCreate(account_name="user1", mobile_number="09120000000", role=UserRole.POLICE),
                db=FakeDB(),
                admin=admin,
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "مدیر میانی فقط می‌تواند کاربران عادی یا تماشا را دعوت کند")

    async def test_create_invitation_returns_existing_active_invitation(self):
        admin = SimpleNamespace(id=1)
        active_invitation = SimpleNamespace(
            id=2,
            token="INV-EXISTING",
            short_code="SHORT123",
            expires_at=datetime.utcnow() + timedelta(days=2),
            kind=InvitationKind.STANDARD,
            role=UserRole.STANDARD,
            is_used=False,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
            revoked_at=None,
        )
        db = FakeDB()

        with patch.object(__import__("api.routers.invitations", fromlist=["settings"]).settings, "bot_username", "test_bot"), patch(
            "core.invitation_contract_service.public_webapp_url_for_links",
            return_value="https://frontend.test",
        ), patch(
            "api.routers.invitations.create_or_reuse_canonical_invitation",
            new=AsyncMock(return_value=SimpleNamespace(invitation=active_invitation, created=False)),
        ):
            result = await create_invitation(
                InvitationCreate(account_name="user1", mobile_number="09120000000", role=UserRole.STANDARD),
                db=db,
                admin=admin,
            )

        self.assertEqual(result["token"], "INV-EXISTING")
        self.assertEqual(result["link"], "https://t.me/test_bot?start=INV-EXISTING")
        self.assertEqual(result["short_link"], "https://frontend.test/i/SHORT123")
        db.commit.assert_awaited_once()
        self.assertEqual(db.added, [])

    async def test_create_invitation_creates_new_record_and_sends_sms(self):
        admin = SimpleNamespace(id=9)
        db = FakeDB()
        expected_expiry = datetime.utcnow() + timedelta(days=2)
        invitation = SimpleNamespace(
            id=3,
            account_name="user1",
            mobile_number="09120000000",
            role=UserRole.STANDARD,
            kind=InvitationKind.STANDARD,
            token="INV-NEW",
            short_code="SHORTNEW",
            created_by_id=9,
            expires_at=expected_expiry,
            is_used=False,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
            revoked_at=None,
        )

        with patch.object(
            __import__("api.routers.invitations", fromlist=["settings"]).settings,
            "bot_username",
            "test_bot",
        ), patch(
            "core.invitation_contract_service.public_webapp_url_for_links",
            return_value="https://frontend.test",
        ), patch(
            "api.routers.invitations.create_or_reuse_canonical_invitation",
            new=AsyncMock(return_value=SimpleNamespace(invitation=invitation, created=True)),
        ), patch("api.routers.invitations.invitation_sms_enabled", return_value=True), patch(
            "api.routers.invitations.send_invitation_sms", return_value=True
        ) as send_sms_mock:
            result = await create_invitation(
                InvitationCreate(account_name="user1", mobile_number="09120000000", role=UserRole.STANDARD),
                db=db,
                admin=admin,
            )

        self.assertEqual(len(db.added), 0)
        self.assertEqual(invitation.account_name, "user1")
        self.assertEqual(invitation.mobile_number, "09120000000")
        self.assertEqual(invitation.role, UserRole.STANDARD)
        self.assertEqual(invitation.kind, InvitationKind.STANDARD)
        self.assertEqual(invitation.token, "INV-NEW")
        self.assertEqual(invitation.short_code, "SHORTNEW")
        self.assertEqual(invitation.created_by_id, 9)
        self.assertEqual(invitation.expires_at, expected_expiry)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(invitation)
        send_sms_mock.assert_called_once_with(
            mobile="09120000000",
            account_name="user1",
            bot_link="https://t.me/test_bot?start=INV-NEW",
            web_link="https://frontend.test/register?token=INV-NEW",
        )
        self.assertEqual(result["token"], "INV-NEW")
        self.assertEqual(result["link"], "https://t.me/test_bot?start=INV-NEW")
        self.assertEqual(result["short_link"], "https://frontend.test/i/SHORTNEW")
        self.assertEqual(result["expires_at"], expected_expiry)

    async def test_list_pending_invitations_serializes_active_general_invites(self):
        admin = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        invitation = SimpleNamespace(
            id=7,
            account_name="user1",
            mobile_number="09120000000",
            role=UserRole.WATCH,
            kind=InvitationKind.STANDARD,
            token="INV-PENDING",
            short_code="SHORT7",
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(hours=1),
            created_at=datetime.utcnow(),
            created_by_id=1,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
            revoked_at=None,
        )
        db = FakeDB([FakeExecuteResult([invitation])])

        with patch.object(
            __import__("api.routers.invitations", fromlist=["settings"]).settings,
            "bot_username",
            "test_bot",
        ), patch(
            "core.invitation_contract_service.public_webapp_url_for_links",
            return_value="https://frontend.test",
        ):
            result = await list_pending_invitations(db=db, admin=admin)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 7)
        self.assertEqual(result[0]["account_name"], "user1")
        self.assertEqual(result[0]["web_link"], "https://frontend.test/register?token=INV-PENDING")
        self.assertEqual(result[0]["short_link"], "https://frontend.test/i/SHORT7")

    async def test_delete_pending_invitation_soft_revokes_only_manageable_pending_general_invites(self):
        admin = SimpleNamespace(id=9, role=UserRole.MIDDLE_MANAGER)
        invitation = SimpleNamespace(
            id=3,
            token="INV-DELETE",
            created_by_id=9,
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db = FakeDB([FakeExecuteResult(None)])

        with patch(
            "api.routers.invitations.lock_invitation_for_transition",
            new=AsyncMock(return_value=invitation),
        ):
            result = await delete_pending_invitation(3, db=db, admin=admin)

        self.assertIsNone(result)
        db.delete.assert_not_awaited()
        self.assertIsNotNone(invitation.revoked_at)
        self.assertEqual(db.execute_results, [])
        db.commit.assert_awaited_once()

        used_invitation = SimpleNamespace(
            id=4,
            token="INV-USED",
            created_by_id=9,
            is_used=True,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        with patch(
            "api.routers.invitations.lock_invitation_for_transition",
            new=AsyncMock(return_value=used_invitation),
        ), self.assertRaises(HTTPException) as exc_info:
            await delete_pending_invitation(4, db=FakeDB(), admin=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        foreign_invitation = SimpleNamespace(
            id=5,
            token="INV-OTHER",
            created_by_id=99,
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        with patch(
            "api.routers.invitations.lock_invitation_for_transition",
            new=AsyncMock(return_value=foreign_invitation),
        ), self.assertRaises(HTTPException) as exc_info:
            await delete_pending_invitation(5, db=FakeDB(), admin=admin)
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_lookup_invitation_handles_error_states_and_success(self):
        with self.assertRaises(HTTPException) as exc_info:
            await lookup_invitation("SHORT", db=FakeDB([FakeExecuteResult(None)]))
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(
            exc_info.exception.headers["Cache-Control"],
            "no-store, max-age=0",
        )

        used = SimpleNamespace(is_used=True, expires_at=datetime.utcnow() + timedelta(minutes=5), token="INV")
        with self.assertRaises(HTTPException) as exc_info:
            await lookup_invitation("SHORT", db=FakeDB([FakeExecuteResult(used)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Invitation already used")

        expired = SimpleNamespace(is_used=False, expires_at=datetime.utcnow() - timedelta(minutes=5), token="INV")
        with self.assertRaises(HTTPException) as exc_info:
            await lookup_invitation("SHORT", db=FakeDB([FakeExecuteResult(expired)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Invitation expired")

        valid = SimpleNamespace(is_used=False, expires_at=datetime.utcnow() + timedelta(minutes=5), token="INV-OK")
        result = await lookup_invitation("SHORT", db=FakeDB([FakeExecuteResult(valid)]))
        self.assertEqual(result, {"token": "INV-OK"})

    async def test_lookup_and_validate_invitation_respect_accountant_pending_relation(self):
        accountant_invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            token="ACCT-OK",
            account_name="acc1",
            mobile_number="09120000000",
            role=UserRole.WATCH,
        )

        with patch(
            "api.routers.invitations.get_pending_accountant_relation_by_invitation_token",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await lookup_invitation("SHORT", db=FakeDB([FakeExecuteResult(accountant_invitation)]))
        self.assertEqual(exc_info.exception.status_code, 400)

        with patch(
            "api.routers.invitations.get_pending_accountant_relation_by_invitation_token",
            new=AsyncMock(return_value=SimpleNamespace(id=1)),
        ):
            result = await validate_invitation("ACCT-OK", db=FakeDB([FakeExecuteResult(accountant_invitation)]))
        self.assertTrue(result["valid"])

        with patch(
            "api.routers.invitations.get_pending_accountant_relation_by_invitation_token",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await validate_invitation("ACCT-OK", db=FakeDB([FakeExecuteResult(accountant_invitation)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Invitation expired")

    async def test_lookup_and_validate_invitation_respect_customer_pending_relation(self):
        customer_invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            token="CUST-OK",
            account_name="cust1",
            mobile_number="09120000000",
            role=UserRole.STANDARD,
        )

        with patch(
            "api.routers.invitations.get_pending_customer_relation_by_invitation_token",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await lookup_invitation("SHORT", db=FakeDB([FakeExecuteResult(customer_invitation)]))
        self.assertEqual(exc_info.exception.status_code, 400)

        with patch(
            "api.routers.invitations.get_pending_customer_relation_by_invitation_token",
            new=AsyncMock(return_value=SimpleNamespace(id=1)),
        ):
            result = await validate_invitation("CUST-OK", db=FakeDB([FakeExecuteResult(customer_invitation)]))
        self.assertTrue(result["valid"])

        with patch(
            "api.routers.invitations.get_pending_customer_relation_by_invitation_token",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await validate_invitation("CUST-OK", db=FakeDB([FakeExecuteResult(customer_invitation)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Invitation expired")

    async def test_validate_invitation_handles_error_states_and_success(self):
        with self.assertRaises(HTTPException) as exc_info:
            await validate_invitation("INV", db=FakeDB([FakeExecuteResult(None)]))
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(
            exc_info.exception.headers["Cache-Control"],
            "no-store, max-age=0",
        )

        used = SimpleNamespace(is_used=True, expires_at=datetime.utcnow() + timedelta(minutes=5))
        with self.assertRaises(HTTPException) as exc_info:
            await validate_invitation("INV", db=FakeDB([FakeExecuteResult(used)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Invitation already used")

        expired = SimpleNamespace(is_used=False, expires_at=datetime.utcnow() - timedelta(minutes=5))
        with self.assertRaises(HTTPException) as exc_info:
            await validate_invitation("INV", db=FakeDB([FakeExecuteResult(expired)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Invitation expired")

        valid = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            account_name="user1",
            mobile_number="09120000000",
            role=UserRole.WATCH,
        )
        result = await validate_invitation("INV", db=FakeDB([FakeExecuteResult(valid)]))
        self.assertEqual(
            result,
            {
                "valid": True,
                "account_name": "user1",
                "mobile_number": "0912****000",
                "role": UserRole.WATCH,
            },
        )


if __name__ == "__main__":
    unittest.main()
