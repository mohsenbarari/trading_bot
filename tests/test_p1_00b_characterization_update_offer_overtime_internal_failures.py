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


if __name__ == "__main__":
    unittest.main()
