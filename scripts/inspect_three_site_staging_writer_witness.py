#!/usr/bin/env python3
"""Read the authenticated Writer-Witness status from one staging WebApp role."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.dr_event_protocol import canonical_json_bytes
from core.runtime_identity import resolve_runtime_identity
from core.writer_witness_client import WriterWitnessClientError, writer_witness_client_from_settings


async def run(args: argparse.Namespace) -> dict:
    try:
        request_id = str(UUID(args.request_id))
    except ValueError as exc:
        raise WriterWitnessClientError("status request id must be a UUID") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_release_sha):
        raise WriterWitnessClientError("status release SHA is invalid")
    if str(settings.release_sha or "").lower() != args.expected_release_sha:
        raise WriterWitnessClientError("status runtime release mismatch")
    identity = resolve_runtime_identity(settings)
    payload = await writer_witness_client_from_settings(identity).status(
        request_id=request_id
    )
    state = payload["state"]
    expires_at = None
    lease_live = False
    if state.get("expires_at") is not None:
        expires_at = datetime.fromisoformat(str(state["expires_at"]).replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            raise WriterWitnessClientError("status lease expiry lacks timezone")
        lease_live = expires_at.astimezone(timezone.utc) > datetime.now(timezone.utc)
    return {
        "status": "ok", "request_id": request_id,
        "observer_site": identity.physical_site,
        "holder_site": state.get("holder_site"),
        "writer_epoch": state.get("writer_epoch"),
        "lease_id": state.get("lease_id"),
        "lease_status": state.get("lease_status"),
        "expires_at": state.get("expires_at"),
        "lease_live": lease_live,
        "witness_receipt_hash": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--expected-release-sha", required=True)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
