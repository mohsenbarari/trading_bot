#!/usr/bin/env python3
"""Build routing-hold convergence evidence from three independent snapshots.

This is deliberately a *builder*, not a form that accepts a green checkbox.
Each site captures its own redacted read-only snapshot; this program joins the
six directed event streams, canonical business parity and the WebApp blob CAS
plane.  Any missing, stale, truncated or inconsistent observation fails
closed before a routing-hold artifact can be written.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_bytes, write_secure_atomic_bytes
from core.sync_parity import business_snapshot_fingerprint, compare_parity_snapshots


SITES = ("bot_fi", "webapp_fi", "webapp_ir")
WEBAPP_SITES = ("webapp_fi", "webapp_ir")
PARITY_COMPARISONS = (
    ("bot-authority", "bot_fi", "webapp_fi"),
    ("bot-authority", "bot_fi", "webapp_ir"),
    ("webapp-authority", "webapp_fi", "bot_fi"),
    ("webapp-authority", "webapp_fi", "webapp_ir"),
)
# Blobs are a WebApp-only content-addressed plane.  Bot-FI has neither a blob
# worker nor a local blob CAS, so treating it as a Blob replica would turn an
# empty initial seed into a false general claim.
BLOB_SCOPES = (
    ("webapp-authority", "webapp_fi", "webapp_ir"),
    ("webapp-authority", "webapp_ir", "webapp_fi"),
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SNAPSHOT_SCHEMA = "three-site-staging-convergence-site-snapshot-v1"
# A freshly generated artifact is not evidence if it was assembled from old
# site observations.  The routing gate has its own (larger) artifact-freshness
# window; this narrower window also bounds capture skew between sites.
MAX_SNAPSHOT_AGE = timedelta(minutes=5)
MAX_SNAPSHOT_FUTURE_SKEW = timedelta(seconds=30)
MAX_SNAPSHOT_CAPTURE_SKEW = timedelta(minutes=2)


class ConvergenceEvidenceError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ConvergenceEvidenceError("snapshot has duplicate JSON fields")
        value[key] = item
    return value


def _read_snapshot(path: Path) -> dict[str, Any]:
    try:
        raw = read_secure_bytes(path, label="three-site convergence snapshot", max_size=64 * 1024 * 1024)
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except Exception as exc:
        raise ConvergenceEvidenceError("convergence snapshot is unreadable") from exc
    if not isinstance(value, dict):
        raise ConvergenceEvidenceError("convergence snapshot must be an object")
    return value


def _mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for item in values:
        site, separator, raw_path = item.partition("=")
        if not separator or site not in SITES or site in result or not raw_path:
            raise ConvergenceEvidenceError("site snapshot mapping is invalid")
        result[site] = Path(raw_path)
    if set(result) != set(SITES):
        raise ConvergenceEvidenceError("all three site snapshots are required")
    return result


def _hash(value: Any, *, label: str, allow_zero: bool = False) -> str:
    raw = str(value or "")
    if SHA256.fullmatch(raw) is None or (not allow_zero and raw == "0" * 64):
        raise ConvergenceEvidenceError(f"{label} hash is invalid")
    return raw


def _nonnegative(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ConvergenceEvidenceError(f"{label} must be a non-negative integer")
    return value


def _validate_snapshot(
    snapshot: Mapping[str, Any],
    *,
    site: str,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
) -> dict[str, Any]:
    fields = {
        "schema", "campaign_id", "release_sha", "plan_sha256", "site", "observed_at",
        "producer_epoch", "source_streams", "destination_streams",
        "unresolved_conflict_count", "database_snapshot", "blob_records",
    }
    if (
        set(snapshot) != fields
        or snapshot.get("schema") != SNAPSHOT_SCHEMA
        or snapshot.get("campaign_id") != campaign_id
        or snapshot.get("release_sha") != release_sha
        or snapshot.get("plan_sha256") != plan_sha256
        or snapshot.get("site") != site
        or type(snapshot.get("producer_epoch")) is not int
        or int(snapshot["producer_epoch"]) < 1
        or not isinstance(snapshot.get("source_streams"), list)
        or not isinstance(snapshot.get("destination_streams"), list)
        or not isinstance(snapshot.get("database_snapshot"), dict)
        or not isinstance(snapshot.get("blob_records"), list)
    ):
        raise ConvergenceEvidenceError(f"{site} snapshot identity or shape is invalid")
    try:
        captured = datetime.fromisoformat(str(snapshot["observed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConvergenceEvidenceError(f"{site} snapshot timestamp is invalid") from exc
    if captured.tzinfo is None:
        raise ConvergenceEvidenceError(f"{site} snapshot timestamp has no timezone")
    captured = captured.astimezone(timezone.utc)
    _nonnegative(snapshot["unresolved_conflict_count"], label=f"{site} conflict count")

    expected_destinations = set(SITES) - {site}
    source_streams: dict[str, dict[str, Any]] = {}
    for row in snapshot["source_streams"]:
        if not isinstance(row, dict) or set(row) != {
            "destination_site", "source_sequence", "source_transaction_hash"
        }:
            raise ConvergenceEvidenceError(f"{site} source stream is invalid")
        destination = str(row.get("destination_site") or "")
        sequence = _nonnegative(row.get("source_sequence"), label=f"{site} source sequence")
        transaction_hash = _hash(
            row.get("source_transaction_hash"),
            label=f"{site} source transaction",
            allow_zero=True,
        )
        if (
            destination not in expected_destinations
            or destination in source_streams
            or (sequence == 0) != (transaction_hash == "0" * 64)
        ):
            raise ConvergenceEvidenceError(f"{site} source stream does not prove a canonical tail")
        source_streams[destination] = {
            "source_sequence": sequence,
            "source_transaction_hash": transaction_hash,
        }
    if set(source_streams) != expected_destinations:
        raise ConvergenceEvidenceError(f"{site} source streams are incomplete")

    destination_streams: dict[tuple[str, int], dict[str, Any]] = {}
    for row in snapshot["destination_streams"]:
        if not isinstance(row, dict) or set(row) != {
            "origin_site", "producer_epoch", "received_sequence", "applied_sequence",
            "received_transaction_hash", "applied_transaction_hash",
        }:
            raise ConvergenceEvidenceError(f"{site} destination stream is invalid")
        origin = str(row.get("origin_site") or "")
        epoch = row.get("producer_epoch")
        received = _nonnegative(row.get("received_sequence"), label=f"{site} received sequence")
        applied = _nonnegative(row.get("applied_sequence"), label=f"{site} applied sequence")
        received_hash = _hash(row.get("received_transaction_hash"), label=f"{site} received transaction", allow_zero=True)
        applied_hash = _hash(row.get("applied_transaction_hash"), label=f"{site} applied transaction", allow_zero=True)
        key = (origin, epoch)
        if (
            origin not in expected_destinations
            or type(epoch) is not int
            or epoch < 1
            or key in destination_streams
            or applied > received
            or (received == 0) != (received_hash == "0" * 64)
            or (applied == 0) != (applied_hash == "0" * 64)
        ):
            raise ConvergenceEvidenceError(f"{site} destination stream is inconsistent")
        destination_streams[key] = {
            "received_sequence": received,
            "applied_sequence": applied,
            "received_transaction_hash": received_hash,
            "applied_transaction_hash": applied_hash,
        }

    parity = snapshot["database_snapshot"]
    if parity.get("mode") != "deep" or not isinstance(parity.get("tables"), dict):
        raise ConvergenceEvidenceError(f"{site} database snapshot is not deep")
    try:
        business_snapshot_fingerprint(parity)
    except ValueError as exc:
        raise ConvergenceEvidenceError(f"{site} database snapshot is incomplete") from exc

    blob_records: dict[str, dict[str, Any]] = {}
    for row in snapshot["blob_records"]:
        fields = {
            "content_hash", "size_bytes", "object_version_id", "object_ciphertext_hash",
            "object_ciphertext_size", "encryption_key_id", "encryption_algorithm",
            "local_content_hash", "local_size_bytes",
        }
        if not isinstance(row, dict) or set(row) != fields:
            raise ConvergenceEvidenceError(f"{site} blob record is invalid")
        content_hash = _hash(row.get("content_hash"), label=f"{site} blob content")
        _hash(row.get("object_ciphertext_hash"), label=f"{site} blob ciphertext")
        if (
            content_hash in blob_records
            or _nonnegative(row.get("size_bytes"), label=f"{site} blob size")
            != _nonnegative(row.get("local_size_bytes"), label=f"{site} local blob size")
            or _hash(row.get("local_content_hash"), label=f"{site} local blob content") != content_hash
            or _nonnegative(row.get("object_ciphertext_size"), label=f"{site} blob ciphertext size") < 1
            or not str(row.get("object_version_id") or "").strip()
            or not str(row.get("encryption_key_id") or "").strip()
            or not str(row.get("encryption_algorithm") or "").strip()
        ):
            raise ConvergenceEvidenceError(f"{site} blob record failed local read-back verification")
        blob_records[content_hash] = dict(row)
    if site == "bot_fi" and blob_records:
        raise ConvergenceEvidenceError("Bot-FI must not claim a WebApp blob replica")
    return {
        "captured_at": captured,
        "producer_epoch": int(snapshot["producer_epoch"]),
        "source_streams": source_streams,
        "destination_streams": destination_streams,
        "unresolved_conflict_count": int(snapshot["unresolved_conflict_count"]),
        "database_snapshot": parity,
        "blob_records": blob_records,
    }


def _snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    return _sha(snapshot.get("tables"))


def _table_set(snapshot: Mapping[str, Any]) -> str:
    tables = snapshot.get("tables")
    if not isinstance(tables, Mapping):
        raise ConvergenceEvidenceError("parity table set is invalid")
    return _sha(sorted(str(name) for name in tables))


def _event_artifact(
    snapshots: dict[str, dict[str, Any]],
    *,
    common: dict[str, str],
) -> dict[str, Any]:
    streams: list[dict[str, Any]] = []
    conflicts = sum(item["unresolved_conflict_count"] for item in snapshots.values())
    if conflicts:
        raise ConvergenceEvidenceError("unresolved DR conflicts prevent convergence")
    for origin in SITES:
        source = snapshots[origin]
        epoch = source["producer_epoch"]
        for destination in SITES:
            if origin == destination:
                continue
            source_tail = source["source_streams"][destination]
            target = snapshots[destination]["destination_streams"].get((origin, epoch))
            # A zero source stream has no row at the target yet.  A nonzero
            # stream must have a target checkpoint in the exact source epoch.
            if target is None:
                target = {
                    "received_sequence": 0,
                    "applied_sequence": 0,
                    "received_transaction_hash": "0" * 64,
                    "applied_transaction_hash": "0" * 64,
                }
            sequences = [
                source_tail["source_sequence"], target["received_sequence"], target["applied_sequence"],
            ]
            hashes = [
                source_tail["source_transaction_hash"], target["received_transaction_hash"], target["applied_transaction_hash"],
            ]
            if len(set(sequences)) != 1 or len(set(hashes)) != 1:
                raise ConvergenceEvidenceError(
                    f"event stream {origin}->{destination} is not exactly applied"
                )
            streams.append(
                {
                    "origin_site": origin,
                    "destination_site": destination,
                    "producer_epoch": epoch,
                    "source_sequence": sequences[0],
                    "received_sequence": sequences[1],
                    "applied_sequence": sequences[2],
                    "source_transaction_hash": hashes[0],
                    "received_transaction_hash": hashes[1],
                    "applied_transaction_hash": hashes[2],
                }
            )
    return {
        "schema": "three-site-staging-event-convergence-v1",
        **common,
        "status": "converged",
        "conflict_count": 0,
        "streams": streams,
    }


def _database_artifact(
    snapshots: dict[str, dict[str, Any]],
    *,
    common: dict[str, str],
) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    for scope, source_site, target_site in PARITY_COMPARISONS:
        source = snapshots[source_site]["database_snapshot"]
        target = snapshots[target_site]["database_snapshot"]
        report = compare_parity_snapshots(source, target, sample_limit=0)
        counts = report.get("severity_counts") or {}
        critical = int(counts.get("critical_drift") or 0)
        business = int(counts.get("business_drift") or 0)
        incomplete = int(counts.get("incomplete") or 0)
        local_only = int(counts.get("local_only_difference") or 0)
        volatile = int(counts.get("volatile_difference") or 0)
        if report.get("status") not in {"ok", "non_business_difference"} or critical or business or incomplete:
            raise ConvergenceEvidenceError(
                f"database parity {source_site}->{target_site} has business/critical drift"
            )
        source_business = business_snapshot_fingerprint(source)
        target_business = business_snapshot_fingerprint(target)
        if source_business != target_business:
            raise ConvergenceEvidenceError(
                f"database parity {source_site}->{target_site} business fingerprint differs"
            )
        source_table_set = _table_set(source)
        target_table_set = _table_set(target)
        if source_table_set != target_table_set:
            raise ConvergenceEvidenceError(
                f"database parity {source_site}->{target_site} table set differs"
            )
        comparisons.append(
            {
                "scope": scope,
                "source_site": source_site,
                "target_site": target_site,
                "table_set_sha256": source_table_set,
                "source_fingerprint_sha256": _snapshot_fingerprint(source),
                "target_fingerprint_sha256": _snapshot_fingerprint(target),
                "source_business_fingerprint_sha256": source_business,
                "target_business_fingerprint_sha256": target_business,
                "source_row_count": sum(int(table.get("row_count") or 0) for table in source["tables"].values()),
                "target_row_count": sum(int(table.get("row_count") or 0) for table in target["tables"].values()),
                "table_count": len(source["tables"]),
                "difference_count": local_only + volatile,
                "business_drift_count": 0,
                "critical_drift_count": 0,
                "incomplete_count": 0,
                "local_only_difference_count": local_only,
                "volatile_difference_count": volatile,
            }
        )
    return {
        "schema": "three-site-staging-database-parity-v1",
        **common,
        "status": "equivalent",
        "mode": "deep",
        "snapshot_id": str(uuid4()),
        "mismatch_count": 0,
        "comparisons": comparisons,
    }


def _blob_payload(records: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "content_hash": content_hash,
            "size_bytes": int(record["size_bytes"]),
            "object_version_id": str(record["object_version_id"]),
            "object_ciphertext_hash": str(record["object_ciphertext_hash"]),
            "object_ciphertext_size": int(record["object_ciphertext_size"]),
            "encryption_key_id": str(record["encryption_key_id"]),
            "encryption_algorithm": str(record["encryption_algorithm"]),
        }
        for content_hash, record in sorted(records.items())
    ]


def _blob_artifact(
    snapshots: dict[str, dict[str, Any]],
    *,
    common: dict[str, str],
) -> dict[str, Any]:
    scopes: list[dict[str, Any]] = []
    for scope, source_site, target_site in BLOB_SCOPES:
        source_records = snapshots[source_site]["blob_records"]
        target_records = snapshots[target_site]["blob_records"]
        source_payload = _blob_payload(source_records)
        target_payload = _blob_payload(target_records)
        if source_payload != target_payload:
            raise ConvergenceEvidenceError(
                f"Blob parity {source_site}->{target_site} is not exact"
            )
        scopes.append(
            {
                "scope": scope,
                "source_site": source_site,
                "target_site": target_site,
                "source_set_sha256": _sha(source_payload),
                "target_set_sha256": _sha(target_payload),
                "source_object_count": len(source_payload),
                "target_object_count": len(target_payload),
                # Every record in a local site snapshot is re-hashed from its
                # content-addressed local file before this builder sees it.
                "readback_sample_count": len(source_payload),
            }
        )
    return {
        "schema": "three-site-staging-blob-parity-v1",
        **common,
        "status": "passed",
        "object_storage_versioning": True,
        "missing_object_count": 0,
        "corrupt_object_count": 0,
        "scopes": scopes,
    }


def build(
    *,
    raw_snapshots: Mapping[str, Mapping[str, Any]],
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    try:
        if str(UUID(campaign_id)) != campaign_id:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ConvergenceEvidenceError("campaign id is invalid") from exc
    if SHA40.fullmatch(release_sha) is None or SHA256.fullmatch(plan_sha256) is None:
        raise ConvergenceEvidenceError("release or plan identity is invalid")
    if set(raw_snapshots) != set(SITES):
        raise ConvergenceEvidenceError("all three raw snapshots are required")
    snapshots = {
        site: _validate_snapshot(
            raw_snapshots[site], site=site, campaign_id=campaign_id,
            release_sha=release_sha, plan_sha256=plan_sha256,
        )
        for site in SITES
    }
    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    captures = [item["captured_at"] for item in snapshots.values()]
    if any(capture > observed_now + MAX_SNAPSHOT_FUTURE_SKEW for capture in captures):
        raise ConvergenceEvidenceError("convergence snapshot is implausibly in the future")
    if any(observed_now - capture > MAX_SNAPSHOT_AGE for capture in captures):
        raise ConvergenceEvidenceError("convergence snapshot is stale")
    if max(captures) - min(captures) > MAX_SNAPSHOT_CAPTURE_SKEW:
        raise ConvergenceEvidenceError("convergence snapshots exceed the capture-skew bound")
    common = {
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "plan_sha256": plan_sha256,
        "observed_at": _now(),
    }
    return {
        "event_checkpoint": _event_artifact(snapshots, common=common),
        "database_parity": _database_artifact(snapshots, common=common),
        "blob_parity": _blob_artifact(snapshots, common=common),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--site-snapshot", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        paths = _mapping(args.site_snapshot)
        artifacts = build(
            raw_snapshots={site: _read_snapshot(path) for site, path in paths.items()},
            campaign_id=args.campaign_id,
            release_sha=args.release_sha,
            plan_sha256=args.plan_sha256,
        )
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        outputs: dict[str, dict[str, str]] = {}
        for label, artifact in artifacts.items():
            path = args.output_dir / f"{label}.json"
            write_secure_atomic_bytes(
                path,
                _canonical(artifact) + b"\n",
                label=f"three-site {label} evidence",
                mode=0o600,
                max_size=32 * 1024 * 1024,
            )
            outputs[label] = {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        print(json.dumps({"status": "passed", "artifacts": outputs}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
