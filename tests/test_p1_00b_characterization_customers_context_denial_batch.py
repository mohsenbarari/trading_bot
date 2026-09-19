"""Characterization tests for customer session handlers under accountant context denial."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from api.routers.customers import (
    list_my_customer_sessions,
    terminate_my_customer_session,
)
from core.services.accountant_relation_service import EffectiveOwnerActor


class _StrictUserSentinel:
    """Strict sentinel rejecting every attribute access, mutation, or deletion."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"Unexpected attribute access on strict user sentinel: {name}")

    def __setattr__(self, name: str, value: object) -> None:
        raise AssertionError(f"Unexpected attribute mutation on strict user sentinel: {name}")

    def __delattr__(self, name: str) -> None:
        raise AssertionError(f"Unexpected attribute deletion on strict user sentinel: {name}")


class _StrictDatabaseSentinel:
    """Strict database sentinel rejecting attribute access, mutation, deletion, call, iteration, await, and non-identity equality."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"Unexpected attribute access on strict db sentinel: {name}")

    def __setattr__(self, name: str, value: object) -> None:
        raise AssertionError(f"Unexpected attribute mutation on strict db sentinel: {name}")

    def __delattr__(self, name: str) -> None:
        raise AssertionError(f"Unexpected attribute deletion on strict db sentinel: {name}")

    def __call__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("Unexpected call on strict db sentinel")

    def __iter__(self) -> None:
        raise AssertionError("Unexpected iteration on strict db sentinel")

    def __await__(self) -> None:
        raise AssertionError("Unexpected await on strict db sentinel")

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        raise AssertionError(f"Unexpected equality comparison on strict db sentinel with {other!r}")

    def __ne__(self, other: object) -> bool:
        if self is other:
            return False
        raise AssertionError(f"Unexpected inequality comparison on strict db sentinel with {other!r}")


class CustomersContextDenialBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.owner_user = _StrictUserSentinel()
        self.actor_user = _StrictUserSentinel()
        self.context = EffectiveOwnerActor(
            owner_user=self.owner_user,
            actor_user=self.actor_user,
            relation=None,
            is_accountant_context=True,
        )
        self.db = _StrictDatabaseSentinel()

    async def test_list_my_customer_sessions_accountant_context_denied_before_relation_or_session_lookup(
        self,
    ) -> None:
        with (
            patch(
                "api.routers.customers.get_active_owner_customer_relation",
                new_callable=AsyncMock,
            ) as mock_get_relation,
            patch(
                "api.routers.customers.get_active_sessions",
                new_callable=AsyncMock,
            ) as mock_get_sessions,
            patch(
                "api.routers.customers.serialize_customer_session",
                new_callable=Mock,
            ) as mock_serialize_session,
        ):
            denied_before = len(self.fence["denied_connects"])
            redis_before = len(self.fence["live_redis_constructs"])

            with self.assertRaises(HTTPException) as cm:
                await list_my_customer_sessions(
                    relation_id=42,
                    context=self.context,
                    db=self.db,
                )

            self.assertEqual(cm.exception.status_code, 403)
            self.assertEqual(
                cm.exception.detail,
                "Accountants cannot manage owner customers",
            )
            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

            mock_get_relation.assert_not_called()
            mock_get_relation.assert_not_awaited()
            mock_get_sessions.assert_not_called()
            mock_get_sessions.assert_not_awaited()
            mock_serialize_session.assert_not_called()

    async def test_terminate_my_customer_session_accountant_context_denied_before_uuid_or_mutation(
        self,
    ) -> None:
        with (
            patch(
                "api.routers.customers.get_active_owner_customer_relation",
                new_callable=AsyncMock,
            ) as mock_get_relation,
            patch(
                "api.routers.customers.get_active_customer_session",
                new_callable=AsyncMock,
            ) as mock_get_session,
            patch(
                "api.routers.customers.logout_session",
                new_callable=AsyncMock,
            ) as mock_logout_session,
            patch(
                "api.routers.customers.audit_log",
                new_callable=Mock,
            ) as mock_audit_log,
        ):
            denied_before = len(self.fence["denied_connects"])
            redis_before = len(self.fence["live_redis_constructs"])

            with self.assertRaises(HTTPException) as cm:
                await terminate_my_customer_session(
                    relation_id=42,
                    session_id="not-a-valid-uuid",
                    context=self.context,
                    db=self.db,
                )

            self.assertEqual(cm.exception.status_code, 403)
            self.assertEqual(
                cm.exception.detail,
                "Accountants cannot manage owner customers",
            )
            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

            mock_get_relation.assert_not_called()
            mock_get_relation.assert_not_awaited()
            mock_get_session.assert_not_called()
            mock_get_session.assert_not_awaited()
            mock_logout_session.assert_not_called()
            mock_logout_session.assert_not_awaited()
            mock_audit_log.assert_not_called()
