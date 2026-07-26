#!/usr/bin/env python3
"""Export one redacted WebApp-IR convergence snapshot through Object Storage.

This is intentionally separate from the non-egress ``*_sync_observer``
service.  It has the same read-only database role and read-only local CAS
mount, but receives only a short-lived presigned PUT descriptor at execution
time.  It has no Object Storage credential, does not retain the descriptor,
and returns only immutable transfer metadata over SSH.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.runtime_identity import resolve_runtime_identity
from scripts.collect_three_site_staging_convergence_snapshot import (
    SHA256,
    SHA40,
    ConvergenceSnapshotError,
    _canonical,
    collect,
)
from scripts.wa_ir_object_storage_preflight_agent import AgentError, upload_evidence


SCHEMA = "three-site-staging-convergence-snapshot-export-v1"
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


class ConvergenceExportError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConvergenceExportError("export descriptor has duplicate fields")
        result[key] = value
    return result


def _descriptor(
    encoded: str,
    *,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
) -> dict[str, Any]:
    if len(encoded) > 32 * 1024:
        raise ConvergenceExportError("export descriptor exceeds the control-plane bound")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ConvergenceExportError) as exc:
        raise ConvergenceExportError("export descriptor is not strict base64 JSON") from exc
    fields = {"schema", "campaign_id", "release_sha", "plan_sha256", "upload"}
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != SCHEMA
        or value.get("campaign_id") != campaign_id
        or value.get("release_sha") != release_sha
        or value.get("plan_sha256") != plan_sha256
    ):
        raise ConvergenceExportError("export descriptor identity is invalid")
    upload = value["upload"]
    if not isinstance(upload, dict):
        raise ConvergenceExportError("export upload descriptor is invalid")
    # ``upload_evidence`` performs the endpoint, HTTP method/header and
    # expected-status allowlist checks before making any network request.
    return upload


def _identity(campaign_id: str, release_sha: str, plan_sha256: str) -> None:
    try:
        if str(UUID(campaign_id)) != campaign_id:
            raise ValueError
        if SHA40.fullmatch(release_sha) is None or SHA256.fullmatch(plan_sha256) is None:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ConvergenceExportError("convergence export identity is invalid") from exc
    if resolve_runtime_identity().physical_site != "webapp_ir":
        raise ConvergenceExportError("convergence exporter is pinned to WebApp-IR")


async def export(
    *,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
    max_rows_per_table: int,
    upload: dict[str, Any],
) -> dict[str, Any]:
    _identity(campaign_id, release_sha, plan_sha256)
    snapshot = await collect(
        campaign_id=campaign_id,
        release_sha=release_sha,
        plan_sha256=plan_sha256,
        max_rows_per_table=max_rows_per_table,
    )
    payload = (_canonical(snapshot) + "\n").encode("utf-8")
    if not payload or len(payload) > MAX_SNAPSHOT_BYTES:
        raise ConvergenceExportError("redacted convergence snapshot exceeds its approved bound")
    with tempfile.TemporaryDirectory(prefix="three-site-convergence-") as raw:
        path = Path(raw) / "webapp-ir-redacted-snapshot.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise ConvergenceExportError("cannot write redacted convergence snapshot")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            result = upload_evidence(upload, path)
        except AgentError as exc:
            raise ConvergenceExportError("redacted convergence snapshot upload failed") from exc
    if result is None or result.get("sha256") != hashlib.sha256(payload).hexdigest() or result.get("bytes") != len(payload):
        raise ConvergenceExportError("redacted convergence snapshot upload receipt differs")
    return {
        "status": "uploaded",
        "site": "webapp_ir",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "plan_sha256": plan_sha256,
        "snapshot_sha256": str(result["sha256"]),
        "snapshot_bytes": int(result["bytes"]),
        "upload_http_status": int(result["http_status"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--upload-json-base64", required=True)
    parser.add_argument("--max-rows-per-table", type=int, default=10000)
    args = parser.parse_args(argv)
    try:
        _identity(args.campaign_id, args.release_sha, args.plan_sha256)
        upload = _descriptor(
            args.upload_json_base64,
            campaign_id=args.campaign_id,
            release_sha=args.release_sha,
            plan_sha256=args.plan_sha256,
        )
        print(json.dumps(asyncio.run(export(
            campaign_id=args.campaign_id,
            release_sha=args.release_sha,
            plan_sha256=args.plan_sha256,
            max_rows_per_table=args.max_rows_per_table,
            upload=upload,
        )), sort_keys=True))
        return 0
    except (ConvergenceSnapshotError, ConvergenceExportError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
