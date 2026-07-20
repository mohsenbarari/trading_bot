"""Strict authenticated ingress for immutable three-site DR event batches."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_dr_projection_db
from core.dr_event_receiver import DrEventReceiveError, receive_batch
from core.dr_event_receiver import reserve_replay_nonce
from core.dr_event_protocol import canonical_json_bytes
from core.dr_event_protocol import transport_peers
from core.dr_sync_auth import DrSyncAuthError, parse_pairwise_keys, verify_request
from core.runtime_identity import resolve_runtime_identity
from models.dr_event import DrBlobDelivery, DrBlobManifest


router = APIRouter()


def _authenticate(request: Request, body: bytes, *, destination_site: str):
    keys = parse_pairwise_keys(settings.dr_sync_pairwise_keys_json)
    return verify_request(
        method=request.method,
        path=request.url.path,
        body=body,
        headers={key.lower(): value for key, value in request.headers.items()},
        keys=keys,
        expected_destination_site=destination_site,
        max_age_seconds=settings.dr_sync_request_max_age_seconds,
    )


@router.post("/events")
async def receive_dr_events(request: Request, db: AsyncSession = Depends(get_dr_projection_db)):
    if not settings.three_site_dr_enabled or not settings.dr_event_protocol_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    identity = resolve_runtime_identity(settings)
    body = await request.body()
    if len(body) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="DR event batch is too large")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) != {"events"}:
            raise DrEventReceiveError("DR request body must contain only events")
        auth = _authenticate(request, body, destination_site=identity.physical_site)
        if auth.source_site not in transport_peers(identity.physical_site):
            raise DrEventReceiveError("DR event source is outside the fixed topology")
        result = await receive_batch(
            db,
            raw_envelopes=payload["events"],
            local_site=identity.physical_site,
            request=auth,
            nonce_ttl_seconds=settings.dr_sync_request_max_age_seconds * 2,
        )
        await db.commit()
        return result
    except (json.JSONDecodeError, DrSyncAuthError, DrEventReceiveError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/blob-receipts")
async def receive_blob_receipt(request: Request, db: AsyncSession = Depends(get_dr_projection_db)):
    """A destination proves local hash verification to the original WebApp site."""

    if not settings.three_site_dr_enabled or not settings.dr_event_protocol_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    identity = resolve_runtime_identity(settings)
    body = await request.body()
    if len(body) > 16 * 1024:
        raise HTTPException(status_code=413, detail="DR blob receipt is too large")
    try:
        payload = json.loads(body)
        required = {
            "content_hash", "size_bytes", "object_version_id",
            "object_ciphertext_hash", "object_ciphertext_size",
            "encryption_key_id", "encryption_algorithm", "receipt_hash",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise DrEventReceiveError("DR blob receipt fields are invalid")
        unsigned = {key: payload[key] for key in required - {"receipt_hash"}}
        expected_receipt_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        if payload["receipt_hash"] != expected_receipt_hash:
            raise DrEventReceiveError("DR blob receipt hash mismatch")
        content_hash = str(payload["content_hash"])
        if len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash):
            raise DrEventReceiveError("DR blob receipt content hash is malformed")
        if type(payload["size_bytes"]) is not int or payload["size_bytes"] < 0:
            raise DrEventReceiveError("DR blob receipt size is invalid")
        if (
            not isinstance(payload["object_ciphertext_hash"], str)
            or len(payload["object_ciphertext_hash"]) != 64
            or any(ch not in "0123456789abcdef" for ch in payload["object_ciphertext_hash"])
            or type(payload["object_ciphertext_size"]) is not int
            or payload["object_ciphertext_size"] < payload["size_bytes"]
            or payload["encryption_algorithm"] != "AES-256-GCM-v1"
            or not isinstance(payload["encryption_key_id"], str)
            or not payload["encryption_key_id"]
        ):
            raise DrEventReceiveError("DR blob receipt cipher identity is invalid")
        if settings.dr_blob_require_versioning and not str(payload["object_version_id"] or ""):
            raise DrEventReceiveError("DR blob receipt lacks required object version identity")
        auth = _authenticate(request, body, destination_site=identity.physical_site)
        if auth.source_site not in transport_peers(identity.physical_site):
            raise DrEventReceiveError("DR blob receipt source is outside the fixed topology")
        await reserve_replay_nonce(
            db,
            request=auth,
            expires_at=datetime.now(timezone.utc)
            + timedelta(
                seconds=max(60, settings.dr_sync_request_max_age_seconds * 2)
            ),
        )
        manifest = await db.get(DrBlobManifest, content_hash)
        delivery = await db.get(DrBlobDelivery, (content_hash, auth.source_site), with_for_update=True)
        if manifest is None or delivery is None:
            raise DrEventReceiveError("DR blob receipt does not match a local delivery intent")
        if delivery.status not in {"available", "acknowledged"} or manifest.state != "uploaded":
            raise DrEventReceiveError("DR blob receipt arrived before durable object availability")
        if int(manifest.size_bytes) != payload["size_bytes"]:
            raise DrEventReceiveError("DR blob receipt size conflicts with local manifest")
        if (
            manifest.object_ciphertext_hash != payload["object_ciphertext_hash"]
            or int(manifest.object_ciphertext_size or 0) != payload["object_ciphertext_size"]
            or manifest.encryption_key_id != payload["encryption_key_id"]
            or manifest.encryption_algorithm != payload["encryption_algorithm"]
        ):
            raise DrEventReceiveError("DR blob receipt cipher identity conflicts with local manifest")
        if (
            settings.dr_blob_require_versioning
            and (
                not manifest.object_version_id
                or str(manifest.object_version_id) != str(payload["object_version_id"])
            )
        ):
            raise DrEventReceiveError("DR blob receipt object version conflicts with local manifest")
        delivery.status = "acknowledged"
        delivery.acknowledged_at = datetime.now(timezone.utc)
        delivery.last_error_code = None
        delivery.next_attempt_at = None
        delivery_hash = hashlib.sha256(
            canonical_json_bytes(
                {
                    "content_hash": content_hash,
                    "destination_site": auth.source_site,
                    "receipt_hash": expected_receipt_hash,
                }
            )
        ).hexdigest()
        delivery.acknowledgement_hash = delivery_hash
        await db.commit()
        unsigned_ack = {
            "destination_site": identity.physical_site,
            "request_hash": auth.request_hash,
            "content_hash": content_hash,
            "receipt_hash": expected_receipt_hash,
            "delivery_hash": delivery_hash,
        }
        return {
            **unsigned_ack,
            "acknowledgement_hash": hashlib.sha256(canonical_json_bytes(unsigned_ack)).hexdigest(),
        }
    except (json.JSONDecodeError, DrSyncAuthError, DrEventReceiveError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
