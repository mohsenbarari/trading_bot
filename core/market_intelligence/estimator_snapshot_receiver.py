"""Durable web-side projection for versioned estimator snapshots."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Callable, Mapping, Sequence

from pydantic import ValidationError

from .coin_prediction_anchors import PREDICTION_AUTHORITY_BASELINE_EPOCH
from .coin_rate_engine import COIN_RATE_ENGINE_VERSION
from .private_pipeline_contracts import (
    EstimatorSnapshotV1,
    EstimatorSnapshotV2,
    content_hash,
    estimator_snapshot_id,
)


SNAPSHOT_RECEIVER_SCHEMA = "estimator_snapshot_receiver/1.0"
DEFAULT_STALE_AFTER_SECONDS = 30
MAXIMUM_RECEIVE_AGE_SECONDS = 60
PREDICTION_LEDGER_RETENTION = timedelta(hours=24)
SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES = 8 * 1024 * 1024
SNAPSHOT_PAYLOAD_RETENTION = timedelta(hours=24)
SNAPSHOT_OPERATIONAL_RETENTION = timedelta(days=7)
SNAPSHOT_COMPACTION_BATCH_SIZE = 250
SNAPSHOT_PUBLICATION_RECONCILIATION_SCHEMA = (
    "estimator_snapshot_publication_reconciliation/1.0"
)
_PREDICTION_COMMODITY = {
    "COIN_IMAM": "امام",
    "COIN_BAHAR": "بهار",
    "COIN_HALF_BAHAR": "نیم بهار",
    "COIN_QUARTER_BAHAR": "ربع بهار",
    "COIN_HALF_LOW_DATE": "نیم تاریخ پایین",
    "COIN_QUARTER_LOW_DATE": "ربع تاریخ پایین",
    "COIN_ONE_GRAM": "یک گرمی",
}


class EstimatorSnapshotReceiverError(RuntimeError):
    """Content-free receiver failure."""


def estimator_snapshot_publication_event_id(
    feed_mode: str,
    snapshot_id: str,
) -> str:
    if feed_mode not in {"PRIVATE_SHADOW", "PRIVATE_PRIMARY"}:
        raise EstimatorSnapshotReceiverError("snapshot_publication_lane_invalid")
    try:
        identity = (
            f"estimator:snapshot-published\0{feed_mode}\0{snapshot_id}"
        ).encode("ascii")
    except UnicodeEncodeError as exc:
        raise EstimatorSnapshotReceiverError(
            "snapshot_publication_identity_invalid"
        ) from exc
    return hashlib.sha256(identity).hexdigest()


def _legacy_estimator_snapshot_v1_id(payload: Mapping[str, object]) -> str:
    """Reproduce the immutable identity emitted by the original V1 runtime.

    V1 snapshots created before the shared ``estimator_snapshot_id`` helper
    existed used the identity contract as the document root and deliberately
    omitted the transport contract, snapshot id and reason codes.  Historical
    retained receipts remain valid evidence and must be verified with that
    exact algorithm rather than weakened or rewritten during reconciliation.
    """

    return content_hash(
        {
            "contract": "estimator_snapshot_identity/1.0",
            **{
                key: value
                for key, value in payload.items()
                if key not in {"contract", "snapshot_id", "reason_codes"}
            },
        }
    )


def _utc(value: datetime | None = None) -> datetime:
    supplied = value or datetime.now(timezone.utc)
    if supplied.tzinfo is None or supplied.utcoffset() is None:
        raise EstimatorSnapshotReceiverError("snapshot_receiver_time_timezone_required")
    return supplied.astimezone(timezone.utc)


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


def _prediction_price(value: object) -> int:
    try:
        project_price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EstimatorSnapshotReceiverError("prediction_ledger_rate_invalid") from exc
    if (
        not project_price.is_finite()
        or project_price <= 0
        or project_price != project_price.to_integral_value()
    ):
        raise EstimatorSnapshotReceiverError("prediction_ledger_rate_invalid")
    return int(project_price) * 1_000


def _prediction_rows(
    snapshot: EstimatorSnapshotV2,
    *,
    created_at_utc: str,
) -> list[tuple[object, ...]]:
    prediction_time = snapshot.generated_at_utc.isoformat().replace("+00:00", "Z")
    authority_epoch = f"PRIVATE_PRIMARY:{snapshot.model_version}"
    rows: list[tuple[object, ...]] = []
    for index, rate in enumerate(snapshot.rates):
        if rate.status != "ESTIMATED":
            continue
        commodity = _PREDICTION_COMMODITY.get(rate.instrument)
        if commodity is None or rate.settlement not in {"CASH", "TOMORROW"}:
            continue
        rows.append(
            (
                # Preserve the V1 ledger namespace.  Primary occupied odd ids
                # and Shadow even ids.  Exact-row verification below prevents
                # an arbitrary retained id from silently swallowing this row.
                (snapshot.snapshot_version * 1000 + index) * 2 + 1,
                prediction_time,
                created_at_utc,
                "MAIN_ONLINE",
                commodity,
                rate.settlement,
                _prediction_price(rate.value),
                authority_epoch,
            )
        )
    return rows


def update_prediction_ledger(
    path: Path | str,
    snapshot: EstimatorSnapshotV2,
    *,
    created_at_utc: str,
) -> int:
    """Stage Primary estimates without changing parser/model authority."""

    if snapshot.feed_mode != "PRIVATE_PRIMARY":
        raise EstimatorSnapshotReceiverError(
            "prediction_ledger_non_primary_snapshot_forbidden"
        )
    destination = Path(path)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ledger = sqlite3.connect(destination, timeout=30)
    try:
        ledger.execute("PRAGMA journal_mode=WAL")
        ledger.execute("PRAGMA synchronous=FULL")
        ledger.executescript(
            """
            CREATE TABLE IF NOT EXISTS coin_estimate_predictions(
              id INTEGER PRIMARY KEY,
              prediction_time_utc TEXT NOT NULL,
              created_at_utc TEXT NOT NULL,
              model_id TEXT NOT NULL,
              commodity TEXT NOT NULL,
              settlement TEXT NOT NULL,
              estimated_price_toman INTEGER NOT NULL,
              authority_epoch TEXT
            );
            CREATE INDEX IF NOT EXISTS coin_estimate_predictions_causal_idx
            ON coin_estimate_predictions(model_id,prediction_time_utc,created_at_utc);
            """
        )
        _ensure_prediction_authority_schema(ledger)
        rows = _prediction_rows(snapshot, created_at_utc=created_at_utc)
        cutoff = (
            snapshot.generated_at_utc.astimezone(timezone.utc)
            - PREDICTION_LEDGER_RETENTION
        ).isoformat().replace("+00:00", "Z")
        with ledger:
            ledger.executemany(
                "INSERT OR IGNORE INTO coin_estimate_predictions("
                "id,prediction_time_utc,created_at_utc,model_id,commodity,"
                "settlement,estimated_price_toman,authority_epoch) "
                "VALUES(?,?,?,?,?,?,?,?)",
                rows,
            )
            for row in rows:
                observed = ledger.execute(
                    "SELECT prediction_time_utc,created_at_utc,model_id,commodity,"
                    "settlement,estimated_price_toman,authority_epoch "
                    "FROM coin_estimate_predictions WHERE id=?",
                    (row[0],),
                ).fetchone()
                if observed is None or tuple(observed) != tuple(row[1:]):
                    raise EstimatorSnapshotReceiverError(
                        "prediction_ledger_id_collision"
                    )
            ledger.execute(
                "DELETE FROM coin_estimate_predictions WHERE prediction_time_utc<?",
                (cutoff,),
            )
        os.chmod(destination, 0o600)
        return len(rows)
    finally:
        ledger.close()


def _ensure_prediction_authority_schema(ledger: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in ledger.execute("PRAGMA table_info(coin_estimate_predictions)")
    }
    if "authority_epoch" not in columns:
        count = int(
            ledger.execute("SELECT COUNT(*) FROM coin_estimate_predictions").fetchone()[0]
        )
        if count:
            raise EstimatorSnapshotReceiverError(
                "prediction_ledger_authority_migration_required"
            )
        ledger.execute(
            "ALTER TABLE coin_estimate_predictions ADD COLUMN authority_epoch TEXT"
        )
    ledger.executescript(
        """
        CREATE TABLE IF NOT EXISTS coin_estimate_prediction_authority(
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          active_epoch TEXT NOT NULL,
          active_feed_mode TEXT NOT NULL CHECK(
            active_feed_mode IN ('LEGACY_BASELINE','PRIVATE_PRIMARY')
          ),
          updated_at_utc TEXT NOT NULL
        );
        INSERT OR IGNORE INTO coin_estimate_prediction_authority
          (singleton,active_epoch,active_feed_mode,updated_at_utc)
        VALUES(1,'LEGACY_BASELINE','LEGACY_BASELINE',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
        """
    )


def activate_legacy_prediction_authority(path: Path | str) -> None:
    """Select seed/legacy anchors without deleting auditable Primary rows."""

    destination = Path(path)
    if not destination.is_file():
        return
    ledger = sqlite3.connect(destination, timeout=30)
    try:
        ledger.execute("PRAGMA journal_mode=WAL")
        ledger.execute("PRAGMA synchronous=FULL")
        columns = {
            str(row[1])
            for row in ledger.execute(
                "PRAGMA table_info(coin_estimate_predictions)"
            )
        }
        # A pre-authority ledger is already interpreted as the legacy lane by
        # old and new readers.  Shadow must not mutate or block on it; Primary
        # will still fail closed until an explicit seed migration is supplied.
        if "authority_epoch" not in columns:
            return
        _ensure_prediction_authority_schema(ledger)
        with ledger:
            ledger.execute(
                "UPDATE coin_estimate_prediction_authority SET active_epoch=?,"
                "active_feed_mode='LEGACY_BASELINE',updated_at_utc=? WHERE singleton=1",
                (PREDICTION_AUTHORITY_BASELINE_EPOCH, _stamp()),
            )
    finally:
        ledger.close()


def activate_private_prediction_authority(
    path: Path | str,
    snapshot: EstimatorSnapshotV2,
    *,
    activated_at_utc: str | None = None,
) -> None:
    """Explicitly select a fully staged Primary epoch during cutover."""

    if snapshot.feed_mode != "PRIVATE_PRIMARY" or snapshot.status != "OK":
        raise EstimatorSnapshotReceiverError(
            "prediction_authority_primary_snapshot_invalid"
        )
    expected_rows = _prediction_rows(snapshot, created_at_utc="")
    if not expected_rows:
        raise EstimatorSnapshotReceiverError(
            "prediction_authority_primary_snapshot_not_rate_ready"
        )
    destination = Path(path)
    if not destination.is_file():
        raise EstimatorSnapshotReceiverError("prediction_ledger_unavailable")
    ledger = sqlite3.connect(destination, timeout=30)
    try:
        ledger.execute("PRAGMA journal_mode=WAL")
        ledger.execute("PRAGMA synchronous=FULL")
        _ensure_prediction_authority_schema(ledger)
        for row in expected_rows:
            staged = ledger.execute(
                "SELECT prediction_time_utc,model_id,commodity,settlement,"
                "estimated_price_toman,authority_epoch "
                "FROM coin_estimate_predictions WHERE id=?",
                (row[0],),
            ).fetchone()
            expected = (row[1], row[3], row[4], row[5], row[6], row[7])
            if staged is None or tuple(staged) != expected:
                raise EstimatorSnapshotReceiverError(
                    "prediction_authority_primary_epoch_incomplete"
                )
        authority_epoch = str(expected_rows[0][7])
        with ledger:
            ledger.execute(
                "UPDATE coin_estimate_prediction_authority SET active_epoch=?,"
                "active_feed_mode='PRIVATE_PRIMARY',updated_at_utc=? WHERE singleton=1",
                (authority_epoch, activated_at_utc or _stamp()),
            )
    finally:
        ledger.close()


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
    with _lane_publication_lock(path.parent, "EVENTS"):
        if path.exists() or path.is_symlink():
            existing = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(existing.st_mode)
                or existing.st_uid not in {0, os.geteuid()}
                or existing.st_nlink != 1
                or stat.S_IMODE(existing.st_mode) & 0o022
            ):
                raise EstimatorSnapshotReceiverError(
                    "snapshot_event_log_security_invalid"
                )
            if (
                existing.st_size > 0
                and existing.st_size + len(payload)
                > SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES
            ):
                rotated = path.with_name(path.name + ".1")
                if rotated.exists() and not rotated.is_file():
                    raise EstimatorSnapshotReceiverError(
                        "snapshot_event_log_rotation_target_invalid"
                    )
                os.replace(path, rotated)
                parent = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise EstimatorSnapshotReceiverError(
                        "snapshot_event_log_short_write"
                    )
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _lane_path(snapshot_root: Path, feed_mode: str) -> Path:
    return snapshot_root / (
        "latest-private-primary.json"
        if feed_mode == "PRIVATE_PRIMARY"
        else "latest-private-shadow.json"
    )


@contextmanager
def _lane_publication_lock(snapshot_root: Path, feed_mode: str):
    snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_status = os.stat(snapshot_root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(root_status.st_mode)
        or root_status.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(root_status.st_mode) & 0o022
    ):
        raise EstimatorSnapshotReceiverError("snapshot_root_security_invalid")
    path = snapshot_root / f".{feed_mode.lower()}.publication.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise EstimatorSnapshotReceiverError(
            "snapshot_publication_lock_unavailable"
        ) from exc
    try:
        lock_status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_status.st_mode)
            or lock_status.st_nlink != 1
            or lock_status.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(lock_status.st_mode) & 0o022
        ):
            raise EstimatorSnapshotReceiverError(
                "snapshot_publication_lock_security_invalid"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _published_view_matches(
    path: Path,
    snapshot: EstimatorSnapshotV2,
) -> bool:
    try:
        value = read_web_snapshot_view(path)
        return (
            value.get("contract") == "estimator_snapshot_web_view/1.0"
            and value.get("snapshot_hash") == snapshot.snapshot_id
            and int(value.get("snapshot_version") or 0)
            == snapshot.snapshot_version
            and value.get("feed_mode") == snapshot.feed_mode
        )
    except EstimatorSnapshotReceiverError:
        return False


def _web_view(
    snapshot: EstimatorSnapshotV2,
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


def _acknowledgement(
    snapshot: EstimatorSnapshotV2,
    *,
    duplicate: bool,
    web_view: Mapping[str, object],
) -> dict[str, object]:
    """Return the receiver-issued view with the transport ACK.

    The bot-side product consumer must never trust the estimator's raw local
    artifact.  Returning the exact view published by the remote receiver lets
    the sender persist an independently acknowledged, Product-readable copy.
    mTLS authenticates the receiver and the identity fields below bind the
    response to the submitted snapshot.
    """

    return {
        "status": "ACK",
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_id,
        "snapshot_version": snapshot.snapshot_version,
        "duplicate": duplicate,
        "web_view": dict(web_view),
    }


def apply_estimator_snapshot(
    connection: sqlite3.Connection,
    document: Mapping[str, object],
    *,
    snapshot_root: Path,
    publication_events_path: Path,
    prediction_ledger_path: Path | None = None,
    allow_private_primary: bool = False,
    now_utc: datetime | None = None,
) -> tuple[int, dict[str, object]]:
    from .private_pipeline_foundation import atomic_json_write

    try:
        snapshot = EstimatorSnapshotV2.model_validate(document)
    except ValidationError:
        return 422, {"status": "REJECTED", "reason_code": "CONTRACT_INVALID"}
    if snapshot.feed_mode == "LEGACY":
        return 422, {"status": "REJECTED", "reason_code": "LEGACY_SNAPSHOT_FORBIDDEN"}
    if snapshot.model_version != COIN_RATE_ENGINE_VERSION:
        return 422, {
            "status": "REJECTED",
            "reason_code": "MODEL_VERSION_UNSUPPORTED",
        }
    if snapshot.feed_mode == "PRIVATE_PRIMARY" and not allow_private_primary:
        return 403, {
            "status": "REJECTED",
            "reason_code": "PRIVATE_PRIMARY_NOT_AUTHORIZED",
        }
    if any(rate.unit != "PROJECT_THOUSAND_TOMAN" for rate in snapshot.rates):
        return 422, {
            "status": "REJECTED",
            "reason_code": "RATE_UNIT_UNSUPPORTED",
        }
    received_time = _utc(now_utc)
    if snapshot.generated_at_utc > received_time:
        return 422, {
            "status": "REJECTED",
            "reason_code": "SNAPSHOT_TIME_FUTURE",
        }
    if (received_time - snapshot.generated_at_utc).total_seconds() > float(
        MAXIMUM_RECEIVE_AGE_SECONDS
    ):
        return 422, {
            "status": "REJECTED",
            "reason_code": "SNAPSHOT_TIME_STALE",
        }
    lane = snapshot.feed_mode
    path = _lane_path(snapshot_root, lane)
    event_id = estimator_snapshot_publication_event_id(lane, snapshot.snapshot_id)
    with _lane_publication_lock(snapshot_root, lane):
        duplicate = False
        connection.execute("BEGIN IMMEDIATE")
        try:
            latest = connection.execute(
                "SELECT * FROM estimator_snapshot_receipts WHERE feed_mode=? "
                "ORDER BY snapshot_version DESC LIMIT 1",
                (lane,),
            ).fetchone()
            if latest is not None and snapshot.snapshot_version < int(
                latest["snapshot_version"]
            ):
                connection.commit()
                return 409, {
                    "status": "REJECTED",
                    "reason_code": "SNAPSHOT_VERSION_REGRESSION",
                }
            if latest is not None and snapshot.snapshot_version == int(
                latest["snapshot_version"]
            ):
                if str(latest["snapshot_id"]) != snapshot.snapshot_id:
                    connection.commit()
                    return 409, {
                        "status": "REJECTED",
                        "reason_code": "SNAPSHOT_VERSION_CONFLICT",
                    }
                received_at = str(latest["received_at_utc"])
                duplicate = True
                outbox = connection.execute(
                    "SELECT published_at_utc,delivered_at_utc "
                    "FROM estimator_snapshot_publication_outbox WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                published_at = str(
                    latest["published_at_utc"]
                    or (outbox["published_at_utc"] if outbox is not None else None)
                    or _stamp()
                )
                if (
                    latest["published_at_utc"] is not None
                    and outbox is not None
                    and outbox["delivered_at_utc"] is not None
                    and _published_view_matches(path, snapshot)
                ):
                    web_view = read_web_snapshot_view(path)
                    connection.commit()
                    return 200, _acknowledgement(
                        snapshot,
                        duplicate=True,
                        web_view=web_view,
                    )
            else:
                received_at = _stamp(received_time)
                published_at = _stamp()
                connection.execute(
                    "INSERT INTO estimator_snapshot_receipts "
                    "VALUES(?,?,?,?,?,?,NULL)",
                    (
                        lane,
                        snapshot.snapshot_version,
                        snapshot.snapshot_id,
                        snapshot.input_snapshot_hash,
                        snapshot.model_dump_json(),
                        received_at,
                    ),
                )
            # Persist the monotonic reservation before making any Product-
            # visible side effect.  A crash leaves a resumable pending row and
            # can never reopen acceptance for an older version.
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

        if (
            prediction_ledger_path is not None
            and snapshot.feed_mode == "PRIVATE_PRIMARY"
        ):
            update_prediction_ledger(
                prediction_ledger_path,
                snapshot,
                created_at_utc=received_at,
            )
        view = _web_view(
            snapshot,
            received_at_utc=received_at,
            published_at_utc=published_at,
        )
        # Persist the publication intent before making the view Product-visible,
        # but do not mark the receipt as published yet.  A crash in this window
        # is therefore visible as pending to health/GET and is repaired by the
        # same-version retry while older versions remain fenced.
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT OR IGNORE INTO estimator_snapshot_publication_outbox "
                "VALUES(?,?,?,?,?,NULL)",
                (
                    event_id,
                    lane,
                    snapshot.snapshot_version,
                    snapshot.snapshot_id,
                    published_at,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        atomic_json_write(path, view)
        invalidation = {
            "contract": "estimator_snapshot_cache_generation/1.0",
            "feed_mode": lane,
            "snapshot_hash": snapshot.snapshot_id,
            "snapshot_version": snapshot.snapshot_version,
            "invalidated_at_utc": published_at,
        }
        atomic_json_write(snapshot_root / f"cache-{lane.lower()}.json", invalidation)
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
                "UPDATE estimator_snapshot_publication_outbox "
                "SET delivered_at_utc=? WHERE event_id=?",
                (_stamp(), event_id),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return 200, _acknowledgement(
            snapshot,
            duplicate=duplicate,
            web_view=view,
        )


def read_web_snapshot_view(
    path: Path | str,
    *,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        snapshot = EstimatorSnapshotV2.model_validate(value["snapshot"])
        if value.get("contract") != "estimator_snapshot_web_view/1.0":
            raise EstimatorSnapshotReceiverError("web_snapshot_contract_invalid")
        if value.get("snapshot_hash") != snapshot.snapshot_id:
            raise EstimatorSnapshotReceiverError("web_snapshot_hash_mismatch")
        if value.get("snapshot_version") != snapshot.snapshot_version:
            raise EstimatorSnapshotReceiverError("web_snapshot_version_mismatch")
        if value.get("feed_mode") != snapshot.feed_mode:
            raise EstimatorSnapshotReceiverError("web_snapshot_feed_mode_mismatch")
        generated = snapshot.generated_at_utc.astimezone(timezone.utc)
        age = (_utc(now_utc) - generated).total_seconds()
        if age < 0:
            raise EstimatorSnapshotReceiverError("web_snapshot_time_future")
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


def read_published_web_snapshot_view(
    connection: sqlite3.Connection,
    path: Path | str,
    *,
    feed_mode: str,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Return only the latest fully published receiver-issued Product view."""

    if feed_mode not in {"PRIVATE_SHADOW", "PRIVATE_PRIMARY"}:
        raise EstimatorSnapshotReceiverError("web_snapshot_feed_mode_invalid")
    view = read_web_snapshot_view(path, now_utc=now_utc)
    latest = connection.execute(
        "SELECT receipt.snapshot_version,receipt.snapshot_id,receipt.published_at_utc,"
        "EXISTS(SELECT 1 FROM estimator_snapshot_publication_outbox AS outbox "
        "WHERE outbox.feed_mode=receipt.feed_mode "
        "AND outbox.snapshot_version=receipt.snapshot_version "
        "AND outbox.snapshot_id=receipt.snapshot_id "
        "AND outbox.delivered_at_utc IS NOT NULL) AS event_delivered "
        "FROM estimator_snapshot_receipts AS receipt WHERE receipt.feed_mode=? "
        "ORDER BY receipt.snapshot_version DESC LIMIT 1",
        (feed_mode,),
    ).fetchone()
    if (
        latest is None
        or latest["published_at_utc"] is None
        or not bool(latest["event_delivered"])
        or int(view.get("snapshot_version") or 0) != int(latest["snapshot_version"])
        or view.get("snapshot_hash") != str(latest["snapshot_id"])
        or view.get("feed_mode") != feed_mode
        or view.get("published_at_utc") != str(latest["published_at_utc"])
    ):
        raise EstimatorSnapshotReceiverError("web_snapshot_publication_pending")
    return view


def _secure_publication_event_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_event_log_unavailable"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_size > SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES
        ):
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_event_log_security_invalid"
            )
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_event_log_short_read"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if os.read(descriptor, 1):
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_event_log_changed"
            )
        return payload
    finally:
        os.close(descriptor)


def _publication_event_index(
    publication_events_path: Path,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    paths = [
        candidate
        for candidate in (
            publication_events_path.with_name(publication_events_path.name + ".1"),
            publication_events_path,
        )
        if candidate.exists() or candidate.is_symlink()
    ]
    events: dict[str, dict[str, object]] = {}
    files: list[dict[str, object]] = []
    required_keys = {
        "contract",
        "event_id",
        "event_type",
        "feed_mode",
        "snapshot_hash",
        "snapshot_version",
        "published_at_utc",
    }
    for path in paths:
        payload = _secure_publication_event_file(path)
        files.append(
            {
                "name": path.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_event_log_encoding_invalid"
            ) from exc
        for line in text.splitlines():
            if not line.strip():
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_event_log_malformed"
                )
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_event_log_malformed"
                ) from exc
            if not isinstance(document, dict) or set(document) != required_keys:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_event_contract_invalid"
                )
            event_id = document.get("event_id")
            snapshot_hash = document.get("snapshot_hash")
            lane = document.get("feed_mode")
            published_at = document.get("published_at_utc")
            if (
                document.get("contract")
                != "estimator_snapshot_realtime_event/1.0"
                or document.get("event_type") != "estimator:snapshot-published"
                or lane not in {"PRIVATE_SHADOW", "PRIVATE_PRIMARY"}
                or not isinstance(event_id, str)
                or len(event_id) != 64
                or any(character not in "0123456789abcdef" for character in event_id)
                or not isinstance(snapshot_hash, str)
                or len(snapshot_hash) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in snapshot_hash
                )
                or not isinstance(document.get("snapshot_version"), int)
                or isinstance(document.get("snapshot_version"), bool)
                or int(document.get("snapshot_version") or 0) <= 0
                or not isinstance(published_at, str)
                or not published_at
            ):
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_event_contract_invalid"
                )
            try:
                event_time = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_event_contract_invalid"
                ) from exc
            if (
                event_time.tzinfo is None
                or event_time.utcoffset() is None
                or event_id
                != estimator_snapshot_publication_event_id(lane, snapshot_hash)
            ):
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_event_contract_invalid"
                )
            if event_id in events:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_event_duplicate"
                )
            events[event_id] = document
    if sum(int(value["size"]) for value in files) > (
        SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES * 2
    ):
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_event_log_total_size_invalid"
        )
    return events, files


def _snapshot_publication_reconciliation_plan(
    connection: sqlite3.Connection,
    *,
    snapshot_root: Path,
    publication_events_path: Path,
    feed_modes: tuple[str, ...],
    release_sha: str,
    release_tree: str,
) -> tuple[dict[str, object], list[tuple[object, ...]]]:
    events, event_files = _publication_event_index(publication_events_path)
    placeholders = ",".join("?" for _ in feed_modes)
    pending = connection.execute(
        "SELECT outbox.event_id,outbox.feed_mode,outbox.snapshot_version,"
        "outbox.snapshot_id,outbox.published_at_utc,"
        "receipt.snapshot_id AS receipt_snapshot_id,"
        "receipt.input_snapshot_hash AS receipt_input_snapshot_hash,"
        "receipt.payload_json AS receipt_payload_json,"
        "receipt.received_at_utc AS receipt_received_at_utc,"
        "receipt.published_at_utc AS receipt_published_at_utc "
        "FROM estimator_snapshot_publication_outbox AS outbox "
        "LEFT JOIN estimator_snapshot_receipts AS receipt "
        "ON receipt.feed_mode=outbox.feed_mode "
        "AND receipt.snapshot_version=outbox.snapshot_version "
        f"WHERE outbox.delivered_at_utc IS NULL AND outbox.feed_mode IN ({placeholders}) "
        "ORDER BY outbox.feed_mode,outbox.snapshot_version,outbox.event_id",
        feed_modes,
    ).fetchall()
    latest_by_lane = {
        str(row["feed_mode"]): row
        for row in connection.execute(
            "SELECT receipt.feed_mode,receipt.snapshot_version,receipt.snapshot_id,"
            "receipt.input_snapshot_hash,receipt.received_at_utc,"
            "receipt.published_at_utc "
            "FROM estimator_snapshot_receipts AS receipt "
            "WHERE receipt.feed_mode IN ("
            f"{placeholders}) AND receipt.snapshot_version=("
            "SELECT MAX(newer.snapshot_version) FROM estimator_snapshot_receipts AS newer "
            "WHERE newer.feed_mode=receipt.feed_mode)",
            feed_modes,
        ).fetchall()
    }
    latest_pending_by_lane: dict[str, sqlite3.Row] = {}
    repair_rows: list[tuple[object, ...]] = []
    identities: list[dict[str, object]] = []
    identities_by_lane: dict[str, list[dict[str, object]]] = {
        lane: [] for lane in feed_modes
    }
    retained_payload_count = 0
    redacted_payload_count = 0
    for row in pending:
        lane = str(row["feed_mode"])
        latest_pending_by_lane[lane] = row
        version = int(row["snapshot_version"])
        snapshot_id = str(row["snapshot_id"])
        published_at = str(row["published_at_utc"] or "")
        event_id = str(row["event_id"])
        expected_event_id = estimator_snapshot_publication_event_id(lane, snapshot_id)
        if event_id != expected_event_id or not published_at:
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_outbox_identity_invalid"
            )
        if (
            row["receipt_snapshot_id"] is None
            or str(row["receipt_snapshot_id"]) != snapshot_id
            or str(row["receipt_published_at_utc"] or "") != published_at
        ):
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_receipt_identity_invalid"
            )
        expected_event: dict[str, object] = {
            "contract": "estimator_snapshot_realtime_event/1.0",
            "event_id": event_id,
            "event_type": "estimator:snapshot-published",
            "feed_mode": lane,
            "snapshot_hash": snapshot_id,
            "snapshot_version": version,
            "published_at_utc": published_at,
        }
        if events.get(event_id) != expected_event:
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_event_evidence_invalid"
            )
        payload_text = str(row["receipt_payload_json"])
        try:
            payload_value = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_receipt_payload_invalid"
            ) from exc
        payload_state: str
        if payload_value == {}:
            payload_state = "REDACTED"
            redacted_payload_count += 1
        else:
            if not isinstance(payload_value, dict):
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_receipt_payload_invalid"
                )
            declared_contract = payload_value.get("contract")
            model = (
                EstimatorSnapshotV1
                if declared_contract == "estimator_snapshot/1.0"
                else EstimatorSnapshotV2
                if declared_contract == "estimator_snapshot/2.0"
                else None
            )
            if model is None:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_receipt_payload_contract_invalid"
                )
            try:
                retained_snapshot = model.model_validate(payload_value)
            except ValidationError as exc:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_receipt_payload_invalid"
                ) from exc
            valid_snapshot_ids = {estimator_snapshot_id(retained_snapshot)}
            if declared_contract == "estimator_snapshot/1.0":
                valid_snapshot_ids.add(
                    _legacy_estimator_snapshot_v1_id(payload_value)
                )
            if (
                retained_snapshot.feed_mode != lane
                or retained_snapshot.snapshot_version != version
                or retained_snapshot.snapshot_id != snapshot_id
                or retained_snapshot.snapshot_id not in valid_snapshot_ids
                or retained_snapshot.input_snapshot_hash
                != str(row["receipt_input_snapshot_hash"])
            ):
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_receipt_payload_identity_invalid"
                )
            payload_state = "RETAINED"
            retained_payload_count += 1
        repair_rows.append((event_id, lane, version, snapshot_id, published_at))
        identity = {
            "event_id": event_id,
            "feed_mode": lane,
            "snapshot_version": version,
            "snapshot_id": snapshot_id,
            "published_at_utc": published_at,
            "receipt_input_snapshot_hash": str(row["receipt_input_snapshot_hash"]),
            "payload_state": payload_state,
        }
        identities.append(identity)
        identities_by_lane[lane].append(identity)
    for lane, latest_pending in latest_pending_by_lane.items():
        latest_receipt = latest_by_lane.get(lane)
        if (
            latest_receipt is None
            or int(latest_receipt["snapshot_version"])
            != int(latest_pending["snapshot_version"])
            or str(latest_receipt["snapshot_id"])
            != str(latest_pending["snapshot_id"])
        ):
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_latest_pending_receipt_mismatch"
            )
        path = _lane_path(snapshot_root, lane)
        try:
            view = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(view, dict)
                or view.get("contract") != "estimator_snapshot_web_view/1.0"
                or not isinstance(view.get("snapshot"), dict)
            ):
                raise ValueError("view_contract")
            declared_contract = view["snapshot"].get("contract")
            model = (
                EstimatorSnapshotV2
                if declared_contract == "estimator_snapshot/2.0"
                else EstimatorSnapshotV1
                if declared_contract == "estimator_snapshot/1.0"
                and lane == "PRIVATE_SHADOW"
                else None
            )
            if model is None:
                raise ValueError("view_snapshot_contract")
            view_snapshot = model.model_validate(view["snapshot"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_latest_view_invalid"
            ) from exc
        if (
            view.get("snapshot_hash") != str(latest_receipt["snapshot_id"])
            or int(view.get("snapshot_version") or 0)
            != int(latest_receipt["snapshot_version"])
            or view.get("feed_mode") != lane
            or view.get("published_at_utc")
            != str(latest_receipt["published_at_utc"] or "")
            or view.get("received_at_utc")
            != str(latest_receipt["received_at_utc"])
            or view_snapshot.snapshot_id != str(latest_receipt["snapshot_id"])
            or view_snapshot.snapshot_version
            != int(latest_receipt["snapshot_version"])
            or view_snapshot.feed_mode != lane
            or view_snapshot.input_snapshot_hash
            != str(latest_receipt["input_snapshot_hash"])
        ):
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_latest_view_identity_invalid"
            )
    lane_aggregates: dict[str, dict[str, object]] = {}
    for lane in feed_modes:
        lane_identities = identities_by_lane[lane]
        versions = [int(value["snapshot_version"]) for value in lane_identities]
        lane_aggregates[lane] = {
            "pending_count": len(lane_identities),
            "minimum_snapshot_version": min(versions) if versions else None,
            "maximum_snapshot_version": max(versions) if versions else None,
            "identity_sha256": hashlib.sha256(
                json.dumps(
                    lane_identities, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
        }
    publication_event_aggregate_sha256 = hashlib.sha256(
        json.dumps(
            {
                "files": event_files,
                "event_count": len(events),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    material = {
        "schema": SNAPSHOT_PUBLICATION_RECONCILIATION_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "feed_modes": list(feed_modes),
        "event_files": event_files,
        "event_count": len(events),
        "pending_identities": identities,
        "lane_aggregates": lane_aggregates,
        "publication_event_aggregate_sha256": publication_event_aggregate_sha256,
    }
    plan_sha256 = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report: dict[str, object] = {
        "schema": SNAPSHOT_PUBLICATION_RECONCILIATION_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "feed_modes": list(feed_modes),
        "plan_sha256": plan_sha256,
        "pending_before": len(repair_rows),
        "retained_payload_count": retained_payload_count,
        "redacted_payload_count": redacted_payload_count,
        "event_file_count": len(event_files),
        "event_count": len(events),
        "lane_aggregates": lane_aggregates,
        "publication_event_aggregate_sha256": publication_event_aggregate_sha256,
        "secrets_disclosed": False,
        "market_values_disclosed": False,
        "publication_quiesced_under_locks": True,
        "mutation_contract": "DELIVERED_AT_EQUALS_PUBLISHED_AT_ONLY",
        "events_appended": 0,
        "views_rewritten": 0,
        "rows_deleted": 0,
        "rows_requeued": 0,
    }
    return report, repair_rows


def _snapshot_receiver_preimage_backup(
    connection: sqlite3.Connection,
    destination: Path,
) -> tuple[sqlite3.Connection, dict[str, object]]:
    if not destination.is_absolute() or destination.is_symlink() or destination.exists():
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_backup_target_invalid"
        )
    parent = destination.parent
    if not parent.exists():
        parent.mkdir(mode=0o700, parents=True)
    try:
        parent_status = parent.lstat()
    except OSError as exc:
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_backup_parent_invalid"
        ) from exc
    if (
        parent.is_symlink()
        or parent.resolve(strict=True) != parent
        or not stat.S_ISDIR(parent_status.st_mode)
        or parent_status.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(parent_status.st_mode) & 0o022
    ):
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_backup_parent_invalid"
        )
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, 0o600)
    except OSError as exc:
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_backup_target_invalid"
        ) from exc
    backup: sqlite3.Connection | None = None
    try:
        backup = sqlite3.connect(destination, timeout=30, isolation_level=None)
        backup.row_factory = sqlite3.Row
        connection.backup(backup)
        integrity = backup.execute("PRAGMA integrity_check").fetchall()
        if len(integrity) != 1 or str(integrity[0][0]).lower() != "ok":
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_backup_integrity_failed"
            )
        backup.commit()
        os.chmod(destination, 0o600, follow_symlinks=False)
        opened = os.fstat(descriptor)
        current = destination.lstat()
        if (
            (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            or opened.st_nlink != 1
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_backup_target_changed"
            )
        os.fsync(descriptor)
        parent_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
        report = {
            "preimage_backup_sha256": hashlib.sha256(payload).hexdigest(),
            "preimage_backup_size_bytes": len(payload),
            "preimage_backup_integrity": "ok",
            "preimage_backup_mode": "0600",
            "preimage_backup_path_disclosed": False,
            "preimage_backup_device": int(opened.st_dev),
            "preimage_backup_inode": int(opened.st_ino),
        }
        return backup, report
    except BaseException:
        if backup is not None:
            backup.close()
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)


def _open_snapshot_receiver_preimage_backup(
    source: Path,
) -> tuple[sqlite3.Connection, dict[str, object]]:
    """Open an immutable reconciliation preimage without following links."""

    if not source.is_absolute() or source.is_symlink():
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_backup_source_invalid"
        )
    try:
        resolved = source.resolve(strict=True)
        before = source.lstat()
    except OSError as exc:
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_backup_source_invalid"
        ) from exc
    if (
        resolved != source
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid not in {0, os.geteuid()}
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
    ):
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_backup_source_invalid"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_backup_source_invalid"
        ) from exc
    backup: sqlite3.Connection | None = None
    try:
        opened = os.fstat(descriptor)
        current = source.lstat()
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_backup_source_changed"
            )
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
        # Percent-encode URI metacharacters in an otherwise exact pathname.
        from urllib.parse import quote

        uri = "file:" + quote(str(source), safe="/") + "?mode=ro&immutable=1"
        backup = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        backup.row_factory = sqlite3.Row
        integrity = backup.execute("PRAGMA integrity_check").fetchall()
        after = source.lstat()
        if (
            len(integrity) != 1
            or str(integrity[0][0]).lower() != "ok"
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_backup_integrity_failed"
            )
        return backup, {
            "preimage_backup_sha256": hashlib.sha256(payload).hexdigest(),
            "preimage_backup_size_bytes": len(payload),
            "preimage_backup_integrity": "ok",
            "preimage_backup_mode": "0600",
            "preimage_backup_path_disclosed": False,
            "preimage_backup_device": int(opened.st_dev),
            "preimage_backup_inode": int(opened.st_ino),
        }
    except BaseException:
        if backup is not None:
            backup.close()
        raise
    finally:
        os.close(descriptor)


def inspect_snapshot_publication_reconciliation_recovery(
    connection: sqlite3.Connection,
    *,
    snapshot_root: Path,
    publication_events_path: Path,
    feed_modes: Sequence[str],
    release_sha: str,
    release_tree: str,
    expected_plan_sha256: str,
    preimage_backup_path: Path,
) -> dict[str, object]:
    """Classify an interrupted apply without mutating or reapplying rows."""

    lanes = tuple(sorted(set(str(value) for value in feed_modes)))
    if not lanes or any(
        lane not in {"PRIVATE_SHADOW", "PRIVATE_PRIMARY"} for lane in lanes
    ):
        raise EstimatorSnapshotReceiverError("snapshot_reconciliation_lane_invalid")
    for value, reason, length in (
        (release_sha, "snapshot_reconciliation_release_sha_invalid", 40),
        (release_tree, "snapshot_reconciliation_release_tree_invalid", 40),
        (
            expected_plan_sha256,
            "snapshot_reconciliation_expected_plan_invalid",
            64,
        ),
    ):
        if len(value) != length or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise EstimatorSnapshotReceiverError(reason)
    snapshot_root = Path(snapshot_root)
    publication_events_path = Path(publication_events_path)
    with ExitStack() as locks:
        for lane in ("PRIVATE_PRIMARY", "PRIVATE_SHADOW"):
            locks.enter_context(_lane_publication_lock(snapshot_root, lane))
        locks.enter_context(
            _lane_publication_lock(publication_events_path.parent, "EVENTS")
        )
        backup, backup_report = _open_snapshot_receiver_preimage_backup(
            Path(preimage_backup_path)
        )
        try:
            report, repair_rows = _snapshot_publication_reconciliation_plan(
                backup,
                snapshot_root=snapshot_root,
                publication_events_path=publication_events_path,
                feed_modes=lanes,
                release_sha=release_sha,
                release_tree=release_tree,
            )
        finally:
            backup.close()
        backup_report["preimage_plan_sha256"] = report["plan_sha256"]
        if report["plan_sha256"] != expected_plan_sha256:
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_recovery_plan_mismatch"
            )
        planned = {
            (str(event_id), str(lane), int(version), str(snapshot_id), str(published))
            for event_id, lane, version, snapshot_id, published in repair_rows
        }
        states: set[str] = set()
        for identity in planned:
            row = connection.execute(
                "SELECT published_at_utc,delivered_at_utc FROM "
                "estimator_snapshot_publication_outbox WHERE event_id=? "
                "AND feed_mode=? AND snapshot_version=? AND snapshot_id=? "
                "AND published_at_utc=?",
                identity,
            ).fetchone()
            if row is None or str(row["published_at_utc"]) != identity[4]:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_recovery_identity_mismatch"
                )
            delivered = row["delivered_at_utc"]
            if delivered is None:
                states.add("PENDING")
            elif str(delivered) == identity[4]:
                states.add("DB_COMMITTED")
            else:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_recovery_delivery_mismatch"
                )
        placeholders = ",".join("?" for _ in lanes)
        live_pending = {
            (
                str(row["event_id"]),
                str(row["feed_mode"]),
                int(row["snapshot_version"]),
                str(row["snapshot_id"]),
                str(row["published_at_utc"]),
            )
            for row in connection.execute(
                "SELECT event_id,feed_mode,snapshot_version,snapshot_id,"
                "published_at_utc FROM estimator_snapshot_publication_outbox "
                f"WHERE delivered_at_utc IS NULL AND feed_mode IN ({placeholders})",
                lanes,
            ).fetchall()
        }
        if not planned:
            recovery_state = "DB_COMMITTED"
        elif states == {"PENDING"} and live_pending == planned:
            recovery_state = "PENDING"
        elif states == {"DB_COMMITTED"} and not live_pending:
            recovery_state = "DB_COMMITTED"
        else:
            raise EstimatorSnapshotReceiverError(
                "snapshot_reconciliation_recovery_state_ambiguous"
            )
        return {
            **report,
            **backup_report,
            "status": recovery_state,
            "repaired_count": len(planned) if recovery_state == "DB_COMMITTED" else 0,
            "pending_after": 0 if recovery_state == "DB_COMMITTED" else len(planned),
        }


def reconcile_snapshot_publication_outbox(
    connection: sqlite3.Connection,
    *,
    snapshot_root: Path,
    publication_events_path: Path,
    feed_modes: Sequence[str],
    release_sha: str,
    release_tree: str,
    apply: bool = False,
    expected_plan_sha256: str | None = None,
    preimage_backup_path: Path | None = None,
    reuse_preimage_backup: bool = False,
    before_mutation: Callable[[dict[str, object]], None] | None = None,
    after_commit: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Prove and optionally close legacy publication-delivery markers.

    This is deliberately narrower than replay: it never writes a view or event,
    never changes receipt publication time, and never deletes/requeues an outbox
    row.  Apply may only copy the already-proven publication timestamp into the
    matching pending row's delivery marker.
    """

    lanes = tuple(sorted(set(str(value) for value in feed_modes)))
    if not lanes or any(
        lane not in {"PRIVATE_SHADOW", "PRIVATE_PRIMARY"} for lane in lanes
    ):
        raise EstimatorSnapshotReceiverError("snapshot_reconciliation_lane_invalid")
    for value, reason in (
        (release_sha, "snapshot_reconciliation_release_sha_invalid"),
        (release_tree, "snapshot_reconciliation_release_tree_invalid"),
    ):
        if len(value) != 40 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise EstimatorSnapshotReceiverError(reason)
    if apply and (
        expected_plan_sha256 is None
        or len(expected_plan_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_plan_sha256
        )
    ):
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_expected_plan_invalid"
        )
    if apply and preimage_backup_path is None:
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_preimage_backup_required"
        )
    if not apply and preimage_backup_path is not None:
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_preimage_backup_forbidden"
        )
    if not apply and (reuse_preimage_backup or before_mutation or after_commit):
        raise EstimatorSnapshotReceiverError(
            "snapshot_reconciliation_apply_hook_forbidden"
        )
    snapshot_root = Path(snapshot_root)
    publication_events_path = Path(publication_events_path)
    with ExitStack() as locks:
        # The JSONL is shared by both lanes.  Even a lane-scoped repair pauses
        # both compliant publishers so no unselected writer can hold SQLite
        # while waiting on the shared EVENTS lock.
        for lane in ("PRIVATE_PRIMARY", "PRIVATE_SHADOW"):
            locks.enter_context(_lane_publication_lock(snapshot_root, lane))
        locks.enter_context(
            _lane_publication_lock(publication_events_path.parent, "EVENTS")
        )
        backup: sqlite3.Connection | None = None
        backup_report: dict[str, object] = {}
        try:
            if preimage_backup_path is not None:
                if reuse_preimage_backup:
                    backup, backup_report = _open_snapshot_receiver_preimage_backup(
                        Path(preimage_backup_path)
                    )
                else:
                    backup, backup_report = _snapshot_receiver_preimage_backup(
                        connection, Path(preimage_backup_path)
                    )
                backup_plan, _backup_rows = _snapshot_publication_reconciliation_plan(
                    backup,
                    snapshot_root=snapshot_root,
                    publication_events_path=publication_events_path,
                    feed_modes=lanes,
                    release_sha=release_sha,
                    release_tree=release_tree,
                )
                backup_report["preimage_plan_sha256"] = backup_plan["plan_sha256"]
            connection.execute("BEGIN IMMEDIATE")
            report, repair_rows = _snapshot_publication_reconciliation_plan(
                connection,
                snapshot_root=snapshot_root,
                publication_events_path=publication_events_path,
                feed_modes=lanes,
                release_sha=release_sha,
                release_tree=release_tree,
            )
            if not apply:
                connection.rollback()
                return {
                    **report,
                    "status": "PLAN",
                    "repaired_count": 0,
                    "pending_after": int(report["pending_before"]),
                }
            if backup is None or backup_report.get("preimage_plan_sha256") != report.get(
                "plan_sha256"
            ):
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_preimage_plan_mismatch"
                )
            if report["plan_sha256"] != expected_plan_sha256:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_plan_cas_mismatch"
                )
            if before_mutation is not None:
                before_mutation({**report, **backup_report})
            repaired = 0
            for event_id, lane, version, snapshot_id, published_at in repair_rows:
                cursor = connection.execute(
                    "UPDATE estimator_snapshot_publication_outbox "
                    "SET delivered_at_utc=published_at_utc "
                    "WHERE event_id=? AND feed_mode=? AND snapshot_version=? "
                    "AND snapshot_id=? AND published_at_utc=? "
                    "AND delivered_at_utc IS NULL",
                    (event_id, lane, version, snapshot_id, published_at),
                )
                if cursor.rowcount != 1:
                    raise EstimatorSnapshotReceiverError(
                        "snapshot_reconciliation_exact_update_failed"
                    )
                repaired += 1
            placeholders = ",".join("?" for _ in lanes)
            pending_after = int(
                connection.execute(
                    "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                    f"WHERE delivered_at_utc IS NULL AND feed_mode IN ({placeholders})",
                    lanes,
                ).fetchone()[0]
            )
            if pending_after:
                raise EstimatorSnapshotReceiverError(
                    "snapshot_reconciliation_postcheck_pending"
                )
            connection.commit()
            result = {
                **report,
                **backup_report,
                "status": "APPLIED" if repaired else "ALREADY_RECONCILED",
                "repaired_count": repaired,
                "pending_after": pending_after,
            }
            if after_commit is not None:
                after_commit(result)
            return result
        except BaseException:
            connection.rollback()
            raise
        finally:
            if backup is not None:
                backup.close()


def compact_snapshot_receiver(
    connection: sqlite3.Connection,
    *,
    now_utc: datetime | None = None,
) -> dict[str, int]:
    """Bound operational growth without weakening the latest-version fence."""

    now = _utc(now_utc)
    payload_cutoff = _stamp(now - SNAPSHOT_PAYLOAD_RETENTION)
    operational_cutoff = _stamp(now - SNAPSHOT_OPERATIONAL_RETENTION)
    def mutate_in_batches(
        select_sql: str,
        parameters: tuple[object, ...],
        mutation_sql: str,
    ) -> int:
        rowids = [int(row[0]) for row in connection.execute(select_sql, parameters)]
        changed = 0
        for offset in range(0, len(rowids), SNAPSHOT_COMPACTION_BATCH_SIZE):
            batch = rowids[offset : offset + SNAPSHOT_COMPACTION_BATCH_SIZE]
            connection.execute("BEGIN IMMEDIATE")
            before = connection.total_changes
            try:
                connection.executemany(mutation_sql, ((rowid,) for rowid in batch))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            changed += connection.total_changes - before
        return changed

    payloads = mutate_in_batches(
        "SELECT rowid FROM estimator_snapshot_receipts "
        "WHERE published_at_utc IS NOT NULL AND payload_json<>'{}' "
        "AND julianday(published_at_utc)<julianday(?) ORDER BY rowid",
        (payload_cutoff,),
        "UPDATE estimator_snapshot_receipts SET payload_json='{}' WHERE rowid=?",
    )
    receipts = mutate_in_batches(
        "SELECT receipt.rowid FROM estimator_snapshot_receipts AS receipt "
        "WHERE receipt.published_at_utc IS NOT NULL "
        "AND julianday(receipt.published_at_utc)<julianday(?) "
        "AND receipt.snapshot_version<(SELECT MAX(newer.snapshot_version) "
        "FROM estimator_snapshot_receipts AS newer "
        "WHERE newer.feed_mode=receipt.feed_mode) ORDER BY receipt.rowid",
        (operational_cutoff,),
        "DELETE FROM estimator_snapshot_receipts WHERE rowid=?",
    )
    outbox = mutate_in_batches(
        "SELECT rowid FROM estimator_snapshot_publication_outbox "
        "WHERE delivered_at_utc IS NOT NULL "
        "AND julianday(delivered_at_utc)<julianday(?) ORDER BY rowid",
        (operational_cutoff,),
        "DELETE FROM estimator_snapshot_publication_outbox WHERE rowid=?",
    )
    rejections = mutate_in_batches(
        "SELECT rowid FROM estimator_snapshot_rejections "
        "WHERE julianday(rejected_at_utc)<julianday(?) ORDER BY rowid",
        (operational_cutoff,),
        "DELETE FROM estimator_snapshot_rejections WHERE rowid=?",
    )
    superseded_pending_receipts = mutate_in_batches(
        "SELECT receipt.rowid FROM estimator_snapshot_receipts AS receipt "
        "WHERE receipt.published_at_utc IS NULL "
        "AND NOT EXISTS(SELECT 1 FROM estimator_snapshot_publication_outbox AS intent "
        "WHERE intent.feed_mode=receipt.feed_mode "
        "AND intent.snapshot_version=receipt.snapshot_version "
        "AND intent.snapshot_id=receipt.snapshot_id) "
        "AND EXISTS(SELECT 1 FROM estimator_snapshot_receipts AS newer "
        "JOIN estimator_snapshot_publication_outbox AS delivered "
        "ON delivered.feed_mode=newer.feed_mode "
        "AND delivered.snapshot_version=newer.snapshot_version "
        "AND delivered.snapshot_id=newer.snapshot_id "
        "WHERE newer.feed_mode=receipt.feed_mode "
        "AND newer.snapshot_version>receipt.snapshot_version "
        "AND newer.published_at_utc IS NOT NULL "
        "AND delivered.delivered_at_utc IS NOT NULL) "
        "ORDER BY receipt.rowid",
        (),
        "DELETE FROM estimator_snapshot_receipts WHERE rowid=?",
    )
    return {
        "payloads_redacted": max(0, int(payloads)),
        "receipts_deleted": max(0, int(receipts)),
        "outbox_rows_deleted": max(0, int(outbox)),
        "rejections_deleted": max(0, int(rejections)),
        "superseded_pending_receipts_deleted": max(
            0, int(superseded_pending_receipts)
        ),
    }


def snapshot_receiver_metrics(
    connection: sqlite3.Connection,
    *,
    now_utc: datetime | None = None,
    expected_lane: str | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    snapshot_root: Path | None = None,
) -> dict[str, object]:
    if expected_lane not in {None, "PRIVATE_SHADOW", "PRIVATE_PRIMARY"}:
        raise EstimatorSnapshotReceiverError("snapshot_expected_lane_invalid")
    now = _utc(now_utc)
    lanes: dict[str, dict[str, object]] = {}
    for row in connection.execute(
        "SELECT receipt.feed_mode,receipt.snapshot_version,receipt.snapshot_id,"
        "receipt.received_at_utc,receipt.published_at_utc,receipt.payload_json,"
        "EXISTS(SELECT 1 FROM estimator_snapshot_publication_outbox AS outbox "
        "WHERE outbox.feed_mode=receipt.feed_mode "
        "AND outbox.snapshot_version=receipt.snapshot_version "
        "AND outbox.snapshot_id=receipt.snapshot_id "
        "AND outbox.delivered_at_utc IS NOT NULL) AS event_delivered "
        "FROM estimator_snapshot_receipts AS receipt "
        "WHERE receipt.snapshot_version=(SELECT MAX(newer.snapshot_version) "
        "FROM estimator_snapshot_receipts AS newer "
        "WHERE newer.feed_mode=receipt.feed_mode)"
    ).fetchall():
        published = row["published_at_utc"]
        publish_age: float | None = None
        snapshot_age: float | None = None
        view_matches_receipt: bool | None = None
        if published is not None:
            try:
                published_time = datetime.fromisoformat(
                    str(published).replace("Z", "+00:00")
                )
                publish_age = max(
                    0.0,
                    (now - published_time.astimezone(timezone.utc)).total_seconds(),
                )
            except ValueError:
                publish_age = None
        try:
            retained_snapshot = EstimatorSnapshotV2.model_validate_json(
                str(row["payload_json"])
            )
            snapshot_age = (
                now
                - retained_snapshot.generated_at_utc.astimezone(timezone.utc)
            ).total_seconds()
        except (TypeError, ValueError, ValidationError):
            snapshot_age = None
        if snapshot_root is not None:
            try:
                view = read_web_snapshot_view(
                    _lane_path(snapshot_root, str(row["feed_mode"])),
                    now_utc=now,
                )
                view_matches_receipt = bool(
                    int(view.get("snapshot_version") or 0)
                    == int(row["snapshot_version"])
                    and view.get("snapshot_hash") == str(row["snapshot_id"])
                    and view.get("feed_mode") == str(row["feed_mode"])
                    and published is not None
                    and view.get("published_at_utc") == str(published)
                )
                if snapshot_age is None:
                    snapshot_age = float(view["age_seconds"])
            except EstimatorSnapshotReceiverError:
                view_matches_receipt = False
        lanes[str(row["feed_mode"])] = {
            "snapshot_version": int(row["snapshot_version"]),
            "snapshot_id": str(row["snapshot_id"]),
            "received_at_utc": row["received_at_utc"],
            "published_at_utc": published,
            "publish_age_seconds": (
                round(publish_age, 3) if publish_age is not None else None
            ),
            "snapshot_age_seconds": (
                round(snapshot_age, 3) if snapshot_age is not None else None
            ),
            "publication_event_delivered": bool(row["event_delivered"]),
            "view_matches_receipt": view_matches_receipt,
        }
    pending_receipts = int(
        connection.execute(
            "SELECT COUNT(*) FROM estimator_snapshot_receipts "
            "WHERE published_at_utc IS NULL"
        ).fetchone()[0]
    )
    pending_events = int(
        connection.execute(
            "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
            "WHERE delivered_at_utc IS NULL"
        ).fetchone()[0]
    )
    expected = lanes.get(str(expected_lane)) if expected_lane is not None else None
    if expected_lane is None:
        readiness = "NOT_EVALUATED"
    elif expected is None:
        readiness = "MISSING"
    elif expected.get("published_at_utc") is None:
        readiness = "PENDING"
    elif not expected.get("publication_event_delivered"):
        readiness = "PENDING"
    elif snapshot_root is None:
        readiness = "VIEW_UNVERIFIED"
    elif expected.get("view_matches_receipt") is not True:
        readiness = "VIEW_MISMATCH"
    elif expected.get("snapshot_age_seconds") is None:
        readiness = "INVALID_TIME"
    elif float(expected["snapshot_age_seconds"]) < 0:
        readiness = "FUTURE"
    elif float(expected["snapshot_age_seconds"]) > float(stale_after_seconds):
        readiness = "STALE"
    elif pending_receipts or pending_events:
        readiness = "DEGRADED"
    else:
        readiness = "READY"
    return {
        "lanes": lanes,
        "expected_lane": expected_lane,
        "snapshot_readiness": readiness,
        "snapshot_ready": readiness == "READY",
        "pending_receipts": pending_receipts,
        "pending_publication_events": pending_events,
        "retained_receipt_count": int(
            connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_receipts"
            ).fetchone()[0]
        ),
        "retained_full_payload_count": int(
            connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_receipts "
                "WHERE payload_json<>'{}'"
            ).fetchone()[0]
        ),
        "rejection_count": int(
            connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_rejections"
            ).fetchone()[0]
        ),
    }


__all__ = [
    "SNAPSHOT_RECEIVER_SCHEMA",
    "SNAPSHOT_PUBLICATION_RECONCILIATION_SCHEMA",
    "EstimatorSnapshotReceiverError",
    "apply_estimator_snapshot",
    "activate_legacy_prediction_authority",
    "activate_private_prediction_authority",
    "compact_snapshot_receiver",
    "connect_snapshot_receiver",
    "estimator_snapshot_publication_event_id",
    "inspect_snapshot_publication_reconciliation_recovery",
    "reconcile_snapshot_publication_outbox",
    "update_prediction_ledger",
    "read_web_snapshot_view",
    "read_published_web_snapshot_view",
    "record_snapshot_rejection",
    "snapshot_receiver_metrics",
]
