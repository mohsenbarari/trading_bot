import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.invitations import (
    InvitationCreate,
    create_invitation,
    lookup_invitation,
    validate_invitation,
)
from models.user import UserRole


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.added = []

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, item):
        self.added.append(item)


class InvitationsRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_invitation_rejects_invalid_mobile_and_existing_user(self):
        admin = SimpleNamespace(id=1)

        with self.assertRaises(HTTPException) as exc_info:
            await create_invitation(
                InvitationCreate(account_name="user1", mobile_number="123", role=UserRole.WATCH),
                db=FakeDB(),
                admin=admin,
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شماره موبایل نامعتبر است")

        existing_user = SimpleNamespace(id=2)
        with self.assertRaises(HTTPException) as exc_info:
            await create_invitation(
                InvitationCreate(account_name="user1", mobile_number="09120000000", role=UserRole.WATCH),
                db=FakeDB([FakeExecuteResult(existing_user)]),
                admin=admin,
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است")

    async def test_create_invitation_returns_existing_active_invitation(self):
        admin = SimpleNamespace(id=1)
        active_invitation = SimpleNamespace(
            token="INV-EXISTING",
            short_code="SHORT123",
            expires_at=datetime.utcnow() + timedelta(days=2),
        )
        db = FakeDB([FakeExecuteResult(None), FakeExecuteResult(active_invitation)])

        with patch.object(__import__("api.routers.invitations", fromlist=["settings"]).settings, "bot_username", "test_bot"), patch.object(
            __import__("api.routers.invitations", fromlist=["settings"]).settings,
            "frontend_url",
            "https://frontend.test",
        ):
            result = await create_invitation(
                InvitationCreate(account_name="user1", mobile_number="09120000000", role=UserRole.WATCH),
                db=db,
                admin=admin,
            )

        self.assertEqual(result["token"], "INV-EXISTING")
        self.assertEqual(result["link"], "https://t.me/test_bot?start=INV-EXISTING")
        self.assertEqual(result["short_link"], "https://frontend.test/i/SHORT123")
        db.commit.assert_not_awaited()
        self.assertEqual(db.added, [])

    async def test_create_invitation_creates_new_record_and_sends_sms(self):
        admin = SimpleNamespace(id=9)
        db = FakeDB([FakeExecuteResult(None), FakeExecuteResult(None)])

        with patch("api.routers.invitations.generate_token", return_value="INV-NEW"), patch(
            "api.routers.invitations.generate_short_code",
            return_value="SHORTNEW",
        ), patch.object(
            __import__("api.routers.invitations", fromlist=["settings"]).settings,
            "bot_username",
            "test_bot",
        ), patch.object(
            __import__("api.routers.invitations", fromlist=["settings"]).settings,
            "frontend_url",
            "https://frontend.test",
        ), patch.object(
            __import__("api.routers.invitations", fromlist=["settings"]).settings,
            "invitation_expiry_days",
            3,
        ), patch(
            "api.routers.invitations.is_internet_connected",
            new=AsyncMock(return_value=True),
        ), patch("api.routers.invitations.send_invitation_sms") as send_sms_mock:
            result = await create_invitation(
                InvitationCreate(account_name="user1", mobile_number="09120000000", role=UserRole.STANDARD),
                db=db,
                admin=admin,
            )

        self.assertEqual(len(db.added), 1)
        invitation = db.added[0]
        self.assertEqual(invitation.account_name, "user1")
        self.assertEqual(invitation.mobile_number, "09120000000")
        self.assertEqual(invitation.role, UserRole.STANDARD)
        self.assertEqual(invitation.token, "INV-NEW")
        self.assertEqual(invitation.short_code, "SHORTNEW")
        self.assertEqual(invitation.created_by_id, 9)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(invitation)
        send_sms_mock.assert_called_once_with(
            mobile="09120000000",
            account_name="user1",
            bot_link="https://t.me/test_bot?start=INV-NEW",
            web_link="https://frontend.test/i/SHORTNEW",
        )
        self.assertEqual(result["token"], "INV-NEW")
        self.assertEqual(result["link"], "https://t.me/test_bot?start=INV-NEW")
        self.assertEqual(result["short_link"], "https://frontend.test/i/SHORTNEW")

    async def test_lookup_invitation_handles_error_states_and_success(self):
        with self.assertRaises(HTTPException) as exc_info:
            await lookup_invitation("SHORT", db=FakeDB([FakeExecuteResult(None)]))
        self.assertEqual(exc_info.exception.status_code, 404)

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

    async def test_validate_invitation_handles_error_states_and_success(self):
        with self.assertRaises(HTTPException) as exc_info:
            await validate_invitation("INV", db=FakeDB([FakeExecuteResult(None)]))
        self.assertEqual(exc_info.exception.status_code, 404)

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
                "mobile_number": "09120000000",
                "role": UserRole.WATCH,
            },
        )


if __name__ == "__main__":
    unittest.main()