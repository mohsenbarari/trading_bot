"""Bounded redelivery of an acknowledged prefix after receiver recovery.

Only original, hash-checked outbox envelopes may be replayed. Sender checkpoints
and existing acknowledgements never move backwards. Normal receiver validation
still decides whether a replay is acceptable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping

from core.market_intelligence.private_pipeline_contracts import (
    MarketFactAckV1, MarketFactBatchV1, MarketFactDeliveryV1, MarketFactV1,
    batch_items_hash, content_hash,
)

MAX_REPLAY_ITEMS = 100
MAX_REPLAY_BYTES = 768 * 1024


class FactRecoveryError(RuntimeError):
    """Payload-free recovery refusal; never mask an identity/integrity error."""


def load_acknowledged_replay(
    connection, *, stream_id: str, receiver_sequence: int,
    sender_sequence: int, sender_instance_id: str,
) -> MarketFactBatchV1:
    if not 0 <= receiver_sequence < sender_sequence:
        raise FactRecoveryError("REPLAY_SEQUENCE_BOUNDS_INVALID")
    last = min(sender_sequence, receiver_sequence + MAX_REPLAY_ITEMS)
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT delivery_sequence,envelope,encode(envelope_hash,'hex'),
                      acknowledged_at_utc,envelope_compacted_at_utc
               FROM market_data.market_fact_outbox
               WHERE stream_id=%s AND delivery_sequence BETWEEN %s AND %s
               ORDER BY delivery_sequence""",
            (stream_id, receiver_sequence + 1, last),
        )
        rows = cursor.fetchall()
    connection.rollback()
    if len(rows) != last - receiver_sequence:
        raise FactRecoveryError("REPLAY_ORIGINAL_PREFIX_UNAVAILABLE")
    items = []
    for expected, row in enumerate(rows, receiver_sequence + 1):
        sequence, envelope, digest, acknowledged, compacted = row
        if sequence != expected or acknowledged is None or compacted is not None:
            raise FactRecoveryError("REPLAY_ORIGINAL_PREFIX_UNAVAILABLE")
        if content_hash(envelope) != digest:
            raise FactRecoveryError("REPLAY_ORIGINAL_HASH_MISMATCH")
        fact = MarketFactV1.model_validate(envelope)
        if fact.stream_id != stream_id:
            raise FactRecoveryError("REPLAY_STREAM_MISMATCH")
        items.append(MarketFactDeliveryV1(delivery_sequence=sequence, fact=fact))
    # Large legitimate envelopes may need a smaller batch. Never omit the head.
    while items:
        items_hash = batch_items_hash(items)
        batch = MarketFactBatchV1(
            contract="market_fact_batch/1.0", schema_version="1.0",
            batch_id=content_hash({
                "contract": "market_fact_batch_identity/1.0", "stream_id": stream_id,
                "first_sequence": items[0].delivery_sequence,
                "last_sequence": items[-1].delivery_sequence, "items_hash": items_hash,
            }),
            stream_id=stream_id, first_sequence=items[0].delivery_sequence,
            last_sequence=items[-1].delivery_sequence,
            created_at_utc=datetime.now(timezone.utc), item_count=len(items),
            items_hash=items_hash, sender_instance_id=sender_instance_id, items=tuple(items),
        )
        if len(batch.model_dump_json().encode()) <= MAX_REPLAY_BYTES:
            return batch
        items.pop()
    raise FactRecoveryError("REPLAY_SINGLE_ITEM_TOO_LARGE")


def validate_replay_ack(batch: MarketFactBatchV1, status: int, response: Mapping) -> MarketFactAckV1:
    ack = MarketFactAckV1.model_validate(response)
    if (status != 200 or ack.status != "ACK" or ack.batch_id != batch.batch_id
            or ack.stream_id != batch.stream_id or ack.rejected_count
            or ack.received_count != batch.item_count
            or ack.accepted_count + ack.duplicate_count != batch.item_count
            or ack.highest_contiguous_sequence < batch.last_sequence):
        raise FactRecoveryError("REPLAY_NOT_ACKNOWLEDGED")
    return ack


def replay_acknowledged_prefix(
    connection, *, stream_id: str, receiver_sequence: int, sender_sequence: int,
    sender_instance_id: str,
    send: Callable[[Mapping[str, object]], tuple[int, Mapping[str, object]]],
) -> dict[str, object]:
    batch = load_acknowledged_replay(
        connection, stream_id=stream_id, receiver_sequence=receiver_sequence,
        sender_sequence=sender_sequence, sender_instance_id=sender_instance_id,
    )
    status, response = send(batch.model_dump(mode="json"))
    ack = validate_replay_ack(batch, status, response)
    return {
        "stream_id": stream_id, "replayed": batch.item_count,
        "first_sequence": batch.first_sequence, "last_sequence": batch.last_sequence,
        "receiver_sequence": ack.highest_contiguous_sequence,
        "accepted": ack.accepted_count, "duplicates": ack.duplicate_count,
    }


def rearm_verified_gap(connection, *, stream_id: str, sender_sequence: int) -> int:
    """Re-arm only gap failures after a separately verified receiver prefix.

    Attempt counts, failure history and existing ACKs are retained. A concurrent
    sender progress/change aborts the compare-and-set transaction.
    """
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT highest_contiguous_sequence FROM market_data.market_fact_delivery_checkpoints "
                "WHERE stream_id=%s FOR UPDATE", (stream_id,),
            )
            row = cursor.fetchone()
            if row is None or int(row[0]) != sender_sequence:
                raise FactRecoveryError("REPLAY_SENDER_CHECKPOINT_CHANGED")
            cursor.execute(
                """SELECT delivery_sequence,last_reason_code
                   FROM market_data.market_fact_outbox
                   WHERE stream_id=%s AND acknowledged_at_utc IS NULL
                     AND dead_lettered_at_utc IS NOT NULL
                   ORDER BY delivery_sequence LIMIT 101 FOR UPDATE""", (stream_id,),
            )
            rows = cursor.fetchall()
            if (not rows or len(rows) > 100
                    or [int(r[0]) for r in rows] != list(range(sender_sequence + 1, sender_sequence + 1 + len(rows)))
                    or any(r[1] != "SEQUENCE_GAP" for r in rows)):
                raise FactRecoveryError("REPLAY_DEAD_HEAD_SCOPE_CHANGED")
            last = int(rows[-1][0])
            cursor.execute(
                """UPDATE market_data.market_fact_dead_letters
                   SET repaired_at_utc=clock_timestamp()
                   WHERE stream_id=%s AND delivery_sequence BETWEEN %s AND %s
                     AND reason_code='SEQUENCE_GAP' AND repaired_at_utc IS NULL""",
                (stream_id, sender_sequence + 1, last),
            )
            if cursor.rowcount != len(rows):
                raise FactRecoveryError("REPLAY_FAILURE_AUDIT_MISMATCH")
            cursor.execute(
                """UPDATE market_data.market_fact_outbox
                   SET dead_lettered_at_utc=NULL,next_attempt_at_utc=clock_timestamp(),
                       last_reason_code='SEQUENCE_GAP_REPLAY_VERIFIED'
                   WHERE stream_id=%s AND delivery_sequence BETWEEN %s AND %s
                     AND acknowledged_at_utc IS NULL AND last_reason_code='SEQUENCE_GAP'""",
                (stream_id, sender_sequence + 1, last),
            )
            if cursor.rowcount != len(rows):
                raise FactRecoveryError("REPLAY_OUTBOX_SCOPE_CHANGED")
    return len(rows)


def main() -> int:
    """Run inside the existing sender environment; dry-run unless --apply."""
    import argparse
    import json
    import os
    from core.market_intelligence.market_fact_sync import _postgres_connection
    from core.market_intelligence.private_market_transport import (
        FACT_PATH, client_tls_context, post_document, read_key,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", required=True)
    parser.add_argument("--receiver-sequence", type=int, required=True)
    parser.add_argument("--sender-sequence", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 0 < args.sender_sequence - args.receiver_sequence <= 1000:
        raise FactRecoveryError("REPLAY_OPERATOR_SCOPE_TOO_LARGE")
    connection = _postgres_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout='15s'")
            cursor.execute("SELECT pg_try_advisory_lock(hashtext('market-fact-replay'),hashtext(%s))", (args.stream,))
            if not cursor.fetchone()[0]:
                raise FactRecoveryError("REPLAY_ALREADY_RUNNING")
            cursor.execute("SELECT highest_contiguous_sequence FROM market_data.market_fact_delivery_checkpoints WHERE stream_id=%s", (args.stream,))
            row = cursor.fetchone()
            if row is None or int(row[0]) != args.sender_sequence:
                raise FactRecoveryError("REPLAY_SENDER_CHECKPOINT_CHANGED")
        connection.commit()
        sender_id = os.environ.get("MARKET_FACT_SENDER_INSTANCE_ID", "web-market-sync-1")
        next_sequence = args.receiver_sequence
        reports = []
        # Preflight the entire bounded prefix before the first external write.
        while next_sequence < args.sender_sequence:
            batch = load_acknowledged_replay(
                connection, stream_id=args.stream, receiver_sequence=next_sequence,
                sender_sequence=args.sender_sequence, sender_instance_id=sender_id,
            )
            next_sequence = batch.last_sequence
        if not args.apply:
            print(json.dumps({"status": "READY", "stream": args.stream, "replay_count": args.sender_sequence - args.receiver_sequence}))
            return 0
        tls = client_tls_context(
            ca=os.environ.get("MARKET_TRANSPORT_CA_PATH", "/run/secrets/market_transport_ca"),
            cert=os.environ.get("MARKET_TRANSPORT_CERT_PATH", "/run/secrets/market_web_transport_cert"),
            key=os.environ.get("MARKET_TRANSPORT_KEY_PATH", "/run/secrets/market_web_transport_key"),
        )
        hmac_key = read_key(os.environ.get("MARKET_HMAC_ACTIVE_PATH", "/run/secrets/market_hmac_active"))

        def send(document):
            return post_document(
                host=os.environ["MARKET_FACT_RECEIVER_HOST"],
                port=int(os.environ.get("MARKET_FACT_RECEIVER_PORT", "9443")),
                path=FACT_PATH, document=document,
                key_id=os.environ.get("MARKET_HMAC_ACTIVE_KEY_ID", "active-v1"),
                hmac_key=hmac_key, tls_context=tls, timeout_seconds=5.0,
            )

        next_sequence = args.receiver_sequence
        while next_sequence < args.sender_sequence:
            report = replay_acknowledged_prefix(
                connection, stream_id=args.stream, receiver_sequence=next_sequence,
                sender_sequence=args.sender_sequence, sender_instance_id=sender_id, send=send,
            )
            reports.append(report)
            next_sequence = int(report["receiver_sequence"])
        rearmed = rearm_verified_gap(connection, stream_id=args.stream, sender_sequence=args.sender_sequence)
        print(json.dumps({"status": "REPLAY_ACKNOWLEDGED", "batches": reports, "rearmed": rearmed}))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    import json
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Contract validation exceptions can embed envelopes. Emit only the
        # fixed, payload-free refusal code or the exception class.
        print(json.dumps({"status": "BLOCKED", "reason": str(exc) if isinstance(exc, FactRecoveryError) else type(exc).__name__}))
        raise SystemExit(2)
