"""Hermetic direct characterization for terminate_my_customer_session.

Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-DELETE-CUSTOMERS-TERMINATE-MY-CUSTOMER-SESSION-L833
Target: api.routers.customers.terminate_my_customer_session

Note: This unit provides direct characterization for the promoted-session success contract
and the two adjacent active-relation/active-customer-session lookup failure boundaries
of seed API-IDENTITY-DELETE-CUSTOMERS-TERMINATE-MY-CUSTOMER-SESSION-L833 under P1-00b
network isolation. It does NOT promote inventory evidence, reduce canonical remaining-seed
counts, or close P1-00b; evidence promotion is a separate unit.
"""

from __future__ import annotations

import unittest
import uuid
from typing import Any
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.customers import terminate_my_customer_session
from core.services.accountant_relation_service import EffectiveOwnerActor
from models.customer_relation import CustomerRelationStatus
from models.user import UserRole

_REAL_UUID = uuid.UUID


class SentinelAsyncSession:
    """Sentinel database session for direct handler characterization."""


class RelationLookupFailure(Exception):
    """Unique exception for relation lookup failure boundary."""


class ActiveCustomerSessionLookupFailure(Exception):
    """Unique exception for active-customer-session lookup failure boundary."""


class P100bCustomerSessionsTerminateCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for terminate_my_customer_session."""

    async def test_terminate_my_customer_session_promoted_success(self) -> None:
        owner_id = 42
        customer_user_id = 202
        relation_id = 101
        session_id_text = "11111111-1111-4111-8111-111111111111"
        terminated_session_uuid = uuid.UUID(session_id_text)
        promoted_session_uuid = uuid.UUID("22222222-2222-4222-8222-222222222222")

        fake_db = SentinelAsyncSession()
        context = EffectiveOwnerActor(
            owner_user=mock.Mock(id=owner_id, role=UserRole.STANDARD),
            actor_user=mock.Mock(id=owner_id, role=UserRole.STANDARD),
            relation=None,
            is_accountant_context=False,
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            customer_user_id=customer_user_id,
            status=CustomerRelationStatus.ACTIVE,
        )
        terminated_session = mock.Mock(
            id=terminated_session_uuid,
            user_id=customer_user_id,
        )
        promoted_session = mock.Mock(
            id=promoted_session_uuid,
            user_id=customer_user_id,
        )

        mock_guard = mock.AsyncMock()
        mock_get_relation = mock.AsyncMock(return_value=relation)
        mock_get_session = mock.AsyncMock(return_value=terminated_session)
        mock_logout = mock.AsyncMock(return_value=promoted_session)
        mock_audit_log = mock.Mock()

        order_tracker = mock.Mock()
        order_tracker.attach_mock(mock_guard, "ensure_owner_context")
        order_tracker.attach_mock(
            mock_get_relation, "get_active_owner_customer_relation"
        )
        order_tracker.attach_mock(
            mock_get_session, "get_active_customer_session"
        )
        order_tracker.attach_mock(mock_logout, "logout_session")
        order_tracker.attach_mock(mock_audit_log, "audit_log")

        with mock.patch(
            "api.routers.customers.ensure_owner_context",
            new=mock_guard,
        ), mock.patch(
            "api.routers.customers.get_active_owner_customer_relation",
            new=mock_get_relation,
        ), mock.patch(
            "api.routers.customers.get_active_customer_session",
            new=mock_get_session,
        ), mock.patch(
            "api.routers.customers.logout_session",
            new=mock_logout,
        ), mock.patch(
            "api.routers.customers.audit_log",
            new=mock_audit_log,
        ):
            response = await terminate_my_customer_session(
                relation_id=relation_id,
                session_id=session_id_text,
                context=context,
                db=fake_db,
            )

        expected_response = {
            "detail": "نشست مشتری با موفقیت پایان یافت",
            "terminated_session_id": str(terminated_session_uuid),
            "promoted_primary_session_id": str(promoted_session_uuid),
        }
        self.assertEqual(response, expected_response)
        self.assertEqual(
            set(response.keys()),
            {"detail", "terminated_session_id", "promoted_primary_session_id"},
        )
        self.assertEqual(response["detail"], "نشست مشتری با موفقیت پایان یافت")
        self.assertEqual(response["terminated_session_id"], session_id_text)
        self.assertEqual(
            response["promoted_primary_session_id"], str(promoted_session_uuid)
        )

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_session.assert_awaited_once_with(
            fake_db,
            customer_user_id=customer_user_id,
            session_id=terminated_session_uuid,
        )
        passed_session_id = mock_get_session.await_args.kwargs["session_id"]
        self.assertIsInstance(passed_session_id, uuid.UUID)
        self.assertEqual(passed_session_id, terminated_session_uuid)

        mock_logout.assert_awaited_once_with(fake_db, terminated_session)
        mock_audit_log.assert_called_once_with(
            "customer.session_terminate",
            target_type="customer_session",
            target_id=terminated_session_uuid,
            after_summary={
                "relation_id": relation_id,
                "customer_user_id": customer_user_id,
                "promoted_primary_session_id": str(promoted_session_uuid),
            },
            actor_id=owner_id,
            actor_role=UserRole.STANDARD.value,
        )

        expected_calls = [
            mock.call.ensure_owner_context(context, fake_db),
            mock.call.get_active_owner_customer_relation(
                fake_db,
                owner_user_id=owner_id,
                relation_id=relation_id,
            ),
            mock.call.get_active_customer_session(
                fake_db,
                customer_user_id=customer_user_id,
                session_id=terminated_session_uuid,
            ),
            mock.call.logout_session(fake_db, terminated_session),
            mock.call.audit_log(
                "customer.session_terminate",
                target_type="customer_session",
                target_id=terminated_session_uuid,
                after_summary={
                    "relation_id": relation_id,
                    "customer_user_id": customer_user_id,
                    "promoted_primary_session_id": str(promoted_session_uuid),
                },
                actor_id=owner_id,
                actor_role=UserRole.STANDARD.value,
            ),
        ]
        order_tracker.assert_has_calls(expected_calls, any_order=False)
        self.assertEqual(order_tracker.mock_calls, expected_calls)

    async def test_terminate_my_customer_session_relation_lookup_failure(
        self,
    ) -> None:
        owner_id = 42
        relation_id = 101
        session_id_text = "11111111-1111-4111-8111-111111111111"
        fake_db = SentinelAsyncSession()
        context = EffectiveOwnerActor(
            owner_user=mock.Mock(id=owner_id, role=UserRole.STANDARD),
            actor_user=mock.Mock(id=owner_id, role=UserRole.STANDARD),
            relation=None,
            is_accountant_context=False,
        )

        relation_error = RelationLookupFailure(
            "active owner-customer relation lookup failed"
        )

        execution_timeline: list[str] = []

        mock_guard = mock.AsyncMock(
            side_effect=lambda *args, **kwargs: execution_timeline.append(
                "ensure_owner_context"
            )
        )

        def spy_uuid_conversion(val: str, *args: Any, **kwargs: Any) -> uuid.UUID:
            execution_timeline.append("uuid_normalization")
            return _REAL_UUID(val, *args, **kwargs)

        def fail_relation_lookup(*args: Any, **kwargs: Any) -> None:
            execution_timeline.append("get_active_owner_customer_relation")
            raise relation_error

        mock_get_relation = mock.AsyncMock(side_effect=fail_relation_lookup)
        mock_get_session = mock.AsyncMock()
        mock_logout = mock.AsyncMock()
        mock_audit_log = mock.Mock()

        order_tracker = mock.Mock()
        order_tracker.attach_mock(mock_guard, "ensure_owner_context")
        order_tracker.attach_mock(
            mock_get_relation, "get_active_owner_customer_relation"
        )
        order_tracker.attach_mock(
            mock_get_session, "get_active_customer_session"
        )
        order_tracker.attach_mock(mock_logout, "logout_session")
        order_tracker.attach_mock(mock_audit_log, "audit_log")

        with mock.patch(
            "api.routers.customers.ensure_owner_context",
            new=mock_guard,
        ), mock.patch(
            "api.routers.customers.uuid.UUID",
            side_effect=spy_uuid_conversion,
        ) as mock_uuid_cls, mock.patch(
            "api.routers.customers.get_active_owner_customer_relation",
            new=mock_get_relation,
        ), mock.patch(
            "api.routers.customers.get_active_customer_session",
            new=mock_get_session,
        ), mock.patch(
            "api.routers.customers.logout_session",
            new=mock_logout,
        ), mock.patch(
            "api.routers.customers.audit_log",
            new=mock_audit_log,
        ):
            with self.assertRaises(RelationLookupFailure) as ctx:
                await terminate_my_customer_session(
                    relation_id=relation_id,
                    session_id=session_id_text,
                    context=context,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, relation_error)
        mock_uuid_cls.assert_called_once_with(session_id_text)
        self.assertEqual(
            execution_timeline,
            [
                "ensure_owner_context",
                "uuid_normalization",
                "get_active_owner_customer_relation",
            ],
        )

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_session.assert_not_called()
        mock_logout.assert_not_called()
        mock_audit_log.assert_not_called()

        expected_calls = [
            mock.call.ensure_owner_context(context, fake_db),
            mock.call.get_active_owner_customer_relation(
                fake_db,
                owner_user_id=owner_id,
                relation_id=relation_id,
            ),
        ]
        order_tracker.assert_has_calls(expected_calls, any_order=False)
        self.assertEqual(order_tracker.mock_calls, expected_calls)

    async def test_terminate_my_customer_session_active_customer_session_lookup_failure(
        self,
    ) -> None:
        owner_id = 42
        customer_user_id = 202
        relation_id = 101
        session_id_text = "11111111-1111-4111-8111-111111111111"
        expected_session_uuid = uuid.UUID(session_id_text)

        fake_db = SentinelAsyncSession()
        context = EffectiveOwnerActor(
            owner_user=mock.Mock(id=owner_id, role=UserRole.STANDARD),
            actor_user=mock.Mock(id=owner_id, role=UserRole.STANDARD),
            relation=None,
            is_accountant_context=False,
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            customer_user_id=customer_user_id,
            status=CustomerRelationStatus.ACTIVE,
        )

        session_lookup_error = ActiveCustomerSessionLookupFailure(
            "active customer session lookup failed"
        )

        mock_guard = mock.AsyncMock()
        mock_get_relation = mock.AsyncMock(return_value=relation)
        mock_get_session = mock.AsyncMock(side_effect=session_lookup_error)
        mock_logout = mock.AsyncMock()
        mock_audit_log = mock.Mock()

        order_tracker = mock.Mock()
        order_tracker.attach_mock(mock_guard, "ensure_owner_context")
        order_tracker.attach_mock(
            mock_get_relation, "get_active_owner_customer_relation"
        )
        order_tracker.attach_mock(
            mock_get_session, "get_active_customer_session"
        )
        order_tracker.attach_mock(mock_logout, "logout_session")
        order_tracker.attach_mock(mock_audit_log, "audit_log")

        with mock.patch(
            "api.routers.customers.ensure_owner_context",
            new=mock_guard,
        ), mock.patch(
            "api.routers.customers.get_active_owner_customer_relation",
            new=mock_get_relation,
        ), mock.patch(
            "api.routers.customers.get_active_customer_session",
            new=mock_get_session,
        ), mock.patch(
            "api.routers.customers.logout_session",
            new=mock_logout,
        ), mock.patch(
            "api.routers.customers.audit_log",
            new=mock_audit_log,
        ):
            with self.assertRaises(ActiveCustomerSessionLookupFailure) as ctx:
                await terminate_my_customer_session(
                    relation_id=relation_id,
                    session_id=session_id_text,
                    context=context,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, session_lookup_error)
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_session.assert_awaited_once_with(
            fake_db,
            customer_user_id=customer_user_id,
            session_id=expected_session_uuid,
        )

        passed_session_id = mock_get_session.await_args.kwargs["session_id"]
        self.assertIsInstance(passed_session_id, uuid.UUID)
        self.assertEqual(passed_session_id, expected_session_uuid)
        self.assertEqual(str(passed_session_id), session_id_text)

        mock_logout.assert_not_called()
        mock_audit_log.assert_not_called()

        expected_calls = [
            mock.call.ensure_owner_context(context, fake_db),
            mock.call.get_active_owner_customer_relation(
                fake_db,
                owner_user_id=owner_id,
                relation_id=relation_id,
            ),
            mock.call.get_active_customer_session(
                fake_db,
                customer_user_id=customer_user_id,
                session_id=expected_session_uuid,
            ),
        ]
        order_tracker.assert_has_calls(expected_calls, any_order=False)
        self.assertEqual(order_tracker.mock_calls, expected_calls)


if __name__ == "__main__":
    unittest.main()
