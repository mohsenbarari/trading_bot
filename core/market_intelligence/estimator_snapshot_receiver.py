"""Durable web-side projection for versioned estimator snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Mapping

from pydantic import ValidationError

from .private_pipeline_contracts import EstimatorSnapshotV1


SNAPSHOT_RECEIVER_SCHEMA = "estimator_snapshot_receiver/1.0"
DEFAULT_STALE_AFTER_SECONDS = 30


class EstimatorSnapshotReceiverError(RuntimeError):
    """Content-free receiver failure."""


def _utc(value: datetime | None = None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )


def _stamp(value: datetime | None = None) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def connect_snapshot_receiver(path: Path | str) -> sqlite3.Connection:
    database = Path(path)
    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    initialize_snapshot_receiver(connection)
    return connection


def initialize_snapshot_receiver(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS estimator_snapshot_receipts (
          feed_mode TEXT NOT NULL CHECK(feed_mode IN ('PRIVATE_SHADOW','PRIVATE_PRIMARY')),
          snapshot_version INTEGER NOT NULL CHECK(snapshot_version>0),
          snapshot_id TEXT NOT NULL,
          input_snapshot_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          received_at_utc TEXT NOT NULL,
          published_at_utc TEXT,
          PRIMARY KEY(feed_mode,snapshot_version),
          UNIQUE(feed_mode,snapshot_id)
        );
        CREATE TABLE IF NOT EXISTS estimator_snapshot_publication_outbox (
          event_id TEXT PRIMARY KEY,
          feed_mode TEXT NOT NULL,
          snapshot_version INTEGER NOT NULL,
          snapshot_id TEXT NOT NULL,
          published_at_utc TEXT NOT NULL,
          delivered_at_utc TEXT
        );
        CREATE TABLE IF NOT EXISTS estimator_snapshot_rejections (
          rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
          reason_code TEXT NOT NULL,
          body_hash TEXT NOT NULL,
          rejected_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transport_nonces (
          key_id TEXT NOT NULL,
          nonce TEXT NOT NULL,
          accepted_at_epoch INTEGER NOT NULL,
          expires_at_epoch INTEGER NOT NULL,
          PRIMARY KEY(key_id,nonce)
        );
        CREATE INDEX IF NOT EXISTS transport_nonces_expiry_idx
        ON transport_nonces(expires_at_epoch);
        """
    )


def record_snapshot_rejection(
    connection: sqlite3.Connection, *, reason_code: str, body_hash: str
) -> None:
    connection.execute(
        "INSERT INTO estimator_snapshot_rejections(reason_code,body_hash,rejected_at_utc) "
        "VALUES(?,?,?)",
        (str(reason_code)[:96], body_hash, _stamp()),
    )


def _fsync_append(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _lane_path(snapshot_root: Path, feed_mode: str) -> Path:
    return snapshot_root / (
        "latest-private-primary.json"
        if feed_mode == "PRIVATE_PRIMARY"
        else "latest-private-shadow.json"
    )


def _web_view(
    snapshot: EstimatorSnapshotV1,
    *,
    received_at_utc: str,
    published_at_utc: str,
) -> dict[str, object]:
    return {
        "contract": "estimator_snapshot_web_view/1.0",
        "snapshot_hash": snapshot.snapshot_id,
        "snapshot_version": snapshot.snapshot_version,
        "feed_mode": snapshot.feed_mode,
        "received_at_utc": received_at_utc,
        "published_at_utc": published_at_utc,
        "transport_state": "FRESH",
        "stale_after_seconds": DEFAULT_STALE_AFTER_SECONDS,
        "snapshot": snapshot.model_dump(mode="json"),
    }


def apply_estimator_snapshot(
    connection: sqlite3.Connection,
    document: Mapping[str, object],
    *,
    snapshot_root: Path,
    publication_events_path: Path,
) -> tuple[int, dict[str, object]]:
    from .private_pipeline_foundation import atomic_json_write

    try:
        snapshot = EstimatorSnapshotV1.model_validate(document)
    except ValidationError:
        return 422, {"status": "REJECTED", "reason_code": "CONTRACT_INVALID"}
    if snapshot.feed_mode == "LEGACY":
        return 422, {"status": "REJECTED", "reason_code": "LEGACY_SNAPSHOT_FORBIDDEN"}
    lane = snapshot.feed_mode
    latest = connection.execute(
        "SELECT * FROM estimator_snapshot_receipts WHERE feed_mode=? "
        "ORDER BY snapshot_version DESC LIMIT 1",
        (lane,),
    ).fetchone()
    if latest is not None and snapshot.snapshot_version < int(latest["snapshot_version"]):
        return 409, {"status": "REJECTED", "reason_code": "SNAPSHOT_VERSION_REGRESSION"}
    if latest is not None and snapshot.snapshot_version == int(latest["snapshot_version"]):
        if str(latest["snapshot_id"]) != snapshot.snapshot_id:
            return 409, {"status": "REJECTED", "reason_code": "SNAPSHOT_VERSION_CONFLICT"}
        if latest["published_at_utc"] is not None:
            return 200, {
                "status": "ACK",
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_hash": snapshot.snapshot_id,
                "snapshot_version": snapshot.snapshot_version,
                "duplicate": True,
            }
        received_at = str(latest["received_at_utc"])
    else:
        received_at = _stamp()
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO estimator_snapshot_receipts VALUES(?,?,?,?,?,?,NULL)",
                (
                    lane,
                    snapshot.snapshot_version,
                    snapshot.snapshot_id,
                    snapshot.input_snapshot_hash,
                    snapshot.model_dump_json(),
                    received_at,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    published_at = _stamp()
    view = _web_view(
        snapshot,
        received_at_utc=received_at,
        published_at_utc=published_at,
    )
    path = _lane_path(snapshot_root, lane)
    atomic_json_write(path, view)
    invalidation = {
        "contract": "estimator_snapshot_cache_generation/1.0",
        "feed_mode": lane,
        "snapshot_hash": snapshot.snapshot_id,
        "snapshot_version": snapshot.snapshot_version,
        "invalidated_at_utc": published_at,
    }
    atomic_json_write(snapshot_root / f"cache-{lane.lower()}.json", invalidation)
    event_id = hashlib.sha256(
        f"estimator:snapshot-published\0{lane}\0{snapshot.snapshot_id}".encode("ascii")
    ).hexdigest()
    event = {
        "contract": "estimator_snapshot_realtime_event/1.0",
        "event_id": event_id,
        "event_type": "estimator:snapshot-published",
        "feed_mode": lane,
        "snapshot_hash": snapshot.snapshot_id,
        "snapshot_version": snapshot.snapshot_version,
        "published_at_utc": published_at,
    }
    _fsync_append(publication_events_path, event)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "UPDATE estimator_snapshot_receipts SET published_at_utc=? "
            "WHERE feed_mode=? AND snapshot_version=?",
            (published_at, lane, snapshot.snapshot_version),
        )
        connection.execute(
            "INSERT OR IGNORE INTO estimator_snapshot_publication_outbox "
            "VALUES(?,?,?,?,?,NULL)",
            (event_id, lane, snapshot.snapshot_version, snapshot.snapshot_id, published_at),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return 200, {
        "status": "ACK",
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_id,
        "snapshot_version": snapshot.snapshot_version,
        "duplicate": False,
    }


def read_web_snapshot_view(
    path: Path | str,
    *,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        snapshot = EstimatorSnapshotV1.model_validate(value["snapshot"])
        if value.get("snapshot_hash") != snapshot.snapshot_id:
            raise EstimatorSnapshotReceiverError("web_snapshot_hash_mismatch")
        generated = snapshot.generated_at_utc.astimezone(timezone.utc)
        age = max(0.0, (_utc(now_utc) - generated).total_seconds())
        output = dict(value)
        output["age_seconds"] = round(age, 3)
        output["transport_state"] = (
            "FRESH"
            if age <= float(value.get("stale_after_seconds") or DEFAULT_STALE_AFTER_SECONDS)
            else "STALE"
        )
        return output
    except (OSError, KeyError, TypeError, ValueError, ValidationError) as exc:
        raise EstimatorSnapshotReceiverError("web_snapshot_unavailable") from exc


def snapshot_receiver_metrics(connection: sqlite3.Connection) -> dict[str, object]:
    lanes: dict[str, object] = {}
    for row in connection.execute(
        "SELECT feed_mode,MAX(snapshot_version) AS version,MAX(published_at_utc) AS published "
        "FROM estimator_snapshot_receipts GROUP BY feed_mode"
    ).fetchall():
        lanes[str(row["feed_mode"])] = {
            "snapshot_version": int(row["version"]),
            "published_at_utc": row["published"],
        }
    return {
        "lanes": lanes,
        "pending_publication_events": int(
            connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc IS NULL"
            ).fetchone()[0]
        ),
        "rejection_count": int(
            connection.execute("SELECT COUNT(*) FROM estimator_snapshot_rejections").fetchone()[0]
        ),
    }


__all__ = [
    "SNAPSHOT_RECEIVER_SCHEMA",
    "EstimatorSnapshotReceiverError",
    "apply_estimator_snapshot",
    "connect_snapshot_receiver",
    "read_web_snapshot_view",
    "record_snapshot_rejection",
    "snapshot_receiver_metrics",
]
