#!/usr/bin/env python3
"""Fail-closed blue/green ownership gate for the live Market Pipeline.

The existing stack is retained, stopped, and used as the exact rollback
runtime.  A differently named PRIVATE_PRIMARY Compose project may then use the
same durable bind mounts.  This tool never deletes a volume, database file,
session, checkpoint, outbox, image, or legacy container.

Database backup/migration and receiver-first startup remain owned by their
existing release-bound tools.  This gate owns only the seams those tools do
not: quiescing the prior project, moving capture authority markers after the
old owners have stopped, starting the three web capture roles, verifying the
single-owner topology, and restoring the exact prior containers on rollback.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

if __package__:
    from scripts import backup_market_pipeline_archive as backup
    from scripts import quiesce_production_legacy_market_collectors as legacy_handoff
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import backup_market_pipeline_archive as backup
    from scripts import quiesce_production_legacy_market_collectors as legacy_handoff


CONFIRMATION = "upgrade-market-pipeline-bluegreen"
# The promotion verifier consumes this public schema identifier.  Keep it
# stable and version the recovery semantics explicitly inside the journal so
# an already-deployed verifier continues to accept a successful new run.
SCHEMA = "market_pipeline_bluegreen_upgrade/1.0"
LEGACY_SCHEMA = SCHEMA
JOURNAL_CONTRACT_REVISION = 2
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
AUTHORITY_CONTRACT = "market_capture_authority/1.0"
POSTGRES_IMAGE = (
    "postgres:15-alpine@sha256:"
    "fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b"
)

ROLE_SERVICES = {
    "bot": (
        "market-fact-receiver",
        "market-store-adapter",
        "coin-estimator",
        "estimator-snapshot-sender",
    ),
    "web": (
        "market-database",
        "market-migration",
        "market-capture-account1",
        "market-capture-account2",
        "market-capture-external",
        "market-processor",
        "market-fact-sync-worker",
        "estimator-snapshot-receiver",
    ),
}
QUIESCE_ORDER = {
    "bot": tuple(reversed(ROLE_SERVICES["bot"])),
    "web": (
        "market-capture-account1",
        "market-capture-account2",
        "market-capture-external",
        "market-fact-sync-worker",
        "market-processor",
        "estimator-snapshot-receiver",
        "market-migration",
    ),
}
RESTORE_ORDER = {
    "bot": ROLE_SERVICES["bot"],
    "web": (
        "market-database",
        "estimator-snapshot-receiver",
        "market-processor",
        "market-fact-sync-worker",
        "market-capture-external",
        "market-capture-account1",
        "market-capture-account2",
    ),
}
WEB_PRIMARY_BASE = (
    "market-database",
    "estimator-snapshot-receiver",
    "market-processor",
    "market-fact-sync-worker",
)
CAPTURE_SERVICES = (
    "market-capture-external",
    "market-capture-account1",
    "market-capture-account2",
)
LEGACY_COLLECTOR_TIMERS = legacy_handoff.ROLE_TIMERS["web"]
LEGACY_COLLECTOR_SERVICES = legacy_handoff.ROLE_SERVICES["web"]
LEGACY_COLLECTOR_UNITS = legacy_handoff.ROLE_UNITS["web"]
LEGACY_COLLECTOR_SOURCE_OWNERSHIP = legacy_handoff.UNIT_SOURCE_OWNERSHIP
DEFAULT_MAINTENANCE_LOCK = Path(
    "/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock"
)


class UpgradeError(RuntimeError):
    """Stable, content-free refusal."""


def _run(
    arguments: Sequence[str], *, label: str, allow_failure: bool = False
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode and not allow_failure:
        raise UpgradeError(f"{label}_failed_rc_{result.returncode}")
    return result


def _text(arguments: Sequence[str], *, label: str) -> str:
    return _run(arguments, label=label).stdout.strip()


def _secure_file(path: Path, *, required_mode: int = 0o600) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise UpgradeError("upgrade_secure_file_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != required_mode
        or info.st_nlink != 1
    ):
        raise UpgradeError("upgrade_secure_file_invalid")


def _parse_env(path: Path) -> dict[str, str]:
    return _parse_env_text(_secure_read(path).decode("utf-8"))


def _parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise UpgradeError("upgrade_env_invalid")
        key, value = line.split("=", 1)
        if not key or key in values:
            raise UpgradeError("upgrade_env_invalid")
        values[key] = value
    return values


def _assert_no_intermediate_symlink(path: Path, *, error: str) -> None:
    """Reject relative, non-directory and symlinked parent components."""

    if not path.is_absolute():
        raise UpgradeError(error)
    current = Path(path.anchor)
    try:
        for component in path.parts[1:-1]:
            current /= component
            info = current.lstat()
            if current.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise UpgradeError(error)
    except OSError as exc:
        raise UpgradeError(error) from exc


def _secure_read(path: Path, *, expected_sha256: str | None = None) -> bytes:
    """Read one exact regular-file inode without lstat/open substitution."""

    _assert_no_intermediate_symlink(path, error="upgrade_secure_file_invalid")
    try:
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise UpgradeError("upgrade_secure_file_unavailable") from exc
    try:
        observed = os.fstat(descriptor)
        if (
            path.is_symlink()
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or stat.S_IMODE(observed.st_mode) != 0o600
            or observed.st_nlink != 1
            or before.st_dev != observed.st_dev
            or before.st_ino != observed.st_ino
        ):
            raise UpgradeError("upgrade_secure_file_invalid")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        value = b"".join(chunks)
    finally:
        os.close(descriptor)
    if expected_sha256 is not None and sha256(value).hexdigest() != expected_sha256:
        raise UpgradeError("upgrade_secure_file_digest_mismatch")
    return value


def _bound_env_values(payload: Mapping[str, Any], key: str) -> dict[str, str]:
    path = Path(str(payload.get(key) or ""))
    expected = str(payload.get(f"{key}_sha256") or "")
    if not HEX64.fullmatch(expected):
        raise UpgradeError("upgrade_env_drift")
    try:
        return _parse_env_text(
            _secure_read(path, expected_sha256=expected).decode("utf-8")
        )
    except (UnicodeDecodeError, UpgradeError) as exc:
        raise UpgradeError("upgrade_env_drift") from exc


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _release_root_binding(
    release_root: Path, *, release_sha: str, release_tree: str
) -> dict[str, str]:
    if (
        not release_root.is_absolute()
        or release_root.is_symlink()
        or not HEX40.fullmatch(release_sha)
        or not HEX40.fullmatch(release_tree)
    ):
        raise UpgradeError("upgrade_release_root_invalid")
    try:
        canonical = release_root.resolve(strict=True)
        metadata = release_root.lstat()
    except OSError as exc:
        raise UpgradeError("upgrade_release_root_invalid") from exc
    if (
        canonical != release_root
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise UpgradeError("upgrade_release_root_invalid")
    # The deployed control payload is a commit-exact ``git archive`` and does
    # not contain ``.git``.  The caller supplies its already-verified commit
    # and tree; this journal binds those identities to the canonical path and
    # exact compose bytes consumed below.
    compose_dir = canonical / "deploy/market-data"
    for directory in (canonical / "deploy", compose_dir):
        try:
            info = directory.lstat()
        except OSError as exc:
            raise UpgradeError("upgrade_release_compose_invalid") from exc
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise UpgradeError("upgrade_release_compose_invalid")
    compose = compose_dir / "compose.yml"
    compose_web = compose_dir / "compose.web.yml"
    for path in (compose, compose_web):
        try:
            info = path.lstat()
        except OSError as exc:
            raise UpgradeError("upgrade_release_compose_invalid") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise UpgradeError("upgrade_release_compose_invalid")
    return {
        "release_root": str(canonical),
        "release_root_path_sha256": sha256(
            str(canonical).encode("utf-8")
        ).hexdigest(),
        "release_tree": release_tree,
        "compose_sha256": _sha256(compose),
        "compose_web_sha256": _sha256(compose_web),
    }


def _secure_control_read(path: Path, *, expected_sha256: str) -> bytes:
    """Read exact root-owned control bytes from a non-replaceable file inode."""

    _assert_no_intermediate_symlink(
        path, error="upgrade_release_compose_invalid"
    )
    try:
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise UpgradeError("upgrade_release_compose_invalid") from exc
    try:
        observed = os.fstat(descriptor)
        if (
            path.is_symlink()
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or observed.st_nlink != 1
            or stat.S_IMODE(observed.st_mode) & 0o022
            or observed.st_dev != before.st_dev
            or observed.st_ino != before.st_ino
        ):
            raise UpgradeError("upgrade_release_compose_invalid")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        value = b"".join(chunks)
    finally:
        os.close(descriptor)
    if sha256(value).hexdigest() != expected_sha256:
        raise UpgradeError("upgrade_release_root_binding_drift")
    return value


def _validate_release_root_binding(
    payload: Mapping[str, Any], *, supplied_root: Path | None = None
) -> Path:
    root_text = str(payload.get("release_root") or "")
    root = Path(root_text)
    if supplied_root is not None and supplied_root != root:
        raise UpgradeError("upgrade_release_root_binding_drift")
    observed = _release_root_binding(
        root,
        release_sha=str(payload.get("release_sha") or ""),
        release_tree=str(payload.get("release_tree") or ""),
    )
    if any(payload.get(key) != value for key, value in observed.items()):
        raise UpgradeError("upgrade_release_root_binding_drift")
    return root


def _json_digest(payload: Mapping[str, Any]) -> str:
    return sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    ).hexdigest()


def _systemd_state(action: str, unit: str) -> bool:
    completed = _run(
        ["systemctl", action, "--quiet", unit],
        label="upgrade_legacy_collector_state",
        allow_failure=True,
    )
    if action == "is-active":
        if completed.returncode not in {0, 3}:
            raise UpgradeError("upgrade_legacy_collector_state_unknown")
    elif action == "is-enabled":
        if completed.returncode not in {0, 1}:
            raise UpgradeError("upgrade_legacy_collector_state_unknown")
    else:
        raise UpgradeError("upgrade_legacy_collector_state_unknown")
    return completed.returncode == 0


def _atomic_json(path: Path, payload: Mapping[str, Any], *, exclusive: bool = False) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    info = parent.lstat()
    if parent.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise UpgradeError("upgrade_journal_parent_invalid")
    if exclusive and (path.exists() or path.is_symlink()):
        raise UpgradeError("upgrade_journal_exists")
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_journal(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_secure_read(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, UpgradeError) as exc:
        raise UpgradeError("upgrade_journal_invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != SCHEMA
        or payload.get("journal_contract_revision")
        not in {None, JOURNAL_CONTRACT_REVISION}
    ):
        raise UpgradeError("upgrade_journal_invalid")
    return payload


def _is_legacy_journal(payload: Mapping[str, Any]) -> bool:
    return payload.get("journal_contract_revision") is None


def _ids(project: str, service: str, *, running: bool = False) -> list[str]:
    arguments = ["docker", "ps", "--no-trunc"]
    if not running:
        arguments.append("-a")
    arguments.extend(
        [
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
        ]
    )
    return [line for line in _text(arguments, label="upgrade_container_inventory").splitlines() if line]


def _project_services(project: str) -> set[str]:
    output = _text(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            '{{.Label "com.docker.compose.service"}}',
        ],
        label="upgrade_project_inventory",
    )
    return {line for line in output.splitlines() if line}


def _inspect(container_id: str) -> dict[str, Any]:
    try:
        document = json.loads(_text(["docker", "inspect", container_id], label="upgrade_inspect"))[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise UpgradeError("upgrade_inspect_invalid") from exc
    if not isinstance(document, dict):
        raise UpgradeError("upgrade_inspect_invalid")
    return document


def _identity(container_id: str, *, project: str, service: str) -> dict[str, Any]:
    document = _inspect(container_id)
    labels = (document.get("Config", {}) or {}).get("Labels", {}) or {}
    state = document.get("State", {}) or {}
    if (
        str(document.get("Id") or "") != container_id
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != service
    ):
        raise UpgradeError("upgrade_container_identity_mismatch")
    health = (state.get("Health", {}) or {}).get("Status")
    restart = (document.get("HostConfig", {}) or {}).get("RestartPolicy", {}) or {}
    return {
        "container_id": container_id,
        "service": service,
        "image_id": str(document.get("Image") or ""),
        "release_sha": str(labels.get("org.opencontainers.image.revision") or ""),
        "restart_name": str(restart.get("Name") or "no"),
        "restart_maximum_retry_count": int(restart.get("MaximumRetryCount") or 0),
        "running": state.get("Running") is True,
        "health": health,
    }


def _validate_envs(
    *, role: str, old_env: Path, new_env: Path, release_sha: str,
    old_project: str, new_project: str,
) -> tuple[dict[str, str], dict[str, str]]:
    old_values, new_values = _parse_env(old_env), _parse_env(new_env)
    if (
        old_values.get("MARKET_PIPELINE_PROJECT_NAME") != old_project
        or new_values.get("MARKET_PIPELINE_PROJECT_NAME") != new_project
        or old_project == new_project
        or not PROJECT.fullmatch(old_project)
        or not PROJECT.fullmatch(new_project)
        or new_values.get("MARKET_PIPELINE_RELEASE_SHA") != release_sha
        or new_values.get("MARKET_PIPELINE_MODE") != "live"
        or new_values.get("MARKET_PIPELINE_FEED_MODE") != "PRIVATE_PRIMARY"
        or new_values.get("MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY") != "1"
        or new_values.get("MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE") != "PRIVATE_PRIMARY"
    ):
        raise UpgradeError("upgrade_env_binding_invalid")
    image = new_values.get("MARKET_PIPELINE_IMAGE", "")
    if not HEX64.fullmatch(image):
        raise UpgradeError("upgrade_env_image_invalid")
    data_key = "MARKET_WEB_DATA_ROOT" if role == "web" else "MARKET_BOT_DATA_ROOT"
    if old_values.get(data_key) != new_values.get(data_key) or not str(new_values.get(data_key) or "").startswith("/"):
        raise UpgradeError("upgrade_data_root_drift")
    return old_values, new_values


def _marker_paths(values: Mapping[str, str]) -> dict[str, Path]:
    root = Path(values["MARKET_WEB_DATA_ROOT"])
    return {
        role: root / "sessions" / account / "authority-container.json"
        for role, account in (
            ("market-capture-account1", "account1"),
            ("market-capture-account2", "account2"),
        )
    }


def _load_marker(path: Path, *, role: str, release_sha: str | None = None) -> dict[str, Any]:
    _assert_no_intermediate_symlink(
        path, error="upgrade_capture_marker_invalid"
    )
    try:
        before = path.lstat()
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            info = os.fstat(descriptor)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(descriptor)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpgradeError("upgrade_capture_marker_invalid") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_dev != before.st_dev
        or info.st_ino != before.st_ino
        or info.st_uid != 10001
        or info.st_gid != 10001
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or not isinstance(payload, dict)
        or payload.get("contract") != AUTHORITY_CONTRACT
        or payload.get("authority") != "container"
        or payload.get("role") != role
        or (release_sha is not None and payload.get("release_sha") != release_sha)
    ):
        raise UpgradeError("upgrade_capture_marker_invalid")
    return payload


def _write_marker(path: Path, payload: Mapping[str, Any]) -> None:
    _assert_no_intermediate_symlink(
        path, error="upgrade_capture_marker_path_invalid"
    )
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    try:
        parent_before = path.parent.lstat()
        directory = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            parent_info = os.fstat(directory)
            current = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory,
            )
            try:
                current_info = os.fstat(current)
            finally:
                os.close(current)
            if (
                path.parent.is_symlink()
                or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_dev != parent_before.st_dev
                or parent_info.st_ino != parent_before.st_ino
                or parent_info.st_uid != 10001
                or stat.S_IMODE(parent_info.st_mode) & 0o022
                or not stat.S_ISREG(current_info.st_mode)
                or current_info.st_uid != 10001
                or current_info.st_gid != 10001
                or stat.S_IMODE(current_info.st_mode) != 0o600
                or current_info.st_nlink != 1
            ):
                raise UpgradeError("upgrade_capture_marker_path_invalid")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                temporary_name, flags, 0o600, dir_fd=directory
            )
            try:
                stream = os.fdopen(descriptor, "w", encoding="utf-8")
                descriptor = -1
                with stream:
                    json.dump(
                        payload,
                        stream,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chown(
                    temporary_name, 10001, 10001, dir_fd=directory
                )
                os.chmod(temporary_name, 0o600, dir_fd=directory)
                os.replace(
                    temporary_name,
                    path.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                )
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)
    except OSError as exc:
        raise UpgradeError("upgrade_capture_marker_path_invalid") from exc


def plan(
    *, role: str, old_env: Path, new_env: Path, journal: Path,
    release_sha: str, release_tree: str, release_root: Path,
    old_project: str, new_project: str,
) -> dict[str, Any]:
    release_binding = _release_root_binding(
        release_root, release_sha=release_sha, release_tree=release_tree
    )
    old_values, new_values = _validate_envs(
        role=role, old_env=old_env, new_env=new_env, release_sha=release_sha,
        old_project=old_project, new_project=new_project,
    )
    expected = set(ROLE_SERVICES[role])
    actual = _project_services(old_project)
    if actual != expected or _project_services(new_project):
        raise UpgradeError("upgrade_project_inventory_invalid")
    rows: list[dict[str, Any]] = []
    for service in ROLE_SERVICES[role]:
        ids = _ids(old_project, service)
        if len(ids) != 1:
            raise UpgradeError("upgrade_old_service_owner_count_invalid")
        row = _identity(ids[0], project=old_project, service=service)
        if service != "market-migration" and (not row["running"] or row["health"] != "healthy"):
            raise UpgradeError("upgrade_old_service_not_healthy")
        if service == "market-migration" and row["running"]:
            raise UpgradeError("upgrade_old_migration_running")
        rows.append(row)
    markers: dict[str, Any] = {}
    if role == "web":
        by_service = {row["service"]: row for row in rows}
        for marker_role, path in _marker_paths(old_values).items():
            marker = _load_marker(
                path, role=marker_role, release_sha=by_service[marker_role]["release_sha"]
            )
            markers[marker_role] = {"path": str(path), "payload": marker, "sha256": _sha256(path)}
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "journal_contract_revision": JOURNAL_CONTRACT_REVISION,
        "status": "planned",
        "role": role,
        "release_sha": release_sha,
        **release_binding,
        "old_project": old_project,
        "new_project": new_project,
        "old_env": str(old_env),
        "new_env": str(new_env),
        "old_env_sha256": _sha256(old_env),
        "new_env_sha256": _sha256(new_env),
        "new_image_id": new_values["MARKET_PIPELINE_IMAGE"],
        "services": rows,
        "markers": markers,
        "marker_transition": {
            "status": "NOT_STARTED",
            "authorized_at_utc": None,
            "entries": {},
        },
        "backup_receipt_sha256": None,
        "source_backup_receipt_sha256": None,
        "offhost_backup_receipt_sha256": None,
        "offhost_backup_binding": None,
        "new_capture_ids": {},
        "product_authority_changed": False,
        "state_deleted": False,
        "secrets_disclosed": False,
    }
    _atomic_json(journal, payload, exclusive=True)
    return payload


def _validate_journal(
    payload: Mapping[str, Any], *, role: str, release_sha: str,
    validate_release_root: bool = True,
) -> None:
    if (
        payload.get("schema") != SCHEMA
        or payload.get("role") != role
        or payload.get("release_sha") != release_sha
        or payload.get("state_deleted") is not False
        or payload.get("secrets_disclosed") is not False
        or payload.get("product_authority_changed") is not False
    ):
        raise UpgradeError("upgrade_journal_binding_invalid")
    for key in ("old_env", "new_env"):
        _bound_env_values(payload, key)
    if _is_legacy_journal(payload) and validate_release_root:
        raise UpgradeError("upgrade_legacy_journal_forward_forbidden")
    if validate_release_root:
        _validate_release_root_binding(payload)


def quiesce_workload(*, journal: Path, role: str, release_sha: str) -> dict[str, Any]:
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
    if payload["status"] not in {"planned", "workload_quiesced"}:
        raise UpgradeError("upgrade_quiesce_state_invalid")
    rows = {row["service"]: row for row in payload["services"]}
    for service in QUIESCE_ORDER[role]:
        row = rows[service]
        if _ids(payload["old_project"], service) != [row["container_id"]]:
            raise UpgradeError("upgrade_old_service_identity_drift")
        current = _identity(row["container_id"], project=payload["old_project"], service=service)
        if current["running"]:
            _run(["docker", "update", "--restart=no", row["container_id"]], label="upgrade_restart_disable")
            _run(["docker", "stop", "-t", "30", row["container_id"]], label="upgrade_stop")
        if _identity(row["container_id"], project=payload["old_project"], service=service)["running"]:
            raise UpgradeError("upgrade_old_workload_stop_failed")
    payload["status"] = "workload_quiesced"
    _atomic_json(journal, payload)
    return payload


def _verify_offhost_backup_receipt(
    *,
    path: Path,
    expected_sha256: str,
    source_receipt: Mapping[str, Any],
    source_receipt_sha256: str,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    expected_web_role_env_sha256: str,
    maximum_age_seconds: int | None,
) -> dict[str, Any]:
    if not HEX64.fullmatch(expected_sha256):
        raise UpgradeError("upgrade_offhost_backup_receipt_digest_mismatch")
    try:
        receipt = json.loads(
            _secure_read(path, expected_sha256=expected_sha256).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, UpgradeError) as exc:
        raise UpgradeError("upgrade_offhost_backup_receipt_invalid") from exc
    expected_keys = {
        "schema", "status", "verified_at_utc", "release_sha", "release_tree",
        "image_id", "image_input_signature", "web_role_env_sha256",
        "host_preflight_receipt_sha256", "source_backup_receipt_sha256",
        "backup_status", "artifact", "off_host_copy_status",
        "database_mutated", "services_started", "product_authority_changed",
        "telegram_capture_cutover_authorized", "secrets_disclosed",
    }
    artifact = receipt.get("artifact")
    expected_artifact_keys = {
        "name", "ciphertext_sha256", "ciphertext_size_bytes",
        "plaintext_sha256", "plaintext_size_bytes",
        "authentication_hmac_sha256", "encryption_algorithm", "kdf",
        "kdf_iterations", "encryption_receipt_sha256",
        "encryption_receipt_path", "bot_copy_path",
    }
    source_artifact = source_receipt.get("backup")
    try:
        verified = datetime.fromisoformat(
            str(receipt["verified_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        source_created = datetime.fromisoformat(
            str(source_receipt["created_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise UpgradeError("upgrade_offhost_backup_receipt_invalid") from exc
    now = datetime.now(timezone.utc)
    bot_copy = Path(str((artifact or {}).get("bot_copy_path") or ""))
    encryption_receipt = Path(
        str((artifact or {}).get("encryption_receipt_path") or "")
    )
    expected_encryption_name = (
        str((artifact or {}).get("name") or "").removesuffix(".enc")
        + ".encryption.json"
    )
    if (
        not isinstance(receipt, dict)
        or set(receipt) != expected_keys
        or receipt.get("schema") != "market_pipeline_backup_offhost_copy/2.0"
        or receipt.get("status") != "PASS"
        or receipt.get("release_sha") != release_sha
        or receipt.get("release_tree") != release_tree
        or receipt.get("image_id") != image_id
        or receipt.get("image_input_signature") != image_input_signature
        or receipt.get("web_role_env_sha256")
        != expected_web_role_env_sha256
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(receipt.get("host_preflight_receipt_sha256") or ""),
        )
        or receipt.get("source_backup_receipt_sha256")
        != source_receipt_sha256
        or receipt.get("backup_status") != "PASS"
        or receipt.get("off_host_copy_status") != "PASS_ENCRYPTED_VERIFIED"
        or receipt.get("database_mutated") is not False
        or receipt.get("services_started") is not False
        or receipt.get("product_authority_changed") is not False
        or receipt.get("telegram_capture_cutover_authorized") is not False
        or receipt.get("secrets_disclosed") is not False
        or not isinstance(artifact, dict)
        or set(artifact) != expected_artifact_keys
        or not isinstance(source_artifact, dict)
        or artifact.get("plaintext_sha256") != source_artifact.get("sha256")
        or artifact.get("plaintext_size_bytes") != source_artifact.get("size_bytes")
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(artifact.get("ciphertext_sha256") or "")
        )
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(artifact.get("authentication_hmac_sha256") or ""),
        )
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(artifact.get("encryption_receipt_sha256") or ""),
        )
        or not isinstance(artifact.get("ciphertext_size_bytes"), int)
        or artifact["ciphertext_size_bytes"] <= 0
        or artifact.get("encryption_algorithm")
        != "AES-256-CBC+PBKDF2-HMAC-SHA256"
        or artifact.get("kdf") != "PBKDF2-HMAC-SHA256"
        or artifact.get("kdf_iterations") != 600000
        or verified != source_created
        or verified > now + timedelta(seconds=30)
        or (
            maximum_age_seconds is not None
            and now - verified > timedelta(seconds=maximum_age_seconds)
        )
        or not bot_copy.is_absolute()
        or not encryption_receipt.is_absolute()
        or bot_copy.parent != encryption_receipt.parent
        or bot_copy.name != artifact.get("name")
        or encryption_receipt.name != expected_encryption_name
        or not re.fullmatch(
            r"market-archive-before-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.dump\.enc",
            bot_copy.name,
        )
    ):
        raise UpgradeError("upgrade_offhost_backup_receipt_invalid")
    return {
        "receipt_path_sha256": sha256(str(path).encode("utf-8")).hexdigest(),
        "receipt_sha256": expected_sha256,
        "source_backup_receipt_sha256": source_receipt_sha256,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "artifact_name": artifact["name"],
        "ciphertext_sha256": artifact["ciphertext_sha256"],
        "ciphertext_size_bytes": artifact["ciphertext_size_bytes"],
        "plaintext_sha256": artifact["plaintext_sha256"],
        "plaintext_size_bytes": artifact["plaintext_size_bytes"],
        "encryption_receipt_sha256": artifact["encryption_receipt_sha256"],
        "off_host_copy_status": "PASS_ENCRYPTED_VERIFIED",
    }


def quiesce_database(
    *, journal: Path, role: str, release_sha: str,
    backup_receipt: Path, expected_backup_receipt_sha256: str,
    offhost_backup_receipt: Path,
    expected_offhost_backup_receipt_sha256: str,
    release_tree: str, image_id: str, image_input_signature: str,
    backup_maximum_age_seconds: int,
) -> dict[str, Any]:
    if (
        role != "web"
        or not HEX64.fullmatch(expected_backup_receipt_sha256)
        or not HEX64.fullmatch(expected_offhost_backup_receipt_sha256)
        or not HEX40.fullmatch(release_tree)
        or not HEX64.fullmatch(image_id)
        or not re.fullmatch(r"[0-9a-f]{64}", image_input_signature)
        or not 60 <= backup_maximum_age_seconds <= 86400
    ):
        raise UpgradeError("upgrade_database_invocation_invalid")
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
    if payload["status"] not in {
        "workload_quiesced", "database_quiesce_prepared", "database_quiesced"
    }:
        raise UpgradeError("upgrade_database_state_invalid")
    if (
        payload["status"] in {"database_quiesce_prepared", "database_quiesced"}
        and (
            payload.get("source_backup_receipt_sha256")
            != expected_backup_receipt_sha256
            or payload.get("offhost_backup_receipt_sha256")
            != expected_offhost_backup_receipt_sha256
        )
    ):
        raise UpgradeError("upgrade_backup_receipt_digest_mismatch")
    try:
        bound_backup_bytes = _secure_read(
            backup_receipt,
            expected_sha256=expected_backup_receipt_sha256,
        )
        bound_backup_payload = json.loads(bound_backup_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, UpgradeError) as exc:
        raise UpgradeError("upgrade_backup_receipt_digest_mismatch")
    try:
        receipt = backup.verify_receipt(
            env_file=Path(str(payload["old_env"])),
            receipt=backup_receipt,
            release_sha=release_sha,
            release_tree=release_tree,
            image_id=image_id,
            image_input_signature=image_input_signature,
            maximum_age_seconds=(
                None
                if payload["status"]
                in {"database_quiesce_prepared", "database_quiesced"}
                else backup_maximum_age_seconds
            ),
        )
    except (OSError, ValueError, backup.BackupError) as exc:
        raise UpgradeError("upgrade_backup_receipt_invalid") from exc
    if receipt != bound_backup_payload:
        raise UpgradeError("upgrade_backup_receipt_drift")
    if receipt.get("status") != "PASS":
        raise UpgradeError("upgrade_backup_receipt_not_pass")
    offhost_binding = _verify_offhost_backup_receipt(
        path=offhost_backup_receipt,
        expected_sha256=expected_offhost_backup_receipt_sha256,
        source_receipt=receipt,
        source_receipt_sha256=expected_backup_receipt_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=image_id,
        image_input_signature=image_input_signature,
        expected_web_role_env_sha256=str(payload["new_env_sha256"]),
        maximum_age_seconds=(
            None
            if payload["status"]
            in {"database_quiesce_prepared", "database_quiesced"}
            else backup_maximum_age_seconds
        ),
    )
    rows = {row["service"]: row for row in payload["services"]}
    database = rows["market-database"]
    if any(_ids(payload["old_project"], service, running=True) for service in QUIESCE_ORDER[role]):
        raise UpgradeError("upgrade_old_workload_still_running")
    if payload["status"] == "workload_quiesced":
        # WAL and both independently-digested receipts are durable before the
        # first restart-policy change or database stop.
        payload["backup_receipt_sha256"] = expected_backup_receipt_sha256
        payload["source_backup_receipt_sha256"] = (
            expected_backup_receipt_sha256
        )
        payload["offhost_backup_receipt_sha256"] = (
            expected_offhost_backup_receipt_sha256
        )
        payload["offhost_backup_binding"] = offhost_binding
        payload["status"] = "database_quiesce_prepared"
        _atomic_json(journal, payload)
    elif payload.get("offhost_backup_binding") != offhost_binding:
        raise UpgradeError("upgrade_offhost_backup_binding_drift")
    # Re-read the exact immutable receipt bytes after the durable PREPARED WAL
    # and immediately before the first Docker mutation.  A pathname swap or
    # operator replacement during verification therefore fails closed.
    if (
        _secure_read(
            backup_receipt,
            expected_sha256=expected_backup_receipt_sha256,
        )
        != bound_backup_bytes
    ):
        raise UpgradeError("upgrade_backup_receipt_drift")
    _secure_read(
        offhost_backup_receipt,
        expected_sha256=expected_offhost_backup_receipt_sha256,
    )
    if _identity(database["container_id"], project=payload["old_project"], service="market-database")["running"]:
        _run(["docker", "update", "--restart=no", database["container_id"]], label="upgrade_database_restart_disable")
        _run(["docker", "stop", "-t", "60", database["container_id"]], label="upgrade_database_stop")
    if _identity(database["container_id"], project=payload["old_project"], service="market-database")["running"]:
        raise UpgradeError("upgrade_old_database_stop_failed")
    payload["status"] = "database_quiesced"
    _atomic_json(journal, payload)
    return payload


def _new_identity(payload: Mapping[str, Any], service: str, *, healthy: bool = True) -> dict[str, Any]:
    ids = _ids(str(payload["new_project"]), service)
    if len(ids) != 1:
        raise UpgradeError("upgrade_new_service_owner_count_invalid")
    row = _identity(ids[0], project=str(payload["new_project"]), service=service)
    expected_image = str(payload["new_image_id"])
    if not expected_image.startswith("sha256:"):
        expected_image = f"sha256:{expected_image}"
    if (
        row["image_id"] != expected_image
        or row["release_sha"] != payload["release_sha"]
        or not row["running"]
        or (healthy and row["health"] != "healthy")
    ):
        raise UpgradeError("upgrade_new_service_identity_invalid")
    return row


def _new_database_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    service = "market-database"
    ids = _ids(str(payload["new_project"]), service)
    if len(ids) != 1:
        raise UpgradeError("upgrade_new_database_owner_count_invalid")
    document = _inspect(ids[0])
    labels = (document.get("Config", {}) or {}).get("Labels", {}) or {}
    state = document.get("State", {}) or {}
    mounts = [
        mount
        for mount in document.get("Mounts", [])
        if mount.get("Destination") == "/var/lib/postgresql/data"
    ]
    new_values = _bound_env_values(payload, "new_env")
    expected_root = Path(new_values["MARKET_WEB_DATA_ROOT"]) / "postgres"
    if (
        str(document.get("Id") or "") != ids[0]
        or (document.get("Config", {}) or {}).get("Image") != POSTGRES_IMAGE
        or labels.get("com.docker.compose.project") != payload["new_project"]
        or labels.get("com.docker.compose.service") != service
        or len(mounts) != 1
        or Path(str(mounts[0].get("Source") or "")) != expected_root
        or state.get("Running") is not True
        or ((state.get("Health", {}) or {}).get("Status")) != "healthy"
    ):
        raise UpgradeError("upgrade_new_database_identity_invalid")
    return {"container_id": ids[0], "running": True, "healthy": True}


@contextmanager
def _maintenance_inode_guard(
    path: Path,
    *,
    release_sha: str,
    expected_lock: Mapping[str, Any] | None = None,
):
    """Hold and validate the exact maintenance inode for one transition."""

    _assert_no_intermediate_symlink(
        path, error="upgrade_maintenance_lock_invalid"
    )
    try:
        descriptor = os.open(
            path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise UpgradeError("upgrade_maintenance_transition_locked") from exc
    try:
        try:
            descriptor_info = os.fstat(descriptor)
            path_info = path.lstat()
            os.lseek(descriptor, 0, os.SEEK_SET)
            live_lock = json.loads(os.read(descriptor, 8192).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpgradeError("upgrade_maintenance_lock_invalid") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(descriptor_info.st_mode)
            or descriptor_info.st_uid != os.geteuid()
            or stat.S_IMODE(descriptor_info.st_mode) != 0o600
            or descriptor_info.st_nlink != 1
            or path_info.st_dev != descriptor_info.st_dev
            or path_info.st_ino != descriptor_info.st_ino
            or not isinstance(live_lock, dict)
            or live_lock.get("schema") != "market_pipeline_maintenance_lock/1.0"
            or live_lock.get("environment") != "production"
            or live_lock.get("host_role") != "web"
            or live_lock.get("release_sha") != release_sha
            or live_lock.get("device") != descriptor_info.st_dev
            or live_lock.get("inode") != descriptor_info.st_ino
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(live_lock.get("nonce_sha256") or "")
            )
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(live_lock.get("journal_path_sha256") or ""),
            )
            or (
                expected_lock is not None
                and live_lock != dict(expected_lock)
            )
        ):
            raise UpgradeError("upgrade_maintenance_lock_invalid")
        yield descriptor, live_lock
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _verify_legacy_collector_handoff(
    path: Path, *, expected_sha256: str, release_sha: str,
    maintenance_lock_path: Path,
    held_lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if held_lock is None:
        with _maintenance_inode_guard(
            maintenance_lock_path, release_sha=release_sha
        ) as (_descriptor, observed):
            return _verify_legacy_collector_handoff(
                path,
                expected_sha256=expected_sha256,
                release_sha=release_sha,
                maintenance_lock_path=maintenance_lock_path,
                held_lock=observed,
            )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise UpgradeError("upgrade_legacy_collector_receipt_digest_invalid")
    try:
        receipt = json.loads(
            _secure_read(path, expected_sha256=expected_sha256).decode("utf-8")
        )
        verified = datetime.fromisoformat(
            str(receipt["verified_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        ValueError,
        UpgradeError,
    ) as exc:
        raise UpgradeError("upgrade_legacy_collector_receipt_invalid") from exc
    now = datetime.now(timezone.utc)
    units = receipt.get("current_units")
    if (
        receipt.get("schema") != legacy_handoff.SCHEMA
        or receipt.get("host_role") != "web"
        or receipt.get("status") != "QUIESCED"
        or receipt.get("release_sha") != release_sha
        or receipt.get("secrets_disclosed") is not False
        or not isinstance(units, dict)
        or set(units) != set(LEGACY_COLLECTOR_UNITS)
        or any(
            not isinstance(units.get(unit), dict)
            or set(units[unit])
            != {"unit_sha256", "active", "enabled", "source_codes"}
            or units[unit].get("active") is not False
            or units[unit].get("enabled") is not False
            or units[unit].get("source_codes")
            != sorted(LEGACY_COLLECTOR_SOURCE_OWNERSHIP[unit])
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(units[unit].get("unit_sha256") or "")
            )
            for unit in LEGACY_COLLECTOR_UNITS
        )
        or verified > now + timedelta(seconds=30)
        or now - verified > timedelta(seconds=120)
    ):
        raise UpgradeError("upgrade_legacy_collector_receipt_invalid")
    lock_binding = receipt.get("maintenance_lock")
    live_lock = dict(held_lock)
    lock_info = maintenance_lock_path.lstat()
    if (
        not isinstance(lock_binding, dict)
        or live_lock != lock_binding
        or live_lock.get("schema") != "market_pipeline_maintenance_lock/1.0"
        or live_lock.get("environment") != "production"
        or live_lock.get("host_role") != "web"
        or live_lock.get("release_sha") != release_sha
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(live_lock.get("journal_path_sha256") or "")
        )
        or live_lock.get("device") != lock_info.st_dev
        or live_lock.get("inode") != lock_info.st_ino
    ):
        raise UpgradeError("upgrade_maintenance_lock_invalid")
    # A recent receipt is only historical evidence.  Re-read the live units
    # immediately before capture authority can move.
    if any(_systemd_state("is-active", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    if any(_systemd_state("is-enabled", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    return receipt


def _verify_bot_authority_handoff(
    path: Path,
    *,
    expected_sha256: str,
    release_sha: str,
    bluegreen_journal: Path,
    prepared_bluegreen_journal_sha256: str,
    marker_authority_sha256: str,
    allow_transferred: bool = False,
) -> dict[str, Any]:
    """Validate bot-fi evidence without pretending its systemd is local.

    The controller performs the live bot-fi unit/lock check on bot-fi.  The
    web upgrader consumes only the exact, fresh, host-bound receipt copied by
    that controller and refuses a generic or web-host receipt.
    """

    if (
        not HEX64.fullmatch(expected_sha256)
        or not HEX64.fullmatch(prepared_bluegreen_journal_sha256)
        or not HEX64.fullmatch(marker_authority_sha256)
    ):
        raise UpgradeError("upgrade_bot_collector_receipt_digest_invalid")
    try:
        receipt = json.loads(
            _secure_read(path, expected_sha256=expected_sha256).decode("utf-8")
        )
        verified = datetime.fromisoformat(
            str(receipt["verified_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        ValueError,
        UpgradeError,
    ) as exc:
        raise UpgradeError("upgrade_bot_collector_receipt_invalid") from exc
    expected_statuses = {"AUTHORITY_TRANSFERRING"}
    if allow_transferred:
        expected_statuses.add("AUTHORITY_TRANSFERRED")
    units = receipt.get("current_units")
    lock = receipt.get("maintenance_lock")
    authority = receipt.get("authority_transfer")
    now = datetime.now(timezone.utc)
    expected_units = legacy_handoff.ROLE_UNITS["bot"]
    if (
        receipt.get("schema") != legacy_handoff.SCHEMA
        or receipt.get("host_role") != "bot"
        or receipt.get("status") not in expected_statuses
        or receipt.get("release_sha") != release_sha
        or receipt.get("secrets_disclosed") is not False
        or not isinstance(units, dict)
        or set(units) != set(expected_units)
        or any(
            not isinstance(units.get(unit), dict)
            or set(units[unit])
            != {"unit_sha256", "active", "enabled", "source_codes"}
            or units[unit].get("active") is not False
            or units[unit].get("enabled") is not False
            or units[unit].get("source_codes")
            != sorted(legacy_handoff.UNIT_SOURCE_OWNERSHIP[unit])
            or not HEX64.fullmatch(
                str(units[unit].get("unit_sha256") or "")
            )
            for unit in expected_units
        )
        or not isinstance(lock, dict)
        or lock.get("schema") != "market_pipeline_maintenance_lock/1.0"
        or lock.get("environment") != "production"
        or lock.get("host_role") != "bot"
        or lock.get("release_sha") != release_sha
        or not HEX64.fullmatch(str(lock.get("nonce_sha256") or ""))
        or not HEX64.fullmatch(
            str(lock.get("journal_path_sha256") or "")
        )
        or not isinstance(lock.get("device"), int)
        or isinstance(lock.get("device"), bool)
        or not isinstance(lock.get("inode"), int)
        or isinstance(lock.get("inode"), bool)
        or not isinstance(authority, dict)
        or authority.get("bluegreen_journal_path_sha256")
        != sha256(str(bluegreen_journal).encode("utf-8")).hexdigest()
        or authority.get("prepared_bluegreen_journal_sha256")
        != prepared_bluegreen_journal_sha256
        or authority.get("marker_authority_sha256")
        != marker_authority_sha256
        or (
            receipt.get("status") == "AUTHORITY_TRANSFERRING"
            and authority.get("authorization_bluegreen_journal_sha256")
            is not None
        )
        or (
            receipt.get("status") == "AUTHORITY_TRANSFERRED"
            and not HEX64.fullmatch(
                str(
                    authority.get(
                        "authorization_bluegreen_journal_sha256"
                    )
                    or ""
                )
            )
        )
        or verified > now + timedelta(seconds=30)
        or now - verified > timedelta(
            seconds=legacy_handoff.MAX_HANDOFF_AGE_SECONDS
        )
    ):
        raise UpgradeError("upgrade_bot_collector_receipt_invalid")
    return receipt


def _verify_transferred_handoff(
    *,
    payload: Mapping[str, Any],
    journal: Path,
    release_sha: str,
    descriptor: int,
    held_lock: Mapping[str, Any],
) -> dict[str, Any]:
    receipt_path = Path(str(payload.get("legacy_collector_receipt") or ""))
    expected_digest = str(
        payload.get("legacy_collector_receipt_sha256") or ""
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise UpgradeError("upgrade_legacy_collector_receipt_digest_invalid")
    try:
        bound_receipt = json.loads(
            _secure_read(
                receipt_path, expected_sha256=expected_digest
            ).decode("utf-8")
        )
        receipt = legacy_handoff._read(
            receipt_path, release_sha=release_sha, host_role="web"
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        UpgradeError,
        legacy_handoff.CollectorHandoffError,
    ) as exc:
        raise UpgradeError("upgrade_legacy_collector_receipt_invalid") from exc
    authority = receipt.get("authority_transfer")
    marker_authority_sha256 = _marker_authority_digest(payload)
    if (
        receipt != bound_receipt
        or receipt.get("status") not in {"AUTHORITY_TRANSFERRED", "PRIMARY_COMMITTED"}
        or receipt.get("maintenance_lock") != dict(held_lock)
        or not isinstance(authority, dict)
        or authority.get("bluegreen_journal_path_sha256")
        != sha256(str(journal).encode("utf-8")).hexdigest()
        or authority.get("marker_authority_sha256") != marker_authority_sha256
        or authority != payload.get("legacy_authority_transfer")
        or authority.get("authorization_bluegreen_journal_sha256")
        != payload.get("legacy_authority_transfer_journal_sha256")
        or os.fstat(descriptor).st_ino != held_lock.get("inode")
    ):
        raise UpgradeError("upgrade_legacy_collector_authority_invalid")
    if any(_systemd_state("is-active", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    if any(_systemd_state("is-enabled", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    return receipt


def _marker_authority_digest(payload: Mapping[str, Any]) -> str:
    """Digest only immutable marker authority, never progress/rollback fields."""

    transition = _validate_marker_transition(payload, allow_rollback=True)
    return _json_digest(
        {
            "authorized_at_utc": transition["authorized_at_utc"],
            "entries": {
                role: {
                    "path": row["path"],
                    "prior_sha256": row["prior_sha256"],
                    "target_sha256": row["target_sha256"],
                    "target_payload": row["target_payload"],
                }
                for role, row in sorted(transition["entries"].items())
            },
        }
    )


def _verify_authority_transfer_in_progress(
    *,
    payload: Mapping[str, Any],
    journal: Path,
    receipt_path: Path,
    release_sha: str,
    descriptor: int,
    held_lock: Mapping[str, Any],
) -> dict[str, Any]:
    _secure_file(receipt_path)
    try:
        receipt = legacy_handoff._read(
            receipt_path, release_sha=release_sha, host_role="web"
        )
    except legacy_handoff.CollectorHandoffError as exc:
        raise UpgradeError("upgrade_legacy_collector_receipt_invalid") from exc
    authority = receipt.get("authority_transfer")
    expected_prepared_digest = payload.get(
        "legacy_authority_prepared_journal_sha256"
    )
    if expected_prepared_digest is None:
        # SIGKILL after the legacy WAL but before the next blue/green journal
        # write leaves the prepared blue journal itself as the exact proof.
        expected_prepared_digest = _sha256(journal)
    if (
        receipt.get("status") != "AUTHORITY_TRANSFERRING"
        or receipt.get("maintenance_lock") != dict(held_lock)
        or not isinstance(authority, dict)
        or authority.get("bluegreen_journal_path_sha256")
        != sha256(str(journal).encode("utf-8")).hexdigest()
        or authority.get("prepared_bluegreen_journal_sha256")
        != expected_prepared_digest
        or authority.get("authorization_bluegreen_journal_sha256") is not None
        or authority.get("marker_authority_sha256")
        != _marker_authority_digest(payload)
        or os.fstat(descriptor).st_ino != held_lock.get("inode")
    ):
        raise UpgradeError("upgrade_legacy_collector_authority_invalid")
    if any(_systemd_state("is-active", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    if any(_systemd_state("is-enabled", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    return receipt


def _recover_completed_authority_binding(
    *, payload: dict[str, Any], journal: Path, receipt_path: Path,
    receipt: Mapping[str, Any], release_sha: str, descriptor: int,
    held_lock: Mapping[str, Any],
) -> dict[str, Any]:
    """Finish the blue journal after SIGKILL following the legacy final WAL."""

    authority = receipt.get("authority_transfer")
    if (
        receipt.get("status") not in {"AUTHORITY_TRANSFERRED", "PRIMARY_COMMITTED"}
        or receipt.get("maintenance_lock") != dict(held_lock)
        or not isinstance(authority, dict)
        or authority.get("bluegreen_journal_path_sha256")
        != sha256(str(journal).encode("utf-8")).hexdigest()
        or authority.get("authorization_bluegreen_journal_sha256")
        != _sha256(journal)
        or authority.get("marker_authority_sha256")
        != _marker_authority_digest(payload)
        or os.fstat(descriptor).st_ino != held_lock.get("inode")
    ):
        raise UpgradeError("upgrade_legacy_collector_authority_invalid")
    if any(_systemd_state("is-active", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    if any(_systemd_state("is-enabled", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    _verify_authorized_markers(payload)
    payload["legacy_collector_receipt_sha256"] = _sha256(receipt_path)
    payload["legacy_authority_transfer_journal_sha256"] = authority[
        "authorization_bluegreen_journal_sha256"
    ]
    payload["legacy_authority_transfer"] = dict(authority)
    _atomic_json(journal, payload)
    return payload


def _marker_target(
    *, marker_role: str, release_sha: str, authorized_at: str
) -> dict[str, Any]:
    return {
        "contract": AUTHORITY_CONTRACT,
        "authority": "container",
        "role": marker_role,
        "release_sha": release_sha,
        "authorized_at_utc": authorized_at,
    }


def _prepare_marker_transition(payload: dict[str, Any]) -> None:
    transition = payload.get("marker_transition")
    if not isinstance(transition, dict):
        raise UpgradeError("upgrade_capture_marker_transition_invalid")
    if transition.get("status") != "NOT_STARTED":
        return
    authorized_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    entries: dict[str, Any] = {}
    for marker_role, row in payload["markers"].items():
        target = _marker_target(
            marker_role=marker_role,
            release_sha=str(payload["release_sha"]),
            authorized_at=authorized_at,
        )
        entries[marker_role] = {
            "path": row["path"],
            "prior_sha256": row["sha256"],
            "prior_payload": row["payload"],
            "target_sha256": _json_digest(target),
            "target_payload": target,
            "status": "PENDING",
            "rollback_status": "NOT_STARTED",
        }
    transition.update(
        {
            "status": "PREPARED",
            "authorized_at_utc": authorized_at,
            "entries": entries,
        }
    )


def _validate_marker_transition(
    payload: Mapping[str, Any], *, allow_rollback: bool = False
) -> dict[str, Any]:
    transition = payload.get("marker_transition")
    if (
        not isinstance(transition, dict)
        or transition.get("status") not in {"PREPARED", "APPLYING", "COMPLETE"}
        or not isinstance(transition.get("authorized_at_utc"), str)
        or not isinstance(transition.get("entries"), dict)
        or set(transition["entries"]) != set(payload["markers"])
        or (
            transition.get("rollback_status") not in {None, "NOT_STARTED"}
            and not allow_rollback
        )
        or (
            allow_rollback
            and transition.get("rollback_status")
            not in {None, "NOT_STARTED", "RESTORING", "COMPLETE"}
        )
    ):
        raise UpgradeError("upgrade_capture_marker_transition_invalid")
    for marker_role, row in transition["entries"].items():
        original = payload["markers"][marker_role]
        expected_target = _marker_target(
            marker_role=marker_role,
            release_sha=str(payload["release_sha"]),
            authorized_at=str(transition["authorized_at_utc"]),
        )
        if (
            not isinstance(row, dict)
            or row.get("path") != original.get("path")
            or row.get("prior_sha256") != original.get("sha256")
            or row.get("prior_payload") != original.get("payload")
            or row.get("target_payload") != expected_target
            or row.get("target_sha256") != _json_digest(expected_target)
            or row.get("status") not in {"PENDING", "APPLIED"}
            or row.get("rollback_status")
            not in ({"NOT_STARTED", "RESTORED"} if allow_rollback else {"NOT_STARTED"})
        ):
            raise UpgradeError("upgrade_capture_marker_transition_invalid")
    return transition


def _apply_marker_transition(*, journal: Path, payload: dict[str, Any]) -> None:
    transition = _validate_marker_transition(payload)
    if transition["status"] == "COMPLETE":
        return
    transition["status"] = "APPLYING"
    _atomic_json(journal, payload)
    for marker_role in sorted(transition["entries"]):
        row = transition["entries"][marker_role]
        path = Path(str(row.get("path") or ""))
        current = _sha256(path)
        if current == row.get("target_sha256"):
            _load_marker(path, role=marker_role, release_sha=str(payload["release_sha"]))
        elif current == row.get("prior_sha256") and row.get("status") == "PENDING":
            _write_marker(path, row["target_payload"])
            if _sha256(path) != row.get("target_sha256"):
                raise UpgradeError("upgrade_capture_marker_write_mismatch")
            _load_marker(path, role=marker_role, release_sha=str(payload["release_sha"]))
        else:
            raise UpgradeError("upgrade_capture_marker_drift")
        row["status"] = "APPLIED"
        _atomic_json(journal, payload)
    transition["status"] = "COMPLETE"
    _atomic_json(journal, payload)


def prepare_capture_authority(
    *,
    journal: Path,
    role: str,
    release_sha: str,
    web_legacy_collector_receipt: Path,
    expected_web_legacy_collector_receipt_sha256: str,
    web_maintenance_lock_path: Path = DEFAULT_MAINTENANCE_LOCK,
) -> dict[str, Any]:
    if role != "web":
        raise UpgradeError("upgrade_capture_role_invalid")
    with _maintenance_inode_guard(
        web_maintenance_lock_path, release_sha=release_sha
    ) as (_descriptor, live_lock):
        payload = _read_journal(journal)
        _validate_journal(payload, role=role, release_sha=release_sha)
        if payload["status"] not in {
            "database_quiesced",
            "capture_authority_prepared",
        }:
            raise UpgradeError("upgrade_capture_prepare_state_invalid")
        if payload["status"] == "capture_authority_prepared":
            if (
                payload.get("legacy_collector_receipt_pre_authority_sha256")
                != expected_web_legacy_collector_receipt_sha256
                or payload.get("legacy_collector_receipt")
                != str(web_legacy_collector_receipt)
                or payload.get("maintenance_lock_path")
                != str(web_maintenance_lock_path)
            ):
                raise UpgradeError(
                    "upgrade_legacy_collector_receipt_digest_invalid"
                )
            _verify_legacy_collector_handoff(
                web_legacy_collector_receipt,
                expected_sha256=(
                    expected_web_legacy_collector_receipt_sha256
                ),
                release_sha=release_sha,
                maintenance_lock_path=web_maintenance_lock_path,
                held_lock=live_lock,
            )
            return payload
        _verify_legacy_collector_handoff(
            web_legacy_collector_receipt,
            expected_sha256=expected_web_legacy_collector_receipt_sha256,
            release_sha=release_sha,
            maintenance_lock_path=web_maintenance_lock_path,
            held_lock=live_lock,
        )
        if any(
            _ids(payload["old_project"], service, running=True)
            for service in ROLE_SERVICES["web"]
        ):
            raise UpgradeError("upgrade_old_owner_still_running")
        for service in WEB_PRIMARY_BASE:
            if service == "market-database":
                _new_database_identity(payload)
            else:
                _new_identity(payload, service)
        if any(
            _ids(payload["new_project"], service)
            for service in CAPTURE_SERVICES
        ):
            raise UpgradeError("upgrade_new_capture_already_exists")
        _prepare_marker_transition(payload)
        payload["legacy_collector_receipt_pre_authority_sha256"] = (
            expected_web_legacy_collector_receipt_sha256
        )
        payload["legacy_collector_receipt"] = str(
            web_legacy_collector_receipt
        )
        payload["maintenance_lock_path"] = str(web_maintenance_lock_path)
        payload["status"] = "capture_authority_prepared"
        _atomic_json(journal, payload)
        return payload


def authorize_captures(
    *, journal: Path, role: str, release_sha: str,
    web_legacy_collector_receipt: Path,
    expected_web_legacy_collector_receipt_sha256: str,
    web_maintenance_lock_path: Path,
    bot_legacy_collector_receipt: Path,
    expected_bot_legacy_collector_receipt_sha256: str,
) -> dict[str, Any]:
    if role != "web":
        raise UpgradeError("upgrade_capture_role_invalid")
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
    if payload["status"] not in {
        "capture_authority_prepared",
        "captures_authorized",
    }:
        raise UpgradeError("upgrade_capture_authorize_state_invalid")
    if (
        payload["status"] == "captures_authorized"
        and payload.get("legacy_collector_receipt_pre_authority_sha256")
        != expected_web_legacy_collector_receipt_sha256
    ):
        raise UpgradeError("upgrade_legacy_collector_receipt_digest_invalid")
    marker_authority_sha256 = _marker_authority_digest(payload)
    if payload["status"] == "capture_authority_prepared":
        prepared_journal_sha256 = _sha256(journal)
    else:
        prepared_journal_sha256 = str(
            payload.get("legacy_authority_prepared_journal_sha256") or ""
        )
    bot_handoff = _verify_bot_authority_handoff(
        bot_legacy_collector_receipt,
        expected_sha256=expected_bot_legacy_collector_receipt_sha256,
        release_sha=release_sha,
        bluegreen_journal=journal,
        prepared_bluegreen_journal_sha256=prepared_journal_sha256,
        marker_authority_sha256=marker_authority_sha256,
        allow_transferred=payload["status"] == "captures_authorized",
    )
    if payload["status"] == "captures_authorized" and (
        payload.get("bot_legacy_collector_receipt")
        != str(bot_legacy_collector_receipt)
        or payload.get("bot_legacy_collector_receipt_sha256")
        != expected_bot_legacy_collector_receipt_sha256
        or payload.get("bot_legacy_authority_transfer")
        != bot_handoff.get("authority_transfer")
    ):
        raise UpgradeError("upgrade_bot_collector_receipt_invalid")
    with _maintenance_inode_guard(
        web_maintenance_lock_path, release_sha=release_sha
    ) as (lock_descriptor, live_lock):
        payload = _read_journal(journal)
        _validate_journal(payload, role=role, release_sha=release_sha)
        try:
            current_handoff = legacy_handoff._read(
                web_legacy_collector_receipt,
                release_sha=release_sha,
                host_role="web",
            )
        except legacy_handoff.CollectorHandoffError as exc:
            raise UpgradeError("upgrade_legacy_collector_receipt_invalid") from exc
        handoff_status = current_handoff.get("status")
        if payload["status"] == "captures_authorized" and handoff_status in {
            "AUTHORITY_TRANSFERRED",
            "PRIMARY_COMMITTED",
        }:
            if (
                payload.get("legacy_collector_receipt_sha256")
                == _sha256(web_legacy_collector_receipt)
                and payload.get("legacy_authority_transfer")
                == current_handoff.get("authority_transfer")
            ):
                _verify_transferred_handoff(
                    payload=payload,
                    journal=journal,
                    release_sha=release_sha,
                    descriptor=lock_descriptor,
                    held_lock=live_lock,
                )
            else:
                _recover_completed_authority_binding(
                    payload=payload,
                    journal=journal,
                    receipt_path=web_legacy_collector_receipt,
                    receipt=current_handoff,
                    release_sha=release_sha,
                    descriptor=lock_descriptor,
                    held_lock=live_lock,
                )
            return payload
        if handoff_status == "QUIESCED":
            _verify_legacy_collector_handoff(
                web_legacy_collector_receipt,
                expected_sha256=(
                    expected_web_legacy_collector_receipt_sha256
                ),
                release_sha=release_sha,
                maintenance_lock_path=web_maintenance_lock_path,
                held_lock=live_lock,
            )
        elif handoff_status == "AUTHORITY_TRANSFERRING":
            if (
                payload.get("legacy_collector_receipt_pre_authority_sha256")
                != expected_web_legacy_collector_receipt_sha256
            ):
                raise UpgradeError(
                    "upgrade_legacy_collector_receipt_digest_invalid"
                )
            _verify_authority_transfer_in_progress(
                payload=payload,
                journal=journal,
                receipt_path=web_legacy_collector_receipt,
                release_sha=release_sha,
                descriptor=lock_descriptor,
                held_lock=live_lock,
            )
        elif handoff_status != "AUTHORITY_TRANSFERRED":
            raise UpgradeError("upgrade_legacy_collector_authority_invalid")
        if any(_ids(payload["old_project"], service, running=True) for service in ROLE_SERVICES["web"]):
            raise UpgradeError("upgrade_old_owner_still_running")
        for service in WEB_PRIMARY_BASE:
            if service == "market-database":
                _new_database_identity(payload)
            else:
                _new_identity(payload, service)
        if any(_ids(payload["new_project"], service) for service in CAPTURE_SERVICES):
            raise UpgradeError("upgrade_new_capture_already_exists")
        _validate_marker_transition(payload)
        if handoff_status == "QUIESCED":
            # This blue/green WAL is durable before the legacy handoff WAL;
            # the latter is durable before the first authority-marker write.
            try:
                transferring = (
                    legacy_handoff.prepare_capture_authority_transfer_with_held_lock(
                        descriptor=lock_descriptor,
                        journal=web_legacy_collector_receipt,
                        release_sha=release_sha,
                        host_role="web",
                        expected_lock=live_lock,
                        bluegreen_journal=journal,
                        prepared_bluegreen_journal_sha256=(
                            prepared_journal_sha256
                        ),
                        marker_authority_sha256=marker_authority_sha256,
                    )
                )
            except legacy_handoff.CollectorHandoffError as exc:
                raise UpgradeError(
                    "upgrade_legacy_authority_transfer_failed"
                ) from exc
            payload["legacy_authority_prepared_journal_sha256"] = (
                prepared_journal_sha256
            )
            payload["legacy_collector_receipt_sha256"] = _sha256(
                web_legacy_collector_receipt
            )
            payload["legacy_authority_transfer"] = transferring[
                "authority_transfer"
            ]
            _atomic_json(journal, payload)
        else:
            _verify_authority_transfer_in_progress(
                payload=payload,
                journal=journal,
                receipt_path=web_legacy_collector_receipt,
                release_sha=release_sha,
                descriptor=lock_descriptor,
                held_lock=live_lock,
            )
        # The historical receipt check may be seconds old after base/runtime
        # validation and two durable WAL writes.  Re-read both the legacy WAL
        # and live systemd state immediately before the first marker rename.
        _verify_authority_transfer_in_progress(
            payload=payload,
            journal=journal,
            receipt_path=web_legacy_collector_receipt,
            release_sha=release_sha,
            descriptor=lock_descriptor,
            held_lock=live_lock,
        )
        _apply_marker_transition(journal=journal, payload=payload)
        payload["bot_legacy_collector_receipt"] = str(
            bot_legacy_collector_receipt
        )
        payload["bot_legacy_collector_receipt_sha256"] = (
            expected_bot_legacy_collector_receipt_sha256
        )
        payload["bot_legacy_authority_transfer"] = bot_handoff.get(
            "authority_transfer"
        )
        payload["status"] = "captures_authorized"
        _atomic_json(journal, payload)
        transfer_journal_sha = _sha256(journal)
        try:
            transferred = (
                legacy_handoff.mark_capture_authority_transferred_with_held_lock(
                    descriptor=lock_descriptor,
                    journal=web_legacy_collector_receipt,
                    release_sha=release_sha,
                    host_role="web",
                    expected_lock=live_lock,
                    bluegreen_journal=journal,
                    authorization_bluegreen_journal_sha256=(
                        transfer_journal_sha
                    ),
                    marker_authority_sha256=marker_authority_sha256,
                )
            )
        except legacy_handoff.CollectorHandoffError as exc:
            raise UpgradeError("upgrade_legacy_authority_transfer_failed") from exc
        payload["legacy_collector_receipt_sha256"] = _sha256(
            web_legacy_collector_receipt
        )
        payload["legacy_authority_transfer_journal_sha256"] = (
            transfer_journal_sha
        )
        payload["legacy_authority_transfer"] = transferred[
            "authority_transfer"
        ]
        _atomic_json(journal, payload)
        return payload


def _compose(release_root: Path, env_file: Path) -> list[str]:
    return _compose_files(
        compose=release_root / "deploy/market-data/compose.yml",
        compose_web=release_root / "deploy/market-data/compose.web.yml",
        env_file=env_file,
    )


def _compose_files(
    *, compose: Path, compose_web: Path, env_file: Path
) -> list[str]:
    return [
        "docker", "compose", "--env-file", str(env_file),
        "-f", str(compose),
        "-f", str(compose_web),
        "--profile", "web",
    ]


def _write_sealed_input(path: Path, value: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(value):
            offset += os.write(descriptor, value[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _sealed_compose_invocation(
    *, journal: Path, payload: Mapping[str, Any], release_root: Path
):
    """Run Compose only from exact copied bytes, never re-open mutable inputs."""

    _validate_release_root_binding(payload, supplied_root=release_root)
    compose_source = release_root / "deploy/market-data/compose.yml"
    compose_web_source = release_root / "deploy/market-data/compose.web.yml"
    compose_bytes = _secure_control_read(
        compose_source, expected_sha256=str(payload["compose_sha256"])
    )
    compose_web_bytes = _secure_control_read(
        compose_web_source, expected_sha256=str(payload["compose_web_sha256"])
    )
    env_bytes = _secure_read(
        Path(str(payload["new_env"])),
        expected_sha256=str(payload["new_env_sha256"]),
    )
    parent = journal.parent
    parent_info = parent.lstat()
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise UpgradeError("upgrade_journal_parent_invalid")
    sealed = parent / (
        f".{journal.name}.compose.{os.getpid()}."
        f"{secrets.token_hex(8)}"
    )
    os.mkdir(sealed, mode=0o700)
    compose = sealed / "compose.yml"
    compose_web = sealed / "compose.web.yml"
    env_file = sealed / "runtime.env"
    try:
        _write_sealed_input(compose, compose_bytes)
        _write_sealed_input(compose_web, compose_web_bytes)
        _write_sealed_input(env_file, env_bytes)
        directory = os.open(sealed, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        yield _compose_files(
            compose=compose, compose_web=compose_web, env_file=env_file
        )
    finally:
        # Delete only the three files and directory created above.  A crash may
        # leave this root-only sealed copy behind, which is safe and visible;
        # it is never followed recursively or reused.
        for path in (env_file, compose_web, compose):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (
                path.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise UpgradeError("upgrade_sealed_compose_cleanup_invalid")
            path.unlink()
        sealed.rmdir()


def _verify_authorized_markers(payload: Mapping[str, Any]) -> None:
    transition = _validate_marker_transition(payload)
    if transition.get("status") != "COMPLETE":
        raise UpgradeError("upgrade_capture_marker_transition_incomplete")
    for marker_role, row in transition["entries"].items():
        path = Path(str(row["path"]))
        if _sha256(path) != row["target_sha256"]:
            raise UpgradeError("upgrade_capture_marker_drift")
        _load_marker(
            path,
            role=marker_role,
            release_sha=str(payload["release_sha"]),
        )


@contextmanager
def _capture_transition_guard(
    *, journal: Path, release_sha: str, supplied_root: Path | None = None
):
    initial = _read_journal(journal)
    _validate_journal(initial, role="web", release_sha=release_sha)
    release_root = _validate_release_root_binding(
        initial, supplied_root=supplied_root
    )
    lock_path = Path(str(initial.get("maintenance_lock_path") or ""))
    expected_lock = None
    receipt_path = Path(str(initial.get("legacy_collector_receipt") or ""))
    try:
        receipt = legacy_handoff._read(
            receipt_path, release_sha=release_sha, host_role="web"
        )
        expected_lock = receipt.get("maintenance_lock")
    except legacy_handoff.CollectorHandoffError as exc:
        raise UpgradeError("upgrade_legacy_collector_receipt_invalid") from exc
    if not isinstance(expected_lock, dict):
        raise UpgradeError("upgrade_maintenance_lock_invalid")
    with _maintenance_inode_guard(
        lock_path, release_sha=release_sha, expected_lock=expected_lock
    ) as (descriptor, live_lock):
        payload = _read_journal(journal)
        _validate_journal(payload, role="web", release_sha=release_sha)
        if _validate_release_root_binding(
            payload, supplied_root=supplied_root
        ) != release_root:
            raise UpgradeError("upgrade_release_root_binding_drift")
        _verify_transferred_handoff(
            payload=payload,
            journal=journal,
            release_sha=release_sha,
            descriptor=descriptor,
            held_lock=live_lock,
        )
        _verify_authorized_markers(payload)
        yield payload, release_root, descriptor, live_lock


def start_captures(
    *, journal: Path, role: str, release_sha: str, release_root: Path,
) -> dict[str, Any]:
    if role != "web":
        raise UpgradeError("upgrade_capture_start_state_invalid")
    with _capture_transition_guard(
        journal=journal, release_sha=release_sha, supplied_root=release_root
    ) as (payload, bound_root, descriptor, live_lock):
        if payload["status"] not in {
            "captures_authorized", "captures_starting", "captures_running"
        }:
            raise UpgradeError("upgrade_capture_start_state_invalid")
        if payload["status"] != "captures_running":
            payload["status"] = "captures_starting"
            _atomic_json(journal, payload)
        with _sealed_compose_invocation(
            journal=journal, payload=payload, release_root=bound_root
        ) as compose:
            for service in CAPTURE_SERVICES:
                _verify_transferred_handoff(
                    payload=payload, journal=journal, release_sha=release_sha,
                    descriptor=descriptor, held_lock=live_lock,
                )
                _verify_authorized_markers(payload)
                existing = _ids(payload["new_project"], service)
                if not existing:
                    _run(
                        [*compose, "up", "-d", "--no-deps", "--no-recreate", service],
                        label="upgrade_capture_start",
                    )
                for _attempt in range(90):
                    try:
                        row = _new_identity(payload, service)
                    except UpgradeError:
                        time.sleep(1)
                        continue
                    payload["new_capture_ids"][service] = row["container_id"]
                    _atomic_json(journal, payload)
                    break
                else:
                    raise UpgradeError("upgrade_capture_health_timeout")
                _verify_transferred_handoff(
                    payload=payload, journal=journal, release_sha=release_sha,
                    descriptor=descriptor, held_lock=live_lock,
                )
                _verify_authorized_markers(payload)
        _verify_transferred_handoff(
            payload=payload,
            journal=journal,
            release_sha=release_sha,
            descriptor=descriptor,
            held_lock=live_lock,
        )
        _verify_authorized_markers(payload)
        payload["status"] = "captures_running"
        _atomic_json(journal, payload)
        return payload


def verify(*, journal: Path, role: str, release_sha: str) -> dict[str, Any]:
    if role == "web":
        with _capture_transition_guard(
            journal=journal, release_sha=release_sha
        ) as (payload, _root, descriptor, live_lock):
            return _verify_runtime(
                journal=journal, role=role, release_sha=release_sha,
                payload=payload, descriptor=descriptor, live_lock=live_lock,
            )
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
    return _verify_runtime(
        journal=journal, role=role, release_sha=release_sha, payload=payload
    )


def _verify_runtime(
    *, journal: Path, role: str, release_sha: str,
    payload: dict[str, Any], descriptor: int | None = None,
    live_lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    expected_status = "captures_running" if role == "web" else "workload_quiesced"
    if payload["status"] not in {expected_status, "PASS"}:
        raise UpgradeError("upgrade_verify_state_invalid")
    if any(_ids(payload["old_project"], service, running=True) for service in ROLE_SERVICES[role]):
        raise UpgradeError("upgrade_old_owner_running")
    expected_new = (
        tuple(service for service in ROLE_SERVICES["web"] if service != "market-migration")
        if role == "web"
        else ROLE_SERVICES["bot"]
    )
    if _project_services(payload["new_project"]) != set(expected_new):
        raise UpgradeError("upgrade_new_project_inventory_invalid")
    for service in expected_new:
        if service == "market-database":
            _new_database_identity(payload)
        else:
            _new_identity(payload, service)
    if role == "web":
        if descriptor is None or live_lock is None:
            raise UpgradeError("upgrade_maintenance_lock_invalid")
        _verify_transferred_handoff(
            payload=payload, journal=journal, release_sha=release_sha,
            descriptor=descriptor, held_lock=live_lock,
        )
        _verify_authorized_markers(payload)
    payload["status"] = "PASS"
    _atomic_json(journal, payload)
    return payload


def _restart_argument(row: Mapping[str, Any]) -> str:
    name = str(row.get("restart_name") or "no")
    maximum = int(row.get("restart_maximum_retry_count") or 0)
    return f"{name}:{maximum}" if name == "on-failure" and maximum > 0 else name


def _legacy_marker_rows(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    markers = payload.get("markers")
    if not isinstance(markers, dict) or not markers:
        raise UpgradeError("upgrade_capture_marker_transition_invalid")
    rows: dict[str, dict[str, Any]] = {}
    for marker_role, original in markers.items():
        if (
            not isinstance(original, dict)
            or not isinstance(original.get("path"), str)
            or not isinstance(original.get("payload"), dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(original.get("sha256") or ""))
        ):
            raise UpgradeError("upgrade_capture_marker_transition_invalid")
        rows[str(marker_role)] = {
            "path": original["path"],
            "prior_sha256": original["sha256"],
            "prior_payload": original["payload"],
            "target_sha256": None,
        }
    return rows


def _assert_live_legacy_quiesced() -> None:
    if any(_systemd_state("is-active", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")
    if any(_systemd_state("is-enabled", unit) for unit in LEGACY_COLLECTOR_UNITS):
        raise UpgradeError("upgrade_legacy_collector_live_overlap")


def _rollback_authority_preflight(
    *, payload: Mapping[str, Any], journal: Path, release_sha: str,
    descriptor: int, live_lock: Mapping[str, Any],
) -> str:
    """Classify the two-journal crash state before any Docker mutation."""

    receipt_path = Path(str(payload.get("legacy_collector_receipt") or ""))
    try:
        receipt = legacy_handoff._read(
            receipt_path, release_sha=release_sha, host_role="web"
        )
    except legacy_handoff.CollectorHandoffError as exc:
        raise UpgradeError("upgrade_legacy_collector_receipt_invalid") from exc
    if (
        receipt.get("maintenance_lock") != dict(live_lock)
        or os.fstat(descriptor).st_dev != live_lock.get("device")
        or os.fstat(descriptor).st_ino != live_lock.get("inode")
    ):
        raise UpgradeError("upgrade_maintenance_lock_invalid")
    _assert_live_legacy_quiesced()
    transition = _validate_marker_transition(payload, allow_rollback=True)
    status = receipt.get("status")
    authority = receipt.get("authority_transfer")
    if status == "PRIMARY_COMMITTED":
        raise UpgradeError("upgrade_committed_runtime_rollback_forbidden")
    if status == "AUTHORITY_TRANSFERRING":
        _verify_authority_transfer_in_progress(
            payload=payload,
            journal=journal,
            receipt_path=receipt_path,
            release_sha=release_sha,
            descriptor=descriptor,
            held_lock=live_lock,
        )
        return "TRANSFER_STARTED"
    if status == "AUTHORITY_TRANSFERRED":
        _verify_transferred_handoff(
            payload=payload,
            journal=journal,
            release_sha=release_sha,
            descriptor=descriptor,
            held_lock=live_lock,
        )
        return "TRANSFER_STARTED"
    if status != "QUIESCED":
        raise UpgradeError("upgrade_legacy_collector_authority_invalid")
    if authority is None:
        pre_authority_digest = str(
            payload.get("legacy_collector_receipt_pre_authority_sha256") or ""
        )
        if (
            transition.get("status") != "PREPARED"
            or transition.get("rollback_status") not in {None, "NOT_STARTED"}
            or not re.fullmatch(r"[0-9a-f]{64}", pre_authority_digest)
            or _sha256(receipt_path) != pre_authority_digest
            or payload.get("legacy_authority_prepared_journal_sha256") is not None
            or any(
                row.get("status") != "PENDING"
                or row.get("rollback_status") != "NOT_STARTED"
                or _sha256(Path(str(row["path"]))) != row["prior_sha256"]
                for row in transition["entries"].values()
            )
        ):
            # A target marker with a still-unbound legacy journal is an
            # impossible state.  Refuse before stopping/removing a container.
            raise UpgradeError("upgrade_prelegacy_authority_state_invalid")
        return "PRE_LEGACY"
    if (
        not isinstance(authority, dict)
        or authority.get("bluegreen_journal_path_sha256")
        != sha256(str(journal).encode("utf-8")).hexdigest()
        or authority.get("marker_authority_sha256")
        != _marker_authority_digest(payload)
        or authority != payload.get("legacy_authority_transfer")
    ):
        raise UpgradeError("upgrade_legacy_collector_authority_invalid")
    # SIGKILL after the legacy restore WAL and before the next blue journal
    # write leaves QUIESCED with the exact historical binding.  Marker bytes
    # must already be completely restored in this state.
    if (
        transition.get("rollback_status") != "COMPLETE"
        or any(
            row.get("rollback_status") != "RESTORED"
            or _sha256(Path(str(row["path"]))) != row["prior_sha256"]
            for row in transition["entries"].values()
        )
    ):
        raise UpgradeError("upgrade_legacy_authority_restore_state_invalid")
    return "ALREADY_RESTORED"


def _rollback_preflight(
    payload: Mapping[str, Any], *, role: str,
    authority_mode: str = "NONE",
) -> None:
    raw_old_rows = payload.get("services")
    if not isinstance(raw_old_rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("service"), str)
        for row in raw_old_rows
    ):
        raise UpgradeError("upgrade_rollback_old_identity_drift")
    old_rows = {str(row["service"]): row for row in raw_old_rows}
    if (
        len(old_rows) != len(raw_old_rows)
        or set(old_rows) != set(ROLE_SERVICES[role])
    ):
        raise UpgradeError("upgrade_rollback_old_identity_drift")
    expected_new = (
        set(ROLE_SERVICES["web"]) - {"market-migration"}
        if role == "web"
        else set(ROLE_SERVICES["bot"])
    )
    actual_new = _project_services(str(payload["new_project"]))
    if not actual_new.issubset(expected_new):
        raise UpgradeError("upgrade_rollback_new_inventory_invalid")

    any_old_running = False
    for service in ROLE_SERVICES[role]:
        row = old_rows.get(service)
        if not isinstance(row, dict) or _ids(str(payload["old_project"]), service) != [
            row.get("container_id")
        ]:
            raise UpgradeError("upgrade_rollback_old_identity_drift")
        current = _identity(
            str(row["container_id"]), project=str(payload["old_project"]), service=service
        )
        if (
            current["image_id"] != row.get("image_id")
            or current["release_sha"] != row.get("release_sha")
            or current["restart_name"] not in {"no", row.get("restart_name")}
            or (
                current["restart_name"] == row.get("restart_name")
                and current["restart_maximum_retry_count"]
                != row.get("restart_maximum_retry_count")
            )
        ):
            raise UpgradeError("upgrade_rollback_old_identity_drift")
        any_old_running = any_old_running or bool(current["running"])
    if actual_new and any_old_running:
        raise UpgradeError("upgrade_rollback_owner_overlap")

    expected_image = str(payload["new_image_id"])
    if not expected_image.startswith("sha256:"):
        expected_image = f"sha256:{expected_image}"
    for service in sorted(actual_new):
        ids = _ids(str(payload["new_project"]), service)
        if len(ids) != 1:
            raise UpgradeError("upgrade_rollback_new_identity_invalid")
        if service == "market-database":
            document = _inspect(ids[0])
            labels = (document.get("Config", {}) or {}).get("Labels", {}) or {}
            mounts = [
                mount
                for mount in document.get("Mounts", [])
                if mount.get("Destination") == "/var/lib/postgresql/data"
            ]
            new_values = _bound_env_values(payload, "new_env")
            if (
                str(document.get("Id") or "") != ids[0]
                or (document.get("Config", {}) or {}).get("Image") != POSTGRES_IMAGE
                or labels.get("com.docker.compose.project") != payload["new_project"]
                or labels.get("com.docker.compose.service") != service
                or len(mounts) != 1
                or Path(str(mounts[0].get("Source") or ""))
                != Path(new_values["MARKET_WEB_DATA_ROOT"]) / "postgres"
            ):
                raise UpgradeError("upgrade_rollback_new_identity_invalid")
        else:
            current = _identity(
                ids[0], project=str(payload["new_project"]), service=service
            )
            if (
                current["image_id"] != expected_image
                or current["release_sha"] != payload["release_sha"]
            ):
                raise UpgradeError("upgrade_rollback_new_identity_invalid")

    if role == "web":
        if _is_legacy_journal(payload):
            entries = _legacy_marker_rows(payload)
            for marker_role, row in entries.items():
                path = Path(str(row["path"]))
                current = _sha256(path)
                if current == row["prior_sha256"]:
                    _load_marker(
                        path,
                        role=marker_role,
                        release_sha=str(row["prior_payload"]["release_sha"]),
                    )
                else:
                    _load_marker(path, role=marker_role, release_sha=str(payload["release_sha"]))
            return
        transition = payload.get("marker_transition")
        if not isinstance(transition, dict):
            raise UpgradeError("upgrade_capture_marker_transition_invalid")
        entries = transition.get("entries")
        if transition.get("status") == "NOT_STARTED":
            entries = {
                marker_role: {
                    "path": row["path"],
                    "prior_sha256": row["sha256"],
                    "prior_payload": row["payload"],
                    "target_sha256": None,
                }
                for marker_role, row in payload["markers"].items()
            }
        else:
            transition = _validate_marker_transition(payload, allow_rollback=True)
            entries = transition["entries"]
        if not isinstance(entries, dict) or set(entries) != set(payload["markers"]):
            raise UpgradeError("upgrade_capture_marker_transition_invalid")
        for marker_role, row in entries.items():
            path = Path(str(row.get("path") or ""))
            current = _sha256(path)
            if current not in {row.get("prior_sha256"), row.get("target_sha256")}:
                raise UpgradeError("upgrade_rollback_marker_drift")
            if authority_mode == "PRE_LEGACY" and current != row.get("prior_sha256"):
                raise UpgradeError("upgrade_prelegacy_authority_state_invalid")
            if not isinstance(marker_role, str):
                raise UpgradeError("upgrade_capture_marker_transition_invalid")
            release = (
                str(row["prior_payload"]["release_sha"])
                if current == row.get("prior_sha256")
                else str(payload["release_sha"])
            )
            _load_marker(path, role=marker_role, release_sha=release)


def _restore_markers(*, journal: Path, payload: dict[str, Any]) -> None:
    transition = payload["marker_transition"]
    if transition.get("status") == "NOT_STARTED":
        return
    transition = _validate_marker_transition(payload, allow_rollback=True)
    entries = transition["entries"]
    transition["rollback_status"] = "RESTORING"
    _atomic_json(journal, payload)
    for marker_role in sorted(entries):
        row = entries[marker_role]
        path = Path(row["path"])
        current = _sha256(path)
        if current == row["prior_sha256"]:
            _load_marker(
                path,
                role=marker_role,
                release_sha=str(row["prior_payload"]["release_sha"]),
            )
        elif current == row["target_sha256"]:
            _write_marker(path, row["prior_payload"])
            if _sha256(path) != row["prior_sha256"]:
                raise UpgradeError("upgrade_rollback_marker_write_mismatch")
        else:
            raise UpgradeError("upgrade_rollback_marker_drift")
        row["rollback_status"] = "RESTORED"
        _atomic_json(journal, payload)
    transition["rollback_status"] = "COMPLETE"
    _atomic_json(journal, payload)


def _verify_terminal_legacy_binding(
    *,
    payload: Mapping[str, Any],
    journal: Path,
    descriptor: int | None,
    live_lock: Mapping[str, Any] | None,
) -> None:
    receipt_path = Path(str(payload.get("legacy_collector_receipt") or ""))
    lock_path = Path(str(payload.get("maintenance_lock_path") or ""))
    expected_digest = str(
        payload.get("legacy_collector_receipt_sha256")
        or payload.get("legacy_collector_receipt_pre_authority_sha256")
        or ""
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise UpgradeError("upgrade_rollback_terminal_authority_drift")
    try:
        bound = json.loads(
            _secure_read(
                receipt_path, expected_sha256=expected_digest
            ).decode("utf-8")
        )
        receipt = legacy_handoff._read(
            receipt_path,
            release_sha=str(payload["release_sha"]),
            host_role="web",
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        UpgradeError,
        legacy_handoff.CollectorHandoffError,
    ) as exc:
        raise UpgradeError("upgrade_rollback_terminal_authority_drift") from exc
    expected_lock = receipt.get("maintenance_lock")
    if (
        receipt != bound
        or receipt.get("status") != "QUIESCED"
        or not isinstance(expected_lock, dict)
        or receipt.get("authority_transfer")
        != payload.get("legacy_authority_transfer")
    ):
        raise UpgradeError("upgrade_rollback_terminal_authority_drift")
    if descriptor is None or live_lock is None:
        with _maintenance_inode_guard(
            lock_path,
            release_sha=str(payload["release_sha"]),
            expected_lock=expected_lock,
        ) as (held_descriptor, held_lock):
            _verify_terminal_legacy_binding(
                payload=payload,
                journal=journal,
                descriptor=held_descriptor,
                live_lock=held_lock,
            )
        return
    authority = receipt.get("authority_transfer")
    if (
        dict(live_lock) != expected_lock
        or os.fstat(descriptor).st_dev != live_lock.get("device")
        or os.fstat(descriptor).st_ino != live_lock.get("inode")
        or (
            authority is not None
            and (
                not isinstance(authority, dict)
                or authority.get("bluegreen_journal_path_sha256")
                != sha256(str(journal).encode("utf-8")).hexdigest()
                or authority.get("marker_authority_sha256")
                != _marker_authority_digest(payload)
            )
        )
    ):
        raise UpgradeError("upgrade_rollback_terminal_authority_drift")
    _assert_live_legacy_quiesced()


def _verify_rolled_back_runtime(
    payload: Mapping[str, Any],
    *,
    role: str,
    journal: Path,
    descriptor: int | None = None,
    live_lock: Mapping[str, Any] | None = None,
) -> None:
    """Freshly prove a terminal rollback instead of trusting its journal word."""

    if _project_services(str(payload["new_project"])):
        raise UpgradeError("upgrade_rollback_new_project_remaining")
    raw_rows = payload.get("services")
    if not isinstance(raw_rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("service"), str)
        for row in raw_rows
    ):
        raise UpgradeError("upgrade_rollback_old_identity_drift")
    rows = {str(row["service"]): row for row in raw_rows}
    if len(rows) != len(raw_rows):
        raise UpgradeError("upgrade_rollback_old_identity_drift")
    if set(rows) != set(ROLE_SERVICES[role]):
        raise UpgradeError("upgrade_rollback_old_identity_drift")
    for service in ROLE_SERVICES[role]:
        expected = rows[service]
        if _ids(str(payload["old_project"]), service) != [expected["container_id"]]:
            raise UpgradeError("upgrade_rollback_old_identity_drift")
        current = _identity(
            str(expected["container_id"]),
            project=str(payload["old_project"]),
            service=service,
        )
        expected_running = service != "market-migration"
        if (
            current["image_id"] != expected["image_id"]
            or current["release_sha"] != expected["release_sha"]
            or current["restart_name"] != expected["restart_name"]
            or current["restart_maximum_retry_count"]
            != expected["restart_maximum_retry_count"]
            or current["running"] is not expected_running
            or (
                expected_running
                and current["health"] != "healthy"
            )
        ):
            raise UpgradeError("upgrade_rollback_terminal_state_drift")
    if role != "web":
        return
    if (
        not _is_legacy_journal(payload)
        and isinstance(payload.get("marker_transition"), dict)
        and payload["marker_transition"].get("status") != "NOT_STARTED"
    ):
        _verify_terminal_legacy_binding(
            payload=payload,
            journal=journal,
            descriptor=descriptor,
            live_lock=live_lock,
        )
    if _is_legacy_journal(payload):
        entries = _legacy_marker_rows(payload)
    else:
        transition = payload.get("marker_transition")
        if not isinstance(transition, dict):
            raise UpgradeError("upgrade_capture_marker_transition_invalid")
        if transition.get("status") == "NOT_STARTED":
            entries = _legacy_marker_rows(payload)
        else:
            transition = _validate_marker_transition(payload, allow_rollback=True)
            if (
                transition.get("rollback_status") != "COMPLETE"
                or any(
                    row.get("rollback_status") != "RESTORED"
                    for row in transition["entries"].values()
                )
            ):
                raise UpgradeError("upgrade_rollback_terminal_state_drift")
            entries = transition["entries"]
    for marker_role, row in entries.items():
        path = Path(str(row["path"]))
        if _sha256(path) != row["prior_sha256"]:
            raise UpgradeError("upgrade_rollback_terminal_state_drift")
        _load_marker(
            path,
            role=marker_role,
            release_sha=str(row["prior_payload"]["release_sha"]),
        )


def rollback(*, journal: Path, role: str, release_sha: str) -> dict[str, Any]:
    payload = _read_journal(journal)
    _validate_journal(
        payload,
        role=role,
        release_sha=release_sha,
        validate_release_root=False,
    )
    if payload["status"] == "ROLLED_BACK":
        _verify_rolled_back_runtime(payload, role=role, journal=journal)
        return payload
    transition = payload.get("marker_transition")
    authority_started = (
        not _is_legacy_journal(payload)
        and role == "web"
        and isinstance(transition, dict)
        and transition.get("status") != "NOT_STARTED"
    )
    if authority_started:
        receipt_path = Path(str(payload.get("legacy_collector_receipt") or ""))
        lock_path = Path(str(payload.get("maintenance_lock_path") or ""))
        try:
            receipt = legacy_handoff._read(
                receipt_path, release_sha=release_sha, host_role="web"
            )
        except legacy_handoff.CollectorHandoffError as exc:
            raise UpgradeError("upgrade_legacy_collector_receipt_invalid") from exc
        expected_lock = receipt.get("maintenance_lock")
        if not isinstance(expected_lock, dict):
            raise UpgradeError("upgrade_maintenance_lock_invalid")
        with _maintenance_inode_guard(
            lock_path, release_sha=release_sha, expected_lock=expected_lock
        ) as (descriptor, live_lock):
            payload = _read_journal(journal)
            _validate_journal(
                payload, role=role, release_sha=release_sha,
                validate_release_root=False,
            )
            return _rollback_locked(
                journal=journal, role=role, release_sha=release_sha,
                payload=payload, descriptor=descriptor, live_lock=live_lock,
            )
    return _rollback_locked(
        journal=journal, role=role, release_sha=release_sha, payload=payload
    )


def _rollback_locked(
    *, journal: Path, role: str, release_sha: str,
    payload: dict[str, Any], descriptor: int | None = None,
    live_lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Resolve every target and prove every immutable identity before the first
    # stop/remove/start operation.  A malformed journal cannot cause a partial
    # destructive rollback.
    authority_mode = "NONE"
    if (
        role == "web"
        and not _is_legacy_journal(payload)
        and isinstance(payload.get("marker_transition"), dict)
        and payload["marker_transition"].get("status") != "NOT_STARTED"
    ):
        if descriptor is None or live_lock is None:
            raise UpgradeError("upgrade_maintenance_lock_invalid")
        authority_mode = _rollback_authority_preflight(
            payload=payload,
            journal=journal,
            release_sha=release_sha,
            descriptor=descriptor,
            live_lock=live_lock,
        )
    _rollback_preflight(payload, role=role, authority_mode=authority_mode)
    old_rows = {row["service"]: row for row in payload["services"]}
    for service in reversed(ROLE_SERVICES[role]):
        for container_id in _ids(payload["new_project"], service):
            row = _identity(container_id, project=payload["new_project"], service=service)
            if row["running"]:
                _run(["docker", "update", "--restart=no", container_id], label="upgrade_rollback_restart_disable")
                _run(["docker", "stop", "-t", "60", container_id], label="upgrade_rollback_stop")
            _run(["docker", "rm", container_id], label="upgrade_rollback_remove")
    if role == "web":
        if _is_legacy_journal(payload):
            for marker_role, row in _legacy_marker_rows(payload).items():
                path = Path(str(row["path"]))
                if _sha256(path) != row["prior_sha256"]:
                    _write_marker(path, row["prior_payload"])
                _load_marker(
                    path,
                    role=marker_role,
                    release_sha=str(row["prior_payload"]["release_sha"]),
                )
        else:
            marker_authority_sha256 = None
            if payload["marker_transition"].get("status") != "NOT_STARTED":
                if descriptor is None or live_lock is None:
                    raise UpgradeError("upgrade_maintenance_lock_invalid")
                marker_authority_sha256 = _marker_authority_digest(payload)
            _restore_markers(journal=journal, payload=payload)
            if (
                marker_authority_sha256 is not None
                and authority_mode == "TRANSFER_STARTED"
            ):
                receipt_path = Path(str(payload["legacy_collector_receipt"]))
                try:
                    restored = (
                        legacy_handoff.mark_capture_authority_restored_with_held_lock(
                            descriptor=descriptor,
                            journal=receipt_path,
                            release_sha=release_sha,
                            host_role="web",
                            expected_lock=live_lock,
                            bluegreen_journal=journal,
                            marker_authority_sha256=marker_authority_sha256,
                        )
                    )
                except legacy_handoff.CollectorHandoffError as exc:
                    raise UpgradeError(
                        "upgrade_legacy_authority_restore_failed"
                    ) from exc
                payload["legacy_collector_receipt_sha256"] = _sha256(receipt_path)
                payload["legacy_authority_transfer"] = restored.get(
                    "authority_transfer"
                )
                _atomic_json(journal, payload)
            elif authority_mode == "ALREADY_RESTORED":
                receipt_path = Path(str(payload["legacy_collector_receipt"]))
                restored = legacy_handoff._read(
                    receipt_path, release_sha=release_sha, host_role="web"
                )
                payload["legacy_collector_receipt_sha256"] = _sha256(
                    receipt_path
                )
                payload["legacy_authority_transfer"] = restored.get(
                    "authority_transfer"
                )
                _atomic_json(journal, payload)
    for service in RESTORE_ORDER[role]:
        row = old_rows[service]
        if _ids(payload["old_project"], service) != [row["container_id"]]:
            raise UpgradeError("upgrade_rollback_old_identity_drift")
        if not _identity(row["container_id"], project=payload["old_project"], service=service)["running"]:
            _run(["docker", "start", row["container_id"]], label="upgrade_rollback_start")
        _run(
            ["docker", "update", f"--restart={_restart_argument(row)}", row["container_id"]],
            label="upgrade_rollback_restart_restore",
        )
        for _attempt in range(90):
            current = _identity(row["container_id"], project=payload["old_project"], service=service)
            if current["running"] and current["health"] == "healthy":
                break
            time.sleep(1)
        else:
            raise UpgradeError("upgrade_rollback_health_timeout")
    if _project_services(payload["new_project"]):
        raise UpgradeError("upgrade_rollback_new_project_remaining")
    payload["status"] = "ROLLED_BACK"
    _atomic_json(journal, payload)
    _verify_rolled_back_runtime(
        payload,
        role=role,
        journal=journal,
        descriptor=descriptor,
        live_lock=live_lock,
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "plan", "quiesce-workload", "quiesce-database",
            "prepare-capture-authority", "authorize-captures",
            "start-captures", "verify", "rollback",
        ),
    )
    parser.add_argument("--role", choices=("bot", "web"), required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--old-env", type=Path)
    parser.add_argument("--new-env", type=Path)
    parser.add_argument("--old-project")
    parser.add_argument("--new-project")
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--backup-receipt", type=Path)
    parser.add_argument("--expected-backup-receipt-sha256")
    parser.add_argument("--offhost-backup-receipt", type=Path)
    parser.add_argument("--expected-offhost-backup-receipt-sha256")
    parser.add_argument("--release-tree")
    parser.add_argument("--image-id")
    parser.add_argument("--image-input-signature")
    parser.add_argument("--backup-maximum-age-seconds", type=int, default=3600)
    parser.add_argument("--web-legacy-collector-receipt", type=Path)
    parser.add_argument("--expected-web-legacy-collector-receipt-sha256")
    parser.add_argument(
        "--web-maintenance-lock-path",
        type=Path,
        default=DEFAULT_MAINTENANCE_LOCK,
    )
    parser.add_argument("--bot-legacy-collector-receipt", type=Path)
    parser.add_argument("--expected-bot-legacy-collector-receipt-sha256")
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION or not HEX40.fullmatch(args.release_sha):
            raise UpgradeError("upgrade_invocation_invalid")
        common = {"journal": args.journal, "role": args.role, "release_sha": args.release_sha}
        if args.command == "plan":
            if not all(
                (
                    args.old_env,
                    args.new_env,
                    args.old_project,
                    args.new_project,
                    args.release_root,
                    args.release_tree,
                )
            ):
                raise UpgradeError("upgrade_plan_arguments_required")
            result = plan(
                old_env=args.old_env, new_env=args.new_env,
                old_project=args.old_project, new_project=args.new_project,
                release_root=args.release_root, release_tree=args.release_tree,
                **common,
            )
        elif args.command == "quiesce-workload":
            result = quiesce_workload(**common)
        elif args.command == "quiesce-database":
            if not all(
                (
                    args.backup_receipt,
                    args.expected_backup_receipt_sha256,
                    args.offhost_backup_receipt,
                    args.expected_offhost_backup_receipt_sha256,
                    args.release_tree,
                    args.image_id,
                    args.image_input_signature,
                )
            ):
                raise UpgradeError("upgrade_backup_arguments_required")
            result = quiesce_database(
                backup_receipt=args.backup_receipt,
                expected_backup_receipt_sha256=args.expected_backup_receipt_sha256,
                offhost_backup_receipt=args.offhost_backup_receipt,
                expected_offhost_backup_receipt_sha256=(
                    args.expected_offhost_backup_receipt_sha256
                ),
                release_tree=args.release_tree,
                image_id=args.image_id,
                image_input_signature=args.image_input_signature,
                backup_maximum_age_seconds=args.backup_maximum_age_seconds,
                **common,
            )
        elif args.command == "prepare-capture-authority":
            if (
                not args.web_legacy_collector_receipt
                or not args.expected_web_legacy_collector_receipt_sha256
            ):
                raise UpgradeError("upgrade_legacy_collector_receipt_required")
            result = prepare_capture_authority(
                web_legacy_collector_receipt=(
                    args.web_legacy_collector_receipt
                ),
                expected_web_legacy_collector_receipt_sha256=(
                    args.expected_web_legacy_collector_receipt_sha256
                ),
                web_maintenance_lock_path=args.web_maintenance_lock_path,
                **common,
            )
        elif args.command == "authorize-captures":
            if (
                not args.web_legacy_collector_receipt
                or not args.expected_web_legacy_collector_receipt_sha256
                or not args.bot_legacy_collector_receipt
                or not args.expected_bot_legacy_collector_receipt_sha256
            ):
                raise UpgradeError("upgrade_legacy_collector_receipt_required")
            result = authorize_captures(
                web_legacy_collector_receipt=(
                    args.web_legacy_collector_receipt
                ),
                expected_web_legacy_collector_receipt_sha256=(
                    args.expected_web_legacy_collector_receipt_sha256
                ),
                web_maintenance_lock_path=args.web_maintenance_lock_path,
                bot_legacy_collector_receipt=(
                    args.bot_legacy_collector_receipt
                ),
                expected_bot_legacy_collector_receipt_sha256=(
                    args.expected_bot_legacy_collector_receipt_sha256
                ),
                **common,
            )
        elif args.command == "start-captures":
            if not args.release_root:
                raise UpgradeError("upgrade_release_root_required")
            result = start_captures(release_root=args.release_root, **common)
        elif args.command == "verify":
            result = verify(**common)
        else:
            result = rollback(**common)
        output = {
            "status": result["status"],
            "role": result["role"],
            "release_sha": result["release_sha"],
            "product_authority_changed": False,
            "state_deleted": False,
            "secrets_disclosed": False,
        }
        if args.command == "prepare-capture-authority":
            output["prepared_bluegreen_journal_sha256"] = _sha256(
                args.journal
            )
            output["marker_authority_sha256"] = _marker_authority_digest(
                result
            )
        print(json.dumps(output, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, UpgradeError) as exc:
        print(
            json.dumps(
                {"status": "FAIL", "reason_code": str(exc), "secrets_disclosed": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
