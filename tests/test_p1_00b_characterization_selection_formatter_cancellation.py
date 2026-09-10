"""Characterization tests for _selection_inference_for_missing_commodity formatter cancellation.

Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

These tests provide helper-level formatter cancellation characterization only;
they do not establish handler closure, seed closure, inference internals,
audit durability, Stage acceptance, or unexecuted test claims.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import asyncio
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
from tests.test_p1_00b_characterization_selection_payload_failures import (
    StrictFakeAsyncSession,
    SyntheticPayloadFormatError,
    _assert_real_formatter_precheck,
    _build_valid_observation,
)


@contextmanager
def _selection_formatter_cancellation_context(
    *,
    auto_selection_enabled: bool,
    observation: CoinInferenceShadowObservation,
    formatter_error: BaseException,
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
        raise formatter_error

    mock_payload_formatter = mock.Mock(side_effect=_mock_format)

    def _mock_log(*args: Any, **kwargs: Any) -> None:
        timeline.append("log_completed")

    mock_log_trading_event = mock.Mock(side_effect=_mock_log)
    mock_logger = mock.Mock()

    mock_datetime = mock.Mock()
    mock_datetime.now = mock.Mock(return_value=fixed_now)

    with mock.patch.object(offers, "settings", synthetic_settings), \
         mock.patch.object(offers, "configured_product_snapshot_authority_path", mock_path_resolver), \
         mock.patch.object(offers, "_configured_product_snapshot_reader", mock_reader_factory), \
         mock.patch.object(offers, "observe_coin_inference_shadow", mock_observe), \
         mock.patch.object(offers, "_catalog_inference_shadow_payload", mock_payload_formatter), \
         mock.patch.object(offers, "log_trading_event", mock_log_trading_event), \
         mock.patch.object(offers, "logger", mock_logger), \
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
            mock_logger=mock_logger,
            mock_datetime=mock_datetime,
        )


def _assert_formatter_cancellation_boundary_interactions(
    test_case: unittest.TestCase,
    ctx: SimpleNamespace,
    *,
    db: StrictFakeAsyncSession,
    observation: CoinInferenceShadowObservation,
    price: int,
    settlement_term: str,
    candidate_scope: str,
    force_confirmation: bool,
    expect_rollback: bool,
) -> None:
    db.commit.assert_called_once_with()
    db.commit.assert_awaited_once_with()

    if expect_rollback:
        db.rollback.assert_called_once_with()
        db.rollback.assert_awaited_once_with()
    else:
        db.rollback.assert_not_called()
        test_case.assertEqual(db.rollback.await_count, 0)
        if hasattr(db.rollback, "assert_not_awaited"):
            db.rollback.assert_not_awaited()

    ctx.mock_path_resolver.assert_called_once_with(ctx.settings)
    test_case.assertIs(ctx.mock_path_resolver.call_args[0][0], ctx.settings)

    ctx.mock_reader_factory.assert_called_once_with()

    ctx.mock_datetime.now.assert_called_once_with(timezone.utc)
    test_case.assertIs(ctx.mock_datetime.now.call_args[0][0], timezone.utc)
    test_case.assertIsNotNone(ctx.fixed_now.tzinfo)

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
    test_case.assertEqual(ctx.mock_observe.call_args[1]["snapshot_path"], ctx.snapshot_path)
    test_case.assertEqual(ctx.mock_observe.call_args[1]["submitted_project_price"], price)
    test_case.assertEqual(ctx.mock_observe.call_args[1]["settlement_term"], settlement_term)
    test_case.assertEqual(ctx.mock_observe.call_args[1]["source_surface"], "WEBAPP")
    test_case.assertIs(ctx.mock_observe.call_args[1]["now_utc"], ctx.fixed_now)
    test_case.assertEqual(ctx.mock_observe.call_args[1]["candidate_scope"], candidate_scope)
    test_case.assertIs(ctx.mock_observe.call_args[1]["force_confirmation"], force_confirmation)
    test_case.assertIs(ctx.mock_observe.call_args[1]["snapshot_reader"], ctx.reader_sentinel)

    ctx.mock_payload_formatter.assert_called_once_with(
        observation.decision,
        decision_key=observation.decision_key,
        edit_candidates=observation.edit_candidates,
        mode="SELECTABLE",
    )
    test_case.assertIs(ctx.mock_payload_formatter.call_args[0][0], observation.decision)
    test_case.assertEqual(ctx.mock_payload_formatter.call_args[1]["decision_key"], observation.decision_key)
    test_case.assertIs(ctx.mock_payload_formatter.call_args[1]["edit_candidates"], observation.edit_candidates)
    test_case.assertEqual(ctx.mock_payload_formatter.call_args[1]["mode"], "SELECTABLE")


class SelectionFormatterCancellationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes formatter cancellation and rollback cancellation in the selection helper."""

    async def test_formatter_direct_cancellation_escapes_without_rollback(self) -> None:
        """Formatter asyncio.CancelledError escapes without rollback or logging."""
        timeline: list[str] = []
        cancellation = asyncio.CancelledError("simulated formatter cancellation")
        self.assertIsNone(cancellation.__context__)
        self.assertIsNone(cancellation.__cause__)
        self.assertFalse(cancellation.__suppress_context__)

        db = StrictFakeAsyncSession(timeline=timeline)
        price = 54_000_000
        observation = _build_valid_observation(price=price, settlement_term="CASH")
        _assert_real_formatter_precheck(self, observation)

        with _selection_formatter_cancellation_context(
            auto_selection_enabled=False,
            observation=observation,
            formatter_error=cancellation,
            timeline=timeline,
        ) as ctx:
            with self.assertRaises(asyncio.CancelledError) as cm:
                await offers._selection_inference_for_missing_commodity(
                    db,
                    price=price,
                    settlement_type="cash",
                    low_date_hint=True,
                    pack_hint=False,
                )

            self.assertIs(cm.exception, cancellation)
            self.assertIsNone(cm.exception.__context__)
            self.assertIsNone(cm.exception.__cause__)
            self.assertFalse(cm.exception.__suppress_context__)

            ctx.mock_log_trading_event.assert_not_called()
            ctx.mock_logger.assert_not_called()
            self.assertEqual(ctx.mock_logger.mock_calls, [])

            self.assertEqual(
                timeline,
                [
                    "observation_completed",
                    "commit_completed",
                    "formatter_attempt",
                ],
            )

            _assert_formatter_cancellation_boundary_interactions(
                self,
                ctx,
                db=db,
                observation=observation,
                price=price,
                settlement_term="CASH",
                candidate_scope=COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
                force_confirmation=True,
                expect_rollback=False,
            )

    async def test_formatter_failure_rollback_cancellation_escapes_with_context(self) -> None:
        """Rollback asyncio.CancelledError chains formatter error in __context__ without logging."""
        timeline: list[str] = []
        formatter_error = SyntheticPayloadFormatError("simulated payload formatting failure")
        rollback_cancellation = asyncio.CancelledError("simulated rollback cancellation")
        self.assertIsNot(formatter_error, rollback_cancellation)
        self.assertIsNone(formatter_error.__context__)
        self.assertIsNone(formatter_error.__cause__)
        self.assertIsNone(rollback_cancellation.__context__)
        self.assertIsNone(rollback_cancellation.__cause__)

        db = StrictFakeAsyncSession(rollback_exc=rollback_cancellation, timeline=timeline)
        price = 62_500_000
        observation = _build_valid_observation(price=price, settlement_term="TOMORROW")
        _assert_real_formatter_precheck(self, observation)

        with _selection_formatter_cancellation_context(
            auto_selection_enabled=True,
            observation=observation,
            formatter_error=formatter_error,
            timeline=timeline,
        ) as ctx:
            with self.assertRaises(asyncio.CancelledError) as cm:
                await offers._selection_inference_for_missing_commodity(
                    db,
                    price=price,
                    settlement_type="tomorrow",
                    low_date_hint=True,
                    pack_hint=True,
                )

            self.assertIs(cm.exception, rollback_cancellation)
            self.assertIsNot(cm.exception, formatter_error)
            self.assertIs(cm.exception.__context__, formatter_error)
            self.assertIsNone(cm.exception.__cause__)
            self.assertFalse(cm.exception.__suppress_context__)

            self.assertIsNone(formatter_error.__context__)
            self.assertIsNone(formatter_error.__cause__)

            ctx.mock_log_trading_event.assert_not_called()
            ctx.mock_logger.assert_not_called()
            self.assertEqual(ctx.mock_logger.mock_calls, [])

            self.assertEqual(
                timeline,
                [
                    "observation_completed",
                    "commit_completed",
                    "formatter_attempt",
                    "rollback_attempt",
                ],
            )

            _assert_formatter_cancellation_boundary_interactions(
                self,
                ctx,
                db=db,
                observation=observation,
                price=price,
                settlement_term="TOMORROW",
                candidate_scope=COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
                force_confirmation=False,
                expect_rollback=True,
            )


if __name__ == "__main__":
    unittest.main()
