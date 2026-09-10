"""Characterization tests for _selection_inference_for_missing_commodity reader boundary failures.

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

from datetime import datetime, timezone  # noqa: E402
from typing import Any  # noqa: E402
import unittest  # noqa: E402
from unittest.mock import AsyncMock, Mock, patch  # noqa: E402

import api.routers.offers as offers_router  # noqa: E402


class SyntheticReaderFailure(Exception):
    """Synthetic exception representing a snapshot reader construction failure."""


class SyntheticRollbackFailure(Exception):
    """Synthetic exception representing an asynchronous database rollback failure."""


class MonitoredBool:
    """Instrumented boolean container tracking explicit __bool__ evaluations."""

    def __init__(self, value: bool, bool_mock: Mock) -> None:
        self._value = bool(value)
        self._bool_mock = bool_mock

    def __bool__(self) -> bool:
        self._bool_mock()
        return self._value


class IsolatedSelectionSettings:
    """Isolated synthetic settings exposing instrumented auto-selection flag."""

    def __init__(self, auto_selection_value: Any, prop_mock: Mock) -> None:
        self._auto_selection_value = auto_selection_value
        self._prop_mock = prop_mock

    @property
    def coin_intelligence_inference_auto_selection_enabled(self) -> Any:
        self._prop_mock()
        return self._auto_selection_value


class StrictFakeSession:
    """Strict fake database session exposing only commit and rollback AsyncMocks."""

    __slots__ = ("commit", "rollback")

    def __init__(self, commit: AsyncMock, rollback: AsyncMock) -> None:
        self.commit = commit
        self.rollback = rollback


class SelectionReaderFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization of reader factory boundary failures in selection inference."""

    def _assert_common_boundary_invariants(
        self,
        *,
        resolver_mock: Mock,
        isolated_settings: IsolatedSelectionSettings,
        clock_mock: Mock,
        prop_mock: Mock,
        bool_mock: Mock,
        reader_mock: Mock,
        rollback_mock: AsyncMock,
        observer_mock: AsyncMock,
        commit_mock: AsyncMock,
        formatter_mock: Mock,
    ) -> None:
        resolver_mock.assert_called_once_with(isolated_settings)
        self.assertIs(resolver_mock.call_args[0][0], isolated_settings)
        self.assertEqual(resolver_mock.call_args[1], {})

        clock_mock.assert_called_once_with(timezone.utc)
        self.assertIs(clock_mock.call_args[0][0], timezone.utc)
        self.assertEqual(clock_mock.call_args[1], {})

        prop_mock.assert_called_once_with()
        self.assertEqual(prop_mock.call_args[0], ())
        self.assertEqual(prop_mock.call_args[1], {})

        bool_mock.assert_called_once_with()
        self.assertEqual(bool_mock.call_args[0], ())
        self.assertEqual(bool_mock.call_args[1], {})

        reader_mock.assert_called_once_with()
        self.assertEqual(reader_mock.call_args[0], ())
        self.assertEqual(reader_mock.call_args[1], {})

        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args[0], ())
        self.assertEqual(rollback_mock.call_args[1], {})

        observer_mock.assert_not_called()
        observer_mock.assert_not_awaited()

        commit_mock.assert_not_called()
        commit_mock.assert_not_awaited()

        formatter_mock.assert_not_called()

    async def test_scenario_1_reader_failure_rolls_back_logs_and_returns_fallback(
        self,
    ) -> None:
        """Reader factory failure with successful rollback logs unavailable and returns fallback."""
        event_log: list[str] = []

        def _on_prop_access() -> None:
            event_log.append("setting_property_accessed")

        prop_mock = Mock(side_effect=_on_prop_access)

        def _on_bool_call() -> None:
            event_log.append("setting_bool_evaluated")

        bool_mock = Mock(side_effect=_on_bool_call)
        monitored_flag = MonitoredBool(True, bool_mock)
        isolated_settings = IsolatedSelectionSettings(monitored_flag, prop_mock)

        synthetic_path = "/synthetic/snapshot/product_authority.json"
        fixed_now = datetime(2026, 9, 10, 8, 30, 0, tzinfo=timezone.utc)
        reader_error = SyntheticReaderFailure("simulated reader boundary failure")

        def _resolve_path(settings_obj: Any) -> str:
            event_log.append("authority_path_resolved")
            return synthetic_path

        resolver_mock = Mock(side_effect=_resolve_path)

        def _clock_now(tz: Any = None) -> datetime:
            event_log.append("utc_clock_evaluated")
            return fixed_now

        clock_mock = Mock(side_effect=_clock_now)
        mock_datetime = Mock()
        mock_datetime.now = clock_mock

        def _reader_factory() -> Any:
            event_log.append("reader_factory_attempted")
            raise reader_error

        reader_mock = Mock(side_effect=_reader_factory)

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
            result = await offers_router._selection_inference_for_missing_commodity(
                session,
                price=54_000_000,
                settlement_type="cash",
                low_date_hint=False,
                pack_hint=False,
            )
            event_log.append("fallback_returned")

        expected_fallback = {
            "mode": "SELECTABLE",
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
        self.assertEqual(pos_args[1], "coin_inference_parse_selection.unavailable")
        self.assertEqual(
            kw_args,
            {
                "level": "warning",
                "action": "coin_inference_parse_selection",
                "result": "abstained",
                "error_class": "SyntheticReaderFailure",
            },
        )
        self.assertEqual(kw_args["error_class"], type(reader_error).__name__)
        self.assertNotEqual(kw_args["error_class"], str(reader_error))

        resolver_index = event_log.index("authority_path_resolved")
        clock_index = event_log.index("utc_clock_evaluated")
        prop_index = event_log.index("setting_property_accessed")
        bool_index = event_log.index("setting_bool_evaluated")
        reader_index = event_log.index("reader_factory_attempted")
        rollback_index = event_log.index("rollback_completed")
        log_index = event_log.index("trading_event_logged")
        return_index = event_log.index("fallback_returned")

        self.assertLess(resolver_index, clock_index)
        self.assertLess(clock_index, prop_index)
        self.assertLess(prop_index, bool_index)
        self.assertLess(bool_index, reader_index)
        self.assertLess(reader_index, rollback_index)
        self.assertLess(rollback_index, log_index)
        self.assertLess(log_index, return_index)

        self._assert_common_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_mock=reader_mock,
            rollback_mock=rollback_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
        )

        expected_events = [
            "authority_path_resolved",
            "utc_clock_evaluated",
            "setting_property_accessed",
            "setting_bool_evaluated",
            "reader_factory_attempted",
            "rollback_completed",
            "trading_event_logged",
            "fallback_returned",
        ]
        self.assertEqual(event_log, expected_events)

    async def test_scenario_2_reader_failure_with_rollback_failure_propagates_rollback_exception(
        self,
    ) -> None:
        """Reader factory failure followed by rollback failure propagates rollback exception directly."""
        event_log: list[str] = []

        def _on_prop_access() -> None:
            event_log.append("setting_property_accessed")

        prop_mock = Mock(side_effect=_on_prop_access)

        def _on_bool_call() -> None:
            event_log.append("setting_bool_evaluated")

        bool_mock = Mock(side_effect=_on_bool_call)
        monitored_flag = MonitoredBool(False, bool_mock)
        isolated_settings = IsolatedSelectionSettings(monitored_flag, prop_mock)

        synthetic_path = "/synthetic/snapshot/product_authority.json"
        fixed_now = datetime(2026, 9, 10, 8, 30, 0, tzinfo=timezone.utc)
        reader_error = SyntheticReaderFailure("simulated reader boundary failure")
        rollback_error = SyntheticRollbackFailure("simulated rollback boundary failure")

        def _resolve_path(settings_obj: Any) -> str:
            event_log.append("authority_path_resolved")
            return synthetic_path

        resolver_mock = Mock(side_effect=_resolve_path)

        def _clock_now(tz: Any = None) -> datetime:
            event_log.append("utc_clock_evaluated")
            return fixed_now

        clock_mock = Mock(side_effect=_clock_now)
        mock_datetime = Mock()
        mock_datetime.now = clock_mock

        def _reader_factory() -> Any:
            event_log.append("reader_factory_attempted")
            raise reader_error

        reader_mock = Mock(side_effect=_reader_factory)

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
                await offers_router._selection_inference_for_missing_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )
            event_log.append("rollback_exception_propagated")

        self.assertIs(cm.exception, rollback_error)
        self.assertIsNot(cm.exception, reader_error)
        self.assertFalse(isinstance(cm.exception, SyntheticReaderFailure))

        log_mock.assert_not_called()

        resolver_index = event_log.index("authority_path_resolved")
        clock_index = event_log.index("utc_clock_evaluated")
        prop_index = event_log.index("setting_property_accessed")
        bool_index = event_log.index("setting_bool_evaluated")
        reader_index = event_log.index("reader_factory_attempted")
        rollback_index = event_log.index("rollback_failed")
        propagated_index = event_log.index("rollback_exception_propagated")

        self.assertLess(resolver_index, clock_index)
        self.assertLess(clock_index, prop_index)
        self.assertLess(prop_index, bool_index)
        self.assertLess(bool_index, reader_index)
        self.assertLess(reader_index, rollback_index)
        self.assertLess(rollback_index, propagated_index)

        self._assert_common_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_mock=reader_mock,
            rollback_mock=rollback_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
        )

        expected_events = [
            "authority_path_resolved",
            "utc_clock_evaluated",
            "setting_property_accessed",
            "setting_bool_evaluated",
            "reader_factory_attempted",
            "rollback_failed",
            "rollback_exception_propagated",
        ]
        self.assertEqual(event_log, expected_events)


if __name__ == "__main__":
    unittest.main()
