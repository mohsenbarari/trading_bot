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
import time
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
    "OT-OFFER-BOT-ORIGIN",
    "OT-REQ-IRAN-TO-IRAN",
)
# Staging Iran currently carries a single commodity; keep the driver pinned to it.
DEFAULT_COMMODITY_ID = 1
DEFAULT_OFFER_QUANTITY = 5
DEFAULT_OFFER_PRICE = 100_000
PREFERENCE_MIRROR_TIMEOUT_SECONDS = 60.0
PREFERENCE_MIRROR_POLL_SECONDS = 2.0
BOT_FORWARD_RETRY_TIMEOUT_SECONDS = 60.0
BOT_FORWARD_RETRY_POLL_SECONDS = 3.0


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


async def _seed_user(
    session,
    run_prefix: str,
    suffix: str,
    *,
    home_server: str | None = None,
    has_bot_access: bool = False,
) -> User:
    """Create an isolated standard user that is neither an accountant nor tier-2.

    With registration sync v2, user INSERT outbox rows are emitted only on Iran.
    Foreign-home bot owners must therefore be seeded on Iran with
    ``home_server='foreign'`` and mirrored before the foreign peer mutates them.
    """
    account_name = _account_name(run_prefix, suffix)
    user = User(
        account_name=account_name,
        mobile_number=_synthetic_mobile(run_prefix, suffix),
        full_name=account_name,
        address="",
        role=UserRole.STANDARD,
        has_bot_access=has_bot_access,
        must_change_password=False,
        home_server=home_server or current_server(),
        offer_overtime_minutes=0,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_owner(
    session,
    run_prefix: str,
    *,
    home_server: str | None = None,
    has_bot_access: bool = False,
) -> User:
    return await _seed_user(
        session,
        run_prefix,
        "owner",
        home_server=home_server,
        has_bot_access=has_bot_access,
    )


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


async def _create_offer(
    session,
    owner: User,
    *,
    notes: str,
    source_surface,
):
    """Create an offer through the same quota path production surfaces use.

    ``quota_policy`` is required: without it the creation helper never freezes
    ``overtime_minutes_snapshot`` from the locked owner row. Market validation is
    skipped so staging competitive-price state cannot flake the overtime contract.
    """
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
            source_surface=source_surface,
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


async def _create_webapp_offer(session, owner: User, *, notes: str):
    from core.offer_source import OfferSourceSurface

    return await _create_offer(
        session,
        owner,
        notes=notes,
        source_surface=OfferSourceSurface.WEBAPP,
    )


async def _create_bot_offer(session, owner: User, *, notes: str):
    from core.offer_source import OfferSourceSurface

    return await _create_offer(
        session,
        owner,
        notes=notes,
        source_surface=OfferSourceSurface.TELEGRAM_BOT,
    )


async def _await_preference_minutes(
    session,
    owner_id: int,
    minutes: int,
    *,
    timeout_seconds: float = PREFERENCE_MIRROR_TIMEOUT_SECONDS,
) -> tuple[bool, float]:
    """Wait until the local user row shows the Iran-authoritative preference."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        owner = (
            await session.execute(select(User).where(User.id == owner_id))
        ).scalar_one()
        if int(owner.offer_overtime_minutes) == minutes:
            return True, round(timeout_seconds - (deadline - time.monotonic()), 3)
        await asyncio.sleep(PREFERENCE_MIRROR_POLL_SECONDS)
        session.expire_all()
    return False, timeout_seconds


async def _save_bot_preference_with_user_sync_retry(
    session,
    owner: User,
    minutes: int,
):
    """Forward the bot preference until the peer has the user, then succeed.

    A freshly created foreign-home user is not yet on Iran. The signed internal
    command must wait for that sync before Iran can persist the field.
    """
    from core.services.offer_overtime_preference_service import (
        OfferOvertimePreferenceTransportError,
        save_overtime_preference_from_bot,
    )

    deadline = time.monotonic() + BOT_FORWARD_RETRY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        await session.refresh(owner)
        try:
            return await save_overtime_preference_from_bot(session, owner, minutes)
        except OfferOvertimePreferenceTransportError as exc:
            last_error = exc
            await asyncio.sleep(BOT_FORWARD_RETRY_POLL_SECONDS)
            session.expire_all()
    if last_error is not None:
        raise last_error
    raise OfferOvertimePreferenceTransportError()


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


async def _scenario_pref_bot_save(
    session,
    run_prefix: str,
    minutes: int,
    *,
    owner_user_id: int | None = None,
) -> dict[str, object]:
    """Persist through the bot helper.

    On Iran the helper writes locally after seeding. On foreign, registration sync
    v2 forbids local user INSERT, so the owner must already exist (seeded on Iran
    with ``home_server=foreign``) and is selected via ``--owner-user-id``.
    """
    from core.services.offer_overtime_preference_service import (
        evaluate_overtime_preference_eligibility,
        save_overtime_preference_from_bot,
    )

    server = current_server()
    if server not in {"iran", "foreign"}:
        raise DriverRefusal(f"unexpected server_mode={server!r}")

    if server == "foreign":
        if owner_user_id is None:
            raise DriverRefusal(
                "OT-PREF-BOT-SAVE on foreign requires --owner-user-id from an "
                "Iran-seeded foreign-home owner (registration sync v2)"
            )
        owner = (
            await session.execute(select(User).where(User.id == int(owner_user_id)))
        ).scalar_one_or_none()
        if owner is None or bool(owner.is_deleted):
            raise DriverRefusal(f"owner_user_id={owner_user_id} is not present on foreign")
        if str(owner.home_server) != "foreign":
            raise DriverRefusal(
                f"owner_user_id={owner_user_id} home_server={owner.home_server!r}, expected foreign"
            )
    else:
        await _cleanup(session, run_prefix)
        owner = await _seed_owner(session, run_prefix)
        await session.commit()
        await session.refresh(owner)

    owner_id = int(owner.id)
    account_name = str(owner.account_name)
    home_server = str(owner.home_server)

    eligibility = await evaluate_overtime_preference_eligibility(session, owner)
    if server == "foreign":
        result = await _save_bot_preference_with_user_sync_retry(session, owner, minutes)
        mirrored, mirror_seconds = await _await_preference_minutes(
            session, owner_id, minutes
        )
        await session.refresh(owner)
        persisted = int(owner.offer_overtime_minutes)
        path = "save_overtime_preference_from_bot+foreign_forward"
    else:
        result = await save_overtime_preference_from_bot(session, owner, minutes)
        await session.refresh(owner)
        persisted = int(owner.offer_overtime_minutes)
        mirrored, mirror_seconds = True, 0.0
        path = "save_overtime_preference_from_bot+iran_local"

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
        "owner_home_server": home_server,
        "path": path,
        "eligibility_allowed": bool(eligibility.allowed),
        "requested_minutes": minutes,
        "persisted_minutes": persisted,
        "save_detail": result.detail,
        "save_warning": result.warning,
        "local_mirror_observed": mirrored,
        "local_mirror_seconds": mirror_seconds,
        "user_change_log_rows": int(change_log_rows),
        "passed": (
            bool(eligibility.allowed)
            and persisted == minutes
            and mirrored
            and (server == "foreign" or int(change_log_rows) > 0)
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


async def _scenario_offer_bot_origin_seed(session, run_prefix: str) -> dict[str, object]:
    """Seed a foreign-home bot owner on Iran so the foreign peer can create offers."""
    if current_server() != "iran":
        raise DriverRefusal("OT-OFFER-BOT-ORIGIN seed phase only runs on the Iran peer")

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(
        session,
        run_prefix,
        home_server="foreign",
        has_bot_access=True,
    )
    await session.commit()
    await session.refresh(owner)
    owner_id = int(owner.id)
    change_log_rows = (
        await session.execute(
            select(func.count())
            .select_from(ChangeLog)
            .where(ChangeLog.table_name == "users", ChangeLog.record_id == owner_id)
        )
    ).scalar_one()
    return {
        "phase": "seed",
        "owner_user_id": owner_id,
        "owner_account_name": str(owner.account_name),
        "owner_home_server": str(owner.home_server),
        "user_change_log_rows": int(change_log_rows),
        "passed": str(owner.home_server) == "foreign" and int(change_log_rows) > 0,
    }


async def _scenario_offer_bot_origin_run(
    session,
    run_prefix: str,
    minutes: int,
    *,
    owner_user_id: int,
) -> dict[str, object]:
    """Freeze bot-creator preference onto a foreign-home offer; public fields stay safe."""
    from api.routers import offers as offers_router
    from core.offer_lifecycle import OfferLifecyclePhase, project_offer_lifecycle
    from core.trading_settings import get_trading_settings

    if current_server() != "foreign":
        raise DriverRefusal("OT-OFFER-BOT-ORIGIN run phase only runs on the foreign peer")
    if minutes <= 0:
        raise DriverRefusal("OT-OFFER-BOT-ORIGIN requires a positive overtime preference")

    # Wait briefly for the Iran-seeded user to mirror before refusing.
    deadline = time.monotonic() + PREFERENCE_MIRROR_TIMEOUT_SECONDS
    owner = None
    while time.monotonic() < deadline:
        owner = (
            await session.execute(select(User).where(User.id == int(owner_user_id)))
        ).scalar_one_or_none()
        if owner is not None and not bool(owner.is_deleted):
            break
        await asyncio.sleep(PREFERENCE_MIRROR_POLL_SECONDS)
        session.expire_all()
        owner = None
    if owner is None:
        raise DriverRefusal(
            f"Iran-seeded owner_user_id={owner_user_id} did not mirror to foreign"
        )
    if str(owner.home_server) != "foreign":
        raise DriverRefusal(
            f"owner_user_id={owner_user_id} home_server={owner.home_server!r}, expected foreign"
        )

    owner_id = int(owner.id)
    home_server = str(owner.home_server)

    await _save_bot_preference_with_user_sync_retry(session, owner, minutes)
    mirrored, mirror_seconds = await _await_preference_minutes(session, owner_id, minutes)
    if not mirrored:
        raise DriverRefusal(
            f"foreign preference mirror for user {owner_id} did not converge to {minutes}"
        )
    await session.refresh(owner)
    preference_before_create = int(owner.offer_overtime_minutes)

    offer, normal_minutes = await _create_bot_offer(
        session,
        owner,
        notes=f"{run_prefix} bot origin",
    )
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
    snapshot = int(offer.overtime_minutes_snapshot)
    created_at = offer.created_at
    offer_home_server = str(offer.home_server)

    # Later preference edits must not rewrite an already-frozen offer.
    await _save_bot_preference_with_user_sync_retry(session, owner, 0)
    cleared_mirror, cleared_seconds = await _await_preference_minutes(session, owner_id, 0)
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
        home_server == "foreign"
        and offer_home_server == "foreign"
        and preference_before_create == minutes
        and snapshot == minutes
        and snapshot_after_pref_clear == minutes
        and preference_after_clear == 0
        and cleared_mirror
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
        "phase": "run",
        "owner_user_id": owner_id,
        "owner_home_server": home_server,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "offer_home_server": offer_home_server,
        "preference_at_create": preference_before_create,
        "preference_mirror_seconds": mirror_seconds,
        "overtime_minutes_snapshot": snapshot,
        "snapshot_after_preference_cleared": snapshot_after_pref_clear,
        "preference_after_clear": preference_after_clear,
        "preference_clear_mirror_seconds": cleared_seconds,
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


async def _backdate_offer_into_overtime(session, offer, *, normal_minutes: int) -> datetime:
    """Move ``created_at`` so wall-clock ``now`` falls inside the overtime window."""
    # 30s past the normal deadline keeps intake in APPROVAL without waiting 2 minutes.
    stamped = datetime.utcnow() - timedelta(minutes=normal_minutes, seconds=30)
    offer.created_at = stamped
    await session.commit()
    await session.refresh(offer)
    return stamped


async def _scenario_req_iran_to_iran(
    session,
    run_prefix: str,
    minutes: int,
) -> dict[str, object]:
    """Create, present, and decide an overtime request on an Iran-home offer."""
    from core.services.offer_overtime_preference_service import persist_overtime_preference
    from core.services.offer_overtime_request_service import (
        OvertimeRequestCreateCommand,
        create_overtime_request,
        reject_by_owner,
    )
    from core.trading_settings import get_trading_settings
    from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus

    if current_server() != "iran":
        raise DriverRefusal("OT-REQ-IRAN-TO-IRAN only runs on the Iran peer")
    if minutes <= 0:
        raise DriverRefusal("OT-REQ-IRAN-TO-IRAN requires a positive overtime preference")

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(session, run_prefix)
    requester = await _seed_user(session, run_prefix, "requester")
    await session.commit()
    await session.refresh(owner)
    await session.refresh(requester)
    owner_id = int(owner.id)
    requester_id = int(requester.id)

    await persist_overtime_preference(session, owner, minutes)
    await session.commit()
    await session.refresh(owner)

    offer, normal_minutes = await _create_webapp_offer(
        session,
        owner,
        notes=f"{run_prefix} iran overtime request",
    )
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
    snapshot = int(offer.overtime_minutes_snapshot)
    if snapshot != minutes:
        raise DriverRefusal(
            f"offer snapshot {snapshot} did not match preference {minutes}"
        )

    await _backdate_offer_into_overtime(session, offer, normal_minutes=normal_minutes)
    offer = await _reload_offer(session, offer_id)
    receipt_at = datetime.utcnow()

    ts = get_trading_settings()
    create_result = await create_overtime_request(
        session,
        OvertimeRequestCreateCommand(
            offer=offer,
            requester_user_id=requester_id,
            actor_user_id=requester_id,
            requested_quantity=min(2, DEFAULT_OFFER_QUANTITY),
            idempotency_key=f"{run_prefix}:ot-req-iran",
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server="iran",
            receipt_at=receipt_at,
            normal_lifetime_minutes=normal_minutes,
            request_home_server="iran",
        ),
        now=receipt_at,
    )
    await session.commit()
    ledger = create_result.ledger
    await session.refresh(ledger)
    presented_status = str(
        getattr(ledger.result_status, "value", ledger.result_status)
    )
    request_public_id = str(ledger.request_public_id)
    request_home = str(ledger.request_home_server)
    request_id = int(ledger.id)

    await reject_by_owner(
        session,
        ledger,
        decided_by_user_id=owner_id,
        now=datetime.utcnow(),
        normal_lifetime_minutes=int(getattr(ts, "offer_expiry_minutes", 0) or 0),
    )
    await session.commit()
    await session.refresh(ledger)
    decided_status = str(
        getattr(ledger.result_status, "value", ledger.result_status)
    )

    request_change_log_rows = (
        await session.execute(
            select(func.count())
            .select_from(ChangeLog)
            .where(
                ChangeLog.table_name == "offer_requests",
                ChangeLog.record_id == request_id,
            )
        )
    ).scalar_one()

    passed = (
        create_result.promoted is True
        and presented_status == OfferRequestStatus.OVERTIME_PRESENTED.value
        and request_home == "iran"
        and decided_status == OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER.value
        and int(request_change_log_rows) > 0
        and snapshot == minutes
    )

    return {
        "owner_user_id": owner_id,
        "requester_user_id": requester_id,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "overtime_minutes_snapshot": snapshot,
        "normal_lifetime_minutes": normal_minutes,
        "request_id": request_id,
        "request_public_id": request_public_id,
        "request_home_server": request_home,
        "presented_status": presented_status,
        "promoted_on_create": bool(create_result.promoted),
        "decided_status": decided_status,
        "decision": "owner_rejected",
        "request_change_log_rows": int(request_change_log_rows),
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
            outcome = await _scenario_pref_bot_save(
                session,
                run_prefix,
                args.minutes,
                owner_user_id=args.owner_user_id,
            )
        elif args.scenario == "OT-PREF-DISABLED-REGRESSION":
            outcome = await _scenario_pref_disabled_regression(session, run_prefix)
        elif args.scenario == "OT-OFFER-WEBAPP-ORIGIN":
            outcome = await _scenario_offer_webapp_origin(session, run_prefix, args.minutes)
        elif args.scenario == "OT-OFFER-BOT-ORIGIN":
            phase = (args.phase or "run").strip().lower()
            if phase == "seed":
                outcome = await _scenario_offer_bot_origin_seed(session, run_prefix)
            elif phase == "run":
                if args.owner_user_id is None:
                    raise DriverRefusal(
                        "OT-OFFER-BOT-ORIGIN run phase requires --owner-user-id "
                        "from the Iran seed phase"
                    )
                outcome = await _scenario_offer_bot_origin_run(
                    session,
                    run_prefix,
                    args.minutes,
                    owner_user_id=int(args.owner_user_id),
                )
            else:
                raise DriverRefusal(f"unsupported OT-OFFER-BOT-ORIGIN phase={phase!r}")
        elif args.scenario == "OT-REQ-IRAN-TO-IRAN":
            outcome = await _scenario_req_iran_to_iran(session, run_prefix, args.minutes)
        else:
            raise DriverRefusal(f"no driver implemented for {args.scenario}")

        cleanup = {"users_retired": 0}
        # Foreign-home owners are Iran-authoritative under registration sync v2;
        # never auto-retire them from the foreign peer after a bot-origin run.
        allow_cleanup = args.cleanup_after and outcome.get("passed")
        if args.scenario == "OT-OFFER-BOT-ORIGIN":
            allow_cleanup = False
        if allow_cleanup:
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
        "--phase",
        choices=("seed", "run"),
        default="run",
        help="for OT-OFFER-BOT-ORIGIN: seed on Iran, then run on foreign",
    )
    parser.add_argument(
        "--owner-user-id",
        type=int,
        default=None,
        help="existing user id for foreign-peer scenarios that cannot INSERT users",
    )
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
