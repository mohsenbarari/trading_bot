"""Transactional Market Fact outbox sender for the private network lane."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import os
from pathlib import Path
import random
import threading
import time
from typing import Callable, Mapping

from pydantic import ValidationError

from .private_market_transport import (
    FACT_PATH,
    MarketTransportError,
    client_tls_context,
    post_document,
    read_key,
)
from .private_pipeline_contracts import (
    MarketFactAckV1,
    MarketFactBatchV1,
    MarketFactDeliveryV1,
    MarketFactV1,
    batch_items_hash,
    content_hash,
)


SYNC_SCHEMA = "market_fact_sync/1.0"
MAX_BATCH_ITEMS = 100
MAX_BATCH_DOCUMENT_BYTES = 768 * 1024
DEFAULT_FLUSH_SECONDS = 0.25
MAX_BACKOFF_SECONDS = 30.0
ALERT_AFTER_ATTEMPTS = 8


class MarketFactSyncError(RuntimeError):
    """An operator-safe sync failure without envelope contents."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat().replace("+00:00", "Z")


def _postgres_connection():
    import psycopg2

    password_path = os.environ.get(
        "MARKET_POSTGRES_PASSWORD_FILE", "/run/secrets/market_postgres_password"
    )
    password = Path(password_path).read_text(encoding="utf-8").strip()
    if not password:
        raise MarketFactSyncError("market_fact_sync_postgres_secret_invalid")
    try:
        connection = psycopg2.connect(
            host=os.environ.get("MARKET_POSTGRES_HOST", "market-database"),
            port=int(os.environ.get("MARKET_POSTGRES_PORT", "5432")),
            user=os.environ.get("MARKET_POSTGRES_USER", "market_data"),
            password=password,
            dbname=os.environ.get("MARKET_POSTGRES_DB", "market_archive"),
            connect_timeout=5,
            application_name="market-fact-sync-worker",
        )
    except psycopg2.Error as exc:
        raise MarketFactSyncError("market_fact_sync_database_unavailable") from exc
    return connection


def _batch_id(
    *, stream_id: str, first_sequence: int, last_sequence: int, items_hash: str
) -> str:
    return content_hash(
        {
            "contract": "market_fact_batch_identity/1.0",
            "stream_id": stream_id,
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
            "items_hash": items_hash,
        }
    )


def load_next_batch(
    connection,
    *,
    sender_instance_id: str,
    max_items: int = MAX_BATCH_ITEMS,
    max_document_bytes: int = MAX_BATCH_DOCUMENT_BYTES,
) -> MarketFactBatchV1 | None:
    """Load one stream head without skipping an unavailable earlier item."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH stream_heads AS (
                SELECT DISTINCT ON (o.stream_id)
                       o.stream_id,o.delivery_sequence,o.created_at_utc,
                       o.next_attempt_at_utc,o.dead_lettered_at_utc,
                       COALESCE(c.highest_contiguous_sequence,0) AS checkpoint
                FROM market_data.market_fact_outbox o
                LEFT JOIN market_data.market_fact_delivery_checkpoints c
                  ON c.stream_id=o.stream_id
                WHERE o.acknowledged_at_utc IS NULL
                ORDER BY o.stream_id,o.delivery_sequence
            )
            SELECT stream_id,checkpoint,delivery_sequence
            FROM stream_heads
            WHERE dead_lettered_at_utc IS NULL
              AND next_attempt_at_utc <= clock_timestamp()
              AND delivery_sequence=checkpoint+1
            ORDER BY created_at_utc,stream_id
            LIMIT 1
            """
        )
        head = cursor.fetchone()
        if head is None:
            connection.rollback()
            return None
        stream_id = str(head[0])
        checkpoint = int(head[1])
        first_pending = int(head[2])
        if first_pending != checkpoint + 1:
            connection.rollback()
            raise MarketFactSyncError("market_fact_sync_sender_sequence_gap")
        cursor.execute(
            """
            SELECT delivery_sequence,envelope
            FROM market_data.market_fact_outbox
            WHERE stream_id=%s
              AND delivery_sequence >= %s
              AND acknowledged_at_utc IS NULL
              AND dead_lettered_at_utc IS NULL
              AND next_attempt_at_utc <= clock_timestamp()
            ORDER BY delivery_sequence
            LIMIT %s
            """,
            (stream_id, checkpoint + 1, min(max_items, MAX_BATCH_ITEMS)),
        )
        rows = cursor.fetchall()
    connection.rollback()
    deliveries: list[MarketFactDeliveryV1] = []
    expected = checkpoint + 1
    for sequence_value, envelope in rows:
        sequence = int(sequence_value)
        if sequence != expected:
            break
        try:
            fact = MarketFactV1.model_validate(envelope)
        except ValidationError as exc:
            raise MarketFactSyncError("market_fact_sync_outbox_contract_invalid") from exc
        candidate = MarketFactDeliveryV1(
            delivery_sequence=sequence,
            fact=fact,
        )
        candidate_items = (*deliveries, candidate)
        candidate_hash = batch_items_hash(candidate_items)
        candidate_batch = MarketFactBatchV1(
            contract="market_fact_batch/1.0",
            batch_id=_batch_id(
                stream_id=stream_id,
                first_sequence=candidate_items[0].delivery_sequence,
                last_sequence=candidate_items[-1].delivery_sequence,
                items_hash=candidate_hash,
            ),
            schema_version="1.0",
            stream_id=stream_id,
            first_sequence=candidate_items[0].delivery_sequence,
            last_sequence=candidate_items[-1].delivery_sequence,
            created_at_utc=_utc_now(),
            item_count=len(candidate_items),
            items_hash=candidate_hash,
            sender_instance_id=sender_instance_id,
            items=candidate_items,
        )
        size = len(candidate_batch.model_dump_json().encode("utf-8"))
        if size > max_document_bytes:
            if not deliveries:
                raise MarketFactSyncError("market_fact_sync_single_item_too_large")
            break
        deliveries.append(candidate)
        expected += 1
    if not deliveries:
        return None
    items_hash = batch_items_hash(deliveries)
    return MarketFactBatchV1(
        contract="market_fact_batch/1.0",
        batch_id=_batch_id(
            stream_id=stream_id,
            first_sequence=deliveries[0].delivery_sequence,
            last_sequence=deliveries[-1].delivery_sequence,
            items_hash=items_hash,
        ),
        schema_version="1.0",
        stream_id=stream_id,
        first_sequence=deliveries[0].delivery_sequence,
        last_sequence=deliveries[-1].delivery_sequence,
        created_at_utc=_utc_now(),
        item_count=len(deliveries),
        items_hash=items_hash,
        sender_instance_id=sender_instance_id,
        items=tuple(deliveries),
    )


def acknowledge_batch(connection, batch: MarketFactBatchV1, ack: MarketFactAckV1) -> None:
    if (
        ack.batch_id != batch.batch_id
        or ack.stream_id != batch.stream_id
        or ack.status != "ACK"
        or ack.highest_contiguous_sequence < batch.last_sequence
        or ack.rejected_count
    ):
        raise MarketFactSyncError("market_fact_sync_ack_invalid")
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE market_data.market_fact_outbox
                SET acknowledged_at_utc=clock_timestamp(),last_reason_code=NULL
                WHERE stream_id=%s
                  AND delivery_sequence BETWEEN %s AND %s
                  AND acknowledged_at_utc IS NULL
                """,
                (batch.stream_id, batch.first_sequence, batch.last_sequence),
            )
            if cursor.rowcount != batch.item_count:
                raise MarketFactSyncError("market_fact_sync_ack_update_mismatch")
            cursor.execute(
                """
                INSERT INTO market_data.market_fact_delivery_checkpoints(
                    stream_id,highest_contiguous_sequence,last_batch_id,updated_at_utc
                ) VALUES(%s,%s,decode(%s,'hex'),clock_timestamp())
                ON CONFLICT(stream_id) DO UPDATE SET
                    highest_contiguous_sequence=GREATEST(
                        market_data.market_fact_delivery_checkpoints.highest_contiguous_sequence,
                        excluded.highest_contiguous_sequence
                    ),
                    last_batch_id=excluded.last_batch_id,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (batch.stream_id, batch.last_sequence, batch.batch_id),
            )


def record_batch_failure(
    connection,
    batch: MarketFactBatchV1,
    *,
    reason_code: str,
    permanent: bool,
    random_value: float | None = None,
) -> int:
    reason = str(reason_code).strip().upper()[:96] or "DELIVERY_FAILED"
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT attempt_count
                FROM market_data.market_fact_outbox
                WHERE stream_id=%s AND delivery_sequence BETWEEN %s AND %s
                ORDER BY attempt_count DESC
                LIMIT 1
                FOR UPDATE
                """,
                (batch.stream_id, batch.first_sequence, batch.last_sequence),
            )
            row = cursor.fetchone()
            attempt = int(row[0]) + 1 if row else 1
            jitter = random.random() if random_value is None else random_value
            delay = min(MAX_BACKOFF_SECONDS, 0.25 * (2 ** min(attempt - 1, 7)))
            delay *= 0.8 + (0.4 * max(0.0, min(1.0, jitter)))
            cursor.execute(
                """
                UPDATE market_data.market_fact_outbox
                SET attempt_count=attempt_count+1,
                    next_attempt_at_utc=clock_timestamp()+(%s * interval '1 second'),
                    last_reason_code=%s,
                    dead_lettered_at_utc=CASE WHEN %s THEN clock_timestamp()
                                             ELSE dead_lettered_at_utc END
                WHERE stream_id=%s AND delivery_sequence BETWEEN %s AND %s
                  AND acknowledged_at_utc IS NULL
                """,
                (
                    delay,
                    reason,
                    permanent,
                    batch.stream_id,
                    batch.first_sequence,
                    batch.last_sequence,
                ),
            )
            if permanent:
                cursor.execute(
                    """
                    INSERT INTO market_data.market_fact_dead_letters(
                        stream_id,delivery_sequence,fact_id,fact_revision,
                        envelope_hash,reason_code,first_failed_at_utc,
                        last_failed_at_utc,failure_count
                    )
                    SELECT stream_id,delivery_sequence,fact_id,fact_revision,
                           envelope_hash,%s,clock_timestamp(),clock_timestamp(),1
                    FROM market_data.market_fact_outbox
                    WHERE stream_id=%s AND delivery_sequence BETWEEN %s AND %s
                    ON CONFLICT(stream_id,delivery_sequence) DO UPDATE SET
                        reason_code=excluded.reason_code,
                        last_failed_at_utc=excluded.last_failed_at_utc,
                        failure_count=market_data.market_fact_dead_letters.failure_count+1,
                        repaired_at_utc=NULL
                    """,
                    (reason, batch.stream_id, batch.first_sequence, batch.last_sequence),
                )
    return attempt


def outbox_metrics(connection) -> dict[str, object]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*),
                   COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-MIN(created_at_utc))),0),
                   COUNT(*) FILTER (WHERE attempt_count >= %s),
                   COUNT(*) FILTER (WHERE dead_lettered_at_utc IS NOT NULL)
            FROM market_data.market_fact_outbox
            WHERE acknowledged_at_utc IS NULL
            """,
            (ALERT_AFTER_ATTEMPTS,),
        )
        row = cursor.fetchone()
        cursor.execute(
            "SELECT COALESCE(SUM(attempt_count),0) FROM market_data.market_fact_outbox"
        )
        attempts = int(cursor.fetchone()[0])
    connection.rollback()
    return {
        "queue_depth": int(row[0]),
        "oldest_age_seconds": round(float(row[1]), 3),
        "retry_alert_count": int(row[2]),
        "dead_letter_count": int(row[3]),
        "send_attempt_count": attempts,
    }


def repair_dead_letter(connection, *, stream_id: str, delivery_sequence: int) -> None:
    """Re-arm exactly the blocked stream head without deleting audit evidence."""

    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.repaired_at_utc,
                       COALESCE(c.highest_contiguous_sequence,0)
                FROM market_data.market_fact_dead_letters d
                LEFT JOIN market_data.market_fact_delivery_checkpoints c
                  ON c.stream_id=d.stream_id
                WHERE d.stream_id=%s AND d.delivery_sequence=%s
                FOR UPDATE OF d
                """,
                (stream_id, delivery_sequence),
            )
            row = cursor.fetchone()
            if row is None:
                raise MarketFactSyncError("market_fact_dead_letter_not_found")
            if row[0] is not None:
                raise MarketFactSyncError("market_fact_dead_letter_already_repaired")
            if delivery_sequence != int(row[1]) + 1:
                raise MarketFactSyncError("market_fact_dead_letter_not_stream_head")
            cursor.execute(
                """
                UPDATE market_data.market_fact_outbox
                SET dead_lettered_at_utc=NULL,next_attempt_at_utc=clock_timestamp(),
                    attempt_count=0,last_reason_code='OPERATOR_REPAIRED'
                WHERE stream_id=%s AND delivery_sequence=%s
                  AND acknowledged_at_utc IS NULL
                """,
                (stream_id, delivery_sequence),
            )
            if cursor.rowcount != 1:
                raise MarketFactSyncError("market_fact_dead_letter_outbox_missing")
            cursor.execute(
                """
                UPDATE market_data.market_fact_dead_letters
                SET repaired_at_utc=clock_timestamp()
                WHERE stream_id=%s AND delivery_sequence=%s
                """,
                (stream_id, delivery_sequence),
            )


def run_sync_cycle(
    connection,
    *,
    sender_instance_id: str,
    send: Callable[[Mapping[str, object]], tuple[int, Mapping[str, object]]],
) -> dict[str, object]:
    batch = load_next_batch(connection, sender_instance_id=sender_instance_id)
    if batch is None:
        return {"sent": 0, "acknowledged": 0, "duplicates": 0, "rejected": 0}
    started = time.perf_counter()
    try:
        status, response = send(batch.model_dump(mode="json"))
    except MarketTransportError:
        attempt = record_batch_failure(
            connection, batch, reason_code="TRANSPORT_UNAVAILABLE", permanent=False
        )
        return {
            "sent": batch.item_count,
            "acknowledged": 0,
            "duplicates": 0,
            "rejected": 0,
            "attempt": attempt,
            "ack_latency_ms": None,
        }
    try:
        ack = MarketFactAckV1.model_validate(response)
    except ValidationError:
        attempt = record_batch_failure(
            connection, batch, reason_code="ACK_CONTRACT_INVALID", permanent=False
        )
        return {
            "sent": batch.item_count,
            "acknowledged": 0,
            "duplicates": 0,
            "rejected": 0,
            "attempt": attempt,
            "ack_latency_ms": None,
        }
    if status == 200 and ack.status == "ACK":
        acknowledge_batch(connection, batch, ack)
        return {
            "sent": batch.item_count,
            "acknowledged": ack.accepted_count + ack.duplicate_count,
            "duplicates": ack.duplicate_count,
            "rejected": 0,
            "ack_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    attempt = record_batch_failure(
        connection,
        batch,
        reason_code=(
            ack.rejection_reason_codes[0]
            if ack.rejection_reason_codes
            else "RECEIVER_REJECTED"
        ),
        permanent=status in {400, 409, 413, 422},
    )
    return {
        "sent": batch.item_count,
        "acknowledged": 0,
        "duplicates": 0,
        "rejected": ack.rejected_count,
        "attempt": attempt,
        "ack_latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def run_market_fact_sync_service(
    *,
    role: str,
    mode: str,
    release_sha: str,
    state_directory: Path,
    stop: threading.Event,
) -> int:
    if role != "market-fact-sync-worker" or mode not in {"fixture", "live"}:
        raise MarketFactSyncError("market_fact_sync_role_or_mode_invalid")
    host = os.environ.get("MARKET_FACT_RECEIVER_HOST", "").strip()
    if mode == "live" and not host:
        raise MarketFactSyncError("market_fact_sync_receiver_host_required")
    port = int(os.environ.get("MARKET_FACT_RECEIVER_PORT", "9443"))
    key_id = os.environ.get("MARKET_HMAC_ACTIVE_KEY_ID", "active-v1").strip()
    sender_instance_id = os.environ.get(
        "MARKET_FACT_SENDER_INSTANCE_ID", "web-market-sync-1"
    ).strip()
    if mode == "live":
        hmac_key = read_key(
            os.environ.get(
                "MARKET_HMAC_ACTIVE_PATH", "/run/secrets/market_hmac_active"
            )
        )
        tls = client_tls_context(
            ca=os.environ.get(
                "MARKET_TRANSPORT_CA_PATH", "/run/secrets/market_transport_ca"
            ),
            cert=os.environ.get(
                "MARKET_TRANSPORT_CERT_PATH", "/run/secrets/market_web_transport_cert"
            ),
            key=os.environ.get(
                "MARKET_TRANSPORT_KEY_PATH", "/run/secrets/market_web_transport_key"
            ),
        )
    else:
        hmac_key = b"fixture-market-hmac-key-material!!"
        tls = None
    state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    health_path = state_directory / "health.json"
    started_at = _utc_text()
    connection = _postgres_connection()
    aggregate = {"sent": 0, "acknowledged": 0, "duplicates": 0, "rejected": 0}
    ack_latencies: deque[float] = deque(maxlen=512)

    def sender(document: Mapping[str, object]) -> tuple[int, Mapping[str, object]]:
        if mode != "live" or tls is None:
            raise MarketTransportError("market_fact_sync_fixture_has_no_peer")
        return post_document(
            host=host,
            port=port,
            path=FACT_PATH,
            document=document,
            key_id=key_id,
            hmac_key=hmac_key,
            tls_context=tls,
            timeout_seconds=3.0,
        )

    from .private_pipeline_foundation import atomic_json_write

    try:
        while not stop.is_set():
            cycle = run_sync_cycle(
                connection,
                sender_instance_id=sender_instance_id,
                send=sender,
            )
            for field in aggregate:
                aggregate[field] += int(cycle.get(field, 0) or 0)
            if cycle.get("ack_latency_ms") is not None:
                ack_latencies.append(float(cycle["ack_latency_ms"]))
            metrics = outbox_metrics(connection)
            ordered_latencies = sorted(ack_latencies)

            def latency_percentile(ratio: float) -> float | None:
                if not ordered_latencies:
                    return None
                index = min(
                    len(ordered_latencies) - 1,
                    max(0, int(len(ordered_latencies) * ratio) - 1),
                )
                return round(ordered_latencies[index], 3)

            atomic_json_write(
                health_path,
                {
                    "schema": SYNC_SCHEMA,
                    "role": role,
                    "mode": mode,
                    "release_sha": release_sha,
                    "pid": os.getpid(),
                    "started_at_utc": started_at,
                    "updated_at_utc": _utc_text(),
                    "status": f"{mode}-ready",
                    "durable_write": True,
                    "private_transport_only": True,
                    "last_ack_latency_ms": (
                        round(ack_latencies[-1], 3) if ack_latencies else None
                    ),
                    "ack_latency_p95_ms": latency_percentile(0.95),
                    "ack_latency_p99_ms": latency_percentile(0.99),
                    "ack_latency_sample_count": len(ack_latencies),
                    **aggregate,
                    **metrics,
                },
            )
            if int(metrics["queue_depth"]) == 0:
                stop.wait(DEFAULT_FLUSH_SECONDS)
    finally:
        connection.close()
    return 0
