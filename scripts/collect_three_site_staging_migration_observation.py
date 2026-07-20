#!/usr/bin/env python3
"""Collect typed, direct observations for three-site staging barriers.

This tool never accepts caller-authored pass/fail booleans.  It executes the
closed Docker/PostgreSQL/HTTP/provider reads itself and emits the observations
that the coordinator reopens and validates.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import read_secure_bytes, write_secure_atomic_bytes
from scripts.arvan_origin_switch import inspect_or_switch, load_token
from scripts.render_three_site_staging_role_compose import parse_env_values


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


class ObservationError(RuntimeError):
    pass


def _run(arguments: list[str], *, timeout: int = 120) -> str:
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
    return result.stdout.strip()


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _artifact(path: Path, *, label: str) -> dict[str, Any]:
    raw = read_secure_bytes(path, label=label, max_size=64 * 1024 * 1024)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ObservationError(f"{label} is not JSON") from exc
    if not isinstance(document, dict):
        raise ObservationError(f"{label} must be one object")
    status = str(document.get("status") or "")
    if status not in {"ok", "passed", "equivalent", "converged"}:
        raise ObservationError(f"{label} does not prove a passing observation")
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema": str(document.get("schema") or document.get("mode") or label),
        "status": status,
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
        "event_checkpoint": _artifact(args.event_checkpoint, label="event checkpoint"),
        "database_parity": _artifact(args.database_parity, label="database parity"),
        "blob_parity": _artifact(args.blob_parity, label="blob parity"),
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
        "running": True,
        "health": health or "not-configured",
        "restart_count": restart_count,
        "release_sha": observed_release,
    }


def collect_role(args: argparse.Namespace) -> dict[str, Any]:
    env = parse_env_values(
        read_secure_bytes(args.env_file, label="role env", max_size=1024 * 1024).decode()
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
    services = [
        _service_observation(args, service) for service in ROLE_SERVICES[args.role]
    ]
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
            "services": [row["service"] for row in services if row["service"].endswith("_tls")]
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
        },
        "queue_owner_legacy": {
            "producer_mode": producer_mode,
            "executor_mode": executor_mode,
        },
        "routing_still_held": {
            "routing_observation_sha256": hashlib.sha256(routing_raw).hexdigest(),
            "origin_ip": routing["current_origin_ip"],
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
    role.add_argument("--expected-head", default="d542e3f4a6b7")
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
