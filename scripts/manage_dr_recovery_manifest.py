#!/usr/bin/env python3
"""Create/verify a transaction-bound DB/event/blob recovery manifest."""

from __future__ import annotations

import argparse
import asyncio
import json

from core.dark_standby import assert_not_dark_standby
from core.db import AsyncSessionLocal, DrProjectionSessionLocal
from core.dr_blob_plane import create_recovery_manifest, verify_recovery_manifest
from core.runtime_identity import resolve_runtime_identity
from core.webapp_writer_control import load_writer_snapshot
from core.writer_fencing import writer_fence_scope
from core.writer_fencing import projection_fence_scope
from core.config import settings


async def run(
    action: str,
    manifest_id: str | None,
    *,
    manifest_kind: str,
    expected_writer_epoch: int | None,
) -> dict:
    assert_not_dark_standby("recovery_manifest")
    identity = resolve_runtime_identity(settings)
    async with AsyncSessionLocal() as session:
        snapshot = await load_writer_snapshot(session)
    scope = (
        writer_fence_scope(
            identity,
            snapshot,
            source="manage_dr_recovery_manifest",
            require_witness_lease=bool(settings.writer_witness_required),
        )
        if manifest_kind == "origin"
        else projection_fence_scope(source="manage_dr_promotion_manifest")
    )
    session_factory = (
        AsyncSessionLocal if manifest_kind == "origin" else DrProjectionSessionLocal
    )
    with scope:
        async with session_factory() as session:
            if action == "create":
                manifest = await create_recovery_manifest(
                    session,
                    manifest_kind=manifest_kind,
                    expected_writer_epoch=expected_writer_epoch,
                )
            else:
                if not manifest_id:
                    raise RuntimeError("--manifest-id is required for verify")
                manifest = await verify_recovery_manifest(
                    session,
                    manifest_id,
                    manifest_kind=manifest_kind,
                    expected_writer_epoch=expected_writer_epoch,
                )
            await session.commit()
            return {
                "status": manifest.status,
                "manifest_id": manifest.manifest_id,
                "manifest_hash": manifest.manifest_hash,
                "manifest_kind": manifest.manifest_kind,
                "site": manifest.physical_site,
                "writer_epoch": manifest.writer_epoch,
                "release_sha": manifest.release_sha,
                "blob_count": manifest.blob_count,
                "blob_set_hash": manifest.blob_set_hash,
                "event_checkpoint_hash": manifest.event_checkpoint_hash,
                "database_row_count": manifest.database_row_count,
                "database_fingerprint_hash": manifest.database_fingerprint_hash,
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("--manifest-id")
    parser.add_argument("--kind", choices=("promotion", "origin"), default="origin")
    parser.add_argument("--expected-writer-epoch", type=int)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run(
                args.action,
                args.manifest_id,
                manifest_kind=args.kind,
                expected_writer_epoch=args.expected_writer_epoch,
            )
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    if args.action == "verify" and result.get("status") != "verified":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
