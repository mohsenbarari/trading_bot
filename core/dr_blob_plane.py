"""Content-addressed WebApp blob persistence and recovery barrier helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import enum
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any
from uuid import uuid4
from uuid import UUID

from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from core.config import settings
from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import append_hash_chained_jsonl, read_secure_text, write_secure_atomic_bytes
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
PENDING_PUBLICATIONS_KEY = "dr_blob_pending_publications"


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


def _blob_destination(content_hash: str, *, root: str | Path | None = None) -> Path:
    if not SHA256_RE.fullmatch(content_hash):
        raise DrBlobPlaneError("blob content hash is malformed")
    root_path = Path(root or settings.dr_blob_root)
    return root_path / "sha256" / content_hash[:2] / content_hash[2:4] / content_hash


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _publish_staged_blob(publication: dict[str, Any]) -> None:
    staged_path = Path(publication["staged_path"])
    final_path = Path(publication["final_path"])
    content_hash = str(publication["content_hash"])
    size_bytes = int(publication["size_bytes"])
    observed_hash, observed_size = _hash_file(staged_path)
    if observed_hash != content_hash or observed_size != size_bytes:
        raise DrBlobPlaneError("staged blob failed hash/size validation before publication")
    final_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if final_path.exists():
        existing_hash, existing_size = _hash_file(final_path)
        if existing_hash != content_hash or existing_size != size_bytes:
            raise DrBlobPlaneError("existing content-addressed blob conflicts with staged publication")
        staged_path.unlink()
    else:
        os.replace(staged_path, final_path)
        os.chmod(final_path, 0o440)
        _fsync_directory(final_path.parent)
    operation_dir = staged_path.parent
    try:
        (operation_dir / "intent.json").unlink()
    except FileNotFoundError:
        pass
    try:
        operation_dir.rmdir()
    except OSError:
        pass


def _discard_staged_blob(publication: dict[str, Any]) -> None:
    operation_dir = Path(publication["staged_path"]).parent
    try:
        shutil.rmtree(operation_dir)
    except FileNotFoundError:
        pass


@event.listens_for(Session, "after_commit")
def _publish_committed_blob_writes(session: Session) -> None:
    # A nested SAVEPOINT commit leaves the root transaction active.  Publication
    # must wait until the database intent is durably committed at the root.
    if session.in_nested_transaction():
        return
    publications = session.info.pop(PENDING_PUBLICATIONS_KEY, [])
    for publication in publications:
        _publish_staged_blob(publication)


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_blob_writes(session: Session) -> None:
    if session.in_nested_transaction():
        return
    publications = session.info.pop(PENDING_PUBLICATIONS_KEY, [])
    for publication in publications:
        _discard_staged_blob(publication)


def stage_content_addressed_bytes(
    contents: bytes,
    *,
    root: str | Path | None = None,
) -> tuple[str, str, dict[str, Any] | None]:
    """Create an operation-owned durable staging write for a DB transaction."""

    content_hash = hashlib.sha256(contents).hexdigest()
    destination = _blob_destination(content_hash, root=root)
    if destination.exists():
        existing_hash, existing_size = _hash_file(destination)
        if existing_hash != content_hash or existing_size != len(contents):
            raise DrBlobPlaneError("existing content-addressed blob failed hash/size validation")
        return content_hash, str(destination), None
    root_path = Path(root or settings.dr_blob_root)
    operation_id = str(uuid4())
    operation_dir = root_path / ".pending" / operation_id
    operation_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    staged_path = operation_dir / "blob"
    publication = {
        "schema": "dr-blob-pending-publication-v1",
        "operation_id": operation_id,
        "content_hash": content_hash,
        "size_bytes": len(contents),
        "staged_path": str(staged_path),
        "final_path": str(destination),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # The fsync'd intent precedes the bytes.  A crash at any later point leaves
    # enough evidence for the scanner to publish or quarantine safely.
    write_secure_atomic_bytes(
        operation_dir / "intent.json",
        canonical_json_bytes(publication),
        label="pending DR blob publication",
        max_size=16 * 1024,
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(staged_path, flags, 0o600)
    try:
        _write_all(fd, contents)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        _discard_staged_blob(publication)
        raise
    else:
        os.close(fd)
    _fsync_directory(operation_dir)
    return content_hash, str(destination), publication


def persist_content_addressed_bytes(contents: bytes, *, root: str | Path | None = None) -> tuple[str, str]:
    content_hash = hashlib.sha256(contents).hexdigest()
    destination = _blob_destination(content_hash, root=root)
    destination_dir = destination.parent
    destination_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
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
        _fsync_directory(destination_dir)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return content_hash, str(destination)


def persist_content_addressed_file(
    source,
    *,
    expected_hash: str,
    expected_size: int,
    root: str | Path | None = None,
) -> str:
    """Stream a verified temporary plaintext into the immutable local CAS."""

    destination = _blob_destination(expected_hash, root=root)
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = destination.parent / f".{expected_hash}.{os.getpid()}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    digest = hashlib.sha256()
    size = 0
    try:
        source.seek(0)
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if size > expected_size:
                raise DrBlobPlaneError("decrypted blob exceeds its expected size")
            _write_all(fd, chunk)
        os.fsync(fd)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(fd)
    if size != expected_size or digest.hexdigest() != expected_hash:
        temporary.unlink(missing_ok=True)
        raise DrBlobPlaneError("decrypted blob failed hash/size validation")
    if destination.exists():
        existing_hash, existing_size = _hash_file(destination)
        temporary.unlink(missing_ok=True)
        if existing_hash != expected_hash or existing_size != expected_size:
            raise DrBlobPlaneError("existing local blob conflicts with decrypted content")
        return str(destination)
    os.replace(temporary, destination)
    os.chmod(destination, 0o440)
    _fsync_directory(destination.parent)
    return str(destination)


async def bind_chat_file_blob(
    db: AsyncSession,
    *,
    chat_file: ChatFile,
    contents: bytes,
) -> tuple[str, str]:
    """Bind the DB intent to an operation-owned staged file publication."""

    strict_blob_plane = bool(getattr(settings, "dr_event_protocol_enabled", False))
    if not strict_blob_plane:
        content_hash, local_path = persist_content_addressed_bytes(contents)
        chat_file.s3_key = local_path
        chat_file.content_hash = content_hash
        chat_file.storage_version = 1
        return content_hash, local_path
    if db.in_nested_transaction():
        raise DrBlobPlaneError("strict DR blob publication is forbidden inside a SAVEPOINT")
    identity = resolve_runtime_identity(settings)
    if identity.physical_site not in WEBAPP_SITES:
        raise DrBlobPlaneError("chat blob production is restricted to a WebApp physical site")
    fence = current_writer_fence_context()
    if fence is None or fence.physical_site != identity.physical_site:
        raise DrBlobPlaneError("chat blob intent lacks the current WebApp writer term")
    publication: dict[str, Any] | None = None
    try:
        content_hash, local_path, publication = stage_content_addressed_bytes(contents)
        if publication is not None:
            db.sync_session.info.setdefault(PENDING_PUBLICATIONS_KEY, []).append(publication)
        chat_file.s3_key = local_path
        chat_file.content_hash = content_hash
        chat_file.storage_version = 1
        manifest = await db.get(DrBlobManifest, content_hash)
        if manifest is None:
            manifest = DrBlobManifest(
                content_hash=content_hash,
                size_bytes=len(contents),
                mime_type=chat_file.mime_type,
                local_path=local_path,
                object_key=None,
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
    except Exception:
        if publication is not None:
            pending = db.sync_session.info.get(PENDING_PUBLICATIONS_KEY, [])
            if publication in pending:
                pending.remove(publication)
            _discard_staged_blob(publication)
        raise
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


def _managed_blob_files(root: Path, *, maximum: int) -> tuple[list[Path], int]:
    managed: list[Path] = []
    total_size = 0
    if not root.exists():
        return managed, total_size
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = [name for name in names if not Path(directory, name).is_symlink()]
        for filename in filenames:
            path = Path(directory, filename)
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                total_size += metadata.st_size
                if ".orphan-quarantine" not in path.parts:
                    managed.append(path)
            if len(managed) > maximum:
                raise DrBlobPlaneError("local blob orphan scan entry limit was exceeded")
    return managed, total_size


def _append_orphan_evidence(root: Path, *, event: str, **fields: Any) -> None:
    append_hash_chained_jsonl(
        root / ".orphan-evidence.jsonl",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        },
    )


def _quarantine_orphan(root: Path, path: Path, *, reason: str) -> None:
    relative = str(path.relative_to(root))
    observed_hash: str | None = None
    observed_size: int | None = None
    if path.is_file():
        try:
            observed_hash, observed_size = _hash_file(path)
        except (OSError, DrBlobPlaneError):
            observed_size = path.lstat().st_size
    quarantine = root / ".orphan-quarantine" / datetime.now(timezone.utc).strftime("%Y%m%d")
    quarantine.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = quarantine / f"{uuid4().hex}-{path.name}"
    evidence = {
        "reason": reason,
        "original_path": relative,
        "quarantine_path": str(destination.relative_to(root)),
        "observed_sha256": observed_hash,
        "observed_size": observed_size,
    }
    # Persist the intended move first.  If power is lost after rename but
    # before the completion record, operators can still locate and reconcile
    # the bytes from a durable, hash-chained record.
    _append_orphan_evidence(root, event="dr.blob.orphan_quarantine_planned", **evidence)
    os.replace(path, destination)
    _fsync_directory(quarantine)
    _append_orphan_evidence(
        root,
        event="dr.blob.orphan_quarantined",
        **evidence,
    )


async def reconcile_orphaned_local_blobs(db: AsyncSession) -> int:
    """Publish committed staged writes and quarantine old untracked bytes.

    Unknown bytes are never deleted directly.  A hash-chained evidence record
    is fsync'd after each atomic move into the owner-only quarantine.
    """

    root = Path(settings.dr_blob_root)
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    maximum = max(1, int(settings.dr_blob_orphan_scan_max_entries))
    managed, total_size = _managed_blob_files(root, maximum=maximum)
    if total_size > max(1, int(settings.dr_blob_local_quota_bytes)):
        raise DrBlobPlaneError("local blob storage quota is exceeded")
    rows = (
        await db.execute(select(DrBlobManifest.content_hash, DrBlobManifest.local_path))
    ).all()
    manifests = {str(content_hash): str(local_path) for content_hash, local_path in rows}
    now = datetime.now(timezone.utc).timestamp()
    grace = max(60, int(settings.dr_blob_orphan_grace_seconds))
    changed = 0

    pending_root = root / ".pending"
    if pending_root.exists():
        for operation_dir in list(pending_root.iterdir())[:maximum]:
            if operation_dir.is_symlink() or not operation_dir.is_dir():
                continue
            intent_path = operation_dir / "intent.json"
            try:
                payload = json.loads(
                    read_secure_text(
                        intent_path,
                        label="pending DR blob publication",
                        max_size=16 * 1024,
                    )
                )
                required = {
                    "schema", "operation_id", "content_hash", "size_bytes",
                    "staged_path", "final_path", "created_at",
                }
                if (
                    not isinstance(payload, dict)
                    or set(payload) != required
                    or payload["schema"] != "dr-blob-pending-publication-v1"
                    or Path(str(payload["staged_path"])).parent != operation_dir
                    or not SHA256_RE.fullmatch(str(payload["content_hash"]))
                ):
                    raise DrBlobPlaneError("pending DR blob publication fields are invalid")
                content_hash = str(payload["content_hash"])
                if (
                    manifests.get(content_hash) == str(payload["final_path"])
                    and Path(str(payload["staged_path"])).exists()
                ):
                    _publish_staged_blob(payload)
                    changed += 1
                    continue
            except Exception:
                payload = None
            if now - operation_dir.stat().st_mtime >= grace:
                _quarantine_orphan(root, operation_dir, reason="stale_uncommitted_publication")
                changed += 1

    sha_root = root / "sha256"
    if sha_root.exists():
        for path in managed:
            if sha_root not in path.parents or not SHA256_RE.fullmatch(path.name):
                continue
            if path.name in manifests:
                if manifests[path.name] != str(path):
                    raise DrBlobPlaneError("manifest local path differs from content-addressed location")
                continue
            if now - path.lstat().st_mtime < grace:
                continue
            _quarantine_orphan(root, path, reason="untracked_content_addressed_blob")
            changed += 1
    return changed


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
