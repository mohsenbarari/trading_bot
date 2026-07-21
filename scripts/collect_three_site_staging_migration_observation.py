#!/usr/bin/env python3
"""Collect typed, direct observations for three-site staging barriers.

This tool never accepts caller-authored pass/fail booleans.  It executes the
closed Docker/PostgreSQL/HTTP/provider reads itself and emits the observations
that the coordinator reopens and validates.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
from typing import Any
import urllib.request
from uuid import UUID

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import read_secure_bytes, write_secure_atomic_bytes
from scripts.arvan_origin_switch import inspect_or_switch, load_token
from scripts.render_three_site_staging_role_compose import parse_env_values
from scripts.verify_three_site_staging_image_inventory import verify_image_document
from scripts.verify_three_site_staging_inventory import _strict_object
from scripts.verify_three_site_staging_role_bundle import _verify_bundle_source


SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
ROLE_DB = {
    "bot_fi": ("bot_fi_db", "BOT_FI_POSTGRES_USER", "BOT_FI_POSTGRES_DB"),
    "webapp_fi": ("webapp_fi_db", "WEBAPP_FI_POSTGRES_USER", "WEBAPP_FI_POSTGRES_DB"),
    "webapp_ir": ("webapp_ir_db", "WEBAPP_IR_POSTGRES_USER", "WEBAPP_IR_POSTGRES_DB"),
    "witness": ("witness_db", "WITNESS_POSTGRES_USER", "WITNESS_POSTGRES_DB"),
}
ROLE_SERVICES = {
    "bot_fi": (
        "bot_fi_api", "bot_fi_bot", "bot_fi_dr_receiver",
        "bot_fi_dr_projection", "bot_fi_dr_delivery", "bot_fi_dr_tls",
    ),
    "webapp_fi": (
        "webapp_fi_api", "webapp_fi_writer_control", "webapp_fi_effects",
        "webapp_fi_dr_receiver", "webapp_fi_dr_projection",
        "webapp_fi_dr_delivery", "webapp_fi_blobs", "webapp_fi_dr_tls",
    ),
    "webapp_ir": (
        "webapp_ir_api", "webapp_ir_writer_control", "webapp_ir_effects",
        "webapp_ir_dr_receiver", "webapp_ir_dr_projection",
        "webapp_ir_dr_delivery", "webapp_ir_blobs", "webapp_ir_dr_tls",
    ),
    "witness": ("witness_api", "witness_dr_tls"),
}
ROLE_API = {
    "bot_fi": (8211, "/api/config"),
    "webapp_fi": (8212, "/api/config"),
    "webapp_ir": (8213, "/api/config"),
    "witness": (8214, "/health/ready"),
}
ROLE_TLS = {
    "bot_fi": ("BOT_FI_DR_BIND_ADDRESS", 8443, "bot-fi-dr.staging.internal"),
    "webapp_fi": ("WEBAPP_FI_DR_BIND_ADDRESS", 8443, "webapp-fi-dr.staging.internal"),
    "webapp_ir": ("WEBAPP_IR_DR_BIND_ADDRESS", 8443, "webapp-ir-dr.staging.internal"),
    "witness": ("WITNESS_DR_BIND_ADDRESS", 8444, "witness-dr.staging.internal"),
}


class ObservationError(RuntimeError):
    pass


HASH_RE = re.compile(r"^[0-9a-f]{64}$")
EVENT_STREAMS = frozenset(
    (origin, destination)
    for origin in ("bot_fi", "webapp_fi", "webapp_ir")
    for destination in ("bot_fi", "webapp_fi", "webapp_ir")
    if origin != destination
)
PARITY_COMPARISONS = frozenset(
    {
        ("bot-authority", "bot_fi", "webapp_fi"),
        ("bot-authority", "bot_fi", "webapp_ir"),
        ("webapp-authority", "webapp_fi", "bot_fi"),
        ("webapp-authority", "webapp_fi", "webapp_ir"),
    }
)
BLOB_SCOPES = PARITY_COMPARISONS


def _fresh_timestamp(value: Any, *, label: str, now: datetime) -> None:
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObservationError(f"{label} observed_at is invalid") from exc
    if (
        observed.tzinfo is None
        or now - observed.astimezone(timezone.utc) > timedelta(minutes=10)
        or observed.astimezone(timezone.utc) > now + timedelta(minutes=2)
    ):
        raise ObservationError(f"{label} is stale or future-dated")


def validate_convergence_artifact(
    document: Any,
    *,
    label: str,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate measurements, not a caller-authored pass/fail word."""
    if not isinstance(document, dict):
        raise ObservationError(f"{label} must be one object")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    common = {"schema", "campaign_id", "release_sha", "plan_sha256", "observed_at", "status"}
    if (
        document.get("campaign_id") != campaign_id
        or document.get("release_sha") != release_sha
        or document.get("plan_sha256") != plan_sha256
    ):
        raise ObservationError(f"{label} identity differs from the migration campaign")
    _fresh_timestamp(document.get("observed_at"), label=label, now=current)

    if label == "event checkpoint":
        if (
            set(document) != common | {"streams", "conflict_count"}
            or document.get("schema") != "three-site-staging-event-convergence-v1"
            or document.get("status") != "converged"
            or document.get("conflict_count") != 0
            or not isinstance(document.get("streams"), list)
        ):
            raise ObservationError("event checkpoint semantic evidence is invalid")
        seen: set[tuple[str, str]] = set()
        for row in document["streams"]:
            fields = {
                "origin_site", "destination_site", "producer_epoch", "source_sequence",
                "received_sequence", "applied_sequence", "source_transaction_hash",
                "received_transaction_hash", "applied_transaction_hash",
            }
            pair = (
                str(row.get("origin_site", "")), str(row.get("destination_site", ""))
            ) if isinstance(row, dict) else ("", "")
            sequences = [row.get(key) for key in (
                "source_sequence", "received_sequence", "applied_sequence"
            )] if isinstance(row, dict) else []
            hashes = [str(row.get(key, "")) for key in (
                "source_transaction_hash", "received_transaction_hash", "applied_transaction_hash"
            )] if isinstance(row, dict) else []
            if (
                not isinstance(row, dict) or set(row) != fields or pair not in EVENT_STREAMS
                or pair in seen or type(row.get("producer_epoch")) is not int
                or row["producer_epoch"] < 1 or len(sequences) != 3
                or any(type(value) is not int or value < 0 for value in sequences)
                or len(set(sequences)) != 1 or len(hashes) != 3
                or any(HASH_RE.fullmatch(value) is None for value in hashes)
                or len(set(hashes)) != 1
                or (sequences[0] == 0) is not (hashes[0] == "0" * 64)
            ):
                raise ObservationError("event checkpoint stream does not prove exact convergence")
            seen.add(pair)
        if seen != EVENT_STREAMS:
            raise ObservationError("event checkpoint does not cover every directed stream")

    elif label == "database parity":
        if (
            set(document) != common | {"mode", "snapshot_id", "comparisons", "mismatch_count"}
            or document.get("schema") != "three-site-staging-database-parity-v1"
            or document.get("status") != "equivalent"
            or document.get("mode") != "deep"
            or document.get("mismatch_count") != 0
            or not isinstance(document.get("comparisons"), list)
        ):
            raise ObservationError("database parity semantic evidence is invalid")
        try:
            if str(UUID(str(document["snapshot_id"]))) != document["snapshot_id"]:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ObservationError("database parity snapshot identity is invalid") from exc
        seen = set()
        for row in document["comparisons"]:
            fields = {
                "scope", "source_site", "target_site", "table_set_sha256",
                "source_fingerprint_sha256", "target_fingerprint_sha256",
                "source_row_count", "target_row_count", "table_count", "difference_count",
            }
            identity = (
                str(row.get("scope", "")), str(row.get("source_site", "")),
                str(row.get("target_site", "")),
            ) if isinstance(row, dict) else ("", "", "")
            if (
                not isinstance(row, dict) or set(row) != fields or identity not in PARITY_COMPARISONS
                or identity in seen or row.get("difference_count") != 0
                or any(HASH_RE.fullmatch(str(row.get(key, ""))) is None for key in (
                    "table_set_sha256", "source_fingerprint_sha256", "target_fingerprint_sha256"
                ))
                or row["source_fingerprint_sha256"] != row["target_fingerprint_sha256"]
                or type(row.get("source_row_count")) is not int
                or row["source_row_count"] < 0
                or row.get("target_row_count") != row["source_row_count"]
                or type(row.get("table_count")) is not int or row["table_count"] < 1
            ):
                raise ObservationError("database parity comparison is not exact")
            seen.add(identity)
        if seen != PARITY_COMPARISONS:
            raise ObservationError("database parity does not cover every authority target")

    elif label == "blob parity":
        if (
            set(document) != common | {
                "object_storage_versioning", "scopes", "missing_object_count",
                "corrupt_object_count",
            }
            or document.get("schema") != "three-site-staging-blob-parity-v1"
            or document.get("status") != "passed"
            or document.get("object_storage_versioning") is not True
            or document.get("missing_object_count") != 0
            or document.get("corrupt_object_count") != 0
            or not isinstance(document.get("scopes"), list)
        ):
            raise ObservationError("blob parity semantic evidence is invalid")
        seen = set()
        for row in document["scopes"]:
            fields = {
                "scope", "source_site", "target_site", "source_set_sha256",
                "target_set_sha256", "source_object_count", "target_object_count",
                "readback_sample_count",
            }
            identity = (
                str(row.get("scope", "")), str(row.get("source_site", "")),
                str(row.get("target_site", "")),
            ) if isinstance(row, dict) else ("", "", "")
            if (
                not isinstance(row, dict) or set(row) != fields or identity not in BLOB_SCOPES
                or identity in seen
                or any(HASH_RE.fullmatch(str(row.get(key, ""))) is None for key in (
                    "source_set_sha256", "target_set_sha256"
                ))
                or row["source_set_sha256"] != row["target_set_sha256"]
                or type(row.get("source_object_count")) is not int
                or row["source_object_count"] < 0
                or row.get("target_object_count") != row["source_object_count"]
                or type(row.get("readback_sample_count")) is not int
                or not 0 <= row["readback_sample_count"] <= row["source_object_count"]
                or (row["source_object_count"] > 0 and row["readback_sample_count"] < 1)
            ):
                raise ObservationError("blob parity scope is not exact/read-back verified")
            seen.add(identity)
        if seen != BLOB_SCOPES:
            raise ObservationError("blob parity does not cover every authority target")
    else:
        raise ObservationError(f"unsupported convergence artifact: {label}")
    return document


def _run(
    arguments: list[str], *, timeout: int = 120, include_stderr: bool = False
) -> str:
    result = subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=timeout,
        env=SAFE_ENV,
    )
    if result.returncode != 0:
        raise ObservationError(
            f"observation command failed closed: {Path(arguments[0]).name}"
        )
    output = result.stdout
    if include_stderr:
        output += result.stderr
    return output.strip()


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _artifact(
    path: Path,
    *,
    label: str,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
) -> dict[str, Any]:
    raw = read_secure_bytes(path, label=label, max_size=64 * 1024 * 1024)
    try:
        document = json.loads(raw, object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise ObservationError(f"{label} is not JSON") from exc
    validate_convergence_artifact(
        document, label=label, campaign_id=campaign_id,
        release_sha=release_sha, plan_sha256=plan_sha256,
    )
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema": str(document.get("schema") or document.get("mode") or label),
        "status": str(document["status"]),
        "observed_at": str(document["observed_at"]),
    }


def collect_routing(args: argparse.Namespace) -> dict[str, Any]:
    provider = inspect_or_switch(
        domain="gold-trading.ir",
        record_name="app",
        target_ip=args.expected_legacy_origin_ip,
        token=load_token(args.arvan_token_file),
        expected_current_ip=None,
        apply=False,
        confirmation=None,
    )
    current_origin = str((provider.get("before") or {}).get("origin_ip") or "")
    if current_origin != args.expected_legacy_origin_ip:
        raise ObservationError("Arvan origin is not held on the approved legacy origin")
    artifacts = {
        "event_checkpoint": _artifact(
            args.event_checkpoint, label="event checkpoint", campaign_id=args.campaign_id,
            release_sha=args.release_sha, plan_sha256=args.plan_sha256,
        ),
        "database_parity": _artifact(
            args.database_parity, label="database parity", campaign_id=args.campaign_id,
            release_sha=args.release_sha, plan_sha256=args.plan_sha256,
        ),
        "blob_parity": _artifact(
            args.blob_parity, label="blob parity", campaign_id=args.campaign_id,
            release_sha=args.release_sha, plan_sha256=args.plan_sha256,
        ),
    }
    return {
        "schema": "three-site-staging-routing-observation-v2",
        "campaign_id": args.campaign_id,
        "release_sha": args.release_sha,
        "plan_sha256": args.plan_sha256,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "domain": "gold-trading.ir",
        "record": "app",
        "current_origin_ip": current_origin,
        "expected_legacy_origin_ip": args.expected_legacy_origin_ip,
        "provider_read_sha256": _sha(provider),
        "artifacts": artifacts,
    }


def _compose(args: argparse.Namespace) -> list[str]:
    return [
        "/usr/bin/docker", "compose", "-f", str(args.role_compose),
        "--env-file", str(args.env_file),
    ]


def _service_observation(
    args: argparse.Namespace,
    service: str,
    *,
    expected_image_reference: str,
    expected_image_id: str,
) -> dict[str, Any]:
    container = _run([*_compose(args), "ps", "-q", service])
    if not container:
        raise ObservationError(f"required service has no container: {service}")
    raw = _run(
        [
            "/usr/bin/docker", "inspect", "--format",
            "{{json .State}}", container,
        ]
    )
    state = json.loads(raw)
    health = (state.get("Health") or {}).get("Status")
    if state.get("Running") is not True or health == "unhealthy":
        raise ObservationError(f"required service is not ready: {service}")
    if health is not None and health != "healthy":
        raise ObservationError(f"required service health is not green: {service}")
    image_id = _run(
        ["/usr/bin/docker", "inspect", "--format", "{{.Image}}", container]
    )
    if image_id != expected_image_id:
        raise ObservationError(f"required service image differs from signed inventory: {service}")
    restart_count = int(
        _run(
            [
                "/usr/bin/docker", "inspect", "--format",
                "{{.RestartCount}}", container,
            ]
        )
    )
    if restart_count != 0:
        raise ObservationError(f"required service restarted during migration: {service}")
    logs = _run(
        ["/usr/bin/docker", "logs", "--since", "5m", "--timestamps", container],
        timeout=30,
        include_stderr=True,
    )
    unexpected_log_lines = 0
    for line in logs.splitlines():
        lowered = line.lower()
        try:
            structured = json.loads(line.partition(" ")[2] or line)
        except json.JSONDecodeError:
            structured = None
        level = str(structured.get("level", "")).upper() if isinstance(structured, dict) else ""
        if level in {"ERROR", "CRITICAL"} or "traceback (most recent call last)" in lowered or "[error]" in lowered:
            unexpected_log_lines += 1
    if unexpected_log_lines:
        raise ObservationError(f"required service emitted errors in the five-minute window: {service}")
    observed_release = None
    if not service.endswith("_tls"):
        observed_release = _run(
            [
                *_compose(args), "exec", "-T", service, "python", "-c",
                "from core.config import settings; print(str(settings.release_sha or ''))",
            ],
            timeout=30,
        )
        if observed_release != args.release_sha:
            raise ObservationError(f"required service has the wrong release: {service}")
    return {
        "service": service,
        "container_id": container,
        "image_id": image_id,
        "expected_image_reference": expected_image_reference,
        "expected_image_id": expected_image_id,
        "running": True,
        "health": health or "not-configured",
        "restart_count": restart_count,
        "release_sha": observed_release,
        "log_window_seconds": 300,
        "log_sha256": hashlib.sha256(logs.encode()).hexdigest(),
        "unexpected_log_lines": unexpected_log_lines,
    }


def _tls_observation(*, role: str, env: dict[str, str]) -> dict[str, Any]:
    bind_key, port, server_name = ROLE_TLS[role]
    bind_address = env.get(bind_key, "").strip()
    ca_path = env.get("STAGING_DR_CA_CERT", "").strip()
    if not bind_address or not ca_path:
        raise ObservationError("role TLS bind/CA inputs are missing")
    context = ssl.create_default_context(cafile=ca_path)
    context.check_hostname = True
    try:
        with socket.create_connection((bind_address, port), timeout=10) as plain:
            with context.wrap_socket(plain, server_hostname=server_name) as secured:
                certificate = secured.getpeercert(binary_form=True)
                secured.sendall(
                    (
                        f"GET /health/ready HTTP/1.1\r\nHost: {server_name}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode()
                )
                response = secured.recv(4096)
                protocol = secured.version()
    except (OSError, ssl.SSLError) as exc:
        raise ObservationError("private TLS endpoint verification failed") from exc
    status_line = response.partition(b"\r\n")[0]
    if not status_line.startswith(b"HTTP/1.1 200") and not status_line.startswith(b"HTTP/1.0 200"):
        raise ObservationError("private TLS readiness endpoint is not green")
    if not certificate or protocol not in {"TLSv1.2", "TLSv1.3"}:
        raise ObservationError("private TLS certificate/protocol is invalid")
    return {
        "server_name": server_name,
        "bind_address": bind_address,
        "port": port,
        "protocol": protocol,
        "certificate_sha256": hashlib.sha256(certificate).hexdigest(),
        "readiness_status_code": 200,
    }


def collect_role(args: argparse.Namespace) -> dict[str, Any]:
    env_bytes = read_secure_bytes(args.env_file, label="role env", max_size=1024 * 1024)
    env = parse_env_values(env_bytes.decode())
    compose_bytes = _verify_bundle_source(args.role_compose, expected_mode=0o640)
    compose_hash = hashlib.sha256(compose_bytes).hexdigest()
    env_hash = hashlib.sha256(env_bytes).hexdigest()
    compose_document = yaml.safe_load(compose_bytes)
    if not isinstance(compose_document, dict) or not isinstance(compose_document.get("services"), dict):
        raise ObservationError("role Compose is invalid")
    image_raw = read_secure_bytes(
        args.image_inventory, label="role image inventory", max_size=4 * 1024 * 1024
    )
    try:
        image_document = json.loads(image_raw, object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise ObservationError("role image inventory is invalid") from exc
    image_result = verify_image_document(
        image_document,
        role=args.role.replace("_", "-"),
        campaign_id=args.campaign_id,
        release_sha=args.release_sha,
        role_compose_sha256=compose_hash,
        role_env_sha256=env_hash,
    )
    db_service, user_key, database_key = ROLE_DB[args.role]
    query = (
        "SELECT json_build_object('database',current_database(),'user',current_user,"
        + (
            "'revision',(SELECT version_num FROM writer_witness_schema_version)"
            if args.role == "witness"
            else "'revision',(SELECT version_num FROM alembic_version)"
        )
        + ")::text"
    )
    database = json.loads(
        _run(
            [
                *_compose(args), "exec", "-T", db_service, "psql",
                "-v", "ON_ERROR_STOP=1", "-U", env[user_key],
                "-d", env[database_key], "-Atqc", query,
            ]
        )
    )
    expected_revision = "002" if args.role == "witness" else args.expected_head
    if database.get("revision") != expected_revision:
        raise ObservationError("database is not at the exact migration revision")
    services = []
    for service in ROLE_SERVICES[args.role]:
        service_config = compose_document["services"].get(service)
        reference = str(service_config.get("image", "")) if isinstance(service_config, dict) else ""
        expected_image_id = image_result["image_ids"].get(reference)
        if not reference or expected_image_id is None:
            raise ObservationError(f"service image is absent from signed inventory: {service}")
        services.append(
            _service_observation(
                args, service,
                expected_image_reference=reference,
                expected_image_id=expected_image_id,
            )
        )
    port, path = ROLE_API[args.role]
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}{path}", timeout=10
    ) as response:
        body = response.read(1024 * 1024)
        status_code = response.status
    if status_code != 200:
        raise ObservationError("direct origin HTTP observation failed")
    try:
        direct_payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ObservationError("direct origin returned invalid JSON") from exc
    routing_raw = read_secure_bytes(
        args.routing_observation,
        label="routing observation",
        max_size=4 * 1024 * 1024,
    )
    routing = json.loads(routing_raw)
    if (
        routing.get("schema") != "three-site-staging-routing-observation-v2"
        or routing.get("campaign_id") != args.campaign_id
        or routing.get("release_sha") != args.release_sha
        or routing.get("plan_sha256") != args.plan_sha256
        or routing.get("current_origin_ip")
        != routing.get("expected_legacy_origin_ip")
    ):
        raise ObservationError("role collector received invalid routing-hold evidence")
    producer_mode = env.get("TELEGRAM_DELIVERY_PRODUCER_MODE", "legacy")
    executor_mode = env.get("TELEGRAM_DELIVERY_EXECUTION_OWNER", "legacy")
    if producer_mode != "legacy" or executor_mode != "legacy":
        raise ObservationError("migration acceptance requires pre-cutover legacy Queue ownership")
    observations = {
        "database_identity": database,
        "migration_head": {"observed": database["revision"], "expected": expected_revision},
        "private_tls": {
            "services": [row["service"] for row in services if row["service"].endswith("_tls")],
            "handshake": _tls_observation(role=args.role, env=env),
        },
        "service_health": {"services": services},
        "direct_origin_http": {
            "url": f"http://127.0.0.1:{port}{path}",
            "status_code": status_code,
            "response_sha256": hashlib.sha256(body).hexdigest(),
        },
        "production_boundaries_untouched": {
            "compose_project": "trading-bot-three-site-staging",
            "test_domain": "gold-trading.ir",
            "routing_change_applied": False,
        },
        "unexpected_errors_absent": {
            "restart_count_total": sum(row["restart_count"] for row in services),
            "window_seconds": 300,
            "unexpected_log_lines_total": sum(row["unexpected_log_lines"] for row in services),
        },
        "queue_owner_legacy": {
            "producer_mode": producer_mode,
            "executor_mode": executor_mode,
        },
        "routing_still_held": {
            "routing_observation_sha256": hashlib.sha256(routing_raw).hexdigest(),
            "origin_ip": routing["current_origin_ip"],
        },
        "signed_runtime_bundle": {
            "role_compose_sha256": compose_hash,
            "role_env_sha256": env_hash,
            "image_inventory_sha256": hashlib.sha256(image_raw).hexdigest(),
        },
    }
    checks = {
        name: {
            "status": "passed",
            "observation": observation,
            "observation_sha256": _sha(observation),
        }
        for name, observation in observations.items()
    }
    return {
        "schema": "three-site-staging-role-observation-v2",
        "campaign_id": args.campaign_id,
        "release_sha": args.release_sha,
        "plan_sha256": args.plan_sha256,
        "role": args.role,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "collector": "collect_three_site_staging_migration_observation.py",
        "checks": checks,
        "routing_observation": {
            "path": str(args.routing_observation.resolve()),
            "sha256": hashlib.sha256(routing_raw).hexdigest(),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("routing", "role"):
        sub = subparsers.add_parser(action)
        sub.add_argument("--campaign-id", required=True)
        sub.add_argument("--release-sha", required=True)
        sub.add_argument("--plan-sha256", required=True)
        sub.add_argument("--output", type=Path, required=True)
    routing = subparsers.choices["routing"]
    routing.add_argument("--expected-legacy-origin-ip", required=True)
    routing.add_argument("--arvan-token-file", type=Path, required=True)
    routing.add_argument("--event-checkpoint", type=Path, required=True)
    routing.add_argument("--database-parity", type=Path, required=True)
    routing.add_argument("--blob-parity", type=Path, required=True)
    role = subparsers.choices["role"]
    role.add_argument("--role", choices=tuple(ROLE_DB), required=True)
    role.add_argument("--role-compose", type=Path, required=True)
    role.add_argument("--env-file", type=Path, required=True)
    role.add_argument("--routing-observation", type=Path, required=True)
    role.add_argument("--image-inventory", type=Path, required=True)
    role.add_argument("--expected-head", default="f764a5b6c8d9")
    args = parser.parse_args(argv)
    try:
        document = collect_routing(args) if args.action == "routing" else collect_role(args)
        write_secure_atomic_bytes(
            args.output,
            canonical_json_bytes(document) + b"\n",
            mode=0o600,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps({"status": "passed", "output": str(args.output), "sha256": _sha(document)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
