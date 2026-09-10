"""Hermetic characterization of auto-selection bool failure in _selection_inference_for_missing_commodity.

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

from contextlib import contextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Any, Iterator  # noqa: E402
import unittest  # noqa: E402
from unittest.mock import AsyncMock, Mock, patch  # noqa: E402

import api.routers.offers as offers_router  # noqa: E402


class SyntheticAutoSelectionBoolError(RuntimeError):
    """Synthetic exception simulating a failure during auto-selection bool evaluation."""


class SyntheticRollbackExecutionError(RuntimeError):
    """Synthetic exception simulating a failure during session rollback."""


class MonitoredFailingBool:
    """Instrumented boolean container whose __bool__ raises a synthetic exception."""

    def __init__(
        self,
        exc: Exception,
        bool_mock: Mock,
        causal_events: list[str] | None = None,
    ) -> None:
        self._exc = exc
        self._bool_mock = bool_mock
        self._causal_events = causal_events

    def __bool__(self) -> bool:
        self._bool_mock()
        if self._causal_events is not None:
            self._causal_events.append("boolean failure")
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
            self._causal_events.append("setting property")
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


class SelectionBoolFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for auto-selection bool failure during selection inference."""

    def _assert_positive_call_boundaries(
        self,
        *,
        resolver: Mock,
        expected_settings: IsolatedSelectionSettings,
        mock_datetime: Mock,
        prop_mock: Mock,
        bool_mock: Mock,
        rollback: AsyncMock,
    ) -> None:
        """Assert exact positive boundaries executed during helper execution."""
        resolver.assert_called_once_with(expected_settings)
        self.assertIs(resolver.call_args.args[0], expected_settings)

        mock_datetime.now.assert_called_once_with(timezone.utc)
        self.assertIs(mock_datetime.now.call_args.args[0], timezone.utc)

        prop_mock.assert_called_once_with()
        self.assertEqual(prop_mock.call_count, 1)
        self.assertEqual(prop_mock.call_args.args, ())
        self.assertEqual(prop_mock.call_args.kwargs, {})

        bool_mock.assert_called_once_with()
        self.assertEqual(bool_mock.call_count, 1)
        self.assertEqual(bool_mock.call_args.args, ())
        self.assertEqual(bool_mock.call_args.kwargs, {})

        rollback.assert_awaited_once_with()
        self.assertEqual(rollback.call_count, 1)
        self.assertEqual(rollback.await_count, 1)
        self.assertEqual(rollback.call_args.args, ())
        self.assertEqual(rollback.call_args.kwargs, {})

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
        self.assertEqual(observer.call_count, 0)
        self.assertEqual(observer.await_count, 0)

        commit.assert_not_called()
        self.assertEqual(commit.call_count, 0)
        self.assertEqual(commit.await_count, 0)

    async def test_auto_selection_bool_failure_with_successful_rollback(self) -> None:
        """Auto-selection bool failure with successful rollback returns fallback and logs warning."""
        causal_events: list[str] = []

        synthetic_path = "/synthetic/snapshot/path.json"
        bool_exc = SyntheticAutoSelectionBoolError("synthetic auto-selection bool evaluation failed")

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        dummy_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

        def clock_side_effect(tz: Any = None) -> datetime:
            causal_events.append("clock")
            return dummy_now

        async def rollback_side_effect(*args: Any, **kwargs: Any) -> None:
            causal_events.append("rollback completion")

        def log_side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:
            causal_events.append("log")
            return {}

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
            result = await offers_router._selection_inference_for_missing_commodity(
                fake_session,  # type: ignore[arg-type]
                price=500_000,
                settlement_type="cash",
                low_date_hint=False,
                pack_hint=False,
            )
            causal_events.append("fallback return")

        # Complete causal event sequence assertion (Requirement 4)
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "clock",
                "setting property",
                "boolean failure",
                "rollback completion",
                "log",
                "fallback return",
            ],
        )

        # Positive boundary assertions (Requirement 4)
        self._assert_positive_call_boundaries(
            resolver=mock_resolver,
            expected_settings=isolated_settings,
            mock_datetime=mock_datetime,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
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
            error_class=SyntheticAutoSelectionBoolError.__name__,
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
            SyntheticAutoSelectionBoolError.__name__,
        )
        self.assertNotEqual(
            mock_log.call_args.kwargs["error_class"],
            str(bool_exc),
        )

        # Negative suppression assertions (Requirement 4)
        self._assert_negative_suppression(
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )

    async def test_auto_selection_bool_failure_with_failed_rollback(self) -> None:
        """Auto-selection bool failure with failed rollback propagates the rollback exception."""
        causal_events: list[str] = []

        synthetic_path = "/synthetic/snapshot/path.json"
        bool_exc = SyntheticAutoSelectionBoolError("synthetic auto-selection bool evaluation failed")
        rollback_exc = SyntheticRollbackExecutionError("synthetic rollback execution failed")

        # Distinct exception classes and instances (Requirement 3)
        self.assertIsNot(type(bool_exc), type(rollback_exc))
        self.assertIsNot(bool_exc, rollback_exc)

        def resolver_side_effect(target_settings: Any) -> str:
            causal_events.append("resolver")
            return synthetic_path

        dummy_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

        def clock_side_effect(tz: Any = None) -> datetime:
            causal_events.append("clock")
            return dummy_now

        async def rollback_side_effect(*args: Any, **kwargs: Any) -> None:
            causal_events.append("rollback failure")
            raise rollback_exc

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
                        causal_events.append("rollback exception escape")
                    raise

        # Assert identical rollback exception escapes (Requirement 3)
        self.assertIs(err_ctx.exception, rollback_exc)
        self.assertIsNot(err_ctx.exception, bool_exc)
        self.assertNotIsInstance(err_ctx.exception, SyntheticAutoSelectionBoolError)

        # Assert its __context__ is the original boolean exception to prove the original injected failure entered recovery (Requirement 3)
        self.assertIs(err_ctx.exception.__context__, bool_exc)

        # Assert logging is not called and no fallback is returned (Requirement 3)
        mock_log.assert_not_called()
        self.assertEqual(mock_log.call_count, 0)

        # Complete causal event sequence assertion (Requirement 4)
        self.assertEqual(
            causal_events,
            [
                "resolver",
                "clock",
                "setting property",
                "boolean failure",
                "rollback failure",
                "rollback exception escape",
            ],
        )

        # Prove no retry occurred (Requirement 3)
        self.assertEqual(mock_resolver.call_count, 1)
        self.assertEqual(mock_datetime.now.call_count, 1)
        self.assertEqual(prop_mock.call_count, 1)
        self.assertEqual(bool_mock.call_count, 1)
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)

        # Positive boundary assertions (Requirement 4)
        self._assert_positive_call_boundaries(
            resolver=mock_resolver,
            expected_settings=isolated_settings,
            mock_datetime=mock_datetime,
            prop_mock=prop_mock,
            bool_mock=bool_mock,
            rollback=mock_rollback,
        )

        # Negative suppression assertions (Requirement 4)
        self._assert_negative_suppression(
            reader_factory=mock_reader_factory,
            formatter=mock_formatter,
            observer=mock_observer,
            commit=mock_commit,
        )


if __name__ == "__main__":
    unittest.main()
