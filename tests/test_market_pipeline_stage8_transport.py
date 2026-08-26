import copy
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from pydantic import TypeAdapter

from core.market_intelligence.market_fact_receiver import (
    apply_fact_batch,
    connect_receiver,
)
from core.market_intelligence.market_fact_projection import observation_payload
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.private_market_transport import (
    FACT_PATH,
    MarketAuthenticationError,
    authenticate_request,
    decode_document,
    encode_document,
    signed_headers,
)
from core.market_intelligence.private_pipeline_contracts import FactPayload, content_hash


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "market_private_pipeline"
    / "market_fact_batch.json"
)


def batch_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def revised_batch(*, delivery_sequence: int, revision: int, price: str):
    value = batch_fixture()
    item = value["items"][0]
    item["delivery_sequence"] = delivery_sequence
    item["fact"]["fact_revision"] = revision
    item["fact"]["payload"]["offered_price_value"] = price
    item["fact"]["payload_hash"] = content_hash(item["fact"]["payload"])
    value["first_sequence"] = delivery_sequence
    value["last_sequence"] = delivery_sequence
    value["batch_id"] = f"{delivery_sequence:064x}"
    value["items_hash"] = content_hash(value["items"])
    return value


class Stage8ReceiverTests(unittest.TestCase):
    def test_lost_ack_replay_and_revision_are_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receiver.sqlite3"
            connection = connect_receiver(path)
            try:
                first = batch_fixture()
                status, ack = apply_fact_batch(connection, first)
                self.assertEqual((status, ack["accepted_count"]), (200, 1))

                # Simulate an ACK lost after the receiver commit.
                status, replay_ack = apply_fact_batch(connection, first)
                self.assertEqual(status, 200)
                self.assertEqual(replay_ack["duplicate_count"], 1)

                revised = revised_batch(
                    delivery_sequence=2, revision=2, price="187600"
                )
                status, revision_ack = apply_fact_batch(connection, revised)
                self.assertEqual((status, revision_ack["accepted_count"]), (200, 1))
                latest = connection.execute(
                    "SELECT fact_revision,payload_json FROM fact_latest"
                ).fetchone()
                self.assertEqual(int(latest["fact_revision"]), 2)
                self.assertEqual(
                    json.loads(latest["payload_json"])["payload"][
                        "offered_price_value"
                    ],
                    "187600",
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM fact_deliveries").fetchone()[0],
                    2,
                )
            finally:
                connection.close()

            # A process restart sees the same durable checkpoint and facts.
            restarted = connect_receiver(path)
            try:
                self.assertEqual(
                    restarted.execute(
                        "SELECT highest_contiguous_sequence FROM fact_checkpoints"
                    ).fetchone()[0],
                    2,
                )
            finally:
                restarted.close()

    def test_coin_trade_projection_references_offer_and_preserves_negotiated_terms(self):
        with tempfile.TemporaryDirectory() as directory:
            market = connect_market_store(Path(directory) / "market.sqlite3")
            initialize_market_store(market)
            offer_key = derive_event_key("stage8", "offer")
            trade_key = derive_event_key("stage8", "trade")
            common = {
                "source_code": "GROUP_1",
                "source_family": "GROUP",
                "event_time_utc": "2026-08-26T05:00:00Z",
                "available_at_utc": "2026-08-26T05:00:01Z",
                "instrument": "COIN_IMAM",
                "market_label": "GROUP_COIN_IMAM",
                "settlement_term": "CASH",
                "trade_form": "PHYSICAL",
                "side": "SELL",
                "price_unit": "PROJECT_THOUSAND_TOMAN",
                "currency": "TOMAN",
                "quantity_unit": "COIN_COUNT",
                "parser_version": "stage8-test-v1",
            }
            upsert_observation(
                market,
                MarketObservation(
                    event_key=offer_key,
                    event_type="OFFER",
                    price="187500",
                    quantity="5",
                    **common,
                ),
            )
            upsert_observation(
                market,
                MarketObservation(
                    event_key=trade_key,
                    event_type="TRADE",
                    price="187300",
                    quantity="2",
                    attributes={"root_offer_event_key": offer_key.hex()},
                    **common,
                ),
            )
            market.commit()
            row = market.execute(
                "SELECT * FROM market_observations WHERE event_key=?", (trade_key,)
            ).fetchone()
            payload = observation_payload(market, row)
            TypeAdapter(FactPayload).validate_python(payload)
            self.assertEqual(payload["outcome"], "CONFIRMED_PARTIAL")
            self.assertEqual(payload["agreed_price_value"], "187300")
            self.assertEqual(payload["agreed_quantity_value"], "2")
            self.assertRegex(payload["offer_fact_id"], r"^[0-9a-f]{64}$")
            market.close()

    def test_herat_projection_preserves_trade_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            market = connect_market_store(Path(directory) / "market.sqlite3")
            initialize_market_store(market)
            event_key = derive_event_key("stage8", "herat-trade")
            upsert_observation(
                market,
                MarketObservation(
                    event_key=event_key,
                    source_code="USD_HERAT",
                    source_family="TELEGRAM_PUBLIC",
                    event_time_utc="2026-08-26T05:00:00Z",
                    available_at_utc="2026-08-26T05:00:01Z",
                    instrument="USD_HERAT",
                    market_label="HERAT_PAPER",
                    settlement_term="TOMORROW",
                    trade_form="PAPER_NORMAL",
                    event_type="TRADE",
                    side="SELL",
                    price="97500",
                    price_unit="TOMAN_PER_USD",
                    currency="TOMAN",
                    quantity="2",
                    quantity_unit="BILLION_TOMAN",
                    parser_version="stage8-test-v1",
                ),
            )
            market.commit()
            row = market.execute(
                "SELECT * FROM market_observations WHERE event_key=?", (event_key,)
            ).fetchone()
            payload = observation_payload(market, row)
            TypeAdapter(FactPayload).validate_python(payload)
            self.assertEqual(payload["kind"], "OBSERVATION")
            self.assertEqual(payload["event_type"], "TRADE")
            self.assertEqual(payload["settlement"], "TOMORROW")
            self.assertEqual(payload["trade_form"], "PAPER_NORMAL")
            self.assertEqual(payload["quantity_value"], "2")
            market.close()

    def test_out_of_order_batch_does_not_advance_or_partially_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = connect_receiver(Path(directory) / "receiver.sqlite3")
            try:
                status, _ = apply_fact_batch(connection, batch_fixture())
                self.assertEqual(status, 200)
                gap = revised_batch(delivery_sequence=3, revision=2, price="187700")
                status, ack = apply_fact_batch(connection, gap)
                self.assertEqual(status, 409)
                self.assertEqual(ack["rejection_reason_codes"], ["SEQUENCE_GAP"])
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM fact_deliveries").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT highest_contiguous_sequence FROM fact_checkpoints"
                    ).fetchone()[0],
                    1,
                )
            finally:
                connection.close()


class Stage8TransportTests(unittest.TestCase):
    def test_hmac_exact_body_gzip_and_persistent_replay_guard(self):
        key = b"k" * 32
        document = {"fact": "x" * 70_000}
        encoded = encode_document(document, compress_threshold_bytes=1)
        self.assertEqual(encoded.content_encoding, "gzip")
        self.assertEqual(decode_document(encoded.body, "gzip"), document)
        now = int(time.time())
        headers = signed_headers(
            key_id="active-v1",
            key=key,
            body=encoded.body,
            content_encoding=encoded.content_encoding,
            timestamp=now,
            nonce="1" * 32,
        )
        connection = sqlite3.connect(":memory:", isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.assertEqual(
                authenticate_request(
                    connection,
                    method="POST",
                    path=FACT_PATH,
                    headers=headers,
                    body=encoded.body,
                    keys={"active-v1": key},
                    now_epoch=now,
                ),
                "gzip",
            )
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(
                MarketAuthenticationError, "AUTH_REPLAY_DETECTED"
            ):
                authenticate_request(
                    connection,
                    method="POST",
                    path=FACT_PATH,
                    headers=headers,
                    body=encoded.body,
                    keys={"active-v1": key},
                    now_epoch=now,
                )
            connection.rollback()
        finally:
            connection.close()

    def test_tamper_and_clock_skew_fail_before_decode(self):
        key = b"z" * 32
        encoded = encode_document({"ok": True})
        now = int(time.time())
        headers = signed_headers(
            key_id="active-v1",
            key=key,
            body=encoded.body,
            content_encoding="identity",
            timestamp=now - 31,
            nonce="2" * 32,
        )
        connection = sqlite3.connect(":memory:", isolation_level=None)
        try:
            with self.assertRaisesRegex(MarketAuthenticationError, "AUTH_CLOCK_SKEW"):
                authenticate_request(
                    connection,
                    method="POST",
                    path=FACT_PATH,
                    headers=headers,
                    body=encoded.body + b"x",
                    keys={"active-v1": key},
                    now_epoch=now,
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
