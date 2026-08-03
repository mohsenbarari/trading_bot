"""Resumable Object Storage transport for content-addressed WebApp blobs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
import tempfile
from typing import Any
import httpx
from botocore.exceptions import ClientError
from sqlalchemy import String, and_, cast, or_, select, update

from api.routers.dr_sync import apply_blob_receipt
from core.config import settings
from core.dark_standby import assert_not_dark_standby
from core.db import DrProjectionSessionLocal, verify_three_site_database_role_bindings
from core.dr_blob_plane import (
    DrBlobPlaneError,
    garbage_collect_expired_local_blobs,
    mark_unreferenced_blobs_tombstoned,
    persist_content_addressed_file,
    reconcile_orphaned_local_blobs,
)
from core.dr_blob_crypto import (
    ENCRYPTION_ALGORITHM,
    FORMAT_OVERHEAD,
    CiphertextIdentity,
    DrBlobCryptoError,
    DrBlobKeyring,
    ciphertext_identity_from_provider,
    decrypt_blob_stream,
    encrypted_object_key,
    encrypt_local_blob,
    load_blob_keyring,
    metadata_for_ciphertext,
)
from core.dr_delivery_worker import _key_for_destination, parse_peer_urls
from core.dr_event_receiver import DrEventReceiveError
from core.dr_event_protocol import canonical_json_bytes
from core.dr_object_storage import (
    ARVAN_ENDPOINT,
    ARVAN_REGION,
    DrObjectStorageError,
    S3Config,
    load_s3_config as _load_s3_config,
    object_not_found,
    object_storage_client,
    validate_versioned_bucket,
)
from core.dr_object_transport import (
    DrObjectTransportError,
    DrObjectTransportMissing,
    list_blob_receipt_record_keys,
    load_blob_receipt_ack,
    load_blob_receipt_record,
    publish_blob_receipt_ack,
    publish_blob_receipt_record,
    uses_object_storage_blob_receipt_transport,
)
from core.dr_sync_auth import (
    PairwiseDrKey,
    acknowledgement_signature_is_valid,
    canonical_request_bytes,
    parse_pairwise_keys,
    sign_acknowledgement,
    sign_request,
)
from core.runtime_identity import resolve_runtime_identity
from core.writer_fencing import projection_fence_scope
from models.chat_file import ChatFile
from models.dr_event import DrBlobDelivery, DrBlobManifest, DrBlobReceipt, DrEvent


MAX_BLOB_BYTES = 50 * 1024 * 1024
BLOB_RECEIPTS_PATH = "/api/dr-sync/blob-receipts"


class DrBlobWorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObjectStorageBlobResult:
    identity: CiphertextIdentity
    version_id: str | None
    etag: str | None


def load_s3_config() -> S3Config:
    try:
        return _load_s3_config()
    except Exception as exc:
        raise DrBlobWorkerError(str(exc)) from exc


def _client(config: S3Config):  # noqa: ANN001
    return object_storage_client(config)


def validate_s3_controls(config: S3Config) -> None:
    """Fail startup unless the transport bucket has the required durability."""

    try:
        validate_versioned_bucket(config)
    except DrObjectStorageError as exc:
        raise DrBlobWorkerError(str(exc)) from exc


async def maintain_blob_retention() -> str:
    with projection_fence_scope(source="dr_blob_retention"):
        async with DrProjectionSessionLocal() as session:
            reconciled = await reconcile_orphaned_local_blobs(session)
            tombstoned = await mark_unreferenced_blobs_tombstoned(session)
            deleted = await garbage_collect_expired_local_blobs(session)
            await session.commit()
    return "changed" if reconciled or tombstoned or deleted else "idle"


def _not_found(exc: ClientError) -> bool:
    return object_not_found(exc)


def _verify_object_body(
    result: dict[str, Any],
    *,
    identity: CiphertextIdentity,
    manifest: dict[str, Any],
    keyring: DrBlobKeyring,
) -> None:
    with tempfile.TemporaryFile(mode="w+b") as plaintext:
        decrypt_blob_stream(
            result["Body"],
            ciphertext_size=identity.ciphertext_size,
            expected_ciphertext_hash=identity.ciphertext_hash,
            content_hash=manifest["content_hash"],
            size_bytes=int(manifest["size_bytes"]),
            mime_type=manifest["mime_type"],
            object_key=identity.object_key,
            key_id=identity.key_id,
            keyring=keyring,
            plaintext_sink=plaintext,
        )


def _existing_object(
    client,
    config: S3Config,
    *,
    manifest: dict[str, Any],
    keyring: DrBlobKeyring,
) -> ObjectStorageBlobResult | None:
    key = str(manifest["object_key"])
    try:
        head = client.head_object(Bucket=config.bucket, Key=key)
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    identity = ciphertext_identity_from_provider(
        object_key=key,
        content_length=head.get("ContentLength"),
        metadata=head.get("Metadata"),
    )
    if (
        identity.key_id != manifest["encryption_key_id"]
        or identity.ciphertext_size != int(manifest["size_bytes"]) + FORMAT_OVERHEAD
        or (
            manifest.get("object_ciphertext_hash")
            and identity.ciphertext_hash != manifest["object_ciphertext_hash"]
        )
    ):
        raise DrBlobWorkerError("existing encrypted object conflicts with the immutable manifest")
    result = client.get_object(Bucket=config.bucket, Key=key)
    _verify_object_body(result, identity=identity, manifest=manifest, keyring=keyring)
    version_id = result.get("VersionId") or head.get("VersionId")
    if settings.dr_blob_require_versioning and not version_id:
        raise DrBlobWorkerError("existing encrypted object lacks a version identity")
    return ObjectStorageBlobResult(
        identity=identity,
        version_id=version_id,
        etag=str(head.get("ETag") or "").strip('"') or None,
    )


def _head_or_upload(
    config: S3Config,
    manifest: dict[str, Any],
    keyring: DrBlobKeyring,
) -> ObjectStorageBlobResult:
    client = _client(config)
    existing = _existing_object(client, config, manifest=manifest, keyring=keyring)
    if existing is not None:
        return existing
    encrypted, identity, _ = encrypt_local_blob(
        local_path=manifest["local_path"],
        content_hash=manifest["content_hash"],
        size_bytes=int(manifest["size_bytes"]),
        mime_type=manifest["mime_type"],
        object_key=manifest["object_key"],
        key_id=manifest["encryption_key_id"],
        keyring=keyring,
    )
    try:
        result = client.put_object(
            Bucket=config.bucket,
            Key=identity.object_key,
            Body=encrypted,
            ContentLength=identity.ciphertext_size,
            ContentType="application/octet-stream",
            Metadata=metadata_for_ciphertext(identity),
        )
    finally:
        encrypted.close()
    head = client.head_object(Bucket=config.bucket, Key=identity.object_key)
    observed = ciphertext_identity_from_provider(
        object_key=identity.object_key,
        content_length=head.get("ContentLength"),
        metadata=head.get("Metadata"),
    )
    if observed != identity:
        raise DrBlobWorkerError("uploaded encrypted object failed read-back identity validation")
    version_id = head.get("VersionId") or result.get("VersionId")
    if settings.dr_blob_require_versioning and not version_id:
        raise DrBlobWorkerError("uploaded encrypted object lacks a version identity")
    return ObjectStorageBlobResult(
        identity=identity,
        version_id=version_id,
        etag=str(head.get("ETag") or "").strip('"') or None,
    )


async def upload_one_blob(config: S3Config, keyring: DrBlobKeyring) -> str:
    identity = resolve_runtime_identity(settings)
    now = datetime.now(timezone.utc)
    with projection_fence_scope(source="dr_blob_upload_claim"):
        async with DrProjectionSessionLocal() as session:
            row = await session.scalar(
                select(DrBlobDelivery)
                .where(
                    DrBlobDelivery.status.in_(("pending_upload", "failed")),
                    or_(DrBlobDelivery.next_attempt_at.is_(None), DrBlobDelivery.next_attempt_at <= now),
                )
                .order_by(DrBlobDelivery.updated_at, DrBlobDelivery.content_hash)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return "idle"
            manifest = await session.get(DrBlobManifest, row.content_hash)
            if manifest is None:
                row.status = "quarantined"
                row.last_error_code = "manifest_missing"
                await session.commit()
                return "quarantined"
            if manifest.object_key is None:
                manifest.encryption_key_id = keyring.active_key_id
                manifest.encryption_algorithm = ENCRYPTION_ALGORITHM
                manifest.object_key = encrypted_object_key(
                    manifest.content_hash,
                    prefix=settings.dr_blob_object_prefix,
                    keyring=keyring,
                    key_id=keyring.active_key_id,
                )
            elif (
                manifest.encryption_algorithm != ENCRYPTION_ALGORITHM
                or not manifest.encryption_key_id
            ):
                row.status = "quarantined"
                row.last_error_code = "cipher_identity_missing"
                await session.commit()
                return "quarantined"
            keyring.key(str(manifest.encryption_key_id))
            row.attempt_count = int(row.attempt_count or 0) + 1
            row.last_attempt_at = now
            row.status = "failed"
            row.next_attempt_at = now + timedelta(seconds=30)
            content_hash = row.content_hash
            destination = row.destination_site
            snapshot = {
                "content_hash": manifest.content_hash,
                "size_bytes": int(manifest.size_bytes),
                "mime_type": manifest.mime_type,
                "local_path": manifest.local_path,
                "object_key": manifest.object_key,
                "encryption_key_id": manifest.encryption_key_id,
                "encryption_algorithm": manifest.encryption_algorithm,
                "object_ciphertext_hash": manifest.object_ciphertext_hash,
            }
            await session.commit()
    try:
        stored = await asyncio.to_thread(_head_or_upload, config, snapshot, keyring)
    except Exception as exc:
        with projection_fence_scope(source="dr_blob_upload_failed"):
            async with DrProjectionSessionLocal() as session:
                row = await session.get(DrBlobDelivery, (content_hash, destination), with_for_update=True)
                if row is not None:
                    row.status = "failed"
                    row.last_error_code = type(exc).__name__[:64]
                    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=min(300, 2 ** min(8, int(row.attempt_count or 1)))
                    )
                    await session.commit()
        return "retry"
    with projection_fence_scope(source="dr_blob_upload_complete"):
        async with DrProjectionSessionLocal() as session:
            row = await session.get(DrBlobDelivery, (content_hash, destination), with_for_update=True)
            manifest = await session.get(DrBlobManifest, content_hash, with_for_update=True)
            if row is None or manifest is None:
                raise DrBlobWorkerError("blob delivery state disappeared after upload")
            manifest.state = "uploaded"
            manifest.object_version_id = stored.version_id
            manifest.object_etag = stored.etag
            manifest.object_ciphertext_hash = stored.identity.ciphertext_hash
            manifest.object_ciphertext_size = stored.identity.ciphertext_size
            manifest.encryption_key_id = stored.identity.key_id
            manifest.encryption_algorithm = stored.identity.algorithm
            manifest.uploaded_at = datetime.now(timezone.utc)
            row.status = "available"
            row.next_attempt_at = None
            row.last_error_code = None
            await session.commit()
    return "uploaded"


def _download_and_verify(
    config: S3Config,
    keyring: DrBlobKeyring,
    *,
    content_hash: str,
    expected_size: int,
    mime_type: str,
) -> tuple[str, ObjectStorageBlobResult]:
    client = _client(config)
    if expected_size > MAX_BLOB_BYTES:
        raise DrBlobWorkerError("encrypted Object Storage blob exceeds the local size limit")
    for key_id in keyring.discovery_order():
        object_key = encrypted_object_key(
            content_hash,
            prefix=settings.dr_blob_object_prefix,
            keyring=keyring,
            key_id=key_id,
        )
        try:
            result = client.get_object(Bucket=config.bucket, Key=object_key)
        except ClientError as exc:
            if _not_found(exc):
                continue
            raise
        identity = ciphertext_identity_from_provider(
            object_key=object_key,
            content_length=result.get("ContentLength"),
            metadata=result.get("Metadata"),
        )
        if identity.key_id != key_id or identity.ciphertext_size != expected_size + FORMAT_OVERHEAD:
            raise DrBlobWorkerError("encrypted Object Storage identity is inconsistent")
        with tempfile.TemporaryFile(mode="w+b") as plaintext:
            decrypt_blob_stream(
                result["Body"],
                ciphertext_size=identity.ciphertext_size,
                expected_ciphertext_hash=identity.ciphertext_hash,
                content_hash=content_hash,
                size_bytes=expected_size,
                mime_type=mime_type,
                object_key=object_key,
                key_id=key_id,
                keyring=keyring,
                plaintext_sink=plaintext,
            )
            local_path = persist_content_addressed_file(
                plaintext,
                expected_hash=content_hash,
                expected_size=expected_size,
            )
        version_id = result.get("VersionId")
        if settings.dr_blob_require_versioning and not version_id:
            raise DrBlobWorkerError("downloaded encrypted object lacks a version identity")
        return local_path, ObjectStorageBlobResult(
            identity=identity,
            version_id=version_id,
            etag=str(result.get("ETag") or "").strip('"') or None,
        )
    raise DrBlobWorkerError("encrypted Object Storage blob is unavailable for every retained key")


async def download_one_blob(config: S3Config, keyring: DrBlobKeyring) -> str:
    identity = resolve_runtime_identity(settings)
    async with DrProjectionSessionLocal() as session:
        candidate = (
            await session.execute(
                select(
                    ChatFile.content_hash,
                    ChatFile.size,
                    ChatFile.mime_type,
                    DrEvent.origin_physical_site,
                )
                .join(
                    DrEvent,
                    and_(
                        DrEvent.aggregate_type == "chat_files",
                        DrEvent.aggregate_db_id == cast(ChatFile.id, String),
                    ),
                )
                .outerjoin(
                    DrBlobReceipt,
                    and_(
                        DrBlobReceipt.content_hash == ChatFile.content_hash,
                        DrBlobReceipt.destination_site == identity.physical_site,
                        DrBlobReceipt.origin_physical_site == DrEvent.origin_physical_site,
                    ),
                )
                .where(
                    ChatFile.content_hash.is_not(None),
                    DrEvent.origin_physical_site != identity.physical_site,
                    DrBlobReceipt.content_hash.is_(None),
                )
                .order_by(ChatFile.created_at, ChatFile.id)
                .limit(1)
            )
        ).one_or_none()
    if candidate is None:
        return "idle"
    content_hash, expected_size, mime_type, origin_site = (
        str(candidate[0]),
        int(candidate[1]),
        str(candidate[2]),
        str(candidate[3]),
    )
    try:
        local_path, stored = await asyncio.to_thread(
            _download_and_verify,
            config,
            keyring,
            content_hash=content_hash,
            expected_size=expected_size,
            mime_type=mime_type,
        )
    except Exception:
        return "retry"
    with projection_fence_scope(source="dr_blob_download_complete"):
        async with DrProjectionSessionLocal() as session:
            receipt_key = (content_hash, identity.physical_site, origin_site)
            existing = await session.get(DrBlobReceipt, receipt_key)
            manifest = await session.get(DrBlobManifest, content_hash, with_for_update=True)
            if manifest is None:
                session.add(
                    DrBlobManifest(
                        content_hash=content_hash,
                        size_bytes=expected_size,
                        mime_type=mime_type,
                        local_path=local_path,
                        object_key=stored.identity.object_key,
                        object_version_id=stored.version_id,
                        object_etag=stored.etag,
                        object_ciphertext_hash=stored.identity.ciphertext_hash,
                        object_ciphertext_size=stored.identity.ciphertext_size,
                        encryption_key_id=stored.identity.key_id,
                        encryption_algorithm=stored.identity.algorithm,
                        state="uploaded",
                        uploaded_at=datetime.now(timezone.utc),
                    )
                )
            elif (
                int(manifest.size_bytes) != expected_size
                or manifest.local_path != local_path
                or manifest.object_key != stored.identity.object_key
                or manifest.object_ciphertext_hash != stored.identity.ciphertext_hash
                or int(manifest.object_ciphertext_size or 0) != stored.identity.ciphertext_size
                or manifest.encryption_key_id != stored.identity.key_id
                or manifest.encryption_algorithm != stored.identity.algorithm
            ):
                raise DrBlobWorkerError("downloaded blob conflicts with the local immutable manifest")
            if existing is None:
                unsigned_receipt = {
                    "content_hash": content_hash,
                    "size_bytes": expected_size,
                    "object_version_id": stored.version_id,
                    "object_ciphertext_hash": stored.identity.ciphertext_hash,
                    "object_ciphertext_size": stored.identity.ciphertext_size,
                    "encryption_key_id": stored.identity.key_id,
                    "encryption_algorithm": stored.identity.algorithm,
                }
                session.add(
                    DrBlobReceipt(
                        content_hash=content_hash,
                        destination_site=identity.physical_site,
                        origin_physical_site=origin_site,
                        object_version_id=stored.version_id,
                        size_bytes=expected_size,
                        object_ciphertext_hash=stored.identity.ciphertext_hash,
                        object_ciphertext_size=stored.identity.ciphertext_size,
                        encryption_key_id=stored.identity.key_id,
                        encryption_algorithm=stored.identity.algorithm,
                        local_path=local_path,
                        receipt_hash=hashlib.sha256(
                            canonical_json_bytes(unsigned_receipt)
                        ).hexdigest(),
                    )
                )
            await session.execute(
                update(ChatFile)
                .where(ChatFile.content_hash == content_hash)
                .values(s3_key=local_path)
                .execution_options(is_sync=True)
            )
            await session.commit()
    return "downloaded"


def _verify_blob_receipt_ack(
    payload: Any,
    *,
    destination_site: str,
    request_hash: str,
    content_hash: str,
    receipt_hash: str,
    key: PairwiseDrKey,
) -> str:
    expected_fields = {
        "destination_site",
        "source_site",
        "key_id",
        "request_hash",
        "content_hash",
        "receipt_hash",
        "delivery_hash",
        "acknowledgement_hash",
        "acknowledgement_mac",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise DrBlobWorkerError("DR blob receipt acknowledgement fields are invalid")
    if (
        payload["destination_site"] != destination_site
        or payload["source_site"] != key.source_site
        or payload["key_id"] != key.key_id
        or payload["request_hash"] != request_hash
        or payload["content_hash"] != content_hash
        or payload["receipt_hash"] != receipt_hash
    ):
        raise DrBlobWorkerError("DR blob receipt acknowledgement identity mismatch")
    signed = {
        name: value for name, value in payload.items() if name != "acknowledgement_mac"
    }
    if not acknowledgement_signature_is_valid(
        payload=signed,
        signature=str(payload["acknowledgement_mac"]),
        secret=key.secret,
    ):
        raise DrBlobWorkerError("DR blob receipt acknowledgement signature is invalid")
    unsigned = {
        name: value
        for name, value in payload.items()
        if name not in {"acknowledgement_hash", "acknowledgement_mac"}
    }
    expected = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    if not secrets.compare_digest(str(payload["acknowledgement_hash"]), expected):
        raise DrBlobWorkerError("DR blob receipt acknowledgement hash mismatch")
    delivery_hash = str(payload["delivery_hash"])
    if len(delivery_hash) != 64 or any(ch not in "0123456789abcdef" for ch in delivery_hash):
        raise DrBlobWorkerError("DR blob delivery acknowledgement hash is malformed")
    return delivery_hash


async def _persist_reported_blob_receipt(
    *,
    local_site: str,
    destination: str,
    snapshot: dict[str, Any],
    acknowledgement_hash: str,
) -> None:
    with projection_fence_scope(source="dr_blob_receipt_reported"):
        async with DrProjectionSessionLocal() as session:
            receipt = await session.get(
                DrBlobReceipt,
                (str(snapshot["content_hash"]), local_site, destination),
                with_for_update=True,
            )
            if receipt is None or receipt.receipt_hash != snapshot["receipt_hash"]:
                raise DrBlobWorkerError("DR blob receipt changed before acknowledgement persistence")
            receipt.source_acknowledgement_hash = acknowledgement_hash
            receipt.reported_at = datetime.now(timezone.utc)
            await session.commit()


async def _report_one_blob_receipt_via_object_storage(
    *,
    local_site: str,
    destination: str,
    snapshot: dict[str, Any],
    config: S3Config,
    keyring: DrBlobKeyring,
    keys: dict[str, PairwiseDrKey],
) -> str:
    key = _key_for_destination(keys, source_site=local_site, destination_site=destination)
    body = canonical_json_bytes(snapshot)
    try:
        record, stored = await asyncio.to_thread(
            publish_blob_receipt_record,
            config,
            body=body,
            source_site=local_site,
            destination_site=destination,
            key=key,
            keyring=keyring,
        )
        acknowledgement = await asyncio.to_thread(
            load_blob_receipt_ack,
            config,
            record=record,
            stored=stored,
            key=key,
            keyring=keyring,
        )
        acknowledgement_hash = _verify_blob_receipt_ack(
            acknowledgement.acknowledgement,
            destination_site=destination,
            request_hash=record.request_hash,
            content_hash=str(snapshot["content_hash"]),
            receipt_hash=str(snapshot["receipt_hash"]),
            key=key,
        )
    except (DrObjectTransportError, DrBlobWorkerError, ValueError):
        return "retry"
    await _persist_reported_blob_receipt(
        local_site=local_site,
        destination=destination,
        snapshot=snapshot,
        acknowledgement_hash=acknowledgement_hash,
    )
    return "acknowledged"


async def report_one_blob_receipt(
    *,
    local_site: str,
    client: httpx.AsyncClient,
    peer_urls: dict[str, str],
    keys,  # noqa: ANN001
    config: S3Config | None = None,
    keyring: DrBlobKeyring | None = None,
) -> str:
    async with DrProjectionSessionLocal() as session:
        receipt = await session.scalar(
            select(DrBlobReceipt)
            .where(
                DrBlobReceipt.destination_site == local_site,
                DrBlobReceipt.reported_at.is_(None),
            )
            .order_by(DrBlobReceipt.verified_at, DrBlobReceipt.content_hash)
            .limit(1)
        )
        if receipt is None:
            return "idle"
        snapshot = {
            "content_hash": receipt.content_hash,
            "size_bytes": int(receipt.size_bytes),
            "object_version_id": receipt.object_version_id,
            "object_ciphertext_hash": receipt.object_ciphertext_hash,
            "object_ciphertext_size": int(receipt.object_ciphertext_size),
            "encryption_key_id": receipt.encryption_key_id,
            "encryption_algorithm": receipt.encryption_algorithm,
            "receipt_hash": receipt.receipt_hash,
            "origin_site": receipt.origin_physical_site,
        }
    destination = str(snapshot.pop("origin_site"))
    if uses_object_storage_blob_receipt_transport(
        source_site=local_site, destination_site=destination
    ):
        if config is None or keyring is None:
            return "retry"
        return await _report_one_blob_receipt_via_object_storage(
            local_site=local_site,
            destination=destination,
            snapshot=snapshot,
            config=config,
            keyring=keyring,
            keys=keys,
        )
    key = _key_for_destination(keys, source_site=local_site, destination_site=destination)
    body = canonical_json_bytes(snapshot)
    timestamp = int(datetime.now(timezone.utc).timestamp())
    nonce = secrets.token_urlsafe(32)
    signature = sign_request(
        secret=key.secret,
        method="POST",
        path=BLOB_RECEIPTS_PATH,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        key_id=key.key_id,
        source_site=local_site,
        destination_site=destination,
    )
    headers = {
        "content-type": "application/json",
        "x-dr-protocol": "dr-sync-v1",
        "x-dr-key-id": key.key_id,
        "x-dr-source-site": local_site,
        "x-dr-destination-site": destination,
        "x-dr-timestamp": str(timestamp),
        "x-dr-nonce": nonce,
        "x-dr-signature": signature,
    }
    request_hash = hashlib.sha256(
        canonical_request_bytes(
            method="POST",
            path=BLOB_RECEIPTS_PATH,
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            key_id=key.key_id,
            source_site=local_site,
            destination_site=destination,
        )
    ).hexdigest()
    try:
        response = await client.post(
            peer_urls[destination] + BLOB_RECEIPTS_PATH,
            content=body,
            headers=headers,
        )
        response.raise_for_status()
        acknowledgement_hash = _verify_blob_receipt_ack(
            response.json(),
            destination_site=destination,
            request_hash=request_hash,
            content_hash=str(snapshot["content_hash"]),
            receipt_hash=str(snapshot["receipt_hash"]),
            key=key,
        )
    except (httpx.HTTPError, ValueError, DrBlobWorkerError):
        return "retry"
    await _persist_reported_blob_receipt(
        local_site=local_site,
        destination=destination,
        snapshot=snapshot,
        acknowledgement_hash=acknowledgement_hash,
    )
    return "acknowledged"


async def consume_one_object_storage_blob_receipt(
    *,
    local_site: str,
    config: S3Config,
    keyring: DrBlobKeyring,
    keys: dict[str, PairwiseDrKey],
) -> str:
    """Apply one peer blob-receipt request without cross-site HTTP."""

    if local_site not in {"webapp_fi", "webapp_ir"}:
        return "idle"
    source_site = "webapp_ir" if local_site == "webapp_fi" else "webapp_fi"
    if not uses_object_storage_blob_receipt_transport(
        source_site=source_site, destination_site=local_site
    ):
        return "idle"
    key = _key_for_destination(keys, source_site=source_site, destination_site=local_site)
    try:
        object_keys = await asyncio.to_thread(
            list_blob_receipt_record_keys,
            config,
            source_site=source_site,
            destination_site=local_site,
            key=key,
        )
    except DrObjectTransportError:
        return "retry"
    for object_key in object_keys:
        try:
            record, stored = await asyncio.to_thread(
                load_blob_receipt_record,
                config,
                object_key=object_key,
                source_site=source_site,
                destination_site=local_site,
                key=key,
                keyring=keyring,
            )
            try:
                await asyncio.to_thread(
                    load_blob_receipt_ack,
                    config,
                    record=record,
                    stored=stored,
                    key=key,
                    keyring=keyring,
                )
                continue
            except DrObjectTransportMissing:
                pass
            with projection_fence_scope(source="dr_object_storage_blob_receipt_receive"):
                async with DrProjectionSessionLocal() as session:
                    acknowledgement = await apply_blob_receipt(
                        session,
                        payload=record.body,
                        local_site=local_site,
                        source_site=source_site,
                        key_id=key.key_id,
                        request_hash=record.request_hash,
                    )
                    await session.commit()
            acknowledgement["acknowledgement_mac"] = sign_acknowledgement(
                payload=acknowledgement, secret=key.secret
            )
            await asyncio.to_thread(
                publish_blob_receipt_ack,
                config,
                record=record,
                stored=stored,
                acknowledgement=acknowledgement,
                key=key,
                keyring=keyring,
            )
            return "acknowledged"
        except (DrObjectTransportError, DrBlobWorkerError, DrEventReceiveError, ValueError):
            return "retry"
    return "idle"


async def dr_blob_loop() -> None:
    assert_not_dark_standby("dr_blob_worker")
    await verify_three_site_database_role_bindings()
    config = load_s3_config()
    keyring = load_blob_keyring(settings.dr_blob_encryption_keyring_file)
    await asyncio.to_thread(validate_s3_controls, config)
    identity = resolve_runtime_identity(settings)
    peer_urls = parse_peer_urls(settings.dr_sync_peer_urls_json, local_site=identity.physical_site)
    keys = parse_pairwise_keys(settings.dr_sync_pairwise_keys_json)
    verify: bool | str = settings.dr_sync_ca_bundle or True
    async with httpx.AsyncClient(
        verify=verify,
        timeout=max(1.0, float(settings.dr_sync_http_timeout_seconds)),
        follow_redirects=False,
    ) as client:
        while True:
            received_receipt = await consume_one_object_storage_blob_receipt(
                local_site=identity.physical_site,
                config=config,
                keyring=keyring,
                keys=keys,
            )
            uploaded = await upload_one_blob(config, keyring)
            downloaded = await download_one_blob(config, keyring)
            reported = await report_one_blob_receipt(
                local_site=identity.physical_site,
                client=client,
                peer_urls=peer_urls,
                keys=keys,
                config=config,
                keyring=keyring,
            )
            retention = await maintain_blob_retention()
            if received_receipt == uploaded == downloaded == reported == retention == "idle":
                await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(dr_blob_loop())
