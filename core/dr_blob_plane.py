"""Content-addressed WebApp blob persistence and recovery barrier helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import enum
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import uuid4
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.dr_event_protocol import canonical_json_bytes
from core.dr_data_policy import WEBAPP_DR_REPLICA_TABLES, canonical_dr_row_payload
from core.runtime_identity import resolve_runtime_identity
from core.runtime_sites import SITE_WEBAPP_FI, SITE_WEBAPP_IR
from core.writer_fencing import current_projection_fence_context, current_writer_fence_context
from models.chat_file import ChatFile
from models.dr_event import (
    DrBlobDelivery,
    DrBlobManifest,
    DrBlobReceipt,
    DrFileIntent,
    DrRecoveryManifest,
    DrStreamCheckpoint,
)
from models.webapp_writer_state import WebappWriterState
from models.database import Base
from core.sync_registry import SyncPolicy, sync_registry_entries


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
WEBAPP_SITES = frozenset({SITE_WEBAPP_FI, SITE_WEBAPP_IR})


class DrBlobPlaneError(RuntimeError):
    """Raised when a blob cannot be made durable and identity-stable."""


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise DrBlobPlaneError("short write while persisting content-addressed blob")
        view = view[written:]


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise DrBlobPlaneError("blob path is not a stable single-link regular file")
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest(), size


def persist_content_addressed_bytes(contents: bytes, *, root: str | Path | None = None) -> tuple[str, str]:
    content_hash = hashlib.sha256(contents).hexdigest()
    root_path = Path(root or settings.dr_blob_root)
    destination_dir = root_path / "sha256" / content_hash[:2] / content_hash[2:4]
    destination_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    destination = destination_dir / content_hash
    if destination.exists():
        existing_hash, existing_size = _hash_file(destination)
        if existing_hash != content_hash or existing_size != len(contents):
            raise DrBlobPlaneError("existing content-addressed blob failed hash/size validation")
        return content_hash, str(destination)

    temporary = destination_dir / f".{content_hash}.{os.getpid()}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o640)
    try:
        _write_all(fd, contents)
        os.fsync(fd)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(fd)
    try:
        os.replace(temporary, destination)
        os.chmod(destination, 0o440)
        directory_fd = os.open(destination_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return content_hash, str(destination)


def blob_object_key(content_hash: str) -> str:
    if not SHA256_RE.fullmatch(content_hash):
        raise DrBlobPlaneError("blob content hash is malformed")
    prefix = str(settings.dr_blob_object_prefix or "").strip("/")
    if not prefix or ".." in prefix.split("/"):
        raise DrBlobPlaneError("blob object prefix is unsafe")
    return f"{prefix}/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}"


async def bind_chat_file_blob(
    db: AsyncSession,
    *,
    chat_file: ChatFile,
    contents: bytes,
) -> tuple[str, str]:
    """Write bytes first, then bind row+file intent in the caller transaction."""

    content_hash, local_path = persist_content_addressed_bytes(contents)
    chat_file.s3_key = local_path
    chat_file.content_hash = content_hash
    chat_file.storage_version = 1
    if not bool(getattr(settings, "dr_event_protocol_enabled", False)):
        return content_hash, local_path
    identity = resolve_runtime_identity(settings)
    if identity.physical_site not in WEBAPP_SITES:
        raise DrBlobPlaneError("chat blob production is restricted to a WebApp physical site")
    fence = current_writer_fence_context()
    if fence is None or fence.physical_site != identity.physical_site:
        raise DrBlobPlaneError("chat blob intent lacks the current WebApp writer term")

    manifest = await db.get(DrBlobManifest, content_hash)
    if manifest is None:
        manifest = DrBlobManifest(
            content_hash=content_hash,
            size_bytes=len(contents),
            mime_type=chat_file.mime_type,
            local_path=local_path,
            object_key=blob_object_key(content_hash),
            state="local",
        )
        db.add(manifest)
    elif int(manifest.size_bytes) != len(contents) or manifest.local_path != local_path:
        raise DrBlobPlaneError("existing blob manifest conflicts with immutable content identity")

    db.add(
        DrFileIntent(
            intent_id=str(uuid4()),
            chat_file_id=chat_file.id,
            content_hash=content_hash,
            origin_physical_site=identity.physical_site,
            writer_epoch=int(fence.writer_epoch),
        )
    )
    destination = next(iter(WEBAPP_SITES - {identity.physical_site}))
    delivery = await db.get(DrBlobDelivery, (content_hash, destination))
    if delivery is None:
        db.add(
            DrBlobDelivery(
                content_hash=content_hash,
                destination_site=destination,
                status="pending_upload",
                attempt_count=0,
            )
        )
    return content_hash, local_path


async def _manifest_epoch(
    db: AsyncSession,
    *,
    manifest_kind: str,
    expected_writer_epoch: int | None,
) -> tuple[Any, int]:
    identity = resolve_runtime_identity(settings)
    if identity.physical_site not in WEBAPP_SITES:
        raise DrBlobPlaneError("recovery manifest is restricted to a WebApp physical site")
    if manifest_kind == "origin":
        fence = current_writer_fence_context()
        if fence is None or fence.physical_site != identity.physical_site:
            raise DrBlobPlaneError("origin manifest requires the current WebApp writer term")
        epoch = int(fence.writer_epoch)
        if expected_writer_epoch is not None and int(expected_writer_epoch) != epoch:
            raise DrBlobPlaneError("origin manifest epoch does not match the current writer")
        return identity, epoch
    if manifest_kind != "promotion":
        raise DrBlobPlaneError("recovery manifest kind is invalid")
    if current_projection_fence_context() is None or current_writer_fence_context() is not None:
        raise DrBlobPlaneError("promotion manifest requires a fenced projection capability")
    state = await db.scalar(
        select(WebappWriterState)
        .where(WebappWriterState.authority == "webapp")
        .with_for_update(read=True)
    )
    if state is None or state.active_site == identity.physical_site:
        raise DrBlobPlaneError("promotion manifest target is missing or already the active writer")
    epoch = int(state.writer_epoch) + 1
    if expected_writer_epoch is None or int(expected_writer_epoch) != epoch:
        raise DrBlobPlaneError("promotion manifest must bind the exact next Writer epoch")
    return identity, epoch


async def _current_checkpoint_and_blob_barrier(
    db: AsyncSession,
    *,
    physical_site: str,
) -> tuple[str, str, int]:
    checkpoints = (
        await db.execute(
            select(
                DrStreamCheckpoint.origin_physical_site,
                DrStreamCheckpoint.producer_epoch,
                DrStreamCheckpoint.contiguous_received_sequence,
                DrStreamCheckpoint.contiguous_applied_sequence,
                DrStreamCheckpoint.last_envelope_hash,
            ).where(DrStreamCheckpoint.destination_site == physical_site)
        )
    ).all()
    checkpoint_payload = [list(row) for row in sorted(checkpoints, key=lambda row: (row[0], row[1]))]
    event_checkpoint_hash = hashlib.sha256(canonical_json_bytes(checkpoint_payload)).hexdigest()
    blobs = (
        await db.execute(
            select(DrBlobManifest.content_hash, DrBlobManifest.size_bytes)
            .where(DrBlobManifest.state != "tombstoned")
            .order_by(DrBlobManifest.content_hash)
        )
    ).all()
    blob_payload = [[row[0], int(row[1])] for row in blobs]
    blob_set_hash = hashlib.sha256(canonical_json_bytes(blob_payload)).hexdigest()
    return event_checkpoint_hash, blob_set_hash, len(blob_payload)


def _fingerprint_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return _fingerprint_json_value(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, (list, tuple)):
        return [_fingerprint_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _fingerprint_json_value(item) for key, item in value.items()}
    raise DrBlobPlaneError(f"unsupported database fingerprint value: {type(value).__name__}")


async def _database_fingerprint(db: AsyncSession) -> tuple[str, int]:
    registry = sync_registry_entries(include_planned=False)
    table_names = sorted(
        {
            table_name
            for table_name, entry in registry.items()
            if entry.policy == SyncPolicy.SYNC
        }
        | set(WEBAPP_DR_REPLICA_TABLES)
    )
    summaries: list[list[Any]] = []
    total_rows = 0
    for table_name in table_names:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            raise DrBlobPlaneError(f"database fingerprint table is not mapped: {table_name}")
        primary_keys = list(table.primary_key.columns)
        if not primary_keys:
            raise DrBlobPlaneError(f"database fingerprint table lacks a primary key: {table_name}")
        digest = hashlib.sha256()
        row_count = 0
        stream = await db.stream(select(table).order_by(*primary_keys))
        async for row in stream.mappings():
            raw = {key: _fingerprint_json_value(value) for key, value in row.items()}
            canonical = canonical_dr_row_payload(table_name, raw)
            digest.update(canonical_json_bytes(canonical))
            digest.update(b"\n")
            row_count += 1
        summaries.append([table_name, row_count, digest.hexdigest()])
        total_rows += row_count
    return hashlib.sha256(canonical_json_bytes(summaries)).hexdigest(), total_rows


async def create_recovery_manifest(
    db: AsyncSession,
    *,
    manifest_kind: str = "origin",
    expected_writer_epoch: int | None = None,
) -> DrRecoveryManifest:
    """Create a DB/event/blob barrier from one transaction snapshot."""

    # The caller must provide a fresh session. This binds every table/file/event
    # digest to one repeatable-read snapshot instead of a sequence of moving
    # read-committed views.
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    identity, manifest_epoch = await _manifest_epoch(
        db,
        manifest_kind=manifest_kind,
        expected_writer_epoch=expected_writer_epoch,
    )
    release_sha = str(settings.release_sha or "").strip().lower()
    if not GIT_SHA_RE.fullmatch(release_sha):
        raise DrBlobPlaneError("recovery manifest requires an exact 40/64-hex release SHA")
    database_lsn, database_snapshot = (
        await db.execute(text("SELECT pg_current_wal_lsn()::text, txid_current_snapshot()::text"))
    ).one()
    event_checkpoint_hash, blob_set_hash, blob_count = await _current_checkpoint_and_blob_barrier(
        db, physical_site=identity.physical_site
    )
    database_fingerprint_hash, database_row_count = await _database_fingerprint(db)
    manifest_payload = {
        "manifest_kind": manifest_kind,
        "physical_site": identity.physical_site,
        "writer_epoch": manifest_epoch,
        "release_sha": release_sha,
        "database_lsn": str(database_lsn),
        "database_snapshot": str(database_snapshot),
        "database_fingerprint_hash": database_fingerprint_hash,
        "database_row_count": database_row_count,
        "event_checkpoint_hash": event_checkpoint_hash,
        "blob_set_hash": blob_set_hash,
        "blob_count": blob_count,
    }
    manifest_hash = hashlib.sha256(canonical_json_bytes(manifest_payload)).hexdigest()
    manifest = DrRecoveryManifest(
        manifest_id=str(uuid4()),
        manifest_hash=manifest_hash,
        status="prepared",
        **manifest_payload,
    )
    db.add(manifest)
    return manifest


async def verify_recovery_manifest(
    db: AsyncSession,
    manifest_id: str,
    *,
    manifest_kind: str | None = None,
    expected_writer_epoch: int | None = None,
) -> DrRecoveryManifest:
    """Recompute event/blob barrier and mark exactly one manifest verified."""

    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    manifest = await db.get(DrRecoveryManifest, manifest_id, with_for_update=True)
    if manifest is None or manifest.status != "prepared":
        raise DrBlobPlaneError("recovery manifest is missing or not prepared")
    if manifest_kind is not None and manifest.manifest_kind != manifest_kind:
        raise DrBlobPlaneError("recovery manifest kind does not match the requested verification")
    identity, manifest_epoch = await _manifest_epoch(
        db,
        manifest_kind=manifest.manifest_kind,
        expected_writer_epoch=(
            expected_writer_epoch
            if expected_writer_epoch is not None
            else int(manifest.writer_epoch)
        ),
    )
    if (
        manifest.physical_site != identity.physical_site
        or int(manifest.writer_epoch) != manifest_epoch
        or manifest.release_sha != str(settings.release_sha or "").strip().lower()
    ):
        raise DrBlobPlaneError("recovery manifest does not match current site/epoch/release")
    missing = await missing_local_blob_hashes(db)
    if missing:
        raise DrBlobPlaneError("recovery manifest references missing/corrupt local blobs")
    checkpoint_hash, blob_hash, blob_count = await _current_checkpoint_and_blob_barrier(
        db, physical_site=identity.physical_site
    )
    database_fingerprint_hash, database_row_count = await _database_fingerprint(db)
    if (
        checkpoint_hash != manifest.event_checkpoint_hash
        or blob_hash != manifest.blob_set_hash
        or blob_count != int(manifest.blob_count)
        or database_fingerprint_hash != manifest.database_fingerprint_hash
        or database_row_count != int(manifest.database_row_count)
    ):
        manifest.status = "invalidated"
        return manifest
    manifest_payload = {
        "manifest_kind": manifest.manifest_kind,
        "physical_site": manifest.physical_site,
        "writer_epoch": int(manifest.writer_epoch),
        "release_sha": manifest.release_sha,
        "database_lsn": manifest.database_lsn,
        "database_snapshot": manifest.database_snapshot,
        "database_fingerprint_hash": manifest.database_fingerprint_hash,
        "database_row_count": int(manifest.database_row_count),
        "event_checkpoint_hash": manifest.event_checkpoint_hash,
        "blob_set_hash": manifest.blob_set_hash,
        "blob_count": int(manifest.blob_count),
    }
    if hashlib.sha256(canonical_json_bytes(manifest_payload)).hexdigest() != manifest.manifest_hash:
        manifest.status = "invalidated"
        return manifest
    manifest.status = "verified"
    manifest.verified_at = datetime.now(timezone.utc)
    return manifest


async def current_verified_recovery_manifest_exists(
    db: AsyncSession,
    *,
    physical_site: str,
    writer_epoch: int,
    release_sha: str,
    manifest_kind: str,
) -> bool:
    """Read-only readiness check that rejects a stale formerly-verified barrier."""

    if await missing_local_blob_hashes(db):
        return False
    checkpoint_hash, blob_hash, blob_count = await _current_checkpoint_and_blob_barrier(
        db, physical_site=physical_site
    )
    database_fingerprint_hash, database_row_count = await _database_fingerprint(db)
    manifests = (
        await db.execute(
            select(DrRecoveryManifest).where(
                DrRecoveryManifest.manifest_kind == manifest_kind,
                DrRecoveryManifest.physical_site == physical_site,
                DrRecoveryManifest.writer_epoch == int(writer_epoch),
                DrRecoveryManifest.release_sha == release_sha,
                DrRecoveryManifest.status == "verified",
            )
        )
    ).scalars().all()
    for manifest in manifests:
        if (
            manifest.event_checkpoint_hash == checkpoint_hash
            and manifest.blob_set_hash == blob_hash
            and int(manifest.blob_count) == blob_count
            and manifest.database_fingerprint_hash == database_fingerprint_hash
            and int(manifest.database_row_count) == database_row_count
        ):
            manifest_payload = {
                "manifest_kind": manifest.manifest_kind,
                "physical_site": manifest.physical_site,
                "writer_epoch": int(manifest.writer_epoch),
                "release_sha": manifest.release_sha,
                "database_lsn": manifest.database_lsn,
                "database_snapshot": manifest.database_snapshot,
                "database_fingerprint_hash": manifest.database_fingerprint_hash,
                "database_row_count": int(manifest.database_row_count),
                "event_checkpoint_hash": manifest.event_checkpoint_hash,
                "blob_set_hash": manifest.blob_set_hash,
                "blob_count": int(manifest.blob_count),
            }
            if hashlib.sha256(canonical_json_bytes(manifest_payload)).hexdigest() == manifest.manifest_hash:
                return True
    return False


async def missing_local_blob_hashes(db: AsyncSession) -> list[str]:
    """Return all DB-referenced blobs that are absent or corrupt locally."""

    rows = (
        await db.execute(
            select(ChatFile.content_hash, ChatFile.s3_key, ChatFile.size)
            .where(ChatFile.content_hash.is_not(None))
            .order_by(ChatFile.content_hash)
        )
    ).all()
    missing: list[str] = []
    for content_hash, local_path, expected_size in rows:
        try:
            actual_hash, actual_size = _hash_file(Path(local_path))
        except (OSError, DrBlobPlaneError):
            missing.append(content_hash)
            continue
        if actual_hash != content_hash or actual_size != int(expected_size):
            missing.append(content_hash)
    return sorted(set(missing))


async def assert_blob_promotion_ready(db: AsyncSession) -> None:
    unhashed = int(
        await db.scalar(select(func.count()).select_from(ChatFile).where(ChatFile.content_hash.is_(None)))
        or 0
    )
    if unhashed:
        raise DrBlobPlaneError(f"{unhashed} legacy chat files lack content hashes")
    missing = await missing_local_blob_hashes(db)
    if missing:
        raise DrBlobPlaneError(f"{len(missing)} referenced blobs are missing or corrupt")


async def mark_unreferenced_blobs_tombstoned(db: AsyncSession) -> int:
    """Start a conservative retention window only after destination ACK."""

    now = datetime.now(timezone.utc)
    retention = timedelta(days=max(7, int(settings.dr_blob_gc_retention_days)))
    candidates = (
        await db.execute(
            select(DrBlobManifest)
            .where(DrBlobManifest.state != "tombstoned")
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    changed = 0
    for manifest in candidates:
        references = int(
            await db.scalar(
                select(func.count())
                .select_from(ChatFile)
                .where(ChatFile.content_hash == manifest.content_hash)
            )
            or 0
        )
        delivery_count = int(
            await db.scalar(
                select(func.count())
                .select_from(DrBlobDelivery)
                .where(DrBlobDelivery.content_hash == manifest.content_hash)
            )
            or 0
        )
        incomplete = int(
            await db.scalar(
                select(func.count())
                .select_from(DrBlobDelivery)
                .where(
                    DrBlobDelivery.content_hash == manifest.content_hash,
                    DrBlobDelivery.status != "acknowledged",
                )
            )
            or 0
        )
        if references or not delivery_count or incomplete:
            continue
        manifest.state = "tombstoned"
        manifest.tombstoned_at = now
        manifest.retain_until = now + retention
        changed += 1
    return changed


async def garbage_collect_expired_local_blobs(db: AsyncSession) -> int:
    """Delete only the local immutable copy after the tombstone delay.

    The versioned Object Storage copy is intentionally retained for the
    separately approved retention period and is never deleted by this worker.
    """

    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(DrBlobManifest)
            .where(
                DrBlobManifest.state == "tombstoned",
                DrBlobManifest.local_deleted_at.is_(None),
                DrBlobManifest.retain_until <= now,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    deleted = 0
    for manifest in rows:
        references = int(
            await db.scalar(
                select(func.count())
                .select_from(ChatFile)
                .where(ChatFile.content_hash == manifest.content_hash)
            )
            or 0
        )
        if references:
            raise DrBlobPlaneError("a tombstoned blob became referenced before local GC")
        path = Path(manifest.local_path)
        try:
            observed_hash, observed_size = _hash_file(path)
        except FileNotFoundError:
            observed_hash, observed_size = manifest.content_hash, int(manifest.size_bytes)
        if observed_hash != manifest.content_hash or observed_size != int(manifest.size_bytes):
            raise DrBlobPlaneError("local GC refused a corrupt or replaced blob path")
        try:
            path.unlink()
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileNotFoundError:
            pass
        manifest.local_deleted_at = now
        deleted += 1
    return deleted
