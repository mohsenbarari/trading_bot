"""Contract, reconciliation, and privacy tests for new capture spools."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.capture_event_adapter import (
    CAPTURE_ADAPTER_SCHEMA_VERSION,
    CaptureEventContractError,
    decode_coin_group_event,
    decode_market_channel_event,
    initialize_capture_adapter,
    project_capture_changes,
    stage_capture_event,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from core.market_intelligence.market_contracts import derive_event_key
from core.market_intelligence.market_fact_projection import initialize_export_ledger
from core.market_intelligence.market_store import connect_market_store, initialize_market_store


def market_event(
    sequence: int,
    *,
    source: str,
    text: str | None,
    event_type: str = "message_created",
    message_id: int = 1,
    published: str | None = "2026-08-24T10:00:00Z",
    available: str = "2026-08-24T10:00:01Z",
    edited: str | None = None,
    is_backfill: bool = False,
    entities: list[dict[str, object]] | None = None,
    content_type: str = "text",
) -> dict[str, object]:
    message = {
        "message_id": str(message_id),
        "published_at_utc": published,
        "edited_at_utc": edited,
        "text": text,
        "text_sha256": sha256(text.encode("utf-8")).hexdigest() if text is not None else None,
        "is_forwarded": False,
        "entities": entities or [],
        "content_type": content_type,
    }
    return {
        "schema": "market_channel_event",
        "schema_version": "1.0",
        "event_id": f"00000000-0000-7000-8000-{sequence:012d}",
        "event_type": event_type,
        "source": {
            "market": "coin_intelligence",
            "source_id": source,
            # The adapter routes by allowlisted source identity.  This legacy
            # producer hint is intentionally not authoritative.
            "parser_profile": "MELTED_FLOW" if source == "MELTED_PRIMARY_FLOW" else source,
        },
        "message": message,
        "producer": {
            "available_at_utc": available,
            "is_backfill": is_backfill,
        },
    }


def group_event(
    sequence: int,
    *,
    text: str | None,
    event_type: str = "message_created",
    message_id: int = 1,
    sender: str = "0123456789abcdef",
    reply_to: int | None = None,
    published: str | None = "2026-08-24T10:00:00Z",
    available: str | None = "2026-08-24T10:00:01Z",
    edited: str | None = None,
    is_backfill: bool = False,
) -> dict[str, object]:
    return {
        "schema": "coin_group_event",
        "schema_version": "2.0",
        "event_id": f"10000000-0000-7000-8000-{sequence:012d}",
        "event_type": event_type,
        "source": {"market": "coin", "source_id": "GROUP_1"},
        "message": {
            "message_id": str(message_id),
            "published_at_utc": published,
            "edited_at_utc": edited,
            "text": text,
            "content_type": "text" if text is not None else None,
            "is_forwarded": False,
            "is_backfill": is_backfill,
            "sender": {"peer_id": sender, "kind": "user", "display_name": None},
            "reply": {
                "status": "resolved_from_live_stream" if reply_to is not None else "not_reply",
                "message_id": str(reply_to) if reply_to is not None else None,
            },
        },
        "producer": {"available_at_utc": available},
    }


class CaptureEventAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.staging = connect_coin_group_staging(root / "capture.sqlite3")
        self.market = connect_market_store(root / "market.sqlite3")
        initialize_capture_adapter(self.staging)
        initialize_market_store(self.market)

    def tearDown(self) -> None:
        self.staging.close()
        self.market.close()
        self.tempdir.cleanup()

    def _stage_market(self, record: dict[str, object]) -> None:
        stage_capture_event(self.staging, decode_market_channel_event(record))
        self.staging.commit()

    def _stage_group(self, record: dict[str, object]) -> None:
        stage_capture_event(self.staging, decode_coin_group_event(record))
        self.staging.commit()

    def _project(self, at: str = "2026-08-24T10:02:00Z"):
        report = project_capture_changes(self.staging, self.market, as_of_utc=at)
        self.market.commit()
        self.staging.commit()
        return report

    def test_digest_mismatch_and_unavailable_legacy_receipt_fail_closed(self) -> None:
        bad = market_event(1, source="XAUUSD", text="4630.10")
        bad["message"]["text_sha256"] = "0" * 64  # type: ignore[index]
        with self.assertRaisesRegex(CaptureEventContractError, "text_digest_mismatch"):
            decode_market_channel_event(bad)
        legacy = group_event(2, text="امام فروش فردا 190000 / 2 تا", available=None)
        with self.assertRaisesRegex(CaptureEventContractError, "available_at_utc_required"):
            decode_coin_group_event(legacy)

    def test_primary_source_uses_dedicated_dimensions_and_edit_is_not_a_trade(self) -> None:
        self._stage_market(
            market_event(
                1,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 5 تا بدون حواله",
            )
        )
        self._project()
        first = self.market.execute(
            "SELECT trade_form,settlement_term,event_type,price_num FROM market_observations "
            "WHERE source_code='PRIVATE_GOLD_CHANNEL' AND quality_state='ELIGIBLE'"
        ).fetchall()
        self.assertEqual(
            [(row["trade_form"], row["settlement_term"], row["event_type"], row["price_num"]) for row in first],
            [("PHYSICAL", "TOMORROW", "OFFER", 95_000_000.0)],
        )

        self._stage_market(
            market_event(
                2,
                source="MELTED_PRIMARY_FLOW",
                text="95,100,000 فروش 5 تا بدون حواله",
                event_type="message_edited",
                edited="2026-08-24T10:00:20Z",
                available="2026-08-24T10:00:21Z",
            )
        )
        self._project()
        rows = self.market.execute(
            "SELECT event_type,price_num FROM market_observations "
            "WHERE source_code='PRIVATE_GOLD_CHANNEL' AND quality_state='ELIGIBLE'"
        ).fetchall()
        self.assertEqual(
            [(row["event_type"], row["price_num"]) for row in rows],
            [("OFFER", 95_000_000.0)],
        )

        self._stage_market(
            market_event(
                3,
                source="MELTED_PRIMARY_FLOW",
                text=None,
                event_type="message_deleted",
                published=None,
                available="2026-08-24T10:00:30Z",
            )
        )
        report = self._project()
        self.assertGreaterEqual(report.market_facts_retracted, 1)
        self.assertEqual(
            self.market.execute(
                "SELECT COUNT(*) FROM market_observations WHERE source_code='PRIVATE_GOLD_CHANNEL' AND quality_state='ELIGIBLE'"
            ).fetchone()[0],
            1,
        )

    def test_out_of_range_primary_offer_is_filtered_without_blocking_following_rows(self) -> None:
        self._stage_market(
            market_event(
                4,
                source="MELTED_PRIMARY_FLOW",
                text="600,000,000 فروش 5 تا نقد حاضر",
                message_id=4,
                available="2026-08-24T10:00:01Z",
            )
        )
        self._stage_market(
            market_event(
                5,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 5 تا بدون حواله",
                message_id=5,
                available="2026-08-24T10:00:02Z",
            )
        )

        report = self._project()

        self.assertEqual(report.market_messages_reprojected, 2)
        self.assertEqual(report.market_facts_upserted, 1)
        rejected = self.staging.execute(
            "SELECT status,disposition_code FROM capture_event_lineage "
            "WHERE source_id='MELTED_PRIMARY_FLOW' AND message_id=4"
        ).fetchone()
        self.assertEqual(
            (str(rejected["status"]), str(rejected["disposition_code"])),
            ("FILTERED", "PRICE_OUT_OF_CANONICAL_RANGE"),
        )
        self.assertIsNotNone(
            self.staging.execute(
                "SELECT finalized_at_utc FROM capture_primary_trade_deadlines "
                "WHERE source_id='MELTED_PRIMARY_FLOW' AND message_id=4 "
                "AND finalized_at_utc IS NOT NULL"
            ).fetchone()
        )
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_dirty_market_messages "
                "WHERE source_id='MELTED_PRIMARY_FLOW' AND message_id=4"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.market.execute(
                "SELECT price_num FROM market_observations "
                "WHERE source_code='PRIVATE_GOLD_CHANNEL' AND quality_state='ELIGIBLE'"
            ).fetchone()[0],
            95_000_000.0,
        )

    def test_xau_keeps_each_event_and_delete_retracts_only_target(self) -> None:
        self._stage_market(
            market_event(
                10,
                source="XAUUSD",
                text="4630.10",
                message_id=10,
                published="2026-08-24T10:00:01Z",
                available="2026-08-24T10:00:02Z",
            )
        )
        self._stage_market(
            market_event(
                11,
                source="XAUUSD",
                text="4631.20",
                message_id=11,
                published="2026-08-24T10:00:50Z",
                available="2026-08-24T10:00:51Z",
            )
        )
        self._project()
        retracted_before = self.market.execute(
            "SELECT inserted_at_utc FROM market_observations "
            "WHERE source_code='XAUUSD' AND price_value='4631.20'"
        ).fetchone()[0]
        self.assertEqual(
            [
                row["price_num"]
                for row in self.market.execute(
                    "SELECT price_num FROM market_observations "
                    "WHERE source_code='XAUUSD' AND quality_state='ELIGIBLE' "
                    "ORDER BY event_time_utc"
                ).fetchall()
            ],
            [4630.1, 4631.2],
        )
        self._stage_market(
            market_event(
                12,
                source="XAUUSD",
                text=None,
                event_type="message_deleted",
                message_id=11,
                published=None,
                available="2026-08-24T10:01:10Z",
            )
        )
        self._project()
        retracted_after = self.market.execute(
            "SELECT inserted_at_utc,quality_state FROM market_observations "
            "WHERE source_code='XAUUSD' AND price_value='4631.20'"
        ).fetchone()
        self.assertEqual(retracted_after["quality_state"], "REJECTED")
        self.assertNotEqual(retracted_after["inserted_at_utc"], retracted_before)
        row = self.market.execute(
            "SELECT price_num,event_time_utc FROM market_observations WHERE source_code='XAUUSD' AND quality_state='ELIGIBLE'"
        ).fetchone()
        self.assertEqual((row["price_num"], row["event_time_utc"]), (4630.1, "2026-08-24T10:00:01Z"))

    def test_non_model_edit_retracts_previous_public_market_fact(self) -> None:
        self._stage_market(
            market_event(
                15,
                source="XAUUSD",
                text="4630.10",
                message_id=15,
                published="2026-08-24T10:00:01Z",
                available="2026-08-24T10:00:02Z",
            )
        )
        self._project()
        self._stage_market(
            market_event(
                16,
                source="XAUUSD",
                text="",
                content_type="media_only",
                event_type="message_edited",
                message_id=15,
                published="2026-08-24T10:00:01Z",
                edited="2026-08-24T10:00:20Z",
                available="2026-08-24T10:00:21Z",
            )
        )
        lineage = self.staging.execute(
            "SELECT status,disposition_code FROM capture_event_lineage "
            "WHERE event_id=?",
            ("00000000-0000-7000-8000-000000000016",),
        ).fetchone()
        self.assertEqual(
            (str(lineage["status"]), str(lineage["disposition_code"])),
            ("FILTERED", "NON_MODEL_MEDIA_ONLY"),
        )
        self._project()
        self.assertEqual(
            self.market.execute(
                "SELECT quality_state FROM market_observations "
                "WHERE source_code='XAUUSD' AND price_value='4630.10'"
            ).fetchone()[0],
            "REJECTED",
        )

    def test_same_receipt_public_edit_marks_the_actual_current_revision_parsed(self) -> None:
        self._stage_market(
            market_event(
                999,
                source="XAUUSD",
                text="4630.10",
                message_id=17,
                published="2026-08-24T10:00:01Z",
                available="2026-08-24T10:00:30Z",
            )
        )
        self._stage_market(
            market_event(
                1,
                source="XAUUSD",
                text="4631.20",
                event_type="message_edited",
                message_id=17,
                published="2026-08-24T10:00:01Z",
                edited="2026-08-24T10:00:20Z",
                available="2026-08-24T10:00:30Z",
            )
        )
        self._project()
        rows = self.staging.execute(
            "SELECT event_id,status,disposition_code FROM capture_event_lineage "
            "WHERE source_id='XAUUSD' AND message_id=17 ORDER BY rowid"
        ).fetchall()
        self.assertEqual(
            [(str(row["status"]), str(row["disposition_code"])) for row in rows],
            [
                ("FILTERED", "SUPERSEDED_BY_NEWER_REVISION"),
                ("PARSED", "PARSER_EXECUTED"),
            ],
        )
        self.assertTrue(str(rows[1]["event_id"]).endswith("000000000001"))
        self.assertEqual(
            self.market.execute(
                "SELECT price_num FROM market_observations "
                "WHERE source_code='XAUUSD' AND quality_state='ELIGIBLE'"
            ).fetchone()[0],
            4631.2,
        )

    def test_same_receipt_private_revisions_keep_reverse_lexical_lineage(self) -> None:
        self._stage_market(
            market_event(
                999,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 5 تا بدون حواله",
                message_id=18,
                published="2026-08-24T10:00:01Z",
                available="2026-08-24T10:00:30Z",
            )
        )
        self._stage_market(
            market_event(
                1,
                source="MELTED_PRIMARY_FLOW",
                text="95,100,000 فروش 5 تا بدون حواله",
                event_type="message_edited",
                message_id=18,
                published="2026-08-24T10:00:01Z",
                edited="2026-08-24T10:00:20Z",
                available="2026-08-24T10:00:30Z",
            )
        )

        self._project()

        rows = self.staging.execute(
            "SELECT event_id,status,disposition_code FROM capture_event_lineage "
            "WHERE source_id='MELTED_PRIMARY_FLOW' AND message_id=18 ORDER BY rowid"
        ).fetchall()
        self.assertEqual(
            [(str(row["status"]), str(row["disposition_code"])) for row in rows],
            [("PARSED", "PARSER_EXECUTED"), ("PARSED", "PARSER_EXECUTED")],
        )
        self.assertTrue(str(rows[0]["event_id"]).endswith("000000000999"))
        self.assertTrue(str(rows[1]["event_id"]).endswith("000000000001"))
        self.assertEqual(
            self.staging.execute(
                "SELECT message_text FROM capture_market_messages "
                "WHERE source_id='MELTED_PRIMARY_FLOW' AND message_id=18"
            ).fetchone()[0],
            "95,100,000 فروش 5 تا بدون حواله",
        )

    def test_same_receipt_group_edit_marks_reverse_lexical_current_parsed(self) -> None:
        self._stage_group(
            group_event(
                999,
                text="امام فروش فردا 190000 / 5 تا",
                message_id=25,
                sender="owner00000000025",
                available="2026-08-24T10:00:30Z",
            )
        )
        self._stage_group(
            group_event(
                1,
                text="امام فروش فردا 190100 / 5 تا",
                event_type="message_edited",
                message_id=25,
                sender="owner00000000025",
                edited="2026-08-24T10:00:20Z",
                available="2026-08-24T10:00:30Z",
            )
        )

        self._project()

        rows = self.staging.execute(
            "SELECT event_id,status,disposition_code FROM capture_event_lineage "
            "WHERE source_id='GROUP_1' AND message_id=25 ORDER BY rowid"
        ).fetchall()
        self.assertEqual(
            [(str(row["status"]), str(row["disposition_code"])) for row in rows],
            [
                ("FILTERED", "SUPERSEDED_BY_NEWER_REVISION"),
                ("PARSED", "PARSER_EXECUTED"),
            ],
        )
        self.assertTrue(str(rows[0]["event_id"]).endswith("000000000999"))
        self.assertTrue(str(rows[1]["event_id"]).endswith("000000000001"))

    def test_market_projection_limit_does_not_starve_dirty_coin_groups(self) -> None:
        self._stage_group(
            group_event(
                1001,
                text="امام فروش فردا 190000 / 5 تا",
                message_id=1001,
                sender="owner00000001001",
            )
        )
        self.staging.executemany(
            "INSERT INTO capture_dirty_market_messages("
            "source_id,message_id,event_time_utc,available_at_utc) "
            "VALUES(?,?,?,?)",
            [
                ("USD_HERAT", message_id, "2026-08-24T09:59:00Z", available)
                for message_id, available in (
                    (2001, "2026-08-24T10:00:01Z"),
                    (2002, "2026-08-24T10:00:02Z"),
                    (2003, "2026-08-24T10:00:03Z"),
                )
            ],
        )
        self.staging.commit()

        report = project_capture_changes(
            self.staging,
            self.market,
            as_of_utc="2026-08-24T10:02:00Z",
            max_market_messages=1,
        )
        self.market.commit()
        self.staging.commit()

        self.assertEqual(report.market_messages_reprojected, 0)
        self.assertIsNotNone(report.group_pipeline)
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_dirty_market_messages"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_dirty_groups"
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(
            self.market.execute(
                "SELECT 1 FROM market_observations "
                "WHERE source_code='GROUP_1' AND event_type='OFFER'"
            ).fetchone()
        )

    def test_dirty_market_batches_use_causal_ready_index_and_upgrade_v8(self) -> None:
        self.staging.execute("DROP INDEX idx_capture_dirty_market_ready")
        self.staging.execute(
            "UPDATE capture_adapter_metadata SET schema_version=8 WHERE singleton=1"
        )
        self.staging.commit()

        initialize_capture_adapter(self.staging)

        self.assertEqual(
            self.staging.execute(
                "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
            ).fetchone()[0],
            CAPTURE_ADAPTER_SCHEMA_VERSION,
        )
        plan = self.staging.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM capture_dirty_market_messages "
            "WHERE available_at_utc<=? "
            "ORDER BY available_at_utc,source_id,message_id LIMIT ?",
            ("2026-08-24T10:02:00Z", 256),
        ).fetchall()
        self.assertTrue(
            any(
                "idx_capture_dirty_market_ready" in str(row[3])
                for row in plan
            ),
            [str(row[3]) for row in plan],
        )

    def test_market_projection_limit_rejects_non_positive_values(self) -> None:
        with self.assertRaisesRegex(
            CaptureEventContractError,
            "capture_market_projection_limit_invalid",
        ):
            project_capture_changes(
                self.staging,
                self.market,
                as_of_utc="2026-08-24T10:02:00Z",
                max_market_messages=0,
            )

    def test_never_exported_dependency_retraction_does_not_requeue(self) -> None:
        self._stage_market(
            market_event(
                13,
                source="XAUUSD",
                text="4630.10",
                message_id=13,
                published="2026-08-24T10:00:01Z",
                available="2026-08-24T10:00:02Z",
            )
        )
        self._project()
        row = self.market.execute(
            "SELECT event_key,inserted_at_utc FROM market_observations "
            "WHERE source_code='XAUUSD'"
        ).fetchone()
        initialize_export_ledger(self.market)
        self.market.execute(
            "INSERT INTO market_fact_export_ledger VALUES(?,?,?,?,?,?,?,?)",
            (
                row["event_key"],
                row["inserted_at_utc"],
                "REJECTED",
                None,
                None,
                "market_fact_projection_offer_dependency_missing",
                1,
                row["inserted_at_utc"],
            ),
        )
        self.market.commit()
        self._stage_market(
            market_event(
                14,
                source="XAUUSD",
                text=None,
                event_type="message_deleted",
                message_id=13,
                published=None,
                available="2026-08-24T10:02:01Z",
            )
        )
        self._project("2026-08-24T10:02:02Z")
        after = self.market.execute(
            "SELECT quality_state,inserted_at_utc FROM market_observations "
            "WHERE event_key=?",
            (row["event_key"],),
        ).fetchone()
        self.assertEqual(after["quality_state"], "REJECTED")
        self.assertEqual(after["inserted_at_utc"], row["inserted_at_utc"])

    def test_group_reply_chain_projects_trade_and_delete_retracts_it(self) -> None:
        self._stage_group(group_event(20, text="امام فروش فردا 190000 / 5 تا", message_id=1, sender="owner00000000001"))
        self._stage_group(
            group_event(
                21,
                text="ب5 تا189900",
                message_id=2,
                sender="buyer00000000001",
                reply_to=1,
                published="2026-08-24T10:00:02Z",
                available="2026-08-24T10:00:03Z",
            )
        )
        self._stage_group(
            group_event(
                22,
                text="برکت",
                message_id=3,
                sender="owner00000000001",
                reply_to=2,
                published="2026-08-24T10:00:04Z",
                available="2026-08-24T10:00:05Z",
            )
        )
        report = self._project()
        self.assertIsNotNone(report.group_pipeline)
        assert report.group_pipeline is not None
        self.assertEqual(report.group_pipeline.eligible_trades, 1)

        deleted = group_event(
            23,
            text=None,
            event_type="message_deleted",
            message_id=2,
            published=None,
            available="2026-08-24T10:00:10Z",
        )
        self._stage_group(deleted)
        self._project()
        trade = self.market.execute(
            "SELECT quality_state FROM market_observations WHERE source_code='GROUP_1' AND event_type='TRADE'"
        ).fetchone()
        self.assertEqual(trade["quality_state"], "REJECTED")

    def test_live_group_revisions_reach_terminal_lineage_after_projection(self) -> None:
        self._stage_group(
            group_event(
                24,
                text="امام فروش فردا 190000 / 5 تا",
                message_id=24,
                sender="owner00000000024",
                available="2026-08-24T10:00:30Z",
            )
        )
        self._stage_group(
            group_event(
                25,
                text="امام فروش فردا 190100 / 5 تا",
                event_type="message_edited",
                message_id=24,
                sender="owner00000000024",
                edited="2026-08-24T10:00:20Z",
                available="2026-08-24T10:00:30Z",
            )
        )
        pending = self.staging.execute(
            "SELECT status FROM capture_event_lineage WHERE source_id='GROUP_1' "
            "AND message_id=24 ORDER BY event_id"
        ).fetchall()
        self.assertEqual([str(row["status"]) for row in pending], ["PENDING", "PENDING"])

        self._project()

        terminal = self.staging.execute(
            "SELECT status,disposition_code,terminal_at_utc "
            "FROM capture_event_lineage WHERE source_id='GROUP_1' "
            "AND message_id=24 ORDER BY event_id"
        ).fetchall()
        self.assertEqual(
            [(str(row["status"]), str(row["disposition_code"])) for row in terminal],
            [
                ("FILTERED", "SUPERSEDED_BY_NEWER_REVISION"),
                ("PARSED", "PARSER_EXECUTED"),
            ],
        )
        self.assertTrue(all(row["terminal_at_utc"] is not None for row in terminal))

    def test_duplicate_event_is_idempotent(self) -> None:
        event = decode_market_channel_event(market_event(30, source="MELTED_FLOW", text="95,000,000 باحواله فروش"))
        first = stage_capture_event(self.staging, event)
        second = stage_capture_event(self.staging, event)
        self.assertTrue(first.accepted)
        self.assertTrue(second.duplicate)
        self.assertEqual(
            self.staging.execute("SELECT COUNT(*) FROM capture_market_messages").fetchone()[0],
            1,
        )

    def test_flow_batch_links_trade_when_prior_offer_arrives_late(self) -> None:
        self._stage_market(
            market_event(
                301,
                source="MELTED_FLOW",
                message_id=2,
                text="79,270,000⏳باحواله✅معامله",
                published="2026-08-24T10:00:30Z",
                available="2026-08-24T10:01:00Z",
            )
        )
        self._stage_market(
            market_event(
                302,
                source="MELTED_FLOW",
                message_id=1,
                text="79,270,000⏳باحواله🔵خرید",
                published="2026-08-24T10:00:00Z",
                available="2026-08-24T10:01:01Z",
            )
        )
        report = self._project("2026-08-24T10:02:00Z")
        row = self.market.execute(
            "SELECT side,parser_version FROM market_observations "
            "WHERE event_type='TRADE'"
        ).fetchone()
        self.assertEqual(report.market_messages_reprojected, 2)
        self.assertEqual(row["side"], "BUY")
        self.assertIn("+offer-link-v1", row["parser_version"])

    def test_duplicate_delivery_keeps_first_receipt_and_skips_reprojection(self) -> None:
        first = decode_market_channel_event(
            market_event(31, source="MELTED_FLOW", text="95,000,000 باحواله فروش")
        )
        repeated = decode_market_channel_event(
            market_event(
                32,
                source="MELTED_FLOW",
                text="95,000,000 باحواله فروش",
                available="2026-08-24T10:10:00Z",
            )
        )
        self.assertTrue(stage_capture_event(self.staging, first).staged_change)
        report = stage_capture_event(self.staging, repeated)
        self.assertTrue(report.accepted)
        self.assertFalse(report.staged_change)
        row = self.staging.execute(
            "SELECT available_at_utc,revision FROM capture_market_messages"
        ).fetchone()
        self.assertEqual(row["available_at_utc"], "2026-08-24T10:00:01Z")
        self.assertEqual(row["revision"], 1)

    def test_schema_v3_is_upgraded_with_revision_and_deadline_state(self) -> None:
        self._stage_market(
            market_event(33, source="MELTED_PRIMARY_FLOW", text="80,000,000 خرید 5 تا با حواله")
        )
        self.staging.execute(
            "UPDATE capture_adapter_metadata SET schema_version=3 WHERE singleton=1"
        )
        self.staging.execute("DROP TABLE capture_market_message_revisions")
        self.staging.execute("DROP TABLE capture_primary_trade_deadlines")
        self.staging.execute(
            "CREATE TABLE capture_market_messages_v3 AS "
            "SELECT source_id,message_id,event_time_utc,available_at_utc,edited_at_utc,"
            "parser_profile,is_forwarded,message_text,content_digest,revision,expires_at_utc "
            "FROM capture_market_messages"
        )
        self.staging.execute("DROP TABLE capture_market_messages")
        self.staging.execute(
            "ALTER TABLE capture_market_messages_v3 RENAME TO capture_market_messages"
        )
        self.staging.commit()
        initialize_capture_adapter(self.staging)
        row = self.staging.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
        self.assertEqual(row["schema_version"], CAPTURE_ADAPTER_SCHEMA_VERSION)
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_market_message_revisions"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_primary_trade_deadlines"
            ).fetchone()[0],
            1,
        )

    def test_schema_v5_schedules_private_and_targeted_group_repair(self) -> None:
        self._stage_market(
            market_event(
                40,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 5 تا بدون حواله",
                message_id=40,
            )
        )
        self._project("2026-08-24T10:00:10Z")
        self.staging.execute(
            """
            UPDATE capture_primary_trade_outcomes
            SET status='FULL',finalized_at_utc='2026-08-24T10:00:20Z',
                executed_quantity=5,remaining_quantity=0,
                evidence_event_id='fixture-evidence'
            WHERE source_id='MELTED_PRIMARY_FLOW' AND message_id=40
            """
        )
        self._stage_group(
            group_event(
                41,
                text="امام فروش فردا 190000 / 5 تا",
                message_id=41,
                sender="owner00000000041",
            )
        )
        self.staging.execute("DELETE FROM capture_dirty_market_messages")
        self.staging.execute("DELETE FROM capture_dirty_groups")
        self.staging.execute(
            "UPDATE capture_adapter_metadata SET schema_version=5 WHERE singleton=1"
        )
        self.staging.commit()

        initialize_capture_adapter(self.staging)

        self.assertEqual(
            self.staging.execute(
                "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
            ).fetchone()[0],
            CAPTURE_ADAPTER_SCHEMA_VERSION,
        )
        self.assertEqual(
            self.staging.execute(
                "SELECT message_id FROM capture_dirty_market_messages"
            ).fetchone()[0],
            40,
        )
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_dirty_groups"
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(
            self.staging.execute(
                "SELECT 1 FROM capture_projection_reconciliations "
                "WHERE completed_at_utc IS NULL"
            ).fetchone()
        )

        report = self._project("2026-08-24T20:00:00Z")
        self.assertIsNotNone(report.group_pipeline)
        assert report.group_pipeline is not None
        self.assertEqual(report.group_pipeline.staged_messages_seen, 1)
        self.assertIsNotNone(
            self.market.execute(
                "SELECT 1 FROM market_observations "
                "WHERE source_code='GROUP_1' AND event_type='OFFER'"
            ).fetchone()
        )
        self.assertIsNotNone(
            self.staging.execute(
                "SELECT 1 FROM capture_projection_reconciliations "
                "WHERE completed_at_utc IS NOT NULL"
            ).fetchone()
        )

    def test_primary_partial_trade_finalizes_at_deadline_without_new_event(self) -> None:
        self._stage_market(
            market_event(
                50,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 10 تا بدون حواله",
                message_id=50,
            )
        )
        self._project("2026-08-24T10:00:10Z")
        self._stage_market(
            market_event(
                51,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 10 تا بدون حواله باقی 6",
                event_type="message_edited",
                message_id=50,
                edited="2026-08-24T10:00:40Z",
                available="2026-08-24T10:00:41Z",
            )
        )
        pending = self._project("2026-08-24T10:01:00Z")
        self.assertEqual(pending.private_trade_facts_upserted, 0)
        final = self._project("2026-08-24T10:02:01Z")
        self.assertEqual(final.private_trade_facts_upserted, 1)
        row = self.market.execute(
            "SELECT quantity_num,parser_version,available_at_utc FROM market_observations "
            "WHERE source_code='PRIVATE_GOLD_CHANNEL' AND event_type='TRADE' "
            "AND quality_state='ELIGIBLE'"
        ).fetchone()
        self.assertEqual(row["quantity_num"], 4.0)
        self.assertEqual(row["parser_version"], "private-gold-trade-revisions-v1")
        self.assertEqual(row["available_at_utc"], "2026-08-24T10:02:01Z")

    def test_primary_no_trade_closure_overrides_tentative_partial(self) -> None:
        self._stage_market(
            market_event(
                60,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 10 تا بدون حواله",
                message_id=60,
            )
        )
        self._stage_market(
            market_event(
                61,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 10 تا بدون حواله باقی 6",
                event_type="message_edited",
                message_id=60,
                edited="2026-08-24T10:00:30Z",
                available="2026-08-24T10:00:31Z",
            )
        )
        self._stage_market(
            market_event(
                62,
                source="MELTED_PRIMARY_FLOW",
                text="95,000,000 فروش 10 تا بدون حواله ✅",
                event_type="message_edited",
                message_id=60,
                edited="2026-08-24T10:01:30Z",
                available="2026-08-24T10:01:31Z",
            )
        )
        report = self._project("2026-08-24T10:01:31Z")
        self.assertEqual(report.private_trade_facts_upserted, 0)
        self.assertEqual(
            self.market.execute(
                "SELECT COUNT(*) FROM market_observations "
                "WHERE source_code='PRIVATE_GOLD_CHANNEL' AND event_type='TRADE' "
                "AND quality_state='ELIGIBLE'"
            ).fetchone()[0],
            0,
        )

    def test_entity_only_revision_is_retained_but_not_a_trade(self) -> None:
        text = "95,000,000 فروش 10 تا بدون حواله"
        self._stage_market(
            market_event(70, source="MELTED_PRIMARY_FLOW", text=text, message_id=70)
        )
        report = stage_capture_event(
            self.staging,
            decode_market_channel_event(
                market_event(
                    71,
                    source="MELTED_PRIMARY_FLOW",
                    text=text,
                    event_type="message_edited",
                    message_id=70,
                    edited="2026-08-24T10:00:20Z",
                    available="2026-08-24T10:00:21Z",
                    entities=[
                        {
                            "type": "MessageEntityBold",
                            "offset_utf16": 0,
                            "length_utf16": 2,
                        }
                    ],
                )
            ),
        )
        self.assertTrue(report.staged_change)
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_market_message_revisions "
                "WHERE source_id='MELTED_PRIMARY_FLOW' AND message_id=70"
            ).fetchone()[0],
            2,
        )
        projection = self._project("2026-08-24T10:02:01Z")
        self.assertEqual(projection.private_trade_facts_upserted, 0)

    def test_old_market_backfill_is_ignored_but_recent_backfill_is_staged(self) -> None:
        old = stage_capture_event(
            self.staging,
            decode_market_channel_event(
                market_event(
                    34,
                    source="MELTED_FLOW",
                    text="95,000,000 باحواله فروش",
                    published="2026-08-24T01:00:00Z",
                    available="2026-08-24T10:00:00Z",
                    is_backfill=True,
                )
            ),
        )
        recent = stage_capture_event(
            self.staging,
            decode_market_channel_event(
                market_event(
                    35,
                    source="MELTED_FLOW",
                    text="95,000,000 باحواله فروش",
                    message_id=2,
                    published="2026-08-24T09:50:00Z",
                    available="2026-08-24T10:00:00Z",
                    is_backfill=True,
                )
            ),
        )
        self.assertFalse(old.staged_change)
        self.assertTrue(recent.staged_change)
        self.assertEqual(
            self.staging.execute("SELECT COUNT(*) FROM capture_market_messages").fetchone()[0],
            1,
        )

    def test_deleting_oldest_group_message_retracts_its_fact(self) -> None:
        self._stage_group(
            group_event(
                40,
                text="امام فروش فردا 190000 / 5 تا",
                message_id=40,
                published="2026-08-24T10:00:00Z",
                available="2026-08-24T10:00:01Z",
            )
        )
        self._stage_group(
            group_event(
                41,
                text="ربع بهار فروش فردا 52000 / 5 تا",
                message_id=41,
                published="2026-08-24T10:10:00Z",
                available="2026-08-24T10:10:01Z",
            )
        )
        self._project(at="2026-08-24T10:10:02Z")
        self._stage_group(
            group_event(
                42,
                text=None,
                event_type="message_deleted",
                message_id=40,
                published=None,
                available="2026-08-24T10:11:00Z",
            )
        )
        self._project(at="2026-08-24T10:11:01Z")
        state = self.market.execute(
            "SELECT quality_state FROM market_observations WHERE event_key=?",
            (derive_event_key("coin-group-offer-v1", 1, 40, 0),),
        ).fetchone()
        self.assertIsNotNone(state)
        self.assertEqual(state["quality_state"], "REJECTED")

    def test_group_reconciliation_is_bounded_but_recent_backfill_is_accepted(self) -> None:
        self._stage_group(
            group_event(
                50,
                text="امام فروش فردا 190000 / 5 تا",
                message_id=50,
                published="2026-08-24T04:00:00Z",
                available="2026-08-24T12:00:00Z",
                is_backfill=True,
            )
        )
        self._stage_group(
            group_event(
                51,
                text="ربع بهار فروش فردا 52000 / 5 تا",
                message_id=51,
                published="2026-08-24T07:00:01Z",
                available="2026-08-24T12:00:00Z",
                is_backfill=True,
            )
        )
        report = self._project(at="2026-08-24T12:00:01Z")
        assert report.group_pipeline is not None
        self.assertEqual(report.group_pipeline.staged_messages_seen, 1)
        old_state = self.market.execute(
            "SELECT COUNT(*) FROM market_observations WHERE event_key=?",
            (derive_event_key("coin-group-offer-v1", 1, 50, 0),),
        ).fetchone()[0]
        recent_state = self.market.execute(
            "SELECT quality_state FROM market_observations WHERE event_key=?",
            (derive_event_key("coin-group-offer-v1", 1, 51, 0),),
        ).fetchone()
        self.assertEqual(old_state, 0)
        self.assertEqual(recent_state["quality_state"], "ELIGIBLE")

if __name__ == "__main__":
    unittest.main()
