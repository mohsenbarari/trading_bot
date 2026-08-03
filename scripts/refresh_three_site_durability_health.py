#!/usr/bin/env python3
"""Refresh the staging durability gate from independently verifiable evidence.

Invoke only through the ``webapp_fi_durability_health`` one-shot Compose
service.  The service has the WebApp-FI control database role, journal
read-back authentication, and read-only access to source Blob receipts.  It
has no application database role, Object Storage credential, public ingress,
or long-running loop.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import re

from sqlalchemy import select

from core.config import settings
from core.dark_standby import assert_not_dark_standby
from core.db import DrControlSessionLocal, verify_three_site_database_role_bindings
from core.dr_durability_health import (
    BlobReceiptEvidence,
    DurabilityHealthError,
    build_durability_health_update,
)
from core.dr_durability_journal_client import recover_prepared_journal_by_gid
from core.runtime_identity import resolve_runtime_identity
from models.dr_event import DrBlobDelivery, DrBlobManifest, DrDurabilityState


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


async def refresh_durability_health(
    *,
    journal_gid: str,
    blob_content_hash: str,
    operator: str,
    max_blob_age_seconds: int,
    ttl_seconds: int,
) -> dict[str, object]:
    assert_not_dark_standby("durability_health_controller")
    identity = resolve_runtime_identity(settings)
    if identity.physical_site != "webapp_fi" or not identity.is_webapp_authority:
        raise DurabilityHealthError("durability health controller is WebApp-FI only")
    if not (
        settings.dr_same_region_journal_enabled
        and settings.dr_same_region_journal_two_phase_enabled
    ):
        raise DurabilityHealthError("same-region two-phase journal is not enabled")
    release_sha = str(settings.release_sha or "")
    if not release_sha:
        raise DurabilityHealthError("release SHA is missing")
    if _SHA256.fullmatch(str(blob_content_hash or "")) is None:
        raise DurabilityHealthError("Blob content hash is invalid")

    await verify_three_site_database_role_bindings()
    # This is a live TLS request to Bot-FI.  The client verifies the signed
    # recovery acknowledgement before this value reaches the control database.
    journal = await asyncio.to_thread(
        recover_prepared_journal_by_gid,
        local_transaction_gid=journal_gid,
    )
    now = datetime.now(timezone.utc)
    async with DrControlSessionLocal() as session:
        state = await session.get(DrDurabilityState, 1, with_for_update=True)
        if state is None:
            raise DurabilityHealthError("DR durability state is missing")
        row = (
            await session.execute(
                select(DrBlobDelivery, DrBlobManifest)
                .join(
                    DrBlobManifest,
                    DrBlobManifest.content_hash == DrBlobDelivery.content_hash,
                )
                .where(
                    DrBlobDelivery.content_hash == blob_content_hash,
                    DrBlobDelivery.destination_site == "webapp_ir",
                )
            )
        ).one_or_none()
        if row is None:
            raise DurabilityHealthError("exact Blob delivery evidence is missing")
        delivery, manifest = row
        update = build_durability_health_update(
            connectivity_mode=str(state.connectivity_mode),
            connectivity_evidence_hash=state.evidence_hash,
            connectivity_evidence_expires_at=state.evidence_expires_at,
            journal_gid=journal_gid,
            journal=journal,
            blob=BlobReceiptEvidence(
                content_hash=str(delivery.content_hash),
                destination_site=str(delivery.destination_site),
                delivery_status=str(delivery.status),
                acknowledged_at=delivery.acknowledged_at,
                acknowledgement_hash=delivery.acknowledgement_hash,
                manifest_state=str(manifest.state),
                object_version_id=manifest.object_version_id,
                object_ciphertext_hash=manifest.object_ciphertext_hash,
                object_ciphertext_size=manifest.object_ciphertext_size,
                encryption_key_id=manifest.encryption_key_id,
                encryption_algorithm=manifest.encryption_algorithm,
            ),
            release_sha=release_sha,
            operator=operator,
            now=now,
            max_blob_age_seconds=max_blob_age_seconds,
            ttl_seconds=ttl_seconds,
        )
        state.event_journal_healthy = True
        state.blob_journal_healthy = True
        state.evidence_hash = update.evidence_hash
        state.evidence_expires_at = update.evidence_expires_at
        state.updated_by = update.updated_by
        await session.commit()
    return {
        "status": "recorded",
        "release_sha": release_sha,
        "journal_state": journal["state"],
        "blob_destination": "webapp_ir",
        "evidence_hash": update.evidence_hash,
        "evidence_expires_at": update.evidence_expires_at.isoformat(),
        "data_transfer": "none",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-gid", required=True)
    parser.add_argument("--blob-content-hash", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--max-blob-age-seconds", type=int, default=120)
    parser.add_argument("--ttl-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            refresh_durability_health(
                journal_gid=args.journal_gid,
                blob_content_hash=args.blob_content_hash,
                operator=args.operator,
                max_blob_age_seconds=args.max_blob_age_seconds,
                ttl_seconds=args.ttl_seconds,
            )
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
