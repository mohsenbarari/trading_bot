from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from core.market_intelligence import (
    estimator_snapshot_receiver,
    estimator_snapshot_receiver_service,
    estimator_snapshot_runtime,
)
from core.market_intelligence.estimator_snapshot_receiver import (
    EstimatorSnapshotReceiverError,
    activate_legacy_prediction_authority,
    activate_private_prediction_authority,
    apply_estimator_snapshot,
    compact_snapshot_receiver,
    connect_snapshot_receiver,
    read_published_web_snapshot_view,
    read_web_snapshot_view,
    snapshot_receiver_metrics,
    update_prediction_ledger,
)
from core.market_intelligence.estimator_snapshot_runtime import (
    SnapshotPublishResult,
    publish_estimator_snapshot,
    run_coin_estimator_service,
    send_latest_snapshot,
)
from core.market_intelligence.market_fact_adapter import (
    initialize_adapter_store,
    run_adapter_cycle,
)
from core.market_intelligence.market_fact_receiver import apply_fact_batch, connect_receiver
from core.market_intelligence.market_store import connect_market_store
from core.market_intelligence.private_pipeline_contracts import (
    EstimatorSnapshotV2,
    estimator_snapshot_id,
)
from scripts import audit_production_market_catchup as catchup_audit
from tests.test_market_pipeline_stage9_adapter import _batch, _fact


class Stage10SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fact_receiver = connect_receiver(self.root / "fact-receiver.sqlite3")
        self.market_path = self.root / "market.sqlite3"
        self.market = connect_market_store(self.market_path)
        initialize_adapter_store(self.market)
        self.estimator_state = self.root / "estimator-state.sqlite3"
        self.sender_state = self.root / "sender-state.sqlite3"
        self.snapshot_path = self.root / "bot" / "latest-estimator-snapshot.json"
        self.web_root = self.root / "web"
        self.prediction_ledger = self.root / "calibration" / "prediction-ledger.sqlite3"
        self.events_path = self.root / "web-state" / "events.jsonl"
        self.web_receiver = connect_snapshot_receiver(
            self.root / "web-state" / "receiver.sqlite3"
        )
        self._seed_private_market()

    def tearDown(self) -> None:
        self.web_receiver.close()
        self.market.close()
        self.fact_receiver.close()
        self.temporary.cleanup()

    def _receive_fact(self, number: int, stream: str, fact: dict[str, object]) -> None:
        status, response = apply_fact_batch(
            self.fact_receiver,
            _batch(number, stream_id=stream, deliveries=[(1, fact)]),
        )
        self.assertEqual((status, response["status"]), (200, "ACK"))

    def _seed_private_market(self) -> None:
        private_stream = "market.fact.private-gold"
        private_offer = _fact(
            101,
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
            102,
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
            103,
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
            104,
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
            (101, private_stream, private_offer),
            (102, coin_stream, coin_offer),
            (103, xau_stream, xau),
            (104, usdt_stream, usdt),
        ):
            with patch(
                "core.market_intelligence.market_fact_receiver._utc_now",
                return_value=datetime(2026, 8, 26, 5, 0, 5, tzinfo=timezone.utc),
            ):
                self._receive_fact(number, stream, fact)
        with patch(
            "core.market_intelligence.market_store._utc_now",
            return_value="2026-08-26T05:00:05.000000Z",
        ):
            report = run_adapter_cycle(self.fact_receiver, self.market)
        self.assertEqual((report.applied, report.rejected), (4, 0))
        self.market.commit()

    def _seed_remaining_primary_inventory(self) -> None:
        facts = (
            (
                105,
                "market.fact.coin.group.2",
                _fact(
                    105,
                    source_code="GROUP_2",
                    stream_id="market.fact.coin.group.2",
                    source_sequence=1,
                    payload={
                        "kind": "COIN_OFFER",
                        "group_code": 2,
                        "instrument": "COIN_IMAM",
                        "side": "BUY",
                        "settlement": "CASH",
                        "trade_form": "PHYSICAL",
                        "offered_price_value": "187400",
                        "price_unit": "PROJECT_THOUSAND_TOMAN",
                        "quantity_value": "1",
                        "quantity_unit": "COIN_COUNT",
                    },
                ),
            ),
            *(
                (
                    number,
                    stream,
                    _fact(
                        number,
                        source_code=source,
                        stream_id=stream,
                        source_sequence=1,
                        payload={
                            "kind": "OBSERVATION",
                            "instrument": "MELTED_GOLD_AGGREGATE",
                            "event_type": "OFFER",
                            "side": "SELL",
                            "settlement": "TODAY",
                            "trade_form": "PHYSICAL",
                            "price_value": value,
                            "price_unit": "TOMAN_PER_MESGHAL_750",
                            "currency": "TOMAN",
                            "quantity_value": None,
                            "quantity_unit": None,
                        },
                    ),
                )
                for number, source, stream, value in (
                    (106, "MELTED_AGGREGATE", "market.fact.melted-aggregate", "80010000"),
                    (107, "MELTED_FLOW", "market.fact.melted-flow", "80020000"),
                )
            ),
            (
                108,
                "market.fact.usd-herat",
                _fact(
                    108,
                    source_code="USD_HERAT",
                    stream_id="market.fact.usd-herat",
                    source_sequence=1,
                    payload={
                        "kind": "OBSERVATION",
                        "instrument": "USD_HERAT",
                        "event_type": "OFFER",
                        "side": "BUY",
                        "settlement": "TOMORROW",
                        "trade_form": "PAPER_NORMAL",
                        "price_value": "97000",
                        "price_unit": "TOMAN_PER_USD",
                        "currency": "TOMAN",
                        "quantity_value": None,
                        "quantity_unit": None,
                    },
                ),
            ),
            (
                109,
                "market.fact.binance-paxg",
                _fact(
                    109,
                    source_code="BINANCE_PAXG_PUBLIC_API",
                    stream_id="market.fact.binance-paxg",
                    source_sequence=1,
                    payload={
                        "kind": "EXTERNAL_QUOTE",
                        "instrument": "PAXG_USD_PROXY",
                        "quote_kind": "MID",
                        "price_value": "3401",
                        "price_unit": "USD_PER_TROY_OUNCE",
                        "currency": "USD",
                    },
                ),
            ),
        )
        for number, stream, fact in facts:
            with patch(
                "core.market_intelligence.market_fact_receiver._utc_now",
                return_value=datetime(2026, 8, 26, 5, 0, 6, tzinfo=timezone.utc),
            ):
                self._receive_fact(number, stream, fact)
        with patch(
            "core.market_intelligence.market_store._utc_now",
            return_value="2026-08-26T05:00:06.000000Z",
        ):
            report = run_adapter_cycle(self.fact_receiver, self.market)
        self.assertEqual((report.applied, report.rejected), (5, 0))
        self.market.commit()

    def _publish(
        self,
        at: str = "2026-08-26T05:00:10Z",
        *,
        feed_mode: str = "PRIVATE_SHADOW",
    ) -> dict[str, object]:
        result = publish_estimator_snapshot(
            market_store_path=self.market_path,
            state_path=self.estimator_state,
            output_path=self.snapshot_path,
            feed_mode=feed_mode,
            as_of_utc=datetime.fromisoformat(at.replace("Z", "+00:00")),
        )
        document = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(result.snapshot_id, document["snapshot_id"])
        return document

    def _apply_web(
        self,
        document: dict[str, object],
        *,
        allow_private_primary: bool = False,
        now_utc: datetime | None = None,
    ):
        if now_utc is None:
            now_utc = datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc)
        return apply_estimator_snapshot(
            self.web_receiver,
            document,
            snapshot_root=self.web_root,
            publication_events_path=self.events_path,
            prediction_ledger_path=self.prediction_ledger,
            allow_private_primary=allow_private_primary,
            now_utc=now_utc,
        )

    def test_snapshot_exposes_source_bound_input_health_for_private_primary_inventory(self):
        document = self._publish(feed_mode="PRIVATE_PRIMARY")
        inputs = {item["component"]: item for item in document["inputs"]}
        expected = {
            "SOURCE_INPUT_MELTED_PRIMARY": ("PRIVATE_GOLD_CHANNEL", True),
            "SOURCE_INPUT_GROUP_1": ("GROUP_1", True),
            "SOURCE_INPUT_GROUP_2": ("GROUP_2", False),
            "SOURCE_INPUT_MELTED_AGGREGATE": ("MELTED_AGGREGATE", False),
            "SOURCE_INPUT_MELTED_FLOW": ("MELTED_FLOW", False),
            "SOURCE_INPUT_USD_HERAT": ("USD_HERAT", False),
            "SOURCE_INPUT_XAUUSD": ("XAUUSD", True),
            "SOURCE_INPUT_WALLEX": ("WALLEX_PUBLIC_API", True),
            "SOURCE_INPUT_BINANCE_PAXG": ("BINANCE_PAXG_PUBLIC_API", False),
        }
        for component, (source, observed) in expected.items():
            with self.subTest(component=component):
                trace = inputs[component]
                self.assertEqual(trace["source_codes"], [source])
                self.assertEqual(trace["selection_method"], "PRIVATE_PRIMARY_SOURCE_READINESS_V1")
                self.assertEqual(trace["source_fact_id"] is not None, observed)
                self.assertEqual(trace["freshness"] == "MISSING", not observed)

    def test_private_primary_snapshot_fact_binds_all_nine_configured_inputs(self):
        self._seed_remaining_primary_inventory()
        document = self._publish(feed_mode="PRIVATE_PRIMARY")
        validated = EstimatorSnapshotV2.model_validate(document)
        indexed = catchup_audit._index_snapshot_inputs(validated)
        self.assertEqual(len(indexed), len(validated.inputs))
        inputs = {item["component"]: item for item in document["inputs"]}
        expected_sources = {
            "SOURCE_INPUT_MELTED_PRIMARY": "PRIVATE_GOLD_CHANNEL",
            "SOURCE_INPUT_GROUP_1": "GROUP_1",
            "SOURCE_INPUT_GROUP_2": "GROUP_2",
            "SOURCE_INPUT_MELTED_AGGREGATE": "MELTED_AGGREGATE",
            "SOURCE_INPUT_MELTED_FLOW": "MELTED_FLOW",
            "SOURCE_INPUT_USD_HERAT": "USD_HERAT",
            "SOURCE_INPUT_XAUUSD": "XAUUSD",
            "SOURCE_INPUT_WALLEX": "WALLEX_PUBLIC_API",
            "SOURCE_INPUT_BINANCE_PAXG": "BINANCE_PAXG_PUBLIC_API",
        }
        for component, source in expected_sources.items():
            with self.subTest(component=component):
                trace = inputs[component]
                self.assertEqual(trace["source_codes"], [source])
                self.assertIsNotNone(trace["source_fact_id"])
                self.assertIsNotNone(trace["source_event_key"])
                self.assertIsNotNone(trace["fact_revision"])
                self.assertNotEqual(trace["freshness"], "MISSING")

        duplicated = validated.model_copy(
            update={"inputs": (*validated.inputs, validated.inputs[0])}
        )
        with self.assertRaisesRegex(
            catchup_audit.CatchupAuditError,
            "estimator_input_component_duplicate",
        ):
            catchup_audit._index_snapshot_inputs(duplicated)

    def test_trace_uses_signal_event_key_when_two_rows_share_event_time(self):
        second = _fact(
            110,
            source_code="GROUP_1",
            stream_id="market.fact.coin.group.1",
            source_sequence=2,
            occurred_at_utc="2026-08-26T05:00:02Z",
            available_at_utc="2026-08-26T05:00:04Z",
            persisted_at_utc="2026-08-26T05:00:05Z",
            payload={
                "kind": "COIN_OFFER",
                "group_code": 1,
                "instrument": "COIN_IMAM",
                "side": "SELL",
                "settlement": "CASH",
                "trade_form": "PHYSICAL",
                "offered_price_value": "187600",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "quantity_value": "1",
                "quantity_unit": "COIN_COUNT",
            },
        )
        with patch(
            "core.market_intelligence.market_fact_receiver._utc_now",
            return_value=datetime(2026, 8, 26, 5, 0, 6, tzinfo=timezone.utc),
        ):
            status, response = apply_fact_batch(
                self.fact_receiver,
                _batch(
                    110,
                    stream_id="market.fact.coin.group.1",
                    deliveries=[(2, second)],
                ),
            )
        self.assertEqual((status, response["status"]), (200, "ACK"))
        with patch(
            "core.market_intelligence.market_store._utc_now",
            return_value="2026-08-26T05:00:07.000000Z",
        ):
            report = run_adapter_cycle(self.fact_receiver, self.market)
        self.assertEqual((report.applied, report.rejected), (1, 0))
        rows = self.market.execute(
            "SELECT o.event_key,o.event_time_utc,o.price_num,p.fact_id "
            "FROM market_observations o JOIN private_fact_adapter_projections p "
            "ON p.event_key=o.event_key WHERE o.source_code='GROUP_1' "
            "ORDER BY o.id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        selected = rows[0]
        trace = estimator_snapshot_runtime._input_traces(
            self.market,
            {
                "SOURCE_INPUT_GROUP_1": {
                    "status": "FRESH",
                    "price_unit": "PROJECT_THOUSAND_TOMAN",
                    "last_event_utc": str(selected["event_time_utc"]),
                    "source_event_key": bytes(selected["event_key"]).hex(),
                    "age_seconds": 1,
                    "observation_count": 1,
                    "source_codes": ["GROUP_1"],
                    "method": "private_primary_source_readiness_v1",
                    "latest_price": str(selected["price_num"]),
                    "mean_price": str(selected["price_num"]),
                }
            },
        )[0]
        self.assertEqual(trace.source_event_key, bytes(selected["event_key"]).hex())
        self.assertEqual(trace.source_fact_id, str(selected["fact_id"]))
        self.assertNotEqual(trace.source_event_key, bytes(rows[1]["event_key"]).hex())

    def test_product_market_regime_is_translated_to_v2_wire_vocabulary(self):
        translate = estimator_snapshot_runtime._estimator_market_regime
        self.assertEqual(
            {
                value: translate(value)
                for value in ("NORMAL", "UP", "DOWN", "VOLATILE", "UNKNOWN")
            },
            {
                "NORMAL": "RANGE",
                "UP": "UP",
                "DOWN": "DOWN",
                "VOLATILE": "SHOCK",
                "UNKNOWN": "UNKNOWN",
            },
        )
        with self.assertRaisesRegex(
            estimator_snapshot_runtime.EstimatorSnapshotRuntimeError,
            "estimator_snapshot_market_regime_invalid",
        ):
            translate("UNSUPPORTED")

    def test_hash_timing_inputs_and_web_view_are_one_authoritative_snapshot(self):
        document = self._publish()
        status, ack = self._apply_web(document)
        self.assertEqual((status, ack["status"]), (200, "ACK"))
        self.assertEqual(ack["snapshot_hash"], document["snapshot_id"])
        self.assertEqual(
            ack["web_view"]["snapshot_hash"],
            document["snapshot_id"],
        )
        view = read_web_snapshot_view(
            self.web_root / "latest-private-shadow.json",
            now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(view["snapshot_hash"], document["snapshot_id"])
        self.assertEqual(view["snapshot"]["rates"], document["rates"])
        self.assertEqual(view["transport_state"], "FRESH")
        observed = [item for item in document["inputs"] if item["freshness"] != "MISSING"]
        self.assertTrue(observed)
        self.assertTrue(all(item["point_value"] is not None for item in observed))
        self.assertTrue(all(item["mean_value"] is not None for item in observed))
        for item in observed:
            self.assertLessEqual(item["occurred_at_utc"], item["available_at_utc"])
            self.assertLessEqual(item["available_at_utc"], item["parsed_at_utc"])
            self.assertLessEqual(item["parsed_at_utc"], item["transferred_at_utc"])
        events = [json.loads(line) for line in self.events_path.read_text().splitlines()]
        self.assertEqual(events[-1]["snapshot_hash"], document["snapshot_id"])
        cache = json.loads(
            (self.web_root / "cache-private_shadow.json").read_text(encoding="utf-8")
        )
        self.assertEqual(cache["snapshot_hash"], document["snapshot_id"])
        self.assertFalse(self.prediction_ledger.exists())

        primary = self._publish(
            "2026-08-26T05:00:15Z", feed_mode="PRIVATE_PRIMARY"
        )
        denied, response = self._apply_web(primary)
        self.assertEqual(
            (denied, response["reason_code"]),
            (403, "PRIVATE_PRIMARY_NOT_AUTHORIZED"),
        )
        self.assertFalse((self.web_root / "latest-private-primary.json").exists())
        self.assertEqual(
            self._apply_web(primary, allow_private_primary=True)[0],
            200,
        )
        ledger = sqlite3.connect(self.prediction_ledger)
        try:
            rows = ledger.execute(
                "SELECT model_id,commodity,settlement,estimated_price_toman "
                "FROM coin_estimate_predictions ORDER BY id"
            ).fetchall()
        finally:
            ledger.close()
        self.assertTrue(rows)
        self.assertTrue(all(row[0] == "MAIN_ONLINE" for row in rows))
        self.assertTrue(all(int(row[3]) % 1000 == 0 for row in rows))

    def test_receiver_preserves_subsecond_causality_for_primary_ledger(self):
        primary = self._publish(
            "2026-08-26T05:00:15.900000Z",
            feed_mode="PRIVATE_PRIMARY",
        )
        self.prediction_ledger.parent.mkdir(parents=True)
        legacy = sqlite3.connect(self.prediction_ledger)
        try:
            legacy.execute(
                "CREATE TABLE coin_estimate_predictions("
                "id INTEGER PRIMARY KEY,prediction_time_utc TEXT NOT NULL,"
                "created_at_utc TEXT NOT NULL,model_id TEXT NOT NULL,"
                "commodity TEXT NOT NULL,settlement TEXT NOT NULL,"
                "estimated_price_toman INTEGER NOT NULL,"
                "authority_epoch TEXT NOT NULL)"
            )
            legacy.execute(
                "CREATE TABLE coin_estimate_prediction_authority("
                "singleton INTEGER PRIMARY KEY,active_epoch TEXT NOT NULL,"
                "active_feed_mode TEXT NOT NULL,updated_at_utc TEXT NOT NULL)"
            )
            legacy.execute(
                "INSERT INTO coin_estimate_prediction_authority "
                "VALUES(1,'LEGACY_BASELINE','LEGACY_BASELINE',?)",
                ("2026-08-26T04:59:01Z",),
            )
            # This id would collide with the abandoned V2 formula
            # version*1000+index, but not with the stable V1 namespace.
            legacy.execute(
                "INSERT INTO coin_estimate_predictions VALUES(?,?,?,?,?,?,?,?)",
                (
                    1000,
                    "2026-08-26T04:59:00Z",
                    "2026-08-26T04:59:01Z",
                    "MAIN_ONLINE",
                    "امام",
                    "CASH",
                    1,
                    "LEGACY_BASELINE",
                ),
            )
            legacy.commit()
        finally:
            legacy.close()
        status, _ = self._apply_web(
            primary,
            allow_private_primary=True,
            now_utc=datetime(
                2026, 8, 26, 5, 0, 15, 950000, tzinfo=timezone.utc
            ),
        )
        self.assertEqual(status, 200)
        ledger = sqlite3.connect(self.prediction_ledger)
        try:
            prediction_time, created_at = ledger.execute(
                "SELECT prediction_time_utc,created_at_utc "
                "FROM coin_estimate_predictions WHERE id=2001"
            ).fetchone()
            inserted = ledger.execute(
                "SELECT COUNT(*) FROM coin_estimate_predictions WHERE id>=2001"
            ).fetchone()[0]
            active = ledger.execute(
                "SELECT active_feed_mode FROM coin_estimate_prediction_authority"
            ).fetchone()[0]
        finally:
            ledger.close()
        self.assertEqual(
            inserted,
            sum(rate["status"] == "ESTIMATED" for rate in primary["rates"]),
        )
        self.assertEqual(active, "LEGACY_BASELINE")
        activate_private_prediction_authority(
            self.prediction_ledger,
            EstimatorSnapshotV2.model_validate(primary),
            activated_at_utc="2026-08-26T05:00:16Z",
        )
        self.assertGreaterEqual(
            datetime.fromisoformat(created_at.replace("Z", "+00:00")),
            datetime.fromisoformat(prediction_time.replace("Z", "+00:00")),
        )

        shadow = self._publish(
            "2026-08-26T05:00:16Z",
            feed_mode="PRIVATE_SHADOW",
        )
        self.assertEqual(self._apply_web(shadow)[0], 200)
        # Receiving a delayed Shadow artifact must not change Product/parser
        # authority.  Rollback is an explicit controller operation.
        ledger = sqlite3.connect(self.prediction_ledger)
        try:
            active = ledger.execute(
                "SELECT active_epoch,active_feed_mode "
                "FROM coin_estimate_prediction_authority"
            ).fetchone()
        finally:
            ledger.close()
        self.assertEqual(active[1], "PRIVATE_PRIMARY")
        activate_legacy_prediction_authority(self.prediction_ledger)
        ledger = sqlite3.connect(self.prediction_ledger)
        try:
            active = ledger.execute(
                "SELECT active_epoch,active_feed_mode "
                "FROM coin_estimate_prediction_authority"
            ).fetchone()
        finally:
            ledger.close()
        self.assertEqual(active, ("LEGACY_BASELINE", "LEGACY_BASELINE"))

    def test_failed_primary_view_publish_never_switches_prediction_authority(self):
        primary = self._publish(
            "2026-08-26T05:00:15Z",
            feed_mode="PRIVATE_PRIMARY",
        )
        with patch(
            "core.market_intelligence.private_pipeline_foundation.atomic_json_write",
            side_effect=OSError("simulated_primary_view_failure"),
        ):
            with self.assertRaisesRegex(
                OSError, "simulated_primary_view_failure"
            ):
                self._apply_web(primary, allow_private_primary=True)

        ledger = sqlite3.connect(self.prediction_ledger)
        try:
            active = ledger.execute(
                "SELECT active_epoch,active_feed_mode "
                "FROM coin_estimate_prediction_authority"
            ).fetchone()
        finally:
            ledger.close()
        self.assertEqual(active, ("LEGACY_BASELINE", "LEGACY_BASELINE"))
        self.assertFalse(
            (self.web_root / "latest-private-primary.json").exists()
        )

    def test_compaction_removes_only_superseded_pre_intent_reservations(self):
        first = self._publish(
            "2026-08-26T05:00:10Z",
            feed_mode="PRIVATE_PRIMARY",
        )
        with patch.object(
            estimator_snapshot_receiver,
            "update_prediction_ledger",
            side_effect=OSError("simulated_pre_intent_failure"),
        ):
            with self.assertRaisesRegex(OSError, "simulated_pre_intent_failure"):
                self._apply_web(first, allow_private_primary=True)

        self.assertEqual(
            self.web_receiver.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_receipts "
                "WHERE published_at_utc IS NULL"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.web_receiver.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox"
            ).fetchone()[0],
            0,
        )
        before_superseded = compact_snapshot_receiver(
            self.web_receiver,
            now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(
            before_superseded["superseded_pending_receipts_deleted"], 0
        )

        second = self._publish(
            "2026-08-26T05:00:15Z",
            feed_mode="PRIVATE_PRIMARY",
        )
        self.assertEqual(
            self._apply_web(second, allow_private_primary=True)[0],
            200,
        )
        result = compact_snapshot_receiver(
            self.web_receiver,
            now_utc=datetime(2026, 8, 26, 5, 0, 25, tzinfo=timezone.utc),
        )
        metrics = snapshot_receiver_metrics(
            self.web_receiver,
            now_utc=datetime(2026, 8, 26, 5, 0, 25, tzinfo=timezone.utc),
            expected_lane="PRIVATE_PRIMARY",
            snapshot_root=self.web_root,
        )

        self.assertEqual(result["superseded_pending_receipts_deleted"], 1)
        self.assertEqual(metrics["pending_receipts"], 0)
        self.assertTrue(metrics["snapshot_ready"])

    def test_prediction_ledger_id_collision_fails_without_silent_drop(self):
        primary = EstimatorSnapshotV2.model_validate(
            self._publish(feed_mode="PRIVATE_PRIMARY")
        )
        created_at = "2026-08-26T05:00:11Z"
        inserted = update_prediction_ledger(
            self.prediction_ledger,
            primary,
            created_at_utc=created_at,
        )
        self.assertGreater(inserted, 0)
        ledger = sqlite3.connect(self.prediction_ledger)
        try:
            first_id = ledger.execute(
                "SELECT MIN(id) FROM coin_estimate_predictions"
            ).fetchone()[0]
            ledger.execute(
                "UPDATE coin_estimate_predictions SET commodity='CORRUPTED' WHERE id=?",
                (first_id,),
            )
            ledger.commit()
        finally:
            ledger.close()

        with self.assertRaisesRegex(
            EstimatorSnapshotReceiverError, "prediction_ledger_id_collision"
        ):
            update_prediction_ledger(
                self.prediction_ledger,
                primary,
                created_at_utc=created_at,
            )
        with self.assertRaisesRegex(
            EstimatorSnapshotReceiverError,
            "prediction_authority_primary_epoch_incomplete",
        ):
            activate_private_prediction_authority(
                self.prediction_ledger,
                primary,
            )

    def test_receiver_rejects_future_and_incompatible_model_before_effects(self):
        future = self._publish()
        status, response = self._apply_web(
            future,
            now_utc=datetime(
                2026, 8, 26, 5, 0, 9, 999999, tzinfo=timezone.utc
            ),
        )
        self.assertEqual((status, response["reason_code"]), (422, "SNAPSHOT_TIME_FUTURE"))
        self.assertFalse((self.web_root / "latest-private-shadow.json").exists())

        stale = self._publish("2026-08-26T05:00:11Z")
        status, response = self._apply_web(
            stale,
            now_utc=datetime(2026, 8, 26, 5, 1, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(
            (status, response["reason_code"]),
            (422, "SNAPSHOT_TIME_STALE"),
        )

        incompatible = dict(future)
        incompatible["model_version"] = "unsupported-model"
        incompatible["snapshot_id"] = estimator_snapshot_id(incompatible)
        status, response = self._apply_web(incompatible)
        self.assertEqual(
            (status, response["reason_code"]),
            (422, "MODEL_VERSION_UNSUPPORTED"),
        )
        self.assertFalse((self.web_root / "latest-private-shadow.json").exists())

    def test_primary_rejects_ambiguous_nonempty_pre_authority_ledger(self):
        primary = self._publish(feed_mode="PRIVATE_PRIMARY")
        old_path = self.root / "calibration" / "old-ledger.sqlite3"
        old_path.parent.mkdir(parents=True, exist_ok=True)
        ledger = sqlite3.connect(old_path)
        try:
            ledger.execute(
                "CREATE TABLE coin_estimate_predictions("
                "id INTEGER PRIMARY KEY,prediction_time_utc TEXT NOT NULL,"
                "created_at_utc TEXT NOT NULL,model_id TEXT NOT NULL,"
                "commodity TEXT NOT NULL,settlement TEXT NOT NULL,"
                "estimated_price_toman INTEGER NOT NULL)"
            )
            ledger.execute(
                "INSERT INTO coin_estimate_predictions VALUES(?,?,?,?,?,?,?)",
                (
                    1,
                    "2026-08-26T04:59:00Z",
                    "2026-08-26T04:59:01Z",
                    "MAIN_ONLINE",
                    "امام",
                    "CASH",
                    187_000_000,
                ),
            )
            ledger.commit()
        finally:
            ledger.close()
        with self.assertRaisesRegex(
            EstimatorSnapshotReceiverError,
            "prediction_ledger_authority_migration_required",
        ):
            apply_estimator_snapshot(
                self.web_receiver,
                primary,
                snapshot_root=self.web_root,
                publication_events_path=self.events_path,
                prediction_ledger_path=old_path,
                allow_private_primary=True,
                now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
            )
        self.assertFalse((self.web_root / "latest-private-primary.json").exists())

    def test_shadow_snapshot_cannot_write_parser_prediction_anchor_ledger(self):
        document = self._publish()
        self.assertEqual(self._apply_web(document)[0], 200)
        self.assertFalse(self.prediction_ledger.exists())

        snapshot = EstimatorSnapshotV2.model_validate(document)
        with self.assertRaisesRegex(
            EstimatorSnapshotReceiverError,
            "prediction_ledger_non_primary_snapshot_forbidden",
        ):
            # Call the lower-level writer as a regression guard: even a future
            # caller may not accidentally bypass apply_estimator_snapshot.
            update_prediction_ledger(
                self.prediction_ledger,
                snapshot,
                created_at_utc="2026-08-26T05:00:11Z",
            )

    def test_receiver_rejects_v1_mixed_release_without_replacing_v2_view(self):
        document = self._publish()
        status, _ = self._apply_web(document)
        self.assertEqual(status, 200)
        path = self.web_root / "latest-private-shadow.json"
        before = path.read_bytes()
        legacy = {
            "contract": "estimator_snapshot/1.0",
            "snapshot_id": "d" * 64,
            "snapshot_version": int(document["snapshot_version"]) + 1,
            "generated_at_utc": document["generated_at_utc"],
            "input_snapshot_hash": "e" * 64,
            "model_version": "legacy-sender",
            "feed_mode": "PRIVATE_SHADOW",
            "status": "OK",
            "rates": [
                {
                    "instrument": "COIN_IMAM",
                    "settlement": "CASH",
                    "value": "187450",
                    "unit": "PROJECT_THOUSAND_TOMAN",
                    "lower_bound": "186900",
                    "upper_bound": "188000",
                    "confidence": 0.91,
                    "method": "WEIGHTED_BOOK",
                }
            ],
            "health": [],
            "inputs": [],
            "reason_codes": [],
        }
        rejected, response = self._apply_web(legacy)
        self.assertEqual((rejected, response["reason_code"]), (422, "CONTRACT_INVALID"))
        self.assertEqual(path.read_bytes(), before)

    def test_live_publish_never_precedes_a_consumed_transfer(self):
        output = self.root / "bot" / "live-timestamp.json"
        publish_estimator_snapshot(
            market_store_path=self.market_path,
            state_path=self.root / "live-timestamp-state.sqlite3",
            output_path=output,
            feed_mode="PRIVATE_SHADOW",
        )
        document = json.loads(output.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(
            str(document["generated_at_utc"]).replace("Z", "+00:00")
        )
        observed = [
            item for item in document["inputs"] if item["freshness"] != "MISSING"
        ]

        self.assertIn(".", str(document["generated_at_utc"]))
        self.assertTrue(observed)
        self.assertTrue(
            all(
                datetime.fromisoformat(
                    str(item["transferred_at_utc"]).replace("Z", "+00:00")
                )
                <= generated
                for item in observed
            )
        )

    def test_live_publish_dates_artifact_after_expensive_evaluation(self):
        output = self.root / "bot" / "live-completion-timestamp.json"
        completed_at = datetime(2030, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
        with patch(
            "core.market_intelligence.estimator_snapshot_runtime."
            "_live_snapshot_completion_utc",
            return_value=completed_at,
        ):
            publish_estimator_snapshot(
                market_store_path=self.market_path,
                state_path=self.root / "live-completion-state.sqlite3",
                output_path=output,
                feed_mode="PRIVATE_SHADOW",
            )

        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            document["generated_at_utc"],
            "2030-01-02T03:04:05.678901Z",
        )
        identity = dict(document)
        identity.pop("snapshot_id")
        self.assertEqual(document["snapshot_id"], estimator_snapshot_id(identity))

    def test_explicit_historical_publish_preserves_requested_generation_time(self):
        document = self._publish("2026-08-26T05:00:10.123456Z")
        self.assertEqual(
            document["generated_at_utc"],
            "2026-08-26T05:00:10.123456Z",
        )

    def test_monotonic_guard_duplicate_and_stale_route_cut(self):
        first = self._publish()
        self.assertEqual(self._apply_web(first)[0], 200)
        second = self._publish("2026-08-26T05:00:15Z")
        self.assertEqual(int(second["snapshot_version"]), 2)
        self.assertEqual(self._apply_web(second)[0], 200)
        status, response = self._apply_web(first)
        self.assertEqual((status, response["reason_code"]), (409, "SNAPSHOT_VERSION_REGRESSION"))
        status, duplicate = self._apply_web(second)
        self.assertEqual((status, duplicate["duplicate"]), (200, True))
        view = read_web_snapshot_view(
            self.web_root / "latest-private-shadow.json",
            now_utc=datetime(2026, 8, 26, 5, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(view["snapshot_hash"], second["snapshot_id"])
        self.assertEqual(view["transport_state"], "STALE")

    def test_failed_view_publish_keeps_version_fence_and_recovers(self):
        first = self._publish()
        self.assertEqual(self._apply_web(first)[0], 200)
        second = self._publish("2026-08-26T05:00:15Z")
        with patch(
            "core.market_intelligence.private_pipeline_foundation.atomic_json_write",
            side_effect=OSError("simulated_view_failure"),
        ):
            with self.assertRaisesRegex(OSError, "simulated_view_failure"):
                self._apply_web(second)

        pending = self.web_receiver.execute(
            "SELECT published_at_utc FROM estimator_snapshot_receipts "
            "WHERE feed_mode='PRIVATE_SHADOW' AND snapshot_version=2"
        ).fetchone()
        self.assertIsNotNone(pending)
        self.assertIsNone(pending[0])
        self.assertEqual(
            self.web_receiver.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc IS NULL"
            ).fetchone()[0],
            1,
        )
        metrics = snapshot_receiver_metrics(
            self.web_receiver,
            now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
            expected_lane="PRIVATE_SHADOW",
            snapshot_root=self.web_root,
        )
        self.assertEqual(metrics["snapshot_readiness"], "PENDING")
        self.assertFalse(metrics["snapshot_ready"])
        with self.assertRaisesRegex(
            EstimatorSnapshotReceiverError, "web_snapshot_publication_pending"
        ):
            read_published_web_snapshot_view(
                self.web_receiver,
                self.web_root / "latest-private-shadow.json",
                feed_mode="PRIVATE_SHADOW",
                now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
            )
        status, response = self._apply_web(first)
        self.assertEqual(
            (status, response["reason_code"]),
            (409, "SNAPSHOT_VERSION_REGRESSION"),
        )
        status, response = self._apply_web(second)
        self.assertEqual((status, response["duplicate"]), (200, True))
        view = read_web_snapshot_view(
            self.web_root / "latest-private-shadow.json",
            now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(view["snapshot_version"], 2)
        published_view = read_published_web_snapshot_view(
            self.web_receiver,
            self.web_root / "latest-private-shadow.json",
            feed_mode="PRIVATE_SHADOW",
            now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(published_view["snapshot_version"], 2)
        self.assertEqual(
            self.web_receiver.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc IS NULL"
            ).fetchone()[0],
            0,
        )

    def test_event_append_completes_a_short_os_write(self):
        event_path = self.root / "short-write" / "events.jsonl"
        real_write = estimator_snapshot_receiver.os.write
        calls = 0

        def short_once(descriptor, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(descriptor, payload[:7])
            return real_write(descriptor, payload)

        with patch.object(
            estimator_snapshot_receiver.os,
            "write",
            side_effect=short_once,
        ):
            estimator_snapshot_receiver._fsync_append(
                event_path,
                {"event_id": "a" * 64, "status": "published"},
            )

        self.assertGreater(calls, 1)
        self.assertEqual(
            json.loads(event_path.read_text(encoding="utf-8"))["event_id"],
            "a" * 64,
        )

    def test_snapshot_receiver_retention_is_bounded_and_keeps_latest_fence(self):
        first = self._publish()
        self.assertEqual(self._apply_web(first)[0], 200)
        second = self._publish("2026-08-26T05:00:15Z")
        self.assertEqual(self._apply_web(second)[0], 200)
        self.web_receiver.execute(
            "UPDATE estimator_snapshot_receipts SET published_at_utc=? "
            "WHERE snapshot_version=1",
            ("2026-08-10T05:00:00Z",),
        )
        self.web_receiver.execute(
            "UPDATE estimator_snapshot_publication_outbox SET delivered_at_utc=? "
            "WHERE snapshot_version=1",
            ("2026-08-10T05:00:00Z",),
        )
        self.web_receiver.execute(
            "UPDATE estimator_snapshot_receipts SET published_at_utc=? "
            "WHERE snapshot_version=2",
            ("2026-08-26T05:00:16Z",),
        )
        current_view_path = self.web_root / "latest-private-shadow.json"
        current_view = json.loads(current_view_path.read_text(encoding="utf-8"))
        current_view["published_at_utc"] = "2026-08-26T05:00:16Z"
        current_view_path.write_text(
            json.dumps(current_view, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        self.web_receiver.execute(
            "INSERT INTO estimator_snapshot_rejections"
            "(reason_code,body_hash,rejected_at_utc) VALUES(?,?,?)",
            ("OLD", "b" * 64, "2026-08-10T05:00:00Z"),
        )

        result = compact_snapshot_receiver(
            self.web_receiver,
            now_utc=datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc),
        )
        metrics = snapshot_receiver_metrics(
            self.web_receiver,
            now_utc=datetime(2026, 8, 26, 5, 0, 25, tzinfo=timezone.utc),
            expected_lane="PRIVATE_SHADOW",
            snapshot_root=self.web_root,
        )

        self.assertEqual(result["receipts_deleted"], 1)
        self.assertEqual(result["rejections_deleted"], 1)
        self.assertEqual(metrics["retained_receipt_count"], 1)
        self.assertEqual(metrics["retained_full_payload_count"], 1)
        self.assertEqual(metrics["lanes"]["PRIVATE_SHADOW"]["snapshot_version"], 2)
        self.assertTrue(metrics["snapshot_ready"])

        later = compact_snapshot_receiver(
            self.web_receiver,
            now_utc=datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc),
        )
        bounded = snapshot_receiver_metrics(self.web_receiver)
        self.assertEqual(later["payloads_redacted"], 1)
        self.assertEqual(bounded["retained_receipt_count"], 1)
        self.assertEqual(bounded["retained_full_payload_count"], 0)

    def test_snapshot_receiver_compaction_batches_a_large_backlog(self):
        old = "2026-08-10T05:00:00Z"
        for version in range(1, 8):
            snapshot_id = f"{version:064x}"
            self.web_receiver.execute(
                "INSERT INTO estimator_snapshot_receipts"
                "(feed_mode,snapshot_version,snapshot_id,input_snapshot_hash,payload_json,"
                "received_at_utc,published_at_utc) VALUES(?,?,?,?,?,?,?)",
                (
                    "PRIVATE_SHADOW",
                    version,
                    snapshot_id,
                    "f" * 64,
                    '{"payload":true}',
                    old,
                    old,
                ),
            )
            self.web_receiver.execute(
                "INSERT INTO estimator_snapshot_publication_outbox"
                "(event_id,feed_mode,snapshot_version,snapshot_id,published_at_utc,"
                "delivered_at_utc) VALUES(?,?,?,?,?,?)",
                (f"{version + 20:064x}", "PRIVATE_SHADOW", version, snapshot_id, old, old),
            )
            self.web_receiver.execute(
                "INSERT INTO estimator_snapshot_rejections"
                "(reason_code,body_hash,rejected_at_utc) VALUES(?,?,?)",
                ("OLD", f"{version + 40:064x}", old),
            )

        with patch.object(
            estimator_snapshot_receiver, "SNAPSHOT_COMPACTION_BATCH_SIZE", 2
        ):
            result = compact_snapshot_receiver(
                self.web_receiver,
                now_utc=datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(result["payloads_redacted"], 7)
        self.assertEqual(result["receipts_deleted"], 6)
        self.assertEqual(result["outbox_rows_deleted"], 7)
        self.assertEqual(result["rejections_deleted"], 7)
        remaining = self.web_receiver.execute(
            "SELECT snapshot_version,payload_json FROM estimator_snapshot_receipts"
        ).fetchall()
        self.assertEqual(
            [(row["snapshot_version"], row["payload_json"]) for row in remaining],
            [(7, "{}")],
        )

    def test_receiver_readiness_uses_snapshot_generation_not_publish_time(self):
        document = self._publish()
        self.assertEqual(self._apply_web(document)[0], 200)

        metrics = snapshot_receiver_metrics(
            self.web_receiver,
            now_utc=datetime(2026, 8, 26, 5, 1, 0, tzinfo=timezone.utc),
            expected_lane="PRIVATE_SHADOW",
            stale_after_seconds=30,
            snapshot_root=self.web_root,
        )

        self.assertEqual(metrics["snapshot_readiness"], "STALE")
        self.assertFalse(metrics["snapshot_ready"])

    def test_view_written_before_event_failure_is_not_product_visible(self):
        document = self._publish()
        with patch.object(
            estimator_snapshot_receiver,
            "_fsync_append",
            side_effect=OSError("simulated_event_failure"),
        ):
            with self.assertRaisesRegex(OSError, "simulated_event_failure"):
                self._apply_web(document)

        self.assertTrue((self.web_root / "latest-private-shadow.json").is_file())
        metrics = snapshot_receiver_metrics(
            self.web_receiver,
            now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
            expected_lane="PRIVATE_SHADOW",
            snapshot_root=self.web_root,
        )
        self.assertEqual(metrics["snapshot_readiness"], "PENDING")
        with self.assertRaisesRegex(
            EstimatorSnapshotReceiverError, "web_snapshot_publication_pending"
        ):
            read_published_web_snapshot_view(
                self.web_receiver,
                self.web_root / "latest-private-shadow.json",
                feed_mode="PRIVATE_SHADOW",
                now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
            )

        status, response = self._apply_web(document)
        self.assertEqual((status, response["duplicate"]), (200, True))
        recovered = read_published_web_snapshot_view(
            self.web_receiver,
            self.web_root / "latest-private-shadow.json",
            feed_mode="PRIVATE_SHADOW",
            now_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(recovered["snapshot_hash"], document["snapshot_id"])

    def test_lost_ack_replay_and_pending_atomic_publish_recover(self):
        document = self._publish()
        failed = False

        def lost_ack(value):
            nonlocal failed
            status, response = self._apply_web(dict(value))
            if not failed:
                failed = True
                raise RuntimeError("simulated_lost_ack")
            return status, response

        with self.assertRaisesRegex(RuntimeError, "simulated_lost_ack"):
            send_latest_snapshot(
                snapshot_path=self.snapshot_path,
                state_path=self.sender_state,
                expected_feed_mode="PRIVATE_SHADOW",
                send=lost_ack,
            )
        sent = send_latest_snapshot(
            snapshot_path=self.snapshot_path,
            state_path=self.sender_state,
            expected_feed_mode="PRIVATE_SHADOW",
            send=lost_ack,
        )
        self.assertEqual((sent.status, sent.snapshot_id), ("ACKNOWLEDGED", document["snapshot_id"]))

    def test_sender_persists_only_receiver_acknowledged_product_view(self):
        document = self._publish()
        local_view = self.root / "bot" / "latest-private-shadow.json"

        sent = send_latest_snapshot(
            snapshot_path=self.snapshot_path,
            state_path=self.sender_state,
            expected_feed_mode="PRIVATE_SHADOW",
            send=lambda value: self._apply_web(dict(value)),
            acknowledged_view_path=local_view,
        )

        self.assertEqual(sent.status, "ACKNOWLEDGED")
        acknowledged = json.loads(local_view.read_text(encoding="utf-8"))
        self.assertEqual(
            (
                acknowledged["contract"],
                acknowledged["snapshot_hash"],
                acknowledged["snapshot"]["snapshot_id"],
            ),
            (
                "estimator_snapshot_web_view/1.0",
                document["snapshot_id"],
                document["snapshot_id"],
            ),
        )

        # Losing only the local acknowledged projection must not make the raw
        # estimator artifact authoritative.  The sender replays the same
        # version, obtains the receiver-issued duplicate ACK, and repairs it.
        local_view.unlink()
        repaired = send_latest_snapshot(
            snapshot_path=self.snapshot_path,
            state_path=self.sender_state,
            expected_feed_mode="PRIVATE_SHADOW",
            send=lambda value: self._apply_web(dict(value)),
            acknowledged_view_path=local_view,
        )
        self.assertEqual(repaired.status, "ACKNOWLEDGED")
        self.assertTrue(local_view.is_file())

    def test_sender_rejects_unbound_acknowledged_product_view(self):
        document = self._publish()
        local_view = self.root / "bot" / "latest-private-shadow.json"

        def tampered_ack(value):
            status, response = self._apply_web(dict(value))
            response["web_view"] = dict(response["web_view"])
            response["web_view"]["snapshot_hash"] = "0" * 64
            return status, response

        with self.assertRaisesRegex(
            estimator_snapshot_runtime.EstimatorSnapshotRuntimeError,
            "snapshot_sender_web_view_identity_mismatch",
        ):
            send_latest_snapshot(
                snapshot_path=self.snapshot_path,
                state_path=self.sender_state,
                expected_feed_mode="PRIVATE_SHADOW",
                send=tampered_ack,
                acknowledged_view_path=local_view,
            )
        self.assertFalse(local_view.exists())

    def test_sender_rejects_artifact_from_another_authority_lane(self):
        self._publish(feed_mode="PRIVATE_PRIMARY")
        calls = []
        with self.assertRaisesRegex(
            estimator_snapshot_runtime.EstimatorSnapshotRuntimeError,
            "snapshot_sender_artifact_feed_mode_mismatch",
        ):
            send_latest_snapshot(
                snapshot_path=self.snapshot_path,
                state_path=self.sender_state,
                expected_feed_mode="PRIVATE_SHADOW",
                send=lambda document: calls.append(document),
            )
        self.assertEqual(calls, [])

    def test_sender_allows_only_monotonic_audited_lane_transition(self):
        shadow = self._publish(feed_mode="PRIVATE_SHADOW")

        def acknowledge(document):
            return 200, {
                "status": "ACK",
                "snapshot_id": document["snapshot_id"],
                "snapshot_hash": document["snapshot_id"],
                "snapshot_version": document["snapshot_version"],
            }

        send_latest_snapshot(
            snapshot_path=self.snapshot_path,
            state_path=self.sender_state,
            expected_feed_mode="PRIVATE_SHADOW",
            send=acknowledge,
        )
        primary = self._publish(
            "2026-08-26T05:00:15Z",
            feed_mode="PRIVATE_PRIMARY",
        )
        self.assertGreater(primary["snapshot_version"], shadow["snapshot_version"])
        result = send_latest_snapshot(
            snapshot_path=self.snapshot_path,
            state_path=self.sender_state,
            expected_feed_mode="PRIVATE_PRIMARY",
            send=acknowledge,
        )
        self.assertEqual(result.status, "ACKNOWLEDGED")
        state = sqlite3.connect(self.sender_state)
        try:
            transition = state.execute(
                "SELECT from_feed_mode,to_feed_mode,snapshot_version,snapshot_id "
                "FROM estimator_snapshot_sender_transitions"
            ).fetchone()
        finally:
            state.close()
        self.assertEqual(
            transition,
            (
                "PRIVATE_SHADOW",
                "PRIVATE_PRIMARY",
                primary["snapshot_version"],
                primary["snapshot_id"],
            ),
        )

        recovered_output = self.root / "bot" / "recovered.json"
        recovered_state = self.root / "recovered-state.sqlite3"
        with patch(
            "core.market_intelligence.private_pipeline_foundation.atomic_json_write",
            side_effect=OSError("simulated_output_failure"),
        ):
            with self.assertRaisesRegex(OSError, "simulated_output_failure"):
                publish_estimator_snapshot(
                    market_store_path=self.market_path,
                    state_path=recovered_state,
                    output_path=recovered_output,
                    feed_mode="PRIVATE_SHADOW",
                    as_of_utc=datetime(2026, 8, 26, 5, 0, 10, tzinfo=timezone.utc),
                )
        recovered = publish_estimator_snapshot(
            market_store_path=self.market_path,
            state_path=recovered_state,
            output_path=recovered_output,
            feed_mode="PRIVATE_SHADOW",
            as_of_utc=datetime(2026, 8, 26, 5, 0, 20, tzinfo=timezone.utc),
        )
        self.assertTrue(recovered.recovered_pending)
        recovered_document = json.loads(recovered_output.read_text(encoding="utf-8"))
        self.assertEqual(int(recovered_document["snapshot_version"]), 1)

    def test_pending_v1_is_quarantined_without_reusing_its_version(self):
        state_path = self.root / "legacy-pending-state.sqlite3"
        state = sqlite3.connect(state_path)
        state.executescript(
            """
            CREATE TABLE estimator_snapshot_publications (
              snapshot_version INTEGER PRIMARY KEY,
              snapshot_id TEXT NOT NULL UNIQUE,
              feed_mode TEXT NOT NULL,
              input_snapshot_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at_utc TEXT NOT NULL,
              published_at_utc TEXT
            );
            """
        )
        state.execute(
            "INSERT INTO estimator_snapshot_publications VALUES(?,?,?,?,?,?,NULL)",
            (
                41,
                "a" * 64,
                "PRIVATE_SHADOW",
                "b" * 64,
                json.dumps({"contract": "estimator_snapshot/1.0"}),
                "2026-08-26T04:59:59Z",
            ),
        )
        state.commit()
        state.close()

        output = self.root / "bot" / "after-v1-upgrade.json"
        result = publish_estimator_snapshot(
            market_store_path=self.market_path,
            state_path=state_path,
            output_path=output,
            feed_mode="PRIVATE_SHADOW",
            as_of_utc=datetime(2026, 8, 26, 5, 0, 10, tzinfo=timezone.utc),
        )

        self.assertFalse(result.recovered_pending)
        self.assertEqual(result.snapshot_version, 42)
        upgraded = sqlite3.connect(state_path)
        try:
            legacy = upgraded.execute(
                "SELECT contract,quarantine_reason,quarantined_at_utc "
                "FROM estimator_snapshot_publications WHERE snapshot_version=41"
            ).fetchone()
        finally:
            upgraded.close()
        self.assertEqual(
            legacy[:2],
            ("estimator_snapshot/1.0", "PENDING_CONTRACT_UNSUPPORTED"),
        )
        self.assertTrue(legacy[2])

    def test_live_receiver_startup_writes_health_before_serving(self):
        stop = threading.Event()

        class FakeServer:
            def __init__(self, *_args, **_kwargs):
                self.socket = object()
                self.timeout = None

            def handle_request(self):
                stop.set()

            def server_close(self):
                return None

        class FakeTls:
            def wrap_socket(self, socket, *, server_side):
                self.server_side = server_side
                return socket

        environment = {
            "MARKET_PIPELINE_ALLOWED_PEER_IP": "10.240.1.10",
            "MARKET_PIPELINE_SNAPSHOT_ROOT": str(self.root / "live-snapshots"),
            "MARKET_PIPELINE_CALIBRATION_ROOT": str(self.root / "live-calibration"),
        }
        with patch.dict("os.environ", environment, clear=False), patch.object(
            estimator_snapshot_receiver_service, "_Server", FakeServer
        ), patch.object(
            estimator_snapshot_receiver_service,
            "server_tls_context",
            return_value=FakeTls(),
        ), patch.object(
            estimator_snapshot_receiver_service,
            "read_key",
            return_value=b"k" * 32,
        ):
            result = estimator_snapshot_receiver_service.run_estimator_snapshot_receiver_service(
                role="estimator-snapshot-receiver",
                mode="live",
                release_sha="a" * 40,
                state_directory=self.root / "live-state",
                stop=stop,
            )
        self.assertEqual(result, 0)
        health = json.loads((self.root / "live-state" / "health.json").read_text())
        self.assertEqual(health["status"], "live-starting")
        self.assertEqual(health["snapshot_readiness"], "MISSING")

    def test_estimator_service_keeps_start_to_start_inference_cadence(self):
        class OneCycleStop:
            def __init__(self):
                self.waits: list[float] = []

            def is_set(self):
                return bool(self.waits)

            def wait(self, seconds):
                self.waits.append(float(seconds))
                return True

        stop = OneCycleStop()
        published = SnapshotPublishResult(
            snapshot_id="a" * 64,
            snapshot_version=1,
            input_snapshot_hash="b" * 64,
            status="OK",
            recovered_pending=False,
        )
        with patch.dict(
            "os.environ",
            {
                "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
                "MARKET_PIPELINE_ESTIMATOR_INTERVAL_SECONDS": "4",
            },
            clear=False,
        ), patch.object(
            estimator_snapshot_runtime,
            "publish_estimator_snapshot",
            return_value=published,
        ), patch.object(
            estimator_snapshot_runtime.time,
            "monotonic",
            side_effect=(100.0, 101.8),
        ), patch(
            "core.market_intelligence.private_pipeline_foundation.atomic_json_write"
        ):
            result = run_coin_estimator_service(
                role="coin-estimator",
                mode="live",
                release_sha="c" * 40,
                state_directory=self.root / "cadence-state",
                stop=stop,
            )
        self.assertEqual(result, 0)
        self.assertEqual(len(stop.waits), 1)
        self.assertAlmostEqual(stop.waits[0], 2.2, places=6)

    def test_estimator_service_rejects_invalid_inference_interval(self):
        with patch.dict(
            "os.environ",
            {
                "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
                "MARKET_PIPELINE_ESTIMATOR_INTERVAL_SECONDS": "0",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                estimator_snapshot_runtime.EstimatorSnapshotRuntimeError,
                "coin_estimator_interval_invalid",
            ):
                run_coin_estimator_service(
                    role="coin-estimator",
                    mode="live",
                    release_sha="c" * 40,
                    state_directory=self.root / "invalid-cadence-state",
                    stop=threading.Event(),
                )


if __name__ == "__main__":
    unittest.main()
