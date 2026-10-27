"""Bot-side estimator snapshot builder, atomic publisher, and private sender."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Callable, Mapping

from .coin_rate_engine import COIN_RATE_ENGINE_VERSION
from .market_snapshot import build_market_snapshot
from .market_store import connect_market_store_read_only, verify_market_store_read_only
from .private_market_transport import (
    SNAPSHOT_PATH,
    MarketTransportError,
    client_tls_context,
    post_document,
    read_key,
)
from .private_pipeline_contracts import (
    EstimatorInputHealthV1,
    EstimatorInputTraceV1,
    EstimatorRateV2,
    EstimatorSnapshotV2,
    content_hash,
    estimator_snapshot_id,
)


ESTIMATOR_RUNTIME_SCHEMA = "coin_estimator_runtime/1.0"
SNAPSHOT_SENDER_SCHEMA = "estimator_snapshot_sender/1.0"
DEFAULT_INFERENCE_SECONDS = 5.0
_CODE = re.compile(r"[^A-Z0-9_]+")


class EstimatorSnapshotRuntimeError(RuntimeError):
    """Payload-free estimator/sender failure."""


def _inference_interval_seconds() -> float:
    raw = os.environ.get(
        "MARKET_PIPELINE_ESTIMATOR_INTERVAL_SECONDS",
        str(DEFAULT_INFERENCE_SECONDS),
    ).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise EstimatorSnapshotRuntimeError("coin_estimator_interval_invalid") from exc
    if not 1.0 <= value <= 60.0:
        raise EstimatorSnapshotRuntimeError("coin_estimator_interval_invalid")
    return value


@dataclass(frozen=True, slots=True)
class SnapshotPublishResult:
    snapshot_id: str
    snapshot_version: int
    input_snapshot_hash: str
    status: str
    recovered_pending: bool


@dataclass(frozen=True, slots=True)
class SnapshotSendResult:
    status: str
    snapshot_id: str | None
    snapshot_version: int


def _utc(value: datetime | None = None) -> datetime:
    return _precise_utc(value).replace(microsecond=0)


def _precise_utc(value: datetime | None = None) -> datetime:
    supplied = value or datetime.now(timezone.utc)
    if supplied.tzinfo is None or supplied.utcoffset() is None:
        raise EstimatorSnapshotRuntimeError("estimator_snapshot_time_timezone_required")
    return supplied.astimezone(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _precise_stamp(value: datetime | None = None) -> str:
    return _precise_utc(value).isoformat().replace("+00:00", "Z")


def _live_snapshot_completion_utc() -> datetime:
    """Return the wall-clock time after a live estimator build completes."""

    return _precise_utc()


def _restamp_live_snapshot(
    snapshot: EstimatorSnapshotV2,
    *,
    generated_at_utc: datetime,
) -> EstimatorSnapshotV2:
    """Bind a live artifact identity to its completion time.

    The estimator evaluates one pinned SQLite read snapshot at the earlier
    ``as_of_utc`` time.  A large Store can take longer than the receiver's
    freshness window to evaluate, however, so the transport artifact must be
    dated when that evaluation has actually completed.  The timestamp is part
    of the signed content identity and therefore the snapshot id is rebuilt as
    well.
    """

    payload = snapshot.model_dump(mode="json")
    payload.pop("snapshot_id", None)
    payload["generated_at_utc"] = _precise_stamp(generated_at_utc)
    return EstimatorSnapshotV2.model_validate(
        {**payload, "snapshot_id": estimator_snapshot_id(payload)}
    )


def _code(value: object, *, fallback: str) -> str:
    normalized = _CODE.sub("_", str(value or "").upper()).strip("_")
    if len(normalized) < 2:
        normalized = fallback
    if len(normalized) > 64:
        suffix = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8].upper()
        normalized = normalized[:55].rstrip("_") + "_" + suffix
    return normalized


def _decimal_text(value: object | None) -> str | None:
    if value is None:
        return None
    rendered = format(Decimal(str(value)), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _estimator_market_regime(value: object) -> str:
    """Translate Product regime labels to the estimator wire vocabulary."""

    normalized = str(value or "UNKNOWN").strip().upper()
    try:
        return {
            "NORMAL": "RANGE",
            "UP": "UP",
            "DOWN": "DOWN",
            "VOLATILE": "SHOCK",
            "UNKNOWN": "UNKNOWN",
        }[normalized]
    except KeyError as exc:
        raise EstimatorSnapshotRuntimeError(
            "estimator_snapshot_market_regime_invalid"
        ) from exc


def initialize_estimator_state(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS estimator_snapshot_publications (
          snapshot_version INTEGER PRIMARY KEY CHECK(snapshot_version>0),
          snapshot_id TEXT NOT NULL UNIQUE,
          feed_mode TEXT NOT NULL CHECK(feed_mode IN ('PRIVATE_SHADOW','PRIVATE_PRIMARY')),
          input_snapshot_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at_utc TEXT NOT NULL,
          published_at_utc TEXT
        );
        CREATE TABLE IF NOT EXISTS estimator_snapshot_sender_state (
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          acknowledged_version INTEGER NOT NULL CHECK(acknowledged_version>=0),
          acknowledged_snapshot_id TEXT,
          attempt_count INTEGER NOT NULL CHECK(attempt_count>=0),
          last_reason_code TEXT,
          updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS estimator_snapshot_sender_transitions (
          transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
          from_feed_mode TEXT NOT NULL,
          to_feed_mode TEXT NOT NULL,
          snapshot_version INTEGER NOT NULL CHECK(snapshot_version>0),
          snapshot_id TEXT NOT NULL,
          transitioned_at_utc TEXT NOT NULL,
          UNIQUE(to_feed_mode,snapshot_id)
        );
        INSERT OR IGNORE INTO estimator_snapshot_sender_state(
          singleton,acknowledged_version,acknowledged_snapshot_id,
          attempt_count,last_reason_code,updated_at_utc
        ) VALUES(1,0,NULL,0,NULL,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
        """
    )
    publication_columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(estimator_snapshot_publications)"
        )
    }
    for name, declaration in (
        ("contract", "TEXT"),
        ("quarantined_at_utc", "TEXT"),
        ("quarantine_reason", "TEXT"),
    ):
        if name not in publication_columns:
            connection.execute(
                f"ALTER TABLE estimator_snapshot_publications "
                f"ADD COLUMN {name} {declaration}"
            )
    sender_columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(estimator_snapshot_sender_state)"
        )
    }
    for name in ("acknowledged_contract", "acknowledged_feed_mode"):
        if name not in sender_columns:
            connection.execute(
                f"ALTER TABLE estimator_snapshot_sender_state ADD COLUMN {name} TEXT"
            )

    # Expand-only upgrade: preserve every historical row and its version, but
    # prevent an incompatible or malformed pending V1 payload from blocking
    # V2 publication forever.  Quarantined rows remain auditable and continue
    # to hold the monotonic version floor.
    pending_rows = connection.execute(
        "SELECT snapshot_version,payload_json,contract,published_at_utc,"
        "quarantined_at_utc FROM estimator_snapshot_publications"
    ).fetchall()
    for row in pending_rows:
        contract = str(row["contract"] or "").strip()
        malformed = False
        if not contract:
            try:
                payload = json.loads(str(row["payload_json"]))
                if not isinstance(payload, Mapping):
                    raise TypeError("pending_payload_not_object")
                contract = str(payload.get("contract") or "").strip()
            except (TypeError, json.JSONDecodeError):
                malformed = True
                contract = "MALFORMED"
            connection.execute(
                "UPDATE estimator_snapshot_publications SET contract=? "
                "WHERE snapshot_version=?",
                (contract, int(row["snapshot_version"])),
            )
        if (
            row["published_at_utc"] is None
            and row["quarantined_at_utc"] is None
            and contract != "estimator_snapshot/2.0"
        ):
            connection.execute(
                "UPDATE estimator_snapshot_publications "
                "SET quarantined_at_utc=?,quarantine_reason=? "
                "WHERE snapshot_version=?",
                (
                    _stamp(),
                    (
                        "PENDING_PAYLOAD_MALFORMED"
                        if malformed
                        else "PENDING_CONTRACT_UNSUPPORTED"
                    ),
                    int(row["snapshot_version"]),
                ),
            )
    connection.commit()


def _trace_row(
    connection: sqlite3.Connection,
    *,
    source_codes: tuple[str, ...],
    last_event_utc: str,
    source_event_key: str,
) -> sqlite3.Row | None:
    if not source_codes or not last_event_utc or not source_event_key:
        return None
    try:
        event_key = bytes.fromhex(source_event_key)
    except ValueError as exc:
        raise EstimatorSnapshotRuntimeError(
            "estimator_snapshot_source_event_key_invalid"
        ) from exc
    if len(event_key) != 32 or event_key.hex() != source_event_key.lower():
        raise EstimatorSnapshotRuntimeError(
            "estimator_snapshot_source_event_key_invalid"
        )
    placeholders = ",".join("?" for _ in source_codes)
    return connection.execute(
        f"""
        SELECT o.event_key,o.source_code,o.event_time_utc,o.available_at_utc,
               p.fact_id,p.fact_revision,p.occurred_at_utc,p.available_at_utc AS fact_available,
               p.parsed_at_utc,p.transferred_at_utc
        FROM market_observations o
        JOIN private_fact_adapter_projections p ON p.event_key=o.event_key
        WHERE o.quality_state='ELIGIBLE'
          AND o.source_code IN ({placeholders})
          AND o.event_time_utc=?
          AND o.event_key=?
        LIMIT 1
        """,
        (*source_codes, last_event_utc, event_key),
    ).fetchone()


def _input_traces(
    connection: sqlite3.Connection,
    signals: Mapping[str, Mapping[str, object]],
) -> tuple[EstimatorInputTraceV1, ...]:
    output: list[EstimatorInputTraceV1] = []
    for component, signal in sorted(signals.items()):
        status = str(signal.get("status") or "MISSING").upper()
        if status not in {"FRESH", "STALE", "MISSING", "REJECTED"}:
            status = "REJECTED"
        source_codes = tuple(str(item) for item in signal.get("source_codes") or ())
        method = _code(signal.get("method"), fallback="NO_DATA")
        common = {
            "component": _code(component, fallback="UNKNOWN_INPUT"),
            "source_codes": source_codes,
            "unit": _code(signal.get("price_unit"), fallback="UNKNOWN_UNIT"),
            "sample_count": int(signal.get("observation_count") or 0),
            "selection_method": method,
            "fallback": any(token in method for token in ("FALLBACK", "BRIDGE", "PROXY")),
            "freshness": status,
            "age_seconds": signal.get("age_seconds"),
        }
        if status == "MISSING":
            output.append(EstimatorInputTraceV1(**common))
            continue
        row = _trace_row(
            connection,
            source_codes=source_codes,
            last_event_utc=str(signal.get("last_event_utc") or ""),
            source_event_key=str(signal.get("source_event_key") or ""),
        )
        if row is None:
            raise EstimatorSnapshotRuntimeError(
                "estimator_snapshot_source_trace_unavailable"
            )
        output.append(
            EstimatorInputTraceV1(
                source_event_key=bytes(row["event_key"]).hex(),
                source_fact_id=str(row["fact_id"]),
                fact_revision=int(row["fact_revision"]),
                occurred_at_utc=str(row["occurred_at_utc"]),
                available_at_utc=str(row["fact_available"]),
                parsed_at_utc=str(row["parsed_at_utc"]),
                transferred_at_utc=str(row["transferred_at_utc"]),
                point_value=_decimal_text(signal.get("latest_price")),
                mean_value=_decimal_text(signal.get("mean_price")),
                **common,
            )
        )
    return tuple(output)


def build_estimator_snapshot(
    connection: sqlite3.Connection,
    *,
    as_of_utc: datetime,
    snapshot_version: int,
    feed_mode: str,
    generated_at_utc: datetime | None = None,
) -> EstimatorSnapshotV2:
    if feed_mode not in {"PRIVATE_SHADOW", "PRIVATE_PRIMARY"}:
        raise EstimatorSnapshotRuntimeError("estimator_snapshot_feed_mode_invalid")
    generated_at = _precise_utc(generated_at_utc or as_of_utc)
    market = build_market_snapshot(connection, as_of_utc=as_of_utc)
    traces = _input_traces(connection, market["signals"])
    input_snapshot_hash = content_hash(
        [item.model_dump(mode="json") for item in traces]
    )
    rates: list[EstimatorRateV2] = []
    for item in market["rates"]["items"]:
        status = str(item["status"])
        underlying_source = item.get("underlying_source")
        reason = item.get("reason")
        rates.append(
            EstimatorRateV2(
                instrument="COIN_" + str(item["commodity_code"]),
                settlement=str(item["settlement_term"]),
                status=status,
                value=(
                    str(item["estimated_project_price"])
                    if item["estimated_project_price"] is not None
                    else None
                ),
                unit="PROJECT_THOUSAND_TOMAN",
                lower_bound=(
                    str(item["lower_project_price"])
                    if item["lower_project_price"] is not None
                    else None
                ),
                upper_bound=(
                    str(item["upper_project_price"])
                    if item["upper_project_price"] is not None
                    else None
                ),
                confidence=str(item["confidence"]),
                method=_code(item["method"], fallback="UNKNOWN_METHOD"),
                reason_code=(
                    _code(reason, fallback="NO_DATA_REASON_UNAVAILABLE")
                    if status == "NO_DATA"
                    else None
                ),
                underlying_source=(
                    _code(underlying_source, fallback="UNKNOWN_SOURCE")
                    if underlying_source is not None
                    else None
                ),
                underlying_age_seconds=item.get("underlying_age_seconds"),
                anchor_age_seconds=item.get("anchor_age_seconds"),
                market_regime=_estimator_market_regime(item["market_regime"]),
            )
        )
    health = tuple(
        EstimatorInputHealthV1(
            component=trace.component,
            status=trace.freshness,
            latest_available_at_utc=trace.available_at_utc,
            age_seconds=trace.age_seconds,
            reason_codes=(),
        )
        for trace in traces
    )
    estimated_count = sum(rate.status == "ESTIMATED" for rate in rates)
    status = "OK" if estimated_count else "SAFE_NO_DATA"
    payload = {
        "contract": "estimator_snapshot/2.0",
        "snapshot_version": snapshot_version,
        "generated_at_utc": _precise_stamp(generated_at),
        "input_snapshot_hash": input_snapshot_hash,
        "model_version": COIN_RATE_ENGINE_VERSION,
        "feed_mode": feed_mode,
        "status": status,
        "rates": [item.model_dump(mode="json") for item in rates],
        "health": [item.model_dump(mode="json") for item in health],
        "inputs": [item.model_dump(mode="json") for item in traces],
        "reason_codes": ([] if estimated_count else ["NO_ESTIMATED_COIN_RATES"]),
    }
    return EstimatorSnapshotV2.model_validate(
        {**payload, "snapshot_id": estimator_snapshot_id(payload)}
    )


def publish_estimator_snapshot(
    *,
    market_store_path: Path | str,
    state_path: Path | str,
    output_path: Path | str,
    feed_mode: str,
    as_of_utc: datetime | None = None,
) -> SnapshotPublishResult:
    from .private_pipeline_foundation import atomic_json_write

    state = sqlite3.connect(state_path)
    state.row_factory = sqlite3.Row
    initialize_estimator_state(state)
    with state:
        state.execute(
            "UPDATE estimator_snapshot_publications "
            "SET quarantined_at_utc=?,quarantine_reason='PENDING_FEED_MODE_MISMATCH' "
            "WHERE published_at_utc IS NULL AND quarantined_at_utc IS NULL "
            "AND contract='estimator_snapshot/2.0' AND feed_mode<>?",
            (_stamp(), feed_mode),
        )
    pending = state.execute(
        "SELECT * FROM estimator_snapshot_publications "
        "WHERE published_at_utc IS NULL AND quarantined_at_utc IS NULL "
        "AND contract='estimator_snapshot/2.0' AND feed_mode=? "
        "ORDER BY snapshot_version LIMIT 1",
        (feed_mode,),
    ).fetchone()
    recovered = pending is not None
    if pending is None:
        row = state.execute(
            "SELECT COALESCE(MAX(snapshot_version),0) FROM estimator_snapshot_publications"
        ).fetchone()
        version = int(row[0]) + 1
        market = connect_market_store_read_only(market_store_path)
        try:
            verify_market_store_read_only(market)
            market.execute("BEGIN")
            # Pin the SQLite read snapshot before choosing a live evaluation
            # time.  Otherwise a fact may commit after the timestamp is
            # chosen but before the first estimator SELECT.
            metadata = market.execute(
                "SELECT schema_version FROM market_store_metadata WHERE singleton=1"
            ).fetchone()
            if metadata is None:
                raise EstimatorSnapshotRuntimeError(
                    "estimator_snapshot_market_metadata_missing"
                )
            generated_at = _precise_utc(as_of_utc)
            snapshot = build_estimator_snapshot(
                market,
                as_of_utc=_utc(generated_at),
                snapshot_version=version,
                feed_mode=feed_mode,
                generated_at_utc=generated_at,
            )
            if as_of_utc is None:
                completed_at = _live_snapshot_completion_utc()
                if completed_at < generated_at:
                    raise EstimatorSnapshotRuntimeError(
                        "estimator_snapshot_completion_time_regression"
                    )
                snapshot = _restamp_live_snapshot(
                    snapshot,
                    generated_at_utc=completed_at,
                )
            market.rollback()
        finally:
            market.close()
        payload_json = snapshot.model_dump_json()
        with state:
            state.execute(
                "INSERT INTO estimator_snapshot_publications("
                "snapshot_version,snapshot_id,feed_mode,input_snapshot_hash,"
                "payload_json,created_at_utc,published_at_utc,contract,"
                "quarantined_at_utc,quarantine_reason"
                ") VALUES(?,?,?,?,?,?,NULL,'estimator_snapshot/2.0',NULL,NULL)",
                (
                    version,
                    snapshot.snapshot_id,
                    feed_mode,
                    snapshot.input_snapshot_hash,
                    payload_json,
                    _stamp(),
                ),
            )
    else:
        payload_json = str(pending["payload_json"])
        snapshot = EstimatorSnapshotV2.model_validate_json(payload_json)
    try:
        atomic_json_write(Path(output_path), json.loads(payload_json))
        with state:
            state.execute(
                "UPDATE estimator_snapshot_publications SET published_at_utc=? "
                "WHERE snapshot_version=?",
                (_stamp(), snapshot.snapshot_version),
            )
            state.execute(
                "DELETE FROM estimator_snapshot_publications WHERE snapshot_version < ? "
                "AND published_at_utc IS NOT NULL",
                (max(1, snapshot.snapshot_version - 100),),
            )
    finally:
        state.close()
    return SnapshotPublishResult(
        snapshot_id=snapshot.snapshot_id,
        snapshot_version=snapshot.snapshot_version,
        input_snapshot_hash=snapshot.input_snapshot_hash,
        status=snapshot.status,
        recovered_pending=recovered,
    )


def send_latest_snapshot(
    *,
    snapshot_path: Path | str,
    state_path: Path | str,
    expected_feed_mode: str,
    send: Callable[[Mapping[str, object]], tuple[int, Mapping[str, object]]],
    acknowledged_view_path: Path | str | None = None,
) -> SnapshotSendResult:
    snapshot = EstimatorSnapshotV2.model_validate_json(
        Path(snapshot_path).read_text(encoding="utf-8")
    )
    if expected_feed_mode not in {"PRIVATE_SHADOW", "PRIVATE_PRIMARY"}:
        raise EstimatorSnapshotRuntimeError("snapshot_sender_expected_feed_mode_invalid")
    if snapshot.feed_mode != expected_feed_mode:
        raise EstimatorSnapshotRuntimeError("snapshot_sender_artifact_feed_mode_mismatch")
    state = sqlite3.connect(state_path)
    state.row_factory = sqlite3.Row
    initialize_estimator_state(state)
    row = state.execute(
        "SELECT acknowledged_version,acknowledged_snapshot_id,"
        "acknowledged_contract,acknowledged_feed_mode "
        "FROM estimator_snapshot_sender_state WHERE singleton=1"
    ).fetchone()
    acknowledged = int(row["acknowledged_version"])
    acknowledged_contract = str(row["acknowledged_contract"] or "")
    acknowledged_feed_mode = str(row["acknowledged_feed_mode"] or "")
    if acknowledged and acknowledged_contract not in {
        "",
        "estimator_snapshot/2.0",
    }:
        state.close()
        raise EstimatorSnapshotRuntimeError("snapshot_sender_contract_mismatch")
    if snapshot.snapshot_version < acknowledged:
        state.close()
        raise EstimatorSnapshotRuntimeError("snapshot_sender_version_regression")
    acknowledged_path = (
        Path(acknowledged_view_path) if acknowledged_view_path is not None else None
    )
    acknowledged_view_is_current = False
    if (
        acknowledged_path is not None
        and not acknowledged_path.is_symlink()
        and acknowledged_path.is_file()
    ):
        try:
            local_view = json.loads(acknowledged_path.read_text(encoding="utf-8"))
            local_snapshot = EstimatorSnapshotV2.model_validate(local_view["snapshot"])
            acknowledged_view_is_current = (
                local_view.get("contract") == "estimator_snapshot_web_view/1.0"
                and local_view.get("snapshot_hash") == snapshot.snapshot_id
                and int(local_view.get("snapshot_version") or 0)
                == snapshot.snapshot_version
                and local_view.get("feed_mode") == snapshot.feed_mode
                and local_snapshot.snapshot_id == snapshot.snapshot_id
                and local_snapshot.snapshot_version == snapshot.snapshot_version
                and local_snapshot.feed_mode == snapshot.feed_mode
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            acknowledged_view_is_current = False
    if snapshot.snapshot_version == acknowledged:
        if str(row["acknowledged_snapshot_id"] or "") != snapshot.snapshot_id:
            state.close()
            raise EstimatorSnapshotRuntimeError("snapshot_sender_version_conflict")
        if acknowledged_path is None or acknowledged_view_is_current:
            state.close()
            return SnapshotSendResult(
                "ALREADY_ACKNOWLEDGED", snapshot.snapshot_id, acknowledged
            )
    try:
        status, response = send(snapshot.model_dump(mode="json"))
    except BaseException:
        with state:
            state.execute(
                "UPDATE estimator_snapshot_sender_state SET attempt_count=attempt_count+1,"
                "last_reason_code='TRANSPORT_UNAVAILABLE',updated_at_utc=? WHERE singleton=1",
                (_stamp(),),
            )
        state.close()
        raise
    if (
        status != 200
        or response.get("status") != "ACK"
        or response.get("snapshot_id") != snapshot.snapshot_id
        or int(response.get("snapshot_version") or 0) != snapshot.snapshot_version
        or response.get("snapshot_hash") != snapshot.snapshot_id
    ):
        with state:
            state.execute(
                "UPDATE estimator_snapshot_sender_state SET attempt_count=attempt_count+1,"
                "last_reason_code='ACK_INVALID',updated_at_utc=? WHERE singleton=1",
                (_stamp(),),
            )
        state.close()
        raise EstimatorSnapshotRuntimeError("snapshot_sender_ack_invalid")
    if acknowledged_path is not None:
        web_view = response.get("web_view")
        if not isinstance(web_view, Mapping):
            state.close()
            raise EstimatorSnapshotRuntimeError("snapshot_sender_web_view_missing")
        try:
            acknowledged_snapshot = EstimatorSnapshotV2.model_validate(
                web_view["snapshot"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            state.close()
            raise EstimatorSnapshotRuntimeError(
                "snapshot_sender_web_view_invalid"
            ) from exc
        if (
            web_view.get("contract") != "estimator_snapshot_web_view/1.0"
            or web_view.get("snapshot_hash") != snapshot.snapshot_id
            or int(web_view.get("snapshot_version") or 0)
            != snapshot.snapshot_version
            or web_view.get("feed_mode") != snapshot.feed_mode
            or acknowledged_snapshot.snapshot_id != snapshot.snapshot_id
            or acknowledged_snapshot.snapshot_version != snapshot.snapshot_version
            or acknowledged_snapshot.feed_mode != snapshot.feed_mode
        ):
            state.close()
            raise EstimatorSnapshotRuntimeError(
                "snapshot_sender_web_view_identity_mismatch"
            )
        from .private_pipeline_foundation import atomic_json_write

        try:
            atomic_json_write(acknowledged_path, dict(web_view))
        except OSError:
            state.close()
            raise
    with state:
        if acknowledged_feed_mode and acknowledged_feed_mode != snapshot.feed_mode:
            state.execute(
                "INSERT OR IGNORE INTO estimator_snapshot_sender_transitions("
                "from_feed_mode,to_feed_mode,snapshot_version,snapshot_id,"
                "transitioned_at_utc) VALUES(?,?,?,?,?)",
                (
                    acknowledged_feed_mode,
                    snapshot.feed_mode,
                    snapshot.snapshot_version,
                    snapshot.snapshot_id,
                    _stamp(),
                ),
            )
        state.execute(
            "UPDATE estimator_snapshot_sender_state SET acknowledged_version=?,"
            "acknowledged_snapshot_id=?,attempt_count=0,last_reason_code=NULL,"
            "updated_at_utc=?,acknowledged_contract='estimator_snapshot/2.0',"
            "acknowledged_feed_mode=? WHERE singleton=1",
            (
                snapshot.snapshot_version,
                snapshot.snapshot_id,
                _stamp(),
                snapshot.feed_mode,
            ),
        )
    state.close()
    return SnapshotSendResult("ACKNOWLEDGED", snapshot.snapshot_id, snapshot.snapshot_version)


def run_coin_estimator_service(
    *, role: str, mode: str, release_sha: str, state_directory: Path, stop: threading.Event
) -> int:
    if role != "coin-estimator" or mode != "live":
        raise EstimatorSnapshotRuntimeError("coin_estimator_role_or_mode_invalid")
    feed_mode = os.environ.get("MARKET_PIPELINE_FEED_MODE", "LEGACY").strip().upper()
    if feed_mode not in {"LEGACY", "PRIVATE_SHADOW", "PRIVATE_PRIMARY"}:
        raise EstimatorSnapshotRuntimeError("coin_estimator_feed_mode_invalid")
    inference_interval_seconds = _inference_interval_seconds()
    from .private_pipeline_foundation import atomic_json_write

    started = _stamp()
    latest: SnapshotPublishResult | None = None
    while not stop.is_set():
        cycle_started = time.monotonic()
        if feed_mode != "LEGACY":
            latest = publish_estimator_snapshot(
                market_store_path=os.environ.get(
                    "MARKET_PIPELINE_PRIVATE_MARKET_STORE_PATH",
                    "/var/lib/market-data/market-store/market-store.sqlite",
                ),
                state_path=state_directory / "estimator-state.sqlite3",
                output_path=os.environ.get(
                    "MARKET_PIPELINE_ESTIMATOR_SNAPSHOT_PATH",
                    "/var/lib/market-data/snapshots/latest-estimator-snapshot.json",
                ),
                feed_mode=feed_mode,
            )
        atomic_json_write(
            state_directory / "health.json",
            {
                "schema": ESTIMATOR_RUNTIME_SCHEMA,
                "role": role,
                "mode": mode,
                "release_sha": release_sha,
                "pid": os.getpid(),
                "started_at_utc": started,
                "updated_at_utc": _stamp(),
                "status": "live-ready",
                "feed_mode": feed_mode,
                "inference_interval_seconds": inference_interval_seconds,
                "publishing_private_snapshot": feed_mode != "LEGACY",
                "latest": asdict(latest) if latest else None,
            },
        )
        interval = inference_interval_seconds if feed_mode != "LEGACY" else 1.0
        stop.wait(max(0.0, interval - (time.monotonic() - cycle_started)))
    return 0


def run_estimator_snapshot_sender_service(
    *, role: str, mode: str, release_sha: str, state_directory: Path, stop: threading.Event
) -> int:
    if role != "estimator-snapshot-sender" or mode != "live":
        raise EstimatorSnapshotRuntimeError("snapshot_sender_role_or_mode_invalid")
    from .private_pipeline_foundation import atomic_json_write

    feed_mode = os.environ.get("MARKET_PIPELINE_FEED_MODE", "LEGACY").strip().upper()
    if feed_mode not in {"LEGACY", "PRIVATE_SHADOW", "PRIVATE_PRIMARY"}:
        raise EstimatorSnapshotRuntimeError("snapshot_sender_feed_mode_invalid")
    snapshot_path = Path(
        os.environ.get(
            "MARKET_PIPELINE_ESTIMATOR_SNAPSHOT_PATH",
            "/var/lib/market-data/snapshots/latest-estimator-snapshot.json",
        )
    )
    acknowledged_view_path = Path(
        os.environ.get(
            "MARKET_PIPELINE_ACKNOWLEDGED_SNAPSHOT_PATH",
            str(
                snapshot_path.parent
                / (
                    "latest-private-primary.json"
                    if feed_mode == "PRIVATE_PRIMARY"
                    else "latest-private-shadow.json"
                )
            ),
        )
    )
    host = ""
    port = 9443
    key_id = ""
    hmac_key = b""
    tls = None
    if feed_mode != "LEGACY":
        host = os.environ.get("MARKET_SNAPSHOT_RECEIVER_HOST", "").strip()
        port = int(os.environ.get("MARKET_SNAPSHOT_RECEIVER_PORT", "9443"))
        key_id = os.environ.get("MARKET_HMAC_ACTIVE_KEY_ID", "active-v1").strip()
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
                "MARKET_TRANSPORT_CERT_PATH",
                "/run/secrets/market_bot_transport_cert",
            ),
            key=os.environ.get(
                "MARKET_TRANSPORT_KEY_PATH",
                "/run/secrets/market_bot_transport_key",
            ),
        )
        if not host:
            raise EstimatorSnapshotRuntimeError("snapshot_sender_private_host_required")
    started = _stamp()
    latest = SnapshotSendResult("IDLE", None, 0)
    failures = 0
    while not stop.is_set():
        if feed_mode != "LEGACY" and snapshot_path.is_file():
            try:
                latest = send_latest_snapshot(
                    snapshot_path=snapshot_path,
                    state_path=state_directory / "sender-state.sqlite3",
                    expected_feed_mode=feed_mode,
                    send=lambda document: post_document(
                        host=host,
                        port=port,
                        path=SNAPSHOT_PATH,
                        document=document,
                        key_id=key_id,
                        hmac_key=hmac_key,
                        tls_context=tls,
                        timeout_seconds=3.0,
                    ),
                    acknowledged_view_path=acknowledged_view_path,
                )
                failures = 0
            except (EstimatorSnapshotRuntimeError, MarketTransportError, OSError):
                failures += 1
        elif feed_mode != "LEGACY":
            failures += 1
            latest = SnapshotSendResult("SNAPSHOT_MISSING", None, 0)
        atomic_json_write(
            state_directory / "health.json",
            {
                "schema": SNAPSHOT_SENDER_SCHEMA,
                "role": role,
                "mode": mode,
                "release_sha": release_sha,
                "pid": os.getpid(),
                "started_at_utc": started,
                "updated_at_utc": _stamp(),
                "status": "live-ready" if failures < 8 else "live-degraded",
                "feed_mode": feed_mode,
                "private_transport_only": True,
                "receiver_acknowledged_view_required": feed_mode != "LEGACY",
                "receiver_acknowledged_view_present": (
                    acknowledged_view_path.is_file() if feed_mode != "LEGACY" else False
                ),
                "consecutive_failures": failures,
                "latest": asdict(latest),
            },
        )
        delay = min(30.0, 0.25 * (2 ** min(failures, 7))) if failures else 0.25
        stop.wait(delay)
    return 0


__all__ = [
    "ESTIMATOR_RUNTIME_SCHEMA",
    "SNAPSHOT_SENDER_SCHEMA",
    "EstimatorSnapshotRuntimeError",
    "build_estimator_snapshot",
    "initialize_estimator_state",
    "publish_estimator_snapshot",
    "run_coin_estimator_service",
    "run_estimator_snapshot_sender_service",
    "send_latest_snapshot",
]
