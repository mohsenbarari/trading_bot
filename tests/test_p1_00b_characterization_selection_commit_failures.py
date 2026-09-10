"""Characterization tests for _selection_inference_for_missing_commodity commit errors.

Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

These tests provide helper-level commit-error handling characterization only;
they do not establish handler closure, inference internals, audit durability,
seed closure, or Stage acceptance.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
import unittest
from unittest import mock

from api.routers import offers
from core.market_intelligence.coin_catalog import (
    CatalogCoinCommodityCandidate,
    CatalogCoinCommodityEditCandidate,
    CatalogCoinCommodityInference,
)
from core.market_intelligence.coin_inference import (
    COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
    COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
)
from core.market_intelligence.coin_inference_shadow import (
    CoinInferenceShadowObservation,
)


class SyntheticCommitError(RuntimeError):
    """Distinct synthetic exception raised by DB commit mock."""


class SyntheticRollbackError(RuntimeError):
    """Distinct synthetic exception raised by DB rollback mock."""


class SyntheticSettings:
    """Minimal synthetic settings fixture for selection inference tests."""

    def __init__(self, *, auto_selection_enabled: bool) -> None:
        self.coin_intelligence_inference_auto_selection_enabled = auto_selection_enabled


class StrictFakeAsyncSession:
    """Strict fake AsyncSession rejecting unexpected DB operations."""

    def __init__(
        self,
        *,
        commit_exc: BaseException | None = None,
        rollback_exc: BaseException | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self._commit_exc = commit_exc
        self._rollback_exc = rollback_exc
        self._timeline = timeline
        self.commit = mock.AsyncMock(side_effect=self._on_commit)
        self.rollback = mock.AsyncMock(side_effect=self._on_rollback)

    async def _on_commit(self) -> None:
        if self._commit_exc is not None:
            if self._timeline is not None:
                self._timeline.append("commit_awaited")
            raise self._commit_exc
        if self._timeline is not None:
            self._timeline.append("commit_completed")

    async def _on_rollback(self) -> None:
        if self._rollback_exc is not None:
            if self._timeline is not None:
                self._timeline.append("rollback_awaited")
            raise self._rollback_exc
        if self._timeline is not None:
            self._timeline.append("rollback_completed")

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unexpected DB operation on strict fake session: {name}")


def _build_valid_observation(
    *,
    price: int = 54_000_000,
    settlement_term: str = "CASH",
) -> CoinInferenceShadowObservation:
    """Build a valid successful observation with nonempty candidates and edit_candidates."""
    candidate = CatalogCoinCommodityCandidate(
        commodity_id=1,
        commodity_code="EMAMI",
        commodity_name="سکه تمام بهار آزادی طرح جدید",
        center_project_price=price,
        lower_project_price=price - 500_000,
        upper_project_price=price + 500_000,
        confidence="HIGH",
        distance_to_center_relative=0.0,
    )
    decision = CatalogCoinCommodityInference(
        status="DECIDED",
        settlement_term=settlement_term,
        candidates=(candidate,),
        snapshot_generated_at_utc="2026-09-10T06:00:00Z",
        snapshot_receipt="rcpt-valid-receipt-commit-test",
        reason=None,
    )
    edit_candidate = CatalogCoinCommodityEditCandidate(
        commodity_id=2,
        commodity_code="BAHAR",
        commodity_name="سکه بهار آزادی طرح قدیم",
    )
    return CoinInferenceShadowObservation(
        decision_key="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        decision=decision,
        edit_candidates=(edit_candidate,),
    )


class _SelectionHarness:
    """Container holding controlled mocks for one test run."""

    def __init__(
        self,
        *,
        synthetic_settings: SyntheticSettings,
        snapshot_path: str,
        reader_sentinel: object,
        fixed_now: datetime,
        valid_observation: CoinInferenceShadowObservation,
        mock_path_resolver: mock.Mock,
        mock_reader_factory: mock.Mock,
        mock_observe: mock.AsyncMock,
        mock_payload_formatter: mock.Mock,
        mock_log_trading_event: mock.Mock,
        mock_datetime: mock.Mock,
        timeline: list[str],
    ) -> None:
        self.synthetic_settings = synthetic_settings
        self.snapshot_path = snapshot_path
        self.reader_sentinel = reader_sentinel
        self.fixed_now = fixed_now
        self.valid_observation = valid_observation
        self.mock_path_resolver = mock_path_resolver
        self.mock_reader_factory = mock_reader_factory
        self.mock_observe = mock_observe
        self.mock_payload_formatter = mock_payload_formatter
        self.mock_log_trading_event = mock_log_trading_event
        self.mock_datetime = mock_datetime
        self.timeline = timeline


@contextmanager
def _selection_test_context(
    *,
    auto_selection_enabled: bool,
    valid_observation: CoinInferenceShadowObservation,
    timeline: list[str],
    snapshot_path: str = "/synthetic/snapshots/authority.json",
) -> Iterator[_SelectionHarness]:
    synthetic_settings = SyntheticSettings(
        auto_selection_enabled=auto_selection_enabled
    )
    reader_sentinel = object()
    fixed_now = datetime(2026, 9, 10, 6, 0, 0, tzinfo=timezone.utc)

    mock_path_resolver = mock.Mock(return_value=snapshot_path)
    mock_reader_factory = mock.Mock(return_value=reader_sentinel)

    async def _mock_observe(*args: Any, **kwargs: Any) -> Any:
        timeline.append("observation_completed")
        return valid_observation

    mock_observe = mock.AsyncMock(side_effect=_mock_observe)

    def _mock_format(*args: Any, **kwargs: Any) -> Any:
        timeline.append("payload_formatter_called")
        return {"leaked": "formatted_payload"}

    mock_payload_formatter = mock.Mock(side_effect=_mock_format)

    def _mock_log(*args: Any, **kwargs: Any) -> None:
        timeline.append("log_completed")

    mock_log_trading_event = mock.Mock(side_effect=_mock_log)

    mock_datetime = mock.Mock()
    mock_datetime.now = mock.Mock(return_value=fixed_now)

    with mock.patch.object(offers, "settings", synthetic_settings), \
         mock.patch.object(offers, "configured_product_snapshot_authority_path", mock_path_resolver), \
         mock.patch.object(offers, "_configured_product_snapshot_reader", mock_reader_factory), \
         mock.patch.object(offers, "observe_coin_inference_shadow", mock_observe), \
         mock.patch.object(offers, "_catalog_inference_shadow_payload", mock_payload_formatter), \
         mock.patch.object(offers, "log_trading_event", mock_log_trading_event), \
         mock.patch.object(offers, "datetime", mock_datetime):
        yield _SelectionHarness(
            synthetic_settings=synthetic_settings,
            snapshot_path=snapshot_path,
            reader_sentinel=reader_sentinel,
            fixed_now=fixed_now,
            valid_observation=valid_observation,
            mock_path_resolver=mock_path_resolver,
            mock_reader_factory=mock_reader_factory,
            mock_observe=mock_observe,
            mock_payload_formatter=mock_payload_formatter,
            mock_log_trading_event=mock_log_trading_event,
            mock_datetime=mock_datetime,
            timeline=timeline,
        )


class SelectionCommitFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes commit failure handling and rollback in the selection helper."""

    async def test_commit_failure_rollback_success(self) -> None:
        timeline: list[str] = []
        commit_error = SyntheticCommitError("simulated commit failure")
        db = StrictFakeAsyncSession(commit_exc=commit_error, timeline=timeline)
        price = 54_000_000
        valid_observation = _build_valid_observation(
            price=price,
            settlement_term="CASH",
        )

        formatted_check = offers._catalog_inference_shadow_payload(
            valid_observation.decision,
            decision_key=valid_observation.decision_key,
            edit_candidates=valid_observation.edit_candidates,
            mode="SELECTABLE",
        )
        self.assertEqual(formatted_check["mode"], "SELECTABLE")
        self.assertEqual(formatted_check["decision_key"], valid_observation.decision_key)
        self.assertEqual(len(formatted_check["candidates"]), 1)
        self.assertEqual(len(formatted_check["edit_candidates"]), 1)

        with _selection_test_context(
            auto_selection_enabled=False,
            valid_observation=valid_observation,
            timeline=timeline,
        ) as harness:
            result = await offers._selection_inference_for_missing_commodity(
                db,
                price=price,
                settlement_type="cash",
                low_date_hint=True,
                pack_hint=False,
            )

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
            self.assertNotEqual(result.get("decision_key"), valid_observation.decision_key)

            db.commit.assert_called_once_with()
            db.commit.assert_awaited_once_with()
            db.rollback.assert_called_once_with()
            db.rollback.assert_awaited_once_with()
            harness.mock_payload_formatter.assert_not_called()

            harness.mock_log_trading_event.assert_called_once_with(
                offers.logger,
                "coin_inference_parse_selection.unavailable",
                level="warning",
                action="coin_inference_parse_selection",
                result="abstained",
                error_class="SyntheticCommitError",
            )
            self.assertIs(harness.mock_log_trading_event.call_args[0][0], offers.logger)

            self.assertEqual(
                timeline,
                [
                    "observation_completed",
                    "commit_awaited",
                    "rollback_completed",
                    "log_completed",
                ],
            )

            harness.mock_path_resolver.assert_called_once_with(harness.synthetic_settings)
            self.assertIs(harness.mock_path_resolver.call_args[0][0], harness.synthetic_settings)
            harness.mock_reader_factory.assert_called_once_with()
            harness.mock_datetime.now.assert_called_once_with(timezone.utc)

            harness.mock_observe.assert_called_once_with(
                db,
                snapshot_path=harness.snapshot_path,
                submitted_project_price=price,
                settlement_term="CASH",
                source_surface="WEBAPP",
                now_utc=harness.fixed_now,
                candidate_scope=COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
                force_confirmation=True,
                snapshot_reader=harness.reader_sentinel,
            )
            harness.mock_observe.assert_awaited_once_with(
                db,
                snapshot_path=harness.snapshot_path,
                submitted_project_price=price,
                settlement_term="CASH",
                source_surface="WEBAPP",
                now_utc=harness.fixed_now,
                candidate_scope=COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
                force_confirmation=True,
                snapshot_reader=harness.reader_sentinel,
            )
            self.assertEqual(harness.mock_observe.call_count, 1)
            self.assertIs(harness.mock_observe.call_args[0][0], db)
            self.assertIs(harness.mock_observe.call_args[1]["snapshot_reader"], harness.reader_sentinel)
            self.assertEqual(
                harness.mock_observe.call_args[1]["candidate_scope"],
                "LOW_DATE_ONLY",
            )
            self.assertEqual(
                harness.mock_observe.call_args[1]["candidate_scope"],
                COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
            )
            self.assertIs(harness.mock_observe.call_args[1]["force_confirmation"], True)
            self.assertEqual(harness.mock_observe.call_args[1]["settlement_term"], "CASH")

    async def test_commit_failure_rollback_failure(self) -> None:
        timeline: list[str] = []
        commit_error = SyntheticCommitError("simulated commit failure")
        rollback_error = SyntheticRollbackError("simulated rollback failure")
        self.assertIsNot(commit_error, rollback_error)

        db = StrictFakeAsyncSession(
            commit_exc=commit_error,
            rollback_exc=rollback_error,
            timeline=timeline,
        )
        price = 62_500_000
        valid_observation = _build_valid_observation(
            price=price,
            settlement_term="TOMORROW",
        )

        formatted_check = offers._catalog_inference_shadow_payload(
            valid_observation.decision,
            decision_key=valid_observation.decision_key,
            edit_candidates=valid_observation.edit_candidates,
            mode="SELECTABLE",
        )
        self.assertEqual(formatted_check["mode"], "SELECTABLE")
        self.assertEqual(formatted_check["decision_key"], valid_observation.decision_key)
        self.assertEqual(len(formatted_check["candidates"]), 1)
        self.assertEqual(len(formatted_check["edit_candidates"]), 1)

        with _selection_test_context(
            auto_selection_enabled=True,
            valid_observation=valid_observation,
            timeline=timeline,
        ) as harness:
            with self.assertRaises(SyntheticRollbackError) as cm:
                await offers._selection_inference_for_missing_commodity(
                    db,
                    price=price,
                    settlement_type="tomorrow",
                    low_date_hint=True,
                    pack_hint=True,
                )

            self.assertIs(cm.exception, rollback_error)
            self.assertIsNot(cm.exception, commit_error)

            db.commit.assert_called_once_with()
            db.commit.assert_awaited_once_with()
            db.rollback.assert_called_once_with()
            db.rollback.assert_awaited_once_with()
            harness.mock_payload_formatter.assert_not_called()
            harness.mock_log_trading_event.assert_not_called()

            self.assertEqual(
                timeline,
                [
                    "observation_completed",
                    "commit_awaited",
                    "rollback_awaited",
                ],
            )

            harness.mock_path_resolver.assert_called_once_with(harness.synthetic_settings)
            self.assertIs(harness.mock_path_resolver.call_args[0][0], harness.synthetic_settings)
            harness.mock_reader_factory.assert_called_once_with()
            harness.mock_datetime.now.assert_called_once_with(timezone.utc)

            harness.mock_observe.assert_called_once_with(
                db,
                snapshot_path=harness.snapshot_path,
                submitted_project_price=price,
                settlement_term="TOMORROW",
                source_surface="WEBAPP",
                now_utc=harness.fixed_now,
                candidate_scope=COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
                force_confirmation=False,
                snapshot_reader=harness.reader_sentinel,
            )
            harness.mock_observe.assert_awaited_once_with(
                db,
                snapshot_path=harness.snapshot_path,
                submitted_project_price=price,
                settlement_term="TOMORROW",
                source_surface="WEBAPP",
                now_utc=harness.fixed_now,
                candidate_scope=COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
                force_confirmation=False,
                snapshot_reader=harness.reader_sentinel,
            )
            self.assertEqual(harness.mock_observe.call_count, 1)
            self.assertIs(harness.mock_observe.call_args[0][0], db)
            self.assertIs(harness.mock_observe.call_args[1]["snapshot_reader"], harness.reader_sentinel)
            self.assertEqual(
                harness.mock_observe.call_args[1]["candidate_scope"],
                "PACK_ONLY",
            )
            self.assertEqual(
                harness.mock_observe.call_args[1]["candidate_scope"],
                COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
            )
            self.assertIs(harness.mock_observe.call_args[1]["force_confirmation"], False)
            self.assertEqual(harness.mock_observe.call_args[1]["settlement_term"], "TOMORROW")


if __name__ == "__main__":
    unittest.main()
