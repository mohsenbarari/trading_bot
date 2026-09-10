"""Characterization tests for _shadow_inference_for_implicit_commodity logging failure boundaries.

Tracked inventory gap path: docs/refactor/two-server/section-1/inventory/scenario-records.json
Tracked inventory gap ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001
Assignment path: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md

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
from core.market_intelligence.coin_catalog import (  # noqa: E402
    CatalogCoinCommodityEditCandidate,
    CatalogCoinCommodityInference,
)
from core.market_intelligence.coin_inference_shadow import (  # noqa: E402
    CoinInferenceShadowObservation,
)


class SyntheticClockFailure(Exception):
    """Synthetic exception representing a UTC clock failure."""


class SyntheticFormatterFailure(Exception):
    """Synthetic exception representing a shadow payload formatter failure."""


class SyntheticLoggingFailure(Exception):
    """Synthetic exception representing a logging subsystem failure."""


class IsolatedSettings:
    """Isolated settings object exposing only the inference preview flag."""

    coin_intelligence_inference_preview_enabled: bool = True


class StrictFakeSession:
    """Strict fake database session exposing only commit and rollback AsyncMocks."""

    __slots__ = ("commit", "rollback")

    def __init__(self, commit: AsyncMock, rollback: AsyncMock) -> None:
        self.commit = commit
        self.rollback = rollback


class ShadowLoggingFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization of logging failure boundaries in shadow inference."""

    async def test_scenario_1_clock_failure_rollback_succeeds_logging_failure_propagates(
        self,
    ) -> None:
        """UTC clock failure followed by successful rollback propagates escaping logging exception."""
        event_log: list[str] = []
        isolated_settings = IsolatedSettings()
        synthetic_path = "/synthetic/snapshot/product_authority.json"
        clock_error = SyntheticClockFailure("simulated utc clock boundary failure")
        logging_error = SyntheticLoggingFailure("simulated logging subsystem failure")

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
            event_log.append("logging_attempted")
            raise logging_error

        log_mock = Mock(side_effect=_log_trading_event)

        unreachable_marker = object()
        result = unreachable_marker

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
            with self.assertRaises(SyntheticLoggingFailure) as cm:
                result = await offers_router._shadow_inference_for_implicit_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                )
            event_log.append("logging_exception_propagated")

        self.assertIs(result, unreachable_marker)
        self.assertIs(cm.exception, logging_error)
        self.assertIsNot(cm.exception, clock_error)
        self.assertFalse(isinstance(cm.exception, SyntheticClockFailure))

        resolver_mock.assert_called_once_with(isolated_settings)
        self.assertEqual(len(resolver_mock.call_args[0]), 1)
        self.assertIs(resolver_mock.call_args[0][0], isolated_settings)
        self.assertEqual(resolver_mock.call_args[1], {})

        clock_mock.assert_called_once_with(timezone.utc)
        self.assertEqual(len(clock_mock.call_args[0]), 1)
        self.assertIs(clock_mock.call_args[0][0], timezone.utc)
        self.assertEqual(clock_mock.call_args[1], {})

        reader_mock.assert_not_called()
        formatter_mock.assert_not_called()

        observer_mock.assert_not_called()
        observer_mock.assert_not_awaited()

        commit_mock.assert_not_called()
        commit_mock.assert_not_awaited()

        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args[0], ())
        self.assertEqual(rollback_mock.call_args[1], {})

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
        self.assertNotEqual(kw_args["error_class"], type(logging_error).__name__)
        self.assertNotEqual(kw_args["error_class"], str(clock_error))

        expected_events = [
            "authority_path_resolved",
            "clock_attempted",
            "rollback_completed",
            "logging_attempted",
            "logging_exception_propagated",
        ]
        self.assertEqual(event_log, expected_events)

        resolution_index = event_log.index("authority_path_resolved")
        clock_index = event_log.index("clock_attempted")
        rollback_index = event_log.index("rollback_completed")
        logging_index = event_log.index("logging_attempted")
        propagation_index = event_log.index("logging_exception_propagated")
        self.assertLess(resolution_index, clock_index)
        self.assertLess(clock_index, rollback_index)
        self.assertLess(rollback_index, logging_index)
        self.assertLess(logging_index, propagation_index)

    async def test_scenario_2_formatter_failure_after_commit_rollback_succeeds_logging_failure_propagates(
        self,
    ) -> None:
        """Formatter failure after commit followed by successful rollback propagates escaping logging exception."""
        event_log: list[str] = []
        isolated_settings = IsolatedSettings()
        synthetic_path = "/synthetic/snapshot/product_authority.json"
        fixed_now = datetime(2026, 9, 10, 8, 30, 0, tzinfo=timezone.utc)
        reader_sentinel = object()
        formatter_error = SyntheticFormatterFailure("simulated payload formatting failure")
        logging_error = SyntheticLoggingFailure("simulated logging subsystem failure")

        synthetic_decision = CatalogCoinCommodityInference(
            status="CANDIDATE",
            settlement_term="CASH",
            candidates=(),
            snapshot_generated_at_utc="2026-09-10T08:30:00Z",
            snapshot_receipt="rcpt-synthetic-001",
            reason=None,
        )
        synthetic_edit_candidates = (
            CatalogCoinCommodityEditCandidate(
                commodity_id=101,
                commodity_code="EMAMI_86",
                commodity_name="Emami Coin 86",
            ),
        )
        synthetic_observation = CoinInferenceShadowObservation(
            decision_key="synthetic_decision_key_abc123",
            decision=synthetic_decision,
            edit_candidates=synthetic_edit_candidates,
        )

        def _resolve_path(settings_obj: Any) -> str:
            event_log.append("authority_path_resolved")
            return synthetic_path

        resolver_mock = Mock(side_effect=_resolve_path)

        def _clock_now(tz: Any = None) -> Any:
            event_log.append("clock_evaluated")
            return fixed_now

        clock_mock = Mock(side_effect=_clock_now)
        mock_datetime = Mock()
        mock_datetime.now = clock_mock

        def _reader_factory() -> Any:
            event_log.append("reader_configured")
            return reader_sentinel

        reader_mock = Mock(side_effect=_reader_factory)

        async def _observe(*args: Any, **kwargs: Any) -> Any:
            event_log.append("observer_completed")
            return synthetic_observation

        observer_mock = AsyncMock(side_effect=_observe)

        async def _commit_impl(*args: Any, **kwargs: Any) -> None:
            event_log.append("commit_completed")

        commit_mock = AsyncMock(side_effect=_commit_impl)

        def _format_payload(*args: Any, **kwargs: Any) -> Any:
            event_log.append("formatter_failed")
            raise formatter_error

        formatter_mock = Mock(side_effect=_format_payload)

        async def _rollback_impl(*args: Any, **kwargs: Any) -> None:
            event_log.append("rollback_completed")

        rollback_mock = AsyncMock(side_effect=_rollback_impl)
        session = StrictFakeSession(commit=commit_mock, rollback=rollback_mock)

        def _log_trading_event(*args: Any, **kwargs: Any) -> dict[str, Any]:
            event_log.append("logging_attempted")
            raise logging_error

        log_mock = Mock(side_effect=_log_trading_event)

        unreachable_marker = object()
        result = unreachable_marker

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
            with self.assertRaises(SyntheticLoggingFailure) as cm:
                result = await offers_router._shadow_inference_for_implicit_commodity(
                    session,
                    price=54_000_000,
                    settlement_type="cash",
                )
            event_log.append("logging_exception_propagated")

        self.assertIs(result, unreachable_marker)
        self.assertIs(cm.exception, logging_error)
        self.assertIsNot(cm.exception, formatter_error)
        self.assertFalse(isinstance(cm.exception, SyntheticFormatterFailure))

        resolver_mock.assert_called_once_with(isolated_settings)
        self.assertEqual(len(resolver_mock.call_args[0]), 1)
        self.assertIs(resolver_mock.call_args[0][0], isolated_settings)
        self.assertEqual(resolver_mock.call_args[1], {})

        clock_mock.assert_called_once_with(timezone.utc)
        self.assertEqual(len(clock_mock.call_args[0]), 1)
        self.assertIs(clock_mock.call_args[0][0], timezone.utc)
        self.assertEqual(clock_mock.call_args[1], {})

        reader_mock.assert_called_once_with()
        self.assertEqual(reader_mock.call_args[0], ())
        self.assertEqual(reader_mock.call_args[1], {})

        observer_mock.assert_called_once_with(
            session,
            snapshot_path=synthetic_path,
            submitted_project_price=54_000_000,
            settlement_term="CASH",
            source_surface="WEBAPP",
            now_utc=fixed_now,
            snapshot_reader=reader_sentinel,
        )
        observer_mock.assert_awaited_once_with(
            session,
            snapshot_path=synthetic_path,
            submitted_project_price=54_000_000,
            settlement_term="CASH",
            source_surface="WEBAPP",
            now_utc=fixed_now,
            snapshot_reader=reader_sentinel,
        )
        self.assertEqual(len(observer_mock.call_args[0]), 1)
        self.assertIs(observer_mock.call_args[0][0], session)
        obs_kw = observer_mock.call_args[1]
        self.assertEqual(obs_kw["snapshot_path"], synthetic_path)
        self.assertEqual(obs_kw["submitted_project_price"], 54_000_000)
        self.assertEqual(obs_kw["settlement_term"], "CASH")
        self.assertEqual(obs_kw["source_surface"], "WEBAPP")
        self.assertIs(obs_kw["now_utc"], fixed_now)
        self.assertIs(obs_kw["snapshot_reader"], reader_sentinel)

        commit_mock.assert_called_once_with()
        commit_mock.assert_awaited_once_with()
        self.assertEqual(commit_mock.call_args[0], ())
        self.assertEqual(commit_mock.call_args[1], {})

        formatter_mock.assert_called_once_with(
            synthetic_decision,
            decision_key="synthetic_decision_key_abc123",
            edit_candidates=synthetic_edit_candidates,
        )
        self.assertEqual(len(formatter_mock.call_args[0]), 1)
        self.assertIs(formatter_mock.call_args[0][0], synthetic_decision)
        self.assertEqual(
            formatter_mock.call_args[1]["decision_key"],
            "synthetic_decision_key_abc123",
        )
        self.assertIs(
            formatter_mock.call_args[1]["edit_candidates"],
            synthetic_edit_candidates,
        )

        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args[0], ())
        self.assertEqual(rollback_mock.call_args[1], {})

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
                "error_class": "SyntheticFormatterFailure",
            },
        )
        self.assertEqual(kw_args["error_class"], type(formatter_error).__name__)
        self.assertEqual(kw_args["error_class"], "SyntheticFormatterFailure")
        self.assertNotEqual(kw_args["error_class"], type(logging_error).__name__)
        self.assertNotEqual(kw_args["error_class"], str(formatter_error))

        expected_events = [
            "authority_path_resolved",
            "clock_evaluated",
            "reader_configured",
            "observer_completed",
            "commit_completed",
            "formatter_failed",
            "rollback_completed",
            "logging_attempted",
            "logging_exception_propagated",
        ]
        self.assertEqual(event_log, expected_events)

        resolution_index = event_log.index("authority_path_resolved")
        clock_index = event_log.index("clock_evaluated")
        reader_index = event_log.index("reader_configured")
        observer_index = event_log.index("observer_completed")
        commit_index = event_log.index("commit_completed")
        formatter_index = event_log.index("formatter_failed")
        rollback_index = event_log.index("rollback_completed")
        logging_index = event_log.index("logging_attempted")
        propagation_index = event_log.index("logging_exception_propagated")

        self.assertLess(resolution_index, clock_index)
        self.assertLess(clock_index, reader_index)
        self.assertLess(reader_index, observer_index)
        self.assertLess(observer_index, commit_index)
        self.assertLess(commit_index, formatter_index)
        self.assertLess(formatter_index, rollback_index)
        self.assertLess(rollback_index, logging_index)
        self.assertLess(logging_index, propagation_index)


if __name__ == "__main__":
    unittest.main()
