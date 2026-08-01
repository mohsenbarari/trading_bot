from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import tempfile
import unittest

from scripts.coin_intelligence_private_ingest.build_group_training_dataset_shadow import (
    VERSION as DATASET_VERSION,
    build_dataset,
)
from scripts.coin_intelligence_private_ingest.evaluate_group_anchor_shadow import (
    evaluate,
)
from scripts.coin_intelligence_private_ingest.train_group_noise_filter import (
    choose_thresholds,
    fit,
    operational_metrics,
)


def _database(path: Path, schema: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(schema)
    return connection


class GroupTrainingDatasetTests(unittest.TestCase):
    def test_source_ids_are_replaced_by_chain_and_correlated_fills_are_capped(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = _database(
                root / "raw.sqlite3",
                """CREATE TABLE source_messages_current(
                    source_key TEXT,message_id TEXT,record_json TEXT
                );""",
            )
            component = _database(
                root / "component.sqlite3",
                """CREATE TABLE offer_component_candidates(
                    source_key TEXT,message_id TEXT,offer_index INTEGER,
                    group_number INTEGER,extraction_status TEXT,
                    extracted_json TEXT
                );""",
            )
            stage = _database(
                root / "stage.sqlite3",
                """CREATE TABLE text_candidates(
                    source_key TEXT,message_id TEXT,telegram_datetime TEXT
                );""",
            )
            trades = _database(
                root / "trades.sqlite3",
                """CREATE TABLE linked_confirmed_trades(
                    source_key TEXT,offer_message_id TEXT,request_message_id TEXT,
                    trade_json TEXT
                );""",
            )
            source = "account2_group1"
            offer_message = "4295000001"
            base_record = {
                "sender_name": "offerer",
                "telegram_datetime": "2026-08-01T10:00:00Z",
                "text": "5 امام فروش 187200",
            }
            rows = [(source, offer_message, json.dumps(base_record))]
            for index in range(2):
                rows.append(
                    (
                        source,
                        str(4295000010 + index),
                        json.dumps(
                            {
                                "sender_name": f"buyer-{index}",
                                "telegram_datetime": f"2026-08-01T10:0{index + 1}:00Z",
                            }
                        ),
                    )
                )
            raw.executemany(
                "INSERT INTO source_messages_current VALUES(?,?,?)", rows
            )
            stage.execute(
                "INSERT INTO text_candidates VALUES(?,?,?)",
                (source, offer_message, "2026-08-01T10:00:00Z"),
            )
            extracted = {
                "group_number": 1,
                "source_text": "5 امام فروش 187200",
                "commodity": "امام",
                "price": 187200,
                "quantity": 5,
                "side": "SELL",
                "settlement": "TOMORROW",
                "trade_form": "PHYSICAL",
                "confidence": 0.99,
            }
            component.execute(
                "INSERT INTO offer_component_candidates VALUES(?,?,?,?,?,?)",
                (
                    source,
                    offer_message,
                    0,
                    1,
                    "SHADOW_ACCEPTED",
                    json.dumps(extracted),
                ),
            )
            for index in range(2):
                trade = {
                    "event_time_utc": f"2026-08-01T10:0{index + 1}:00Z",
                    "commodity": "امام",
                    "price": 187200,
                    "quantity": 2,
                    "side": "SELL",
                    "settlement": "TOMORROW",
                    "trade_form": "PHYSICAL",
                    "confirmation_type": "REPLY_CHAIN",
                    "confidence": 0.98,
                    "training_eligible": True,
                }
                trades.execute(
                    "INSERT INTO linked_confirmed_trades VALUES(?,?,?,?)",
                    (
                        source,
                        offer_message,
                        str(4295000010 + index),
                        json.dumps(trade),
                    ),
                )
            for connection in (raw, component, stage, trades):
                connection.commit()
                connection.close()

            output = root / "training.sqlite3"
            result = build_dataset(
                component_path=root / "component.sqlite3",
                raw_path=root / "raw.sqlite3",
                stage_path=root / "stage.sqlite3",
                trades_path=root / "trades.sqlite3",
                output_path=output,
                manifest_path=root / "manifest.json",
                snapshot_root=root / "snapshots",
            )
            connection = sqlite3.connect(output)
            offer = connection.execute(
                "SELECT economic_chain_id,dataset_version FROM offer_training_examples"
            ).fetchone()
            trade_rows = connection.execute(
                """SELECT economic_chain_id,chain_trade_count,training_weight
                FROM confirmed_trade_training_examples ORDER BY id"""
            ).fetchall()
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(confirmed_trade_training_examples)"
                )
            }
            connection.close()

            self.assertEqual(result["independent_trade_chains"], 1)
            self.assertEqual(offer[1], DATASET_VERSION)
            self.assertEqual({row[0] for row in trade_rows}, {offer[0]})
            self.assertEqual({row[1] for row in trade_rows}, {2})
            self.assertAlmostEqual(
                sum(float(row[2]) for row in trade_rows), 4 * (2**0.5)
            )
            self.assertNotIn("message_id", columns)
            self.assertNotIn("source_key", columns)


class GroupAnchorEvaluationTests(unittest.TestCase):
    def test_evaluation_purges_same_chain_and_keeps_tuning_out_of_test(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """CREATE TABLE offer_training_examples(
                    id INTEGER PRIMARY KEY,economic_chain_id INTEGER,
                    occurred_at_utc TEXT,commodity TEXT,settlement TEXT,
                    trade_form TEXT,price INTEGER,training_weight REAL
                );
                CREATE TABLE confirmed_trade_training_examples(
                    id INTEGER PRIMARY KEY,economic_chain_id INTEGER,
                    occurred_at_utc TEXT,commodity TEXT,settlement TEXT,
                    trade_form TEXT,price INTEGER,training_weight REAL
                );"""
            )
            # Twelve independent chains are enough to exercise all three folds.
            for index in range(12):
                chain = index + 1
                minute = index * 5
                hour, minute_in_hour = divmod(minute, 60)
                offer_time = f"2026-08-01T{10 + hour:02d}:{minute_in_hour:02d}:00Z"
                trade_time = f"2026-08-01T{10 + hour:02d}:{minute_in_hour + 1:02d}:00Z"
                price = 180000 + index * 100
                connection.execute(
                    "INSERT INTO offer_training_examples VALUES(?,?,?,?,?,?,?,?)",
                    (
                        chain,
                        chain,
                        offer_time,
                        "امام",
                        "TOMORROW",
                        "PHYSICAL",
                        price,
                        1.0,
                    ),
                )
                connection.execute(
                    "INSERT INTO confirmed_trade_training_examples VALUES(?,?,?,?,?,?,?,?)",
                    (
                        chain,
                        chain,
                        trade_time,
                        "امام",
                        "TOMORROW",
                        "PHYSICAL",
                        price,
                        4.0,
                    ),
                )
            connection.commit()
            connection.close()

            report = evaluate(path)

            self.assertEqual(
                report["split"]["method"],
                "chronological_60_20_20_by_economic_chain",
            )
            self.assertTrue(report["split"]["same_chain_purged_from_features"])
            self.assertEqual(
                report["tuned_weighted_candidate"]["selected_on"],
                "VALIDATION_ONLY",
            )
            self.assertEqual(report["promotion"]["status"], "SHADOW_NOT_PROMOTED")


class GroupRelevanceTrainingTests(unittest.TestCase):
    def test_operational_thresholds_report_auto_reject_false_negatives(self) -> None:
        training = [
            ("2026-08-01T08:00:00Z", "5 امام فروش 187200", 1),
            ("2026-08-01T08:01:00Z", "10 ربع خرید 52300", 1),
            ("2026-08-01T08:02:00Z", "سلام صبح بخیر", 0),
            ("2026-08-01T08:03:00Z", "ممنون", 0),
        ] * 8
        model = fit(training)
        keep, reject = choose_thresholds(model, training)
        report = operational_metrics(
            model,
            training,
            keep_threshold=keep,
            reject_threshold=reject,
        )

        self.assertGreater(keep, reject)
        self.assertEqual(report["relevant_rows_auto_rejected"], 0)
        self.assertGreater(report["auto_keep_count"], 0)


if __name__ == "__main__":
    unittest.main()
