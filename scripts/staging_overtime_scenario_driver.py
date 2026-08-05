#!/usr/bin/env python3
"""Mutating Stage 16 overtime scenario driver, executed inside a staging app container.

The acceptance runner invokes this over SSH with `docker compose exec`. It refuses
to run outside a staging environment, writes only rows carrying the caller's run
prefix, and can hard-delete everything it created.

Prints one JSON object on stdout so the caller can archive it as evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Executed as a file inside the container, where sys.path[0] is scripts/.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import delete, func, select

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import current_server
from models.change_log import ChangeLog
from models.user import User, UserRole


RUN_PREFIX_MARKER = "OTACC_"
SCENARIOS = ("OT-PREF-WEBAPP-SAVE",)


class DriverRefusal(RuntimeError):
    pass


def _guard_environment() -> None:
    environment = (getattr(settings, "environment", "") or "").strip().lower()
    if environment != "staging":
        raise DriverRefusal(f"driver refuses to run outside staging (environment={environment!r})")


def _guard_run_prefix(run_prefix: str) -> str:
    value = (run_prefix or "").strip()
    if not value.startswith(RUN_PREFIX_MARKER) or len(value) <= len(RUN_PREFIX_MARKER):
        raise DriverRefusal(f"run prefix must start with {RUN_PREFIX_MARKER}")
    if not value.replace("_", "").isalnum():
        raise DriverRefusal("run prefix must be alphanumeric with underscores")
    return value


def _account_name(run_prefix: str, suffix: str) -> str:
    return f"{run_prefix}_{suffix}"


def _synthetic_mobile(run_prefix: str, suffix: str) -> str:
    """Stable per-prefix number so two runs never collide on the unique index."""
    digest = hashlib.sha256(f"{run_prefix}:{suffix}".encode("utf-8")).hexdigest()
    return "09" + str(int(digest[:12], 16)).zfill(9)[-9:]


async def _seed_owner(session, run_prefix: str) -> User:
    """Create an isolated owner that is neither an accountant nor a tier-2 customer."""
    account_name = _account_name(run_prefix, "owner")
    owner = User(
        account_name=account_name,
        mobile_number=_synthetic_mobile(run_prefix, "owner"),
        full_name=account_name,
        address="",
        role=UserRole.STANDARD,
        has_bot_access=False,
        must_change_password=False,
        home_server=current_server(),
        offer_overtime_minutes=0,
    )
    session.add(owner)
    await session.flush()
    return owner


async def _cleanup(session, run_prefix: str) -> dict[str, int]:
    """Retire synthetic users through the product's own deletion flow.

    A raw or bulk delete on ``users`` is refused by the sync-outbox guard, and
    rightly so: the peer would never learn about it. ``delete_user_account`` is
    the authoritative path and already invalidates overtime state.
    """
    from core.services.user_deletion_service import delete_user_account

    owners = (
        await session.execute(
            select(User).where(
                User.account_name.like(f"{run_prefix}%"),
                User.is_deleted.is_(False),
            )
        )
    ).scalars().all()
    retired = 0
    for owner in owners:
        await delete_user_account(session, owner)
        retired += 1
    if retired:
        await session.commit()
    return {"users_retired": retired}


async def _scenario_pref_webapp_save(session, run_prefix: str, minutes: int) -> dict[str, object]:
    """Save the preference through the same authoritative path the WebApp uses."""
    from core.services.offer_overtime_preference_service import (
        evaluate_overtime_preference_eligibility,
        persist_overtime_preference,
    )

    if current_server() != "iran":
        raise DriverRefusal("the preference writer scenario only runs on the Iran peer")

    # Re-running a prefix must be safe; a half-finished attempt must not block it.
    await _cleanup(session, run_prefix)
    owner = await _seed_owner(session, run_prefix)
    await session.commit()
    # commit expires attributes; refresh before anything reads them again
    await session.refresh(owner)
    owner_id = int(owner.id)
    account_name = str(owner.account_name)

    eligibility = await evaluate_overtime_preference_eligibility(session, owner)
    invalid = await _rejects_out_of_range(session, owner)
    await session.refresh(owner)

    result = await persist_overtime_preference(session, owner, minutes)
    await session.commit()
    await session.refresh(owner)
    persisted = int(owner.offer_overtime_minutes)

    change_log_rows = (
        await session.execute(
            select(func.count())
            .select_from(ChangeLog)
            .where(ChangeLog.table_name == "users", ChangeLog.record_id == owner_id)
        )
    ).scalar_one()

    return {
        "owner_user_id": owner_id,
        "owner_account_name": account_name,
        "eligibility_allowed": bool(eligibility.allowed),
        "requested_minutes": minutes,
        "persisted_minutes": persisted,
        "save_detail": result.detail,
        "save_warning": result.warning,
        "user_change_log_rows": int(change_log_rows),
        "out_of_range_rejected": invalid,
        "passed": (
            bool(eligibility.allowed)
            and persisted == minutes
            and int(change_log_rows) > 0
            and invalid
        ),
    }


async def _rejects_out_of_range(session, owner: User) -> bool:
    """The 0..10 contract must hold on the authoritative writer, not just the UI."""
    from core.services.offer_overtime_preference_service import (
        OfferOvertimePreferenceError,
        persist_overtime_preference,
    )

    try:
        await persist_overtime_preference(session, owner, 11)
    except OfferOvertimePreferenceError:
        await session.rollback()
        return True
    await session.rollback()
    return False


async def main_async(args: argparse.Namespace) -> dict[str, object]:
    _guard_environment()
    run_prefix = _guard_run_prefix(args.run_prefix)
    started = datetime.now(timezone.utc).isoformat()

    # The API process registers these at startup. Without them the driver would
    # write user rows that never enter the sync stream, which would look like a
    # product defect rather than a missing import.
    from core.events import setup_event_listeners

    setup_event_listeners()

    async with AsyncSessionLocal() as session:
        if args.mode == "cleanup":
            removed = await _cleanup(session, run_prefix)
            return {
                "mode": "cleanup",
                "run_prefix": run_prefix,
                "server_mode": current_server(),
                "started_at": started,
                "removed": removed,
                "passed": True,
            }

        if args.scenario == "OT-PREF-WEBAPP-SAVE":
            outcome = await _scenario_pref_webapp_save(session, run_prefix, args.minutes)
        else:
            raise DriverRefusal(f"no driver implemented for {args.scenario}")

    return {
        "mode": "run",
        "scenario": args.scenario,
        "run_prefix": run_prefix,
        "server_mode": current_server(),
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        **outcome,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("run", "cleanup"), default="run")
    parser.add_argument("--scenario", choices=SCENARIOS, default=SCENARIOS[0])
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--minutes", type=int, default=4)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = asyncio.run(main_async(args))
    except DriverRefusal as exc:
        print(json.dumps({"passed": False, "refused": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 - evidence must record the failure
        print(json.dumps({"passed": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
