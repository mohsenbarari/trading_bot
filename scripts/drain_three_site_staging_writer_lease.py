#!/usr/bin/env python3
"""Drain the active staging source Writer lease using one persistent request id."""

from __future__ import annotations

import argparse
import asyncio
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
from core.writer_witness_client import (
    WriterWitnessClientError,
    drain_local_writer_lease_once,
    writer_witness_client_from_settings,
)


def confirmation_phrase(operation_id: str, request_id: str, epoch: int) -> str:
    return f"drain-writer:{operation_id}:{request_id}:{epoch}"


async def run(args: argparse.Namespace) -> dict:
    try:
        operation_id = str(UUID(args.operation_id))
        request_id = str(UUID(args.request_id))
    except ValueError as exc:
        raise WriterWitnessClientError("operation/request id must be UUIDs") from exc
    if args.expected_epoch < 1 or not re.fullmatch(
        r"[0-9a-f]{40}", str(args.expected_release_sha)
    ):
        raise WriterWitnessClientError("drain epoch/release is invalid")
    if str(settings.release_sha or "").lower() != args.expected_release_sha:
        raise WriterWitnessClientError("drain runtime release mismatch")
    identity = resolve_runtime_identity(settings)
    required = confirmation_phrase(operation_id, request_id, args.expected_epoch)
    if not args.apply:
        return {
            "status": "planned", "operation_id": operation_id,
            "request_id": request_id, "source_site": identity.physical_site,
            "expected_epoch": args.expected_epoch, "required_confirmation": required,
        }
    if args.confirm != required:
        raise WriterWitnessClientError("drain confirmation mismatch")
    payload = await drain_local_writer_lease_once(
        client=writer_witness_client_from_settings(identity),
        request_id=request_id,
        operation_id=operation_id,
        expected_epoch=args.expected_epoch,
        identity=identity,
    )
    state = payload["state"]
    return {
        "status": "draining", "operation_id": operation_id,
        "request_id": request_id, "source_site": identity.physical_site,
        "writer_epoch": state["writer_epoch"], "lease_id": state["lease_id"],
        "expires_at": state["expires_at"],
        "witness_receipt_hash": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--expected-epoch", required=True, type=int)
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
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
