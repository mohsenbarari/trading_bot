import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.market_fact_recovery import (
    FactRecoveryError, load_acknowledged_replay, replay_acknowledged_prefix,
)
from core.market_intelligence.market_fact_receiver import apply_fact_batch, connect_receiver
from core.market_intelligence.private_pipeline_contracts import content_hash
from core.market_intelligence.private_pipeline_contracts import MarketFactBatchV1
from core.market_intelligence.market_fact_sync import run_sync_cycle
from core.market_intelligence.private_market_transport import MarketTransportError
from tests.test_market_pipeline_stage8_transport import batch_fixture, revised_batch


class Outbox:
    """Only a SELECT of retained immutable deliveries is available to replay."""
    def __init__(self, rows):
        self.rows = rows
        self.selected = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, args):
        if not sql.lstrip().startswith("SELECT"):
            raise AssertionError("replay must not mutate sender state")
        stream, first, last = args
        self.selected = [r for r in self.rows if first <= r[0] <= last]

    def fetchall(self):
        return self.selected

    def rollback(self):
        pass


def outbox_row(batch):
    item = batch["items"][0]
    return (item["delivery_sequence"], copy.deepcopy(item["fact"]),
            content_hash(item["fact"]), "2026-09-06T12:00:00Z", None)


class FactRecoveryTests(unittest.TestCase):
    def test_sender_automatically_recovers_receiver_rollback_before_normal_delivery(self):
        first = batch_fixture()
        second = revised_batch(delivery_sequence=2, revision=2, price="187600")
        third = MarketFactBatchV1.model_validate(revised_batch(delivery_sequence=3, revision=3, price="187700"))
        db = Outbox([outbox_row(first), outbox_row(second)])
        with tempfile.TemporaryDirectory() as tmp:
            receiver = connect_receiver(Path(tmp) / "receiver.sqlite")
            try:
                apply_fact_batch(receiver, first)
                with patch("core.market_intelligence.market_fact_sync.load_next_batch", return_value=third), \
                     patch("core.market_intelligence.market_fact_sync.record_batch_failure") as failure, \
                     patch("core.market_intelligence.market_fact_sync.acknowledge_batch") as acknowledge:
                    kwargs = dict(sender_instance_id="test-recovery", send=lambda doc: apply_fact_batch(receiver, doc))
                    result = run_sync_cycle(db, **kwargs)
                    self.assertEqual(result["replayed"], 1)
                    acknowledge.assert_not_called()
                    failure.assert_not_called()
                    result = run_sync_cycle(db, **kwargs)
                    self.assertEqual(result["acknowledged"], 1)
                    acknowledge.assert_called_once()
                    self.assertEqual(receiver.execute("SELECT count(*) FROM fact_deliveries").fetchone()[0], 3)
            finally:
                receiver.close()

    def test_lost_replay_ack_retries_without_permanent_block_or_duplicate_apply(self):
        first = batch_fixture()
        second = revised_batch(delivery_sequence=2, revision=2, price="187600")
        third = MarketFactBatchV1.model_validate(revised_batch(delivery_sequence=3, revision=3, price="187700"))
        with tempfile.TemporaryDirectory() as tmp:
            receiver = connect_receiver(Path(tmp) / "receiver.sqlite")
            try:
                apply_fact_batch(receiver, first)
                def send(doc):
                    result = apply_fact_batch(receiver, doc)
                    if doc["first_sequence"] == 2:
                        raise MarketTransportError("lost_ack")
                    return result
                with patch("core.market_intelligence.market_fact_sync.load_next_batch", return_value=third), \
                     patch("core.market_intelligence.market_fact_sync.record_batch_failure", return_value=1) as failure:
                    run_sync_cycle(Outbox([outbox_row(second)]), sender_instance_id="test-recovery", send=send)
                    self.assertFalse(failure.call_args.kwargs["permanent"])
                    self.assertEqual(receiver.execute("SELECT count(*) FROM fact_deliveries").fetchone()[0], 2)
            finally:
                receiver.close()

    def test_older_receiver_recovers_exact_prefix_and_accepts_next_delivery(self):
        first = batch_fixture()
        second = revised_batch(delivery_sequence=2, revision=2, price="187600")
        third = revised_batch(delivery_sequence=3, revision=3, price="187700")
        db = Outbox([outbox_row(first), outbox_row(second)])
        with tempfile.TemporaryDirectory() as tmp:
            receiver = connect_receiver(Path(tmp) / "receiver.sqlite")
            try:
                self.assertEqual(apply_fact_batch(receiver, first)[0], 200)
                code, rejected = apply_fact_batch(receiver, third)
                self.assertEqual((code, rejected["rejection_reason_codes"]), (409, ["SEQUENCE_GAP"]))
                kwargs = dict(stream_id=first["stream_id"], receiver_sequence=1,
                              sender_sequence=2, sender_instance_id="test-recovery",
                              send=lambda doc: apply_fact_batch(receiver, doc))
                result = replay_acknowledged_prefix(db, **kwargs)
                self.assertEqual((result["accepted"], result["receiver_sequence"]), (1, 2))
                repeat = replay_acknowledged_prefix(db, **kwargs)
                self.assertEqual((repeat["accepted"], repeat["duplicates"]), (0, 1))
                self.assertEqual(apply_fact_batch(receiver, third)[0], 200)
                self.assertEqual(receiver.execute("SELECT count(*) FROM fact_deliveries").fetchone()[0], 3)
            finally:
                receiver.close()

    def test_temporary_replay_http_refusal_keeps_original_batch_retryable(self):
        first = batch_fixture()
        second = revised_batch(delivery_sequence=2, revision=2, price="187600")
        third = MarketFactBatchV1.model_validate(revised_batch(delivery_sequence=3, revision=3, price="187700"))
        for status in (408, 425, 429, 500, 502, 503, 504):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                receiver = connect_receiver(Path(tmp) / "receiver.sqlite")
                try:
                    apply_fact_batch(receiver, first)
                    def send(doc):
                        if doc["first_sequence"] == 2:
                            return status, {"error": "temporarily unavailable"}
                        return apply_fact_batch(receiver, doc)
                    with patch("core.market_intelligence.market_fact_sync.load_next_batch", return_value=third), \
                         patch("core.market_intelligence.market_fact_sync.record_batch_failure", return_value=1) as failure:
                        result = run_sync_cycle(Outbox([outbox_row(second)]), sender_instance_id="test-recovery", send=send)
                        self.assertFalse(failure.call_args.kwargs["permanent"])
                        self.assertEqual(result["rejected"], 0)
                        self.assertEqual(receiver.execute("SELECT count(*) FROM fact_deliveries").fetchone()[0], 1)
                finally:
                    receiver.close()

    def test_large_replay_stops_at_one_hundred_originals_then_continues(self):
        rows = [outbox_row(revised_batch(delivery_sequence=i, revision=i, price=str(187000 + i)))
                for i in range(1, 102)]
        stream_id = rows[0][1]["stream_id"]
        first = load_acknowledged_replay(
            Outbox(rows), stream_id=stream_id, receiver_sequence=0,
            sender_sequence=101, sender_instance_id="test-recovery",
        )
        self.assertEqual((first.first_sequence, first.last_sequence, first.item_count), (1, 100, 100))
        second = load_acknowledged_replay(
            Outbox(rows), stream_id=stream_id, receiver_sequence=100,
            sender_sequence=101, sender_instance_id="test-recovery",
        )
        self.assertEqual((second.first_sequence, second.last_sequence, second.item_count), (101, 101, 1))

    def test_unavailable_compacted_unacked_or_changed_original_is_not_sent(self):
        first = batch_fixture()
        original = outbox_row(first)
        variants = [[], [(1, original[1], original[2], None, None)],
                    [(1, original[1], original[2], original[3], "compacted")],
                    [(1, original[1], "0" * 64, original[3], None)]]
        for rows in variants:
            with self.subTest(rows=len(rows)):
                calls = []
                with self.assertRaises(FactRecoveryError):
                    replay_acknowledged_prefix(
                        Outbox(rows), stream_id=first["stream_id"], receiver_sequence=0,
                        sender_sequence=1, sender_instance_id="test-recovery",
                        send=lambda doc: calls.append(doc),
                    )
                self.assertEqual(calls, [])

    def test_mismatched_ack_is_never_recovery_proof(self):
        first = batch_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            receiver = connect_receiver(Path(tmp) / "receiver.sqlite")
            try:
                def mismatched(doc):
                    status, ack = apply_fact_batch(receiver, doc)
                    ack["batch_id"] = "0" * 64
                    return status, ack
                with self.assertRaisesRegex(FactRecoveryError, "NOT_ACKNOWLEDGED"):
                    replay_acknowledged_prefix(
                        Outbox([outbox_row(first)]), stream_id=first["stream_id"],
                        receiver_sequence=0, sender_sequence=1,
                        sender_instance_id="test-recovery", send=mismatched,
                    )
            finally:
                receiver.close()

    def test_replay_never_crosses_bounded_prefix(self):
        first = batch_fixture()
        with self.assertRaisesRegex(FactRecoveryError, "PREFIX_UNAVAILABLE"):
            load_acknowledged_replay(
                Outbox([outbox_row(first)]), stream_id=first["stream_id"],
                receiver_sequence=0, sender_sequence=500, sender_instance_id="test-recovery",
            )


if __name__ == "__main__":
    unittest.main()
