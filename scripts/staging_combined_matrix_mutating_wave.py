#!/usr/bin/env python3
"""Mutating combined-matrix wave fragment, run inside a staging app container."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import current_server
from models.user import User, UserRole


RUN_PREFIX_MARKER = "CMB_"
DEFAULT_COMMODITY_ID = 1
DEFAULT_OFFER_QUANTITY = 5
DEFAULT_OFFER_PRICE = 100_000


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


def _telegram_id(prefix: str, suffix: str) -> int:
    digest = hashlib.sha256(f"{prefix}:tg:{suffix}".encode()).hexdigest()
    return 9_100_000_000 + (int(digest[:12], 16) % 800_000_000)


async def _seed_user(
    session,
    prefix: str,
    suffix: str,
    *,
    overtime: int,
    with_telegram: bool,
    has_bot_access: bool,
) -> User:
    account_name = f"{prefix}_{suffix}"
    existing = (
        await session.execute(
            select(User).where(User.account_name == account_name, User.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if existing is not None:
        if overtime and int(getattr(existing, "offer_overtime_minutes", 0) or 0) != overtime:
            existing.offer_overtime_minutes = overtime
            await session.commit()
        return existing
    user = User(
        account_name=account_name,
        mobile_number=_mobile(prefix, suffix),
        full_name=account_name,
        address="",
        role=UserRole.STANDARD,
        has_bot_access=has_bot_access,
        must_change_password=False,
        home_server=current_server(),
        offer_overtime_minutes=int(overtime),
        telegram_id=_telegram_id(prefix, suffix) if with_telegram else None,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _shape_lots(shape: str) -> tuple[bool, int, list[int] | None]:
    if shape == "retail_two_lot":
        return False, 10, [5, 5]
    if shape == "retail_three_lot":
        return False, 15, [5, 5, 5]
    return True, DEFAULT_OFFER_QUANTITY, None


async def _create_offer(
    session,
    owner: User,
    *,
    notes: str,
    surface: str,
    shape: str,
    offer_type: str = "sell",
    quantity_override: int | None = None,
    price_override: int | None = None,
    max_active_override: int | None = None,
):
    from core.offer_source import OfferSourceSurface
    from core.services.offer_creation_service import (
        OfferCreationCommand,
        OfferCreationQuotaPolicy,
        create_authoritative_offer_with_outcome,
    )
    from core.trading_settings import get_trading_settings_async
    from models.offer import OfferType

    source = (
        OfferSourceSurface.WEBAPP
        if surface == "webapp"
        else OfferSourceSurface.TELEGRAM_BOT
    )
    is_wholesale, quantity, lot_sizes = _shape_lots(shape)
    if quantity_override is not None:
        quantity = int(quantity_override)
    price = DEFAULT_OFFER_PRICE if price_override is None else int(price_override)
    offer_type_enum = OfferType.BUY if str(offer_type).lower() == "buy" else OfferType.SELL
    # Must use async settings loader: sync get_trading_settings() bridges through
    # another loop/thread and trips greenlet_spawn under AsyncSession.
    ts = await get_trading_settings_async()
    # Keep commit in the caller's session lifecycle to avoid nested-commit
    # / lazy-refresh greenlet errors during the realtime wave.
    outcome = await create_authoritative_offer_with_outcome(
        session,
        OfferCreationCommand(
            source_surface=source,
            owner_user_id=int(owner.id),
            actor_user_id=int(owner.id),
            offer_type=offer_type_enum,
            commodity_id=DEFAULT_COMMODITY_ID,
            quantity=quantity,
            price=price,
            notes=notes,
            is_wholesale=is_wholesale,
            lot_sizes=lot_sizes,
            original_lot_sizes=lot_sizes,
        ),
        validate_market=False,
        enforce_market_admission=False,
        quota_policy=OfferCreationQuotaPolicy(
            max_active_offers=(
                int(max_active_override)
                if max_active_override is not None
                else int(getattr(ts, "max_active_offers", 4) or 4)
            ),
        ),
        commit=False,
        refresh=False,
    )
    await session.commit()
    await session.refresh(outcome.offer)
    return outcome.offer


async def _place_request(
    *,
    requester: User,
    offer,
    request_surface: str,
    seq: int,
    prefix: str,
    telegram_harness=None,
) -> dict[str, object]:
    """Place a live request through the requested WebApp or Telegram path."""

    from scripts.trading_core_probe_worker import (
        MixedLoadAttemptSpec,
        execute_bot_trade_with_dispatcher,
        execute_webapp_trade_for_user,
    )

    quantity = 1
    lots = getattr(offer, "lot_sizes", None) or getattr(offer, "original_lot_sizes", None)
    if lots:
        try:
            quantity = int(list(lots)[0])
        except Exception:  # noqa: BLE001
            quantity = 1
    else:
        remaining = int(getattr(offer, "remaining_quantity", None) or getattr(offer, "quantity", 1) or 1)
        quantity = max(1, min(remaining, DEFAULT_OFFER_QUANTITY))
    if request_surface == "telegram":
        if telegram_harness is None:
            raise RuntimeError("telegram request surface requires dispatcher harness")
        telegram_id = getattr(requester, "telegram_id", None)
        if telegram_id is None:
            raise RuntimeError("telegram requester has no telegram_id")
        status = await execute_bot_trade_with_dispatcher(
            harness=telegram_harness,
            spec=MixedLoadAttemptSpec(
                index=int(seq),
                surface="telegram",
                user_id=int(requester.id),
                telegram_id=int(telegram_id),
            ),
            offer=offer,
            amount=quantity,
            prefix=f"{prefix}:req:",
        )
    else:
        status = await execute_webapp_trade_for_user(
            user_id=int(requester.id),
            offer_id=int(offer.id),
            offer_public_id=getattr(offer, "offer_public_id", None),
            quantity=quantity,
            idempotency_key=f"{prefix}:req:{seq}:{request_surface}"[:64],
        )
    return {
        "status": status,
        "request_surface": request_surface,
        "execution_surface": request_surface,
        "quantity": quantity,
        "ok": status == "success",
    }


async def _expire_offer(session, offer, owner: User, *, surface: str) -> None:
    from core.services.offer_expiry_service import (
        OfferExpiryCommand,
        OfferExpiryReason,
        OfferExpirySourceSurface,
        expire_offer_authoritatively,
    )

    source_surface = (
        OfferExpirySourceSurface.WEBAPP
        if surface == "webapp"
        else OfferExpirySourceSurface.TELEGRAM_BOT
    )
    await expire_offer_authoritatively(
        session,
        offer,
        OfferExpiryCommand(
            reason=OfferExpiryReason.MANUAL,
            source_surface=source_surface,
            source_server=current_server(),
            expired_by_user_id=int(owner.id),
            expired_by_actor_user_id=int(owner.id),
            require_active=True,
        ),
        commit=False,
        require_authority=False,
    )
    await session.commit()


async def _offer_is_published(session, offer_public_id: str) -> bool:
    """True once the offer's channel post exists.

    On the foreign server the row carries ``telegram_message_id`` directly;
    on Iran that column never syncs (local execution evidence), but the
    ``status`` column does, so ``sent``/``visible`` is the shared signal.
    """
    from sqlalchemy import text

    result = await session.execute(
        text(
            "SELECT 1 FROM offer_publication_states "
            "WHERE offer_public_id = :pid "
            "AND (telegram_message_id IS NOT NULL OR status IN ('sent', 'visible')) "
            "LIMIT 1"
        ),
        {"pid": str(offer_public_id)},
    )
    return result.first() is not None


def _deferred_action_for_seq(seq: int, *, trade_percent: int, manual_expire_percent: int) -> str | None:
    """Deterministic action mix so reruns behave identically."""
    bucket = seq % 100
    if bucket < trade_percent:
        return "trade"
    if bucket < trade_percent + manual_expire_percent:
        return "manual_expire"
    return None  # left to natural time-limit expiry


async def _cleanup(session, prefix: str) -> int:
    from core.services.user_deletion_service import delete_user_account

    owners = (
        await session.execute(
            select(User).where(
                User.account_name.like(f"{prefix}%"),
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
    return retired


async def _run(args: argparse.Namespace) -> dict[str, object]:
    _guard()
    prefix = args.run_prefix.strip()
    if not prefix.startswith(RUN_PREFIX_MARKER):
        raise DriverRefusal("run prefix must start with CMB_")

    async with AsyncExitStack() as stack:
        session = await stack.enter_async_context(AsyncSessionLocal())
        if args.cleanup_only:
            removed = await _cleanup(session, prefix)
            return {"ok": True, "mode": "cleanup", "removed_users": removed, "at_utc": _utc()}

        events_bytes = Path(args.events_file).read_bytes()
        events_file_sha256 = hashlib.sha256(events_bytes).hexdigest()
        expected_events_sha256 = (args.events_sha256 or "").strip().lower()
        if expected_events_sha256 and events_file_sha256 != expected_events_sha256:
            raise DriverRefusal(
                "events file checksum mismatch "
                f"expected={expected_events_sha256} actual={events_file_sha256}"
            )
        raw_events = json.loads(events_bytes.decode("utf-8"))
        if isinstance(raw_events, dict):
            events = list(raw_events.get("events") or [])
        else:
            events = list(raw_events)
        surface_filter = (args.surface_filter or "").strip().lower()
        if surface_filter in {"webapp", "bot"}:
            events = [item for item in events if str(item.get("surface") or "").lower() == surface_filter]
        input_seq_ids = [int(item["seq"]) for item in events]
        if len(input_seq_ids) != len(set(input_seq_ids)):
            raise DriverRefusal("events file contains duplicate seq values")
        expected_valid_count = sum(1 for item in events if item.get("kind") != "invalid")
        expected_invalid_count = len(events) - expected_valid_count
        telegram_harness = None
        if any(
            item.get("kind") != "invalid"
            and str(item.get("request_surface") or "") == "telegram"
            for item in events
        ):
            from scripts.trading_core_probe_worker import AiogramDispatcherHarness

            telegram_harness = AiogramDispatcherHarness()
            stack.push_async_callback(telegram_harness.close)
        started = time.perf_counter()
        created: list[dict[str, object]] = []
        errors: list[dict[str, object]] = []
        estimate_shadows: list[dict[str, object]] = []
        requests_placed: list[dict[str, object]] = []
        attempted_seq_ids: list[int] = []
        invalid_rejected_seq_ids: list[int] = []

        max_active_offers = max(1, int(args.max_active_offers))
        overtime_counts = {
            surface: sum(
                1
                for item in events
                if item.get("kind") != "invalid"
                and item.get("overtime_creator")
                and str(item.get("surface") or "") == surface
            )
            for surface in ("webapp", "bot")
        }
        owners: dict[str, list[User]] = {"web_ot": [], "bot_ot": []}
        for surface, key, overtime, with_telegram, has_bot_access in (
            ("webapp", "web_ot", 3, False, False),
            ("bot", "bot_ot", 2, True, True),
        ):
            pool_size = max(
                1,
                (overtime_counts[surface] + max_active_offers - 1)
                // max_active_offers,
            )
            for idx in range(pool_size):
                owners[key].append(
                    await _seed_user(
                        session,
                        prefix,
                        f"wave_{key}_{idx:02d}",
                        overtime=overtime,
                        with_telegram=with_telegram,
                        has_bot_access=has_bot_access,
                    )
                )
        # Pool of plain users keeps create churn under max_active_offers.
        plain_pool = []
        for idx in range(max(4, int(args.owner_pool_size))):
            plain_pool.append(
                await _seed_user(
                    session,
                    prefix,
                    f"wave_plain_{idx:02d}",
                    overtime=0,
                    with_telegram=True,
                    has_bot_access=True,
                )
            )
        requester_pool = []
        for idx in range(max(8, int(args.owner_pool_size))):
            requester_pool.append(
                await _seed_user(
                    session,
                    prefix,
                    f"wave_req_{idx:02d}",
                    overtime=0,
                    with_telegram=True,
                    has_bot_access=True,
                )
            )
        plain_cursor = 0
        invalid_cursor = 0
        req_cursor = 0
        overtime_cursors = {"web_ot": 0, "bot_ot": 0}

        # Deferred-action mode: offers are traded/expired only AFTER their
        # channel post exists, mirroring real users who react to what they see.
        # Owner pools are sized by the real max-active quota; the wave must not
        # widen the product policy merely to manufacture a larger backlog.
        defer_actions = bool(getattr(args, "defer_actions", False))
        pending_actions: list[dict[str, object]] = []

        async def _process_due_actions() -> None:
            nonlocal req_cursor
            now = time.perf_counter()
            for pending in list(pending_actions):
                if float(pending["due_at"]) > now:
                    continue
                offer = pending["offer"]
                entry = pending["entry"]
                try:
                    await session.refresh(offer)
                except Exception:  # noqa: BLE001
                    await session.rollback()
                    pending_actions.remove(pending)
                    entry["status"] = "action_offer_refresh_failed"
                    continue
                status_value = str(
                    getattr(getattr(offer, "status", None), "value", getattr(offer, "status", "")) or ""
                ).lower()
                if status_value not in {"active", "open", "published", ""}:
                    pending_actions.remove(pending)
                    entry["status"] = f"terminal_before_action_{status_value or 'unknown'}"
                    continue
                try:
                    published = await _offer_is_published(
                        session, str(getattr(offer, "offer_public_id", "") or "")
                    )
                except Exception:  # noqa: BLE001
                    await session.rollback()
                    published = False
                if not published:
                    if now >= float(pending["give_up_at"]):
                        pending_actions.remove(pending)
                        entry["status"] = "publish_wait_timeout_left_to_expiry"
                    else:
                        pending["due_at"] = now + 15.0
                    continue
                pending_actions.remove(pending)
                entry["publication_observed_at_utc"] = _utc()
                entry["publication_observed_elapsed"] = round(
                    time.perf_counter() - wall_started, 6
                )
                try:
                    if pending["action"] == "trade":
                        owner = pending["owner"]
                        await session.refresh(owner)
                        requester = requester_pool[req_cursor % len(requester_pool)]
                        req_cursor += 1
                        await session.refresh(requester)
                        if int(requester.id) == int(owner.id):
                            requester = requester_pool[req_cursor % len(requester_pool)]
                            req_cursor += 1
                            await session.refresh(requester)
                        request_result = await _place_request(
                            requester=requester,
                            offer=offer,
                            request_surface=str(pending["request_surface"]),
                            seq=int(pending["seq"]),
                            prefix=prefix,
                            telegram_harness=telegram_harness,
                        )
                        requests_placed.append({"seq": pending["seq"], **request_result})
                        entry["request_status"] = request_result.get("status")
                        entry["action_completed_at_utc"] = _utc()
                        entry["action_completed_elapsed"] = round(
                            time.perf_counter() - wall_started, 6
                        )
                        if request_result.get("status") == "success":
                            entry["status"] = "published_then_traded"
                        else:
                            entry["status"] = "published_then_trade_failed"
                            errors.append(
                                {
                                    "seq": pending["seq"],
                                    "status": "failed",
                                    "error": (
                                        "deferred trade did not succeed after publication: "
                                        f"{request_result.get('status')}"
                                    ),
                                }
                            )
                    else:
                        await session.refresh(pending["owner"])
                        await _expire_offer(
                            session, offer, pending["owner"], surface=str(pending["surface"])
                        )
                        entry["action_completed_at_utc"] = _utc()
                        entry["action_completed_elapsed"] = round(
                            time.perf_counter() - wall_started, 6
                        )
                        entry["status"] = "published_then_manual_expired"
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    errors.append(
                        {"seq": pending["seq"], "status": "failed", "error": str(exc)[:240]}
                    )
                    entry["status"] = "action_failed"

        wall_started = time.perf_counter()

        for item in events:
            seq = int(item["seq"])
            attempted_seq_ids.append(seq)
            if defer_actions and pending_actions:
                await _process_due_actions()
            if args.realtime:
                target = float(item.get("t_seconds") or 0.0) / max(float(args.speed), 0.01)
                delay = target - (time.perf_counter() - wall_started)
                if delay > 0:
                    await asyncio.sleep(min(delay, 5.0) if float(args.speed) >= 20 else delay)
            surface = str(item.get("surface") or "webapp")
            if item.get("kind") == "invalid":
                # Invalid probes must not perturb valid-owner round robin.
                # Otherwise an uneven invalid distribution can place more than
                # max_active_offers valid offers on one owner even though the
                # auto-sized pool is large enough for the valid wave.
                owner = plain_pool[invalid_cursor % len(plain_pool)]
                invalid_cursor += 1
                try:
                    await session.refresh(owner)
                    await _create_offer(
                        session,
                        owner,
                        notes=f"{prefix} invalid seq={seq}",
                        surface=surface,
                        shape="wholesale",
                        quantity_override=0,
                        price_override=-1,
                    )
                    errors.append(
                        {
                            "seq": seq,
                            "status": "invalid_attempt_unexpected_success",
                            "surface": surface,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    invalid_rejected_seq_ids.append(seq)
                    errors.append(
                        {
                            "seq": seq,
                            "status": "invalid_attempt_rejected",
                            "surface": surface,
                            "error": str(exc)[:160],
                        }
                    )
                continue
            if item.get("overtime_creator"):
                owner_key = "web_ot" if surface == "webapp" else "bot_ot"
                owner_pool = owners[owner_key]
                owner = owner_pool[overtime_cursors[owner_key] % len(owner_pool)]
                overtime_cursors[owner_key] += 1
            else:
                owner = plain_pool[plain_cursor % len(plain_pool)]
                plain_cursor += 1
            shape = str(item.get("shape") or "wholesale")
            offer_type = str(item.get("offer_type") or "sell")
            request_surface = str(item.get("request_surface") or ("webapp" if surface == "webapp" else "telegram"))
            try:
                await session.refresh(owner)
                if args.snapshot_path and item.get("estimate_probe") and surface == "webapp":
                    from core.market_intelligence.coin_inference_shadow import (
                        observe_coin_inference_shadow,
                    )

                    observation = await observe_coin_inference_shadow(
                        session,
                        snapshot_path=Path(args.snapshot_path),
                        submitted_project_price=DEFAULT_OFFER_PRICE,
                        settlement_term="CASH",
                        source_surface="WEBAPP",
                        force_confirmation=True,
                    )
                    await session.commit()
                    estimate_shadows.append(
                        {
                            "seq": seq,
                            "decision_key": observation.decision_key,
                            "status": getattr(observation.decision, "status", None),
                        }
                    )
                offer = await _create_offer(
                    session,
                    owner,
                    notes=f"{prefix} wave seq={seq} estimate={int(bool(item.get('estimate_probe')))}",
                    surface=surface,
                    shape=shape,
                    offer_type=offer_type,
                    max_active_override=max_active_offers,
                )
                if defer_actions:
                    action = _deferred_action_for_seq(
                        seq,
                        trade_percent=int(args.trade_percent),
                        manual_expire_percent=int(args.manual_expire_percent),
                    )
                    # Spread reactions 0.6x..1.4x around the base delay so the
                    # queue sees a stream of user actions, not one thundering herd.
                    action_delay = float(args.action_delay_seconds) * (0.6 + (seq % 9) / 10.0)
                    entry: dict[str, object] = {
                        "seq": seq,
                        "offer_public_id": getattr(offer, "offer_public_id", None),
                        "surface": surface,
                        "shape": shape,
                        "offer_type": offer_type,
                        "request_surface": request_surface,
                        "estimate_probe": bool(item.get("estimate_probe")),
                        "overtime_minutes": int(getattr(owner, "offer_overtime_minutes", 0) or 0),
                        "t_seconds": item.get("t_seconds"),
                        "wall_elapsed": round(time.perf_counter() - wall_started, 3),
                        "planned_action": action or "natural_expiry",
                        "status": "created_queued_for_publication",
                    }
                    created.append(entry)
                    if action:
                        now_mark = time.perf_counter()
                        pending_actions.append(
                            {
                                "seq": seq,
                                "offer": offer,
                                "owner": owner,
                                "surface": surface,
                                "request_surface": request_surface,
                                "action": action,
                                "due_at": now_mark + action_delay,
                                "give_up_at": now_mark
                                + action_delay
                                + float(args.publish_wait_timeout_seconds),
                                "entry": entry,
                            }
                        )
                    continue
                # Brief dwell so Telegram publication can enqueue before recycle.
                if surface == "bot" and args.publish_dwell_seconds > 0:
                    await asyncio.sleep(float(args.publish_dwell_seconds))
                requester = requester_pool[req_cursor % len(requester_pool)]
                req_cursor += 1
                # A rollback from an earlier (invalid) event expires every pooled
                # instance; touching requester.id would then lazy-load through the
                # sync path and raise MissingGreenlet. Refresh before reading.
                await session.refresh(requester)
                if int(requester.id) == int(owner.id):
                    requester = requester_pool[req_cursor % len(requester_pool)]
                    req_cursor += 1
                    await session.refresh(requester)
                request_result = await _place_request(
                    requester=requester,
                    offer=offer,
                    request_surface=request_surface,
                    seq=seq,
                    prefix=prefix,
                    telegram_harness=telegram_harness,
                )
                requests_placed.append({"seq": seq, **request_result})
                # Expire only if the offer is still active after the request.
                try:
                    await session.refresh(offer)
                    status_value = str(getattr(getattr(offer, "status", None), "value", getattr(offer, "status", "")) or "")
                    if status_value.lower() in {"active", "open", "published", ""}:
                        await _expire_offer(session, offer, owner, surface=surface)
                        terminal = "created_requested_expired"
                    else:
                        terminal = f"created_requested_{status_value or 'terminal'}"
                except Exception:  # noqa: BLE001
                    await session.rollback()
                    terminal = "created_requested_expire_skipped"
                created.append(
                    {
                        "seq": seq,
                        "offer_public_id": getattr(offer, "offer_public_id", None),
                        "surface": surface,
                        "shape": shape,
                        "offer_type": offer_type,
                        "request_surface": request_surface,
                        "request_status": request_result.get("status"),
                        "estimate_probe": bool(item.get("estimate_probe")),
                        "overtime_minutes": int(getattr(owner, "offer_overtime_minutes", 0) or 0),
                        "t_seconds": item.get("t_seconds"),
                        "wall_elapsed": round(time.perf_counter() - wall_started, 3),
                        "status": terminal,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                import traceback as _tb

                full_trace = _tb.format_exc()
                try:
                    Path(f"/tmp/wave-fail-seq{seq}.trace").write_text(full_trace)
                except Exception:  # noqa: BLE001
                    pass
                app_frames = [
                    line
                    for line in full_trace.splitlines()
                    if "/app/" in line or "site-packages" not in line
                ]
                errors.append(
                    {
                        "seq": seq,
                        "status": "failed",
                        "error": str(exc)[:240],
                        # App-level frames tell us which module raised (greenlet
                        # errors carry no useful message on their own).
                        "trace_tail": "\n".join(app_frames)[-1200:],
                    }
                )

        # Keep reacting until every scheduled action ran (publication pace
        # limits how fast posts appear; user reactions follow the posts).
        action_drain_timeouts = 0
        if defer_actions and pending_actions:
            drain_deadline = time.perf_counter() + float(args.action_drain_timeout_seconds)
            while pending_actions and time.perf_counter() < drain_deadline:
                await _process_due_actions()
                if pending_actions:
                    await asyncio.sleep(3.0)
            for pending in pending_actions:
                pending["entry"]["status"] = "action_abandoned_drain_timeout"
            action_drain_timeouts = len(pending_actions)
            pending_actions.clear()

        removed = await _cleanup(session, prefix) if args.cleanup_after else 0

    failed = sum(1 for item in errors if item.get("status") == "failed")
    invalid_rejected = sum(1 for item in errors if item.get("status") == "invalid_attempt_rejected")
    invalid_unexpected = sum(1 for item in errors if item.get("status") == "invalid_attempt_unexpected_success")
    req_web = sum(1 for item in requests_placed if item.get("request_surface") == "webapp")
    req_tg = sum(1 for item in requests_placed if item.get("request_surface") == "telegram")
    executed_web = sum(
        1 for item in requests_placed if item.get("execution_surface") == "webapp"
    )
    executed_tg = sum(
        1 for item in requests_placed if item.get("execution_surface") == "telegram"
    )
    created_seq_ids = [int(item["seq"]) for item in created]
    offer_public_ids = [
        str(item["offer_public_id"])
        for item in created
        if item.get("offer_public_id")
    ]
    trades_planned = sum(1 for item in created if item.get("planned_action") == "trade")
    manual_expiry_planned = sum(
        1 for item in created if item.get("planned_action") == "manual_expire"
    )
    trades_after_publication = sum(
        1 for item in created if item.get("status") == "published_then_traded"
    )
    manual_expired_after_publication = sum(
        1 for item in created if item.get("status") == "published_then_manual_expired"
    )
    publish_wait_timeouts = sum(
        1
        for item in created
        if item.get("status") == "publish_wait_timeout_left_to_expiry"
    )
    action_order_violations = sum(
        1
        for item in created
        if item.get("action_completed_elapsed") is not None
        and (
            item.get("publication_observed_elapsed") is None
            or float(item["action_completed_elapsed"])
            < float(item["publication_observed_elapsed"])
        )
    )
    assertions = {
        "input_seq_unique": len(input_seq_ids) == len(set(input_seq_ids)),
        "all_input_events_attempted_once": sorted(attempted_seq_ids) == sorted(input_seq_ids)
        and len(attempted_seq_ids) == len(set(attempted_seq_ids)),
        "valid_created_exactly_once": len(created_seq_ids) == expected_valid_count
        and len(created_seq_ids) == len(set(created_seq_ids)),
        "offer_public_ids_unique": len(offer_public_ids) == len(created_seq_ids)
        and len(offer_public_ids) == len(set(offer_public_ids)),
        "invalid_rejected_exactly_once": invalid_rejected == expected_invalid_count
        and sorted(invalid_rejected_seq_ids)
        == sorted(item["seq"] for item in events if item.get("kind") == "invalid"),
        "no_invalid_unexpected_success": invalid_unexpected == 0,
        "no_event_failures": failed == 0,
        "no_publish_wait_timeouts": publish_wait_timeouts == 0,
        "no_action_drain_timeouts": action_drain_timeouts == 0,
        "all_planned_trades_after_publication": trades_after_publication == trades_planned,
        "all_manual_expiry_after_publication": (
            manual_expired_after_publication == manual_expiry_planned
        ),
        "action_order_valid": action_order_violations == 0,
    }
    return {
        "ok": bool(created) and all(assertions.values()),
        "mode": "mutating_wave",
        "run_prefix": prefix,
        "server_mode": getattr(settings, "server_mode", None),
        "events_file_sha256": events_file_sha256,
        "expected_events_file_sha256": expected_events_sha256 or None,
        "input_event_count": len(events),
        "expected_valid_count": expected_valid_count,
        "expected_invalid_count": expected_invalid_count,
        "input_seq_ids": input_seq_ids,
        "attempted_seq_ids": attempted_seq_ids,
        "created_seq_ids": created_seq_ids,
        "invalid_rejected_seq_ids": invalid_rejected_seq_ids,
        "offer_public_ids": offer_public_ids,
        "assertions": assertions,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "created_count": len(created),
        "invalid_rejected": invalid_rejected,
        "invalid_unexpected_success": invalid_unexpected,
        "failed_count": failed,
        "requests_placed": len(requests_placed),
        "request_surface_mix": {"webapp": req_web, "telegram": req_tg},
        "request_execution_mix": {
            "webapp": executed_web,
            "telegram": executed_tg,
        },
        "estimate_probes": sum(1 for item in created if item.get("estimate_probe")),
        "estimate_shadow_count": len(estimate_shadows),
        "overtime_creates": sum(1 for item in created if int(item.get("overtime_minutes") or 0) > 0),
        "removed_users": removed,
        "surface_filter": surface_filter or None,
        "realtime": bool(args.realtime),
        "speed": float(args.speed),
        "defer_actions": bool(getattr(args, "defer_actions", False)),
        "deferred_action_stats": {
            "trades_planned": trades_planned,
            "trades_after_publication": trades_after_publication,
            "manual_expiry_planned": manual_expiry_planned,
            "manual_expired_after_publication": manual_expired_after_publication,
            "left_to_natural_expiry": sum(
                1 for item in created if item.get("planned_action") == "natural_expiry"
            ),
            "publish_wait_timeouts": publish_wait_timeouts,
            "action_drain_timeouts": action_drain_timeouts,
            "action_order_violations": action_order_violations,
        },
        "owner_pool_size": len(plain_pool) if not args.cleanup_only else 0,
        "created_sample": created[:40],
        "request_sample": requests_placed[:40],
        "estimate_shadow_sample": estimate_shadows[:20],
        "error_sample": errors[:40],
        "at_utc": _utc(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--events-file", required=True)
    parser.add_argument(
        "--events-sha256",
        default="",
        help="expected SHA-256 of the exact events file bytes",
    )
    parser.add_argument("--surface-filter", choices=["webapp", "bot", ""], default="")
    parser.add_argument("--snapshot-path", default="")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--speed", type=float, default=1.0, help=">1 compresses realtime waits")
    parser.add_argument("--owner-pool-size", type=int, default=16)
    parser.add_argument("--max-active-offers", type=int, default=10)
    parser.add_argument("--publish-dwell-seconds", type=float, default=0.35)
    parser.add_argument(
        "--defer-actions",
        action="store_true",
        help=(
            "realistic mode: trade/expire offers only after their channel post "
            "is published, so the send queue fills up like a real peak"
        ),
    )
    parser.add_argument(
        "--action-delay-seconds",
        type=float,
        default=45.0,
        help="base delay between an offer's creation and the user reaction to it",
    )
    parser.add_argument("--trade-percent", type=int, default=40)
    parser.add_argument("--manual-expire-percent", type=int, default=20)
    parser.add_argument(
        "--publish-wait-timeout-seconds",
        type=float,
        default=1800.0,
        help="how long a deferred action waits for the channel post before giving up",
    )
    parser.add_argument(
        "--action-drain-timeout-seconds",
        type=float,
        default=2400.0,
        help="after the last create, keep processing deferred actions this long",
    )
    parser.add_argument("--cleanup-after", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true")
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
