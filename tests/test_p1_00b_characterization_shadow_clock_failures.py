"""Characterization tests for _shadow_inference_for_implicit_commodity UTC clock failure boundary.

Tracked inventory gap path: docs/refactor/two-server/section-1/inventory/scenario-records.json
Tracked inventory gap ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

This module provides helper-only mocked-boundary evidence only, never seed or
Stage closure.
"""

from __future__ import annotations

import os

# Set unit-test environment configuration before importing application modules.
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (  # noqa: E402
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import asyncio  # noqa: E402
from datetime import timezone  # noqa: E402
from typing import Any  # noqa: E402
import unittest  # noqa: E402
from unittest.mock import AsyncMock, Mock, patch  # noqa: E402

import api.routers.offers as offers_router  # noqa: E402


class SyntheticClockFailure(Exception):
    """Synthetic exception representing a UTC clock failure."""


class SyntheticRollbackFailure(Exception):
    """Synthetic exception representing an asynchronous database rollback failure."""


class IsolatedSettings:
    """Isolated settings object exposing only the inference preview flag."""

    coin_intelligence_inference_preview_enabled: bool = True


class StrictFakeSession:
    """Strict fake database session exposing only commit and rollback AsyncMocks."""

    __slots__ = ("commit", "rollback")

    def __init__(self, commit: AsyncMock, rollback: AsyncMock) -> None:
        self.commit = commit
        self.rollback = rollback


class ShadowClockFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization of UTC clock failure boundary in shadow inference."""

    def _assert_common_boundary_invariants(
        self,
        *,
        resolver_mock: Mock,
        isolated_settings: IsolatedSettings,
        clock_mock: Mock,
        reader_mock: Mock,
        rollback_mock: AsyncMock,
        observer_mock: AsyncMock,
        commit_mock: AsyncMock,
        formatter_mock: Mock,
    ) -> None:
        resolver_mock.assert_called_once_with(isolated_settings)
        self.assertEqual(len(resolver_mock.call_args[0]), 1)
        self.assertIs(resolver_mock.call_args[0][0], isolated_settings)
        self.assertEqual(resolver_mock.call_args[1], {})

        clock_mock.assert_called_once_with(timezone.utc)
        self.assertEqual(len(clock_mock.call_args[0]), 1)
        self.assertIs(clock_mock.call_args[0][0], timezone.utc)
        self.assertEqual(clock_mock.call_args[1], {})

        reader_mock.assert_not_called()

        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args[0], ())
        self.assertEqual(rollback_mock.call_args[1], {})

        observer_mock.assert_not_called()
        observer_mock.assert_not_awaited()

        commit_mock.assert_not_called()
        commit_mock.assert_not_awaited()

        formatter_mock.assert_not_called()

    async def test_scenario_1_clock_failure_rolls_back_logs_and_returns_fallback(
        self,
    ) -> None:
        """UTC clock evaluation failure with successful rollback logs unavailable and returns fallback."""
        event_log: list[str] = []
        isolated_settings = IsolatedSettings()
        synthetic_path = "/synthetic/snapshot/product_authority.json"
        clock_error = SyntheticClockFailure("simulated utc clock boundary failure")

        def _resolve_path(settings_obj: Any) -> str:
            event_log.append("authority_path_resolved")
            return synthetic_path

        resolver_mock = Mock(side_effect=_resolve_path)

        def _clock_now(tz: Any = None) -> Any:
            event_log.append("clock_attempted")
            raise clock_error

        clock_mock = Mock(side_effect=_clock_now)
        mock_datetime = Mock()
        mock_datetime.now = clock_mock

        reader_mock = Mock()

        async def _rollback_impl(*args: Any, **kwargs: Any) -> None:
            event_log.append("rollback_completed")

        rollback_mock = AsyncMock(side_effect=_rollback_impl)
        commit_mock = AsyncMock()
        session = StrictFakeSession(commit=commit_mock, rollback=rollback_mock)

        observer_mock = AsyncMock()
        formatter_mock = Mock()

        def _log_trading_event(*args: Any, **kwargs: Any) -> dict[str, Any]:
            event_log.append("trading_event_logged")
            return {}

        log_mock = Mock(side_effect=_log_trading_event)

        with (
            patch.object(offers_router, "settings", isolated_settings),
            patch.object(
                offers_router,
                "configured_product_snapshot_authority_path",
                resolver_mock,
            ),
            patch.object(offers_router, "datetime", mock_datetime),
            patch.object(
                offers_router,
                "_configured_product_snapshot_reader",
                reader_mock,
            ),
            patch.object(
                offers_router,
                "observe_coin_inference_shadow",
                observer_mock,
            ),
            patch.object(offers_router, "log_trading_event", log_mock),
            patch.object(
                offers_router,
                "_catalog_inference_shadow_payload",
                formatter_mock,
            ),
        ):
            result = await offers_router._shadow_inference_for_implicit_commodity(
                session,
                price=54_000_000,
                settlement_type="cash",
            )
            event_log.append("fallback_returned")

        expected_fallback = {
            "mode": "SHADOW_ONLY",
            "status": "ABSTAIN",
            "decision_key": None,
            "snapshot_generated_at_utc": None,
            "snapshot_receipt": None,
            "reason": "INFERENCE_UNAVAILABLE",
            "candidates": [],
        }
        self.assertEqual(result, expected_fallback)
        self.assertEqual(len(result), 7)
        self.assertEqual(
            result.keys(),
            {
                "mode",
                "status",
                "decision_key",
                "snapshot_generated_at_utc",
                "snapshot_receipt",
                "reason",
                "candidates",
            },
        )
        self.assertIs(result["decision_key"], None)
        self.assertIs(result["snapshot_generated_at_utc"], None)
        self.assertIs(result["snapshot_receipt"], None)
        self.assertEqual(result["candidates"], [])
        self.assertNotIn("edit_candidates", result)

        log_mock.assert_called_once()
        self.assertEqual(len(log_mock.call_args_list), 1)
        pos_args, kw_args = log_mock.call_args
        self.assertEqual(len(pos_args), 2)
        self.assertIs(pos_args[0], offers_router.logger)
        self.assertEqual(pos_args[1], "coin_inference_parse_shadow.unavailable")
        self.assertEqual(
            kw_args,
            {
                "level": "warning",
                "action": "coin_inference_parse_shadow",
                "result": "abstained",
                "error_class": "SyntheticClockFailure",
            },
        )
        self.assertEqual(kw_args["error_class"], type(clock_error).__name__)
        self.assertNotEqual(kw_args["error_class"], str(clock_error))

        resolution_index = event_log.index("authority_path_resolved")
        clock_index = event_log.index("clock_attempted")
        rollback_index = event_log.index("rollback_completed")
        log_index = event_log.index("trading_event_logged")
        return_index = event_log.index("fallback_returned")
        self.assertLess(resolution_index, clock_index)
        self.assertLess(clock_index, rollback_index)
        self.assertLess(rollback_index, log_index)
        self.assertLess(log_index, return_index)

        self._assert_common_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            reader_mock=reader_mock,
            rollback_mock=rollback_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
        )

        expected_events = [
            "authority_path_resolved",
            "clock_attempted",
            "rollback_completed",
            "trading_event_logged",
            "fallback_returned",
        ]
        self.assertEqual(event_log, expected_events)

    async def test_scenario_2_clock_failure_with_rollback_failure_propagates_rollback_exception(
        self,
    ) -> None:
        """UTC clock evaluation failure followed by rollback failure propagates rollback exception directly."""
        event_log: list[str] = []
        isolated_settings = IsolatedSettings()
        synthetic_path = "/synthetic/snapshot/product_authority.json"
        clock_error = SyntheticClockFailure("simulated utc clock boundary failure")
        rollback_error = SyntheticRollbackFailure("simulated rollback boundary failure")

        def _resolve_path(settings_obj: Any) -> str:
            event_log.append("authority_path_resolved")
            return synthetic_path

        resolver_mock = Mock(side_effect=_resolve_path)

        def _clock_now(tz: Any = None) -> Any:
            event_log.append("clock_attempted")
            raise clock_error

        clock_mock = Mock(side_effect=_clock_now)
        mock_datetime = Mock()
        mock_datetime.now = clock_mock

        reader_mock = Mock()

        async def _failing_rollback(*args: Any, **kwargs: Any) -> None:
            event_log.append("rollback_failed")
            raise rollback_error

        rollback_mock = AsyncMock(side_effect=_failing_rollback)
        commit_mock = AsyncMock()
        session = StrictFakeSession(commit=commit_mock, rollback=rollback_mock)

        observer_mock = AsyncMock()
        formatter_mock = Mock()
        log_mock = Mock()

        with (
            patch.object(offers_router, "settings", isolated_settings),
            patch.object(
                offers_router,
                "configured_product_snapshot_authority_path",
                resolver_mock,
            ),
            patch.object(offers_router, "datetime", mock_datetime),
            patch.object(
                offers_router,
                "_configured_product_snapshot_reader",
                reader_mock,
            ),
            patch.object(
                offers_router,
                "observe_coin_inference_shadow",
                observer_mock,
            ),
            patch.object(offers_router, "log_trading_event", log_mock),
            patch.object(
                offers_router,
                "_catalog_inference_shadow_payload",
                formatter_mock,
            ),
        ):
            with self.assertRaises(SyntheticRollbackFailure) as cm:
                await offers_router._shadow_inference_for_implicit_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                )
            event_log.append("rollback_exception_propagated")

        self.assertIs(cm.exception, rollback_error)
        self.assertIsNot(cm.exception, clock_error)
        self.assertFalse(isinstance(cm.exception, SyntheticClockFailure))

        log_mock.assert_not_called()

        resolution_index = event_log.index("authority_path_resolved")
        clock_index = event_log.index("clock_attempted")
        rollback_index = event_log.index("rollback_failed")
        propagation_index = event_log.index("rollback_exception_propagated")
        self.assertLess(resolution_index, clock_index)
        self.assertLess(clock_index, rollback_index)
        self.assertLess(rollback_index, propagation_index)

        self._assert_common_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            reader_mock=reader_mock,
            rollback_mock=rollback_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
        )

        expected_events = [
            "authority_path_resolved",
            "clock_attempted",
            "rollback_failed",
            "rollback_exception_propagated",
        ]
        self.assertEqual(event_log, expected_events)

    async def test_scenario_3_clock_failure_with_logging_cancellation_propagates_cancellation(
        self,
    ) -> None:
        """UTC clock failure followed by rollback and logging CancelledError propagates cancellation."""
        event_log: list[str] = []
        isolated_settings = IsolatedSettings()
        synthetic_path = "/synthetic/snapshot/product_authority.json"
        clock_error = SyntheticClockFailure("simulated utc clock boundary failure")
        cancel_error = asyncio.CancelledError("simulated logging cancellation")

        def _resolve_path(settings_obj: Any) -> str:
            event_log.append("authority_path_resolved")
            return synthetic_path

        resolver_mock = Mock(side_effect=_resolve_path)

        def _clock_now(tz: Any = None) -> Any:
            event_log.append("clock_attempted")
            raise clock_error

        clock_mock = Mock(side_effect=_clock_now)
        mock_datetime = Mock()
        mock_datetime.now = clock_mock

        reader_mock = Mock()

        async def _rollback_impl(*args: Any, **kwargs: Any) -> None:
            event_log.append("rollback_completed")

        rollback_mock = AsyncMock(side_effect=_rollback_impl)
        commit_mock = AsyncMock()
        session = StrictFakeSession(commit=commit_mock, rollback=rollback_mock)

        observer_mock = AsyncMock()
        formatter_mock = Mock()

        def _log_trading_event(*args: Any, **kwargs: Any) -> Any:
            event_log.append("logging_attempted")
            raise cancel_error

        log_mock = Mock(side_effect=_log_trading_event)

        result_sentinel = object()
        result = result_sentinel

        with (
            patch.object(offers_router, "settings", isolated_settings),
            patch.object(
                offers_router,
                "configured_product_snapshot_authority_path",
                resolver_mock,
            ),
            patch.object(offers_router, "datetime", mock_datetime),
            patch.object(
                offers_router,
                "_configured_product_snapshot_reader",
                reader_mock,
            ),
            patch.object(
                offers_router,
                "observe_coin_inference_shadow",
                observer_mock,
            ),
            patch.object(offers_router, "log_trading_event", log_mock),
            patch.object(
                offers_router,
                "_catalog_inference_shadow_payload",
                formatter_mock,
            ),
        ):
            with self.assertRaises(asyncio.CancelledError) as cm:
                result = await offers_router._shadow_inference_for_implicit_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                )
            event_log.append("cancellation_propagated")

        self.assertIs(result, result_sentinel)
        self.assertIsNot(result, None)

        escaping_exception = cm.exception
        self.assertIs(escaping_exception, cancel_error)
        self.assertIsInstance(escaping_exception, asyncio.CancelledError)
        self.assertIsNot(escaping_exception, clock_error)
        self.assertFalse(isinstance(escaping_exception, SyntheticClockFailure))

        self.assertIs(escaping_exception.__context__, clock_error)
        self.assertIsInstance(escaping_exception.__context__, SyntheticClockFailure)
        self.assertIsInstance(escaping_exception.__context__, Exception)
        self.assertIsNot(escaping_exception, escaping_exception.__context__)

        log_mock.assert_called_once()
        self.assertEqual(len(log_mock.call_args_list), 1)
        pos_args, kw_args = log_mock.call_args
        self.assertEqual(len(pos_args), 2)
        self.assertIs(pos_args[0], offers_router.logger)
        self.assertEqual(pos_args[1], "coin_inference_parse_shadow.unavailable")
        self.assertEqual(
            kw_args,
            {
                "level": "warning",
                "action": "coin_inference_parse_shadow",
                "result": "abstained",
                "error_class": "SyntheticClockFailure",
            },
        )
        self.assertEqual(kw_args["error_class"], type(clock_error).__name__)
        self.assertEqual(kw_args["error_class"], "SyntheticClockFailure")
        self.assertNotEqual(kw_args["error_class"], type(cancel_error).__name__)
        self.assertNotEqual(kw_args["error_class"], "CancelledError")
        self.assertNotEqual(kw_args["error_class"], str(clock_error))
        self.assertNotEqual(kw_args["error_class"], str(cancel_error))
        self.assertNotIn("simulated", kw_args["error_class"])

        resolution_index = event_log.index("authority_path_resolved")
        clock_index = event_log.index("clock_attempted")
        rollback_index = event_log.index("rollback_completed")
        log_index = event_log.index("logging_attempted")
        propagation_index = event_log.index("cancellation_propagated")
        self.assertLess(resolution_index, clock_index)
        self.assertLess(clock_index, rollback_index)
        self.assertLess(rollback_index, log_index)
        self.assertLess(log_index, propagation_index)

        self._assert_common_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            reader_mock=reader_mock,
            rollback_mock=rollback_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
        )

        self.assertEqual(reader_mock.call_count, 0)
        self.assertEqual(formatter_mock.call_count, 0)
        self.assertEqual(observer_mock.call_count, 0)
        self.assertEqual(observer_mock.await_count, 0)
        self.assertEqual(commit_mock.call_count, 0)
        self.assertEqual(commit_mock.await_count, 0)
        self.assertEqual(rollback_mock.call_count, 1)
        self.assertEqual(rollback_mock.await_count, 1)

        expected_events = [
            "authority_path_resolved",
            "clock_attempted",
            "rollback_completed",
            "logging_attempted",
            "cancellation_propagated",
        ]
        self.assertEqual(event_log, expected_events)


if __name__ == "__main__":
    unittest.main()
