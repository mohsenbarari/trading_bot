#!/usr/bin/env python3
"""Re-attest an already-frozen legacy staging source without app mutation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.freeze_three_site_staging_sources import (
    DATA_SERVICES,
    IMAGE_ID_RE,
    ROLE_APP_SERVICE,
    SHA_RE,
    SOURCE_ROLES,
    SourceFreezeError,
    _run,
    _validate_static,
)
from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.run_three_site_staging_source_backup import _database_fingerprint, _psql
from scripts.verify_three_site_staging_inventory import load_inventory, verify_approved_inventory


MAX_EVIDENCE_BYTES = 1024 * 1024
FREEZE_FIELDS = {
    "schema", "campaign_id", "target_release_sha", "project_name", "observed_at",
    "source_roles", "previously_running_services", "stopped_services",
    "running_services", "postgres", "redis_observation", "legacy_restore_bundle",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_secure_json(path: Path, *, label: str) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SourceFreezeError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= MAX_EVIDENCE_BYTES
    ):
        raise SourceFreezeError(f"{label} must be an owner-owned non-linked mode-0600 file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceFreezeError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise SourceFreezeError(f"{label} must contain an object")
    return value


def _load_prior_freeze(
    path: Path,
    *,
    source_role: str,
    source_release_sha: str,
    project_name: str,
) -> dict[str, object]:
    evidence = _load_secure_json(path, label="prior source-freeze evidence")
    roles = evidence.get("source_roles")
    matching = [
        row for row in roles or []
        if isinstance(row, dict) and row.get("source_role") == source_role
    ]
    if (
        set(evidence) != FREEZE_FIELDS
        or evidence.get("schema") != "three-site-staging-source-freeze-v1"
        or evidence.get("project_name") != project_name
        or evidence.get("running_services") != ["db", "redis"]
        or not isinstance(evidence.get("previously_running_services"), list)
        or not isinstance(evidence.get("stopped_services"), list)
        or len(matching) != 1
        or set(matching[0]) != {"source_role", "app_service", "source_release_sha"}
        or matching[0].get("app_service") != ROLE_APP_SERVICE[source_role]
        or matching[0].get("source_release_sha") != source_release_sha
        or ROLE_APP_SERVICE[source_role] not in evidence["previously_running_services"]
        or ROLE_APP_SERVICE[source_role] not in evidence["stopped_services"]
        or "db" in evidence["stopped_services"]
        or "redis" in evidence["stopped_services"]
    ):
        raise SourceFreezeError("prior source-freeze identity/state is invalid")
    try:
        observed = datetime.fromisoformat(str(evidence["observed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceFreezeError("prior source-freeze timestamp is invalid") from exc
    if observed.tzinfo is None or observed > datetime.now(timezone.utc):
        raise SourceFreezeError("prior source-freeze timestamp is future-dated")
    postgres = evidence.get("postgres")
    redis = evidence.get("redis_observation")
    if (
        not isinstance(postgres, dict)
        or set(postgres) != {
            "system_id", "alembic_revision", "database_fingerprint_sha256",
            "database_row_count", "public_table_count",
        }
        or re.fullmatch(r"[0-9]{10,20}", str(postgres.get("system_id", ""))) is None
        or re.fullmatch(
            r"[0-9a-f]{64}", str(postgres.get("database_fingerprint_sha256", ""))
        ) is None
        or not isinstance(redis, dict)
        or set(redis) != {"dbsize", "appendonly", "lastsave_unix", "restore"}
        or redis.get("appendonly") is not True
        or redis.get("restore") is not False
    ):
        raise SourceFreezeError("prior source-freeze data evidence is invalid")
    return evidence


def _verify_restore_bundle(
    reference: object,
    *,
    prior_evidence: dict[str, object],
    source_role: str,
    source_release_sha: str,
    project_name: str,
) -> dict[str, object]:
    if (
        not isinstance(reference, dict)
        or set(reference) != {"schema", "path", "sha256", "size"}
        or reference.get("schema")
        != "three-site-staging-legacy-restore-bundle-reference-v1"
        or re.fullmatch(r"[0-9a-f]{64}", str(reference.get("sha256", ""))) is None
        or type(reference.get("size")) is not int
        or not 1 <= int(reference["size"]) <= MAX_EVIDENCE_BYTES
    ):
        raise SourceFreezeError("prior rollback reference is invalid")
    manifest_path = Path(str(reference.get("path", "")))
    if not manifest_path.is_absolute():
        raise SourceFreezeError("prior rollback manifest path must be absolute")
    manifest = _load_secure_json(manifest_path, label="prior rollback manifest")
    if manifest_path.stat().st_size != reference["size"] or _sha256(manifest_path) != reference["sha256"]:
        raise SourceFreezeError("prior rollback manifest bytes differ from its reference")
    source_releases = manifest.get("source_releases")
    service_images = manifest.get("service_images")
    if (
        manifest.get("schema") != "three-site-staging-legacy-restore-bundle-v1"
        or manifest.get("campaign_id") != prior_evidence.get("campaign_id")
        or manifest.get("target_release_sha") != prior_evidence.get("target_release_sha")
        or manifest.get("project_name") != project_name
        or manifest.get("previously_running_services")
        != prior_evidence.get("previously_running_services")
        or not isinstance(source_releases, dict)
        or source_releases.get(source_role) != source_release_sha
        or not isinstance(service_images, dict)
        or set(service_images) != set(prior_evidence["previously_running_services"])
        or any(IMAGE_ID_RE.fullmatch(str(value)) is None for value in service_images.values())
    ):
        raise SourceFreezeError("prior rollback manifest identity/content is invalid")
    compose = manifest.get("compose")
    if (
        not isinstance(compose, dict)
        or set(compose) != {"path", "sha256", "size"}
        or re.fullmatch(r"[0-9a-f]{64}", str(compose.get("sha256", ""))) is None
        or type(compose.get("size")) is not int
    ):
        raise SourceFreezeError("prior rollback Compose reference is invalid")
    compose_path = Path(str(compose.get("path", "")))
    if not compose_path.is_absolute():
        raise SourceFreezeError("prior rollback Compose path must be absolute")
    try:
        compose_metadata = compose_path.lstat()
    except OSError as exc:
        raise SourceFreezeError("prior rollback Compose is unavailable") from exc
    if (
        not stat.S_ISREG(compose_metadata.st_mode)
        or stat.S_ISLNK(compose_metadata.st_mode)
        or compose_metadata.st_nlink != 1
        or compose_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(compose_metadata.st_mode) != 0o600
        or compose_metadata.st_size != compose["size"]
        or _sha256(compose_path) != compose["sha256"]
    ):
        raise SourceFreezeError("prior rollback Compose bytes/mode/hash differ")
    return dict(reference)


def confirmation_phrase(campaign_id: str, source_role: str, target_sha: str) -> str:
    return f"adopt-frozen-staging:{campaign_id}:{source_role}:{target_sha}"


def build_plan(args: argparse.Namespace, inventory: dict[str, object]) -> dict[str, object]:
    return {
        "status": "planned",
        "campaign_id": inventory["campaign_id"],
        "target_release_sha": inventory["release_sha"],
        "source_role": args.source_role,
        "project_name": args.project_name,
        "application_mutation": False,
        "redis_restore": False,
        "required_confirmation": confirmation_phrase(
            str(inventory["campaign_id"]), args.source_role, str(inventory["release_sha"])
        ),
    }


def execute(args: argparse.Namespace, *, inventory_result: dict[str, object]) -> dict[str, object]:
    _repo, _env, prefix, _services, user, database = _validate_static(
        args, inventory_result
    )
    required = confirmation_phrase(
        str(inventory_result["campaign_id"]),
        args.source_role[0],
        str(inventory_result["release_sha"]),
    )
    if args.confirm != required:
        raise SourceFreezeError("adopt-frozen confirmation mismatch")
    if args.output.exists():
        raise SourceFreezeError("fresh source-freeze output already exists")
    source_role = args.source_role[0]
    prior = _load_prior_freeze(
        args.prior_freeze_evidence,
        source_role=source_role,
        source_release_sha=args.expected_source_release_sha[source_role],
        project_name=args.project_name,
    )
    restore_bundle = _verify_restore_bundle(
        prior["legacy_restore_bundle"],
        prior_evidence=prior,
        source_role=source_role,
        source_release_sha=args.expected_source_release_sha[source_role],
        project_name=args.project_name,
    )
    running = sorted(
        value for value in _run(
            [*prefix, "ps", "--status", "running", "--services"]
        ).splitlines() if value
    )
    if set(running) != DATA_SERVICES:
        raise SourceFreezeError("already-frozen source must have only DB and Redis running")
    app_container = _run([*prefix, "ps", "-q", ROLE_APP_SERVICE[source_role]])
    if app_container:
        app_running = _run(
            ["/usr/bin/docker", "inspect", "--format", "{{.State.Running}}", app_container]
        )
        if app_running != "false":
            raise SourceFreezeError("legacy source application is unexpectedly running")
    source_system_id = _psql(
        prefix, "db", user, database, "SELECT system_identifier FROM pg_control_system()"
    )
    revision = _psql(prefix, "db", user, database, "SELECT version_num FROM alembic_version")
    prior_postgres = prior["postgres"]
    if source_system_id != prior_postgres["system_id"] or revision != prior_postgres["alembic_revision"]:
        raise SourceFreezeError("current PostgreSQL identity/revision differs from prior freeze")
    fingerprint, row_count, table_count = _database_fingerprint(
        lambda sql: _psql(prefix, "db", user, database, sql)
    )
    redis_dbsize = _run([*prefix, "exec", "-T", "redis", "redis-cli", "--raw", "DBSIZE"])
    appendonly = _run(
        [*prefix, "exec", "-T", "redis", "redis-cli", "--raw", "CONFIG", "GET", "appendonly"]
    ).splitlines()
    lastsave = _run([*prefix, "exec", "-T", "redis", "redis-cli", "--raw", "LASTSAVE"])
    if not redis_dbsize.isdigit() or appendonly != ["appendonly", "yes"] or not lastsave.isdigit():
        raise SourceFreezeError("current Redis persistence observation is invalid")
    evidence = {
        "schema": "three-site-staging-source-freeze-v1",
        "campaign_id": inventory_result["campaign_id"],
        "target_release_sha": inventory_result["release_sha"],
        "project_name": args.project_name,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_roles": [
            {
                "source_role": source_role,
                "app_service": ROLE_APP_SERVICE[source_role],
                "source_release_sha": args.expected_source_release_sha[source_role],
            }
        ],
        "previously_running_services": prior["previously_running_services"],
        "stopped_services": prior["stopped_services"],
        "running_services": running,
        "postgres": {
            "system_id": source_system_id,
            "alembic_revision": revision,
            "database_fingerprint_sha256": fingerprint,
            "database_row_count": row_count,
            "public_table_count": table_count,
        },
        "redis_observation": {
            "dbsize": int(redis_dbsize),
            "appendonly": True,
            "lastsave_unix": int(lastsave),
            "restore": False,
        },
        "legacy_restore_bundle": restore_bundle,
    }
    encoded = (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode()
    _atomic_write(args.output, encoded, mode=0o600)
    return {
        "status": "adopted-frozen",
        "campaign_id": inventory_result["campaign_id"],
        "source_role": source_role,
        "evidence": str(args.output),
        "evidence_sha256": hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "application_mutation": False,
        "redis_restore": False,
    }


def _release_mapping(source_role: str, value: str) -> dict[str, str]:
    role, separator, release = value.partition("=")
    if not separator or role != source_role or SHA_RE.fullmatch(release) is None:
        raise SourceFreezeError("--expected-source-release-sha must match source_role=40hex")
    return {role: release}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-role", choices=SOURCE_ROLES, required=True)
    parser.add_argument("--expected-source-release-sha", required=True)
    parser.add_argument("--prior-freeze-evidence", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        role = args.source_role
        args.source_role = [role]
        args.expected_source_release_sha = _release_mapping(
            role, args.expected_source_release_sha
        )
        inventory = load_inventory(args.inventory)
        inventory_result = verify_approved_inventory(
            inventory,
            approval=load_inventory(args.inventory_approval),
            approval_policy=load_inventory(args.approval_policy),
            host_destructive=None,
        )
        if inventory_result["inventory_stage"] != "provisioned":
            raise SourceFreezeError("adoption requires approved provisioned inventory")
        _validate_static(args, inventory_result)
        args.source_role = [role]
        result = execute(args, inventory_result=inventory_result) if args.apply else build_plan(
            argparse.Namespace(source_role=role, project_name=args.project_name), inventory_result
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
