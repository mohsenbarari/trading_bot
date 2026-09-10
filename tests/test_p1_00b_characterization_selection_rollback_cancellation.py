"""docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Characterization tests for api/routers/offers.py::_selection_inference_for_missing_commodity
rollback cancellation boundaries under Task Card P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
and Assignment WA-P1-00B-FREEZE-20260904-001.

Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184.
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md.
Assignment ID: WA-P1-00B-FREEZE-20260904-001.

Explicitly limits evidence to helper rollback-cancellation boundaries after ordinary observation
or commit failure; does not claim seed closure, inference internals or audit durability
verification, executed-test confirmation, or Stage acceptance.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from typing import Any
import unittest
from unittest.mock import call

# Requirement 5: Set APP_ENV_FILE before project imports
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

# Requirement 5: Install the established P1-00 network fence before importing offers or reusable harness
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.offers import _selection_inference_for_missing_commodity
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
from tests.test_p1_00b_characterization_selection_cancellation import (
    DummyReader,
    IsolatedSettings,
    SyntheticAsyncSession,
    patch_selection_boundaries,
)


class TestSelectionRollbackCancellationCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_observation_failure_rollback_cancellation(self) -> None:
        """Characterize cancellation escaping db.rollback after ordinary observation failure."""
        timeline: list[str] = []
        isolated_settings = IsolatedSettings(
            auto_selection_enabled=False,
            timeline=timeline,
        )
        snapshot_path = "/isolated/product/snapshot/path_cash.json"
        reader = DummyReader()
        fixed_now = datetime(2026, 9, 10, 10, 15, 0, tzinfo=timezone.utc)
        db = SyntheticAsyncSession()

        observation_error = Exception("ordinary observation failure")
        self.assertIs(type(observation_error), Exception)

        rollback_cancelled_error = asyncio.CancelledError(
            "rollback await cancelled by runtime after observation failure"
        )
        self.assertIs(type(rollback_cancelled_error), asyncio.CancelledError)

        async def fake_rollback(*args: Any, **kwargs: Any) -> None:
            timeline.append("rollback_await")
            raise rollback_cancelled_error

        db.rollback.side_effect = fake_rollback

        async def fake_observe(
            session: Any,
            *,
            snapshot_path: Any,
            submitted_project_price: Any,
            settlement_term: Any,
            source_surface: Any,
            now_utc: Any,
            candidate_scope: Any,
            force_confirmation: Any,
            snapshot_reader: Any,
        ) -> Any:
            timeline.append("observation_await")
            raise observation_error

        with patch_selection_boundaries(
            isolated_settings=isolated_settings,
            snapshot_path=snapshot_path,
            reader=reader,
            fixed_now=fixed_now,
            timeline=timeline,
            observe_coroutine_fn=fake_observe,
        ) as harness:
            caught: asyncio.CancelledError | None = None
            result: Any = None
            try:
                result = await _selection_inference_for_missing_commodity(
                    db,
                    price=54000000,
                    settlement_type="cash",
                    low_date_hint=True,
                    pack_hint=False,
                )
            except asyncio.CancelledError as exc:
                timeline.append("cancellation_catch")
                caught = exc

        # Assert no return or fallback
        self.assertIsNone(result)
        self.assertIsNotNone(caught)

        # Assert exact cancellation identity escapes with original exception as context
        self.assertIs(caught, rollback_cancelled_error)
        self.assertIs(caught.__context__, observation_error)
        self.assertIsNone(caught.__cause__)
        self.assertIs(caught.__suppress_context__, False)
        self.assertIsNot(caught, observation_error)
        self.assertIsNone(observation_error.__context__)
        self.assertIsNone(observation_error.__cause__)
        self.assertIs(observation_error.__suppress_context__, False)

        # Assert observer called and awaited once with exact parameters and identities
        observe_mock = harness["observe_mock"]
        observe_mock.assert_called_once_with(
            db,
            snapshot_path=snapshot_path,
            submitted_project_price=54000000,
            settlement_term="CASH",
            source_surface="WEBAPP",
            now_utc=fixed_now,
            candidate_scope="LOW_DATE_ONLY",
            force_confirmation=True,
            snapshot_reader=reader,
        )
        observe_mock.assert_awaited_once_with(
            db,
            snapshot_path=snapshot_path,
            submitted_project_price=54000000,
            settlement_term="CASH",
            source_surface="WEBAPP",
            now_utc=fixed_now,
            candidate_scope="LOW_DATE_ONLY",
            force_confirmation=True,
            snapshot_reader=reader,
        )
        self.assertEqual(observe_mock.call_count, 1)
        self.assertEqual(observe_mock.await_count, 1)

        expected_observer_keys = {
            "snapshot_path",
            "submitted_project_price",
            "settlement_term",
            "source_surface",
            "now_utc",
            "candidate_scope",
            "force_confirmation",
            "snapshot_reader",
        }
        expected_observer_kwargs = {
            "snapshot_path": snapshot_path,
            "submitted_project_price": 54000000,
            "settlement_term": "CASH",
            "source_surface": "WEBAPP",
            "now_utc": fixed_now,
            "candidate_scope": "LOW_DATE_ONLY",
            "force_confirmation": True,
            "snapshot_reader": reader,
        }
        obs_args, obs_kwargs = observe_mock.call_args
        self.assertEqual(len(obs_args), 1)
        self.assertIs(obs_args[0], db)
        self.assertEqual(set(obs_kwargs.keys()), expected_observer_keys)
        self.assertEqual(obs_kwargs, expected_observer_kwargs)
        self.assertIs(obs_kwargs["snapshot_path"], snapshot_path)
        self.assertIs(obs_kwargs["now_utc"], fixed_now)
        self.assertIs(obs_kwargs["snapshot_reader"], reader)
        self.assertIs(obs_kwargs["force_confirmation"], True)
        self.assertEqual(obs_kwargs["submitted_project_price"], 54000000)
        self.assertEqual(obs_kwargs["settlement_term"], "CASH")
        self.assertEqual(obs_kwargs["source_surface"], "WEBAPP")
        self.assertEqual(
            obs_kwargs["candidate_scope"],
            COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
        )
        self.assertEqual(obs_kwargs["candidate_scope"], "LOW_DATE_ONLY")

        # Rollback called and awaited once with no args; commit never called or awaited
        db.rollback.assert_called_once_with()
        db.rollback.assert_awaited_once_with()
        self.assertEqual(db.rollback.call_args, call())
        self.assertEqual(db.rollback.await_args, call())
        self.assertEqual(db.rollback.call_count, 1)
        self.assertEqual(db.rollback.await_count, 1)

        db.commit.assert_not_called()
        db.commit.assert_not_awaited()
        self.assertEqual(db.commit.call_count, 0)
        self.assertEqual(db.commit.await_count, 0)

        # Formatter and unavailable logger not called
        harness["formatter_mock"].assert_not_called()
        harness["log_event_mock"].assert_not_called()
        self.assertEqual(harness["logger_mock"].mock_calls, [])

        # Boundary assertions
        harness["resolver_mock"].assert_called_once_with(isolated_settings)
        self.assertIs(harness["resolver_mock"].call_args[0][0], isolated_settings)
        self.assertEqual(harness["resolver_mock"].call_args[1], {})
        harness["datetime_mock"].now.assert_called_once_with(timezone.utc)
        self.assertIs(harness["datetime_mock"].now.call_args[0][0], timezone.utc)
        self.assertEqual(harness["datetime_mock"].now.call_args[1], {})
        self.assertEqual(isolated_settings.auto_selection_access_count, 1)
        harness["reader_factory_mock"].assert_called_once_with()
        self.assertEqual(harness["reader_factory_mock"].call_args[0], ())
        self.assertEqual(harness["reader_factory_mock"].call_args[1], {})
        self.assertEqual(
            len({id(snapshot_path), id(reader), id(fixed_now), id(db)}),
            4,
        )

        # Assert independently specified causal sequence
        expected_timeline = [
            "path_resolution",
            "clock",
            "setting_access",
            "reader_construction",
            "observation_await",
            "rollback_await",
            "cancellation_catch",
        ]
        self.assertEqual(timeline, expected_timeline)

    async def test_commit_failure_rollback_cancellation(self) -> None:
        """Characterize cancellation escaping db.rollback after ordinary commit failure."""
        timeline: list[str] = []
        isolated_settings = IsolatedSettings(
            auto_selection_enabled=True,
            timeline=timeline,
        )
        snapshot_path = "/isolated/product/snapshot/path_tomorrow.json"
        reader = DummyReader()
        fixed_now = datetime(2026, 9, 10, 10, 20, 0, tzinfo=timezone.utc)
        db = SyntheticAsyncSession()

        commit_error = Exception("ordinary commit failure")
        self.assertIs(type(commit_error), Exception)

        rollback_cancelled_error = asyncio.CancelledError(
            "rollback await cancelled by runtime after commit failure"
        )
        self.assertIs(type(rollback_cancelled_error), asyncio.CancelledError)

        async def fake_commit(*args: Any, **kwargs: Any) -> None:
            timeline.append("commit_await")
            raise commit_error

        db.commit.side_effect = fake_commit

        async def fake_rollback(*args: Any, **kwargs: Any) -> None:
            timeline.append("rollback_await")
            raise rollback_cancelled_error

        db.rollback.side_effect = fake_rollback

        candidate = CatalogCoinCommodityCandidate(
            commodity_id=201,
            commodity_code="EMAMI_86",
            commodity_name="سکه تمام بهار آزادی طرح جدید",
            center_project_price=62500000,
            lower_project_price=62000000,
            upper_project_price=63000000,
            confidence="HIGH",
            distance_to_center_relative=0.0,
        )
        decision = CatalogCoinCommodityInference(
            status="ACCEPTED",
            settlement_term="TOMORROW",
            candidates=(candidate,),
            snapshot_generated_at_utc="2026-09-10T10:20:00Z",
            snapshot_receipt="rcpt-commit-cancellation-test",
            reason=None,
        )
        edit_candidate = CatalogCoinCommodityEditCandidate(
            commodity_id=202,
            commodity_code="BAHAR_OLD",
            commodity_name="سکه تمام بهار آزادی طرح قدیم",
        )
        valid_observation = CoinInferenceShadowObservation(
            decision_key="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            decision=decision,
            edit_candidates=(edit_candidate,),
        )

        async def fake_observe(
            session: Any,
            *,
            snapshot_path: Any,
            submitted_project_price: Any,
            settlement_term: Any,
            source_surface: Any,
            now_utc: Any,
            candidate_scope: Any,
            force_confirmation: Any,
            snapshot_reader: Any,
        ) -> Any:
            timeline.append("observation_await")
            timeline.append("observation_complete")
            return valid_observation

        with patch_selection_boundaries(
            isolated_settings=isolated_settings,
            snapshot_path=snapshot_path,
            reader=reader,
            fixed_now=fixed_now,
            timeline=timeline,
            observe_coroutine_fn=fake_observe,
        ) as harness:
            caught: asyncio.CancelledError | None = None
            result: Any = None
            try:
                result = await _selection_inference_for_missing_commodity(
                    db,
                    price=62500000,
                    settlement_type="tomorrow",
                    low_date_hint=True,
                    pack_hint=True,
                )
            except asyncio.CancelledError as exc:
                timeline.append("cancellation_catch")
                caught = exc

        # Assert no return or fallback
        self.assertIsNone(result)
        self.assertIsNotNone(caught)

        # Assert exact cancellation identity escapes with commit exception as context
        self.assertIs(caught, rollback_cancelled_error)
        self.assertIs(caught.__context__, commit_error)
        self.assertIsNone(caught.__cause__)
        self.assertIs(caught.__suppress_context__, False)
        self.assertIsNot(caught, commit_error)
        self.assertIsNone(commit_error.__context__)
        self.assertIsNone(commit_error.__cause__)
        self.assertIs(commit_error.__suppress_context__, False)

        # Assert exact observer calls/awaits and identities with TOMORROW, PACK_ONLY precedence, force_confirmation False
        observe_mock = harness["observe_mock"]
        observe_mock.assert_called_once_with(
            db,
            snapshot_path=snapshot_path,
            submitted_project_price=62500000,
            settlement_term="TOMORROW",
            source_surface="WEBAPP",
            now_utc=fixed_now,
            candidate_scope="PACK_ONLY",
            force_confirmation=False,
            snapshot_reader=reader,
        )
        observe_mock.assert_awaited_once_with(
            db,
            snapshot_path=snapshot_path,
            submitted_project_price=62500000,
            settlement_term="TOMORROW",
            source_surface="WEBAPP",
            now_utc=fixed_now,
            candidate_scope="PACK_ONLY",
            force_confirmation=False,
            snapshot_reader=reader,
        )
        self.assertEqual(observe_mock.call_count, 1)
        self.assertEqual(observe_mock.await_count, 1)

        expected_observer_keys = {
            "snapshot_path",
            "submitted_project_price",
            "settlement_term",
            "source_surface",
            "now_utc",
            "candidate_scope",
            "force_confirmation",
            "snapshot_reader",
        }
        expected_observer_kwargs = {
            "snapshot_path": snapshot_path,
            "submitted_project_price": 62500000,
            "settlement_term": "TOMORROW",
            "source_surface": "WEBAPP",
            "now_utc": fixed_now,
            "candidate_scope": "PACK_ONLY",
            "force_confirmation": False,
            "snapshot_reader": reader,
        }
        obs_args, obs_kwargs = observe_mock.call_args
        self.assertEqual(len(obs_args), 1)
        self.assertIs(obs_args[0], db)
        self.assertEqual(set(obs_kwargs.keys()), expected_observer_keys)
        self.assertEqual(obs_kwargs, expected_observer_kwargs)
        self.assertIs(obs_kwargs["snapshot_path"], snapshot_path)
        self.assertIs(obs_kwargs["now_utc"], fixed_now)
        self.assertIs(obs_kwargs["snapshot_reader"], reader)
        self.assertIs(obs_kwargs["force_confirmation"], False)
        self.assertEqual(obs_kwargs["submitted_project_price"], 62500000)
        self.assertEqual(obs_kwargs["settlement_term"], "TOMORROW")
        self.assertEqual(obs_kwargs["source_surface"], "WEBAPP")
        self.assertEqual(
            obs_kwargs["candidate_scope"],
            COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
        )
        self.assertEqual(obs_kwargs["candidate_scope"], "PACK_ONLY")

        # Commit called and awaited exactly once before rollback; rollback called and awaited once
        db.commit.assert_called_once_with()
        db.commit.assert_awaited_once_with()
        self.assertEqual(db.commit.call_args, call())
        self.assertEqual(db.commit.await_args, call())
        self.assertEqual(db.commit.call_count, 1)
        self.assertEqual(db.commit.await_count, 1)

        db.rollback.assert_called_once_with()
        db.rollback.assert_awaited_once_with()
        self.assertEqual(db.rollback.call_args, call())
        self.assertEqual(db.rollback.await_args, call())
        self.assertEqual(db.rollback.call_count, 1)
        self.assertEqual(db.rollback.await_count, 1)

        # Formatter and unavailable logger never called
        harness["formatter_mock"].assert_not_called()
        harness["log_event_mock"].assert_not_called()
        self.assertEqual(harness["logger_mock"].mock_calls, [])

        # Boundary assertions
        harness["resolver_mock"].assert_called_once_with(isolated_settings)
        self.assertIs(harness["resolver_mock"].call_args[0][0], isolated_settings)
        self.assertEqual(harness["resolver_mock"].call_args[1], {})
        harness["datetime_mock"].now.assert_called_once_with(timezone.utc)
        self.assertIs(harness["datetime_mock"].now.call_args[0][0], timezone.utc)
        self.assertEqual(harness["datetime_mock"].now.call_args[1], {})
        self.assertEqual(isolated_settings.auto_selection_access_count, 1)
        harness["reader_factory_mock"].assert_called_once_with()
        self.assertEqual(harness["reader_factory_mock"].call_args[0], ())
        self.assertEqual(harness["reader_factory_mock"].call_args[1], {})
        self.assertEqual(
            len({id(snapshot_path), id(reader), id(fixed_now), id(db)}),
            4,
        )

        # Assert independently expected ordering through observation completion, commit await, rollback await and cancellation catch
        expected_timeline = [
            "path_resolution",
            "clock",
            "setting_access",
            "reader_construction",
            "observation_await",
            "observation_complete",
            "commit_await",
            "rollback_await",
            "cancellation_catch",
        ]
        self.assertEqual(timeline, expected_timeline)


if __name__ == "__main__":
    unittest.main()
