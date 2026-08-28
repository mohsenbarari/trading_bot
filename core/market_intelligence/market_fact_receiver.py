"""Durable, idempotent receiver store for private Market Fact deliveries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Mapping

from pydantic import ValidationError

from .private_pipeline_contracts import (
    MarketFactAckV1,
    MarketFactBatchV1,
)


RECEIVER_SCHEMA = "market_fact_receiver/1.0"
ADAPTER_WATERMARK_SCHEMA = "market_fact_adapter_watermark/1.0"
DEFAULT_PAYLOAD_RETENTION_SECONDS = 259_200
MAX_COMPACTION_BATCH_ROWS = 10_000


class ReceiverCompactionError(RuntimeError):
    """A downstream watermark cannot safely authorize payload compaction."""


@dataclass(frozen=True, slots=True)
class ReceiverCompactionReport:
    delivery_payloads: int
    latest_payloads: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def connect_receiver(path: Path | str) -> sqlite3.Connection:
    database = Path(path)
    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    initialize_receiver(connection)
    return connection


def initialize_receiver(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS fact_deliveries (
            stream_id TEXT NOT NULL,
            delivery_sequence INTEGER NOT NULL CHECK(delivery_sequence > 0),
            fact_id TEXT NOT NULL,
            fact_revision INTEGER NOT NULL CHECK(fact_revision > 0),
            payload_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            received_at_utc TEXT NOT NULL,
            payload_compacted_at_utc TEXT,
            PRIMARY KEY(stream_id, delivery_sequence),
            UNIQUE(fact_id, fact_revision)
        );
        CREATE TABLE IF NOT EXISTS fact_latest (
            fact_id TEXT PRIMARY KEY,
            stream_id TEXT NOT NULL,
            source_sequence INTEGER NOT NULL CHECK(source_sequence > 0),
            fact_revision INTEGER NOT NULL CHECK(fact_revision > 0),
            payload_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            received_at_utc TEXT NOT NULL,
            payload_compacted_at_utc TEXT,
            UNIQUE(stream_id, source_sequence)
        );
        CREATE TABLE IF NOT EXISTS fact_checkpoints (
            stream_id TEXT PRIMARY KEY,
            highest_contiguous_sequence INTEGER NOT NULL CHECK(highest_contiguous_sequence >= 0),
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fact_rejections (
            rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_id TEXT,
            first_sequence INTEGER,
            last_sequence INTEGER,
            batch_id TEXT,
            reason_code TEXT NOT NULL,
            body_hash TEXT NOT NULL,
            rejected_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS fact_rejections_stream_idx
        ON fact_rejections(stream_id, rejected_at_utc);
        CREATE TABLE IF NOT EXISTS receiver_counters (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            accepted_count INTEGER NOT NULL CHECK(accepted_count >= 0),
            duplicate_count INTEGER NOT NULL CHECK(duplicate_count >= 0),
            rejection_count INTEGER NOT NULL CHECK(rejection_count >= 0)
        );
        INSERT OR IGNORE INTO receiver_counters(
            singleton,accepted_count,duplicate_count,rejection_count
        ) VALUES(1,0,0,0);
        CREATE TABLE IF NOT EXISTS receiver_status_counts (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            delivery_count INTEGER NOT NULL CHECK(delivery_count >= 0),
            fact_count INTEGER NOT NULL CHECK(fact_count >= 0)
        );
        CREATE TRIGGER IF NOT EXISTS receiver_delivery_count_insert
        AFTER INSERT ON fact_deliveries
        BEGIN
          UPDATE receiver_status_counts
          SET delivery_count=delivery_count+1
          WHERE singleton=1;
        END;
        CREATE TRIGGER IF NOT EXISTS receiver_fact_count_insert
        AFTER INSERT ON fact_latest
        BEGIN
          UPDATE receiver_status_counts
          SET fact_count=fact_count+1
          WHERE singleton=1;
        END;
        INSERT OR IGNORE INTO receiver_status_counts(
            singleton,delivery_count,fact_count
        ) SELECT 1,
            (SELECT COUNT(*) FROM fact_deliveries),
            (SELECT COUNT(*) FROM fact_latest);
        CREATE TABLE IF NOT EXISTS transport_nonces (
            key_id TEXT NOT NULL,
            nonce TEXT NOT NULL,
            accepted_at_epoch INTEGER NOT NULL,
            expires_at_epoch INTEGER NOT NULL,
            PRIMARY KEY(key_id, nonce)
        );
        CREATE INDEX IF NOT EXISTS transport_nonces_expiry_idx
        ON transport_nonces(expires_at_epoch);
        """
    )
    _initialize_payload_compaction(connection)


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _initialize_payload_compaction(connection: sqlite3.Connection) -> None:
    """Upgrade old receiver databases without rebuilding their large ledgers."""

    if "payload_compacted_at_utc" not in _column_names(
        connection, "fact_deliveries"
    ):
        connection.execute(
            "ALTER TABLE fact_deliveries "
            "ADD COLUMN payload_compacted_at_utc TEXT"
        )
    if "payload_compacted_at_utc" not in _column_names(connection, "fact_latest"):
        connection.execute(
            "ALTER TABLE fact_latest ADD COLUMN payload_compacted_at_utc TEXT"
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS receiver_compaction_cursors (
            stream_id TEXT PRIMARY KEY,
            highest_examined_sequence INTEGER NOT NULL
                CHECK(highest_examined_sequence>=0),
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS receiver_compaction_status (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            compacted_delivery_count INTEGER NOT NULL
                CHECK(compacted_delivery_count>=0),
            compacted_fact_count INTEGER NOT NULL
                CHECK(compacted_fact_count>=0),
            last_compacted_at_utc TEXT
        );
        INSERT OR IGNORE INTO receiver_compaction_status(
            singleton,compacted_delivery_count,compacted_fact_count,
            last_compacted_at_utc
        ) VALUES(1,0,0,NULL);
        CREATE TRIGGER IF NOT EXISTS receiver_delivery_compacted
        AFTER UPDATE OF payload_compacted_at_utc ON fact_deliveries
        WHEN OLD.payload_compacted_at_utc IS NULL
             AND NEW.payload_compacted_at_utc IS NOT NULL
        BEGIN
          UPDATE receiver_compaction_status
          SET compacted_delivery_count=compacted_delivery_count+1,
              last_compacted_at_utc=NEW.payload_compacted_at_utc
          WHERE singleton=1;
        END;
        CREATE TRIGGER IF NOT EXISTS receiver_fact_compacted
        AFTER UPDATE OF payload_compacted_at_utc ON fact_latest
        WHEN OLD.payload_compacted_at_utc IS NULL
             AND NEW.payload_compacted_at_utc IS NOT NULL
        BEGIN
          UPDATE receiver_compaction_status
          SET compacted_fact_count=compacted_fact_count+1,
              last_compacted_at_utc=NEW.payload_compacted_at_utc
          WHERE singleton=1;
        END;
        CREATE TRIGGER IF NOT EXISTS receiver_fact_rehydrated
        AFTER UPDATE OF payload_compacted_at_utc ON fact_latest
        WHEN OLD.payload_compacted_at_utc IS NOT NULL
             AND NEW.payload_compacted_at_utc IS NULL
        BEGIN
          UPDATE receiver_compaction_status
          SET compacted_fact_count=compacted_fact_count-1
          WHERE singleton=1;
        END;
        """
    )


def load_adapter_watermark(path: Path | str) -> dict[str, int]:
    """Load the adapter's durable, payload-free consumption watermark."""

    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiverCompactionError("receiver_compaction_watermark_unavailable") from exc
    if not isinstance(document, dict) or document.get("schema") != ADAPTER_WATERMARK_SCHEMA:
        raise ReceiverCompactionError("receiver_compaction_watermark_invalid")
    streams = document.get("streams")
    if not isinstance(streams, dict) or len(streams) > 128:
        raise ReceiverCompactionError("receiver_compaction_watermark_invalid")
    normalized: dict[str, int] = {}
    for stream_id, sequence in streams.items():
        if (
            not isinstance(stream_id, str)
            or not stream_id
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
        ):
            raise ReceiverCompactionError("receiver_compaction_watermark_invalid")
        normalized[stream_id] = sequence
    return normalized


def compact_consumed_payloads(
    connection: sqlite3.Connection,
    *,
    applied_checkpoints: Mapping[str, int],
    now: datetime | None = None,
    retention_seconds: int = DEFAULT_PAYLOAD_RETENTION_SECONDS,
    max_rows: int = 2_000,
) -> ReceiverCompactionReport:
    """Redact consumed payload copies while preserving transport identity forever.

    The adapter's permanent Market Store owns the economic history.  The
    receiver keeps stream/sequence/fact/revision/hash metadata so lost-ACK
    replay and conflict detection remain exact after payload compaction.
    """

    if not 3_600 <= retention_seconds <= 604_800:
        raise ReceiverCompactionError("receiver_compaction_retention_invalid")
    if not 1 <= max_rows <= MAX_COMPACTION_BATCH_ROWS:
        raise ReceiverCompactionError("receiver_compaction_batch_invalid")
    current = (now or _utc_now()).astimezone(timezone.utc)
    cutoff = (current - timedelta(seconds=retention_seconds)).isoformat().replace(
        "+00:00", "Z"
    )
    compacted_at = current.isoformat().replace("+00:00", "Z")
    receiver_checkpoints = {
        str(row["stream_id"]): int(row["highest_contiguous_sequence"])
        for row in connection.execute(
            "SELECT stream_id,highest_contiguous_sequence FROM fact_checkpoints"
        ).fetchall()
    }
    for stream_id, sequence in applied_checkpoints.items():
        if (
            stream_id not in receiver_checkpoints
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
            or sequence > receiver_checkpoints[stream_id]
        ):
            raise ReceiverCompactionError("receiver_compaction_checkpoint_invalid")

    candidates: list[sqlite3.Row] = []
    cursor_updates: dict[str, int] = {}
    scanned_rows = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for stream_id in sorted(applied_checkpoints):
            remaining = max_rows - scanned_rows
            if remaining <= 0:
                break
            cursor_row = connection.execute(
                "SELECT highest_examined_sequence "
                "FROM receiver_compaction_cursors WHERE stream_id=?",
                (stream_id,),
            ).fetchone()
            cursor = int(cursor_row[0]) if cursor_row else 0
            if int(applied_checkpoints[stream_id]) < cursor:
                raise ReceiverCompactionError(
                    "receiver_compaction_watermark_regression"
                )
            rows = connection.execute(
                """
                SELECT rowid,delivery_sequence,fact_id,fact_revision,
                       received_at_utc,payload_compacted_at_utc
                FROM fact_deliveries
                WHERE stream_id=? AND delivery_sequence>?
                  AND delivery_sequence<=?
                ORDER BY delivery_sequence
                LIMIT ?
                """,
                (
                    stream_id,
                    cursor,
                    int(applied_checkpoints[stream_id]),
                    remaining,
                ),
            ).fetchall()
            examined = cursor
            for row in rows:
                scanned_rows += 1
                # received_at is assigned in the same transaction that
                # advances this stream's contiguous delivery sequence.  Stop
                # at the first row still inside the safety window so a cursor
                # never skips payload that is not yet eligible.
                if str(row["received_at_utc"]) > cutoff:
                    break
                examined = int(row["delivery_sequence"])
                if row["payload_compacted_at_utc"] is None:
                    candidates.append(row)
            if examined > cursor:
                cursor_updates[stream_id] = examined
        connection.executemany(
            """
            INSERT INTO receiver_compaction_cursors(
                stream_id,highest_examined_sequence,updated_at_utc
            ) VALUES(?,?,?)
            ON CONFLICT(stream_id) DO UPDATE SET
                highest_examined_sequence=excluded.highest_examined_sequence,
                updated_at_utc=excluded.updated_at_utc
            WHERE excluded.highest_examined_sequence>=
                  receiver_compaction_cursors.highest_examined_sequence
            """,
            (
                (stream_id, sequence, compacted_at)
                for stream_id, sequence in cursor_updates.items()
            )
        )
        if not candidates:
            connection.commit()
            return ReceiverCompactionReport(0, 0)
        connection.executemany(
            """
            UPDATE fact_deliveries
            SET payload_json='',payload_compacted_at_utc=?
            WHERE rowid=? AND payload_compacted_at_utc IS NULL
            """,
            ((compacted_at, int(row["rowid"])) for row in candidates),
        )
        latest_cursor = connection.executemany(
            """
            UPDATE fact_latest
            SET payload_json='',payload_compacted_at_utc=?
            WHERE fact_id=? AND fact_revision=?
              AND payload_compacted_at_utc IS NULL
            """,
            (
                (compacted_at, str(row["fact_id"]), int(row["fact_revision"]))
                for row in candidates
            ),
        )
        latest_count = latest_cursor.rowcount
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return ReceiverCompactionReport(len(candidates), latest_count)


def _rejected_ack(
    batch: MarketFactBatchV1,
    *,
    highest: int,
    reason_code: str,
) -> MarketFactAckV1:
    return MarketFactAckV1(
        contract="market_fact_ack/1.0",
        batch_id=batch.batch_id,
        stream_id=batch.stream_id,
        status="REJECTED",
        highest_contiguous_sequence=highest,
        received_count=len(batch.items),
        accepted_count=0,
        duplicate_count=0,
        rejected_count=len(batch.items),
        rejection_reason_codes=(reason_code,),
        receiver_timestamp_utc=_utc_now(),
    )


def record_rejection(
    connection: sqlite3.Connection,
    *,
    reason_code: str,
    body_hash: str,
    batch: MarketFactBatchV1 | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO fact_rejections(
            stream_id,first_sequence,last_sequence,batch_id,reason_code,
            body_hash,rejected_at_utc
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            batch.stream_id if batch else None,
            batch.first_sequence if batch else None,
            batch.last_sequence if batch else None,
            batch.batch_id if batch else None,
            reason_code,
            body_hash,
            _utc_text(),
        ),
    )
    connection.execute(
        "UPDATE receiver_counters SET rejection_count=rejection_count+1 "
        "WHERE singleton=1"
    )


def apply_fact_batch(
    connection: sqlite3.Connection,
    document: Mapping[str, object],
) -> tuple[int, dict[str, object]]:
    try:
        batch = MarketFactBatchV1.model_validate(document)
    except ValidationError:
        return 422, {"status": "REJECTED", "reason_code": "CONTRACT_INVALID"}

    accepted = 0
    duplicate = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT highest_contiguous_sequence FROM fact_checkpoints WHERE stream_id=?",
            (batch.stream_id,),
        ).fetchone()
        highest = int(row[0]) if row else 0
        if batch.first_sequence > highest + 1:
            ack = _rejected_ack(
                batch, highest=highest, reason_code="SEQUENCE_GAP"
            )
            connection.rollback()
            return 409, ack.model_dump(mode="json")

        for delivery in batch.items:
            sequence = delivery.delivery_sequence
            fact = delivery.fact
            existing_delivery = connection.execute(
                "SELECT fact_id,fact_revision,payload_hash FROM fact_deliveries "
                "WHERE stream_id=? AND delivery_sequence=?",
                (batch.stream_id, sequence),
            ).fetchone()
            if existing_delivery is not None:
                if (
                    str(existing_delivery["fact_id"]) != fact.fact_id
                    or int(existing_delivery["fact_revision"]) != fact.fact_revision
                    or str(existing_delivery["payload_hash"]) != fact.payload_hash
                ):
                    ack = _rejected_ack(
                        batch, highest=highest, reason_code="SEQUENCE_CONFLICT"
                    )
                    connection.rollback()
                    return 409, ack.model_dump(mode="json")
                duplicate += 1
                highest = max(highest, sequence)
                continue
            if sequence != highest + 1:
                ack = _rejected_ack(
                    batch, highest=highest, reason_code="SEQUENCE_GAP"
                )
                connection.rollback()
                return 409, ack.model_dump(mode="json")

            latest = connection.execute(
                "SELECT fact_revision,payload_hash,stream_id,source_sequence "
                "FROM fact_latest WHERE fact_id=?",
                (fact.fact_id,),
            ).fetchone()
            if latest is not None:
                if fact.fact_revision <= int(latest["fact_revision"]):
                    ack = _rejected_ack(
                        batch, highest=highest, reason_code="FACT_REVISION_REGRESSION"
                    )
                    connection.rollback()
                    return 409, ack.model_dump(mode="json")
                if (
                    str(latest["stream_id"]) != fact.stream_id
                    or int(latest["source_sequence"]) != fact.source_sequence
                ):
                    ack = _rejected_ack(
                        batch, highest=highest, reason_code="FACT_IDENTITY_CONFLICT"
                    )
                    connection.rollback()
                    return 409, ack.model_dump(mode="json")

            payload_json = fact.model_dump_json()
            received_at = _utc_text()
            try:
                connection.execute(
                    """
                    INSERT INTO fact_deliveries(
                        stream_id,delivery_sequence,fact_id,fact_revision,
                        payload_hash,payload_json,received_at_utc
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        batch.stream_id,
                        sequence,
                        fact.fact_id,
                        fact.fact_revision,
                        fact.payload_hash,
                        payload_json,
                        received_at,
                    ),
                )
            except sqlite3.IntegrityError:
                ack = _rejected_ack(
                    batch, highest=highest, reason_code="FACT_REVISION_RESEQUENCED"
                )
                connection.rollback()
                return 409, ack.model_dump(mode="json")
            connection.execute(
                """
                INSERT INTO fact_latest(
                    fact_id,stream_id,source_sequence,fact_revision,payload_hash,
                    payload_json,received_at_utc
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(fact_id) DO UPDATE SET
                    fact_revision=excluded.fact_revision,
                    payload_hash=excluded.payload_hash,
                    payload_json=excluded.payload_json,
                    received_at_utc=excluded.received_at_utc,
                    payload_compacted_at_utc=NULL
                """,
                (
                    fact.fact_id,
                    fact.stream_id,
                    fact.source_sequence,
                    fact.fact_revision,
                    fact.payload_hash,
                    payload_json,
                    received_at,
                ),
            )
            highest = sequence
            accepted += 1

        connection.execute(
            """
            INSERT INTO fact_checkpoints(
                stream_id,highest_contiguous_sequence,updated_at_utc
            ) VALUES(?,?,?)
            ON CONFLICT(stream_id) DO UPDATE SET
                highest_contiguous_sequence=excluded.highest_contiguous_sequence,
                updated_at_utc=excluded.updated_at_utc
            """,
            (batch.stream_id, highest, _utc_text()),
        )
        connection.execute(
            "UPDATE receiver_counters SET "
            "accepted_count=accepted_count+?,duplicate_count=duplicate_count+? "
            "WHERE singleton=1",
            (accepted, duplicate),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

    ack = MarketFactAckV1(
        contract="market_fact_ack/1.0",
        batch_id=batch.batch_id,
        stream_id=batch.stream_id,
        status="ACK",
        highest_contiguous_sequence=highest,
        received_count=len(batch.items),
        accepted_count=accepted,
        duplicate_count=duplicate,
        rejected_count=0,
        rejection_reason_codes=(),
        receiver_timestamp_utc=_utc_now(),
    )
    return 200, ack.model_dump(mode="json")


def receiver_metrics(connection: sqlite3.Connection) -> dict[str, object]:
    checkpoints = {
        str(row["stream_id"]): int(row["highest_contiguous_sequence"])
        for row in connection.execute(
            "SELECT stream_id,highest_contiguous_sequence FROM fact_checkpoints"
        )
    }
    counters = connection.execute(
        "SELECT accepted_count,duplicate_count,rejection_count "
        "FROM receiver_counters WHERE singleton=1"
    ).fetchone()
    status_counts = connection.execute(
        "SELECT delivery_count,fact_count "
        "FROM receiver_status_counts WHERE singleton=1"
    ).fetchone()
    compaction = connection.execute(
        "SELECT compacted_delivery_count,compacted_fact_count,last_compacted_at_utc "
        "FROM receiver_compaction_status WHERE singleton=1"
    ).fetchone()
    return {
        "schema": RECEIVER_SCHEMA,
        "streams": checkpoints,
        "delivery_count": int(status_counts[0]),
        "fact_count": int(status_counts[1]),
        "accepted_count": int(counters[0]),
        "duplicate_count": int(counters[1]),
        "rejection_count": int(counters[2]),
        "compacted_delivery_count": int(compaction[0]),
        "compacted_fact_count": int(compaction[1]),
        "last_compacted_at_utc": compaction[2],
    }
