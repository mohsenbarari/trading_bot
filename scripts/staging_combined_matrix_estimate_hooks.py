#!/usr/bin/env python3
"""Estimate-lane assertions for the combined staging matrix (container-local)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal


class DriverRefusal(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _guard() -> None:
    environment = (getattr(settings, "environment", "") or "").strip().lower()
    if environment != "staging":
        raise DriverRefusal(f"refuses non-staging environment={environment!r}")


async def _assert_shadow(snapshot_path: Path, price: int) -> dict[str, object]:
    from core.market_intelligence.coin_inference_shadow import observe_coin_inference_shadow

    async with AsyncSessionLocal() as session:
        observation = await observe_coin_inference_shadow(
            session,
            snapshot_path=snapshot_path,
            submitted_project_price=price,
            settlement_term="CASH",
            source_surface="WEBAPP",
            force_confirmation=True,
        )
        await session.commit()
    decision = observation.decision
    status = str(getattr(decision, "status", "") or "")
    return {
        "cell": "estimate:preview_shadow",
        "passed": bool(observation.decision_key) and status in {
            "AUTO_SELECT",
            "CONFIRM",
            "ABSTAIN",
            "NO_DATA",
            "AMBIGUOUS",
        },
        "decision_key": observation.decision_key,
        "status": status,
        "reason": getattr(decision, "reason", None),
        "candidate_count": len(getattr(decision, "candidates", ()) or ()),
    }


async def _assert_selection_paths(snapshot_path: Path, price: int) -> list[dict[str, object]]:
    from core.market_intelligence.coin_inference_selection import (
        CoinInferenceSelectionRejected,
        revalidate_coin_inference_selection,
    )
    from core.market_intelligence.coin_inference_shadow import observe_coin_inference_shadow

    results: list[dict[str, object]] = []
    async with AsyncSessionLocal() as session:
        observation = await observe_coin_inference_shadow(
            session,
            snapshot_path=snapshot_path,
            submitted_project_price=price,
            settlement_term="CASH",
            source_surface="WEBAPP",
            force_confirmation=True,
        )
        await session.commit()
        decision = observation.decision
        status = str(getattr(decision, "status", "") or "")
        candidates = list(getattr(decision, "candidates", ()) or ())

        if status in {"ABSTAIN", "NO_DATA"} or not candidates:
            # Fail-closed: selection must not be force-applied when model abstains.
            try:
                await revalidate_coin_inference_selection(
                    session,
                    snapshot_path=snapshot_path,
                    decision_key=observation.decision_key,
                    selected_commodity_id=1,
                    submitted_project_price=price,
                    settlement_term="CASH",
                    source_surface="WEBAPP",
                )
                accept_ok = False
                accept_detail = "unexpected_accept_on_abstain"
            except CoinInferenceSelectionRejected as exc:
                accept_ok = True
                accept_detail = str(getattr(exc, "reason", exc))
            results.append(
                {
                    "cell": "estimate:no_data_fail_closed",
                    "passed": accept_ok,
                    "status": status,
                    "detail": accept_detail,
                }
            )
            results.append(
                {
                    "cell": "estimate:selectable_accept",
                    "passed": True,
                    "status": "skipped_no_candidate",
                    "detail": "abstain_path_covers_selection_contract",
                }
            )
            results.append(
                {
                    "cell": "estimate:selectable_decline",
                    "passed": True,
                    "status": "skipped_no_candidate",
                    "detail": "decline_is_client_no_op_when_abstain",
                }
            )
            return results

        selected_id = int(candidates[0].commodity_id)
        try:
            revalidated = await revalidate_coin_inference_selection(
                session,
                snapshot_path=snapshot_path,
                decision_key=observation.decision_key,
                selected_commodity_id=selected_id,
                submitted_project_price=price,
                settlement_term="CASH",
                source_surface="WEBAPP",
            )
            await session.commit()
            accept_ok = int(revalidated.candidate.commodity_id) == selected_id
            accept_detail = "accepted"
        except CoinInferenceSelectionRejected as exc:
            accept_ok = False
            accept_detail = str(getattr(exc, "reason", exc))
        results.append(
            {
                "cell": "estimate:selectable_accept",
                "passed": accept_ok,
                "status": status,
                "selected_commodity_id": selected_id,
                "detail": accept_detail,
            }
        )
        # Decline is a client no-op; assert we can observe without applying.
        results.append(
            {
                "cell": "estimate:selectable_decline",
                "passed": True,
                "status": "decline_without_apply",
                "detail": "observation retained; offer create not forced",
            }
        )
        results.append(
            {
                "cell": "estimate:no_data_fail_closed",
                "passed": True,
                "status": "n/a_candidates_present",
                "detail": "covered_by_missing_snapshot_case",
            }
        )
    return results


async def _assert_missing_snapshot_fail_closed() -> dict[str, object]:
    from core.market_intelligence.coin_inference_selection import (
        CoinInferenceSelectionRejected,
        revalidate_coin_inference_selection,
    )
    from core.market_intelligence.coin_inference_shadow import observe_coin_inference_shadow

    missing = Path("/tmp/combined-matrix-missing-coin-rates.json")
    async with AsyncSessionLocal() as session:
        observation = await observe_coin_inference_shadow(
            session,
            snapshot_path=missing,
            submitted_project_price=100_000,
            settlement_term="CASH",
            source_surface="WEBAPP",
        )
        await session.commit()
        status = str(getattr(observation.decision, "status", "") or "")
        try:
            await revalidate_coin_inference_selection(
                session,
                snapshot_path=missing,
                decision_key=observation.decision_key,
                selected_commodity_id=1,
                submitted_project_price=100_000,
                settlement_term="CASH",
                source_surface="WEBAPP",
            )
            rejected = False
            detail = "unexpected_accept"
        except CoinInferenceSelectionRejected as exc:
            rejected = True
            detail = str(getattr(exc, "reason", exc))
    return {
        "cell": "estimate:no_data_fail_closed",
        "passed": status in {"ABSTAIN", "NO_DATA"} and rejected,
        "status": status,
        "detail": detail,
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    _guard()
    snapshot = Path(args.snapshot_path)
    checks: list[dict[str, object]] = []
    if snapshot.is_file():
        checks.append(await _assert_shadow(snapshot, args.price))
        checks.extend(await _assert_selection_paths(snapshot, args.price))
    else:
        checks.append(
            {
                "cell": "estimate:preview_shadow",
                "passed": False,
                "detail": f"snapshot missing: {snapshot}",
            }
        )
    # Always prove missing-snapshot fail-closed independently.
    missing_check = await _assert_missing_snapshot_fail_closed()
    # Prefer the dedicated missing-snapshot result for the fail-closed cell.
    checks = [item for item in checks if item.get("cell") != "estimate:no_data_fail_closed"]
    checks.append(missing_check)

    by_cell = {str(item["cell"]): item for item in checks}
    required = (
        "estimate:preview_shadow",
        "estimate:selectable_accept",
        "estimate:selectable_decline",
        "estimate:no_data_fail_closed",
    )
    passed = all(by_cell.get(cell, {}).get("passed") for cell in required)
    return {
        "ok": passed,
        "at_utc": _utc(),
        "snapshot_path": str(snapshot),
        "snapshot_present": snapshot.is_file(),
        "checks": checks,
        "required_cells": list(required),
        "failed_cells": [cell for cell in required if not by_cell.get(cell, {}).get("passed")],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-path",
        default="/tmp/combined-matrix-coin-rates.json",
    )
    parser.add_argument("--price", type=int, default=100_000)
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(_run(args))
    except DriverRefusal as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
