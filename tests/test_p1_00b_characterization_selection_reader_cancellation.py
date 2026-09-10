"""Characterization tests for _selection_inference_for_missing_commodity reader cancellation boundaries.

Tracked inventory gap path: docs/refactor/two-server/section-1/inventory/scenario-records.json
Tracked inventory gap ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

This module provides helper-only mocked-boundary evidence only, never seed or
Stage closure; Stage P1-00b remains open.
"""

from __future__ import annotations

import asyncio
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


class MonitoredBool:
    """Instrumented boolean container tracking explicit __bool__ evaluations."""

    __slots__ = ("_value", "_bool_mock")

    def __init__(self, value: bool, bool_mock: Mock) -> None:
        self._value = bool(value)
        self._bool_mock = bool_mock

    def __bool__(self) -> bool:
        self._bool_mock()
        return self._value


class IsolatedSelectionSettings:
    """Isolated synthetic settings exposing instrumented auto-selection flag."""

    __slots__ = ("_auto_selection_value", "_prop_mock")

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


class SelectionReaderCancellationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization of reader cancellation boundaries in selection inference."""

    def _assert_common_boundary_invariants(
        self,
        *,
        resolver_mock: Mock,
        isolated_settings: IsolatedSelectionSettings,
        clock_mock: Mock,
        prop_mock: Mock,
        bool_mock: Mock,
        reader_mock: Mock,
        observer_mock: AsyncMock,
        commit_mock: AsyncMock,
        formatter_mock: Mock,
        session: StrictFakeSession,
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

        observer_mock.assert_not_called()
        observer_mock.assert_not_awaited()

        commit_mock.assert_not_called()
        commit_mock.assert_not_awaited()

        formatter_mock.assert_not_called()

        with self.assertRaises(AttributeError):
            _ = isolated_settings.unconfigured_setting

        with self.assertRaises(AttributeError):
            _ = session.execute

    async def test_scenario_1_direct_reader_cancellation_escapes_without_rollback_or_logging(
        self,
    ) -> None:
        """Direct reader cancellation escapes helper without rollback invocation or logging."""
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
        direct_cancel = asyncio.CancelledError("simulated direct reader cancellation")

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
            raise direct_cancel

        reader_mock = Mock(side_effect=_reader_factory)

        rollback_mock = AsyncMock()
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
            with self.assertRaises(asyncio.CancelledError) as cm:
                await offers_router._selection_inference_for_missing_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )
            event_log.append("direct_cancellation_captured")

        captured_exc = cm.exception
        self.assertIs(captured_exc, direct_cancel)
        self.assertIs(captured_exc.__context__, None)
        self.assertIs(captured_exc.__cause__, None)
        self.assertFalse(captured_exc.__suppress_context__)

        rollback_mock.assert_not_called()
        rollback_mock.assert_not_awaited()
        log_mock.assert_not_called()

        expected_events = [
            "authority_path_resolved",
            "utc_clock_evaluated",
            "setting_property_accessed",
            "setting_bool_evaluated",
            "reader_factory_attempted",
            "direct_cancellation_captured",
        ]
        self.assertEqual(event_log, expected_events)

        resolver_idx = event_log.index("authority_path_resolved")
        clock_idx = event_log.index("utc_clock_evaluated")
        prop_idx = event_log.index("setting_property_accessed")
        bool_idx = event_log.index("setting_bool_evaluated")
        reader_idx = event_log.index("reader_factory_attempted")
        captured_idx = event_log.index("direct_cancellation_captured")

        self.assertLess(resolver_idx, clock_idx)
        self.assertLess(clock_idx, prop_idx)
        self.assertLess(prop_idx, bool_idx)
        self.assertLess(bool_idx, reader_idx)
        self.assertLess(reader_idx, captured_idx)

        self._assert_common_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_mock=reader_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
            session=session,
        )

    async def test_scenario_2_reader_failure_then_rollback_cancellation_escapes_with_context(
        self,
    ) -> None:
        """Reader failure followed by rollback cancellation propagates cancellation with reader error context."""
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
        rollback_cancel = asyncio.CancelledError("simulated rollback cancellation")

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
            event_log.append("rollback_attempted")
            raise rollback_cancel

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
            with self.assertRaises(asyncio.CancelledError) as cm:
                await offers_router._selection_inference_for_missing_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )
            event_log.append("rollback_cancellation_captured")

        captured_exc = cm.exception
        self.assertIs(captured_exc, rollback_cancel)
        self.assertIsNot(captured_exc, reader_error)
        self.assertIs(captured_exc.__context__, reader_error)
        self.assertIs(captured_exc.__cause__, None)
        self.assertFalse(captured_exc.__suppress_context__)

        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args[0], ())
        self.assertEqual(rollback_mock.call_args[1], {})

        log_mock.assert_not_called()

        expected_events = [
            "authority_path_resolved",
            "utc_clock_evaluated",
            "setting_property_accessed",
            "setting_bool_evaluated",
            "reader_factory_attempted",
            "rollback_attempted",
            "rollback_cancellation_captured",
        ]
        self.assertEqual(event_log, expected_events)

        resolver_idx = event_log.index("authority_path_resolved")
        clock_idx = event_log.index("utc_clock_evaluated")
        prop_idx = event_log.index("setting_property_accessed")
        bool_idx = event_log.index("setting_bool_evaluated")
        reader_idx = event_log.index("reader_factory_attempted")
        rollback_idx = event_log.index("rollback_attempted")
        captured_idx = event_log.index("rollback_cancellation_captured")

        self.assertLess(resolver_idx, clock_idx)
        self.assertLess(clock_idx, prop_idx)
        self.assertLess(prop_idx, bool_idx)
        self.assertLess(bool_idx, reader_idx)
        self.assertLess(reader_idx, rollback_idx)
        self.assertLess(rollback_idx, captured_idx)

        self._assert_common_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_mock=reader_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
            session=session,
        )

    async def test_scenario_3_reader_failure_then_logging_cancellation_escapes_with_context(
        self,
    ) -> None:
        """Reader failure followed by logging cancellation propagates cancellation with reader error context."""
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
        log_cancel = asyncio.CancelledError("simulated logging cancellation")

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

        def _failing_log(*args: Any, **kwargs: Any) -> Any:
            event_log.append("trading_event_attempted")
            raise log_cancel

        log_mock = Mock(side_effect=_failing_log)

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
                await offers_router._selection_inference_for_missing_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )
            event_log.append("log_cancellation_captured")

        captured_exc = cm.exception
        self.assertIs(captured_exc, log_cancel)
        self.assertIsNot(captured_exc, reader_error)
        self.assertIs(captured_exc.__context__, reader_error)
        self.assertIs(captured_exc.__cause__, None)
        self.assertFalse(captured_exc.__suppress_context__)

        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args[0], ())
        self.assertEqual(rollback_mock.call_args[1], {})

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
        self.assertNotEqual(kw_args["error_class"], type(log_cancel).__name__)

        expected_events = [
            "authority_path_resolved",
            "utc_clock_evaluated",
            "setting_property_accessed",
            "setting_bool_evaluated",
            "reader_factory_attempted",
            "rollback_completed",
            "trading_event_attempted",
            "log_cancellation_captured",
        ]
        self.assertEqual(event_log, expected_events)

        resolver_idx = event_log.index("authority_path_resolved")
        clock_idx = event_log.index("utc_clock_evaluated")
        prop_idx = event_log.index("setting_property_accessed")
        bool_idx = event_log.index("setting_bool_evaluated")
        reader_idx = event_log.index("reader_factory_attempted")
        rollback_idx = event_log.index("rollback_completed")
        log_idx = event_log.index("trading_event_attempted")
        captured_idx = event_log.index("log_cancellation_captured")

        self.assertLess(resolver_idx, clock_idx)
        self.assertLess(clock_idx, prop_idx)
        self.assertLess(prop_idx, bool_idx)
        self.assertLess(bool_idx, reader_idx)
        self.assertLess(reader_idx, rollback_idx)
        self.assertLess(rollback_idx, log_idx)
        self.assertLess(log_idx, captured_idx)

        self._assert_common_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_mock=reader_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
            session=session,
        )


if __name__ == "__main__":
    unittest.main()
