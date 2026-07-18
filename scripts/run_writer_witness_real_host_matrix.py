#!/usr/bin/env python3
"""Guarded one-scenario-at-a-time executor for the dark Witness real-host Matrix."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any
import uuid
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPT = ROOT / "scripts/writer_witness_matrix_client.py"
PREFLIGHT_SCHEMA = "writer_witness_real_host_matrix_preflight_v1"
RUNNER_SCHEMA = "writer_witness_real_host_matrix_runner_v1"
APPROVAL_SCHEMA = "writer_witness_real_host_matrix_observer_approval_v1"
EXPECTED_BRANCH = "feature/arvan-controlled-origin-failover"
CONFIRM_ENV = "WRITER_WITNESS_REAL_HOST_MATRIX_CONFIRM"
CONFIRM_VALUE = "execute-dark-witness-real-host-matrix"
SCENARIO_CONFIRM_ENV = "WRITER_WITNESS_REAL_HOST_MATRIX_SCENARIO"
OBSERVER_CONFIRM_ENV = "WRITER_WITNESS_REAL_HOST_MATRIX_OBSERVER_CONFIRM"
OBSERVER_CONFIRM_VALUE = "approve-one-dark-witness-scenario"
DEFAULT_CONTROLLER_ROOT = Path("/var/lib/trading-bot-witness-matrix")
DEFAULT_ARTIFACT_ROOT = DEFAULT_CONTROLLER_ROOT / "runs"
LOCK_PATH = DEFAULT_CONTROLLER_ROOT / "active.lock"
DEFAULT_CAMPAIGN_ROOT = Path("/var/lib/trading-bot-witness-matrix/campaigns")
DEFAULT_SECRET_ROOT = Path("/run/writer-witness-matrix-controller")
TRUSTED_ALLOWED_SIGNERS = Path("/etc/trading-bot-witness-matrix/allowed_signers")
# Deliberately fail-closed until two independently custodied public keys are
# selected and their canonical allowed_signers bytes are pinned by a reviewed
# source commit. Root on the Matrix controller cannot choose this value at run
# time, so replacing both keys and signatures is no longer self-authorizing.
TRUSTED_ALLOWED_SIGNERS_SHA256 = "UNCONFIGURED-INDEPENDENT-SIGNER-POLICY"
REMOTE_CAMPAIGN_ROOT = "/var/lib/trading-bot-witness/matrix-campaign"
ROLLBACK_STATE_MANIFEST_SHA256 = (
    "0be506962c48d6e19b9f13b8e8f4f5961fade0d4a852de0dcea9d2819a7d61a7"
)
CONTROL_REQUEST_BYTES_UPPER_BOUND = 32_768
MAX_CONTROL_REQUESTS = 100
MAX_SCENARIO_SECONDS = 900
MAX_CLEANUP_SECONDS = 900
ABORT_MONITOR_INTERVAL_SECONDS = 10.0
SSH_MASTER_BYTES_UPPER_BOUND = 131_072
SSH_COMMAND_BYTES_UPPER_BOUND = 32_768
SSH_COMMAND_PAYLOAD_MAX = 65_536
SCP_SESSION_BYTES_UPPER_BOUND = 65_536
SCP_FILE_BYTES_MAX = 1_048_576
MAX_SCENARIO_TRANSPORT_OPERATIONS = 1_024
CLEANUP_TRANSPORT_BYTES_RESERVE = 16 * 1024 * 1024
MAX_CLEANUP_TRANSPORT_OPERATIONS = 512
MIN_DPI_BYTE_BUDGET = 64 * 1024 * 1024
REMOTE_ISOLATED_PYTHON = (
    "/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin "
    "/usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null"
)
REQUIRED_PREFLIGHT_CHECK_IDS = frozenset(
    {
        "git_branch_clean",
        "source_regression_gate",
        "webapp_fi_baseline",
        "webapp_ir_standby_baseline",
        "matrix_witness_dark_baseline",
        "rollback_witness_baseline",
    }
)
CLEANUP_STEP_IDS = (
    "stop_and_join_requesters",
    "retain_pre_recovery_evidence",
    "recover_active_live_restore",
    "revoke_transient_capability",
    "resume_paused_runtime",
    "remove_isolated_pressure",
    "remove_scoped_network_faults",
    "restore_vacant_baseline",
    "verify_complete_baseline",
)


@dataclass(frozen=True)
class Host:
    role: str
    address: str
    port: int = 22


HOSTS = {
    "webapp_fi": Host("webapp_fi", "65.109.220.59"),
    "webapp_ir": Host("webapp_ir", "87.236.212.194", 37067),
    "matrix_witness": Host("matrix_witness", "185.206.95.94"),
    "rollback_witness": Host("rollback_witness", "185.231.182.6"),
}


SCENARIOS: dict[str, tuple[str, ...]] = {
    "RH-001": ("stage_pairwise_clients", "concurrent_acquire", "assert_one_winner"),
    "RH-002": ("stage_fi_client", "close_after_commit", "exact_success_replay"),
    "RH-003": ("establish_live_fi_lease", "durable_ir_rejection", "replay_after_expiry"),
    "RH-004": ("establish_live_fi_lease", "partition_fi_to_witness", "ir_next_epoch"),
    "RH-005": ("establish_live_ir_lease", "partition_ir_to_witness", "fi_next_epoch"),
    "RH-006": ("service_stop_start", "service_sigstop_cont", "durable_state"),
    "RH-007": ("postgres_stop_start", "postgres_sigstop_cont", "durable_state"),
    "RH-008": ("record_state", "reboot_replacement_witness", "prove_reboot_persistence"),
    "RH-009": ("isolated_tmpfs_postgres", "force_disk_full", "prove_production_untouched"),
    "RH-010": (
        "client_auth_clock_boundaries", "isolated_postgres_clock_jump",
        "prove_lease_expiry_and_epoch_fencing",
    ),
    "RH-011": ("partition_ir", "rotate_fi_overlap", "cross_key_replay_and_rollback"),
    "RH-012": ("acquire_epoch_one", "fault_each_restore_phase", "restore_exact_epoch_zero"),
}


class MatrixError(RuntimeError):
    pass


class MatrixAbort(MatrixError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def rollback_manifest_helper_guard() -> str:
    helper = "/usr/local/sbin/writer-witness-state-manifest"
    return (
        f"helper={shlex.quote(helper)}; test -f \"$helper\"; test ! -L \"$helper\"; "
        "test \"$(stat -c %u \"$helper\")\" = 0; "
        "test \"$(stat -c %g \"$helper\")\" = 0; "
        "test \"$(stat -c %a \"$helper\")\" = 755; "
        "test \"$(stat -c %h \"$helper\")\" = 1; "
        "before=$(stat -c '%d:%i:%s:%Y:%Z' \"$helper\"); "
        "test \"$(sha256sum \"$helper\" | awk '{print $1}')\" = "
        f"{ROLLBACK_STATE_MANIFEST_SHA256}; "
        "test \"$(stat -c '%d:%i:%s:%Y:%Z' \"$helper\")\" = \"$before\""
    )


def safe_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"cannot read Matrix JSON artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise MatrixError("Matrix JSON artifact must be an object")
    return payload


def parse_json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MatrixError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise MatrixError(f"{label} must be a JSON object")
    return payload


def secure_directory(path: Path, *, create: bool = True) -> Path:
    """Return an owner-only real directory without following a final symlink."""

    if create:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise MatrixError(f"cannot create secure Matrix directory: {path}") from exc
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MatrixError(f"secure Matrix directory is missing: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise MatrixError(f"Matrix directory must be real, owner-controlled, and mode 0700: {path}")
    return path


def secure_durable_child_directory(root: Path, name: str) -> Path:
    """Create one fixed owner-only child and durably anchor its directory entry."""

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        raise MatrixError("secure Matrix child directory name is invalid")
    secure_directory(root)
    child = root / name
    created = False
    try:
        child.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise MatrixError(f"cannot create secure Matrix directory: {child}") from exc
    secure_directory(child, create=False)
    if created:
        fsync_directory(child)
        fsync_directory(root)
    return child


def read_secure_regular(path: Path, *, label: str, max_bytes: int = 1_048_576) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MatrixError(f"cannot securely open {label}: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_nlink != 1
            or metadata.st_size < 1
            or metadata.st_size > max_bytes
        ):
            raise MatrixError(f"{label} must be one owner-only regular file")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise MatrixError(f"{label} changed during its immutable read")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or after.st_ctime_ns != metadata.st_ctime_ns
            or after.st_uid != metadata.st_uid
            or after.st_gid != metadata.st_gid
            or after.st_mode != metadata.st_mode
            or after.st_nlink != metadata.st_nlink
        ):
            raise MatrixError(f"{label} changed during its immutable read")
        return raw
    finally:
        os.close(descriptor)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a complete inode without replacing prior evidence."""

    try:
        function = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise MatrixError("renameat2 is required for durable Matrix publication") from exc
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(source), -100, os.fsencode(destination), 1) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), destination)
    raise OSError(error_number, os.strerror(error_number), destination)


def direct_durable_child(path: Path, root: Path, *, label: str) -> Path:
    """Require one canonical, non-hidden direct child below a trusted root."""

    if not path.is_absolute() or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}", path.name
    ):
        raise MatrixError(f"{label} is not a safe absolute durable path")
    try:
        root_real = root.resolve(strict=True)
        parent_real = path.parent.resolve(strict=True)
    except OSError as exc:
        raise MatrixError(f"{label} durable parent is unavailable") from exc
    if path.parent != root or parent_real != root_real or path != root / path.name:
        raise MatrixError(f"{label} must be one canonical direct child of its durable root")
    if path.exists() or path.is_symlink():
        try:
            if path.resolve(strict=True) != path:
                raise MatrixError(f"{label} must not traverse a symlink")
        except OSError as exc:
            raise MatrixError(f"{label} cannot be resolved safely") from exc
    return path


class CampaignJournal:
    """Durable controller intent, written before every remote side effect."""

    def __init__(self, path: Path, payload: dict[str, Any], *, create: bool) -> None:
        self.path = path
        self.payload = payload
        self._lock = threading.RLock()
        secure_directory(self.path.parent)
        if create:
            self._create_atomically()

    def _create_atomically(self) -> None:
        """Publish a complete first journal inode; never expose an empty final file."""

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.create-",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            raw = (
                json.dumps(self.payload, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n"
            ).encode("utf-8")
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            rename_noreplace(temporary, self.path)
            fsync_directory(self.path.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> "CampaignJournal":
        payload = parse_json_bytes(
            read_secure_regular(path, label="campaign journal"),
            label="campaign journal",
        )
        if payload.get("schema_version") != RUNNER_SCHEMA:
            raise MatrixError("campaign journal schema is invalid")
        return cls(path, payload, create=False)

    def flush(self) -> None:
        with self._lock:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                temporary.write_text(
                    json.dumps(self.payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.chmod(temporary, 0o600)
                with temporary.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                fsync_directory(self.path.parent)
            finally:
                temporary.unlink(missing_ok=True)

    def update(self, **fields: Any) -> None:
        with self._lock:
            self.payload.update(fields)
            self.payload["updated_at"] = utc_now()
            self.flush()

    def claim(self, resource: str, value: str) -> None:
        with self._lock:
            resources = self.payload.setdefault("resources", {})
            if not isinstance(resources, dict):
                raise MatrixError("campaign journal resources are invalid")
            values = resources.setdefault(resource, [])
            if not isinstance(values, list):
                raise MatrixError("campaign journal resource list is invalid")
            if value not in values:
                values.append(value)
            self.update(status="active", dirty=True)

    def values(self, resource: str) -> set[str]:
        resources = self.payload.get("resources")
        values = resources.get(resource, []) if isinstance(resources, dict) else []
        return {str(value) for value in values}


def assert_no_dirty_campaigns(root: Path) -> None:
    secure_directory(root)
    stale_temps = tuple(root.glob(".wwm_*.json.*.tmp"))
    for path in stale_temps:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink not in {1, 2}
        ):
            raise MatrixError(f"unrecognized Matrix journal temporary state: {path.name}")
        path.unlink()
    if stale_temps:
        fsync_directory(root)
    dirty: list[str] = []
    for path in sorted(root.glob("*.json")):
        payload = parse_json_bytes(
            read_secure_regular(path, label="campaign journal"),
            label="campaign journal",
        )
        if payload.get("dirty") is True or payload.get("status") in {"active", "failed", "recovering"}:
            dirty.append(path.name)
    if dirty:
        raise MatrixError(f"unfinished Matrix campaign journals require recovery: {dirty}")


def parse_last_json(value: str) -> dict[str, Any]:
    for line in reversed(value.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise MatrixError("command output did not contain a JSON object")


def owner_only_env(path: Path) -> dict[str, str]:
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise MatrixError("Matrix credential file is missing or not owner-only")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise MatrixError("Matrix credential file is invalid")
        values[key] = value.strip()
    return values


def validate_preflight(payload: dict[str, Any], expected_head: str) -> dict[str, dict[str, str]]:
    if payload.get("schema_version") != PREFLIGHT_SCHEMA or payload.get("status") != "preflight_passed":
        raise MatrixError("a passing real-host preflight artifact is required")
    try:
        generated_at = datetime.fromisoformat(
            str(payload.get("generated_at")).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        completed_at = datetime.fromisoformat(
            str(payload.get("completed_at")).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise MatrixError("preflight artifact timestamps are invalid") from exc
    if completed_at < generated_at:
        raise MatrixError("preflight completion predates plan generation")
    age_seconds = (
        datetime.now(timezone.utc) - completed_at
    ).total_seconds()
    if age_seconds < -30 or age_seconds > 300:
        raise MatrixError("preflight artifact is outside the five-minute execution window")
    git = payload.get("git")
    bundle = payload.get("run_bundle")
    baseline = payload.get("observed_baseline")
    if not isinstance(git, dict) or not isinstance(bundle, dict) or not isinstance(baseline, dict):
        raise MatrixError("preflight artifact lacks its frozen run bundle or observed baseline")
    if git.get("head") != expected_head or git.get("expected_commit") != expected_head:
        raise MatrixError("preflight artifact is not pinned to the current exact commit")
    if bundle.get("expected_commit") != expected_head:
        raise MatrixError("preflight run bundle commit mismatch")
    expected_python_runtime = safe_json(ROOT / "deploy/writer-witness/python-runtime.json")
    expected_nftables_policy = safe_json(ROOT / "deploy/writer-witness/nftables-policy.json")
    expected_file_hashes = {
        "requirements_lock_sha256": hashlib.sha256(
            (ROOT / "deploy/writer-witness/requirements.lock").read_bytes()
        ).hexdigest(),
        "wheelhouse_manifest_sha256": hashlib.sha256(
            (ROOT / "deploy/writer-witness/wheelhouse.sha256").read_bytes()
        ).hexdigest(),
    }
    if not re.fullmatch(r"[0-9a-f]{64}", str(bundle.get("source_sha256", ""))):
        raise MatrixError("preflight run bundle source identity is invalid")
    if bundle.get("python_runtime") != expected_python_runtime:
        raise MatrixError("preflight run bundle Python runtime binding drifted")
    if bundle.get("nftables_policy") != expected_nftables_policy:
        raise MatrixError("preflight run bundle nftables binding drifted")
    for key, expected in expected_file_hashes.items():
        if bundle.get(key) != expected:
            raise MatrixError(f"preflight run bundle {key} drifted")
    if (
        bundle.get("source_gate_requires_zero_skips") is not True
        or bundle.get("source_gate_requires_guarded_postgres_tests") != 4
        or bundle.get("source_gate_requires_four_database_drill") is not True
        or bundle.get("expected_active_campaign_tag") is not None
        or bundle.get("expected_active_campaign_scenario") is not None
    ):
        raise MatrixError("preflight run bundle safety contract is incomplete")
    if payload.get("failed_checks"):
        raise MatrixError("preflight artifact contains failed checks")
    required = {
        "webapp_fi_baseline",
        "webapp_ir_standby_baseline",
        "matrix_witness_dark_baseline",
        "rollback_witness_baseline",
    }
    if not required.issubset(baseline) or not all(isinstance(baseline[item], dict) for item in required):
        raise MatrixError("preflight observed baseline is incomplete")
    matrix = baseline["matrix_witness_dark_baseline"]
    rollback = baseline["rollback_witness_baseline"]
    if matrix.get("state") != "webapp:0:vacant" or matrix.get("receipts") != "0":
        raise MatrixError("replacement Witness baseline is not epoch-zero vacant")
    if rollback.get("state") != "webapp:0:vacant" or rollback.get("receipts") != "0":
        raise MatrixError("original Witness baseline is not epoch-zero vacant")
    for key in (
        "manifest_sha256",
        "cert_sha256",
        "backup_sha256",
        "credential_bundle_sha256",
        "database_inventory_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(matrix.get(key, ""))):
            raise MatrixError(f"replacement Witness baseline has invalid {key}")
    for key in ("manifest_sha256", "cert_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(rollback.get(key, ""))):
            raise MatrixError(f"original Witness baseline has invalid {key}")
    if matrix.get("cert_sha256") == rollback.get("cert_sha256"):
        raise MatrixError("replacement and original Witness TLS identities are not distinct")
    if bundle.get("witness_release_manifest_sha256") != matrix.get(
        "release_manifest_sha256"
    ):
        raise MatrixError("preflight release manifest is not bound to the observed generation")
    if not re.fullmatch(r"writer-witness-[0-9]{8}T[0-9]{6}Z\.dump", str(matrix.get("backup", ""))):
        raise MatrixError("replacement Witness baseline backup identity is invalid")
    required_markers = {
        "webapp_fi_baseline": {
            "app": "healthy", "db": "healthy", "api": "200",
            "witness_flags_enabled": "no", "client_credentials_installed": "no",
        },
        "webapp_ir_standby_baseline": {
            "app": "stopped", "sync_worker": "stopped", "db": "running",
            "witness_flags_enabled": "no", "client_credentials_installed": "no",
        },
        "matrix_witness_dark_baseline": {
            "installed_helpers_match": "yes", "running_release_match": "yes",
            "release_manifest_attested": "yes", "release_metadata_attested": "yes",
            "effective_unit_attested": "yes", "system_runtime_attested": "yes",
            "runtime_attested": "yes",
            "runtime_provenance_attested": "yes", "offsite_upload_attested": "yes",
            "network_policy_semantics_match": "yes", "connection_enabled_aux_databases": "0",
            "orphan_candidate_failed_databases": "0", "campaign_state": "absent",
            "nftables_policy_sha256": str(expected_nftables_policy["policy_sha256"]),
        },
        "rollback_witness_baseline": {"rollback_helper_attested": "yes"},
    }
    for role, markers in required_markers.items():
        if any(str(baseline[role].get(key)) != value for key, value in markers.items()):
            raise MatrixError(f"preflight baseline lacks a required current marker for {role}")
    results = payload.get("preflight_results")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        raise MatrixError("preflight result inventory is invalid")
    indexed: dict[str, dict[str, Any]] = {}
    for item in results:
        check_id = str(item.get("check_id") or "")
        if not check_id or check_id in indexed:
            raise MatrixError("preflight result inventory contains a missing or duplicate check")
        if item.get("status") != "passed" or item.get("return_code") != 0:
            raise MatrixError(f"preflight check did not pass: {check_id}")
        indexed[check_id] = item
    if set(indexed) != REQUIRED_PREFLIGHT_CHECK_IDS:
        raise MatrixError("preflight result inventory does not match the closed required checks")
    source = indexed["source_regression_gate"]
    source_output = str((source or {}).get("stdout") or "")
    for marker in ('"guarded_postgres_tests":4', '"skipped":0', '"four_database_drill":true'):
        if marker not in source_output:
            raise MatrixError("preflight source gate is not zero-skip and four-database complete")
    return baseline  # type: ignore[return-value]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_approval(
    payload: dict[str, Any],
    *,
    scenario: str,
    expected_head: str,
    preflight_sha256: str,
    operator: str,
    reason: str,
    change_id: str,
    expected_restore_sha256: str,
    expected_allowed_signers_sha256: str,
) -> tuple[str, str]:
    if payload.get("schema_version") != APPROVAL_SCHEMA or payload.get("status") != "approved":
        raise MatrixError("independent observer approval is missing or invalid")
    if payload.get("scenario") != scenario or payload.get("expected_commit") != expected_head:
        raise MatrixError("observer approval is for a different scenario or commit")
    if payload.get("preflight_sha256") != preflight_sha256:
        raise MatrixError("observer approval is not bound to the supplied preflight")
    observer = str(payload.get("observer") or "").strip()
    commander = str(payload.get("incident_commander") or "").strip()
    normalized_roles = {value.casefold() for value in (operator.strip(), observer, commander)}
    if not operator.strip() or not observer or not commander or len(normalized_roles) != 3:
        raise MatrixError("operator, observer, and incident commander must be three distinct identities")
    if not reason.strip() or str(payload.get("reason") or "").strip() != reason.strip():
        raise MatrixError("execution reason must exactly match the approved incident reason")
    if not change_id.strip() or str(payload.get("change_id") or "").strip() != change_id.strip():
        raise MatrixError("execution change ID must exactly match the approval")
    if payload.get("max_control_requests") != 100:
        raise MatrixError("approval request budget is invalid")
    if payload.get("max_scenario_seconds") != MAX_SCENARIO_SECONDS:
        raise MatrixError("approval scenario runtime bound is invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", str(payload.get("authorization_nonce") or "")):
        raise MatrixError("approval authorization nonce is invalid")
    if not all(
        payload.get(field) is True
        for field in (
            "out_of_band_console_ready",
            "alternate_communications_ready",
            "maintenance_window_confirmed",
            "dpi_budget_confirmed",
            "restore_authorized",
        )
    ):
        raise MatrixError("observer approval lacks an operational safety assertion")
    try:
        approved = datetime.fromisoformat(str(payload.get("approved_at")).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(payload.get("expires_at")).replace("Z", "+00:00"))
        window_start = datetime.fromisoformat(
            str(payload.get("maintenance_window_start")).replace("Z", "+00:00")
        )
        window_end = datetime.fromisoformat(
            str(payload.get("maintenance_window_end")).replace("Z", "+00:00")
        )
        for value in (approved, expires, window_start, window_end):
            if value.tzinfo is None:
                raise ValueError("timezone is required")
    except (TypeError, ValueError) as exc:
        raise MatrixError("observer approval expiry is invalid") from exc
    now = datetime.now(timezone.utc)
    if approved > now + timedelta(seconds=30) or expires <= now or expires > approved + timedelta(hours=1):
        raise MatrixError("observer approval has expired")
    if window_start > now or window_end <= now or window_end > window_start + timedelta(hours=8):
        raise MatrixError("execution is outside the approved maintenance window")
    for field in (
        "change_id",
        "out_of_band_console",
        "alternate_communications",
        "maintenance_window_start",
        "maintenance_window_end",
    ):
        if not str(payload.get(field) or "").strip():
            raise MatrixError(f"approval lacks concrete {field}")
    if (
        not isinstance(payload.get("dpi_byte_budget"), int)
        or payload["dpi_byte_budget"] < MIN_DPI_BYTE_BUDGET
    ):
        raise MatrixError("approval all-transport DPI byte budget is invalid")
    if not str(payload.get("restore_authorized_by") or "").strip():
        raise MatrixError("approval lacks a named restore authorizer")
    if payload.get("restore_backup_sha256") != expected_restore_sha256:
        raise MatrixError("approval is not bound to the pinned restore backup")
    if payload.get("allowed_signers_sha256") != expected_allowed_signers_sha256:
        raise MatrixError("approval is not bound to the trusted signer policy")
    return observer, commander


def verify_approval_signature(
    approval_raw: bytes,
    signature_raw: bytes,
    *,
    identity: str,
    allowed_signers_raw: bytes,
) -> None:
    descriptors: list[int] = []
    try:
        for name, raw in (("allowed-signers", allowed_signers_raw), ("signature", signature_raw)):
            if not hasattr(os, "memfd_create"):
                raise MatrixError("immutable in-memory signature verification requires memfd_create")
            descriptor = os.memfd_create(f"writer-witness-{name}", flags=0)
            descriptors.append(descriptor)
            if os.write(descriptor, raw) != len(raw):
                raise MatrixError(f"failed to stage immutable {name} bytes")
            os.lseek(descriptor, 0, os.SEEK_SET)
        completed = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify", "-f", f"/proc/self/fd/{descriptors[0]}",
                "-I", identity, "-n", "writer-witness-matrix",
                "-s", f"/proc/self/fd/{descriptors[1]}",
            ],
            input=approval_raw,
            capture_output=True,
            pass_fds=tuple(descriptors),
        )
        if completed.returncode != 0:
            raise MatrixError(f"approval signature verification failed for {identity}")
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def assert_independent_signer_keys(
    allowed_signers_raw: bytes,
    observer: str,
    incident_commander: str,
) -> None:
    identities = {observer: set(), incident_commander: set()}
    try:
        rendered = allowed_signers_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MatrixError("trusted signer policy is not UTF-8") from exc
    for raw_line in rendered.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3:
            raise MatrixError("allowed-signers file contains an invalid entry")
        principals, key_type, key_data = fields[:3]
        fingerprint = f"{key_type} {key_data}"
        for identity in identities:
            if identity in principals.split(","):
                identities[identity].add(fingerprint)
    if not all(identities.values()):
        raise MatrixError("observer or incident commander lacks an allowed signing key")
    if identities[observer] & identities[incident_commander]:
        raise MatrixError("observer and incident commander must use independent signing keys")


def assert_source_pinned_signer_policy(allowed_signers_raw: bytes) -> str:
    expected = TRUSTED_ALLOWED_SIGNERS_SHA256
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise MatrixError(
            "independent signer policy is not yet pinned by the reviewed source"
        )
    observed = hashlib.sha256(allowed_signers_raw).hexdigest()
    if observed != expected:
        raise MatrixError("trusted signer policy differs from the reviewed source pin")
    return observed


def consume_approval(payload: dict[str, Any], root: Path) -> Path:
    nonce = str(payload["authorization_nonce"])
    consumed = secure_durable_child_directory(root, "consumed-approvals")
    path = consumed / nonce
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        raw = f"consumed_at={utc_now()}\n".encode()
        if os.write(descriptor, raw) != len(raw):
            raise MatrixError("failed to persist approval consumption")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(consumed)
    return path


def consume_preflight(preflight_sha256: str, scenario: str, root: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", preflight_sha256) or scenario not in SCENARIOS:
        raise MatrixError("preflight consumption identity is invalid")
    consumed = secure_durable_child_directory(root, "consumed-preflights")
    path = consumed / preflight_sha256
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        raw = f"consumed_at={utc_now()}\nscenario={scenario}\n".encode()
        if os.write(descriptor, raw) != len(raw):
            raise MatrixError("failed to persist preflight consumption")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(consumed)
    return path


class Controller:
    # Safe bounded defaults keep deliberately bare unit-test controllers on the
    # production wire schema; normal construction always installs instance
    # values derived from the signed approval or durable journal.
    campaign_not_after = (
        datetime.now(timezone.utc) + timedelta(seconds=MAX_SCENARIO_SECONDS)
    ).isoformat().replace("+00:00", "Z")
    remote_campaign_expired = False

    def __init__(
        self,
        *,
        scenario: str,
        operator: str,
        observer: str,
        incident_commander: str,
        reason: str,
        expected_head: str,
        preflight: dict[str, Any],
        baseline: dict[str, dict[str, str]],
        artifact_dir: Path,
        campaign_root: Path = DEFAULT_CAMPAIGN_ROOT,
        dpi_byte_budget: int = MIN_DPI_BYTE_BUDGET,
        campaign_not_after: str | None = None,
        tag: str | None = None,
        existing_journal: CampaignJournal | None = None,
    ) -> None:
        self.scenario = scenario
        self.operator = operator
        self.observer = observer
        self.incident_commander = incident_commander
        self.reason = reason
        self.expected_head = expected_head
        self.preflight = preflight
        self.baseline = baseline
        seed = f"{scenario}|{operator}|{observer}|{utc_now()}|{uuid.uuid4()}".encode()
        self.tag = tag or ("wwm_" + hashlib.sha256(seed).hexdigest()[:12])
        if not re.fullmatch(r"wwm_[0-9a-f]{12}", self.tag):
            raise MatrixError("campaign ownership tag is invalid")
        self.remote_root = f"/run/writer-witness-matrix/{self.tag}"
        self.artifact_dir = artifact_dir
        self.events_path = artifact_dir / "events.jsonl"
        self.summary_path = artifact_dir / "summary.json"
        secret_parent = secure_directory(DEFAULT_SECRET_ROOT)
        filesystem = subprocess.run(
            ["findmnt", "-n", "-o", "FSTYPE", "-T", str(secret_parent)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if filesystem != "tmpfs":
            raise MatrixError("controller Matrix secrets require a verified tmpfs path")
        self.local_secret_root = secret_parent / self.tag
        if existing_journal is None:
            try:
                self.local_secret_root.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise MatrixError("stale deterministic Matrix secret directory requires recovery") from exc
        else:
            self.local_secret_root.mkdir(mode=0o700, exist_ok=True)
        secure_directory(self.local_secret_root, create=False)
        self._event_lock = threading.Lock()
        self._budget_lock = threading.Lock()
        self.evidence_failed = False
        self.secret_output_detected = False
        self._secret_sentinels: set[str] = set()
        self.network_fault_sites: set[str] = set()
        self.staged_sites: set[str] = set()
        self.witness_mutated = False
        self.rotation_sites: set[str] = set()
        self.control_requests = 0
        self.control_bytes_upper_bound = 0
        self.transport_operations = 0
        self.transport_bytes_upper_bound = 0
        self.cleanup_transport_operations = 0
        self.cleanup_transport_bytes_upper_bound = 0
        self._transport_master_roles: set[str] = set()
        self._transport_master_deadlines: dict[str, float] = {}
        self.dpi_byte_budget = int(dpi_byte_budget)
        if self.dpi_byte_budget < MIN_DPI_BYTE_BUDGET:
            raise MatrixError("campaign DPI byte budget is below the closed transport minimum")
        self.started_at = utc_now()
        now = datetime.now(timezone.utc)
        if existing_journal is not None:
            campaign_not_after = str(
                existing_journal.payload.get("campaign_not_after") or ""
            )
            if not campaign_not_after:
                try:
                    legacy_created_at = datetime.fromisoformat(
                        str(existing_journal.payload.get("created_at") or "").replace(
                            "Z", "+00:00"
                        )
                    )
                    if legacy_created_at.tzinfo is None:
                        raise ValueError("timezone is required")
                    campaign_not_after = (
                        legacy_created_at.astimezone(timezone.utc)
                        + timedelta(seconds=MAX_SCENARIO_SECONDS)
                    ).isoformat().replace("+00:00", "Z")
                except (TypeError, ValueError) as exc:
                    raise MatrixError(
                        "legacy campaign journal cannot derive a bounded Witness expiry"
                    ) from exc
        elif campaign_not_after is None:
            # Unit-level callers use the same bounded default. Production
            # execute mode supplies the independently signed approval bound.
            campaign_not_after = (now + timedelta(seconds=MAX_SCENARIO_SECONDS)).isoformat().replace(
                "+00:00", "Z"
            )
        try:
            parsed_not_after = datetime.fromisoformat(
                str(campaign_not_after).replace("Z", "+00:00")
            )
            if parsed_not_after.tzinfo is None:
                raise ValueError("timezone is required")
            parsed_not_after = parsed_not_after.astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise MatrixError("campaign has an invalid Witness-enforced expiry") from exc
        if existing_journal is None and (
            parsed_not_after <= now
            or parsed_not_after > now + timedelta(seconds=MAX_SCENARIO_SECONDS + 30)
        ):
            raise MatrixError("campaign Witness expiry is outside the approved runtime")
        self.campaign_not_after = parsed_not_after.isoformat().replace("+00:00", "Z")
        self.remote_campaign_expired = False
        self._scenario_deadline = time.monotonic() + MAX_SCENARIO_SECONDS
        self.remote_campaign_claimed = False
        self.remote_campaign_conflict = False
        self.remote_campaign_ambiguous = False
        self._abort_event = threading.Event()
        self._abort_reason: str | None = None
        self._monitor_thread: threading.Thread | None = None
        self._cleanup_mode = False
        if existing_journal is None:
            campaign_path = campaign_root / f"{self.tag}.json"
            self.journal = CampaignJournal(
                campaign_path,
                {
                    "schema_version": RUNNER_SCHEMA,
                    "status": "active",
                    "lifecycle_phase": "controller_created",
                    "remote_campaign_protocol": "atomic-helper-v1",
                    "dirty": True,
                    "tag": self.tag,
                    "scenario": scenario,
                    "expected_commit": expected_head,
                    "operator": operator,
                    "observer": observer,
                    "incident_commander": incident_commander,
                    "reason": reason,
                    "dpi_byte_budget": self.dpi_byte_budget,
                    "max_scenario_seconds": MAX_SCENARIO_SECONDS,
                    "campaign_not_after": self.campaign_not_after,
                    "control_requests": 0,
                    "control_bytes_upper_bound": 0,
                    "transport_operations": 0,
                    "transport_bytes_upper_bound": 0,
                    "cleanup_transport_operations": 0,
                    "cleanup_transport_bytes_upper_bound": 0,
                    "created_at": self.started_at,
                    "updated_at": self.started_at,
                    "artifact_dir": str(artifact_dir),
                    "baseline": baseline,
                    "resources": {},
                },
                create=True,
            )
        else:
            self.journal = existing_journal
            self.staged_sites = self.journal.values("staged_sites")
            self.network_fault_sites = self.journal.values("network_fault_sites")
            self.rotation_sites = self.journal.values("rotation_sites")
            self.witness_mutated = bool(self.journal.payload.get("witness_mutated"))
            self.remote_campaign_claimed = bool(self.journal.payload.get("remote_campaign_claimed"))
            self.remote_campaign_conflict = bool(self.journal.payload.get("remote_campaign_conflict"))
            self.remote_campaign_ambiguous = bool(self.journal.payload.get("remote_campaign_ambiguous"))
            claim_state = str(self.journal.payload.get("remote_campaign_claim_state") or "")
            if (
                not self.remote_campaign_claimed
                and not self.remote_campaign_conflict
                and claim_state
                in {
                    "claiming",
                    "ambiguous_retrying",
                    "ambiguous",
                    "lost_or_foreign",
                }
            ):
                # A hard kill can land after the remote atomic claim but before
                # the local success bit. Repeating the exact idempotent claim is
                # the only safe reconciliation path.
                self.remote_campaign_ambiguous = True
            for attribute in (
                "control_requests",
                "control_bytes_upper_bound",
                "transport_operations",
                "transport_bytes_upper_bound",
                "cleanup_transport_operations",
                "cleanup_transport_bytes_upper_bound",
            ):
                value = self.journal.payload.get(attribute, 0)
                if not isinstance(value, int) or value < 0:
                    raise MatrixError(f"campaign journal has invalid {attribute}")
                setattr(self, attribute, value)
            if self.journal.payload.get("max_scenario_seconds") != MAX_SCENARIO_SECONDS:
                raise MatrixError("campaign journal runtime bound drifted")
            try:
                created_at = datetime.fromisoformat(
                    str(self.journal.payload.get("created_at") or "").replace("Z", "+00:00")
                )
                if created_at.tzinfo is None:
                    raise ValueError("timezone is required")
            except (TypeError, ValueError) as exc:
                raise MatrixError("campaign journal has an invalid bounded start time") from exc
            now = datetime.now(timezone.utc)
            if created_at > now + timedelta(seconds=30):
                raise MatrixError("campaign journal start time is in the future")
            elapsed = max(0.0, (now - created_at).total_seconds())
            self.started_at = created_at.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
            self._scenario_deadline = time.monotonic() + max(
                0.0, MAX_SCENARIO_SECONDS - elapsed
            )

    def event(self, event: str, **fields: Any) -> bool:
        payload = {
            "schema_version": RUNNER_SCHEMA,
            "at": utc_now(),
            "scenario": self.scenario,
            "tag": self.tag,
            "event": event,
            **fields,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with self._event_lock:
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(self.events_path, flags, 0o600)
                try:
                    metadata = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_uid != os.geteuid()
                        or stat.S_IMODE(metadata.st_mode) != 0o600
                        or metadata.st_nlink != 1
                    ):
                        raise OSError("Matrix evidence path is not one owner-only regular file")
                    raw = encoded.encode("utf-8")
                    if os.write(descriptor, raw) != len(raw):
                        raise OSError("short Matrix evidence write")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return True
        except OSError:
            self.evidence_failed = True
            return False

    def raise_if_abort_requested(self) -> None:
        if self._abort_reason is not None:
            raise MatrixAbort(self._abort_reason)
        if not self._cleanup_mode and time.monotonic() > getattr(
            self, "_scenario_deadline", float("inf")
        ):
            self._abort_reason = "Matrix scenario exceeded its approved 900-second runtime"
            raise MatrixAbort(self._abort_reason)

    def raise_if_cleanup_deadline_exceeded(self) -> None:
        if time.monotonic() > getattr(self, "_cleanup_deadline", float("inf")):
            raise MatrixAbort("Matrix cleanup exceeded its separate 900-second recovery bound")

    def enter_cleanup_mode(self) -> None:
        """Start one non-renewable cleanup allowance for this controller run."""

        self._cleanup_mode = True
        if not hasattr(self, "_cleanup_deadline"):
            self._cleanup_deadline = time.monotonic() + MAX_CLEANUP_SECONDS
        self.raise_if_cleanup_deadline_exceeded()

    def _reserve_transport_budget(
        self,
        *,
        role: str,
        kind: str,
        payload_bytes: int,
        transfer_bytes: int = 0,
        force_master_reservation: bool = False,
    ) -> int:
        if role not in HOSTS:
            raise MatrixError("transport budget role is invalid")
        if kind not in {"ssh", "scp"}:
            raise MatrixError("transport budget kind is invalid")
        if payload_bytes < 0 or payload_bytes > SSH_COMMAND_PAYLOAD_MAX:
            raise MatrixError("transport command exceeds its closed payload bound")
        if transfer_bytes < 0 or transfer_bytes > SCP_FILE_BYTES_MAX:
            raise MatrixError("transport file exceeds its closed transfer bound")
        base = SSH_COMMAND_BYTES_UPPER_BOUND if kind == "ssh" else SCP_SESSION_BYTES_UPPER_BOUND
        with self._budget_lock:
            roles = self._transport_master_roles
            deadlines = getattr(self, "_transport_master_deadlines", {})
            now = time.monotonic()
            reusable = role in roles and now < deadlines.get(role, 0.0)
            if role in roles and not reusable:
                roles.discard(role)
                deadlines.pop(role, None)
            amount = base + payload_bytes + transfer_bytes
            # OpenSSH may transparently replace a dead ControlMaster and still
            # return success. Charge one complete handshake on every operation
            # so accounting never depends on observing that internal reconnect.
            amount += SSH_MASTER_BYTES_UPPER_BOUND
            if self._cleanup_mode:
                next_operations = self.cleanup_transport_operations + 1
                next_bytes = self.cleanup_transport_bytes_upper_bound + amount
                if (
                    next_operations > MAX_CLEANUP_TRANSPORT_OPERATIONS
                    or next_bytes > CLEANUP_TRANSPORT_BYTES_RESERVE
                ):
                    raise MatrixError("cleanup exceeded its pre-authorized transport reserve")
                self.cleanup_transport_operations = next_operations
                self.cleanup_transport_bytes_upper_bound = next_bytes
            else:
                next_operations = self.transport_operations + 1
                next_bytes = self.transport_bytes_upper_bound + amount
                non_cleanup_ceiling = self.dpi_byte_budget - CLEANUP_TRANSPORT_BYTES_RESERVE
                if (
                    next_operations > MAX_SCENARIO_TRANSPORT_OPERATIONS
                    or self.control_bytes_upper_bound + next_bytes > non_cleanup_ceiling
                ):
                    raise MatrixError("scenario exceeded its approved all-transport DPI budget")
                self.transport_operations = next_operations
                self.transport_bytes_upper_bound = next_bytes
            self.journal.update(
                control_requests=self.control_requests,
                control_bytes_upper_bound=self.control_bytes_upper_bound,
                transport_operations=self.transport_operations,
                transport_bytes_upper_bound=self.transport_bytes_upper_bound,
                cleanup_transport_operations=self.cleanup_transport_operations,
                cleanup_transport_bytes_upper_bound=self.cleanup_transport_bytes_upper_bound,
                dirty=True,
            )
        return amount

    def _mark_transport_master(self, role: str) -> None:
        """Record a reusable SSH master only after a successful transport operation."""
        with self._budget_lock:
            self._transport_master_roles.add(role)
            deadlines = getattr(self, "_transport_master_deadlines", None)
            if deadlines is None:
                deadlines = {}
                self._transport_master_deadlines = deadlines
            # OpenSSH ControlPersist is 30 seconds.  Expire our accounting five
            # seconds earlier so a command can never reuse a logically cached
            # master after sshd has already removed the socket.
            deadlines[role] = time.monotonic() + 25.0

    def _forget_transport_master(self, role: str) -> None:
        """Force the next operation to reserve a fresh SSH handshake."""
        with self._budget_lock:
            self._transport_master_roles.discard(role)
            getattr(self, "_transport_master_deadlines", {}).pop(role, None)

    def _abort_probe(self) -> None:
        true_pattern = "^(1|true|t|yes|y|on)$"
        probes = (
            (
                "webapp_fi",
                "set -Eeuo pipefail; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "for c in trading_bot_app trading_bot_db trading_bot_redis trading_bot_sync_worker; do "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$c\")\" = running; done; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' trading_bot_app)\" = healthy; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' trading_bot_db)\" = healthy; "
                "curl -fsS http://127.0.0.1:8000/api/config >/dev/null; "
                "if { sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p' /srv/trading-bot/current/.env; "
                "for c in trading_bot_app trading_bot_sync_worker; do docker inspect \"$c\" --format "
                "'{{range .Config.Env}}{{println .}}{{end}}'; done "
                "| sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p'; } "
                f"| awk -F= '{{v=tolower($2); gsub(/[[:space:]]/,\"\",v); if (v ~ /{true_pattern}/) found=1}} END {{exit found ? 0 : 1}}'; "
                "then exit 41; fi; "
                "! grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))=' /srv/trading-bot/current/.env; "
                "for c in trading_bot_app trading_bot_sync_worker; do ! docker inspect \"$c\" --format "
                "'{{range .Config.Env}}{{println .}}{{end}}' | grep -Eq "
                "'^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))='; done",
            ),
            (
                "webapp_ir",
                "set -Eeuo pipefail; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "app=$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=app); "
                "sync=$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=sync_worker); "
                "db=$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=db); "
                "test -n \"$app\"; test -n \"$sync\"; test -n \"$db\"; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$app\")\" != running; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$sync\")\" != running; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$db\")\" = running; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' \"$db\")\" = healthy; "
                "if { sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p' /srv/trading-bot/current/.env; "
                "for c in \"$app\" \"$sync\"; do docker inspect \"$c\" --format "
                "'{{range .Config.Env}}{{println .}}{{end}}'; done "
                "| sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p'; } "
                f"| awk -F= '{{v=tolower($2); gsub(/[[:space:]]/,\"\",v); if (v ~ /{true_pattern}/) found=1}} END {{exit found ? 0 : 1}}'; "
                "then exit 42; fi; "
                "! grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))=' /srv/trading-bot/current/.env; "
                "for c in \"$app\" \"$sync\"; do ! docker inspect \"$c\" --format "
                "'{{range .Config.Env}}{{println .}}{{end}}' | grep -Eq "
                "'^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))='; done",
            ),
            (
                "rollback_witness",
                "set -Eeuo pipefail; "
                + rollback_manifest_helper_guard()
                + "; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "test \"$(/usr/local/sbin/writer-witness-state-manifest)\" = "
                f"{shlex.quote(self.baseline['rollback_witness_baseline']['manifest_sha256'])}; "
                "test \"$(systemctl is-active writer-witness.service)\" = active",
            ),
        )
        for role, command in probes:
            self._reserve_transport_budget(
                role=role,
                kind="ssh",
                payload_bytes=len(command.encode("utf-8")),
            )
            try:
                completed = subprocess.run(
                    self.ssh_args(HOSTS[role], command),
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except BaseException:
                self._forget_transport_master(role)
                raise
            if completed.returncode != 0:
                self._forget_transport_master(role)
                raise MatrixAbort(f"continuous abort probe failed for {role}")
            self._mark_transport_master(role)

    def _monitor_loop(self) -> None:
        while not self._abort_event.is_set():
            try:
                self._abort_probe()
            except BaseException as exc:
                self._abort_reason = str(exc) or exc.__class__.__name__
                self.event("abort_monitor.failed", reason=self._abort_reason)
                self._abort_event.set()
                return
            self._abort_event.wait(ABORT_MONITOR_INTERVAL_SECONDS)

    def start_abort_monitor(self) -> None:
        self._abort_probe()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name=f"{self.tag}-abort-monitor",
            daemon=True,
        )
        self._monitor_thread.start()
        self.event("abort_monitor.started")

    def stop_abort_monitor(self) -> None:
        self._abort_event.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=30)
            if self._monitor_thread.is_alive():
                self._abort_reason = self._abort_reason or "continuous abort monitor did not stop"
            self._monitor_thread = None
        self.event("abort_monitor.stopped", reason=self._abort_reason)

    def interruptible_sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self.raise_if_abort_requested()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._abort_event.wait(min(0.25, remaining))

    def claim(self, resource: str, value: str) -> None:
        self.journal.claim(resource, value)

    def mark_witness_may_mutate(self) -> None:
        # Every state-changing Witness path must reacquire a server-clock
        # authorization decision. The controller deadline is only an
        # additional abort mechanism, never the authority clock.
        self.assert_remote_campaign_owned()
        if not self.witness_mutated:
            self.witness_mutated = True
            self.journal.update(witness_mutated=True, status="active", dirty=True)

    def remote_campaign_command(
        self,
        operation: str,
        *,
        expect: str | None = None,
        authorization_nonce: str | None = None,
        preflight_sha256: str | None = None,
    ) -> str:
        arguments = [
            "/usr/bin/env", "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            "/usr/bin/python3.12", "-I", "-S", "-B", "-X", "utf8",
            "-X", "pycache_prefix=/dev/null",
            "/usr/local/sbin/writer-witness-matrix-campaign",
            operation,
            "--tag",
            self.tag,
            "--expected-commit",
            self.expected_head,
            "--scenario",
            self.scenario,
            "--not-after",
            self.campaign_not_after,
        ]
        if expect is not None:
            arguments.extend(("--expect", expect))
        if authorization_nonce is not None:
            arguments.extend(("--authorization-nonce", authorization_nonce))
        if preflight_sha256 is not None:
            arguments.extend(("--preflight-sha256", preflight_sha256))
        return " ".join(shlex.quote(item) for item in arguments)

    def inspect_remote_campaign(self, label: str) -> str:
        completed = self.remote(
            "matrix_witness",
            label,
            self.remote_campaign_command("inspect"),
            check=False,
        )
        if completed.returncode != 0:
            raise MatrixError("replacement Witness campaign inspection failed")
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise MatrixError("replacement Witness campaign inspection is not one exact record")
        try:
            payload = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            raise MatrixError("replacement Witness campaign inspection is invalid JSON") from exc
        expected_identity = {
            "tag": self.tag,
            "expected_commit": self.expected_head,
            "scenario": self.scenario,
            "not_after": self.campaign_not_after,
        }
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "inspected"
            or any(payload.get(key) != value for key, value in expected_identity.items())
        ):
            raise MatrixError("replacement Witness campaign inspection identity is invalid")
        state = payload.get("state")
        expired = payload.get("expired")
        active_relation = payload.get("active_relation")
        release_relation = payload.get("release_relation")
        valid_relations = {"absent", "exact", "foreign"}
        valid_states = {
            "active_exact",
            "active_foreign",
            "released_exact",
            "released_foreign",
            "absent",
        }
        if (
            state not in valid_states
            or active_relation not in valid_relations
            or release_relation not in valid_relations
            or not isinstance(expired, bool)
        ):
            raise MatrixError("replacement Witness campaign inspection classification is invalid")
        if (
            (state == "active_exact" and active_relation != "exact")
            or (state == "active_foreign" and active_relation != "foreign")
            or (state == "released_exact" and release_relation != "exact")
            or (state == "released_foreign" and release_relation != "foreign")
            or (
                state == "absent"
                and (active_relation != "absent" or release_relation != "absent")
            )
        ):
            raise MatrixError("replacement Witness campaign inspection is internally inconsistent")
        self.remote_campaign_expired = expired
        self.journal.update(
            remote_campaign_inspection_state=state,
            remote_campaign_expired=expired,
            dirty=True,
        )
        return str(state)

    def mark_remote_campaign_ambiguous(self, state: str, *, conflict: bool = False) -> None:
        self.remote_campaign_claimed = False
        self.remote_campaign_ambiguous = not conflict
        self.remote_campaign_conflict = conflict
        self.journal.update(
            remote_campaign_claimed=False,
            remote_campaign_ambiguous=not conflict,
            remote_campaign_conflict=conflict,
            remote_campaign_claim_state=state,
            dirty=True,
        )

    def claim_remote_campaign(self) -> None:
        self.journal.update(
            lifecycle_phase="remote_claim_intent",
            remote_campaign_claim_state="claiming",
            dirty=True,
        )
        self.claim("remote_campaign", self.tag)
        command = self.remote_campaign_command("claim")
        try:
            for attempt in (1, 2):
                try:
                    completed = self.remote(
                        "matrix_witness",
                        f"claim_remote_campaign_attempt_{attempt}",
                        command,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    self.journal.update(
                        remote_campaign_claim_state="ambiguous_retrying",
                        remote_campaign_ambiguous=True,
                        dirty=True,
                    )
                    continue
                if completed.returncode == 0:
                    break
            inspected_state = self.inspect_remote_campaign(
                "inspect_claimed_remote_campaign"
            )
        except Exception as exc:
            self.mark_remote_campaign_ambiguous("ambiguous")
            raise MatrixError("replacement Witness campaign ownership is ambiguous") from exc
        if inspected_state != "active_exact":
            if inspected_state in {
                "active_foreign",
                "released_exact",
                "released_foreign",
            }:
                self.mark_remote_campaign_ambiguous(
                    inspected_state,
                    conflict=True,
                )
                raise MatrixError("replacement Witness campaign is owned by foreign or stale state")
            self.mark_remote_campaign_ambiguous("ambiguous")
            raise MatrixError("replacement Witness campaign ownership is ambiguous")
        self.remote_campaign_claimed = True
        self.remote_campaign_ambiguous = False
        self.remote_campaign_conflict = False
        self.journal.update(
            lifecycle_phase="remote_claimed",
            remote_campaign_claimed=True,
            remote_campaign_ambiguous=False,
            remote_campaign_conflict=False,
            remote_campaign_claim_state="owned",
            dirty=True,
        )

    def assert_remote_campaign_owned(self, *, allow_expired_cleanup: bool = False) -> None:
        try:
            inspected_state = self.inspect_remote_campaign(
                "verify_remote_campaign_ownership"
            )
        except Exception as exc:
            self.mark_remote_campaign_ambiguous("lost_or_foreign")
            raise MatrixError("replacement Witness campaign ownership is not proven") from exc
        if inspected_state != "active_exact":
            self.mark_remote_campaign_ambiguous(
                inspected_state,
                conflict=inspected_state != "absent",
            )
            raise MatrixError("replacement Witness campaign ownership is not proven")
        if self.remote_campaign_expired and not allow_expired_cleanup:
            raise MatrixError("replacement Witness campaign authorization has expired")
        self.remote_campaign_claimed = True
        self.remote_campaign_ambiguous = False
        self.journal.update(
            remote_campaign_claimed=True,
            remote_campaign_ambiguous=False,
            remote_campaign_claim_state="owned",
            dirty=True,
        )

    def assert_remote_campaign_released(self) -> None:
        try:
            inspected_state = self.inspect_remote_campaign(
                "verify_remote_campaign_release_tombstone"
            )
        except Exception as exc:
            self.journal.update(remote_campaign_release_state="ambiguous", dirty=True)
            raise MatrixError("replacement Witness campaign release tombstone is not proven") from exc
        if inspected_state != "released_exact":
            self.journal.update(remote_campaign_release_state="ambiguous", dirty=True)
            raise MatrixError("replacement Witness campaign release tombstone is not proven")
        self.remote_campaign_claimed = False
        self.remote_campaign_ambiguous = False
        self.journal.update(
            remote_campaign_claimed=False,
            remote_campaign_ambiguous=False,
            remote_campaign_release_state="released",
            dirty=True,
        )

    def consume_remote_authorization(self, authorization_nonce: str, preflight_sha256: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", authorization_nonce):
            raise MatrixError("remote approval consumption nonce is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", preflight_sha256):
            raise MatrixError("remote preflight consumption hash is invalid")
        self.journal.update(
            lifecycle_phase="remote_authorization_intent",
            remote_authorization_consumption="pending",
            dirty=True,
        )
        command = self.remote_campaign_command(
            "consume",
            authorization_nonce=authorization_nonce,
            preflight_sha256=preflight_sha256,
        )
        completed: subprocess.CompletedProcess[str] | None = None
        try:
            for attempt in (1, 2):
                try:
                    candidate = self.remote(
                        "matrix_witness",
                        f"consume_remote_approval_and_preflight_attempt_{attempt}",
                        command,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    self.journal.update(
                        remote_authorization_consumption="ambiguous_retrying",
                        dirty=True,
                    )
                    continue
                if candidate.returncode == 0:
                    completed = candidate
                    break
            asserted = self.remote(
                "matrix_witness",
                "assert_remote_campaign_after_authorization",
                self.remote_campaign_command("assert", expect="active"),
                check=False,
            )
        except Exception as exc:
            self.journal.update(remote_authorization_consumption="ambiguous", dirty=True)
            raise MatrixError("remote approval/preflight consumption is ambiguous") from exc
        if completed is None or asserted.returncode != 0:
            self.journal.update(remote_authorization_consumption="ambiguous", dirty=True)
            raise MatrixError("remote approval/preflight consumption is ambiguous")
        self.journal.update(
            lifecycle_phase="remote_authorization_consumed",
            remote_authorization_consumption="consumed",
            remote_approval_nonce=authorization_nonce,
            remote_preflight_sha256=preflight_sha256,
            dirty=True,
        )

    def critical_precheck(self) -> None:
        self.assert_remote_campaign_owned()
        true_pattern = "^(1|true|t|yes|y|on)$"
        self.remote(
            "webapp_fi",
            "critical_precheck_webapp_fi",
            "set -Eeuo pipefail; "
            "test \"$(docker inspect -f '{{.State.Health.Status}}' trading_bot_app)\" = healthy; "
            "test \"$(docker inspect -f '{{.State.Health.Status}}' trading_bot_db)\" = healthy; "
            "test \"$(findmnt -n -o FSTYPE -T /run)\" = tmpfs; "
            "if { sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p' /srv/trading-bot/current/.env; "
            "for c in trading_bot_app trading_bot_sync_worker; do docker inspect \"$c\" --format "
            "'{{range .Config.Env}}{{println .}}{{end}}'; done "
            "| sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p'; } "
            f"| awk -F= '{{v=tolower($2); gsub(/[[:space:]]/,\"\",v); if (v ~ /{true_pattern}/) found=1}} END {{exit found ? 0 : 1}}'; "
            "then exit 41; fi; "
            "! grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))=' /srv/trading-bot/current/.env; "
            "for c in trading_bot_app trading_bot_sync_worker; do ! docker inspect \"$c\" --format "
            "'{{range .Config.Env}}{{println .}}{{end}}' | grep -Eq "
            "'^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))='; done; "
            "test -z \"$(find /run/writer-witness-matrix -mindepth 1 -print -quit 2>/dev/null)\"",
        )
        self.remote(
            "webapp_ir",
            "critical_precheck_webapp_ir",
            "set -Eeuo pipefail; "
            "app=$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=app); "
            "sync=$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=sync_worker); "
            "test -n \"$app\"; test -n \"$sync\"; "
            "test \"$(findmnt -n -o FSTYPE -T /run)\" = tmpfs; "
            "test \"$(docker inspect -f '{{.State.Status}}' \"$app\")\" != running; "
            "test \"$(docker inspect -f '{{.State.Status}}' \"$sync\")\" != running; "
            "if { sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p' /srv/trading-bot/current/.env; "
            "for c in \"$app\" \"$sync\"; do docker inspect \"$c\" --format "
            "'{{range .Config.Env}}{{println .}}{{end}}'; done "
            "| sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p'; } "
            f"| awk -F= '{{v=tolower($2); gsub(/[[:space:]]/,\"\",v); if (v ~ /{true_pattern}/) found=1}} END {{exit found ? 0 : 1}}'; "
            "then exit 42; fi; "
            "! grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))=' /srv/trading-bot/current/.env; "
            "for c in \"$app\" \"$sync\"; do ! docker inspect \"$c\" --format "
            "'{{range .Config.Env}}{{println .}}{{end}}' | grep -Eq "
            "'^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))='; done; "
            "test -z \"$(find /run/writer-witness-matrix -mindepth 1 -print -quit 2>/dev/null)\"",
        )
        matrix = self.witness_state("matrix_witness")
        original = self.witness_state("rollback_witness")
        if matrix["manifest_sha256"] != self.baseline["matrix_witness_dark_baseline"]["manifest_sha256"]:
            raise MatrixError("replacement Witness drifted after preflight")
        if original["manifest_sha256"] != self.baseline["rollback_witness_baseline"]["manifest_sha256"]:
            raise MatrixError("reference Witness drifted after preflight")
        if self.credential_bundle_sha256() != self.baseline["matrix_witness_dark_baseline"]["credential_bundle_sha256"]:
            raise MatrixError("replacement Witness credentials drifted after preflight")
        self.remote(
            "matrix_witness",
            "critical_precheck_witness",
            "set -Eeuo pipefail; "
            "test ! -e /var/lib/trading-bot-witness/restore-state/active.env; "
            "test ! -L /var/lib/trading-bot-witness/restore-state/active.env; "
            "test -z \"$(find /var/lib/trading-bot-witness/restore-state -mindepth 1 -maxdepth 1 "
            "-name '.active.*.env' -print -quit)\"; "
            "test ! -e /var/lib/trading-bot-witness/hmac-rotation/webapp_fi; "
            "test ! -e /var/lib/trading-bot-witness/hmac-rotation/webapp_ir",
        )
        self.event("critical_precheck.passed")

    def release_remote_campaign(self) -> None:
        if not self.remote_campaign_claimed and self.tag not in self.journal.values("remote_campaign"):
            return
        command = self.remote_campaign_command("release")
        try:
            for attempt in (1, 2):
                try:
                    completed = self.remote(
                        "matrix_witness",
                        f"release_remote_campaign_attempt_{attempt}",
                        command,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    self.journal.update(
                        remote_campaign_release_state="ambiguous_retrying",
                        dirty=True,
                    )
                    continue
                if completed.returncode == 0:
                    break
            inspected_state = self.inspect_remote_campaign(
                "inspect_released_remote_campaign"
            )
        except Exception as exc:
            self.journal.update(remote_campaign_release_state="ambiguous", dirty=True)
            raise MatrixError("replacement Witness campaign release is ambiguous") from exc
        if inspected_state != "released_exact":
            self.journal.update(remote_campaign_release_state="ambiguous", dirty=True)
            raise MatrixError("replacement Witness campaign release is ambiguous")
        self.remote_campaign_claimed = False
        self.remote_campaign_ambiguous = False
        self.journal.update(
            lifecycle_phase="remote_campaign_released",
            remote_campaign_claimed=False,
            remote_campaign_ambiguous=False,
            remote_campaign_release_state="released",
            dirty=True,
        )

    def _ssh_control_path(self, host: Host) -> Path:
        identity = hashlib.sha256(f"{host.address}:{host.port}".encode()).hexdigest()[:16]
        return self.local_secret_root / f"ssh-{identity}.sock"

    def ssh_args(self, host: Host, command: str) -> list[str]:
        args = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=30",
            "-o", f"ControlPath={self._ssh_control_path(host)}",
        ]
        if host.port != 22:
            args.extend(("-p", str(host.port)))
        args.extend((f"root@{host.address}", command))
        return args

    def scp_args(self, host: Host, source: str, destination: str, *, from_remote: bool) -> list[str]:
        args = [
            "scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes", "-o", "ControlMaster=auto",
            "-o", "ControlPersist=30", "-o", f"ControlPath={self._ssh_control_path(host)}",
        ]
        if host.port != 22:
            args.extend(("-P", str(host.port)))
        if from_remote:
            args.extend((f"root@{host.address}:{source}", destination))
        else:
            args.extend((source, f"root@{host.address}:{destination}"))
        return args

    def command(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 30,
        check: bool = True,
        record_output: bool = True,
        transport_role: str | None = None,
        transport_kind: str | None = None,
        transport_payload_bytes: int = 0,
        transport_file_bytes: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        if self._cleanup_mode:
            self.raise_if_cleanup_deadline_exceeded()
            remaining_cleanup_seconds = self._cleanup_deadline - time.monotonic()
            if remaining_cleanup_seconds <= 0:
                self.raise_if_cleanup_deadline_exceeded()
            effective_timeout: float = min(float(timeout), remaining_cleanup_seconds)
        else:
            self.raise_if_abort_requested()
            effective_timeout = float(timeout)
        intent_recorded = self.event(
            "command.start",
            name=name,
            command_sha256=hashlib.sha256("\0".join(args).encode()).hexdigest(),
        )
        if not intent_recorded and not self._cleanup_mode:
            raise MatrixError("command refused because its evidence intent could not be persisted")
        if transport_role is not None:
            if transport_kind is None:
                raise MatrixError("transport command is missing its budget kind")
            self._reserve_transport_budget(
                role=transport_role,
                kind=transport_kind,
                payload_bytes=transport_payload_bytes,
                transfer_bytes=transport_file_bytes,
            )
        try:
            completed = subprocess.run(
                args,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except BaseException:
            if transport_role is not None:
                # Timeout, cancellation, and local execution failures make the
                # multiplexed connection state ambiguous.  The next attempt
                # must reserve a full handshake before any retry.
                self._forget_transport_master(transport_role)
            raise
        if transport_role is not None:
            if completed.returncode == 0:
                self._mark_transport_master(transport_role)
            else:
                # A non-zero SSH/SCP result can be a remote-command error or a
                # dead multiplexed transport. Conservatively forget the master
                # so the next attempt reserves a complete handshake again.
                self._forget_transport_master(transport_role)
        stdout = self.redact_command_output(completed.stdout[-12000:]) if record_output else "[redacted transient transfer]"
        stderr = self.redact_command_output(completed.stderr[-12000:]) if record_output else "[redacted transient transfer]"
        completion_recorded = self.event(
            "command.end", name=name, return_code=completed.returncode,
            stdout=stdout, stderr=stderr,
        )
        if not completion_recorded and not self._cleanup_mode:
            raise MatrixError(
                "command outcome is ambiguous because its evidence result could not be persisted"
            )
        if self.secret_output_detected and not self._cleanup_mode:
            raise MatrixError("command output contained Matrix secret material and was redacted")
        if check and completed.returncode != 0:
            raise MatrixError(f"{name} failed with exit code {completed.returncode}")
        if self._cleanup_mode:
            self.raise_if_cleanup_deadline_exceeded()
        else:
            self.raise_if_abort_requested()
        return completed

    def redact_command_output(self, value: str) -> str:
        rendered = value
        leaked = False
        for sentinel in self._secret_sentinels:
            if len(sentinel) >= 16 and sentinel in rendered:
                rendered = rendered.replace(sentinel, "[REDACTED_MATRIX_SECRET]")
                leaked = True
        redacted = re.sub(
            r"(?i)(WRITER_WITNESS_[A-Z0-9_]*SECRET=)[^\s]+",
            r"\1[REDACTED]",
            rendered,
        )
        leaked = leaked or redacted != rendered
        if leaked:
            self.secret_output_detected = True
        return redacted

    def remote(
        self,
        role: str,
        name: str,
        command: str,
        *,
        timeout: int = 30,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.command(
            name,
            self.ssh_args(HOSTS[role], command),
            timeout=timeout,
            check=check,
            transport_role=role,
            transport_kind="ssh",
            transport_payload_bytes=len(command.encode("utf-8")),
        )

    def transfer_from(self, role: str, remote_path: str, local_path: Path, name: str) -> None:
        if len(remote_path.encode("utf-8")) > SSH_COMMAND_PAYLOAD_MAX:
            raise MatrixError("remote transfer path exceeds its closed bound")
        self.command(
            name,
            self.scp_args(HOSTS[role], remote_path, str(local_path), from_remote=True),
            timeout=30,
            record_output=False,
            transport_role=role,
            transport_kind="scp",
            transport_payload_bytes=len(remote_path.encode("utf-8")),
            transport_file_bytes=SCP_FILE_BYTES_MAX,
        )
        os.chmod(local_path, 0o600)
        if local_path.stat().st_size > SCP_FILE_BYTES_MAX:
            raise MatrixError("remote transfer exceeded its reserved file bound")

    def transfer_to(self, role: str, local_path: Path, remote_path: str, name: str) -> None:
        transfer_bytes = local_path.stat().st_size
        if transfer_bytes > SCP_FILE_BYTES_MAX:
            raise MatrixError("local transfer exceeds its closed file bound")
        self.command(
            name,
            self.scp_args(HOSTS[role], str(local_path), remote_path, from_remote=False),
            timeout=30,
            record_output=False,
            transport_role=role,
            transport_kind="scp",
            transport_payload_bytes=len(remote_path.encode("utf-8")),
            transport_file_bytes=transfer_bytes,
        )

    def stage_site(
        self,
        site: str,
        *,
        remote_name: str = "client.env",
        retain_previous: bool = False,
    ) -> None:
        if site not in {"webapp_fi", "webapp_ir"}:
            raise MatrixError("unsupported WebApp Matrix site")
        short = site.removeprefix("webapp_")
        previous_env = self.local_secret_root / f"{site}-previous.env"
        if site not in self.journal.values("rotation_sites"):
            if retain_previous:
                self.transfer_from(
                    "matrix_witness",
                    f"/root/writer-witness-client-material/webapp-{short}.env",
                    previous_env,
                    f"fetch_{site}_previous_env",
                )
            self.claim("rotation_sites", site)
            self.rotation_sites.add(site)
            self.remote(
                "matrix_witness",
                f"prepare_{site}_matrix_credential",
                f"{REMOTE_ISOLATED_PYTHON} /usr/local/sbin/writer-witness-rotate-hmac "
                f"prepare --site {site} --expected-epoch 0 --campaign-tag {self.tag} "
                f"--campaign-not-after {shlex.quote(self.campaign_not_after)}",
            )
        self.claim("staged_sites", site)
        self.staged_sites.add(site)
        local_env = self.local_secret_root / f"{site}-{remote_name}"
        local_ca = self.local_secret_root / "witness-ca.crt"
        self.transfer_from(
            "matrix_witness",
            f"/root/writer-witness-client-material/webapp-{short}.env",
            local_env,
            f"fetch_{site}_{remote_name}",
        )
        values = owner_only_env(local_env)
        secret = values.get("WRITER_WITNESS_CLIENT_SECRET", "")
        if len(secret.encode("utf-8")) < 32:
            raise MatrixError("staged Matrix credential secret is invalid")
        self._secret_sentinels.add(secret)
        if retain_previous:
            previous_values = owner_only_env(previous_env)
            previous_secret = previous_values.get("WRITER_WITNESS_CLIENT_SECRET", "")
            if len(previous_secret.encode("utf-8")) < 32:
                raise MatrixError("previous Matrix credential secret is invalid")
            self._secret_sentinels.add(previous_secret)
        parsed_url = urlparse(values.get("WRITER_WITNESS_INTERNAL_URL", ""))
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != HOSTS["matrix_witness"].address
            or (parsed_url.port or 443) != 443
            or parsed_url.path not in {"", "/"}
        ):
            raise MatrixError("staged Matrix credential does not target the replacement Witness")
        key_id = values.get("WRITER_WITNESS_CLIENT_KEY_ID", "")
        if not key_id:
            raise MatrixError("staged Matrix credential lacks a key ID")
        self.claim("matrix_key_ids", key_id)
        self.remote(
            "rollback_witness",
            f"prove_{site}_matrix_key_absent_from_original",
            "set -Eeuo pipefail; "
            f"key_id={shlex.quote(key_id)}; "
            "! grep -R -F -l -- \"$key_id\" /etc/trading-bot-witness/runtime.env "
            "/root/writer-witness-client-material 2>/dev/null",
        )
        self.claim("original_key_absence_sites", site)
        if not local_ca.exists():
            self.transfer_from(
                "matrix_witness",
                "/root/writer-witness-client-material/witness-ca.crt",
                local_ca,
                "fetch_witness_ca",
            )
        self.remote(
            site,
            f"prepare_{site}_runtime_dir",
            f"install -d -m 0700 -o root -g root {shlex.quote(self.remote_root)}",
        )
        self.transfer_to(site, local_env, f"{self.remote_root}/{remote_name}", f"stage_{site}_{remote_name}")
        if retain_previous:
            self.transfer_to(site, previous_env, f"{self.remote_root}/old.env", f"stage_{site}_old_env")
        self.transfer_to(site, local_ca, f"{self.remote_root}/witness-ca.crt", f"stage_{site}_ca")
        self.transfer_to(site, CLIENT_SCRIPT, f"{self.remote_root}/client.py", f"stage_{site}_client")
        self.remote(
            site,
            f"secure_{site}_runtime_files",
            f"chmod 0600 {shlex.quote(self.remote_root)}/client.env {shlex.quote(self.remote_root)}/witness-ca.crt 2>/dev/null || true; "
            f"chmod 0600 {shlex.quote(self.remote_root)}/{shlex.quote(remote_name)}; "
            f"test ! -e {shlex.quote(self.remote_root)}/old.env || chmod 0600 {shlex.quote(self.remote_root)}/old.env; "
            f"key_id=$(sed -n 's/^WRITER_WITNESS_CLIENT_KEY_ID=//p' {shlex.quote(self.remote_root)}/{shlex.quote(remote_name)}); "
            "test -n \"$key_id\"; "
            "for container in $(docker ps -aq 2>/dev/null); do "
            "docker inspect \"$container\" --format '{{range .Config.Env}}{{println .}}{{end}}' "
            "| grep -F -- \"$key_id\" >/dev/null && exit 44 || true; done; "
            f"chmod 0700 {shlex.quote(self.remote_root)}/client.py",
        )

    def client(
        self,
        site: str,
        command: str,
        *,
        request_id: str,
        env_name: str = "client.env",
        expected_statuses: tuple[int, ...] = (200,),
        action: str | None = None,
        expected_epoch: int | None = None,
        expected_lease_id: str | None = None,
        reason: str | None = None,
        lease_seconds: int = 30,
        timestamp_offset: int = 0,
        not_before_unix_ms: int | None = None,
        check: bool = True,
        timeout: int = 15,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
        with self._budget_lock:
            next_requests = self.control_requests + 1
            next_bytes = self.control_bytes_upper_bound + CONTROL_REQUEST_BYTES_UPPER_BOUND
            if next_requests > MAX_CONTROL_REQUESTS:
                raise MatrixError("scenario exceeded the approved 100-request control-plane budget")
            if self._cleanup_mode:
                if (
                    self.cleanup_transport_bytes_upper_bound + CONTROL_REQUEST_BYTES_UPPER_BOUND
                    > CLEANUP_TRANSPORT_BYTES_RESERVE
                ):
                    raise MatrixError("cleanup exceeded its pre-authorized transport reserve")
                self.cleanup_transport_bytes_upper_bound += CONTROL_REQUEST_BYTES_UPPER_BOUND
            elif next_bytes + self.transport_bytes_upper_bound > (
                self.dpi_byte_budget - CLEANUP_TRANSPORT_BYTES_RESERVE
            ):
                raise MatrixError("scenario exceeded its approved all-transport DPI budget")
            self.control_requests = next_requests
            self.control_bytes_upper_bound = next_bytes
            self.journal.update(
                control_requests=self.control_requests,
                control_bytes_upper_bound=self.control_bytes_upper_bound,
                transport_operations=self.transport_operations,
                transport_bytes_upper_bound=self.transport_bytes_upper_bound,
                cleanup_transport_operations=self.cleanup_transport_operations,
                cleanup_transport_bytes_upper_bound=self.cleanup_transport_bytes_upper_bound,
                dirty=True,
            )
        parts = [
            "/usr/bin/python3", "-I", "-S", "-B", "-X", "utf8",
            "-X", "pycache_prefix=/dev/null", f"{self.remote_root}/client.py", command,
            "--env-file", f"{self.remote_root}/{env_name}",
            "--ca-bundle", f"{self.remote_root}/witness-ca.crt",
            "--site", site,
            "--request-id", request_id,
            "--timeout", "5",
            "--timestamp-offset-seconds", str(timestamp_offset),
            "--expected-host", HOSTS["matrix_witness"].address,
            "--expected-port", "443",
            "--expected-cert-sha256",
            str(self.baseline["matrix_witness_dark_baseline"]["cert_sha256"]),
        ]
        for status_code in expected_statuses:
            parts.extend(("--expect-http-status", str(status_code)))
        if not_before_unix_ms is not None:
            parts.extend(("--not-before-unix-ms", str(not_before_unix_ms)))
        if action is not None:
            parts.extend(("--action", action))
        if expected_epoch is not None:
            parts.extend(("--expected-epoch", str(expected_epoch)))
        if expected_lease_id is not None:
            parts.extend(("--expected-lease-id", expected_lease_id))
        if reason is not None:
            parts.extend(("--reason", reason))
        if command != "status":
            parts.extend(("--lease-duration-seconds", str(lease_seconds)))
        completed = self.remote(site, f"client_{site}_{request_id}", shlex.join(parts), timeout=timeout, check=check)
        if completed.returncode != 0:
            return completed, None
        return completed, parse_last_json(completed.stdout)

    def transition(
        self,
        site: str,
        *,
        action: str,
        expected_epoch: int,
        request_id: str,
        expected_lease_id: str | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        env_name: str = "client.env",
        fire_and_close: bool = False,
    ) -> dict[str, Any]:
        self.mark_witness_may_mutate()
        _, payload = self.client(
            site,
            "fire-and-close" if fire_and_close else "transition",
            request_id=request_id,
            env_name=env_name,
            expected_statuses=expected_statuses,
            action=action,
            expected_epoch=expected_epoch,
            expected_lease_id=expected_lease_id,
            reason=f"{self.tag} {self.scenario} {action}",
        )
        if payload is None:
            raise MatrixError("transition did not produce a result")
        return payload

    def witness_state(self, role: str = "matrix_witness") -> dict[str, Any]:
        command = (
            "set -Eeuo pipefail; "
            + (rollback_manifest_helper_guard() + "; " if role == "rollback_witness" else "")
            +
            "state=$(runuser -u postgres -- psql -XAt writer_witness -c \"SELECT json_build_object(" 
            "'holder_site',holder_site,'writer_epoch',writer_epoch,'lease_id',lease_id," 
            "'lease_status',lease_status,'issued_at',issued_at,'expires_at',expires_at," 
            "'transition_id',transition_id)::text FROM webapp_writer_witness_state\"); "
            "receipts=$(runuser -u postgres -- psql -XAt writer_witness -c 'SELECT count(*) FROM webapp_writer_witness_receipts'); "
            "manifest=$(/usr/local/sbin/writer-witness-state-manifest); "
            "printf '{\"state\":%s,\"receipts\":%s,\"manifest_sha256\":\"%s\"}\\n' \"$state\" \"$receipts\" \"$manifest\""
        )
        completed = self.remote(role, f"inspect_{role}_state", command)
        return parse_last_json(completed.stdout)

    def database_inventory(self) -> list[dict[str, Any]]:
        command = (
            "set -Eeuo pipefail; runuser -u postgres -- psql -XAt postgres -c \""
            "SELECT COALESCE(json_agg(json_build_object('name',datname,'oid',oid,'allow_connections',datallowconn) "
            "ORDER BY datname),'[]'::json)::text FROM pg_database "
            "WHERE datname='writer_witness' OR datname LIKE 'writer_witness_candidate_%' "
            "OR datname LIKE 'writer_witness_failed_%' OR datname LIKE 'writer_witness_rollback_%'\""
        )
        completed = self.remote("matrix_witness", "inspect_witness_database_inventory", command)
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise MatrixError("Witness database inventory is invalid")
        return payload

    @staticmethod
    def database_inventory_sha256(inventory: list[dict[str, Any]]) -> str:
        try:
            rendered = "\n".join(
                f"{item['name']}:{int(item['oid'])}:"
                f"{'true' if item['allow_connections'] is True else 'false'}"
                for item in sorted(inventory, key=lambda value: str(value["name"]))
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MatrixError("Witness database inventory cannot be attested") from exc
        return hashlib.sha256(rendered.encode()).hexdigest()

    def credential_bundle_sha256(self) -> str:
        completed = self.remote(
            "matrix_witness",
            "inspect_witness_credential_bundle",
            "set -Eeuo pipefail; sha256sum /etc/trading-bot-witness/runtime.env "
            "/root/writer-witness-client-material/webapp-fi.env "
            "/root/writer-witness-client-material/webapp-ir.env "
            "/root/writer-witness-client-material/witness-ca.crt | sha256sum | awk '{print $1}'",
        )
        digest = completed.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MatrixError("Witness credential bundle digest is invalid")
        return digest

    def acquire(self, site: str, request_id: str) -> dict[str, Any]:
        payload = self.transition(site, action="acquire", expected_epoch=0, request_id=request_id)
        response = payload.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("state"), dict):
            raise MatrixError("accepted acquisition response lacks Witness state")
        return response["state"]

    def wait_until_expired(self, state: dict[str, Any]) -> None:
        value = state.get("expires_at")
        if not isinstance(value, str):
            raise MatrixError("lease expiration is missing")
        expires = datetime.fromisoformat(value.replace("Z", "+00:00"))
        seconds = max(0.0, (expires - datetime.now(timezone.utc)).total_seconds() + 2.0)
        if seconds > 40:
            raise MatrixError("lease expiry wait exceeds the Matrix 30-second term")
        self.event("lease.wait", seconds=seconds)
        self.interruptible_sleep(seconds)

    def add_partition(self, site: str) -> None:
        table = self.tag
        self.claim("network_fault_sites", site)
        self.network_fault_sites.add(site)
        command = (
            f"set -Eeuo pipefail; nft list table inet {table} >/dev/null 2>&1 && exit 31 || true; "
            f"nft add table inet {table}; "
            f"nft 'add chain inet {table} output {{ type filter hook output priority -150; policy accept; }}'; "
            f"nft add rule inet {table} output ip daddr {HOSTS['matrix_witness'].address} tcp dport 443 "
            f"counter drop comment {shlex.quote(self.tag)}; nft list table inet {table}"
        )
        self.remote(site, f"partition_{site}_to_witness", command)

    def remove_partition(self, site: str) -> None:
        table = self.tag
        self.remote(
            site,
            f"remove_partition_{site}",
            f"set -Eeuo pipefail; nft list table inet {table} >/dev/null 2>&1 && nft delete table inet {table} || true; "
            f"! nft list table inet {table} >/dev/null 2>&1",
        )
        self.network_fault_sites.discard(site)

    def assert_unreachable(self, site: str, request_id: str) -> None:
        completed, _ = self.client(site, "status", request_id=request_id, check=False, timeout=12)
        if completed.returncode == 0:
            raise MatrixError(f"{site} unexpectedly reached the Witness during its partition")

    def run_scenario(self, *, authorization_nonce: str, preflight_sha256: str) -> None:
        handler = getattr(self, f"scenario_{self.scenario.replace('-', '_')}", None)
        if handler is None:
            raise MatrixError(f"scenario handler is missing for {self.scenario}")
        self.claim_remote_campaign()
        self.attest_initial_database_inventory()
        self.consume_remote_authorization(authorization_nonce, preflight_sha256)

        self.critical_precheck()
        self.journal.update(lifecycle_phase="scenario_executing", dirty=True)
        self.event("scenario.start", steps=list(SCENARIOS[self.scenario]))
        self.start_abort_monitor()
        try:
            handler()
            self.raise_if_abort_requested()
        finally:
            self.stop_abort_monitor()
        self.event("scenario.assertions_passed")

    def attest_initial_database_inventory(self) -> None:
        initial_inventory = self.database_inventory()
        expected_inventory_sha256 = str(
            self.baseline["matrix_witness_dark_baseline"].get(
                "database_inventory_sha256", ""
            )
        )
        if self.database_inventory_sha256(initial_inventory) != expected_inventory_sha256:
            raise MatrixError("replacement Witness database inventory drifted after preflight")
        self.journal.update(
            initial_database_inventory=initial_inventory,
            lifecycle_phase="initial_inventory_attested",
            dirty=True,
        )

    def scenario_RH_001(self) -> None:
        self.stage_site("webapp_fi")
        self.stage_site("webapp_ir")
        local_barrier = threading.Barrier(3, timeout=5)
        release_at_ms = int(time.time() * 1000) + 5000
        def attempt(site: str) -> dict[str, Any]:
            local_barrier.wait()
            _, payload = self.client(
                site,
                "transition",
                action="acquire",
                expected_epoch=0,
                request_id=f"{self.tag}-{site[-2:]}-race",
                expected_statuses=(200, 409),
                reason=f"{self.tag} {self.scenario} acquire",
                not_before_unix_ms=release_at_ms,
            )
            if payload is None:
                raise MatrixError("concurrent acquisition did not return evidence")
            return payload
        self.mark_witness_may_mutate()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(attempt, site) for site in ("webapp_fi", "webapp_ir")]
            local_barrier.wait()
            outcomes = [future.result() for future in futures]
        codes = [int(item["http_status"]) for item in outcomes]
        if sorted(codes) != [200, 409]:
            raise MatrixError(f"concurrent acquisition was not exclusive: {codes}")
        state = self.witness_state()
        if state["state"].get("writer_epoch") != 1 or state["receipts"] != 2:
            raise MatrixError("concurrent acquisition did not persist one term and two receipts")
        sent = [int(item.get("sent_at_unix_ns", 0)) for item in outcomes]
        ready = [int(item.get("tls_ready_at_unix_ns", 0)) for item in outcomes]
        release_at_ns = release_at_ms * 1_000_000
        if not all(ready) or any(value > release_at_ns - 250_000_000 for value in ready):
            raise MatrixError(f"concurrent acquisition clients were not TLS-ready before release: {ready}")
        if not all(sent) or max(sent) - min(sent) > 25_000_000:
            raise MatrixError(f"concurrent acquisition requests were not released together: {sent}")
        self.event(
            "rh001.concurrent_release",
            release_at_ms=release_at_ms,
            tls_ready_at_unix_ns=ready,
            sent_at_unix_ns=sent,
            send_delta_ns=max(sent) - min(sent),
        )

    def scenario_RH_002(self) -> None:
        self.stage_site("webapp_fi")
        request_id = f"{self.tag}-lost-response"
        self.transition("webapp_fi", action="acquire", expected_epoch=0, request_id=request_id, fire_and_close=True)
        for _ in range(20):
            state = self.witness_state()
            if state["receipts"] == 1:
                break
            self.interruptible_sleep(0.25)
        else:
            raise MatrixError("fire-and-close request did not durably commit")
        replay = self.transition("webapp_fi", action="acquire", expected_epoch=0, request_id=request_id)
        if replay.get("response", {}).get("replayed") is not True:
            raise MatrixError("lost committed response was not exactly replayed")

    def scenario_RH_003(self) -> None:
        self.stage_site("webapp_fi")
        self.stage_site("webapp_ir")
        lease = self.acquire("webapp_fi", f"{self.tag}-fi-term")
        request_id = f"{self.tag}-delayed-ir"
        first = self.transition(
            "webapp_ir", action="acquire", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=request_id,
            expected_statuses=(409,),
        )
        if first.get("response", {}).get("replayed") is not False:
            raise MatrixError("first live-lease rejection was unexpectedly a replay")
        before = self.witness_state()["receipts"]
        self.wait_until_expired(lease)
        replay = self.transition(
            "webapp_ir", action="acquire", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=request_id,
            expected_statuses=(409,),
        )
        if replay.get("response", {}).get("replayed") is not True:
            raise MatrixError("durable negative receipt did not remain one-shot")
        if self.witness_state()["receipts"] != before:
            raise MatrixError("negative replay created a second receipt")

    def directional_partition(self, holder: str, next_site: str) -> None:
        self.stage_site(holder)
        self.stage_site(next_site)
        lease = self.acquire(holder, f"{self.tag}-{holder[-2:]}-term")
        self.add_partition(holder)
        self.assert_unreachable(holder, f"{self.tag}-blocked-status")
        self.wait_until_expired(lease)
        promoted = self.transition(
            next_site, action="acquire", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=f"{self.tag}-{next_site[-2:]}-epoch2",
        )
        if promoted.get("response", {}).get("state", {}).get("writer_epoch") != 2:
            raise MatrixError("reachable peer did not acquire the next epoch after expiry")

    def scenario_RH_004(self) -> None:
        self.directional_partition("webapp_fi", "webapp_ir")

    def scenario_RH_005(self) -> None:
        self.directional_partition("webapp_ir", "webapp_fi")

    def scenario_RH_006(self) -> None:
        self.stage_site("webapp_fi")
        lease = self.acquire("webapp_fi", f"{self.tag}-service-term")
        self.remote("matrix_witness", "stop_witness_service", "systemctl stop writer-witness.service")
        self.assert_unreachable("webapp_fi", f"{self.tag}-service-stopped")
        self.remote("matrix_witness", "start_witness_service", "systemctl start writer-witness.service")
        self.client("webapp_fi", "status", request_id=f"{self.tag}-service-restarted")
        self.remote(
            "matrix_witness", "pause_witness_service",
            "pid=$(systemctl show -p MainPID --value writer-witness.service); test \"$pid\" -gt 1; kill -STOP \"$pid\"",
        )
        self.assert_unreachable("webapp_fi", f"{self.tag}-service-paused")
        self.remote(
            "matrix_witness", "resume_witness_service",
            "pid=$(systemctl show -p MainPID --value writer-witness.service); test \"$pid\" -gt 1; kill -CONT \"$pid\"",
        )
        renewed = self.transition(
            "webapp_fi", action="renew", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=f"{self.tag}-renew-after-service",
        )
        if renewed.get("response", {}).get("state", {}).get("lease_id") != lease["lease_id"]:
            raise MatrixError("service restart changed the durable lease identity")

    def scenario_RH_007(self) -> None:
        self.stage_site("webapp_fi")
        lease = self.acquire("webapp_fi", f"{self.tag}-postgres-term")
        self.remote("matrix_witness", "stop_witness_postgres", "systemctl stop postgresql.service", timeout=30)
        self.assert_unreachable("webapp_fi", f"{self.tag}-postgres-stopped")
        self.remote(
            "matrix_witness", "start_witness_postgres",
            "systemctl start postgresql.service; systemctl start writer-witness.service",
        )
        self.client("webapp_fi", "status", request_id=f"{self.tag}-postgres-restarted")
        self.remote(
            "matrix_witness", "pause_witness_postgres",
            "pid=$(head -1 /var/lib/postgresql/*/main/postmaster.pid); test \"$pid\" -gt 1; kill -STOP \"$pid\"",
        )
        self.assert_unreachable("webapp_fi", f"{self.tag}-postgres-paused")
        self.remote(
            "matrix_witness", "resume_witness_postgres",
            "pid=$(head -1 /var/lib/postgresql/*/main/postmaster.pid); test \"$pid\" -gt 1; kill -CONT \"$pid\"",
        )
        renewed = self.transition(
            "webapp_fi", action="renew", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=f"{self.tag}-renew-after-postgres",
        )
        if renewed.get("response", {}).get("state", {}).get("lease_id") != lease["lease_id"]:
            raise MatrixError("PostgreSQL restart changed the durable lease identity")

    def wait_for_ssh_after_reboot(self) -> None:
        saw_down = False
        role = "matrix_witness"
        self._forget_transport_master(role)
        control_path = self._ssh_control_path(HOSTS[role])
        try:
            control_path.unlink()
        except FileNotFoundError:
            pass
        for _ in range(36):
            command = "true"
            self._reserve_transport_budget(
                role=role,
                kind="ssh",
                payload_bytes=len(command.encode("utf-8")),
                force_master_reservation=True,
            )
            try:
                completed = subprocess.run(
                    self.ssh_args(HOSTS[role], command),
                    cwd=ROOT, capture_output=True, text=True, timeout=12,
                )
            except BaseException:
                self._forget_transport_master(role)
                raise
            if completed.returncode != 0:
                saw_down = True
            elif saw_down:
                self._mark_transport_master(role)
                self.event("reboot.ssh_restored")
                return
            self.interruptible_sleep(5)
        raise MatrixError("replacement Witness did not complete a bounded reboot")

    def scenario_RH_008(self) -> None:
        self.stage_site("webapp_fi")
        self.acquire("webapp_fi", f"{self.tag}-reboot-term")
        before = self.witness_state()
        self.remote("matrix_witness", "reboot_replacement_witness", "systemctl reboot", check=False, timeout=12)
        self.wait_for_ssh_after_reboot()
        self.remote(
            "matrix_witness", "verify_services_after_reboot",
            "for unit in postgresql nginx writer-witness; do test \"$(systemctl is-active \"$unit\")\" = active; done",
        )
        after = self.witness_state()
        if after != before:
            raise MatrixError("replacement Witness durable state changed across reboot")

    def scenario_RH_009(self) -> None:
        self.claim("isolated_pressure", self.tag)
        before = self.witness_state()
        completed = self.remote(
            "matrix_witness", "isolated_postgres_disk_full",
            f"/usr/local/sbin/writer-witness-matrix-host-faults disk-full --tag {self.tag}",
            timeout=180,
        )
        evidence = parse_last_json(completed.stdout)
        if (
            evidence.get("status") != "passed"
            or evidence.get("tag") != self.tag
            or evidence.get("production_state_unchanged") is not True
            or evidence.get("production_before") != evidence.get("production_after")
        ):
            raise MatrixError("isolated disk-full evidence did not prove its production boundary")
        after = self.witness_state()
        if after != before:
            raise MatrixError("isolated disk-full scenario touched the production Witness database")

    def scenario_RH_010(self) -> None:
        self.stage_site("webapp_fi")
        cases = ((-17, 401), (7, 401), (-14, 200), (4, 200))
        for offset, status_code in cases:
            _, payload = self.client(
                "webapp_fi", "status", request_id=f"{self.tag}-clock-{offset:+d}",
                expected_statuses=(status_code,), timestamp_offset=offset,
            )
            if payload is None or payload["http_status"] != status_code:
                raise MatrixError(f"clock boundary {offset} did not return {status_code}")
        self.claim("isolated_pressure", self.tag)
        completed = self.remote(
            "matrix_witness", "isolated_postgres_clock_jump",
            f"/usr/local/sbin/writer-witness-matrix-host-faults clock-jump --tag {self.tag}",
            timeout=180,
        )
        try:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise MatrixError("isolated clock-jump helper did not return valid JSON") from exc
        required = {
            "status": "passed",
            "scenario": "isolated-postgresql-real-database-clock-jump",
            "production_clock_path": "SELECT clock_timestamp()",
            "synthetic_time_argument_used": False,
            "backward_clock_steal_rejected": True,
            "postgres_restart_persistence_proved": True,
            "forward_expiry_acquire_epoch": 2,
            "old_epoch_revival_rejected": True,
            "final_holder_site": "webapp_ir",
            "final_writer_epoch": 2,
            "production_state_unchanged": True,
            "production_processes_never_loaded_libfaketime": True,
        }
        if any(result.get(key) != value for key, value in required.items()):
            raise MatrixError("isolated clock-jump probe did not prove the lease/epoch contract")
        before = result.get("production_before")
        after = result.get("production_after")
        isolation = result.get("isolation")
        restart = result.get("disposable_postgres_restart")
        if (
            not isinstance(before, dict)
            or before != after
            or not isinstance(isolation, dict)
            or isolation.get("unix_socket_transport") is not True
            or isolation.get("listen_addresses") != ""
            or isolation.get("libfaketime_loaded") is not True
            or not isinstance(restart, dict)
            or restart.get("before") == restart.get("after")
        ):
            raise MatrixError("isolated clock-jump evidence did not prove its production boundary")
        production_fields = {
            "manifest_sha256",
            "database_inventory_sha256",
            "credential_bundle_sha256",
            "system_identifier",
            "writer_service_pid",
            "writer_service_start_ticks",
            "postgres_pid",
            "postgres_start_ticks",
            "libfaketime_loaded",
        }
        if set(before) != production_fields or before.get("libfaketime_loaded") is not False:
            raise MatrixError("production clock-jump attestation is incomplete")
        current = self.witness_state()
        if current["manifest_sha256"] != self.baseline["matrix_witness_dark_baseline"]["manifest_sha256"]:
            raise MatrixError("isolated clock-jump scenario touched the production Witness database")

    def scenario_RH_011(self) -> None:
        self.stage_site("webapp_fi", retain_previous=True)
        self.stage_site("webapp_ir")
        self.add_partition("webapp_ir")
        self.client("webapp_fi", "status", env_name="old.env", request_id=f"{self.tag}-old-overlap")
        self.client("webapp_fi", "status", request_id=f"{self.tag}-new-overlap")
        request_id = f"{self.tag}-cross-key-replay"
        old = self.transition(
            "webapp_fi", action="acquire", expected_epoch=99, request_id=request_id,
            expected_statuses=(409,), env_name="old.env",
        )
        new = self.transition(
            "webapp_fi", action="acquire", expected_epoch=99, request_id=request_id,
            expected_statuses=(409,), env_name="client.env",
        )
        if old.get("response", {}).get("replayed") is not False or new.get("response", {}).get("replayed") is not True:
            raise MatrixError("exact durable request did not replay across HMAC key generations")
        self.remote(
            "matrix_witness", "revoke_old_fi_hmac",
            f"{REMOTE_ISOLATED_PYTHON} /usr/local/sbin/writer-witness-rotate-hmac "
            "revoke --site webapp_fi --expected-epoch 0",
        )
        self.client(
            "webapp_fi", "status", env_name="old.env", request_id=f"{self.tag}-old-revoked",
            expected_statuses=(401,),
        )
        self.client("webapp_fi", "status", request_id=f"{self.tag}-new-active")
        self.remote(
            "matrix_witness", "recover_fi_hmac_rotation",
            "set -Eeuo pipefail; "
            "systemctl stop writer-witness.service; "
            "test \"$(runuser -u postgres -- psql -XAt -F '|' -d writer_witness -c "
            "\"SELECT authority, writer_epoch, lease_status, COALESCE(holder_site, '') "
            "FROM webapp_writer_witness_state WHERE authority='webapp';\")\" = "
            "'webapp|0|vacant|'; "
            f"{REMOTE_ISOLATED_PYTHON} /usr/local/sbin/writer-witness-rotate-hmac "
            f"recover --leave-service-stopped "
            f"--site webapp_fi --expected-epoch 0 --campaign-tag {self.tag}; "
            "systemctl start writer-witness.service; "
            "curl --fail --silent --show-error --retry 20 --retry-delay 1 "
            "--retry-all-errors http://127.0.0.1:8011/health/ready >/dev/null",
        )
        self.rotation_sites.discard("webapp_fi")
        self.client("webapp_fi", "status", env_name="old.env", request_id=f"{self.tag}-old-restored")

    def restore_once(self, *, fail_after: str | None = None, expect_failure: bool = False) -> None:
        baseline = self.baseline["matrix_witness_dark_baseline"]
        current = self.witness_state()
        inventory_before = self.database_inventory()
        live_before = [item for item in inventory_before if item.get("name") == "writer_witness"]
        if (
            len(live_before) != 1
            or not isinstance(live_before[0].get("oid"), int)
            or int(live_before[0]["oid"]) <= 0
            or live_before[0].get("allow_connections") is not True
        ):
            raise MatrixError("restore cannot bind the current live database OID")
        live_oid_before = int(live_before[0]["oid"])
        backup = str(baseline["backup"])
        if not re.fullmatch(r"writer-witness-[0-9]{8}T[0-9]{6}Z\.dump", backup):
            raise MatrixError("pinned restore backup filename is unsafe")
        environment = {
            "WRITER_WITNESS_RESTORE_OPERATION_TAG": self.tag,
            "WRITER_WITNESS_RESTORE_EXPECTED_STATE": "webapp:0:vacant",
            "WRITER_WITNESS_RESTORE_EXPECTED_RECEIPTS": "0",
            "WRITER_WITNESS_RESTORE_EXPECTED_MANIFEST_SHA256": baseline["manifest_sha256"],
            "WRITER_WITNESS_RESTORE_EXPECTED_BACKUP_SHA256": baseline["backup_sha256"],
            "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE": (
                f"webapp:{current['state']['writer_epoch']}:{current['state']['lease_status']}"
            ),
            "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS": str(current["receipts"]),
            "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256": current["manifest_sha256"],
        }
        if fail_after:
            environment["WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER"] = fail_after
        command = (
            "env " + " ".join(f"{key}={shlex.quote(str(value))}" for key, value in sorted(environment.items()))
            + " /usr/local/sbin/writer-witness-live-restore --apply-from-stdin"
            + f" < /var/backups/trading-bot-witness/{backup}"
        )
        completed = self.remote(
            "matrix_witness", f"restore_baseline_{fail_after or 'success'}", command,
            timeout=180, check=not expect_failure,
        )
        if expect_failure:
            if completed.returncode == 0:
                raise MatrixError(f"restore fault point {fail_after} unexpectedly succeeded")
            recover_environment = {
                "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE": environment[
                    "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE"
                ],
                "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS": environment[
                    "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS"
                ],
                "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256": environment[
                    "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256"
                ],
            }
            recover_command = (
                "set -Eeuo pipefail; env "
                + " ".join(
                    f"{key}={shlex.quote(str(value))}"
                    for key, value in sorted(recover_environment.items())
                )
                + " /usr/local/sbin/writer-witness-live-restore --recover; "
                "test ! -e /var/lib/trading-bot-witness/restore-state/active.env; "
                "test ! -L /var/lib/trading-bot-witness/restore-state/active.env; "
                "test -z \"$(find /var/lib/trading-bot-witness/restore-state "
                "-mindepth 1 -maxdepth 1 -name '.active.*.env' -print -quit)\"; "
                "test -z \"$(find /var/backups/trading-bot-witness -mindepth 1 -maxdepth 1 "
                "-name '.replacement-restore.*.dump' -print -quit)\"; "
                "test \"$(systemctl is-active writer-witness.service)\" = active"
            )
            self.remote(
                "matrix_witness",
                f"recover_hard_killed_restore_{fail_after}",
                recover_command,
                timeout=180,
            )
            recovered = self.witness_state()
            if recovered != current:
                raise MatrixError(f"restore recovery after {fail_after} did not preserve the prior OID state")
            inventory = self.database_inventory()
            if inventory != inventory_before:
                raise MatrixError(
                    f"restore recovery after {fail_after} changed the exact database inventory"
                )
            unsafe = [
                item for item in inventory
                if item.get("name") != "writer_witness" and item.get("allow_connections") is not False
            ]
            if unsafe:
                raise MatrixError(f"restore recovery left an auxiliary database enabled: {unsafe}")
            self.event("restore.failpoint_recovered", fail_after=fail_after, database_inventory=inventory)
            return

        result = parse_last_json(completed.stdout)
        rollback_database = str(result.get("rollback_database") or "")
        if (
            result.get("status") != "restored-live-dark"
            or not re.fullmatch(
                rf"writer_witness_rollback_{re.escape(self.tag)}_[0-9_]+",
                rollback_database,
            )
        ):
            raise MatrixError("successful restore did not return an owned rollback identity")
        inventory_after = self.database_inventory()
        rollback = [item for item in inventory_after if item.get("name") == rollback_database]
        if (
            len(rollback) != 1
            or rollback[0].get("oid") != live_oid_before
            or rollback[0].get("allow_connections") is not False
        ):
            raise MatrixError("successful restore rollback database lacks exact OID ownership")
        self.claim("restore_owned_databases", f"{rollback_database}:{live_oid_before}")

    def scenario_RH_012(self) -> None:
        self.stage_site("webapp_fi")
        self.acquire("webapp_fi", f"{self.tag}-restore-term")
        for point in (
            "input_validated", "candidate_created", "candidate_restored",
            "candidate_validated", "grants_applied", "prepared",
            "service_stopped", "current_disabled", "current_renamed",
            "candidate_promoted", "candidate_enabled", "service_started",
        ):
            self.restore_once(fail_after=point, expect_failure=True)
        self.restore_once()
        self.witness_mutated = False
        final = self.witness_state()
        if final["manifest_sha256"] != self.baseline["matrix_witness_dark_baseline"]["manifest_sha256"]:
            raise MatrixError("final restore did not reproduce the pinned epoch-zero manifest")

    def capture_pre_recovery(self) -> None:
        for role in ("matrix_witness", "rollback_witness", "webapp_fi", "webapp_ir"):
            witness_manifest = (
                (
                    rollback_manifest_helper_guard()
                    + "; /usr/local/sbin/writer-witness-state-manifest --json; "
                    if role == "rollback_witness"
                    else "/usr/local/sbin/writer-witness-state-manifest --json; "
                )
                if role in {"matrix_witness", "rollback_witness"}
                else ""
            )
            evidence_command = (
                "set -Eeuo pipefail; date -u +%FT%TZ; "
                "systemctl is-active writer-witness postgresql nginx 2>/dev/null || true; "
                "nft list ruleset 2>/dev/null | sha256sum; "
                + witness_manifest
                + (
                    "runuser -u postgres -- psql -XAt postgres -c \"SELECT datname||':'||oid||':'||datallowconn FROM pg_database WHERE datname='writer_witness' OR datname LIKE 'writer_witness_candidate_%' OR datname LIKE 'writer_witness_failed_%' OR datname LIKE 'writer_witness_rollback_%' ORDER BY datname\"; "
                    if role == "matrix_witness" else ""
                )
                + "find /var/lib/trading-bot-witness/restore-state -maxdepth 2 -type f "
                "-printf '%p %m %s\\n' 2>/dev/null || true; "
                "find /run/writer-witness-matrix -maxdepth 3 -type f "
                "-printf '%p %m\\n' 2>/dev/null || true; "
                "echo evidence_capture_complete"
            )
            self.remote(
                role,
                f"capture_{role}_pre_recovery",
                evidence_command,
            )
        self.event("evidence.pre_recovery_retained")

    def stop_and_remove_requesters(self) -> None:
        sites = self.staged_sites | self.journal.values("staged_sites")
        for site in sorted(sites):
            pattern = self.remote_root + "/[c]lient.py"
            self.remote(
                site,
                f"stop_requesters_{site}",
                "set -Eeuo pipefail; "
                f"pids=$(pgrep -f {shlex.quote(pattern)} || true); "
                "test -z \"$pids\" || kill $pids; "
                "for i in $(seq 1 20); do pgrep -f "
                f"{shlex.quote(pattern)} >/dev/null || break; sleep 0.1; done; "
                f"! pgrep -f {shlex.quote(pattern)}; "
                f"! ss -Htnp | grep -E '^(ESTAB|SYN-SENT)' | grep -F {shlex.quote(HOSTS['matrix_witness'].address + ':443')}; "
                f"rm -rf {shlex.quote(self.remote_root)}; test ! -e {shlex.quote(self.remote_root)}",
            )
        self.staged_sites.clear()

    def recover_rotation(self) -> None:
        sites = self.rotation_sites | self.journal.values("rotation_sites")
        for site in sorted(sites):
            command = (
                "set -Eeuo pipefail; "
                "systemctl stop writer-witness.service; "
                "state=$(systemctl is-active writer-witness.service || true); "
                "test \"$state\" = inactive -o \"$state\" = failed; "
                f"{REMOTE_ISOLATED_PYTHON} /usr/local/sbin/writer-witness-rotate-hmac "
                f"recover --leave-service-stopped "
                f"--site {site} --expected-epoch 0 --campaign-tag {self.tag}; "
                "state=$(systemctl is-active writer-witness.service || true); "
                "test \"$state\" = inactive -o \"$state\" = failed; "
                f"test ! -e /var/lib/trading-bot-witness/hmac-rotation/{site}; "
                f"test -z \"$(find /var/lib/trading-bot-witness/hmac-rotation -maxdepth 1 "
                f"-name '.{site}.prepare-*' -print -quit)\"; "
                f"test -z \"$(find /var/lib/trading-bot-witness/hmac-rotation -maxdepth 1 "
                f"-name '.rotation-delete-{site}-*' -print -quit)\""
            )
            self.remote("matrix_witness", f"recover_hmac_rotation_{site}", command)
            self.rotation_sites.discard(site)
        if sites:
            self.remote(
                "matrix_witness",
                "attest_hmac_rotation_root_clean",
                "set -Eeuo pipefail; root=/var/lib/trading-bot-witness/hmac-rotation; "
                "test -d \"$root\"; test ! -L \"$root\"; "
                "test \"$(stat -c %u \"$root\")\" = 0; "
                "test \"$(stat -c %a \"$root\")\" = 700; "
                "test -f \"$root/.runtime.lock\"; test ! -L \"$root/.runtime.lock\"; "
                "test \"$(stat -c %u \"$root/.runtime.lock\")\" = 0; "
                "test \"$(stat -c %a \"$root/.runtime.lock\")\" = 600; "
                "test \"$(stat -c %h \"$root/.runtime.lock\")\" = 1; "
                "flock -n \"$root/.runtime.lock\" -c true; "
                "test -z \"$(find \"$root\" -mindepth 1 -maxdepth 1 "
                "! -name '.runtime.lock' -print -quit)\"",
            )

    def recover_active_live_restore(self) -> None:
        self.remote(
            "matrix_witness",
            "recover_active_live_restore_journal",
            "set -Eeuo pipefail; "
            "journal=/var/lib/trading-bot-witness/restore-state/active.env; "
            "journal_temp=$(find /var/lib/trading-bot-witness/restore-state -mindepth 1 -maxdepth 1 "
            "-name '.active.*.env' -print -quit); "
            "orphan=$(find /var/backups/trading-bot-witness -mindepth 1 -maxdepth 1 "
            "-name '.replacement-restore.*.dump' -print -quit); "
            "if test -e \"$journal\" || test -L \"$journal\" || test -n \"$journal_temp\" || test -n \"$orphan\"; then "
            "/usr/local/sbin/writer-witness-live-restore --recover; fi; "
            "test ! -e \"$journal\"; test ! -L \"$journal\"; "
            "test -z \"$(find /var/lib/trading-bot-witness/restore-state -mindepth 1 -maxdepth 1 "
            "-name '.active.*.env' -print -quit)\"; "
            "test -z \"$(find /var/backups/trading-bot-witness -mindepth 1 -maxdepth 1 "
            "-name '.replacement-restore.*.dump' -print -quit)\"",
            timeout=180,
        )

    def resume_witness_runtime(self) -> None:
        self.remote(
            "matrix_witness", "resume_replacement_runtime",
            "set -Eeuo pipefail; "
            "pid=$(systemctl show -p MainPID --value writer-witness.service); test \"${pid:-0}\" -le 1 || kill -CONT \"$pid\"; "
            "for file in /var/lib/postgresql/*/main/postmaster.pid; do test -f \"$file\" || continue; pid=$(head -1 \"$file\"); kill -CONT \"$pid\" 2>/dev/null || true; done; "
            "systemctl start postgresql.service nginx.service writer-witness.service; "
            "test \"$(systemctl is-active postgresql.service)\" = active; "
            "test \"$(systemctl is-active nginx.service)\" = active; "
            "test \"$(systemctl is-active writer-witness.service)\" = active",
            timeout=45,
        )

    def remove_isolated_pressure(self) -> None:
        self.remote(
            "matrix_witness", "remove_isolated_pressure",
            "set -Eeuo pipefail; "
            f"/usr/local/sbin/writer-witness-matrix-host-faults recover --tag {self.tag}; "
            f"for root in /run/{self.tag}-disk /run/{self.tag}-clock; do "
            "test ! -e \"$root\"; ! mountpoint -q \"$root\"; done; "
            f"for state in /var/lib/trading-bot-witness/matrix-host-faults/{self.tag}-disk "
            f"/var/lib/trading-bot-witness/matrix-host-faults/{self.tag}-clock; do test ! -e \"$state\"; done; "
            "! ss -H -ltn | awk '{print $4}' | grep -Eq '(^|:)(55439|55440)$'",
        )

    def verify_complete_baseline(self) -> None:
        matrix = self.witness_state("matrix_witness")
        original = self.witness_state("rollback_witness")
        if matrix["manifest_sha256"] != self.baseline["matrix_witness_dark_baseline"]["manifest_sha256"]:
            raise MatrixError("replacement Witness did not return to its pinned full manifest")
        if original["manifest_sha256"] != self.baseline["rollback_witness_baseline"]["manifest_sha256"]:
            raise MatrixError("original Witness changed during the Matrix scenario")
        if self.credential_bundle_sha256() != self.baseline["matrix_witness_dark_baseline"]["credential_bundle_sha256"]:
            raise MatrixError("replacement Witness credentials did not return to their pinned baseline")
        initial_inventory = self.journal.payload.get("initial_database_inventory")
        current_inventory = self.database_inventory()
        if not isinstance(initial_inventory, list):
            raise MatrixError("campaign lacks its initial Witness database inventory")
        initial_by_name = {str(item.get("name")): item for item in initial_inventory if isinstance(item, dict)}
        current_by_name = {str(item.get("name")): item for item in current_inventory}
        for name, item in current_by_name.items():
            if name == "writer_witness":
                continue
            if item.get("allow_connections") is not False:
                raise MatrixError(f"non-live Witness database remains connection-enabled: {name}")
            if name not in initial_by_name:
                raise MatrixError(f"Matrix-owned auxiliary database was not removed: {name}")
        for name, item in initial_by_name.items():
            if name not in current_by_name or current_by_name[name].get("oid") != item.get("oid"):
                raise MatrixError(f"pre-existing Witness database inventory changed: {name}")
        self.journal.update(final_database_inventory=current_inventory)
        for role in ("webapp_fi", "webapp_ir"):
            key_checks = " ".join(
                f"! grep -R -F -l -- {shlex.quote(key_id)} /tmp /var/tmp /srv/trading-bot/current/.env 2>/dev/null;"
                for key_id in sorted(self.journal.values("matrix_key_ids"))
            )
            self.remote(
                role,
                f"verify_no_hidden_state_{role}",
                "set -Eeuo pipefail; "
                f"test ! -e {shlex.quote(self.remote_root)}; "
                f"test ! -L {shlex.quote(self.remote_root)}; "
                f"if nft list table inet {self.tag} >/dev/null 2>&1; then exit 1; fi; "
                + key_checks,
            )
        self.event("baseline.verified", replacement_manifest=matrix["manifest_sha256"], original_manifest=original["manifest_sha256"])

    def remove_owned_aux_databases(self) -> None:
        initial = self.journal.payload.get("initial_database_inventory")
        if not isinstance(initial, list) or not all(isinstance(item, dict) for item in initial):
            raise MatrixError("cannot clean auxiliary databases without initial inventory ownership")
        initial_by_name = {str(item.get("name")): item for item in initial}
        owned: dict[str, int] = {}
        for raw in self.journal.values("restore_owned_databases"):
            name, separator, oid_raw = raw.rpartition(":")
            if (
                separator != ":"
                or not re.fullmatch(
                    rf"writer_witness_rollback_{re.escape(self.tag)}_[0-9_]+",
                    name,
                )
                or not oid_raw.isdigit()
                or int(oid_raw) <= 0
                or name in owned
            ):
                raise MatrixError("restore-owned database journal entry is unsafe")
            owned[name] = int(oid_raw)

        current = self.database_inventory()
        current_by_name = {str(item.get("name")): item for item in current}
        tag_pattern = re.compile(
            rf"writer_witness_(?:candidate|rollback|failed)_{re.escape(self.tag)}_[0-9_]+\Z"
        )
        for name, item in current_by_name.items():
            if not tag_pattern.fullmatch(name):
                continue
            initial_item = initial_by_name.get(name)
            if initial_item is not None and initial_item.get("oid") == item.get("oid"):
                continue
            if owned.get(name) != item.get("oid"):
                raise MatrixError(
                    f"campaign-tagged auxiliary database lacks exact journaled OID ownership: {name}"
                )

        for name, oid in sorted(owned.items()):
            if name in initial_by_name:
                raise MatrixError("restore cleanup refuses an initial-inventory database name")
            observed = current_by_name.get(name)
            if (
                not isinstance(observed, dict)
                or observed.get("oid") != oid
                or observed.get("allow_connections") is not False
            ):
                raise MatrixError("restore-owned database no longer matches its exact cleanup claim")
            command = (
                "set -Eeuo pipefail; "
                f"name={shlex.quote(name)}; expected_oid={oid}; "
                "observed=$(runuser -u postgres -- psql -XAt postgres -c "
                "\"SELECT oid||':'||datallowconn FROM pg_database WHERE datname='$name'\"); "
                "test \"$observed\" = \"$expected_oid:f\"; "
                "runuser -u postgres -- dropdb \"$name\"; "
                "test -z \"$(runuser -u postgres -- psql -XAt postgres -c "
                "\"SELECT oid FROM pg_database WHERE datname='$name'\")\""
            )
            self.remote(
                "matrix_witness",
                f"remove_owned_aux_database_{hashlib.sha256(name.encode()).hexdigest()[:12]}",
                command,
                timeout=120,
            )

        after = self.database_inventory()
        after_by_name = {str(item.get("name")): item for item in after}
        for name, item in initial_by_name.items():
            if after_by_name.get(name) != item:
                raise MatrixError(f"auxiliary cleanup changed initial database inventory: {name}")
        for name in owned:
            if name in after_by_name:
                raise MatrixError(f"restore-owned auxiliary database remains after cleanup: {name}")
        self.event("cleanup.owned_aux_databases_removed")

    def cleanup(self) -> None:
        self.enter_cleanup_mode()
        if (
            self.journal.payload.get("remote_campaign_protocol") == "atomic-helper-v1"
            and self.journal.payload.get("lifecycle_phase")
            in {
                "controller_created",
                "local_authorization_consumption_intent",
                "local_preflight_consumed",
                "local_authorization_consumed",
            }
            and not self.remote_campaign_claimed
            and not self.remote_campaign_ambiguous
            and not self.remote_campaign_conflict
        ):
            # The lifecycle intent is persisted before the first remote command.
            # This exact phase therefore proves that cleanup is local-only.
            shutil.rmtree(self.local_secret_root, ignore_errors=True)
            self.journal.update(
                status="completed_without_remote_claim",
                lifecycle_phase="completed",
                dirty=False,
                cleanup_errors=[],
                local_authorization_reconciled=True,
                completed_at=utc_now(),
            )
            self.event("cleanup.completed_before_remote_claim")
            return
        if self.remote_campaign_conflict and not self.remote_campaign_claimed:
            shutil.rmtree(self.local_secret_root, ignore_errors=True)
            self.journal.update(
                status="failed",
                dirty=True,
                cleanup_errors=["remote_campaign_ownership:foreign_or_stale"],
            )
            self.event("cleanup.blocked_foreign_campaign_owner")
            raise MatrixError("cleanup refused mutation for an unproven foreign campaign state")
        if self.remote_campaign_ambiguous and not self.remote_campaign_claimed:
            # The claim SSH response may have been lost after the replacement
            # Witness durably created our marker. Resolving that exact marker is
            # read-only and is the only safe way to make the recovery path
            # progress; a missing or foreign marker still fences every mutation.
            self.claim_remote_campaign()
        if not self.remote_campaign_claimed:
            shutil.rmtree(self.local_secret_root, ignore_errors=True)
            self.journal.update(
                status="failed",
                dirty=True,
                cleanup_errors=["remote_campaign_ownership:not_proven"],
            )
            raise MatrixError("cleanup refused every remote mutation without proven campaign ownership")
        self.assert_remote_campaign_owned(allow_expired_cleanup=True)
        if not isinstance(self.journal.payload.get("initial_database_inventory"), list):
            if self.journal.payload.get("lifecycle_phase") != "remote_claimed":
                raise MatrixError(
                    "initial Witness database inventory is missing after the pre-scenario recovery boundary"
                )
            self.attest_initial_database_inventory()
        self.journal.update(lifecycle_phase="cleanup_started", dirty=True)
        self.event("cleanup.start", order=list(CLEANUP_STEP_IDS))
        errors: list[str] = []
        assurance_errors: list[str] = []
        critical_errors: list[str] = []

        # Requesters are stopped while isolation remains active. Evidence is
        # retained before any recovery, an interrupted live restore is reconciled
        # before HMAC material can touch/restart the service, and the HMAC helper
        # deliberately leaves the runtime stopped until the explicit resume step.
        # Capability revocation is a safety action, not a reconnect action. If
        # requester termination is ambiguous we keep isolation in place but
        # still stop the Witness and remove the campaign HMAC, so an orphaned
        # retry process cannot retain an accepted credential indefinitely.
        for name, action, critical in (
            ("stop_and_join_requesters", self.stop_and_remove_requesters, True),
            ("retain_pre_recovery_evidence", self.capture_pre_recovery, False),
            ("recover_active_live_restore", self.recover_active_live_restore, True),
            ("revoke_transient_capability", self.recover_rotation, True),
        ):
            try:
                action()
                self.event("cleanup.step", step=name, status="passed")
            except Exception as exc:
                rendered = f"{name}:{exc}"
                errors.append(rendered)
                if critical:
                    critical_errors.append(rendered)
                else:
                    assurance_errors.append(rendered)
                self.event("cleanup.step", step=name, status="failed", error=str(exc))
                # Do not break before the credential-revocation boundary.
        if critical_errors:
            shutil.rmtree(self.local_secret_root, ignore_errors=True)
            self.journal.update(status="failed", dirty=True, cleanup_errors=errors)
            raise MatrixError(
                "cleanup stopped before reconnect/restore to preserve fault isolation: "
                + "; ".join(critical_errors)
            )
        for name, action in (
            ("resume_paused_runtime", self.resume_witness_runtime),
            ("remove_isolated_pressure", self.remove_isolated_pressure),
        ):
            try:
                action()
                self.event("cleanup.step", step=name, status="passed")
            except Exception as exc:
                rendered = f"{name}:{exc}"
                errors.append(rendered)
                critical_errors.append(rendered)
                self.event("cleanup.step", step=name, status="failed", error=str(exc))
                break
        if critical_errors:
            shutil.rmtree(self.local_secret_root, ignore_errors=True)
            self.journal.update(status="failed", dirty=True, cleanup_errors=errors)
            raise MatrixError(
                "cleanup stopped before reconnect/restore to preserve fault isolation: "
                + "; ".join(critical_errors)
            )
        network_errors: list[str] = []
        sites = self.network_fault_sites | self.journal.values("network_fault_sites")
        for site in sorted(sites):
            try:
                self.remove_partition(site)
            except Exception as exc:
                rendered = f"remove_scoped_network_faults:{site}:{exc}"
                errors.append(rendered)
                network_errors.append(rendered)
        if network_errors:
            shutil.rmtree(self.local_secret_root, ignore_errors=True)
            self.journal.update(status="failed", dirty=True, cleanup_errors=errors)
            raise MatrixError(
                "cleanup stopped before restore because network isolation could not be reconciled: "
                + "; ".join(network_errors)
            )
        baseline_errors: list[str] = []
        try:
            if self.witness_mutated:
                self.restore_once()
                self.witness_mutated = False
            self.remove_owned_aux_databases()
            self.event("cleanup.step", step="restore_vacant_baseline", status="passed")
        except Exception as exc:
            rendered = f"restore_vacant_baseline:{exc}"
            errors.append(rendered)
            baseline_errors.append(rendered)
            self.event("cleanup.step", step="restore_vacant_baseline", status="failed", error=str(exc))
        try:
            self.verify_complete_baseline()
            self.event("cleanup.step", step="verify_complete_baseline", status="passed")
        except Exception as exc:
            rendered = f"verify_complete_baseline:{exc}"
            errors.append(rendered)
            baseline_errors.append(rendered)
            self.event("cleanup.step", step="verify_complete_baseline", status="failed", error=str(exc))
        shutil.rmtree(self.local_secret_root, ignore_errors=True)
        if baseline_errors:
            self.journal.update(status="failed", dirty=True, cleanup_errors=errors)
            raise MatrixError("; ".join(baseline_errors))
        if assurance_errors:
            self.journal.update(status="failed", dirty=True, cleanup_errors=errors)
            raise MatrixError(
                "cleanup reached a safe baseline but assurance remains incomplete: "
                + "; ".join(assurance_errors)
            )
        self.journal.update(
            status="cleanup_verified_pending_postflight",
            lifecycle_phase="cleanup_verified_pending_postflight",
            dirty=True,
            cleanup_errors=errors,
            witness_mutated=False,
            evidence_failed=self.evidence_failed,
        )
        self.event("cleanup.complete")

    def write_summary(self, *, status: str, error: str | None = None) -> None:
        payload = {
            "schema_version": RUNNER_SCHEMA,
            "status": status,
            "scenario": self.scenario,
            "tag": self.tag,
            "operator": self.operator,
            "observer": self.observer,
            "incident_commander": self.incident_commander,
            "reason": self.reason,
            "expected_commit": self.expected_head,
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "error": error,
            "cleanup_verified": status == "passed",
            "runner_arvan_mutation_attempted": False,
            "runner_webapp_writer_flag_mutation_attempted": False,
            "original_witness_transition_prevented": (
                self.journal.values("staged_sites")
                <= self.journal.values("original_key_absence_sites")
            ),
            "control_requests": self.control_requests,
            "control_request_budget": 100,
            "control_bytes_upper_bound": self.control_bytes_upper_bound,
            "transport_operations": self.transport_operations,
            "transport_bytes_upper_bound": self.transport_bytes_upper_bound,
            "cleanup_transport_operations": self.cleanup_transport_operations,
            "cleanup_transport_bytes_upper_bound": self.cleanup_transport_bytes_upper_bound,
            "dpi_byte_budget": self.dpi_byte_budget,
            "dpi_accounting": "conservative_all_transport_upper_bound",
            "max_scenario_seconds": MAX_SCENARIO_SECONDS,
            "secret_output_detected": self.secret_output_detected,
        }
        temporary = self.summary_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, self.summary_path)
        fsync_directory(self.summary_path.parent)

    def run_full_postflight(self, *, expect_remote_campaign: bool = True) -> None:
        output = self.artifact_dir / "postflight.json"
        arguments = [
            "python3",
            str(ROOT / "scripts/plan_writer_witness_real_host_matrix.py"),
            "--mode",
            "preflight",
            "--expected-commit",
            self.expected_head,
        ]
        if expect_remote_campaign:
            arguments.extend(
                (
                    "--expected-active-campaign-tag",
                    self.tag,
                    "--expected-active-campaign-scenario",
                    self.scenario,
                    "--expected-active-campaign-not-after",
                    self.campaign_not_after,
                )
            )
            if getattr(self, "_cleanup_mode", False):
                arguments.append("--allow-expired-active-campaign")
        arguments.extend(("--output", str(output)))
        # The pinned preflight helper opens one independent SSH session to each
        # of the four fixed roles. It is intentionally treated as an opaque,
        # worst-case transport consumer because it cannot reuse this runner's
        # ControlMaster sockets.
        for role in ("webapp_fi", "webapp_ir", "matrix_witness", "rollback_witness"):
            self._reserve_transport_budget(
                role=role,
                kind="ssh",
                payload_bytes=SSH_COMMAND_PAYLOAD_MAX,
                force_master_reservation=True,
            )
        completed = self.command(
            "full_exact_sha_postflight",
            arguments,
            timeout=900,
        )
        payload = safe_json(output)
        if completed.returncode != 0 or payload.get("status") != "preflight_passed":
            raise MatrixError("full exact-SHA postflight did not pass")
        self.journal.update(
            status="postflight_verified_release_pending",
            lifecycle_phase="postflight_verified_release_pending",
            dirty=True,
            postflight_path=str(output),
            postflight_sha256=file_sha256(output),
            postflight_expected_remote_campaign=expect_remote_campaign,
        )

    def finalize_campaign(self) -> None:
        if self.journal.payload.get("lifecycle_phase") != "postflight_verified_release_pending":
            raise MatrixError("campaign cannot release ownership before exact postflight")
        self.assert_remote_campaign_owned(allow_expired_cleanup=True)
        self.release_remote_campaign()
        self.journal.update(
            status="completed",
            lifecycle_phase="completed",
            dirty=False,
            completed_at=utc_now(),
            cleanup_errors=[],
        )

    def recover_postflight_release_pending(self) -> None:
        """Reconcile only an exact active claim or its exact release tombstone."""
        try:
            inspected_state = self.inspect_remote_campaign(
                "recover_postflight_remote_campaign"
            )
        except Exception as exc:
            self.journal.update(remote_campaign_release_state="ambiguous", dirty=True)
            raise MatrixError(
                "postflight campaign ownership/release state is ambiguous"
            ) from exc
        if inspected_state == "released_exact":
            self.remote_campaign_claimed = False
            self.remote_campaign_ambiguous = False
            self.journal.update(
                remote_campaign_claimed=False,
                remote_campaign_ambiguous=False,
                remote_campaign_release_state="released",
                dirty=True,
            )
            self.run_full_postflight(expect_remote_campaign=False)
            self.journal.update(
                status="completed",
                lifecycle_phase="completed",
                dirty=False,
                completed_at=utc_now(),
                remote_release_reconciled=True,
            )
            return
        if inspected_state != "active_exact":
            self.journal.update(remote_campaign_release_state="ambiguous", dirty=True)
            raise MatrixError(
                "postflight recovery requires the exact active claim or exact release tombstone"
            )
        self.remote_campaign_claimed = True
        self.remote_campaign_ambiguous = False
        self.journal.update(
            remote_campaign_claimed=True,
            remote_campaign_ambiguous=False,
            remote_campaign_claim_state="owned",
            dirty=True,
        )
        self.run_full_postflight()
        self.finalize_campaign()


def git_value(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def acquire_local_lock(scenario: str, expected_head: str) -> int:
    secure_directory(LOCK_PATH.parent)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(LOCK_PATH, flags, 0o600)
    except OSError as exc:
        raise MatrixError("Matrix lock cannot be opened as an owner-controlled regular file") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise MatrixError("Matrix lock must be one owner-only regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise MatrixError(f"another Matrix run owns {LOCK_PATH}") from exc
    except BaseException:
        os.close(descriptor)
        raise
    token = uuid.uuid4().hex
    payload = f"pid={os.getpid()}\ntoken={token}\nscenario={scenario}\nhead={expected_head}\n".encode()
    os.ftruncate(descriptor, 0)
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.write(descriptor, payload) != len(payload):
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise MatrixError("failed to persist Matrix lock ownership")
    os.fsync(descriptor)
    return descriptor


def release_local_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def build_plan(args: argparse.Namespace, expected_head: str) -> dict[str, Any]:
    return {
        "schema_version": RUNNER_SCHEMA,
        "status": "planned",
        "mutates_state": False,
        "scenario": args.scenario,
        "steps": list(SCENARIOS[args.scenario]),
        "expected_commit": expected_head,
        "safety": {
            "one_scenario_per_process": True,
            "requires_passing_pinned_preflight": True,
            "requires_distinct_operator_observer": True,
            "requires_preflight_bound_observer_approval": True,
            "requires_out_of_band_console_and_restore_authorization": True,
            "requires_persistent_multiplexed_ssh": True,
            "accounts_http_ssh_scp_and_reconnect_upper_bounds": True,
            "max_scenario_seconds": MAX_SCENARIO_SECONDS,
            "minimum_all_transport_dpi_byte_budget": MIN_DPI_BYTE_BUDGET,
            "keeps_network_isolation_until_requesters_and_credentials_are_gone": True,
            "retains_evidence_before_restore": True,
            "uses_only_replacement_witness_for_transitions": True,
            "arvan_changes_forbidden": True,
            "webapp_writer_activation_forbidden": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "approve", "execute", "recover"), default="plan")
    parser.add_argument("--scenario", choices=tuple(SCENARIOS))
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--campaign-journal", type=Path)
    parser.add_argument("--operator")
    parser.add_argument("--observer")
    parser.add_argument("--incident-commander")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--observer-signature", type=Path)
    parser.add_argument("--commander-signature", type=Path)
    parser.add_argument("--allowed-signers", type=Path, default=TRUSTED_ALLOWED_SIGNERS)
    parser.add_argument("--reason")
    parser.add_argument("--change-id")
    parser.add_argument("--out-of-band-console")
    parser.add_argument("--alternate-communications")
    parser.add_argument("--maintenance-window-start")
    parser.add_argument("--maintenance-window-end")
    parser.add_argument("--dpi-byte-budget", type=int)
    parser.add_argument("--restore-authorized-by")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign_root = DEFAULT_CAMPAIGN_ROOT
    if args.mode != "plan":
        secure_directory(DEFAULT_CONTROLLER_ROOT)
        secure_directory(DEFAULT_ARTIFACT_ROOT)
        secure_directory(campaign_root)
    if args.mode == "recover":
        if not args.campaign_journal:
            raise MatrixError("recover mode requires --campaign-journal")
        journal_path = args.campaign_journal.resolve()
        if journal_path.parent != campaign_root.resolve():
            raise MatrixError("campaign journal is outside the configured durable campaign root")
        journal = CampaignJournal.load(journal_path)
        if journal.payload.get("dirty") is not True:
            raise MatrixError("campaign journal is not marked dirty")
        scenario = str(journal.payload.get("scenario") or "")
        expected_head = str(journal.payload.get("expected_commit") or "")
        tag = str(journal.payload.get("tag") or "")
        baseline = journal.payload.get("baseline")
        recorded_artifact_dir = Path(str(journal.payload.get("artifact_dir") or ""))
        if (
            scenario not in SCENARIOS
            or not re.fullmatch(r"[0-9a-f]{40}", expected_head)
            or not re.fullmatch(r"wwm_[0-9a-f]{12}", tag)
            or not isinstance(baseline, dict)
            or not recorded_artifact_dir.is_absolute()
        ):
            raise MatrixError("campaign journal cannot be recovered safely")
        recorded_artifact_dir = direct_durable_child(
            recorded_artifact_dir,
            DEFAULT_ARTIFACT_ROOT,
            label="campaign artifact directory",
        )
        if recorded_artifact_dir.exists():
            artifact_dir = secure_directory(recorded_artifact_dir, create=False)
        else:
            artifact_dir = (
                DEFAULT_ARTIFACT_ROOT
                / f"recovery-{tag}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}"
            )
            artifact_dir.mkdir(mode=0o700)
            secure_directory(artifact_dir, create=False)
            journal.update(
                artifact_dir=str(artifact_dir),
                original_artifact_dir=str(recorded_artifact_dir),
                evidence_loss_detected=True,
                dirty=True,
            )
        if git_value("branch", "--show-current") != EXPECTED_BRANCH:
            raise MatrixError("campaign recovery is on the wrong branch")
        if git_value("rev-parse", "HEAD") != expected_head or git_value("status", "--porcelain"):
            raise MatrixError("campaign recovery requires the exact clean frozen commit")
        lock_descriptor = acquire_local_lock(scenario, expected_head)
        controller: Controller | None = None
        try:
            recovery_entry_phase = str(
                journal.payload.get("lifecycle_phase")
                or journal.payload.get("status")
                or ""
            )
            journal.update(status="recovering", dirty=True, recovery_started_at=utc_now())
            controller = Controller(
                scenario=scenario,
                operator=str(journal.payload.get("operator") or "recovery-operator"),
                observer=str(journal.payload.get("observer") or "recovery-observer"),
                incident_commander=str(journal.payload.get("incident_commander") or "recovery-commander"),
                reason=str(journal.payload.get("reason") or "recover interrupted matrix campaign"),
                expected_head=expected_head,
                preflight={},
                baseline=baseline,  # type: ignore[arg-type]
                artifact_dir=artifact_dir,
                campaign_root=campaign_root,
                dpi_byte_budget=int(journal.payload.get("dpi_byte_budget") or 0),
                tag=tag,
                existing_journal=journal,
            )
            try:
                controller.enter_cleanup_mode()
                if recovery_entry_phase in {
                    "postflight_verified_release_pending",
                    "remote_campaign_released",
                }:
                    controller.recover_postflight_release_pending()
                else:
                    controller.cleanup()
                    controller.run_full_postflight()
                    controller.finalize_campaign()
            except BaseException as exc:
                error = str(exc) or exc.__class__.__name__
                journal.update(status="failed", dirty=True, recovery_error=error)
                controller.write_summary(status="failed", error=f"recovery:{error}")
                raise MatrixError(f"campaign recovery remains incomplete: {error}") from exc
            controller.write_summary(status="passed")
            print(json.dumps({"status": "recovered", "scenario": scenario, "tag": tag}, sort_keys=True))
            return 0
        finally:
            release_local_lock(lock_descriptor)

    if not args.scenario or not args.expected_commit:
        raise MatrixError("plan, approve, and execute modes require --scenario and --expected-commit")
    expected_head = args.expected_commit.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise MatrixError("--expected-commit must be one exact lowercase Git SHA")
    if args.mode == "plan":
        payload = build_plan(args, expected_head)
        output = args.output or Path(f"/tmp/{args.scenario.lower()}-writer-witness-plan.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(output, 0o600)
        print(json.dumps({"status": "planned", "scenario": args.scenario, "output": str(output)}, sort_keys=True))
        return 0

    if args.mode == "approve":
        if os.getenv(OBSERVER_CONFIRM_ENV) != OBSERVER_CONFIRM_VALUE:
            raise MatrixError("observer approval confirmation is missing")
        required_approval_values = (
            args.preflight, args.observer, args.incident_commander, args.reason,
            args.change_id, args.out_of_band_console, args.alternate_communications,
            args.maintenance_window_start, args.maintenance_window_end, args.dpi_byte_budget,
            args.restore_authorized_by,
        )
        if not all(required_approval_values):
            raise MatrixError("approve mode requires concrete roles, incident, window, communications, and DPI budget")
        if args.dpi_byte_budget < MIN_DPI_BYTE_BUDGET:
            raise MatrixError("approve mode DPI byte budget is below the closed all-transport ceiling")
        if args.observer.strip().casefold() == args.incident_commander.strip().casefold():
            raise MatrixError("abort observer and incident commander must be distinct")
        try:
            window_start = datetime.fromisoformat(args.maintenance_window_start.replace("Z", "+00:00"))
            window_end = datetime.fromisoformat(args.maintenance_window_end.replace("Z", "+00:00"))
            if window_start.tzinfo is None or window_end.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise MatrixError("maintenance window must be timezone-aware ISO-8601") from exc
        now = datetime.now(timezone.utc)
        if window_start > now or window_end <= now or window_end > window_start + timedelta(hours=8):
            raise MatrixError("approval must be issued inside a maintenance window of at most eight hours")
        if args.allowed_signers != TRUSTED_ALLOWED_SIGNERS:
            raise MatrixError("approve mode requires the canonical trusted signer policy")
        allowed_signers_raw = read_secure_regular(
            TRUSTED_ALLOWED_SIGNERS,
            label="trusted signer policy",
        )
        assert_source_pinned_signer_policy(allowed_signers_raw)
        preflight_raw = read_secure_regular(args.preflight, label="preflight artifact")
        preflight = parse_json_bytes(preflight_raw, label="preflight artifact")
        validate_preflight(preflight, expected_head)
        approval_now = datetime.now(timezone.utc)
        output = args.output or (
            DEFAULT_CONTROLLER_ROOT / "approvals" / f"{args.scenario.lower()}-writer-witness-approval.json"
        )
        secure_directory(output.parent)
        payload = {
            "schema_version": APPROVAL_SCHEMA,
            "status": "approved",
            "scenario": args.scenario,
            "expected_commit": expected_head,
            "preflight_sha256": hashlib.sha256(preflight_raw).hexdigest(),
            "observer": args.observer.strip(),
            "incident_commander": args.incident_commander.strip(),
            "reason": args.reason.strip(),
            "change_id": args.change_id.strip(),
            "authorization_nonce": uuid.uuid4().hex,
            "approved_at": approval_now.isoformat().replace("+00:00", "Z"),
            "expires_at": (approval_now + timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
            "out_of_band_console_ready": True,
            "alternate_communications_ready": True,
            "maintenance_window_confirmed": True,
            "dpi_budget_confirmed": True,
            "restore_authorized": True,
            "max_control_requests": 100,
            "max_scenario_seconds": MAX_SCENARIO_SECONDS,
            "dpi_byte_budget": args.dpi_byte_budget,
            "out_of_band_console": args.out_of_band_console.strip(),
            "alternate_communications": args.alternate_communications.strip(),
            "maintenance_window_start": args.maintenance_window_start.strip(),
            "maintenance_window_end": args.maintenance_window_end.strip(),
            "restore_backup_sha256": preflight["observed_baseline"]["matrix_witness_dark_baseline"]["backup_sha256"],
            "restore_authorized_by": args.restore_authorized_by.strip(),
            "allowed_signers_sha256": hashlib.sha256(allowed_signers_raw).hexdigest(),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            output_flags |= os.O_NOFOLLOW
        descriptor = os.open(output, output_flags, 0o600)
        try:
            raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            if os.write(descriptor, raw) != len(raw):
                raise MatrixError("failed to persist complete observer approval")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(output.parent)
        print(json.dumps({"status": "approved", "scenario": args.scenario, "output": str(output)}, sort_keys=True))
        return 0

    if os.getenv(CONFIRM_ENV) != CONFIRM_VALUE or os.getenv(SCENARIO_CONFIRM_ENV) != args.scenario:
        raise MatrixError("live Matrix execution confirmation is missing or does not match the one scenario")
    if (
        not args.preflight or not args.approval or not args.operator or not args.operator.strip()
        or not args.reason or not args.reason.strip() or not args.observer_signature
        or not args.commander_signature
        or not args.change_id or not args.change_id.strip()
    ):
        raise MatrixError("execute mode requires signed approval, named operator, preflight, and incident reason")
    if git_value("branch", "--show-current") != EXPECTED_BRANCH:
        raise MatrixError("Matrix runner is on the wrong branch")
    if git_value("rev-parse", "HEAD") != expected_head or git_value("status", "--porcelain"):
        raise MatrixError("Matrix runner requires the exact clean frozen commit")
    if args.allowed_signers != TRUSTED_ALLOWED_SIGNERS:
        raise MatrixError("execute mode requires the canonical trusted signer policy")
    preflight_raw = read_secure_regular(args.preflight, label="preflight artifact")
    approval_raw = read_secure_regular(args.approval, label="observer approval")
    observer_signature_raw = read_secure_regular(
        args.observer_signature,
        label="observer signature",
    )
    commander_signature_raw = read_secure_regular(
        args.commander_signature,
        label="incident commander signature",
    )
    allowed_signers_raw = read_secure_regular(
        TRUSTED_ALLOWED_SIGNERS,
        label="trusted signer policy",
    )
    assert_source_pinned_signer_policy(allowed_signers_raw)
    preflight = parse_json_bytes(preflight_raw, label="preflight artifact")
    baseline = validate_preflight(preflight, expected_head)
    approval = parse_json_bytes(approval_raw, label="observer approval")
    observer, incident_commander = validate_approval(
        approval,
        scenario=args.scenario,
        expected_head=expected_head,
        preflight_sha256=hashlib.sha256(preflight_raw).hexdigest(),
        operator=args.operator.strip(),
        reason=args.reason.strip(),
        change_id=args.change_id.strip(),
        expected_restore_sha256=baseline["matrix_witness_dark_baseline"]["backup_sha256"],
        expected_allowed_signers_sha256=hashlib.sha256(allowed_signers_raw).hexdigest(),
    )
    assert_independent_signer_keys(allowed_signers_raw, observer, incident_commander)
    verify_approval_signature(
        approval_raw,
        observer_signature_raw,
        identity=observer,
        allowed_signers_raw=allowed_signers_raw,
    )
    verify_approval_signature(
        approval_raw,
        commander_signature_raw,
        identity=incident_commander,
        allowed_signers_raw=allowed_signers_raw,
    )
    approval_expiry = datetime.fromisoformat(
        str(approval["expires_at"]).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    campaign_not_after = min(
        approval_expiry,
        datetime.now(timezone.utc) + timedelta(seconds=MAX_SCENARIO_SECONDS),
    ).isoformat().replace("+00:00", "Z")

    lock_descriptor = acquire_local_lock(args.scenario, expected_head)
    controller: Controller | None = None
    previous_handlers: dict[int, Any] = {}
    error: str | None = None
    artifact_dir = args.artifact_dir or (
        DEFAULT_ARTIFACT_ROOT
        / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-{args.scenario.lower()}"
    )
    artifact_dir = direct_durable_child(
        artifact_dir,
        DEFAULT_ARTIFACT_ROOT,
        label="Matrix artifact directory",
    )
    try:
        assert_no_dirty_campaigns(campaign_root)
        artifact_dir.mkdir(exist_ok=False, mode=0o700)
        secure_directory(artifact_dir, create=False)
        controller = Controller(
            scenario=args.scenario,
            operator=args.operator.strip(),
            observer=observer,
            incident_commander=incident_commander,
            reason=args.reason.strip(),
            expected_head=expected_head,
            preflight=preflight,
            baseline=baseline,
            artifact_dir=artifact_dir,
            campaign_root=campaign_root,
            dpi_byte_budget=int(approval["dpi_byte_budget"]),
            campaign_not_after=campaign_not_after,
        )
        controller.journal.update(
            approval_path=str(args.approval),
            approval_sha256=hashlib.sha256(approval_raw).hexdigest(),
            observer_signature_sha256=hashlib.sha256(observer_signature_raw).hexdigest(),
            commander_signature_sha256=hashlib.sha256(commander_signature_raw).hexdigest(),
            allowed_signers_sha256=hashlib.sha256(allowed_signers_raw).hexdigest(),
        )

        pending_signal: int | None = None

        def abort_handler(signum: int, _frame: Any) -> None:
            nonlocal pending_signal
            pending_signal = signum
            if controller is not None:
                controller._abort_reason = f"Matrix controller received signal {signum}"

        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, abort_handler)
        try:
            preflight_sha256 = hashlib.sha256(preflight_raw).hexdigest()
            authorization_nonce = str(approval["authorization_nonce"])
            controller.journal.update(
                lifecycle_phase="local_authorization_consumption_intent",
                local_authorization_nonce=authorization_nonce,
                local_preflight_sha256=preflight_sha256,
                dirty=True,
            )
            consumed_preflight = consume_preflight(
                preflight_sha256, args.scenario, campaign_root
            )
            controller.journal.update(
                lifecycle_phase="local_preflight_consumed",
                consumed_preflight=str(consumed_preflight),
                dirty=True,
            )
            consumed_approval = consume_approval(approval, campaign_root)
            controller.journal.update(
                lifecycle_phase="local_authorization_consumed",
                consumed_approval=str(consumed_approval),
                dirty=True,
            )
            controller.run_scenario(
                authorization_nonce=authorization_nonce,
                preflight_sha256=preflight_sha256,
            )
        except BaseException as exc:
            error = str(exc) or exc.__class__.__name__
            controller.event("scenario.failed", error=error)
        finally:
            try:
                controller.cleanup()
            except BaseException as exc:
                error = f"{error + '; ' if error else ''}cleanup:{str(exc) or exc.__class__.__name__}"
        if (
            controller.journal.payload.get("status") == "cleanup_verified_pending_postflight"
            and not controller.remote_campaign_conflict
            and not controller.journal.payload.get("cleanup_errors")
        ):
            try:
                controller.run_full_postflight()
                controller.finalize_campaign()
            except BaseException as exc:
                postflight_error = str(exc) or exc.__class__.__name__
                error = f"{error + '; ' if error else ''}postflight:{postflight_error}"
                controller.journal.update(status="failed", dirty=True, error=error)
        if controller.evidence_failed:
            error = f"{error + '; ' if error else ''}evidence:one or more evidence writes failed"
        if controller.secret_output_detected:
            error = f"{error + '; ' if error else ''}evidence:a secret sentinel was redacted from command output"
        if pending_signal is not None:
            controller.journal.update(signal_received=pending_signal)
            error = f"{error + '; ' if error else ''}signal:received during Matrix execution or cleanup"
        status = "passed" if error is None else "failed"
        if status == "failed" and controller.journal.payload.get("dirty") is False:
            controller.journal.update(status="recovered_failed", dirty=False, error=error)
        controller.write_summary(status=status, error=error)
        print(
            json.dumps(
                {"status": status, "scenario": args.scenario, "artifact_dir": str(artifact_dir), "error": error},
                sort_keys=True,
            )
        )
        return 0 if status == "passed" else 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        release_local_lock(lock_descriptor)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MatrixError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"writer witness real-host Matrix failed closed: {exc}") from exc
