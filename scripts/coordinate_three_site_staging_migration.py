#!/usr/bin/env python3
"""Issue fail-closed cross-role barriers from four durable migration journals."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import ipaddress
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes
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
        "routing_held", "change_applied", "initial_convergence",
        "event_checkpoint_evidence_sha256", "database_parity_evidence_sha256",
        "blob_parity_evidence_sha256",
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
        or value.get("schema") != "three-site-staging-routing-observation-v1"
        or any(value.get(key) != expected for key, expected in identity.items())
        or value.get("domain") != "gold-trading.ir"
        or value.get("record") != "app"
        or current_origin != expected_origin
        or value.get("routing_held") is not True
        or value.get("change_applied") is not False
        or value.get("initial_convergence") is not True
        or any(
            not isinstance(value.get(field), str)
            or len(value[field]) != 64
            or any(character not in "0123456789abcdef" for character in value[field])
            for field in (
                "event_checkpoint_evidence_sha256",
                "database_parity_evidence_sha256",
                "blob_parity_evidence_sha256",
            )
        )
    ):
        raise MigrationCoordinationError("routing hold observation is invalid")
    _observed_at(value["observed_at"], label="routing hold")
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value, digest


def _acceptance_observations(
    values: list[str], identity: dict[str, str]
) -> dict[str, tuple[dict[str, Any], str]]:
    paths = _mapping(values, label="--acceptance-observation")
    result: dict[str, tuple[dict[str, Any], str]] = {}
    for role, path in paths.items():
        value = _secure_json(path, label=f"{role} acceptance observation")
        fields = {
            "schema", "campaign_id", "release_sha", "plan_sha256", "role",
            "observed_at", "checks",
        }
        if (
            set(value) != fields
            or value.get("schema") != "three-site-staging-role-observation-v1"
            or any(value.get(key) != expected for key, expected in identity.items())
            or value.get("role") != role
            or not isinstance(value.get("checks"), dict)
            or set(value["checks"]) != ACCEPTANCE_CHECKS
            or any(check is not True for check in value["checks"].values())
        ):
            raise MigrationCoordinationError(f"{role} acceptance observation is invalid")
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
                "schema": "three-site-staging-global-commit-v1",
                "status": "passed",
                **identity,
                "issued_at": issued_at,
                "campaign_journals_sha256": campaign_hash,
                "role_journals": hashes,
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
        _acceptance_observations(acceptance_observations or [], identity)
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
