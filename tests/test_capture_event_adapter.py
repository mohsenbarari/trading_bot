"""Contract, reconciliation, and privacy tests for new capture spools."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.capture_event_adapter import (
    CaptureEventContractError,
    decode_coin_group_event,
    decode_market_channel_event,
    initialize_capture_adapter,
    project_capture_changes,
    stage_capture_event,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from core.market_intelligence.market_contracts import derive_event_key
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
) -> dict[str, object]:
    message = {
        "message_id": str(message_id),
        "published_at_utc": published,
        "edited_at_utc": edited,
        "text": text,
        "text_sha256": sha256(text.encode("utf-8")).hexdigest() if text is not None else None,
        "is_forwarded": False,
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
            "edited_at_utc": None,
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
        self.assertEqual([(row["event_type"], row["price_num"]) for row in rows], [("OFFER", 95_100_000.0)])

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
            0,
        )

    def test_xau_delete_restores_previous_current_minute_quote(self) -> None:
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
        self.assertEqual(
            self.market.execute(
                "SELECT price_num FROM market_observations WHERE source_code='XAUUSD' AND quality_state='ELIGIBLE'"
            ).fetchone()[0],
            4631.2,
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
        row = self.market.execute(
            "SELECT price_num,event_time_utc FROM market_observations WHERE source_code='XAUUSD' AND quality_state='ELIGIBLE'"
        ).fetchone()
        self.assertEqual((row["price_num"], row["event_time_utc"]), (4630.1, "2026-08-24T10:00:01Z"))

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

    def test_schema_v2_digest_is_recomputed_without_reprojection(self) -> None:
        self._stage_market(
            market_event(33, source="MELTED_PRIMARY_FLOW", text="80,000,000 خرید 5 تا با حواله")
        )
        self.staging.execute(
            "UPDATE capture_market_messages SET content_digest=?",
            (b"x" * 32,),
        )
        self.staging.execute(
            "UPDATE capture_adapter_metadata SET schema_version=2 WHERE singleton=1"
        )
        self.staging.commit()
        initialize_capture_adapter(self.staging)
        row = self.staging.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
        digest = self.staging.execute(
            "SELECT content_digest FROM capture_market_messages"
        ).fetchone()[0]
        self.assertEqual(row["schema_version"], 3)
        self.assertNotEqual(bytes(digest), b"x" * 32)

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
