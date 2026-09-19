"""Hermetic characterization batch for api/routers/accountants.py context denial."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()
fence = install_p1_00_network_fence()

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.routers.accountants import (
    cancel_my_pending_accountant,
    list_my_accountant_sessions,
    terminate_my_accountant_session,
)
from core.services.accountant_relation_service import (
    AccountantRelationDeletionAction,
    EffectiveOwnerActor,
)


class _StrictUserSentinel:
    """Strict user sentinel rejecting every attribute access, mutation, or deletion."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unexpected user sentinel attribute access: {name}")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AssertionError(f"Unexpected user sentinel attribute mutation: {name}")

    def __delattr__(self, name: str) -> None:
        raise AssertionError(f"Unexpected user sentinel attribute deletion: {name}")


class _StrictDatabaseSentinel:
    """Strict database sentinel rejecting all attribute access, mutation, deletion, call, iteration, await, and non-identity equality."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unexpected database sentinel attribute access: {name}")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AssertionError(f"Unexpected database sentinel attribute mutation: {name}")

    def __delattr__(self, name: str) -> None:
        raise AssertionError(f"Unexpected database sentinel attribute deletion: {name}")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Unexpected database sentinel call")

    def __iter__(self) -> Any:
        raise AssertionError("Unexpected database sentinel iteration")

    def __await__(self) -> Any:
        raise AssertionError("Unexpected database sentinel await")
        yield

    def __eq__(self, other: object) -> bool:
        return self is other

    def __ne__(self, other: object) -> bool:
        return self is not other


class AccountantsContextDenialBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.owner_user = _StrictUserSentinel()
        self.actor_user = _StrictUserSentinel()
        self.context = EffectiveOwnerActor(
            owner_user=self.owner_user,  # type: ignore[arg-type]
            actor_user=self.actor_user,  # type: ignore[arg-type]
            relation=None,
            is_accountant_context=True,
        )
        self.db = _StrictDatabaseSentinel()

    async def test_list_my_accountant_sessions_accountant_context_denied_before_session_lookup(self) -> None:
        with (
            patch("api.routers.accountants.get_active_owner_accountant_relation", new_callable=AsyncMock) as mock_get_relation,
            patch("api.routers.accountants.get_active_sessions", new_callable=AsyncMock) as mock_get_sessions,
            patch("api.routers.accountants.serialize_accountant_session", new_callable=MagicMock) as mock_serialize_session,
        ):
            denied_before = len(fence["denied_connects"])
            redis_before = len(fence["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await list_my_accountant_sessions(
                    relation_id=42,
                    context=self.context,
                    db=self.db,  # type: ignore[arg-type]
                )

            self.assertEqual(len(fence["denied_connects"]), denied_before)
            self.assertEqual(len(fence["live_redis_constructs"]), redis_before)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "Accountants cannot manage owner accountants")

            mock_get_relation.assert_not_called()
            mock_get_relation.assert_not_awaited()
            mock_get_sessions.assert_not_called()
            mock_get_sessions.assert_not_awaited()
            mock_serialize_session.assert_not_called()

    async def test_terminate_my_accountant_session_accountant_context_denied_before_uuid_or_mutation(self) -> None:
        with (
            patch("api.routers.accountants.get_active_owner_accountant_relation", new_callable=AsyncMock) as mock_get_relation,
            patch("api.routers.accountants.get_active_accountant_session", new_callable=AsyncMock) as mock_get_session,
            patch("api.routers.accountants.logout_session", new_callable=AsyncMock) as mock_logout_session,
            patch("api.routers.accountants.audit_log", new_callable=MagicMock) as mock_audit_log,
        ):
            denied_before = len(fence["denied_connects"])
            redis_before = len(fence["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await terminate_my_accountant_session(
                    relation_id=42,
                    session_id="non-uuid-session-identifier",
                    context=self.context,
                    db=self.db,  # type: ignore[arg-type]
                )

            self.assertEqual(len(fence["denied_connects"]), denied_before)
            self.assertEqual(len(fence["live_redis_constructs"]), redis_before)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "Accountants cannot manage owner accountants")

            mock_get_relation.assert_not_called()
            mock_get_relation.assert_not_awaited()
            mock_get_session.assert_not_called()
            mock_get_session.assert_not_awaited()
            mock_logout_session.assert_not_called()
            mock_logout_session.assert_not_awaited()
            mock_audit_log.assert_not_called()

    async def test_cancel_my_pending_accountant_accountant_context_denied_before_unlink_or_audit(self) -> None:
        with (
            patch("api.routers.accountants.unlink_owner_accountant_relation", new_callable=AsyncMock) as mock_unlink_relation,
            patch("api.routers.accountants.audit_log", new_callable=MagicMock) as mock_audit_log,
            patch("api.routers.accountants.serialize_accountant_relation", new_callable=MagicMock) as mock_serialize_relation,
        ):
            denied_before = len(fence["denied_connects"])
            redis_before = len(fence["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await cancel_my_pending_accountant(
                    relation_id=42,
                    expected_action=AccountantRelationDeletionAction.CANCEL_PENDING,
                    context=self.context,
                    db=self.db,  # type: ignore[arg-type]
                )

            self.assertEqual(len(fence["denied_connects"]), denied_before)
            self.assertEqual(len(fence["live_redis_constructs"]), redis_before)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "Accountants cannot manage owner accountants")

            mock_unlink_relation.assert_not_called()
            mock_unlink_relation.assert_not_awaited()
            mock_audit_log.assert_not_called()
            mock_serialize_relation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
