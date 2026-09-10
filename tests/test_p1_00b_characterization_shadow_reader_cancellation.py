"""Characterization tests for _shadow_inference_for_implicit_commodity reader cancellation scenarios.

Tracked inventory gap path: docs/refactor/two-server/section-1/inventory/scenario-records.json
Tracked inventory gap ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

Scenarios covered:
1. Synchronous reader factory raises asyncio.CancelledError directly: cancellation escapes
   unmodified without rollback, logging, or fallback value.
2. Reader factory raises ordinary exception, then awaited rollback raises asyncio.CancelledError:
   cancellation escapes with chained exception context, without logging or fallback value.
3. Reader factory raises ordinary exception, rollback succeeds, then synchronous log_trading_event
   raises asyncio.CancelledError: cancellation escapes with chained exception context, exactly
   one rollback precedes logging, and no fallback or retry occurs.

Evidence limits:
Helper-only mocked-boundary evidence without estimator/audit persistence or full
parse/seed/Stage closure.
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
    """Synthetic exception representing an ordinary snapshot reader failure."""


class IsolatedSettings:
    """Isolated settings object exposing only the inference preview flag."""

    coin_intelligence_inference_preview_enabled: bool = True


class StrictFakeSession:
    """Strict fake database session exposing only commit and rollback AsyncMocks."""

    __slots__ = ("commit", "rollback")

    def __init__(self, commit: AsyncMock, rollback: AsyncMock) -> None:
        self.commit = commit
        self.rollback = rollback


class ShadowReaderCancellationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization of reader cancellation scenarios in shadow inference."""

    def _assert_boundary_invariants(
        self,
        *,
        resolver_mock: Mock,
        isolated_settings: IsolatedSettings,
        clock_mock: Mock,
        reader_mock: Mock,
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

        reader_mock.assert_called_once_with()
        self.assertEqual(reader_mock.call_args[0], ())
        self.assertEqual(reader_mock.call_args[1], {})

        observer_mock.assert_not_called()
        observer_mock.assert_not_awaited()

        commit_mock.assert_not_called()
        commit_mock.assert_not_awaited()

        formatter_mock.assert_not_called()

    async def test_scenario_1_reader_cancellation_escapes_without_rollback_or_logging(
        self,
    ) -> None:
        """Synchronous reader factory raises CancelledError escaping directly without rollback or logging."""
        event_log: list[str] = []
        isolated_settings = IsolatedSettings()
        synthetic_path = "/synthetic/snapshot/product_authority.json"
        fixed_now = datetime(2026, 9, 10, 8, 30, 0, tzinfo=timezone.utc)
        cancel_error = asyncio.CancelledError("scenario-1-reader-cancelled")

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
            raise cancel_error

        reader_mock = Mock(side_effect=_reader_factory)

        async def _rollback_impl(*args: Any, **kwargs: Any) -> None:
            event_log.append("rollback_attempted")

        rollback_mock = AsyncMock(side_effect=_rollback_impl)
        commit_mock = AsyncMock()
        session = StrictFakeSession(commit=commit_mock, rollback=rollback_mock)

        observer_mock = AsyncMock()
        formatter_mock = Mock()
        log_mock = Mock()

        sentinel = object()
        result: Any = sentinel
        caught_exc: BaseException | None = None

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
            try:
                result = await offers_router._shadow_inference_for_implicit_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                )
                event_log.append("unexpected_return")
            except BaseException as exc:
                caught_exc = exc
                event_log.append("cancellation_escaped")

        self.assertIs(result, sentinel)

        self.assertIs(caught_exc, cancel_error)
        self.assertIs(caught_exc.__context__, None)
        self.assertIs(caught_exc.__cause__, None)
        self.assertIs(caught_exc.__suppress_context__, False)

        rollback_mock.assert_not_called()
        rollback_mock.assert_not_awaited()
        log_mock.assert_not_called()

        self._assert_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            reader_mock=reader_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
        )

        expected_events = [
            "authority_path_resolved",
            "utc_clock_evaluated",
            "reader_factory_attempted",
            "cancellation_escaped",
        ]
        self.assertEqual(event_log, expected_events)

    async def test_scenario_2_reader_failure_with_rollback_cancellation_escapes_with_chained_context(
        self,
    ) -> None:
        """Reader failure followed by rollback CancelledError escapes with chained exception context."""
        event_log: list[str] = []
        isolated_settings = IsolatedSettings()
        synthetic_path = "/synthetic/snapshot/product_authority.json"
        fixed_now = datetime(2026, 9, 10, 8, 30, 0, tzinfo=timezone.utc)
        reader_error = SyntheticReaderFailure("simulated reader boundary failure")
        cancel_error = asyncio.CancelledError("scenario-2-rollback-cancelled")

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
            raise cancel_error

        rollback_mock = AsyncMock(side_effect=_failing_rollback)
        commit_mock = AsyncMock()
        session = StrictFakeSession(commit=commit_mock, rollback=rollback_mock)

        observer_mock = AsyncMock()
        formatter_mock = Mock()
        log_mock = Mock()

        sentinel = object()
        result: Any = sentinel
        caught_exc: BaseException | None = None

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
            try:
                result = await offers_router._shadow_inference_for_implicit_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                )
                event_log.append("unexpected_return")
            except BaseException as exc:
                caught_exc = exc
                event_log.append("cancellation_escaped")

        self.assertIs(result, sentinel)

        self.assertIs(caught_exc, cancel_error)
        self.assertIs(caught_exc.__context__, reader_error)
        self.assertIs(caught_exc.__cause__, None)
        self.assertIs(caught_exc.__suppress_context__, False)

        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args[0], ())
        self.assertEqual(rollback_mock.call_args[1], {})

        log_mock.assert_not_called()

        self._assert_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            reader_mock=reader_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
        )

        expected_events = [
            "authority_path_resolved",
            "utc_clock_evaluated",
            "reader_factory_attempted",
            "rollback_attempted",
            "cancellation_escaped",
        ]
        self.assertEqual(event_log, expected_events)

    async def test_scenario_3_reader_failure_with_logging_cancellation_escapes_with_chained_context(
        self,
    ) -> None:
        """Reader failure with successful rollback followed by logging CancelledError escapes with chained context."""
        event_log: list[str] = []
        isolated_settings = IsolatedSettings()
        synthetic_path = "/synthetic/snapshot/product_authority.json"
        fixed_now = datetime(2026, 9, 10, 8, 30, 0, tzinfo=timezone.utc)
        reader_error = SyntheticReaderFailure("simulated reader boundary failure")
        cancel_error = asyncio.CancelledError("scenario-3-logging-cancelled")

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

        def _log_trading_event(*args: Any, **kwargs: Any) -> Any:
            event_log.append("trading_event_attempted")
            raise cancel_error

        log_mock = Mock(side_effect=_log_trading_event)

        observer_mock = AsyncMock()
        formatter_mock = Mock()

        sentinel = object()
        result: Any = sentinel
        caught_exc: BaseException | None = None

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
            try:
                result = await offers_router._shadow_inference_for_implicit_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                )
                event_log.append("unexpected_return")
            except BaseException as exc:
                caught_exc = exc
                event_log.append("cancellation_escaped")

        self.assertIs(result, sentinel)

        self.assertIs(caught_exc, cancel_error)
        self.assertIs(caught_exc.__context__, reader_error)
        self.assertIs(caught_exc.__cause__, None)
        self.assertIs(caught_exc.__suppress_context__, False)

        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args[0], ())
        self.assertEqual(rollback_mock.call_args[1], {})
        self.assertEqual(rollback_mock.call_count, 1)
        self.assertEqual(rollback_mock.await_count, 1)

        log_mock.assert_called_once()
        self.assertEqual(len(log_mock.call_args_list), 1)
        self.assertEqual(log_mock.call_count, 1)
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
                "error_class": "SyntheticReaderFailure",
            },
        )
        self.assertEqual(kw_args["error_class"], type(reader_error).__name__)
        self.assertNotEqual(kw_args["error_class"], str(reader_error))
        self.assertNotEqual(kw_args["error_class"], type(cancel_error).__name__)

        rollback_index = event_log.index("rollback_completed")
        log_index = event_log.index("trading_event_attempted")
        self.assertLess(rollback_index, log_index)

        self._assert_boundary_invariants(
            resolver_mock=resolver_mock,
            isolated_settings=isolated_settings,
            clock_mock=clock_mock,
            reader_mock=reader_mock,
            observer_mock=observer_mock,
            commit_mock=commit_mock,
            formatter_mock=formatter_mock,
        )

        expected_events = [
            "authority_path_resolved",
            "utc_clock_evaluated",
            "reader_factory_attempted",
            "rollback_completed",
            "trading_event_attempted",
            "cancellation_escaped",
        ]
        self.assertEqual(event_log, expected_events)


if __name__ == "__main__":
    unittest.main()
