#!/usr/bin/env python3
"""Mutating Stage 16 overtime scenario driver, executed inside a staging app container.

The acceptance runner invokes this over SSH with `docker compose exec`. It refuses
to run outside a staging environment, writes only rows carrying the caller's run
prefix, and retires them through ``delete_user_account`` so the peer learns too.

Prints one JSON object on stdout so the caller can archive it as evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Executed as a file inside the container, where sys.path[0] is scripts/.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func, select

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import current_server
from models.change_log import ChangeLog
from models.user import User, UserRole


RUN_PREFIX_MARKER = "OTACC_"
SCENARIOS = (
    "OT-PREF-WEBAPP-SAVE",
    "OT-PREF-BOT-SAVE",
    "OT-PREF-DISABLED-REGRESSION",
    "OT-OFFER-WEBAPP-ORIGIN",
)
# Staging Iran currently carries a single commodity; keep the driver pinned to it.
DEFAULT_COMMODITY_ID = 1
DEFAULT_OFFER_QUANTITY = 5
DEFAULT_OFFER_PRICE = 100_000


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
    the authoritative path and already invalidates overtime state / active offers.
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


async def _create_webapp_offer(session, owner: User, *, notes: str):
    """Create an Iran-home offer through the same quota path the WebApp uses.

    ``quota_policy`` is required: without it the creation helper never freezes
    ``overtime_minutes_snapshot`` from the locked owner row. Market validation is
    skipped so staging competitive-price state cannot flake the overtime contract.
    """
    from core.offer_source import OfferSourceSurface
    from core.services.offer_creation_service import (
        OfferCreationCommand,
        OfferCreationQuotaPolicy,
        create_authoritative_offer_with_outcome,
    )
    from core.trading_settings import get_trading_settings
    from models.offer import OfferType

    ts = get_trading_settings()
    outcome = await create_authoritative_offer_with_outcome(
        session,
        OfferCreationCommand(
            source_surface=OfferSourceSurface.WEBAPP,
            owner_user_id=int(owner.id),
            actor_user_id=int(owner.id),
            offer_type=OfferType.SELL,
            commodity_id=DEFAULT_COMMODITY_ID,
            quantity=DEFAULT_OFFER_QUANTITY,
            price=DEFAULT_OFFER_PRICE,
            notes=notes,
        ),
        validate_market=False,
        enforce_market_admission=False,
        quota_policy=OfferCreationQuotaPolicy(
            max_active_offers=int(getattr(ts, "max_active_offers", 4) or 4),
        ),
        commit=True,
        refresh=True,
    )
    return outcome.offer, int(getattr(ts, "offer_expiry_minutes", 2) or 2)


async def _scenario_pref_webapp_save(session, run_prefix: str, minutes: int) -> dict[str, object]:
    """Save the preference through the same authoritative path the WebApp uses."""
    from core.services.offer_overtime_preference_service import (
        evaluate_overtime_preference_eligibility,
        persist_overtime_preference,
    )

    if current_server() != "iran":
        raise DriverRefusal("the preference writer scenario only runs on the Iran peer")

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(session, run_prefix)
    await session.commit()
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


async def _scenario_pref_bot_save(session, run_prefix: str, minutes: int) -> dict[str, object]:
    """Persist through the bot helper; on Iran that still writes Iran-authoritatively."""
    from core.services.offer_overtime_preference_service import (
        evaluate_overtime_preference_eligibility,
        save_overtime_preference_from_bot,
    )

    if current_server() != "iran":
        raise DriverRefusal(
            "OT-PREF-BOT-SAVE Iran-path driver only runs on the Iran peer; "
            "foreign-forward coverage is a separate follow-up"
        )

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(session, run_prefix)
    await session.commit()
    await session.refresh(owner)
    owner_id = int(owner.id)
    account_name = str(owner.account_name)

    eligibility = await evaluate_overtime_preference_eligibility(session, owner)
    # save_overtime_preference_from_bot commits on Iran itself
    result = await save_overtime_preference_from_bot(session, owner, minutes)
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
        "path": "save_overtime_preference_from_bot",
        "eligibility_allowed": bool(eligibility.allowed),
        "requested_minutes": minutes,
        "persisted_minutes": persisted,
        "save_detail": result.detail,
        "save_warning": result.warning,
        "user_change_log_rows": int(change_log_rows),
        "passed": (
            bool(eligibility.allowed)
            and persisted == minutes
            and int(change_log_rows) > 0
        ),
    }


async def _scenario_pref_disabled_regression(session, run_prefix: str) -> dict[str, object]:
    """Users at preference 0 keep automatic-trade-only lifetime behavior."""
    from core.offer_lifecycle import (
        OfferLifecyclePhase,
        OfferRequestIntakePhase,
        classify_request_intake_phase,
        project_offer_lifecycle,
    )

    if current_server() != "iran":
        raise DriverRefusal("the disabled-regression scenario only runs on the Iran peer")

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(session, run_prefix)
    await session.commit()
    await session.refresh(owner)
    owner_id = int(owner.id)
    preference = int(owner.offer_overtime_minutes)

    offer, normal_minutes = await _create_webapp_offer(
        session,
        owner,
        notes=f"{run_prefix} disabled regression",
    )
    await session.refresh(offer)
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
    snapshot = int(offer.overtime_minutes_snapshot)

    normal_projection = project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
    )
    after_normal = (offer.created_at or datetime.utcnow()) + timedelta(
        minutes=normal_minutes,
        seconds=1,
    )
    expired_projection = project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
        as_of=after_normal,
    )
    intake_after_normal = classify_request_intake_phase(
        receipt_at=after_normal,
        normal_deadline_at=expired_projection.normal_deadline_at,
        final_deadline_at=expired_projection.final_deadline_at,
        overtime_minutes_snapshot=snapshot,
    )

    offer_change_log_rows = (
        await session.execute(
            select(func.count())
            .select_from(ChangeLog)
            .where(ChangeLog.table_name == "offers", ChangeLog.record_id == offer_id)
        )
    ).scalar_one()

    passed = (
        preference == 0
        and snapshot == 0
        and normal_projection.phase == OfferLifecyclePhase.NORMAL
        and normal_projection.accepts_automatic_trade is True
        and normal_projection.accepts_overtime_request is False
        and expired_projection.phase == OfferLifecyclePhase.EXPIRED
        and expired_projection.accepts_overtime_request is False
        and intake_after_normal == OfferRequestIntakePhase.REJECTED
        and int(offer_change_log_rows) > 0
    )

    return {
        "owner_user_id": owner_id,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "preference_minutes": preference,
        "overtime_minutes_snapshot": snapshot,
        "normal_lifetime_minutes": normal_minutes,
        "normal_phase": normal_projection.phase.value,
        "accepts_automatic_trade_in_normal": normal_projection.accepts_automatic_trade,
        "accepts_overtime_request_in_normal": normal_projection.accepts_overtime_request,
        "phase_after_normal_deadline": expired_projection.phase.value,
        "accepts_overtime_after_normal_deadline": expired_projection.accepts_overtime_request,
        "intake_after_normal_deadline": intake_after_normal.value,
        "offer_change_log_rows": int(offer_change_log_rows),
        "passed": passed,
    }


async def _reload_offer(session, offer_id: int):
    """Re-load an offer with commodity so public serializers avoid sync lazy IO."""
    from sqlalchemy.orm import selectinload

    from models.offer import Offer

    offer = (
        await session.execute(
            select(Offer)
            .where(Offer.id == offer_id)
            .options(selectinload(Offer.commodity))
        )
    ).scalar_one()
    return offer


async def _scenario_offer_webapp_origin(
    session,
    run_prefix: str,
    minutes: int,
) -> dict[str, object]:
    """Freeze the WebApp creator preference onto the offer; public fields stay safe."""
    from api.routers import offers as offers_router
    from core.offer_lifecycle import OfferLifecyclePhase, project_offer_lifecycle
    from core.services.offer_overtime_preference_service import persist_overtime_preference
    from core.trading_settings import get_trading_settings

    if current_server() != "iran":
        raise DriverRefusal("the webapp-origin offer scenario only runs on the Iran peer")
    if minutes <= 0:
        raise DriverRefusal("OT-OFFER-WEBAPP-ORIGIN requires a positive overtime preference")

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(session, run_prefix)
    await session.commit()
    await session.refresh(owner)
    owner_id = int(owner.id)

    await persist_overtime_preference(session, owner, minutes)
    await session.commit()
    await session.refresh(owner)
    preference_before_create = int(owner.offer_overtime_minutes)

    offer, normal_minutes = await _create_webapp_offer(
        session,
        owner,
        notes=f"{run_prefix} webapp origin",
    )
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
    snapshot = int(offer.overtime_minutes_snapshot)
    created_at = offer.created_at

    # Later preference edits must not rewrite an already-frozen offer.
    await session.refresh(owner)
    await persist_overtime_preference(session, owner, 0)
    await session.commit()
    await session.refresh(owner)
    preference_after_clear = int(owner.offer_overtime_minutes)

    offer = await _reload_offer(session, offer_id)
    snapshot_after_pref_clear = int(offer.overtime_minutes_snapshot)

    ts = get_trading_settings()
    lifecycle_fields = offers_router._offer_lifecycle_response_fields(
        offer,
        start_settings=ts,
    )
    public = offers_router._build_public_offer_response(offer, start_settings=ts)
    public_payload = public.model_dump() if hasattr(public, "model_dump") else public.dict()

    overtime_as_of = (created_at or datetime.utcnow()) + timedelta(
        minutes=normal_minutes,
        seconds=1,
    )
    overtime_projection = project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
        as_of=overtime_as_of,
    )

    # PublicOfferResponse must not expose private owner identity fields.
    private_identity_leaked = any(
        key in public_payload for key in ("user_id", "user_account_name", "is_own_offer")
    )

    offer_change_log_rows = (
        await session.execute(
            select(func.count())
            .select_from(ChangeLog)
            .where(ChangeLog.table_name == "offers", ChangeLog.record_id == offer_id)
        )
    ).scalar_one()

    passed = (
        preference_before_create == minutes
        and snapshot == minutes
        and snapshot_after_pref_clear == minutes
        and preference_after_clear == 0
        and lifecycle_fields.get("overtime_minutes_snapshot") == minutes
        and lifecycle_fields.get("lifecycle_phase") == OfferLifecyclePhase.NORMAL.value
        and lifecycle_fields.get("accepts_automatic_trade") is True
        and lifecycle_fields.get("accepts_overtime_request") is False
        and overtime_projection.phase == OfferLifecyclePhase.OVERTIME
        and overtime_projection.accepts_automatic_trade is False
        and overtime_projection.accepts_overtime_request is True
        and public_payload.get("overtime_minutes_snapshot") == minutes
        and public_payload.get("safe_public_state_label") == "active"
        and not private_identity_leaked
        and int(offer_change_log_rows) > 0
    )

    return {
        "owner_user_id": owner_id,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "preference_at_create": preference_before_create,
        "overtime_minutes_snapshot": snapshot,
        "snapshot_after_preference_cleared": snapshot_after_pref_clear,
        "preference_after_clear": preference_after_clear,
        "normal_lifetime_minutes": normal_minutes,
        "lifecycle_fields": {
            "lifecycle_phase": lifecycle_fields.get("lifecycle_phase"),
            "overtime_minutes_snapshot": lifecycle_fields.get("overtime_minutes_snapshot"),
            "accepts_automatic_trade": lifecycle_fields.get("accepts_automatic_trade"),
            "accepts_overtime_request": lifecycle_fields.get("accepts_overtime_request"),
            "normal_deadline_ts": lifecycle_fields.get("normal_deadline_ts"),
            "final_deadline_ts": lifecycle_fields.get("final_deadline_ts"),
        },
        "overtime_phase": overtime_projection.phase.value,
        "accepts_automatic_in_overtime": overtime_projection.accepts_automatic_trade,
        "accepts_overtime_in_overtime": overtime_projection.accepts_overtime_request,
        "public_safe": {
            "safe_public_state_label": public_payload.get("safe_public_state_label"),
            "overtime_minutes_snapshot": public_payload.get("overtime_minutes_snapshot"),
            "private_identity_leaked": private_identity_leaked,
        },
        "offer_change_log_rows": int(offer_change_log_rows),
        "passed": passed,
    }


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
        elif args.scenario == "OT-PREF-BOT-SAVE":
            outcome = await _scenario_pref_bot_save(session, run_prefix, args.minutes)
        elif args.scenario == "OT-PREF-DISABLED-REGRESSION":
            outcome = await _scenario_pref_disabled_regression(session, run_prefix)
        elif args.scenario == "OT-OFFER-WEBAPP-ORIGIN":
            outcome = await _scenario_offer_webapp_origin(session, run_prefix, args.minutes)
        else:
            raise DriverRefusal(f"no driver implemented for {args.scenario}")

        cleanup = {"users_retired": 0}
        if args.cleanup_after and outcome.get("passed"):
            cleanup = await _cleanup(session, run_prefix)

    return {
        "mode": "run",
        "scenario": args.scenario,
        "run_prefix": run_prefix,
        "server_mode": current_server(),
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "cleanup": cleanup,
        **outcome,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("run", "cleanup"), default="run")
    parser.add_argument("--scenario", choices=SCENARIOS, default=SCENARIOS[0])
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--minutes", type=int, default=4)
    parser.add_argument(
        "--cleanup-after",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="retire synthetic users after a passing run (default: true)",
    )
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
