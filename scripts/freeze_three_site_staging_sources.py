#!/usr/bin/env python3
"""Dry-run-first, reversible freeze and evidence capture for legacy staging."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.run_three_site_staging_source_backup import (
    DOCKER,
    GIT,
    IDENT_RE,
    SAFE_ENV,
    _database_fingerprint,
    _psql,
    _secure_env,
)
from scripts.verify_three_site_staging_inventory import (
    load_inventory,
    verify_signed_inventory,
)


ROLE_APP_SERVICE = {"bot_fi": "foreign_app", "webapp_fi": "app"}
SOURCE_ROLES = tuple(ROLE_APP_SERVICE)
DATA_SERVICES = frozenset({"db", "redis"})
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class SourceFreezeError(RuntimeError):
    pass


def _run(arguments: list[str], *, timeout: int = 30) -> str:
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
        raise SourceFreezeError(f"source-freeze command unavailable: {arguments[0]}") from exc
    if result.returncode != 0:
        raise SourceFreezeError(f"source-freeze command failed closed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _compose(args: argparse.Namespace) -> list[str]:
    if args.project_name != "trading_bot_staging":
        raise SourceFreezeError("source freeze requires the exact legacy staging project")
    return [
        DOCKER, "compose", "-p", args.project_name,
        "-f", str(args.compose), "--env-file", str(args.env_file),
    ]


def _secure_bundle_directory(path: Path) -> Path:
    """Create/verify the owner-only durable rollback artifact directory."""
    if path.exists() and path.is_symlink():
        raise SourceFreezeError("rollback bundle directory cannot be a symlink")
    resolved = path.resolve()
    if not path.is_absolute():
        raise SourceFreezeError("rollback bundle directory must be absolute")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = resolved.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise SourceFreezeError("rollback bundle directory must be owner-owned mode-0700")
    return resolved


def _capture_legacy_restore_bundle(
    args: argparse.Namespace,
    *,
    prefix: list[str],
    previously_running: list[str],
    source_releases: dict[str, str],
    campaign_id: str,
    target_release_sha: str,
) -> dict[str, object]:
    """Pin resolved legacy Compose bytes and every running container image ID."""
    bundle_dir = _secure_bundle_directory(args.rollback_bundle_dir)
    compose_bytes = (_run([*prefix, "config", "--format", "yaml"]) + "\n").encode()
    compose_hash = hashlib.sha256(compose_bytes).hexdigest()
    compose_path = bundle_dir / f"legacy-compose-{compose_hash}.yaml"
    _atomic_write(compose_path, compose_bytes, mode=0o600)

    service_images: dict[str, str] = {}
    for service in sorted(previously_running):
        container_id = _run([*prefix, "ps", "-q", service])
        if not container_id:
            raise SourceFreezeError(f"running legacy service has no container: {service}")
        image_id = _run([DOCKER, "inspect", "--format", "{{.Image}}", container_id])
        if IMAGE_ID_RE.fullmatch(image_id) is None:
            raise SourceFreezeError(f"legacy service image ID is invalid: {service}")
        service_images[service] = image_id

    manifest = {
        "schema": "three-site-staging-legacy-restore-bundle-v1",
        "campaign_id": campaign_id,
        "target_release_sha": target_release_sha,
        "project_name": args.project_name,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_releases": dict(sorted(source_releases.items())),
        "previously_running_services": sorted(previously_running),
        "compose": {
            "path": str(compose_path),
            "sha256": compose_hash,
            "size": len(compose_bytes),
        },
        "service_images": service_images,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path = bundle_dir / f"legacy-restore-bundle-{manifest_hash}.json"
    _atomic_write(manifest_path, manifest_bytes, mode=0o600)
    return {
        "schema": "three-site-staging-legacy-restore-bundle-reference-v1",
        "path": str(manifest_path),
        "sha256": manifest_hash,
        "size": len(manifest_bytes),
    }


def confirmation_phrase(campaign_id: str, roles: list[str], target_sha: str) -> str:
    return f"freeze-staging:{campaign_id}:{','.join(sorted(roles))}:{target_sha}"


def build_plan(
    *, campaign_id: str, release_sha: str, roles: list[str], services: list[str]
) -> dict[str, object]:
    return {
        "status": "planned",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "source_roles": sorted(roles),
        "services_to_stop": sorted(set(services) - DATA_SERVICES),
        "services_to_keep": sorted(DATA_SERVICES),
        "redis_restore": False,
        "required_confirmation": confirmation_phrase(campaign_id, roles, release_sha),
    }


def _validate_static(args: argparse.Namespace, inventory_result: dict[str, object]):
    repo = args.repo.resolve()
    expected_compose = (repo / "deploy/staging/docker-compose.staging.yml").resolve()
    if args.compose.resolve() != expected_compose:
        raise SourceFreezeError("source freeze is locked to the reviewed legacy staging Compose")
    if _run([GIT, "-C", str(repo), "rev-parse", "HEAD"]) != inventory_result["release_sha"]:
        raise SourceFreezeError("source-freeze controller is not the signed target release")
    if _run([GIT, "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"]):
        raise SourceFreezeError("source-freeze controller repository must be clean")
    env = _secure_env(args.env_file)
    user = env.get("POSTGRES_USER", "")
    database = env.get("POSTGRES_DB", "")
    if not IDENT_RE.fullmatch(user) or not IDENT_RE.fullmatch(database):
        raise SourceFreezeError("legacy staging database identity is invalid")
    prefix = _compose(args)
    _run([*prefix, "config", "--quiet"])
    services = [value for value in _run([*prefix, "config", "--services"]).splitlines() if value]
    if not DATA_SERVICES.issubset(services):
        raise SourceFreezeError("legacy staging Compose lacks database or Redis")
    for role in args.source_role:
        if ROLE_APP_SERVICE[role] not in services:
            raise SourceFreezeError(f"legacy staging Compose lacks source role {role}")
    return repo, env, prefix, services, user, database


def execute(
    args: argparse.Namespace,
    *,
    inventory_result: dict[str, object],
) -> dict[str, object]:
    repo, env, prefix, services, user, database = _validate_static(args, inventory_result)
    required_confirmation = confirmation_phrase(
        str(inventory_result["campaign_id"]), args.source_role, str(inventory_result["release_sha"])
    )
    if args.confirm != required_confirmation:
        raise SourceFreezeError("source-freeze confirmation mismatch")
    previously_running = [
        value for value in _run([*prefix, "ps", "--status", "running", "--services"]).splitlines()
        if value
    ]
    if not DATA_SERVICES.issubset(previously_running):
        raise SourceFreezeError("legacy staging DB and Redis must be running before freeze")
    releases: dict[str, str] = {}
    for role in args.source_role:
        app_service = ROLE_APP_SERVICE[role]
        if app_service not in previously_running:
            raise SourceFreezeError(f"legacy source application is not running: {app_service}")
        release = _run([*prefix, "exec", "-T", app_service, "printenv", "RELEASE_SHA"])
        expected = args.expected_source_release_sha[role]
        if release != expected:
            raise SourceFreezeError(f"legacy runtime release differs for {role}")
        releases[role] = release
    # Capture rollback material before stopping anything.  The bundle is
    # content-addressed and contains expanded Compose bytes plus immutable
    # Docker image IDs, so restore never depends on the target-release checkout.
    restore_bundle = _capture_legacy_restore_bundle(
        args,
        prefix=prefix,
        previously_running=previously_running,
        source_releases=releases,
        campaign_id=str(inventory_result["campaign_id"]),
        target_release_sha=str(inventory_result["release_sha"]),
    )
    mutating_services = sorted(set(services) - DATA_SERVICES)
    _run([*prefix, "stop", "--timeout", "30", *mutating_services], timeout=180)
    running_after = [
        value for value in _run([*prefix, "ps", "--status", "running", "--services"]).splitlines()
        if value
    ]
    if set(running_after) != DATA_SERVICES:
        raise SourceFreezeError("legacy staging has unexpected running services after freeze")
    source_system_id = _psql(
        prefix, "db", user, database, "SELECT system_identifier FROM pg_control_system()"
    )
    revision = _psql(prefix, "db", user, database, "SELECT version_num FROM alembic_version")
    fingerprint, row_count, table_count = _database_fingerprint(
        lambda sql: _psql(prefix, "db", user, database, sql)
    )
    redis_dbsize = _run([*prefix, "exec", "-T", "redis", "redis-cli", "--raw", "DBSIZE"])
    appendonly = _run(
        [*prefix, "exec", "-T", "redis", "redis-cli", "--raw", "CONFIG", "GET", "appendonly"]
    ).splitlines()
    lastsave = _run([*prefix, "exec", "-T", "redis", "redis-cli", "--raw", "LASTSAVE"])
    if (
        not redis_dbsize.isdigit()
        or appendonly != ["appendonly", "yes"]
        or not lastsave.isdigit()
    ):
        raise SourceFreezeError("legacy Redis persistence observation is invalid")
    evidence = {
        "schema": "three-site-staging-source-freeze-v1",
        "campaign_id": inventory_result["campaign_id"],
        "target_release_sha": inventory_result["release_sha"],
        "project_name": args.project_name,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_roles": [
            {
                "source_role": role,
                "app_service": ROLE_APP_SERVICE[role],
                "source_release_sha": releases[role],
            }
            for role in sorted(args.source_role)
        ],
        "previously_running_services": sorted(previously_running),
        "stopped_services": mutating_services,
        "running_services": sorted(running_after),
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
        "status": "frozen",
        "campaign_id": inventory_result["campaign_id"],
        "source_roles": sorted(args.source_role),
        "evidence": str(args.output),
        "evidence_sha256": hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "rollback_services": sorted(previously_running),
    }


def _role_mapping(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        role, separator, release = value.partition("=")
        if not separator or role not in SOURCE_ROLES or role in result or not SHA_RE.fullmatch(release):
            raise SourceFreezeError("--expected-source-release-sha must use unique role=40hex")
        result[role] = release
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-role", action="append", choices=SOURCE_ROLES, required=True)
    parser.add_argument("--expected-source-release-sha", action="append", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--project-name", default="trading_bot_staging")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--signer-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rollback-bundle-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        args.source_role = list(dict.fromkeys(args.source_role))
        args.expected_source_release_sha = _role_mapping(args.expected_source_release_sha)
        if set(args.source_role) != set(args.expected_source_release_sha):
            raise SourceFreezeError("every selected source role requires one expected release")
        inventory = load_inventory(args.inventory)
        inventory_result = verify_signed_inventory(
            inventory,
            approval=load_inventory(args.inventory_approval),
            signer_policy=load_inventory(args.signer_policy),
            host_destructive=True,
        )
        if inventory_result["inventory_stage"] != "provisioned":
            raise SourceFreezeError("source freeze requires signed provisioned inventory")
        _repo, _env, _prefix, services, _user, _database = _validate_static(
            args, inventory_result
        )
        plan = build_plan(
            campaign_id=str(inventory_result["campaign_id"]),
            release_sha=str(inventory_result["release_sha"]),
            roles=args.source_role,
            services=services,
        )
        result = execute(args, inventory_result=inventory_result) if args.apply else plan
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
