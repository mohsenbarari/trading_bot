"""Durable, privacy-minimized ledger for Shadow relationship labels."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator, Mapping


LEDGER_SCHEMA_VERSION = "COIN_RELATIONSHIP_LEDGER_V1"
LABEL_SCHEMA = "COIN_INTRINSIC_RELATIONSHIP_DATASET_V1_SHADOW_20260803"
RAW_OR_IDENTITY_KEYS = frozenset(
    {
        "offer_text",
        "message_id",
        "source_message_id",
        "sender",
        "sender_name",
        "counterparty",
        "counterparty_name",
        "phone",
        "channel",
        "channel_name",
    }
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS coin_intrinsic_labels (
    label_key_sha256 TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    realized_at_utc TEXT NOT NULL,
    commodity TEXT NOT NULL,
    settlement TEXT NOT NULL,
    trade_form TEXT NOT NULL,
    melted_anchor_market TEXT NOT NULL,
    melted_anchor_age_seconds REAL NOT NULL,
    intrinsic_project_price REAL NOT NULL,
    actual_project_price REAL NOT NULL,
    bubble_ratio REAL NOT NULL,
    features_json TEXT NOT NULL,
    first_ingested_at_utc TEXT NOT NULL,
    last_ingested_at_utc TEXT NOT NULL,
    CHECK(intrinsic_project_price > 0),
    CHECK(actual_project_price > 0)
);
CREATE INDEX IF NOT EXISTS idx_coin_intrinsic_labels_realized
    ON coin_intrinsic_labels(realized_at_utc, commodity, settlement, trade_form);
"""


def _utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("relationship_ledger_timezone_required")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_positive(value: object, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"relationship_ledger_{field}_invalid") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"relationship_ledger_{field}_invalid")
    return number


def _finite(value: object, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"relationship_ledger_{field}_invalid") from exc
    if not math.isfinite(number):
        raise ValueError(f"relationship_ledger_{field}_invalid")
    return number


def _compact_json(value: Mapping) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_label(item: Mapping[str, object]) -> dict[str, object]:
    """Validate and reduce a generated label before it enters durable storage."""

    forbidden = RAW_OR_IDENTITY_KEYS.intersection(item)
    if forbidden:
        raise ValueError("relationship_ledger_raw_or_identity_field_forbidden")
    if item.get("schema_version") != LABEL_SCHEMA:
        raise ValueError("relationship_ledger_label_schema_invalid")
    available = _utc(item.get("available_at_utc"))
    realized = _utc(item.get("realized_at_utc"))
    if realized <= available:
        raise ValueError("relationship_ledger_label_not_strictly_future")
    commodity = str(item.get("commodity") or "").strip()
    settlement = str(item.get("settlement") or "").strip().upper()
    trade_form = str(item.get("trade_form") or "").strip().upper()
    anchor = str(item.get("melted_anchor_market") or "").strip()
    if not commodity or not settlement or not trade_form or not anchor:
        raise ValueError("relationship_ledger_market_dimension_invalid")
    feature_input = item.get("features")
    if not isinstance(feature_input, Mapping) or not feature_input:
        raise ValueError("relationship_ledger_features_invalid")
    features = {
        str(name): _finite(value, field="feature")
        for name, value in feature_input.items()
    }
    return {
        "available_at_utc": _iso(available),
        "realized_at_utc": _iso(realized),
        "commodity": commodity,
        "settlement": settlement,
        "trade_form": trade_form,
        "melted_anchor_market": anchor,
        "melted_anchor_age_seconds": _finite(
            item.get("melted_anchor_age_seconds"), field="anchor_age"
        ),
        "intrinsic_project_price": _finite_positive(
            item.get("intrinsic_project_price"), field="intrinsic_price"
        ),
        "actual_project_price": _finite_positive(
            item.get("actual_project_price"), field="actual_price"
        ),
        "bubble_ratio": _finite(item.get("bubble_ratio"), field="bubble"),
        "features": features,
    }


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_compact_json(value).encode("utf-8")).hexdigest()


def _label_key(label: Mapping[str, object]) -> str:
    """Stable economic key; no source identifier is retained in the ledger."""

    identity = {
        key: label[key]
        for key in (
            "realized_at_utc",
            "commodity",
            "settlement",
            "trade_form",
            "melted_anchor_market",
        )
    }
    return _digest(identity)


def open_ledger(path: Path) -> sqlite3.Connection:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT OR REPLACE INTO ledger_meta(key,value) VALUES('schema_version',?)",
        (LEDGER_SCHEMA_VERSION,),
    )
    connection.commit()
    return connection


def append_labels(
    ledger_path: Path,
    labels: Iterable[Mapping[str, object]],
    *,
    ingested_at_utc: datetime | None = None,
    retention_days: int | None = 180,
) -> dict[str, int | str]:
    """Upsert current numeric labels and compact only explicitly aged rows."""

    if retention_days is not None and retention_days <= 0:
        raise ValueError("relationship_ledger_retention_days_invalid")
    now = _utc(ingested_at_utc or datetime.now(timezone.utc))
    now_text = _iso(now)
    inserted = updated = unchanged = rejected = 0
    connection = open_ledger(ledger_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for item in labels:
            try:
                label = normalize_label(item)
            except ValueError:
                rejected += 1
                continue
            content = dict(label)
            content["features"] = dict(sorted(dict(label["features"]).items()))
            key = _label_key(label)
            content_digest = _digest(content)
            previous = connection.execute(
                "SELECT content_sha256 FROM coin_intrinsic_labels WHERE label_key_sha256=?",
                (key,),
            ).fetchone()
            if previous is not None and previous[0] == content_digest:
                unchanged += 1
                continue
            if previous is None:
                inserted += 1
                first_ingested = now_text
            else:
                updated += 1
                first_ingested = connection.execute(
                    "SELECT first_ingested_at_utc FROM coin_intrinsic_labels WHERE label_key_sha256=?",
                    (key,),
                ).fetchone()[0]
            connection.execute(
                """INSERT INTO coin_intrinsic_labels(
                  label_key_sha256,content_sha256,available_at_utc,realized_at_utc,
                  commodity,settlement,trade_form,melted_anchor_market,
                  melted_anchor_age_seconds,intrinsic_project_price,
                  actual_project_price,bubble_ratio,features_json,
                  first_ingested_at_utc,last_ingested_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(label_key_sha256) DO UPDATE SET
                  content_sha256=excluded.content_sha256,
                  available_at_utc=excluded.available_at_utc,
                  intrinsic_project_price=excluded.intrinsic_project_price,
                  bubble_ratio=excluded.bubble_ratio,
                  melted_anchor_age_seconds=excluded.melted_anchor_age_seconds,
                  features_json=excluded.features_json,
                  last_ingested_at_utc=excluded.last_ingested_at_utc""",
                (
                    key, content_digest, label["available_at_utc"], label["realized_at_utc"],
                    label["commodity"], label["settlement"], label["trade_form"],
                    label["melted_anchor_market"], label["melted_anchor_age_seconds"],
                    label["intrinsic_project_price"], label["actual_project_price"],
                    label["bubble_ratio"], _compact_json(content["features"]),
                    first_ingested, now_text,
                ),
            )
        deleted = 0
        if retention_days is not None:
            cutoff = _iso(now - timedelta(days=retention_days))
            deleted = connection.execute(
                "DELETE FROM coin_intrinsic_labels WHERE realized_at_utc < ?", (cutoff,)
            ).rowcount
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": rejected,
        "retention_deleted": deleted,
        "ingested_at_utc": now_text,
    }


def iter_labels(ledger_path: Path) -> Iterator[dict[str, object]]:
    connection = sqlite3.connect(f"file:{ledger_path.expanduser().resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for row in connection.execute(
            """SELECT available_at_utc,realized_at_utc,commodity,settlement,trade_form,
                      melted_anchor_market,melted_anchor_age_seconds,
                      intrinsic_project_price,actual_project_price,bubble_ratio,features_json
               FROM coin_intrinsic_labels ORDER BY available_at_utc ASC"""
        ):
            yield {
                "schema_version": LABEL_SCHEMA,
                "available_at_utc": row["available_at_utc"],
                "realized_at_utc": row["realized_at_utc"],
                "commodity": row["commodity"],
                "settlement": row["settlement"],
                "trade_form": row["trade_form"],
                "melted_anchor_market": row["melted_anchor_market"],
                "melted_anchor_age_seconds": row["melted_anchor_age_seconds"],
                "intrinsic_project_price": row["intrinsic_project_price"],
                "actual_project_price": row["actual_project_price"],
                "bubble_ratio": row["bubble_ratio"],
                "features": json.loads(row["features_json"]),
            }
    finally:
        connection.close()
