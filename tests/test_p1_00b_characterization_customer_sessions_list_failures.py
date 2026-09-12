"""Hermetic direct characterization for list_my_customer_sessions failure boundaries.

Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-CUSTOMERS-LIST-MY-CUSTOMER-SESSIONS-L817
Target: api.routers.customers.list_my_customer_sessions

Note: This unit provides direct characterization for the three missing failure boundaries
(relation lookup failure, active-session lookup failure, and first-session serialization
failure) of seed API-IDENTITY-GET-CUSTOMERS-LIST-MY-CUSTOMER-SESSIONS-L817 under P1-00b
network isolation. It does NOT promote inventory evidence, reduce canonical remaining-seed
counts, or close P1-00b; evidence promotion is a separate unit.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.customers import list_my_customer_sessions
from core.services.accountant_relation_service import EffectiveOwnerActor
from models.customer_relation import CustomerRelationStatus


class SentinelAsyncSession:
    """Sentinel database session for direct handler characterization."""


class RelationLookupFailure(Exception):
    """Unique exception for relation lookup failure boundary."""


class ActiveSessionLookupFailure(Exception):
    """Unique exception for active-session lookup failure boundary."""


class FirstSessionSerializationFailure(Exception):
    """Unique exception for first-session serialization failure boundary."""


class P100bCustomerSessionsListFailuresCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for list_my_customer_sessions failure boundaries."""

    async def test_list_my_customer_sessions_relation_lookup_failure(self) -> None:
        owner_id = 42
        relation_id = 101
        fake_db = SentinelAsyncSession()
        context = EffectiveOwnerActor(
            owner_user=mock.Mock(id=owner_id),
            actor_user=mock.Mock(id=owner_id),
            relation=None,
            is_accountant_context=False,
        )

        relation_error = RelationLookupFailure(
            "active owner-customer relation lookup failed"
        )

        mock_guard = mock.AsyncMock()
        mock_get_relation = mock.AsyncMock(side_effect=relation_error)
        mock_get_sessions = mock.AsyncMock()
        mock_serialize = mock.Mock()

        order_tracker = mock.Mock()
        order_tracker.attach_mock(mock_guard, "ensure_owner_context")
        order_tracker.attach_mock(
            mock_get_relation, "get_active_owner_customer_relation"
        )
        order_tracker.attach_mock(mock_get_sessions, "get_active_sessions")
        order_tracker.attach_mock(mock_serialize, "serialize_customer_session")

        with mock.patch(
            "api.routers.customers.ensure_owner_context",
            new=mock_guard,
        ), mock.patch(
            "api.routers.customers.get_active_owner_customer_relation",
            new=mock_get_relation,
        ), mock.patch(
            "api.routers.customers.get_active_sessions",
            new=mock_get_sessions,
        ), mock.patch(
            "api.routers.customers.serialize_customer_session",
            new=mock_serialize,
        ):
            with self.assertRaises(RelationLookupFailure) as ctx:
                await list_my_customer_sessions(
                    relation_id=relation_id,
                    context=context,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, relation_error)
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_sessions.assert_not_called()
        mock_serialize.assert_not_called()

        order_tracker.assert_has_calls(
            [
                mock.call.ensure_owner_context(context, fake_db),
                mock.call.get_active_owner_customer_relation(
                    fake_db,
                    owner_user_id=owner_id,
                    relation_id=relation_id,
                ),
            ],
            any_order=False,
        )
        self.assertEqual(
            order_tracker.mock_calls,
            [
                mock.call.ensure_owner_context(context, fake_db),
                mock.call.get_active_owner_customer_relation(
                    fake_db,
                    owner_user_id=owner_id,
                    relation_id=relation_id,
                ),
            ],
        )

    async def test_list_my_customer_sessions_active_session_lookup_failure(
        self,
    ) -> None:
        owner_id = 42
        relation_id = 101
        customer_user_id = 202
        fake_db = SentinelAsyncSession()
        context = EffectiveOwnerActor(
            owner_user=mock.Mock(id=owner_id),
            actor_user=mock.Mock(id=owner_id),
            relation=None,
            is_accountant_context=False,
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            customer_user_id=customer_user_id,
            status=CustomerRelationStatus.ACTIVE,
        )

        session_lookup_error = ActiveSessionLookupFailure(
            "active-session lookup failed"
        )

        mock_guard = mock.AsyncMock()
        mock_get_relation = mock.AsyncMock(return_value=relation)
        mock_get_sessions = mock.AsyncMock(side_effect=session_lookup_error)
        mock_serialize = mock.Mock()

        order_tracker = mock.Mock()
        order_tracker.attach_mock(mock_guard, "ensure_owner_context")
        order_tracker.attach_mock(
            mock_get_relation, "get_active_owner_customer_relation"
        )
        order_tracker.attach_mock(mock_get_sessions, "get_active_sessions")
        order_tracker.attach_mock(mock_serialize, "serialize_customer_session")

        with mock.patch(
            "api.routers.customers.ensure_owner_context",
            new=mock_guard,
        ), mock.patch(
            "api.routers.customers.get_active_owner_customer_relation",
            new=mock_get_relation,
        ), mock.patch(
            "api.routers.customers.get_active_sessions",
            new=mock_get_sessions,
        ), mock.patch(
            "api.routers.customers.serialize_customer_session",
            new=mock_serialize,
        ):
            with self.assertRaises(ActiveSessionLookupFailure) as ctx:
                await list_my_customer_sessions(
                    relation_id=relation_id,
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
        mock_get_sessions.assert_awaited_once_with(fake_db, customer_user_id)
        mock_serialize.assert_not_called()

        order_tracker.assert_has_calls(
            [
                mock.call.ensure_owner_context(context, fake_db),
                mock.call.get_active_owner_customer_relation(
                    fake_db,
                    owner_user_id=owner_id,
                    relation_id=relation_id,
                ),
                mock.call.get_active_sessions(fake_db, customer_user_id),
            ],
            any_order=False,
        )
        self.assertEqual(
            order_tracker.mock_calls,
            [
                mock.call.ensure_owner_context(context, fake_db),
                mock.call.get_active_owner_customer_relation(
                    fake_db,
                    owner_user_id=owner_id,
                    relation_id=relation_id,
                ),
                mock.call.get_active_sessions(fake_db, customer_user_id),
            ],
        )

    async def test_list_my_customer_sessions_first_session_serialization_failure(
        self,
    ) -> None:
        owner_id = 42
        relation_id = 101
        customer_user_id = 202
        fake_db = SentinelAsyncSession()
        context = EffectiveOwnerActor(
            owner_user=mock.Mock(id=owner_id),
            actor_user=mock.Mock(id=owner_id),
            relation=None,
            is_accountant_context=False,
        )
        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            customer_user_id=customer_user_id,
            status=CustomerRelationStatus.ACTIVE,
        )

        session_1 = mock.Mock(name="active_session_1")
        session_2 = mock.Mock(name="active_session_2")
        self.assertIsNot(session_1, session_2)

        serialization_error = FirstSessionSerializationFailure(
            "first-session serialization failed"
        )

        mock_guard = mock.AsyncMock()
        mock_get_relation = mock.AsyncMock(return_value=relation)
        mock_get_sessions = mock.AsyncMock(return_value=[session_1, session_2])
        mock_serialize = mock.Mock(side_effect=serialization_error)

        order_tracker = mock.Mock()
        order_tracker.attach_mock(mock_guard, "ensure_owner_context")
        order_tracker.attach_mock(
            mock_get_relation, "get_active_owner_customer_relation"
        )
        order_tracker.attach_mock(mock_get_sessions, "get_active_sessions")
        order_tracker.attach_mock(mock_serialize, "serialize_customer_session")

        with mock.patch(
            "api.routers.customers.ensure_owner_context",
            new=mock_guard,
        ), mock.patch(
            "api.routers.customers.get_active_owner_customer_relation",
            new=mock_get_relation,
        ), mock.patch(
            "api.routers.customers.get_active_sessions",
            new=mock_get_sessions,
        ), mock.patch(
            "api.routers.customers.serialize_customer_session",
            new=mock_serialize,
        ):
            with self.assertRaises(FirstSessionSerializationFailure) as ctx:
                await list_my_customer_sessions(
                    relation_id=relation_id,
                    context=context,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, serialization_error)
        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        mock_get_sessions.assert_awaited_once_with(fake_db, customer_user_id)
        mock_serialize.assert_called_once_with(session_1)
        self.assertEqual(mock_serialize.call_count, 1)
        self.assertNotIn(mock.call(session_2), mock_serialize.mock_calls)
        self.assertNotIn(
            mock.call.serialize_customer_session(session_2),
            order_tracker.mock_calls,
        )

        order_tracker.assert_has_calls(
            [
                mock.call.ensure_owner_context(context, fake_db),
                mock.call.get_active_owner_customer_relation(
                    fake_db,
                    owner_user_id=owner_id,
                    relation_id=relation_id,
                ),
                mock.call.get_active_sessions(fake_db, customer_user_id),
                mock.call.serialize_customer_session(session_1),
            ],
            any_order=False,
        )
        self.assertEqual(
            order_tracker.mock_calls,
            [
                mock.call.ensure_owner_context(context, fake_db),
                mock.call.get_active_owner_customer_relation(
                    fake_db,
                    owner_user_id=owner_id,
                    relation_id=relation_id,
                ),
                mock.call.get_active_sessions(fake_db, customer_user_id),
                mock.call.serialize_customer_session(session_1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
