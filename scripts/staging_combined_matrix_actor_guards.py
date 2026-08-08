#!/usr/bin/env python3
"""Live actor/terminal guards for combined matrix: tier1 success + tier2/reject paths."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import current_server
from models.customer_relation import CustomerTier
from models.user import User, UserRole


class DriverRefusal(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _guard() -> None:
    environment = (getattr(settings, "environment", "") or "").strip().lower()
    if environment != "staging":
        raise DriverRefusal(f"refuses non-staging environment={environment!r}")


def _mobile(prefix: str, suffix: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{suffix}".encode()).hexdigest()
    return "09" + "".join(str(int(ch, 16) % 10) for ch in digest[:9])


async def _run_negative(case_id: str, prefix: str) -> dict[str, object]:
    from scripts.trading_core_probe_worker import run_negative_guard_case

    result = await run_negative_guard_case(
        prefix=f"{prefix}_{case_id[:12]}_",
        case_id=case_id,
        skip_initial_cleanup=False,
    )
    return {
        "case_id": case_id,
        "ok": str(result.get("status")) == "passed",
        "result_status": result.get("status"),
        "assertion_failures": result.get("assertion_failures") or [],
        "status_sequence": result.get("status_sequence") or [],
    }


async def _tier1_success(prefix: str) -> dict[str, object]:
    """Tier1 customer can successfully request via webapp under an active owner."""

    from scripts.trading_core_probe_worker import (
        create_active_customer_relation_for_negative_guard,
        create_offer_for_user,
        execute_webapp_trade_for_user,
        resolve_commodity,
    )

    async with AsyncSessionLocal() as session:
        owner = User(
            account_name=f"{prefix}_tier1_owner",
            mobile_number=_mobile(prefix, "tier1_owner"),
            full_name=f"{prefix}_tier1_owner",
            address="",
            role=UserRole.STANDARD,
            has_bot_access=True,
            must_change_password=False,
            home_server=current_server(),
            telegram_id=9_200_000_001,
        )
        tier1 = User(
            account_name=f"{prefix}_tier1_actor",
            mobile_number=_mobile(prefix, "tier1_actor"),
            full_name=f"{prefix}_tier1_actor",
            address="",
            role=UserRole.STANDARD,
            has_bot_access=True,
            must_change_password=False,
            home_server=current_server(),
            telegram_id=9_200_000_002,
        )
        session.add_all([owner, tier1])
        await session.commit()
        await session.refresh(owner)
        await session.refresh(tier1)

    await create_active_customer_relation_for_negative_guard(
        owner_user_id=int(owner.id),
        customer_user_id=int(tier1.id),
        tier=CustomerTier.TIER_1,
        prefix=prefix,
    )
    commodity_id, _ = await resolve_commodity()
    offer_id = await create_offer_for_user(
        user_id=int(owner.id),
        commodity_id=commodity_id,
        prefix=prefix,
        index=1,
        offer_type="sell",
        quantity=5,
        price=100000,
    )
    status = await execute_webapp_trade_for_user(
        user_id=int(tier1.id),
        offer_id=offer_id,
        quantity=5,
        idempotency_key=f"{prefix}:tier1-ok"[:64],
    )
    return {
        "case_id": "tier1_customer_success",
        "cell": "market:actor:tier1_customer",
        "ok": status == "success",
        "trade_status": status,
        "offer_id": offer_id,
    }


_NEGATIVE_CASES = (
    "tier2_offer_creation",
    "tier2_telegram_request",
    "invalid_request_amount",
    "own_offer_request",
)


async def _run(
    prefix: str,
    *,
    case_ids: tuple[str, ...] = _NEGATIVE_CASES,
    include_tier1: bool = True,
) -> dict[str, object]:
    _guard()
    if not prefix.startswith("CMB_"):
        raise DriverRefusal("run prefix must start with CMB_")
    started = time.perf_counter()
    cases: list[dict[str, object]] = []
    for case_id in case_ids:
        try:
            cases.append(await _run_negative(case_id, prefix))
        except Exception as exc:  # noqa: BLE001
            cases.append({"case_id": case_id, "ok": False, "error": str(exc)[:240]})
    if include_tier1:
        try:
            cases.append(await _tier1_success(prefix))
        except Exception as exc:  # noqa: BLE001
            cases.append(
                {
                    "case_id": "tier1_customer_success",
                    "cell": "market:actor:tier1_customer",
                    "ok": False,
                    "error": str(exc)[:240],
                }
            )
    ok = all(bool(item.get("ok")) for item in cases)
    return {
        "ok": ok,
        "at_utc": _utc(),
        "server_mode": getattr(settings, "server_mode", None),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cases": cases,
        "cells_covered": [
            "market:actor:tier1_customer",
            "market:actor:tier2_customer",
            "market:terminal:rejected",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--case-id", action="append", choices=_NEGATIVE_CASES)
    parser.add_argument("--skip-tier1", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(
            _run(
                args.run_prefix,
                case_ids=tuple(args.case_id or _NEGATIVE_CASES),
                include_tier1=not bool(args.skip_tier1),
            )
        )
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
