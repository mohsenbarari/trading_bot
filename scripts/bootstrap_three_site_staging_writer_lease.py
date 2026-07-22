#!/usr/bin/env python3
"""Acquire and atomically import the initial WebApp-FI epoch-1 Witness lease."""

from __future__ import annotations

import argparse
import asyncio
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
from core.writer_witness_client import (
    WriterWitnessClientError,
    initialize_local_writer_lease_once,
    writer_witness_client_from_settings,
)


def confirmation_phrase(campaign_id: str, request_id: str, release_sha: str) -> str:
    return f"bootstrap-writer:{campaign_id}:{request_id}:{release_sha}"


async def run(args: argparse.Namespace) -> dict:
    try:
        campaign_id = str(UUID(args.campaign_id))
        request_id = str(UUID(args.request_id))
    except ValueError as exc:
        raise WriterWitnessClientError(
            "campaign-id and request-id must be UUIDs",
            code="writer_witness_bootstrap_identity_invalid",
        ) from exc
    release_sha = str(args.expected_release_sha).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise WriterWitnessClientError(
            "expected release SHA is malformed",
            code="writer_witness_bootstrap_release_invalid",
        )
    configured_release = str(settings.release_sha or "").lower()
    if configured_release != release_sha:
        raise WriterWitnessClientError(
            "runtime release differs from the migration campaign",
            code="writer_witness_bootstrap_release_mismatch",
        )
    identity = resolve_runtime_identity(settings)
    required = confirmation_phrase(campaign_id, request_id, release_sha)
    if not args.apply:
        return {
            "status": "planned",
            "campaign_id": campaign_id,
            "request_id": request_id,
            "release_sha": release_sha,
            "physical_site": identity.physical_site,
            "required_confirmation": required,
        }
    if args.confirm != required:
        raise WriterWitnessClientError(
            "initial Writer lease confirmation mismatch",
            code="writer_witness_bootstrap_confirmation_mismatch",
        )
    proof = await initialize_local_writer_lease_once(
        client=writer_witness_client_from_settings(identity),
        request_id=request_id,
        campaign_id=campaign_id,
        identity=identity,
    )
    return {
        "status": "initialized",
        "campaign_id": campaign_id,
        "request_id": request_id,
        "release_sha": release_sha,
        "holder_site": proof.holder_site,
        "writer_epoch": proof.writer_epoch,
        "lease_id": proof.lease_id,
        "witness_transition_id": proof.witness_transition_id,
        "proof_hash": proof.proof_hash,
        "expires_at": proof.expires_at.isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
