#!/usr/bin/env python3
"""Create and verify a release-bound Market Pipeline PostgreSQL backup.

The command runs on the web/data host against an already-running, healthy
Market Pipeline database container.  It never starts or changes that database.
The custom-format dump is written atomically under a root-only directory and is
restored into an isolated, unpublished, labelled PostgreSQL container.  The
caller must copy the verified artifact to the bot/authority host before any
migration; this tool records that requirement but does not perform transport.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.prepare_market_pipeline_release import (
        DYNAMIC_VALUES,
        IMAGE_ID,
        RELEASE_SHA,
        ReleaseContractError,
        parse_env_text,
        validate_source,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.prepare_market_pipeline_release import (
        DYNAMIC_VALUES,
        IMAGE_ID,
        RELEASE_SHA,
        ReleaseContractError,
        parse_env_text,
        validate_source,
    )


POSTGRES_IMAGE = (
    "postgres:15-alpine@"
    "sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b"
)
CREATE_CONFIRMATION = "create-production-market-pipeline-archive-backup"
HOT_CREATE_CONFIRMATION = "create-production-market-pipeline-archive-hot-backup"
RECEIPT_SCHEMA = "market_pipeline_backup_restore/1.4"
JOURNAL_SCHEMA = "market_pipeline_backup_journal/1.2"
METADATA_LEGACY_RECEIPT_SCHEMA = "market_pipeline_backup_restore/1.3"
METADATA_LEGACY_JOURNAL_SCHEMA = "market_pipeline_backup_journal/1.1"
LEGACY_RECEIPT_SCHEMA = "market_pipeline_backup_restore/1.2"
LEGACY_JOURNAL_SCHEMA = "market_pipeline_backup_journal/1.0"
# Control documents only; never apply this bound to PostgreSQL dump artifacts.
MAX_HISTORICAL_DOCUMENT_BYTES = 16 * 1024 * 1024
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
SAFE_SQL_ID = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_NAME = re.compile(
    r"^market-archive-before-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.dump$"
)


class BackupError(RuntimeError):
    """A stable, content-free backup refusal."""


@dataclass(frozen=True)
class BackupExecutionLimits:
    """Finite boundaries for an independently supervised backup caller.

    Client timeouts do not cancel Docker daemon work or PostgreSQL backends.
    The owning caller must independently supervise its named resources/backend.
    """

    metadata_timeout_seconds: int
    dump_timeout_seconds: int
    restore_timeout_seconds: int
    dump_max_bytes: int
    minimum_free_bytes: int
    pg_application_name: str
    restore_cpus: int | float
    restore_memory_bytes: int
    restore_pids_limit: int
    daemon_deadline_epoch_seconds: int
    checkpoint: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        bounds = (
            (self.metadata_timeout_seconds, 1, 60),
            (self.dump_timeout_seconds, 1, 1800),
            (self.restore_timeout_seconds, 1, 1800),
            (self.dump_max_bytes, 1, 64 * 1024**3),
            (self.minimum_free_bytes, 1, 1024**4),
            (self.restore_memory_bytes, 128 * 1024**2, 16 * 1024**3),
            (self.restore_pids_limit, 16, 1024),
        )
        if (any(type(value) is not int or not lower <= value <= upper for value, lower, upper in bounds)
                or type(self.restore_cpus) not in (int, float) or not 0 < self.restore_cpus <= 8
                or type(self.pg_application_name) is not str
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", self.pg_application_name)
                or type(self.daemon_deadline_epoch_seconds) is not int
                or not time.time() < self.daemon_deadline_epoch_seconds <= time.time() + 7200
                or (self.checkpoint is not None and not callable(self.checkpoint))):
            raise BackupError("backup_execution_limits_invalid")


def _execution_kwargs(limits: BackupExecutionLimits | None) -> dict[str, Any]:
    return {} if limits is None else {"execution_limits": limits}


@contextmanager
def _postgres_candidate_context(control: Path | None, digest: str | None):
    if control is None and digest is None:
        yield None
        return
    if control is None or digest is None:
        raise BackupError("backup_postgres_candidate_control_required")
    # Lazy: historical/default staged callers retain their two-script contract.
    from scripts.backup_postgres_candidate import bound
    with bound(control, digest, "iran") as candidate:
        candidate.image_check()
        yield candidate


def _candidate_kwargs(candidate: Any) -> dict[str, Any]:
    return {} if candidate is None else {"postgres_candidate": candidate}


def _candidate_inputs(bound: Any, candidate: Any) -> None:
    bound.check()
    if candidate is not None:
        candidate.check()


def _checkpoint(limits: BackupExecutionLimits | None) -> None:
    if limits is not None:
        if time.time() >= limits.daemon_deadline_epoch_seconds:
            raise BackupError("backup_execution_deadline")
        if limits.checkpoint is not None:
            limits.checkpoint()


def _validate_execution_options(
    limits: BackupExecutionLimits | None, permission: bool, *, hot: bool,
) -> None:
    if limits is not None:
        if type(limits) is not BackupExecutionLimits:
            raise BackupError("backup_execution_limits_invalid")
        limits.__post_init__()
    if type(permission) is not bool or (permission and (not hot or limits is None)):
        raise BackupError("backup_primary_source_permission_invalid")


def _run_process(
    arguments: Sequence[str], *, execution_limits: BackupExecutionLimits | None = None,
    timeout_seconds: int | float | None = None, **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    _checkpoint(execution_limits)
    if execution_limits is not None:
        kwargs["timeout"] = (execution_limits.metadata_timeout_seconds
                             if timeout_seconds is None else timeout_seconds)
    try:
        result = subprocess.run(list(arguments), **kwargs)
    except subprocess.TimeoutExpired:
        raise BackupError("backup_command_timeout") from None
    _checkpoint(execution_limits)
    return result


def _exec_prefix(container: str, limits: BackupExecutionLimits | None, *, interactive: bool = False) -> list[str]:
    return ["docker", "exec", *(["-i"] if interactive else []),
            *(["-e", "PGAPPNAME=" + limits.pg_application_name] if limits is not None else []), container]


_DAEMON_GUARD = (
    'command -v date >/dev/null; command -v timeout >/dev/null; '
    'deadline="$1"; stage="$2"; shift 2; now="$(date +%s)"; '
    'remaining=$((deadline - now)); [ "$remaining" -gt 0 ] || exit 124; '
    '[ "$remaining" -le "$stage" ] || remaining="$stage"; '
    'exec timeout -s TERM -k 5 "$remaining" "$@"'
)


def _guarded_arguments(limits: BackupExecutionLimits, arguments: Sequence[str], seconds: int) -> list[str]:
    return ["/bin/sh", "-ec", _DAEMON_GUARD, "backup-deadline",
            str(limits.daemon_deadline_epoch_seconds), str(seconds), *arguments]


def _exec_arguments(container: str, limits: BackupExecutionLimits | None, arguments: Sequence[str], *,
                    interactive: bool = False, timeout_seconds: int | None = None) -> list[str]:
    guarded = (list(arguments) if limits is None else _guarded_arguments(
        limits, arguments, limits.metadata_timeout_seconds if timeout_seconds is None else timeout_seconds))
    return [*_exec_prefix(container, limits, interactive=interactive), *guarded]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def file_digest(path: Path, *, execution_limits: BackupExecutionLimits | None = None) -> str:
    digest = sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024 if execution_limits is None else min(1024 * 1024, execution_limits.dump_max_bytes + 1 - total)):
            _checkpoint(execution_limits)
            total += len(chunk)
            if execution_limits is not None and total > execution_limits.dump_max_bytes:
                raise BackupError("backup_dump_byte_bound")
            digest.update(chunk)
    return digest.hexdigest()


def _secure_directory(path: Path, *, create: bool) -> Path:
    if not path.is_absolute() or path in {Path("/"), Path("/root"), Path("/srv")}:
        raise BackupError("backup_directory_invalid")
    if Path("/tmp") == path or Path("/tmp") in path.parents:
        raise BackupError("backup_directory_tmp_forbidden")
    if "staging" in str(path).lower():
        raise BackupError("backup_directory_staging_forbidden")
    if path.resolve(strict=False) != path:
        raise BackupError("backup_directory_noncanonical")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    try:
        info = path.lstat()
    except OSError as exc:
        raise BackupError("backup_directory_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise BackupError("backup_directory_owner_mode_invalid")
    return path


def _secure_regular(path: Path, *, expected_mode: int = 0o600) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BackupError("backup_file_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != expected_mode
        or info.st_nlink != 1
    ):
        raise BackupError("backup_file_owner_mode_invalid")


def _run_text(arguments: Sequence[str], *, label: str,
              execution_limits: BackupExecutionLimits | None = None) -> str:
    result = _run_process(
        list(arguments),
        **_execution_kwargs(execution_limits),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise BackupError(f"{label}_failed_rc_{result.returncode}")
    return result.stdout.strip()


def _parent_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)


def _file_identity(info: os.stat_result) -> dict[str, int]:
    return {
        "device": info.st_dev, "inode": info.st_ino,
        "uid": info.st_uid, "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode), "nlink": info.st_nlink,
        "size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def _trusted_env_parent(info: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid in {0, os.geteuid()}
        and not stat.S_IMODE(info.st_mode) & 0o022
    )


class BoundRoleEnv:
    """Keep one private Env inode and its canonical parent chain open.

    Only the ordinary validator consumes bounded contents in RAM. Receipts
    record metadata, never a digest of the Env or its parsed values.
    """

    def __init__(self, path: Path):
        self.path = path
        self.descriptors: list[int] = []
        self.parents: list[tuple[Path, int, tuple[int, ...]]] = []
        self.descriptor = -1
        self.binding: dict[str, Any] = {}

    def __enter__(self) -> "BoundRoleEnv":
        try:
            if not self.path.is_absolute():
                raise BackupError("backup_role_env_path_invalid")
            try:
                canonical = self.path.resolve(strict=False)
            except (OSError, RuntimeError):
                # Path.resolve raises RuntimeError for symlink loops on some
                # Python versions. Limit that conversion to this FS boundary.
                raise BackupError("backup_role_env_path_invalid") from None
            if canonical != self.path:
                raise BackupError("backup_role_env_path_invalid")
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            directory = os.open("/", flags)
            self.descriptors.append(directory)
            parent_path = Path("/")
            for component in (None, *self.path.parent.parts[1:]):
                if component is not None:
                    directory = os.open(component, flags, dir_fd=directory)
                    self.descriptors.append(directory)
                    parent_path /= component
                info = os.fstat(directory)
                if not _trusted_env_parent(info):
                    raise BackupError("backup_role_env_parent_invalid")
                self.parents.append((parent_path, directory, _parent_identity(info)))
            self.descriptor = os.open(
                self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory,
            )
            self.descriptors.append(self.descriptor)
            info = os.fstat(self.descriptor)
            if (
                not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or not 0 < info.st_size <= 64 * 1024
            ):
                raise BackupError("backup_role_env_owner_mode_invalid")
            self.binding = {
                "schema": "market_pipeline_role_env_file_binding/1.0",
                "path": str(self.path), **_file_identity(info),
                "parents": [
                    {"path": str(parent), "device": identity[0],
                     "inode": identity[1], "uid": identity[2],
                     "gid": identity[3], "mode": identity[4]}
                    for parent, _, identity in self.parents
                ],
            }
            self.check()
            return self
        except OSError:
            self.close()
            raise BackupError("backup_role_env_unavailable") from None
        except BaseException:
            self.close()
            raise

    def check(self) -> None:
        try:
            for path, descriptor, expected in self.parents:
                if (
                    _parent_identity(os.fstat(descriptor)) != expected
                    or _parent_identity(path.lstat()) != expected
                ):
                    raise BackupError("backup_role_env_parent_drifted")
            info = os.fstat(self.descriptor)
            current = os.stat(
                self.path.name, dir_fd=self.parents[-1][1], follow_symlinks=False
            )
            if (
                not stat.S_ISREG(current.st_mode)
                or _file_identity(info) != {k: self.binding[k] for k in _file_identity(info)}
                or _file_identity(current) != _file_identity(info)
            ):
                raise BackupError("backup_role_env_drifted")
        except OSError as exc:
            raise BackupError("backup_role_env_drifted") from exc

    def values(self) -> dict[str, str]:
        self.check()
        try:
            raw = os.pread(self.descriptor, 64 * 1024 + 1, 0)
            if len(raw) != self.binding["size_bytes"]:
                raise BackupError("backup_role_env_drifted")
            values = parse_env_text(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise BackupError("backup_role_env_read_failed") from exc
        self.check()
        return values

    def close(self) -> None:
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)
        self.descriptors.clear()

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> None:
        try:
            if kind is None:
                self.check()
        finally:
            self.close()


def validate_release_env(
    env_file: Path,
    *,
    release_sha: str,
    image_id: str,
    allow_target_identity_mismatch: bool = False,
    allow_private_primary_source: bool = False,
) -> dict[str, str]:
    with BoundRoleEnv(env_file) as bound:
        return _validate_release_values(
            bound.values(), release_sha=release_sha, image_id=image_id,
            allow_target_identity_mismatch=allow_target_identity_mismatch,
            allow_private_primary_source=allow_private_primary_source,
        )


def _validate_release_values(
    values: dict[str, str], *, release_sha: str, image_id: str,
    allow_target_identity_mismatch: bool = False,
    allow_private_primary_source: bool = False,
) -> dict[str, str]:
    if type(allow_private_primary_source) is not bool:
        raise BackupError("backup_primary_source_permission_invalid")
    source_values = {key: value for key, value in values.items() if key not in DYNAMIC_VALUES}
    validate_source("web", source_values)
    expected = {"MARKET_PIPELINE_MODE": "live"}
    source_lane = (
        values.get("MARKET_PIPELINE_FEED_MODE"),
        values.get("MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY"),
        values.get("MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE"),
    )
    allowed_source_lanes = {
        ("PRIVATE_SHADOW", "0", "PRIVATE_SHADOW"),
    }
    if allow_target_identity_mismatch or allow_private_primary_source:
        # PRIMARY lane permission and identity relaxation are independent.
        # The explicit source permission retains exact release/image checks;
        # the existing Blue/Green mode keeps its original contract. Ordinary
        # callers remain Shadow-only. Neither permission activates a runtime.
        allowed_source_lanes.add(("PRIVATE_PRIMARY", "1", "PRIVATE_PRIMARY"))
    if source_lane not in allowed_source_lanes:
        raise BackupError("backup_release_env_identity_mismatch")
    if allow_private_primary_source:
        if not RELEASE_SHA.fullmatch(values.get("MARKET_PIPELINE_RELEASE_SHA", "")):
            raise BackupError("backup_source_release_sha_invalid")
        if not IMAGE_ID.fullmatch(values.get("MARKET_PIPELINE_IMAGE", "")):
            raise BackupError("backup_source_image_identity_invalid")
    if not allow_target_identity_mismatch:
        expected.update(
            {
                "MARKET_PIPELINE_RELEASE_SHA": release_sha,
                "MARKET_PIPELINE_IMAGE": image_id,
            }
        )
    else:
        if not RELEASE_SHA.fullmatch(values.get("MARKET_PIPELINE_RELEASE_SHA", "")):
            raise BackupError("backup_source_release_sha_invalid")
        if not IMAGE_ID.fullmatch(values.get("MARKET_PIPELINE_IMAGE", "")):
            raise BackupError("backup_source_image_identity_invalid")
    if any(values.get(key) != value for key, value in expected.items()):
        raise BackupError("backup_release_env_identity_mismatch")
    project = values.get("MARKET_PIPELINE_PROJECT_NAME", "")
    user = values.get("MARKET_POSTGRES_USER", "market_data")
    database = values.get("MARKET_POSTGRES_DB", "market_archive")
    if not PROJECT_NAME.fullmatch(project):
        raise BackupError("backup_project_name_invalid")
    if not SAFE_SQL_ID.fullmatch(user) or not SAFE_SQL_ID.fullmatch(database):
        raise BackupError("backup_database_identity_invalid")
    return values


def _postgres_path(values: Mapping[str, str]) -> Path:
    return Path(values["MARKET_WEB_DATA_ROOT"]) / "postgres"


def _initial_store_is_empty(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink() and next(path.iterdir(), None) is None
    except OSError as exc:
        raise BackupError("backup_postgres_root_unavailable") from exc


def _container_ids(*, project: str, postgres_path: Path,
                   execution_limits: BackupExecutionLimits | None = None) -> tuple[str, str]:
    labelled = _run_text(
        [
            "docker", "ps", "-q",
            "--filter", f"label=com.docker.compose.project={project}",
            "--filter", "label=com.docker.compose.service=market-database",
        ],
        label="backup_database_inventory",
        **_execution_kwargs(execution_limits),
    ).splitlines()
    mounted = _run_text(
        ["docker", "ps", "-q", "--filter", f"volume={postgres_path}"],
        label="backup_database_mount_inventory",
        **_execution_kwargs(execution_limits),
    ).splitlines()
    labelled = [item for item in labelled if item]
    mounted = [item for item in mounted if item]
    if len(labelled) != 1 or mounted != labelled:
        raise BackupError("backup_database_single_owner_mismatch")
    return labelled[0], mounted[0]


def _database_invariants(query: Any) -> dict[str, Any]:
    """Return exact, deterministic restore invariants for all market_data data.

    A total table count and one representative fact count can both match after
    data loss.  The receipt therefore binds every table row count, every owned
    sequence position, the ordered migration set, and the schema catalogue.
    """

    table_names = [
        item
        for item in query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='market_data' ORDER BY table_name",
            label="backup_table_names",
        ).splitlines()
        if item
    ]
    if not table_names or len(table_names) != len(set(table_names)) or any(
        not SAFE_SQL_ID.fullmatch(item) for item in table_names
    ):
        raise BackupError("backup_database_table_inventory_invalid")
    table_row_counts = {
        table: int(
            query(
                f"SELECT count(*) FROM market_data.{table}",
                label="backup_table_row_count",
            )
        )
        for table in table_names
    }
    if any(value < 0 for value in table_row_counts.values()):
        raise BackupError("backup_database_table_inventory_invalid")
    sequence_names = [
        item
        for item in query(
            "SELECT sequencename FROM pg_sequences "
            "WHERE schemaname='market_data' ORDER BY sequencename",
            label="backup_sequence_names",
        ).splitlines()
        if item
    ]
    if len(sequence_names) != len(set(sequence_names)) or any(
        not SAFE_SQL_ID.fullmatch(item) for item in sequence_names
    ):
        raise BackupError("backup_database_sequence_inventory_invalid")
    sequence_values: dict[str, dict[str, Any]] = {}
    for name in sequence_names:
        raw = query(
            f"SELECT last_value::text || '|' || is_called::text "
            f"FROM market_data.{name}",
            label="backup_sequence_value",
        )
        try:
            value, is_called = raw.split("|", 1)
        except ValueError as exc:
            raise BackupError("backup_database_sequence_inventory_invalid") from exc
        called = is_called.strip().lower()
        if called not in {"t", "true", "f", "false"}:
            raise BackupError("backup_database_sequence_inventory_invalid")
        sequence_values[name] = {
            "last_value": int(value),
            "is_called": called in {"t", "true"},
        }
    def schema_document(sql: str, *, label: str) -> str:
        # Each query runs in one psql connection with -q: SET must complete
        # before introspection, and the returned setting is checked before
        # hashing. A CTE/cross join does not guarantee this evaluation order.
        raw = query(
            "SET search_path TO pg_catalog;\n"
            "SELECT current_setting('search_path');\n" + sql,
            label=label,
        )
        setting, separator, document = raw.partition("\n")
        if setting != "pg_catalog" or not separator:
            raise BackupError("backup_schema_search_path_invalid")
        try:
            value = json.loads(document)
        except (ValueError, TypeError):
            raise BackupError("backup_schema_catalogue_invalid") from None
        if not isinstance(value, list):
            raise BackupError("backup_schema_catalogue_invalid")
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    catalogue = schema_document(
        "SELECT COALESCE(json_agg(row_to_json(x) ORDER BY "
        "x.table_name,x.ordinal_position)::text,'[]') FROM ("
        "SELECT table_name,ordinal_position,column_name,data_type,is_nullable,"
        "COALESCE(column_default,'') AS column_default "
        "FROM information_schema.columns WHERE table_schema='market_data') x",
        label="backup_schema_catalogue",
    )
    schema_objects = schema_document(
        "SELECT COALESCE(json_agg(row_to_json(x) ORDER BY x.kind,x.identity)::text,'[]') "
        "FROM ("
        "SELECT 'constraint' AS kind, c.oid::regclass::text || ':' || con.conname || ':' || "
        "pg_get_constraintdef(con.oid, true) AS identity "
        "FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='market_data' "
        "UNION ALL SELECT 'index', indexname || ':' || indexdef FROM pg_indexes "
        "WHERE schemaname='market_data' "
        "UNION ALL SELECT 'view', table_name || ':' || view_definition FROM information_schema.views "
        "WHERE table_schema='market_data' "
        "UNION ALL SELECT 'trigger', c.oid::regclass::text || ':' || t.tgname || ':' || "
        "pg_get_triggerdef(t.oid, true) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='market_data' AND NOT t.tgisinternal "
        "UNION ALL SELECT 'function', p.oid::regprocedure::text || ':' || pg_get_functiondef(p.oid) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='market_data') x",
        label="backup_schema_objects",
    )
    return {
        "schema_versions": [
            int(item)
            for item in query(
                "SELECT COALESCE(string_agg(version::text, ',' ORDER BY version),'') "
                "FROM market_data.schema_migrations",
                label="backup_schema_versions",
            ).split(",")
            if item
        ],
        "table_count": len(table_names),
        "fact_count": table_row_counts.get("market_facts", 0),
        "table_row_counts": table_row_counts,
        "sequence_values": sequence_values,
        "schema_catalog_sha256": sha256(catalogue.encode("utf-8")).hexdigest(),
        "schema_objects_sha256": sha256(schema_objects.encode("utf-8")).hexdigest(),
    }


def _running_project_services(project: str, *, execution_limits: BackupExecutionLimits | None = None) -> list[str]:
    output = _run_text(
        [
            "docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
            "--format", '{{.Label "com.docker.compose.service"}}',
        ],
        label="backup_project_workload_inventory",
        **_execution_kwargs(execution_limits),
    )
    return sorted({line for line in output.splitlines() if line})


def _assert_writer_workloads_quiesced(project: str, *, execution_limits: BackupExecutionLimits | None = None) -> None:
    if _running_project_services(project, **_execution_kwargs(execution_limits)) != ["market-database"]:
        raise BackupError("backup_writer_workloads_not_quiesced")


def inspect_source_database(
    values: Mapping[str, str],
    *,
    require_quiesce: bool = True,
    execution_limits: BackupExecutionLimits | None = None,
    postgres_candidate: Any = None,
) -> dict[str, Any]:
    project = values["MARKET_PIPELINE_PROJECT_NAME"]
    running = _running_project_services(project, **_execution_kwargs(execution_limits))
    if require_quiesce:
        _assert_writer_workloads_quiesced(project, **_execution_kwargs(execution_limits))
    elif "market-database" not in running:
        raise BackupError("backup_database_not_running")
    postgres_path = _postgres_path(values)
    container_id, _ = _container_ids(
        project=values["MARKET_PIPELINE_PROJECT_NAME"], postgres_path=postgres_path,
        **_execution_kwargs(execution_limits),
    )
    try:
        document = json.loads(
            _run_text(["docker", "inspect", container_id], label="backup_database_inspect",
                      **_execution_kwargs(execution_limits))
        )[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise BackupError("backup_database_inspect_invalid") from exc
    labels = document.get("Config", {}).get("Labels", {}) or {}
    health = document.get("State", {}).get("Health", {}).get("Status")
    mounts = [
        mount
        for mount in document.get("Mounts", [])
        if mount.get("Destination") == "/var/lib/postgresql/data"
    ]
    if (
        document.get("State", {}).get("Running") is not True
        or health != "healthy"
        or (postgres_candidate is None and document.get("Config", {}).get("Image") != POSTGRES_IMAGE)
        or labels.get("com.docker.compose.project")
        != values["MARKET_PIPELINE_PROJECT_NAME"]
        or labels.get("com.docker.compose.service") != "market-database"
        or len(mounts) != 1
        or Path(str(mounts[0].get("Source") or "")) != postgres_path
    ):
        raise BackupError("backup_database_runtime_identity_mismatch")
    candidate_binding = (None if postgres_candidate is None else postgres_candidate.runtime_check(document))
    user = values.get("MARKET_POSTGRES_USER", "market_data")
    database = values.get("MARKET_POSTGRES_DB", "market_archive")

    def query(sql: str, *, label: str) -> str:
        return _run_text(
            _exec_arguments(container_id, execution_limits, ["psql",
                "-X", "-q", "-v", "ON_ERROR_STOP=1", "-At",
                "-U", user, "-d", database, "-c", sql,
            ]),
            label=label,
            **_execution_kwargs(execution_limits),
        )

    has_migrations = query(
        "SELECT to_regclass('market_data.schema_migrations') IS NOT NULL",
        label="backup_schema_probe",
    ) == "t"
    if not has_migrations:
        raise BackupError("backup_database_schema_unavailable")
    invariants = _database_invariants(query)
    system_identifier = query(
        "SELECT system_identifier FROM pg_control_system()",
        label="backup_system_identifier",
    )
    size_bytes = int(
        query("SELECT pg_database_size(current_database())", label="backup_database_size")
    )
    if not system_identifier.isdigit() or size_bytes <= 0:
        raise BackupError("backup_database_metadata_invalid")
    schema_versions = invariants["schema_versions"]
    if not schema_versions or schema_versions != sorted(set(schema_versions)):
        raise BackupError("backup_database_schema_versions_invalid")
    return {
        "container_id": str(document.get("Id") or container_id),
        "database": database,
        "database_size_bytes": size_bytes,
        "database_identity_sha256": sha256(
            f"web\0{database}\0{system_identifier}".encode("utf-8")
        ).hexdigest(),
        **invariants,
        **({"postgres_candidate": candidate_binding} if candidate_binding is not None else {}),
    }


def _dump_archive_is_valid(*, container_id: str, path: Path,
                           execution_limits: BackupExecutionLimits | None = None,
                           artifact_descriptor: int | None = None) -> bool:
    _secure_regular(path)
    with (path.open("rb") if artifact_descriptor is None
          else os.fdopen(os.dup(artifact_descriptor), "rb")) as stream:
        stream.seek(0)
        result = _run_process(
            _exec_arguments(container_id, execution_limits, ["pg_restore", "--list"], interactive=True,
                            timeout_seconds=None if execution_limits is None else execution_limits.restore_timeout_seconds),
            **_execution_kwargs(execution_limits),
            timeout_seconds=None if execution_limits is None else execution_limits.restore_timeout_seconds,
            stdin=stream,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    return result.returncode == 0


def _check_dump_storage(descriptor: int, count: int, limits: BackupExecutionLimits) -> None:
    space = os.fstatvfs(descriptor)
    if space.f_bavail * space.f_frsize - count < limits.minimum_free_bytes:
        raise BackupError("backup_dump_free_floor")


def _bounded_dump(arguments: list[str], stream: Any, limits: BackupExecutionLimits) -> subprocess.CompletedProcess[Any]:
    _checkpoint(limits)
    _check_dump_storage(stream.fileno(), 0, limits)
    child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + limits.dump_timeout_seconds
    total = 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                _checkpoint(limits)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackupError("backup_pg_dump_timeout")
                for key, _ in selector.select(min(.25, remaining)):
                    chunk = os.read(key.fileobj.fileno(), min(65536, limits.dump_max_bytes + 1 - total))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > limits.dump_max_bytes:
                        raise BackupError("backup_dump_byte_bound")
                    stream.flush()
                    _check_dump_storage(stream.fileno(), len(chunk), limits)
                    stream.write(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BackupError("backup_pg_dump_timeout")
            try:
                status = child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise BackupError("backup_pg_dump_timeout") from None
        _checkpoint(limits)
        return subprocess.CompletedProcess(arguments, status)
    finally:
        # This only bounds the local Docker client. The owning independent
        # watchdog must cancel the exact source backend and daemon resources.
        try:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=limits.metadata_timeout_seconds)
        except subprocess.TimeoutExpired:
            raise BackupError("backup_dump_client_cleanup_timeout") from None
        finally:
            if child.stdout is not None:
                child.stdout.close()


def _write_dump(
    *, container_id: str, user: str, database: str, destination: Path,
    execution_limits: BackupExecutionLimits | None = None,
) -> None:
    candidate = destination.parent / f".{destination.name}.pending"
    if candidate.exists() or candidate.is_symlink():
        _secure_regular(candidate)
        if execution_limits is not None and candidate.stat().st_size > execution_limits.dump_max_bytes:
            raise BackupError("backup_dump_byte_bound")
        if destination.exists() or destination.is_symlink():
            raise BackupError("backup_dump_resume_ambiguous")
        # A process can be killed while pg_dump is still writing.  Presence of
        # the deterministic candidate alone is therefore not evidence that it
        # is complete.  pg_restore's archive parser is the authoritative local
        # completeness check; only a complete archive is promoted.
        if _dump_archive_is_valid(container_id=container_id, path=candidate, **_execution_kwargs(execution_limits)):
            os.replace(candidate, destination)
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return
        # The name is not discovered or guessed: it is the exact candidate
        # bound by the durable journal.  An invalid partial write is safe to
        # discard and recreate from the still-quiesced source.
        candidate.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            dump_arguments = ["pg_dump",
                    "-U", user, "-d", database, "-Fc",
                    "--no-owner", "--no-privileges",
                ]
            arguments = _exec_arguments(container_id, execution_limits, dump_arguments,
                                        timeout_seconds=None if execution_limits is None else execution_limits.dump_timeout_seconds)
            process = (_bounded_dump(arguments, stream, execution_limits) if execution_limits is not None
                       else subprocess.run(arguments, stdout=stream, stderr=subprocess.PIPE, check=False))
            stream.flush()
            os.fsync(stream.fileno())
        if process.returncode:
            raise BackupError(f"backup_pg_dump_failed_rc_{process.returncode}")
        if candidate.stat().st_size <= 0:
            raise BackupError("backup_pg_dump_empty")
        if not _dump_archive_is_valid(container_id=container_id, path=candidate, **_execution_kwargs(execution_limits)):
            raise BackupError("backup_pg_dump_archive_invalid")
        os.replace(candidate, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


def _restore_query(container: str, sql: str, *, execution_limits: BackupExecutionLimits | None = None) -> str:
    return _run_text(
        _exec_arguments(container, execution_limits, ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1",
            "-At", "-U", "restore", "-d", "restore", "-c", sql,
        ]),
        label="backup_restore_query",
        **_execution_kwargs(execution_limits),
    )


def _known_restore_absence(kind: str, name: str, result: subprocess.CompletedProcess[Any]) -> bool:
    messages = {
        f"Error: No such {kind}: {name}",
        f"Error response from daemon: No such {kind}: {name}",
    }
    if kind == "container":
        messages.add(f"Error: No such object: {name}")
    elif kind == "volume":
        messages.add(f"Error response from daemon: get {name}: no such volume")
    return result.returncode == 1 and type(result.stderr) is str and result.stderr.strip() in messages


def _assert_restore_resource_absent(kind: str, name: str, *, execution_limits: BackupExecutionLimits | None = None) -> None:
    result = _run_process(
        ["docker", kind, "inspect", name],
        **_execution_kwargs(execution_limits),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL if execution_limits is None else subprocess.PIPE,
        **({"text": True} if execution_limits is not None else {}),
        check=False,
    )
    if result.returncode == 0:
        raise BackupError(f"backup_restore_{kind}_cleanup_incomplete")
    if execution_limits is not None and not _known_restore_absence(kind, name, result):
        raise BackupError(f"backup_restore_{kind}_absence_unknown")


def _cleanup_owned_restore_resource(kind: str, name: str, label: str, *, execution_limits: BackupExecutionLimits | None = None) -> None:
    template = (
        '{{index .Config.Labels "io.gold-trade.market-backup-run"}}'
        if kind == "container"
        else '{{index .Labels "io.gold-trade.market-backup-run"}}'
    )
    result = _run_process(
        ["docker", kind, "inspect", "-f", template, name],
        **_execution_kwargs(execution_limits),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if execution_limits is None else subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        if execution_limits is not None and not _known_restore_absence(kind, name, result):
            raise BackupError(f"backup_restore_{kind}_absence_unknown")
        return
    if result.stdout.strip() != label:
        raise BackupError(f"backup_restore_{kind}_owner_mismatch")
    command = (
        ["docker", "rm", "-f", name]
        if kind == "container"
        else ["docker", "volume", "rm", name]
    )
    _run_text(command, label=f"backup_restore_{kind}_recover", **_execution_kwargs(execution_limits))
    _assert_restore_resource_absent(kind, name, **_execution_kwargs(execution_limits))


def _valid_restore_resources(binding: Any) -> bool:
    if (not isinstance(binding, Mapping) or set(binding) != {"container", "volume", "label"}
            or any(type(value) is not str for value in binding.values())):
        return False
    nonce = binding["container"].removeprefix("market_pipeline_restore_")
    return bool(
        re.fullmatch(r"[0-9a-f]{16}", nonce)
        and binding["container"] == f"market_pipeline_restore_{nonce}"
        and binding["volume"] == f"market_pipeline_restore_{nonce}_data"
        and binding["label"] == f"market-pipeline-backup-{nonce}"
    )


def restore_smoke(
    artifact: Path,
    source: Mapping[str, Any],
    *,
    resource_binding: Mapping[str, str] | None = None,
    before_cleanup: Callable[[str], None] | None = None,
    artifact_descriptor: int | None = None,
    execution_limits: BackupExecutionLimits | None = None,
    postgres_candidate: Any = None,
) -> dict[str, Any]:
    _validate_execution_options(execution_limits, False, hot=True)
    if source.get("postgres_candidate") is not None and postgres_candidate is None:
        raise BackupError("backup_postgres_candidate_control_required")
    if postgres_candidate is not None:
        postgres_candidate.image_check()
    if resource_binding is None:
        nonce = secrets.token_hex(8)
        resource_binding = {
            "container": f"market_pipeline_restore_{nonce}",
            "volume": f"market_pipeline_restore_{nonce}_data",
            "label": f"market-pipeline-backup-{nonce}",
        }
    if not _valid_restore_resources(resource_binding):
        raise BackupError("backup_restore_resource_binding_invalid")
    container = str(resource_binding["container"])
    volume = str(resource_binding["volume"])
    label = str(resource_binding["label"])
    _cleanup_owned_restore_resource("container", container, label, **_execution_kwargs(execution_limits))
    _cleanup_owned_restore_resource("volume", volume, label, **_execution_kwargs(execution_limits))
    created_volume = False
    created_container = False
    cleanup_error: BackupError | None = None
    try:
        _run_text(
            [
                "docker", "volume", "create", "--label",
                f"io.gold-trade.market-backup-run={label}", volume,
            ],
            label="backup_restore_volume_create",
            **_execution_kwargs(execution_limits),
        )
        created_volume = True
        _run_text(
            [
                "docker", "run", "-d", "--name", container,
                *(["--cpus", str(execution_limits.restore_cpus),
                   "--memory", str(execution_limits.restore_memory_bytes),
                   "--memory-swap", str(execution_limits.restore_memory_bytes),
                   "--pids-limit", str(execution_limits.restore_pids_limit)] if execution_limits is not None else []),
                "--label", f"io.gold-trade.market-backup-run={label}",
                "--network", "none",
                "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
                "-e", "POSTGRES_USER=restore",
                "-e", "POSTGRES_DB=restore",
                "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
                *(["--entrypoint", "/bin/sh"] if execution_limits is not None else []),
                *(["--pull", "never"] if postgres_candidate is not None else []),
                POSTGRES_IMAGE if postgres_candidate is None else postgres_candidate.image,
                *(_guarded_arguments(execution_limits, ["/usr/local/bin/docker-entrypoint.sh", "postgres"],
                                     execution_limits.restore_timeout_seconds)[1:] if execution_limits is not None else []),
            ],
            label="backup_restore_container_start",
            **_execution_kwargs(execution_limits),
        )
        created_container = True
        if postgres_candidate is not None:
            restored_document = json.loads(_run_text(["docker", "inspect", container],
                label="backup_candidate_restore_inspect", **_execution_kwargs(execution_limits)))[0]
            postgres_candidate.runtime_check(restored_document, restore=True)
        ready = False
        consecutive = 0
        for _attempt in range(90):
            ready_probe = _run_process(
                _exec_arguments(container, execution_limits, ["pg_isready", "-U", "restore", "-d", "restore"]),
                **_execution_kwargs(execution_limits),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            sql_probe = _run_process(
                _exec_arguments(container, execution_limits, ["psql",
                    "-X", "-v", "ON_ERROR_STOP=1", "-At",
                    "-U", "restore", "-d", "restore", "-c", "SELECT 1",
                ]),
                **_execution_kwargs(execution_limits),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                text=True,
            )
            if ready_probe.returncode == 0 and sql_probe.returncode == 0 and sql_probe.stdout.strip() == "1":
                consecutive += 1
                if consecutive >= 2:
                    ready = True
                    break
            else:
                consecutive = 0
            time.sleep(1)
        if not ready:
            raise BackupError("backup_restore_not_ready")
        result = None
        for attempt in range(2):
            # HOT verification restores the same retained inode it hashed.
            stream = (artifact.open("rb") if artifact_descriptor is None
                      else os.fdopen(os.dup(artifact_descriptor), "rb"))
            with stream:
                stream.seek(0)
                result = _run_process(
                    _exec_arguments(container, execution_limits, ["pg_restore",
                        "-U", "restore", "-d", "restore",
                        "--exit-on-error", "--no-owner", "--no-privileges",
                    ], interactive=True,
                        timeout_seconds=None if execution_limits is None else execution_limits.restore_timeout_seconds),
                    **_execution_kwargs(execution_limits),
                    timeout_seconds=None if execution_limits is None else execution_limits.restore_timeout_seconds,
                    stdin=stream,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            if result.returncode == 0:
                break
            stderr_text = (result.stderr or b"").decode("utf-8", "replace").lower()
            if attempt == 0 and "no such file or directory" in stderr_text:
                time.sleep(2)
                continue
            raise BackupError(f"backup_restore_failed_rc_{result.returncode}")
        if result is None or result.returncode:
            raise BackupError("backup_restore_failed")
        def restore_query(sql: str, *, label: str) -> str:
            del label
            return _restore_query(container, sql, **_execution_kwargs(execution_limits))

        restored = _database_invariants(restore_query)
        # pg_dump owns a transactionally consistent snapshot while the live
        # source may continue receiving facts.  The restored dump is therefore
        # the authoritative row/sequence inventory.  The migration set must
        # still match the pre-dump source observation; a concurrent schema
        # transition is not safe to accept.
        if not restored["schema_versions"]:
            raise BackupError("backup_restore_schema_missing")
        if restored["schema_versions"] != source["schema_versions"]:
            raise BackupError("backup_restore_reconciliation_mismatch")
        if before_cleanup is not None:
            before_cleanup(container)
        if postgres_candidate is not None:
            postgres_candidate.check()
        return {"status": "PASS", **restored, "cleanup_status": "PASS",
                **({"postgres_restore_candidate": postgres_candidate.summary()} if postgres_candidate is not None else {})}
    finally:
        try:
            if created_container:
                owner = _run_text(
                    [
                        "docker", "inspect", "-f",
                        '{{index .Config.Labels "io.gold-trade.market-backup-run"}}',
                        container,
                    ],
                    label="backup_restore_container_owner",
                    **_execution_kwargs(execution_limits),
                )
                if owner != label:
                    raise BackupError("backup_restore_container_owner_mismatch")
                _run_text(["docker", "rm", "-f", container], label="backup_restore_container_remove",
                          **_execution_kwargs(execution_limits))
            if created_volume:
                owner = _run_text(
                    [
                        "docker", "volume", "inspect", "-f",
                        '{{index .Labels "io.gold-trade.market-backup-run"}}', volume,
                    ],
                    label="backup_restore_volume_owner",
                    **_execution_kwargs(execution_limits),
                )
                if owner != label:
                    raise BackupError("backup_restore_volume_owner_mismatch")
                _run_text(["docker", "volume", "rm", volume], label="backup_restore_volume_remove",
                          **_execution_kwargs(execution_limits))
            if created_container:
                _assert_restore_resource_absent("container", container, **_execution_kwargs(execution_limits))
            if created_volume:
                _assert_restore_resource_absent("volume", volume, **_execution_kwargs(execution_limits))
        except BackupError as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            raise cleanup_error


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    _secure_directory(path.parent, create=False)
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags, 0o600)
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


def _journal_path(root: Path) -> Path:
    return root / "market-pipeline-backup-journal.json"


def _read_journal(path: Path, *, historical: bool = False) -> dict[str, Any]:
    _secure_regular(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup_journal_invalid") from exc
    if isinstance(payload, dict) and payload.get("schema") == LEGACY_JOURNAL_SCHEMA:
        if historical:
            return payload
        raise BackupError("backup_role_env_binding_migration_required")
    if isinstance(payload, dict) and payload.get("schema") == METADATA_LEGACY_JOURNAL_SCHEMA:
        if historical:
            return payload
        raise BackupError("backup_mode_migration_required")
    if not isinstance(payload, dict) or payload.get("schema") != JOURNAL_SCHEMA:
        raise BackupError("backup_journal_invalid")
    return payload


def _write_journal(path: Path, payload: Mapping[str, Any]) -> None:
    _write_receipt(path, payload)


def _invariant_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_versions",
        "table_count",
        "fact_count",
        "table_row_counts",
        "sequence_values",
        "schema_catalog_sha256",
        "schema_objects_sha256",
    )
    return {key: payload.get(key) for key in keys}


def _same_database_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left.get("database_identity_sha256")
        and left.get("database_identity_sha256") == right.get("database_identity_sha256")
        and left.get("container_id") == right.get("container_id")
        and left.get("database") == right.get("database")
        and left.get("postgres_candidate") == right.get("postgres_candidate")
    )


def _same_schema_window(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left.get("schema_versions") == right.get("schema_versions")
        and left.get("schema_catalog_sha256") == right.get("schema_catalog_sha256")
        and left.get("schema_objects_sha256") == right.get("schema_objects_sha256")
        and bool(left.get("schema_versions"))
        and HEX64.fullmatch(str(left.get("schema_catalog_sha256") or ""))
        and HEX64.fullmatch(str(left.get("schema_objects_sha256") or ""))
    )


def _valid_invariants(payload: Mapping[str, Any]) -> bool:
    versions = payload.get("schema_versions")
    tables = payload.get("table_row_counts")
    sequences = payload.get("sequence_values")
    return (
        isinstance(versions, list) and bool(versions)
        and all(type(item) is int and item > 0 for item in versions)
        and versions == sorted(set(versions))
        and type(payload.get("table_count")) is int and payload["table_count"] > 0
        and type(payload.get("fact_count")) is int and payload["fact_count"] >= 0
        and isinstance(tables, dict) and len(tables) == payload["table_count"]
        and tables.get("market_facts", 0) == payload["fact_count"]
        and all(SAFE_SQL_ID.fullmatch(str(key)) and type(value) is int and value >= 0
                for key, value in tables.items())
        and isinstance(sequences, dict)
        and all(SAFE_SQL_ID.fullmatch(str(key)) and isinstance(value, dict)
                and set(value) == {"last_value", "is_called"}
                and type(value["last_value"]) is int and type(value["is_called"]) is bool
                for key, value in sequences.items())
        and type(payload.get("schema_catalog_sha256")) is str
        and bool(HEX64.fullmatch(payload["schema_catalog_sha256"]))
        and type(payload.get("schema_objects_sha256")) is str
        and bool(HEX64.fullmatch(payload["schema_objects_sha256"]))
    )


def _valid_source_metadata(payload: Mapping[str, Any]) -> bool:
    return (
        type(payload.get("container_id")) is str
        and bool(re.fullmatch(r"[0-9a-f]{12,64}", payload["container_id"]))
        and type(payload.get("database")) is str
        and bool(SAFE_SQL_ID.fullmatch(payload["database"]))
        and type(payload.get("database_identity_sha256")) is str
        and bool(HEX64.fullmatch(payload["database_identity_sha256"]))
        and type(payload.get("database_size_bytes")) is int
        and payload["database_size_bytes"] > 0
        and _valid_invariants(payload)
    )


def _source_window_compatible(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    allow_running_writers: bool,
) -> bool:
    """Quiesced backups require byte-equal inventories; hot backups allow row drift."""

    if not _same_database_identity(before, after) or not _same_schema_window(before, after):
        return False
    if allow_running_writers:
        return True
    return _invariant_view(before) == _invariant_view(after)


def _row_inventory_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("schema_versions", "table_count", "fact_count", "table_row_counts", "sequence_values")
    return {key: payload.get(key) for key in keys}


def _restore_window_compatible(
    source: Mapping[str, Any],
    source_after: Mapping[str, Any],
    restore: Mapping[str, Any],
    *,
    allow_running_writers: bool,
) -> bool:
    if not _source_window_compatible(
        source, source_after, allow_running_writers=allow_running_writers
    ):
        return False
    if not _same_schema_window(source, restore):
        return False
    if (set(source.get("table_row_counts", {})) != set(restore.get("table_row_counts", {}))
            or set(source.get("sequence_values", {})) != set(restore.get("sequence_values", {}))):
        return False
    if allow_running_writers:
        return True
    return _row_inventory_view(source) == _row_inventory_view(restore)


def _empty_prepared_journal(journal: Mapping[str, Any], *, root: Path) -> bool:
    if journal.get("status") != "PREPARED" or journal.get("backup") is not None:
        return False
    artifact = Path(str(journal.get("artifact_path") or ""))
    candidate = Path(str(journal.get("candidate_path") or ""))
    if artifact.parent != root or candidate.parent != root:
        return False
    return not artifact.exists() and not candidate.exists() and not artifact.is_symlink() and not candidate.is_symlink()


def _abandon_empty_prepared_journal(path: Path, journal: Mapping[str, Any], *, root: Path) -> None:
    if not _empty_prepared_journal(journal, root=root):
        raise BackupError("backup_journal_not_abandonable")
    path.unlink()
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _same_env_binding(observed: Any, expected: Any) -> bool:
    """Require exact JSON types, so booleans cannot impersonate stat integers."""
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            _same_env_binding(observed[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _same_env_binding(left, right) for left, right in zip(observed, expected)
        )
    return observed == expected


def _validate_journal_identity(
    payload: Mapping[str, Any],
    *,
    root: Path,
    role_env_binding: Mapping[str, Any],
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    historical: bool = False,
    backup_mode: str | None = None,
) -> None:
    old_digest = historical and payload.get("schema") == LEGACY_JOURNAL_SCHEMA
    env_key = "role_env_sha256" if old_digest else "role_env_binding"
    expected = {
        "schema": (LEGACY_JOURNAL_SCHEMA if old_digest else METADATA_LEGACY_JOURNAL_SCHEMA) if historical else JOURNAL_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        env_key: role_env_binding[env_key],
        "receipt_path": str(receipt),
        "secrets_disclosed": False,
    }
    if not _same_env_binding({key: payload.get(key) for key in expected}, expected):
        raise BackupError("backup_journal_identity_mismatch")
    journal_keys = {
        "schema", "status", "backup_status", "run_id", "created_at_utc",
        "release_sha", "release_tree", "image_id", "image_input_signature",
        env_key, "receipt_path", "artifact_path", "candidate_path",
        "source_before", "source_after", "backup", "restore_smoke",
        "restore_resources", "secrets_disclosed",
    }
    if not historical:
        journal_keys.add("backup_mode")
        if type(payload.get("backup_mode")) is not str or payload["backup_mode"] not in {"hot", "quiesced"} or (
            backup_mode is not None and payload.get("backup_mode") != backup_mode
        ):
            raise BackupError("backup_mode_mismatch")
    if (
        set(payload) != journal_keys
        or type(payload.get("status")) is not str
        or payload["status"] not in {"PREPARED", "DUMP_READY", "RESTORE_RUNNING", "COMPLETE"}
        or not re.fullmatch(r"[0-9a-f]{16}", str(payload.get("run_id") or ""))
    ):
        raise BackupError("backup_journal_schema_invalid")
    artifact = Path(str(payload.get("artifact_path") or ""))
    candidate = Path(str(payload.get("candidate_path") or ""))
    if (
        artifact.parent != root
        or not ARTIFACT_NAME.fullmatch(artifact.name)
        or candidate != root / f".{artifact.name}.pending"
    ):
        raise BackupError("backup_journal_artifact_invalid")
    resources = payload.get("restore_resources")
    if resources is not None and not _valid_restore_resources(resources):
        raise BackupError("backup_journal_resource_binding_invalid")


def _receipt_from_journal(journal: Mapping[str, Any]) -> dict[str, Any]:
    status = journal.get("backup_status")
    if status == "INITIAL_EMPTY":
        source: Mapping[str, Any] = {"database_initialized": False}
        source_after: Mapping[str, Any] = {"database_initialized": False}
        backup_payload = None
        restore: Mapping[str, Any] = {"status": "NOT_APPLICABLE"}
        offhost = False
    elif status == "PASS":
        source = journal["source_before"]
        source_after = journal["source_after"]
        backup_payload = journal["backup"]
        restore = journal["restore_smoke"]
        offhost = True
    else:
        raise BackupError("backup_journal_status_invalid")
    return {
        "schema": {LEGACY_JOURNAL_SCHEMA: LEGACY_RECEIPT_SCHEMA,
                   METADATA_LEGACY_JOURNAL_SCHEMA: METADATA_LEGACY_RECEIPT_SCHEMA,
                   JOURNAL_SCHEMA: RECEIPT_SCHEMA}[journal["schema"]],
        **({"backup_mode": journal["backup_mode"]} if journal["schema"] == JOURNAL_SCHEMA else {}),
        "status": status,
        "created_at_utc": journal["created_at_utc"],
        "release_sha": journal["release_sha"],
        "release_tree": journal["release_tree"],
        "image_id": journal["image_id"],
        "image_input_signature": journal["image_input_signature"],
        **({"role_env_sha256": journal["role_env_sha256"]} if journal["schema"] == LEGACY_JOURNAL_SCHEMA else {"role_env_binding": journal["role_env_binding"]}),
        "backup_run_id": journal["run_id"],
        "source": source,
        "source_after": source_after,
        "backup": backup_payload,
        "restore_smoke": restore,
        "off_host_copy_required": offhost,
        "database_mutated": False,
        "services_started": False,
        "secrets_disclosed": False,
    }


def _create_backup_bound(
    *,
    env_file: Path,
    _env_binding: BoundRoleEnv,
    backup_dir: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    refresh_complete: bool = False,
    allow_running_writers: bool = False,
    allow_target_identity_mismatch: bool = False,
    allow_private_primary_source: bool = False,
    execution_limits: BackupExecutionLimits | None = None,
    restore_resource_binding: Mapping[str, str] | None = None,
    postgres_candidate: Any = None,
) -> dict[str, Any]:
    values = _validate_release_values(
        _env_binding.values(),
        release_sha=release_sha,
        image_id=image_id,
        allow_target_identity_mismatch=allow_target_identity_mismatch,
        allow_private_primary_source=allow_private_primary_source,
    )
    if allow_private_primary_source and (
        values.get("MARKET_PIPELINE_RELEASE_SHA") != release_sha or values.get("MARKET_PIPELINE_IMAGE") != image_id
    ):
        raise BackupError("backup_hot_source_identity_mismatch")
    require_quiesce = not allow_running_writers
    backup_mode = "hot" if allow_running_writers else "quiesced"
    root = _secure_directory(backup_dir, create=True)
    if receipt.parent != root or receipt.name != "market-pipeline-backup-receipt.json":
        raise BackupError("backup_receipt_destination_invalid")
    postgres = _postgres_path(values)
    if root == postgres or root in postgres.parents or postgres in root.parents:
        raise BackupError("backup_database_directory_overlap")
    journal_path = _journal_path(root)
    journal: dict[str, Any] | None = None
    if journal_path.exists() or journal_path.is_symlink():
        journal = _read_journal(journal_path)
    if journal is not None:
        _validate_journal_identity(
            journal,
            root=root,
            role_env_binding={"role_env_binding": _env_binding.binding},
            receipt=receipt,
            release_sha=release_sha,
            release_tree=release_tree,
            image_id=image_id,
            image_input_signature=image_input_signature,
            backup_mode=backup_mode,
        )
        if restore_resource_binding is not None and journal.get("restore_resources") != restore_resource_binding:
            raise BackupError("backup_restore_resource_binding_mismatch")
        if allow_running_writers and _empty_prepared_journal(journal, root=root):
            _candidate_inputs(_env_binding, postgres_candidate)
            _abandon_empty_prepared_journal(journal_path, journal, root=root)
            journal = None
    if journal is not None:
        if postgres_candidate is not None:
            actual = inspect_source_database(values, require_quiesce=require_quiesce,
                **_execution_kwargs(execution_limits), **_candidate_kwargs(postgres_candidate))
            if not _source_window_compatible(journal["source_before"], actual,
                                             allow_running_writers=allow_running_writers):
                raise BackupError("backup_postgres_candidate_journal_mismatch")
        if journal.get("status") == "COMPLETE":
            payload = _receipt_from_journal(journal)
            if not refresh_complete:
                _candidate_inputs(_env_binding, postgres_candidate)
                _write_receipt(receipt, payload)
                return payload
            run_id = str(journal.get("run_id") or "")
            if not re.fullmatch(r"[0-9a-f]{16}", run_id):
                raise BackupError("backup_journal_invalid")
            archived_receipt = root / f"market-pipeline-backup-receipt.{run_id}.json"
            archived_journal = root / f"market-pipeline-backup-journal.{run_id}.json"
            if receipt.exists() or receipt.is_symlink():
                _secure_regular(receipt)
                try:
                    current_payload = json.loads(receipt.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BackupError("backup_refresh_receipt_drift") from exc
                if current_payload != payload:
                    raise BackupError("backup_refresh_receipt_drift")
            elif not (archived_receipt.exists() or archived_receipt.is_symlink()):
                raise BackupError("backup_refresh_receipt_missing")
            if archived_journal.exists() or archived_journal.is_symlink():
                raise BackupError("backup_refresh_archive_exists")
            if archived_receipt.exists() or archived_receipt.is_symlink():
                _secure_regular(archived_receipt)
                try:
                    archived_payload = json.loads(
                        archived_receipt.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BackupError("backup_refresh_archive_invalid") from exc
                if archived_payload != payload or receipt.exists() or receipt.is_symlink():
                    raise BackupError("backup_refresh_archive_invalid")
            else:
                _candidate_inputs(_env_binding, postgres_candidate)
                os.replace(receipt, archived_receipt)
                directory = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            # If a kill happened after archiving the receipt but before this
            # replace, the exact archived receipt above authorizes resumption.
            _candidate_inputs(_env_binding, postgres_candidate)
            os.replace(journal_path, archived_journal)
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            journal = None
    created_at = utc_now()
    if not (postgres / "pgdata" / "PG_VERSION").is_file():
        if _running_project_services(values["MARKET_PIPELINE_PROJECT_NAME"], **_execution_kwargs(execution_limits)):
            raise BackupError("backup_writer_workloads_not_quiesced")
        if not _initial_store_is_empty(postgres):
            raise BackupError("backup_uninitialized_store_not_empty")
        if journal is None:
            nonce = secrets.token_hex(8)
            stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
            artifact = root / (
                f"market-archive-before-{release_sha[:12]}-{stamp}-{nonce[:8]}.dump"
            )
            journal = {
                "schema": JOURNAL_SCHEMA,
                "backup_mode": backup_mode,
                "status": "COMPLETE",
                "backup_status": "INITIAL_EMPTY",
                "run_id": nonce,
                "created_at_utc": utc_text(created_at),
                "release_sha": release_sha,
                "release_tree": release_tree,
                "image_id": image_id,
                "image_input_signature": image_input_signature,
                "role_env_binding": dict(_env_binding.binding),
                "receipt_path": str(receipt),
                "artifact_path": str(artifact),
                "candidate_path": str(root / f".{artifact.name}.pending"),
                "source_before": {"database_initialized": False},
                "source_after": {"database_initialized": False},
                "backup": None,
                "restore_smoke": {"status": "NOT_APPLICABLE"},
                "restore_resources": None,
                "secrets_disclosed": False,
            }
            _candidate_inputs(_env_binding, postgres_candidate)
            _write_journal(journal_path, journal)
        payload = _receipt_from_journal(journal)
        _candidate_inputs(_env_binding, postgres_candidate)
        _write_receipt(receipt, payload)
        return payload

    if journal is None:
        source = inspect_source_database(values, require_quiesce=require_quiesce, **_execution_kwargs(execution_limits), **_candidate_kwargs(postgres_candidate))
        nonce = secrets.token_hex(8)
        stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        restore_nonce = secrets.token_hex(8)
        artifact = root / (
            f"market-archive-before-{release_sha[:12]}-{stamp}-{nonce[:8]}.dump"
        )
        journal = {
            "schema": JOURNAL_SCHEMA,
            "backup_mode": backup_mode,
            "status": "PREPARED",
            "backup_status": None,
            "run_id": nonce,
            "created_at_utc": utc_text(created_at),
            "release_sha": release_sha,
            "release_tree": release_tree,
            "image_id": image_id,
            "image_input_signature": image_input_signature,
            "role_env_binding": dict(_env_binding.binding),
            "receipt_path": str(receipt),
            "artifact_path": str(artifact),
            "candidate_path": str(root / f".{artifact.name}.pending"),
            "source_before": source,
            "source_after": None,
            "backup": None,
            "restore_smoke": None,
            "restore_resources": dict(restore_resource_binding) if restore_resource_binding is not None else {
                "container": f"market_pipeline_restore_{restore_nonce}",
                "volume": f"market_pipeline_restore_{restore_nonce}_data",
                "label": f"market-pipeline-backup-{restore_nonce}",
            },
            "secrets_disclosed": False,
        }
        _candidate_inputs(_env_binding, postgres_candidate)
        _write_journal(journal_path, journal)
    source = journal["source_before"]
    current_source = inspect_source_database(values, require_quiesce=require_quiesce, **_execution_kwargs(execution_limits), **_candidate_kwargs(postgres_candidate))
    _candidate_inputs(_env_binding, postgres_candidate)
    if not _source_window_compatible(
        source, current_source, allow_running_writers=allow_running_writers
    ):
        raise BackupError("backup_source_changed_before_dump")
    artifact = Path(str(journal["artifact_path"]))
    if not artifact.exists():
        _candidate_inputs(_env_binding, postgres_candidate)
        _write_dump(
            container_id=str(source["container_id"]),
            user=values.get("MARKET_POSTGRES_USER", "market_data"),
            database=values.get("MARKET_POSTGRES_DB", "market_archive"),
            destination=artifact,
            **_execution_kwargs(execution_limits),
        )
    _candidate_inputs(_env_binding, postgres_candidate)
    _secure_regular(artifact)
    if execution_limits is not None and artifact.stat().st_size > execution_limits.dump_max_bytes:
        raise BackupError("backup_dump_byte_bound")
    if not _dump_archive_is_valid(
        container_id=str(source["container_id"]), path=artifact, **_execution_kwargs(execution_limits)
    ):
        raise BackupError("backup_artifact_archive_invalid")
    observed_backup = {
        "path": str(artifact),
        "sha256": file_digest(artifact, **_execution_kwargs(execution_limits)),
        "size_bytes": artifact.stat().st_size,
        "format": "postgres_custom",
    }
    if journal.get("backup") is not None and journal["backup"] != observed_backup:
        raise BackupError("backup_artifact_drifted_during_resume")
    pinned = (_pinned_backup_artifact(artifact, observed_backup, **_execution_kwargs(execution_limits))
              if execution_limits is not None or postgres_candidate is not None else nullcontext(None))
    with pinned as descriptor:
        identity = None if descriptor is None else _file_identity(os.fstat(descriptor))
        def check_artifact() -> None:
            _checkpoint(execution_limits)
            if descriptor is not None and (
                _file_identity(os.fstat(descriptor)) != identity or _file_identity(artifact.lstat()) != identity
            ):
                raise BackupError("backup_artifact_drifted")
        journal["backup"] = observed_backup
        journal["status"] = "DUMP_READY"
        _candidate_inputs(_env_binding, postgres_candidate)
        _write_journal(journal_path, journal)
        source_after = inspect_source_database(values, require_quiesce=require_quiesce, **_execution_kwargs(execution_limits), **_candidate_kwargs(postgres_candidate))
        _candidate_inputs(_env_binding, postgres_candidate)
        if not _source_window_compatible(
            source, source_after, allow_running_writers=allow_running_writers
        ):
            raise BackupError("backup_source_changed_during_dump")
        journal["source_after"] = source_after
        journal["status"] = "RESTORE_RUNNING"
        _candidate_inputs(_env_binding, postgres_candidate)
        _write_journal(journal_path, journal)
        _candidate_inputs(_env_binding, postgres_candidate)
        restore = restore_smoke(
            artifact, source, resource_binding=journal["restore_resources"],
            **_candidate_kwargs(postgres_candidate),
            **({"artifact_descriptor": descriptor} if descriptor is not None else {}),
            **_execution_kwargs(execution_limits),
        )
        _candidate_inputs(_env_binding, postgres_candidate)
        check_artifact()
        if not _restore_window_compatible(
            source, source_after, restore, allow_running_writers=allow_running_writers,
        ):
            raise BackupError("backup_restore_reconciliation_mismatch")
        journal["restore_smoke"] = restore
        journal["backup_status"] = "PASS"
        journal["status"] = "COMPLETE"
        _candidate_inputs(_env_binding, postgres_candidate)
        check_artifact()
        _write_journal(journal_path, journal)
        payload = _receipt_from_journal(journal)
        _candidate_inputs(_env_binding, postgres_candidate)
        check_artifact()
        _write_receipt(receipt, payload)
        return payload


def _refuse_legacy_receipt(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _secure_regular(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("backup_receipt_invalid") from exc
        if isinstance(payload, dict) and payload.get("schema") == LEGACY_RECEIPT_SCHEMA:
            raise BackupError("backup_role_env_binding_migration_required")
        if isinstance(payload, dict) and payload.get("schema") == METADATA_LEGACY_RECEIPT_SCHEMA:
            raise BackupError("backup_mode_migration_required")


def create_backup(
    *, env_file: Path, backup_dir: Path, receipt: Path, release_sha: str,
    release_tree: str, image_id: str, image_input_signature: str,
    refresh_complete: bool = False, allow_running_writers: bool = False,
    allow_target_identity_mismatch: bool = False,
    allow_private_primary_source: bool = False,
    execution_limits: BackupExecutionLimits | None = None,
    restore_resource_binding: Mapping[str, str] | None = None,
    postgres_candidate_control: Path | None = None,
    postgres_candidate_sha256: str | None = None,
) -> dict[str, Any]:
    _validate_execution_options(execution_limits, allow_private_primary_source, hot=allow_running_writers)
    if restore_resource_binding is not None and not _valid_restore_resources(restore_resource_binding):
        raise BackupError("backup_restore_resource_binding_invalid")
    # Refuse old transactions before any protected Env open/read.
    _refuse_legacy_receipt(receipt)
    journal_path = _journal_path(backup_dir)
    if journal_path.exists() or journal_path.is_symlink():
        _read_journal(journal_path)
    with _postgres_candidate_context(postgres_candidate_control, postgres_candidate_sha256) as candidate, BoundRoleEnv(env_file) as bound:
        return _create_backup_bound(
            env_file=env_file, _env_binding=bound, backup_dir=backup_dir,
            receipt=receipt, release_sha=release_sha, release_tree=release_tree,
            image_id=image_id, image_input_signature=image_input_signature,
            refresh_complete=refresh_complete,
            allow_running_writers=allow_running_writers,
            allow_target_identity_mismatch=allow_target_identity_mismatch,
            allow_private_primary_source=allow_private_primary_source,
            **_execution_kwargs(execution_limits),
            restore_resource_binding=restore_resource_binding,
            **_candidate_kwargs(candidate),
        )


def _quiesced_primary_options(
    execution_limits: BackupExecutionLimits, *, release_sha: str,
    release_tree: str, image_id: str, image_input_signature: str,
) -> None:
    if type(execution_limits) is not BackupExecutionLimits:
        raise BackupError("backup_execution_limits_invalid")
    _validate_execution_options(execution_limits, False, hot=False)
    if (type(release_sha) is not str or not RELEASE_SHA.fullmatch(release_sha)
            or type(release_tree) is not str or not RELEASE_SHA.fullmatch(release_tree)
            or type(image_id) is not str or not IMAGE_ID.fullmatch(image_id)
            or type(image_input_signature) is not str or not HEX64.fullmatch(image_input_signature)):
        raise BackupError("backup_quiesced_primary_identity_invalid")
    _checkpoint(execution_limits)


def _quiesced_primary_values(bound: BoundRoleEnv, *, release_sha: str, image_id: str) -> dict[str, str]:
    values = _validate_release_values(
        bound.values(), release_sha=release_sha, image_id=image_id,
        allow_target_identity_mismatch=False, allow_private_primary_source=True,
    )
    if (values.get("MARKET_PIPELINE_FEED_MODE"), values.get("MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY"),
            values.get("MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE")) != ("PRIVATE_PRIMARY", "1", "PRIVATE_PRIMARY"):
        raise BackupError("backup_quiesced_primary_lane_invalid")
    return values


def create_quiesced_primary_backup(
    *, env_file: Path, backup_dir: Path, receipt: Path, release_sha: str,
    release_tree: str, image_id: str, image_input_signature: str,
    execution_limits: BackupExecutionLimits, restore_resource_binding: Mapping[str, str],
    postgres_candidate_control: Path | None = None, postgres_candidate_sha256: str | None = None,
) -> dict[str, Any]:
    """Fresh bounded PRIMARY backup, with strict writer and restore parity.

    RELEASE is the configuration identity; immutable image/OCI provenance is
    separately established by the owning producer. No identity relaxation,
    hot mode, receipt adoption, or operation authority is supplied by this API.
    """
    _quiesced_primary_options(execution_limits, release_sha=release_sha, release_tree=release_tree,
                             image_id=image_id, image_input_signature=image_input_signature)
    if not _valid_restore_resources(restore_resource_binding):
        raise BackupError("backup_restore_resource_binding_invalid")
    _refuse_legacy_receipt(receipt)
    journal = _journal_path(backup_dir)
    if receipt.exists() or receipt.is_symlink() or journal.exists() or journal.is_symlink():
        raise BackupError("backup_quiesced_primary_fresh_required")
    if backup_dir.exists() or backup_dir.is_symlink():
        _secure_directory(backup_dir, create=False)
        if any(backup_dir.iterdir()):
            raise BackupError("backup_quiesced_primary_fresh_required")
    with _postgres_candidate_context(postgres_candidate_control, postgres_candidate_sha256) as candidate, BoundRoleEnv(env_file) as bound:
        values = _quiesced_primary_values(bound, release_sha=release_sha, image_id=image_id)
        _assert_writer_workloads_quiesced(values["MARKET_PIPELINE_PROJECT_NAME"],
                                         execution_limits=execution_limits)
        bound.check()
        payload = _create_backup_bound(
            env_file=env_file, _env_binding=bound, backup_dir=backup_dir, receipt=receipt,
            release_sha=release_sha, release_tree=release_tree, image_id=image_id,
            image_input_signature=image_input_signature, allow_running_writers=False,
            allow_target_identity_mismatch=False, allow_private_primary_source=True,
            execution_limits=execution_limits, restore_resource_binding=restore_resource_binding,
            **_candidate_kwargs(candidate),
        )
        if payload.get("status") != "PASS" or payload.get("backup_mode") != "quiesced":
            raise BackupError("backup_quiesced_primary_initialized_source_required")
        with _pinned_backup_artifact(Path(payload["backup"]["path"]), payload["backup"],
                                     execution_limits=execution_limits):
            current = inspect_source_database(values, require_quiesce=True, execution_limits=execution_limits,
                                               **_candidate_kwargs(candidate))
            bound.check()
            if (not _valid_source_metadata(current)
                    or not _source_window_compatible(payload["source_after"], current, allow_running_writers=False)):
                raise BackupError("backup_quiesced_primary_current_source_mismatch")
        return payload


def _verify_receipt_evidence(
    *, receipt: Path, role_env_binding: Mapping[str, Any],
    release_sha: str, release_tree: str, image_id: str,
    image_input_signature: str, maximum_age_seconds: int | None,
    now: datetime | None = None, historical: bool = False,
    _snapshots: tuple[dict[str, Any], dict[str, Any]] | None = None,
    backup_mode: str = "quiesced",
    _artifact_verified: bool = False,
) -> dict[str, Any]:
    old_digest = historical and _snapshots is not None and _snapshots[1].get("schema") == LEGACY_JOURNAL_SCHEMA
    env_key = "role_env_sha256" if old_digest else "role_env_binding"
    _secure_regular(receipt)
    _secure_directory(receipt.parent, create=False)
    if _snapshots is None:
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("backup_receipt_invalid") from exc
    else:
        payload = _snapshots[0]
    expected = {
        "schema": (LEGACY_RECEIPT_SCHEMA if old_digest else METADATA_LEGACY_RECEIPT_SCHEMA) if historical else RECEIPT_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        env_key: role_env_binding[env_key],
        "database_mutated": False,
        "services_started": False,
        "secrets_disclosed": False,
    }
    if not _same_env_binding({key: payload.get(key) for key in expected}, expected):
        raise BackupError("backup_receipt_identity_mismatch")
    common_keys = {
        "schema", "status", "created_at_utc", "release_sha", "release_tree",
        "image_id", "image_input_signature", env_key, "backup_run_id",
        "source", "source_after",
        "backup", "restore_smoke", "off_host_copy_required", "database_mutated",
        "services_started", "secrets_disclosed",
    }
    if not historical:
        common_keys.add("backup_mode")
        if payload.get("backup_mode") != backup_mode:
            raise BackupError("backup_mode_mismatch")
    if set(payload) != common_keys:
        raise BackupError("backup_receipt_schema_invalid")
    if not re.fullmatch(r"[0-9a-f]{16}", str(payload.get("backup_run_id") or "")):
        raise BackupError("backup_receipt_run_id_invalid")
    journal_path = _journal_path(receipt.parent)
    journal = (
        _read_journal(journal_path, historical=historical)
        if _snapshots is None else _snapshots[1]
    )
    _validate_journal_identity(
        journal,
        root=receipt.parent,
        role_env_binding=role_env_binding,
        historical=historical,
        receipt=receipt,
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=image_id,
        image_input_signature=image_input_signature,
        backup_mode=backup_mode,
    )
    if journal.get("status") != "COMPLETE" or payload != _receipt_from_journal(journal):
        raise BackupError("backup_receipt_journal_mismatch")
    try:
        created = datetime.fromisoformat(
            str(payload["created_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError("backup_receipt_timestamp_invalid") from exc
    reference = (now or utc_now()).astimezone(timezone.utc)
    if created > reference + timedelta(seconds=30) or (
        maximum_age_seconds is not None
        and reference - created > timedelta(seconds=maximum_age_seconds)
    ):
        raise BackupError("backup_receipt_stale")
    status = payload.get("status")
    if status == "INITIAL_EMPTY":
        if (
            payload.get("source") != {"database_initialized": False}
            or payload.get("source_after") != {"database_initialized": False}
            or payload.get("backup") is not None
            or payload.get("restore_smoke") != {"status": "NOT_APPLICABLE"}
            or payload.get("off_host_copy_required") is not False
        ):
            raise BackupError("backup_initial_empty_contract_invalid")
    elif status == "PASS":
        backup = payload.get("backup")
        restore = payload.get("restore_smoke")
        source_after = payload.get("source_after")
        if (
            not isinstance(backup, dict)
            or not isinstance(restore, dict)
            or set(backup) != {"path", "sha256", "size_bytes", "format"}
            or set(restore)
            != {
                "status", "schema_versions", "table_count", "fact_count",
                "table_row_counts", "sequence_values", "schema_catalog_sha256",
                "schema_objects_sha256", "cleanup_status",
            }
            or not isinstance(payload.get("source"), dict)
            or not isinstance(source_after, dict)
            or set(payload["source"])
            != {
                "container_id", "database", "database_size_bytes",
                "database_identity_sha256", "schema_versions", "table_count",
                "fact_count", "table_row_counts", "sequence_values",
                "schema_catalog_sha256", "schema_objects_sha256",
            }
            or set(source_after) != set(payload["source"])
            or restore.get("status") != "PASS"
            or restore.get("cleanup_status") != "PASS"
            or payload.get("off_host_copy_required") is not True
        ):
            raise BackupError("backup_restore_receipt_invalid")
        source = payload["source"]
        if (
            not all(_valid_source_metadata(row) for row in (source, source_after))
            or not _valid_invariants(restore)
            or source.get("database_identity_sha256")
            != source_after.get("database_identity_sha256")
            or (not historical and not _restore_window_compatible(
                source, source_after, restore, allow_running_writers=backup_mode == "hot"
            ))
            or (historical and (
                _invariant_view(source) != _invariant_view(source_after)
                or _row_inventory_view(source) != _row_inventory_view(restore)
            ))
        ):
            raise BackupError("backup_source_restore_metadata_invalid")
        artifact = Path(str(backup.get("path") or ""))
        if artifact.parent != receipt.parent or not ARTIFACT_NAME.fullmatch(artifact.name):
            raise BackupError("backup_artifact_location_invalid")
        _secure_regular(artifact)
        if (
            not HEX64.fullmatch(str(backup.get("sha256") or ""))
            or type(backup.get("size_bytes")) is not int or backup["size_bytes"] <= 0
            or (not _artifact_verified and file_digest(artifact) != backup["sha256"])
            or artifact.stat().st_size != backup["size_bytes"]
            or backup.get("format") != "postgres_custom"
        ):
            raise BackupError("backup_artifact_drifted")
    else:
        raise BackupError("backup_receipt_status_invalid")
    return payload


def verify_receipt(
    *, env_file: Path, receipt: Path, release_sha: str, release_tree: str,
    image_id: str, image_input_signature: str, maximum_age_seconds: int | None,
    now: datetime | None = None, allow_target_identity_mismatch: bool = False,
    postgres_candidate_control: Path | None = None, postgres_candidate_sha256: str | None = None,
) -> dict[str, Any]:
    # Legacy evidence can be inspected separately, never adopted as a live gate.
    _refuse_legacy_receipt(receipt)
    _read_journal(_journal_path(receipt.parent))
    with _postgres_candidate_context(postgres_candidate_control, postgres_candidate_sha256) as candidate, BoundRoleEnv(env_file) as bound:
        values = _validate_release_values(
            bound.values(), release_sha=release_sha, image_id=image_id,
            allow_target_identity_mismatch=allow_target_identity_mismatch,
        )
        payload = _verify_receipt_evidence(
            receipt=receipt, role_env_binding={"role_env_binding": bound.binding},
            release_sha=release_sha, release_tree=release_tree, image_id=image_id,
            image_input_signature=image_input_signature,
            maximum_age_seconds=maximum_age_seconds, now=now,
        )
        if payload["status"] == "INITIAL_EMPTY" and not _initial_store_is_empty(_postgres_path(values)):
            raise BackupError("backup_initial_store_changed")
        if candidate is not None:
            current = inspect_source_database(values, require_quiesce=payload["backup_mode"] == "quiesced",
                                               **_candidate_kwargs(candidate))
            _candidate_inputs(bound, candidate)
            if not _source_window_compatible(payload["source_after"], current,
                                             allow_running_writers=payload["backup_mode"] == "hot"):
                raise BackupError("backup_postgres_candidate_current_mismatch")
        return payload


@contextmanager
def _pinned_control_snapshots(
    receipt: Path, receipt_sha256: str, journal_sha256: str,
    *, hot: bool = False, quiesced_primary: bool = False,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Hash and parse the same bounded bytes; retain both identities to return."""
    prefix = "backup_quiesced_primary" if quiesced_primary else "backup_hot" if hot else "backup_historical"
    if not HEX64.fullmatch(receipt_sha256) or not HEX64.fullmatch(journal_sha256):
        raise BackupError(prefix + "_evidence_identity_mismatch")
    _secure_directory(receipt.parent, create=False)
    descriptors: list[int] = []
    files: list[tuple[str, int, dict[str, int]]] = []
    try:
        parent = os.open(
            receipt.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        descriptors.append(parent)
        parent_info = os.fstat(parent)
        if parent_info.st_uid != os.geteuid() or stat.S_IMODE(parent_info.st_mode) != 0o700:
            raise BackupError(prefix + "_evidence_invalid")
        parent_identity = _parent_identity(parent_info)

        def check() -> None:
            if (
                _parent_identity(os.fstat(parent)) != parent_identity
                or _parent_identity(receipt.parent.lstat()) != parent_identity
            ):
                raise BackupError(prefix + "_evidence_identity_mismatch")
            for name, descriptor, expected in files:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISREG(current.st_mode)
                    or _file_identity(os.fstat(descriptor)) != expected
                    or _file_identity(current) != expected
                ):
                    raise BackupError(prefix + "_evidence_identity_mismatch")

        documents: list[dict[str, Any]] = []
        for name, expected_sha256 in (
            (receipt.name, receipt_sha256),
            (_journal_path(receipt.parent).name, journal_sha256),
        ):
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=parent,
            )
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or not 0 < info.st_size <= MAX_HISTORICAL_DOCUMENT_BYTES
            ):
                raise BackupError(prefix + "_evidence_invalid")
            files.append((name, descriptor, _file_identity(info)))
            check()
            raw = os.pread(descriptor, MAX_HISTORICAL_DOCUMENT_BYTES + 1, 0)
            check()
            if len(raw) != info.st_size or sha256(raw).hexdigest() != expected_sha256:
                raise BackupError(prefix + "_evidence_identity_mismatch")
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise BackupError(prefix + "_evidence_invalid") from None
            if not isinstance(document, dict):
                raise BackupError(prefix + "_evidence_invalid")
            documents.append(document)
        check()
        yield documents[0], documents[1]
        check()
    except OSError:
        raise BackupError(prefix + "_evidence_identity_mismatch") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def _pinned_backup_artifact(artifact: Path, expected: Mapping[str, Any], *,
                            execution_limits: BackupExecutionLimits | None = None) -> Iterator[int]:
    if type(expected.get("size_bytes")) is not int or expected["size_bytes"] <= 0:
        raise BackupError("backup_artifact_drifted")
    if execution_limits is not None and expected["size_bytes"] > execution_limits.dump_max_bytes:
        raise BackupError("backup_dump_byte_bound")
    _secure_regular(artifact)
    descriptor = os.open(artifact, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        identity = _file_identity(info)
        def check() -> None:
            if (_file_identity(os.fstat(descriptor)) != identity
                    or _file_identity(artifact.lstat()) != identity):
                raise BackupError("backup_artifact_drifted")
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size != expected["size_bytes"]):
            raise BackupError("backup_artifact_drifted")
        digest = sha256()
        total = 0
        while chunk := os.read(descriptor, min(1024 * 1024, expected["size_bytes"] + 1 - total)):
            _checkpoint(execution_limits)
            total += len(chunk)
            if total > expected["size_bytes"]:
                raise BackupError("backup_artifact_drifted")
            digest.update(chunk)
        check()
        if total != expected["size_bytes"] or digest.hexdigest() != expected["sha256"]:
            raise BackupError("backup_artifact_drifted")
        yield descriptor
        check()
    except OSError:
        raise BackupError("backup_artifact_drifted") from None
    finally:
        os.close(descriptor)


def verify_quiesced_primary_receipt(
    *, env_file: Path, receipt: Path, receipt_sha256: str, journal_sha256: str,
    release_sha: str, release_tree: str, image_id: str, image_input_signature: str,
    maximum_age_seconds: int, execution_limits: BackupExecutionLimits,
    now: datetime | None = None,
    postgres_candidate_control: Path | None = None, postgres_candidate_sha256: str | None = None,
) -> dict[str, Any]:
    """Strict current PRIMARY verification; CREATE's owned restore is retained.

    This performs no second restore. It rechecks actual writer quiescence and
    exact source inventories around the retained, hash-pinned artifact FD.
    The caller still owns locks, finite cleanup, capacity and offhost proof.
    """
    _quiesced_primary_options(execution_limits, release_sha=release_sha, release_tree=release_tree,
                             image_id=image_id, image_input_signature=image_input_signature)
    if type(maximum_age_seconds) is not int or not 60 <= maximum_age_seconds <= 86400:
        raise BackupError("backup_maximum_age_invalid")
    if (type(receipt_sha256) is not str or not HEX64.fullmatch(receipt_sha256)
            or type(journal_sha256) is not str or not HEX64.fullmatch(journal_sha256)):
        raise BackupError("backup_quiesced_primary_evidence_identity_mismatch")
    with _pinned_control_snapshots(receipt, receipt_sha256, journal_sha256,
                                   quiesced_primary=True) as snapshots:
        payload, journal = snapshots
        if type(payload.get("schema")) is not str or type(journal.get("schema")) is not str:
            raise BackupError("backup_quiesced_primary_evidence_invalid")
        if (payload.get("schema") in {LEGACY_RECEIPT_SCHEMA, METADATA_LEGACY_RECEIPT_SCHEMA}
                or journal.get("schema") in {LEGACY_JOURNAL_SCHEMA, METADATA_LEGACY_JOURNAL_SCHEMA}):
            raise BackupError("backup_mode_migration_required")
        if payload.get("backup_mode") != "quiesced" or journal.get("backup_mode") != "quiesced":
            raise BackupError("backup_mode_mismatch")
        with _postgres_candidate_context(postgres_candidate_control, postgres_candidate_sha256) as candidate, BoundRoleEnv(env_file) as bound:
            values = _quiesced_primary_values(bound, release_sha=release_sha, image_id=image_id)
            payload = _verify_receipt_evidence(
                receipt=receipt, role_env_binding={"role_env_binding": bound.binding},
                release_sha=release_sha, release_tree=release_tree, image_id=image_id,
                image_input_signature=image_input_signature,
                maximum_age_seconds=maximum_age_seconds, now=now,
                _snapshots=snapshots, backup_mode="quiesced", _artifact_verified=True,
            )
            if payload["status"] != "PASS":
                raise BackupError("backup_quiesced_primary_initialized_source_required")
            current = inspect_source_database(values, require_quiesce=True, execution_limits=execution_limits,
                                               **_candidate_kwargs(candidate))
            bound.check()
            if (not _valid_source_metadata(current)
                    or not _source_window_compatible(payload["source_after"], current, allow_running_writers=False)):
                raise BackupError("backup_quiesced_primary_current_source_mismatch")
            with _pinned_backup_artifact(Path(payload["backup"]["path"]), payload["backup"],
                                         execution_limits=execution_limits):
                after = inspect_source_database(values, require_quiesce=True, execution_limits=execution_limits,
                                                 **_candidate_kwargs(candidate))
                bound.check()
                if (not _valid_source_metadata(after)
                        or not _source_window_compatible(current, after, allow_running_writers=False)):
                    raise BackupError("backup_quiesced_primary_current_source_mismatch")
            return payload


def verify_hot_receipt(
    *, env_file: Path, receipt: Path, receipt_sha256: str, journal_sha256: str,
    release_sha: str, release_tree: str, image_id: str, image_input_signature: str,
    maximum_age_seconds: int | None, now: datetime | None = None,
    allow_target_identity_mismatch: bool = False,
    restore_resource_binding: Mapping[str, str] | None = None,
    allow_private_primary_source: bool = False,
    execution_limits: BackupExecutionLimits | None = None,
    postgres_candidate_control: Path | None = None, postgres_candidate_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify an explicit hot snapshot with a fresh independent isolated restore.

    The finite owning caller remains responsible for capacity, daemon-side
    deadline cleanup, current application image provenance and offhost proof.
    """
    _validate_execution_options(execution_limits, allow_private_primary_source, hot=True)
    if type(maximum_age_seconds) is not int or not 60 <= maximum_age_seconds <= 86400:
        raise BackupError("backup_maximum_age_invalid")
    with _pinned_control_snapshots(receipt, receipt_sha256, journal_sha256, hot=True) as snapshots:
        payload, journal = snapshots
        if type(payload.get("schema")) is not str or type(journal.get("schema")) is not str:
            raise BackupError("backup_hot_evidence_invalid")
        if payload.get("schema") in {LEGACY_RECEIPT_SCHEMA, METADATA_LEGACY_RECEIPT_SCHEMA} or journal.get("schema") in {LEGACY_JOURNAL_SCHEMA, METADATA_LEGACY_JOURNAL_SCHEMA}:
            raise BackupError("backup_mode_migration_required")
        if payload.get("backup_mode") != "hot" or journal.get("backup_mode") != "hot":
            raise BackupError("backup_mode_mismatch")
        if restore_resource_binding is None:
            nonce = secrets.token_hex(8)
            restore_resource_binding = {
                "container": f"market_pipeline_restore_{nonce}",
                "volume": f"market_pipeline_restore_{nonce}_data",
                "label": f"market-pipeline-backup-{nonce}",
            }
        if not _valid_restore_resources(restore_resource_binding):
            raise BackupError("backup_restore_resource_binding_invalid")
        previous_resources = journal.get("restore_resources")
        if previous_resources is not None and not _valid_restore_resources(previous_resources):
            raise BackupError("backup_journal_resource_binding_invalid")
        previous_resources = previous_resources or {}
        if any(restore_resource_binding.get(key) == previous_resources.get(key)
               for key in ("container", "volume", "label")):
            raise BackupError("backup_hot_restore_resources_not_independent")
        with _postgres_candidate_context(postgres_candidate_control, postgres_candidate_sha256) as candidate, BoundRoleEnv(env_file) as bound:
            values = _validate_release_values(
                bound.values(), release_sha=release_sha, image_id=image_id,
                allow_target_identity_mismatch=allow_target_identity_mismatch,
                allow_private_primary_source=allow_private_primary_source,
            )
            # A hot backup is bound to the actual source, never an unrelated
            # deployment target accepted by the older Blue/Green interface.
            if (values.get("MARKET_PIPELINE_RELEASE_SHA") != release_sha
                    or values.get("MARKET_PIPELINE_IMAGE") != image_id):
                raise BackupError("backup_hot_source_identity_mismatch")
            payload = _verify_receipt_evidence(
                receipt=receipt, role_env_binding={"role_env_binding": bound.binding},
                release_sha=release_sha, release_tree=release_tree, image_id=image_id,
                image_input_signature=image_input_signature,
                maximum_age_seconds=maximum_age_seconds, now=now,
                _snapshots=snapshots, backup_mode="hot", _artifact_verified=True,
            )
            if payload["status"] != "PASS":
                raise BackupError("backup_hot_initialized_source_required")
            source = inspect_source_database(values, require_quiesce=False, **_execution_kwargs(execution_limits),
                                             **_candidate_kwargs(candidate))
            bound.check()
            if not _valid_source_metadata(source) or not _source_window_compatible(payload["source_after"], source, allow_running_writers=True):
                raise BackupError("backup_hot_current_source_mismatch")
            with _pinned_backup_artifact(Path(payload["backup"]["path"]), payload["backup"],
                                         **_execution_kwargs(execution_limits)) as descriptor:
                restored = restore_smoke(
                    Path(payload["backup"]["path"]), source,
                    resource_binding=restore_resource_binding,
                    artifact_descriptor=descriptor,
                    **_candidate_kwargs(candidate),
                    **_execution_kwargs(execution_limits),
                )
                bound.check()
                if (restored.get("status") != "PASS" or restored.get("cleanup_status") != "PASS"
                        or not _valid_invariants(restored)
                        or not _same_env_binding(_invariant_view(restored), _invariant_view(payload["restore_smoke"]))):
                    raise BackupError("backup_hot_independent_restore_mismatch")
                source_after = inspect_source_database(values, require_quiesce=False, **_execution_kwargs(execution_limits),
                                                       **_candidate_kwargs(candidate))
                bound.check()
                if not _valid_source_metadata(source_after) or not _source_window_compatible(source, source_after, allow_running_writers=True):
                    raise BackupError("backup_hot_current_source_mismatch")
            return {
                "status": "PASS", "backup_mode": "hot", "receipt": payload,
                "current_environment_verified": True, "fresh_restore_verified": True,
                "eligible_for_new_deployment": False, "off_host_copy_required": True,
                "current_source": source, "current_source_after": source_after,
                "independent_restore": restored, "secrets_disclosed": False,
                "restore_resources": dict(restore_resource_binding),
            }


def verify_historical_receipt(
    *, receipt: Path, receipt_sha256: str, journal_sha256: str,
    release_sha: str, release_tree: str, image_id: str,
    image_input_signature: str,
) -> dict[str, Any]:
    """Verify pinned historical evidence without accepting a current Env claim.

    Old controls have no backup mode. Preserve their strict VERIFY inventory
    contract; never retrospectively assume hot CREATE semantics for row drift.
    """
    with _pinned_control_snapshots(receipt, receipt_sha256, journal_sha256) as snapshots:
        journal = snapshots[1]
        if type(journal.get("schema")) is not str or journal["schema"] not in {LEGACY_JOURNAL_SCHEMA, METADATA_LEGACY_JOURNAL_SCHEMA}:
            raise BackupError("backup_historical_evidence_invalid")
        env_key = "role_env_sha256" if journal["schema"] == LEGACY_JOURNAL_SCHEMA else "role_env_binding"
        if env_key not in journal or (env_key == "role_env_sha256" and not HEX64.fullmatch(str(journal[env_key]))):
            raise BackupError("backup_historical_evidence_invalid")
        payload = _verify_receipt_evidence(
            receipt=receipt, role_env_binding={env_key: journal[env_key]},
            release_sha=release_sha, release_tree=release_tree, image_id=image_id,
            image_input_signature=image_input_signature, maximum_age_seconds=None,
            historical=True, _snapshots=snapshots,
        )
        return {
            "status": "PASS", "historical_only": True,
            "current_environment_verified": False,
            "eligible_for_new_deployment": False, "receipt": payload,
            "secrets_disclosed": False,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "verify", "verify-hot"):
        command = commands.add_parser(name)
        command.add_argument("--env-file", type=Path, required=True)
        command.add_argument("--receipt", type=Path, required=True)
        command.add_argument("--release-sha", required=True)
        command.add_argument("--release-tree", required=True)
        command.add_argument("--image-id", required=True)
        command.add_argument("--image-input-signature", required=True)
        if name == "create":
            command.add_argument("--backup-dir", type=Path, required=True)
            command.add_argument("--refresh-complete", action="store_true")
            command.add_argument("--allow-running-writers", action="store_true")
            command.add_argument("--confirm", required=True)
        else:
            command.add_argument("--maximum-age-seconds", type=int, default=3600)
            if name == "verify":
                command.add_argument("--allow-stale", action="store_true")
            else:
                command.add_argument("--receipt-sha256", required=True)
                command.add_argument("--journal-sha256", required=True)
                command.add_argument("--restore-run-id", required=True)
        command.add_argument("--bluegreen-source-env", action="store_true")
        command.add_argument("--postgres-candidate-control", type=Path)
        command.add_argument("--postgres-candidate-sha256")
    history = commands.add_parser("verify-historical")
    history.add_argument("--receipt", type=Path, required=True)
    history.add_argument("--receipt-sha256", required=True)
    history.add_argument("--journal-sha256", required=True)
    history.add_argument("--release-sha", required=True)
    history.add_argument("--release-tree", required=True)
    history.add_argument("--image-id", required=True)
    history.add_argument("--image-input-signature", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not RELEASE_SHA.fullmatch(args.release_sha):
            raise BackupError("backup_release_sha_invalid")
        if not RELEASE_SHA.fullmatch(args.release_tree):
            raise BackupError("backup_release_tree_invalid")
        if not IMAGE_ID.fullmatch(args.image_id):
            raise BackupError("backup_image_id_invalid")
        if not HEX64.fullmatch(args.image_input_signature):
            raise BackupError("backup_image_signature_invalid")
        if args.command == "verify-historical":
            historical = verify_historical_receipt(
                receipt=args.receipt, receipt_sha256=args.receipt_sha256,
                journal_sha256=args.journal_sha256, release_sha=args.release_sha,
                release_tree=args.release_tree, image_id=args.image_id,
                image_input_signature=args.image_input_signature,
            )
            document = historical["receipt"]
        else:
            common = {
                "env_file": args.env_file,
                "receipt": args.receipt,
                "release_sha": args.release_sha,
                "release_tree": args.release_tree,
                "image_id": args.image_id,
                "image_input_signature": args.image_input_signature,
                "allow_target_identity_mismatch": args.bluegreen_source_env,
                **({"postgres_candidate_control": args.postgres_candidate_control,
                    "postgres_candidate_sha256": args.postgres_candidate_sha256}
                   if args.postgres_candidate_control is not None or args.postgres_candidate_sha256 is not None else {}),
            }
            if args.command == "create":
                expected_confirm = (
                    HOT_CREATE_CONFIRMATION
                    if args.allow_running_writers
                    else CREATE_CONFIRMATION
                )
                if args.confirm != expected_confirm:
                    raise BackupError("backup_confirmation_invalid")
                document = create_backup(
                    backup_dir=args.backup_dir,
                    refresh_complete=args.refresh_complete,
                    allow_running_writers=args.allow_running_writers,
                    **common,
                )
            else:
                if not getattr(args, "allow_stale", False) and not 60 <= args.maximum_age_seconds <= 86400:
                    raise BackupError("backup_maximum_age_invalid")
                if args.command == "verify-hot":
                    if not re.fullmatch(r"[0-9a-f]{16}", args.restore_run_id):
                        raise BackupError("backup_restore_resource_binding_invalid")
                    hot = verify_hot_receipt(
                        receipt_sha256=args.receipt_sha256, journal_sha256=args.journal_sha256,
                        maximum_age_seconds=args.maximum_age_seconds,
                        restore_resource_binding={
                            "container": f"market_pipeline_restore_{args.restore_run_id}",
                            "volume": f"market_pipeline_restore_{args.restore_run_id}_data",
                            "label": f"market-pipeline-backup-{args.restore_run_id}",
                        }, **common,
                    )
                    document = hot["receipt"]
                else:
                    document = verify_receipt(
                        maximum_age_seconds=(None if args.allow_stale else args.maximum_age_seconds),
                        **common,
                    )
        artifact = document.get("backup")
        print(
            json.dumps(
                {
                    "status": "pass",
                    **({"historical_only": True, "current_environment_verified": False,
                        "eligible_for_new_deployment": False}
                       if args.command == "verify-historical" else {}),
                    **({"backup_mode": "hot", "current_environment_verified": True,
                        "fresh_restore_verified": True, "eligible_for_new_deployment": False}
                       if args.command == "verify-hot" else {}),
                    "backup_status": document["status"],
                    "release_sha": document["release_sha"],
                    "artifact_name": (
                        Path(str(artifact["path"])).name
                        if isinstance(artifact, dict)
                        else None
                    ),
                    "artifact_sha256": (
                        artifact["sha256"] if isinstance(artifact, dict) else None
                    ),
                    "artifact_size_bytes": (
                        artifact["size_bytes"] if isinstance(artifact, dict) else None
                    ),
                    "database_mutated": False,
                    "services_started": False,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, BackupError, ReleaseContractError) as exc:
        # Source validation is part of the real backup operation. Keep its
        # refusal visible without rendering paths, private values or tracebacks.
        reason = str(exc)
        if not isinstance(exc, (BackupError, ReleaseContractError)) or not re.fullmatch(
            r"[a-z0-9_]{1,96}", reason
        ):
            reason = "backup_cli_input_or_io_failure"
        print(
            json.dumps(
                {"status": "fail", "reason_code": reason, "secrets_disclosed": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
