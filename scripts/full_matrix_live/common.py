"""Closed helpers shared by the live Full Matrix doer and oracle.

All mutating choices remain source-owned.  The owner-only runtime plan supplies
only exact host identity, file locations, and credentials references.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import sys
from typing import Any, Iterable


REPO_ROOT = Path.cwd().resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.three_site_full_matrix_campaign import (  # noqa: E402
    customer_actor_pair_contracts,
    scenarios_for_execution_class,
    sync_timing_policy,
)


PLAN_SCHEMA = "three-site-staging-full-matrix-live-plan-v1"
FAILOVER_CONTROL_SCHEMA = "three-site-full-matrix-failover-control-v1"
INGRESS_CONFIG_SCHEMA = "three-site-full-matrix-ingress-config-v1"
RUNNER_SCHEMA = "three-site-staging-full-matrix-live-runner-result-v1"
ORACLE_SCHEMA = "three-site-staging-full-matrix-live-oracle-result-v1"
ROLE_NAMES = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
IRAN_PAYLOAD_ROLES = frozenset({"webapp_ir", "witness"})
EXECUTION_CLASSES = frozenset({"shared-host-safe", "dedicated-host-destructive"})
OPERATIONS = frozenset({"preflight", "recovery", "scenario", "cleanup", "finalize"})
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
INGRESS_CLIENT_AUTH = re.compile(
    r"[A-Za-z][A-Za-z0-9_-]{3,31}:[A-Za-z0-9_-]{32,128}\n\Z"
)
UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,190}\Z")
ROLE_PROJECT_SUFFIX = {
    "bot_fi": "-bot-fi",
    "webapp_fi": "-webapp-fi",
    "webapp_ir": "-webapp-ir",
    "witness": "-witness",
}
ROLE_AGENT_SERVICE = {
    "bot_fi": "bot_fi_migration",
    "webapp_fi": "webapp_fi_migration",
    "webapp_ir": "webapp_ir_migration",
    "witness": "witness_migration",
}
ROLE_OBSERVER_SERVICE = {
    "bot_fi": "bot_fi_sync_observer",
    "webapp_fi": "webapp_fi_sync_observer",
    "webapp_ir": "webapp_ir_sync_observer",
}
ROLE_WORKLOAD_SERVICE = {
    "bot_fi": "bot_fi_api",
    "webapp_fi": "webapp_fi_api",
    "webapp_ir": "webapp_ir_api",
}
ROLE_DB_SERVICE = {
    "bot_fi": "bot_fi_db",
    "webapp_fi": "webapp_fi_db",
    "webapp_ir": "webapp_ir_db",
    "witness": "witness_db",
}
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}


class LiveMatrixError(RuntimeError):
    """Live driver input, execution, or evidence failed closed."""


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LiveMatrixError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_component(value: str, *, label: str) -> str:
    if SAFE_COMPONENT.fullmatch(value) is None:
        raise LiveMatrixError(f"{label} is unsafe")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or len(parsed.parts) != 1:
        raise LiveMatrixError(f"{label} is not one path component")
    return value


def safe_read(
    path: Path,
    *,
    label: str,
    owner_only: bool,
    max_size: int = 16 * 1024 * 1024,
) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise LiveMatrixError(f"{label} path is unsafe")
    try:
        before = path.stat()
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise LiveMatrixError(f"{label} cannot be opened") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_size < 2
            or opened.st_size > max_size
            or stat.S_IMODE(opened.st_mode) & (0o077 if owner_only else 0o022)
        ):
            raise LiveMatrixError(f"{label} is not an owner-controlled regular file")
        raw = os.pread(descriptor, opened.st_size + 1, 0)
        after = os.fstat(descriptor)
        if (
            len(raw) != opened.st_size
            or opened.st_dev != after.st_dev
            or opened.st_ino != after.st_ino
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
            or opened.st_ctime_ns != after.st_ctime_ns
        ):
            raise LiveMatrixError(f"{label} changed while being read")
        return raw
    finally:
        os.close(descriptor)


def secure_json(
    path: Path,
    *,
    label: str,
    owner_only: bool = True,
    max_size: int = 16 * 1024 * 1024,
) -> tuple[dict[str, Any], bytes]:
    raw = safe_read(path, label=label, owner_only=owner_only, max_size=max_size)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise LiveMatrixError(f"{label} must be a JSON object")
    return value, raw


def validate_binding(binding: Any, *, label: str) -> tuple[Path, dict[str, Any]]:
    if (
        not isinstance(binding, dict)
        or set(binding) != {"path", "sha256"}
        or not Path(str(binding["path"])).is_absolute()
        or SHA256.fullmatch(str(binding["sha256"])) is None
    ):
        raise LiveMatrixError(f"{label} binding is invalid")
    path = Path(str(binding["path"]))
    value, raw = secure_json(path, label=label)
    if sha256_bytes(raw) != binding["sha256"]:
        raise LiveMatrixError(f"{label} binding hash differs")
    return path, value


def parse_common_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("execute", "verify"))
    parser.add_argument("--operation", required=True, choices=sorted(OPERATIONS))
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--gate-group-id", required=True)
    parser.add_argument("--execution-class", required=True, choices=sorted(EXECUTION_CLASSES))
    parser.add_argument("--campaign-hash", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--activation-sha", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--runtime-plan", type=Path, required=True)
    parser.add_argument("--runner-evidence", type=Path)
    parser.add_argument("--phase")
    parser.add_argument("--scenario-id")
    parser.add_argument("--iteration", type=int)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--failed")
    args = parser.parse_args(argv)
    if args.action == "execute" and args.runner_evidence is not None:
        raise LiveMatrixError("runner evidence is oracle-only")
    if args.action == "verify" and args.runner_evidence is None:
        raise LiveMatrixError("oracle requires retained runner evidence")
    validate_common_args(args)
    return args


def bool_or_none(value: str | None) -> bool | None:
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise LiveMatrixError("failed flag is invalid")


def operation_context(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "phase": args.phase or "",
        "scenario_id": args.scenario_id or "",
        "iteration": int(args.iteration or 0),
        "attempt": int(args.attempt or 0),
        "failed": bool_or_none(args.failed),
    }


def identity(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "campaign_id": args.campaign_id,
        "gate_group_id": args.gate_group_id,
        "execution_class": args.execution_class,
        "campaign_hash": args.campaign_hash,
        "release_sha": args.release_sha,
        "activation_sha": args.activation_sha,
    }


def child_identity(args: argparse.Namespace, *, schema: str) -> dict[str, Any]:
    return {
        "schema": schema,
        "status": "passed",
        "operation": args.operation,
        "operation_id": args.operation_id,
        **identity(args),
        **operation_context(args),
        "production_touched": False,
    }


def validate_common_args(args: argparse.Namespace) -> None:
    if (
        UUID.fullmatch(str(args.operation_id)) is None
        or UUID.fullmatch(str(args.campaign_id)) is None
        or UUID.fullmatch(str(args.gate_group_id)) is None
        or SHA256.fullmatch(str(args.campaign_hash)) is None
        or SHA40.fullmatch(str(args.release_sha)) is None
        or args.activation_sha != args.release_sha
    ):
        raise LiveMatrixError("live operation identity is invalid")
    if not args.artifact_root.is_absolute() or args.artifact_root.is_symlink():
        raise LiveMatrixError("artifact root path is unsafe")
    metadata = args.artifact_root.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise LiveMatrixError("artifact root is not owner-only")
    if args.operation in {"scenario", "recovery"}:
        if (
            not args.phase
            or not args.scenario_id
            or args.iteration not in {1, 2}
            or args.attempt is None
            or args.attempt < 1
            or args.failed is not None
        ):
            raise LiveMatrixError("scenario/recovery context is invalid")
    elif args.operation == "cleanup":
        if (
            not args.phase
            or args.scenario_id is not None
            or args.iteration not in {1, 2}
            or args.attempt is not None
            or args.failed not in {"true", "false"}
        ):
            raise LiveMatrixError("cleanup context is invalid")
    elif any(
        value is not None
        for value in (
            args.phase,
            args.scenario_id,
            args.iteration,
            args.attempt,
            args.failed,
        )
    ):
        raise LiveMatrixError("campaign operation has scenario context")


def verify_clean_release(repo_root: Path, release_sha: str) -> dict[str, Any]:
    if repo_root != Path.cwd().resolve():
        raise LiveMatrixError("live driver repository root changed")
    commands = (
        ("head", ["git", "-C", str(repo_root), "rev-parse", "HEAD"]),
        (
            "tracked",
            [
                "git",
                "-C",
                str(repo_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
        ),
    )
    values: dict[str, str] = {}
    for name, command in commands:
        result = subprocess.run(
            command,
            cwd=repo_root,
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0 or result.stderr:
            raise LiveMatrixError("release Git identity cannot be verified")
        values[name] = result.stdout.strip()
    if values["head"] != release_sha or values["tracked"]:
        raise LiveMatrixError("live driver release checkout is dirty or differs")
    return {
        "head": values["head"],
        "clean": True,
        "repo_root": str(repo_root),
    }


def _validate_roles(
    roles: Any,
    *,
    inventory: dict[str, Any],
    execution_class: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(roles, dict) or set(roles) != set(ROLE_NAMES):
        raise LiveMatrixError("live plan role targets are incomplete")
    inventory_roles = {
        str(item.get("role")): item
        for item in inventory.get("roles", [])
        if isinstance(item, dict)
    }
    if set(inventory_roles) != set(ROLE_NAMES):
        raise LiveMatrixError("inventory role identities are incomplete")
    result: dict[str, dict[str, Any]] = {}
    fields = {
        "transport",
        "host_ip",
        "ssh_port",
        "ssh_user",
        "ssh_identity_file",
        "ssh_known_hosts_file",
        "repo_root",
        "compose_file",
        "env_file",
        "project_name",
        "storage_root",
        "payload_transport",
        "command_prefix",
        "agent_config",
    }
    for role in ROLE_NAMES:
        value = roles[role]
        inventory_role = inventory_roles[role]
        expected_transport = (
            "local"
            if role == "bot_fi"
            else "object-storage-agent"
            if role == "webapp_ir"
            else "ssh"
        )
        expected_payload = (
            "object-storage"
            if role in IRAN_PAYLOAD_ROLES
            else "local"
            if role == "bot_fi"
            else "direct-finland"
        )
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value.get("transport") != expected_transport
            or value.get("host_ip") != inventory_role.get("host_ip")
            or value.get("payload_transport") != expected_payload
            or value.get("storage_root") != inventory_role.get("storage_root")
            or not str(value.get("project_name") or "").endswith(ROLE_PROJECT_SUFFIX[role])
            or value.get("command_prefix")
            not in ([], ["/usr/bin/sudo", "-n"])
            or not all(
                Path(str(value[name])).is_absolute()
                for name in ("repo_root", "compose_file", "env_file", "storage_root")
            )
        ):
            raise LiveMatrixError(f"live plan target differs for {role}")
        if expected_transport == "ssh":
            if (
                type(value.get("ssh_port")) is not int
                or not 1 <= value["ssh_port"] <= 65535
                or value.get("ssh_user") not in {"root", "ubuntu"}
                or (
                    value.get("ssh_user") == "ubuntu"
                    and value.get("command_prefix") != ["/usr/bin/sudo", "-n"]
                )
                or (
                    value.get("ssh_user") == "root"
                    and value.get("command_prefix") != []
                )
                or not all(
                    Path(str(value[name])).is_absolute()
                    for name in ("ssh_identity_file", "ssh_known_hosts_file")
                )
            ):
                raise LiveMatrixError(f"SSH target is invalid for {role}")
            safe_read(
                Path(str(value["ssh_identity_file"])),
                label=f"{role} SSH identity",
                owner_only=True,
                max_size=64 * 1024,
            )
            safe_read(
                Path(str(value["ssh_known_hosts_file"])),
                label=f"{role} known hosts",
                owner_only=True,
                max_size=1024 * 1024,
            )
            if value.get("agent_config") != "":
                raise LiveMatrixError(f"SSH target has agent material for {role}")
        elif expected_transport == "object-storage-agent":
            if (
                any(
                    value.get(name) not in {"", 0}
                    for name in (
                        "ssh_port",
                        "ssh_user",
                        "ssh_identity_file",
                        "ssh_known_hosts_file",
                    )
                )
                or value.get("command_prefix") != []
                or not Path(str(value.get("agent_config") or "")).is_absolute()
            ):
                raise LiveMatrixError(f"Object Storage agent target is invalid for {role}")
            safe_read(
                Path(str(value["agent_config"])),
                label=f"{role} Object Storage agent config",
                owner_only=True,
                max_size=1024 * 1024,
            )
        elif any(
            value.get(name) not in {"", 0}
            for name in (
                "ssh_port",
                "ssh_user",
                "ssh_identity_file",
                "ssh_known_hosts_file",
            )
        ):
            raise LiveMatrixError("local role unexpectedly has SSH material")
        elif value.get("command_prefix") != []:
            raise LiveMatrixError("local role unexpectedly has a privilege prefix")
        elif value.get("agent_config") != "":
            raise LiveMatrixError("local role unexpectedly has Object Storage agent material")
        result[role] = dict(value)
    if inventory.get("host_safety_mode") != execution_class:
        raise LiveMatrixError("inventory host safety mode differs from campaign")
    return result


def load_plan(args: argparse.Namespace) -> dict[str, Any]:
    value, raw = secure_json(args.runtime_plan, label="live runtime plan")
    fields = {
        "schema",
        "campaign_id",
        "gate_group_id",
        "execution_class",
        "release_sha",
        "production_forbidden",
        "inventory",
        "inventory_approval",
        "human_approval_policy",
        "migration_plan",
        "migration_approval",
        "global_commit",
        "campaign_bundle",
        "queue_activation_transition",
        "ingress_config",
        "roles",
        "object_storage_transport",
        "convergence_config",
        "sync_timing_config",
        "failover_schedule",
        "failover_control_config",
        "destructive_control_config",
        "production_boundaries",
        "scenario_state_root",
    }
    if (
        set(value) != fields
        or value.get("schema") != PLAN_SCHEMA
        or value.get("campaign_id") != args.campaign_id
        or value.get("gate_group_id") != args.gate_group_id
        or value.get("execution_class") != args.execution_class
        or value.get("release_sha") != args.release_sha
        or value.get("production_forbidden") is not True
    ):
        raise LiveMatrixError("live runtime plan identity/schema is invalid")
    bindings: dict[str, dict[str, Any]] = {}
    for name in (
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
    ):
        path, payload = validate_binding(value[name], label=name.replace("_", " "))
        bindings[name] = {
            "path": str(path),
            "sha256": value[name]["sha256"],
            "payload": payload,
        }
    inventory = bindings["inventory"]["payload"]
    if (
        inventory.get("campaign_id") != args.campaign_id
        or inventory.get("release_sha") != args.release_sha
        or inventory.get("inventory_stage") != "provisioned"
    ):
        raise LiveMatrixError("live plan inventory differs from campaign")
    ingress = bindings["ingress_config"]["payload"]
    ingress_fields = {
        "schema",
        "release_sha",
        "public_host",
        "public_url",
        "expected_active_origin",
        "client_auth_file",
        "client_auth_sha256",
    }
    if (
        not isinstance(ingress, dict)
        or set(ingress) != ingress_fields
        or ingress.get("schema") != INGRESS_CONFIG_SCHEMA
        or ingress.get("release_sha") != args.release_sha
        or ingress.get("public_host") != "app.gold-trading.ir"
        or ingress.get("public_url")
        != "https://app.gold-trading.ir/health/origin-ready?require_global_convergence=true"
        or ingress.get("expected_active_origin") != "webapp_fi"
        or SHA256.fullmatch(str(ingress.get("client_auth_sha256") or "")) is None
    ):
        raise LiveMatrixError("ingress probe configuration is invalid")
    client_auth_path = Path(str(ingress["client_auth_file"]))
    client_auth = safe_read(
        client_auth_path,
        label="Full Matrix ingress Basic Auth client material",
        owner_only=True,
        max_size=16 * 1024,
    )
    try:
        client_auth_text = client_auth.decode("ascii")
    except UnicodeDecodeError as exc:
        raise LiveMatrixError("ingress Basic Auth client material is not ASCII") from exc
    if (
        INGRESS_CLIENT_AUTH.fullmatch(client_auth_text) is None
        or sha256_bytes(client_auth) != ingress["client_auth_sha256"]
    ):
        raise LiveMatrixError("ingress Basic Auth client material differs")
    from core.dr_full_matrix_failover_schedule import (
        FullMatrixFailoverScheduleError,
        validate_schedule,
    )

    try:
        validate_schedule(
            bindings["failover_schedule"]["payload"],
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            execution_class=args.execution_class,
            release_sha=args.release_sha,
            repetitions=2,
        )
    except FullMatrixFailoverScheduleError as exc:
        raise LiveMatrixError("live plan failover schedule is invalid") from exc
    failover_control = bindings["failover_control_config"]["payload"]
    failover_control_fields = {
        "schema", "campaign_id", "gate_group_id", "execution_class",
        "release_sha", "backend_config", "relay_credentials",
        "witness_relay_public_key_file", "journal_root",
    }
    pointer_fields = failover_control_fields - {
        "schema", "campaign_id", "gate_group_id", "execution_class", "release_sha",
    }
    if (
        not isinstance(failover_control, dict)
        or set(failover_control) != failover_control_fields
        or failover_control.get("schema") != FAILOVER_CONTROL_SCHEMA
        or failover_control.get("campaign_id") != args.campaign_id
        or failover_control.get("gate_group_id") != args.gate_group_id
        or failover_control.get("execution_class") != args.execution_class
        or failover_control.get("release_sha") != args.release_sha
        or any(
            not Path(str(failover_control[name])).is_absolute()
            for name in pointer_fields
        )
    ):
        raise LiveMatrixError("live failover control configuration is invalid")
    destructive_control = bindings["destructive_control_config"]["payload"]
    destructive_fields = {
        "schema", "campaign_id", "gate_group_id", "execution_class",
        "release_sha", "enabled", "provider_state_file", "provider_token_file",
        "audit_root",
    }
    destructive_pointers = {"provider_state_file", "provider_token_file", "audit_root"}
    destructive_enabled = args.execution_class == "dedicated-host-destructive"
    if (
        not isinstance(destructive_control, dict)
        or set(destructive_control) != destructive_fields
        or destructive_control.get("schema")
        != "three-site-full-matrix-destructive-control-v1"
        or destructive_control.get("campaign_id") != args.campaign_id
        or destructive_control.get("gate_group_id") != args.gate_group_id
        or destructive_control.get("execution_class") != args.execution_class
        or destructive_control.get("release_sha") != args.release_sha
        or destructive_control.get("enabled") is not destructive_enabled
        or (
            destructive_enabled
            and any(
                not Path(str(destructive_control[name])).is_absolute()
                for name in destructive_pointers
            )
        )
        or (
            not destructive_enabled
            and any(destructive_control.get(name) != "" for name in destructive_pointers)
        )
    ):
        raise LiveMatrixError("live destructive control configuration is invalid")
    roles = _validate_roles(
        value["roles"],
        inventory=inventory,
        execution_class=args.execution_class,
    )
    state_root = Path(str(value["scenario_state_root"]))
    if not state_root.is_absolute() or state_root.is_symlink():
        raise LiveMatrixError("scenario state root path is unsafe")
    state_metadata = state_root.stat()
    if (
        not stat.S_ISDIR(state_metadata.st_mode)
        or state_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(state_metadata.st_mode) & 0o077
    ):
        raise LiveMatrixError("scenario state root is not owner-only")
    production = value["production_boundaries"]
    inventory_production = inventory.get("production_boundaries")
    if production != inventory_production or not isinstance(production, dict):
        raise LiveMatrixError("production boundaries differ from signed inventory")
    production_hosts = production.get("host_ips")
    production_buckets = production.get("buckets")
    object_storage = inventory.get("object_storage")
    if (
        not isinstance(production_hosts, list)
        or not isinstance(production_buckets, list)
        or not isinstance(object_storage, dict)
        or any(role["host_ip"] in production_hosts for role in roles.values())
        or object_storage.get("bucket") in production_buckets
    ):
        raise LiveMatrixError("live plan overlaps a production host or bucket")
    return {
        **value,
        "_sha256": sha256_bytes(raw),
        "_bindings": bindings,
        "_roles": roles,
        "_inventory": inventory,
        "_ingress": ingress,
        "_failover_control": dict(failover_control),
        "_state_root": state_root,
    }


def _remote_prefix(role: dict[str, Any]) -> list[str]:
    if role["transport"] == "local":
        return []
    if role["transport"] != "ssh":
        raise LiveMatrixError("role does not expose a direct command transport")
    return [
        "/usr/bin/ssh",
        "-i",
        role["ssh_identity_file"],
        "-p",
        str(role["ssh_port"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={role['ssh_known_hosts_file']}",
        "-o",
        "ConnectTimeout=10",
        f"{role['ssh_user']}@{role['host_ip']}",
    ]


def run_role_command(
    role_name: str,
    role: dict[str, Any],
    command: list[str],
    *,
    timeout: int,
    allow_stderr: bool = False,
) -> dict[str, Any]:
    if not command or any(not isinstance(item, str) or "\x00" in item for item in command):
        raise LiveMatrixError("role command argv is invalid")
    if role["transport"] == "object-storage-agent":
        raise LiveMatrixError("Object Storage role rejects direct command execution")
    prefixed = [*role["command_prefix"], *command]
    if role["transport"] == "local":
        argv = prefixed
    else:
        argv = [*_remote_prefix(role), shlex.join(prefixed)]
    try:
        result = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LiveMatrixError(f"{role_name} role command failed closed") from exc
    if (
        result.returncode != 0
        or (result.stderr and not allow_stderr)
        or len(result.stdout) > 16 * 1024 * 1024
        or len(result.stderr) > 1024 * 1024
    ):
        raise LiveMatrixError(f"{role_name} role command returned a failure")
    return {
        "role": role_name,
        "returncode": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="strict"),
        "stderr": result.stderr.decode("utf-8", errors="strict"),
    }


def run_role_agent_operation(
    role_name: str,
    role: dict[str, Any],
    *,
    operation: str,
    context: dict[str, Any],
    attempt: int,
    timeout: int,
) -> dict[str, Any]:
    if role_name != "webapp_ir" or role.get("transport") != "object-storage-agent":
        raise LiveMatrixError("role is not the pinned Object Storage agent target")
    from scripts.full_matrix_live.object_storage_controller import (
        ObjectStorageControllerError,
        dispatch,
    )

    try:
        result = dispatch(
            Path(str(role["agent_config"])),
            operation=operation,
            context=context,
            attempt=attempt,
            timeout_seconds=timeout,
        )
    except ObjectStorageControllerError as exc:
        raise LiveMatrixError("Object Storage role operation failed closed") from exc
    if (
        not isinstance(result, dict)
        or result.get("status") != "passed"
        or result.get("role") != role_name
        or result.get("production_touched") is not False
    ):
        raise LiveMatrixError("Object Storage role result is invalid")
    return result


def run_compose_role_service(
    role_name: str,
    role: dict[str, Any],
    *,
    service: str,
    command: list[str],
    timeout: int,
) -> dict[str, Any]:
    allowed = {
        ROLE_AGENT_SERVICE[role_name],
        *(
            {ROLE_WORKLOAD_SERVICE[role_name]}
            if role_name in ROLE_WORKLOAD_SERVICE
            else set()
        ),
        *(
            {ROLE_OBSERVER_SERVICE[role_name]}
            if role_name in ROLE_OBSERVER_SERVICE
            else set()
        ),
    }
    if service not in allowed:
        raise LiveMatrixError("role service is outside the closed probe allowlist")
    if role["transport"] == "object-storage-agent":
        expected_prefix = [
            "/app/scripts/full_matrix_live/site_probe.py",
            "--operation",
        ]
        if (
            command[:2] != expected_prefix
            or len(command) != 3
            or command[2]
            not in {
                "migration_state",
                "observer_privileges",
                "convergence_state",
                "secret_boundary_state",
                "writer_lease_state",
            }
        ):
            raise LiveMatrixError(
                "Object Storage role accepts only a pinned semantic site probe"
            )
        observer_service = ROLE_OBSERVER_SERVICE.get(role_name)
        observer = service == observer_service
        operation = "scenario_observe" if observer else "scenario_execute"
        control = run_role_agent_operation(
            role_name,
            role,
            operation=operation,
            context={
                "probe": command[2],
                "service_class": "observer" if observer else "migration",
            },
            attempt=1,
            timeout=timeout,
        )
        envelope = control.get("result")
        site_result = envelope.get("result") if isinstance(envelope, dict) else None
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema")
            != "three-site-full-matrix-site-agent-result-v1"
            or envelope.get("status") != "passed"
            or envelope.get("role") != role_name
            or envelope.get("operation") != operation
            or not isinstance(site_result, dict)
            or site_result.get("status") != "passed"
            or not isinstance(site_result.get("probe_payload"), dict)
        ):
            raise LiveMatrixError("Object Storage site probe did not pass")
        return {
            "role": role_name,
            "returncode": 0,
            "stdout": json.dumps(
                site_result["probe_payload"],
                sort_keys=True,
                separators=(",", ":"),
            ),
            "stderr": "",
        }
    return run_role_command(
        role_name,
        role,
        [
            "/usr/bin/docker",
            "compose",
            "--env-file",
            role["env_file"],
            "-f",
            role["compose_file"],
            "run",
            "--rm",
            "--no-deps",
            "-T",
            service,
            "python",
            "-I",
            "-B",
            *command,
        ],
        timeout=timeout,
        allow_stderr=True,
    )


def run_compose_db_command(
    role_name: str,
    role: dict[str, Any],
    command: list[str],
    *,
    timeout: int,
) -> dict[str, Any]:
    if role_name not in ROLE_DB_SERVICE:
        raise LiveMatrixError("role has no closed database service")
    return run_role_command(
        role_name,
        role,
        [
            "/usr/bin/docker",
            "compose",
            "--env-file",
            role["env_file"],
            "-f",
            role["compose_file"],
            "exec",
            "-T",
            ROLE_DB_SERVICE[role_name],
            *command,
        ],
        timeout=timeout,
        allow_stderr=True,
    )


def collect_host_snapshot(role_name: str, role: dict[str, Any], release_sha: str) -> dict[str, Any]:
    if role["transport"] == "object-storage-agent":
        control = run_role_agent_operation(
            role_name,
            role,
            operation="host_snapshot",
            context={
                "compose_file": role["compose_file"],
                "env_file": role["env_file"],
                "project_name": role["project_name"],
                "storage_root": role["storage_root"],
            },
            attempt=1,
            timeout=180,
        )
        envelope = control.get("result")
        payload = envelope.get("result") if isinstance(envelope, dict) else None
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema")
            != "three-site-full-matrix-site-agent-result-v1"
            or envelope.get("status") != "passed"
            or envelope.get("role") != role_name
            or envelope.get("release_sha") != release_sha
            or not isinstance(payload, dict)
            or payload.get("status") != "passed"
        ):
            raise LiveMatrixError(f"{role_name} agent host snapshot did not pass")
        return payload
    script = (
        "import hashlib,json,pathlib,subprocess,sys;"
        "repo=pathlib.Path(sys.argv[1]);compose=pathlib.Path(sys.argv[2]);"
        "env=pathlib.Path(sys.argv[3]);project=sys.argv[4];release=sys.argv[5];"
        "run=lambda a:subprocess.run(a,stdin=subprocess.DEVNULL,capture_output=True,"
        "text=True,check=False,timeout=45);"
        "head=run(['git','-C',str(repo),'rev-parse','HEAD']);"
        "dirty=run(['git','-C',str(repo),'status','--porcelain=v1','--untracked-files=all']);"
        "ps=run(['docker','compose','--env-file',str(env),'-f',str(compose),'ps',"
        "'--format','json']);"
        "fc=run(['docker','ps','-aq','--filter','label=trading-bot.full-matrix.fault=true']);"
        "fn=run(['docker','network','ls','-q','--filter','label=trading-bot.full-matrix.fault=true']);"
        "mount=run(['findmnt','-J','-T',sys.argv[6],'-o','TARGET,SOURCE,FSTYPE,UUID,AVAIL,SIZE']);"
        "mid=pathlib.Path('/etc/machine-id').read_text().strip();"
        "files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (compose,env)};"
        "ok=(head.returncode==dirty.returncode==ps.returncode==fc.returncode=="
        "fn.returncode==mount.returncode==0 and "
        "head.stdout.strip()==release and not dirty.stdout.strip());"
        "print(json.dumps({'status':'passed' if ok else 'failed','release_sha':head.stdout.strip(),"
        "'clean':not bool(dirty.stdout.strip()),'project':project,'machine_id':mid,"
        "'files':files,'compose_ps_sha256':hashlib.sha256(ps.stdout.encode()).hexdigest(),"
        "'compose_ps_bytes':len(ps.stdout.encode()),"
        "'managed_fault_container_count':len([x for x in fc.stdout.splitlines() if x.strip()]),"
        "'managed_fault_network_count':len([x for x in fn.stdout.splitlines() if x.strip()]),"
        "'mount':json.loads(mount.stdout) if mount.returncode==0 else {},"
        "'failures':[] if ok else ['host_identity_or_runtime']} ,sort_keys=True))"
    )
    result = run_role_command(
        role_name,
        role,
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            script,
            role["repo_root"],
            role["compose_file"],
            role["env_file"],
            role["project_name"],
            release_sha,
            role["storage_root"],
        ],
        timeout=90,
    )
    try:
        payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
    except json.JSONDecodeError as exc:
        raise LiveMatrixError(f"{role_name} host snapshot is invalid") from exc
    if not isinstance(payload, dict) or payload.get("status") != "passed":
        raise LiveMatrixError(f"{role_name} host snapshot did not pass")
    return payload


def collect_all_host_snapshots(plan: dict[str, Any], release_sha: str) -> dict[str, Any]:
    return {
        role: collect_host_snapshot(role, plan["_roles"][role], release_sha)
        for role in ROLE_NAMES
    }


def validate_catalog(args: argparse.Namespace) -> dict[str, list[str]]:
    catalog = {
        phase: list(scenarios)
        for phase, scenarios in scenarios_for_execution_class(args.execution_class).items()
    }
    if args.operation in {"scenario", "recovery"} and (
        args.phase not in catalog or args.scenario_id not in catalog[args.phase]
    ):
        raise LiveMatrixError("scenario is outside the source-owned catalog")
    return catalog


def scenario_contract(args: argparse.Namespace) -> dict[str, Any]:
    if args.operation != "scenario" or not args.scenario_id:
        raise LiveMatrixError("scenario contract requested outside scenario")
    customer = customer_actor_pair_contracts(args.scenario_id)
    timing = sync_timing_policy(args.scenario_id)
    return {
        "phase": args.phase,
        "scenario_id": args.scenario_id,
        "oracle_id": f"{args.phase}.{args.scenario_id}.v1",
        "customer_contracts": customer,
        "sync_timing_policy": timing,
    }


def retained_runner(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    value, _raw = secure_json(path, label="retained live runner evidence")
    required = {
        "schema": RUNNER_SCHEMA,
        "status": "passed",
        "operation": args.operation,
        "operation_id": args.operation_id,
        **identity(args),
        **operation_context(args),
        "production_touched": False,
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise LiveMatrixError("retained runner evidence identity differs")
    return value


def operation_assertion_names(operation: str) -> tuple[str, ...]:
    names = {
        "preflight": (
            "campaign_identity_bound",
            "prerequisites_verified",
            "topology_ready",
            "production_boundary",
        ),
        "recovery": (
            "faults_removed",
            "writer_state_safe",
            "residue_zero",
            "production_boundary",
        ),
        "cleanup": (
            "faults_removed",
            "writer_state_safe",
            "residue_zero",
            "production_boundary",
        ),
        "finalize": (
            "all_faults_removed",
            "writer_state_safe",
            "residue_zero",
            "production_boundary",
        ),
    }.get(operation)
    if names is None:
        raise LiveMatrixError("operation has no operation assertion set")
    return names


def hash_summary(value: Any) -> str:
    return sha256_bytes(json_bytes(value))


def require_keys(value: dict[str, Any], keys: Iterable[str], *, label: str) -> None:
    missing = set(keys) - set(value)
    if missing:
        raise LiveMatrixError(f"{label} is missing fields: {sorted(missing)}")
