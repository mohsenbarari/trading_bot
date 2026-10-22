from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

import api.routers.trading_settings as trading_settings_router
from api.routers.trading_settings import get_market_state, get_settings


class TradingSettingsReadDependencyFailuresCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Bounded P1-00b characterization for trading_settings read dependency failures.

    Canonical blocker P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE remains OPEN.
    """

    def setUp(self) -> None:
        super().setUp()
        self._fence = install_p1_00_network_fence()
        self.assertTrue(self._fence["installed"])
        self._denied_connects_before = len(self._fence["denied_connects"])
        self._live_redis_constructs_before = len(self._fence["live_redis_constructs"])

        self._original_symbols = {
            "get_settings": trading_settings_router.get_settings,
            "get_market_state": trading_settings_router.get_market_state,
            "load_trading_settings_async": trading_settings_router.load_trading_settings_async,
            "get_market_runtime_view": trading_settings_router.get_market_runtime_view,
            "_serialize_trading_settings": trading_settings_router._serialize_trading_settings,
            "TradingSettingsResponse": trading_settings_router.TradingSettingsResponse,
            "MarketRuntimeStateResponse": trading_settings_router.MarketRuntimeStateResponse,
        }

        self.load_trading_settings_async_mock = AsyncMock(
            name="load_trading_settings_async"
        )
        self.get_market_runtime_view_mock = AsyncMock(name="get_market_runtime_view")
        self.serialize_trading_settings_mock = Mock(
            name="_serialize_trading_settings",
            side_effect=AssertionError(
                "_serialize_trading_settings must not be called on dependency failure"
            ),
        )
        self.trading_settings_response_mock = Mock(
            name="TradingSettingsResponse",
            side_effect=AssertionError(
                "TradingSettingsResponse must not be constructed on dependency failure"
            ),
        )
        self.market_runtime_state_response_mock = Mock(
            name="MarketRuntimeStateResponse",
            side_effect=AssertionError(
                "MarketRuntimeStateResponse must not be constructed on dependency failure"
            ),
        )

        self._patch_stack = ExitStack()
        self.addCleanup(self._cleanup_and_verify_hermetic_state)
        self._patch_stack.enter_context(
            patch.object(
                trading_settings_router,
                "load_trading_settings_async",
                new=self.load_trading_settings_async_mock,
            )
        )
        self._patch_stack.enter_context(
            patch.object(
                trading_settings_router,
                "get_market_runtime_view",
                new=self.get_market_runtime_view_mock,
            )
        )
        self._patch_stack.enter_context(
            patch.object(
                trading_settings_router,
                "_serialize_trading_settings",
                new=self.serialize_trading_settings_mock,
            )
        )
        self._patch_stack.enter_context(
            patch.object(
                trading_settings_router,
                "TradingSettingsResponse",
                new=self.trading_settings_response_mock,
            )
        )
        self._patch_stack.enter_context(
            patch.object(
                trading_settings_router,
                "MarketRuntimeStateResponse",
                new=self.market_runtime_state_response_mock,
            )
        )

    def _assert_fence_counters_unchanged(self) -> None:
        self.assertEqual(
            len(self._fence["denied_connects"]),
            self._denied_connects_before,
        )
        self.assertEqual(
            len(self._fence["live_redis_constructs"]),
            self._live_redis_constructs_before,
        )

    def _cleanup_and_verify_hermetic_state(self) -> None:
        self._patch_stack.close()
        for attr_name, original_obj in self._original_symbols.items():
            self.assertIs(
                getattr(trading_settings_router, attr_name),
                original_obj,
            )
        self._assert_fence_counters_unchanged()

    async def test_get_settings_propagates_loader_failure_before_serialization(
        self,
    ) -> None:
        expected_error = RuntimeError("load_trading_settings_async failure sentinel")
        self.load_trading_settings_async_mock.side_effect = expected_error
        self.assertIs(get_settings, self._original_symbols["get_settings"])

        with self.assertRaises(RuntimeError) as exc_info:
            await get_settings()

        self.assertIs(exc_info.exception, expected_error)
        self.assertEqual(self.load_trading_settings_async_mock.call_count, 1)
        self.assertEqual(self.load_trading_settings_async_mock.await_count, 1)
        self.assertEqual(self.load_trading_settings_async_mock.call_args, call())
        self.assertEqual(self.load_trading_settings_async_mock.await_args, call())
        self.assertEqual(self.load_trading_settings_async_mock.call_args.args, ())
        self.assertEqual(self.load_trading_settings_async_mock.call_args.kwargs, {})

        self.assertEqual(self.get_market_runtime_view_mock.call_count, 0)
        self.assertEqual(self.get_market_runtime_view_mock.await_count, 0)
        self.assertEqual(self.serialize_trading_settings_mock.call_count, 0)
        self.assertEqual(self.trading_settings_response_mock.call_count, 0)
        self.assertEqual(self.market_runtime_state_response_mock.call_count, 0)
        self._assert_fence_counters_unchanged()

    async def test_get_market_state_propagates_runtime_view_failure_before_response(
        self,
    ) -> None:
        opaque_db = object()
        expected_error = RuntimeError("get_market_runtime_view failure sentinel")
        self.get_market_runtime_view_mock.side_effect = expected_error
        self.assertIs(get_market_state, self._original_symbols["get_market_state"])

        with self.assertRaises(RuntimeError) as exc_info:
            await get_market_state(db=opaque_db)

        self.assertIs(exc_info.exception, expected_error)
        self.assertEqual(self.get_market_runtime_view_mock.call_count, 1)
        self.assertEqual(self.get_market_runtime_view_mock.await_count, 1)
        self.assertEqual(self.get_market_runtime_view_mock.call_args, call(opaque_db))
        self.assertEqual(self.get_market_runtime_view_mock.await_args, call(opaque_db))
        self.assertEqual( len(self.get_market_runtime_view_mock.call_args.args), 1)
        self.assertIs(self.get_market_runtime_view_mock.call_args.args[0], opaque_db)
        self.assertEqual(self.get_market_runtime_view_mock.call_args.kwargs, {})

        self.assertEqual(self.load_trading_settings_async_mock.call_count, 0)
        self.assertEqual(self.load_trading_settings_async_mock.await_count, 0)
        self.assertEqual(self.serialize_trading_settings_mock.call_count, 0)
        self.assertEqual(self.trading_settings_response_mock.call_count, 0)
        self.assertEqual(self.market_runtime_state_response_mock.call_count, 0)
        self._assert_fence_counters_unchanged()


if __name__ == "__main__":
    unittest.main()
