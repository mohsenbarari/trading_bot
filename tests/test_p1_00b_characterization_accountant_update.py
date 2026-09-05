"""Hermetic direct characterization for update_my_accountant endpoint.

Target: api.routers.accountants.update_my_accountant

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

import schemas
from api.routers.accountants import update_my_accountant
from models.accountant_relation import AccountantRelationStatus


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAccountantUpdateCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for update_my_accountant."""

    async def test_update_my_accountant_success_pending_relation_with_actor_user_enum_role(self) -> None:
        """Characterize success path for pending relation with actor_user enum role, invitation lookup, audit, and serialization."""
        owner_id = 42
        relation_id = 7
        token = "ACCT-tok-pending-123"

        class RoleEnum(enum.Enum):
            OWNER = "owner"

        actor = mock.Mock(id=owner_id, role=RoleEnum.OWNER)
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=actor,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )
        payload = schemas.AccountantRelationUpdate(duty_description="  Bookkeeping and review  ")
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            status=AccountantRelationStatus.PENDING,
            invitation_token=token,
        )
        invitation_mock = mock.Mock(token=token, short_code="inv12345")
        serialized_payload = {
            "id": relation_id,
            "owner_user_id": owner_id,
            "status": AccountantRelationStatus.PENDING,
            "duty_description": "Bookkeeping and review",
            "short_code": "inv12345",
        }
        fake_db = FakeAsyncSession()

        call_order: list[str] = []

        async def _fake_guard(*args, **kwargs):
            call_order.append("ensure_owner_context")

        async def _fake_update(*args, **kwargs):
            call_order.append("update_owner_accountant_relation")
            return relation

        async def _fake_execute(*args, **kwargs):
            call_order.append("db_execute")
            result_mock = mock.Mock()
            result_mock.scalar_one_or_none.return_value = invitation_mock
            return result_mock

        fake_db.execute.side_effect = _fake_execute

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
            "api.routers.accountants.update_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=_fake_update,
        ) as mock_update, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=_fake_audit,
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
            side_effect=_fake_serialize,
        ) as mock_serialize:
            result = await update_my_accountant(
                relation_id=relation_id,
                payload=payload,
                context=context,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "update_owner_accountant_relation",
                "db_execute",
                "audit_log",
                "serialize_accountant_relation",
            ],
        )
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_update.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            duty_description="Bookkeeping and review",
        )
        fake_db.execute.assert_awaited_once()
        stmt = fake_db.execute.await_args.args[0]
        self.assertEqual(stmt.whereclause.right.value, token)

        mock_audit.assert_called_once_with(
            "accountant.update",
            target_type="accountant_relation",
            target_id=relation_id,
            after_summary={
                "updated_fields": ["duty_description"],
                "status": AccountantRelationStatus.PENDING,
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        mock_serialize.assert_called_once_with(relation, invitation=invitation_mock)
        self.assertEqual(result, serialized_payload)

    async def test_update_my_accountant_success_active_relation_fallback_owner_user_string_role(self) -> None:
        """Characterize success path for active relation: no invitation query, preserves audit fields and string role."""
        owner_id = 42
        relation_id = 7

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationUpdate(duty_description="Daily accounting and reconciliation")
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            status=AccountantRelationStatus.ACTIVE,
            invitation_token="ACCT-tok-active-456",
        )
        serialized_payload = {
            "id": relation_id,
            "owner_user_id": owner_id,
            "status": AccountantRelationStatus.ACTIVE,
            "duty_description": "Daily accounting and reconciliation",
        }
        fake_db = FakeAsyncSession()

        call_order: list[str] = []

        async def _fake_guard(*args, **kwargs):
            call_order.append("ensure_owner_context")

        async def _fake_update(*args, **kwargs):
            call_order.append("update_owner_accountant_relation")
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
            "api.routers.accountants.update_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=_fake_update,
        ) as mock_update, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=_fake_audit,
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
            side_effect=_fake_serialize,
        ) as mock_serialize:
            result = await update_my_accountant(
                relation_id=relation_id,
                payload=payload,
                context=context,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "update_owner_accountant_relation",
                "audit_log",
                "serialize_accountant_relation",
            ],
        )
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_update.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            duty_description="Daily accounting and reconciliation",
        )
        fake_db.execute.assert_not_called()

        mock_audit.assert_called_once_with(
            "accountant.update",
            target_type="accountant_relation",
            target_id=relation_id,
            after_summary={
                "updated_fields": ["duty_description"],
                "status": AccountantRelationStatus.ACTIVE,
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        mock_serialize.assert_called_once_with(relation, invitation=None)
        self.assertEqual(result, serialized_payload)

    async def test_update_my_accountant_owner_context_denial(self) -> None:
        """Characterize owner-context denial as fail-fast without update service, db, audit, or serialize."""
        context = mock.Mock(
            is_accountant_context=True,
            actor_user=None,
            owner_user=mock.Mock(id=42, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationUpdate(duty_description="Updated duties")
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=403,
                detail="Accountants cannot manage owner accountants",
            ),
        ) as mock_guard, mock.patch(
            "api.routers.accountants.update_owner_accountant_relation",
            new_callable=mock.AsyncMock,
        ) as mock_update, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await update_my_accountant(
                    relation_id=7,
                    payload=payload,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Accountants cannot manage owner accountants")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_update.assert_not_called()
        fake_db.execute.assert_not_called()
        mock_audit.assert_not_called()
        mock_serialize.assert_not_called()

    async def test_update_my_accountant_update_service_failure(self) -> None:
        """Characterize update service failure: preserve original exception, prove no lookup, audit, or serialize."""
        owner_id = 42
        relation_id = 7
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationUpdate(duty_description="Updated duties")
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.update_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=404,
                detail="رابطه حسابدار یافت نشد",
            ),
        ) as mock_update, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await update_my_accountant(
                    relation_id=relation_id,
                    payload=payload,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "رابطه حسابدار یافت نشد")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_update.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            duty_description="Updated duties",
        )
        fake_db.execute.assert_not_called()
        mock_audit.assert_not_called()
        mock_serialize.assert_not_called()

    async def test_update_my_accountant_pending_invitation_lookup_failure(self) -> None:
        """Characterize pending Invitation lookup failure: preserve exception after update, prove no audit or serialize."""
        owner_id = 42
        relation_id = 7
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            status=AccountantRelationStatus.PENDING,
            invitation_token="ACCT-token-999",
        )
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationUpdate(duty_description="Updated duties")
        fake_db = FakeAsyncSession()
        fake_db.execute.side_effect = RuntimeError("Database lookup failed")

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.update_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=relation,
        ) as mock_update, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(RuntimeError) as ctx:
                await update_my_accountant(
                    relation_id=relation_id,
                    payload=payload,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(str(ctx.exception), "Database lookup failed")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_update.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            duty_description="Updated duties",
        )
        fake_db.execute.assert_awaited_once()
        mock_audit.assert_not_called()
        mock_serialize.assert_not_called()

    async def test_update_my_accountant_audit_log_failure_after_pending_lookup(self) -> None:
        """Characterize audit failure after update and pending lookup: preserve exception and prove no serialize."""
        owner_id = 42
        relation_id = 7
        token = "ACCT-token-999"
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            status=AccountantRelationStatus.PENDING,
            invitation_token=token,
        )
        invitation_mock = mock.Mock(token=token)
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationUpdate(duty_description="Updated duties")
        fake_db = FakeAsyncSession()

        async def _fake_execute(*args, **kwargs):
            result_mock = mock.Mock()
            result_mock.scalar_one_or_none.return_value = invitation_mock
            return result_mock

        fake_db.execute.side_effect = _fake_execute

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.update_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=relation,
        ) as mock_update, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=RuntimeError("Audit emission failed"),
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(RuntimeError) as ctx:
                await update_my_accountant(
                    relation_id=relation_id,
                    payload=payload,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(str(ctx.exception), "Audit emission failed")
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_update.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
            duty_description="Updated duties",
        )
        fake_db.execute.assert_awaited_once()
        mock_audit.assert_called_once_with(
            "accountant.update",
            target_type="accountant_relation",
            target_id=relation_id,
            after_summary={
                "updated_fields": ["duty_description"],
                "status": AccountantRelationStatus.PENDING,
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        mock_serialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
