#!/usr/bin/env python3
"""Restore value-free adapter lineage from the canonical Market archive.

Older receiver deliveries may have had their raw envelopes compacted before
the adapter began persisting ``quality_state`` and ``envelope_hash``.  The
canonical PostgreSQL outbox retains those exact immutable values.  This tool
exports only value-free identity/lineage fields on the web host and applies
them on the bot host after matching both durable receiver and adapter
deliveries.  It never changes a market observation or model input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.private_pipeline_contracts import (
    content_hash,
    load_source_registry,
)


SCHEMA = "compacted_market_fact_lineage/1.0"
RECEIPT_SCHEMA = "compacted_market_fact_lineage_receipt/1.0"
CUTOFF_UTC = "2026-08-25T09:33:00Z"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
QUALITY = frozenset({"ELIGIBLE", "REVIEW", "REJECTED", "AUDIT_ONLY"})
STATUSES = frozenset({"APPLIED", "REJECTED", "AUDIT_ONLY"})
TARGET_SOURCES = frozenset(
    {
        "BINANCE_PAXG_PUBLIC_API",
        "GROUP_1",
        "GROUP_2",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
        "PRIVATE_GOLD_CHANNEL",
        "PRIVATE_GOLD_PAPER_MINUTE",
        "USD_HERAT",
        "WALLEX_PUBLIC_API",
        "XAUUSD",
    }
)
FIELD_COUNT = 13


class LineageHydrationError(RuntimeError):
    pass


def _fail(reason: str) -> None:
    raise LineageHydrationError(reason)


def _canonical(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("lineage_timestamp_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail("lineage_timestamp_invalid")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path: Path, payload: bytes, *, exclusive: bool = True) -> None:
    if not path.is_absolute() or path.is_symlink():
        _fail("output_path_invalid")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _row(fields: Sequence[str]) -> tuple[str, ...]:
    if len(fields) != FIELD_COUNT:
        _fail("lineage_field_count_invalid")
    (
        fact_id,
        source,
        stream,
        source_sequence,
        revision,
        delivery_sequence,
        event_key,
        payload_hash,
        quality,
        envelope_hash,
        occurred,
        available,
        persisted,
    ) = fields
    registry = load_source_registry().by_code()
    definition = registry.get(source)
    if (
        not HEX64.fullmatch(fact_id)
        or definition is None
        or definition.fact_stream_id != stream
        or not HEX64.fullmatch(event_key)
        or not HEX64.fullmatch(payload_hash)
        or quality not in QUALITY
        or not HEX64.fullmatch(envelope_hash)
    ):
        _fail("lineage_identity_invalid")
    try:
        if min(int(source_sequence), int(revision), int(delivery_sequence)) < 1:
            _fail("lineage_sequence_invalid")
    except ValueError:
        _fail("lineage_sequence_invalid")
    return (
        fact_id,
        source,
        stream,
        source_sequence,
        revision,
        delivery_sequence,
        event_key,
        payload_hash,
        quality,
        envelope_hash,
        _utc(occurred),
        _utc(available),
        _utc(persisted),
    )


def _rows(path: Path) -> Iterable[tuple[str, ...]]:
    if path.is_symlink() or not path.is_file():
        _fail("lineage_artifact_unavailable")
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line in stream:
            if not line.endswith("\n"):
                _fail("lineage_artifact_truncated")
            yield _row(line[:-1].split("\t"))


def export_lineage(args: argparse.Namespace) -> dict[str, object]:
    if not HEX40.fullmatch(args.release_sha):
        _fail("release_sha_invalid")
    if not SAFE_CONTAINER.fullmatch(args.postgres_container):
        _fail("postgres_container_invalid")
    if not SAFE_NAME.fullmatch(args.postgres_user) or not SAFE_NAME.fullmatch(
        args.postgres_database
    ):
        _fail("postgres_identity_invalid")
    sources = sorted(TARGET_SOURCES)
    quoted_sources = ",".join("'" + item.replace("'", "''") + "'" for item in sources)
    sql = f"""
      SELECT encode(f.fact_id,'hex'),f.source_code,o.envelope->>'stream_id',
             o.envelope->>'source_sequence',r.fact_revision,o.delivery_sequence,
             o.envelope->>'event_key',encode(r.payload_hash,'hex'),r.quality_state,
             encode(o.envelope_hash,'hex'),o.envelope->>'occurred_at_utc',
             o.envelope->>'available_at_utc',o.envelope->>'persisted_at_utc'
      FROM market_data.market_facts f
      JOIN market_data.market_fact_revisions r ON r.fact_id=f.fact_id
      JOIN market_data.market_fact_outbox o
        ON o.fact_id=r.fact_id AND o.fact_revision=r.fact_revision
      WHERE f.source_code IN ({quoted_sources})
        AND (f.occurred_at_utc>=TIMESTAMPTZ '{CUTOFF_UTC}'
             OR f.available_at_utc>=TIMESTAMPTZ '{CUTOFF_UTC}')
        AND o.acknowledged_at_utc IS NOT NULL
      ORDER BY f.source_code,f.source_sequence,r.fact_revision;
    """
    output = Path(args.output)
    if output.exists():
        _fail("output_exists")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            completed = subprocess.run(
                [
                    "docker",
                    "exec",
                    args.postgres_container,
                    "psql",
                    "-XAt",
                    "-F",
                    "\t",
                    "-U",
                    args.postgres_user,
                    "-d",
                    args.postgres_database,
                    "-c",
                    sql,
                ],
                stdout=stream,
                stderr=subprocess.PIPE,
                timeout=900,
                check=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        if completed.returncode != 0:
            _fail("postgres_lineage_export_failed")
        count = 0
        source_counts: Counter[str] = Counter()
        for fields in _rows(temporary):
            count += 1
            source_counts[fields[1]] += 1
        if count < 1:
            _fail("postgres_lineage_export_empty")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    artifact_sha = _digest_path(output)
    receipt = {
        "schema": SCHEMA,
        "status": "PASS",
        "release_sha": args.release_sha,
        "cutoff_utc": CUTOFF_UTC,
        "artifact_sha256": artifact_sha,
        "row_count": count,
        "source_counts": dict(sorted(source_counts.items())),
        "value_free": True,
        "secrets_disclosed": False,
    }
    _write(Path(args.receipt), _canonical(receipt))
    return receipt


def _read_receipt(path: Path, expected_sha: str, artifact: Path) -> Mapping[str, object]:
    if not HEX64.fullmatch(expected_sha) or _digest_path(path) != expected_sha:
        _fail("lineage_receipt_digest_mismatch")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("lineage_receipt_invalid")
    if (
        not isinstance(value, Mapping)
        or value.get("schema") != SCHEMA
        or value.get("status") != "PASS"
        or value.get("cutoff_utc") != CUTOFF_UTC
        or value.get("artifact_sha256") != _digest_path(artifact)
        or value.get("value_free") is not True
        or value.get("secrets_disclosed") is not False
    ):
        _fail("lineage_receipt_invalid")
    return value


def _database(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if path.is_symlink() or not path.is_file():
        _fail("lineage_database_unavailable")
    connection = sqlite3.connect(
        f"file:{path}?mode={'ro' if read_only else 'rw'}", uri=True, timeout=60
    )
    connection.row_factory = sqlite3.Row
    return connection


def _backup(source: sqlite3.Connection, path: Path) -> str:
    if path.exists() or path.is_symlink():
        _fail("backup_output_exists")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = sqlite3.connect(path)
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            _fail("backup_integrity_failed")
    finally:
        target.close()
    os.chmod(path, 0o600)
    return _digest_path(path)


def apply_lineage(args: argparse.Namespace) -> dict[str, object]:
    artifact = Path(args.artifact)
    source_receipt = _read_receipt(
        Path(args.source_receipt), args.expected_source_receipt_sha256, artifact
    )
    if source_receipt.get("release_sha") != args.release_sha:
        _fail("lineage_release_mismatch")
    receiver = _database(Path(args.receiver_db), read_only=True)
    store = _database(Path(args.market_store_db))
    store.execute("PRAGMA busy_timeout=60000")
    backup_path = Path(args.backup)
    backup_sha = _backup(store, backup_path)
    counts = Counter()
    migration = "compacted-lineage:" + str(source_receipt["artifact_sha256"])
    try:
        store.execute("BEGIN IMMEDIATE")
        store.execute(
            "CREATE TABLE IF NOT EXISTS private_fact_adapter_migrations("
            "migration_code TEXT PRIMARY KEY,applied_at_utc TEXT NOT NULL)"
        )
        if store.execute(
            "SELECT 1 FROM private_fact_adapter_migrations WHERE migration_code=?",
            (migration,),
        ).fetchone() is not None:
            _fail("lineage_migration_already_applied")
        for fields in _rows(artifact):
            (
                fact_id,
                _source,
                stream,
                source_sequence,
                revision,
                delivery_sequence,
                event_key,
                payload_hash,
                quality,
                envelope_hash,
                occurred,
                available,
                persisted,
            ) = fields
            delivery = receiver.execute(
                "SELECT fact_id,fact_revision,payload_hash,payload_json,"
                "payload_compacted_at_utc,received_at_utc FROM fact_deliveries "
                "WHERE stream_id=? AND delivery_sequence=?",
                (stream, int(delivery_sequence)),
            ).fetchone()
            adapted = store.execute(
                "SELECT fact_id,fact_revision,payload_hash,status,applied_at_utc "
                "FROM private_fact_adapter_deliveries "
                "WHERE stream_id=? AND delivery_sequence=?",
                (stream, int(delivery_sequence)),
            ).fetchone()
            if (
                delivery is None
                or adapted is None
                or str(delivery["fact_id"]) != fact_id
                or int(delivery["fact_revision"]) != int(revision)
                or str(delivery["payload_hash"]) != payload_hash
                or str(adapted["fact_id"]) != fact_id
                or int(adapted["fact_revision"]) != int(revision)
                or str(adapted["payload_hash"]) != payload_hash
                or str(adapted["status"]) not in STATUSES
            ):
                _fail("lineage_delivery_mismatch")
            payload_text = str(delivery["payload_json"] or "")
            if payload_text:
                try:
                    envelope = json.loads(payload_text)
                except json.JSONDecodeError:
                    _fail("lineage_receiver_payload_invalid")
                if (
                    not isinstance(envelope, Mapping)
                    or envelope.get("fact_id") != fact_id
                    or int(envelope.get("fact_revision") or 0) != int(revision)
                    or envelope.get("payload_hash") != payload_hash
                    or envelope.get("quality_state") != quality
                    or content_hash(envelope) != envelope_hash
                ):
                    _fail("lineage_receiver_payload_mismatch")
                counts["retained_payload_validated"] += 1
            elif delivery["payload_compacted_at_utc"] is not None:
                counts["compacted_payload_restored"] += 1
            else:
                _fail("lineage_receiver_payload_missing")
            projection = store.execute(
                "SELECT * FROM private_fact_adapter_projections WHERE fact_id=?",
                (fact_id,),
            ).fetchone()
            if projection is None:
                _fail("lineage_projection_missing")
            if (
                str(projection["stream_id"]) != stream
                or int(projection["source_sequence"]) != int(source_sequence)
                or bytes(projection["event_key"]).hex() != event_key
            ):
                _fail("lineage_projection_identity_mismatch")
            if int(projection["fact_revision"]) == int(revision):
                current_quality = str(projection["quality_state"] or "")
                current_hash = str(projection["envelope_hash"] or "")
                if current_quality or current_hash:
                    if current_quality != quality or current_hash != envelope_hash:
                        _fail("lineage_projection_semantics_conflict")
                else:
                    store.execute(
                        "UPDATE private_fact_adapter_projections "
                        "SET quality_state=?,envelope_hash=? WHERE fact_id=?",
                        (quality, envelope_hash, fact_id),
                    )
                    counts["projections_updated"] += 1
            existing = store.execute(
                "SELECT * FROM private_fact_adapter_projection_revisions "
                "WHERE fact_id=? AND fact_revision=?",
                (fact_id, int(revision)),
            ).fetchone()
            expected = (
                stream,
                int(source_sequence),
                int(delivery_sequence),
                event_key,
                payload_hash,
                quality,
                envelope_hash,
                str(adapted["status"]),
            )
            if existing is not None:
                actual = (
                    str(existing["stream_id"]),
                    int(existing["source_sequence"]),
                    int(existing["delivery_sequence"]),
                    bytes(existing["event_key"]).hex(),
                    str(existing["payload_hash"]),
                    str(existing["quality_state"]),
                    str(existing["envelope_hash"]),
                    str(existing["status"]),
                )
                if actual != expected:
                    _fail("lineage_revision_conflict")
                counts["revisions_already_present"] += 1
            else:
                store.execute(
                    "INSERT INTO private_fact_adapter_projection_revisions("
                    "fact_id,fact_revision,stream_id,source_sequence,"
                    "delivery_sequence,event_key,payload_hash,quality_state,"
                    "envelope_hash,status,occurred_at_utc,available_at_utc,"
                    "parsed_at_utc,transferred_at_utc,adapted_at_utc) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        fact_id,
                        int(revision),
                        stream,
                        int(source_sequence),
                        int(delivery_sequence),
                        bytes.fromhex(event_key),
                        payload_hash,
                        quality,
                        envelope_hash,
                        str(adapted["status"]),
                        occurred,
                        available,
                        persisted,
                        str(delivery["received_at_utc"]),
                        str(adapted["applied_at_utc"]),
                    ),
                )
                counts["revisions_inserted"] += 1
            counts["rows_verified"] += 1
        store.execute(
            "INSERT INTO private_fact_adapter_migrations VALUES(?,?)",
            (migration, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
        )
        store.commit()
        if store.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            _fail("market_store_integrity_failed")
    except BaseException:
        store.rollback()
        raise
    finally:
        receiver.close()
        store.close()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "release_sha": args.release_sha,
        "cutoff_utc": CUTOFF_UTC,
        "source_receipt_sha256": args.expected_source_receipt_sha256,
        "artifact_sha256": source_receipt["artifact_sha256"],
        "backup_sha256": backup_sha,
        "counts": dict(sorted(counts.items())),
        "observations_changed": False,
        "model_inputs_changed": False,
        "state_deleted": False,
        "secrets_disclosed": False,
    }
    _write(Path(args.receipt), _canonical(receipt))
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--release-sha", required=True)
    export.add_argument("--postgres-container", required=True)
    export.add_argument("--postgres-user", required=True)
    export.add_argument("--postgres-database", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--receipt", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--release-sha", required=True)
    apply.add_argument("--artifact", required=True)
    apply.add_argument("--source-receipt", required=True)
    apply.add_argument("--expected-source-receipt-sha256", required=True)
    apply.add_argument("--receiver-db", required=True)
    apply.add_argument("--market-store-db", required=True)
    apply.add_argument("--backup", required=True)
    apply.add_argument("--receipt", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        receipt = export_lineage(args) if args.command == "export" else apply_lineage(args)
    except (LineageHydrationError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
