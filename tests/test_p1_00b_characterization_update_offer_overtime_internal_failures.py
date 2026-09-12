"""Hermetic characterization tests for api.routers.auth.update_offer_overtime_internal.

Covers pre-persistence rejections under Stage P1-00b (Assignment WA-P1-00B-FREEZE-20260904-001):
- Malformed JSON body rejection (HTTP 422)
- Non-integer user_id rejection (HTTP 422)
- Missing user lookup rejection (HTTP 404)
- Soft-deleted user rejection (HTTP 404)

Preserves product behavior; Stage P1-00b and blocker remain open.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.auth import (
    _verify_foreign_internal_command,
    update_offer_overtime_internal,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.offer_overtime_preference_service import (
    INVALID_OVERTIME_VALUE_MESSAGE,
    OVERTIME_NOT_AVAILABLE_MESSAGE,
    OfferOvertimePreferenceError,
    OfferOvertimePreferenceNotAllowedError,
    OvertimePreferenceSaveResult,
)
from models.user import User
import schemas


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = headers or {
            "x-timestamp": "1700000000",
            "x-signature": "test-signature",
            "x-api-key": "test-sync-api-key",
            "x-source-server": SERVER_FOREIGN,
        }

    async def body(self) -> bytes:
        return self._body


class _InstrumentedUser:
    """Runtime-shaped user fake instrumented to record offer_overtime_minutes state."""

    def __init__(
        self,
        user_id: int = 7,
        telegram_id: int | None = 998877,
        offer_overtime_minutes: int = 0,
        is_deleted: bool = False,
    ) -> None:
        self.id = user_id
        self.telegram_id = telegram_id
        self.is_deleted = is_deleted
        self._offer_overtime_minutes = offer_overtime_minutes
        self._mutation_count = 0

    @property
    def offer_overtime_minutes(self) -> int:
        return self._offer_overtime_minutes

    @offer_overtime_minutes.setter
    def offer_overtime_minutes(self, value: int) -> None:
        self._mutation_count += 1
        self._offer_overtime_minutes = value

    @property
    def mutation_count(self) -> int:
        return self._mutation_count


class TestP100bUpdateOfferOvertimeInternalFailures(unittest.IsolatedAsyncioTestCase):
    async def test_pre_persistence_rejection_malformed_json_body(self) -> None:
        malformed_body = b'{"user_id": 11, "offer_overtime_minutes": '
        request = _FakeRequest(body=malformed_body)
        db = SimpleNamespace(
            get=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            rollback=AsyncMock(),
        )

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True) as mock_sig, \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=AsyncMock()) as mock_persist, \
             patch("core.cache.invalidate_user_cache", new=AsyncMock()) as mock_cache, \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(HTTPException) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail, "Invalid internal overtime preference command")

        spy_verify.assert_called_once_with(request, malformed_body)
        mock_sig.assert_called_once_with(
            malformed_body,
            request.headers["x-timestamp"],
            request.headers["x-signature"],
            request.headers["x-api-key"],
        )

        db.get.assert_not_called()
        mock_persist.assert_not_called()
        db.commit.assert_not_called()
        db.refresh.assert_not_called()
        db.rollback.assert_not_called()
        mock_cache.assert_not_called()
        mock_response.assert_not_called()

    async def test_pre_persistence_rejection_non_integer_user_id(self) -> None:
        body = json.dumps({"user_id": "not-an-integer", "offer_overtime_minutes": 5}).encode("utf-8")
        request = _FakeRequest(body=body)
        db = SimpleNamespace(
            get=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            rollback=AsyncMock(),
        )

        event_log: list[str] = []

        def _spy_verify(req: object, b: bytes) -> None:
            event_log.append("verified")
            return _verify_foreign_internal_command(req, b)

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True), \
             patch("api.routers.auth._verify_foreign_internal_command", side_effect=_spy_verify) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=AsyncMock()) as mock_persist, \
             patch("core.cache.invalidate_user_cache", new=AsyncMock()) as mock_cache, \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(HTTPException) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)
            event_log.append("rejected")

        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail, "Invalid internal overtime preference user")

        self.assertEqual(event_log, ["verified", "rejected"])
        spy_verify.assert_called_once_with(request, body)

        db.get.assert_not_called()
        mock_persist.assert_not_called()
        db.commit.assert_not_called()
        db.refresh.assert_not_called()
        db.rollback.assert_not_called()
        mock_cache.assert_not_called()
        mock_response.assert_not_called()

    async def test_pre_persistence_rejection_missing_user(self) -> None:
        user_id = 42
        body = json.dumps({"user_id": user_id, "offer_overtime_minutes": 5}).encode("utf-8")
        request = _FakeRequest(body=body)
        db = SimpleNamespace(
            get=AsyncMock(return_value=None),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            rollback=AsyncMock(),
        )

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True), \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=AsyncMock()) as mock_persist, \
             patch("core.cache.invalidate_user_cache", new=AsyncMock()) as mock_cache, \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(HTTPException) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, "کاربر یافت نشد")

        spy_verify.assert_called_once_with(request, body)
        db.get.assert_awaited_once_with(User, user_id)

        mock_persist.assert_not_called()
        db.commit.assert_not_called()
        db.refresh.assert_not_called()
        db.rollback.assert_not_called()
        mock_cache.assert_not_called()
        mock_response.assert_not_called()

    async def test_pre_persistence_rejection_soft_deleted_user(self) -> None:
        user_id = 88
        body = json.dumps({"user_id": user_id, "offer_overtime_minutes": 3}).encode("utf-8")
        request = _FakeRequest(body=body)
        user = SimpleNamespace(
            id=user_id,
            telegram_id=999,
            offer_overtime_minutes=0,
            is_deleted=True,
        )
        initial_user_state = dict(user.__dict__)
        db = SimpleNamespace(
            get=AsyncMock(return_value=user),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            rollback=AsyncMock(),
        )

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True), \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=AsyncMock()) as mock_persist, \
             patch("core.cache.invalidate_user_cache", new=AsyncMock()) as mock_cache, \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(HTTPException) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, "کاربر یافت نشد")

        spy_verify.assert_called_once_with(request, body)
        db.get.assert_awaited_once_with(User, user_id)

        self.assertEqual(user.__dict__, initial_user_state)
        self.assertEqual(user.offer_overtime_minutes, 0)
        self.assertTrue(user.is_deleted)
        self.assertEqual(user.id, user_id)
        self.assertEqual(user.telegram_id, 999)

        mock_persist.assert_not_called()
        db.commit.assert_not_called()
        db.refresh.assert_not_called()
        db.rollback.assert_not_called()
        mock_cache.assert_not_called()
        mock_response.assert_not_called()

    async def test_post_lookup_service_error_maps_to_http_400_and_suppresses_phases(self) -> None:
        user_id = 7
        target_minutes = 15
        body = json.dumps({"user_id": user_id, "offer_overtime_minutes": target_minutes}).encode("utf-8")
        request = _FakeRequest(body=body)
        user = _InstrumentedUser(user_id=user_id, telegram_id=998877, offer_overtime_minutes=0, is_deleted=False)

        order_tracker: list[str] = []

        async def _fake_get(model, pk):
            order_tracker.append("lookup")
            if model is User and pk == user_id:
                return user
            return None

        async def _fake_commit():
            order_tracker.append("commit")

        async def _fake_refresh(instance):
            order_tracker.append("refresh")

        async def _fake_rollback():
            order_tracker.append("rollback")

        db = SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            commit=AsyncMock(side_effect=_fake_commit),
            refresh=AsyncMock(side_effect=_fake_refresh),
            rollback=AsyncMock(side_effect=_fake_rollback),
        )

        service_error = OfferOvertimePreferenceError(INVALID_OVERTIME_VALUE_MESSAGE)

        async def _failing_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            raise service_error

        persist_mock = AsyncMock(side_effect=_failing_persist)

        async def _fake_invalidate(tg_id):
            order_tracker.append("cache_invalidation")

        mock_cache = AsyncMock(side_effect=_fake_invalidate)

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True) as mock_sig, \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=persist_mock), \
             patch("core.cache.invalidate_user_cache", new=mock_cache), \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(HTTPException) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail, INVALID_OVERTIME_VALUE_MESSAGE)
        self.assertEqual(caught.exception.detail, service_error.message)
        self.assertIs(caught.exception.__cause__, service_error)

        spy_verify.assert_called_once_with(request, body)
        self.assertEqual(spy_verify.call_count, 1)
        mock_sig.assert_called_once_with(
            body,
            request.headers["x-timestamp"],
            request.headers["x-signature"],
            request.headers["x-api-key"],
        )
        self.assertEqual(mock_sig.call_count, 1)

        db.get.assert_awaited_once_with(User, user_id)
        self.assertEqual(db.get.await_count, 1)
        persist_mock.assert_awaited_once_with(db, user, target_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], target_minutes)

        self.assertEqual(user.offer_overtime_minutes, 0)
        self.assertEqual(user.mutation_count, 0)

        db.commit.assert_not_called()
        self.assertEqual(db.commit.await_count, 0)
        db.refresh.assert_not_called()
        self.assertEqual(db.refresh.await_count, 0)
        db.rollback.assert_not_called()
        self.assertEqual(db.rollback.await_count, 0)
        mock_cache.assert_not_called()
        self.assertEqual(mock_cache.await_count, 0)
        mock_response.assert_not_called()
        self.assertEqual(mock_response.call_count, 0)

        self.assertEqual(order_tracker, ["lookup", "persist"])
        self.assertNotIn("commit", order_tracker)
        self.assertNotIn("refresh", order_tracker)
        self.assertNotIn("rollback", order_tracker)
        self.assertNotIn("cache_invalidation", order_tracker)

    async def test_post_lookup_service_not_allowed_maps_to_http_403_and_suppresses_phases(self) -> None:
        user_id = 9
        target_minutes = 3
        body = json.dumps({"user_id": user_id, "offer_overtime_minutes": target_minutes}).encode("utf-8")
        request = _FakeRequest(body=body)
        user = _InstrumentedUser(user_id=user_id, telegram_id=998877, offer_overtime_minutes=0, is_deleted=False)

        order_tracker: list[str] = []

        async def _fake_get(model, pk):
            order_tracker.append("lookup")
            if model is User and pk == user_id:
                return user
            return None

        async def _fake_commit():
            order_tracker.append("commit")

        async def _fake_refresh(instance):
            order_tracker.append("refresh")

        async def _fake_rollback():
            order_tracker.append("rollback")

        db = SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            commit=AsyncMock(side_effect=_fake_commit),
            refresh=AsyncMock(side_effect=_fake_refresh),
            rollback=AsyncMock(side_effect=_fake_rollback),
        )

        service_error = OfferOvertimePreferenceNotAllowedError(OVERTIME_NOT_AVAILABLE_MESSAGE)

        async def _failing_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            raise service_error

        persist_mock = AsyncMock(side_effect=_failing_persist)

        async def _fake_invalidate(tg_id):
            order_tracker.append("cache_invalidation")

        mock_cache = AsyncMock(side_effect=_fake_invalidate)

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True) as mock_sig, \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=persist_mock), \
             patch("core.cache.invalidate_user_cache", new=mock_cache), \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(HTTPException) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, OVERTIME_NOT_AVAILABLE_MESSAGE)
        self.assertEqual(caught.exception.detail, service_error.message)
        self.assertIs(caught.exception.__cause__, service_error)

        spy_verify.assert_called_once_with(request, body)
        self.assertEqual(spy_verify.call_count, 1)
        mock_sig.assert_called_once_with(
            body,
            request.headers["x-timestamp"],
            request.headers["x-signature"],
            request.headers["x-api-key"],
        )
        self.assertEqual(mock_sig.call_count, 1)

        db.get.assert_awaited_once_with(User, user_id)
        self.assertEqual(db.get.await_count, 1)
        persist_mock.assert_awaited_once_with(db, user, target_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], target_minutes)

        self.assertEqual(user.offer_overtime_minutes, 0)
        self.assertEqual(user.mutation_count, 0)

        db.commit.assert_not_called()
        self.assertEqual(db.commit.await_count, 0)
        db.refresh.assert_not_called()
        self.assertEqual(db.refresh.await_count, 0)
        db.rollback.assert_not_called()
        self.assertEqual(db.rollback.await_count, 0)
        mock_cache.assert_not_called()
        self.assertEqual(mock_cache.await_count, 0)
        mock_response.assert_not_called()
        self.assertEqual(mock_response.call_count, 0)

        self.assertEqual(order_tracker, ["lookup", "persist"])
        self.assertNotIn("commit", order_tracker)
        self.assertNotIn("refresh", order_tracker)
        self.assertNotIn("rollback", order_tracker)
        self.assertNotIn("cache_invalidation", order_tracker)

    async def test_post_lookup_commit_failure_preserves_mutation_and_suppresses_phases(self) -> None:
        class SentinelCommitError(Exception):
            """Unique sentinel exception during database commit."""

        sentinel_error = SentinelCommitError("database transaction failed during overtime preference commit")

        user_id = 15
        target_minutes = 5
        body = json.dumps({"user_id": user_id, "offer_overtime_minutes": target_minutes}).encode("utf-8")
        request = _FakeRequest(body=body)
        user = _InstrumentedUser(user_id=user_id, telegram_id=998877, offer_overtime_minutes=0, is_deleted=False)

        order_tracker: list[str] = []

        async def _fake_get(model, pk):
            order_tracker.append("lookup")
            if model is User and pk == user_id:
                return user
            return None

        async def _failing_commit():
            order_tracker.append("commit")
            raise sentinel_error

        async def _fake_refresh(instance):
            order_tracker.append("refresh")

        async def _fake_rollback():
            order_tracker.append("rollback")

        db = SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            commit=AsyncMock(side_effect=_failing_commit),
            refresh=AsyncMock(side_effect=_fake_refresh),
            rollback=AsyncMock(side_effect=_fake_rollback),
        )

        save_result = OvertimePreferenceSaveResult(
            offer_overtime_minutes=target_minutes,
            detail="مهلت پیشنهاد با موفقیت ثبت شد.",
            warning=None,
        )

        async def _succeeding_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            user_arg.offer_overtime_minutes = value_arg
            return save_result

        persist_mock = AsyncMock(side_effect=_succeeding_persist)

        async def _fake_invalidate(tg_id):
            order_tracker.append("cache_invalidation")

        mock_cache = AsyncMock(side_effect=_fake_invalidate)

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True) as mock_sig, \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=persist_mock), \
             patch("core.cache.invalidate_user_cache", new=mock_cache), \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(SentinelCommitError) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertIs(caught.exception, sentinel_error)

        spy_verify.assert_called_once_with(request, body)
        self.assertEqual(spy_verify.call_count, 1)
        mock_sig.assert_called_once_with(
            body,
            request.headers["x-timestamp"],
            request.headers["x-signature"],
            request.headers["x-api-key"],
        )
        self.assertEqual(mock_sig.call_count, 1)

        db.get.assert_awaited_once_with(User, user_id)
        self.assertEqual(db.get.await_count, 1)
        persist_mock.assert_awaited_once_with(db, user, target_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], target_minutes)

        self.assertEqual(user.offer_overtime_minutes, target_minutes)
        self.assertEqual(user.mutation_count, 1)

        db.commit.assert_awaited_once_with()
        self.assertEqual(db.commit.await_count, 1)

        self.assertEqual(order_tracker, ["lookup", "persist", "commit"])
        lookup_idx = order_tracker.index("lookup")
        persist_idx = order_tracker.index("persist")
        commit_idx = order_tracker.index("commit")
        self.assertLess(lookup_idx, persist_idx)
        self.assertLess(persist_idx, commit_idx)

        db.refresh.assert_not_called()
        self.assertEqual(db.refresh.await_count, 0)
        db.rollback.assert_not_called()
        self.assertEqual(db.rollback.await_count, 0)
        mock_cache.assert_not_called()
        self.assertEqual(mock_cache.await_count, 0)
        mock_response.assert_not_called()
        self.assertEqual(mock_response.call_count, 0)

        self.assertNotIn("refresh", order_tracker)
        self.assertNotIn("rollback", order_tracker)
        self.assertNotIn("cache_invalidation", order_tracker)

    async def test_post_commit_refresh_failure_preserves_mutation_and_suppresses_phases(self) -> None:
        class SentinelRefreshError(Exception):
            """Unique sentinel exception during database refresh."""

        sentinel_error = SentinelRefreshError("database refresh failed after commit")

        user_id = 18
        target_minutes = 6
        telegram_id = 887766
        body = json.dumps({"user_id": user_id, "offer_overtime_minutes": target_minutes}).encode("utf-8")
        request = _FakeRequest(body=body)
        user = _InstrumentedUser(
            user_id=user_id,
            telegram_id=telegram_id,
            offer_overtime_minutes=0,
            is_deleted=False,
        )

        order_tracker: list[str] = []

        async def _fake_get(model, pk):
            order_tracker.append("lookup")
            if model is User and pk == user_id:
                return user
            return None

        async def _fake_commit():
            order_tracker.append("commit")

        async def _failing_refresh(instance):
            order_tracker.append("refresh")
            raise sentinel_error

        async def _fake_rollback():
            order_tracker.append("rollback")

        db = SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            commit=AsyncMock(side_effect=_fake_commit),
            refresh=AsyncMock(side_effect=_failing_refresh),
            rollback=AsyncMock(side_effect=_fake_rollback),
        )

        save_result = OvertimePreferenceSaveResult(
            offer_overtime_minutes=target_minutes,
            detail="مهلت پیشنهاد با موفقیت ثبت شد.",
            warning=None,
        )

        async def _succeeding_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            user_arg.offer_overtime_minutes = value_arg
            return save_result

        persist_mock = AsyncMock(side_effect=_succeeding_persist)

        async def _fake_invalidate(tg_id):
            order_tracker.append("cache_invalidation")

        mock_cache = AsyncMock(side_effect=_fake_invalidate)

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True) as mock_sig, \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=persist_mock), \
             patch("core.cache.invalidate_user_cache", new=mock_cache), \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(SentinelRefreshError) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertIs(caught.exception, sentinel_error)

        spy_verify.assert_called_once_with(request, body)
        self.assertEqual(spy_verify.call_count, 1)
        mock_sig.assert_called_once_with(
            body,
            request.headers["x-timestamp"],
            request.headers["x-signature"],
            request.headers["x-api-key"],
        )
        self.assertEqual(mock_sig.call_count, 1)

        db.get.assert_awaited_once_with(User, user_id)
        self.assertEqual(db.get.await_count, 1)
        persist_mock.assert_awaited_once_with(db, user, target_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], target_minutes)

        self.assertEqual(user.offer_overtime_minutes, target_minutes)
        self.assertEqual(user.mutation_count, 1)

        db.commit.assert_awaited_once_with()
        self.assertEqual(db.commit.await_count, 1)

        db.refresh.assert_awaited_once_with(user)
        self.assertEqual(db.refresh.await_count, 1)
        self.assertIs(db.refresh.call_args[0][0], user)

        self.assertEqual(order_tracker, ["lookup", "persist", "commit", "refresh"])
        lookup_idx = order_tracker.index("lookup")
        persist_idx = order_tracker.index("persist")
        commit_idx = order_tracker.index("commit")
        refresh_idx = order_tracker.index("refresh")
        self.assertLess(lookup_idx, persist_idx)
        self.assertLess(persist_idx, commit_idx)
        self.assertLess(commit_idx, refresh_idx)

        db.rollback.assert_not_called()
        self.assertEqual(db.rollback.await_count, 0)
        mock_cache.assert_not_called()
        self.assertEqual(mock_cache.await_count, 0)
        mock_response.assert_not_called()
        self.assertEqual(mock_response.call_count, 0)

        self.assertNotIn("rollback", order_tracker)
        self.assertNotIn("cache_invalidation", order_tracker)
        self.assertNotIn("response_construction", order_tracker)

    async def test_post_refresh_cache_invalidation_failure_preserves_mutation_and_suppresses_response(self) -> None:
        class SentinelCacheInvalidationError(Exception):
            """Unique sentinel exception during cache invalidation."""

        sentinel_error = SentinelCacheInvalidationError("redis connection error during user cache invalidation")

        user_id = 23
        target_minutes = 8
        telegram_id = 665544
        body = json.dumps({"user_id": user_id, "offer_overtime_minutes": target_minutes}).encode("utf-8")
        request = _FakeRequest(body=body)
        user = _InstrumentedUser(
            user_id=user_id,
            telegram_id=telegram_id,
            offer_overtime_minutes=0,
            is_deleted=False,
        )

        order_tracker: list[str] = []

        async def _fake_get(model, pk):
            order_tracker.append("lookup")
            if model is User and pk == user_id:
                return user
            return None

        async def _fake_commit():
            order_tracker.append("commit")

        async def _fake_refresh(instance):
            order_tracker.append("refresh")

        async def _failing_invalidate(tg_id):
            order_tracker.append("cache_invalidation")
            raise sentinel_error

        async def _fake_rollback():
            order_tracker.append("rollback")

        db = SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            commit=AsyncMock(side_effect=_fake_commit),
            refresh=AsyncMock(side_effect=_fake_refresh),
            rollback=AsyncMock(side_effect=_fake_rollback),
        )

        save_result = OvertimePreferenceSaveResult(
            offer_overtime_minutes=target_minutes,
            detail="مهلت پیشنهاد با موفقیت ثبت شد.",
            warning=None,
        )

        async def _succeeding_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            user_arg.offer_overtime_minutes = value_arg
            return save_result

        persist_mock = AsyncMock(side_effect=_succeeding_persist)
        mock_cache = AsyncMock(side_effect=_failing_invalidate)

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True) as mock_sig, \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=persist_mock), \
             patch("core.cache.invalidate_user_cache", new=mock_cache), \
             patch("schemas.UserOfferOvertimeUpdateResponse") as mock_response:

            with self.assertRaises(SentinelCacheInvalidationError) as caught:
                await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertIs(caught.exception, sentinel_error)

        spy_verify.assert_called_once_with(request, body)
        self.assertEqual(spy_verify.call_count, 1)
        mock_sig.assert_called_once_with(
            body,
            request.headers["x-timestamp"],
            request.headers["x-signature"],
            request.headers["x-api-key"],
        )
        self.assertEqual(mock_sig.call_count, 1)

        db.get.assert_awaited_once_with(User, user_id)
        self.assertEqual(db.get.await_count, 1)
        persist_mock.assert_awaited_once_with(db, user, target_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], target_minutes)

        self.assertEqual(user.offer_overtime_minutes, target_minutes)
        self.assertEqual(user.mutation_count, 1)

        db.commit.assert_awaited_once_with()
        self.assertEqual(db.commit.await_count, 1)

        db.refresh.assert_awaited_once_with(user)
        self.assertEqual(db.refresh.await_count, 1)
        self.assertIs(db.refresh.call_args[0][0], user)

        mock_cache.assert_awaited_once_with(telegram_id)
        self.assertEqual(mock_cache.await_count, 1)
        self.assertEqual(mock_cache.call_args[0][0], telegram_id)

        self.assertEqual(order_tracker, ["lookup", "persist", "commit", "refresh", "cache_invalidation"])
        lookup_idx = order_tracker.index("lookup")
        persist_idx = order_tracker.index("persist")
        commit_idx = order_tracker.index("commit")
        refresh_idx = order_tracker.index("refresh")
        cache_idx = order_tracker.index("cache_invalidation")
        self.assertLess(lookup_idx, persist_idx)
        self.assertLess(persist_idx, commit_idx)
        self.assertLess(commit_idx, refresh_idx)
        self.assertLess(refresh_idx, cache_idx)

        db.rollback.assert_not_called()
        self.assertEqual(db.rollback.await_count, 0)
        mock_response.assert_not_called()
        self.assertEqual(mock_response.call_count, 0)

        self.assertNotIn("rollback", order_tracker)
        self.assertNotIn("response_construction", order_tracker)

    async def test_telegram_id_present_success_executes_complete_order_and_returns_response(self) -> None:
        user_id = 31
        target_minutes = 4
        telegram_id = 443322
        body = json.dumps({"user_id": user_id, "offer_overtime_minutes": target_minutes}).encode("utf-8")
        request = _FakeRequest(body=body)
        user = _InstrumentedUser(
            user_id=user_id,
            telegram_id=telegram_id,
            offer_overtime_minutes=0,
            is_deleted=False,
        )

        order_tracker: list[str] = []

        async def _fake_get(model, pk):
            order_tracker.append("lookup")
            if model is User and pk == user_id:
                return user
            return None

        async def _fake_commit():
            order_tracker.append("commit")

        async def _fake_refresh(instance):
            order_tracker.append("refresh")

        async def _fake_invalidate(tg_id):
            order_tracker.append("cache_invalidation")
            return True

        async def _fake_rollback():
            order_tracker.append("rollback")

        db = SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            commit=AsyncMock(side_effect=_fake_commit),
            refresh=AsyncMock(side_effect=_fake_refresh),
            rollback=AsyncMock(side_effect=_fake_rollback),
        )

        save_result = OvertimePreferenceSaveResult(
            offer_overtime_minutes=target_minutes,
            detail="مهلت پیشنهاد با موفقیت ثبت شد.",
            warning="توجه: آزمایشی",
        )

        async def _succeeding_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            user_arg.offer_overtime_minutes = value_arg
            return save_result

        persist_mock = AsyncMock(side_effect=_succeeding_persist)
        mock_cache = AsyncMock(side_effect=_fake_invalidate)

        expected_response = schemas.UserOfferOvertimeUpdateResponse(
            offer_overtime_minutes=save_result.offer_overtime_minutes,
            detail=save_result.detail,
            warning=save_result.warning,
        )

        def _fake_response_constructor(*args, **kwargs):
            order_tracker.append("response_construction")
            return expected_response

        with override_current_server(SERVER_IRAN), \
             patch("api.routers.auth.verify_internal_signature", return_value=True) as mock_sig, \
             patch("api.routers.auth._verify_foreign_internal_command", wraps=_verify_foreign_internal_command) as spy_verify, \
             patch("core.services.offer_overtime_preference_service.persist_overtime_preference", new=persist_mock), \
             patch("core.cache.invalidate_user_cache", new=mock_cache), \
             patch("schemas.UserOfferOvertimeUpdateResponse", side_effect=_fake_response_constructor) as mock_response:

            response = await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertIs(response, expected_response)
        self.assertEqual(response.offer_overtime_minutes, target_minutes)
        self.assertEqual(response.detail, save_result.detail)
        self.assertEqual(response.warning, save_result.warning)

        spy_verify.assert_called_once_with(request, body)
        self.assertEqual(spy_verify.call_count, 1)
        mock_sig.assert_called_once_with(
            body,
            request.headers["x-timestamp"],
            request.headers["x-signature"],
            request.headers["x-api-key"],
        )
        self.assertEqual(mock_sig.call_count, 1)

        db.get.assert_awaited_once_with(User, user_id)
        self.assertEqual(db.get.await_count, 1)
        persist_mock.assert_awaited_once_with(db, user, target_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], target_minutes)

        self.assertEqual(user.offer_overtime_minutes, target_minutes)
        self.assertEqual(user.mutation_count, 1)

        db.commit.assert_awaited_once_with()
        self.assertEqual(db.commit.await_count, 1)

        db.refresh.assert_awaited_once_with(user)
        self.assertEqual(db.refresh.await_count, 1)
        self.assertIs(db.refresh.call_args[0][0], user)

        mock_cache.assert_awaited_once_with(telegram_id)
        self.assertEqual(mock_cache.await_count, 1)
        self.assertEqual(mock_cache.call_args[0][0], telegram_id)

        mock_response.assert_called_once_with(
            offer_overtime_minutes=save_result.offer_overtime_minutes,
            detail=save_result.detail,
            warning=save_result.warning,
        )
        self.assertEqual(mock_response.call_count, 1)
        self.assertEqual(mock_response.call_args.kwargs["offer_overtime_minutes"], save_result.offer_overtime_minutes)
        self.assertEqual(mock_response.call_args.kwargs["detail"], save_result.detail)
        self.assertEqual(mock_response.call_args.kwargs["warning"], save_result.warning)
        self.assertEqual(mock_response.call_args[0], ())

        self.assertEqual(
            order_tracker,
            ["lookup", "persist", "commit", "refresh", "cache_invalidation", "response_construction"],
        )
        lookup_idx = order_tracker.index("lookup")
        persist_idx = order_tracker.index("persist")
        commit_idx = order_tracker.index("commit")
        refresh_idx = order_tracker.index("refresh")
        cache_idx = order_tracker.index("cache_invalidation")
        resp_idx = order_tracker.index("response_construction")
        self.assertLess(lookup_idx, persist_idx)
        self.assertLess(persist_idx, commit_idx)
        self.assertLess(commit_idx, refresh_idx)
        self.assertLess(refresh_idx, cache_idx)
        self.assertLess(cache_idx, resp_idx)

        db.rollback.assert_not_called()
        self.assertEqual(db.rollback.await_count, 0)
        self.assertNotIn("rollback", order_tracker)


if __name__ == "__main__":
    unittest.main()
