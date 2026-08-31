from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.coin_rate_engine import build_coin_rate_estimates
from core.market_intelligence.market_fact_adapter import (
    MarketFactAdapterError,
    adapter_metrics,
    initialize_adapter_store,
    normalize_feed_mode,
    publish_adapter_watermark,
    run_adapter_cycle,
    select_estimator_feeds,
)
from core.market_intelligence.market_fact_receiver import (
    apply_fact_batch,
    compact_consumed_payloads,
    connect_receiver,
)
from core.market_intelligence.market_input_materializer import materialize_input_snapshot
from core.market_intelligence.market_store import connect_market_store
from core.market_intelligence.private_pipeline_contracts import content_hash
from core.market_intelligence.snapshot_publisher import publish_rate_ready_snapshot
from scripts import audit_production_market_catchup as catchup_audit


AT = "2026-08-26T05:00:00Z"
AVAILABLE = "2026-08-26T05:00:01Z"
PERSISTED = "2026-08-26T05:00:02Z"


def _fact(
    number: int,
    *,
    source_code: str,
    stream_id: str,
    source_sequence: int,
    payload: dict[str, object],
    quality_state: str = "ELIGIBLE",
    occurred_at_utc: str = AT,
    available_at_utc: str = AVAILABLE,
    persisted_at_utc: str = PERSISTED,
) -> dict[str, object]:
    return {
        "contract": "market_fact/1.0",
        "fact_id": f"{number:064x}",
        "event_key": f"{number + 10_000:064x}",
        "origin_event_key": f"{number + 20_000:064x}",
        "source_code": source_code,
        "stream_id": stream_id,
        "source_sequence": source_sequence,
        "occurred_at_utc": occurred_at_utc,
        "available_at_utc": available_at_utc,
        "persisted_at_utc": persisted_at_utc,
        "schema_version": "1.0",
        "parser_version": "stage9-fixture-v1",
        "fact_revision": 1,
        "quality_state": quality_state,
        "quality_reason_codes": [],
        "payload_hash": content_hash(payload),
        "payload": payload,
    }


def _batch(
    number: int,
    *,
    stream_id: str,
    deliveries: list[tuple[int, dict[str, object]]],
) -> dict[str, object]:
    items = [
        {"delivery_sequence": sequence, "fact": fact}
        for sequence, fact in deliveries
    ]
    return {
        "contract": "market_fact_batch/1.0",
        "batch_id": f"{number:064x}",
        "schema_version": "1.0",
        "stream_id": stream_id,
        "first_sequence": deliveries[0][0],
        "last_sequence": deliveries[-1][0],
        "created_at_utc": "2026-08-26T05:00:03Z",
        "item_count": len(items),
        "items_hash": content_hash(items),
        "sender_instance_id": "stage9-fixture-1",
        "items": items,
    }


class Stage9AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.receiver = connect_receiver(root / "receiver.sqlite3")
        self.market_path = root / "market.sqlite3"
        self.market = connect_market_store(self.market_path)
        initialize_adapter_store(self.market)
        self.market_clock = patch(
            "core.market_intelligence.market_store._utc_now",
            return_value="2026-08-26T05:00:04.000000Z",
        )
        self.market_clock.start()

    def tearDown(self) -> None:
        self.market_clock.stop()
        self.market.close()
        self.receiver.close()
        self.directory.cleanup()

    def _receive(self, batch: dict[str, object]) -> None:
        status, response = apply_fact_batch(self.receiver, batch)
        self.assertEqual((status, response.get("status")), (200, "ACK"))

    def test_projection_event_key_lookup_has_a_durable_index(self):
        indexes = {
            str(row[1])
            for row in self.market.execute(
                "PRAGMA index_list(private_fact_adapter_projections)"
            )
        }
        self.assertIn(
            "private_fact_adapter_projections_event_key_idx",
            indexes,
        )
        columns = [
            str(row[2])
            for row in self.market.execute(
                "PRAGMA index_info(private_fact_adapter_projections_event_key_idx)"
            )
        ]
        self.assertEqual(columns, ["event_key"])

    def test_compaction_watermark_is_durable_and_never_regresses(self):
        path = Path(self.directory.name) / "adapter-watermark.json"
        checkpoints = {"market.fact.coin.group.1": 2}
        self.assertTrue(publish_adapter_watermark(path, checkpoints))
        self.assertFalse(publish_adapter_watermark(path, checkpoints))
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["streams"], checkpoints)
        self.assertEqual(
            document["schema"],
            "market_fact_adapter_watermark/1.0",
        )
        with self.assertRaisesRegex(
            MarketFactAdapterError,
            "market_fact_adapter_watermark_regression",
        ):
            publish_adapter_watermark(
                path,
                {"market.fact.coin.group.1": 1},
            )

    def test_adapter_fails_closed_if_payload_was_compacted_ahead_of_store(self):
        stream = "market.fact.coin.group.1"
        offer = _fact(
            991,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=1,
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "SELL",
                "settlement": "CASH",
                "trade_form": "PHYSICAL",
                "offered_price_value": "187500",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "1",
                "quantity_unit": "COIN_COUNT",
            },
        )
        self._receive(_batch(991, stream_id=stream, deliveries=[(1, offer)]))
        self.receiver.execute(
            "UPDATE fact_deliveries SET received_at_utc=?",
            ("2026-08-20T00:00:00Z",),
        )
        compact_consumed_payloads(
            self.receiver,
            applied_checkpoints={stream: 1},
            now=datetime(2026, 8, 27, tzinfo=timezone.utc),
            retention_seconds=3_600,
        )
        with self.assertRaisesRegex(
            MarketFactAdapterError,
            "market_fact_adapter_payload_compacted_before_checkpoint",
        ):
            run_adapter_cycle(self.receiver, self.market)

    def test_coin_trade_uses_root_dimensions_and_exact_negotiated_terms(self):
        stream = "market.fact.coin.group.1"
        offer = _fact(
            1,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=1,
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "SELL",
                "settlement": "CASH",
                "trade_form": "PHYSICAL",
                "offered_price_value": "187500",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "5",
                "quantity_unit": "COIN_COUNT",
            },
        )
        trade = _fact(
            2,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=2,
            payload={
                "kind": "COIN_TRADE",
                "offer_fact_id": offer["fact_id"],
                "outcome": "CONFIRMED_PARTIAL",
                "agreed_price_value": "187300",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "agreed_quantity_value": "2",
                "quantity_unit": "COIN_COUNT",
            },
        )
        self._receive(_batch(1, stream_id=stream, deliveries=[(1, offer), (2, trade)]))
        report = run_adapter_cycle(self.receiver, self.market)
        self.assertEqual((report.applied, report.rejected), (2, 0))
        rows = self.market.execute(
            "SELECT event_type,instrument,settlement_term,price_value,quantity_value "
            "FROM market_observations ORDER BY event_type"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        trade_row = next(row for row in rows if row["event_type"] == "TRADE")
        self.assertEqual(str(trade_row["instrument"]), "COIN_IMAM")
        self.assertEqual(str(trade_row["settlement_term"]), "CASH")
        self.assertEqual(str(trade_row["price_value"]), "187300")
        self.assertEqual(str(trade_row["quantity_value"]), "2")

    def test_applied_trade_revised_to_audit_only_retires_prior_observation(self):
        stream = "market.fact.coin.group.1"
        offer = _fact(
            201,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=1,
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "SELL",
                "settlement": "CASH",
                "trade_form": "PHYSICAL",
                "offered_price_value": "187500",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "5",
                "quantity_unit": "COIN_COUNT",
            },
        )
        trade = _fact(
            202,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=2,
            payload={
                "kind": "COIN_TRADE",
                "offer_fact_id": offer["fact_id"],
                "outcome": "CONFIRMED_PARTIAL",
                "agreed_price_value": "187300",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "agreed_quantity_value": "2",
                "quantity_unit": "COIN_COUNT",
            },
        )
        self._receive(
            _batch(201, stream_id=stream, deliveries=[(1, offer), (2, trade)])
        )
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).applied, 2)

        revised = json.loads(json.dumps(trade))
        revised["fact_revision"] = 2
        revised["quality_state"] = "REJECTED"
        revised["quality_reason_codes"] = ["NO_LONGER_CURRENT"]
        revised["payload"] = {
            "kind": "COIN_TRADE",
            "offer_fact_id": offer["fact_id"],
            "outcome": "REJECTED",
            "agreed_price_value": None,
            "price_unit": None,
            "agreed_quantity_value": None,
            "quantity_unit": None,
        }
        revised["payload_hash"] = content_hash(revised["payload"])
        self._receive(_batch(202, stream_id=stream, deliveries=[(3, revised)]))
        report = run_adapter_cycle(self.receiver, self.market)
        self.assertEqual((report.audit_only, report.rejected), (1, 0))

        observation = self.market.execute(
            "SELECT quality_state,parse_confidence,attributes_json "
            "FROM market_observations WHERE event_key=?",
            (bytes.fromhex(str(trade["event_key"])),),
        ).fetchone()
        self.assertIsNotNone(observation)
        self.assertEqual(str(observation["quality_state"]), "IGNORED")
        self.assertEqual(float(observation["parse_confidence"]), 0.0)
        attributes = json.loads(str(observation["attributes_json"]))
        self.assertEqual(attributes["fact_revision"], 2)
        self.assertEqual(attributes["adapter_disposition"], "AUDIT_ONLY")
        projection = self.market.execute(
            "SELECT fact_revision,status FROM private_fact_adapter_projections "
            "WHERE fact_id=?",
            (str(trade["fact_id"]),),
        ).fetchone()
        self.assertEqual((int(projection[0]), str(projection[1])), (2, "AUDIT_ONLY"))
        history = self.market.execute(
            "SELECT fact_revision,status FROM "
            "private_fact_adapter_projection_revisions WHERE fact_id=? "
            "ORDER BY fact_revision",
            (str(trade["fact_id"]),),
        ).fetchall()
        self.assertEqual(
            [(int(row[0]), str(row[1])) for row in history],
            [(1, "APPLIED"), (2, "AUDIT_ONLY")],
        )
        revision_lineage = catchup_audit._receiver_revision_rows(
            self.receiver, self.market
        )
        self.assertEqual(len(revision_lineage["GROUP_1"]), 3)
        self.assertEqual(
            [
                (row[3], row[7])
                for row in revision_lineage["GROUP_1"]
                if row[0] == str(trade["fact_id"])
            ],
            [("1", "ELIGIBLE"), ("2", "REJECTED")],
        )

        revised_again = json.loads(json.dumps(revised))
        revised_again["fact_revision"] = 3
        revised_again["quality_reason_codes"] = ["STILL_NOT_CURRENT"]
        self._receive(
            _batch(203, stream_id=stream, deliveries=[(4, revised_again)])
        )
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).audit_only, 1)
        refreshed = self.market.execute(
            "SELECT attributes_json FROM market_observations WHERE event_key=?",
            (bytes.fromhex(str(trade["event_key"])),),
        ).fetchone()
        self.assertEqual(
            json.loads(str(refreshed["attributes_json"]))["fact_revision"],
            3,
        )
        revision_lineage = catchup_audit._receiver_revision_rows(
            self.receiver, self.market
        )
        self.assertEqual(
            [
                (row[3], row[7])
                for row in revision_lineage["GROUP_1"]
                if row[0] == str(trade["fact_id"])
            ],
            [("1", "ELIGIBLE"), ("2", "REJECTED"), ("3", "REJECTED")],
        )

        self.receiver.execute(
            "UPDATE fact_deliveries SET received_at_utc=?",
            ("2026-08-20T00:00:00Z",),
        )
        compact_consumed_payloads(
            self.receiver,
            applied_checkpoints={stream: 4},
            now=datetime(2026, 8, 27, tzinfo=timezone.utc),
            retention_seconds=3_600,
        )
        compacted_lineage = catchup_audit._receiver_revision_rows(
            self.receiver, self.market
        )
        self.assertEqual(compacted_lineage, revision_lineage)

    def test_catchup_revision_audit_ignores_non_target_component_stream(self):
        stream = "market.fact.private-gold-minute"
        fact = _fact(
            1199,
            source_code="PRIVATE_GOLD_PAPER_MINUTE",
            stream_id=stream,
            source_sequence=1,
            payload={
                "kind": "OBSERVATION",
                "instrument": "MELTED_GOLD_PRIVATE",
                "event_type": "QUOTE",
                "side": "MID",
                "settlement": "TOMORROW",
                "trade_form": "PAPER_NORMAL",
                "price_value": "120000000",
                "price_unit": "TOMAN_PER_MESGHAL_750",
                "currency": "TOMAN",
                "quantity_value": None,
                "quantity_unit": None,
            },
        )
        self._receive(_batch(1199, stream_id=stream, deliveries=[(1, fact)]))
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).applied, 1)

        lineage = catchup_audit._receiver_revision_rows(
            self.receiver, self.market
        )

        self.assertTrue(all(not rows for rows in lineage.values()))

    def test_catchup_revision_audit_includes_pre_cutoff_revision_of_fact(self):
        stream = "market.fact.coin.group.1"
        fact = _fact(
            1200,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=1,
            occurred_at_utc="2026-08-25T08:00:00Z",
            available_at_utc="2026-08-25T08:00:01Z",
            persisted_at_utc="2026-08-25T08:00:02Z",
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "SELL",
                "settlement": "TOMORROW",
                "trade_form": "PHYSICAL",
                "offered_price_value": "216000",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "5",
                "quantity_unit": "COIN_COUNT",
            },
        )
        self._receive(_batch(1200, stream_id=stream, deliveries=[(1, fact)]))
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).applied, 1)
        revised = json.loads(json.dumps(fact))
        revised["fact_revision"] = 2
        revised["available_at_utc"] = "2026-08-25T10:00:01Z"
        revised["persisted_at_utc"] = "2026-08-25T10:00:02Z"
        revised["quality_state"] = "REJECTED"
        revised["quality_reason_codes"] = ["NO_LONGER_CURRENT"]
        self._receive(_batch(1201, stream_id=stream, deliveries=[(2, revised)]))
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).selected, 1)

        lineage = catchup_audit._receiver_revision_rows(
            self.receiver, self.market
        )

        self.assertEqual(
            [row[3] for row in lineage["GROUP_1"] if row[0] == fact["fact_id"]],
            ["1", "2"],
        )

    def test_upgrade_retires_observation_left_eligible_by_old_adapter(self):
        stream = "market.fact.coin.group.1"
        offer = _fact(
            1201,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=1,
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "SELL",
                "settlement": "TOMORROW",
                "trade_form": "PHYSICAL",
                "offered_price_value": "216000",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "5",
                "quantity_unit": "COIN_COUNT",
            },
        )
        trade = _fact(
            1202,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=2,
            payload={
                "kind": "COIN_TRADE",
                "offer_fact_id": offer["fact_id"],
                "outcome": "CONFIRMED_PARTIAL",
                "agreed_price_value": "216000",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "agreed_quantity_value": "2",
                "quantity_unit": "COIN_COUNT",
            },
        )
        self._receive(
            _batch(1201, stream_id=stream, deliveries=[(1, offer), (2, trade)])
        )
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).applied, 2)

        # Reproduce the durable state written by an adapter release that
        # advanced lineage to AUDIT_ONLY but did not retire its old row.
        self.market.execute(
            "UPDATE private_fact_adapter_projections "
            "SET fact_revision=2,status='AUDIT_ONLY',quality_state='REJECTED',"
            "available_at_utc=? WHERE fact_id=?",
            ("2026-08-26T05:01:00Z", str(trade["fact_id"])),
        )
        self.market.execute(
            """
            INSERT INTO private_fact_adapter_projection_revisions(
              fact_id,fact_revision,stream_id,source_sequence,delivery_sequence,
              event_key,payload_hash,quality_state,envelope_hash,status,
              occurred_at_utc,available_at_utc,parsed_at_utc,
              transferred_at_utc,adapted_at_utc
            )
            SELECT fact_id,2,stream_id,source_sequence,1002,event_key,
                   payload_hash,'REJECTED',envelope_hash,'AUDIT_ONLY',
                   occurred_at_utc,'2026-08-26T05:01:00Z',parsed_at_utc,
                   transferred_at_utc,'2026-08-26T05:01:01Z'
            FROM private_fact_adapter_projections WHERE fact_id=?
            """,
            (str(trade["fact_id"]),),
        )
        self.market.execute(
            "DELETE FROM private_fact_adapter_migrations "
            "WHERE migration_code='retire-stale-audit-only-observations-v2'"
        )
        self.market.commit()

        report = run_adapter_cycle(self.receiver, self.market)
        self.assertEqual(report.selected, 0)
        observation = self.market.execute(
            "SELECT quality_state,parse_confidence,attributes_json "
            "FROM market_observations WHERE event_key=?",
            (bytes.fromhex(str(trade["event_key"])),),
        ).fetchone()
        self.assertIsNotNone(observation)
        self.assertEqual((str(observation[0]), float(observation[1])), ("IGNORED", 0.0))
        attributes = json.loads(str(observation[2]))
        self.assertEqual(attributes["fact_revision"], 2)
        self.assertEqual(attributes["delivery_sequence"], 1002)
        self.assertEqual(attributes["adapter_disposition"], "AUDIT_ONLY")
        self.assertEqual(
            attributes["resolution_reason"], "FACT_REVISION_AUDIT_ONLY"
        )

    def test_restart_and_revision_are_idempotent_without_unit_conversion(self):
        stream = "market.fact.coin.group.1"
        offer = _fact(
            10,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=1,
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "BUY",
                "settlement": "CASH",
                "trade_form": "PHYSICAL",
                "offered_price_value": "187500",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "3",
                "quantity_unit": "COIN_COUNT",
            },
        )
        self._receive(_batch(10, stream_id=stream, deliveries=[(1, offer)]))
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).applied, 1)
        row = self.market.execute(
            "SELECT price_value,price_unit FROM market_observations"
        ).fetchone()
        self.assertEqual((str(row[0]), str(row[1])), ("187500", "PROJECT_THOUSAND_TOMAN"))

        revised = json.loads(json.dumps(offer))
        revised["fact_revision"] = 2
        revised["payload"]["offered_price_value"] = "187600"
        revised["payload_hash"] = content_hash(revised["payload"])
        self._receive(_batch(11, stream_id=stream, deliveries=[(2, revised)]))
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).applied, 1)
        self.assertEqual(
            self.market.execute("SELECT price_value FROM market_observations").fetchone()[0],
            "187600",
        )
        self.assertEqual(
            self.market.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0],
            1,
        )

        self.market.close()
        self.market = connect_market_store(self.market_path)
        initialize_adapter_store(self.market)
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).selected, 0)
        projection = self.market.execute(
            "SELECT fact_revision,event_key FROM private_fact_adapter_projections"
        ).fetchone()
        self.assertEqual(int(projection["fact_revision"]), 2)
        self.assertEqual(bytes(projection["event_key"]).hex(), str(offer["event_key"]))

    def test_adapter_metrics_use_durable_insert_counts_and_reconcile_on_restart(self):
        stream = "market.fact.coin.group.1"
        offer = _fact(
            12,
            source_code="GROUP_1",
            stream_id=stream,
            source_sequence=1,
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "BUY",
                "settlement": "CASH",
                "trade_form": "PHYSICAL",
                "offered_price_value": "187500",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "1",
                "quantity_unit": "COIN_COUNT",
            },
        )
        self._receive(_batch(12, stream_id=stream, deliveries=[(1, offer)]))
        self.assertEqual(run_adapter_cycle(self.receiver, self.market).applied, 1)
        self.assertEqual(adapter_metrics(self.market)["applied_count"], 1)

        # Simulate an older store whose ledger existed before the trigger.
        self.market.execute(
            "UPDATE private_fact_adapter_status_counts SET delivery_count=0"
        )
        self.market.commit()
        initialize_adapter_store(self.market)
        statements: list[str] = []
        self.market.set_trace_callback(statements.append)
        metrics = adapter_metrics(self.market)
        self.market.set_trace_callback(None)
        self.assertEqual(metrics["applied_count"], 1)
        self.assertEqual(metrics["rejected_count"], 0)
        self.assertFalse(
            any(
                "COUNT(*)" in statement
                and "private_fact_adapter_deliveries" in statement
                for statement in statements
            )
        )

    def test_malformed_unit_is_rejected_and_next_fact_advances(self):
        stream = "market.fact.melted-aggregate"
        invalid = _fact(
            20,
            source_code="MELTED_AGGREGATE",
            stream_id=stream,
            source_sequence=1,
            payload={
                "kind": "OBSERVATION",
                "instrument": "MELTED_GOLD_AGGREGATE",
                "event_type": "OFFER",
                "side": "BUY",
                "settlement": "TODAY",
                "trade_form": "PHYSICAL",
                "price_value": "80000000",
                "price_unit": "TOMAN_PER_USD",
                "currency": "TOMAN",
                "quantity_value": None,
                "quantity_unit": None,
            },
        )
        healthy = _fact(
            21,
            source_code="MELTED_AGGREGATE",
            stream_id=stream,
            source_sequence=2,
            payload={
                "kind": "OBSERVATION",
                "instrument": "MELTED_GOLD_AGGREGATE",
                "event_type": "OFFER",
                "side": "BUY",
                "settlement": "TODAY",
                "trade_form": "PHYSICAL",
                "price_value": "80000000",
                "price_unit": "TOMAN_PER_MESGHAL_750",
                "currency": "TOMAN",
                "quantity_value": None,
                "quantity_unit": None,
            },
        )
        self._receive(
            _batch(20, stream_id=stream, deliveries=[(1, invalid), (2, healthy)])
        )
        report = run_adapter_cycle(self.receiver, self.market)
        self.assertEqual((report.rejected, report.applied), (1, 1))
        self.assertEqual(
            self.market.execute(
                "SELECT highest_delivery_sequence FROM private_fact_adapter_checkpoints"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.market.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0],
            1,
        )

    def test_existing_rate_engine_and_immutable_input_snapshot_use_private_facts(self):
        private_stream = "market.fact.private-gold"
        private_offer = _fact(
            30,
            source_code="PRIVATE_GOLD_CHANNEL",
            stream_id=private_stream,
            source_sequence=1,
            payload={
                "kind": "PRIVATE_GOLD_OFFER",
                "instrument": "MELTED_GOLD_PRIVATE",
                "side": "SELL",
                "settlement": "TODAY",
                "trade_form": "PHYSICAL",
                "offered_price_value": "80000000",
                "price_unit": "TOMAN_PER_MESGHAL_750",
                "quantity_value": "10",
                "quantity_unit": "LOT_COUNT",
                "lifetime_seconds": 120,
            },
        )
        coin_stream = "market.fact.coin.group.1"
        coin_offer = _fact(
            31,
            source_code="GROUP_1",
            stream_id=coin_stream,
            source_sequence=1,
            occurred_at_utc="2026-08-26T05:00:02Z",
            available_at_utc="2026-08-26T05:00:03Z",
            persisted_at_utc="2026-08-26T05:00:04Z",
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "SELL",
                "settlement": "CASH",
                "trade_form": "PHYSICAL",
                "offered_price_value": "187500",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "2",
                "quantity_unit": "COIN_COUNT",
            },
        )
        xau_stream = "market.fact.xauusd"
        xau = _fact(
            32,
            source_code="XAUUSD",
            stream_id=xau_stream,
            source_sequence=1,
            payload={
                "kind": "EXTERNAL_QUOTE",
                "instrument": "XAUUSD",
                "quote_kind": "MID",
                "price_value": "3400",
                "price_unit": "USD_PER_TROY_OUNCE",
                "currency": "USD",
            },
        )
        usdt_stream = "market.fact.wallex-usdt"
        usdt = _fact(
            33,
            source_code="WALLEX_PUBLIC_API",
            stream_id=usdt_stream,
            source_sequence=1,
            payload={
                "kind": "EXTERNAL_QUOTE",
                "instrument": "USDT_IRT",
                "quote_kind": "MID",
                "price_value": "97000",
                "price_unit": "TOMAN_PER_USDT",
                "currency": "TOMAN",
            },
        )
        for number, stream, fact in (
            (30, private_stream, private_offer),
            (31, coin_stream, coin_offer),
            (32, xau_stream, xau),
            (33, usdt_stream, usdt),
        ):
            self._receive(_batch(number, stream_id=stream, deliveries=[(1, fact)]))
        report = run_adapter_cycle(self.receiver, self.market)
        self.assertEqual(report.applied, 4)
        estimates = build_coin_rate_estimates(
            self.market,
            as_of_utc=datetime(2026, 8, 26, 5, 0, 10, tzinfo=timezone.utc),
        )
        imam = next(
            row
            for row in estimates
            if row.commodity_code == "IMAM" and row.settlement_term == "CASH"
        )
        self.assertEqual(imam.status, "ESTIMATED", repr(imam))
        self.market.commit()
        published = publish_rate_ready_snapshot(
            market_store_path=self.market_path,
            snapshot_path=Path(self.directory.name) / "stage9-rate-snapshot.json",
            as_of_utc="2026-08-26T05:00:10Z",
        )
        self.assertEqual(published.status, "PUBLISHED")
        self.assertIsNotNone(published.snapshot_digest)
        snapshot = materialize_input_snapshot(
            self.market, as_of_utc="2026-08-26T05:00:10Z"
        )
        repeated = materialize_input_snapshot(
            self.market, as_of_utc="2026-08-26T05:00:10Z"
        )
        self.assertEqual(snapshot.input_snapshot_hash, repeated.input_snapshot_hash)
        self.assertTrue(
            all(
                component.source_event_key is None
                or self.market.execute(
                    "SELECT 1 FROM private_fact_adapter_projections WHERE event_key=?",
                    (component.source_event_key,),
                ).fetchone()
                is not None
                for component in snapshot.components
            )
        )

    def test_feed_mode_is_explicit_and_fail_closed(self):
        self.assertEqual(normalize_feed_mode(None), "LEGACY")
        self.assertEqual(normalize_feed_mode("private_shadow"), "PRIVATE_SHADOW")
        self.assertEqual(normalize_feed_mode("PRIVATE_PRIMARY"), "PRIVATE_PRIMARY")
        with self.assertRaises(MarketFactAdapterError):
            normalize_feed_mode("AUTO")
        legacy = Path(self.directory.name) / "legacy.sqlite3"
        private = Path(self.directory.name) / "private.sqlite3"
        shadow = select_estimator_feeds(
            feed_mode="PRIVATE_SHADOW",
            legacy_store=legacy,
            private_store=private,
        )
        self.assertEqual((shadow.primary_store, shadow.shadow_store), (legacy, private))
        primary = select_estimator_feeds(
            feed_mode="PRIVATE_PRIMARY",
            legacy_store=legacy,
            private_store=private,
        )
        self.assertEqual((primary.primary_store, primary.shadow_store), (private, private))
        rollback = select_estimator_feeds(
            feed_mode="LEGACY",
            legacy_store=legacy,
            private_store=private,
        )
        self.assertEqual((rollback.primary_store, rollback.shadow_store), (legacy, None))
        self.assertTrue(rollback.private_capture_continues)

    def test_large_inbox_selection_is_bounded_and_causal_per_stream(self):
        xau_stream = "market.fact.xauusd"
        usdt_stream = "market.fact.wallex-usdt"
        xau_1 = _fact(
            101,
            source_code="XAUUSD",
            stream_id=xau_stream,
            source_sequence=1,
            payload={
                "kind": "EXTERNAL_QUOTE",
                "instrument": "XAUUSD",
                "quote_kind": "MID",
                "price_value": "3400",
                "price_unit": "USD_PER_TROY_OUNCE",
                "currency": "USD",
            },
        )
        xau_2 = _fact(
            102,
            source_code="XAUUSD",
            stream_id=xau_stream,
            source_sequence=2,
            payload={
                **xau_1["payload"],
                "price_value": "3401",
            },
        )
        xau_2["payload_hash"] = content_hash(xau_2["payload"])
        usdt = _fact(
            103,
            source_code="WALLEX_PUBLIC_API",
            stream_id=usdt_stream,
            source_sequence=1,
            payload={
                "kind": "EXTERNAL_QUOTE",
                "instrument": "USDT_IRT",
                "quote_kind": "MID",
                "price_value": "97000",
                "price_unit": "TOMAN_PER_USDT",
                "currency": "TOMAN",
            },
        )
        self._receive(
            _batch(101, stream_id=xau_stream, deliveries=[(1, xau_1), (2, xau_2)])
        )
        self._receive(_batch(102, stream_id=usdt_stream, deliveries=[(1, usdt)]))
        # A replay can make receipt timestamps non-monotonic.  The adapter must
        # still expose each stream's sequence N before N+1.
        self.receiver.execute(
            "UPDATE fact_deliveries SET received_at_utc=? "
            "WHERE stream_id=? AND delivery_sequence=1",
            ("2026-08-26T05:00:10Z", xau_stream),
        )
        self.receiver.execute(
            "UPDATE fact_deliveries SET received_at_utc=? "
            "WHERE stream_id=? AND delivery_sequence=2",
            ("2026-08-26T05:00:00Z", xau_stream),
        )
        self.receiver.execute(
            "UPDATE fact_deliveries SET received_at_utc=? WHERE stream_id=?",
            ("2026-08-26T05:00:05Z", usdt_stream),
        )
        statements: list[str] = []
        self.receiver.set_trace_callback(statements.append)
        first = run_adapter_cycle(self.receiver, self.market, max_deliveries=2)
        self.receiver.set_trace_callback(None)
        second = run_adapter_cycle(self.receiver, self.market, max_deliveries=2)

        self.assertEqual((first.selected, first.applied), (2, 2))
        self.assertEqual((second.selected, second.applied), (1, 1))
        checkpoints = dict(
            self.market.execute(
                "SELECT stream_id,highest_delivery_sequence "
                "FROM private_fact_adapter_checkpoints"
            ).fetchall()
        )
        self.assertEqual(checkpoints, {usdt_stream: 1, xau_stream: 2})
        normalized = [" ".join(statement.split()).upper() for statement in statements]
        self.assertTrue(
            any(
                "WHERE STREAM_ID=" in statement
                and "DELIVERY_SEQUENCE>" in statement
                and "LIMIT" in statement
                for statement in normalized
            )
        )
        self.assertFalse(
            any(
                "FROM FACT_DELIVERIES ORDER BY RECEIVED_AT_UTC" in statement
                for statement in normalized
            )
        )


if __name__ == "__main__":
    unittest.main()
