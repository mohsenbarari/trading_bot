"""Destination-specific delivery worker for immutable three-site DR events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import or_, select

from core.config import settings
from core.dark_standby import assert_not_dark_standby
from core.db import DrProjectionSessionLocal, verify_three_site_database_role_bindings
from core.dr_event_protocol import canonical_json_bytes, transport_peers, validate_envelope
from core.dr_sync_auth import (
    PairwiseDrKey,
    acknowledgement_signature_is_valid,
    canonical_request_bytes,
    parse_pairwise_keys,
    sign_request,
)
from core.runtime_identity import resolve_runtime_identity
from core.runtime_sites import PHYSICAL_SITES
from core.writer_fencing import projection_fence_scope
from models.dr_event import DrEvent, DrEventDelivery


DR_EVENTS_PATH = "/api/dr-sync/events"


class DrDeliveryError(RuntimeError):
    """Raised when peer configuration or acknowledgement is unsafe."""


def _mark_delivery_attempt(delivery: Any, *, now: datetime, local_site: str) -> None:
    """Claim one row without erasing the first delivery timestamp on retries."""

    delivery.status = "inflight"
    delivery.attempt_count = int(delivery.attempt_count or 0) + 1
    delivery.first_attempt_at = delivery.first_attempt_at or now
    delivery.last_attempt_at = now
    delivery.last_error_code = None
    delivery.next_attempt_at = now + timedelta(
        seconds=max(5, int(settings.dr_delivery_claim_seconds))
    )
    delivery.relay_site = delivery.relay_site or local_site


@dataclass(frozen=True)
class ClaimedDeliveryBatch:
    claim_id: str
    destination_site: str
    event_ids: tuple[str, ...]
    envelopes: tuple[dict[str, Any], ...]


def _strict_json(raw: str | None) -> Any:
    def reject_duplicates(pairs):  # noqa: ANN001
        result = {}
        for key, value in pairs:
            if key in result:
                raise DrDeliveryError(f"duplicate DR peer field: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw or "", object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, DrDeliveryError) as exc:
        raise DrDeliveryError("DR peer URL configuration is not strict JSON") from exc


def parse_peer_urls(raw: str | None, *, local_site: str) -> dict[str, str]:
    payload = _strict_json(raw)
    if not isinstance(payload, list):
        raise DrDeliveryError("DR peer URLs must be a list")
    result: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"site", "base_url"}:
            raise DrDeliveryError("DR peer URL entry fields are invalid")
        site = str(item["site"])
        base_url = str(item["base_url"]).rstrip("/")
        if site not in PHYSICAL_SITES or site == local_site or site in result:
            raise DrDeliveryError("DR peer site is unknown, local, or duplicate")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise DrDeliveryError("DR peer base URL must be a credential-free HTTPS origin")
        result[site] = base_url
    expected = set(transport_peers(local_site))
    if set(result) != expected:
        raise DrDeliveryError("DR peer URL configuration does not match the fixed sparse topology")
    return result


def _key_for_destination(
    keys: dict[str, PairwiseDrKey], *, source_site: str, destination_site: str
) -> PairwiseDrKey:
    matches = [
        key
        for key in keys.values()
        if key.source_site == source_site and key.destination_site == destination_site
    ]
    if len(matches) != 1:
        raise DrDeliveryError("exactly one active directed DR key is required")
    return matches[0]


def event_envelope(event: DrEvent) -> dict[str, Any]:
    created_at = event.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    payload = {
        "protocol_version": event.protocol_version,
        "event_id": event.event_id,
        "origin_authority": event.origin_authority,
        "origin_physical_site": event.origin_physical_site,
        "producer_epoch": event.producer_epoch,
        "producer_sequence": event.producer_sequence,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "aggregate_db_id": event.aggregate_db_id,
        "aggregate_version": event.aggregate_version,
        "operation": event.operation,
        "canonical_payload": event.canonical_payload,
        "canonical_payload_hash": event.canonical_payload_hash,
        "schema_version": event.schema_version,
        "causation_id": event.causation_id,
        "idempotency_key": event.idempotency_key,
        "writer_epoch": event.writer_epoch,
        "tombstone": event.tombstone,
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
    }
    if int(event.protocol_version) >= 2:
        payload.update(
            transaction_id=event.transaction_id,
            transaction_position=event.transaction_position,
            transaction_size=event.transaction_size,
            transaction_hash=event.transaction_hash,
            destination_streams=event.destination_streams,
        )
    validated = validate_envelope(payload)
    if validated.envelope_hash != event.envelope_hash:
        raise DrDeliveryError(f"stored DR event hash mismatch for {event.event_id}")
    return validated.payload


async def claim_delivery_batch(*, local_site: str) -> ClaimedDeliveryBatch | None:
    now = datetime.now(timezone.utc)
    limit = max(1, min(500, int(settings.dr_delivery_batch_size)))
    claim_id = str(uuid4())
    with projection_fence_scope(source="dr_delivery_claim"):
        async with DrProjectionSessionLocal() as session:
            first = await session.scalar(
                select(DrEventDelivery)
                .join(DrEvent, DrEvent.event_id == DrEventDelivery.event_id)
                .where(
                    DrEventDelivery.destination_site != local_site,
                    or_(
                        DrEventDelivery.status == "pending",
                        (DrEventDelivery.status == "inflight")
                        & (DrEventDelivery.next_attempt_at <= now),
                    ),
                    or_(DrEventDelivery.next_attempt_at.is_(None), DrEventDelivery.next_attempt_at <= now),
                )
                .order_by(
                    DrEventDelivery.destination_site,
                    DrEvent.origin_physical_site,
                    DrEvent.producer_epoch,
                    DrEvent.producer_sequence,
                )
                .with_for_update(of=DrEventDelivery, skip_locked=True)
                .limit(1)
            )
            if first is None:
                return None
            first_event = await session.get(DrEvent, first.event_id)
            if first_event is None:
                raise DrDeliveryError("delivery references a missing immutable event")
            rows = (
                await session.execute(
                    select(DrEventDelivery, DrEvent)
                    .join(DrEvent, DrEvent.event_id == DrEventDelivery.event_id)
                    .where(
                        DrEventDelivery.destination_site == first.destination_site,
                        DrEvent.origin_physical_site == first_event.origin_physical_site,
                        DrEvent.producer_epoch == first_event.producer_epoch,
                        or_(
                            DrEventDelivery.status == "pending",
                            (DrEventDelivery.status == "inflight")
                            & (DrEventDelivery.next_attempt_at <= now),
                        ),
                        or_(DrEventDelivery.next_attempt_at.is_(None), DrEventDelivery.next_attempt_at <= now),
                    )
                    .order_by(DrEvent.producer_sequence)
                    .with_for_update(of=DrEventDelivery, skip_locked=True)
                    .limit(limit)
                )
            ).all()
            if not rows:
                return None
            event_ids: list[str] = []
            envelopes: list[dict[str, Any]] = []
            for delivery, event_row in rows:
                # next_attempt_at doubles as a durable claim-expiry boundary.
                _mark_delivery_attempt(delivery, now=now, local_site=local_site)
                event_ids.append(event_row.event_id)
                envelopes.append(event_envelope(event_row))
            await session.commit()
    return ClaimedDeliveryBatch(
        claim_id=claim_id,
        destination_site=first.destination_site,
        event_ids=tuple(event_ids),
        envelopes=tuple(envelopes),
    )


def _verify_acknowledgement(
    payload: Any,
    *,
    batch: ClaimedDeliveryBatch,
    request_hash: str,
    key: PairwiseDrKey,
) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {
        "destination_site", "source_site", "key_id", "request_hash", "results",
        "acknowledgement_hash", "acknowledgement_mac",
    }:
        raise DrDeliveryError("DR acknowledgement fields are invalid")
    if (
        payload["destination_site"] != batch.destination_site
        or payload["source_site"] != key.source_site
        or payload["key_id"] != key.key_id
        or payload["request_hash"] != request_hash
    ):
        raise DrDeliveryError("DR acknowledgement identity mismatch")
    signed = {name: value for name, value in payload.items() if name != "acknowledgement_mac"}
    if not acknowledgement_signature_is_valid(
        payload=signed,
        signature=str(payload["acknowledgement_mac"]),
        secret=key.secret,
    ):
        raise DrDeliveryError("DR acknowledgement signature is invalid")
    unsigned = {
        name: value
        for name, value in payload.items()
        if name not in {"acknowledgement_hash", "acknowledgement_mac"}
    }
    expected_hash = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if not secrets.compare_digest(str(payload["acknowledgement_hash"]), expected_hash):
        raise DrDeliveryError("DR acknowledgement hash mismatch")
    results = payload["results"]
    if not isinstance(results, list) or len(results) != len(batch.event_ids):
        raise DrDeliveryError("DR acknowledgement result cardinality mismatch")
    by_id: dict[str, dict[str, Any]] = {}
    expected_hashes = {
        envelope["event_id"]: hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()
        for envelope in batch.envelopes
    }
    for result in results:
        if not isinstance(result, dict):
            raise DrDeliveryError("DR acknowledgement result is malformed")
        event_id = str(result.get("event_id") or "")
        if event_id not in expected_hashes or event_id in by_id:
            raise DrDeliveryError("DR acknowledgement contains unknown/duplicate event")
        status = str(result.get("status") or "")
        if status not in {"received", "applied", "blocked_gap", "duplicate", "quarantined"}:
            raise DrDeliveryError("DR acknowledgement status is invalid")
        if status != "quarantined" and result.get("envelope_hash") != expected_hashes[event_id]:
            raise DrDeliveryError("DR acknowledgement event hash mismatch")
        if status == "applied":
            envelope = next(
                item for item in batch.envelopes if item["event_id"] == event_id
            )
            stream = (
                envelope.get("destination_streams", {}).get(batch.destination_site)
                if int(envelope.get("protocol_version") or 1) >= 2
                else {"sequence": int(envelope["producer_sequence"])}
            )
            checkpoint = result.get("applied_checkpoint")
            if not isinstance(stream, dict) or not isinstance(checkpoint, dict):
                raise DrDeliveryError("applied acknowledgement lacks checkpoint evidence")
            expected_checkpoint = {
                "destination_site": batch.destination_site,
                "origin_physical_site": envelope["origin_physical_site"],
                "producer_epoch": envelope["producer_epoch"],
                "contiguous_applied_sequence": checkpoint.get(
                    "contiguous_applied_sequence"
                ),
                "event_id": event_id,
                "envelope_hash": expected_hashes[event_id],
            }
            expected_checkpoint_hash = hashlib.sha256(
                json.dumps(
                    expected_checkpoint,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if (
                set(checkpoint) != {*expected_checkpoint, "checkpoint_hash"}
                or type(checkpoint["contiguous_applied_sequence"]) is not int
                or checkpoint["contiguous_applied_sequence"] < int(stream["sequence"])
                or any(
                    checkpoint.get(name) != value
                    for name, value in expected_checkpoint.items()
                )
                or not secrets.compare_digest(
                    str(checkpoint.get("checkpoint_hash") or ""),
                    expected_checkpoint_hash,
                )
            ):
                raise DrDeliveryError("applied checkpoint evidence is inconsistent")
        by_id[event_id] = result
    return by_id


async def _finish_batch(
    batch: ClaimedDeliveryBatch,
    *,
    results: dict[str, dict[str, Any]] | None,
    acknowledgement_hash: str | None = None,
    error_code: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    with projection_fence_scope(source="dr_delivery_finish"):
        async with DrProjectionSessionLocal() as session:
            rows = (
                await session.execute(
                    select(DrEventDelivery)
                    .where(
                        DrEventDelivery.destination_site == batch.destination_site,
                        DrEventDelivery.event_id.in_(batch.event_ids),
                    )
                    .with_for_update()
                )
            ).scalars().all()
            if len(rows) != len(batch.event_ids):
                raise DrDeliveryError("claimed DR delivery rows disappeared")
            for row in rows:
                result = results.get(row.event_id) if results is not None else None
                _update_delivery_from_result(
                    row,
                    result=result,
                    now=now,
                    acknowledgement_hash=acknowledgement_hash,
                    error_code=error_code,
                )
            await session.commit()


def _update_delivery_from_result(
    row: Any,
    *,
    result: dict[str, Any] | None,
    now: datetime,
    acknowledgement_hash: str | None,
    error_code: str | None,
) -> None:
    """Persist only destination-applied evidence as a terminal ACK."""

    if result is not None and result["status"] == "applied":
        row.status = "acknowledged"
        row.acknowledged_at = now
        row.acknowledgement_hash = acknowledgement_hash
        row.next_attempt_at = None
        row.last_error_code = None
        return
    if result is not None and result["status"] == "quarantined":
        row.status = "quarantined"
        row.next_attempt_at = None
        row.last_error_code = str(result.get("reason") or "remote_quarantine")[:64]
        return
    row.status = "pending"
    delay = min(300, 2 ** min(8, int(row.attempt_count or 1)))
    row.next_attempt_at = now + timedelta(seconds=delay)
    remote_status = str(result.get("status")) if result is not None else None
    row.last_error_code = str(
        f"remote_{remote_status}" if remote_status else (error_code or "delivery_failed")
    )[:64]


async def deliver_batch(
    batch: ClaimedDeliveryBatch,
    *,
    local_site: str,
    client: httpx.AsyncClient,
    peer_urls: dict[str, str],
    keys: dict[str, PairwiseDrKey],
) -> str:
    key = _key_for_destination(keys, source_site=local_site, destination_site=batch.destination_site)
    body = canonical_json_bytes({"events": list(batch.envelopes)})
    timestamp = int(datetime.now(timezone.utc).timestamp())
    nonce = secrets.token_urlsafe(32)
    signature = sign_request(
        secret=key.secret,
        method="POST",
        path=DR_EVENTS_PATH,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        key_id=key.key_id,
        source_site=local_site,
        destination_site=batch.destination_site,
    )
    headers = {
        "content-type": "application/json",
        "x-dr-protocol": "dr-sync-v1",
        "x-dr-key-id": key.key_id,
        "x-dr-source-site": local_site,
        "x-dr-destination-site": batch.destination_site,
        "x-dr-timestamp": str(timestamp),
        "x-dr-nonce": nonce,
        "x-dr-signature": signature,
    }
    request_hash = hashlib.sha256(
        canonical_request_bytes(
            method="POST",
            path=DR_EVENTS_PATH,
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            key_id=key.key_id,
            source_site=local_site,
            destination_site=batch.destination_site,
        )
    ).hexdigest()
    try:
        response = await client.post(peer_urls[batch.destination_site] + DR_EVENTS_PATH, content=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
        results = _verify_acknowledgement(
            payload,
            batch=batch,
            request_hash=request_hash,
            key=key,
        )
        await _finish_batch(
            batch,
            results=results,
            acknowledgement_hash=str(payload["acknowledgement_hash"]),
        )
        return "acknowledged"
    except (httpx.HTTPError, ValueError, DrDeliveryError) as exc:
        await _finish_batch(batch, results=None, error_code=type(exc).__name__)
        return "retry"


async def dr_delivery_loop() -> None:
    assert_not_dark_standby("dr_delivery_worker")
    identity = resolve_runtime_identity(settings)
    if not settings.three_site_dr_enabled or not settings.dr_event_protocol_enabled:
        raise DrDeliveryError("DR delivery worker requires enabled three-site event protocol")
    await verify_three_site_database_role_bindings()
    if not settings.dr_sync_verify_tls:
        raise DrDeliveryError("three-site DR transport refuses disabled TLS verification")
    peer_urls = parse_peer_urls(settings.dr_sync_peer_urls_json, local_site=identity.physical_site)
    keys = parse_pairwise_keys(settings.dr_sync_pairwise_keys_json)
    verify: bool | str = settings.dr_sync_ca_bundle or True
    timeout = max(1.0, float(settings.dr_sync_http_timeout_seconds))
    async with httpx.AsyncClient(verify=verify, timeout=timeout, follow_redirects=False) as client:
        while True:
            batch = await claim_delivery_batch(local_site=identity.physical_site)
            if batch is None:
                await asyncio.sleep(max(0.05, float(settings.dr_delivery_poll_seconds)))
                continue
            await deliver_batch(
                batch,
                local_site=identity.physical_site,
                client=client,
                peer_urls=peer_urls,
                keys=keys,
            )


if __name__ == "__main__":
    asyncio.run(dr_delivery_loop())
