"""Hermetic direct characterization for list_my_accountant_sessions endpoint.

Seed: API-IDENTITY-GET-ACCOUNTANTS-LIST-MY-ACCOUNTANT-SESSIONS-L280
Target: api.routers.accountants.list_my_accountant_sessions

Note: This unit provides direct characterization for the currently
unexecuted seed API-IDENTITY-GET-ACCOUNTANTS-LIST-MY-ACCOUNTANT-SESSIONS-L280
under P1-00b network isolation. It does NOT add this seed to
any executed-seed registry, reduce the canonical remaining-seed blocker,
or mark P1-00b complete; evidence promotion is a separate unit.
"""

from __future__ import annotations

import datetime
import enum
import unittest
import uuid
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.accountants import (
    list_my_accountant_sessions,
    serialize_accountant_session,
)
from models.accountant_relation import AccountantRelationStatus


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAccountantSessionsListCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for API-IDENTITY-GET-ACCOUNTANTS-LIST-MY-ACCOUNTANT-SESSIONS-L280."""

    async def test_list_my_accountant_sessions_success(self) -> None:
        owner_id = 42
        relation_id = 7
        accountant_id = 99
        created_dt = datetime.datetime(2026, 9, 5, 10, 0, 0, tzinfo=datetime.timezone.utc)
        last_active_dt = datetime.datetime(2026, 9, 5, 11, 0, 0, tzinfo=datetime.timezone.utc)

        class PlatformEnum(enum.Enum):
            WEB = "web"

        session_1 = mock.Mock(
            id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            device_name="Desktop Firefox",
            device_ip="192.0.2.10",
            platform=PlatformEnum.WEB,
            home_server="eu-central-node",
            is_primary=True,
            is_active=True,
            created_at=created_dt,
            last_active_at=last_active_dt,
        )
        session_2 = mock.Mock(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            device_name="Mobile Android",
            device_ip="192.0.2.20",
            platform="android_app",
            home_server=None,
            is_primary=False,
            is_active=True,
            created_at=created_dt,
            last_active_at=last_active_dt,
        )

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
            status=AccountantRelationStatus.ACTIVE,
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=relation,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_sessions",
            new_callable=mock.AsyncMock,
            return_value=[session_1, session_2],
        ) as mock_get_sessions, mock.patch(
            "api.routers.accountants.serialize_accountant_session",
            wraps=serialize_accountant_session,
        ) as mock_serialize:
            result = await list_my_accountant_sessions(
                relation_id=relation_id,
                context=context,
                db=fake_db,
            )

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_sessions.assert_awaited_once_with(fake_db, accountant_id)
        self.assertEqual(mock_serialize.call_count, 2)

        self.assertEqual(len(result), 2)
        # Verify session 1: platform enum normalization and home_server preserved
        self.assertEqual(result[0].id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(result[0].device_name, "Desktop Firefox")
        self.assertEqual(result[0].device_ip, "192.0.2.10")
        self.assertEqual(result[0].platform, "web")
        self.assertEqual(result[0].home_server, "eu-central-node")
        self.assertTrue(result[0].is_primary)
        self.assertTrue(result[0].is_active)
        self.assertEqual(result[0].created_at, created_dt)
        self.assertEqual(result[0].last_active_at, last_active_dt)

        # Verify session 2: platform str normalization and foreign home_server fallback
        self.assertEqual(result[1].id, "22222222-2222-2222-2222-222222222222")
        self.assertEqual(result[1].device_name, "Mobile Android")
        self.assertEqual(result[1].device_ip, "192.0.2.20")
        self.assertEqual(result[1].platform, "android_app")
        self.assertEqual(result[1].home_server, "foreign")
        self.assertFalse(result[1].is_primary)
        self.assertTrue(result[1].is_active)
        self.assertEqual(result[1].created_at, created_dt)
        self.assertEqual(result[1].last_active_at, last_active_dt)

    async def test_list_my_accountant_sessions_empty_sessions(self) -> None:
        owner_id = 42
        relation_id = 7
        accountant_id = 99

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
            status=AccountantRelationStatus.ACTIVE,
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=relation,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_sessions",
            new_callable=mock.AsyncMock,
            return_value=[],
        ) as mock_get_sessions, mock.patch(
            "api.routers.accountants.serialize_accountant_session",
            wraps=serialize_accountant_session,
        ) as mock_serialize:
            result = await list_my_accountant_sessions(
                relation_id=relation_id,
                context=context,
                db=fake_db,
            )

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_sessions.assert_awaited_once_with(fake_db, accountant_id)
        mock_serialize.assert_not_called()
        self.assertEqual(result, [])

    async def test_list_my_accountant_sessions_owner_context_denial(self) -> None:
        context = mock.Mock(
            is_accountant_context=True,
            actor_user=None,
            owner_user=mock.Mock(id=42, is_customer=False),
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=403,
                detail="Accountants cannot manage owner accountants",
            ),
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_sessions",
            new_callable=mock.AsyncMock,
        ) as mock_get_sessions, mock.patch(
            "api.routers.accountants.serialize_accountant_session",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await list_my_accountant_sessions(
                    relation_id=7,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Accountants cannot manage owner accountants")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_not_called()
        mock_get_sessions.assert_not_called()
        mock_serialize.assert_not_called()

    async def test_list_my_accountant_sessions_relation_not_found(self) -> None:
        owner_id = 42
        relation_id = 999
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=404,
                detail="رابطه حسابدار یافت نشد",
            ),
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_sessions",
            new_callable=mock.AsyncMock,
        ) as mock_get_sessions, mock.patch(
            "api.routers.accountants.serialize_accountant_session",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await list_my_accountant_sessions(
                    relation_id=relation_id,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "رابطه حسابدار یافت نشد")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_sessions.assert_not_called()
        mock_serialize.assert_not_called()

    async def test_list_my_accountant_sessions_relation_not_active(self) -> None:
        owner_id = 42
        relation_id = 8
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=400,
                detail="فقط حسابدار فعال نشست قابل مدیریت دارد",
            ),
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_sessions",
            new_callable=mock.AsyncMock,
        ) as mock_get_sessions, mock.patch(
            "api.routers.accountants.serialize_accountant_session",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await list_my_accountant_sessions(
                    relation_id=relation_id,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "فقط حسابدار فعال نشست قابل مدیریت دارد")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_sessions.assert_not_called()
        mock_serialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
