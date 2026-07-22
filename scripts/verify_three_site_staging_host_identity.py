#!/usr/bin/env python3
"""Attest one physical staging host against its signed role inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from scripts.render_three_site_staging_role_compose import _atomic_write, parse_env_values
from scripts.verify_three_site_staging_inventory import (
    ROLE_VOLUME_LOGICAL_NAMES,
    load_inventory,
    verify_signed_inventory,
)
from scripts.verify_three_site_staging_role_bundle import (
    PHYSICAL_SITE,
    _verify_bundle_source,
    verify_role_bundle,
)


GIT = "/usr/bin/git"
DOCKER = "/usr/bin/docker"
IP = "/usr/sbin/ip"
TIMEDATECTL = "/usr/bin/timedatectl"
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}
ROLE_DB = {
    "bot-fi": ("bot_fi_db", "BOT_FI_POSTGRES_USER", "BOT_FI_POSTGRES_DB"),
    "webapp-fi": (
        "webapp_fi_db", "WEBAPP_FI_POSTGRES_USER", "WEBAPP_FI_POSTGRES_DB",
    ),
    "webapp-ir": (
        "webapp_ir_db", "WEBAPP_IR_POSTGRES_USER", "WEBAPP_IR_POSTGRES_DB",
    ),
    "witness": ("witness_db", "WITNESS_POSTGRES_USER", "WITNESS_POSTGRES_DB"),
}
ROLE_VOLUME_FIELDS = {
    role.replace("_", "-"): dict(fields)
    for role, fields in ROLE_VOLUME_LOGICAL_NAMES.items()
}


class HostIdentityError(RuntimeError):
    pass


def _run(arguments: list[str], *, timeout: int = 20) -> str:
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostIdentityError(f"host identity command unavailable: {arguments[0]}") from exc
    if result.returncode != 0:
        raise HostIdentityError(f"host identity command failed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _git(repo: Path, *arguments: str) -> str:
    return _run(
        [
            GIT,
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-C", str(repo),
            *arguments,
        ]
    )


def _volume_identity(expected_name: str) -> str | None:
    result = subprocess.run(
        [DOCKER, "volume", "inspect", "--format", "{{.Name}}", expected_name],
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=20,
        env=SAFE_ENV,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if value != expected_name:
        raise HostIdentityError("Docker returned a different volume identity")
    return value


def collect_host_snapshot(
    *,
    role: str,
    repo: Path,
    role_compose: Path,
    env_file: Path,
    role_inventory: dict[str, Any],
    stage: str,
) -> dict[str, Any]:
    try:
        machine_id = Path("/etc/machine-id").read_text(encoding="utf-8").strip().lower()
    except OSError as exc:
        raise HostIdentityError("machine-id is unavailable") from exc
    if not re.fullmatch(r"[0-9a-f]{32}", machine_id):
        raise HostIdentityError("machine-id is malformed")
    addresses_payload = json.loads(_run([IP, "-j", "-4", "addr", "show"]))
    addresses = sorted(
        {
            str(item["local"])
            for interface in addresses_payload
            for item in interface.get("addr_info", [])
            if item.get("family") == "inet" and item.get("local")
        }
    )
    docker_id = _run([DOCKER, "info", "--format", "{{.ID}}"]).strip()
    timezone = _run([TIMEDATECTL, "show", "-p", "Timezone", "--value"])
    ntp_value = _run(
        [TIMEDATECTL, "show", "-p", "NTPSynchronized", "--value"]
    ).lower()
    clock_tool = next(
        (
            Path(binary).name
            for name in ("chronyc", "ntpq")
            if (binary := shutil.which(name, path=SAFE_ENV["PATH"])) is not None
        ),
        None,
    )
    head_before = _git(repo, "rev-parse", "HEAD")
    clean = not bool(_git(repo, "status", "--porcelain=v1", "--untracked-files=all"))
    head_after = _git(repo, "rev-parse", "HEAD")
    if head_before != head_after:
        raise HostIdentityError("repository identity changed during host attestation")

    volumes = {
        field: _volume_identity(str(role_inventory[field]))
        for field in ROLE_VOLUME_FIELDS[role]
    }
    postgres_system_id: str | None = None
    if stage in {"measure-provisioned", "provisioned"}:
        env = parse_env_values(env_file.read_text(encoding="utf-8"))
        service, user_key, database_key = ROLE_DB[role]
        postgres_system_id = _run(
            [
                DOCKER, "compose", "-f", str(role_compose),
                "--env-file", str(env_file),
                "exec", "-T", service,
                "psql", "-U", env[user_key], "-d", env[database_key],
                "-Atqc", "SELECT system_identifier FROM pg_control_system()",
            ],
            timeout=30,
        )
    return {
        "schema": "three-site-staging-host-snapshot-v1",
        "role": role,
        "stage": stage,
        "release_sha": head_before,
        "worktree_clean": clean,
        "machine_id": machine_id,
        "docker_daemon_id": docker_id,
        "ipv4_addresses": addresses,
        "timezone": timezone,
        "ntp_synchronized": ntp_value == "yes",
        "clock_measurement_tool": clock_tool,
        "volumes": volumes,
        "postgres_system_id": postgres_system_id,
    }


def verify_host_snapshot(
    snapshot: dict[str, Any],
    *,
    role: str,
    role_inventory: dict[str, Any],
    release_sha: str,
    stage: str,
) -> dict[str, Any]:
    expected_fields = {
        "schema", "role", "stage", "release_sha", "worktree_clean",
        "machine_id", "docker_daemon_id", "ipv4_addresses", "timezone",
        "ntp_synchronized", "clock_measurement_tool", "volumes",
        "postgres_system_id",
    }
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != expected_fields
        or snapshot["schema"] != "three-site-staging-host-snapshot-v1"
        or snapshot["role"] != role
        or snapshot["stage"] != stage
    ):
        raise HostIdentityError("host snapshot fields/schema/role are invalid")
    expected_inventory_stage = (
        "planned" if stage in {"fresh-preflight", "measure-provisioned"}
        else "provisioned"
    )
    if (
        expected_inventory_stage == "planned"
        and role_inventory.get("postgres_system_id") is not None
    ):
        raise HostIdentityError("fresh host requires a planned inventory")
    if role_inventory.get("postgres_system_id") is None and expected_inventory_stage == "provisioned":
        raise HostIdentityError("provisioned host requires a measured PostgreSQL identity")
    if (
        snapshot["release_sha"] != release_sha
        or snapshot["worktree_clean"] is not True
        or snapshot["machine_id"] != str(role_inventory["machine_id"]).lower()
        or snapshot["docker_daemon_id"] != role_inventory["docker_daemon_id"]
        or role_inventory["host_ip"] not in snapshot["ipv4_addresses"]
        or snapshot["timezone"] != "UTC"
        or snapshot["ntp_synchronized"] is not True
        or (
            role in {"bot-fi", "webapp-fi", "webapp-ir"}
            and snapshot["clock_measurement_tool"] not in {"chronyc", "ntpq"}
        )
        or snapshot["clock_measurement_tool"] not in {None, "chronyc", "ntpq"}
    ):
        raise HostIdentityError("live host identity differs from signed inventory/SHA/time policy")
    volumes = snapshot["volumes"]
    expected_volume_fields = set(ROLE_VOLUME_FIELDS[role])
    if not isinstance(volumes, dict) or set(volumes) != expected_volume_fields:
        raise HostIdentityError("host volume snapshot is incomplete")
    if stage == "fresh-preflight":
        if any(value is not None for value in volumes.values()):
            raise HostIdentityError("fresh staging role already has a declared mutable volume")
        if snapshot["postgres_system_id"] is not None:
            raise HostIdentityError("fresh staging role unexpectedly has a PostgreSQL identity")
    elif stage in {"measure-provisioned", "provisioned"}:
        if any(
            volumes[field] != role_inventory[field]
            for field in expected_volume_fields
        ):
            raise HostIdentityError("provisioned Docker volume differs from signed inventory")
        postgres_system_id = str(snapshot["postgres_system_id"] or "")
        if not re.fullmatch(r"[0-9]{10,20}", postgres_system_id):
            raise HostIdentityError("measured PostgreSQL system identifier is malformed")
        if stage == "provisioned" and postgres_system_id != str(
            role_inventory["postgres_system_id"]
        ):
            raise HostIdentityError("PostgreSQL system identifier differs from signed inventory")
    else:
        raise HostIdentityError("unsupported host attestation stage")
    digest = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "status": "verified",
        "role": role,
        "stage": stage,
        "release_sha": release_sha,
        "host_snapshot_sha256": digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(PHYSICAL_SITE), required=True)
    parser.add_argument(
        "--stage",
        choices=("fresh-preflight", "measure-provisioned", "provisioned"),
        required=True,
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--role-compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--signer-policy", type=Path, required=True)
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        help="Optional owner-only canonical snapshot used to finalize provisioned inventory.",
    )
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.inventory)
        approval = load_inventory(args.approval)
        signer_policy = load_inventory(args.signer_policy)
        inventory_result = verify_signed_inventory(
            inventory,
            approval=approval,
            signer_policy=signer_policy,
            host_destructive=None,
        )
        expected_inventory_stage = (
            "planned"
            if args.stage in {"fresh-preflight", "measure-provisioned"}
            else "provisioned"
        )
        if inventory_result["inventory_stage"] != expected_inventory_stage:
            raise HostIdentityError(
                f"{args.stage} requires a signed {expected_inventory_stage} inventory"
            )
        role_compose_bytes = _verify_bundle_source(
            args.role_compose, expected_mode=0o640
        )
        env_bytes = _verify_bundle_source(args.env_file, expected_mode=0o600)
        env_values = parse_env_values(env_bytes.decode("utf-8"))
        if Path(env_values.get("STAGING_SOURCE_ROOT", "")).resolve() != args.repo.resolve():
            raise HostIdentityError("role source root differs from the attested Git repository")
        verify_role_bundle(
            role=args.role,
            canonical_compose=yaml.safe_load(
                args.canonical_compose.read_text(encoding="utf-8")
            ),
            role_compose_bytes=role_compose_bytes,
            env_bytes=env_bytes,
            inventory=inventory,
            approval=approval,
            signer_policy=signer_policy,
            verify_files=True,
            required_inventory_stage=expected_inventory_stage,
        )
        role_inventory = next(
            item for item in inventory["roles"]
            if item["role"] == PHYSICAL_SITE[args.role]
        )
        snapshot = collect_host_snapshot(
            role=args.role,
            repo=args.repo,
            role_compose=args.role_compose,
            env_file=args.env_file,
            role_inventory=role_inventory,
            stage=args.stage,
        )
        result = verify_host_snapshot(
            snapshot,
            role=args.role,
            role_inventory=role_inventory,
            release_sha=inventory_result["release_sha"],
            stage=args.stage,
        )
        if args.snapshot_output is not None:
            _atomic_write(
                args.snapshot_output,
                (
                    json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode(),
                mode=0o600,
            )
            result["snapshot_output"] = str(args.snapshot_output)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
