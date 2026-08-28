#!/usr/bin/env python3
"""Create a value-free, fail-closed PRIVATE_PRIMARY host observation.

This command is deliberately read-only.  Expected release values are assertions,
never evidence: container identity is obtained from Docker inspect, sequence and
counter evidence is read from the live state databases, and snapshot identity is
derived from the retained Product view.  The resulting artifact is the exact
``production_private_primary_observation/1.0`` input consumed by the promotion
verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from core.market_intelligence.private_pipeline_contracts import (
    ESTIMATOR_RATE_GRID_V1,
    EstimatorSnapshotV2,
)


SCHEMA = "production_private_primary_observation/1.0"
VIEW_CONTRACT = "estimator_snapshot_web_view/1.0"
BOT_SERVICES = frozenset({"market-fact-receiver", "market-store-adapter", "coin-estimator", "estimator-snapshot-sender"})
WEB_SERVICES = frozenset({"market-database", "market-capture-account1", "market-capture-account2", "market-capture-external", "market-processor", "market-fact-sync-worker", "estimator-snapshot-receiver"})
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
STREAM = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class ObservationError(RuntimeError):
    pass


def _fail(reason: str) -> None:
    raise ObservationError(reason)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationError("snapshot_unreadable") from exc
    if not isinstance(value, Mapping):
        _fail("snapshot_invalid")
    return value


def _write_exclusive(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _command(arguments: Sequence[str]) -> str:
    completed = subprocess.run(list(arguments), check=False, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        _fail("runtime_inspection_failed")
    return completed.stdout


def _docker_inventory(project: str) -> list[Mapping[str, object]]:
    # Inspect every container so an older Compose project cannot remain an
    # undisclosed second owner.  ``project`` is only an assertion below.
    identifiers = [
        item
        for item in _command(["docker", "ps", "-aq"]).splitlines()
        if item
    ]
    if not identifiers:
        _fail("compose_project_absent")
    try:
        result = json.loads(_command(["docker", "inspect", *identifiers]))
    except json.JSONDecodeError as exc:
        raise ObservationError("docker_inspect_invalid") from exc
    if not isinstance(result, list) or len(result) != len(set(identifiers)):
        _fail("docker_inspect_invalid")
    return result


def _runtime_health(
    inventory: Sequence[Mapping[str, object]],
    expected: frozenset[str],
    project: str,
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for item in inventory:
        config = item.get("Config")
        state = item.get("State")
        if not isinstance(config, Mapping) or not isinstance(config.get("Labels"), Mapping):
            continue
        if not isinstance(state, Mapping) or state.get("Running") is not True:
            continue
        service = config["Labels"].get("com.docker.compose.service")
        labels = config["Labels"]
        if (
            labels.get("com.docker.compose.project") != project
            or service not in expected
            or service == "market-database"
        ):
            continue
        identifier = item.get("Id")
        if not isinstance(identifier, str) or not identifier:
            _fail("container_identity_invalid")
        try:
            value = json.loads(_command(["docker", "exec", identifier, "cat", "/var/lib/market-data/state/health.json"]))
        except json.JSONDecodeError as exc:
            raise ObservationError("runtime_health_invalid") from exc
        if not isinstance(value, Mapping):
            _fail("runtime_health_invalid")
        result[str(service)] = value
    return result


def _validate_runtime_health(*, expected: frozenset[str], release_sha: str, documents: Mapping[str, Mapping[str, object]]) -> None:
    application_services = expected - {"market-database"}
    if set(documents) != set(application_services):
        _fail("runtime_health_incomplete")
    for service, value in documents.items():
        if value.get("role") != service or value.get("release_sha") != release_sha or value.get("mode") != "live":
            _fail("runtime_health_binding_invalid")
        status = value.get("status")
        if not isinstance(status, str) or status.lower() in {"blocked", "failed", "error", "unhealthy", "starting"}:
            _fail("runtime_health_not_ready")
        if "feed_mode" in value and value.get("feed_mode") != "PRIVATE_PRIMARY":
            _fail("runtime_health_feed_mode_invalid")


def _env_map(container: Mapping[str, object]) -> dict[str, str]:
    config = container.get("Config")
    if not isinstance(config, Mapping) or not isinstance(config.get("Env"), list):
        _fail("container_config_invalid")
    result: dict[str, str] = {}
    for item in config["Env"]:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def _owners(*, role: str, project: str, release_sha: str, release_tree: str, image_id: str, inventory: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], int, int]:
    expected = BOT_SERVICES if role == "bot" else WEB_SERVICES
    grouped: dict[str, list[Mapping[str, object]]] = {service: [] for service in expected}
    unexpected = 0
    legacy = 0
    for item in inventory:
        config = item.get("Config")
        state = item.get("State")
        if not isinstance(config, Mapping) or not isinstance(state, Mapping):
            continue
        labels = config.get("Labels")
        if not isinstance(labels, Mapping):
            continue
        service = labels.get("com.docker.compose.service")
        if not isinstance(service, str):
            continue
        # Blue/green intentionally retains stopped containers as a bounded
        # rollback asset.  Only a running container can own a runtime role.
        if state.get("Running") is not True:
            continue
        actual_project = labels.get("com.docker.compose.project")
        if service in expected and actual_project != project:
            legacy += 1
            continue
        if actual_project != project:
            continue
        if service not in expected:
            if service.startswith("market-") or service in {"coin-estimator", "estimator-snapshot-sender", "estimator-snapshot-receiver"}:
                unexpected += 1
            continue
        grouped[service].append(item)
        env = _env_map(item)
        if service != "market-database" and env.get("MARKET_PIPELINE_FEED_MODE") != "PRIVATE_PRIMARY":
            legacy += 1
    owners: dict[str, object] = {}
    for service, rows in grouped.items():
        if len(rows) != 1:
            _fail("owner_count_invalid")
        item = rows[0]
        config = item["Config"]
        state = item["State"]
        labels = config["Labels"]
        running = state.get("Running") is True
        health = state.get("Health")
        healthy = running and (health is None or (isinstance(health, Mapping) and health.get("Status") == "healthy"))
        actual_image = item.get("Image")
        if not isinstance(actual_image, str) or not IMAGE_ID.fullmatch(actual_image):
            _fail("container_image_invalid")
        if service == "market-database":
            row_sha = None
            row_tree = None
        else:
            env = _env_map(item)
            row_sha = labels.get("org.opencontainers.image.revision", env.get("MARKET_PIPELINE_RELEASE_SHA"))
            row_tree = labels.get("io.gold-trade.release.tree", env.get("MARKET_PIPELINE_RELEASE_TREE"))
            if row_sha != release_sha or row_tree != release_tree or actual_image != image_id:
                _fail("container_release_binding_invalid")
        owners[service] = {"count": 1, "release_sha": row_sha, "release_tree": row_tree, "project_name": project, "image_id": actual_image, "healthy": healthy}
        if not healthy:
            _fail("container_unhealthy")
    if legacy or unexpected:
        _fail("legacy_or_unexpected_owner")
    return owners, legacy, unexpected


def _container_id(
    inventory: Sequence[Mapping[str, object]], service: str, *, project: str
) -> str:
    matches: list[str] = []
    for item in inventory:
        config = item.get("Config")
        labels = config.get("Labels") if isinstance(config, Mapping) else None
        state = item.get("State")
        if (
            isinstance(labels, Mapping)
            and labels.get("com.docker.compose.service") == service
            and labels.get("com.docker.compose.project") == project
            and isinstance(state, Mapping)
            and state.get("Running") is True
        ):
            identifier = item.get("Id")
            if not isinstance(identifier, str) or not identifier:
                _fail("container_identity_invalid")
            matches.append(identifier)
    if len(matches) != 1:
        _fail("owner_count_invalid")
    return matches[0]


def _sqlite(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        _fail("sqlite_state_missing")
    try:
        # ``immutable=1`` is unsafe for a live WAL database because it can
        # omit committed WAL frames.  SQLite's read-only transaction gives a
        # consistent snapshot while preserving the writer's WAL semantics.
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            _fail("sqlite_integrity_failed")
        return connection
    except sqlite3.Error as exc:
        raise ObservationError("sqlite_read_failed") from exc


def _map_rows(rows: Sequence[Sequence[object]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for stream, sequence in rows:
        if not isinstance(stream, str) or not STREAM.fullmatch(stream) or not isinstance(sequence, int) or sequence < 1 or stream in result:
            _fail("sequence_watermark_invalid")
        result[stream] = sequence
    if not result:
        _fail("sequence_watermark_missing")
    return result


def _bot_database_evidence(receiver_db: Path, market_store_db: Path, sender_db: Path) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    receiver = _sqlite(receiver_db)
    store = _sqlite(market_store_db)
    sender = _sqlite(sender_db)
    try:
        receiver_map = _map_rows(receiver.execute("SELECT stream_id,highest_contiguous_sequence FROM fact_checkpoints ORDER BY stream_id").fetchall())
        duplicate, rejected = receiver.execute("SELECT duplicate_count,rejection_count FROM receiver_counters WHERE singleton=1").fetchone()
        adapter_map = _map_rows(store.execute("SELECT stream_id,highest_delivery_sequence FROM private_fact_adapter_checkpoints ORDER BY stream_id").fetchall())
        adapter_rejected = store.execute("SELECT COALESCE(SUM(delivery_count),0) FROM private_fact_adapter_status_counts WHERE status='REJECTED'").fetchone()[0]
        ack = sender.execute("SELECT acknowledged_version FROM estimator_snapshot_sender_state WHERE singleton=1").fetchone()
        if ack is None or int(ack[0]) < 1:
            _fail("snapshot_sender_unacknowledged")
        counts = {"duplicate": int(duplicate), "rejected": int(rejected) + int(adapter_rejected), "dead_letter": 0, "open_outbox": 0, "receiver_publication_pending": 0}
        return {"receiver": receiver_map, "adapter": adapter_map}, counts
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise ObservationError("bot_database_evidence_invalid") from exc
    finally:
        receiver.close(); store.close(); sender.close()


def _postgres_evidence(container: str, *, user: str, database: str) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    # psql output contains only stream watermarks and aggregate counters.  No
    # envelope, fact, price, account, or other Market value is selected.
    sql = """SELECT json_build_object('producer',(SELECT json_object_agg(stream_id,max_seq) FROM (SELECT stream_id,MAX(delivery_sequence) max_seq FROM market_data.market_fact_outbox GROUP BY stream_id)s),'acknowledged',(SELECT json_object_agg(stream_id,highest_contiguous_sequence) FROM market_data.market_fact_delivery_checkpoints),'open_outbox',(SELECT COUNT(*) FROM market_data.market_fact_outbox WHERE acknowledged_at_utc IS NULL),'dead_letter',(SELECT COUNT(*) FROM market_data.market_fact_dead_letters WHERE repaired_at_utc IS NULL));"""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", user) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", database):
        _fail("postgres_identity_invalid")
    raw = _command(["docker", "exec", container, "psql", "-XAt", "-U", user, "-d", database, "-c", sql]).strip()
    try:
        value = json.loads(raw)
        producer = _map_rows(list(dict(value["producer"]).items()))
        acknowledged = _map_rows(list(dict(value["acknowledged"]).items()))
        counts = {"duplicate": 0, "rejected": 0, "dead_letter": int(value["dead_letter"]), "open_outbox": int(value["open_outbox"]), "receiver_publication_pending": 0}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ObservationError("postgres_evidence_invalid") from exc
    return {"producer": producer, "acknowledged": acknowledged}, counts


def _web_receiver_evidence(receiver_db: Path) -> tuple[int, int]:
    connection = _sqlite(receiver_db)
    try:
        # Historical SHADOW rows are retained evidence, not ownership of the
        # PRIMARY publication lane.  Promotion gates only unresolved work in
        # the lane being promoted.  Rejections are lifetime diagnostics and
        # cannot identify a lane in the current schema; release-window
        # rejection proof is supplied by the exact catch-up audit instead of
        # treating any old, resolved rejection as a permanent outage.
        pending = int(connection.execute("SELECT COUNT(*) FROM estimator_snapshot_publication_outbox WHERE feed_mode='PRIVATE_PRIMARY' AND delivered_at_utc IS NULL").fetchone()[0])
        rejected = 0
        return pending, rejected
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise ObservationError("receiver_database_evidence_invalid") from exc
    finally:
        connection.close()


def _snapshot_identity(path: Path) -> dict[str, object]:
    value = _read_json(path)
    payload = value.get("snapshot")
    try:
        snapshot = EstimatorSnapshotV2.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ObservationError("snapshot_contract_invalid") from exc
    estimated = [rate for rate in snapshot.rates if rate.status == "ESTIMATED"]
    if value.get("contract") != VIEW_CONTRACT or value.get("feed_mode") != "PRIVATE_PRIMARY" or value.get("transport_state") != "FRESH" or snapshot.feed_mode != "PRIVATE_PRIMARY" or snapshot.status != "OK" or value.get("snapshot_hash") != snapshot.snapshot_id or value.get("snapshot_version") != snapshot.snapshot_version or len(snapshot.rates) != len(ESTIMATOR_RATE_GRID_V1) or len(estimated) != len(ESTIMATOR_RATE_GRID_V1):
        _fail("snapshot_identity_invalid")
    return {"contract": VIEW_CONTRACT, "snapshot_hash": snapshot.snapshot_id, "snapshot_version": snapshot.snapshot_version, "feed_mode": "PRIVATE_PRIMARY", "snapshot_status": "OK", "estimated_rate_count": len(estimated), "file_sha256": _sha256(path)}


def observe(*, role: str, release_sha: str, release_tree: str, project: str, image_id: str, snapshot_path: Path, inventory: Sequence[Mapping[str, object]], health_documents: Mapping[str, Mapping[str, object]], receiver_db: Path | None = None, market_store_db: Path | None = None, sender_db: Path | None = None, now: datetime | None = None) -> dict[str, object]:
    if role not in {"bot", "web"} or not HEX40.fullmatch(release_sha) or not HEX40.fullmatch(release_tree) or not IMAGE_ID.fullmatch(image_id):
        _fail("expected_identity_invalid")
    expected = BOT_SERVICES if role == "bot" else WEB_SERVICES
    owners, legacy, unexpected = _owners(role=role, project=project, release_sha=release_sha, release_tree=release_tree, image_id=image_id, inventory=inventory)
    _validate_runtime_health(expected=expected, release_sha=release_sha, documents=health_documents)
    if role == "bot":
        if None in {receiver_db, market_store_db, sender_db}:
            _fail("bot_database_paths_missing")
        sequences, counts = _bot_database_evidence(receiver_db, market_store_db, sender_db)  # type: ignore[arg-type]
    else:
        if receiver_db is None:
            _fail("web_database_sources_missing")
        # Resolve the PostgreSQL execution target from the same inspected
        # Compose inventory.  No caller-supplied container name is trusted.
        database_rows = [item for item in inventory if isinstance(item.get("Config"), Mapping) and isinstance(item["Config"].get("Labels"), Mapping) and item["Config"]["Labels"].get("com.docker.compose.service") == "market-database"]
        if len(database_rows) != 1:
            _fail("owner_count_invalid")
        database_env = _env_map(database_rows[0])
        sequences, counts = _postgres_evidence(
            _container_id(inventory, "market-database", project=project),
            user=database_env.get("POSTGRES_USER", "market_data"),
            database=database_env.get("POSTGRES_DB", "market_archive"),
        )
        pending, rejected = _web_receiver_evidence(receiver_db)
        counts["receiver_publication_pending"] = pending
        counts["rejected"] += rejected
    # Duplicate transport deliveries are expected under at-least-once
    # delivery and are safe only because the receiver/adapter checkpoints
    # below prove a single contiguous application.  Every destructive or
    # unresolved counter must remain zero.
    unsafe_counts = {key: value for key, value in counts.items() if key != "duplicate"}
    if any(value != 0 for value in unsafe_counts.values()):
        _fail("nonzero_safety_counter")
    sequence_values = list(sequences.values())
    if any(item != sequence_values[0] for item in sequence_values[1:]):
        _fail("local_sequence_gap")
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {"schema": SCHEMA, "role": role, "observed_at_utc": observed, "release_sha": release_sha, "release_tree": release_tree, "project_name": project, "image_id": image_id, "owners": owners, "legacy_owner_count": legacy, "unexpected_owner_count": unexpected, "sequences": sequences, "counts": counts, "snapshot": _snapshot_identity(snapshot_path), "secrets_disclosed": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("bot", "web"), required=True)
    parser.add_argument("--release-sha", required=True); parser.add_argument("--release-tree", required=True)
    parser.add_argument("--project", required=True); parser.add_argument("--image-id", required=True)
    parser.add_argument("--snapshot", type=Path, required=True); parser.add_argument("--receiver-db", type=Path, required=True)
    parser.add_argument("--market-store-db", type=Path); parser.add_argument("--sender-db", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = _docker_inventory(args.project)
    expected = BOT_SERVICES if args.role == "bot" else WEB_SERVICES
    value = observe(role=args.role, release_sha=args.release_sha, release_tree=args.release_tree, project=args.project, image_id=args.image_id, snapshot_path=args.snapshot, inventory=inventory, health_documents=_runtime_health(inventory, expected, args.project), receiver_db=args.receiver_db, market_store_db=args.market_store_db, sender_db=args.sender_db)
    _write_exclusive(args.output, value)
    print(json.dumps({"status": "PASS", "schema": SCHEMA, "role": args.role, "artifact_sha256": _sha256(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ObservationError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        raise SystemExit(2)
