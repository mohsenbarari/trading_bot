from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import online_recalibration

from online_recalibration import (
    COMPARISON_EVALUATION_ROLE,
    MAIN_COMPARISON_MODEL_ID,
    MAX_RESIDUAL_RATIO,
    PENDING_EXPIRY_HOURS,
    SHADOW_LEDGER_MIN_INTERVAL_SECONDS,
    UNMATCHED_EVALUATION_MODE,
    apply_calibration,
    apply_recent_realized_calibration,
    ensure_schema,
    expire_unmatched_predictions,
    ledger_storage_report,
    maintain_prediction_ledger,
    prune_prediction_ledger,
    reconcile_predictions,
    record_predictions,
    summarize_model_outcomes,
)


UTC = timezone.utc


class OnlineRecalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE confirmed_trades (
                id INTEGER PRIMARY KEY,
                event_time_utc TEXT NOT NULL,
                commodity TEXT NOT NULL,
                price INTEGER NOT NULL,
                quantity INTEGER,
                settlement TEXT NOT NULL,
                trade_form TEXT NOT NULL,
                confidence REAL NOT NULL,
                training_eligible INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        ensure_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def _record(self, at: str, price: int, *, enabled: bool = False) -> None:
        record_predictions(
            self.connection,
            prediction_time=datetime.fromisoformat(at.replace("Z", "+00:00")),
            settlement="CASH",
            rates=[
                {
                    "commodity_name": "امام",
                    "estimated_price_toman": price,
                    "tolerance": {
                        "lower_price_toman": price - 1_000_000,
                        "upper_price_toman": price + 1_000_000,
                    },
                }
            ],
            group_live_enabled=enabled,
        )
        self.connection.commit()

    def _record_comparison(
        self,
        at: str,
        price: int,
        *,
        model_id: str = MAIN_COMPARISON_MODEL_ID,
        model_version: str = "test",
    ) -> None:
        prediction_time = datetime.fromisoformat(at.replace("Z", "+00:00"))
        record_predictions(
            self.connection,
            prediction_time=prediction_time,
            settlement="CASH",
            rates=[
                {
                    "commodity_name": "امام",
                    "estimated_price_toman": price,
                    "tolerance": {
                        "lower_price_toman": price - 1_000_000,
                        "upper_price_toman": price + 1_000_000,
                    },
                }
            ],
            group_live_enabled=True,
            model_id=model_id,
            model_version=model_version,
            evaluation_role=COMPARISON_EVALUATION_ROLE,
            comparison_cohort=prediction_time,
        )
        self.connection.commit()

    def test_reconnect_evaluates_one_pending_prediction_and_updates_state(self) -> None:
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=False)
        self._record("2026-08-05T10:01:00Z", 181_000_000, enabled=False)
        self._record("2026-08-05T10:02:00Z", 182_000_000, enabled=False)
        self.connection.execute(
            """
            INSERT INTO confirmed_trades(
                id,event_time_utc,commodity,price,quantity,settlement,
                trade_form,confidence,training_eligible
            ) VALUES (1,'2026-08-05T12:00:30Z','امام',185000,5,'CASH','PHYSICAL',0.99,1)
            """
        )
        self.connection.commit()

        result = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 12, 1, tzinfo=UTC),
            live_group_enabled=True,
            reconnect_at=datetime(2026, 8, 5, 11, 59, tzinfo=UTC),
        )
        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(result["reconnect_bridged"], 1)
        row = self.connection.execute(
            "SELECT evaluation_mode, actual_price_toman FROM coin_estimate_predictions WHERE evaluated_at_utc IS NOT NULL"
        ).fetchone()
        self.assertEqual(row["evaluation_mode"], "RECONNECT_BRIDGE")
        self.assertEqual(row["actual_price_toman"], 185000000.0)
        state = self.connection.execute(
            "SELECT sample_count, residual_mean FROM coin_online_residual_state"
        ).fetchone()
        self.assertEqual(state["sample_count"], 1)
        self.assertGreater(state["residual_mean"], 0)

    def test_reconciliation_reads_from_a_separate_observation_store(self) -> None:
        calibration = sqlite3.connect(":memory:")
        calibration.row_factory = sqlite3.Row
        observations = sqlite3.connect(":memory:")
        observations.row_factory = sqlite3.Row
        try:
            ensure_schema(calibration)
            record_predictions(
                calibration,
                prediction_time=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
                settlement="CASH",
                rates=[
                    {
                        "commodity_name": "امام",
                        "estimated_price_toman": 180_000_000,
                    }
                ],
                group_live_enabled=True,
            )
            observations.executescript(
                """
                CREATE TABLE confirmed_trades (
                    id INTEGER PRIMARY KEY,
                    event_time_utc TEXT NOT NULL,
                    commodity TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    quantity INTEGER,
                    settlement TEXT NOT NULL,
                    trade_form TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    training_eligible INTEGER NOT NULL DEFAULT 1
                );
                INSERT INTO confirmed_trades(
                    event_time_utc,commodity,price,settlement,trade_form,confidence
                ) VALUES ('2026-08-05T10:01:00Z','امام',181000,'CASH','PHYSICAL',0.99);
                """
            )
            result = reconcile_predictions(
                calibration,
                now=datetime(2026, 8, 5, 10, 2, tzinfo=UTC),
                live_group_enabled=True,
                observation_connection=observations,
            )
            self.assertEqual(result["evaluated"], 1)
            self.assertEqual(
                calibration.execute(
                    "SELECT actual_price_toman FROM coin_estimate_predictions"
                ).fetchone()[0],
                181_000_000,
            )
            self.assertEqual(
                observations.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='coin_estimate_predictions'"
                ).fetchone()[0],
                0,
            )
        finally:
            observations.close()
            calibration.close()

    def test_old_normal_prediction_is_not_requeried_but_is_retained(self) -> None:
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=True)
        result = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 20, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(result["evaluated"], 0)
        row = self.connection.execute(
            "SELECT evaluated_at_utc FROM coin_estimate_predictions"
        ).fetchone()
        # A performance guard must not destroy historical training/audit data.
        self.assertIsNone(row["evaluated_at_utc"])

    def test_same_trade_evaluates_each_model_but_only_main_learns_residual(self) -> None:
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=True)
        record_predictions(
            self.connection,
            prediction_time=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
            settlement="CASH",
            rates=[
                {
                    "commodity_name": "امام",
                    "estimated_price_toman": 179_000_000,
                }
            ],
            group_live_enabled=True,
            model_id="SHADOW1_PREVIOUS",
            model_version="test",
        )
        self.connection.execute(
            """
            INSERT INTO confirmed_trades(
                id,event_time_utc,commodity,price,quantity,settlement,
                trade_form,confidence,training_eligible
            ) VALUES (1,'2026-08-05T10:00:30Z','امام',181000,5,'CASH','PHYSICAL',0.99,1)
            """
        )
        self.connection.commit()
        result = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(result["evaluated"], 2)
        rows = self.connection.execute(
            "SELECT model_id, actual_price_toman FROM coin_estimate_predictions ORDER BY model_id"
        ).fetchall()
        self.assertEqual([row["model_id"] for row in rows], ["MAIN_ONLINE", "SHADOW1_PREVIOUS"])
        self.assertEqual([row["actual_price_toman"] for row in rows], [181_000_000.0, 181_000_000.0])
        state = self.connection.execute(
            "SELECT sample_count FROM coin_online_residual_state"
        ).fetchone()
        self.assertEqual(state["sample_count"], 1)

    def test_large_error_is_scored_uncensored_but_learned_clipped(self) -> None:
        # 180,000,000 predicted vs 200,000,000 realised is an 11.1% miss: far
        # beyond MAX_RESIDUAL_RATIO.  Scoring must see it, learning must not.
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=True)
        self._record_comparison("2026-08-05T10:00:00Z", 180_000_000)
        self.connection.execute(
            """
            INSERT INTO confirmed_trades(
                id,event_time_utc,commodity,price,quantity,settlement,
                trade_form,confidence,training_eligible
            ) VALUES (1,'2026-08-05T10:00:30Z','امام',200000,5,'CASH','PHYSICAL',0.99,1)
            """
        )
        self.connection.commit()
        result = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(result["evaluated"], 2)

        stored = self.connection.execute(
            "SELECT residual_ratio, residual_ratio_raw FROM coin_estimate_predictions "
            "WHERE model_id='MAIN_ONLINE'"
        ).fetchone()
        # The learning column keeps its bounded contract...
        self.assertAlmostEqual(stored["residual_ratio"], MAX_RESIDUAL_RATIO, places=9)
        # ...while the evaluation column keeps the true error.
        self.assertAlmostEqual(stored["residual_ratio_raw"], 20.0 / 180.0, places=6)

        state = self.connection.execute(
            "SELECT residual_mean FROM coin_online_residual_state"
        ).fetchone()
        self.assertAlmostEqual(state["residual_mean"], MAX_RESIDUAL_RATIO, places=9)

        outcomes = summarize_model_outcomes(
            self.connection,
            as_of=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            min_refresh_seconds=0,
        )
        self.assertAlmostEqual(
            outcomes[MAIN_COMPARISON_MODEL_ID]["mape_percent"],
            20.0 / 180.0 * 100.0,
            places=4,
        )
        self.assertEqual(
            outcomes[MAIN_COMPARISON_MODEL_ID]["error_source"], "RAW_UNCLIPPED"
        )
        self.assertEqual(
            outcomes[MAIN_COMPARISON_MODEL_ID]["capped_only_sample_count"], 0
        )

    def test_legacy_rows_without_raw_error_are_flagged_not_silently_averaged(self) -> None:
        self.connection.execute(
            """
            INSERT INTO coin_estimate_predictions(
                prediction_time_utc, model_id, evaluation_role,
                comparison_cohort_utc, commodity, settlement,
                structural_estimated_price_toman, estimated_price_toman,
                group_live_enabled, actual_price_toman, actual_event_utc,
                residual_ratio, evaluated_at_utc, created_at_utc
            ) VALUES ('2026-08-05T10:00:00Z','MAIN_COMPARISON','COMPARISON',
                '2026-08-05T10:00:00Z','امام','CASH',
                1.8e8, 1.8e8, 1, 1.9e8, '2026-08-05T10:00:30Z',
                0.035, '2026-08-05T10:01:00Z', '2026-08-05T10:00:00Z')
            """
        )
        self.connection.commit()
        outcomes = summarize_model_outcomes(
            self.connection,
            as_of=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            min_refresh_seconds=0,
        )
        entry = outcomes[MAIN_COMPARISON_MODEL_ID]
        self.assertEqual(entry["capped_only_sample_count"], 1)
        self.assertEqual(entry["error_source"], "MIXED_RAW_AND_LEGACY_CLIPPED")
        self.assertAlmostEqual(entry["mape_percent"], 3.5, places=6)

    def test_outcome_summary_reports_latest_evaluated_version_not_lexical_max(self) -> None:
        self.connection.executemany(
            """
            INSERT INTO coin_estimate_predictions(
                prediction_time_utc, model_id, model_version, evaluation_role,
                comparison_cohort_utc, commodity, settlement,
                structural_estimated_price_toman, estimated_price_toman,
                group_live_enabled, actual_price_toman, actual_event_utc,
                residual_ratio, residual_ratio_raw, evaluated_at_utc,
                created_at_utc
            ) VALUES (?, 'MAIN_COMPARISON', ?, 'COMPARISON', ?, 'امام', 'CASH',
                1.8e8, 1.8e8, 1, 1.81e8, ?, 0.005, 0.005, ?, ?)
            """,
            [
                (
                    "2026-08-05T10:00:00Z",
                    "Z_OLD",
                    "2026-08-05T10:00:00Z",
                    "2026-08-05T10:00:30Z",
                    "2026-08-05T10:01:00Z",
                    "2026-08-05T10:00:00Z",
                ),
                (
                    "2026-08-05T10:02:00Z",
                    "A_NEW",
                    "2026-08-05T10:02:00Z",
                    "2026-08-05T10:02:30Z",
                    "2026-08-05T10:03:00Z",
                    "2026-08-05T10:02:00Z",
                ),
            ],
        )
        self.connection.commit()
        outcomes = summarize_model_outcomes(
            self.connection,
            as_of=datetime(2026, 8, 5, 10, 4, tzinfo=UTC),
            min_refresh_seconds=0,
        )
        self.assertEqual(outcomes[MAIN_COMPARISON_MODEL_ID]["model_version"], "A_NEW")

    def test_shadow_books_are_sampled_more_coarsely_than_the_main_book(self) -> None:
        base = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
        rates = [{"commodity_name": "امام", "estimated_price_toman": 180_000_000}]
        for offset in (0, 30, 60, 90, 120):
            at = base + timedelta(seconds=offset)
            record_predictions(
                self.connection,
                prediction_time=at,
                settlement="CASH",
                rates=rates,
                group_live_enabled=True,
                model_id="MAIN_ONLINE",
            )
            for model_id in (MAIN_COMPARISON_MODEL_ID, "SHADOW1_PREVIOUS"):
                record_predictions(
                    self.connection,
                    prediction_time=at,
                    settlement="CASH",
                    rates=rates,
                    group_live_enabled=True,
                    model_id=model_id,
                    evaluation_role=COMPARISON_EVALUATION_ROLE,
                    comparison_cohort=at,
                )
        self.connection.commit()
        counts = dict(
            self.connection.execute(
                "SELECT model_id, COUNT(*) FROM coin_estimate_predictions GROUP BY model_id"
            ).fetchall()
        )
        self.assertEqual(counts["MAIN_ONLINE"], 5)
        self.assertEqual(counts[MAIN_COMPARISON_MODEL_ID], 2)
        self.assertEqual(counts["SHADOW1_PREVIOUS"], 2)
        self.assertEqual(SHADOW_LEDGER_MIN_INTERVAL_SECONDS, 120)
        # Every shadow timestamp must also exist for the main book, so the two
        # remain directly comparable at the same instant.
        main_comparison_times = {
            row[0]
            for row in self.connection.execute(
                "SELECT prediction_time_utc FROM coin_estimate_predictions "
                "WHERE model_id='MAIN_COMPARISON'"
            )
        }
        shadow_times = {
            row[0]
            for row in self.connection.execute(
                "SELECT prediction_time_utc FROM coin_estimate_predictions WHERE model_id='SHADOW1_PREVIOUS'"
            )
        }
        self.assertEqual(shadow_times, main_comparison_times)

    def test_actual_scores_latest_forecasts_and_same_comparison_cohort(self) -> None:
        base = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
        rates = [{"commodity_name": "امام", "estimated_price_toman": 180_000_000}]
        for offset in range(0, 181, 30):
            at = base + timedelta(seconds=offset)
            record_predictions(
                self.connection,
                prediction_time=at,
                settlement="CASH",
                rates=rates,
                group_live_enabled=True,
                model_id="MAIN_ONLINE",
            )
        for offset in (0, 120):
            at = base + timedelta(seconds=offset)
            for model_id in (MAIN_COMPARISON_MODEL_ID, "SHADOW1_PREVIOUS"):
                record_predictions(
                    self.connection,
                    prediction_time=at,
                    settlement="CASH",
                    rates=rates,
                    group_live_enabled=True,
                    model_id=model_id,
                    evaluation_role=COMPARISON_EVALUATION_ROLE,
                    comparison_cohort=at,
                )
        self.connection.execute(
            """
            INSERT INTO confirmed_trades(
                id,event_time_utc,commodity,price,quantity,settlement,
                trade_form,confidence,training_eligible
            ) VALUES (1,'2026-08-05T10:03:15Z','امام',181000,5,'CASH','PHYSICAL',0.99,1)
            """
        )
        self.connection.commit()

        result = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 4, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(result["evaluated"], 3)
        matched = dict(
            self.connection.execute(
                "SELECT model_id, prediction_time_utc FROM coin_estimate_predictions "
                "WHERE actual_event_utc IS NOT NULL"
            ).fetchall()
        )
        self.assertEqual(matched["MAIN_ONLINE"], "2026-08-05T10:03:00Z")
        self.assertEqual(
            matched[MAIN_COMPARISON_MODEL_ID], "2026-08-05T10:02:00Z"
        )
        self.assertEqual(matched["SHADOW1_PREVIOUS"], "2026-08-05T10:02:00Z")
        outcomes = summarize_model_outcomes(
            self.connection,
            as_of=datetime(2026, 8, 5, 10, 4, tzinfo=UTC),
            min_refresh_seconds=0,
        )
        self.assertNotIn("MAIN_ONLINE", outcomes)
        self.assertEqual(
            set(outcomes), {MAIN_COMPARISON_MODEL_ID, "SHADOW1_PREVIOUS"}
        )

    def test_pending_rows_expire_only_past_every_matching_horizon(self) -> None:
        as_of = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        reachable = as_of - timedelta(hours=PENDING_EXPIRY_HOURS - 1)
        unreachable = as_of - timedelta(hours=PENDING_EXPIRY_HOURS + 1)
        self._record(reachable.isoformat().replace("+00:00", "Z"), 180_000_000)
        self._record(unreachable.isoformat().replace("+00:00", "Z"), 179_000_000)

        expired = expire_unmatched_predictions(self.connection, as_of=as_of)
        self.connection.commit()
        self.assertEqual(expired, 1)

        rows = dict(
            self.connection.execute(
                "SELECT prediction_time_utc, evaluation_mode FROM coin_estimate_predictions"
            ).fetchall()
        )
        self.assertIsNone(rows[reachable.isoformat().replace("+00:00", "Z")])
        self.assertEqual(
            rows[unreachable.isoformat().replace("+00:00", "Z")],
            UNMATCHED_EVALUATION_MODE,
        )
        # An expired row has no outcome, so it must not enter scoring.
        outcomes = summarize_model_outcomes(
            self.connection, as_of=as_of, min_refresh_seconds=0
        )
        self.assertEqual(outcomes, {})
        report = ledger_storage_report(self.connection, as_of=as_of)
        self.assertEqual(report["pending_rows"], 1)
        self.assertEqual(report["unmatched_rows"], 1)

    def test_outcome_summary_is_cached_only_for_a_file_backed_ledger(self) -> None:
        # An in-memory ledger has no stable identity, so two unrelated test or
        # one-shot connections must never be able to read each other's summary.
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=True)
        self._record_comparison("2026-08-05T10:00:00Z", 180_000_000)
        self.connection.execute(
            """
            INSERT INTO confirmed_trades(
                id,event_time_utc,commodity,price,quantity,settlement,
                trade_form,confidence,training_eligible
            ) VALUES (1,'2026-08-05T10:00:30Z','امام',181000,5,'CASH','PHYSICAL',0.99,1)
            """
        )
        self.connection.commit()
        reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            live_group_enabled=True,
        )
        as_of = datetime(2026, 8, 5, 10, 1, tzinfo=UTC)
        first = summarize_model_outcomes(self.connection, as_of=as_of)
        self.assertEqual(first[MAIN_COMPARISON_MODEL_ID]["sample_count"], 1)

        self.connection.execute("DELETE FROM coin_estimate_predictions")
        self.connection.commit()
        # Default cache TTL must be bypassed for an in-memory connection.
        again = summarize_model_outcomes(self.connection, as_of=as_of)
        self.assertEqual(again, {})

    def test_new_outcome_invalidates_the_file_backed_summary_cache(self) -> None:
        first_at = "2026-08-05T10:00:00Z"
        self._record_comparison(first_at, 180_000_000)
        self.connection.execute(
            """
            INSERT INTO confirmed_trades(
                id,event_time_utc,commodity,price,quantity,settlement,
                trade_form,confidence,training_eligible
            ) VALUES (1,'2026-08-05T10:00:30Z','امام',181000,5,'CASH','PHYSICAL',0.99,1)
            """
        )
        self.connection.commit()
        first_now = datetime(2026, 8, 5, 10, 1, tzinfo=UTC)
        with patch.object(
            online_recalibration,
            "_connection_identity",
            return_value="/tmp/test-outcome-cache.sqlite3",
        ):
            reconcile_predictions(
                self.connection, now=first_now, live_group_enabled=True
            )
            first = summarize_model_outcomes(self.connection, as_of=first_now)
            self.assertEqual(first[MAIN_COMPARISON_MODEL_ID]["sample_count"], 1)

            self._record_comparison("2026-08-05T10:02:00Z", 181_000_000)
            self.connection.execute(
                """
                INSERT INTO confirmed_trades(
                    id,event_time_utc,commodity,price,quantity,settlement,
                    trade_form,confidence,training_eligible
                ) VALUES (2,'2026-08-05T10:02:30Z','امام',182000,5,'CASH','PHYSICAL',0.99,1)
                """
            )
            self.connection.commit()
            second_now = datetime(2026, 8, 5, 10, 3, tzinfo=UTC)
            reconcile_predictions(
                self.connection, now=second_now, live_group_enabled=True
            )
            # This is inside the five-minute cache TTL.  The new outcome must
            # nevertheless be visible because reconciliation invalidated it.
            second = summarize_model_outcomes(self.connection, as_of=second_now)
            self.assertEqual(second[MAIN_COMPARISON_MODEL_ID]["sample_count"], 2)

    def test_prune_reports_before_deleting_and_keeps_the_labelled_corpus(self) -> None:
        as_of = datetime(2026, 8, 5, 10, 1, tzinfo=UTC)
        # An outcome row inside the one-year corpus horizon.
        self._record("2026-05-01T10:00:00Z", 150_000_000, enabled=True)
        self.connection.execute(
            """
            UPDATE coin_estimate_predictions
            SET evaluated_at_utc='2026-05-01T10:05:00Z',
                actual_event_utc='2026-05-01T10:02:00Z',
                actual_price_toman=1.51e8, residual_ratio=0.006,
                residual_ratio_raw=0.006
            WHERE prediction_time_utc='2026-05-01T10:00:00Z'
            """
        )
        # An unmatched row well past the short unmatched horizon.
        self._record("2026-01-01T10:00:00Z", 149_000_000, enabled=True)
        self.connection.execute(
            "UPDATE coin_estimate_predictions "
            f"SET evaluated_at_utc='2026-01-05T10:00:00Z', evaluation_mode='{UNMATCHED_EVALUATION_MODE}' "
            "WHERE prediction_time_utc='2026-01-01T10:00:00Z'"
        )
        self.connection.commit()

        report = prune_prediction_ledger(self.connection, as_of=as_of)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["deleted_rows"], 0)
        self.assertEqual(report["total_rows"], 2)
        self.assertEqual(report["unmatched_prunable_rows"], 1)
        self.assertEqual(report["outcome_prunable_rows"], 0)

        applied = prune_prediction_ledger(self.connection, as_of=as_of, dry_run=False)
        self.assertEqual(applied["deleted_unmatched_rows"], 1)
        self.assertEqual(applied["deleted_outcome_rows"], 0)
        remaining = self.connection.execute(
            "SELECT prediction_time_utc FROM coin_estimate_predictions"
        ).fetchall()
        self.assertEqual([row[0] for row in remaining], ["2026-05-01T10:00:00Z"])

    def test_maintenance_batches_a_backlog_instead_of_one_huge_transaction(self) -> None:
        as_of = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        stale = as_of - timedelta(hours=PENDING_EXPIRY_HOURS + 1)
        self.connection.executemany(
            """
            INSERT INTO coin_estimate_predictions(
                prediction_time_utc, model_id, commodity, settlement,
                structural_estimated_price_toman, estimated_price_toman,
                group_live_enabled, created_at_utc
            ) VALUES (?, 'MAIN_ONLINE', 'امام', 'CASH', 1.8e8, 1.8e8, 1, ?)
            """,
            [
                (
                    (stale - timedelta(seconds=i)).isoformat().replace("+00:00", "Z"),
                    "2026-08-05T10:00:00Z",
                )
                for i in range(25)
            ],
        )
        self.connection.commit()

        first = expire_unmatched_predictions(self.connection, as_of=as_of, batch_rows=10)
        self.connection.commit()
        self.assertEqual(first, 10)
        remaining = self.connection.execute(
            "SELECT COUNT(*) FROM coin_estimate_predictions WHERE evaluated_at_utc IS NULL"
        ).fetchone()[0]
        self.assertEqual(remaining, 15)

        # Successive passes converge on the backlog.
        while expire_unmatched_predictions(self.connection, as_of=as_of, batch_rows=10):
            self.connection.commit()
        self.connection.commit()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM coin_estimate_predictions WHERE evaluated_at_utc IS NULL"
            ).fetchone()[0],
            0,
        )

    def test_failed_maintenance_is_retried_instead_of_suppressed(self) -> None:
        as_of = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
        identity = "/tmp/test-maintenance-retry.sqlite3"
        online_recalibration._LEDGER_MAINTENANCE_AT.pop(identity, None)
        with patch.object(
            online_recalibration, "_connection_identity", return_value=identity
        ), patch.object(
            online_recalibration,
            "prune_prediction_ledger",
            side_effect=RuntimeError("simulated failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                maintain_prediction_ledger(self.connection, as_of=as_of)
        self.assertNotIn(identity, online_recalibration._LEDGER_MAINTENANCE_AT)

        with patch.object(
            online_recalibration, "_connection_identity", return_value=identity
        ), patch.object(
            online_recalibration,
            "prune_prediction_ledger",
            return_value={"deleted_rows": 0},
        ) as prune:
            report = maintain_prediction_ledger(self.connection, as_of=as_of)
        prune.assert_called_once()
        self.assertEqual(report["status"], "RAN")
        online_recalibration._LEDGER_MAINTENANCE_AT.pop(identity, None)

    def test_prune_never_touches_a_row_reconciliation_can_still_reach(self) -> None:
        as_of = datetime(2026, 8, 5, 10, 1, tzinfo=UTC)
        recent = as_of - timedelta(hours=1)
        self._record(recent.isoformat().replace("+00:00", "Z"), 150_000_000)
        report = prune_prediction_ledger(self.connection, as_of=as_of, dry_run=False)
        self.assertEqual(report["expired_rows"], 0)
        self.assertEqual(report["deleted_rows"], 0)
        self.assertEqual(report["pending_rows"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM coin_estimate_predictions"
            ).fetchone()[0],
            1,
        )

    def test_offer_fallback_requires_a_near_synchronous_two_sided_book(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE messages (
                import_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                event_time_utc TEXT NOT NULL,
                PRIMARY KEY(import_id, message_id)
            );
            CREATE TABLE offers (
                id INTEGER PRIMARY KEY,
                import_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                commodity TEXT NOT NULL,
                price INTEGER NOT NULL,
                quantity INTEGER,
                side TEXT NOT NULL,
                settlement TEXT NOT NULL,
                trade_form TEXT NOT NULL,
                confidence REAL NOT NULL
            );
            INSERT INTO messages VALUES (1, 1, '2026-08-05T10:00:20Z');
            INSERT INTO offers VALUES (1, 1, 1, 'امام', 180000, 5, 'BUY', 'CASH', 'PHYSICAL', 0.95);
            """
        )
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=True)
        first = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(first["evaluated"], 0)
        self.connection.executescript(
            """
            INSERT INTO messages VALUES (1, 2, '2026-08-05T10:00:40Z');
            INSERT INTO offers VALUES (2, 1, 2, 'امام', 182000, 5, 'SELL', 'CASH', 'PHYSICAL', 0.95);
            """
        )
        self.connection.commit()
        second = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(second["evaluated"], 1)
        row = self.connection.execute(
            "SELECT actual_price_toman, evaluation_mode FROM coin_estimate_predictions"
        ).fetchone()
        self.assertEqual(row["actual_price_toman"], 181_000_000.0)
        self.assertEqual(row["evaluation_mode"], "FORWARD_5M")

    def test_correction_waits_for_three_samples_and_never_narrows_range(self) -> None:
        for index in range(3):
            prediction = datetime(2026, 8, 5, 10, index, tzinfo=UTC)
            self._record(prediction.isoformat().replace("+00:00", "Z"), 180_000_000, enabled=True)
            self.connection.execute(
                """
                INSERT INTO confirmed_trades(
                    id,event_time_utc,commodity,price,quantity,settlement,
                    trade_form,confidence,training_eligible
                ) VALUES (?,?,?,?,?,?,?,?,1)
                """,
                (index + 1, f"2026-08-05T10:0{index}:30Z", "امام", 181_000, 5, "CASH", "PHYSICAL", 0.99),
            )
            self.connection.commit()
            reconcile_predictions(
                self.connection,
                now=datetime(2026, 8, 5, 10, index, 59, tzinfo=UTC),
                live_group_enabled=True,
            )
        rate = {
            "commodity_name": "امام",
            "estimated_price_toman": 180_000_000,
            "tolerance": {
                "lower_price_toman": 179_000_000,
                "upper_price_toman": 181_000_000,
            },
        }
        info = apply_calibration(
            self.connection, commodity="امام", settlement="CASH", rate=rate
        )
        self.assertEqual(info["status"], "APPLIED")
        self.assertGreater(rate["estimated_price_toman"], 180_000_000)
        self.assertLessEqual(rate["tolerance"]["lower_price_toman"], 179_000_000)
        self.assertGreaterEqual(rate["tolerance"]["upper_price_toman"], 181_000_000)

    def test_recent_realized_correction_recenters_quiet_book_from_distinct_actuals(self) -> None:
        now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
        for offset, residual in ((45, -0.008), (15, -0.010)):
            occurred = now - timedelta(minutes=offset)
            self.connection.execute(
                """
                INSERT INTO coin_estimate_predictions(
                    prediction_time_utc, commodity, settlement,
                    structural_estimated_price_toman, estimated_price_toman,
                    lower_price_toman, upper_price_toman, group_live_enabled,
                    actual_price_toman, actual_event_utc, residual_ratio,
                    evaluated_at_utc, evaluation_mode, created_at_utc
                ) VALUES (?, 'امام', 'TOMORROW', 180000000, 180000000,
                          179000000, 181000000, 1,
                          178200000, ?, ?, ?, 'FORWARD_5M', ?)
                """,
                (
                    (occurred - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                    occurred.isoformat().replace("+00:00", "Z"),
                    residual,
                    now.isoformat().replace("+00:00", "Z"),
                    now.isoformat().replace("+00:00", "Z"),
                ),
            )
        self.connection.commit()
        rate = {
            "estimated_price_toman": 180_000_000,
            "tolerance": {
                "lower_price_toman": 179_000_000,
                "upper_price_toman": 181_000_000,
            },
            "group_offer_anchor": {"status": "NO_DATA"},
        }
        info = apply_recent_realized_calibration(
            self.connection,
            commodity="امام",
            settlement="TOMORROW",
            rate=rate,
            as_of=now,
        )
        self.assertEqual(info["status"], "APPLIED")
        self.assertEqual(info["actual_event_count"], 2)
        self.assertAlmostEqual(info["correction_ratio"], -0.010)
        self.assertEqual(rate["estimated_price_toman"], 178_200_000)
        self.assertEqual(rate["estimated_project_price"], 178_200)
        self.assertLess(rate["tolerance"]["lower_price_toman"], 179_000_000)
        self.assertIn("RECENT_REALIZED_RESIDUAL", rate["method"])

    def test_recent_realized_correction_never_overrides_fresh_book(self) -> None:
        now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
        self.connection.execute(
            """
            INSERT INTO coin_estimate_predictions(
                prediction_time_utc, commodity, settlement,
                structural_estimated_price_toman, estimated_price_toman,
                lower_price_toman, upper_price_toman, group_live_enabled,
                actual_price_toman, actual_event_utc, residual_ratio,
                evaluated_at_utc, evaluation_mode, created_at_utc
            ) VALUES ('2026-08-05T11:40:00Z','امام','CASH',180000000,180000000,
                      179000000,181000000,1,178200000,'2026-08-05T11:45:00Z',-0.01,
                      '2026-08-05T11:45:01Z','FORWARD_5M','2026-08-05T11:45:01Z')
            """
        )
        self.connection.commit()
        rate = {
            "estimated_price_toman": 180_000_000,
            "group_offer_anchor": {"status": "OBSERVED"},
        }
        info = apply_recent_realized_calibration(
            self.connection,
            commodity="امام",
            settlement="CASH",
            rate=rate,
            as_of=now,
        )
        self.assertEqual(info["status"], "SKIPPED_FRESH_LIVE_GROUP_ANCHOR")
        self.assertEqual(rate["estimated_price_toman"], 180_000_000)


if __name__ == "__main__":
    unittest.main()
