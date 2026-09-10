"""Characterization tests for _selection_inference_for_missing_commodity payload format errors.

Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

These tests provide helper-level payload-error and rollback characterization only;
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
from types import SimpleNamespace
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


class SyntheticPayloadFormatError(RuntimeError):
    """Distinct synthetic exception raised by payload formatter mock."""


class SyntheticRollbackError(RuntimeError):
    """Distinct synthetic exception raised by DB rollback mock."""


class StrictFakeAsyncSession:
    """Strict fake AsyncSession rejecting unexpected DB operations."""

    def __init__(
        self,
        *,
        rollback_exc: BaseException | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self._rollback_exc = rollback_exc
        self._timeline = timeline
        self.commit = mock.AsyncMock(side_effect=self._on_commit)
        self.rollback = mock.AsyncMock(side_effect=self._on_rollback)

    async def _on_commit(self) -> None:
        if self._timeline is not None:
            self._timeline.append("commit_completed")

    async def _on_rollback(self) -> None:
        if self._rollback_exc is not None:
            if self._timeline is not None:
                self._timeline.append("rollback_attempt")
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
        snapshot_receipt="rcpt-valid-receipt-payload-test",
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


def _assert_real_formatter_precheck(
    test_case: unittest.TestCase,
    observation: CoinInferenceShadowObservation,
) -> None:
    """Verify the observation formats cleanly with the unpatched formatter before injecting error."""
    formatted = offers._catalog_inference_shadow_payload(
        observation.decision,
        decision_key=observation.decision_key,
        edit_candidates=observation.edit_candidates,
        mode="SELECTABLE",
    )
    test_case.assertEqual(formatted["mode"], "SELECTABLE")
    test_case.assertEqual(formatted["status"], "DECIDED")
    test_case.assertEqual(formatted["decision_key"], observation.decision_key)
    test_case.assertEqual(
        formatted["snapshot_generated_at_utc"],
        "2026-09-10T06:00:00Z",
    )
    test_case.assertEqual(
        formatted["snapshot_receipt"],
        "rcpt-valid-receipt-payload-test",
    )
    test_case.assertIsNone(formatted["reason"])
    test_case.assertEqual(len(formatted["candidates"]), 1)
    test_case.assertEqual(len(formatted["edit_candidates"]), 1)


@contextmanager
def _selection_test_context(
    *,
    auto_selection_enabled: bool,
    observation: CoinInferenceShadowObservation,
    payload_error: BaseException,
    timeline: list[str],
    snapshot_path: str = "/synthetic/snapshots/authority.json",
) -> Iterator[SimpleNamespace]:
    synthetic_settings = SimpleNamespace(
        coin_intelligence_inference_auto_selection_enabled=auto_selection_enabled
    )
    reader_sentinel = object()
    fixed_now = datetime(2026, 9, 10, 6, 0, 0, tzinfo=timezone.utc)

    mock_path_resolver = mock.Mock(return_value=snapshot_path)
    mock_reader_factory = mock.Mock(return_value=reader_sentinel)

    async def _mock_observe(*args: Any, **kwargs: Any) -> Any:
        timeline.append("observation_completed")
        return observation

    mock_observe = mock.AsyncMock(side_effect=_mock_observe)

    def _mock_format(*args: Any, **kwargs: Any) -> Any:
        timeline.append("formatter_attempt")
        raise payload_error

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
        yield SimpleNamespace(
            settings=synthetic_settings,
            snapshot_path=snapshot_path,
            reader_sentinel=reader_sentinel,
            fixed_now=fixed_now,
            mock_path_resolver=mock_path_resolver,
            mock_reader_factory=mock_reader_factory,
            mock_observe=mock_observe,
            mock_payload_formatter=mock_payload_formatter,
            mock_log_trading_event=mock_log_trading_event,
            mock_datetime=mock_datetime,
        )


def _assert_boundary_interactions(
    test_case: unittest.TestCase,
    ctx: SimpleNamespace,
    *,
    db: StrictFakeAsyncSession,
    observation: CoinInferenceShadowObservation,
    price: int,
    settlement_term: str,
    candidate_scope: str,
    force_confirmation: bool,
) -> None:
    """Assert shared boundary invocations and parameter bindings across both scenarios."""
    db.commit.assert_called_once_with()
    db.commit.assert_awaited_once_with()
    db.rollback.assert_called_once_with()
    db.rollback.assert_awaited_once_with()

    ctx.mock_path_resolver.assert_called_once_with(ctx.settings)
    test_case.assertIs(ctx.mock_path_resolver.call_args[0][0], ctx.settings)
    ctx.mock_reader_factory.assert_called_once_with()
    ctx.mock_datetime.now.assert_called_once_with(timezone.utc)

    ctx.mock_observe.assert_called_once_with(
        db,
        snapshot_path=ctx.snapshot_path,
        submitted_project_price=price,
        settlement_term=settlement_term,
        source_surface="WEBAPP",
        now_utc=ctx.fixed_now,
        candidate_scope=candidate_scope,
        force_confirmation=force_confirmation,
        snapshot_reader=ctx.reader_sentinel,
    )
    ctx.mock_observe.assert_awaited_once_with(
        db,
        snapshot_path=ctx.snapshot_path,
        submitted_project_price=price,
        settlement_term=settlement_term,
        source_surface="WEBAPP",
        now_utc=ctx.fixed_now,
        candidate_scope=candidate_scope,
        force_confirmation=force_confirmation,
        snapshot_reader=ctx.reader_sentinel,
    )
    test_case.assertIs(ctx.mock_observe.call_args[0][0], db)
    test_case.assertIs(ctx.mock_observe.call_args[1]["snapshot_reader"], ctx.reader_sentinel)

    ctx.mock_payload_formatter.assert_called_once_with(
        observation.decision,
        decision_key=observation.decision_key,
        edit_candidates=observation.edit_candidates,
        mode="SELECTABLE",
    )
    test_case.assertIs(ctx.mock_payload_formatter.call_args[0][0], observation.decision)
    test_case.assertIs(ctx.mock_payload_formatter.call_args[1]["edit_candidates"], observation.edit_candidates)


class SelectionPayloadFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes payload formatting failure handling and rollback in the selection helper."""

    async def test_payload_format_failure_rollback_success(self) -> None:
        timeline: list[str] = []
        formatter_error = SyntheticPayloadFormatError("simulated payload formatting failure")
        db = StrictFakeAsyncSession(timeline=timeline)
        price = 54_000_000
        observation = _build_valid_observation(price=price, settlement_term="CASH")
        _assert_real_formatter_precheck(self, observation)

        with _selection_test_context(
            auto_selection_enabled=False,
            observation=observation,
            payload_error=formatter_error,
            timeline=timeline,
        ) as ctx:
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

            ctx.mock_log_trading_event.assert_called_once_with(
                offers.logger,
                "coin_inference_parse_selection.unavailable",
                level="warning",
                action="coin_inference_parse_selection",
                result="abstained",
                error_class="SyntheticPayloadFormatError",
            )
            self.assertIs(ctx.mock_log_trading_event.call_args[0][0], offers.logger)

            self.assertEqual(
                timeline,
                [
                    "observation_completed",
                    "commit_completed",
                    "formatter_attempt",
                    "rollback_completed",
                    "log_completed",
                ],
            )

            _assert_boundary_interactions(
                self,
                ctx,
                db=db,
                observation=observation,
                price=price,
                settlement_term="CASH",
                candidate_scope=COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
                force_confirmation=True,
            )

    async def test_payload_format_failure_rollback_failure(self) -> None:
        timeline: list[str] = []
        formatter_error = SyntheticPayloadFormatError("simulated payload formatting failure")
        rollback_error = SyntheticRollbackError("simulated rollback failure")
        self.assertIsNot(formatter_error, rollback_error)

        db = StrictFakeAsyncSession(rollback_exc=rollback_error, timeline=timeline)
        price = 62_500_000
        observation = _build_valid_observation(price=price, settlement_term="TOMORROW")
        _assert_real_formatter_precheck(self, observation)

        with _selection_test_context(
            auto_selection_enabled=True,
            observation=observation,
            payload_error=formatter_error,
            timeline=timeline,
        ) as ctx:
            with self.assertRaises(SyntheticRollbackError) as cm:
                await offers._selection_inference_for_missing_commodity(
                    db,
                    price=price,
                    settlement_type="tomorrow",
                    low_date_hint=True,
                    pack_hint=True,
                )

            self.assertIs(cm.exception, rollback_error)
            self.assertIsNot(cm.exception, formatter_error)

            ctx.mock_log_trading_event.assert_not_called()

            self.assertEqual(
                timeline,
                [
                    "observation_completed",
                    "commit_completed",
                    "formatter_attempt",
                    "rollback_attempt",
                ],
            )

            _assert_boundary_interactions(
                self,
                ctx,
                db=db,
                observation=observation,
                price=price,
                settlement_term="TOMORROW",
                candidate_scope=COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
                force_confirmation=False,
            )


if __name__ == "__main__":
    unittest.main()
