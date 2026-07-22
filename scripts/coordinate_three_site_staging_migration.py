#!/usr/bin/env python3
"""Issue fail-closed cross-role barriers from four durable migration journals."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import ipaddress
import re
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes
from core.secure_file_io import read_secure_bytes
from scripts.collect_three_site_staging_migration_observation import (
    ObservationError,
    ROLE_SERVICES,
    ROLE_TLS,
    validate_convergence_artifact,
)
from scripts.run_three_site_staging_role_migration import _secure_json
from scripts.three_site_staging_migration_journal import MigrationJournal, ROLE_PHASES


ROLES = tuple(ROLE_PHASES)
PRODUCT_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
REQUIRED_PHASE = {
    "private-barrier": {
        "bot_fi": "private_ready",
        "webapp_fi": "writer_initialized",
        "webapp_ir": "standby_fenced",
        "witness": "private_ready",
    },
    "routing-hold": {
        "bot_fi": "workers_ready",
        "webapp_fi": "workers_ready",
        "webapp_ir": "workers_ready",
        "witness": "private_ready",
    },
    "role-acceptance": {
        "bot_fi": "public_ready",
        "webapp_fi": "public_ready",
        "webapp_ir": "public_ready",
        "witness": "private_ready",
    },
}
ACCEPTANCE_CHECKS = {
    "database_identity", "migration_head", "private_tls", "service_health",
    "direct_origin_http", "production_boundaries_untouched",
    "unexpected_errors_absent", "queue_owner_legacy", "routing_still_held",
    "signed_runtime_bundle",
}


class MigrationCoordinationError(RuntimeError):
    pass


def _mapping(values: list[str], *, label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for item in values:
        role, separator, raw_path = item.partition("=")
        if not separator or role not in ROLES or role in result or not raw_path:
            raise MigrationCoordinationError(f"{label} requires unique role=/path mappings")
        result[role] = Path(raw_path)
    if set(result) != set(ROLES):
        raise MigrationCoordinationError(f"{label} role set is incomplete")
    return result


def _load_journals(values: list[str]) -> dict[str, dict[str, Any]]:
    paths = _mapping(values, label="--journal")
    result = {role: MigrationJournal(path).load() for role, path in paths.items()}
    identities = {
        (state["campaign_id"], state["release_sha"], state["plan_sha256"])
        for state in result.values()
    }
    if len(identities) != 1 or any(state["role"] != role for role, state in result.items()):
        raise MigrationCoordinationError("role journals do not belong to one exact campaign")
    return result


def _at_least(state: dict[str, Any], phase: str) -> bool:
    phases = ROLE_PHASES[state["role"]]
    return phase in state["completed_phases"] and state["started_phase"] is None


def _assert_barrier_state(action: str, journals: dict[str, dict[str, Any]]) -> None:
    if action == "global-commit":
        if any(state["status"] != "committed" for state in journals.values()):
            raise MigrationCoordinationError("global commit requires all four committed roles")
        return
    if any(state["status"] != "active" for state in journals.values()):
        raise MigrationCoordinationError("barrier requires four active, non-interrupted journals")
    for role, phase in REQUIRED_PHASE[action].items():
        if not _at_least(journals[role], phase):
            raise MigrationCoordinationError(f"{action} is waiting for {role}:{phase}")


def _identity(journals: dict[str, dict[str, Any]]) -> dict[str, str]:
    first = journals["bot_fi"]
    return {
        "campaign_id": first["campaign_id"],
        "release_sha": first["release_sha"],
        "plan_sha256": first["plan_sha256"],
    }


def _journal_hashes(journals: dict[str, dict[str, Any]]) -> tuple[dict[str, str], str]:
    hashes = {role: state["state_sha256"] for role, state in sorted(journals.items())}
    digest = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return hashes, digest


def _observed_at(value: Any, *, label: str) -> None:
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise MigrationCoordinationError(f"{label} observed_at is invalid") from exc
    now = datetime.now(timezone.utc)
    if (
        observed.tzinfo is None
        or now - observed.astimezone(timezone.utc) > timedelta(minutes=5)
        or observed.astimezone(timezone.utc) > now + timedelta(minutes=2)
    ):
        raise MigrationCoordinationError(f"{label} observation is stale or future-dated")


def _routing_observation(path: Path, identity: dict[str, str]) -> tuple[dict[str, Any], str]:
    value = _secure_json(path, label="routing hold observation")
    fields = {
        "schema", "campaign_id", "release_sha", "plan_sha256", "observed_at",
        "domain", "record", "current_origin_ip", "expected_legacy_origin_ip",
        "provider_read_sha256", "artifacts",
    }
    try:
        current_origin = str(ipaddress.ip_address(str(value.get("current_origin_ip", ""))))
        expected_origin = str(
            ipaddress.ip_address(str(value.get("expected_legacy_origin_ip", "")))
        )
    except ValueError as exc:
        raise MigrationCoordinationError("routing hold origin IP is invalid") from exc
    if (
        set(value) != fields
        or value.get("schema") != "three-site-staging-routing-observation-v2"
        or any(value.get(key) != expected for key, expected in identity.items())
        or value.get("domain") != "gold-trading.ir"
        or value.get("record") != "app"
        or current_origin != expected_origin
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("provider_read_sha256", ""))) is None
        or not isinstance(value.get("artifacts"), dict)
        or set(value["artifacts"]) != {"event_checkpoint", "database_parity", "blob_parity"}
    ):
        raise MigrationCoordinationError("routing hold observation is invalid")
    for label, reference in value["artifacts"].items():
        if not isinstance(reference, dict) or set(reference) != {
            "path", "sha256", "schema", "status", "observed_at"
        }:
            raise MigrationCoordinationError(f"routing {label} artifact reference is invalid")
        artifact_path = Path(str(reference["path"]))
        if not artifact_path.is_absolute():
            raise MigrationCoordinationError(f"routing {label} artifact path is not absolute")
        raw = read_secure_bytes(
            artifact_path,
            label=f"routing {label} artifact",
            max_size=64 * 1024 * 1024,
        )
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise MigrationCoordinationError(f"routing {label} artifact changed")
        try:
            artifact = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MigrationCoordinationError(f"routing {label} artifact is invalid") from exc
        artifact_label = label.replace("_", " ")
        try:
            validate_convergence_artifact(
                artifact,
                label=artifact_label,
                campaign_id=identity["campaign_id"],
                release_sha=identity["release_sha"],
                plan_sha256=identity["plan_sha256"],
            )
        except ObservationError as exc:
            raise MigrationCoordinationError(
                f"routing {label} artifact lacks semantic convergence evidence"
            ) from exc
        if (
            artifact["schema"] != reference["schema"]
            or artifact["status"] != reference["status"]
            or artifact["observed_at"] != reference["observed_at"]
        ):
            raise MigrationCoordinationError(f"routing {label} artifact reference differs")
    _observed_at(value["observed_at"], label="routing hold")
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value, digest


def _acceptance_observations(
    values: list[str], identity: dict[str, str], journals: dict[str, dict[str, Any]]
) -> dict[str, tuple[dict[str, Any], str]]:
    paths = _mapping(values, label="--acceptance-observation")
    result: dict[str, tuple[dict[str, Any], str]] = {}
    for role, path in paths.items():
        value = _secure_json(path, label=f"{role} acceptance observation")
        fields = {
            "schema", "campaign_id", "release_sha", "plan_sha256", "role",
            "observed_at", "collector", "checks", "routing_observation",
        }
        if (
            set(value) != fields
            or value.get("schema") != "three-site-staging-role-observation-v2"
            or any(value.get(key) != expected for key, expected in identity.items())
            or value.get("role") != role
            or value.get("collector") != "collect_three_site_staging_migration_observation.py"
            or not isinstance(value.get("checks"), dict)
            or set(value["checks"]) != ACCEPTANCE_CHECKS
        ):
            raise MigrationCoordinationError(f"{role} acceptance observation is invalid")
        for check_name, check in value["checks"].items():
            if (
                not isinstance(check, dict)
                or set(check) != {"status", "observation", "observation_sha256"}
                or check.get("status") != "passed"
                or not isinstance(check.get("observation"), dict)
                or hashlib.sha256(
                    json.dumps(
                        check["observation"], sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest() != check.get("observation_sha256")
            ):
                raise MigrationCoordinationError(
                    f"{role} acceptance check {check_name} is invalid"
                )
        observations = {
            name: check["observation"] for name, check in value["checks"].items()
        }
        expected_revision = "002" if role == "witness" else "b986c7d8e0f1"
        services = observations["service_health"].get("services")
        service_names = [row.get("service") for row in services] if isinstance(services, list) else []
        tls = observations["private_tls"]
        expected_tls_service = [name for name in ROLE_SERVICES[role] if name.endswith("_tls")]
        tls_handshake = tls.get("handshake") if isinstance(tls, dict) else None
        _bind_key, expected_tls_port, expected_tls_name = ROLE_TLS[role]
        runtime_bundle = observations["signed_runtime_bundle"]
        if (
            observations["migration_head"] != {
                "observed": expected_revision,
                "expected": expected_revision,
            }
            or not isinstance(services, list)
            or not services
            or service_names != list(ROLE_SERVICES[role])
            or any(
                row.get("running") is not True
                or row.get("health") not in {"healthy", "not-configured"}
                or row.get("restart_count") != 0
                or re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("image_id", ""))) is None
                or row.get("image_id") != row.get("expected_image_id")
                or not row.get("expected_image_reference")
                or row.get("log_window_seconds") != 300
                or row.get("unexpected_log_lines") != 0
                or re.fullmatch(r"[0-9a-f]{64}", str(row.get("log_sha256", ""))) is None
                or (
                    not str(row.get("service", "")).endswith("_tls")
                    and row.get("release_sha") != identity["release_sha"]
                )
                for row in services
            )
            or not isinstance(tls, dict)
            or tls.get("services") != expected_tls_service
            or not isinstance(tls_handshake, dict)
            or tls_handshake.get("server_name") != expected_tls_name
            or tls_handshake.get("port") != expected_tls_port
            or tls_handshake.get("protocol") not in {"TLSv1.2", "TLSv1.3"}
            or tls_handshake.get("readiness_status_code") != 200
            or re.fullmatch(
                r"[0-9a-f]{64}", str(tls_handshake.get("certificate_sha256", ""))
            ) is None
            or observations["direct_origin_http"].get("status_code") != 200
            or re.fullmatch(
                r"[0-9a-f]{64}",
                str(observations["direct_origin_http"].get("response_sha256", "")),
            ) is None
            or observations["production_boundaries_untouched"].get("routing_change_applied") is not False
            or observations["production_boundaries_untouched"].get("test_domain") != "gold-trading.ir"
            or observations["production_boundaries_untouched"].get("compose_project")
            != "trading-bot-three-site-staging"
            or observations["unexpected_errors_absent"].get("restart_count_total") != 0
            or observations["unexpected_errors_absent"].get("window_seconds") != 300
            or observations["unexpected_errors_absent"].get("unexpected_log_lines_total") != 0
            or observations["queue_owner_legacy"].get("producer_mode") != "legacy"
            or observations["queue_owner_legacy"].get("executor_mode") != "legacy"
            or not isinstance(runtime_bundle, dict)
            or runtime_bundle != {
                "role_compose_sha256": journals[role]["role_compose_sha256"],
                "role_env_sha256": journals[role]["role_env_sha256"],
                "image_inventory_sha256": journals[role]["image_inventory_sha256"],
            }
        ):
            raise MigrationCoordinationError(
                f"{role} acceptance observations do not satisfy the closed policy"
            )
        routing_reference = value.get("routing_observation")
        if not isinstance(routing_reference, dict) or set(routing_reference) != {"path", "sha256"}:
            raise MigrationCoordinationError(f"{role} routing observation reference is invalid")
        routing_path = Path(str(routing_reference["path"]))
        routing_raw = read_secure_bytes(
            routing_path,
            label=f"{role} routing observation",
            max_size=4 * 1024 * 1024,
        )
        if hashlib.sha256(routing_raw).hexdigest() != routing_reference["sha256"]:
            raise MigrationCoordinationError(f"{role} routing observation changed")
        _routing_observation(routing_path, identity)
        _observed_at(value["observed_at"], label=f"{role} acceptance")
        digest = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        result[role] = (value, digest)
    return result


def build_documents(
    *,
    action: str,
    journals: dict[str, dict[str, Any]],
    routing_observation: Path | None = None,
    acceptance_observations: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    _assert_barrier_state(action, journals)
    identity = _identity(journals)
    hashes, campaign_hash = _journal_hashes(journals)
    issued_at = datetime.now(timezone.utc).isoformat()
    if action == "global-commit":
        return {
            "global-commit": {
                "schema": "three-site-staging-global-commit-v2",
                "status": "passed",
                **identity,
                "issued_at": issued_at,
                "campaign_journals_sha256": campaign_hash,
                "role_journals": hashes,
                "committed_role_states": {
                    role: state for role, state in sorted(journals.items())
                },
                "all_roles_committed": True,
            }
        }
    if action == "routing-hold":
        if routing_observation is None:
            raise MigrationCoordinationError("routing-hold requires --routing-observation")
        _value, observation_hash = _routing_observation(routing_observation, identity)
    else:
        observation_hash = ""
    observations = (
        _acceptance_observations(acceptance_observations or [], identity, journals)
        if action == "role-acceptance"
        else {}
    )
    target_roles = ROLES if action == "role-acceptance" else PRODUCT_ROLES
    schema = {
        "private-barrier": "three-site-staging-private-barrier-v1",
        "routing-hold": "three-site-staging-routing-hold-v1",
        "role-acceptance": "three-site-staging-role-acceptance-v1",
    }[action]
    documents = {}
    for role in target_roles:
        document = {
            "schema": schema,
            "status": "passed",
            **identity,
            "role": role,
            "issued_at": issued_at,
            "role_journal_state_sha256": hashes[role],
            "campaign_journals_sha256": campaign_hash,
        }
        if action == "routing-hold":
            document["routing_observation_sha256"] = observation_hash
        elif action == "role-acceptance":
            document["acceptance_observation_sha256"] = observations[role][1]
        documents[role] = document
    return documents


def confirmation_phrase(action: str, identity: dict[str, str]) -> str:
    return f"migration-barrier:{identity['campaign_id']}:{action}:{identity['plan_sha256']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("private-barrier", "routing-hold", "role-acceptance", "global-commit")
    )
    parser.add_argument("--journal", action="append", required=True)
    parser.add_argument("--routing-observation", type=Path)
    parser.add_argument("--acceptance-observation", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        journals = _load_journals(args.journal)
        documents = build_documents(
            action=args.action,
            journals=journals,
            routing_observation=args.routing_observation,
            acceptance_observations=args.acceptance_observation,
        )
        required = confirmation_phrase(args.action, _identity(journals))
        if not args.apply:
            result = {
                "status": "planned", "action": args.action,
                "outputs": sorted(documents), "required_confirmation": required,
            }
        else:
            if args.confirm != required:
                raise MigrationCoordinationError("migration barrier confirmation mismatch")
            paths = {}
            for role, document in documents.items():
                path = args.output_dir / f"{args.action}-{role}.json"
                write_secure_atomic_bytes(
                    path,
                    (json.dumps(document, sort_keys=True, indent=2) + "\n").encode(),
                    label="migration coordination evidence",
                    mode=0o600,
                )
                paths[role] = str(path)
            result = {"status": "written", "action": args.action, "paths": paths}
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
