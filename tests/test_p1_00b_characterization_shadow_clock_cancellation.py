"""Hermetic UTC-clock cancellation characterization for _shadow_inference_for_implicit_commodity.

Tracked inventory path: docs/refactor/two-server/section-1/inventory/scenario-records.json
Gap ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

These tests are helper-only mocked-boundary cases without estimator/audit
persistence or full parse/seed/Stage closure.
"""

from __future__ import annotations

import os

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


class IsolatedSettings:
    """Isolated settings object exposing only the inference preview flag."""

    coin_intelligence_inference_preview_enabled: bool = True


class StrictFakeSession:
    """Strict fake database session exposing only commit and rollback AsyncMocks."""

    __slots__ = ("commit", "rollback")

    def __init__(self, commit: AsyncMock, rollback: AsyncMock) -> None:
        self.commit = commit
        self.rollback = rollback


class ShadowClockCancellationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize UTC-clock cancellation outcomes for the shadow inference helper."""

    async def test_direct_utc_clock_cancellation_escapes_without_rollback_or_logging(
        self,
    ) -> None:
        events: list[str] = []

        mock_commit = AsyncMock()
        mock_rollback = AsyncMock()

        async def unexpected_rollback(*args: Any, **kwargs: Any) -> None:
            events.append("rollback")

        mock_rollback.side_effect = unexpected_rollback
        fake_session = StrictFakeSession(commit=mock_commit, rollback=mock_rollback)

        isolated_settings = IsolatedSettings()
        cancellation_error = asyncio.CancelledError("clock cancelled directly")

        mock_resolver = Mock()

        def resolver_action(settings_arg: Any) -> str:
            events.append("resolver")
            return "/synthetic/authority/path/snapshot.json"

        mock_resolver.side_effect = resolver_action

        mock_datetime = Mock()
        mock_now = Mock()

        def clock_action(tz_arg: Any) -> Any:
            events.append("clock")
            raise cancellation_error

        mock_now.side_effect = clock_action
        mock_datetime.now = mock_now

        mock_reader_factory = Mock()
        mock_formatter = Mock()
        mock_observer = AsyncMock()
        mock_logger = Mock()
        mock_log_trading_event = Mock()

        sentinel = object()
        result: Any = sentinel
        caught_exc: BaseException | None = None

        with (
            patch.object(offers_router, "settings", isolated_settings),
            patch.object(
                offers_router,
                "configured_product_snapshot_authority_path",
                mock_resolver,
            ),
            patch.object(offers_router, "datetime", mock_datetime),
            patch.object(
                offers_router,
                "_configured_product_snapshot_reader",
                mock_reader_factory,
            ),
            patch.object(
                offers_router,
                "observe_coin_inference_shadow",
                mock_observer,
            ),
            patch.object(
                offers_router,
                "_catalog_inference_shadow_payload",
                mock_formatter,
            ),
            patch.object(offers_router, "logger", mock_logger),
            patch.object(offers_router, "log_trading_event", mock_log_trading_event),
        ):
            try:
                result = await offers_router._shadow_inference_for_implicit_commodity(
                    fake_session,
                    price=50_000_000,
                    settlement_type="cash",
                )
                events.append("unexpected_return")
            except asyncio.CancelledError as exc:
                events.append("caught_cancellation")
                caught_exc = exc

        # Explicitly detect unexpected return
        self.assertNotIn("unexpected_return", events)
        if caught_exc is None:
            self.fail(
                f"Expected asyncio.CancelledError to escape, but got return value: {result!r}"
            )

        # Exact instance escapes, context/cause are None, suppress_context is False
        self.assertIs(caught_exc, cancellation_error)
        self.assertIsNone(caught_exc.__context__)
        self.assertIsNone(caught_exc.__cause__)
        self.assertFalse(caught_exc.__suppress_context__)

        # Result sentinel remains unchanged (no fallback)
        self.assertIs(result, sentinel)

        # Rollback has zero calls AND awaits
        mock_rollback.assert_not_called()
        mock_rollback.assert_not_awaited()
        self.assertEqual(mock_rollback.call_count, 0)
        self.assertEqual(mock_rollback.await_count, 0)

        # No logging produced
        mock_logger.assert_not_called()
        self.assertEqual(len(mock_logger.mock_calls), 0)
        mock_log_trading_event.assert_not_called()
        self.assertEqual(mock_log_trading_event.call_count, 0)

        # Resolver called exactly once with exact isolated settings and no kwargs
        mock_resolver.assert_called_once_with(isolated_settings)
        self.assertIs(mock_resolver.call_args.args[0], isolated_settings)
        self.assertEqual(mock_resolver.call_args.kwargs, {})

        # Clock called exactly once with identical timezone.utc and no kwargs
        mock_now.assert_called_once_with(timezone.utc)
        self.assertIs(mock_now.call_args.args[0], timezone.utc)
        self.assertEqual(mock_now.call_args.kwargs, {})

        # Reader factory and formatter have zero calls
        mock_reader_factory.assert_not_called()
        self.assertEqual(mock_reader_factory.call_count, 0)
        mock_formatter.assert_not_called()
        self.assertEqual(mock_formatter.call_count, 0)

        # Observer and commit have zero calls AND awaits
        mock_observer.assert_not_called()
        mock_observer.assert_not_awaited()
        self.assertEqual(mock_observer.call_count, 0)
        self.assertEqual(mock_observer.await_count, 0)
        mock_commit.assert_not_called()
        mock_commit.assert_not_awaited()
        self.assertEqual(mock_commit.call_count, 0)
        self.assertEqual(mock_commit.await_count, 0)

        # Independent assertion of expected side-effect sequence
        self.assertEqual(events, ["resolver", "clock", "caught_cancellation"])

    async def test_clock_failure_then_rollback_cancellation_escapes_with_context_and_no_logging(
        self,
    ) -> None:
        events: list[str] = []

        clock_error = SyntheticClockFailure("UTC clock read failed")
        rollback_cancellation = asyncio.CancelledError("rollback cancelled")

        # Verify initial state of clock_error before triggering
        self.assertIsNone(clock_error.__context__)
        self.assertIsNone(clock_error.__cause__)
        self.assertFalse(clock_error.__suppress_context__)

        mock_commit = AsyncMock()
        mock_rollback = AsyncMock()

        async def rollback_action(*args: Any, **kwargs: Any) -> None:
            events.append("rollback")
            raise rollback_cancellation

        mock_rollback.side_effect = rollback_action
        fake_session = StrictFakeSession(commit=mock_commit, rollback=mock_rollback)

        isolated_settings = IsolatedSettings()

        mock_resolver = Mock()

        def resolver_action(settings_arg: Any) -> str:
            events.append("resolver")
            return "/synthetic/authority/path/snapshot.json"

        mock_resolver.side_effect = resolver_action

        mock_datetime = Mock()
        mock_now = Mock()

        def clock_action(tz_arg: Any) -> Any:
            events.append("clock")
            raise clock_error

        mock_now.side_effect = clock_action
        mock_datetime.now = mock_now

        mock_reader_factory = Mock()
        mock_formatter = Mock()
        mock_observer = AsyncMock()
        mock_logger = Mock()
        mock_log_trading_event = Mock()

        sentinel = object()
        result: Any = sentinel
        caught_exc: BaseException | None = None

        with (
            patch.object(offers_router, "settings", isolated_settings),
            patch.object(
                offers_router,
                "configured_product_snapshot_authority_path",
                mock_resolver,
            ),
            patch.object(offers_router, "datetime", mock_datetime),
            patch.object(
                offers_router,
                "_configured_product_snapshot_reader",
                mock_reader_factory,
            ),
            patch.object(
                offers_router,
                "observe_coin_inference_shadow",
                mock_observer,
            ),
            patch.object(
                offers_router,
                "_catalog_inference_shadow_payload",
                mock_formatter,
            ),
            patch.object(offers_router, "logger", mock_logger),
            patch.object(offers_router, "log_trading_event", mock_log_trading_event),
        ):
            try:
                result = await offers_router._shadow_inference_for_implicit_commodity(
                    fake_session,
                    price=50_000_000,
                    settlement_type="cash",
                )
                events.append("unexpected_return")
            except asyncio.CancelledError as exc:
                events.append("caught_cancellation")
                caught_exc = exc

        # Explicitly detect unexpected return
        self.assertNotIn("unexpected_return", events)
        if caught_exc is None:
            self.fail(
                f"Expected asyncio.CancelledError to escape, but got return value: {result!r}"
            )

        # Exact escaping cancellation identity
        self.assertIs(caught_exc, rollback_cancellation)

        # __context__ identity with the original clock error
        self.assertIs(caught_exc.__context__, clock_error)

        # __cause__ None and __suppress_context__ False
        self.assertIsNone(caught_exc.__cause__)
        self.assertFalse(caught_exc.__suppress_context__)

        # Original clock error has no context/cause and no suppression
        self.assertIsNone(clock_error.__context__)
        self.assertIsNone(clock_error.__cause__)
        self.assertFalse(clock_error.__suppress_context__)

        # Rollback called and awaited exactly once with no arguments
        mock_rollback.assert_called_once_with()
        mock_rollback.assert_awaited_once_with()
        self.assertEqual(mock_rollback.call_count, 1)
        self.assertEqual(mock_rollback.await_count, 1)
        self.assertEqual(mock_rollback.call_args.args, ())
        self.assertEqual(mock_rollback.call_args.kwargs, {})
        self.assertEqual(mock_rollback.await_args.args, ())
        self.assertEqual(mock_rollback.await_args.kwargs, {})

        # No logging, unchanged result sentinel, and no retry or fallback
        mock_logger.assert_not_called()
        self.assertEqual(len(mock_logger.mock_calls), 0)
        mock_log_trading_event.assert_not_called()
        self.assertEqual(mock_log_trading_event.call_count, 0)
        self.assertIs(result, sentinel)

        # Resolver called exactly once with exact isolated settings and no kwargs (no retry)
        mock_resolver.assert_called_once_with(isolated_settings)
        self.assertIs(mock_resolver.call_args.args[0], isolated_settings)
        self.assertEqual(mock_resolver.call_args.kwargs, {})

        # Clock called exactly once with identical timezone.utc and no kwargs (no retry)
        mock_now.assert_called_once_with(timezone.utc)
        self.assertIs(mock_now.call_args.args[0], timezone.utc)
        self.assertEqual(mock_now.call_args.kwargs, {})

        # Reader factory and formatter have zero calls
        mock_reader_factory.assert_not_called()
        self.assertEqual(mock_reader_factory.call_count, 0)
        mock_formatter.assert_not_called()
        self.assertEqual(mock_formatter.call_count, 0)

        # Observer and commit have zero calls AND awaits
        mock_observer.assert_not_called()
        mock_observer.assert_not_awaited()
        self.assertEqual(mock_observer.call_count, 0)
        self.assertEqual(mock_observer.await_count, 0)
        mock_commit.assert_not_called()
        mock_commit.assert_not_awaited()
        self.assertEqual(mock_commit.call_count, 0)
        self.assertEqual(mock_commit.await_count, 0)

        # Independent assertion of expected side-effect sequence
        self.assertEqual(
            events,
            ["resolver", "clock", "rollback", "caught_cancellation"],
        )


if __name__ == "__main__":
    unittest.main()
