#!/usr/bin/env python3
"""Build an atomic, privacy-minimized group training snapshot.

The source Telegram identifiers are used only while joining the private
staging databases.  The published dataset replaces them with a local
``economic_chain_id`` so evaluation can keep every reply/fill from one offer
chain in the same fold without retaining the source identifier.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.coin_intelligence_private_ingest.runtime_paths import (
        PIPELINE_ROOT as PIPE,
    )
except ModuleNotFoundError:  # Standalone immutable runtime deployment.
    PIPE = Path(__file__).resolve().parent


COMPONENT = PIPE / "offer_field_staging.sqlite3"
RAW = PIPE / "raw_events.sqlite3"
STAGE = PIPE / "text_staging.sqlite3"
TRADES = PIPE / "trade_link_staging.sqlite3"
OUT = PIPE / "group_training_dataset_shadow.sqlite3"
MANIFEST = PIPE / "group_training_dataset_shadow.latest.json"
SNAPSHOT_ROOT = PIPE / "training-snapshots"
VERSION = "group-training-shadow-v2.0-economic-chain"
SNAPSHOT_RETENTION = 12
GROUP_SOURCES = ("account2_group1", "account2_group2")

SCHEMA = """
CREATE TABLE dataset_runs (
 id INTEGER PRIMARY KEY,
 created_at_utc TEXT NOT NULL,
 version TEXT NOT NULL,
 offer_count INTEGER NOT NULL,
 trade_count INTEGER NOT NULL,
 independent_trade_chain_count INTEGER NOT NULL,
 dataset_sha256 TEXT
);
CREATE TABLE offer_training_examples (
 id INTEGER PRIMARY KEY,
 economic_chain_id INTEGER NOT NULL,
 occurred_at_utc TEXT NOT NULL,
 group_number INTEGER NOT NULL CHECK(group_number IN (1,2)),
 offerer_name TEXT,
 offer_text TEXT NOT NULL,
 commodity TEXT NOT NULL,
 price INTEGER NOT NULL CHECK(price > 0),
 quantity INTEGER,
 side TEXT NOT NULL,
 settlement TEXT NOT NULL,
 trade_form TEXT NOT NULL,
 extraction_confidence REAL NOT NULL,
 training_weight REAL NOT NULL,
 dataset_version TEXT NOT NULL
);
CREATE TABLE confirmed_trade_training_examples (
 id INTEGER PRIMARY KEY,
 economic_chain_id INTEGER NOT NULL,
 occurred_at_utc TEXT NOT NULL,
 group_number INTEGER NOT NULL CHECK(group_number IN (1,2)),
 offerer_name TEXT,
 counterparty_name TEXT,
 offer_text TEXT NOT NULL,
 commodity TEXT NOT NULL,
 price INTEGER NOT NULL CHECK(price > 0),
 quantity INTEGER,
 side TEXT NOT NULL,
 settlement TEXT NOT NULL,
 trade_form TEXT NOT NULL,
 confirmation_type TEXT NOT NULL,
 extraction_confidence REAL NOT NULL,
 chain_trade_count INTEGER NOT NULL CHECK(chain_trade_count > 0),
 training_weight REAL NOT NULL,
 dataset_version TEXT NOT NULL
);
CREATE INDEX idx_offer_training_when
 ON offer_training_examples(occurred_at_utc,commodity,settlement,trade_form);
CREATE INDEX idx_offer_training_chain
 ON offer_training_examples(economic_chain_id);
CREATE INDEX idx_trade_training_when
 ON confirmed_trade_training_examples(occurred_at_utc,commodity,settlement,trade_form);
CREATE INDEX idx_trade_training_chain
 ON confirmed_trade_training_examples(economic_chain_id);
"""


def now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro", uri=True, timeout=30
    )
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    return connection


def name_and_time(
    records: dict[tuple[str, str], dict],
    display_names: dict[tuple[str, str], str],
    source: str,
    message: str | None,
) -> tuple[str | None, str | None]:
    if message is None:
        return None, None
    record = records.get((source, str(message)), {})
    name = record.get("sender_name") or display_names.get(
        (source, str(record.get("sender_peer_id") or ""))
    )
    occurred_at = record.get("telegram_datetime") or None
    return (
        str(name).strip() if name else None,
        str(occurred_at) if occurred_at else None,
    )


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _retain_recent_snapshots(root: Path) -> None:
    snapshots = sorted(
        root.glob("group-training-*.sqlite3"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in snapshots[SNAPSHOT_RETENTION:]:
        stale.unlink()


def _source_fingerprint(
    accepted_rows: list[sqlite3.Row],
    eligible_trades: list[tuple[sqlite3.Row, dict]],
    records: dict[tuple[str, str], dict],
    timestamps: dict[tuple[str, str], str],
) -> str:
    value = hashlib.sha256()
    for row in accepted_rows:
        key = (row["source_key"], row["message_id"])
        record = records.get(key) or {}
        payload = [
            row["source_key"],
            row["message_id"],
            row["offer_index"],
            json.loads(row["extracted_json"]),
            timestamps.get(key),
            record.get("sender_name"),
        ]
        value.update(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        value.update(b"\n")
    for row, data in eligible_trades:
        payload = [
            row["source_key"],
            row["offer_message_id"],
            row["request_message_id"],
            data,
        ]
        value.update(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        value.update(b"\n")
    return value.hexdigest()


def build_dataset(
    *,
    component_path: Path = COMPONENT,
    raw_path: Path = RAW,
    stage_path: Path = STAGE,
    trades_path: Path = TRADES,
    output_path: Path = OUT,
    manifest_path: Path = MANIFEST,
    snapshot_root: Path = SNAPSHOT_ROOT,
) -> dict:
    created_at = now()
    component = readonly(component_path)
    raw = readonly(raw_path)
    stage = readonly(stage_path)
    trades = readonly(trades_path)

    records: dict[tuple[str, str], dict] = {}
    placeholders = ",".join("?" for _ in GROUP_SOURCES)
    for row in raw.execute(
        f"""SELECT source_key,message_id,record_json
        FROM source_messages_current
        WHERE source_key IN ({placeholders})""",
        GROUP_SOURCES,
    ):
        records[(row["source_key"], row["message_id"])] = json.loads(
            row["record_json"]
        )

    display_names: dict[tuple[str, str], str] = {}
    for (source, _), record in records.items():
        peer = str(record.get("sender_peer_id") or "")
        name = str(record.get("sender_name") or "").strip()
        if peer and name:
            display_names[(source, peer)] = name

    timestamps = {
        (row["source_key"], row["message_id"]): row["telegram_datetime"]
        for row in stage.execute(
            f"""SELECT source_key,message_id,telegram_datetime
            FROM text_candidates WHERE source_key IN ({placeholders})""",
            GROUP_SOURCES,
        )
    }
    accepted_rows = component.execute(
        """SELECT * FROM offer_component_candidates
        WHERE extraction_status='SHADOW_ACCEPTED'
        ORDER BY source_key,message_id,offer_index"""
    ).fetchall()
    accepted: dict[tuple[str, str], sqlite3.Row] = {}
    for row in accepted_rows:
        accepted.setdefault((row["source_key"], row["message_id"]), row)

    eligible_trades: list[tuple[sqlite3.Row, dict]] = []
    chain_trade_counts: Counter[tuple[str, str]] = Counter()
    for row in trades.execute(
        "SELECT * FROM linked_confirmed_trades ORDER BY source_key,offer_message_id"
    ):
        key = (row["source_key"], str(row["offer_message_id"] or ""))
        if key not in accepted:
            continue
        data = json.loads(row["trade_json"])
        if not bool(data.get("training_eligible", True)):
            continue
        eligible_trades.append((row, data))
        chain_trade_counts[key] += 1

    chain_keys = sorted(accepted)
    chain_ids = {key: index + 1 for index, key in enumerate(chain_keys)}
    source_fingerprint = _source_fingerprint(
        accepted_rows, eligible_trades, records, timestamps
    )
    if manifest_path.exists() and output_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}
        if (
            previous.get("version") == VERSION
            and previous.get("source_fingerprint_sha256")
            == source_fingerprint
        ):
            for connection in (component, raw, stage, trades):
                connection.close()
            result = dict(previous)
            result["status"] = "NO_TRAINING_DATA_CHANGE"
            return result

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    out = sqlite3.connect(temporary)
    try:
        out.executescript(SCHEMA)
        for row in accepted_rows:
            data = json.loads(row["extracted_json"])
            key = (row["source_key"], row["message_id"])
            offerer, raw_when = name_and_time(
                records, display_names, *key
            )
            occurred_at = timestamps.get(key) or raw_when
            if not occurred_at:
                continue
            out.execute(
                """INSERT INTO offer_training_examples(
                  economic_chain_id,occurred_at_utc,group_number,offerer_name,
                  offer_text,commodity,price,quantity,side,settlement,trade_form,
                  extraction_confidence,training_weight,dataset_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chain_ids[key],
                    occurred_at,
                    row["group_number"],
                    offerer,
                    data.get("source_text") or "",
                    data["commodity"],
                    int(data["price"]),
                    data.get("quantity"),
                    data["side"],
                    data["settlement"],
                    data["trade_form"],
                    float(data["confidence"]),
                    1.0,
                    VERSION,
                ),
            )

        for row, data in eligible_trades:
            key = (row["source_key"], str(row["offer_message_id"] or ""))
            offerer, offer_when = name_and_time(
                records, display_names, row["source_key"], row["offer_message_id"]
            )
            counterparty, _ = name_and_time(
                records, display_names, row["source_key"], row["request_message_id"]
            )
            accepted_data = json.loads(accepted[key]["extracted_json"])
            count = chain_trade_counts[key]
            # Multiple partial fills are real evidence, but correlated replies
            # must not contribute N times the weight of independent markets.
            per_trade_weight = 4.0 * math.sqrt(count) / count
            occurred_at = data.get("event_time_utc") or offer_when
            if not occurred_at:
                continue
            out.execute(
                """INSERT INTO confirmed_trade_training_examples(
                  economic_chain_id,occurred_at_utc,group_number,offerer_name,
                  counterparty_name,offer_text,commodity,price,quantity,side,
                  settlement,trade_form,confirmation_type,extraction_confidence,
                  chain_trade_count,training_weight,dataset_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chain_ids[key],
                    occurred_at,
                    accepted_data["group_number"],
                    offerer,
                    counterparty,
                    accepted_data.get("source_text") or "",
                    data["commodity"],
                    int(data["price"]),
                    data.get("quantity"),
                    data["side"],
                    data["settlement"],
                    data["trade_form"],
                    data["confirmation_type"],
                    float(data["confidence"]),
                    count,
                    per_trade_weight,
                    VERSION,
                ),
            )

        offer_count = int(
            out.execute("SELECT count(*) FROM offer_training_examples").fetchone()[0]
        )
        trade_count = int(
            out.execute(
                "SELECT count(*) FROM confirmed_trade_training_examples"
            ).fetchone()[0]
        )
        independent_trade_chains = int(
            out.execute(
                """SELECT count(DISTINCT economic_chain_id)
                FROM confirmed_trade_training_examples"""
            ).fetchone()[0]
        )
        out.execute(
            """INSERT INTO dataset_runs(
              created_at_utc,version,offer_count,trade_count,
              independent_trade_chain_count,dataset_sha256
            ) VALUES(?,?,?,?,?,NULL)""",
            (
                created_at,
                VERSION,
                offer_count,
                trade_count,
                independent_trade_chains,
            ),
        )
        out.commit()
        integrity = str(out.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        out.close()
        component.close()
        raw.close()
        stage.close()
        trades.close()
    if integrity != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"training dataset integrity failed: {integrity}")
    os.chmod(temporary, 0o600)
    temporary.replace(output_path)

    dataset_sha = digest(output_path)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    stamp = created_at.replace("-", "").replace(":", "")
    snapshot = snapshot_root / f"group-training-{stamp}-{dataset_sha[:12]}.sqlite3"
    if not snapshot.exists():
        snapshot_temporary = snapshot.with_suffix(".sqlite3.tmp")
        shutil.copy2(output_path, snapshot_temporary)
        os.chmod(snapshot_temporary, 0o600)
        snapshot_temporary.replace(snapshot)
    _retain_recent_snapshots(snapshot_root)

    result = {
        "status": "SHADOW_TRAINING_SNAPSHOT_READY",
        "version": VERSION,
        "created_at_utc": created_at,
        "offers": offer_count,
        "confirmed_trade_rows": trade_count,
        "independent_trade_chains": independent_trade_chains,
        "dataset_sha256": dataset_sha,
        "source_fingerprint_sha256": source_fingerprint,
        "dataset": str(output_path),
        "immutable_snapshot": str(snapshot),
        "integrity": integrity,
    }
    _atomic_json(manifest_path, result)
    return result


def main() -> None:
    print(json.dumps(build_dataset(), ensure_ascii=False))


if __name__ == "__main__":
    main()
