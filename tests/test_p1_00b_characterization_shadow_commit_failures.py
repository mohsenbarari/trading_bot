"""Characterization unit for api.routers.offers._shadow_inference_for_implicit_commodity.

Tracked Inventory Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage Card: P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
Assignment: WA-P1-00B-FREEZE-20260904-001 (/root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md)

This unit characterizes helper transaction failure behavior when db.commit raises
following successful shadow observation, testing both successful and failing
rollback paths. It proves helper commit/rollback/logging behavior with mocked
observation without claiming seed/Stage closure or real estimator/audit persistence.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timezone
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import api.routers.offers as offers_router
from core.config import settings
from core.market_intelligence.coin_catalog import (
    CatalogCoinCommodityCandidate,
    CatalogCoinCommodityEditCandidate,
    CatalogCoinCommodityInference,
)
from core.market_intelligence.coin_inference_shadow import (
    CoinInferenceShadowObservation,
)


class SyntheticCommitError(RuntimeError):
    """Synthetic exception simulating database commit failure."""


class SyntheticRollbackError(RuntimeError):
    """Synthetic exception simulating database rollback failure."""


class StrictFakeSession:
    """Strict fake session exposing only commit and rollback AsyncMocks."""

    __slots__ = ("commit", "rollback")

    def __init__(self, commit: AsyncMock, rollback: AsyncMock) -> None:
        self.commit = commit
        self.rollback = rollback


class TestShadowCommitFailuresCharacterization(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for shadow inference commit and rollback failure contracts."""

    def _create_harness(
        self,
        *,
        commit_exc: Exception,
        rollback_exc: Exception | None = None,
    ) -> dict[str, Any]:
        events: list[str] = []
        fixed_now_utc = datetime(2026, 9, 10, 7, 45, 0, tzinfo=timezone.utc)
        synthetic_snapshot_path = "/synthetic/snapshots/authority.json"
        reader_sentinel = object()

        observation_sentinel = CoinInferenceShadowObservation(
            decision_key="synthetic-decision-key-0123456789abcdef",
            decision=CatalogCoinCommodityInference(
                status="ACCEPTED",
                settlement_term="CASH",
                candidates=(
                    CatalogCoinCommodityCandidate(
                        commodity_id=42,
                        commodity_code="EMAMI_SYNTHETIC",
                        commodity_name="Synthetic Emami",
                        center_project_price=10_500_000,
                        lower_project_price=10_000_000,
                        upper_project_price=11_000_000,
                        confidence="HIGH",
                        distance_to_center_relative=0.005,
                    ),
                ),
                snapshot_generated_at_utc="2026-09-10T00:00:00Z",
                snapshot_receipt="synthetic-snapshot-receipt-001",
                reason=None,
            ),
            edit_candidates=(
                CatalogCoinCommodityEditCandidate(
                    commodity_id=43,
                    commodity_code="BAHAR_SYNTHETIC",
                    commodity_name="Synthetic Bahar",
                ),
            ),
        )

        def resolver_spy(settings_arg: object) -> str:
            events.append("resolution")
            return synthetic_snapshot_path

        def clock_spy(tz: object) -> datetime:
            events.append("clock")
            return fixed_now_utc

        def reader_factory_spy() -> object:
            events.append("reader_construction")
            return reader_sentinel

        async def observer_spy(*args: Any, **kwargs: Any) -> CoinInferenceShadowObservation:
            events.append("observer_completion")
            return observation_sentinel

        async def commit_spy(*args: Any, **kwargs: Any) -> None:
            events.append("commit_failure")
            raise commit_exc

        async def rollback_spy(*args: Any, **kwargs: Any) -> None:
            events.append("rollback_await")
            if rollback_exc is not None:
                raise rollback_exc

        def log_spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
            events.append("log")
            return {}

        resolver_mock = MagicMock(side_effect=resolver_spy)
        clock_mock = MagicMock()
        clock_mock.now.side_effect = clock_spy
        reader_factory_mock = MagicMock(side_effect=reader_factory_spy)
        observer_mock = AsyncMock(side_effect=observer_spy)
        commit_mock = AsyncMock(side_effect=commit_spy)
        rollback_mock = AsyncMock(side_effect=rollback_spy)
        formatter_mock = MagicMock()
        log_mock = MagicMock(side_effect=log_spy)

        fake_session = StrictFakeSession(commit=commit_mock, rollback=rollback_mock)

        return {
            "events": events,
            "fixed_now_utc": fixed_now_utc,
            "synthetic_snapshot_path": synthetic_snapshot_path,
            "reader_sentinel": reader_sentinel,
            "observation_sentinel": observation_sentinel,
            "resolver_mock": resolver_mock,
            "clock_mock": clock_mock,
            "reader_factory_mock": reader_factory_mock,
            "observer_mock": observer_mock,
            "commit_mock": commit_mock,
            "rollback_mock": rollback_mock,
            "formatter_mock": formatter_mock,
            "log_mock": log_mock,
            "fake_session": fake_session,
        }

    def _assert_common_contract(
        self,
        harness: dict[str, Any],
        *,
        price: int,
        expected_settlement_term: str,
    ) -> None:
        resolver_mock = harness["resolver_mock"]
        resolver_mock.assert_called_once_with(settings)
        self.assertEqual(len(resolver_mock.call_args[0]), 1)
        self.assertIs(resolver_mock.call_args[0][0], settings)
        self.assertEqual(resolver_mock.call_args[1], {})

        clock_mock = harness["clock_mock"]
        clock_mock.now.assert_called_once_with(timezone.utc)
        self.assertEqual(len(clock_mock.now.call_args[0]), 1)
        self.assertIs(clock_mock.now.call_args[0][0], timezone.utc)
        self.assertEqual(clock_mock.now.call_args[1], {})

        reader_factory_mock = harness["reader_factory_mock"]
        reader_factory_mock.assert_called_once_with()
        self.assertEqual(reader_factory_mock.call_args, ((), {}))

        fake_session = harness["fake_session"]
        expected_kwargs = {
            "snapshot_path": harness["synthetic_snapshot_path"],
            "submitted_project_price": price,
            "settlement_term": expected_settlement_term,
            "source_surface": "WEBAPP",
            "now_utc": harness["fixed_now_utc"],
            "snapshot_reader": harness["reader_sentinel"],
        }
        expected_kwarg_keys = {
            "snapshot_path",
            "submitted_project_price",
            "settlement_term",
            "source_surface",
            "now_utc",
            "snapshot_reader",
        }

        observer_mock = harness["observer_mock"]
        observer_mock.assert_called_once_with(fake_session, **expected_kwargs)
        observer_mock.assert_awaited_once_with(fake_session, **expected_kwargs)

        self.assertIsNotNone(observer_mock.call_args)
        self.assertEqual(len(observer_mock.call_args[0]), 1)
        self.assertIs(observer_mock.call_args[0][0], fake_session)
        self.assertEqual(set(observer_mock.call_args[1].keys()), expected_kwarg_keys)
        self.assertIs(
            observer_mock.call_args[1]["snapshot_path"],
            harness["synthetic_snapshot_path"],
        )
        self.assertIs(
            observer_mock.call_args[1]["submitted_project_price"],
            price,
        )
        self.assertEqual(
            observer_mock.call_args[1]["settlement_term"],
            expected_settlement_term,
        )
        self.assertEqual(
            observer_mock.call_args[1]["source_surface"],
            "WEBAPP",
        )
        self.assertIs(
            observer_mock.call_args[1]["now_utc"],
            harness["fixed_now_utc"],
        )
        self.assertIs(
            observer_mock.call_args[1]["snapshot_reader"],
            harness["reader_sentinel"],
        )

        self.assertIsNotNone(observer_mock.await_args)
        self.assertEqual(len(observer_mock.await_args[0]), 1)
        self.assertIs(observer_mock.await_args[0][0], fake_session)
        self.assertEqual(set(observer_mock.await_args[1].keys()), expected_kwarg_keys)
        self.assertIs(
            observer_mock.await_args[1]["snapshot_path"],
            harness["synthetic_snapshot_path"],
        )
        self.assertIs(
            observer_mock.await_args[1]["submitted_project_price"],
            price,
        )
        self.assertEqual(
            observer_mock.await_args[1]["settlement_term"],
            expected_settlement_term,
        )
        self.assertEqual(
            observer_mock.await_args[1]["source_surface"],
            "WEBAPP",
        )
        self.assertIs(
            observer_mock.await_args[1]["now_utc"],
            harness["fixed_now_utc"],
        )
        self.assertIs(
            observer_mock.await_args[1]["snapshot_reader"],
            harness["reader_sentinel"],
        )

        commit_mock = harness["commit_mock"]
        commit_mock.assert_called_once_with()
        commit_mock.assert_awaited_once_with()
        self.assertEqual(commit_mock.call_args, ((), {}))
        self.assertEqual(commit_mock.await_args, ((), {}))

        rollback_mock = harness["rollback_mock"]
        rollback_mock.assert_called_once_with()
        rollback_mock.assert_awaited_once_with()
        self.assertEqual(rollback_mock.call_args, ((), {}))
        self.assertEqual(rollback_mock.await_args, ((), {}))

        harness["formatter_mock"].assert_not_called()

    async def test_shadow_commit_failure_with_successful_rollback_returns_abstain_and_logs(
        self,
    ) -> None:
        """Scenario 1: Commit fails, rollback succeeds -> fallback payload returned and logged."""
        commit_exc = SyntheticCommitError("synthetic database commit failure")
        harness = self._create_harness(commit_exc=commit_exc, rollback_exc=None)

        price = 10_500_000
        settlement_type = "cash"
        expected_settlement_term = "CASH"

        with (
            patch.object(
                offers_router.settings,
                "coin_intelligence_inference_preview_enabled",
                True,
            ),
            patch.object(
                offers_router,
                "configured_product_snapshot_authority_path",
                harness["resolver_mock"],
            ),
            patch.object(offers_router, "datetime", harness["clock_mock"]),
            patch.object(
                offers_router,
                "_configured_product_snapshot_reader",
                harness["reader_factory_mock"],
            ),
            patch.object(
                offers_router,
                "observe_coin_inference_shadow",
                harness["observer_mock"],
            ),
            patch.object(
                offers_router,
                "_catalog_inference_shadow_payload",
                harness["formatter_mock"],
            ),
            patch.object(
                offers_router,
                "log_trading_event",
                harness["log_mock"],
            ),
        ):
            result = await offers_router._shadow_inference_for_implicit_commodity(
                harness["fake_session"],
                price=price,
                settlement_type=settlement_type,
            )

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
        self.assertEqual(
            set(result.keys()),
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
        self.assertEqual(len(result), 7)
        self.assertNotIn("edit_candidates", result)
        self.assertIsNone(result["decision_key"])
        self.assertIsNone(result["snapshot_generated_at_utc"])
        self.assertIsNone(result["snapshot_receipt"])
        self.assertEqual(result["candidates"], [])

        log_mock = harness["log_mock"]
        log_mock.assert_called_once()
        self.assertEqual(len(log_mock.call_args[0]), 2)
        self.assertIs(log_mock.call_args[0][0], offers_router.logger)
        self.assertEqual(
            log_mock.call_args[0][1], "coin_inference_parse_shadow.unavailable"
        )
        self.assertEqual(
            log_mock.call_args.kwargs,
            {
                "level": "warning",
                "action": "coin_inference_parse_shadow",
                "result": "abstained",
                "error_class": "SyntheticCommitError",
            },
        )
        self.assertEqual(
            log_mock.call_args.kwargs["error_class"],
            type(commit_exc).__name__,
        )
        self.assertNotEqual(
            log_mock.call_args.kwargs["error_class"],
            str(commit_exc),
        )

        self.assertEqual(
            harness["events"],
            [
                "resolution",
                "clock",
                "reader_construction",
                "observer_completion",
                "commit_failure",
                "rollback_await",
                "log",
            ],
        )

        self._assert_common_contract(
            harness,
            price=price,
            expected_settlement_term=expected_settlement_term,
        )

    async def test_shadow_commit_failure_with_failing_rollback_propagates_rollback_exception(
        self,
    ) -> None:
        """Scenario 2: Commit fails, rollback fails -> rollback exception propagates identically."""
        commit_exc = SyntheticCommitError("synthetic database commit failure")
        rollback_exc = SyntheticRollbackError("synthetic database rollback failure")
        harness = self._create_harness(
            commit_exc=commit_exc,
            rollback_exc=rollback_exc,
        )

        price = 11_200_000
        settlement_type = "tomorrow"
        expected_settlement_term = "TOMORROW"

        with (
            patch.object(
                offers_router.settings,
                "coin_intelligence_inference_preview_enabled",
                True,
            ),
            patch.object(
                offers_router,
                "configured_product_snapshot_authority_path",
                harness["resolver_mock"],
            ),
            patch.object(offers_router, "datetime", harness["clock_mock"]),
            patch.object(
                offers_router,
                "_configured_product_snapshot_reader",
                harness["reader_factory_mock"],
            ),
            patch.object(
                offers_router,
                "observe_coin_inference_shadow",
                harness["observer_mock"],
            ),
            patch.object(
                offers_router,
                "_catalog_inference_shadow_payload",
                harness["formatter_mock"],
            ),
            patch.object(
                offers_router,
                "log_trading_event",
                harness["log_mock"],
            ),
        ):
            with self.assertRaises(SyntheticRollbackError) as ctx:
                await offers_router._shadow_inference_for_implicit_commodity(
                    harness["fake_session"],
                    price=price,
                    settlement_type=settlement_type,
                )

        self.assertIs(ctx.exception, rollback_exc)
        self.assertEqual(type(ctx.exception), SyntheticRollbackError)

        harness["log_mock"].assert_not_called()

        self.assertEqual(
            harness["events"],
            [
                "resolution",
                "clock",
                "reader_construction",
                "observer_completion",
                "commit_failure",
                "rollback_await",
            ],
        )

        self._assert_common_contract(
            harness,
            price=price,
            expected_settlement_term=expected_settlement_term,
        )


if __name__ == "__main__":
    unittest.main()
