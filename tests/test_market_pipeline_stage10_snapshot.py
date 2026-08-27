from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from core.market_intelligence import estimator_snapshot_receiver_service
from core.market_intelligence.estimator_snapshot_receiver import (
    apply_estimator_snapshot,
    connect_snapshot_receiver,
    read_web_snapshot_view,
)
from core.market_intelligence.estimator_snapshot_runtime import (
    publish_estimator_snapshot,
    send_latest_snapshot,
)
from core.market_intelligence.market_fact_adapter import (
    initialize_adapter_store,
    run_adapter_cycle,
)
from core.market_intelligence.market_fact_receiver import apply_fact_batch, connect_receiver
from core.market_intelligence.market_store import connect_market_store
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

    def _publish(self, at: str = "2026-08-26T05:00:10Z") -> dict[str, object]:
        result = publish_estimator_snapshot(
            market_store_path=self.market_path,
            state_path=self.estimator_state,
            output_path=self.snapshot_path,
            feed_mode="PRIVATE_SHADOW",
            as_of_utc=datetime.fromisoformat(at.replace("Z", "+00:00")),
        )
        document = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(result.snapshot_id, document["snapshot_id"])
        return document

    def _apply_web(self, document: dict[str, object]):
        return apply_estimator_snapshot(
            self.web_receiver,
            document,
            snapshot_root=self.web_root,
            publication_events_path=self.events_path,
            prediction_ledger_path=self.prediction_ledger,
        )

    def test_hash_timing_inputs_and_web_view_are_one_authoritative_snapshot(self):
        document = self._publish()
        status, ack = self._apply_web(document)
        self.assertEqual((status, ack["status"]), (200, "ACK"))
        self.assertEqual(ack["snapshot_hash"], document["snapshot_id"])
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
                send=lost_ack,
            )
        sent = send_latest_snapshot(
            snapshot_path=self.snapshot_path,
            state_path=self.sender_state,
            send=lost_ack,
        )
        self.assertEqual((sent.status, sent.snapshot_id), ("ACKNOWLEDGED", document["snapshot_id"]))

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
        self.assertEqual(health["status"], "live-ready")


if __name__ == "__main__":
    unittest.main()
