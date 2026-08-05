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
    "OT-CANCEL-REQUESTER",
    "OT-QUEUE-ORDER",
    "OT-REQ-FOREIGN-TO-FOREIGN",
    "OT-FINAL-TAIL",
    "OT-REQ-CROSS-FORWARD",
    "OT-CHANNEL-MARKER",
    "OT-SYNC-RECOVERY",
    "OT-TG-RETRY",
)
# Staging Iran currently carries a single commodity; keep the driver pinned to it.
DEFAULT_COMMODITY_ID = 1
DEFAULT_OFFER_QUANTITY = 5
DEFAULT_OFFER_PRICE = 100_000
PREFERENCE_MIRROR_TIMEOUT_SECONDS = 60.0
PREFERENCE_MIRROR_POLL_SECONDS = 2.0
BOT_FORWARD_RETRY_TIMEOUT_SECONDS = 60.0
BOT_FORWARD_RETRY_POLL_SECONDS = 3.0
OFFER_MIRROR_TIMEOUT_SECONDS = 60.0
OFFER_MIRROR_POLL_SECONDS = 2.0
CHANNEL_PUBLICATION_TIMEOUT_SECONDS = 90.0
CHANNEL_PUBLICATION_POLL_SECONDS = 3.0
REQUEST_MIRROR_TIMEOUT_SECONDS = 60.0
REQUEST_MIRROR_POLL_SECONDS = 2.0


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


def _synthetic_telegram_id(run_prefix: str, suffix: str) -> int:
    digest = hashlib.sha256(f"{run_prefix}:tg:{suffix}".encode("utf-8")).hexdigest()
    # Stay in a high positive range that will not collide with real bot users.
    return 9_000_000_000 + (int(digest[:12], 16) % 900_000_000)


async def _seed_user(
    session,
    run_prefix: str,
    suffix: str,
    *,
    home_server: str | None = None,
    has_bot_access: bool = False,
    with_telegram: bool = False,
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
        telegram_id=_synthetic_telegram_id(run_prefix, suffix) if with_telegram else None,
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
    with_telegram: bool = False,
) -> User:
    return await _seed_user(
        session,
        run_prefix,
        "owner",
        home_server=home_server,
        has_bot_access=has_bot_access,
        with_telegram=with_telegram,
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
    is_wholesale: bool = True,
    lot_sizes: list[int] | None = None,
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
            is_wholesale=is_wholesale,
            lot_sizes=lot_sizes,
            original_lot_sizes=lot_sizes,
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


async def _create_webapp_offer(
    session,
    owner: User,
    *,
    notes: str,
    is_wholesale: bool = True,
    lot_sizes: list[int] | None = None,
):
    from core.offer_source import OfferSourceSurface

    return await _create_offer(
        session,
        owner,
        notes=notes,
        source_surface=OfferSourceSurface.WEBAPP,
        is_wholesale=is_wholesale,
        lot_sizes=lot_sizes,
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
        # Expire only this row so sibling instances (e.g. requester) stay usable.
        session.expire(owner)
    return False, timeout_seconds


async def _await_offer_by_public_id(
    session,
    offer_public_id: str,
    *,
    timeout_seconds: float = OFFER_MIRROR_TIMEOUT_SECONDS,
):
    """Wait until a mirrored offer row is locally readable by public id."""
    from models.offer import Offer

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        offer = (
            await session.execute(
                select(Offer).where(Offer.offer_public_id == offer_public_id)
            )
        ).scalar_one_or_none()
        if offer is not None:
            return offer, round(timeout_seconds - (deadline - time.monotonic()), 3)
        await asyncio.sleep(OFFER_MIRROR_POLL_SECONDS)
        session.expire_all()
    return None, timeout_seconds


async def _await_user_by_id(
    session,
    user_id: int,
    *,
    timeout_seconds: float = PREFERENCE_MIRROR_TIMEOUT_SECONDS,
) -> User | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        user = (
            await session.execute(select(User).where(User.id == int(user_id)))
        ).scalar_one_or_none()
        if user is not None and not bool(user.is_deleted):
            return user
        await asyncio.sleep(PREFERENCE_MIRROR_POLL_SECONDS)
        session.expire_all()
    return None


async def _await_channel_publication(
    session,
    offer_public_id: str,
    *,
    channel_id: int,
    timeout_seconds: float = CHANNEL_PUBLICATION_TIMEOUT_SECONDS,
):
    """Wait until the foreign channel publication state carries a Telegram message id."""
    from models.offer_publication_state import (
        OfferPublicationState,
        OfferPublicationSurface,
    )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = (
            await session.execute(
                select(OfferPublicationState).where(
                    OfferPublicationState.offer_public_id == offer_public_id,
                    OfferPublicationState.surface
                    == OfferPublicationSurface.TELEGRAM_CHANNEL,
                    OfferPublicationState.telegram_message_id.is_not(None),
                    OfferPublicationState.telegram_chat_id == int(channel_id),
                )
            )
        ).scalar_one_or_none()
        if state is not None:
            return state, round(
                timeout_seconds - (deadline - time.monotonic()), 3
            )
        await asyncio.sleep(CHANNEL_PUBLICATION_POLL_SECONDS)
        session.expire_all()
    return None, timeout_seconds


async def _await_request_status(
    session,
    request_public_id: str,
    *,
    expected_status: str | None = None,
    timeout_seconds: float = REQUEST_MIRROR_TIMEOUT_SECONDS,
):
    """Wait until a mirrored overtime request exists, optionally at a status."""
    from models.offer_request import OfferRequest

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ledger = (
            await session.execute(
                select(OfferRequest).where(
                    OfferRequest.request_public_id == request_public_id
                )
            )
        ).scalar_one_or_none()
        if ledger is not None:
            status = str(getattr(ledger.result_status, "value", ledger.result_status))
            if expected_status is None or status == expected_status:
                return ledger, status, round(
                    timeout_seconds - (deadline - time.monotonic()), 3
                )
        await asyncio.sleep(REQUEST_MIRROR_POLL_SECONDS)
        session.expire_all()
    return None, None, timeout_seconds


def _request_status_value(ledger) -> str:
    return str(getattr(ledger.result_status, "value", ledger.result_status))


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
    # Must go through the ORM so sync outbox listeners still emit change_log rows.
    stamped = datetime.utcnow() - timedelta(minutes=normal_minutes, seconds=30)
    offer.created_at = stamped
    await session.commit()
    await session.refresh(offer)
    return stamped


async def _backdate_offer_past_final(
    session,
    offer,
    *,
    normal_minutes: int,
    overtime_minutes: int,
) -> datetime:
    """Move ``created_at`` so wall-clock ``now`` is past the final public deadline."""
    stamped = datetime.utcnow() - timedelta(
        minutes=normal_minutes + max(0, int(overtime_minutes)),
        seconds=5,
    )
    offer.created_at = stamped
    await session.commit()
    await session.refresh(offer)
    return stamped


async def _approve_presented_overtime_request(
    session,
    *,
    request_public_id: str,
    owner_id: int,
    requester_id: int,
):
    """Owner-approve through the same trade commit path the product uses."""
    from fastapi import BackgroundTasks, HTTPException

    from api.routers.trades import (
        TradeCreate,
        _execute_trade_authoritatively_with_transient_retry,
    )
    from core.services.accountant_relation_service import EffectiveOwnerActor
    from core.services.offer_overtime_request_service import (
        claim_owner_approval,
        load_overtime_request_by_public_id,
    )

    owner = await session.get(User, owner_id)
    requester = await session.get(User, requester_id)
    if owner is None or requester is None:
        raise DriverRefusal("owner or requester missing before overtime approval")
    ledger = await load_overtime_request_by_public_id(
        session,
        request_public_id,
        for_update=True,
    )
    if ledger is None:
        raise DriverRefusal("overtime request disappeared before approval")
    await claim_owner_approval(
        ledger,
        decided_by_user_id=owner_id,
        now=datetime.utcnow(),
    )
    try:
        return await _execute_trade_authoritatively_with_transient_retry(
            trade_data=TradeCreate(
                offer_id=int(ledger.local_offer_id),
                offer_public_id=getattr(ledger, "offer_public_id", None),
                quantity=int(ledger.requested_quantity),
                idempotency_key=getattr(ledger, "idempotency_key", None),
            ),
            background_tasks=BackgroundTasks(),
            db=session,
            context=EffectiveOwnerActor(
                owner_user=requester,
                actor_user=requester,
                relation=None,
                is_accountant_context=False,
            ),
            edge_received_at=getattr(ledger, "received_at", None) or datetime.utcnow(),
            request_source_surface=getattr(ledger, "request_source_surface", None),
            request_source_server=getattr(
                ledger, "request_source_server", current_server()
            ),
            overtime_approval_ledger=ledger,
            overtime_decided_by_user_id=owner_id,
        )
    except HTTPException as exc:
        raise DriverRefusal(f"overtime approval trade failed: {exc.detail}") from exc


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


async def _scenario_cancel_requester(
    session,
    run_prefix: str,
    minutes: int,
) -> dict[str, object]:
    """Requester cancel closes a nonterminal overtime row and frees the offer seat."""
    from core.services.offer_overtime_preference_service import persist_overtime_preference
    from core.services.offer_overtime_request_service import (
        OvertimeRequestCreateCommand,
        create_overtime_request,
        cancel_by_requester,
        get_active_request_for_offer,
    )
    from core.trading_settings import get_trading_settings
    from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus

    if current_server() != "iran":
        raise DriverRefusal("OT-CANCEL-REQUESTER driver currently runs on the Iran peer")
    if minutes <= 0:
        raise DriverRefusal("OT-CANCEL-REQUESTER requires a positive overtime preference")

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
        notes=f"{run_prefix} cancel requester",
    )
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
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
            idempotency_key=f"{run_prefix}:ot-cancel",
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
    request_id = int(ledger.id)
    request_public_id = str(ledger.request_public_id)
    presented_status = str(
        getattr(ledger.result_status, "value", ledger.result_status)
    )

    await cancel_by_requester(
        session,
        ledger,
        requester_user_id=requester_id,
        now=datetime.utcnow(),
        normal_lifetime_minutes=int(getattr(ts, "offer_expiry_minutes", 0) or 0),
    )
    await session.commit()
    await session.refresh(ledger)
    cancelled_status = str(
        getattr(ledger.result_status, "value", ledger.result_status)
    )

    active_after = await get_active_request_for_offer(
        session,
        request_home_server="iran",
        offer_public_id=offer_public_id,
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
        and cancelled_status == OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER.value
        and active_after is None
        and int(request_change_log_rows) > 0
    )

    return {
        "owner_user_id": owner_id,
        "requester_user_id": requester_id,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "request_id": request_id,
        "request_public_id": request_public_id,
        "presented_status": presented_status,
        "cancelled_status": cancelled_status,
        "offer_seat_free_after_cancel": active_after is None,
        "request_change_log_rows": int(request_change_log_rows),
        "passed": passed,
    }


async def _scenario_queue_order(
    session,
    run_prefix: str,
    minutes: int,
) -> dict[str, object]:
    """FIFO promote: second owner-scoped request stays queued until the first frees the seat."""
    from core.services.offer_overtime_preference_service import persist_overtime_preference
    from core.services.offer_overtime_request_service import (
        OvertimeRequestCreateCommand,
        create_overtime_request,
        reject_by_owner,
    )
    from core.trading_settings import get_trading_settings
    from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus

    if current_server() != "iran":
        raise DriverRefusal("OT-QUEUE-ORDER only runs on the Iran peer")
    if minutes <= 0:
        raise DriverRefusal("OT-QUEUE-ORDER requires a positive overtime preference")

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(session, run_prefix)
    requester_a = await _seed_user(session, run_prefix, "requester_a")
    requester_b = await _seed_user(session, run_prefix, "requester_b")
    await session.commit()
    await session.refresh(owner)
    await session.refresh(requester_a)
    await session.refresh(requester_b)
    owner_id = int(owner.id)
    requester_a_id = int(requester_a.id)
    requester_b_id = int(requester_b.id)

    await persist_overtime_preference(session, owner, minutes)
    await session.commit()
    await session.refresh(owner)

    # One live request per offer; FIFO is owner-scoped across the owner's offers.
    offer_a, normal_minutes = await _create_webapp_offer(
        session, owner, notes=f"{run_prefix} queue a"
    )
    offer_b, _ = await _create_webapp_offer(
        session, owner, notes=f"{run_prefix} queue b"
    )
    await _backdate_offer_into_overtime(session, offer_a, normal_minutes=normal_minutes)
    await _backdate_offer_into_overtime(session, offer_b, normal_minutes=normal_minutes)
    offer_a = await _reload_offer(session, int(offer_a.id))
    offer_b = await _reload_offer(session, int(offer_b.id))
    receipt_at = datetime.utcnow()
    ts = get_trading_settings()

    first = await create_overtime_request(
        session,
        OvertimeRequestCreateCommand(
            offer=offer_a,
            requester_user_id=requester_a_id,
            actor_user_id=requester_a_id,
            requested_quantity=min(2, DEFAULT_OFFER_QUANTITY),
            idempotency_key=f"{run_prefix}:ot-queue-a",
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server="iran",
            receipt_at=receipt_at,
            normal_lifetime_minutes=normal_minutes,
            request_home_server="iran",
        ),
        now=receipt_at,
    )
    second = await create_overtime_request(
        session,
        OvertimeRequestCreateCommand(
            offer=offer_b,
            requester_user_id=requester_b_id,
            actor_user_id=requester_b_id,
            requested_quantity=min(2, DEFAULT_OFFER_QUANTITY),
            idempotency_key=f"{run_prefix}:ot-queue-b",
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server="iran",
            receipt_at=receipt_at,
            normal_lifetime_minutes=normal_minutes,
            request_home_server="iran",
        ),
        now=receipt_at,
    )
    await session.commit()
    await session.refresh(first.ledger)
    await session.refresh(second.ledger)

    first_status_before = str(
        getattr(first.ledger.result_status, "value", first.ledger.result_status)
    )
    second_status_before = str(
        getattr(second.ledger.result_status, "value", second.ledger.result_status)
    )
    first_seq = int(first.ledger.queue_sequence or 0)
    second_seq = int(second.ledger.queue_sequence or 0)

    await reject_by_owner(
        session,
        first.ledger,
        decided_by_user_id=owner_id,
        now=datetime.utcnow(),
        normal_lifetime_minutes=int(getattr(ts, "offer_expiry_minutes", 0) or 0),
    )
    await session.commit()
    await session.refresh(first.ledger)
    await session.refresh(second.ledger)

    first_status_after = str(
        getattr(first.ledger.result_status, "value", first.ledger.result_status)
    )
    second_status_after = str(
        getattr(second.ledger.result_status, "value", second.ledger.result_status)
    )

    passed = (
        first.promoted is True
        and second.promoted is False
        and first_status_before == OfferRequestStatus.OVERTIME_PRESENTED.value
        and second_status_before == OfferRequestStatus.OVERTIME_QUEUED.value
        and first_seq < second_seq
        and first_status_after == OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER.value
        and second_status_after == OfferRequestStatus.OVERTIME_PRESENTED.value
    )

    return {
        "owner_user_id": owner_id,
        "requester_a_user_id": requester_a_id,
        "requester_b_user_id": requester_b_id,
        "offer_a_public_id": str(offer_a.offer_public_id),
        "offer_b_public_id": str(offer_b.offer_public_id),
        "first_request_public_id": str(first.ledger.request_public_id),
        "second_request_public_id": str(second.ledger.request_public_id),
        "first_queue_sequence": first_seq,
        "second_queue_sequence": second_seq,
        "first_status_before_reject": first_status_before,
        "second_status_before_reject": second_status_before,
        "first_status_after_reject": first_status_after,
        "second_status_after_reject": second_status_after,
        "passed": passed,
    }


async def _scenario_req_foreign_to_foreign_seed(
    session,
    run_prefix: str,
) -> dict[str, object]:
    """Seed foreign-home owner + requester on Iran for the foreign request path."""
    if current_server() != "iran":
        raise DriverRefusal("OT-REQ-FOREIGN-TO-FOREIGN seed phase only runs on Iran")

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(
        session,
        run_prefix,
        home_server="foreign",
        has_bot_access=True,
        with_telegram=True,
    )
    requester = await _seed_user(
        session,
        run_prefix,
        "requester",
        home_server="foreign",
        has_bot_access=True,
        with_telegram=True,
    )
    await session.commit()
    await session.refresh(owner)
    await session.refresh(requester)
    return {
        "phase": "seed",
        "owner_user_id": int(owner.id),
        "requester_user_id": int(requester.id),
        "owner_home_server": str(owner.home_server),
        "requester_home_server": str(requester.home_server),
        "owner_telegram_id": int(owner.telegram_id),
        "passed": (
            str(owner.home_server) == "foreign"
            and str(requester.home_server) == "foreign"
            and int(owner.telegram_id or 0) > 0
        ),
    }


async def _scenario_req_foreign_to_foreign_run(
    session,
    run_prefix: str,
    minutes: int,
    *,
    owner_user_id: int,
    requester_user_id: int,
) -> dict[str, object]:
    """Bot overtime request on a foreign-home offer stays foreign-authoritative."""
    from core.services.offer_overtime_request_service import (
        OvertimeRequestCreateCommand,
        create_overtime_request,
        mark_presented,
        reject_by_owner,
    )
    from core.trading_settings import get_trading_settings
    from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus

    if current_server() != "foreign":
        raise DriverRefusal("OT-REQ-FOREIGN-TO-FOREIGN run phase only runs on foreign")
    if minutes <= 0:
        raise DriverRefusal("OT-REQ-FOREIGN-TO-FOREIGN requires a positive overtime preference")

    deadline = time.monotonic() + PREFERENCE_MIRROR_TIMEOUT_SECONDS
    owner = requester = None
    while time.monotonic() < deadline:
        owner = (
            await session.execute(select(User).where(User.id == int(owner_user_id)))
        ).scalar_one_or_none()
        requester = (
            await session.execute(select(User).where(User.id == int(requester_user_id)))
        ).scalar_one_or_none()
        if (
            owner is not None
            and requester is not None
            and not bool(owner.is_deleted)
            and not bool(requester.is_deleted)
        ):
            break
        await asyncio.sleep(PREFERENCE_MIRROR_POLL_SECONDS)
        owner = requester = None
    if owner is None or requester is None:
        raise DriverRefusal("Iran-seeded foreign-home users did not mirror to foreign")

    owner_id = int(owner.id)
    requester_id = int(requester.id)

    await _save_bot_preference_with_user_sync_retry(session, owner, minutes)
    mirrored, mirror_seconds = await _await_preference_minutes(
        session, owner_id, minutes
    )
    if not mirrored:
        raise DriverRefusal("foreign preference mirror did not converge before offer create")
    owner = (
        await session.execute(select(User).where(User.id == owner_id))
    ).scalar_one()

    offer, normal_minutes = await _create_bot_offer(
        session,
        owner,
        notes=f"{run_prefix} foreign overtime request",
    )
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
    offer_home = str(offer.home_server)
    snapshot = int(offer.overtime_minutes_snapshot)
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
            idempotency_key=f"{run_prefix}:ot-req-foreign",
            request_source_surface=OfferRequestSourceSurface.TELEGRAM_BOT,
            request_source_server="foreign",
            receipt_at=receipt_at,
            normal_lifetime_minutes=normal_minutes,
            request_home_server="foreign",
        ),
        now=receipt_at,
    )
    await session.commit()
    ledger = create_result.ledger
    await session.refresh(ledger)
    delivering_status = str(
        getattr(ledger.result_status, "value", ledger.result_status)
    )
    request_public_id = str(ledger.request_public_id)
    request_home = str(ledger.request_home_server)
    request_id = int(ledger.id)

    # Simulate Telegram accept landing so the owner decision clock can start.
    await mark_presented(
        session,
        ledger,
        presented_at=datetime.utcnow(),
        telegram_message_id=700_000 + (request_id % 100_000),
    )
    await session.commit()
    await session.refresh(ledger)
    presented_status = str(
        getattr(ledger.result_status, "value", ledger.result_status)
    )

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
        offer_home == "foreign"
        and snapshot == minutes
        and create_result.promoted is True
        and delivering_status == OfferRequestStatus.OVERTIME_DELIVERING.value
        and presented_status == OfferRequestStatus.OVERTIME_PRESENTED.value
        and decided_status == OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER.value
        and request_home == "foreign"
        and int(request_change_log_rows) > 0
    )

    return {
        "phase": "run",
        "owner_user_id": owner_id,
        "requester_user_id": requester_id,
        "preference_mirror_seconds": mirror_seconds,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "offer_home_server": offer_home,
        "overtime_minutes_snapshot": snapshot,
        "request_id": request_id,
        "request_public_id": request_public_id,
        "request_home_server": request_home,
        "delivering_status": delivering_status,
        "presented_status": presented_status,
        "decided_status": decided_status,
        "request_change_log_rows": int(request_change_log_rows),
        "passed": passed,
    }


async def _scenario_req_cross_forward_seed(
    session,
    run_prefix: str,
    minutes: int,
) -> dict[str, object]:
    """Iran: seed owner/requester and an Iran-home overtime offer for foreign edge forward."""
    from core.services.offer_overtime_preference_service import persist_overtime_preference

    if current_server() != "iran":
        raise DriverRefusal("OT-REQ-CROSS-FORWARD seed only runs on the Iran peer")
    if minutes <= 0:
        raise DriverRefusal("OT-REQ-CROSS-FORWARD requires a positive overtime preference")

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
        notes=f"{run_prefix} cross forward",
    )
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
    snapshot = int(offer.overtime_minutes_snapshot)
    if snapshot != minutes:
        raise DriverRefusal(
            f"offer snapshot {snapshot} did not match preference {minutes}"
        )
    await _backdate_offer_into_overtime(session, offer, normal_minutes=normal_minutes)
    # Sync peers can briefly bounce created_at; pin overtime again and prove phase.
    offer = await _reload_offer(session, offer_id)
    await _backdate_offer_into_overtime(session, offer, normal_minutes=normal_minutes)
    offer = await _reload_offer(session, offer_id)
    from core.offer_lifecycle import OfferLifecyclePhase, project_offer_lifecycle

    projection = project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
        as_of=datetime.utcnow(),
    )
    if projection.phase != OfferLifecyclePhase.OVERTIME:
        raise DriverRefusal(
            "seed offer was not in overtime after backdate "
            f"(phase={projection.phase.value})"
        )

    return {
        "phase": "seed",
        "owner_user_id": owner_id,
        "requester_user_id": requester_id,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "offer_home_server": "iran",
        "overtime_minutes_snapshot": snapshot,
        "normal_lifetime_minutes": normal_minutes,
        "lifecycle_phase_after_backdate": projection.phase.value,
        "passed": True,
    }


async def _scenario_req_cross_forward_rebackdate(
    session,
    *,
    offer_public_id: str,
) -> dict[str, object]:
    """Iran: re-pin an Iran-home offer into overtime just before the foreign edge run."""
    from core.offer_lifecycle import OfferLifecyclePhase, project_offer_lifecycle
    from core.trading_settings import get_trading_settings
    from models.offer import Offer

    if current_server() != "iran":
        raise DriverRefusal("OT-REQ-CROSS-FORWARD rebackdate only runs on the Iran peer")
    public_id = (offer_public_id or "").strip()
    if not public_id:
        raise DriverRefusal("rebackdate requires --offer-public-id")

    offer = (
        await session.execute(select(Offer).where(Offer.offer_public_id == public_id))
    ).scalar_one_or_none()
    if offer is None:
        raise DriverRefusal(f"offer {public_id} not found on Iran for rebackdate")
    normal_minutes = int(getattr(get_trading_settings(), "offer_expiry_minutes", 2) or 2)
    await _backdate_offer_into_overtime(session, offer, normal_minutes=normal_minutes)
    offer = await _reload_offer(session, int(offer.id))
    projection = project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
        as_of=datetime.utcnow(),
    )
    passed = projection.phase == OfferLifecyclePhase.OVERTIME
    return {
        "phase": "rebackdate",
        "offer_id": int(offer.id),
        "offer_public_id": public_id,
        "lifecycle_phase": projection.phase.value,
        "accepts_overtime_request": projection.accepts_overtime_request,
        "passed": passed,
    }


async def _scenario_req_cross_forward_run(
    session,
    run_prefix: str,
    minutes: int,
    *,
    owner_user_id: int,
    requester_user_id: int,
    offer_public_id: str,
) -> dict[str, object]:
    """Foreign edge: ambiguous 504 → M18 pending; live forward → overtime, not trade-complete."""
    import json
    from unittest.mock import AsyncMock, patch

    from fastapi import BackgroundTasks

    from api.routers.trades import TradeCreate, _forward_trade_if_remote_home
    from core.server_routing import is_remote_home
    from core.services.accountant_relation_service import EffectiveOwnerActor
    from core.services.offer_request_ledger_service import load_offer_request_by_idempotency
    from core.trade_forward_pending import (
        AMBIGUOUS_FORWARD_PENDING_MESSAGE,
        clear_trade_forward_pending,
        get_trade_forward_pending,
    )
    from models.offer_request import OfferRequest

    del minutes  # seed already froze the snapshot; run only needs mirrored rows
    if current_server() != "foreign":
        raise DriverRefusal("OT-REQ-CROSS-FORWARD run phase only runs on the foreign peer")
    public_id = (offer_public_id or "").strip()
    if not public_id:
        raise DriverRefusal("OT-REQ-CROSS-FORWARD run requires --offer-public-id from Iran seed")

    requester = await _await_user_by_id(session, requester_user_id)
    if requester is None:
        raise DriverRefusal(
            f"Iran-seeded requester_user_id={requester_user_id} did not mirror to foreign"
        )
    owner = await _await_user_by_id(session, owner_user_id)
    if owner is None:
        raise DriverRefusal(
            f"Iran-seeded owner_user_id={owner_user_id} did not mirror to foreign"
        )

    offer, offer_mirror_seconds = await _await_offer_by_public_id(session, public_id)
    if offer is None:
        raise DriverRefusal(f"Iran offer {public_id} did not mirror to foreign")
    offer_id = int(offer.id)
    offer_home = str(offer.home_server)
    if offer_home != "iran" or not is_remote_home(offer_home):
        raise DriverRefusal(
            f"expected remote Iran-home offer, got home_server={offer_home!r}"
        )

    requester_id = int(requester.id)
    trade_qty = int(
        getattr(offer, "remaining_quantity", None)
        if getattr(offer, "remaining_quantity", None) is not None
        else getattr(offer, "quantity", DEFAULT_OFFER_QUANTITY)
    )
    context = EffectiveOwnerActor(
        owner_user=requester,
        actor_user=requester,
        relation=None,
        is_accountant_context=False,
    )
    edge_received_at = datetime.utcnow()

    # --- Ambiguous timeout path (forced 504): M18 + redis retain, no local ledger ---
    m18_key = f"{run_prefix}:ot-cross-m18"
    background = BackgroundTasks()
    with patch(
        "api.routers.trades.forward_trade_to_home_server",
        AsyncMock(return_value=(504, {"detail": "timeout"})),
    ):
        m18_response = await _forward_trade_if_remote_home(
            session,
            TradeCreate(
                offer_id=offer_id,
                offer_public_id=public_id,
                quantity=trade_qty,
                idempotency_key=m18_key,
            ),
            context,
            edge_received_at,
            request_source_surface="webapp",
            background_tasks=background,
        )
    if m18_response is None:
        raise DriverRefusal("remote-home forward returned None; offer was not treated as remote")
    m18_body = json.loads(m18_response.body.decode())
    pending_row = await get_trade_forward_pending(m18_key)
    local_m18_ledger = (
        await session.execute(
            select(OfferRequest).where(OfferRequest.idempotency_key == m18_key)
        )
    ).scalar_one_or_none()
    # Do not run the queued reconciler; clear the marker so staging redis stays clean.
    await clear_trade_forward_pending(m18_key)
    pending_after_clear = await get_trade_forward_pending(m18_key)

    m18_ok = (
        int(m18_response.status_code) == 202
        and m18_body.get("detail") == AMBIGUOUS_FORWARD_PENDING_MESSAGE
        and m18_body.get("workflow") == "forward_pending"
        and m18_body.get("pending") is True
        and pending_row is not None
        and str(pending_row.get("home_server")) == "iran"
        and local_m18_ledger is None
        and pending_after_clear is None
        and len(background.tasks) == 1
    )

    # --- Live forward: Iran home answers overtime intake, never a false trade-complete ---
    live_key = f"{run_prefix}:ot-cross-ok"
    live_response = await _forward_trade_if_remote_home(
        session,
        TradeCreate(
            offer_id=offer_id,
            offer_public_id=public_id,
            quantity=trade_qty,
            idempotency_key=live_key,
        ),
        context,
        edge_received_at,
        request_source_surface="webapp",
        background_tasks=BackgroundTasks(),
    )
    if live_response is None:
        raise DriverRefusal("live remote-home forward returned None")
    live_body = json.loads(live_response.body.decode())
    local_live_ledger = (
        await session.execute(
            select(OfferRequest).where(OfferRequest.idempotency_key == live_key)
        )
    ).scalar_one_or_none()
    # Authoritative overtime row belongs on Iran; foreign must not invent one.
    iran_home_ledger = await load_offer_request_by_idempotency(
        session,
        request_home_server="foreign",
        idempotency_key=live_key,
    )

    live_workflow = str(live_body.get("workflow") or "")
    live_ok = (
        int(live_response.status_code) == 202
        and live_workflow == "overtime"
        and live_body.get("pending") is not True
        and "trade_number" not in live_body
        and local_live_ledger is None
        and iran_home_ledger is None
    )

    passed = m18_ok and live_ok
    return {
        "phase": "run",
        "owner_user_id": int(owner.id),
        "requester_user_id": requester_id,
        "offer_id": offer_id,
        "offer_public_id": public_id,
        "offer_home_server": offer_home,
        "offer_mirror_seconds": offer_mirror_seconds,
        "m18": {
            "status_code": int(m18_response.status_code),
            "detail": m18_body.get("detail"),
            "workflow": m18_body.get("workflow"),
            "pending": m18_body.get("pending"),
            "redis_pending_retained": pending_row is not None,
            "local_ledger_created": local_m18_ledger is not None,
            "reconcile_task_queued": len(background.tasks) == 1,
            "redis_cleared_after": pending_after_clear is None,
            "passed": m18_ok,
        },
        "live_forward": {
            "status_code": int(live_response.status_code),
            "workflow": live_workflow,
            "result_status": live_body.get("result_status"),
            "has_trade_number": "trade_number" in live_body,
            "local_ledger_created": local_live_ledger is not None,
            "body_keys": sorted(str(key) for key in live_body.keys()),
            "passed": live_ok,
        },
        "passed": passed,
    }


async def _scenario_channel_marker_seed(session, run_prefix: str) -> dict[str, object]:
    """Iran seed: foreign-home bot owner for channel lifecycle marker coverage."""
    return await _scenario_offer_bot_origin_seed(session, run_prefix)


async def _scenario_channel_marker_run(
    session,
    run_prefix: str,
    minutes: int,
    *,
    owner_user_id: int,
) -> dict[str, object]:
    """Foreign channel: overtime/final-tail edits enqueue with the ⏳ marker."""
    from core.config import settings
    from core.offer_lifecycle import OfferLifecyclePhase
    from core.services.telegram_offer_channel_service import (
        CHANNEL_LIFECYCLE_METADATA_KEY,
        TELEGRAM_OFFER_OVERTIME_MARKER,
        build_offer_channel_message,
        build_offer_channel_reply_markup,
        offer_channel_overtime_marker_visible,
        project_offer_channel_lifecycle,
    )
    from core.services.telegram_offer_queue_service import (
        enqueue_offer_lifecycle_channel_handoffs,
    )
    from core.telegram_delivery_queue_contract import TelegramDeliveryAction
    from models.offer_publication_state import OfferPublicationState
    from models.telegram_delivery_job import TelegramDeliveryJobRecord

    if current_server() != "foreign":
        raise DriverRefusal("OT-CHANNEL-MARKER run phase only runs on the foreign peer")
    if minutes <= 0:
        raise DriverRefusal("OT-CHANNEL-MARKER requires a positive overtime preference")

    channel_id = int(getattr(settings, "channel_id", 0) or 0)
    if channel_id == 0:
        raise DriverRefusal("staging foreign channel_id is not configured")

    owner = await _await_user_by_id(session, owner_user_id)
    if owner is None:
        raise DriverRefusal(
            f"Iran-seeded owner_user_id={owner_user_id} did not mirror to foreign"
        )
    if str(owner.home_server) != "foreign":
        raise DriverRefusal(
            f"owner_user_id={owner_user_id} home_server={owner.home_server!r}, expected foreign"
        )
    owner_id = int(owner.id)

    await _save_bot_preference_with_user_sync_retry(session, owner, minutes)
    mirrored, mirror_seconds = await _await_preference_minutes(session, owner_id, minutes)
    if not mirrored:
        raise DriverRefusal(
            f"foreign preference mirror for user {owner_id} did not converge to {minutes}"
        )
    await session.refresh(owner)

    offer, normal_minutes = await _create_bot_offer(
        session,
        owner,
        notes=f"{run_prefix} channel marker",
    )
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
    snapshot = int(offer.overtime_minutes_snapshot)
    if snapshot != minutes:
        raise DriverRefusal(
            f"offer snapshot {snapshot} did not match preference {minutes}"
        )

    pub_state, publication_seconds = await _await_channel_publication(
        session,
        offer_public_id,
        channel_id=channel_id,
    )
    if pub_state is None:
        raise DriverRefusal(
            f"channel publication for {offer_public_id} did not receive a message id"
        )
    telegram_message_id = int(pub_state.telegram_message_id)
    # Publication polling expires the session; reload before channel renderers.
    offer = await _reload_offer(session, offer_id)

    # Normal phase must not show the overtime marker on an active channel post.
    normal_projection = project_offer_channel_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
    )
    normal_message = build_offer_channel_message(
        offer,
        lifecycle_phase=normal_projection.phase.value,
    )
    normal_marker_hidden = (
        normal_projection.phase == OfferLifecyclePhase.NORMAL
        and TELEGRAM_OFFER_OVERTIME_MARKER not in normal_message
    )

    await _backdate_offer_into_overtime(session, offer, normal_minutes=normal_minutes)
    offer = await _reload_offer(session, offer_id)
    overtime_projection = project_offer_channel_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
    )
    overtime_handoffs = await enqueue_offer_lifecycle_channel_handoffs(
        session,
        current_server=current_server(),
        expected_channel_id=channel_id,
        offer_expiry_minutes=normal_minutes,
        limit=100,
    )
    await session.commit()
    offer = await _reload_offer(session, offer_id)
    overtime_projection = project_offer_channel_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
    )
    overtime_ours = [
        item
        for item in overtime_handoffs
        if str(item.offer_public_id) == offer_public_id
    ]
    overtime_handoff = overtime_ours[0] if overtime_ours else None
    overtime_action = (
        str(getattr(overtime_handoff.action, "value", overtime_handoff.action))
        if overtime_handoff is not None and overtime_handoff.action is not None
        else None
    )
    overtime_enqueued = bool(
        overtime_handoff is not None
        and overtime_handoff.queue_result is not None
        and overtime_handoff.skipped_reason is None
        and overtime_action == TelegramDeliveryAction.OVERTIME_CHANNEL_EDIT.value
    )

    pub_state = (
        await session.execute(
            select(OfferPublicationState).where(
                OfferPublicationState.offer_public_id == offer_public_id
            )
        )
    ).scalar_one()
    overtime_metadata_phase = str(
        (pub_state.state_metadata or {}).get(CHANNEL_LIFECYCLE_METADATA_KEY) or ""
    )
    overtime_message = build_offer_channel_message(
        offer,
        lifecycle_phase=overtime_projection.phase.value,
    )
    overtime_marker_visible = (
        overtime_projection.phase == OfferLifecyclePhase.OVERTIME
        and offer_channel_overtime_marker_visible(
            offer,
            lifecycle_phase=overtime_projection.phase.value,
        )
        and TELEGRAM_OFFER_OVERTIME_MARKER in overtime_message
    )

    overtime_jobs = (
        await session.execute(
            select(func.count())
            .select_from(TelegramDeliveryJobRecord)
            .where(
                TelegramDeliveryJobRecord.source_natural_id == offer_public_id,
                TelegramDeliveryJobRecord.action_kind
                == TelegramDeliveryAction.OVERTIME_CHANNEL_EDIT,
            )
        )
    ).scalar_one()

    await _backdate_offer_past_final(
        session,
        offer,
        normal_minutes=normal_minutes,
        overtime_minutes=snapshot,
    )
    offer = await _reload_offer(session, offer_id)
    final_projection = project_offer_channel_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
    )
    final_handoffs = await enqueue_offer_lifecycle_channel_handoffs(
        session,
        current_server=current_server(),
        expected_channel_id=channel_id,
        offer_expiry_minutes=normal_minutes,
        limit=100,
    )
    await session.commit()
    offer = await _reload_offer(session, offer_id)
    final_projection = project_offer_channel_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
    )
    final_ours = [
        item
        for item in final_handoffs
        if str(item.offer_public_id) == offer_public_id
    ]
    final_handoff = final_ours[0] if final_ours else None
    final_action = (
        str(getattr(final_handoff.action, "value", final_handoff.action))
        if final_handoff is not None and final_handoff.action is not None
        else None
    )
    final_enqueued = bool(
        final_handoff is not None
        and final_handoff.queue_result is not None
        and final_handoff.skipped_reason is None
        and final_action == TelegramDeliveryAction.FINAL_TAIL_CHANNEL_EDIT.value
    )

    pub_state = (
        await session.execute(
            select(OfferPublicationState).where(
                OfferPublicationState.offer_public_id == offer_public_id
            )
        )
    ).scalar_one()
    final_metadata_phase = str(
        (pub_state.state_metadata or {}).get(CHANNEL_LIFECYCLE_METADATA_KEY) or ""
    )
    final_message = build_offer_channel_message(
        offer,
        lifecycle_phase=final_projection.phase.value,
    )
    final_marker_visible = (
        final_projection.phase == OfferLifecyclePhase.FINAL_TAIL
        and TELEGRAM_OFFER_OVERTIME_MARKER in final_message
    )
    final_markup = build_offer_channel_reply_markup(
        offer,
        accepts_new_public_interaction=final_projection.accepts_new_public_interaction,
    )
    final_jobs = (
        await session.execute(
            select(func.count())
            .select_from(TelegramDeliveryJobRecord)
            .where(
                TelegramDeliveryJobRecord.source_natural_id == offer_public_id,
                TelegramDeliveryJobRecord.action_kind
                == TelegramDeliveryAction.FINAL_TAIL_CHANNEL_EDIT,
            )
        )
    ).scalar_one()

    passed = (
        snapshot == minutes
        and telegram_message_id > 0
        and normal_marker_hidden
        and overtime_enqueued
        and overtime_metadata_phase == OfferLifecyclePhase.OVERTIME.value
        and overtime_marker_visible
        and int(overtime_jobs) > 0
        and final_enqueued
        and final_metadata_phase == OfferLifecyclePhase.FINAL_TAIL.value
        and final_marker_visible
        and final_markup is None
        and int(final_jobs) > 0
    )

    return {
        "phase": "run",
        "owner_user_id": owner_id,
        "preference_mirror_seconds": mirror_seconds,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "overtime_minutes_snapshot": snapshot,
        "normal_lifetime_minutes": normal_minutes,
        "channel_id": channel_id,
        "telegram_message_id": telegram_message_id,
        "publication_seconds": publication_seconds,
        "normal_phase": normal_projection.phase.value,
        "normal_marker_hidden": normal_marker_hidden,
        "overtime_phase": overtime_projection.phase.value,
        "overtime_channel_edit_enqueued": overtime_enqueued,
        "overtime_metadata_phase": overtime_metadata_phase,
        "overtime_marker_visible": overtime_marker_visible,
        "overtime_delivery_jobs": int(overtime_jobs),
        "final_tail_phase": final_projection.phase.value,
        "final_tail_channel_edit_enqueued": final_enqueued,
        "final_tail_metadata_phase": final_metadata_phase,
        "final_tail_marker_visible": final_marker_visible,
        "final_tail_trade_buttons_stripped": final_markup is None,
        "final_tail_delivery_jobs": int(final_jobs),
        "passed": passed,
    }


async def _scenario_sync_recovery_seed(
    session,
    run_prefix: str,
    minutes: int,
) -> dict[str, object]:
    """Iran: seed an Iran-home overtime request that must mirror before partition."""
    from core.services.offer_overtime_preference_service import persist_overtime_preference
    from core.services.offer_overtime_request_service import (
        OvertimeRequestCreateCommand,
        create_overtime_request,
    )
    from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus

    if current_server() != "iran":
        raise DriverRefusal("OT-SYNC-RECOVERY seed only runs on the Iran peer")
    if minutes <= 0:
        raise DriverRefusal("OT-SYNC-RECOVERY requires a positive overtime preference")

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
        notes=f"{run_prefix} sync recovery",
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
    create_result = await create_overtime_request(
        session,
        OvertimeRequestCreateCommand(
            offer=offer,
            requester_user_id=requester_id,
            actor_user_id=requester_id,
            requested_quantity=min(2, DEFAULT_OFFER_QUANTITY),
            idempotency_key=f"{run_prefix}:ot-sync-a",
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
    # Orchestration (mirror + worker stop) can exceed the default 30s decision
    # window; pin a longer deadline so partition_mutate can still cancel.
    ledger.decision_deadline_at = datetime.utcnow() + timedelta(minutes=15)
    await session.commit()
    await session.refresh(ledger)
    status = _request_status_value(ledger)
    passed = (
        create_result.promoted is True
        and status == OfferRequestStatus.OVERTIME_PRESENTED.value
        and ledger.decision_deadline_at is not None
    )
    return {
        "phase": "seed",
        "owner_user_id": owner_id,
        "requester_user_id": requester_id,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "overtime_minutes_snapshot": snapshot,
        "normal_lifetime_minutes": normal_minutes,
        "request_a_public_id": str(ledger.request_public_id),
        "request_a_idempotency_key": f"{run_prefix}:ot-sync-a",
        "request_a_status": status,
        "decision_deadline_at": (
            ledger.decision_deadline_at.isoformat()
            if ledger.decision_deadline_at is not None
            else None
        ),
        "passed": passed,
    }


async def _scenario_sync_recovery_assert_mirror(
    session,
    *,
    request_a_public_id: str,
) -> dict[str, object]:
    """Foreign: prove request A mirrored as presented before the partition."""
    from models.offer_request import OfferRequestStatus

    if current_server() != "foreign":
        raise DriverRefusal("OT-SYNC-RECOVERY assert_mirror only runs on foreign")
    public_id = (request_a_public_id or "").strip()
    if not public_id:
        raise DriverRefusal("assert_mirror requires --request-a-public-id")

    ledger, status, seconds = await _await_request_status(
        session,
        public_id,
        expected_status=OfferRequestStatus.OVERTIME_PRESENTED.value,
    )
    passed = ledger is not None and status == OfferRequestStatus.OVERTIME_PRESENTED.value
    return {
        "phase": "assert_mirror",
        "request_a_public_id": public_id,
        "request_a_status": status,
        "mirror_seconds": seconds,
        "passed": passed,
    }


async def _scenario_sync_recovery_partition_mutate(
    session,
    run_prefix: str,
    minutes: int,
    *,
    request_a_public_id: str,
) -> dict[str, object]:
    """Iran under partition: terminalize A and open B so foreign can stay skewed."""
    from core.services.offer_overtime_request_service import (
        OvertimeRequestCreateCommand,
        cancel_by_requester,
        create_overtime_request,
        get_active_request_for_offer,
        load_overtime_request_by_public_id,
    )
    from core.trading_settings import get_trading_settings
    from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus

    if current_server() != "iran":
        raise DriverRefusal("OT-SYNC-RECOVERY partition_mutate only runs on Iran")
    public_a = (request_a_public_id or "").strip()
    if not public_a:
        raise DriverRefusal("partition_mutate requires --request-a-public-id")

    ledger_a = await load_overtime_request_by_public_id(session, public_a, for_update=True)
    if ledger_a is None:
        raise DriverRefusal(f"request A {public_a} missing on Iran before mutate")
    owner_id = int(ledger_a.offer_owner_user_id)
    requester_id = int(ledger_a.requester_user_id)
    offer_public_id = str(ledger_a.offer_public_id)
    offer_id = int(ledger_a.local_offer_id)
    ts = get_trading_settings()
    normal_minutes = int(getattr(ts, "offer_expiry_minutes", 0) or 0)

    await cancel_by_requester(
        session,
        ledger_a,
        requester_user_id=requester_id,
        now=datetime.utcnow(),
        normal_lifetime_minutes=normal_minutes,
    )
    await session.commit()
    await session.refresh(ledger_a)
    status_a = _request_status_value(ledger_a)
    seat_after_cancel = await get_active_request_for_offer(
        session,
        request_home_server="iran",
        offer_public_id=offer_public_id,
    )

    offer = await _reload_offer(session, offer_id)
    # Keep the offer inside overtime after any sync bounce of created_at.
    await _backdate_offer_into_overtime(session, offer, normal_minutes=normal_minutes)
    offer = await _reload_offer(session, offer_id)
    receipt_b = datetime.utcnow()
    create_b = await create_overtime_request(
        session,
        OvertimeRequestCreateCommand(
            offer=offer,
            requester_user_id=requester_id,
            actor_user_id=requester_id,
            requested_quantity=min(2, DEFAULT_OFFER_QUANTITY),
            idempotency_key=f"{run_prefix}:ot-sync-b",
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server="iran",
            receipt_at=receipt_b,
            normal_lifetime_minutes=normal_minutes,
            request_home_server="iran",
        ),
        now=receipt_b,
    )
    await session.commit()
    ledger_b = create_b.ledger
    await session.refresh(ledger_b)
    status_b = _request_status_value(ledger_b)
    active = await get_active_request_for_offer(
        session,
        request_home_server="iran",
        offer_public_id=offer_public_id,
    )

    passed = (
        status_a == OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER.value
        and seat_after_cancel is None
        and create_b.promoted is True
        and status_b == OfferRequestStatus.OVERTIME_PRESENTED.value
        and active is not None
        and str(active.request_public_id) == str(ledger_b.request_public_id)
    )
    return {
        "phase": "partition_mutate",
        "owner_user_id": owner_id,
        "requester_user_id": requester_id,
        "offer_public_id": offer_public_id,
        "request_a_public_id": public_a,
        "request_a_status": status_a,
        "request_b_public_id": str(ledger_b.request_public_id),
        "request_b_idempotency_key": f"{run_prefix}:ot-sync-b",
        "request_b_status": status_b,
        "active_request_public_id": (
            str(active.request_public_id) if active is not None else None
        ),
        "minutes": minutes,
        "passed": passed,
    }


async def _scenario_sync_recovery_assert_skew(
    session,
    *,
    request_a_public_id: str,
    request_b_public_id: str,
) -> dict[str, object]:
    """Foreign under partition: A still nonterminal; B must not have arrived yet."""
    from models.offer_request import OfferRequest, OfferRequestStatus

    if current_server() != "foreign":
        raise DriverRefusal("OT-SYNC-RECOVERY assert_skew only runs on foreign")
    public_a = (request_a_public_id or "").strip()
    public_b = (request_b_public_id or "").strip()
    if not public_a or not public_b:
        raise DriverRefusal("assert_skew requires --request-a-public-id and --request-b-public-id")

    ledger_a = (
        await session.execute(
            select(OfferRequest).where(OfferRequest.request_public_id == public_a)
        )
    ).scalar_one_or_none()
    ledger_b = (
        await session.execute(
            select(OfferRequest).where(OfferRequest.request_public_id == public_b)
        )
    ).scalar_one_or_none()
    status_a = _request_status_value(ledger_a) if ledger_a is not None else None
    passed = (
        ledger_a is not None
        and status_a == OfferRequestStatus.OVERTIME_PRESENTED.value
        and ledger_b is None
    )
    return {
        "phase": "assert_skew",
        "request_a_public_id": public_a,
        "request_a_status": status_a,
        "request_b_present": ledger_b is not None,
        "passed": passed,
    }


async def _scenario_sync_recovery_assert_converge(
    session,
    *,
    offer_public_id: str,
    request_a_public_id: str,
    request_b_public_id: str,
    owner_user_id: int,
) -> dict[str, object]:
    """After recover: A terminal, B presented, one nonterminal seat, no dual occupy."""
    from core.services.offer_overtime_request_service import (
        get_active_request_for_offer,
        list_nonterminal_overtime_requests,
    )
    from models.offer_request import (
        OVERTIME_OWNER_OCCUPYING_STATUSES,
        OfferRequest,
        OfferRequestStatus,
    )

    public_offer = (offer_public_id or "").strip()
    public_a = (request_a_public_id or "").strip()
    public_b = (request_b_public_id or "").strip()
    if not public_offer or not public_a or not public_b:
        raise DriverRefusal(
            "assert_converge requires offer and both request public ids"
        )

    # Wait for terminal A and presented B to land on this peer.
    ledger_a, status_a, seconds_a = await _await_request_status(
        session,
        public_a,
        expected_status=OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER.value,
        timeout_seconds=90.0,
    )
    ledger_b, status_b, seconds_b = await _await_request_status(
        session,
        public_b,
        expected_status=OfferRequestStatus.OVERTIME_PRESENTED.value,
        timeout_seconds=90.0,
    )

    nonterminals = await list_nonterminal_overtime_requests(
        session,
        offer_public_id=public_offer,
        request_home_server="iran",
    )
    nonterminal_ids = [str(row.request_public_id) for row in nonterminals]
    occupying = (
        await session.execute(
            select(OfferRequest).where(
                OfferRequest.request_home_server == "iran",
                OfferRequest.offer_owner_user_id == int(owner_user_id),
                OfferRequest.result_status.in_(OVERTIME_OWNER_OCCUPYING_STATUSES),
            )
        )
    ).scalars().all()
    active = await get_active_request_for_offer(
        session,
        request_home_server="iran",
        offer_public_id=public_offer,
    )

    passed = (
        ledger_a is not None
        and status_a == OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER.value
        and ledger_b is not None
        and status_b == OfferRequestStatus.OVERTIME_PRESENTED.value
        and nonterminal_ids == [public_b]
        and len(occupying) == 1
        and str(occupying[0].request_public_id) == public_b
        and active is not None
        and str(active.request_public_id) == public_b
    )
    return {
        "phase": "assert_converge",
        "server_mode": current_server(),
        "offer_public_id": public_offer,
        "request_a_status": status_a,
        "request_a_converge_seconds": seconds_a,
        "request_b_status": status_b,
        "request_b_converge_seconds": seconds_b,
        "nonterminal_request_public_ids": nonterminal_ids,
        "owner_occupying_count": len(occupying),
        "active_request_public_id": (
            str(active.request_public_id) if active is not None else None
        ),
        "passed": passed,
    }


async def _scenario_final_tail(
    session,
    run_prefix: str,
    minutes: int,
) -> dict[str, object]:
    """Partial overtime trade leaves a remainder; occupying request past final → final_tail."""
    from core.offer_expiry import _offer_ids_with_final_tail_requests
    from core.offer_lifecycle import OfferLifecyclePhase, project_offer_lifecycle
    from core.services.offer_overtime_preference_service import persist_overtime_preference
    from core.services.offer_overtime_request_service import (
        OvertimeRequestCreateCommand,
        create_overtime_request,
        load_overtime_request_by_public_id,
    )
    from core.services.telegram_offer_channel_service import (
        offer_channel_overtime_marker_visible,
    )
    from models.offer import OfferStatus
    from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus

    if current_server() != "iran":
        raise DriverRefusal("OT-FINAL-TAIL currently runs on the Iran peer")
    if minutes <= 0:
        raise DriverRefusal("OT-FINAL-TAIL requires a positive overtime preference")

    partial_qty = min(2, DEFAULT_OFFER_QUANTITY)
    expected_remaining = DEFAULT_OFFER_QUANTITY - partial_qty
    if expected_remaining <= 0:
        raise DriverRefusal("OT-FINAL-TAIL needs an offer quantity larger than the partial trade")

    await _cleanup(session, run_prefix)
    owner = await _seed_owner(session, run_prefix)
    requester_a = await _seed_user(session, run_prefix, "requester_a")
    requester_b = await _seed_user(session, run_prefix, "requester_b")
    await session.commit()
    await session.refresh(owner)
    await session.refresh(requester_a)
    await session.refresh(requester_b)
    owner_id = int(owner.id)
    requester_a_id = int(requester_a.id)
    requester_b_id = int(requester_b.id)

    await persist_overtime_preference(session, owner, minutes)
    await session.commit()
    await session.refresh(owner)

    # Retail lots are required: wholesale offers only accept the full remainder.
    offer, normal_minutes = await _create_webapp_offer(
        session,
        owner,
        notes=f"{run_prefix} final tail",
        is_wholesale=False,
        lot_sizes=[partial_qty],
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
    receipt_a = datetime.utcnow()

    first = await create_overtime_request(
        session,
        OvertimeRequestCreateCommand(
            offer=offer,
            requester_user_id=requester_a_id,
            actor_user_id=requester_a_id,
            requested_quantity=partial_qty,
            idempotency_key=f"{run_prefix}:ot-final-a",
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server="iran",
            receipt_at=receipt_a,
            normal_lifetime_minutes=normal_minutes,
            request_home_server="iran",
        ),
        now=receipt_a,
    )
    await session.commit()
    first_ledger = first.ledger
    await session.refresh(first_ledger)
    first_request_public_id = str(first_ledger.request_public_id)
    first_presented = str(
        getattr(first_ledger.result_status, "value", first_ledger.result_status)
    )

    await _approve_presented_overtime_request(
        session,
        request_public_id=first_request_public_id,
        owner_id=owner_id,
        requester_id=requester_a_id,
    )

    offer = await _reload_offer(session, offer_id)
    first_ledger = await load_overtime_request_by_public_id(
        session,
        first_request_public_id,
    )
    if first_ledger is None:
        raise DriverRefusal("first overtime request disappeared after approval")
    first_decided = str(
        getattr(first_ledger.result_status, "value", first_ledger.result_status)
    )
    remaining_after_trade = int(
        getattr(offer, "remaining_quantity", None)
        if getattr(offer, "remaining_quantity", None) is not None
        else getattr(offer, "quantity", 0)
    )
    committed_after_trade = bool(getattr(offer, "overtime_trade_committed", False))
    status_after_trade = str(getattr(offer.status, "value", offer.status))

    # Second occupying request while the remainder is still public in overtime.
    receipt_b = datetime.utcnow()
    second = await create_overtime_request(
        session,
        OvertimeRequestCreateCommand(
            offer=offer,
            requester_user_id=requester_b_id,
            actor_user_id=requester_b_id,
            requested_quantity=min(partial_qty, remaining_after_trade),
            idempotency_key=f"{run_prefix}:ot-final-b",
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server="iran",
            receipt_at=receipt_b,
            normal_lifetime_minutes=normal_minutes,
            request_home_server="iran",
        ),
        now=receipt_b,
    )
    await session.commit()
    second_ledger = second.ledger
    await session.refresh(second_ledger)
    second_request_public_id = str(second_ledger.request_public_id)
    second_presented = str(
        getattr(second_ledger.result_status, "value", second_ledger.result_status)
    )
    second_request_id = int(second_ledger.id)

    await _backdate_offer_past_final(
        session,
        offer,
        normal_minutes=normal_minutes,
        overtime_minutes=snapshot,
    )
    offer = await _reload_offer(session, offer_id)
    as_of = datetime.utcnow()
    occupying_ids = await _offer_ids_with_final_tail_requests(session, [offer_id])
    has_occupying_tail = offer_id in occupying_ids

    without_tail = project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
        as_of=as_of,
        has_final_tail_request=False,
    )
    with_tail = project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=normal_minutes,
        as_of=as_of,
        has_final_tail_request=True,
    )
    marker_visible = offer_channel_overtime_marker_visible(
        offer,
        lifecycle_phase=with_tail.phase.value,
    )

    passed = (
        first.promoted is True
        and first_presented == OfferRequestStatus.OVERTIME_PRESENTED.value
        and first_decided == OfferRequestStatus.COMPLETED_TRADE.value
        and committed_after_trade is True
        and remaining_after_trade == expected_remaining
        and status_after_trade == OfferStatus.ACTIVE.value
        and second.promoted is True
        and second_presented == OfferRequestStatus.OVERTIME_PRESENTED.value
        and has_occupying_tail is True
        and without_tail.phase == OfferLifecyclePhase.EXPIRED
        and without_tail.terminal_expiry_due is True
        and with_tail.phase == OfferLifecyclePhase.FINAL_TAIL
        and with_tail.terminal_expiry_due is False
        and with_tail.accepts_new_public_interaction is False
        and marker_visible is True
    )

    return {
        "owner_user_id": owner_id,
        "requester_a_user_id": requester_a_id,
        "requester_b_user_id": requester_b_id,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "overtime_minutes_snapshot": snapshot,
        "normal_lifetime_minutes": normal_minutes,
        "partial_quantity": partial_qty,
        "first_request_public_id": first_request_public_id,
        "first_presented_status": first_presented,
        "first_decided_status": first_decided,
        "remaining_after_trade": remaining_after_trade,
        "overtime_trade_committed": committed_after_trade,
        "offer_status_after_trade": status_after_trade,
        "second_request_id": second_request_id,
        "second_request_public_id": second_request_public_id,
        "second_presented_status": second_presented,
        "has_occupying_final_tail_request": has_occupying_tail,
        "phase_without_tail_hold": without_tail.phase.value,
        "terminal_expiry_without_tail_hold": without_tail.terminal_expiry_due,
        "phase_with_tail_hold": with_tail.phase.value,
        "terminal_expiry_with_tail_hold": with_tail.terminal_expiry_due,
        "accepts_new_public_interaction_in_final_tail": (
            with_tail.accepts_new_public_interaction
        ),
        "channel_marker_visible_in_final_tail": marker_visible,
        "passed": passed,
    }


async def _lease_telegram_delivery_job_by_id(
    session,
    *,
    job_id: int,
    worker_id: str,
    request_timeout_seconds: float = 10.0,
    lease_seconds: float = 30.0,
) -> object:
    """Lease one known job id without racing the shared staging claim lane."""
    from core.services.telegram_delivery_queue_service import (
        telegram_delivery_database_now,
    )
    from core.telegram_delivery_queue_contract import (
        CLAIMABLE_DELIVERY_STATES,
        MINIMUM_LEASE_MARGIN_SECONDS,
        TelegramDeliveryState,
    )
    from models.telegram_delivery_job import TelegramDeliveryJobRecord

    if float(lease_seconds) < float(request_timeout_seconds) + float(
        MINIMUM_LEASE_MARGIN_SECONDS
    ):
        raise DriverRefusal("lease_must_cover_request_timeout_plus_margin")

    now = await telegram_delivery_database_now(session)
    record = (
        await session.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None:
        raise DriverRefusal(f"telegram delivery job {job_id} missing")

    state = str(getattr(record.state, "value", record.state) or "")
    claimable = {item.value for item in CLAIMABLE_DELIVERY_STATES}
    if state not in claimable:
        raise DriverRefusal(f"job {job_id} not claimable (state={state})")
    if record.next_retry_at is not None and record.next_retry_at > now:
        raise DriverRefusal(
            f"job {job_id} next_retry_at still in the future ({record.next_retry_at})"
        )
    if record.bot_cooldown_until is not None and record.bot_cooldown_until > now:
        raise DriverRefusal(
            f"job {job_id} bot_cooldown_until still active ({record.bot_cooldown_until})"
        )
    if record.eligible_at is not None and record.eligible_at > now:
        raise DriverRefusal(f"job {job_id} not yet eligible ({record.eligible_at})")

    record.state = TelegramDeliveryState.LEASED
    record.worker_id = str(worker_id)
    record.lease_token = int(record.lease_token or 0) + 1
    record.lease_until = now + timedelta(seconds=float(lease_seconds))
    record.dispatch_started_at = None
    record.attempt_count = int(record.attempt_count or 0) + 1
    record.updated_at = now
    await session.flush()
    return record


async def _await_job_retry_eligible(session, *, job_id: int) -> float:
    """Sleep until the durable next_retry_at / bot cooldown window has passed."""
    from core.services.telegram_delivery_queue_service import (
        telegram_delivery_database_now,
    )
    from models.telegram_delivery_job import TelegramDeliveryJobRecord

    waited = 0.0
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        now = await telegram_delivery_database_now(session)
        job = await session.get(TelegramDeliveryJobRecord, int(job_id))
        if job is None:
            raise DriverRefusal(f"telegram delivery job {job_id} missing while waiting")
        gates = [
            ts
            for ts in (job.next_retry_at, job.bot_cooldown_until)
            if ts is not None and ts > now
        ]
        if not gates:
            return waited
        sleep_for = min(1.0, max(0.05, (max(gates) - now).total_seconds() + 0.05))
        await asyncio.sleep(sleep_for)
        waited += sleep_for
        await session.rollback()
    raise DriverRefusal(f"job {job_id} did not become retry-eligible in time")


async def _noop_telegram_dispatch_guard(_db, _job, _now) -> None:
    return None


async def _noop_telegram_delivery_feedback(_db, _job, _decision, _now) -> None:
    return None


async def _force_queue_retry_then_sent(
    session,
    *,
    job_id: int,
    worker_id: str,
    synthetic_message_id: int,
    feedback=None,
    dispatch_guard=None,
) -> dict[str, object]:
    """Claim → synthetic 429 → pending_retry → reclaim → synthetic SENT."""
    from core.services.telegram_delivery_queue_service import (
        mark_telegram_delivery_dispatch_started,
        resolve_telegram_delivery_result,
        telegram_delivery_database_now,
    )
    from core.telegram_delivery_queue_contract import (
        TelegramDeliveryOutcome,
        TelegramDeliveryState,
    )
    from core.telegram_gateway import TelegramGatewayResult
    from models.telegram_delivery_job import TelegramDeliveryJobRecord

    retry_after_safety = float(
        getattr(settings, "telegram_delivery_queue_retry_after_safety_seconds", 0.1)
    )
    retry_base = float(
        getattr(settings, "telegram_delivery_queue_retry_base_seconds", 1.0)
    )
    retry_max = float(
        getattr(settings, "telegram_delivery_queue_retry_max_seconds", 300.0)
    )

    leased = await _lease_telegram_delivery_job_by_id(
        session, job_id=job_id, worker_id=worker_id
    )
    lease_token = int(leased.lease_token)
    marked = await mark_telegram_delivery_dispatch_started(
        session,
        current_server=current_server(),
        job_id=int(job_id),
        worker_id=worker_id,
        lease_token=lease_token,
        dispatch_guard=dispatch_guard,
    )
    if not marked:
        raise DriverRefusal(f"failed to mark dispatch started for job {job_id}")
    now = await telegram_delivery_database_now(session)
    retry_decision = await resolve_telegram_delivery_result(
        session,
        current_server=current_server(),
        job_id=int(job_id),
        worker_id=worker_id,
        lease_token=lease_token,
        result=TelegramGatewayResult(
            ok=False,
            method="sendMessage",
            status_code=429,
            response_json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 1",
                "parameters": {"retry_after": 1},
            },
            transport_phase="response_received",
        ),
        retry_after_safety_seconds=retry_after_safety,
        retry_base_seconds=retry_base,
        retry_max_seconds=retry_max,
        retry_jitter_ratio=0.0,
        feedback=feedback,
        now=now,
    )
    await session.commit()
    job = await session.get(TelegramDeliveryJobRecord, int(job_id))
    retry_state = str(getattr(job.state, "value", job.state) if job else "")
    retry_outcome = str(
        getattr(retry_decision.outcome, "value", retry_decision.outcome)
    )
    if (
        retry_outcome != TelegramDeliveryOutcome.RETRY_PENDING.value
        or retry_state != TelegramDeliveryState.PENDING_RETRY.value
    ):
        raise DriverRefusal(
            f"expected pending_retry after 429; outcome={retry_outcome} state={retry_state}"
        )

    wait_seconds = await _await_job_retry_eligible(session, job_id=int(job_id))
    reclaimed = await _lease_telegram_delivery_job_by_id(
        session, job_id=job_id, worker_id=f"{worker_id}:retry"
    )
    reclaim_token = int(reclaimed.lease_token)
    marked_retry = await mark_telegram_delivery_dispatch_started(
        session,
        current_server=current_server(),
        job_id=int(job_id),
        worker_id=f"{worker_id}:retry",
        lease_token=reclaim_token,
        dispatch_guard=dispatch_guard,
    )
    if not marked_retry:
        raise DriverRefusal(f"failed to mark retry dispatch for job {job_id}")
    now = await telegram_delivery_database_now(session)
    sent_decision = await resolve_telegram_delivery_result(
        session,
        current_server=current_server(),
        job_id=int(job_id),
        worker_id=f"{worker_id}:retry",
        lease_token=reclaim_token,
        result=TelegramGatewayResult(
            ok=True,
            method="sendMessage",
            status_code=200,
            response_json={
                "ok": True,
                "result": {"message_id": int(synthetic_message_id)},
            },
            transport_phase="response_received",
        ),
        retry_after_safety_seconds=retry_after_safety,
        retry_base_seconds=retry_base,
        retry_max_seconds=retry_max,
        retry_jitter_ratio=0.0,
        feedback=feedback,
        now=now,
    )
    await session.commit()
    job = await session.get(TelegramDeliveryJobRecord, int(job_id))
    sent_state = str(getattr(job.state, "value", job.state) if job else "")
    sent_outcome = str(getattr(sent_decision.outcome, "value", sent_decision.outcome))
    telegram_message_id = (
        int(job.telegram_message_id)
        if job is not None and job.telegram_message_id is not None
        else None
    )
    if (
        sent_outcome != TelegramDeliveryOutcome.SENT.value
        or sent_state != TelegramDeliveryState.SENT.value
        or telegram_message_id != int(synthetic_message_id)
    ):
        raise DriverRefusal(
            f"expected SENT after retry; outcome={sent_outcome} state={sent_state} "
            f"message_id={telegram_message_id}"
        )
    return {
        "job_id": int(job_id),
        "retry_outcome": retry_outcome,
        "retry_state": retry_state,
        "retry_wait_seconds": round(wait_seconds, 3),
        "sent_outcome": sent_outcome,
        "sent_state": sent_state,
        "telegram_message_id": telegram_message_id,
        "attempt_count": int(job.attempt_count or 0) if job is not None else 0,
    }


async def _scenario_tg_retry_seed(session, run_prefix: str) -> dict[str, object]:
    """Seed foreign-home owner + requester (same topology as foreign request)."""
    return await _scenario_req_foreign_to_foreign_seed(session, run_prefix)


async def _scenario_tg_retry_run(
    session,
    run_prefix: str,
    minutes: int,
    *,
    owner_user_id: int,
    requester_user_id: int,
) -> dict[str, object]:
    """Owner approval + requester private status survive synthetic 429 via queue."""
    from core.services.offer_overtime_request_service import (
        OvertimeRequestCreateCommand,
        create_overtime_request,
    )
    from core.services.telegram_delivery_queue_service import (
        TELEGRAM_PRIMARY_BOT_IDENTITY,
        enqueue_telegram_delivery_job,
    )
    from core.services.telegram_overtime_owner_approval_queue_feedback import (
        TelegramOvertimeOwnerApprovalQueueLifecycleFeedback,
    )
    from core.telegram_delivery_overtime_owner_approval_contract import (
        overtime_owner_approval_source_natural_id,
    )
    from core.telegram_delivery_queue_contract import (
        TelegramDeliveryAction,
        TelegramDestinationClass,
        TelegramFeederKind,
    )
    from models.offer_request import OfferRequest, OfferRequestSourceSurface, OfferRequestStatus
    from models.telegram_delivery_job import TelegramDeliveryJobRecord

    if current_server() != "foreign":
        raise DriverRefusal("OT-TG-RETRY run phase only runs on foreign")
    if minutes <= 0:
        raise DriverRefusal("OT-TG-RETRY requires a positive overtime preference")

    owner = await _await_user_by_id(session, owner_user_id)
    requester = await _await_user_by_id(session, requester_user_id)
    if owner is None or requester is None:
        raise DriverRefusal("Iran-seeded foreign-home users did not mirror to foreign")
    owner_id = int(owner.id)
    requester_id = int(requester.id)
    requester_telegram_id = int(requester.telegram_id or 0)
    if requester_telegram_id <= 0:
        raise DriverRefusal("requester telegram_id missing for private status job")

    await _save_bot_preference_with_user_sync_retry(session, owner, minutes)
    mirrored, mirror_seconds = await _await_preference_minutes(
        session, owner_id, minutes
    )
    if not mirrored:
        raise DriverRefusal("foreign preference mirror did not converge before offer create")
    owner = (
        await session.execute(select(User).where(User.id == owner_id))
    ).scalar_one()

    offer, normal_minutes = await _create_bot_offer(
        session,
        owner,
        notes=f"{run_prefix} tg retry",
    )
    offer_id = int(offer.id)
    offer_public_id = str(offer.offer_public_id)
    offer_home = str(offer.home_server)
    snapshot = int(offer.overtime_minutes_snapshot)
    await _backdate_offer_into_overtime(session, offer, normal_minutes=normal_minutes)
    offer = await _reload_offer(session, offer_id)
    receipt_at = datetime.utcnow()

    create_result = await create_overtime_request(
        session,
        OvertimeRequestCreateCommand(
            offer=offer,
            requester_user_id=requester_id,
            actor_user_id=requester_id,
            requested_quantity=min(2, DEFAULT_OFFER_QUANTITY),
            idempotency_key=f"{run_prefix}:ot-tg-retry",
            request_source_surface=OfferRequestSourceSurface.TELEGRAM_BOT,
            request_source_server="foreign",
            receipt_at=receipt_at,
            normal_lifetime_minutes=normal_minutes,
            request_home_server="foreign",
        ),
        now=receipt_at,
    )
    await session.commit()
    ledger = create_result.ledger
    await session.refresh(ledger)
    request_public_id = str(ledger.request_public_id)
    request_id = int(ledger.id)
    delivering_status = str(
        getattr(ledger.result_status, "value", ledger.result_status)
    )
    if (
        not create_result.promoted
        or delivering_status != OfferRequestStatus.OVERTIME_DELIVERING.value
    ):
        raise DriverRefusal(
            f"expected OVERTIME_DELIVERING after promote; status={delivering_status}"
        )

    owner_source = overtime_owner_approval_source_natural_id(request_public_id)
    owner_job = (
        await session.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.source_natural_id == owner_source)
            .order_by(TelegramDeliveryJobRecord.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if owner_job is None:
        raise DriverRefusal(
            f"owner approval job missing for source {owner_source!r}"
        )
    owner_job_id = int(owner_job.id)

    owner_feedback = TelegramOvertimeOwnerApprovalQueueLifecycleFeedback()
    owner_cycle = await _force_queue_retry_then_sent(
        session,
        job_id=owner_job_id,
        worker_id=f"otacc-tg-retry-owner:{run_prefix[-12:]}",
        synthetic_message_id=710_000 + (request_id % 100_000),
        feedback=owner_feedback.apply_delivery_result,
    )

    ledger = await session.get(OfferRequest, request_id)
    if ledger is None:
        raise DriverRefusal("ledger missing after owner approval SENT")
    await session.refresh(ledger)
    presented_status = str(
        getattr(ledger.result_status, "value", ledger.result_status)
    )
    presented_message_id = (
        int(ledger.telegram_message_id)
        if ledger.telegram_message_id is not None
        else None
    )
    if (
        presented_status != OfferRequestStatus.OVERTIME_PRESENTED.value
        or presented_message_id != int(owner_cycle["telegram_message_id"])
    ):
        raise DriverRefusal(
            "owner approval SENT did not mark request presented "
            f"(status={presented_status}, message_id={presented_message_id})"
        )

    # Staging foreign runs legacy telegram runtime (queue worker off), so requester
    # status does not auto-enqueue; exercise the same private-queue retry path with
    # a durable general_immediate job bound to this request.
    requester_source = f"ot-tg-retry-requester-status:{request_public_id}"
    requester_enqueue = await enqueue_telegram_delivery_job(
        session,
        current_server=current_server(),
        feeder=TelegramFeederKind.DIRECT,
        source_natural_id=requester_source,
        source_version=1,
        action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
        bot_identity=TELEGRAM_PRIMARY_BOT_IDENTITY,
        destination_key=f"private:{requester_telegram_id}",
        destination_class=TelegramDestinationClass.PRIVATE,
        method="sendMessage",
        payload={
            "chat_id": requester_telegram_id,
            "text": f"{run_prefix} requester overtime status retry",
        },
        template_version="ot-tg-retry-requester-v1",
        run_id=run_prefix,
    )
    await session.commit()
    requester_job_id = int(requester_enqueue.job.id)
    requester_cycle = await _force_queue_retry_then_sent(
        session,
        job_id=requester_job_id,
        worker_id=f"otacc-tg-retry-req:{run_prefix[-12:]}",
        synthetic_message_id=720_000 + (request_id % 100_000),
        dispatch_guard=_noop_telegram_dispatch_guard,
        feedback=_noop_telegram_delivery_feedback,
    )

    passed = (
        offer_home == "foreign"
        and snapshot == minutes
        and create_result.promoted is True
        and delivering_status == OfferRequestStatus.OVERTIME_DELIVERING.value
        and presented_status == OfferRequestStatus.OVERTIME_PRESENTED.value
        and owner_cycle["retry_state"] == "pending_retry"
        and owner_cycle["sent_state"] == "sent"
        and requester_cycle["retry_state"] == "pending_retry"
        and requester_cycle["sent_state"] == "sent"
        and int(owner_cycle["attempt_count"]) >= 2
        and int(requester_cycle["attempt_count"]) >= 2
    )
    return {
        "phase": "run",
        "owner_user_id": owner_id,
        "requester_user_id": requester_id,
        "preference_mirror_seconds": mirror_seconds,
        "offer_id": offer_id,
        "offer_public_id": offer_public_id,
        "offer_home_server": offer_home,
        "overtime_minutes_snapshot": snapshot,
        "request_id": request_id,
        "request_public_id": request_public_id,
        "delivering_status": delivering_status,
        "presented_status": presented_status,
        "owner_approval_source_natural_id": owner_source,
        "owner_retry": owner_cycle,
        "requester_status_source_natural_id": requester_source,
        "requester_retry": requester_cycle,
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

    # App startup normally opens Redis; scripts need the same client for M18 markers.
    from core.redis import close_redis, init_redis

    await init_redis()
    try:
        return await _main_async_with_session(args, run_prefix, started)
    finally:
        await close_redis()


async def _main_async_with_session(
    args: argparse.Namespace,
    run_prefix: str,
    started: str,
) -> dict[str, object]:
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
        elif args.scenario == "OT-CANCEL-REQUESTER":
            outcome = await _scenario_cancel_requester(session, run_prefix, args.minutes)
        elif args.scenario == "OT-QUEUE-ORDER":
            outcome = await _scenario_queue_order(session, run_prefix, args.minutes)
        elif args.scenario == "OT-REQ-FOREIGN-TO-FOREIGN":
            phase = (args.phase or "run").strip().lower()
            if phase == "seed":
                outcome = await _scenario_req_foreign_to_foreign_seed(session, run_prefix)
            elif phase == "run":
                if args.owner_user_id is None or args.requester_user_id is None:
                    raise DriverRefusal(
                        "OT-REQ-FOREIGN-TO-FOREIGN run phase requires "
                        "--owner-user-id and --requester-user-id from Iran seed"
                    )
                outcome = await _scenario_req_foreign_to_foreign_run(
                    session,
                    run_prefix,
                    args.minutes,
                    owner_user_id=int(args.owner_user_id),
                    requester_user_id=int(args.requester_user_id),
                )
            else:
                raise DriverRefusal(
                    f"unsupported OT-REQ-FOREIGN-TO-FOREIGN phase={phase!r}"
                )
        elif args.scenario == "OT-FINAL-TAIL":
            outcome = await _scenario_final_tail(session, run_prefix, args.minutes)
        elif args.scenario == "OT-REQ-CROSS-FORWARD":
            phase = (args.phase or "run").strip().lower()
            if phase == "seed":
                outcome = await _scenario_req_cross_forward_seed(
                    session, run_prefix, args.minutes
                )
            elif phase == "rebackdate":
                outcome = await _scenario_req_cross_forward_rebackdate(
                    session,
                    offer_public_id=str(args.offer_public_id or ""),
                )
            elif phase == "run":
                if (
                    args.owner_user_id is None
                    or args.requester_user_id is None
                    or not (args.offer_public_id or "").strip()
                ):
                    raise DriverRefusal(
                        "OT-REQ-CROSS-FORWARD run phase requires "
                        "--owner-user-id, --requester-user-id, and --offer-public-id"
                    )
                outcome = await _scenario_req_cross_forward_run(
                    session,
                    run_prefix,
                    args.minutes,
                    owner_user_id=int(args.owner_user_id),
                    requester_user_id=int(args.requester_user_id),
                    offer_public_id=str(args.offer_public_id),
                )
            else:
                raise DriverRefusal(
                    f"unsupported OT-REQ-CROSS-FORWARD phase={phase!r}"
                )
        elif args.scenario == "OT-CHANNEL-MARKER":
            phase = (args.phase or "run").strip().lower()
            if phase == "seed":
                outcome = await _scenario_channel_marker_seed(session, run_prefix)
            elif phase == "run":
                if args.owner_user_id is None:
                    raise DriverRefusal(
                        "OT-CHANNEL-MARKER run phase requires --owner-user-id "
                        "from the Iran seed phase"
                    )
                outcome = await _scenario_channel_marker_run(
                    session,
                    run_prefix,
                    args.minutes,
                    owner_user_id=int(args.owner_user_id),
                )
            else:
                raise DriverRefusal(f"unsupported OT-CHANNEL-MARKER phase={phase!r}")
        elif args.scenario == "OT-SYNC-RECOVERY":
            phase = (args.phase or "run").strip().lower()
            if phase == "seed":
                outcome = await _scenario_sync_recovery_seed(
                    session, run_prefix, args.minutes
                )
            elif phase == "assert_mirror":
                outcome = await _scenario_sync_recovery_assert_mirror(
                    session,
                    request_a_public_id=str(args.request_a_public_id or ""),
                )
            elif phase == "partition_mutate":
                outcome = await _scenario_sync_recovery_partition_mutate(
                    session,
                    run_prefix,
                    args.minutes,
                    request_a_public_id=str(args.request_a_public_id or ""),
                )
            elif phase == "assert_skew":
                outcome = await _scenario_sync_recovery_assert_skew(
                    session,
                    request_a_public_id=str(args.request_a_public_id or ""),
                    request_b_public_id=str(args.request_b_public_id or ""),
                )
            elif phase == "assert_converge":
                if args.owner_user_id is None:
                    raise DriverRefusal(
                        "assert_converge requires --owner-user-id"
                    )
                outcome = await _scenario_sync_recovery_assert_converge(
                    session,
                    offer_public_id=str(args.offer_public_id or ""),
                    request_a_public_id=str(args.request_a_public_id or ""),
                    request_b_public_id=str(args.request_b_public_id or ""),
                    owner_user_id=int(args.owner_user_id),
                )
            else:
                raise DriverRefusal(f"unsupported OT-SYNC-RECOVERY phase={phase!r}")
        elif args.scenario == "OT-TG-RETRY":
            phase = (args.phase or "run").strip().lower()
            if phase == "seed":
                outcome = await _scenario_tg_retry_seed(session, run_prefix)
            elif phase == "run":
                if args.owner_user_id is None or args.requester_user_id is None:
                    raise DriverRefusal(
                        "OT-TG-RETRY run phase requires "
                        "--owner-user-id and --requester-user-id from Iran seed"
                    )
                outcome = await _scenario_tg_retry_run(
                    session,
                    run_prefix,
                    args.minutes,
                    owner_user_id=int(args.owner_user_id),
                    requester_user_id=int(args.requester_user_id),
                )
            else:
                raise DriverRefusal(f"unsupported OT-TG-RETRY phase={phase!r}")
        else:
            raise DriverRefusal(f"no driver implemented for {args.scenario}")

        cleanup = {"users_retired": 0}
        # Foreign-home owners are Iran-authoritative under registration sync v2;
        # never auto-retire them from the foreign peer after a bot-origin run.
        allow_cleanup = args.cleanup_after and outcome.get("passed")
        if args.scenario in {
            "OT-OFFER-BOT-ORIGIN",
            "OT-REQ-FOREIGN-TO-FOREIGN",
            "OT-REQ-CROSS-FORWARD",
            "OT-CHANNEL-MARKER",
            "OT-SYNC-RECOVERY",
            "OT-TG-RETRY",
        }:
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
        choices=(
            "seed",
            "run",
            "rebackdate",
            "assert_mirror",
            "partition_mutate",
            "assert_skew",
            "assert_converge",
        ),
        default="run",
        help="two-peer phases for foreign/cross/sync scenarios",
    )
    parser.add_argument(
        "--owner-user-id",
        type=int,
        default=None,
        help="existing user id for foreign-peer scenarios that cannot INSERT users",
    )
    parser.add_argument(
        "--requester-user-id",
        type=int,
        default=None,
        help="existing requester user id for foreign-peer request scenarios",
    )
    parser.add_argument(
        "--offer-public-id",
        default=None,
        help="mirrored offer public id for foreign-edge cross-forward scenarios",
    )
    parser.add_argument(
        "--request-a-public-id",
        default=None,
        help="first overtime request public id for sync-recovery phases",
    )
    parser.add_argument(
        "--request-b-public-id",
        default=None,
        help="second overtime request public id for sync-recovery phases",
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
