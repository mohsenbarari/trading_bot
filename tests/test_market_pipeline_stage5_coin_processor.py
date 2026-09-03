from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.market_intelligence.private_coin_processor import (
    CoinProcessorError,
    CoinProcessorPaths,
    _load_causal_inputs,
    _paths,
    process_coin_spool_cycle,
)
from core.market_intelligence.capture_event_adapter import initialize_capture_adapter
from core.market_intelligence.coin_group_feedback import (
    load_coin_group_parser_feedback,
    record_coin_group_parser_feedback,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from core.market_intelligence.market_contracts import derive_event_key


def event(
    sequence: int,
    *,
    group: int,
    message_id: int,
    text: str,
    sender: str,
    second: int,
    reply_to: int | None = None,
) -> dict[str, object]:
    return {
        "schema": "coin_group_event",
        "schema_version": "2.0",
        "event_id": f"50000000-0000-7000-8000-{sequence:012d}",
        "event_type": "message_created",
        "source": {"market": "coin", "source_id": f"GROUP_{group}"},
        "message": {
            "message_id": str(message_id),
            "published_at_utc": f"2026-08-24T10:00:{second:02d}Z",
            "edited_at_utc": None,
            "text": text,
            "content_type": "text",
            "is_forwarded": False,
            "is_backfill": False,
            "sender": {
                "peer_id": sender,
                "kind": "user",
                "display_name": None,
            },
            "reply": {
                "status": (
                    "resolved_from_live_stream" if reply_to is not None else "not_reply"
                ),
                "message_id": str(reply_to) if reply_to is not None else None,
            },
        },
        "producer": {
            "available_at_utc": f"2026-08-24T10:00:{second + 1:02d}Z"
        },
    }


class MarketPipelineStage5CoinProcessorTests(unittest.TestCase):
    def _fixture(self, root: Path) -> CoinProcessorPaths:
        spool = root / "capture" / "account2"
        spool.mkdir(parents=True)
        state = root / "state"
        state.mkdir()
        return CoinProcessorPaths(
            spool_directory=spool,
            staging_database=state / "staging.sqlite3",
            market_database=state / "market.sqlite3",
            corpus_database=state / "corpus.sqlite3",
            feedback_database=None,
            prediction_database=None,
        )

    def test_processor_only_sqlite_cache_is_bounded_and_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:02:00Z",
                sqlite_cache_kib_per_store=64,
            )
            self.assertEqual(report["records"], 0)

        with tempfile.TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            with self.assertRaisesRegex(
                CoinProcessorError,
                "coin_processor_sqlite_cache_invalid",
            ):
                process_coin_spool_cycle(
                    paths=paths,
                    mode="fixture",
                    now_utc="2026-08-24T10:02:00Z",
                    sqlite_cache_kib_per_store=131_073,
                )
            self.assertFalse(paths.staging_database.exists())
            self.assertFalse(paths.market_database.exists())

    def test_fixture_cycle_projects_both_groups_and_exact_reply_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            raw_offer = "امام فروش فردا 190000 / 5 تا"
            records = [
                event(1, group=1, message_id=1, text=raw_offer, sender="a" * 16, second=0),
                event(
                    2,
                    group=1,
                    message_id=2,
                    text="ب5 تا189900",
                    sender="b" * 16,
                    second=2,
                    reply_to=1,
                ),
                event(
                    3,
                    group=1,
                    message_id=3,
                    text="برکت",
                    sender="a" * 16,
                    second=4,
                    reply_to=2,
                ),
                event(
                    4,
                    group=2,
                    message_id=1,
                    text="ربع بهار نقدی فروش 51900 / 3 تا",
                    sender="c" * 16,
                    second=6,
                ),
            ]
            spool = paths.spool_directory / "events-2026-08-24.jsonl"
            spool.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
                encoding="utf-8",
            )
            report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:02:00Z",
            )
            self.assertEqual(report["records"], 4)
            self.assertEqual(report["group_eligible_trades"], 1)
            market = sqlite3.connect(paths.market_database)
            market.row_factory = sqlite3.Row
            try:
                rows = market.execute(
                    "SELECT source_code,event_type,instrument,price_value,attributes_json "
                    "FROM market_observations WHERE quality_state='ELIGIBLE' "
                    "ORDER BY source_code,event_type"
                ).fetchall()
            finally:
                market.close()
            self.assertEqual(
                {(row["source_code"], row["event_type"]) for row in rows},
                {("GROUP_1", "OFFER"), ("GROUP_1", "TRADE"), ("GROUP_2", "OFFER")},
            )
            trade = next(row for row in rows if row["event_type"] == "TRADE")
            self.assertEqual(trade["price_value"], "189900")
            evidence = json.loads(trade["attributes_json"])["field_evidence"]
            self.assertEqual(evidence["price"], ["EXACT_REPLY_BRANCH_LAST_AGREED_TERM"])
            self.assertNotIn(raw_offer, trade["attributes_json"])

    def test_partial_tail_is_not_advanced_and_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            first = event(
                11,
                group=1,
                message_id=11,
                text="نیم بهار نقدی فروش 95000 / 2 تا",
                sender="d" * 16,
                second=0,
            )
            second = event(
                12,
                group=2,
                message_id=12,
                text="ربع بهار نقدی خرید 51000 / 1 تا",
                sender="e" * 16,
                second=2,
            )
            second_line = json.dumps(second, ensure_ascii=False)
            spool = paths.spool_directory / "events-2026-08-24.jsonl"
            spool.write_text(
                json.dumps(first, ensure_ascii=False) + "\n" + second_line[:40],
                encoding="utf-8",
            )
            first_report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:02:00Z",
            )
            self.assertEqual(first_report["records"], 1)
            with spool.open("a", encoding="utf-8") as handle:
                handle.write(second_line[40:] + "\n")
            second_report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:03:00Z",
            )
            self.assertEqual(second_report["records"], 1)
            replay = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:04:00Z",
            )
            self.assertEqual(replay["records"], 0)
            connection = sqlite3.connect(paths.market_database)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM market_observations WHERE event_type='OFFER'"
                    ).fetchone()[0],
                    2,
                )
            finally:
                connection.close()

    def test_cutover_reconciliation_loads_historical_anchors_without_dirty_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            record = event(
                15,
                group=1,
                message_id=15,
                text="امام نقدی فروش 190000 / 1 تا",
                sender="a" * 16,
                second=0,
            )
            (paths.spool_directory / "events-2026-08-24.jsonl").write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:02:00Z",
            )
            staging = connect_coin_group_staging(paths.staging_database)
            try:
                staging.execute("DELETE FROM capture_dirty_groups")
                staging.execute(
                    "UPDATE capture_adapter_metadata SET schema_version=5 "
                    "WHERE singleton=1"
                )
                staging.commit()
                initialize_capture_adapter(staging)
                self.assertEqual(
                    staging.execute(
                        "SELECT COUNT(*) FROM capture_dirty_groups"
                    ).fetchone()[0],
                    0,
                )
                paths = replace(
                    paths,
                    prediction_database=Path(directory) / "prediction.sqlite3",
                )
                loaded = SimpleNamespace(
                    anchors=("anchor",), rows_seen=1, rows_rejected=0
                )
                with patch(
                    "core.market_intelligence.private_coin_processor."
                    "load_coin_prediction_anchors",
                    return_value=loaded,
                ) as loader:
                    _feedback, anchors, stats = _load_causal_inputs(
                        staging,
                        paths,
                        mode="fixture",
                        as_of_utc="2026-08-24T10:02:01Z",
                    )
                self.assertEqual(anchors, ("anchor",))
                self.assertEqual(stats["anchors"], 1)
                self.assertEqual(
                    loader.call_args.kwargs["earliest_event_time_utc"],
                    "2026-08-24T10:00:00Z",
                )
            finally:
                staging.close()

    def test_live_paths_require_both_causal_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "capture" / "account2").mkdir(parents=True)
            environment = {
                "MARKET_PIPELINE_CAPTURE_ROOT": str(root / "capture"),
                "MARKET_PROCESSOR_FEEDBACK_DB": "",
                "MARKET_PROCESSOR_PREDICTION_DB": "",
            }
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(
                    CoinProcessorError, "causal_inputs_required"
                ):
                    _paths(mode="live", state_directory=root / "state")

    def test_paths_bind_sqlite_temp_files_to_private_processor_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "capture" / "account2").mkdir(parents=True)
            environment = {
                "MARKET_PIPELINE_CAPTURE_ROOT": str(root / "capture"),
            }
            with patch.dict(os.environ, environment, clear=False):
                _paths(mode="fixture", state_directory=root / "state")
                sqlite_tmp = root / "state" / "sqlite-tmp"
                self.assertEqual(os.environ["SQLITE_TMPDIR"], str(sqlite_tmp))
                self.assertEqual(sqlite_tmp.stat().st_mode & 0o777, 0o700)

    def test_expired_raw_exact_review_projects_and_marks_feedback_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixture(root)
            record = event(
                18,
                group=1,
                message_id=18,
                text="ف 190000 5تا",
                sender="a" * 16,
                second=0,
            )
            (paths.spool_directory / "events-2026-08-24.jsonl").write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:02:00Z",
            )
            event_key = derive_event_key("coin-group-offer-v1", 1, 18, 0)
            feedback_path = root / "feedback.sqlite3"
            review = record_coin_group_parser_feedback(
                feedback_path,
                event_key=event_key,
                event_type="OFFER",
                group_number=1,
                source_event_time_utc="2026-08-24T10:00:00Z",
                ambiguous_fields=["commodity"],
                event_confirmed=True,
                commodity_code="IMAM",
                side="SELL",
                price_project_thousand_toman=190_000,
                quantity=5,
                settlement_term="TOMORROW",
                trade_form="PHYSICAL",
                is_conditional=False,
                reviewer="operator",
                reviewed_at_utc="2026-08-28T10:00:00Z",
            )
            self.assertEqual(review.applied_revision, 0)
            paths = replace(paths, feedback_database=feedback_path)
            report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-28T10:01:00Z",
            )
            self.assertEqual(report["feedback_reviews_projected"], 1)
            self.assertEqual(report["feedback_reviews_marked_applied"], 1)
            applied = load_coin_group_parser_feedback(feedback_path)[event_key]
            self.assertEqual(applied.applied_revision, applied.review_revision)
            market = sqlite3.connect(paths.market_database)
            try:
                row = market.execute(
                    "SELECT instrument,quality_state,available_at_utc "
                    "FROM market_observations WHERE event_key=?",
                    (event_key,),
                ).fetchone()
            finally:
                market.close()
            self.assertEqual(row, ("COIN_IMAM", "ELIGIBLE", "2026-08-28T10:00:00Z"))

    def test_invalid_sibling_is_quarantined_without_payload_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            valid = event(
                21,
                group=1,
                message_id=21,
                text="امام نقدی فروش 190000 / 1 تا",
                sender="f" * 16,
                second=0,
            )
            invalid = b'{"private":"must-not-persist"}\n'
            spool = paths.spool_directory / "events-2026-08-24.jsonl"
            spool.write_bytes(
                invalid + (json.dumps(valid, ensure_ascii=False) + "\n").encode("utf-8")
            )
            report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:02:00Z",
            )
            self.assertEqual((report["rejected"], report["accepted"]), (1, 1))
            staging = sqlite3.connect(paths.staging_database)
            try:
                row = staging.execute(
                    "SELECT record_sha256,reason FROM capture_rejected_records"
                ).fetchone()
            finally:
                staging.close()
            self.assertEqual(row[0], sha256(invalid).hexdigest())
            self.assertNotIn("must-not-persist", row[1])


if __name__ == "__main__":
    unittest.main()
