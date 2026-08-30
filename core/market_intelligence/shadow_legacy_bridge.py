"""One-way Shadow → Legacy estimator input projection.

Product authority stays LEGACY.  This module copies only model-usable
canonical fields from a read-only Shadow Market Store into the Legacy
Market Store.  It never writes Shadow, never converts price units, and
never stores raw Telegram text or identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import blake2b
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .market_contracts import (
    MARKET_STORE_CONTRACT_VERSION,
    MarketObservation,
    MarketStoreContractError,
    QUALITY_STATES,
    normalize_utc,
)
from .market_store import (
    MARKET_STORE_SCHEMA_VERSION,
    connect_market_store,
    connect_market_store_read_only,
    upsert_observation,
    verify_market_store_read_only,
)


BRIDGE_VERSION = "private-shadow-legacy-bridge-v1"
HEALTH_SCHEMA = "private-shadow-legacy-bridge-health/1.0"
AUTHORIZED_CUTOFF_UTC = "2026-08-25T09:33:00Z"
INCREMENTAL_OVERLAP_SECONDS = 120
PRIVATE_SOURCES = frozenset({"PRIVATE_GOLD_CHANNEL", "PRIVATE_GOLD_PAPER_MINUTE"})
GROUP_SOURCES = frozenset({"GROUP_1", "GROUP_2"})
MARKET_BRIDGE_SOURCES = frozenset(PRIVATE_SOURCES | GROUP_SOURCES)
RETIRED_QUALITY = "IGNORED"
NON_REALTIME_QUALITY = frozenset({"PENDING_REVIEW", "REJECTED", "IGNORED", "AMBIGUOUS"})

_OBSERVATION_COLUMNS = (
    "event_key",
    "source_code",
    "source_family",
    "event_time_utc",
    "available_at_utc",
    "instrument",
    "market_label",
    "settlement_term",
    "trade_form",
    "event_type",
    "side",
    "price_value",
    "price_num",
    "price_unit",
    "currency",
    "quantity_value",
    "quantity_num",
    "quantity_unit",
    "parse_confidence",
    "parser_version",
    "quality_state",
    "quality_policy_version",
    "is_conditional",
    "attributes_json",
)
_ATTRIBUTE_ALLOWLIST = frozenset(
    {
        "adapter_disposition",
        "commodity_resolution",
        "condition_class",
        "conditional_reason",
        "confirmation_kind",
        "delivery_sequence",
        "executed_quantity",
        "fact_id",
        "fact_revision",
        "group_number",
        "has_description",
        "human_feedback_revision",
        "human_pattern_calibration_fields",
        "human_pattern_calibration_revision",
        "is_aggregate",
        "lifecycle_phase",
        "offer_fact_id",
        "outcome",
        "paper_variant",
        "quality_reason_codes",
        "remaining_quantity",
        "requires_market_comparability",
        "resolution_reason",
        "root_offer_event_key",
        "root_offer_fact_id",
        "trade_status",
        "transfer_fact_id",
    }
)
_FORBIDDEN_ATTRIBUTE_PARTS = frozenset(
    {
        "channel",
        "chat",
        "link",
        "message",
        "phone",
        "raw",
        "sender",
        "source_text",
        "telegram",
        "text",
        "user",
        "username",
        "actor",
        "participant",
        "reply",
    }
)
_PARSER_PREFIXES = (
    "coin-group-rules-v",
    "coin-group-context-v",
    "coin-group-trade-link-v",
    "private-gold-rules-v",
    "private-gold-minute-v",
    "private-gold-trade-revisions-v",
    "human-feedback-r",
    "human-pattern-r",
)
_LEGACY_PARSER = re.compile(r"^legacy-parser-sha256:[0-9a-f]{64}$")
_HEX_KEY = re.compile(r"^[0-9a-f]{32,128}$")
_FACT_ID = re.compile(r"^[0-9a-f]{64}$")
_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_ledger (
    event_key BLOB PRIMARY KEY CHECK(length(event_key) BETWEEN 16 AND 64),
    source_code TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    quality_state TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    event_time_utc TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('APPLIED','RETIRED')),
    projection_revision INTEGER NOT NULL CHECK(projection_revision > 0),
    projected_at_utc TEXT NOT NULL,
    projection_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS projection_ledger_source_status
    ON projection_ledger(source_code, status);
CREATE TABLE IF NOT EXISTS projection_checkpoint (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    last_completed_at_utc TEXT NOT NULL,
    last_status TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    last_source_available_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS projection_source_watermark (
    source_code TEXT PRIMARY KEY,
    last_available_at_utc TEXT
);
"""


class BridgeError(RuntimeError):
    """Payload-free bridge failure."""


@dataclass(frozen=True, slots=True)
class ProjectionCounts:
    projected: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    selected: int = 0
    audit_only: int = 0

    def merged(self, other: "ProjectionCounts") -> "ProjectionCounts":
        return ProjectionCounts(
            projected=self.projected + other.projected,
            updated=self.updated + other.updated,
            unchanged=self.unchanged + other.unchanged,
            removed=self.removed + other.removed,
            selected=self.selected + other.selected,
            audit_only=self.audit_only + other.audit_only,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "projected": self.projected,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "selected": self.selected,
            "audit_only": self.audit_only,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def require_product_mode_legacy() -> str:
    mode = str(os.environ.get("PRODUCT_ESTIMATOR_SNAPSHOT_MODE") or "LEGACY").strip()
    if mode != "LEGACY":
        raise BridgeError("product_mode_not_legacy")
    return mode


def sqlite_quick_check(path: Path) -> str:
    if not path.is_file():
        raise BridgeError("quick_check_target_missing")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=15)
    try:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA query_only=ON")
        result = connection.execute("PRAGMA quick_check").fetchone()
        return "OK" if result is not None and str(result[0]).lower() == "ok" else "FAILED"
    finally:
        connection.close()


def online_backup(source: Path, destination: Path) -> None:
    """Copy a live SQLite database with the official backup API."""

    if destination.exists():
        raise BridgeError("backup_destination_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True, timeout=60)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    os.chmod(destination, 0o600)


def parser_version_allowed(version: str) -> bool:
    value = str(version or "").strip()
    if not value or "staging-market-input-bridge" in value:
        return False
    if _LEGACY_PARSER.fullmatch(value):
        return True
    for part in value.split("+"):
        token = part.strip()
        if not token:
            return False
        if _LEGACY_PARSER.fullmatch(token):
            continue
        if not any(token.startswith(prefix) for prefix in _PARSER_PREFIXES):
            return False
        suffix = token[len(next(p for p in _PARSER_PREFIXES if token.startswith(p))) :]
        if not suffix or not re.fullmatch(r"[A-Za-z0-9._-]+", suffix):
            return False
    return True


def _sanitize_attributes(raw: object) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise BridgeError("attributes_json_invalid") from exc
    elif isinstance(raw, Mapping):
        loaded = raw
    else:
        raise BridgeError("attributes_json_invalid")
    if not isinstance(loaded, Mapping):
        raise BridgeError("attributes_json_invalid")
    sanitized: dict[str, Any] = {}
    for key, value in loaded.items():
        name = str(key).strip()
        lowered = name.lower()
        if any(part in lowered for part in _FORBIDDEN_ATTRIBUTE_PARTS):
            continue
        if name not in _ATTRIBUTE_ALLOWLIST:
            continue
        if name == "root_offer_event_key":
            hex_key = str(value or "").strip().lower()
            if hex_key and not _HEX_KEY.fullmatch(hex_key):
                raise BridgeError("root_offer_event_key_invalid")
            sanitized[name] = hex_key
            continue
        if name == "root_offer_fact_id":
            fact_id = str(value or "").strip().lower()
            if fact_id and not _FACT_ID.fullmatch(fact_id):
                raise BridgeError("root_offer_fact_id_invalid")
            sanitized[name] = fact_id
            continue
        sanitized[name] = value
    return sanitized


def observation_from_row(row: sqlite3.Row) -> MarketObservation:
    quality = str(row["quality_state"] or "").strip().upper()
    if quality not in QUALITY_STATES:
        raise BridgeError("quality_state_unknown")
    parser_version = str(row["parser_version"] or "").strip()
    if not parser_version_allowed(parser_version):
        raise BridgeError("parser_version_incompatible")
    quantity = row["quantity_num"]
    quantity_unit = row["quantity_unit"]
    return MarketObservation(
        event_key=bytes(row["event_key"]),
        source_code=str(row["source_code"]),
        source_family=str(row["source_family"]),
        event_time_utc=str(row["event_time_utc"]),
        available_at_utc=str(row["available_at_utc"]),
        instrument=str(row["instrument"]),
        market_label=str(row["market_label"]),
        settlement_term=str(row["settlement_term"]),
        trade_form=str(row["trade_form"]),
        event_type=str(row["event_type"]),
        side=str(row["side"]),
        price=Decimal(str(row["price_num"])),
        price_unit=str(row["price_unit"]),
        currency=str(row["currency"] or "TOMAN"),
        quantity=None if quantity is None else Decimal(str(quantity)),
        quantity_unit=None if quantity_unit is None else str(quantity_unit),
        parse_confidence=float(row["parse_confidence"]),
        parser_version=parser_version,
        quality_state=quality,
        quality_policy_version=str(row["quality_policy_version"] or "quality-v1"),
        is_conditional=bool(row["is_conditional"]),
        attributes=_sanitize_attributes(row["attributes_json"]),
    )


def payload_hash(observation: MarketObservation) -> str:
    normalized = observation.normalized()
    blob = json.dumps(
        {
            "event_key": normalized.event_key.hex(),
            "source_code": normalized.source_code,
            "source_family": normalized.source_family,
            "event_time_utc": normalized.event_time_utc,
            "available_at_utc": normalized.available_at_utc,
            "instrument": normalized.instrument,
            "market_label": normalized.market_label,
            "settlement_term": normalized.settlement_term,
            "trade_form": normalized.trade_form,
            "event_type": normalized.event_type,
            "side": normalized.side,
            "price": str(normalized.price),
            "price_unit": normalized.price_unit,
            "currency": normalized.currency,
            "quantity": None if normalized.quantity is None else str(normalized.quantity),
            "quantity_unit": normalized.quantity_unit,
            "parse_confidence": normalized.parse_confidence,
            "parser_version": normalized.parser_version,
            "quality_state": normalized.quality_state,
            "quality_policy_version": normalized.quality_policy_version,
            "is_conditional": normalized.is_conditional,
            "attributes_json": normalized.attributes_json,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return blake2b(blob, digest_size=32, person=b"shadow-legacy-v1").hexdigest()


def _column_sql() -> str:
    return ",".join(_OBSERVATION_COLUMNS)


def _open_source(path: Path) -> sqlite3.Connection:
    connection = connect_market_store_read_only(path)
    try:
        verify_market_store_read_only(connection)
    except Exception:
        connection.close()
        raise
    return connection


def _verify_destination(connection: sqlite3.Connection) -> None:
    verify_market_store_read_only(connection)
    row = connection.execute(
        "SELECT schema_version, contract_version FROM market_store_metadata WHERE singleton=1"
    ).fetchone()
    if int(row["schema_version"]) != MARKET_STORE_SCHEMA_VERSION:
        raise BridgeError("destination_schema_unknown")
    if int(row["contract_version"]) != MARKET_STORE_CONTRACT_VERSION:
        raise BridgeError("destination_contract_unknown")


def _validate_ledger_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    text = str(resolved)
    if not text.startswith("/") or "'" in text or "\n" in text or "\\" in text:
        raise BridgeError("ledger_path_invalid")
    return resolved


def _attach_ledger(connection: sqlite3.Connection, ledger: Path) -> None:
    resolved = _validate_ledger_path(ledger)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if not resolved.exists():
        seed = sqlite3.connect(resolved)
        try:
            seed.executescript(_LEDGER_SCHEMA)
            seed.commit()
        finally:
            seed.close()
        os.chmod(resolved, 0o600)
    connection.execute(f"ATTACH DATABASE '{resolved}' AS bridge")
    _ensure_ledger_schema(connection)


def _ensure_ledger_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS bridge.projection_ledger (
            event_key BLOB PRIMARY KEY CHECK(length(event_key) BETWEEN 16 AND 64),
            source_code TEXT NOT NULL,
            payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
            quality_state TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            event_time_utc TEXT NOT NULL,
            available_at_utc TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('APPLIED','RETIRED')),
            projection_revision INTEGER NOT NULL CHECK(projection_revision > 0),
            projected_at_utc TEXT NOT NULL,
            projection_version TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS bridge.projection_ledger_source_status
            ON projection_ledger(source_code, status);
        CREATE TABLE IF NOT EXISTS bridge.projection_checkpoint (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            last_completed_at_utc TEXT NOT NULL,
            last_status TEXT NOT NULL,
            projection_version TEXT NOT NULL,
            last_source_available_at_utc TEXT
        );
        CREATE TABLE IF NOT EXISTS bridge.projection_source_watermark (
            source_code TEXT PRIMARY KEY,
            last_available_at_utc TEXT
        );
        """
    )


def _checkpoint_since(
    dest_conn: sqlite3.Connection,
    selected_sources: Sequence[str],
    cutoff_utc: str,
) -> tuple[str | None, bool]:
    checkpoint = dest_conn.execute(
        """
        SELECT last_source_available_at_utc, last_status
        FROM bridge.projection_checkpoint
        WHERE singleton=1
        """
    ).fetchone()
    if checkpoint is None or str(checkpoint["last_status"]) != "OK":
        return None, True
    stored = {
        str(row["source_code"]): str(row["last_available_at_utc"])
        for row in dest_conn.execute(
            """
            SELECT source_code, last_available_at_utc
            FROM bridge.projection_source_watermark
            WHERE source_code IN ({})
            """.format(",".join("?" for _ in selected_sources)),
            selected_sources,
        )
        if row["last_available_at_utc"]
    }
    if not stored:
        stored = {
            source: stamp
            for source, stamp in source_watermarks(dest_conn, selected_sources).items()
            if stamp
        }
    if not stored:
        return None, True
    since = max(
        cutoff_utc,
        _shift_utc(min(stored.values()), -INCREMENTAL_OVERLAP_SECONDS),
    )
    return since, False


def _shift_utc(stamp: str, seconds: int) -> str:
    return (
        (parse_utc(stamp) + timedelta(seconds=seconds))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _load_rows(
    connection: sqlite3.Connection,
    sources: Sequence[str],
    *,
    table: str,
    since_utc: str | None = None,
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in sources)
    clause = ""
    params: list[object] = list(sources)
    if since_utc is not None:
        clause = " AND (available_at_utc >= ? OR inserted_at_utc >= ?)"
        params.extend((since_utc, since_utc))
    return list(
        connection.execute(
            f"""
            SELECT {_column_sql()}
            FROM {table}
            WHERE source_code IN ({placeholders})
            {clause}
            ORDER BY available_at_utc, event_time_utc, event_key
            """,
            params,
        )
    )


def _load_rows_by_keys(
    connection: sqlite3.Connection,
    *,
    table: str,
    keys: Sequence[bytes],
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    pending = list(keys)
    while pending:
        chunk = pending[:400]
        pending = pending[400:]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            connection.execute(
                f"SELECT {_column_sql()} FROM {table} WHERE event_key IN ({placeholders})",
                chunk,
            )
        )
    return rows


def _root_keys(rows: Sequence[sqlite3.Row]) -> set[bytes]:
    found: set[bytes] = set()
    for row in rows:
        attributes = _sanitize_attributes(row["attributes_json"])
        root = str(attributes.get("root_offer_event_key") or "").strip()
        if root:
            found.add(bytes.fromhex(root))
    return found


def _needed_keys(
    rows: Sequence[sqlite3.Row],
    *,
    cutoff_utc: str,
    prior_keys: set[bytes],
) -> set[bytes]:
    by_key = {bytes(row["event_key"]): row for row in rows}
    needed: set[bytes] = set()
    latest: dict[tuple[str, ...], sqlite3.Row] = {}
    for row in rows:
        key = bytes(row["event_key"])
        if str(row["available_at_utc"]) >= cutoff_utc:
            needed.add(key)
        book = (
            str(row["source_code"]),
            str(row["instrument"]),
            str(row["settlement_term"]),
            str(row["trade_form"]),
            str(row["event_type"]),
            str(row["side"]),
        )
        previous = latest.get(book)
        if previous is None or str(row["available_at_utc"]) >= str(
            previous["available_at_utc"]
        ):
            latest[book] = row
    for row in latest.values():
        needed.add(bytes(row["event_key"]))
    needed.update(key for key in prior_keys if key in by_key)
    pending = True
    while pending:
        pending = False
        for key in tuple(needed):
            row = by_key.get(key)
            if row is None:
                continue
            attributes = _sanitize_attributes(row["attributes_json"])
            root = str(attributes.get("root_offer_event_key") or "").strip()
            if not root:
                continue
            root_key = bytes.fromhex(root)
            if root_key in by_key and root_key not in needed:
                needed.add(root_key)
                pending = True
    return needed


def _destination_row(
    connection: sqlite3.Connection, event_key: bytes
) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT {_column_sql()} FROM market_observations WHERE event_key=?",
        (event_key,),
    ).fetchone()


def _same_observation(existing: sqlite3.Row, incoming: MarketObservation) -> bool:
    try:
        return payload_hash(observation_from_row(existing)) == payload_hash(incoming)
    except (BridgeError, MarketStoreContractError, TypeError, ValueError):
        return False


def _retire_observation(
    connection: sqlite3.Connection,
    event_key: bytes,
    *,
    sources: Sequence[str],
    now: str,
) -> bool:
    placeholders = ",".join("?" for _ in sources)
    cursor = connection.execute(
        f"""
        UPDATE market_observations
        SET quality_state=?, parse_confidence=0, inserted_at_utc=?
        WHERE event_key=? AND source_code IN ({placeholders})
          AND quality_state <> ?
        """,
        (RETIRED_QUALITY, now, event_key, *sources, RETIRED_QUALITY),
    )
    return cursor.rowcount > 0


def unrelated_row_count(
    connection: sqlite3.Connection, sources: Sequence[str]
) -> int:
    placeholders = ",".join("?" for _ in sources)
    return int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM market_observations
            WHERE source_code NOT IN ({placeholders})
            """,
            tuple(sources),
        ).fetchone()[0]
    )


def source_watermarks(
    connection: sqlite3.Connection, sources: Sequence[str]
) -> dict[str, str | None]:
    marks: dict[str, str | None] = {}
    for source in sources:
        row = connection.execute(
            """
            SELECT available_at_utc
            FROM market_observations
            WHERE source_code=?
            ORDER BY available_at_utc DESC
            LIMIT 1
            """,
            (source,),
        ).fetchone()
        marks[source] = None if row is None else str(row["available_at_utc"])
    return marks


def verify_source_read_only(path: Path) -> None:
    connection = _open_source(path)
    try:
        try:
            connection.execute(
                "UPDATE market_store_metadata SET schema_version=schema_version WHERE singleton=1"
            )
        except sqlite3.Error:
            return
        raise BridgeError("shadow_source_writable")
    finally:
        connection.close()


def project_shadow_to_legacy_market(
    *,
    source: Path,
    destination: Path,
    ledger: Path,
    sources: Iterable[str] = PRIVATE_SOURCES,
    cutoff_utc: str = AUTHORIZED_CUTOFF_UTC,
    dry_run: bool = False,
    force_full_reconcile: bool = False,
) -> dict[str, Any]:
    selected_sources = tuple(sorted({str(item).strip().upper() for item in sources}))
    unknown = [item for item in selected_sources if item not in MARKET_BRIDGE_SOURCES]
    if unknown:
        raise BridgeError("source_code_unsupported")
    if not selected_sources:
        raise BridgeError("source_code_required")
    normalize_utc(cutoff_utc, field_name="cutoff_utc")
    source_conn = _open_source(source)
    dest_conn = connect_market_store(destination)
    counts = ProjectionCounts()
    try:
        _verify_destination(dest_conn)
        _attach_ledger(dest_conn, ledger)
        prior_keys = {
            bytes(row["event_key"])
            for row in dest_conn.execute(
                """
                SELECT event_key
                FROM bridge.projection_ledger
                WHERE source_code IN ({})
                """.format(",".join("?" for _ in selected_sources)),
                selected_sources,
            )
        }
        if force_full_reconcile:
            since_utc, full_reconcile = None, True
        else:
            since_utc, full_reconcile = _checkpoint_since(
                dest_conn, selected_sources, cutoff_utc
            )
        hot = _load_rows(
            source_conn,
            selected_sources,
            table="market_observations",
            since_utc=None if full_reconcile else since_utc,
        )
        combined = list(hot)
        seen = {bytes(row["event_key"]) for row in hot}
        wanted = set(_root_keys(hot))
        if full_reconcile:
            wanted.update(prior_keys)
        missing = [key for key in wanted if key not in seen]
        if missing:
            for table in ("market_observations", "market_observations_archive"):
                leftover = [key for key in missing if key not in seen]
                if not leftover:
                    break
                for row in _load_rows_by_keys(
                    source_conn,
                    table=table,
                    keys=leftover,
                ):
                    key = bytes(row["event_key"])
                    if key not in seen:
                        combined.append(row)
                        seen.add(key)
        if dry_run:
            dest_conn.execute("BEGIN")
        else:
            dest_conn.execute("BEGIN IMMEDIATE")
        prior = {
            bytes(row["event_key"]): row
            for row in dest_conn.execute(
                """
                SELECT event_key, payload_hash, status, projection_revision,
                       source_code, available_at_utc
                FROM bridge.projection_ledger
                WHERE source_code IN ({})
                """.format(",".join("?" for _ in selected_sources)),
                selected_sources,
            )
        }
        if full_reconcile:
            needed = _needed_keys(
                combined, cutoff_utc=cutoff_utc, prior_keys=set(prior)
            )
        else:
            needed = {bytes(row["event_key"]) for row in combined}
        before_unrelated = unrelated_row_count(dest_conn, selected_sources)
        now = utc_now()
        source_by_key = {bytes(row["event_key"]): row for row in combined}
        for key in sorted(needed):
            row = source_by_key[key]
            observation = observation_from_row(row)
            digest = payload_hash(observation)
            existing = _destination_row(dest_conn, key)
            previous = prior.get(key)
            counts = ProjectionCounts(
                projected=counts.projected,
                updated=counts.updated,
                unchanged=counts.unchanged,
                removed=counts.removed,
                selected=counts.selected + 1,
                audit_only=counts.audit_only
                + int(observation.quality_state in NON_REALTIME_QUALITY),
            )
            if (
                existing is not None
                and previous is not None
                and str(previous["payload_hash"]) == digest
                and str(previous["status"]) == "APPLIED"
                and _same_observation(existing, observation)
            ):
                counts = ProjectionCounts(
                    projected=counts.projected,
                    updated=counts.updated,
                    unchanged=counts.unchanged + 1,
                    removed=counts.removed,
                    selected=counts.selected,
                    audit_only=counts.audit_only,
                )
                continue
            if not dry_run:
                upsert_observation(dest_conn, observation)
                revision = 1 if previous is None else int(previous["projection_revision"]) + (
                    0 if str(previous["payload_hash"]) == digest else 1
                )
                dest_conn.execute(
                    """
                    INSERT INTO bridge.projection_ledger(
                        event_key, source_code, payload_hash, quality_state,
                        parser_version, event_time_utc, available_at_utc, status,
                        projection_revision, projected_at_utc, projection_version
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_key) DO UPDATE SET
                        source_code=excluded.source_code,
                        payload_hash=excluded.payload_hash,
                        quality_state=excluded.quality_state,
                        parser_version=excluded.parser_version,
                        event_time_utc=excluded.event_time_utc,
                        available_at_utc=excluded.available_at_utc,
                        status=excluded.status,
                        projection_revision=excluded.projection_revision,
                        projected_at_utc=excluded.projected_at_utc,
                        projection_version=excluded.projection_version
                    """,
                    (
                        key,
                        observation.source_code,
                        digest,
                        observation.quality_state,
                        observation.parser_version,
                        normalize_utc(
                            observation.event_time_utc, field_name="event_time_utc"
                        ),
                        normalize_utc(
                            observation.available_at_utc, field_name="available_at_utc"
                        ),
                        "APPLIED",
                        revision,
                        now,
                        BRIDGE_VERSION,
                    ),
                )
            if previous is None and existing is None:
                counts = ProjectionCounts(
                    projected=counts.projected + 1,
                    updated=counts.updated,
                    unchanged=counts.unchanged,
                    removed=counts.removed,
                    selected=counts.selected,
                    audit_only=counts.audit_only,
                )
            else:
                counts = ProjectionCounts(
                    projected=counts.projected,
                    updated=counts.updated + 1,
                    unchanged=counts.unchanged,
                    removed=counts.removed,
                    selected=counts.selected,
                    audit_only=counts.audit_only,
                )
        for key, previous in prior.items():
            if key in needed or str(previous["status"]) == "RETIRED":
                continue
            if not full_reconcile and str(previous["available_at_utc"]) < since_utc:
                continue
            retired = False if dry_run else _retire_observation(
                dest_conn, key, sources=selected_sources, now=now
            )
            if not dry_run:
                dest_conn.execute(
                    """
                    UPDATE bridge.projection_ledger
                    SET status='RETIRED', projected_at_utc=?, projection_version=?
                    WHERE event_key=?
                    """,
                    (now, BRIDGE_VERSION, key),
                )
            if dry_run or retired or str(previous["status"]) == "APPLIED":
                counts = ProjectionCounts(
                    projected=counts.projected,
                    updated=counts.updated,
                    unchanged=counts.unchanged,
                    removed=counts.removed + 1,
                    selected=counts.selected,
                    audit_only=counts.audit_only,
                )
        after_unrelated = unrelated_row_count(dest_conn, selected_sources)
        if after_unrelated != before_unrelated:
            raise BridgeError("unrelated_rows_mutated")
        source_latest = source_watermarks(source_conn, selected_sources)
        dest_latest = source_watermarks(dest_conn, selected_sources)
        if not dry_run:
            dest_conn.execute(
                """
                INSERT INTO bridge.projection_checkpoint(
                    singleton, last_completed_at_utc, last_status,
                    projection_version, last_source_available_at_utc
                ) VALUES (1,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                    last_completed_at_utc=excluded.last_completed_at_utc,
                    last_status=excluded.last_status,
                    projection_version=excluded.projection_version,
                    last_source_available_at_utc=excluded.last_source_available_at_utc
                """,
                (
                    now,
                    "OK",
                    BRIDGE_VERSION,
                    max((item or "" for item in source_latest.values()), default="")
                    or None,
                ),
            )
            for source_code, stamp in source_latest.items():
                if not stamp:
                    continue
                dest_conn.execute(
                    """
                    INSERT INTO bridge.projection_source_watermark(
                        source_code, last_available_at_utc
                    ) VALUES (?,?)
                    ON CONFLICT(source_code) DO UPDATE SET
                        last_available_at_utc=excluded.last_available_at_utc
                    """,
                    (source_code, stamp),
                )
            dest_conn.commit()
        else:
            dest_conn.rollback()
        return {
            "status": "DRY_RUN" if dry_run else "PROJECTED",
            "sources": list(selected_sources),
            "cutoff_utc": cutoff_utc,
            "mode": "full" if full_reconcile else "incremental",
            "since_utc": None if full_reconcile else since_utc,
            "source_latest_available_at_utc": source_latest,
            "destination_latest_available_at_utc": dest_latest,
            "unrelated_rows": after_unrelated,
            **counts.as_dict(),
        }
    except BaseException:
        dest_conn.rollback()
        raise
    finally:
        try:
            dest_conn.execute("DETACH DATABASE bridge")
        except sqlite3.Error:
            pass
        source_conn.close()
        dest_conn.close()


def deactivate_projected_rows(
    *,
    destination: Path,
    ledger: Path,
    sources: Iterable[str] = MARKET_BRIDGE_SOURCES,
) -> dict[str, int]:
    selected_sources = tuple(sorted({str(item).strip().upper() for item in sources}))
    dest_conn = connect_market_store(destination)
    removed = 0
    try:
        _verify_destination(dest_conn)
        dest_conn.execute("BEGIN IMMEDIATE")
        _attach_ledger(dest_conn, ledger)
        now = utc_now()
        rows = list(
            dest_conn.execute(
                """
                SELECT event_key, source_code, status
                FROM bridge.projection_ledger
                WHERE source_code IN ({}) AND status='APPLIED'
                """.format(",".join("?" for _ in selected_sources)),
                selected_sources,
            )
        )
        for row in rows:
            if _retire_observation(
                dest_conn, bytes(row["event_key"]), sources=selected_sources, now=now
            ):
                removed += 1
            dest_conn.execute(
                """
                UPDATE bridge.projection_ledger
                SET status='RETIRED', projected_at_utc=?, projection_version=?
                WHERE event_key=?
                """,
                (now, BRIDGE_VERSION, bytes(row["event_key"])),
            )
        dest_conn.commit()
        return {"deactivated": removed, "ledger_rows": len(rows)}
    except BaseException:
        dest_conn.rollback()
        raise
    finally:
        try:
            dest_conn.execute("DETACH DATABASE bridge")
        except sqlite3.Error:
            pass
        dest_conn.close()


def lag_seconds(
    source_latest: Mapping[str, str | None],
    destination_latest: Mapping[str, str | None],
) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for source, stamp in source_latest.items():
        dest = destination_latest.get(source)
        if not stamp or not dest:
            result[source] = None
            continue
        delta = int((parse_utc(stamp) - parse_utc(dest)).total_seconds())
        result[source] = max(0, delta)
    return result


def write_health(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def empty_health(*, release_sha: str, status: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "schema": HEALTH_SCHEMA,
        "version": BRIDGE_VERSION,
        "release_sha": release_sha,
        "status": status,
        "started_at_utc": None,
        "completed_at_utc": None,
        "source_latest_available_at_utc": {},
        "destination_latest_available_at_utc": {},
        "projected": 0,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
        "lag_seconds": {},
        "last_successful_run_at_utc": None,
        "failure_reason_code": reason,
        "source_quick_check": None,
        "destination_quick_check": {},
    }
