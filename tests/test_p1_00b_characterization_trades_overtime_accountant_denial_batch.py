"""Stage P1-00b characterization: accountant denial for trades overtime reconnect handlers."""

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


class TradesOvertimeAccountantDenialBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_pending_owner_denies_accountant_before_home_lookup_or_serialization(self) -> None:
        mock_list_requests = AsyncMock()
        mock_current_server = Mock(spec=trades.current_server)
        mock_public_payload = Mock(spec=trades._overtime_request_public_payload)

        with patch(
            "api.routers.trades.list_nonterminal_overtime_requests",
            new=mock_list_requests,
        ), patch(
            "api.routers.trades.current_server",
            new=mock_current_server,
        ), patch(
            "api.routers.trades._overtime_request_public_payload",
            new=mock_public_payload,
        ):
            denied_connects_before = len(_FENCE["denied_connects"])
            live_redis_before = len(_FENCE["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await trades.get_pending_owner_overtime_requests(
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

            mock_list_requests.assert_not_called()
            mock_list_requests.assert_not_awaited()
            mock_current_server.assert_not_called()
            mock_public_payload.assert_not_called()

    async def test_pending_requester_denies_accountant_before_source_lookup_or_serialization(self) -> None:
        mock_list_requests = AsyncMock()
        mock_current_server = Mock(spec=trades.current_server)
        mock_public_payload = Mock(spec=trades._overtime_request_public_payload)

        with patch(
            "api.routers.trades.list_nonterminal_overtime_requests",
            new=mock_list_requests,
        ), patch(
            "api.routers.trades.current_server",
            new=mock_current_server,
        ), patch(
            "api.routers.trades._overtime_request_public_payload",
            new=mock_public_payload,
        ):
            denied_connects_before = len(_FENCE["denied_connects"])
            live_redis_before = len(_FENCE["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await trades.get_pending_requester_overtime_requests(
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

            mock_list_requests.assert_not_called()
            mock_list_requests.assert_not_awaited()
            mock_current_server.assert_not_called()
            mock_public_payload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
