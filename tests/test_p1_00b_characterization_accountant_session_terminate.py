"""Hermetic direct characterization for terminate_my_accountant_session endpoint.

Target: api.routers.accountants.terminate_my_accountant_session

Note: This module provides direct characterization only and does not update
an executed-seed registry, reduce a canonical blocker, promote evidence,
or mark P1-00b complete; evidence promotion is a separate unit.
"""

from __future__ import annotations

import enum
import unittest
import uuid
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.accountants import terminate_my_accountant_session


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAccountantSessionTerminateCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for terminate_my_accountant_session."""

    async def test_terminate_my_accountant_session_success_with_promoted_primary(self) -> None:
        owner_id = 42
        relation_id = 7
        accountant_id = 99
        raw_session_id = "11111111-1111-1111-1111-111111111111".upper()
        session_uuid = uuid.UUID(raw_session_id)
        promoted_uuid = uuid.UUID("22222222-2222-2222-2222-222222222222")

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
        )
        target_session = mock.Mock(id=session_uuid)
        promoted_session = mock.Mock(id=promoted_uuid)
        fake_db = FakeAsyncSession()

        call_order: list[str] = []

        async def _fake_guard(*args, **kwargs):
            call_order.append("ensure_owner_context")

        async def _fake_get_relation(*args, **kwargs):
            call_order.append("get_active_owner_accountant_relation")
            return relation

        async def _fake_get_session(*args, **kwargs):
            call_order.append("get_active_accountant_session")
            return target_session

        async def _fake_logout(*args, **kwargs):
            call_order.append("logout_session")
            return promoted_session

        def _fake_audit(*args, **kwargs):
            call_order.append("audit_log")

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=_fake_guard,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_relation,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_accountant_session",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_session,
        ) as mock_get_session, mock.patch(
            "api.routers.accountants.logout_session",
            new_callable=mock.AsyncMock,
            side_effect=_fake_logout,
        ) as mock_logout, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=_fake_audit,
        ) as mock_audit:
            result = await terminate_my_accountant_session(
                relation_id=relation_id,
                session_id=raw_session_id,
                context=context,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "get_active_owner_accountant_relation",
                "get_active_accountant_session",
                "logout_session",
                "audit_log",
            ],
        )
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_session.assert_awaited_once_with(
            fake_db,
            accountant_user_id=accountant_id,
            session_id=session_uuid,
        )
        passed_session_id = mock_get_session.await_args.kwargs["session_id"]
        self.assertIsInstance(passed_session_id, uuid.UUID)
        self.assertEqual(passed_session_id, session_uuid)

        mock_logout.assert_awaited_once_with(fake_db, target_session)
        mock_audit.assert_called_once_with(
            "accountant.session_terminate",
            target_type="accountant_session",
            target_id=session_uuid,
            after_summary={
                "relation_id": relation_id,
                "accountant_user_id": accountant_id,
                "promoted_primary_session_id": str(promoted_uuid),
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        self.assertEqual(
            result,
            {
                "detail": "نشست حسابدار با موفقیت پایان یافت",
                "terminated_session_id": str(session_uuid),
                "promoted_primary_session_id": str(promoted_uuid),
            },
        )

    async def test_terminate_my_accountant_session_success_without_promoted_primary(self) -> None:
        owner_id = 42
        relation_id = 7
        accountant_id = 99
        session_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")

        class RoleEnum(enum.Enum):
            OWNER = "owner"

        actor = mock.Mock(id=owner_id, role=RoleEnum.OWNER)
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=actor,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
        )
        target_session = mock.Mock(id=session_uuid)
        fake_db = FakeAsyncSession()

        call_order: list[str] = []

        async def _fake_guard(*args, **kwargs):
            call_order.append("ensure_owner_context")

        async def _fake_get_relation(*args, **kwargs):
            call_order.append("get_active_owner_accountant_relation")
            return relation

        async def _fake_get_session(*args, **kwargs):
            call_order.append("get_active_accountant_session")
            return target_session

        async def _fake_logout(*args, **kwargs):
            call_order.append("logout_session")
            return None

        def _fake_audit(*args, **kwargs):
            call_order.append("audit_log")

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=_fake_guard,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_relation,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_accountant_session",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_session,
        ) as mock_get_session, mock.patch(
            "api.routers.accountants.logout_session",
            new_callable=mock.AsyncMock,
            side_effect=_fake_logout,
        ) as mock_logout, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=_fake_audit,
        ) as mock_audit:
            result = await terminate_my_accountant_session(
                relation_id=relation_id,
                session_id=str(session_uuid),
                context=context,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "get_active_owner_accountant_relation",
                "get_active_accountant_session",
                "logout_session",
                "audit_log",
            ],
        )
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_session.assert_awaited_once_with(
            fake_db,
            accountant_user_id=accountant_id,
            session_id=session_uuid,
        )
        mock_logout.assert_awaited_once_with(fake_db, target_session)
        mock_audit.assert_called_once_with(
            "accountant.session_terminate",
            target_type="accountant_session",
            target_id=session_uuid,
            after_summary={
                "relation_id": relation_id,
                "accountant_user_id": accountant_id,
                "promoted_primary_session_id": None,
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        self.assertEqual(
            result,
            {
                "detail": "نشست حسابدار با موفقیت پایان یافت",
                "terminated_session_id": str(session_uuid),
                "promoted_primary_session_id": None,
            },
        )

    async def test_terminate_my_accountant_session_invalid_session_id(self) -> None:
        owner_id = 42
        relation_id = 7
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_accountant_session",
            new_callable=mock.AsyncMock,
        ) as mock_get_session, mock.patch(
            "api.routers.accountants.logout_session",
            new_callable=mock.AsyncMock,
        ) as mock_logout, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit:
            with self.assertRaises(HTTPException) as ctx:
                await terminate_my_accountant_session(
                    relation_id=relation_id,
                    session_id="not-a-valid-uuid",
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "شناسه نشست نامعتبر است")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_not_called()
        mock_get_session.assert_not_called()
        mock_logout.assert_not_called()
        mock_audit.assert_not_called()

    async def test_terminate_my_accountant_session_owner_context_denial(self) -> None:
        context = mock.Mock(
            is_accountant_context=True,
            actor_user=None,
            owner_user=mock.Mock(id=42, role="owner", is_customer=False),
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
            "api.routers.accountants.get_active_accountant_session",
            new_callable=mock.AsyncMock,
        ) as mock_get_session, mock.patch(
            "api.routers.accountants.logout_session",
            new_callable=mock.AsyncMock,
        ) as mock_logout, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit:
            with self.assertRaises(HTTPException) as ctx:
                await terminate_my_accountant_session(
                    relation_id=7,
                    session_id="11111111-1111-1111-1111-111111111111",
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Accountants cannot manage owner accountants")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_not_called()
        mock_get_session.assert_not_called()
        mock_logout.assert_not_called()
        mock_audit.assert_not_called()

    async def test_terminate_my_accountant_session_relation_lookup_failure(self) -> None:
        owner_id = 42
        relation_id = 999
        session_uuid_str = "11111111-1111-1111-1111-111111111111"
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
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
            "api.routers.accountants.get_active_accountant_session",
            new_callable=mock.AsyncMock,
        ) as mock_get_session, mock.patch(
            "api.routers.accountants.logout_session",
            new_callable=mock.AsyncMock,
        ) as mock_logout, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit:
            with self.assertRaises(HTTPException) as ctx:
                await terminate_my_accountant_session(
                    relation_id=relation_id,
                    session_id=session_uuid_str,
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
        mock_get_session.assert_not_called()
        mock_logout.assert_not_called()
        mock_audit.assert_not_called()

    async def test_terminate_my_accountant_session_session_lookup_failure(self) -> None:
        owner_id = 42
        relation_id = 7
        accountant_id = 99
        session_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
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
            "api.routers.accountants.get_active_accountant_session",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=404,
                detail="نشست یافت نشد",
            ),
        ) as mock_get_session, mock.patch(
            "api.routers.accountants.logout_session",
            new_callable=mock.AsyncMock,
        ) as mock_logout, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit:
            with self.assertRaises(HTTPException) as ctx:
                await terminate_my_accountant_session(
                    relation_id=relation_id,
                    session_id=str(session_uuid),
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "نشست یافت نشد")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_session.assert_awaited_once_with(
            fake_db,
            accountant_user_id=accountant_id,
            session_id=session_uuid,
        )
        mock_logout.assert_not_called()
        mock_audit.assert_not_called()

    async def test_terminate_my_accountant_session_logout_failure(self) -> None:
        owner_id = 42
        relation_id = 7
        accountant_id = 99
        session_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
        )
        target_session = mock.Mock(id=session_uuid)
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_active_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=relation,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.get_active_accountant_session",
            new_callable=mock.AsyncMock,
            return_value=target_session,
        ) as mock_get_session, mock.patch(
            "api.routers.accountants.logout_session",
            new_callable=mock.AsyncMock,
            side_effect=RuntimeError("Logout failure"),
        ) as mock_logout, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit:
            with self.assertRaises(RuntimeError) as ctx:
                await terminate_my_accountant_session(
                    relation_id=relation_id,
                    session_id=str(session_uuid),
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(str(ctx.exception), "Logout failure")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_session.assert_awaited_once_with(
            fake_db,
            accountant_user_id=accountant_id,
            session_id=session_uuid,
        )
        mock_logout.assert_awaited_once_with(fake_db, target_session)
        mock_audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
