"""Hermetic characterization of datetime.now(timezone.utc) failure in _selection_inference_for_missing_commodity.

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
from datetime import timezone  # noqa: E402
from typing import Any, Iterator  # noqa: E402
import unittest  # noqa: E402
from unittest.mock import AsyncMock, Mock, patch  # noqa: E402

import api.routers.offers as offers_router  # noqa: E402


class SyntheticClockEvaluationError(RuntimeError):
    """Synthetic exception simulating a failure during clock evaluation."""


class SyntheticRollbackExecutionError(RuntimeError):
    """Synthetic exception simulating a failure during session rollback."""


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


class SelectionClockFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for datetime.now(timezone.utc) failure during selection."""

    def _assert_positive_call_boundaries(
        self,
        *,
        resolver: Mock,
        expected_settings: IsolatedSelectionSettings,
        mock_datetime: Mock,
        rollback: AsyncMock,
    ) -> None:
        """Assert exact positive boundaries executed during helper execution."""
        resolver.assert_called_once_with(expected_settings)
        self.assertIs(resolver.call_args.args[0], expected_settings)

        mock_datetime.now.assert_called_once_with(timezone.utc)
        self.assertIs(mock_datetime.now.call_args.args[0], timezone.utc)

        rollback.assert_awaited_once_with()
        self.assertEqual(rollback.call_count, 1)
        self.assertEqual(rollback.await_count, 1)
        self.assertEqual(rollback.call_args.args, ())
        self.assertEqual(rollback.call_args.kwargs, {})

    def _assert_negative_suppression(
        self,
        *,
        prop_mock: Mock,
        bool_mock: Mock,
        reader_factory: Mock,
        formatter: Mock,
        observer: AsyncMock,
        commit: AsyncMock,
    ) -> None:
        """Assert that unreached downstream components remain uninvoked."""
        prop_mock.assert_not_called()
        self.assertEqual(prop_mock.call_count, 0)

        bool_mock.assert_not_called()
        self.assertEqual(bool_mock.call_count, 0)

        reader_factory.assert_not_called()
        self.assertEqual(reader_factory.call_count, 0)

        formatter.assert_not_called()
        self.assertEqual(formatter.call_count, 0)

        observer.assert_not_called()
        self.assertEqual(observer.call_count, 0)
        self.assertEqual(observer.await_count, 0)

        commit.assert_not_called()
        self.assertEqual(commit.call_count, 0)
        self.assertEqual(commit.await_count, 0)

    async def test_clock_failure_with_successful_rollback(self) -> None:
        """Clock failure with successful rollback returns fallback and logs warning."""
        causal_events: list[str] = []

        synthetic_path = "/synthetic/snapshot/path.json"
        clock_exc = SyntheticClockEvaluationError("synthetic clock evaluation failed")

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        def clock_side_effect(*args: Any, **kwargs: Any) -> Any:
            causal_events.append("clock failure")
            raise clock_exc

        async def rollback_side_effect(*args: Any, **kwargs: Any) -> None:
            causal_events.append("rollback completion")

        def log_side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:
            causal_events.append("log")
            return {}

        prop_mock = Mock()
        bool_mock = Mock()
        monitored_bool = MonitoredBool(True, bool_mock)
        isolated_settings = IsolatedSelectionSettings(monitored_bool, prop_mock)

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
            result = await offers_router._selection_inference_for_missing_commodity(
                fake_session,  # type: ignore[arg-type]
                price=500_000,
                settlement_type="cash",
                low_date_hint=False,
                pack_hint=False,
            )
            causal_events.append("returned fallback")

        # Complete causal event sequence assertion (Requirement 4)
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "clock failure",
                "rollback completion",
                "log",
                "returned fallback",
            ],
        )

        # Positive boundary assertions (Requirement 4)
        self._assert_positive_call_boundaries(
            resolver=mock_resolver,
            expected_settings=isolated_settings,
            mock_datetime=mock_datetime,
            rollback=mock_rollback,
        )

        # Fallback dictionary assertions (Requirement 2)
        expected_fallback: dict[str, object] = {
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
        self.assertNotIn("edit_candidates", result)
        self.assertIsNone(result["decision_key"])
        self.assertIsNone(result["snapshot_generated_at_utc"])
        self.assertIsNone(result["snapshot_receipt"])
        self.assertEqual(result["candidates"], [])

        # Trading log assertions (Requirement 2)
        mock_log.assert_called_once_with(
            offers_router.logger,
            "coin_inference_parse_selection.unavailable",
            level="warning",
            action="coin_inference_parse_selection",
            result="abstained",
            error_class=SyntheticClockEvaluationError.__name__,
        )
        self.assertIs(mock_log.call_args.args[0], offers_router.logger)
        self.assertEqual(
            mock_log.call_args.args[1],
            "coin_inference_parse_selection.unavailable",
        )
        self.assertEqual(mock_log.call_args.kwargs["level"], "warning")
        self.assertEqual(mock_log.call_args.kwargs["action"], "coin_inference_parse_selection")
        self.assertEqual(mock_log.call_args.kwargs["result"], "abstained")
        self.assertEqual(
            mock_log.call_args.kwargs["error_class"],
            SyntheticClockEvaluationError.__name__,
        )
        self.assertNotEqual(
            mock_log.call_args.kwargs["error_class"],
            str(clock_exc),
        )

        # Negative suppression assertions (Requirement 4)
        self._assert_negative_suppression(
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )

    async def test_clock_failure_with_failed_rollback(self) -> None:
        """Clock failure with failed rollback propagates the rollback exception."""
        causal_events: list[str] = []

        synthetic_path = "/synthetic/snapshot/path.json"
        clock_exc = SyntheticClockEvaluationError("synthetic clock evaluation failed")
        rollback_exc = SyntheticRollbackExecutionError("synthetic rollback execution failed")

        # Distinct exception classes and instances (Requirement 3)
        self.assertIsNot(type(clock_exc), type(rollback_exc))
        self.assertIsNot(clock_exc, rollback_exc)

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        def clock_side_effect(*args: Any, **kwargs: Any) -> Any:
            causal_events.append("clock failure")
            raise clock_exc

        async def rollback_side_effect(*args: Any, **kwargs: Any) -> None:
            causal_events.append("rollback failure")
            raise rollback_exc

        prop_mock = Mock()
        bool_mock = Mock()
        monitored_bool = MonitoredBool(True, bool_mock)
        isolated_settings = IsolatedSelectionSettings(monitored_bool, prop_mock)

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
            with self.assertRaises(SyntheticRollbackExecutionError) as err_ctx:
                try:
                    await offers_router._selection_inference_for_missing_commodity(
                        fake_session,  # type: ignore[arg-type]
                        price=500_000,
                        settlement_type="cash",
                        low_date_hint=False,
                        pack_hint=False,
                    )
                except Exception as caught_exc:
                    if isinstance(caught_exc, SyntheticRollbackExecutionError):
                        causal_events.append("propagated rollback exception")
                    raise

        # Assert identical rollback exception escapes (Requirement 3)
        self.assertIs(err_ctx.exception, rollback_exc)
        self.assertIsNot(err_ctx.exception, clock_exc)
        self.assertNotIsInstance(err_ctx.exception, SyntheticClockEvaluationError)

        # Assert logging is not called and no fallback is returned (Requirement 3)
        mock_log.assert_not_called()

        # Complete causal event sequence assertion (Requirement 4)
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "clock failure",
                "rollback failure",
                "propagated rollback exception",
            ],
        )

        # Prove no retry occurred (Requirement 3)
        self.assertEqual(mock_resolver.call_count, 1)
        self.assertEqual(mock_datetime.now.call_count, 1)
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)

        # Positive boundary assertions (Requirement 4)
        self._assert_positive_call_boundaries(
            resolver=mock_resolver,
            expected_settings=isolated_settings,
            mock_datetime=mock_datetime,
            rollback=mock_rollback,
        )

        # Negative suppression assertions (Requirement 4)
        self._assert_negative_suppression(
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )

    async def test_clock_evaluation_cancellation_propagates_directly(self) -> None:
        """Direct clock cancellation escapes unchanged without rollback or logging."""
        causal_events: list[str] = []

        synthetic_path = "/synthetic/snapshot/path.json"
        cancel_exc = asyncio.CancelledError("synthetic clock evaluation cancelled")

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        def clock_side_effect(*args: Any, **kwargs: Any) -> Any:
            causal_events.append("clock cancellation")
            raise cancel_exc

        prop_mock = Mock()
        bool_mock = Mock()
        monitored_bool = MonitoredBool(True, bool_mock)
        isolated_settings = IsolatedSelectionSettings(monitored_bool, prop_mock)

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

        sentinel_result = object()
        result: object = sentinel_result
        caught_cancellation: asyncio.CancelledError | None = None

        with _patch_selection_boundaries(
            settings=isolated_settings,
            resolver=mock_resolver,
            mock_datetime=mock_datetime,
            reader_factory=mock_reader_factory,
            observer=mock_observer,
            formatter=mock_formatter,
            log_trading_event=mock_log,
        ):
            try:
                result = await offers_router._selection_inference_for_missing_commodity(
                    fake_session,  # type: ignore[arg-type]
                    price=500_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )
            except asyncio.CancelledError as exc:
                causal_events.append("caught cancellation")
                caught_cancellation = exc

        # Causal event sequence assertion (Requirements 2 and 5)
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "clock cancellation",
                "caught cancellation",
            ],
        )

        # Cancellation identity and chaining assertions (Requirement 2)
        self.assertIsNotNone(caught_cancellation)
        self.assertIs(caught_cancellation, cancel_exc)
        self.assertIsNone(caught_cancellation.__context__)
        self.assertIsNone(caught_cancellation.__cause__)
        self.assertFalse(caught_cancellation.__suppress_context__)

        # Unchanged result sentinel proving no fallback return (Requirements 2 and 5)
        self.assertIs(result, sentinel_result)

        # Resolver boundary assertions (Requirement 5)
        mock_resolver.assert_called_once_with(isolated_settings)
        self.assertEqual(mock_resolver.call_count, 1)
        self.assertEqual(mock_resolver.call_args.args, (isolated_settings,))
        self.assertEqual(mock_resolver.call_args.kwargs, {})
        self.assertIs(mock_resolver.call_args.args[0], isolated_settings)

        # Clock boundary assertions (Requirement 5)
        mock_datetime.now.assert_called_once_with(timezone.utc)
        self.assertEqual(mock_datetime.now.call_count, 1)
        self.assertEqual(mock_datetime.now.call_args.args, (timezone.utc,))
        self.assertEqual(mock_datetime.now.call_args.kwargs, {})
        self.assertIs(mock_datetime.now.call_args.args[0], timezone.utc)

        # Zero rollback calls and awaits assertion (Requirement 2)
        mock_rollback.assert_not_called()
        self.assertEqual(mock_rollback.call_count, 0)
        self.assertEqual(mock_rollback.await_count, 0)

        # Zero log calls assertion (Requirement 2)
        mock_log.assert_not_called()
        self.assertEqual(mock_log.call_count, 0)

        # Negative suppression assertions (Requirement 5)
        self._assert_negative_suppression(
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )

    async def test_clock_failure_with_rollback_cancellation_propagates(self) -> None:
        """Clock failure followed by rollback cancellation propagates cancellation with chained context."""
        causal_events: list[str] = []

        synthetic_path = "/synthetic/snapshot/path.json"
        clock_exc = SyntheticClockEvaluationError("synthetic clock evaluation failed")
        rollback_cancel_exc = asyncio.CancelledError("synthetic rollback cancelled")

        # Distinct exception classes and instances (Requirement 3)
        self.assertIsNot(type(clock_exc), type(rollback_cancel_exc))
        self.assertIsNot(clock_exc, rollback_cancel_exc)

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        def clock_side_effect(*args: Any, **kwargs: Any) -> Any:
            causal_events.append("clock failure")
            raise clock_exc

        async def rollback_side_effect(*args: Any, **kwargs: Any) -> None:
            causal_events.append("rollback cancellation")
            raise rollback_cancel_exc

        prop_mock = Mock()
        bool_mock = Mock()
        monitored_bool = MonitoredBool(True, bool_mock)
        isolated_settings = IsolatedSelectionSettings(monitored_bool, prop_mock)

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

        sentinel_result = object()
        result: object = sentinel_result
        caught_cancellation: asyncio.CancelledError | None = None

        with _patch_selection_boundaries(
            settings=isolated_settings,
            resolver=mock_resolver,
            mock_datetime=mock_datetime,
            reader_factory=mock_reader_factory,
            observer=mock_observer,
            formatter=mock_formatter,
            log_trading_event=mock_log,
        ):
            try:
                result = await offers_router._selection_inference_for_missing_commodity(
                    fake_session,  # type: ignore[arg-type]
                    price=500_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )
            except asyncio.CancelledError as exc:
                causal_events.append("caught cancellation")
                caught_cancellation = exc

        # Causal event sequence assertion (Requirements 3 and 5)
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "clock failure",
                "rollback cancellation",
                "caught cancellation",
            ],
        )

        # Cancellation identity and chaining assertions (Requirement 3)
        self.assertIsNotNone(caught_cancellation)
        self.assertIs(caught_cancellation, rollback_cancel_exc)
        self.assertIs(caught_cancellation.__context__, clock_exc)
        self.assertIsNone(caught_cancellation.__cause__)
        self.assertFalse(caught_cancellation.__suppress_context__)

        # Unchanged result sentinel proving no fallback return (Requirements 3 and 5)
        self.assertIs(result, sentinel_result)

        # Resolver boundary assertions (Requirement 5)
        mock_resolver.assert_called_once_with(isolated_settings)
        self.assertEqual(mock_resolver.call_count, 1)
        self.assertEqual(mock_resolver.call_args.args, (isolated_settings,))
        self.assertEqual(mock_resolver.call_args.kwargs, {})
        self.assertIs(mock_resolver.call_args.args[0], isolated_settings)

        # Clock boundary assertions (Requirement 5)
        mock_datetime.now.assert_called_once_with(timezone.utc)
        self.assertEqual(mock_datetime.now.call_count, 1)
        self.assertEqual(mock_datetime.now.call_args.args, (timezone.utc,))
        self.assertEqual(mock_datetime.now.call_args.kwargs, {})
        self.assertIs(mock_datetime.now.call_args.args[0], timezone.utc)

        # Rollback call and await arguments/counts (Requirement 3)
        mock_rollback.assert_awaited_once_with()
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)
        self.assertEqual(mock_rollback.call_args.args, ())
        self.assertEqual(mock_rollback.call_args.kwargs, {})

        # Zero log calls assertion (Requirement 3)
        mock_log.assert_not_called()
        self.assertEqual(mock_log.call_count, 0)

        # Negative suppression assertions (Requirement 5)
        self._assert_negative_suppression(
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )

    async def test_clock_failure_with_log_cancellation_propagates(self) -> None:
        """Clock failure followed by log cancellation propagates cancellation with chained context."""
        causal_events: list[str] = []

        synthetic_path = "/synthetic/snapshot/path.json"
        clock_exc = SyntheticClockEvaluationError("synthetic clock evaluation failed")
        log_cancel_exc = asyncio.CancelledError("synthetic log trading event cancelled")

        # Distinct exception classes and instances (Requirement 4)
        self.assertIsNot(type(clock_exc), type(log_cancel_exc))
        self.assertIsNot(clock_exc, log_cancel_exc)

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        def clock_side_effect(*args: Any, **kwargs: Any) -> Any:
            causal_events.append("clock failure")
            raise clock_exc

        async def rollback_side_effect(*args: Any, **kwargs: Any) -> None:
            causal_events.append("completed rollback")

        def log_side_effect(*args: Any, **kwargs: Any) -> Any:
            causal_events.append("attempted log")
            raise log_cancel_exc

        prop_mock = Mock()
        bool_mock = Mock()
        monitored_bool = MonitoredBool(True, bool_mock)
        isolated_settings = IsolatedSelectionSettings(monitored_bool, prop_mock)

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

        sentinel_result = object()
        result: object = sentinel_result
        caught_cancellation: asyncio.CancelledError | None = None

        with _patch_selection_boundaries(
            settings=isolated_settings,
            resolver=mock_resolver,
            mock_datetime=mock_datetime,
            reader_factory=mock_reader_factory,
            observer=mock_observer,
            formatter=mock_formatter,
            log_trading_event=mock_log,
        ):
            try:
                result = await offers_router._selection_inference_for_missing_commodity(
                    fake_session,  # type: ignore[arg-type]
                    price=500_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )
            except asyncio.CancelledError as exc:
                causal_events.append("caught cancellation")
                caught_cancellation = exc

        # Causal event sequence assertion (Requirements 4 and 5)
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "clock failure",
                "completed rollback",
                "attempted log",
                "caught cancellation",
            ],
        )

        # Cancellation identity and chaining assertions (Requirement 4)
        self.assertIsNotNone(caught_cancellation)
        self.assertIs(caught_cancellation, log_cancel_exc)
        self.assertIs(caught_cancellation.__context__, clock_exc)
        self.assertIsNone(caught_cancellation.__cause__)
        self.assertFalse(caught_cancellation.__suppress_context__)

        # Unchanged result sentinel proving no fallback return (Requirements 4 and 5)
        self.assertIs(result, sentinel_result)

        # Resolver boundary assertions (Requirement 5)
        mock_resolver.assert_called_once_with(isolated_settings)
        self.assertEqual(mock_resolver.call_count, 1)
        self.assertEqual(mock_resolver.call_args.args, (isolated_settings,))
        self.assertEqual(mock_resolver.call_args.kwargs, {})
        self.assertIs(mock_resolver.call_args.args[0], isolated_settings)

        # Clock boundary assertions (Requirement 5)
        mock_datetime.now.assert_called_once_with(timezone.utc)
        self.assertEqual(mock_datetime.now.call_count, 1)
        self.assertEqual(mock_datetime.now.call_args.args, (timezone.utc,))
        self.assertEqual(mock_datetime.now.call_args.kwargs, {})
        self.assertIs(mock_datetime.now.call_args.args[0], timezone.utc)

        # Rollback call and await arguments/counts (Requirement 4)
        mock_rollback.assert_awaited_once_with()
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)
        self.assertEqual(mock_rollback.call_args.args, ())
        self.assertEqual(mock_rollback.call_args.kwargs, {})

        # Trading log assertions (Requirement 4)
        mock_log.assert_called_once_with(
            offers_router.logger,
            "coin_inference_parse_selection.unavailable",
            level="warning",
            action="coin_inference_parse_selection",
            result="abstained",
            error_class=SyntheticClockEvaluationError.__name__,
        )
        self.assertEqual(mock_log.call_count, 1)
        self.assertIs(mock_log.call_args.args[0], offers_router.logger)
        self.assertEqual(
            mock_log.call_args.args[1],
            "coin_inference_parse_selection.unavailable",
        )
        self.assertEqual(mock_log.call_args.kwargs["level"], "warning")
        self.assertEqual(mock_log.call_args.kwargs["action"], "coin_inference_parse_selection")
        self.assertEqual(mock_log.call_args.kwargs["result"], "abstained")
        self.assertEqual(
            mock_log.call_args.kwargs["error_class"],
            SyntheticClockEvaluationError.__name__,
        )
        self.assertNotEqual(
            mock_log.call_args.kwargs["error_class"],
            str(clock_exc),
        )

        # Negative suppression assertions (Requirement 5)
        self._assert_negative_suppression(
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )


if __name__ == "__main__":
    unittest.main()
