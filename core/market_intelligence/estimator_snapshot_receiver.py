"""Durable web-side projection for versioned estimator snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Mapping

from pydantic import ValidationError

from .coin_prediction_anchors import PREDICTION_AUTHORITY_BASELINE_EPOCH
from .coin_rate_engine import COIN_RATE_ENGINE_VERSION
from .private_pipeline_contracts import EstimatorSnapshotV2


SNAPSHOT_RECEIVER_SCHEMA = "estimator_snapshot_receiver/1.0"
DEFAULT_STALE_AFTER_SECONDS = 30
MAXIMUM_RECEIVE_AGE_SECONDS = 60
PREDICTION_LEDGER_RETENTION = timedelta(hours=24)
SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES = 8 * 1024 * 1024
SNAPSHOT_PAYLOAD_RETENTION = timedelta(hours=24)
SNAPSHOT_OPERATIONAL_RETENTION = timedelta(days=7)
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
    event_id = hashlib.sha256(
        f"estimator:snapshot-published\0{lane}\0{snapshot.snapshot_id}".encode(
            "ascii"
        )
    ).hexdigest()
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


def compact_snapshot_receiver(
    connection: sqlite3.Connection,
    *,
    now_utc: datetime | None = None,
) -> dict[str, int]:
    """Bound operational growth without weakening the latest-version fence."""

    now = _utc(now_utc)
    payload_cutoff = _stamp(now - SNAPSHOT_PAYLOAD_RETENTION)
    operational_cutoff = _stamp(now - SNAPSHOT_OPERATIONAL_RETENTION)
    connection.execute("BEGIN IMMEDIATE")
    try:
        payloads = connection.execute(
            "UPDATE estimator_snapshot_receipts SET payload_json='{}' "
            "WHERE published_at_utc IS NOT NULL AND payload_json<>'{}' "
            "AND julianday(published_at_utc)<julianday(?)",
            (payload_cutoff,),
        ).rowcount
        receipts = connection.execute(
            "DELETE FROM estimator_snapshot_receipts "
            "WHERE published_at_utc IS NOT NULL "
            "AND julianday(published_at_utc)<julianday(?) "
            "AND snapshot_version<(SELECT MAX(newer.snapshot_version) "
            "FROM estimator_snapshot_receipts AS newer "
            "WHERE newer.feed_mode=estimator_snapshot_receipts.feed_mode)",
            (operational_cutoff,),
        ).rowcount
        outbox = connection.execute(
            "DELETE FROM estimator_snapshot_publication_outbox "
            "WHERE delivered_at_utc IS NOT NULL "
            "AND julianday(delivered_at_utc)<julianday(?)",
            (operational_cutoff,),
        ).rowcount
        rejections = connection.execute(
            "DELETE FROM estimator_snapshot_rejections "
            "WHERE julianday(rejected_at_utc)<julianday(?)",
            (operational_cutoff,),
        ).rowcount
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {
        "payloads_redacted": max(0, int(payloads)),
        "receipts_deleted": max(0, int(receipts)),
        "outbox_rows_deleted": max(0, int(outbox)),
        "rejections_deleted": max(0, int(rejections)),
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
    "EstimatorSnapshotReceiverError",
    "apply_estimator_snapshot",
    "activate_legacy_prediction_authority",
    "activate_private_prediction_authority",
    "compact_snapshot_receiver",
    "connect_snapshot_receiver",
    "update_prediction_ledger",
    "read_web_snapshot_view",
    "read_published_web_snapshot_view",
    "record_snapshot_rejection",
    "snapshot_receiver_metrics",
]
