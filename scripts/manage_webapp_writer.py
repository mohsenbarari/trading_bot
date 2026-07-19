#!/usr/bin/env python3
"""Dry-run-first local writer fencing and readiness approval tool."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import DrControlSessionLocal
from core.runtime_identity import resolve_runtime_identity
from core.webapp_writer_control import (
    ACTION_ACTIVATE,
    ACTION_APPROVE,
    ACTION_FENCE,
    ACTION_LEASE_REFRESH,
    WriterControlError,
    load_writer_snapshot,
    transition_writer_state,
    validate_readiness_evidence,
)
from core.writer_witness_contract import WitnessProofError, validate_witness_lease_proof


ACTIONS = ("status", ACTION_FENCE, ACTION_ACTIVATE, ACTION_APPROVE, ACTION_LEASE_REFRESH)


def _expected_site(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return None if normalized in {"none", "null", "-"} else normalized


def confirmation_phrase(
    *,
    action: str,
    physical_site: str,
    expected_epoch: int,
    target_epoch: int,
) -> str:
    return f"writer:{action}:{physical_site}:{expected_epoch}:{target_epoch}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect or transition local WebApp writer state.")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--expected-epoch", type=int)
    parser.add_argument("--expected-active-site", help="Use 'none' for a fenced state.")
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    parser.add_argument("--evidence-file")
    parser.add_argument("--witness-proof-file")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def _snapshot_payload(snapshot, physical_site: str) -> dict:
    return {
        "active_site": snapshot.active_site,
        "writer_epoch": snapshot.writer_epoch,
        "control_state": snapshot.control_state,
        "transition_id": snapshot.transition_id,
        "local_runtime_role": snapshot.local_runtime_role(physical_site),
        "readiness_evidence_id": snapshot.readiness_evidence_id,
        "readiness_approved_by": snapshot.readiness_approved_by,
        "readiness_approved_at": (
            snapshot.readiness_approved_at.isoformat()
            if snapshot.readiness_approved_at is not None
            else None
        ),
        "readiness_expires_at": (
            snapshot.readiness_expires_at.isoformat()
            if snapshot.readiness_expires_at is not None
            else None
        ),
        "witness_lease_id": snapshot.witness_lease_id,
        "witness_lease_expires_at": (
            snapshot.witness_lease_expires_at.isoformat()
            if snapshot.witness_lease_expires_at is not None
            else None
        ),
        "witness_transition_id": snapshot.witness_transition_id,
    }


async def run(args: argparse.Namespace) -> dict:
    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_site:
        raise WriterControlError("this command is valid only on webapp_fi or webapp_ir")
    async with DrControlSessionLocal() as session:
        before = await load_writer_snapshot(session)
    if args.action == "status":
        return {
            "status": "ok",
            "physical_site": identity.physical_site,
            "state": _snapshot_payload(before, identity.physical_site),
        }
    if args.expected_epoch is None or args.expected_active_site is None:
        raise WriterControlError(
            "--expected-epoch and --expected-active-site are mandatory for a transition"
        )
    expected_site = _expected_site(args.expected_active_site)
    if before.writer_epoch != args.expected_epoch or before.active_site != expected_site:
        raise WriterControlError(
            "dry-run expectation is stale: "
            f"current_epoch={before.writer_epoch} current_site={before.active_site!r}"
        )

    witness_proof = None
    if args.witness_proof_file:
        public_key = str(settings.writer_witness_public_key or "").strip()
        if not public_key:
            raise WriterControlError(
                "WRITER_WITNESS_PUBLIC_KEY is required to validate a witness proof"
            )
        witness_payload = json.loads(Path(args.witness_proof_file).read_text(encoding="utf-8"))
        witness_proof = validate_witness_lease_proof(
            witness_payload,
            public_key_base64=public_key,
            expected_site=identity.physical_site,
            expected_epoch=(before.writer_epoch if args.action == ACTION_LEASE_REFRESH else None),
            safety_margin_seconds=settings.writer_witness_safety_margin_seconds,
            max_clock_skew_seconds=settings.writer_witness_max_clock_skew_seconds,
            max_lifetime_seconds=settings.writer_witness_lease_duration_seconds,
        )
    if settings.writer_witness_required and args.action in {
        ACTION_ACTIVATE,
        ACTION_LEASE_REFRESH,
    } and witness_proof is None:
        raise WriterControlError(f"{args.action} requires --witness-proof-file")

    target_epoch = (
        witness_proof.writer_epoch
        if args.action == ACTION_ACTIVATE and witness_proof is not None
        else before.writer_epoch + 1
        if args.action == ACTION_ACTIVATE
        else before.writer_epoch
    )
    evidence = None
    if args.action in {ACTION_ACTIVATE, ACTION_APPROVE}:
        if not args.evidence_file:
            raise WriterControlError(f"{args.action} requires --evidence-file")
        evidence_payload = json.loads(Path(args.evidence_file).read_text(encoding="utf-8"))
        evidence = validate_readiness_evidence(
            evidence_payload,
            target_site=identity.physical_site,
            writer_epoch=target_epoch,
        )

    required_confirmation = confirmation_phrase(
        action=args.action,
        physical_site=identity.physical_site,
        expected_epoch=before.writer_epoch,
        target_epoch=target_epoch,
    )
    plan = {
        "status": "planned",
        "applied": False,
        "action": args.action,
        "physical_site": identity.physical_site,
        "before": _snapshot_payload(before, identity.physical_site),
        "target_epoch": target_epoch,
        "required_confirmation": required_confirmation,
        "evidence_hash": evidence.content_hash if evidence is not None else None,
        "witness_proof_hash": witness_proof.proof_hash if witness_proof is not None else None,
    }
    if not args.apply:
        return plan
    if not str(getattr(settings, "physical_site", None) or "").strip():
        raise WriterControlError(
            "PHYSICAL_SITE must be explicitly configured before applying a writer transition"
        )
    if not args.operator or not args.reason:
        raise WriterControlError("--operator and --reason are mandatory with --apply")
    if args.confirm != required_confirmation:
        raise WriterControlError(
            f"confirmation mismatch; use --confirm {required_confirmation!r} after dry-run review"
        )

    async with DrControlSessionLocal() as session:
        try:
            after = await transition_writer_state(
                session,
                action=args.action,
                identity=identity,
                expected_epoch=args.expected_epoch,
                expected_active_site=expected_site,
                operator=args.operator,
                reason=args.reason,
                evidence=evidence,
                witness_proof=witness_proof,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    async with DrControlSessionLocal() as session:
        verified = await load_writer_snapshot(session)
    if args.action == ACTION_LEASE_REFRESH:
        if (
            witness_proof is None
            or verified.witness_proof_hash != witness_proof.proof_hash
            or verified.transition_id != before.transition_id
        ):
            raise WriterControlError("witness lease refresh commit could not be verified")
    elif verified.transition_id != after.transition_id:
        raise WriterControlError("writer transition commit could not be verified")
    plan.update(
        status="applied",
        applied=True,
        after=_snapshot_payload(verified, identity.physical_site),
    )
    return plan


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except (
        WriterControlError,
        WitnessProofError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
