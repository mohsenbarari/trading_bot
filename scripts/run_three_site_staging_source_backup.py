#!/usr/bin/env python3
"""Create and restore-verify one legacy staging source backup, locally."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.render_three_site_staging_role_compose import _atomic_write, parse_env_values
from core.three_site_staging_source_contract import (
    legacy_staging_project_allowed,
)
from scripts.verify_three_site_staging_inventory import (
    load_inventory,
    verify_approved_inventory,
)


DOCKER = "/usr/bin/docker"
GIT = "/usr/bin/git"
POSTGRES_IMAGE = "postgres:15-alpine"
ROLE_APP_SERVICE = {"bot_fi": "foreign_app", "webapp_fi": "app"}
ROLE_PROFILE = {"bot_fi": "staging-bot", "webapp_fi": None}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


class StagingBackupError(RuntimeError):
    pass


def confirmation_phrase(campaign_id: str, source_role: str, target_release_sha: str) -> str:
    return f"backup-and-restore:{campaign_id}:{source_role}:{target_release_sha}"


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
        raise StagingBackupError(f"required command is unavailable: {arguments[0]}") from exc
    if result.returncode != 0:
        raise StagingBackupError(f"command failed closed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _compose_base(
    compose: Path, env_file: Path, source_role: str, project_name: str
) -> list[str]:
    if not legacy_staging_project_allowed(project_name, (source_role,)):
        raise StagingBackupError(
            "legacy source backup project is not approved for the selected source role"
        )
    result = [
        DOCKER, "compose", "-p", project_name,
        "-f", str(compose), "--env-file", str(env_file),
    ]
    profile = ROLE_PROFILE[source_role]
    if profile:
        result.extend(("--profile", profile))
    return result


def _secure_env(path: Path) -> dict[str, str]:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise StagingBackupError("legacy staging environment must be a non-linked mode-0600 file")
    return parse_env_values(path.read_text(encoding="utf-8"))


def _prepare_output(path: Path, *, repo: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(repo.resolve())
    except ValueError:
        pass
    else:
        raise StagingBackupError("backup artifacts must be outside the Git repository")
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise StagingBackupError("backup output directory must be absent or empty")
        path.chmod(0o700)
    else:
        path.mkdir(mode=0o700, parents=True)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise StagingBackupError("backup output directory must be mode 0700")


def _stream_to_file(arguments: list[str], target: Path, *, timeout: int) -> None:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.PIPE,
                env=SAFE_ENV,
            )
            try:
                _stderr = process.communicate(timeout=timeout)[1]
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.communicate()
                raise StagingBackupError("backup stream timed out") from exc
            if process.returncode != 0:
                raise StagingBackupError("backup stream command failed closed")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if target.stat().st_size <= 0:
        target.unlink(missing_ok=True)
        raise StagingBackupError("backup stream produced an empty artifact")


def _sha_file(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _load_freeze_evidence(
    path: Path,
    *,
    campaign_id: str,
    target_release_sha: str,
    source_role: str,
    expected_source_release_sha: str,
    project_name: str,
) -> tuple[dict[str, object], str]:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size <= 0
        or metadata.st_size > 1024 * 1024
    ):
        raise StagingBackupError("source-freeze evidence must be a non-linked mode-0600 file")
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagingBackupError("source-freeze evidence is unreadable") from exc
    fields = {
        "schema", "campaign_id", "target_release_sha", "project_name", "observed_at",
        "source_roles", "previously_running_services", "stopped_services",
        "running_services", "postgres", "redis_observation", "legacy_restore_bundle",
    }
    if (
        not isinstance(evidence, dict)
        or set(evidence) != fields
        or evidence["schema"] != "three-site-staging-source-freeze-v1"
        or evidence["campaign_id"] != campaign_id
        or evidence["target_release_sha"] != target_release_sha
        or evidence["project_name"] != project_name
        or evidence["running_services"] != ["db", "redis"]
        or "db" in evidence["stopped_services"]
        or "redis" in evidence["stopped_services"]
        or not isinstance(evidence["legacy_restore_bundle"], dict)
        or set(evidence["legacy_restore_bundle"]) != {"schema", "path", "sha256", "size"}
        or evidence["legacy_restore_bundle"].get("schema")
        != "three-site-staging-legacy-restore-bundle-reference-v1"
        or not Path(str(evidence["legacy_restore_bundle"].get("path", ""))).is_absolute()
        or re.fullmatch(
            r"[0-9a-f]{64}", str(evidence["legacy_restore_bundle"].get("sha256", ""))
        ) is None
        or type(evidence["legacy_restore_bundle"].get("size")) is not int
        or not 1 <= evidence["legacy_restore_bundle"]["size"] <= 1024 * 1024
    ):
        raise StagingBackupError("source-freeze evidence identity/state is invalid")
    try:
        observed = datetime.fromisoformat(str(evidence["observed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise StagingBackupError("source-freeze evidence timestamp is invalid") from exc
    now = datetime.now(timezone.utc)
    if observed.tzinfo is None or observed > now + timedelta(minutes=5) or now - observed > timedelta(hours=1):
        raise StagingBackupError("source-freeze evidence is stale or future-dated")
    roles = evidence["source_roles"]
    if not isinstance(roles, list):
        raise StagingBackupError("source-freeze role evidence is invalid")
    matching = [
        row for row in roles
        if isinstance(row, dict) and row.get("source_role") == source_role
    ]
    if (
        len(matching) != 1
        or set(matching[0]) != {"source_role", "app_service", "source_release_sha"}
        or matching[0]["app_service"] != ROLE_APP_SERVICE[source_role]
        or matching[0]["source_release_sha"] != expected_source_release_sha
    ):
        raise StagingBackupError("source-freeze evidence does not bind the selected source role")
    postgres = evidence["postgres"]
    redis = evidence["redis_observation"]
    if (
        not isinstance(postgres, dict)
        or set(postgres) != {
            "system_id", "alembic_revision", "database_fingerprint_sha256",
            "database_row_count", "public_table_count",
        }
        or not re.fullmatch(r"[0-9]{10,20}", str(postgres["system_id"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(postgres["database_fingerprint_sha256"]))
        or type(postgres["database_row_count"]) is not int
        or type(postgres["public_table_count"]) is not int
        or not isinstance(redis, dict)
        or set(redis) != {"dbsize", "appendonly", "lastsave_unix", "restore"}
        or type(redis["dbsize"]) is not int
        or redis["dbsize"] < 0
        or redis["appendonly"] is not True
        or type(redis["lastsave_unix"]) is not int
        or redis["lastsave_unix"] <= 0
        or redis["restore"] is not False
    ):
        raise StagingBackupError("source-freeze database/Redis evidence is invalid")
    return evidence, _canonical_hash(evidence)


def verify_backup_manifest(
    manifest: dict[str, object],
    *,
    campaign_id: str,
    source_role: str,
    source_release_sha: str,
    target_release_sha: str,
    verify_files: bool,
) -> dict[str, object]:
    required = {
        "schema", "campaign_id", "source_role", "source_release_sha",
        "target_release_sha", "created_at", "source_postgres_system_id",
        "source_alembic_revision", "artifacts", "restore_drill",
        "source_freeze_evidence_sha256", "redis_observation",
        "redis_restore", "application_mutation",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != required
        or manifest["schema"] != "three-site-staging-source-backup-v2"
        or manifest["campaign_id"] != campaign_id
        or manifest["source_role"] != source_role
        or manifest["source_release_sha"] != source_release_sha
        or manifest["target_release_sha"] != target_release_sha
        or manifest["redis_restore"] is not False
        or manifest["application_mutation"] is not False
        or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_freeze_evidence_sha256"]))
    ):
        raise StagingBackupError("source backup manifest identity/fields are invalid")
    try:
        created = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise StagingBackupError("source backup timestamp is invalid") from exc
    if created.tzinfo is None:
        raise StagingBackupError("source backup timestamp lacks timezone")
    source_system_id = str(manifest["source_postgres_system_id"])
    if not re.fullmatch(r"[0-9]{10,20}", source_system_id):
        raise StagingBackupError("source PostgreSQL system identity is malformed")
    redis = manifest["redis_observation"]
    if (
        not isinstance(redis, dict)
        or set(redis) != {"dbsize", "appendonly", "lastsave_unix", "restore"}
        or type(redis["dbsize"]) is not int
        or redis["dbsize"] < 0
        or redis["appendonly"] is not True
        or type(redis["lastsave_unix"]) is not int
        or redis["lastsave_unix"] <= 0
        or redis["restore"] is not False
    ):
        raise StagingBackupError("source Redis observation is invalid")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {"postgres", "uploads", "audit"}:
        raise StagingBackupError("source backup artifact set is incomplete")
    artifact_hashes: dict[str, str] = {}
    for kind, raw in artifacts.items():
        expected_fields = {"path", "bytes", "sha256"}
        if kind != "postgres":
            expected_fields.add("safe_member_count")
        if (
            not isinstance(raw, dict)
            or set(raw) != expected_fields
            or type(raw["bytes"]) is not int
            or int(raw["bytes"]) <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(raw["sha256"]))
            or (kind != "postgres" and type(raw["safe_member_count"]) is not int)
        ):
            raise StagingBackupError(f"{kind} backup artifact evidence is invalid")
        artifact_hashes[kind] = str(raw["sha256"])
        if verify_files:
            path = Path(str(raw["path"]))
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size != raw["bytes"]
                or _sha_file(path)["sha256"] != raw["sha256"]
            ):
                raise StagingBackupError(f"{kind} backup artifact bytes/mode/hash differ")
            if kind != "postgres" and verify_tar_artifact(path) != raw["safe_member_count"]:
                raise StagingBackupError(f"{kind} archive member inventory differs")
    restore = manifest["restore_drill"]
    restore_fields = {
        "status", "restored_alembic_revision", "scratch_postgres_system_id",
        "database_fingerprint_sha256", "database_row_count", "public_table_count",
    }
    if (
        not isinstance(restore, dict)
        or set(restore) != restore_fields
        or restore["status"] != "passed"
        or restore["restored_alembic_revision"] != manifest["source_alembic_revision"]
        or restore["scratch_postgres_system_id"] == source_system_id
        or not re.fullmatch(r"[0-9]{10,20}", str(restore["scratch_postgres_system_id"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(restore["database_fingerprint_sha256"]))
        or type(restore["database_row_count"]) is not int
        or int(restore["database_row_count"]) < 0
        or type(restore["public_table_count"]) is not int
        or int(restore["public_table_count"]) <= 0
    ):
        raise StagingBackupError("restore-drill evidence is incomplete or inconsistent")
    return {
        "status": "verified",
        "campaign_id": campaign_id,
        "source_role": source_role,
        "artifact_sha256": artifact_hashes,
        "database_fingerprint_sha256": restore["database_fingerprint_sha256"],
        "database_row_count": restore["database_row_count"],
    }


def verify_tar_artifact(path: Path) -> int:
    count = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                normalized = PurePosixPath(member.name)
                if (
                    normalized.is_absolute()
                    or ".." in normalized.parts
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    raise StagingBackupError("archive contains an unsafe member")
                count += 1
    except (OSError, tarfile.TarError) as exc:
        raise StagingBackupError("archive integrity check failed") from exc
    return count


def _psql(
    docker_prefix: list[str],
    service: str,
    user: str,
    database: str,
    sql: str,
) -> str:
    return _run(
        [
            *docker_prefix, "exec", "-T", service,
            "psql", "-v", "ON_ERROR_STOP=1", "-U", user, "-d", database,
            "-Atqc", sql,
        ],
        timeout=60,
    )


def _scratch_psql(container: str, sql: str) -> str:
    return _run(
        [
            DOCKER, "exec", container, "psql", "-v", "ON_ERROR_STOP=1",
            "-U", "restore", "-d", "restore", "-Atqc", sql,
        ],
        timeout=60,
    )


def _wait_for_scratch_database(container: str, *, attempts: int = 30) -> None:
    """Wait until the initialized scratch database, not merely PostgreSQL, is usable.

    The official PostgreSQL image can accept socket connections while its entrypoint
    is still creating ``POSTGRES_DB``.  ``pg_isready`` alone therefore races with
    the first ``pg_restore``.  A successful query against the exact restore
    database is the readiness condition required by the restore drill.
    """
    for _attempt in range(attempts):
        ready = subprocess.run(
            [DOCKER, "exec", container, "pg_isready", "-U", "restore", "-d", "restore"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
            env=SAFE_ENV,
        )
        if ready.returncode == 0:
            probe = subprocess.run(
                [
                    DOCKER, "exec", container, "psql", "-v", "ON_ERROR_STOP=1",
                    "-U", "restore", "-d", "restore", "-Atqc", "SELECT 1",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
                env=SAFE_ENV,
            )
            if probe.returncode == 0 and probe.stdout.strip() == "1":
                return
        time.sleep(1)
    raise StagingBackupError("scratch PostgreSQL restore database did not become ready")


def _database_fingerprint(query) -> tuple[str, int, int]:  # noqa: ANN001
    tables = [
        value for value in query(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).splitlines() if value
    ]
    summaries: list[list[object]] = []
    total_rows = 0
    for table in tables:
        if not IDENT_RE.fullmatch(table):
            raise StagingBackupError("database contains an unsupported public table identifier")
        result = query(
            "SELECT count(*)::text || '|' || "
            "coalesce(md5(string_agg(row_data, E'\\n' ORDER BY row_data)), md5('')) "
            f"FROM (SELECT row_to_json(source_row)::text AS row_data FROM public.\"{table}\" source_row) rows"
        )
        count_text, separator, digest = result.partition("|")
        if not separator or not count_text.isdigit() or not re.fullmatch(r"[0-9a-f]{32}", digest):
            raise StagingBackupError("database table fingerprint is malformed")
        count = int(count_text)
        total_rows += count
        summaries.append([table, count, digest])
    sequences = query(
        "SELECT sequencename || '|' || coalesce(last_value::text, 'null') "
        "FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
    ).splitlines()
    payload = {"tables": summaries, "sequences": sequences}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return digest, total_rows, len(tables)


def _restore_drill(dump_path: Path, *, container: str) -> dict[str, object]:
    _run(
        [
            DOCKER, "run", "-d", "--name", container,
            "--label", "trading-bot.three-site-staging.restore-drill=true",
            "-e", "POSTGRES_USER=restore", "-e", "POSTGRES_DB=restore",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust", POSTGRES_IMAGE,
        ],
        timeout=60,
    )
    try:
        _wait_for_scratch_database(container)
        with dump_path.open("rb") as source:
            result = subprocess.run(
                [
                    DOCKER, "exec", "-i", container, "pg_restore",
                    "-U", "restore", "-d", "restore", "--exit-on-error",
                    "--no-owner", "--no-acl",
                ],
                stdin=source,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=900,
                env=SAFE_ENV,
            )
        if result.returncode != 0:
            raise StagingBackupError("PostgreSQL restore drill failed")
        revision = _scratch_psql(container, "SELECT version_num FROM alembic_version")
        system_id = _scratch_psql(
            container, "SELECT system_identifier FROM pg_control_system()"
        )
        fingerprint, row_count, table_count = _database_fingerprint(
            lambda sql: _scratch_psql(container, sql)
        )
        return {
            "status": "passed",
            "restored_alembic_revision": revision,
            "scratch_postgres_system_id": system_id,
            "database_fingerprint_sha256": fingerprint,
            "database_row_count": row_count,
            "public_table_count": table_count,
        }
    finally:
        subprocess.run(
            [DOCKER, "rm", "-f", container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
            env=SAFE_ENV,
        )


def build_plan(args: argparse.Namespace, inventory_result: dict[str, object]) -> dict[str, object]:
    return {
        "status": "planned",
        "campaign_id": inventory_result["campaign_id"],
        "source_role": args.source_role,
        "source_release_sha": args.expected_source_release_sha,
        "target_release_sha": inventory_result["release_sha"],
        "artifacts": ["postgres.custom", "uploads.tar.gz", "audit.tar.gz"],
        "restore_drill": "isolated-postgres-15-no-published-port",
        "redis_restore": False,
        "required_confirmation": confirmation_phrase(
            str(inventory_result["campaign_id"]),
            args.source_role,
            str(inventory_result["release_sha"]),
        ),
    }


def execute(args: argparse.Namespace, inventory_result: dict[str, object]) -> dict[str, object]:
    repo = args.repo.resolve()
    expected_compose = (repo / "deploy/staging/docker-compose.staging.yml").resolve()
    if args.compose.resolve() != expected_compose:
        raise StagingBackupError("source backup is locked to the reviewed legacy staging Compose")
    if _run([GIT, "-C", str(repo), "rev-parse", "HEAD"]) != args.target_release_sha:
        raise StagingBackupError("backup controller repository is not the exact target release")
    if _run([GIT, "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"]):
        raise StagingBackupError("backup controller repository must be clean")
    env = _secure_env(args.env_file)
    user = env.get("POSTGRES_USER", "")
    database = env.get("POSTGRES_DB", "")
    if not IDENT_RE.fullmatch(user) or not IDENT_RE.fullmatch(database):
        raise StagingBackupError("legacy staging database identity is invalid")
    _prepare_output(args.output_dir, repo=repo)
    prefix = _compose_base(
        args.compose, args.env_file, args.source_role, args.project_name
    )
    _run([*prefix, "config", "--quiet"], timeout=30)
    freeze, freeze_hash = _load_freeze_evidence(
        args.source_freeze_evidence,
        campaign_id=str(inventory_result["campaign_id"]),
        target_release_sha=str(inventory_result["release_sha"]),
        source_role=args.source_role,
        expected_source_release_sha=args.expected_source_release_sha,
        project_name=args.project_name,
    )
    app_service = ROLE_APP_SERVICE[args.source_role]
    if not _run([*prefix, "ps", "--status", "running", "-q", "db"]):
        raise StagingBackupError("required legacy staging database is not running")
    if _run([*prefix, "ps", "--status", "running", "-q", app_service]):
        raise StagingBackupError("legacy source application resumed after freeze")
    source_system_id = _psql(
        prefix, "db", user, database,
        "SELECT system_identifier FROM pg_control_system()",
    )
    source_revision = _psql(
        prefix, "db", user, database, "SELECT version_num FROM alembic_version"
    )
    if (
        source_system_id != freeze["postgres"]["system_id"]
        or source_revision != freeze["postgres"]["alembic_revision"]
    ):
        raise StagingBackupError("legacy database identity changed after source freeze")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{args.source_role}-{inventory_result['campaign_id']}-{stamp}"
    dump = args.output_dir / f"{base}.postgres.custom"
    uploads = args.output_dir / f"{base}.uploads.tar.gz"
    audit = args.output_dir / f"{base}.audit.tar.gz"
    _stream_to_file(
        [
            *prefix, "exec", "-T", "db", "pg_dump", "-U", user, "-d", database,
            "-Fc", "--no-owner", "--no-acl",
        ],
        dump,
        timeout=900,
    )
    for target, directory in ((uploads, "/app/uploads"), (audit, "/app/audit_trail")):
        _stream_to_file(
            [
                *prefix, "run", "--rm", "--no-deps", "-T",
                "--entrypoint", "tar", app_service,
                "-C", directory, "-czf", "-", ".",
            ],
            target,
            timeout=900,
        )
    upload_members = verify_tar_artifact(uploads)
    audit_members = verify_tar_artifact(audit)
    scratch = f"tb3-restore-{args.source_role.replace('_', '-')}-{str(inventory_result['campaign_id'])[:8]}"
    # Refuse to replace an unrelated or stale scratch resource.
    existing = subprocess.run(
        [DOCKER, "container", "inspect", scratch],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=10,
        env=SAFE_ENV,
    )
    if existing.returncode == 0:
        raise StagingBackupError("restore-drill container name is already occupied")
    restore = _restore_drill(dump, container=scratch)
    if restore["restored_alembic_revision"] != source_revision:
        raise StagingBackupError("restore drill schema revision differs from source backup")
    if restore["scratch_postgres_system_id"] == source_system_id:
        raise StagingBackupError("restore drill did not use an independent PostgreSQL cluster")
    if restore["database_fingerprint_sha256"] != freeze["postgres"]["database_fingerprint_sha256"]:
        raise StagingBackupError("restored backup fingerprint differs from frozen source")
    running_after = {
        value for value in _run(
            [*prefix, "ps", "--status", "running", "--services"]
        ).splitlines() if value
    }
    if running_after != {"db", "redis"}:
        raise StagingBackupError("legacy staging changed its frozen service state during backup")
    manifest = {
        "schema": "three-site-staging-source-backup-v2",
        "campaign_id": inventory_result["campaign_id"],
        "source_role": args.source_role,
        "source_release_sha": args.expected_source_release_sha,
        "target_release_sha": inventory_result["release_sha"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_postgres_system_id": source_system_id,
        "source_alembic_revision": source_revision,
        "source_freeze_evidence_sha256": freeze_hash,
        "redis_observation": freeze["redis_observation"],
        "artifacts": {
            "postgres": _sha_file(dump),
            "uploads": {**_sha_file(uploads), "safe_member_count": upload_members},
            "audit": {**_sha_file(audit), "safe_member_count": audit_members},
        },
        "restore_drill": restore,
        "redis_restore": False,
        "application_mutation": False,
    }
    manifest_path = args.output_dir / f"{base}.manifest.json"
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        mode=0o600,
    )
    verify_backup_manifest(
        manifest,
        campaign_id=str(inventory_result["campaign_id"]),
        source_role=args.source_role,
        source_release_sha=args.expected_source_release_sha,
        target_release_sha=str(inventory_result["release_sha"]),
        verify_files=True,
    )
    return {
        "status": "backup-and-restore-verified",
        "campaign_id": inventory_result["campaign_id"],
        "source_role": args.source_role,
        "manifest": str(manifest_path),
        "manifest_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "database_fingerprint_sha256": restore["database_fingerprint_sha256"],
        "redis_restore": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-role", choices=sorted(ROLE_APP_SERVICE), required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--project-name", default="trading_bot_staging")
    parser.add_argument("--source-freeze-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--expected-source-release-sha", required=True)
    parser.add_argument("--target-release-sha", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if not SHA_RE.fullmatch(args.expected_source_release_sha) or not SHA_RE.fullmatch(
            args.target_release_sha
        ):
            raise StagingBackupError("source and target releases must be exact 40-hex SHAs")
        inventory_result = verify_approved_inventory(
            load_inventory(args.inventory),
            approval=load_inventory(args.inventory_approval),
            approval_policy=load_inventory(args.approval_policy),
            host_destructive=None,
        )
        if inventory_result["inventory_stage"] != "provisioned":
            raise StagingBackupError("final source backup requires approved provisioned inventory")
        if inventory_result["release_sha"] != args.target_release_sha:
            raise StagingBackupError("target SHA differs from approved planned inventory")
        plan = build_plan(args, inventory_result)
        if not args.apply:
            result = plan
        else:
            if args.confirm != plan["required_confirmation"]:
                raise StagingBackupError("backup confirmation phrase is missing or stale")
            result = execute(args, inventory_result)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
