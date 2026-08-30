#!/usr/bin/env python3
"""Run the release-bound Market Pipeline archive migration exactly twice."""

from __future__ import annotations

import argparse
from hashlib import sha256
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
    from scripts import backup_market_pipeline_archive as backup
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import backup_market_pipeline_archive as backup


CONFIRMATION = "run-production-market-pipeline-archive-migration"
RESULT_SCHEMA = "market_pipeline_migration_receipt/1.0"
JOURNAL_SCHEMA = "market_pipeline_migration_journal/1.0"
POSTGRES_IMAGE = backup.POSTGRES_IMAGE
MARKET_SCHEMA_VERSION = 3
MARKET_SCHEMA_TABLE_COUNT = 28
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MARKET_SCHEMA_VERSIONS = list(range(1, MARKET_SCHEMA_VERSION + 1))
MARKET_SCHEMA_VERSIONS_TEXT = ",".join(str(item) for item in MARKET_SCHEMA_VERSIONS)


class MigrationError(RuntimeError):
    """A stable, content-free migration refusal."""


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _secure_parent(path: Path) -> None:
    info = path.parent.lstat()
    if (
        not path.parent.is_absolute()
        or path.parent.is_symlink()
        or not path.parent.is_dir()
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise MigrationError("migration_state_parent_invalid")


def _secure_file(path: Path) -> None:
    info = path.lstat()
    if (
        path.is_symlink()
        or not path.is_file()
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise MigrationError("migration_state_file_invalid")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _secure_parent(path)
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(
        candidate,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    _secure_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("migration_state_invalid") from exc
    if not isinstance(payload, dict):
        raise MigrationError("migration_state_invalid")
    return payload


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
        raise MigrationError(f"{label}_failed_rc_{result.returncode}")
    return result


def _text(arguments: Sequence[str], *, label: str) -> str:
    return _run(arguments, label=label).stdout.strip()


def _compose(root: Path, env_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(root / "deploy/market-data/compose.yml"),
        "-f",
        str(root / "deploy/market-data/compose.web.yml"),
        "--profile",
        "web",
    ]


def _validate_primary_target_env(
    env_file: Path, *, release_sha: str, image_id: str
) -> dict[str, str]:
    values = backup.parse_env(env_file, secure_input=True)
    source_values = {
        key: value for key, value in values.items() if key not in backup.DYNAMIC_VALUES
    }
    backup.validate_source("web", source_values)
    expected = {
        "MARKET_PIPELINE_RELEASE_SHA": release_sha,
        "MARKET_PIPELINE_IMAGE": image_id,
        "MARKET_PIPELINE_MODE": "live",
        "MARKET_PIPELINE_FEED_MODE": "PRIVATE_PRIMARY",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "1",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_PRIMARY",
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise MigrationError("migration_target_env_identity_mismatch")
    project = values.get("MARKET_PIPELINE_PROJECT_NAME", "")
    user = values.get("MARKET_POSTGRES_USER", "market_data")
    database = values.get("MARKET_POSTGRES_DB", "market_archive")
    if not backup.PROJECT_NAME.fullmatch(project):
        raise MigrationError("migration_target_project_invalid")
    if not backup.SAFE_SQL_ID.fullmatch(user) or not backup.SAFE_SQL_ID.fullmatch(database):
        raise MigrationError("migration_target_database_identity_invalid")
    return values


def _container_ids(project: str) -> list[str]:
    output = _text(
        [
            "docker",
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.service=market-database",
        ],
        label="migration_database_inventory",
    )
    return [line for line in output.splitlines() if line]


def _inspect(container_id: str) -> Mapping[str, Any]:
    try:
        documents = json.loads(
            _text(["docker", "inspect", container_id], label="migration_database_inspect")
        )
        document = documents[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise MigrationError("migration_database_inspect_invalid") from exc
    if not isinstance(document, dict):
        raise MigrationError("migration_database_inspect_invalid")
    return document


def _running_services(project: str) -> list[str]:
    output = _text(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            '{{.Label "com.docker.compose.service"}}',
        ],
        label="migration_running_services",
    )
    return sorted({line for line in output.splitlines() if line})


def _database_identity(
    document: Mapping[str, Any], *, project: str, postgres_root: Path
) -> dict[str, Any]:
    labels = document.get("Config", {}).get("Labels", {}) or {}
    mounts = [
        mount
        for mount in document.get("Mounts", [])
        if mount.get("Destination") == "/var/lib/postgresql/data"
    ]
    state = document.get("State", {}) or {}
    health = (state.get("Health", {}) or {}).get("Status")
    if (
        document.get("Config", {}).get("Image") != POSTGRES_IMAGE
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != "market-database"
        or len(mounts) != 1
        or Path(str(mounts[0].get("Source") or "")) != postgres_root
    ):
        raise MigrationError("migration_database_runtime_identity_mismatch")
    container_id = str(document.get("Id") or "")
    if not HEX64.fullmatch(container_id):
        raise MigrationError("migration_database_container_id_invalid")
    return {
        "container_id": container_id,
        "running": state.get("Running") is True,
        "healthy": health == "healthy",
        "image": POSTGRES_IMAGE,
    }


def _migration_result(output: str, *, second: bool) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise MigrationError("migration_pass_output_missing")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise MigrationError("migration_pass_output_invalid") from exc
    expected_status = {"already_current"} if second else {"applied", "already_current"}
    if (
        set(payload) != {"status", "version", "table_count"}
        or payload.get("status") not in expected_status
        or payload.get("version") != MARKET_SCHEMA_VERSION
        or payload.get("table_count") != MARKET_SCHEMA_TABLE_COUNT
    ):
        raise MigrationError("migration_pass_contract_invalid")
    return payload


def validate_receipt(
    payload: Mapping[str, Any],
    *,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    offhost_receipt_sha256: str,
    host_preflight_receipt_sha256: str,
    source_backup_receipt_sha256: str,
    web_role_env_sha256: str,
) -> None:
    expected_keys = {
        "schema", "status", "release_sha", "release_tree", "image_id",
        "image_input_signature", "offhost_backup_receipt_sha256",
        "host_preflight_receipt_sha256", "source_backup_receipt_sha256",
        "web_role_env_sha256", "backup_status", "before", "after",
        "first_pass", "second_pass", "schema_versions", "table_count",
        "fact_count", "database_mutated", "database_container_created",
        "running_services", "private_shadow_only", "product_authority_changed",
        "telegram_capture_cutover_authorized", "secrets_disclosed",
    }
    identity = {
        "schema": RESULT_SCHEMA,
        "status": "PASS",
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        "offhost_backup_receipt_sha256": offhost_receipt_sha256,
        "host_preflight_receipt_sha256": host_preflight_receipt_sha256,
        "source_backup_receipt_sha256": source_backup_receipt_sha256,
        "web_role_env_sha256": web_role_env_sha256,
        "schema_versions": MARKET_SCHEMA_VERSIONS,
        "table_count": MARKET_SCHEMA_TABLE_COUNT,
        "running_services": ["market-database"],
        "product_authority_changed": False,
        "telegram_capture_cutover_authorized": False,
        "secrets_disclosed": False,
    }
    if set(payload) != expected_keys or any(payload.get(k) != v for k, v in identity.items()):
        raise MigrationError("migration_receipt_identity_invalid")
    before, after = payload.get("before"), payload.get("after")
    first, second = payload.get("first_pass"), payload.get("second_pass")
    if (
        payload.get("backup_status") not in {"PASS", "INITIAL_EMPTY"}
        or not isinstance(payload.get("fact_count"), int)
        or payload["fact_count"] < 0
        or not isinstance(before, dict)
        or set(before) != {"container_id", "running"}
        or not isinstance(after, dict)
        or set(after) != {"container_id", "running", "healthy", "image"}
        or not HEX64.fullmatch(str(after.get("container_id") or ""))
        or after.get("running") is not True
        or after.get("healthy") is not True
        or after.get("image") != POSTGRES_IMAGE
        or not isinstance(first, dict)
        or set(first) != {"status", "version", "table_count"}
        or first.get("status") not in {"applied", "already_current"}
        or first.get("version") != MARKET_SCHEMA_VERSION
        or first.get("table_count") != MARKET_SCHEMA_TABLE_COUNT
        or second
        != {
            "status": "already_current",
            "version": MARKET_SCHEMA_VERSION,
            "table_count": MARKET_SCHEMA_TABLE_COUNT,
        }
    ):
        raise MigrationError("migration_receipt_contract_invalid")
    created = payload.get("database_container_created")
    if payload["backup_status"] == "PASS":
        source_valid = (
            (
                created is False
                and before.get("running") is True
                and before.get("container_id") == after.get("container_id")
            )
            or (
                created is True
                and before.get("running") is False
                and isinstance(before.get("container_id"), str)
                and HEX64.fullmatch(before["container_id"])
                and before.get("container_id") != after.get("container_id")
            )
        )
    else:
        source_valid = created is True and before == {
            "container_id": None,
            "running": False,
        }
    bluegreen_transition = (
        payload["backup_status"] == "PASS"
        and created is True
        and before.get("running") is False
        and isinstance(before.get("container_id"), str)
    )
    if (
        not source_valid
        or payload.get("private_shadow_only") is not (not bluegreen_transition)
        or payload.get("database_mutated")
        is not (created is True or first["status"] == "applied")
    ):
        raise MigrationError("migration_receipt_transition_invalid")


def _journal_identity(
    *,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    offhost_receipt_sha256: str,
    host_preflight_receipt_sha256: str,
    source_backup_receipt_sha256: str,
    web_role_env_sha256: str,
    backup_status: str,
    before: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": JOURNAL_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        "offhost_backup_receipt_sha256": offhost_receipt_sha256,
        "host_preflight_receipt_sha256": host_preflight_receipt_sha256,
        "source_backup_receipt_sha256": source_backup_receipt_sha256,
        "web_role_env_sha256": web_role_env_sha256,
        "backup_status": backup_status,
        "before": dict(before),
        "product_authority_changed": False,
        "telegram_capture_cutover_authorized": False,
        "secrets_disclosed": False,
    }


def _validate_journal(
    payload: Mapping[str, Any], *, expected: Mapping[str, Any], receipt_path: Path
) -> None:
    status = payload.get("status")
    if status not in {"PREPARED", "APPLYING", "COMPLETE"} or payload.get(
        "receipt_path"
    ) != str(receipt_path):
        raise MigrationError("migration_journal_state_invalid")
    for key, value in expected.items():
        if payload.get(key) != value:
            raise MigrationError("migration_journal_identity_mismatch")
    allowed = set(expected) | {"status", "receipt_path", "receipt_sha256"}
    if set(payload) != allowed:
        raise MigrationError("migration_journal_schema_invalid")
    if status == "COMPLETE":
        if not HEX64.fullmatch(str(payload.get("receipt_sha256") or "")):
            raise MigrationError("migration_journal_state_invalid")
    elif payload.get("receipt_sha256") is not None:
        raise MigrationError("migration_journal_state_invalid")


def _write_remote_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_json(path, payload)
    _secure_file(path)


def _query(container_id: str, user: str, database: str, sql: str) -> str:
    return _text(
        [
            "docker",
            "exec",
            container_id,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-U",
            user,
            "-d",
            database,
            "-c",
            sql,
        ],
        label="migration_database_query",
    )


def _verify_completed_live_state(
    payload: Mapping[str, Any], *, values: Mapping[str, str], project: str,
    postgres_root: Path,
) -> None:
    after = payload.get("after")
    if not isinstance(after, dict):
        raise MigrationError("migration_recovery_receipt_invalid")
    container_id = str(after.get("container_id") or "")
    if _container_ids(project) != [container_id]:
        raise MigrationError("migration_recovery_database_drift")
    identity = _database_identity(
        _inspect(container_id), project=project, postgres_root=postgres_root
    )
    if identity != after or _running_services(project) != ["market-database"]:
        raise MigrationError("migration_recovery_database_drift")
    user = values.get("MARKET_POSTGRES_USER", "market_data")
    database = values.get("MARKET_POSTGRES_DB", "market_archive")
    versions = _query(
        container_id, user, database,
        "SELECT string_agg(version::text, ',' ORDER BY version) "
        "FROM market_data.schema_migrations",
    )
    facts = int(
        _query(container_id, user, database, "SELECT count(*) FROM market_data.market_facts")
    )
    if versions != MARKET_SCHEMA_VERSIONS_TEXT or facts < int(payload.get("fact_count", -1)):
        raise MigrationError("migration_recovery_database_drift")


def run_migration(
    *,
    release_root: Path,
    env_file: Path,
    backup_env_file: Path | None = None,
    backup_receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    offhost_receipt_sha256: str,
    host_preflight_receipt_sha256: str,
    backup_maximum_age_seconds: int,
    journal_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    bluegreen = backup_env_file is not None
    if bluegreen:
        values = _validate_primary_target_env(
            env_file, release_sha=release_sha, image_id=image_id
        )
        source_values = backup.validate_release_env(
            backup_env_file,
            release_sha=release_sha,
            image_id=image_id,
            allow_target_identity_mismatch=True,
        )
        if (
            source_values["MARKET_PIPELINE_PROJECT_NAME"]
            == values["MARKET_PIPELINE_PROJECT_NAME"]
            or source_values["MARKET_WEB_DATA_ROOT"] != values["MARKET_WEB_DATA_ROOT"]
            or source_values.get("MARKET_POSTGRES_USER", "market_data")
            != values.get("MARKET_POSTGRES_USER", "market_data")
            or source_values.get("MARKET_POSTGRES_DB", "market_archive")
            != values.get("MARKET_POSTGRES_DB", "market_archive")
        ):
            raise MigrationError("migration_bluegreen_topology_invalid")
    else:
        source_values = values = backup.validate_release_env(
            env_file, release_sha=release_sha, image_id=image_id
        )
    _secure_parent(journal_path)
    _secure_parent(receipt_path)
    if journal_path.parent != receipt_path.parent or journal_path == receipt_path:
        raise MigrationError("migration_state_destination_invalid")
    existing_journal = (
        _read_json(journal_path)
        if journal_path.exists() or journal_path.is_symlink()
        else None
    )
    receipt_env = backup_env_file or env_file
    backup_document = backup.verify_receipt(
        env_file=receipt_env,
        receipt=backup_receipt,
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=image_id,
        image_input_signature=image_input_signature,
        allow_target_identity_mismatch=bluegreen,
        # Freshness is a pre-PREPARED admission gate.  Once the exact journal
        # exists, retries after a kill or lost SSH session must continue from
        # the immutable bound backup even if wall-clock time crossed the
        # ordinary freshness window.
        maximum_age_seconds=(
            None if existing_journal is not None else backup_maximum_age_seconds
        ),
    )
    if not HEX64.fullmatch(offhost_receipt_sha256) or not HEX64.fullmatch(
        host_preflight_receipt_sha256
    ):
        raise MigrationError("migration_prerequisite_receipt_digest_invalid")
    source_backup_receipt_sha256 = backup.file_digest(backup_receipt)
    web_role_env_sha256 = backup.file_digest(env_file)
    project = values["MARKET_PIPELINE_PROJECT_NAME"]
    source_project = source_values["MARKET_PIPELINE_PROJECT_NAME"]
    postgres_root = Path(values["MARKET_WEB_DATA_ROOT"]) / "postgres"
    status = backup_document["status"]
    if existing_journal is not None:
        before = existing_journal.get("before")
        if not isinstance(before, dict):
            raise MigrationError("migration_journal_state_invalid")
    else:
        before_ids = _container_ids(source_project)
        target_before_ids = _container_ids(project) if bluegreen else before_ids
        if status == "PASS":
            expected_id = str(backup_document["source"]["container_id"])
            if before_ids != [expected_id]:
                raise MigrationError("migration_source_database_identity_changed")
            before_identity = _database_identity(
                _inspect(expected_id), project=source_project, postgres_root=postgres_root
            )
            if bluegreen:
                if before_identity["running"] or target_before_ids:
                    raise MigrationError("migration_bluegreen_source_not_quiesced")
                before = {"container_id": expected_id, "running": False}
            else:
                if not before_identity["running"] or not before_identity["healthy"]:
                    raise MigrationError("migration_source_database_not_healthy")
                before = {"container_id": expected_id, "running": True}
        elif status == "INITIAL_EMPTY":
            if before_ids or target_before_ids:
                raise MigrationError("migration_initial_store_container_exists")
            before = {"container_id": None, "running": False}
        else:
            raise MigrationError("migration_backup_status_invalid")
    expected_journal = _journal_identity(
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=image_id,
        image_input_signature=image_input_signature,
        offhost_receipt_sha256=offhost_receipt_sha256,
        host_preflight_receipt_sha256=host_preflight_receipt_sha256,
        source_backup_receipt_sha256=source_backup_receipt_sha256,
        web_role_env_sha256=web_role_env_sha256,
        backup_status=status,
        before=before,
    )
    if existing_journal is None:
        existing_journal = {
            **expected_journal,
            "status": "PREPARED",
            "receipt_path": str(receipt_path),
            "receipt_sha256": None,
        }
        _atomic_json(journal_path, existing_journal)
    else:
        _validate_journal(existing_journal, expected=expected_journal, receipt_path=receipt_path)
    if bluegreen and _running_services(source_project):
        raise MigrationError("migration_bluegreen_source_project_running")
    if [service for service in _running_services(project) if service != "market-database"]:
        raise MigrationError("migration_other_market_service_running")

    if receipt_path.exists() or receipt_path.is_symlink():
        recovered = _read_json(receipt_path)
        validate_receipt(
            recovered,
            release_sha=release_sha,
            release_tree=release_tree,
            image_id=image_id,
            image_input_signature=image_input_signature,
            offhost_receipt_sha256=offhost_receipt_sha256,
            host_preflight_receipt_sha256=host_preflight_receipt_sha256,
            source_backup_receipt_sha256=source_backup_receipt_sha256,
            web_role_env_sha256=web_role_env_sha256,
        )
        _verify_completed_live_state(
            recovered, values=values, project=project, postgres_root=postgres_root
        )
        receipt_sha256 = _digest(receipt_path)
        if (
            existing_journal.get("status") == "COMPLETE"
            and existing_journal.get("receipt_sha256") != receipt_sha256
        ):
            raise MigrationError("migration_receipt_journal_mismatch")
        existing_journal["status"] = "COMPLETE"
        existing_journal["receipt_sha256"] = receipt_sha256
        _atomic_json(journal_path, existing_journal)
        return recovered
    if existing_journal.get("status") == "COMPLETE":
        raise MigrationError("migration_complete_receipt_missing")

    existing_journal["status"] = "APPLYING"
    existing_journal["receipt_sha256"] = None
    _atomic_json(journal_path, existing_journal)

    compose = _compose(release_root, env_file)
    _run([*compose, "config", "--quiet"], label="migration_compose_config")
    created = False
    after_id = ""
    try:
        _run(
            [*compose, "up", "-d", "--no-deps", "--no-recreate", "market-database"],
            label="migration_database_start",
        )
        created = bluegreen or status == "INITIAL_EMPTY"
        after_ids = _container_ids(project)
        if len(after_ids) != 1:
            raise MigrationError("migration_database_owner_count_invalid")
        after_id = after_ids[0]
        if not created and after_id != before["container_id"]:
            raise MigrationError("migration_database_recreated_unexpectedly")
        after: dict[str, Any] = {}
        for _attempt in range(60):
            after = _database_identity(
                _inspect(after_id), project=project, postgres_root=postgres_root
            )
            if after["running"] and after["healthy"]:
                break
            time.sleep(1)
        else:
            raise MigrationError("migration_database_not_healthy")
        first = _migration_result(
            _text(
                [*compose, "run", "--rm", "--no-deps", "market-migration"],
                label="migration_first_pass",
            ),
            second=False,
        )
        second = _migration_result(
            _text(
                [*compose, "run", "--rm", "--no-deps", "market-migration"],
                label="migration_second_pass",
            ),
            second=True,
        )
        user = values.get("MARKET_POSTGRES_USER", "market_data")
        database = values.get("MARKET_POSTGRES_DB", "market_archive")
        versions = _query(
            after_id,
            user,
            database,
            "SELECT string_agg(version::text, ',' ORDER BY version) "
            "FROM market_data.schema_migrations",
        )
        table_count = int(
            _query(
                after_id,
                user,
                database,
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='market_data'",
            )
        )
        fact_count = int(
            _query(
                after_id,
                user,
                database,
                "SELECT count(*) FROM market_data.market_facts",
            )
        )
        if (
            versions != MARKET_SCHEMA_VERSIONS_TEXT
            or table_count != MARKET_SCHEMA_TABLE_COUNT
            or fact_count < 0
        ):
            raise MigrationError("migration_database_reconciliation_mismatch")
        if _running_services(project) != ["market-database"]:
            raise MigrationError("migration_unexpected_market_service_running")
        result = {
            "schema": RESULT_SCHEMA,
            "status": "PASS",
            "release_sha": release_sha,
            "release_tree": release_tree,
            "image_id": image_id,
            "image_input_signature": image_input_signature,
            "offhost_backup_receipt_sha256": offhost_receipt_sha256,
            "host_preflight_receipt_sha256": host_preflight_receipt_sha256,
            "source_backup_receipt_sha256": backup.file_digest(backup_receipt),
            "web_role_env_sha256": backup.file_digest(env_file),
            "backup_status": status,
            "before": before,
            "after": after,
            "first_pass": first,
            "second_pass": second,
            "schema_versions": MARKET_SCHEMA_VERSIONS,
            "table_count": table_count,
            "fact_count": fact_count,
            "database_mutated": created or first["status"] == "applied",
            "database_container_created": created,
            "running_services": ["market-database"],
            "private_shadow_only": not bluegreen,
            "product_authority_changed": False,
            "telegram_capture_cutover_authorized": False,
            "secrets_disclosed": False,
        }
        validate_receipt(
            result,
            release_sha=release_sha,
            release_tree=release_tree,
            image_id=image_id,
            image_input_signature=image_input_signature,
            offhost_receipt_sha256=offhost_receipt_sha256,
            host_preflight_receipt_sha256=host_preflight_receipt_sha256,
            source_backup_receipt_sha256=backup.file_digest(backup_receipt),
            web_role_env_sha256=backup.file_digest(env_file),
        )
        _write_remote_receipt(receipt_path, result)
        existing_journal["status"] = "COMPLETE"
        existing_journal["receipt_sha256"] = _digest(receipt_path)
        _atomic_json(journal_path, existing_journal)
        return result
    except Exception as exc:
        if created and after_id:
            restart_result = _run(
                ["docker", "update", "--restart=no", after_id],
                label="migration_failure_restart_disable",
                allow_failure=True,
            )
            stop_result = _run(
                ["docker", "stop", "-t", "30", after_id],
                label="migration_failure_database_stop",
                allow_failure=True,
            )
            cleanup_complete = False
            if restart_result.returncode == 0 and stop_result.returncode == 0:
                document = _inspect(after_id)
                cleanup_complete = (
                    document.get("State", {}).get("Running") is False
                    and document.get("HostConfig", {})
                    .get("RestartPolicy", {})
                    .get("Name")
                    == "no"
                )
            if not cleanup_complete:
                raise MigrationError("migration_failure_cleanup_incomplete") from exc
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--backup-env-file", type=Path)
    parser.add_argument("--backup-receipt", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-input-signature", required=True)
    parser.add_argument("--offhost-receipt-sha256", required=True)
    parser.add_argument("--host-preflight-receipt-sha256", required=True)
    parser.add_argument("--backup-maximum-age-seconds", type=int, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if (
            args.confirm != CONFIRMATION
            or not HEX40.fullmatch(args.release_sha)
            or not HEX40.fullmatch(args.release_tree)
            or not backup.IMAGE_ID.fullmatch(args.image_id)
            or not HEX64.fullmatch(args.image_input_signature)
            or not 300 <= args.backup_maximum_age_seconds <= 86400
        ):
            raise MigrationError("migration_invocation_invalid")
        result = run_migration(
            release_root=args.release_root,
            env_file=args.env_file,
            backup_env_file=args.backup_env_file,
            backup_receipt=args.backup_receipt,
            release_sha=args.release_sha,
            release_tree=args.release_tree,
            image_id=args.image_id,
            image_input_signature=args.image_input_signature,
            offhost_receipt_sha256=args.offhost_receipt_sha256,
            host_preflight_receipt_sha256=args.host_preflight_receipt_sha256,
            backup_maximum_age_seconds=args.backup_maximum_age_seconds,
            journal_path=args.journal,
            receipt_path=args.receipt,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, backup.BackupError, MigrationError) as exc:
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
