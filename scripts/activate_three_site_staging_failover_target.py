#!/usr/bin/env python3
"""Acquire the exact next Witness term and atomically activate a fenced target."""

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
from core.runtime_identity import resolve_runtime_identity
from core.secure_file_io import read_secure_text
from core.writer_witness_client import (
    WriterWitnessClientError,
    acquire_and_activate_local_writer_once,
    writer_witness_client_from_settings,
)


def confirmation_phrase(operation_id: str, request_id: str, epoch: int) -> str:
    return f"activate-target:{operation_id}:{request_id}:{epoch}"


def _read_readiness(path: Path) -> tuple[dict, str]:
    try:
        document = json.loads(
            read_secure_text(path, label="target readiness evidence", max_size=512 * 1024)
        )
    except Exception as exc:
        raise WriterWitnessClientError("target readiness evidence is invalid") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != "three-site-staging-target-readiness-v1"
        or document.get("status") != "ok"
        or not isinstance(document.get("readiness_evidence"), dict)
    ):
        raise WriterWitnessClientError("target readiness wrapper is invalid")
    digest = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return document, digest


async def run(args: argparse.Namespace) -> dict:
    try:
        operation_id = str(UUID(args.operation_id))
        status_request_id = str(UUID(args.status_request_id))
        acquire_request_id = str(UUID(args.acquire_request_id))
    except ValueError as exc:
        raise WriterWitnessClientError("operation/request ids must be UUIDs") from exc
    if args.target_epoch < 2 or not re.fullmatch(
        r"[0-9a-f]{40}", str(args.expected_release_sha)
    ):
        raise WriterWitnessClientError("target epoch/release is invalid")
    if str(settings.release_sha or "").lower() != args.expected_release_sha:
        raise WriterWitnessClientError("target activation runtime release mismatch")
    wrapper, wrapper_hash = _read_readiness(args.readiness_evidence)
    if (
        wrapper.get("operation_id") != operation_id
        or wrapper.get("release_sha") != args.expected_release_sha
        or wrapper.get("target_epoch") != args.target_epoch
    ):
        raise WriterWitnessClientError("target readiness belongs to another operation")
    identity = resolve_runtime_identity(settings)
    required = confirmation_phrase(operation_id, acquire_request_id, args.target_epoch)
    if not args.apply:
        return {
            "status": "planned", "operation_id": operation_id,
            "target_site": identity.physical_site, "target_epoch": args.target_epoch,
            "readiness_wrapper_sha256": wrapper_hash,
            "required_confirmation": required,
        }
    if args.confirm != required:
        raise WriterWitnessClientError("target activation confirmation mismatch")
    proof = await acquire_and_activate_local_writer_once(
        client=writer_witness_client_from_settings(identity),
        status_request_id=status_request_id,
        acquire_request_id=acquire_request_id,
        operation_id=operation_id,
        target_epoch=args.target_epoch,
        readiness_payload=wrapper["readiness_evidence"],
        identity=identity,
    )
    return {
        "status": "activated", "operation_id": operation_id,
        "target_site": proof.holder_site, "writer_epoch": proof.writer_epoch,
        "lease_id": proof.lease_id, "expires_at": proof.expires_at.isoformat(),
        "witness_transition_id": proof.witness_transition_id,
        "proof_hash": proof.proof_hash,
        "readiness_wrapper_sha256": wrapper_hash,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--status-request-id", required=True)
    parser.add_argument("--acquire-request-id", required=True)
    parser.add_argument("--target-epoch", required=True, type=int)
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--readiness-evidence", type=Path, required=True)
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
