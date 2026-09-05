"""Hermetic direct characterization for cancel_my_pending_accountant endpoint.

Target: api.routers.accountants.cancel_my_pending_accountant

Note: This module provides direct characterization only and does not update
an executed-seed registry, reduce a canonical blocker, promote evidence,
or mark P1-00b complete; evidence promotion is a separate unit.
"""

from __future__ import annotations

import enum
import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.accountants import cancel_my_pending_accountant
from core.services.accountant_relation_service import AccountantRelationDeletionAction


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAccountantCancelCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for cancel_my_pending_accountant."""

    async def test_cancel_my_pending_accountant_success_with_actor_user_enum_role(self) -> None:
        """Characterize success path with actor_user enum role normalization and exact call ordering."""
        owner_id = 42
        relation_id = 7
        accountant_id = 99
        relation_status = "pending"
        deleted_timestamp = "2026-09-05T23:00:00Z"

        class RoleEnum(enum.Enum):
            OWNER = "owner"

        actor = mock.Mock(id=owner_id, role=RoleEnum.OWNER)
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=actor,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )
        expected_action = (
            next(iter(AccountantRelationDeletionAction))
            if isinstance(AccountantRelationDeletionAction, type)
            and issubclass(AccountantRelationDeletionAction, enum.Enum)
            else mock.Mock(spec=AccountantRelationDeletionAction)
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
            status=relation_status,
            deleted_at=deleted_timestamp,
        )
        serialized_payload = {
            "id": relation_id,
            "owner_user_id": owner_id,
            "accountant_user_id": accountant_id,
            "status": relation_status,
            "deleted_at": deleted_timestamp,
        }
        fake_db = FakeAsyncSession()

        call_order: list[str] = []

        async def _fake_guard(*args, **kwargs):
            call_order.append("ensure_owner_context")

        async def _fake_unlink(*args, **kwargs):
            call_order.append("unlink_owner_accountant_relation")
            return relation

        def _fake_audit(*args, **kwargs):
            call_order.append("audit_log")

        def _fake_serialize(*args, **kwargs):
            call_order.append("serialize_accountant_relation")
            return serialized_payload

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=_fake_guard,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.unlink_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=_fake_unlink,
        ) as mock_unlink, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=_fake_audit,
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
            side_effect=_fake_serialize,
        ) as mock_serialize:
            result = await cancel_my_pending_accountant(
                relation_id=relation_id,
                expected_action=expected_action,
                context=context,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "unlink_owner_accountant_relation",
                "audit_log",
                "serialize_accountant_relation",
            ],
        )
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_unlink.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            expected_action=expected_action,
        )
        passed_action = mock_unlink.await_args.kwargs["expected_action"]
        self.assertIs(passed_action, expected_action)

        mock_audit.assert_called_once_with(
            "accountant.unlink",
            target_type="accountant_relation",
            target_id=relation_id,
            before_summary={
                "owner_user_id": owner_id,
                "accountant_user_id": accountant_id,
                "status": relation_status,
            },
            after_summary={
                "deleted_at": deleted_timestamp,
                "status": relation_status,
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        mock_serialize.assert_called_once_with(relation)
        self.assertEqual(result, serialized_payload)
        fake_db.execute.assert_not_called()

    async def test_cancel_my_pending_accountant_success_fallback_owner_user_string_role(self) -> None:
        """Characterize success path with actor_user=None falling back to owner_user and string role."""
        owner_id = 42
        relation_id = 7
        accountant_id = 99
        relation_status = "pending"
        deleted_timestamp = "2026-09-05T23:00:00Z"

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        expected_action = (
            next(iter(AccountantRelationDeletionAction))
            if isinstance(AccountantRelationDeletionAction, type)
            and issubclass(AccountantRelationDeletionAction, enum.Enum)
            else mock.Mock(spec=AccountantRelationDeletionAction)
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
            status=relation_status,
            deleted_at=deleted_timestamp,
        )
        serialized_payload = {
            "id": relation_id,
            "owner_user_id": owner_id,
            "accountant_user_id": accountant_id,
            "status": relation_status,
            "deleted_at": deleted_timestamp,
        }
        fake_db = FakeAsyncSession()

        call_order: list[str] = []

        async def _fake_guard(*args, **kwargs):
            call_order.append("ensure_owner_context")

        async def _fake_unlink(*args, **kwargs):
            call_order.append("unlink_owner_accountant_relation")
            return relation

        def _fake_audit(*args, **kwargs):
            call_order.append("audit_log")

        def _fake_serialize(*args, **kwargs):
            call_order.append("serialize_accountant_relation")
            return serialized_payload

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=_fake_guard,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.unlink_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=_fake_unlink,
        ) as mock_unlink, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=_fake_audit,
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
            side_effect=_fake_serialize,
        ) as mock_serialize:
            result = await cancel_my_pending_accountant(
                relation_id=relation_id,
                expected_action=expected_action,
                context=context,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "unlink_owner_accountant_relation",
                "audit_log",
                "serialize_accountant_relation",
            ],
        )
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_unlink.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            expected_action=expected_action,
        )
        mock_audit.assert_called_once_with(
            "accountant.unlink",
            target_type="accountant_relation",
            target_id=relation_id,
            before_summary={
                "owner_user_id": owner_id,
                "accountant_user_id": accountant_id,
                "status": relation_status,
            },
            after_summary={
                "deleted_at": deleted_timestamp,
                "status": relation_status,
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        mock_serialize.assert_called_once_with(relation)
        self.assertEqual(result, serialized_payload)
        fake_db.execute.assert_not_called()

    async def test_cancel_my_pending_accountant_owner_context_denial(self) -> None:
        """Characterize owner-context denial as fail-fast without unlink, audit, or serialization."""
        context = mock.Mock(
            is_accountant_context=True,
            actor_user=None,
            owner_user=mock.Mock(id=42, role="owner", is_customer=False),
        )
        expected_action = mock.Mock(spec=AccountantRelationDeletionAction)
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=403,
                detail="Accountants cannot manage owner accountants",
            ),
        ) as mock_guard, mock.patch(
            "api.routers.accountants.unlink_owner_accountant_relation",
            new_callable=mock.AsyncMock,
        ) as mock_unlink, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await cancel_my_pending_accountant(
                    relation_id=7,
                    expected_action=expected_action,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Accountants cannot manage owner accountants")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_unlink.assert_not_called()
        mock_audit.assert_not_called()
        mock_serialize.assert_not_called()
        fake_db.execute.assert_not_called()

    async def test_cancel_my_pending_accountant_unlink_failure(self) -> None:
        """Characterize unlink failure: preserves original exception and proves audit/serialize not called."""
        owner_id = 42
        relation_id = 7
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        expected_action = mock.Mock(spec=AccountantRelationDeletionAction)
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.unlink_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=404,
                detail="رابطه حسابدار یافت نشد",
            ),
        ) as mock_unlink, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await cancel_my_pending_accountant(
                    relation_id=relation_id,
                    expected_action=expected_action,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "رابطه حسابدار یافت نشد")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_unlink.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            expected_action=expected_action,
        )
        mock_audit.assert_not_called()
        mock_serialize.assert_not_called()
        fake_db.execute.assert_not_called()

    async def test_cancel_my_pending_accountant_audit_log_failure(self) -> None:
        """Characterize audit failure after unlink: preserve original exception, prove serialize not called."""
        owner_id = 42
        relation_id = 7
        accountant_id = 99
        relation_status = "pending"
        deleted_timestamp = "2026-09-05T23:00:00Z"

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        expected_action = mock.Mock(spec=AccountantRelationDeletionAction)
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=accountant_id,
            status=relation_status,
            deleted_at=deleted_timestamp,
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.unlink_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=relation,
        ) as mock_unlink, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=RuntimeError("Audit emission failed"),
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(RuntimeError) as ctx:
                await cancel_my_pending_accountant(
                    relation_id=relation_id,
                    expected_action=expected_action,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(str(ctx.exception), "Audit emission failed")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_unlink.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            expected_action=expected_action,
        )
        mock_audit.assert_called_once_with(
            "accountant.unlink",
            target_type="accountant_relation",
            target_id=relation_id,
            before_summary={
                "owner_user_id": owner_id,
                "accountant_user_id": accountant_id,
                "status": relation_status,
            },
            after_summary={
                "deleted_at": deleted_timestamp,
                "status": relation_status,
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        mock_serialize.assert_not_called()
        fake_db.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
