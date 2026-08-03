#!/usr/bin/env python3
"""Run the bounded, Object-Storage-only Stage 4R transport probes.

The event probe republishes a *previously applied* FI->IR event batch.  It is
therefore a non-mutating authenticated replay: no product row or source event
ledger is created or edited.  The Blob probe is deliberately separate from
``chat_files``.  It writes one small, content-addressed staging marker to the
existing internal Blob plane, verifies its encrypted Object Storage read-back
in IR, and returns the receipt through Object Storage.

This utility is intentionally executable only from the two WebApp delivery or
Blob worker images on the reviewed staging bucket.  It neither imports an HTTP
client nor accepts peer URLs; Finland/Iran payloads can only use the existing
versioned Object Storage protocol helpers.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID

from sqlalchemy import func, select


# ``docker compose run --entrypoint python`` executes this file from
# ``/app/scripts``.  Keep the one-shot self-contained instead of relying on a
# caller-provided PYTHONPATH; the worker image itself remains the only source
# of protocol code and credentials.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import DrProjectionSessionLocal
from core.dr_blob_crypto import load_blob_keyring
from core.dr_blob_plane import persist_content_addressed_bytes
from core.dr_blob_worker import (
    _download_and_verify,
    _key_for_destination,
    _persist_reported_blob_receipt,
    _verify_blob_receipt_ack,
    load_s3_config,
    upload_one_blob,
)
from core.dr_delivery_worker import ObjectStorageTransport
from core.dr_event_protocol import canonical_json_bytes, event_envelope_from_record
from core.dr_event_receiver import receive_object_storage_batch
from core.dr_object_storage import validate_versioned_bucket
from core.dr_object_transport import (
    DrObjectTransportMissing,
    load_blob_receipt_ack,
    load_event_receipt,
    load_event_record,
    publish_blob_receipt_record,
    publish_event_receipt,
    publish_event_record,
)
from core.dr_sync_auth import parse_pairwise_keys, sign_acknowledgement
from core.runtime_identity import resolve_runtime_identity
from core.writer_fencing import projection_fence_scope
from models.dr_event import DrBlobDelivery, DrBlobManifest, DrBlobReceipt, DrEvent, DrEventDelivery


PROBE_SCHEMA = "three-site-stage4r-object-storage-probe-v1"
STAGING_BUCKET = "gold-trade-staging-three-site-dr"
WEBAPP_FI = "webapp_fi"
WEBAPP_IR = "webapp_ir"
PROBE_MIME_TYPE = "application/vnd.gold-trade.stage4r-probe+json"
MAX_RECEIPT_WAIT_SECONDS = 90


class Stage4rObjectProbeError(RuntimeError):
    """Raised when the bounded staging probe cannot prove its exact boundary."""


def _require_scope(expected_site: str) -> None:
    identity = resolve_runtime_identity(settings)
    if settings.environment != "staging":
        raise Stage4rObjectProbeError("Stage 4R Object Storage probe is staging-only")
    if identity.physical_site != expected_site:
        raise Stage4rObjectProbeError("probe was invoked from the wrong physical site")
    if expected_site not in {WEBAPP_FI, WEBAPP_IR}:
        raise Stage4rObjectProbeError("probe site is outside the WebApp pair")
    config = load_s3_config()
    if config.bucket != STAGING_BUCKET:
        raise Stage4rObjectProbeError("probe refuses a bucket other than the reviewed staging bucket")
    validate_versioned_bucket(config)


def _run_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise Stage4rObjectProbeError("probe run id must be a UUID") from exc


def _release_sha() -> str:
    value = str(settings.release_sha or "")
    if len(value) not in {40, 64} or any(char not in "0123456789abcdef" for char in value):
        raise Stage4rObjectProbeError("probe requires an exact lowercase release SHA")
    return value


def _probe_blob_bytes(*, run_id: str, release_sha: str) -> bytes:
    return canonical_json_bytes(
        {
            "schema": PROBE_SCHEMA,
            "kind": "non_business_blob_readback",
            "run_id": run_id,
            "release_sha": release_sha,
        }
    )


def _json_receipt(value: dict[str, Any]) -> None:
    """Write a short receipt only; never include payload bytes or credentials."""

    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def _pairwise_key(*, source_site: str, destination_site: str):  # noqa: ANN202
    keys = parse_pairwise_keys(settings.dr_sync_pairwise_keys_json)
    return keys, _key_for_destination(
        keys, source_site=source_site, destination_site=destination_site
    )


def _require_applied_results(acknowledgement: dict[str, Any], event_ids: tuple[str, ...]) -> None:
    results = acknowledgement.get("results")
    if not isinstance(results, list) or len(results) != len(event_ids):
        raise Stage4rObjectProbeError("event replay acknowledgement cardinality is invalid")
    observed = [str(item.get("event_id") or "") for item in results if isinstance(item, dict)]
    if len(observed) != len(event_ids) or set(observed) != set(event_ids):
        raise Stage4rObjectProbeError("event replay acknowledgement identity is invalid")
    if any(not isinstance(item, dict) or item.get("status") != "applied" for item in results):
        raise Stage4rObjectProbeError("event replay did not reach applied status")


async def _event_publish() -> None:
    _require_scope(WEBAPP_FI)
    config = load_s3_config()
    keyring = load_blob_keyring(settings.dr_blob_encryption_keyring_file)
    keys, key = _pairwise_key(source_site=WEBAPP_FI, destination_site=WEBAPP_IR)
    del keys
    async with DrProjectionSessionLocal() as session:
        rows = (
            await session.execute(
                select(DrEvent)
                .join(DrEventDelivery, DrEventDelivery.event_id == DrEvent.event_id)
                .where(
                    DrEventDelivery.destination_site == WEBAPP_IR,
                    DrEventDelivery.status == "acknowledged",
                )
                .order_by(DrEvent.created_at, DrEvent.event_id)
                .limit(2)
            )
        ).scalars().all()
    if len(rows) != 2:
        raise Stage4rObjectProbeError("two previously applied FI-to-IR events are required")
    envelopes = tuple(event_envelope_from_record(row) for row in rows)
    event, stored = await asyncio.to_thread(
        publish_event_record,
        config,
        body=canonical_json_bytes({"events": list(envelopes)}),
        source_site=WEBAPP_FI,
        destination_site=WEBAPP_IR,
        key=key,
        keyring=keyring,
    )
    _json_receipt(
        {
            "schema": PROBE_SCHEMA,
            "kind": "non_mutating_event_replay_source",
            "event_count": len(envelopes),
            "event_ids": [item["event_id"] for item in envelopes],
            "event_object_key": stored.object_key,
            "event_object_version_id": stored.version_id,
            "event_record_hash": event.record_hash,
        }
    )


async def _event_consume(object_key: str) -> None:
    _require_scope(WEBAPP_IR)
    if not object_key.startswith(str(settings.dr_object_transport_prefix).rstrip("/") + "/event/"):
        raise Stage4rObjectProbeError("event object key is outside the configured transport prefix")
    config = load_s3_config()
    keyring = load_blob_keyring(settings.dr_blob_encryption_keyring_file)
    _keys, key = _pairwise_key(source_site=WEBAPP_FI, destination_site=WEBAPP_IR)
    event, stored = await asyncio.to_thread(
        load_event_record,
        config,
        object_key=object_key,
        source_site=WEBAPP_FI,
        destination_site=WEBAPP_IR,
        key=key,
        keyring=keyring,
    )
    event_ids = tuple(str(item.get("event_id") or "") for item in event.events)
    if len(event_ids) != 2 or not all(event_ids):
        raise Stage4rObjectProbeError("event replay record has an unexpected batch shape")
    with projection_fence_scope(source="stage4r_object_probe_event_receive"):
        async with DrProjectionSessionLocal() as session:
            acknowledgement = await receive_object_storage_batch(
                session,
                raw_envelopes=list(event.events),
                local_site=WEBAPP_IR,
                source_site=WEBAPP_FI,
                key_id=key.key_id,
                request_hash=event.request_hash,
            )
            await session.commit()
    acknowledgement["acknowledgement_mac"] = sign_acknowledgement(
        payload=acknowledgement, secret=key.secret
    )
    _require_applied_results(acknowledgement, event_ids)
    receipt = await asyncio.to_thread(
        publish_event_receipt,
        config,
        event=event,
        stored=stored,
        acknowledgement=acknowledgement,
        key=key,
        keyring=keyring,
    )
    _json_receipt(
        {
            "schema": PROBE_SCHEMA,
            "kind": "non_mutating_event_replay_receipt",
            "event_count": len(event_ids),
            "event_object_version_id": stored.version_id,
            "receipt_object_key": receipt.object_key,
            "receipt_object_version_id": receipt.object_version_id,
            "receipt_hash": receipt.receipt_hash,
        }
    )


async def _event_verify(object_key: str) -> None:
    _require_scope(WEBAPP_FI)
    config = load_s3_config()
    keyring = load_blob_keyring(settings.dr_blob_encryption_keyring_file)
    _keys, key = _pairwise_key(source_site=WEBAPP_FI, destination_site=WEBAPP_IR)
    event, stored = await asyncio.to_thread(
        load_event_record,
        config,
        object_key=object_key,
        source_site=WEBAPP_FI,
        destination_site=WEBAPP_IR,
        key=key,
        keyring=keyring,
    )
    receipt = await asyncio.to_thread(
        load_event_receipt,
        config,
        event=event,
        stored=stored,
        key=key,
        keyring=keyring,
    )
    _require_applied_results(
        receipt.acknowledgement,
        tuple(str(item.get("event_id") or "") for item in event.events),
    )
    _json_receipt(
        {
            "schema": PROBE_SCHEMA,
            "kind": "non_mutating_event_replay_verified",
            "event_object_version_id": stored.version_id,
            "receipt_object_version_id": receipt.object_version_id,
            "receipt_hash": receipt.receipt_hash,
        }
    )


async def _blob_source(run_id: str) -> None:
    _require_scope(WEBAPP_FI)
    release_sha = _release_sha()
    config = load_s3_config()
    keyring = load_blob_keyring(settings.dr_blob_encryption_keyring_file)
    async with DrProjectionSessionLocal() as session:
        pending = int(
            await session.scalar(
                select(func.count())
                .select_from(DrBlobDelivery)
                .where(DrBlobDelivery.status.in_(("pending_upload", "failed")))
            )
            or 0
        )
    if pending:
        raise Stage4rObjectProbeError("probe refuses to overtake an existing Blob delivery")
    contents = _probe_blob_bytes(run_id=run_id, release_sha=release_sha)
    content_hash, local_path = persist_content_addressed_bytes(contents)
    with projection_fence_scope(source="stage4r_object_probe_blob_seed"):
        async with DrProjectionSessionLocal() as session:
            if await session.get(DrBlobManifest, content_hash) is not None:
                raise Stage4rObjectProbeError("probe content hash unexpectedly already exists")
            session.add(
                DrBlobManifest(
                    content_hash=content_hash,
                    size_bytes=len(contents),
                    mime_type=PROBE_MIME_TYPE,
                    local_path=local_path,
                    state="local",
                )
            )
            session.add(
                DrBlobDelivery(
                    content_hash=content_hash,
                    destination_site=WEBAPP_IR,
                    status="pending_upload",
                    attempt_count=0,
                )
            )
            await session.commit()
    result = await upload_one_blob(config, keyring)
    if result not in {"uploaded", "idle"}:
        raise Stage4rObjectProbeError("probe Blob upload did not complete")
    async with DrProjectionSessionLocal() as session:
        manifest = await session.get(DrBlobManifest, content_hash)
        delivery = await session.get(DrBlobDelivery, (content_hash, WEBAPP_IR))
    if (
        manifest is None
        or delivery is None
        or manifest.state != "uploaded"
        or delivery.status != "available"
        or not manifest.object_version_id
        or not manifest.object_key
    ):
        raise Stage4rObjectProbeError("probe Blob source object is not durably available")
    _json_receipt(
        {
            "schema": PROBE_SCHEMA,
            "kind": "non_business_blob_source",
            "run_id": run_id,
            "content_hash": content_hash,
            "size_bytes": len(contents),
            "blob_object_key": manifest.object_key,
            "blob_object_version_id": manifest.object_version_id,
            "blob_ciphertext_hash": manifest.object_ciphertext_hash,
        }
    )


async def _blob_consume(content_hash: str, size_bytes: int) -> None:
    _require_scope(WEBAPP_IR)
    if len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash):
        raise Stage4rObjectProbeError("probe content hash is invalid")
    if not 1 <= size_bytes <= 4096:
        raise Stage4rObjectProbeError("probe Blob size is outside its bounded range")
    config = load_s3_config()
    keyring = load_blob_keyring(settings.dr_blob_encryption_keyring_file)
    local_path, stored = await asyncio.to_thread(
        _download_and_verify,
        config,
        keyring,
        content_hash=content_hash,
        expected_size=size_bytes,
        mime_type=PROBE_MIME_TYPE,
    )
    unsigned_receipt = {
        "content_hash": content_hash,
        "size_bytes": size_bytes,
        "object_version_id": stored.version_id,
        "object_ciphertext_hash": stored.identity.ciphertext_hash,
        "object_ciphertext_size": stored.identity.ciphertext_size,
        "encryption_key_id": stored.identity.key_id,
        "encryption_algorithm": stored.identity.algorithm,
    }
    receipt_hash = hashlib.sha256(canonical_json_bytes(unsigned_receipt)).hexdigest()
    with projection_fence_scope(source="stage4r_object_probe_blob_readback"):
        async with DrProjectionSessionLocal() as session:
            manifest = await session.get(DrBlobManifest, content_hash)
            if manifest is None:
                session.add(
                    DrBlobManifest(
                        content_hash=content_hash,
                        size_bytes=size_bytes,
                        mime_type=PROBE_MIME_TYPE,
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
                int(manifest.size_bytes) != size_bytes
                or manifest.object_version_id != stored.version_id
                or manifest.object_ciphertext_hash != stored.identity.ciphertext_hash
            ):
                raise Stage4rObjectProbeError("probe Blob conflicts with an existing IR manifest")
            receipt_key = (content_hash, WEBAPP_IR, WEBAPP_FI)
            existing = await session.get(DrBlobReceipt, receipt_key)
            if existing is None:
                session.add(
                    DrBlobReceipt(
                        content_hash=content_hash,
                        destination_site=WEBAPP_IR,
                        origin_physical_site=WEBAPP_FI,
                        object_version_id=stored.version_id,
                        size_bytes=size_bytes,
                        object_ciphertext_hash=stored.identity.ciphertext_hash,
                        object_ciphertext_size=stored.identity.ciphertext_size,
                        encryption_key_id=stored.identity.key_id,
                        encryption_algorithm=stored.identity.algorithm,
                        local_path=local_path,
                        receipt_hash=receipt_hash,
                    )
                )
            elif existing.receipt_hash != receipt_hash:
                raise Stage4rObjectProbeError("probe Blob receipt conflicts with existing IR evidence")
            await session.commit()
    _keys, key = _pairwise_key(source_site=WEBAPP_IR, destination_site=WEBAPP_FI)
    snapshot = {**unsigned_receipt, "receipt_hash": receipt_hash}
    record, receipt_object = await asyncio.to_thread(
        publish_blob_receipt_record,
        config,
        body=canonical_json_bytes(snapshot),
        source_site=WEBAPP_IR,
        destination_site=WEBAPP_FI,
        key=key,
        keyring=keyring,
    )
    acknowledgement = None
    for _attempt in range(MAX_RECEIPT_WAIT_SECONDS):
        try:
            acknowledgement = await asyncio.to_thread(
                load_blob_receipt_ack,
                config,
                record=record,
                stored=receipt_object,
                key=key,
                keyring=keyring,
            )
            break
        except DrObjectTransportMissing:
            await asyncio.sleep(1)
    if acknowledgement is None:
        raise Stage4rObjectProbeError("probe Blob receipt acknowledgement was not published")
    acknowledgement_hash = _verify_blob_receipt_ack(
        acknowledgement.acknowledgement,
        destination_site=WEBAPP_FI,
        request_hash=record.request_hash,
        content_hash=content_hash,
        receipt_hash=receipt_hash,
        key=key,
    )
    await _persist_reported_blob_receipt(
        local_site=WEBAPP_IR,
        destination=WEBAPP_FI,
        snapshot=snapshot,
        acknowledgement_hash=acknowledgement_hash,
    )
    _json_receipt(
        {
            "schema": PROBE_SCHEMA,
            "kind": "non_business_blob_receipt",
            "content_hash": content_hash,
            "blob_object_version_id": stored.version_id,
            "receipt_object_key": receipt_object.object_key,
            "receipt_object_version_id": receipt_object.version_id,
            "receipt_ack_object_version_id": acknowledgement.object_version_id,
            "source_acknowledgement_hash": acknowledgement_hash,
        }
    )


async def _blob_verify(content_hash: str) -> None:
    _require_scope(WEBAPP_FI)
    async with DrProjectionSessionLocal() as session:
        manifest = await session.get(DrBlobManifest, content_hash)
        delivery = await session.get(DrBlobDelivery, (content_hash, WEBAPP_IR))
    if (
        manifest is None
        or delivery is None
        or delivery.status != "acknowledged"
        or not delivery.acknowledgement_hash
        or not manifest.object_version_id
    ):
        raise Stage4rObjectProbeError("probe Blob delivery is not terminally acknowledged at source")
    _json_receipt(
        {
            "schema": PROBE_SCHEMA,
            "kind": "non_business_blob_verified",
            "content_hash": content_hash,
            "blob_object_version_id": manifest.object_version_id,
            "delivery_acknowledgement_hash": delivery.acknowledgement_hash,
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("event-publish")
    consume = sub.add_parser("event-consume")
    consume.add_argument("--object-key", required=True)
    verify = sub.add_parser("event-verify")
    verify.add_argument("--object-key", required=True)
    blob_source = sub.add_parser("blob-source")
    blob_source.add_argument("--run-id", required=True, type=_run_id)
    blob_consume = sub.add_parser("blob-consume")
    blob_consume.add_argument("--content-hash", required=True)
    blob_consume.add_argument("--size-bytes", required=True, type=int)
    blob_verify = sub.add_parser("blob-verify")
    blob_verify.add_argument("--content-hash", required=True)
    return parser


async def _run(args: argparse.Namespace) -> None:
    if args.command == "event-publish":
        await _event_publish()
    elif args.command == "event-consume":
        await _event_consume(args.object_key)
    elif args.command == "event-verify":
        await _event_verify(args.object_key)
    elif args.command == "blob-source":
        await _blob_source(args.run_id)
    elif args.command == "blob-consume":
        await _blob_consume(args.content_hash, args.size_bytes)
    elif args.command == "blob-verify":
        await _blob_verify(args.content_hash)
    else:  # pragma: no cover - argparse keeps this unreachable.
        raise Stage4rObjectProbeError("unknown probe command")


def main() -> None:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except Stage4rObjectProbeError as exc:
        raise SystemExit(f"stage4r Object Storage probe failed: {exc}") from exc


if __name__ == "__main__":
    main()
