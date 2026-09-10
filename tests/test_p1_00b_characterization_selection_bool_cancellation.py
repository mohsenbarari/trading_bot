"""Hermetic characterization of auto-selection bool cancellation in _selection_inference_for_missing_commodity.

Tracked inventory gap path: docs/refactor/two-server/section-1/inventory/scenario-records.json
Gap ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

This characterization is strictly limited to helper-level mocked boundaries around
the real api.routers.offers._selection_inference_for_missing_commodity function.
This evidence does not claim handler seed closure, Stage acceptance,
production readiness, actual test execution, or syscall proof.
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (  # noqa: E402
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import asyncio  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Any, Iterator  # noqa: E402
import unittest  # noqa: E402
from unittest.mock import AsyncMock, Mock, patch  # noqa: E402

import api.routers.offers as offers_router  # noqa: E402


class SyntheticAutoSelectionBoolError(RuntimeError):
    """Synthetic exception simulating an ordinary failure during auto-selection bool evaluation."""


class MonitoredFailingBool:
    """Instrumented boolean container whose __bool__ raises a supplied BaseException."""

    def __init__(
        self,
        exc: BaseException,
        bool_mock: Mock,
        causal_events: list[str] | None = None,
    ) -> None:
        self._exc = exc
        self._bool_mock = bool_mock
        self._causal_events = causal_events

    def __bool__(self) -> bool:
        self._bool_mock()
        if self._causal_events is not None:
            self._causal_events.append("actual boolean conversion")
        raise self._exc


class IsolatedSelectionSettings:
    """Isolated synthetic settings exposing instrumented auto-selection flag."""

    def __init__(
        self,
        auto_selection_flag: Any,
        prop_mock: Mock,
        causal_events: list[str] | None = None,
    ) -> None:
        self._auto_selection_flag = auto_selection_flag
        self._prop_mock = prop_mock
        self._causal_events = causal_events

    @property
    def coin_intelligence_inference_auto_selection_enabled(self) -> Any:
        self._prop_mock()
        if self._causal_events is not None:
            self._causal_events.append("settings property")
        return self._auto_selection_flag


class StrictFakeSession:
    """Strict fake database session exposing only commit and rollback AsyncMocks."""

    __slots__ = ("commit", "rollback")

    def __init__(self, commit: AsyncMock, rollback: AsyncMock) -> None:
        self.commit = commit
        self.rollback = rollback


@contextmanager
def _patch_selection_boundaries(
    *,
    settings: Any,
    resolver: Mock,
    mock_datetime: Mock,
    reader_factory: Mock,
    observer: AsyncMock,
    formatter: Mock,
    log_trading_event: Mock,
) -> Iterator[None]:
    """Patch all external boundaries of _selection_inference_for_missing_commodity."""
    with patch.object(offers_router, "settings", settings), \
         patch.object(offers_router, "configured_product_snapshot_authority_path", resolver), \
         patch.object(offers_router, "datetime", mock_datetime), \
         patch.object(offers_router, "_configured_product_snapshot_reader", reader_factory), \
         patch.object(offers_router, "observe_coin_inference_shadow", observer), \
         patch.object(offers_router, "_catalog_inference_shadow_payload", formatter), \
         patch.object(offers_router, "log_trading_event", log_trading_event):
        yield


class SelectionBoolCancellationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for auto-selection bool cancellation during selection inference."""

    def _assert_common_positive_boundaries(
        self,
        *,
        resolver: Mock,
        expected_settings: IsolatedSelectionSettings,
        mock_datetime: Mock,
        prop_mock: Mock,
        bool_mock: Mock,
    ) -> None:
        """Assert exact positive boundaries reached before failure/cancellation."""
        resolver.assert_called_once_with(expected_settings)
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(len(resolver.call_args_list), 1)
        self.assertIs(resolver.call_args.args[0], expected_settings)
        self.assertEqual(resolver.call_args.kwargs, {})

        mock_datetime.now.assert_called_once_with(timezone.utc)
        self.assertEqual(mock_datetime.now.call_count, 1)
        self.assertEqual(len(mock_datetime.now.call_args_list), 1)
        self.assertIs(mock_datetime.now.call_args.args[0], timezone.utc)
        self.assertEqual(mock_datetime.now.call_args.kwargs, {})

        prop_mock.assert_called_once_with()
        self.assertEqual(prop_mock.call_count, 1)
        self.assertEqual(prop_mock.call_args.args, ())
        self.assertEqual(prop_mock.call_args.kwargs, {})

        bool_mock.assert_called_once_with()
        self.assertEqual(bool_mock.call_count, 1)
        self.assertEqual(bool_mock.call_args.args, ())
        self.assertEqual(bool_mock.call_args.kwargs, {})

    def _assert_negative_suppression(
        self,
        *,
        reader_factory: Mock,
        formatter: Mock,
        observer: AsyncMock,
        commit: AsyncMock,
    ) -> None:
        """Assert that unreached downstream components remain uninvoked."""
        reader_factory.assert_not_called()
        self.assertEqual(reader_factory.call_count, 0)

        formatter.assert_not_called()
        self.assertEqual(formatter.call_count, 0)

        observer.assert_not_called()
        observer.assert_not_awaited()
        self.assertEqual(observer.call_count, 0)
        self.assertEqual(observer.await_count, 0)

        commit.assert_not_called()
        commit.assert_not_awaited()
        self.assertEqual(commit.call_count, 0)
        self.assertEqual(commit.await_count, 0)

    async def test_scenario_1_direct_cancellation_in_bool(self) -> None:
        """Scenario 1: __bool__ raises CancelledError directly, bypassing exception handler."""
        causal_events: list[str] = []
        synthetic_path = "/synthetic/snapshot/path.json"
        cancel_exc = asyncio.CancelledError("scenario 1 direct bool cancellation")

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        dummy_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

        def clock_side_effect(tz: Any = None) -> datetime:
            causal_events.append("UTC clock")
            return dummy_now

        prop_mock = Mock()
        bool_mock = Mock()
        monitored_bool = MonitoredFailingBool(cancel_exc, bool_mock, causal_events)
        isolated_settings = IsolatedSelectionSettings(monitored_bool, prop_mock, causal_events)

        mock_resolver = Mock(side_effect=resolver_side_effect)
        mock_datetime = Mock()
        mock_datetime.now = Mock(side_effect=clock_side_effect)

        mock_commit = AsyncMock()
        mock_rollback = AsyncMock()
        fake_session = StrictFakeSession(mock_commit, mock_rollback)

        mock_reader_factory = Mock()
        mock_observer = AsyncMock()
        mock_formatter = Mock()
        mock_log = Mock()

        with _patch_selection_boundaries(
            settings=isolated_settings,
            resolver=mock_resolver,
            mock_datetime=mock_datetime,
            reader_factory=mock_reader_factory,
            observer=mock_observer,
            formatter=mock_formatter,
            log_trading_event=mock_log,
        ):
            with self.assertRaises(asyncio.CancelledError) as err_ctx:
                try:
                    await offers_router._selection_inference_for_missing_commodity(
                        fake_session,  # type: ignore[arg-type]
                        price=500_000,
                        settlement_type="cash",
                        low_date_hint=False,
                        pack_hint=False,
                    )
                except asyncio.CancelledError:
                    causal_events.append("cancellation escape")
                    raise

        # Assert exact cancellation instance escapes; context/cause None, unsuppressed
        self.assertIs(err_ctx.exception, cancel_exc)
        self.assertIsNone(err_ctx.exception.__context__)
        self.assertIsNone(err_ctx.exception.__cause__)
        self.assertFalse(err_ctx.exception.__suppress_context__)

        # Assert rollback is neither called nor awaited, log is never called, no fallback returned
        mock_rollback.assert_not_called()
        mock_rollback.assert_not_awaited()
        self.assertEqual(mock_rollback.call_count, 0)
        self.assertEqual(mock_rollback.await_count, 0)

        mock_log.assert_not_called()
        self.assertEqual(mock_log.call_count, 0)

        # Full causal event sequence
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "UTC clock",
                "settings property",
                "actual boolean conversion",
                "cancellation escape",
            ],
        )

        # Positive boundary assertions
        self._assert_common_positive_boundaries(
            resolver=mock_resolver,
            expected_settings=isolated_settings,
            mock_datetime=mock_datetime,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
        )

        # Negative suppression assertions
        self._assert_negative_suppression(
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )

    async def test_scenario_2_cancelled_error_in_rollback(self) -> None:
        """Scenario 2: __bool__ raises ordinary error and rollback raises CancelledError."""
        causal_events: list[str] = []
        synthetic_path = "/synthetic/snapshot/path.json"
        bool_exc = SyntheticAutoSelectionBoolError("scenario 2 synthetic bool error")
        cancel_exc = asyncio.CancelledError("scenario 2 rollback cancellation")

        self.assertIsNot(type(bool_exc), type(cancel_exc))
        self.assertIsNot(bool_exc, cancel_exc)

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        dummy_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

        def clock_side_effect(tz: Any = None) -> datetime:
            causal_events.append("UTC clock")
            return dummy_now

        async def rollback_side_effect(*args: Any, **kwargs: Any) -> None:
            causal_events.append("rollback cancellation")
            raise cancel_exc

        prop_mock = Mock()
        bool_mock = Mock()
        monitored_bool = MonitoredFailingBool(bool_exc, bool_mock, causal_events)
        isolated_settings = IsolatedSelectionSettings(monitored_bool, prop_mock, causal_events)

        mock_resolver = Mock(side_effect=resolver_side_effect)
        mock_datetime = Mock()
        mock_datetime.now = Mock(side_effect=clock_side_effect)

        mock_commit = AsyncMock()
        mock_rollback = AsyncMock(side_effect=rollback_side_effect)
        fake_session = StrictFakeSession(mock_commit, mock_rollback)

        mock_reader_factory = Mock()
        mock_observer = AsyncMock()
        mock_formatter = Mock()
        mock_log = Mock()

        with _patch_selection_boundaries(
            settings=isolated_settings,
            resolver=mock_resolver,
            mock_datetime=mock_datetime,
            reader_factory=mock_reader_factory,
            observer=mock_observer,
            formatter=mock_formatter,
            log_trading_event=mock_log,
        ):
            with self.assertRaises(asyncio.CancelledError) as err_ctx:
                try:
                    await offers_router._selection_inference_for_missing_commodity(
                        fake_session,  # type: ignore[arg-type]
                        price=500_000,
                        settlement_type="cash",
                        low_date_hint=False,
                        pack_hint=False,
                    )
                except asyncio.CancelledError:
                    causal_events.append("cancellation escape")
                    raise

        # Assert exact cancellation identity, context is original bool error, unsuppressed
        self.assertIs(err_ctx.exception, cancel_exc)
        self.assertIsNot(err_ctx.exception, bool_exc)
        self.assertIs(err_ctx.exception.__context__, bool_exc)
        self.assertIsNone(err_ctx.exception.__cause__)
        self.assertFalse(err_ctx.exception.__suppress_context__)

        # Assert rollback called and awaited exactly once with no arguments
        mock_rollback.assert_called_once_with()
        mock_rollback.assert_awaited_once_with()
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)
        self.assertEqual(mock_rollback.call_args.args, ())
        self.assertEqual(mock_rollback.call_args.kwargs, {})
        self.assertEqual(mock_rollback.await_args.args, ())
        self.assertEqual(mock_rollback.await_args.kwargs, {})

        # Assert log never called, no fallback returned
        mock_log.assert_not_called()
        self.assertEqual(mock_log.call_count, 0)

        # Full causal event sequence
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "UTC clock",
                "settings property",
                "actual boolean conversion",
                "rollback cancellation",
                "cancellation escape",
            ],
        )

        # Assert no retry occurred
        self.assertEqual(mock_resolver.call_count, 1)
        self.assertEqual(mock_datetime.now.call_count, 1)
        self.assertEqual(prop_mock.call_count, 1)
        self.assertEqual(bool_mock.call_count, 1)
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)

        # Positive boundary assertions
        self._assert_common_positive_boundaries(
            resolver=mock_resolver,
            expected_settings=isolated_settings,
            mock_datetime=mock_datetime,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
        )

        # Negative suppression assertions
        self._assert_negative_suppression(
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )

    async def test_scenario_3_cancelled_error_in_log_trading_event(self) -> None:
        """Scenario 3: rollback succeeds, log_trading_event raises CancelledError."""
        causal_events: list[str] = []
        synthetic_path = "/synthetic/snapshot/path.json"
        bool_exc = SyntheticAutoSelectionBoolError("fresh scenario 3 synthetic bool error")
        cancel_exc = asyncio.CancelledError("scenario 3 log cancellation")

        self.assertIsNot(type(bool_exc), type(cancel_exc))
        self.assertIsNot(bool_exc, cancel_exc)

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        dummy_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

        def clock_side_effect(tz: Any = None) -> datetime:
            causal_events.append("UTC clock")
            return dummy_now

        async def rollback_side_effect(*args: Any, **kwargs: Any) -> None:
            causal_events.append("rollback completion")

        def log_side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:
            causal_events.append("log cancellation")
            raise cancel_exc

        prop_mock = Mock()
        bool_mock = Mock()
        monitored_bool = MonitoredFailingBool(bool_exc, bool_mock, causal_events)
        isolated_settings = IsolatedSelectionSettings(monitored_bool, prop_mock, causal_events)

        mock_resolver = Mock(side_effect=resolver_side_effect)
        mock_datetime = Mock()
        mock_datetime.now = Mock(side_effect=clock_side_effect)

        mock_commit = AsyncMock()
        mock_rollback = AsyncMock(side_effect=rollback_side_effect)
        fake_session = StrictFakeSession(mock_commit, mock_rollback)

        mock_reader_factory = Mock()
        mock_observer = AsyncMock()
        mock_formatter = Mock()
        mock_log = Mock(side_effect=log_side_effect)

        with _patch_selection_boundaries(
            settings=isolated_settings,
            resolver=mock_resolver,
            mock_datetime=mock_datetime,
            reader_factory=mock_reader_factory,
            observer=mock_observer,
            formatter=mock_formatter,
            log_trading_event=mock_log,
        ):
            with self.assertRaises(asyncio.CancelledError) as err_ctx:
                try:
                    await offers_router._selection_inference_for_missing_commodity(
                        fake_session,  # type: ignore[arg-type]
                        price=500_000,
                        settlement_type="cash",
                        low_date_hint=False,
                        pack_hint=False,
                    )
                except asyncio.CancelledError:
                    causal_events.append("cancellation escape")
                    raise

        # Assert exact cancellation identity and original bool-error chaining
        self.assertIs(err_ctx.exception, cancel_exc)
        self.assertIsNot(err_ctx.exception, bool_exc)
        self.assertIs(err_ctx.exception.__context__, bool_exc)
        self.assertIsNone(err_ctx.exception.__cause__)
        self.assertFalse(err_ctx.exception.__suppress_context__)

        # Assert rollback called/awaited once with no arguments before log
        mock_rollback.assert_called_once_with()
        mock_rollback.assert_awaited_once_with()
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)
        self.assertEqual(mock_rollback.call_args.args, ())
        self.assertEqual(mock_rollback.call_args.kwargs, {})
        self.assertEqual(mock_rollback.await_args.args, ())
        self.assertEqual(mock_rollback.await_args.kwargs, {})

        # Assert log called once with exact logger, message, kwargs
        mock_log.assert_called_once_with(
            offers_router.logger,
            "coin_inference_parse_selection.unavailable",
            level="warning",
            action="coin_inference_parse_selection",
            result="abstained",
            error_class=SyntheticAutoSelectionBoolError.__name__,
        )
        self.assertEqual(mock_log.call_count, 1)
        self.assertIs(mock_log.call_args.args[0], offers_router.logger)
        self.assertEqual(mock_log.call_args.args[1], "coin_inference_parse_selection.unavailable")
        self.assertEqual(mock_log.call_args.kwargs["level"], "warning")
        self.assertEqual(mock_log.call_args.kwargs["action"], "coin_inference_parse_selection")
        self.assertEqual(mock_log.call_args.kwargs["result"], "abstained")
        self.assertEqual(
            mock_log.call_args.kwargs["error_class"],
            SyntheticAutoSelectionBoolError.__name__,
        )
        self.assertEqual(mock_log.call_args.kwargs["error_class"], "SyntheticAutoSelectionBoolError")
        self.assertNotEqual(mock_log.call_args.kwargs["error_class"], str(bool_exc))
        self.assertNotEqual(mock_log.call_args.kwargs["error_class"], type(cancel_exc).__name__)

        # Full causal event sequence
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "UTC clock",
                "settings property",
                "actual boolean conversion",
                "rollback completion",
                "log cancellation",
                "cancellation escape",
            ],
        )

        # Assert rollback preceded log in causal execution
        self.assertLess(
            causal_events.index("rollback completion"),
            causal_events.index("log cancellation"),
        )

        # Assert no retry occurred
        self.assertEqual(mock_resolver.call_count, 1)
        self.assertEqual(mock_datetime.now.call_count, 1)
        self.assertEqual(prop_mock.call_count, 1)
        self.assertEqual(bool_mock.call_count, 1)
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)
        self.assertEqual(mock_log.call_count, 1)

        # Positive boundary assertions
        self._assert_common_positive_boundaries(
            resolver=mock_resolver,
            expected_settings=isolated_settings,
            mock_datetime=mock_datetime,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
        )

        # Negative suppression assertions
        self._assert_negative_suppression(
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )


if __name__ == "__main__":
    unittest.main()
