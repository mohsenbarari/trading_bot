#!/usr/bin/env python3
"""Closed, release-owned operation surface for one Full Matrix role host."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any


SCHEMA = "three-site-full-matrix-site-agent-result-v1"
REQUEST_SCHEMA = "three-site-full-matrix-object-storage-request-v1"
ROLE = "webapp_ir"
REPO_ROOT = Path("/srv/trading-bot-three-site/current")
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}


class SiteAgentError(RuntimeError):
    """The closed site operation could not be executed or proved."""


# This is the same complete-prefix bound enforced by timing_probe before it
# appends the longest route/idempotency suffix.  The Object Storage edge must
# reject oversize values before it can start a local workload container.
_TIMING_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,23}$")
_CUSTOMER_PREFIX = re.compile(r"^FMX_[A-Za-z0-9_]{12,96}$")
_SESSION_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FAILOVER_SITE_ACTIONS = frozenset(
    {
        "source-fenced",
        "source-drained-and-fenced",
        "source-connections-drained",
        "target-ready",
        "target-term-attested",
        "target-term-acquired",
        "safe-fence",
    }
)
_FAILOVER_COMPOSE = Path("/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml")
_FAILOVER_ENV = Path("/root/secure-envs/full-matrix/roles/webapp-ir.env")
_FAILOVER_POLICY = Path(
    "/etc/trading-bot/security/human-approval/human-approval-policy.json"
)
_RECOVERY_FAULT_STATE = Path(
    "/root/secure-envs/full-matrix/recovery-delivery-fault.json"
)
_RECOVERY_FAULT_ID = re.compile(r"^FMX_[A-Za-z0-9_]{12,96}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,190}$")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SiteAgentError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_request(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink():
        raise SiteAgentError("request file path is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 2 <= metadata.st_size <= 128 * 1024
        ):
            raise SiteAgentError("request file is not owner-only")
        raw = os.pread(descriptor, metadata.st_size + 1, 0)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise SiteAgentError("request file changed while reading")
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("request file is invalid JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != REQUEST_SCHEMA:
        raise SiteAgentError("request schema is invalid")
    return value


def _run(
    argv: list[str],
    *,
    timeout: int = 90,
    allow_stderr: bool = False,
) -> str:
    result = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        env=SAFE_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if (
        result.returncode != 0
        or (result.stderr and not allow_stderr)
        or len(result.stdout.encode()) > 16 * 1024 * 1024
        or len(result.stderr.encode()) > 1024 * 1024
    ):
        raise SiteAgentError("fixed site inspection command failed")
    return result.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_release(request: dict[str, Any]) -> str:
    release = _run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"]).strip()
    if release != request["release_sha"]:
        raise SiteAgentError("site release differs from request")
    dirty = _run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ]
    ).strip()
    if dirty:
        raise SiteAgentError("site release checkout is dirty")
    return release


def _host_snapshot(request: dict[str, Any]) -> dict[str, Any]:
    context = request.get("context")
    expected = {
        "compose_file",
        "env_file",
        "project_name",
        "storage_root",
    }
    if not isinstance(context, dict) or set(context) != expected:
        raise SiteAgentError("host snapshot context fields are invalid")
    compose = Path(str(context["compose_file"]))
    env_file = Path(str(context["env_file"]))
    storage = Path(str(context["storage_root"]))
    project = str(context["project_name"])
    approved_base = Path("/srv/trading-bot-three-site")
    if (
        compose != Path("/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml")
        or env_file != Path("/root/secure-envs/full-matrix/roles/webapp-ir.env")
        or storage != Path("/srv/trading-bot-three-site-staging-data")
        or not project.endswith("-webapp-ir")
        or len(project) > 190
        or REPO_ROOT.resolve().is_relative_to(approved_base.resolve()) is False
    ):
        raise SiteAgentError("host snapshot target differs from the pinned role")
    for path in (compose, env_file):
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
        ):
            raise SiteAgentError("host snapshot role file is unsafe")
    release = _verify_release(request)
    compose_ps = _run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(compose),
            "ps",
            "--format",
            "json",
        ]
    )
    fault_containers = _run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=trading-bot.full-matrix.fault=true",
        ]
    ).splitlines()
    fault_networks = _run(
        [
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            "label=trading-bot.full-matrix.fault=true",
        ]
    ).splitlines()
    mount = _run(
        [
            "findmnt",
            "-J",
            "-T",
            str(storage),
            "-o",
            "TARGET,SOURCE,FSTYPE,UUID,AVAIL,SIZE",
        ]
    )
    return {
        "status": "passed",
        "release_sha": release,
        "clean": True,
        "project": project,
        "machine_id": Path("/etc/machine-id").read_text(encoding="ascii").strip(),
        "files": {
            compose.name: _sha256(compose),
            env_file.name: _sha256(env_file),
        },
        "compose_ps_sha256": hashlib.sha256(compose_ps.encode()).hexdigest(),
        "compose_ps_bytes": len(compose_ps.encode()),
        "managed_fault_container_count": len(
            [value for value in fault_containers if value.strip()]
        ),
        "managed_fault_network_count": len(
            [value for value in fault_networks if value.strip()]
        ),
        "mount": json.loads(mount),
        "failures": [],
    }


def _scenario_probe(request: dict[str, Any], *, observer: bool) -> dict[str, Any]:
    context = request.get("context")
    expected_class = "observer" if observer else "migration"
    if (
        not isinstance(context, dict)
        or set(context) != {"probe", "service_class"}
        or context.get("service_class") != expected_class
        or context.get("probe")
        not in {
            "migration_state",
            "observer_privileges",
            "convergence_state",
            "secret_boundary_state",
            "writer_lease_state",
        }
    ):
        raise SiteAgentError("scenario probe context is invalid")
    probe = str(context["probe"])
    if (
        probe
        in {
            "observer_privileges",
            "convergence_state",
            "secret_boundary_state",
            "writer_lease_state",
        }
    ) is not observer:
        raise SiteAgentError("scenario probe privilege class is invalid")
    _verify_release(request)
    compose = Path("/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml")
    env_file = Path("/root/secure-envs/full-matrix/roles/webapp-ir.env")
    service = (
        "webapp_ir_sync_observer"
        if observer
        else "webapp_ir_migration"
    )
    raw = _run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(compose),
            "run",
            "--rm",
            "--no-deps",
            "-T",
            service,
            "python",
            "-I",
            "-B",
            "/app/scripts/full_matrix_live/site_probe.py",
            "--operation",
            probe,
        ],
        timeout=180,
        allow_stderr=True,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("scenario probe result is invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "three-site-full-matrix-site-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("operation") != probe
        or payload.get("role") != ROLE
        or not isinstance(payload.get("result"), dict)
    ):
        raise SiteAgentError("scenario probe did not pass")
    return {
        "status": "passed",
        "probe": probe,
        "service_class": expected_class,
        "probe_payload": payload,
    }


def _timing_clock(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("context") != {}:
        raise SiteAgentError("timing clock context is invalid")
    release = _verify_release(request)
    raw = _run(
        [
            "/usr/bin/python3",
            str(REPO_ROOT / "scripts" / "measure_three_site_host_clock.py"),
            "--site",
            ROLE,
            "--release-sha",
            release,
        ],
        timeout=90,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("timing clock output is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "three-site-host-clock-v1"
        or payload.get("site") != ROLE
        or payload.get("release_sha") != release
        or payload.get("synchronized") is not True
    ):
        raise SiteAgentError("timing clock did not attest the pinned role")
    return {"status": "passed", "clock": payload}


def _timing_snapshot(request: dict[str, Any]) -> dict[str, Any]:
    context = request.get("context")
    if not isinstance(context, dict) or set(context) != {"correlation_prefix", "clock"}:
        raise SiteAgentError("timing snapshot context is invalid")
    prefix = str(context["correlation_prefix"])
    clock = context["clock"]
    if _TIMING_PREFIX.fullmatch(prefix) is None or not isinstance(clock, dict):
        raise SiteAgentError("timing snapshot identity is invalid")
    release = _verify_release(request)
    if clock.get("site") != ROLE or clock.get("release_sha") != release:
        raise SiteAgentError("timing snapshot clock differs from pinned role")
    encoded_clock = base64.urlsafe_b64encode(
        json.dumps(clock, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    compose = Path("/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml")
    env_file = Path("/root/secure-envs/full-matrix/roles/webapp-ir.env")
    raw = _run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(compose),
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "webapp_ir_sync_observer",
            "python",
            "-I",
            "-B",
            "/app/scripts/collect_three_site_sync_timing_snapshot.py",
            "--correlation-prefix",
            prefix,
            "--clock-evidence-base64",
            encoded_clock,
        ],
        timeout=600,
        allow_stderr=True,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("timing snapshot output is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "three-site-staging-sync-site-snapshot-v1"
        or payload.get("site") != ROLE
        or payload.get("release_sha") != release
        or payload.get("correlation_prefix") != prefix
    ):
        raise SiteAgentError("timing snapshot did not attest the pinned role")
    return {"status": "passed", "snapshot": payload}


def _timing_emit(request: dict[str, Any]) -> dict[str, Any]:
    """Emit only the fixed IR-origin recovery routes through the local app."""

    context = request.get("context")
    required = {
        "fixture_prefix",
        "correlation_prefix",
        "samples_per_route",
        "target_rps",
    }
    if (
        not isinstance(context, dict)
        or set(context) != required
        or _CUSTOMER_PREFIX.fullmatch(str(context.get("fixture_prefix") or "")) is None
        or _TIMING_PREFIX.fullmatch(str(context.get("correlation_prefix") or "")) is None
        or type(context.get("samples_per_route")) is not int
        or not 1 <= int(context["samples_per_route"]) <= 500
        or type(context.get("target_rps")) not in {int, float}
        or isinstance(context.get("target_rps"), bool)
        or not 0.1 <= float(context["target_rps"]) <= 1000.0
    ):
        raise SiteAgentError("timing emit context is invalid")
    _verify_release(request)
    return {"status": "passed", "emitter": _run_ir_timing_emit(context)}


def _timing_cleanup(request: dict[str, Any]) -> dict[str, Any]:
    """Remove only the exact IR timing fixture through the pinned local app."""

    context = request.get("context")
    if (
        not isinstance(context, dict)
        or set(context) != {"fixture_prefix"}
        or _CUSTOMER_PREFIX.fullmatch(str(context.get("fixture_prefix") or "")) is None
    ):
        raise SiteAgentError("timing cleanup context is invalid")
    _verify_release(request)
    fixture_prefix = str(context["fixture_prefix"])
    raw = _run(
        [
            "docker",
            "compose",
            "--env-file",
            str(_FAILOVER_ENV),
            "-f",
            str(_FAILOVER_COMPOSE),
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "webapp_ir_api",
            "python",
            "-I",
            "-B",
            "/app/scripts/full_matrix_live/timing_probe.py",
            "--role",
            ROLE,
            "--fixture-prefix",
            fixture_prefix,
            "--cleanup-only",
        ],
        timeout=1800,
        allow_stderr=True,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("timing cleanup output is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "three-site-full-matrix-timing-emitter-v1"
        or payload.get("status") != "passed"
        or payload.get("action") != "cleanup"
        or payload.get("role") != ROLE
        or payload.get("fixture_prefix") != fixture_prefix
        or payload.get("production_touched") is not False
    ):
        raise SiteAgentError("timing cleanup did not attest the pinned IR Writer")
    return {"status": "passed", "cleanup": payload}


def _recovery_emit_context(request: dict[str, Any]) -> dict[str, Any]:
    """Validate the one closed post-reconnect IR workload shape."""

    context = request.get("context")
    required = {
        "fault_id",
        "fixture_prefix",
        "correlation_prefix",
        "samples_per_route",
        "target_rps",
    }
    if (
        not isinstance(context, dict)
        or set(context) != required
        or _RECOVERY_FAULT_ID.fullmatch(str(context.get("fault_id") or "")) is None
        or _CUSTOMER_PREFIX.fullmatch(str(context.get("fixture_prefix") or "")) is None
        or _TIMING_PREFIX.fullmatch(str(context.get("correlation_prefix") or "")) is None
        or type(context.get("samples_per_route")) is not int
        or not 1 <= int(context["samples_per_route"]) <= 500
        or type(context.get("target_rps")) not in {int, float}
        or isinstance(context.get("target_rps"), bool)
        or not 0.1 <= float(context["target_rps"]) <= 1000.0
    ):
        raise SiteAgentError("recovery resume emit context is invalid")
    return dict(context)


def _run_ir_timing_emit(context: dict[str, Any]) -> dict[str, Any]:
    """Execute the fixed IR timing probe and validate its local attestation."""

    raw = _run(
        [
            "docker",
            "compose",
            "--env-file",
            str(_FAILOVER_ENV),
            "-f",
            str(_FAILOVER_COMPOSE),
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "webapp_ir_api",
            "python",
            "-I",
            "-B",
            "/app/scripts/full_matrix_live/timing_probe.py",
            "--role",
            ROLE,
            "--fixture-prefix",
            str(context["fixture_prefix"]),
            "--correlation-prefix",
            str(context["correlation_prefix"]),
            "--samples-per-route",
            str(context["samples_per_route"]),
            "--target-rps",
            str(context["target_rps"]),
        ],
        timeout=1800,
        allow_stderr=True,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("timing emitter output is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "three-site-full-matrix-timing-emitter-v1"
        or payload.get("status") != "passed"
        or payload.get("role") != ROLE
        or payload.get("fixture_prefix") != context["fixture_prefix"]
        or payload.get("correlation_prefix") != context["correlation_prefix"]
        or payload.get("sample_count") != int(context["samples_per_route"]) * 2
        or payload.get("production_touched") is not False
        or payload.get("three_site_writer_fence") is not True
        or not isinstance(payload.get("samples"), list)
    ):
        raise SiteAgentError("timing emitter did not attest the pinned IR Writer")
    return payload


def _recovery_fault_state() -> dict[str, Any] | None:
    path = _RECOVERY_FAULT_STATE
    if not path.exists() and not path.is_symlink():
        return None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise SiteAgentError("recovery delivery fault state cannot be opened") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 2 <= metadata.st_size <= 16 * 1024
        ):
            raise SiteAgentError("recovery delivery fault state is unsafe")
        raw = os.pread(descriptor, metadata.st_size + 1, 0)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise SiteAgentError("recovery delivery fault state changed while reading")
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("recovery delivery fault state is invalid") from exc
    required = {"schema", "campaign_id", "release_sha", "fault_id", "phase"}
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema") != "three-site-full-matrix-recovery-delivery-fault-v1"
        or _SAFE_NAME.fullmatch(str(value.get("campaign_id") or "")) is None
        or _SHA40.fullmatch(str(value.get("release_sha") or "")) is None
        or _RECOVERY_FAULT_ID.fullmatch(str(value.get("fault_id") or "")) is None
        or value.get("phase") not in {"pausing", "paused"}
    ):
        raise SiteAgentError("recovery delivery fault state differs")
    return value


def _write_recovery_fault_state(value: dict[str, Any], *, replace: bool) -> None:
    path = _RECOVERY_FAULT_STATE
    parent = path.parent
    try:
        metadata = parent.lstat()
    except OSError as exc:
        raise SiteAgentError("recovery delivery fault directory is unavailable") from exc
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise SiteAgentError("recovery delivery fault directory is unsafe")
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    descriptor = os.open(temporary, flags, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise SiteAgentError("recovery delivery fault state write was incomplete")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        if not replace and (path.exists() or path.is_symlink()):
            raise SiteAgentError("recovery delivery fault state already exists")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_recovery_fault_state() -> None:
    path = _RECOVERY_FAULT_STATE
    state = _recovery_fault_state()
    if state is None:
        raise SiteAgentError("recovery delivery fault state disappeared before cleanup")
    path.unlink()


def _ir_delivery_running() -> bool:
    compose = Path("/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml")
    env_file = Path("/root/secure-envs/full-matrix/roles/webapp-ir.env")
    raw = _run(
        [
            "docker", "compose", "--env-file", str(env_file), "-f", str(compose),
            "ps", "--status", "running", "--services",
        ],
        timeout=90,
        allow_stderr=True,
    )
    services = {line.strip() for line in raw.splitlines() if line.strip()}
    if any(not re.fullmatch(r"[a-z0-9_]{3,96}", item) for item in services):
        raise SiteAgentError("recovery delivery service listing is invalid")
    return "webapp_ir_dr_delivery" in services


def _recovery_delivery_fault(request: dict[str, Any]) -> dict[str, Any]:
    """Pause/resume only IR's DR-delivery service for one recovery probe."""

    context = request.get("context")
    if (
        not isinstance(context, dict)
        or set(context) != {"action", "fault_id"}
        or context.get("action") not in {"pause", "resume"}
        or _RECOVERY_FAULT_ID.fullmatch(str(context.get("fault_id") or "")) is None
    ):
        raise SiteAgentError("recovery delivery fault context is invalid")
    release = _verify_release(request)
    action = str(context["action"])
    fault_id = str(context["fault_id"])
    compose = Path("/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml")
    env_file = Path("/root/secure-envs/full-matrix/roles/webapp-ir.env")
    state = _recovery_fault_state()
    expected = {
        "schema": "three-site-full-matrix-recovery-delivery-fault-v1",
        "campaign_id": request["campaign_id"],
        "release_sha": release,
        "fault_id": fault_id,
    }
    if action == "pause":
        if state is not None and any(
            state.get(key) != value for key, value in expected.items()
        ):
            raise SiteAgentError("another recovery delivery fault is retained")
        # Object Storage delivery is at-least-once around a controller timeout.
        # A replay of the exact signed pause must prove the retained pause, not
        # turn a completed fault into a terminal controller failure.
        if state is not None and state.get("phase") == "paused":
            if _ir_delivery_running():
                raise SiteAgentError("retained recovery fault has an unexpected running service")
            return {
                "status": "passed",
                "action": action,
                "fault_id": fault_id,
                "phase": "paused",
            }
        if state is None:
            _write_recovery_fault_state({**expected, "phase": "pausing"}, replace=False)
        _run(
            [
                "docker", "compose", "--env-file", str(env_file), "-f", str(compose),
                "stop", "--timeout", "30", "webapp_ir_dr_delivery",
            ],
            timeout=120,
            allow_stderr=True,
        )
        if _ir_delivery_running():
            raise SiteAgentError("IR delivery service remained running after pause")
        _write_recovery_fault_state({**expected, "phase": "paused"}, replace=True)
        return {"status": "passed", "action": action, "fault_id": fault_id, "phase": "paused"}
    if state is None or any(state.get(key) != value for key, value in expected.items()):
        if state is None and _ir_delivery_running():
            return {
                "status": "passed",
                "action": action,
                "fault_id": fault_id,
                "phase": "resumed",
            }
        raise SiteAgentError("recovery delivery resume does not match retained fault")
    _run(
        [
            "docker", "compose", "--env-file", str(env_file), "-f", str(compose),
            "up", "-d", "--no-deps", "webapp_ir_dr_delivery",
        ],
        timeout=180,
        allow_stderr=True,
    )
    if not _ir_delivery_running():
        raise SiteAgentError("IR delivery service did not resume")
    _remove_recovery_fault_state()
    return {"status": "passed", "action": action, "fault_id": fault_id, "phase": "resumed"}


def _recovery_delivery_resume_emit(request: dict[str, Any]) -> dict[str, Any]:
    """Resume one retained IR delivery pause and immediately issue fixed traffic.

    The service start and timing emitter share one sealed WA-IR agent operation,
    preventing a controller-side transport gap from being mistaken for live
    traffic while the retained backlog drains.
    """

    context = _recovery_emit_context(request)
    release = _verify_release(request)
    expected = {
        "schema": "three-site-full-matrix-recovery-delivery-fault-v1",
        "campaign_id": request["campaign_id"],
        "release_sha": release,
        "fault_id": str(context["fault_id"]),
    }
    state = _recovery_fault_state()
    if state is None or any(state.get(key) != value for key, value in expected.items()):
        raise SiteAgentError("recovery resume emit does not match retained fault")
    _run(
        [
            "docker", "compose", "--env-file", str(_FAILOVER_ENV), "-f",
            str(_FAILOVER_COMPOSE), "up", "-d", "--no-deps",
            "webapp_ir_dr_delivery",
        ],
        timeout=180,
        allow_stderr=True,
    )
    if not _ir_delivery_running():
        raise SiteAgentError("IR delivery service did not resume before live emit")
    payload = _run_ir_timing_emit(context)
    _remove_recovery_fault_state()
    return {
        "status": "passed",
        "fault_id": str(context["fault_id"]),
        "phase": "resumed_with_live_emit",
        "emitter": payload,
    }


def _origin_local_probe(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("context") != {}:
        raise SiteAgentError("origin local probe context is invalid")
    release = _verify_release(request)
    raw = _run(
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(REPO_ROOT / "scripts" / "full_matrix_live" / "origin_probe.py"),
            "--site",
            ROLE,
            "--release-sha",
            release,
            "--port",
            "8213",
        ],
        timeout=90,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("origin local probe output is invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema",
            "status",
            "site",
            "release_sha",
            "origin_tls_status",
            "origin_cache_control",
            "application_ready",
            "application_physical_site",
        }
        or payload.get("schema") != "three-site-full-matrix-origin-local-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("site") != ROLE
        or payload.get("release_sha") != release
        or payload.get("origin_tls_status") != 204
        or "no-store" not in str(payload.get("origin_cache_control") or "").lower()
        or payload.get("application_ready") is not True
        or payload.get("application_physical_site") != ROLE
    ):
        raise SiteAgentError("origin local probe did not attest the pinned role")
    return {"status": "passed", "origin": payload}


def _customer_actor_matrix(request: dict[str, Any]) -> dict[str, Any]:
    """Run only the two WA-IR-owned lifecycle probes via the pull channel."""

    context = request.get("context")
    if (
        not isinstance(context, dict)
        or set(context) != {"scenario_id", "prefix", "observer"}
        or context.get("scenario_id")
        not in {
            "customer_actor_matrix_iran_active_outage",
            "customer_actor_matrix_recovery_ir_routed",
        }
        or type(context.get("observer")) is not bool
        or _CUSTOMER_PREFIX.fullmatch(str(context.get("prefix") or "")) is None
    ):
        raise SiteAgentError("customer actor matrix context is invalid")
    suffix = "ORACLE_" if context["observer"] else "DOER_"
    if not str(context["prefix"]).endswith(suffix):
        raise SiteAgentError("customer actor matrix prefix role is invalid")
    _verify_release(request)
    compose = Path("/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml")
    env_file = Path("/root/secure-envs/full-matrix/roles/webapp-ir.env")
    # These values are not caller-controlled.  The signed, campaign-bound
    # operation is the authorization boundary for the isolated staging cleanup.
    environment = {
        **SAFE_ENV,
        "PRODUCTION_FULL_MATRIX_CONFIRM": "execute-production-full-matrix",
        "PRODUCTION_TEST_CLEANUP_CONFIRM": "hard-delete-test-data",
    }
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", str(env_file), "-f", str(compose),
            "run", "--rm", "--no-deps", "-T",
            "-e", "PRODUCTION_FULL_MATRIX_CONFIRM=execute-production-full-matrix",
            "-e", "PRODUCTION_TEST_CLEANUP_CONFIRM=hard-delete-test-data",
            "webapp_ir_api",
            "python", "-I", "-B",
            "/app/scripts/full_matrix_live/customer_actor_probe.py",
            "--scenario-id", str(context["scenario_id"]),
            "--writer-role", ROLE,
            "--prefix", str(context["prefix"]),
            "--allow-production-execution",
            "--allow-production-cleanup",
        ],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )
    if (
        result.returncode != 0
        or len(result.stdout.encode()) > 16 * 1024 * 1024
        or len(result.stderr.encode()) > 1024 * 1024
    ):
        raise SiteAgentError("customer actor matrix command failed")
    try:
        payload = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("customer actor matrix output is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "three-site-full-matrix-customer-actor-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("scenario_id") != context["scenario_id"]
        or payload.get("writer_role") != ROLE
        or payload.get("prefix") != context["prefix"]
        or payload.get("pair_count") != 17
    ):
        raise SiteAgentError("customer actor matrix did not attest the pinned role")
    return {"status": "passed", "probe_payload": payload}


def _cross_writer_session_verify(request: dict[str, Any]) -> dict[str, Any]:
    """Run the post-promotion half of the session contract on WA-IR only."""

    context = request.get("context")
    required = {
        "prefix",
        "user_id",
        "primary_session_id",
        "backup_session_id",
        "descriptor_sha256",
        "observer",
    }
    if (
        not isinstance(context, dict)
        or set(context) != required
        or _CUSTOMER_PREFIX.fullmatch(str(context.get("prefix") or "")) is None
        or type(context.get("user_id")) is not int
        or int(context["user_id"]) < 1
        or _SESSION_UUID.fullmatch(str(context.get("primary_session_id") or "")) is None
        or _SESSION_UUID.fullmatch(str(context.get("backup_session_id") or "")) is None
        or context["primary_session_id"] == context["backup_session_id"]
        or _SHA256.fullmatch(str(context.get("descriptor_sha256") or "")) is None
        or type(context.get("observer")) is not bool
    ):
        raise SiteAgentError("cross-Writer session context is invalid")
    suffix = "ORACLE_" if context["observer"] else "DOER_"
    if not str(context["prefix"]).endswith(suffix):
        raise SiteAgentError("cross-Writer session prefix role is invalid")
    _verify_release(request)
    compose = Path("/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml")
    env_file = Path("/root/secure-envs/full-matrix/roles/webapp-ir.env")
    environment = {
        **SAFE_ENV,
        "PRODUCTION_FULL_MATRIX_CONFIRM": "execute-production-full-matrix",
        "PRODUCTION_TEST_CLEANUP_CONFIRM": "hard-delete-test-data",
    }
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", str(env_file), "-f", str(compose),
            "run", "--rm", "--no-deps", "-T",
            "-e", "PRODUCTION_FULL_MATRIX_CONFIRM=execute-production-full-matrix",
            "-e", "PRODUCTION_TEST_CLEANUP_CONFIRM=hard-delete-test-data",
            "webapp_ir_api",
            "python", "-I", "-B",
            "/app/scripts/full_matrix_live/cross_writer_session_probe.py",
            "--mode", "verify",
            "--prefix", str(context["prefix"]),
            "--user-id", str(context["user_id"]),
            "--primary-session-id", str(context["primary_session_id"]),
            "--backup-session-id", str(context["backup_session_id"]),
            "--allow-production-execution",
            "--allow-production-cleanup",
        ],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )
    if (
        result.returncode != 0
        or len(result.stdout.encode()) > 16 * 1024 * 1024
        or len(result.stderr.encode()) > 1024 * 1024
    ):
        raise SiteAgentError("cross-Writer session command failed")
    try:
        payload = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteAgentError("cross-Writer session output is invalid") from exc
    required_payload = {
        "schema", "status", "mode", "role", "prefix", "writer_epoch",
        "descriptor_sha256", "observation", "cleanup",
    }
    expected_observation = {
        "pre_promotion_session_accepted_after_ir_writer_activation",
        "post_promotion_websocket_reauthenticated_and_received_exact_event",
        "ir_writer_revoked_primary_session_fail_closed",
        "ir_writer_promoted_backup_session_and_authorized_it",
    }
    expected_cleanup = {
        "only_prefixed_session_fixture_rows_deleted",
        "exact_session_blacklist_keys_removed",
        "fixture_residue_zero",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required_payload
        or payload.get("schema") != "three-site-full-matrix-cross-writer-session-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("mode") != "verify"
        or payload.get("role") != ROLE
        or payload.get("prefix") != context["prefix"]
        or type(payload.get("writer_epoch")) is not int
        or int(payload["writer_epoch"]) < 1
        or payload.get("descriptor_sha256") != context["descriptor_sha256"]
        or not isinstance(payload.get("observation"), dict)
        or set(payload["observation"]) != expected_observation
        or any(value is not True for value in payload["observation"].values())
        or not isinstance(payload.get("cleanup"), dict)
        or set(payload["cleanup"]) != expected_cleanup
        or any(value is not True for value in payload["cleanup"].values())
    ):
        raise SiteAgentError("cross-Writer session did not attest the pinned contract")
    return {"status": "passed", "probe_payload": payload}


def _write_private(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise SiteAgentError("private failover input write was incomplete")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _failover_site_operation(request: dict[str, Any]) -> dict[str, Any]:
    """Run one fixed WA-IR-local failover action through the pull channel.

    The controller can send only a signed approved plan, an exact action and
    the two typed proof inputs needed by the existing local site agent.  It
    never supplies a command, path, Compose target or environment.  The plan
    and any source-tail boundary exist only in a private temporary directory
    on WA-IR and are removed before the encrypted response is emitted.
    """

    context = request.get("context")
    required = {
        "action",
        "plan",
        "source_tail_boundary",
        "readiness_evidence",
        "previous_proof_hash",
    }
    if not isinstance(context, dict) or set(context) != required:
        raise SiteAgentError("failover site-operation context fields are invalid")
    action = str(context.get("action") or "")
    plan_payload = context.get("plan")
    boundary = context.get("source_tail_boundary")
    readiness_evidence = context.get("readiness_evidence")
    previous_proof_hash = context.get("previous_proof_hash")
    if (
        action not in _FAILOVER_SITE_ACTIONS
        or not isinstance(plan_payload, dict)
        or (boundary is not None and not isinstance(boundary, dict))
        or (readiness_evidence is not None and not isinstance(readiness_evidence, dict))
        or (previous_proof_hash is not None and _SHA256.fullmatch(str(previous_proof_hash)) is None)
    ):
        raise SiteAgentError("failover site-operation context is invalid")
    try:
        from core.dr_command_orchestration_adapter import TYPED_OPERATIONS
        from core.dr_failover_orchestrator import parse_plan

        plan = parse_plan(plan_payload)
    except Exception as exc:
        raise SiteAgentError("failover site-operation plan is invalid") from exc
    if plan.release_sha != request["release_sha"]:
        raise SiteAgentError("failover site-operation release differs")
    if action in {
        "source-fenced",
        "source-drained-and-fenced",
        "source-connections-drained",
    }:
        if plan.source_site != ROLE:
            raise SiteAgentError("failover source action is not pinned to WA-IR")
    elif action != "safe-fence" and plan.target_site != ROLE:
        raise SiteAgentError("failover target action is not pinned to WA-IR")
    if (action == "target-ready") != (boundary is not None):
        raise SiteAgentError("failover source-tail input does not match its action")
    if (action == "target-term-acquired") != (readiness_evidence is not None):
        raise SiteAgentError("failover readiness input does not match its action")
    if (action == "target-term-attested") != (previous_proof_hash is not None):
        raise SiteAgentError("failover previous-proof input does not match its action")
    _verify_release(request)
    with tempfile.TemporaryDirectory(prefix="full-matrix-ir-failover-") as temporary:
        root = Path(temporary)
        os.chmod(root, 0o700)
        plan_path = root / "plan.json"
        manifest_path = root / "typed-operation-manifest.json"
        output_path = root / "evidence.json"
        _write_private(plan_path, plan_payload)
        _write_private(
            manifest_path,
            {
                "schema": "three-site-typed-operation-adapter-v1",
                "operation_id": plan.operation_id,
                "operations": TYPED_OPERATIONS,
            },
        )
        command = [
            "/usr/bin/python3",
            str(REPO_ROOT / "scripts/run_three_site_staging_failover_site_agent.py"),
            action,
            "--role", ROLE,
            "--plan", str(plan_path),
            "--command-manifest", str(manifest_path),
            "--approver-policy", str(_FAILOVER_POLICY),
            "--role-compose", str(_FAILOVER_COMPOSE),
            "--env-file", str(_FAILOVER_ENV),
            "--output", str(output_path),
            "--apply",
            "--confirm",
            f"staging-site-op:{plan.operation_id}:{ROLE}:{action}:{plan.plan_hash}",
        ]
        if boundary is not None:
            source_tail_path = root / "source-tail.json"
            _write_private(source_tail_path, {"source_tail_boundary": boundary})
            command.extend(["--source-tail", str(source_tail_path)])
        if readiness_evidence is not None:
            readiness_path = root / "target-readiness.json"
            _write_private(readiness_path, readiness_evidence)
            command.extend(["--readiness-evidence", str(readiness_path)])
        if previous_proof_hash is not None:
            command.extend(["--previous-proof-hash", str(previous_proof_hash)])
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        if (
            result.returncode != 0
            or len(result.stdout.encode()) > 1024 * 1024
            or len(result.stderr.encode()) > 1024 * 1024
        ):
            raise SiteAgentError("fixed WA-IR failover site operation failed")
        try:
            output = json.loads(result.stdout, object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SiteAgentError("WA-IR failover site output is invalid") from exc
        if (
            not isinstance(output, dict)
            or output.get("status") != "ok"
            or output.get("operation_id") != plan.operation_id
            or output_path.is_symlink()
            or not output_path.is_file()
        ):
            raise SiteAgentError("WA-IR failover site operation did not attest its plan")
        return {"status": "passed", "action": action, "evidence": output}


def execute(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    if operation == "host_snapshot":
        result = _host_snapshot(request)
    elif operation == "scenario_execute":
        result = _scenario_probe(request, observer=False)
    elif operation == "scenario_observe":
        result = _scenario_probe(request, observer=True)
    elif operation == "timing_clock":
        result = _timing_clock(request)
    elif operation == "timing_snapshot":
        result = _timing_snapshot(request)
    elif operation == "timing_emit":
        result = _timing_emit(request)
    elif operation == "timing_cleanup":
        result = _timing_cleanup(request)
    elif operation == "recovery_delivery_fault":
        result = _recovery_delivery_fault(request)
    elif operation == "recovery_delivery_resume_emit":
        result = _recovery_delivery_resume_emit(request)
    elif operation == "origin_local_probe":
        result = _origin_local_probe(request)
    elif operation == "customer_actor_matrix":
        result = _customer_actor_matrix(request)
    elif operation == "cross_writer_session_verify":
        result = _cross_writer_session_verify(request)
    elif operation == "failover_site_operation":
        result = _failover_site_operation(request)
    else:
        # Scenario operations are enabled only alongside their exact handlers.
        raise SiteAgentError(f"site operation is not implemented: {operation}")
    return {
        "schema": SCHEMA,
        "status": "passed",
        "role": ROLE,
        "request_id": request["request_id"],
        "release_sha": request["release_sha"],
        "sequence": request["sequence"],
        "operation": operation,
        "result": result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        payload = execute(_read_request(args.request))
    except Exception:
        return 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
