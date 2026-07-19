#!/usr/bin/env python3
"""Record short-lived multi-vantage connectivity evidence with the control role."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from core.db import DrControlSessionLocal, verify_three_site_database_role_bindings
from core.dark_standby import assert_not_dark_standby
from core.dr_durability_gate import build_connectivity_state_update
from core.dr_connectivity_classifier import load_connectivity_policy
from core.secure_file_io import read_secure_text
from models.dr_event import DrDurabilityState


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"duplicate connectivity evidence field: {key}")
        result[key] = value
    return result


def load_rounds(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(
            read_secure_text(path, label="connectivity evidence", max_size=512 * 1024),
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise RuntimeError("connectivity evidence is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "rounds"}:
        raise RuntimeError("connectivity evidence fields are invalid")
    if payload["schema"] != "three-site-connectivity-evidence-v1" or not isinstance(payload["rounds"], list):
        raise RuntimeError("connectivity evidence schema is invalid")
    return payload["rounds"]


async def update_state(*, rounds: list[dict[str, Any]], operator: str, ttl_seconds: int) -> dict[str, Any]:
    assert_not_dark_standby("connectivity_controller")
    update = build_connectivity_state_update(
        rounds,
        policy=load_connectivity_policy(),
        operator=operator,
        ttl_seconds=ttl_seconds,
    )
    await verify_three_site_database_role_bindings()
    async with DrControlSessionLocal() as session:
        state = await session.get(DrDurabilityState, 1, with_for_update=True)
        if state is None:
            raise RuntimeError("DR durability state is missing")
        state.connectivity_mode = update.classification.mode
        # This controller never claims same-region durability. During isolated
        # or ambiguous connectivity these false values deliberately freeze all
        # critical writes until a separately implemented/approved local journal
        # can provide verifiable health evidence.
        state.event_journal_healthy = False
        state.blob_journal_healthy = False
        state.evidence_hash = update.classification.evidence_hash
        state.evidence_expires_at = update.evidence_expires_at
        state.updated_by = update.updated_by
        await session.commit()
    return {
        "status": "recorded",
        "mode": update.classification.mode,
        "confidence": update.classification.confidence,
        "consecutive_rounds": update.classification.consecutive_rounds,
        "evidence_hash": update.classification.evidence_hash,
        "campaign_id": update.classification.campaign_id,
        "policy_hash": update.classification.policy_hash,
        "evidence_expires_at": update.evidence_expires_at.isoformat(),
        "critical_isolated_writes": "frozen",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--ttl-seconds", type=int, default=60)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            update_state(
                rounds=load_rounds(args.evidence),
                operator=args.operator,
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
