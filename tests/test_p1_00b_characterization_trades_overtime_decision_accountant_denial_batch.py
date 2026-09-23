"""Stage P1-00b characterization: accountant denial for trades overtime decision handlers."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import tests.test_p1_00_characterization_network_isolation
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()
tests.test_p1_00_characterization_network_isolation.install_p1_00_network_fence()
_FENCE = tests.test_p1_00_characterization_network_isolation._FENCE

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from api.routers import trades
from core.services.accountant_relation_service import EffectiveOwnerActor


class StrictInertDBSentinel:
    """Strict inert sentinel ensuring no database query or execution occurs."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"Unexpected database access on strict inert sentinel: {name}")

    def __call__(self, *args, **kwargs) -> None:
        raise AssertionError("Unexpected invocation of strict inert sentinel")


class TradesOvertimeDecisionAccountantDenialBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner_user = SimpleNamespace(id=101)
        self.actor_user = SimpleNamespace(id=202)
        self.context = EffectiveOwnerActor(
            owner_user=self.owner_user,  # type: ignore[arg-type]
            actor_user=self.actor_user,  # type: ignore[arg-type]
            relation=None,
            is_accountant_context=True,
        )
        self.db = StrictInertDBSentinel()

    async def test_get_overtime_request_denies_accountant_before_lookup_or_serialization(self) -> None:
        mock_load_request = AsyncMock()
        mock_public_payload = Mock(spec=trades._overtime_request_public_payload)
        mock_current_server = Mock(spec=trades.current_server)

        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=mock_load_request,
        ), patch(
            "api.routers.trades._overtime_request_public_payload",
            new=mock_public_payload,
        ), patch(
            "api.routers.trades.current_server",
            new=mock_current_server,
        ):
            denied_connects_before = len(_FENCE["denied_connects"])
            live_redis_before = len(_FENCE["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await trades.get_overtime_request(
                    request_public_id="req_test_get_1",
                    db=self.db,  # type: ignore[arg-type]
                    context=self.context,
                )

            self.assertEqual(len(_FENCE["denied_connects"]), denied_connects_before)
            self.assertEqual(len(_FENCE["live_redis_constructs"]), live_redis_before)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(
                exc_info.exception.detail,
                trades.ACCOUNTANT_MARKET_BLOCKED_DETAIL,
            )
            self.assertEqual(
                exc_info.exception.detail,
                "حسابدار دسترسی به بازار ندارد.",
            )

            mock_load_request.assert_not_called()
            mock_load_request.assert_not_awaited()
            mock_public_payload.assert_not_called()
            mock_current_server.assert_not_called()

    async def test_reject_overtime_request_denies_accountant_before_locked_lookup_or_mutation(self) -> None:
        mock_load_request = AsyncMock()
        mock_reject_by_owner = AsyncMock()
        mock_current_server = Mock(spec=trades.current_server)

        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=mock_load_request,
        ), patch(
            "api.routers.trades.reject_by_owner",
            new=mock_reject_by_owner,
        ), patch(
            "api.routers.trades.current_server",
            new=mock_current_server,
        ):
            denied_connects_before = len(_FENCE["denied_connects"])
            live_redis_before = len(_FENCE["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await trades.reject_overtime_request(
                    request_public_id="req_test_reject_1",
                    db=self.db,  # type: ignore[arg-type]
                    context=self.context,
                )

            self.assertEqual(len(_FENCE["denied_connects"]), denied_connects_before)
            self.assertEqual(len(_FENCE["live_redis_constructs"]), live_redis_before)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(
                exc_info.exception.detail,
                trades.ACCOUNTANT_MARKET_BLOCKED_DETAIL,
            )
            self.assertEqual(
                exc_info.exception.detail,
                "حسابدار دسترسی به بازار ندارد.",
            )

            mock_load_request.assert_not_called()
            mock_load_request.assert_not_awaited()
            mock_reject_by_owner.assert_not_called()
            mock_reject_by_owner.assert_not_awaited()
            mock_current_server.assert_not_called()

    async def test_cancel_overtime_request_denies_accountant_before_lookup_or_forwarding(self) -> None:
        mock_load_request = AsyncMock()
        mock_cancel_by_requester = AsyncMock()
        mock_forward_cancel = AsyncMock()
        mock_current_server = Mock(spec=trades.current_server)

        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=mock_load_request,
        ), patch(
            "api.routers.trades.cancel_by_requester",
            new=mock_cancel_by_requester,
        ), patch(
            "api.routers.trades.forward_overtime_requester_cancel",
            new=mock_forward_cancel,
        ), patch(
            "api.routers.trades.current_server",
            new=mock_current_server,
        ):
            denied_connects_before = len(_FENCE["denied_connects"])
            live_redis_before = len(_FENCE["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await trades.cancel_overtime_request(
                    request_public_id="req_test_cancel_1",
                    db=self.db,  # type: ignore[arg-type]
                    context=self.context,
                )

            self.assertEqual(len(_FENCE["denied_connects"]), denied_connects_before)
            self.assertEqual(len(_FENCE["live_redis_constructs"]), live_redis_before)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(
                exc_info.exception.detail,
                trades.ACCOUNTANT_MARKET_BLOCKED_DETAIL,
            )
            self.assertEqual(
                exc_info.exception.detail,
                "حسابدار دسترسی به بازار ندارد.",
            )

            mock_load_request.assert_not_called()
            mock_load_request.assert_not_awaited()
            mock_cancel_by_requester.assert_not_called()
            mock_cancel_by_requester.assert_not_awaited()
            mock_forward_cancel.assert_not_called()
            mock_forward_cancel.assert_not_awaited()
            mock_current_server.assert_not_called()


if __name__ == "__main__":
    unittest.main()
