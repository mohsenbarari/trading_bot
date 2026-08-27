#!/usr/bin/env python3
"""Run the release-bound Market Pipeline archive migration exactly twice."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
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
POSTGRES_IMAGE = backup.POSTGRES_IMAGE
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class MigrationError(RuntimeError):
    """A stable, content-free migration refusal."""


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
        or payload.get("version") != 2
        or payload.get("table_count") != 26
    ):
        raise MigrationError("migration_pass_contract_invalid")
    return payload


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


def run_migration(
    *,
    release_root: Path,
    env_file: Path,
    backup_receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    offhost_receipt_sha256: str,
    host_preflight_receipt_sha256: str,
    backup_maximum_age_seconds: int,
) -> dict[str, Any]:
    values = backup.validate_release_env(
        env_file, release_sha=release_sha, image_id=image_id
    )
    backup_document = backup.verify_receipt(
        env_file=env_file,
        receipt=backup_receipt,
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=image_id,
        image_input_signature=image_input_signature,
        maximum_age_seconds=backup_maximum_age_seconds,
    )
    if not HEX64.fullmatch(offhost_receipt_sha256) or not HEX64.fullmatch(
        host_preflight_receipt_sha256
    ):
        raise MigrationError("migration_prerequisite_receipt_digest_invalid")
    project = values["MARKET_PIPELINE_PROJECT_NAME"]
    postgres_root = Path(values["MARKET_WEB_DATA_ROOT"]) / "postgres"
    before_ids = _container_ids(project)
    status = backup_document["status"]
    if status == "PASS":
        expected_id = str(backup_document["source"]["container_id"])
        if before_ids != [expected_id]:
            raise MigrationError("migration_source_database_identity_changed")
        before_identity = _database_identity(
            _inspect(expected_id), project=project, postgres_root=postgres_root
        )
        if not before_identity["running"] or not before_identity["healthy"]:
            raise MigrationError("migration_source_database_not_healthy")
        before = {"container_id": expected_id, "running": True}
    elif status == "INITIAL_EMPTY":
        if before_ids:
            raise MigrationError("migration_initial_store_container_exists")
        before = {"container_id": None, "running": False}
    else:
        raise MigrationError("migration_backup_status_invalid")
    if [service for service in _running_services(project) if service != "market-database"]:
        raise MigrationError("migration_other_market_service_running")

    compose = _compose(release_root, env_file)
    _run([*compose, "config", "--quiet"], label="migration_compose_config")
    created = False
    after_id = ""
    try:
        _run(
            [*compose, "up", "-d", "--no-deps", "--no-recreate", "market-database"],
            label="migration_database_start",
        )
        after_ids = _container_ids(project)
        if len(after_ids) != 1:
            raise MigrationError("migration_database_owner_count_invalid")
        after_id = after_ids[0]
        created = status == "INITIAL_EMPTY"
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
        if versions != "1,2" or table_count != 26 or fact_count < 0:
            raise MigrationError("migration_database_reconciliation_mismatch")
        if _running_services(project) != ["market-database"]:
            raise MigrationError("migration_unexpected_market_service_running")
        return {
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
            "schema_versions": [1, 2],
            "table_count": table_count,
            "fact_count": fact_count,
            "database_mutated": created or first["status"] == "applied",
            "database_container_created": created,
            "running_services": ["market-database"],
            "private_shadow_only": True,
            "product_authority_changed": False,
            "telegram_capture_cutover_authorized": False,
            "secrets_disclosed": False,
        }
    except Exception:
        if created and after_id:
            _run(
                ["docker", "update", "--restart=no", after_id],
                label="migration_failure_restart_disable",
                allow_failure=True,
            )
            _run(
                ["docker", "stop", "-t", "30", after_id],
                label="migration_failure_database_stop",
                allow_failure=True,
            )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--backup-receipt", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-input-signature", required=True)
    parser.add_argument("--offhost-receipt-sha256", required=True)
    parser.add_argument("--host-preflight-receipt-sha256", required=True)
    parser.add_argument("--backup-maximum-age-seconds", type=int, required=True)
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
            backup_receipt=args.backup_receipt,
            release_sha=args.release_sha,
            release_tree=args.release_tree,
            image_id=args.image_id,
            image_input_signature=args.image_input_signature,
            offhost_receipt_sha256=args.offhost_receipt_sha256,
            host_preflight_receipt_sha256=args.host_preflight_receipt_sha256,
            backup_maximum_age_seconds=args.backup_maximum_age_seconds,
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
