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
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.migrate_market_pipeline_archive import POSTGRES_IMAGE
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.migrate_market_pipeline_archive import POSTGRES_IMAGE


CONFIRMATION = "upgrade-market-pipeline-bluegreen"
SCHEMA = "market_pipeline_bluegreen_upgrade/1.0"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
AUTHORITY_CONTRACT = "market_capture_authority/1.0"

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
    _secure_file(path)
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
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


def _sha256(path: Path) -> str:
    from hashlib import sha256

    return sha256(path.read_bytes()).hexdigest()


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
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_journal(path: Path) -> dict[str, Any]:
    _secure_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpgradeError("upgrade_journal_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise UpgradeError("upgrade_journal_invalid")
    return payload


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
        or new_values.get("MARKET_PIPELINE_FEED_MODE") != "PRIVATE_PRIMARY"
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
    try:
        info = path.lstat()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpgradeError("upgrade_capture_marker_invalid") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 10001
        or info.st_gid != 10001
        or stat.S_IMODE(info.st_mode) != 0o600
        or not isinstance(payload, dict)
        or payload.get("contract") != AUTHORITY_CONTRACT
        or payload.get("authority") != "container"
        or payload.get("role") != role
        or (release_sha is not None and payload.get("release_sha") != release_sha)
    ):
        raise UpgradeError("upgrade_capture_marker_invalid")
    return payload


def _write_marker(path: Path, payload: Mapping[str, Any]) -> None:
    parent_info = path.parent.lstat()
    current_info = path.lstat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != 10001
        or path.is_symlink()
        or not stat.S_ISREG(current_info.st_mode)
        or current_info.st_uid != 10001
        or current_info.st_gid != 10001
        or stat.S_IMODE(current_info.st_mode) != 0o600
        or current_info.st_nlink != 1
    ):
        raise UpgradeError("upgrade_capture_marker_path_invalid")
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 10001, 10001)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def plan(
    *, role: str, old_env: Path, new_env: Path, journal: Path,
    release_sha: str, old_project: str, new_project: str,
) -> dict[str, Any]:
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
        "status": "planned",
        "role": role,
        "release_sha": release_sha,
        "old_project": old_project,
        "new_project": new_project,
        "old_env": str(old_env),
        "new_env": str(new_env),
        "old_env_sha256": _sha256(old_env),
        "new_env_sha256": _sha256(new_env),
        "new_image_id": new_values["MARKET_PIPELINE_IMAGE"],
        "services": rows,
        "markers": markers,
        "backup_receipt_sha256": None,
        "new_capture_ids": {},
        "product_authority_changed": False,
        "state_deleted": False,
        "secrets_disclosed": False,
    }
    _atomic_json(journal, payload, exclusive=True)
    return payload


def _validate_journal(payload: Mapping[str, Any], *, role: str, release_sha: str) -> None:
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
        path = Path(str(payload.get(key) or ""))
        _secure_file(path)
        if _sha256(path) != payload.get(f"{key}_sha256"):
            raise UpgradeError("upgrade_env_drift")


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


def quiesce_database(
    *, journal: Path, role: str, release_sha: str,
    backup_receipt: Path, expected_backup_receipt_sha256: str,
) -> dict[str, Any]:
    if role != "web" or not HEX64.fullmatch(expected_backup_receipt_sha256):
        raise UpgradeError("upgrade_database_invocation_invalid")
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
    if payload["status"] not in {"workload_quiesced", "database_quiesced"}:
        raise UpgradeError("upgrade_database_state_invalid")
    _secure_file(backup_receipt)
    if _sha256(backup_receipt) != expected_backup_receipt_sha256:
        raise UpgradeError("upgrade_backup_receipt_digest_mismatch")
    try:
        receipt = json.loads(backup_receipt.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpgradeError("upgrade_backup_receipt_invalid") from exc
    restore_smoke = receipt.get("restore_smoke")
    source = receipt.get("source")
    if (
        receipt.get("status") != "PASS"
        or not isinstance(source, dict)
        or not isinstance(restore_smoke, dict)
        or restore_smoke.get("status") != "PASS"
        or restore_smoke.get("cleanup_status") != "PASS"
        or any(
            restore_smoke.get(key) != source.get(key)
            for key in ("schema_versions", "table_count", "fact_count")
        )
    ):
        raise UpgradeError("upgrade_backup_receipt_not_pass")
    rows = {row["service"]: row for row in payload["services"]}
    database = rows["market-database"]
    if any(_ids(payload["old_project"], service, running=True) for service in QUIESCE_ORDER[role]):
        raise UpgradeError("upgrade_old_workload_still_running")
    if _identity(database["container_id"], project=payload["old_project"], service="market-database")["running"]:
        _run(["docker", "update", "--restart=no", database["container_id"]], label="upgrade_database_restart_disable")
        _run(["docker", "stop", "-t", "60", database["container_id"]], label="upgrade_database_stop")
    if _identity(database["container_id"], project=payload["old_project"], service="market-database")["running"]:
        raise UpgradeError("upgrade_old_database_stop_failed")
    payload["backup_receipt_sha256"] = expected_backup_receipt_sha256
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
    new_values = _parse_env(Path(str(payload["new_env"])))
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


def authorize_captures(*, journal: Path, role: str, release_sha: str) -> dict[str, Any]:
    if role != "web":
        raise UpgradeError("upgrade_capture_role_invalid")
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
    if payload["status"] not in {"database_quiesced", "captures_authorized"}:
        raise UpgradeError("upgrade_capture_authorize_state_invalid")
    if any(_ids(payload["old_project"], service, running=True) for service in ROLE_SERVICES["web"]):
        raise UpgradeError("upgrade_old_owner_still_running")
    for service in WEB_PRIMARY_BASE:
        if service == "market-database":
            _new_database_identity(payload)
        else:
            _new_identity(payload, service)
    if any(_ids(payload["new_project"], service) for service in CAPTURE_SERVICES):
        raise UpgradeError("upgrade_new_capture_already_exists")
    if payload["status"] != "captures_authorized":
        for marker_role, row in payload["markers"].items():
            path = Path(row["path"])
            if _sha256(path) != row["sha256"]:
                raise UpgradeError("upgrade_capture_marker_drift")
            _write_marker(
                path,
                {
                    "contract": AUTHORITY_CONTRACT,
                    "authority": "container",
                    "role": marker_role,
                    "release_sha": release_sha,
                    "authorized_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            _load_marker(path, role=marker_role, release_sha=release_sha)
        payload["status"] = "captures_authorized"
        _atomic_json(journal, payload)
    return payload


def _compose(release_root: Path, env_file: Path) -> list[str]:
    return [
        "docker", "compose", "--env-file", str(env_file),
        "-f", str(release_root / "deploy/market-data/compose.yml"),
        "-f", str(release_root / "deploy/market-data/compose.web.yml"),
        "--profile", "web",
    ]


def start_captures(
    *, journal: Path, role: str, release_sha: str, release_root: Path,
) -> dict[str, Any]:
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
    if role != "web" or payload["status"] not in {"captures_authorized", "captures_running"}:
        raise UpgradeError("upgrade_capture_start_state_invalid")
    env_file = Path(payload["new_env"])
    for service in CAPTURE_SERVICES:
        existing = _ids(payload["new_project"], service)
        if not existing:
            _run(
                [*_compose(release_root, env_file), "up", "-d", "--no-deps", "--no-recreate", service],
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
    payload["status"] = "captures_running"
    _atomic_json(journal, payload)
    return payload


def verify(*, journal: Path, role: str, release_sha: str) -> dict[str, Any]:
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
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
        for marker_role, row in payload["markers"].items():
            _load_marker(Path(row["path"]), role=marker_role, release_sha=release_sha)
    payload["status"] = "PASS"
    _atomic_json(journal, payload)
    return payload


def _restart_argument(row: Mapping[str, Any]) -> str:
    name = str(row.get("restart_name") or "no")
    maximum = int(row.get("restart_maximum_retry_count") or 0)
    return f"{name}:{maximum}" if name == "on-failure" and maximum > 0 else name


def rollback(*, journal: Path, role: str, release_sha: str) -> dict[str, Any]:
    payload = _read_journal(journal)
    _validate_journal(payload, role=role, release_sha=release_sha)
    if payload["status"] == "ROLLED_BACK":
        return payload
    old_rows = {row["service"]: row for row in payload["services"]}
    for service in reversed(ROLE_SERVICES[role]):
        for container_id in _ids(payload["new_project"], service):
            row = _identity(container_id, project=payload["new_project"], service=service)
            if row["running"]:
                _run(["docker", "update", "--restart=no", container_id], label="upgrade_rollback_restart_disable")
                _run(["docker", "stop", "-t", "60", container_id], label="upgrade_rollback_stop")
            _run(["docker", "rm", container_id], label="upgrade_rollback_remove")
    if role == "web":
        for marker_role, row in payload["markers"].items():
            _write_marker(Path(row["path"]), row["payload"])
            _load_marker(
                Path(row["path"]), role=marker_role,
                release_sha=str(row["payload"]["release_sha"]),
            )
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
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "plan", "quiesce-workload", "quiesce-database",
            "authorize-captures", "start-captures", "verify", "rollback",
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
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION or not HEX40.fullmatch(args.release_sha):
            raise UpgradeError("upgrade_invocation_invalid")
        common = {"journal": args.journal, "role": args.role, "release_sha": args.release_sha}
        if args.command == "plan":
            if not all((args.old_env, args.new_env, args.old_project, args.new_project)):
                raise UpgradeError("upgrade_plan_arguments_required")
            result = plan(
                old_env=args.old_env, new_env=args.new_env,
                old_project=args.old_project, new_project=args.new_project, **common,
            )
        elif args.command == "quiesce-workload":
            result = quiesce_workload(**common)
        elif args.command == "quiesce-database":
            if not args.backup_receipt or not args.expected_backup_receipt_sha256:
                raise UpgradeError("upgrade_backup_arguments_required")
            result = quiesce_database(
                backup_receipt=args.backup_receipt,
                expected_backup_receipt_sha256=args.expected_backup_receipt_sha256,
                **common,
            )
        elif args.command == "authorize-captures":
            result = authorize_captures(**common)
        elif args.command == "start-captures":
            if not args.release_root:
                raise UpgradeError("upgrade_release_root_required")
            result = start_captures(release_root=args.release_root, **common)
        elif args.command == "verify":
            result = verify(**common)
        else:
            result = rollback(**common)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "role": result["role"],
                    "release_sha": result["release_sha"],
                    "product_authority_changed": False,
                    "state_deleted": False,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
            )
        )
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
