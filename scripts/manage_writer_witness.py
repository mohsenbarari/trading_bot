#!/usr/bin/env python3
"""Dry-run-first local operator CLI for the WebApp writer witness."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
import sys
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.runtime_identity import SITE_WEBAPP_IR, resolve_runtime_identity
from core.writer_witness_control import (
    ACTION_ACQUIRE,
    ACTION_DRAIN,
    ACTION_RENEW,
    WriterWitnessError,
    load_witness_snapshot,
    persist_witness_rejection,
    transition_witness_state,
)
from core.writer_witness_contract import WitnessProofError, witness_timing_configuration_is_safe


ACTIONS = ("status", ACTION_ACQUIRE, ACTION_RENEW, ACTION_DRAIN)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect or transition WebApp writer witness state.")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--requester-site", choices=("webapp_fi", "webapp_ir"))
    parser.add_argument("--expected-epoch", type=int)
    parser.add_argument("--expected-lease-id", help="Use 'none' when no prior lease exists.")
    parser.add_argument("--request-id")
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    parser.add_argument("--private-key-file")
    parser.add_argument("--lease-duration-seconds", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def _expected_lease(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return None if normalized.lower() in {"", "none", "null", "-"} else normalized


def _state_payload(snapshot) -> dict:
    return {
        "holder_site": snapshot.holder_site,
        "writer_epoch": snapshot.writer_epoch,
        "lease_id": snapshot.lease_id,
        "lease_status": snapshot.lease_status,
        "issued_at": snapshot.issued_at.isoformat() if snapshot.issued_at else None,
        "expires_at": snapshot.expires_at.isoformat() if snapshot.expires_at else None,
        "transition_id": snapshot.transition_id,
    }


def _confirmation(action: str, site: str, epoch: int, lease_id: str | None) -> str:
    return f"witness:{action}:{site}:{epoch}:{lease_id or 'none'}"


def _read_private_key(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise WriterWitnessError("witness private key file must not be group/world accessible")
    value = path.read_text(encoding="utf-8").strip()
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise WriterWitnessError("witness private key file is not valid base64") from exc
    if len(decoded) != 32:
        raise WriterWitnessError("witness private key must decode to 32 bytes")
    return value


async def run(args: argparse.Namespace) -> dict:
    identity = resolve_runtime_identity(settings)
    configured_site = str(getattr(settings, "physical_site", None) or "").strip()
    authoritative_site = str(settings.writer_witness_authoritative_site or "").strip()
    if authoritative_site != SITE_WEBAPP_IR:
        raise WriterWitnessError(
            "this implementation supports only WRITER_WITNESS_AUTHORITATIVE_SITE=webapp_ir"
        )
    if configured_site != authoritative_site or identity.physical_site != authoritative_site:
        raise WriterWitnessError(
            "witness commands require explicit PHYSICAL_SITE=webapp_ir on the witness host"
        )
    async with AsyncSessionLocal() as session:
        before = await load_witness_snapshot(session)
    if args.action == "status":
        return {"status": "ok", "state": _state_payload(before)}
    if args.requester_site is None or args.expected_epoch is None:
        raise WriterWitnessError("--requester-site and --expected-epoch are mandatory")
    expected_lease_id = _expected_lease(args.expected_lease_id)
    if before.writer_epoch != args.expected_epoch or before.lease_id != expected_lease_id:
        raise WriterWitnessError(
            "dry-run expectation is stale: "
            f"current_epoch={before.writer_epoch} current_lease_id={before.lease_id!r}"
        )
    duration = int(
        args.lease_duration_seconds or settings.writer_witness_lease_duration_seconds
    )
    if not witness_timing_configuration_is_safe(
        lease_duration_seconds=duration,
        renew_interval_seconds=settings.writer_witness_renew_interval_seconds,
        safety_margin_seconds=settings.writer_witness_safety_margin_seconds,
        max_clock_skew_seconds=settings.writer_witness_max_clock_skew_seconds,
    ):
        raise WriterWitnessError("writer witness timing configuration is unsafe")
    request_id = str(args.request_id or uuid4())
    required_confirmation = _confirmation(
        args.action,
        args.requester_site,
        args.expected_epoch,
        expected_lease_id,
    )
    plan = {
        "status": "planned",
        "applied": False,
        "action": args.action,
        "requester_site": args.requester_site,
        "request_id": request_id,
        "lease_duration_seconds": duration,
        "before": _state_payload(before),
        "required_confirmation": required_confirmation,
    }
    if not args.apply:
        return plan

    if not args.operator or not args.reason:
        raise WriterWitnessError("--operator and --reason are mandatory with --apply")
    if not args.request_id:
        raise WriterWitnessError("--request-id is mandatory with --apply; reuse the dry-run value")
    if args.confirm != required_confirmation:
        raise WriterWitnessError(
            f"confirmation mismatch; use --confirm {required_confirmation!r} after dry-run review"
        )
    private_key_path = args.private_key_file or settings.writer_witness_private_key_file
    private_key = (
        _read_private_key(private_key_path)
        if args.action in {ACTION_ACQUIRE, ACTION_RENEW}
        else None
    )
    async with AsyncSessionLocal() as session:
        try:
            result = await transition_witness_state(
                session,
                action=args.action,
                requester_site=args.requester_site,
                expected_epoch=args.expected_epoch,
                expected_lease_id=expected_lease_id,
                request_id=args.request_id,
                operator=args.operator,
                reason=args.reason,
                private_key_base64=private_key,
                lease_duration_seconds=duration,
            )
            await session.commit()
        except WriterWitnessError as exc:
            rejection = await persist_witness_rejection(
                session,
                action=args.action,
                requester_site=args.requester_site,
                expected_epoch=args.expected_epoch,
                expected_lease_id=expected_lease_id,
                request_id=args.request_id,
                operator=args.operator,
                reason=args.reason,
                lease_duration_seconds=duration,
                error=exc,
            )
            await session.commit()
            raise rejection
        except Exception:
            await session.rollback()
            raise
    plan.update(
        status="applied",
        applied=True,
        replayed=result.replayed,
        after=_state_payload(result.state),
        witness_proof=result.proof,
    )
    return plan


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except (WriterWitnessError, WitnessProofError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
