#!/usr/bin/env python3
"""Build the owner-only, hash-bound runtime plan for one live Matrix class."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    read_secure_bytes,
    write_secure_atomic_bytes,
)
from core.three_site_execution_safety import EXECUTION_CLASSES  # noqa: E402
from scripts.full_matrix_live.common import (  # noqa: E402
    PLAN_SCHEMA,
    ROLE_NAMES,
    SHA40,
    _validate_roles,
    strict_object,
)


class LivePlanBuildError(RuntimeError):
    pass


BINDING_NAMES = (
    "inventory",
    "inventory_approval",
    "human_approval_policy",
    "migration_plan",
    "migration_approval",
    "global_commit",
    "campaign_bundle",
    "queue_activation_transition",
    "ingress_config",
    "object_storage_transport",
    "convergence_config",
    "sync_timing_config",
    "failover_schedule",
    "failover_control_config",
    "destructive_control_config",
)


def _json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = read_secure_bytes(path, label=label, max_size=16 * 1024 * 1024)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except Exception as exc:
        raise LivePlanBuildError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise LivePlanBuildError(f"{label} must be an object")
    return value, raw


def _mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        path = Path(raw_path)
        if (
            not separator
            or name not in BINDING_NAMES
            or name in result
            or not path.is_absolute()
        ):
            raise LivePlanBuildError("live plan binding mapping is invalid")
        result[name] = path
    if set(result) != set(BINDING_NAMES):
        raise LivePlanBuildError("live plan binding mapping is incomplete")
    return result


def _owner_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise LivePlanBuildError("scenario state root is not owner-only")
    return path.resolve()


def build_payload(
    *,
    campaign_id: str,
    gate_group_id: str,
    execution_class: str,
    release_sha: str,
    mappings: dict[str, Path],
    role_targets: dict[str, Any],
    scenario_state_root: Path,
) -> dict[str, Any]:
    try:
        normalized_campaign = str(UUID(campaign_id))
        normalized_group = str(UUID(gate_group_id))
    except ValueError as exc:
        raise LivePlanBuildError("live plan UUID identity is invalid") from exc
    if execution_class not in EXECUTION_CLASSES or SHA40.fullmatch(release_sha) is None:
        raise LivePlanBuildError("live plan release or execution class is invalid")
    if set(mappings) != set(BINDING_NAMES):
        raise LivePlanBuildError("live plan bindings are incomplete")
    if not isinstance(role_targets, dict) or set(role_targets) != set(ROLE_NAMES):
        raise LivePlanBuildError("live plan role targets are incomplete")

    bindings: dict[str, dict[str, str]] = {}
    payloads: dict[str, dict[str, Any]] = {}
    for name, path in mappings.items():
        payload, raw = _json(path, label=name.replace("_", " "))
        bindings[name] = {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        payloads[name] = payload
    inventory = payloads["inventory"]
    if (
        inventory.get("campaign_id") != normalized_campaign
        or inventory.get("release_sha") != release_sha
        or inventory.get("inventory_stage") != "provisioned"
        or inventory.get("host_safety_mode") != execution_class
    ):
        raise LivePlanBuildError("inventory differs from live plan identity")
    production = inventory.get("production_boundaries")
    if not isinstance(production, dict):
        raise LivePlanBuildError("inventory lacks production boundaries")
    control = payloads["failover_control_config"]
    control_fields = {
        "schema", "campaign_id", "gate_group_id", "execution_class",
        "release_sha", "backend_config", "relay_credentials",
        "witness_relay_public_key_file", "journal_root",
    }
    if (
        not isinstance(control, dict)
        or set(control) != control_fields
        or control.get("schema")
        != "three-site-full-matrix-failover-control-v1"
        or control.get("campaign_id") != normalized_campaign
        or control.get("gate_group_id") != normalized_group
        or control.get("execution_class") != execution_class
        or control.get("release_sha") != release_sha
        or any(not Path(str(control[name])).is_absolute() for name in control_fields - {
            "schema", "campaign_id", "gate_group_id", "execution_class", "release_sha",
        })
    ):
        raise LivePlanBuildError("failover control configuration differs from live plan identity")
    destructive = payloads["destructive_control_config"]
    destructive_fields = {
        "schema", "campaign_id", "gate_group_id", "execution_class",
        "release_sha", "enabled", "provider_state_file", "provider_token_file",
        "audit_root",
    }
    pointers = {"provider_state_file", "provider_token_file", "audit_root"}
    destructive_enabled = execution_class == "dedicated-host-destructive"
    if (
        not isinstance(destructive, dict)
        or set(destructive) != destructive_fields
        or destructive.get("schema") != "three-site-full-matrix-destructive-control-v1"
        or destructive.get("campaign_id") != normalized_campaign
        or destructive.get("gate_group_id") != normalized_group
        or destructive.get("execution_class") != execution_class
        or destructive.get("release_sha") != release_sha
        or destructive.get("enabled") is not destructive_enabled
        or (
            destructive_enabled
            and any(not Path(str(destructive[name])).is_absolute() for name in pointers)
        )
        or (
            not destructive_enabled
            and any(destructive.get(name) != "" for name in pointers)
        )
    ):
        raise LivePlanBuildError("destructive control configuration differs from live plan identity")
    try:
        validated_roles = _validate_roles(
            role_targets,
            inventory=inventory,
            execution_class=execution_class,
        )
    except Exception as exc:
        raise LivePlanBuildError("live plan role targets violate transport policy") from exc
    return {
        "schema": PLAN_SCHEMA,
        "campaign_id": normalized_campaign,
        "gate_group_id": normalized_group,
        "execution_class": execution_class,
        "release_sha": release_sha,
        "production_forbidden": True,
        **bindings,
        "roles": validated_roles,
        "production_boundaries": production,
        "scenario_state_root": str(_owner_directory(scenario_state_root)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--gate-group-id", required=True)
    parser.add_argument("--execution-class", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--binding", action="append", default=[])
    parser.add_argument("--role-targets", required=True, type=Path)
    parser.add_argument("--scenario-state-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        role_targets, _raw = _json(args.role_targets, label="live role targets")
        plan = build_payload(
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            execution_class=args.execution_class,
            release_sha=args.release_sha,
            mappings=_mapping(args.binding),
            role_targets=role_targets,
            scenario_state_root=args.scenario_state_root,
        )
        raw = (
            json.dumps(
                plan,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode()
        write_secure_atomic_bytes(
            args.output,
            raw,
            label="Full Matrix live runtime plan",
            mode=0o600,
            max_size=16 * 1024 * 1024,
        )
        print(
            json.dumps(
                {
                    "status": "built",
                    "output": str(args.output),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "campaign_id": plan["campaign_id"],
                    "gate_group_id": plan["gate_group_id"],
                    "execution_class": plan["execution_class"],
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
