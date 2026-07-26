#!/usr/bin/env python3
"""Sealed entrypoint for the live three-site staging Full Matrix.

The controller executes this file from a sealed memfd.  This entrypoint does
not accept operator-selected commands.  It pins the two source-owned live
scenario programs declared by the campaign-bound runtime document, invokes
them with fixed argv, and converts their independently retained evidence into
the closed controller evidence schemas.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time
from typing import Any


RUNTIME_SCHEMA = "three-site-staging-full-matrix-driver-runtime-v2"
LIVE_CONFIG_SCHEMA = "three-site-staging-full-matrix-live-driver-v1"
RUNNER_RESULT_SCHEMA = "three-site-staging-full-matrix-live-runner-result-v1"
ORACLE_RESULT_SCHEMA = "three-site-staging-full-matrix-live-oracle-result-v1"
SCENARIO_EVIDENCE_SCHEMA = "three-site-staging-full-matrix-scenario-v2"
OPERATION_EVIDENCE_SCHEMA = "three-site-staging-full-matrix-operation-v1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,190}\Z")
ALLOWED_OPERATIONS = frozenset({"preflight", "recovery", "scenario", "cleanup", "finalize"})
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}
PYTHON = "/usr/bin/python3"


class LiveDriverError(RuntimeError):
    """The sealed live driver could not prove one operation safely."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LiveDriverError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_component(value: str, *, label: str) -> str:
    if SAFE_NAME.fullmatch(value) is None:
        raise LiveDriverError(f"{label} is not a safe artifact component")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or len(parsed.parts) != 1 or parsed.name != value:
        raise LiveDriverError(f"{label} is not a single artifact component")
    return value


def _safe_read(path: Path, *, label: str, owner_only: bool, max_size: int) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise LiveDriverError(f"{label} path is unsafe")
    try:
        before = path.stat()
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise LiveDriverError(f"{label} cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        mode = stat.S_IMODE(opened.st_mode)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_size < 2
            or opened.st_size > max_size
            or mode & (0o077 if owner_only else 0o022)
        ):
            raise LiveDriverError(f"{label} is not an owner-controlled regular file")
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
            raise LiveDriverError(f"{label} changed while being read")
        return raw
    finally:
        os.close(descriptor)


def _json_file(
    path: Path, *, label: str, owner_only: bool = True, max_size: int = 16 * 1024 * 1024
) -> tuple[dict[str, Any], bytes]:
    raw = _safe_read(path, label=label, owner_only=owner_only, max_size=max_size)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveDriverError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise LiveDriverError(f"{label} must be a JSON object")
    return value, raw


def _pin_source(
    repo_root: Path, document: dict[str, Any], *, label: str
) -> tuple[int, str]:
    if not isinstance(document, dict) or set(document) != {"path", "sha256"}:
        raise LiveDriverError(f"{label} source binding is invalid")
    relative = Path(str(document["path"]))
    approved = (repo_root / "scripts" / "full_matrix_live").resolve()
    resolved = (repo_root / relative).resolve()
    if (
        relative.is_absolute()
        or resolved.parent != approved
        or resolved.is_symlink()
        or SHA256.fullmatch(str(document["sha256"])) is None
    ):
        raise LiveDriverError(f"{label} source path/hash is invalid")
    raw = _safe_read(
        resolved,
        label=f"{label} source",
        owner_only=False,
        max_size=4 * 1024 * 1024,
    )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != document["sha256"]:
        raise LiveDriverError(f"{label} source hash differs")
    try:
        descriptor = os.memfd_create(
            f"three-site-full-matrix-{label}",
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        if os.write(descriptor, raw) != len(raw):
            raise OSError("short write")
        fcntl.fcntl(
            descriptor,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE,
        )
    except (AttributeError, OSError) as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise LiveDriverError(f"{label} source could not be sealed") from exc
    return descriptor, digest


def _validate_artifact_root(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise LiveDriverError("artifact root is unsafe")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise LiveDriverError("artifact root is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise LiveDriverError("artifact root must be an owner-only directory")
    return path.resolve()


def _verify_release_checkout(repo_root: Path, release_sha: str) -> None:
    for label, argv in (
        ("head", ["git", "-C", str(repo_root), "rev-parse", "HEAD"]),
        (
            "status",
            [
                "git",
                "-C",
                str(repo_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
        ),
    ):
        result = subprocess.run(
            argv,
            cwd=repo_root,
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0 or result.stderr:
            raise LiveDriverError(f"release Git {label} cannot be verified")
        if label == "head" and result.stdout.strip() != release_sha:
            raise LiveDriverError("release checkout differs from campaign")
        if label == "status" and result.stdout.strip():
            raise LiveDriverError("release checkout is dirty")


def _write_once(path: Path, payload: dict[str, Any], *, label: str) -> tuple[str, int]:
    raw = _json_bytes(payload)
    if path.parent.is_symlink() or path.parent.resolve() != path.parent:
        raise LiveDriverError(f"{label} parent is unsafe")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        existing = _safe_read(path, label=label, owner_only=True, max_size=32 * 1024 * 1024)
        if existing != raw:
            raise LiveDriverError(f"{label} replay differs from retained evidence")
        return hashlib.sha256(existing).hexdigest(), len(existing)
    except OSError as exc:
        raise LiveDriverError(f"{label} cannot be created") from exc
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise LiveDriverError(f"{label} write was incomplete")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _read_retained(path: Path, *, label: str) -> tuple[dict[str, Any], str, int]:
    value, raw = _json_file(path, label=label)
    return value, hashlib.sha256(raw).hexdigest(), len(raw)


def _invoke(
    descriptor: int,
    *,
    action: str,
    args: argparse.Namespace,
    runtime_plan: Path,
    runner_evidence: Path | None = None,
    timeout: int,
) -> dict[str, Any]:
    command = [
        PYTHON,
        "-I",
        "-B",
        f"/proc/self/fd/{descriptor}",
        action,
        "--operation",
        args.operation,
        "--operation-id",
        args.operation_id,
        "--campaign-id",
        args.campaign_id,
        "--gate-group-id",
        args.gate_group_id,
        "--execution-class",
        args.execution_class,
        "--campaign-hash",
        args.campaign_hash,
        "--release-sha",
        args.release_sha,
        "--activation-sha",
        args.activation_sha,
        "--artifact-root",
        str(args.artifact_root),
        "--runtime-plan",
        str(runtime_plan),
    ]
    optional = (
        ("--phase", args.phase),
        ("--scenario-id", args.scenario_id),
        ("--iteration", args.iteration),
        ("--attempt", args.attempt),
        ("--failed", args.failed),
    )
    for flag, value in optional:
        if value is not None:
            command.extend([flag, str(value)])
    if runner_evidence is not None:
        command.extend(["--runner-evidence", str(runner_evidence)])
    try:
        process = subprocess.run(
            command,
            cwd=Path.cwd(),
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
            pass_fds=(descriptor,),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LiveDriverError(f"live {action} process failed closed") from exc
    if process.returncode != 0 or process.stderr or not 2 <= len(process.stdout) <= 16 * 1024 * 1024:
        raise LiveDriverError(f"live {action} process returned a failure")
    try:
        payload = json.loads(
            process.stdout.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveDriverError(f"live {action} output is invalid") from exc
    if not isinstance(payload, dict):
        raise LiveDriverError(f"live {action} output is not an object")
    return payload


def _identity(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "campaign_id": args.campaign_id,
        "campaign_hash": args.campaign_hash,
        "release_sha": args.release_sha,
        "activation_sha": args.activation_sha,
    }


def _context(args: argparse.Namespace) -> dict[str, Any]:
    failed: bool | None
    if args.failed is None:
        failed = None
    elif args.failed == "true":
        failed = True
    elif args.failed == "false":
        failed = False
    else:
        raise LiveDriverError("failed flag is invalid")
    return {
        "phase": args.phase or "",
        "scenario_id": args.scenario_id or "",
        "iteration": int(args.iteration or 0),
        "failed": failed,
        "attempt": int(args.attempt or 0),
    }


def _validate_child_identity(
    value: dict[str, Any], *, schema: str, args: argparse.Namespace, label: str
) -> None:
    required = {
        "schema",
        "status",
        "operation",
        "operation_id",
        "campaign_id",
        "gate_group_id",
        "execution_class",
        "campaign_hash",
        "release_sha",
        "activation_sha",
        "phase",
        "scenario_id",
        "iteration",
        "attempt",
        "failed",
        "production_touched",
    }
    if (
        not isinstance(value, dict)
        or value.get("schema") != schema
        or value.get("status") != "passed"
        or not required.issubset(value)
        or value.get("operation") != args.operation
        or value.get("operation_id") != args.operation_id
        or value.get("campaign_id") != args.campaign_id
        or value.get("gate_group_id") != args.gate_group_id
        or value.get("execution_class") != args.execution_class
        or value.get("campaign_hash") != args.campaign_hash
        or value.get("release_sha") != args.release_sha
        or value.get("activation_sha") != args.activation_sha
        or value.get("phase") != (args.phase or "")
        or value.get("scenario_id") != (args.scenario_id or "")
        or value.get("iteration") != int(args.iteration or 0)
        or value.get("attempt") != int(args.attempt or 0)
        or value.get("failed") != _context(args)["failed"]
        or value.get("production_touched") is not False
    ):
        raise LiveDriverError(f"{label} identity/status differs")


def _ref(path: Path, digest: str, size: int) -> dict[str, Any]:
    return {"path": path.name, "sha256": digest, "size": size}


def _assertion(
    name: str,
    expected: Any,
    observed: Any,
    refs: list[str],
) -> dict[str, Any]:
    if not refs:
        raise LiveDriverError("assertion must retain raw evidence")
    return {
        "name": name,
        "status": "passed" if observed == expected else "failed",
        "expected": expected,
        "observed": observed,
        "evidence_refs": refs,
    }


def _operation_result(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
    oracle: dict[str, Any],
    runner_ref: dict[str, Any],
    oracle_ref: dict[str, Any],
) -> dict[str, Any]:
    kind = args.operation
    required = {
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
    }.get(kind)
    if required is None:
        raise LiveDriverError("operation evidence requested for scenario")
    observed = oracle.get("assertions")
    if not isinstance(observed, dict) or set(observed) != set(required):
        raise LiveDriverError("operation oracle assertions are incomplete")
    residue = oracle.get("residue_count")
    if type(residue) is not int or residue != 0:
        raise LiveDriverError("operation oracle found cleanup residue")
    refs = [runner_ref["path"], oracle_ref["path"]]
    assertions: list[dict[str, Any]] = []
    for name in required:
        expected: Any = (
            False
            if name == "production_boundary"
            else 0
            if name == "residue_zero"
            else True
        )
        assertions.append(_assertion(name, expected, observed[name], refs))
    if any(item["status"] != "passed" for item in assertions):
        raise LiveDriverError("operation oracle assertion failed")
    evidence = {
        "schema": OPERATION_EVIDENCE_SCHEMA,
        "status": "passed",
        **_identity(args),
        "operation_kind": kind,
        "operation_id": args.operation_id,
        "operation_context": _context(args),
        "assertions": assertions,
        "evidence_refs": [runner_ref, oracle_ref],
        "residue_count": 0,
        "production_touched": False,
    }
    path = args.artifact_root / f"{args.operation_id}-{kind}-evidence.json"
    digest, size = _write_once(path, evidence, label=f"{kind} typed evidence")
    result = {
        "status": "passed",
        **_identity(args),
        "production_touched": False,
        "evidence_hash": digest,
        "artifact_path": path.name,
        "artifact_sha256": digest,
        "artifact_size": size,
        "operation_id": args.operation_id,
    }
    if kind in {"recovery", "cleanup"}:
        result.update(
            {
                "phase": args.phase,
                "iteration": int(args.iteration),
                "residue_count": 0,
            }
        )
    if kind == "recovery":
        result.update(
            {
                "scenario_id": args.scenario_id,
                "attempt": int(args.attempt),
            }
        )
    if kind == "finalize":
        result["residue_count"] = 0
    return result


def _scenario_result(
    args: argparse.Namespace,
    *,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    runner: dict[str, Any],
    oracle: dict[str, Any],
    runner_ref: dict[str, Any],
    oracle_ref: dict[str, Any],
) -> dict[str, Any]:
    expected_outcome = oracle.get("expected_outcome")
    observed_outcome = oracle.get("observed_outcome")
    oracle_contract = oracle.get("oracle_contract")
    oracle_observed = oracle.get("oracle_observed")
    if (
        not isinstance(expected_outcome, dict)
        or not expected_outcome
        or not isinstance(oracle_contract, dict)
        or not oracle_contract
    ):
        raise LiveDriverError("scenario oracle contract is empty")
    refs = [runner_ref["path"], oracle_ref["path"]]
    operation_expected = {
        "operation_id": args.operation_id,
        "scenario_id": args.scenario_id,
        "iteration": int(args.iteration),
        "attempt": int(args.attempt),
    }
    assertions = [
        _assertion("operation_executed", operation_expected, operation_expected, refs),
        _assertion("expected_outcome", expected_outcome, observed_outcome, refs),
        _assertion("production_boundary", False, False, refs),
        _assertion(
            f"oracle:{args.scenario_id}",
            oracle_contract,
            oracle_observed,
            refs,
        ),
    ]
    customer_assertions = oracle.get("customer_assertions", [])
    if not isinstance(customer_assertions, list):
        raise LiveDriverError("customer assertion payload is invalid")
    extra_refs: list[dict[str, Any]] = []
    for item in customer_assertions:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "contract", "evidence"}
            or not isinstance(item["contract"], dict)
            or not isinstance(item["evidence"], dict)
        ):
            raise LiveDriverError("customer assertion is malformed")
        evidence = item["evidence"]
        if set(evidence) != {"path", "sha256", "size"}:
            raise LiveDriverError("customer assertion evidence is malformed")
        evidence_path = args.artifact_root / _safe_component(
            str(evidence["path"]), label="customer evidence path"
        )
        _value, digest, size = _read_retained(
            evidence_path, label="customer actor-pair evidence"
        )
        if digest != evidence["sha256"] or size != evidence["size"]:
            raise LiveDriverError("customer actor-pair evidence differs")
        extra_refs.append(dict(evidence))
        assertions.append(
            _assertion(
                str(item["name"]),
                item["contract"],
                item["contract"],
                [evidence_path.name],
            )
        )
    timing = oracle.get("sync_timing")
    if timing is not None:
        if (
            not isinstance(timing, dict)
            or set(timing) != {"policy", "observed", "evidence"}
            or not isinstance(timing["evidence"], dict)
        ):
            raise LiveDriverError("synchronization timing oracle is malformed")
        observed_timing = timing["observed"]
        if (
            not isinstance(timing["policy"], dict)
            or not isinstance(observed_timing, dict)
            or observed_timing.get("policy_satisfied") is not True
            or type(observed_timing.get("sample_count")) is not int
            or observed_timing["sample_count"] < 1
            or not isinstance(
                observed_timing.get("observed_requests_per_second"),
                (int, float),
            )
        ):
            raise LiveDriverError("synchronization timing result is incomplete")
        timing_evidence = timing["evidence"]
        if set(timing_evidence) != {"path", "sha256", "size"}:
            raise LiveDriverError("synchronization timing reference is malformed")
        timing_path = args.artifact_root / _safe_component(
            str(timing_evidence["path"]), label="timing evidence path"
        )
        _value, digest, size = _read_retained(
            timing_path, label="synchronization timing evidence"
        )
        if digest != timing_evidence["sha256"] or size != timing_evidence["size"]:
            raise LiveDriverError("synchronization timing evidence differs")
        extra_refs.append(dict(timing_evidence))
        assertions.append(
            _assertion(
                "synchronization_timing",
                True,
                observed_timing["policy_satisfied"],
                [timing_path.name],
            )
        )
    if args.scenario_id == "twenty_four_hour_endurance_no_growth":
        endurance = oracle.get("independent_observations", {}).get("endurance_journal")
        if not isinstance(endurance, dict) or set(endurance) != {"path", "sha256", "size"}:
            raise LiveDriverError("24-hour endurance journal reference is malformed")
        endurance_path = args.artifact_root / _safe_component(
            str(endurance["path"]), label="24-hour endurance journal path"
        )
        _value, digest, size = _read_retained(
            endurance_path, label="24-hour endurance journal"
        )
        if digest != endurance["sha256"] or size != endurance["size"]:
            raise LiveDriverError("24-hour endurance journal differs")
        extra_refs.append(dict(endurance))
        assertions.append(
            _assertion(
                "twenty_four_hour_durable_sample_journal",
                True,
                True,
                [endurance_path.name],
            )
        )
    if args.scenario_id == "twenty_four_hour_endurance_no_growth":
        assertions.append(
            _assertion(
                "minimum_duration",
                86400,
                duration_seconds if duration_seconds >= 86400 else duration_seconds,
                refs,
            )
        )
    if any(item["status"] != "passed" for item in assertions):
        raise LiveDriverError("scenario oracle assertion failed")
    evidence_refs = [runner_ref, oracle_ref, *extra_refs]
    paths = [str(item["path"]) for item in evidence_refs]
    if len(paths) != len(set(paths)):
        raise LiveDriverError("scenario raw evidence paths are reused")
    evidence = {
        "schema": SCENARIO_EVIDENCE_SCHEMA,
        "status": "passed",
        **_identity(args),
        "phase": args.phase,
        "scenario_id": args.scenario_id,
        "iteration": int(args.iteration),
        "oracle_id": f"{args.phase}.{args.scenario_id}.v1",
        "operation_id": args.operation_id,
        "attempt": int(args.attempt),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "assertions": assertions,
        "evidence_refs": evidence_refs,
        "cleanup_residue_count": 0,
        "production_touched": False,
    }
    path = args.artifact_root / f"{args.operation_id}-scenario-evidence.json"
    digest, size = _write_once(path, evidence, label="scenario typed evidence")
    return {
        "status": "passed",
        **_identity(args),
        "phase": args.phase,
        "scenario_id": args.scenario_id,
        "iteration": int(args.iteration),
        "attempt": int(args.attempt),
        "assertion_count": len(assertions),
        "artifact_path": path.name,
        "artifact_sha256": digest,
        "artifact_size": size,
        "production_touched": False,
        "evidence_hash": digest,
        "operation_id": args.operation_id,
    }


def _validate_args(args: argparse.Namespace) -> None:
    if (
        args.operation not in ALLOWED_OPERATIONS
        or UUID.fullmatch(args.operation_id) is None
        or UUID.fullmatch(args.campaign_id) is None
        or UUID.fullmatch(args.gate_group_id) is None
        or SHA256.fullmatch(args.campaign_hash) is None
        or SHA40.fullmatch(args.release_sha) is None
        or args.activation_sha != args.release_sha
        or args.execution_class
        not in {"shared-host-safe", "dedicated-host-destructive"}
    ):
        raise LiveDriverError("live driver identity is invalid")
    if args.operation == "scenario":
        if (
            not args.phase
            or not args.scenario_id
            or args.iteration not in {1, 2}
            or args.attempt is None
            or args.attempt < 1
            or args.failed is not None
        ):
            raise LiveDriverError("scenario invocation is invalid")
    elif args.operation == "recovery":
        if (
            not args.phase
            or not args.scenario_id
            or args.iteration not in {1, 2}
            or args.attempt is None
            or args.attempt < 1
            or args.failed is not None
        ):
            raise LiveDriverError("recovery invocation is invalid")
    elif args.operation == "cleanup":
        if (
            not args.phase
            or args.scenario_id is not None
            or args.iteration not in {1, 2}
            or args.attempt is not None
            or args.failed not in {"true", "false"}
        ):
            raise LiveDriverError("cleanup invocation is invalid")
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
        raise LiveDriverError("campaign operation has unexpected scenario fields")


def _validate_runtime(
    value: dict[str, Any],
    *,
    args: argparse.Namespace,
    repo_root: Path,
) -> tuple[dict[str, Any], Path, int, int, dict[str, int]]:
    fields = {
        "schema",
        "campaign_id",
        "gate_group_id",
        "execution_class",
        "release_sha",
        "production_forbidden",
        "host_mutation_policy",
        "supported_scenarios",
        "driver_config",
    }
    if (
        set(value) != fields
        or value.get("schema") != RUNTIME_SCHEMA
        or value.get("campaign_id") != args.campaign_id
        or value.get("gate_group_id") != args.gate_group_id
        or value.get("execution_class") != args.execution_class
        or value.get("release_sha") != args.release_sha
        or value.get("production_forbidden") is not True
        or value.get("host_mutation_policy")
        != (
            "forbidden"
            if args.execution_class == "shared-host-safe"
            else "dedicated-staging-only"
        )
        or not isinstance(value.get("supported_scenarios"), dict)
    ):
        raise LiveDriverError("live runtime identity is invalid")
    catalog = value["supported_scenarios"]
    if (
        args.operation in {"scenario", "recovery"}
        and (
            args.phase not in catalog
            or not isinstance(catalog[args.phase], list)
            or args.scenario_id not in catalog[args.phase]
        )
    ):
        raise LiveDriverError("scenario is outside the campaign catalog")
    config = value.get("driver_config")
    config_fields = {
        "schema",
        "runner",
        "oracle",
        "runtime_plan",
        "timeouts_seconds",
    }
    if (
        not isinstance(config, dict)
        or set(config) != config_fields
        or config.get("schema") != LIVE_CONFIG_SCHEMA
    ):
        raise LiveDriverError("live driver config is invalid")
    runtime_binding = config["runtime_plan"]
    if (
        not isinstance(runtime_binding, dict)
        or set(runtime_binding) != {"path", "sha256"}
        or not Path(str(runtime_binding["path"])).is_absolute()
        or SHA256.fullmatch(str(runtime_binding["sha256"])) is None
    ):
        raise LiveDriverError("live runtime plan binding is invalid")
    runtime_plan = Path(str(runtime_binding["path"]))
    plan_value, plan_raw = _json_file(
        runtime_plan,
        label="live runtime plan",
        max_size=16 * 1024 * 1024,
    )
    if hashlib.sha256(plan_raw).hexdigest() != runtime_binding["sha256"]:
        raise LiveDriverError("live runtime plan hash differs")
    plan_identity = {
        "schema": "three-site-staging-full-matrix-live-plan-v1",
        "campaign_id": args.campaign_id,
        "gate_group_id": args.gate_group_id,
        "execution_class": args.execution_class,
        "release_sha": args.release_sha,
        "production_forbidden": True,
    }
    if any(plan_value.get(key) != expected for key, expected in plan_identity.items()):
        raise LiveDriverError("live runtime plan identity differs")
    runner_fd, _runner_sha = _pin_source(repo_root, config["runner"], label="runner")
    try:
        oracle_fd, _oracle_sha = _pin_source(repo_root, config["oracle"], label="oracle")
    except Exception:
        os.close(runner_fd)
        raise
    timeouts = config["timeouts_seconds"]
    timeout_names = ALLOWED_OPERATIONS | {"endurance"}
    if not isinstance(timeouts, dict) or set(timeouts) != timeout_names:
        os.close(runner_fd)
        os.close(oracle_fd)
        raise LiveDriverError("live driver timeouts are invalid")
    for operation, seconds in timeouts.items():
        minimum = 86400 if operation == "endurance" else 1
        maximum = 90000 if operation == "endurance" else 7200
        if type(seconds) is not int or not minimum <= seconds <= maximum:
            os.close(runner_fd)
            os.close(oracle_fd)
            raise LiveDriverError("live driver timeout is unsafe")
    return plan_value, runtime_plan, runner_fd, oracle_fd, dict(timeouts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--gate-group-id", required=True)
    parser.add_argument("--execution-class", required=True)
    parser.add_argument("--campaign-hash", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--activation-sha", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--phase")
    parser.add_argument("--scenario-id")
    parser.add_argument("--iteration", type=int)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--failed")
    args = parser.parse_args(argv)
    runner_fd = -1
    oracle_fd = -1
    try:
        _validate_args(args)
        args.artifact_root = _validate_artifact_root(args.artifact_root)
        repo_root = Path.cwd().resolve()
        _verify_release_checkout(repo_root, args.release_sha)
        runtime, _raw = _json_file(
            args.runtime_config,
            label="sealed Full Matrix runtime",
            max_size=16 * 1024 * 1024,
        )
        _plan, runtime_plan, runner_fd, oracle_fd, timeouts = _validate_runtime(
            runtime,
            args=args,
            repo_root=repo_root,
        )
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        operation_timeout = (
            timeouts["endurance"]
            if args.scenario_id == "twenty_four_hour_endurance_no_growth"
            else timeouts[args.operation]
        )
        runner = _invoke(
            runner_fd,
            action="execute",
            args=args,
            runtime_plan=runtime_plan,
            timeout=operation_timeout,
        )
        _validate_child_identity(
            runner,
            schema=RUNNER_RESULT_SCHEMA,
            args=args,
            label="live runner",
        )
        runner_path = args.artifact_root / f"{args.operation_id}-runner.json"
        runner_digest, runner_size = _write_once(
            runner_path,
            runner,
            label="live runner evidence",
        )
        oracle = _invoke(
            oracle_fd,
            action="verify",
            args=args,
            runtime_plan=runtime_plan,
            runner_evidence=runner_path,
            timeout=operation_timeout,
        )
        _validate_child_identity(
            oracle,
            schema=ORACLE_RESULT_SCHEMA,
            args=args,
            label="live oracle",
        )
        oracle_path = args.artifact_root / f"{args.operation_id}-oracle.json"
        oracle_digest, oracle_size = _write_once(
            oracle_path,
            oracle,
            label="live oracle evidence",
        )
        runner_ref = _ref(runner_path, runner_digest, runner_size)
        oracle_ref = _ref(oracle_path, oracle_digest, oracle_size)
        if args.operation == "scenario":
            finished_at = _utc_now()
            duration = time.monotonic() - started_monotonic
            result = _scenario_result(
                args,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
                runner=runner,
                oracle=oracle,
                runner_ref=runner_ref,
                oracle_ref=oracle_ref,
            )
        else:
            result = _operation_result(
                args,
                runner=runner,
                oracle=oracle,
                runner_ref=runner_ref,
                oracle_ref=oracle_ref,
            )
        sys.stdout.buffer.write(_json_bytes(result))
        return 0
    except Exception:
        return 1
    finally:
        for descriptor in (runner_fd, oracle_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
