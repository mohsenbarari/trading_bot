"""Reconciliation helpers for cross-surface offer publication state."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from core.config import settings
from core.services.offer_publication_state_service import (
    ACTIVE_PUBLICATION_STATUSES,
    TERMINAL_OFFER_STATUSES,
    apply_publication_state_update,
    build_offer_publication_state,
    normalize_publication_status,
)
from core.services.telegram_offer_publication_service import (
    apply_existing_telegram_publication_to_offer,
    get_or_create_telegram_publication_state,
    publish_offer_to_telegram_channel_once,
)
from core.utils import utc_now, utc_now_naive
from models.offer import Offer, OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)


SendOfferToChannel = Callable[[Any, Any], Awaitable[int | None]]

FOREIGN_TELEGRAM_REPAIR_ISSUES = {
    "active_offer_without_telegram_state",
    "failed_telegram_publication",
    "lagged_telegram_publication",
    "legacy_telegram_message_without_state",
    "offer_missing_legacy_message_id",
}
IRAN_WEBAPP_REPAIR_ISSUES = {
    "active_offer_without_webapp_state",
    "stale_terminal_webapp_state",
}
ACTIVE_PUBLICATION_REPAIR_ISSUES = {
    "active_offer_without_telegram_state",
    "failed_telegram_publication",
    "lagged_telegram_publication",
    "active_offer_without_webapp_state",
}


_REPAIR_ATTEMPT_METADATA_KEY = "reconciliation_attempt_count"
_REPAIR_ATTEMPT_VERSION_METADATA_KEY = "reconciliation_attempt_offer_version_id"
_REPAIR_LOCK_PREFIX = "offer-publication-reconciliation"


@dataclass(slots=True)
class PublicationReconciliationCandidate:
    issue: str
    offer: Any
    state: Any | None
    surface: OfferPublicationSurface


@dataclass(frozen=True, slots=True)
class PublicationReconciliationBacklog:
    total: int = 0
    due: int = 0
    oldest_age_seconds: float | None = None
    oldest_due_age_seconds: float | None = None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _result_all(result: Any) -> list[Any]:
    try:
        return list(result.all())
    except AttributeError:
        pass
    try:
        return list(result.scalars().all())
    except AttributeError:
        return []


def _row_items(row: Any) -> tuple[Any, Any | None]:
    if isinstance(row, tuple):
        return row[0], row[1] if len(row) > 1 else None
    try:
        return row[0], row[1]
    except Exception:
        return row, None


def _safe_count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _repair_attempt_count(state: Any | None) -> int:
    metadata = getattr(state, "state_metadata", None)
    if not isinstance(metadata, Mapping):
        return 0
    return _safe_count(metadata.get(_REPAIR_ATTEMPT_METADATA_KEY))


def _set_repair_attempt_count(state: Any, count: int, *, offer_version_id: int | None = None) -> None:
    metadata = getattr(state, "state_metadata", None)
    updated_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    if count > 0:
        updated_metadata[_REPAIR_ATTEMPT_METADATA_KEY] = int(count)
        if offer_version_id is not None:
            updated_metadata[_REPAIR_ATTEMPT_VERSION_METADATA_KEY] = int(offer_version_id)
    else:
        updated_metadata.pop(_REPAIR_ATTEMPT_METADATA_KEY, None)
        updated_metadata.pop(_REPAIR_ATTEMPT_VERSION_METADATA_KEY, None)
    state.state_metadata = updated_metadata or None


def _bounded_retry_setting(name: str, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _repair_retry_delay_seconds(
    attempt_count: int,
    *,
    retry_after_seconds: int | None = None,
) -> float:
    base = _bounded_retry_setting(
        "offer_publication_worker_retry_base_seconds",
        default=5.0,
        minimum=1.0,
        maximum=60.0,
    )
    maximum = _bounded_retry_setting(
        "offer_publication_worker_retry_max_seconds",
        default=300.0,
        minimum=base,
        maximum=3600.0,
    )
    exponent = max(0, min(int(attempt_count or 1) - 1, 10))
    delay = min(maximum, base * (2**exponent))
    if retry_after_seconds is not None:
        delay = max(delay, min(maximum, max(1.0, float(retry_after_seconds))))
    return delay


def _schedule_candidate_retry(
    candidate: PublicationReconciliationCandidate,
    result: Mapping[str, Any],
    *,
    now: datetime,
) -> float:
    state = candidate.state
    if state is None:
        return 0.0
    metadata = getattr(state, "state_metadata", None)
    previous_version = (
        _coerce_int(metadata.get(_REPAIR_ATTEMPT_VERSION_METADATA_KEY))
        if isinstance(metadata, Mapping)
        else None
    )
    offer_version = _coerce_int(getattr(candidate.offer, "version_id", None))
    attempt_count = (_repair_attempt_count(state) if previous_version == offer_version else 0) + 1
    delay = _repair_retry_delay_seconds(
        attempt_count,
        retry_after_seconds=_coerce_int(result.get("telegram_retry_after_seconds")),
    )
    _set_repair_attempt_count(state, attempt_count, offer_version_id=offer_version)
    if candidate.surface == OfferPublicationSurface.TELEGRAM_CHANNEL:
        apply_publication_state_update(
            state,
            offer_status=getattr(candidate.offer, "status", None),
            offer_version_id=offer_version,
            requested_status=OfferPublicationStatus.FAILED,
            now=now,
            error_code=str(result.get("reason") or "reconciliation_failed")[:96],
        )
    state.last_attempt_at = now
    state.next_retry_at = now + timedelta(seconds=delay)
    state.error_code = str(result.get("reason") or "reconciliation_failed")[:96]
    return delay


def _clear_candidate_retry(candidate: PublicationReconciliationCandidate) -> None:
    state = candidate.state
    if state is None:
        return
    _set_repair_attempt_count(state, 0)
    state.next_retry_at = None


def _candidate_due_condition(state: Any, *, now: datetime):
    return or_(
        state.id.is_(None),
        state.next_retry_at.is_(None),
        state.next_retry_at <= now,
    )


def _candidate_age_anchor(state: Any):
    return func.coalesce(
        state.next_retry_at,
        state.last_attempt_at,
        Offer.created_at,
        Offer.updated_at,
    )


def _foreign_candidate_condition(state: Any):
    return and_(
        Offer.status == OfferStatus.ACTIVE,
        or_(
            state.id.is_(None),
            state.status.in_([OfferPublicationStatus.FAILED, OfferPublicationStatus.LAGGED]),
            state.next_retry_at.isnot(None),
            and_(Offer.channel_message_id.isnot(None), state.telegram_message_id.is_(None)),
            and_(Offer.channel_message_id.is_(None), state.telegram_message_id.isnot(None)),
        ),
    )


def _iran_candidate_condition(state: Any):
    return or_(
        and_(
            Offer.status == OfferStatus.ACTIVE,
            or_(
                state.id.is_(None),
                state.status.notin_([OfferPublicationStatus.SENT, OfferPublicationStatus.VISIBLE]),
            ),
        ),
        and_(
            Offer.status.in_([OfferStatus.COMPLETED, OfferStatus.CANCELLED, OfferStatus.EXPIRED]),
            state.status.in_([OfferPublicationStatus(status) for status in ACTIVE_PUBLICATION_STATUSES]),
        ),
    )


def _age_seconds(now: datetime, value: Any) -> float | None:
    if not isinstance(value, datetime):
        return None
    normalized_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    normalized_value = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return max(0.0, (normalized_now - normalized_value).total_seconds())


async def publication_reconciliation_backlog(
    db: AsyncSession,
    *,
    server_mode: str,
    now: datetime | None = None,
) -> PublicationReconciliationBacklog:
    current_time = now or utc_now()
    state = aliased(OfferPublicationState)
    surface = (
        OfferPublicationSurface.TELEGRAM_CHANNEL
        if server_mode == "foreign"
        else OfferPublicationSurface.WEBAPP_MARKET
    )
    condition = _foreign_candidate_condition(state) if server_mode == "foreign" else _iran_candidate_condition(state)
    due = _candidate_due_condition(state, now=current_time)
    age_anchor = Offer.created_at
    stmt = (
        select(
            func.count(Offer.id),
            func.count(case((due, 1))),
            func.min(age_anchor),
            func.min(case((due, age_anchor), else_=None)),
        )
        .outerjoin(
            state,
            and_(
                state.offer_public_id == Offer.offer_public_id,
                state.surface == surface,
            ),
        )
        .where(condition)
    )
    result = await db.execute(stmt)
    row = result.one()
    total, due_count, oldest, oldest_due = row[0], row[1], row[2], row[3]
    return PublicationReconciliationBacklog(
        total=_safe_count(total),
        due=_safe_count(due_count),
        oldest_age_seconds=_age_seconds(current_time, oldest),
        oldest_due_age_seconds=_age_seconds(current_time, oldest_due),
    )


def _is_postgresql_session(db: Any) -> bool:
    get_bind = getattr(db, "get_bind", None)
    if get_bind is None:
        return False
    try:
        bind = get_bind()
        return str(getattr(getattr(bind, "dialect", None), "name", "")) == "postgresql"
    except Exception:
        return False


async def _try_acquire_candidate_repair_lock(
    db: AsyncSession,
    candidate: PublicationReconciliationCandidate,
) -> bool:
    if not _is_postgresql_session(db):
        return True
    lock_key = (
        f"{_REPAIR_LOCK_PREFIX}:{candidate.surface.value}:"
        f"{getattr(candidate.offer, 'offer_public_id', '')}"
    )
    result = await db.execute(
        sa_text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    return bool(result.scalar())


async def _count_scalar(db: AsyncSession, sql: str) -> int:
    result = await db.execute(sa_text(sql))
    try:
        return _safe_count(result.scalar())
    except AttributeError:
        try:
            row = result.one()
            return _safe_count(row[0])
        except Exception:
            return 0


async def publication_observability_summary(
    db: AsyncSession,
    *,
    server_mode: str | None = None,
    unsynced_by_table: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return sync-health-safe publication state and reconciliation counts."""
    state_counts_result = await db.execute(
        select(
            OfferPublicationState.surface,
            OfferPublicationState.status,
            func.count(OfferPublicationState.id),
        )
        .group_by(OfferPublicationState.surface, OfferPublicationState.status)
        .order_by(OfferPublicationState.surface, OfferPublicationState.status)
    )

    state_counts: dict[str, dict[str, int]] = {}
    for surface, status, count in _result_all(state_counts_result):
        surface_value = _enum_value(surface)
        status_value = _enum_value(status)
        state_counts.setdefault(surface_value, {})[status_value] = _safe_count(count)

    finding_counts = {
        "active_offer_without_telegram_state": await _count_scalar(
            db,
            """
            SELECT COUNT(*)
            FROM offers o
            LEFT JOIN offer_publication_states ps
              ON ps.offer_public_id = o.offer_public_id
             AND ps.surface = 'telegram_channel'
            WHERE o.status = 'ACTIVE'
              AND ps.id IS NULL
            """,
        ),
        "failed_telegram_publication": await _count_scalar(
            db,
            """
            SELECT COUNT(*)
            FROM offer_publication_states
            WHERE surface = 'telegram_channel'
              AND status = 'failed'
            """,
        ),
        "lagged_telegram_publication": await _count_scalar(
            db,
            """
            SELECT COUNT(*)
            FROM offer_publication_states
            WHERE surface = 'telegram_channel'
              AND status = 'lagged'
            """,
        ),
        "legacy_telegram_message_without_state": await _count_scalar(
            db,
            """
            SELECT COUNT(*)
            FROM offers o
            LEFT JOIN offer_publication_states ps
              ON ps.offer_public_id = o.offer_public_id
             AND ps.surface = 'telegram_channel'
            WHERE o.status = 'ACTIVE'
              AND o.channel_message_id IS NOT NULL
              AND (ps.id IS NULL OR ps.telegram_message_id IS NULL)
            """,
        ),
        "offer_missing_legacy_message_id": await _count_scalar(
            db,
            """
            SELECT COUNT(*)
            FROM offers o
            JOIN offer_publication_states ps
              ON ps.offer_public_id = o.offer_public_id
             AND ps.surface = 'telegram_channel'
            WHERE o.status = 'ACTIVE'
              AND ps.telegram_message_id IS NOT NULL
              AND o.channel_message_id IS NULL
            """,
        ),
        "active_offer_without_webapp_state": await _count_scalar(
            db,
            """
            SELECT COUNT(*)
            FROM offers o
            LEFT JOIN offer_publication_states ps
              ON ps.offer_public_id = o.offer_public_id
             AND ps.surface = 'webapp_market'
            WHERE o.status = 'ACTIVE'
              AND (ps.id IS NULL OR ps.status NOT IN ('sent', 'visible'))
            """,
        ),
        "stale_terminal_webapp_state": await _count_scalar(
            db,
            """
            SELECT COUNT(*)
            FROM offers o
            JOIN offer_publication_states ps
              ON ps.offer_public_id = o.offer_public_id
             AND ps.surface = 'webapp_market'
            WHERE o.status IN ('COMPLETED', 'CANCELLED', 'EXPIRED')
              AND ps.status IN ('pending', 'sent', 'visible', 'lagged')
            """,
        ),
        "unsynced_publication_state_backlog": _safe_count((unsynced_by_table or {}).get("offer_publication_states")),
        "unsynced_offer_backlog": _safe_count((unsynced_by_table or {}).get("offers")),
    }
    action_required = any(value > 0 for value in finding_counts.values())
    return {
        "status": "action_required" if action_required else "ok",
        "server_mode": server_mode or settings.server_mode,
        "state_counts": state_counts,
        "finding_counts": finding_counts,
        "repairable_issues": sorted(FOREIGN_TELEGRAM_REPAIR_ISSUES | IRAN_WEBAPP_REPAIR_ISSUES),
    }


async def load_foreign_telegram_reconciliation_candidates(
    db: AsyncSession,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> list[PublicationReconciliationCandidate]:
    current_time = now or utc_now()
    telegram_state = aliased(OfferPublicationState)
    stmt = (
        select(Offer, telegram_state)
        .outerjoin(
            telegram_state,
            and_(
                telegram_state.offer_public_id == Offer.offer_public_id,
                telegram_state.surface == OfferPublicationSurface.TELEGRAM_CHANNEL,
            ),
        )
        .options(selectinload(Offer.user), selectinload(Offer.commodity))
        .where(
            _foreign_candidate_condition(telegram_state),
            _candidate_due_condition(telegram_state, now=current_time),
        )
        .order_by(_candidate_age_anchor(telegram_state).asc().nullsfirst(), Offer.id.asc())
        .limit(max(int(limit or 1), 1))
    )
    result = await db.execute(stmt)
    candidates: list[PublicationReconciliationCandidate] = []
    for row in _result_all(result):
        offer, state = _row_items(row)
        issue = _foreign_telegram_issue_for(offer, state)
        if issue:
            candidates.append(
                PublicationReconciliationCandidate(
                    issue=issue,
                    offer=offer,
                    state=state,
                    surface=OfferPublicationSurface.TELEGRAM_CHANNEL,
                )
            )
    return candidates


async def load_iran_webapp_reconciliation_candidates(
    db: AsyncSession,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> list[PublicationReconciliationCandidate]:
    current_time = now or utc_now()
    webapp_state = aliased(OfferPublicationState)
    stmt = (
        select(Offer, webapp_state)
        .outerjoin(
            webapp_state,
            and_(
                webapp_state.offer_public_id == Offer.offer_public_id,
                webapp_state.surface == OfferPublicationSurface.WEBAPP_MARKET,
            ),
        )
        .where(
            _iran_candidate_condition(webapp_state),
            _candidate_due_condition(webapp_state, now=current_time),
        )
        .order_by(_candidate_age_anchor(webapp_state).asc().nullsfirst(), Offer.id.asc())
        .limit(max(int(limit or 1), 1))
    )
    result = await db.execute(stmt)
    candidates: list[PublicationReconciliationCandidate] = []
    for row in _result_all(result):
        offer, state = _row_items(row)
        issue = _iran_webapp_issue_for(offer, state)
        if issue:
            candidates.append(
                PublicationReconciliationCandidate(
                    issue=issue,
                    offer=offer,
                    state=state,
                    surface=OfferPublicationSurface.WEBAPP_MARKET,
                )
            )
    return candidates


def _foreign_telegram_issue_for(offer: Any, state: Any | None) -> str | None:
    offer_message_id = _coerce_int(getattr(offer, "channel_message_id", None))
    state_message_id = _coerce_int(getattr(state, "telegram_message_id", None))
    state_status = _enum_value(getattr(state, "status", None))

    if state_message_id and not offer_message_id:
        return "offer_missing_legacy_message_id"
    if offer_message_id and not state_message_id:
        return "legacy_telegram_message_without_state"
    if state_status == OfferPublicationStatus.FAILED.value:
        return "failed_telegram_publication"
    if state_status == OfferPublicationStatus.LAGGED.value:
        return "lagged_telegram_publication"
    if getattr(state, "next_retry_at", None) is not None:
        return "failed_telegram_publication"
    if state is None:
        return "active_offer_without_telegram_state"
    return None


def _iran_webapp_issue_for(offer: Any, state: Any | None) -> str | None:
    offer_status = _enum_value(getattr(offer, "status", None))
    state_status = _enum_value(getattr(state, "status", None))
    if offer_status == OfferStatus.ACTIVE.value and state_status not in {
        OfferPublicationStatus.SENT.value,
        OfferPublicationStatus.VISIBLE.value,
    }:
        return "active_offer_without_webapp_state"
    if offer_status in TERMINAL_OFFER_STATUSES and state_status in ACTIVE_PUBLICATION_STATUSES:
        return "stale_terminal_webapp_state"
    return None


async def reconcile_offer_publications(
    db: AsyncSession,
    *,
    server_mode: str | None = None,
    dry_run: bool = True,
    limit: int = 50,
    send_offer_to_channel: SendOfferToChannel | None = None,
    allow_active_publication: bool = True,
    telegram_send_spacing_seconds: float = 0.0,
    collect_observability: bool = False,
) -> dict[str, Any]:
    """Report or repair local publication drift for the current server role."""
    mode = (server_mode or settings.server_mode or "").strip().lower()
    current_time = utc_now()
    if mode == "foreign":
        candidates = await load_foreign_telegram_reconciliation_candidates(db, limit=limit, now=current_time)
    elif mode == "iran":
        candidates = await load_iran_webapp_reconciliation_candidates(db, limit=limit, now=current_time)
    else:
        return {
            "status": "error",
            "server_mode": mode or None,
            "dry_run": dry_run,
            "error": "unsupported_server_mode",
            "processed": 0,
            "repaired": 0,
            "failed": 0,
            "findings": [],
        }

    findings: list[dict[str, Any]] = []
    repaired = 0
    failed = 0
    gated = 0
    telegram_rate_limited = 0
    telegram_retry_after_seconds: int | None = None
    telegram_response_counts: dict[str, int] = {}
    skipped_locked = 0
    stop_repair_loop = False
    for candidate_index, candidate in enumerate(candidates):
        if dry_run:
            findings.append(_candidate_payload(candidate, result="reported"))
            continue

        if not allow_active_publication and candidate.issue in ACTIVE_PUBLICATION_REPAIR_ISSUES:
            result = {"result": "gated", "reason": "active_publication_gate_enabled"}
        elif not await _try_acquire_candidate_repair_lock(db, candidate):
            skipped_locked += 1
            commit = getattr(db, "commit", None)
            if commit is not None:
                await commit()
            continue
        else:
            try:
                result = await _repair_candidate(
                    db,
                    candidate,
                    server_mode=mode,
                    send_offer_to_channel=send_offer_to_channel,
                    allow_active_publication=allow_active_publication,
                )
            except Exception as exc:
                result = {
                    "result": "failed",
                    "reason": type(exc).__name__,
                }

        if result.get("result") == "repaired":
            _clear_candidate_retry(candidate)
        elif result.get("result") == "failed":
            retry_delay = _schedule_candidate_retry(candidate, result, now=utc_now())
            if retry_delay:
                result["retry_delay_seconds"] = retry_delay

        try:
            if result.get("result") in {"repaired", "failed"}:
                commit = getattr(db, "commit", None)
                if commit is not None:
                    await commit()
        except Exception:
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                await rollback()
            raise

        if result.get("result") == "repaired":
            repaired += 1
        elif result.get("result") == "failed":
            failed += 1
        elif result.get("result") == "gated":
            gated += 1
        response_class = str(result.get("telegram_response_class") or "").strip()
        if response_class:
            telegram_response_counts[response_class] = telegram_response_counts.get(response_class, 0) + 1
        if response_class == "429":
            telegram_rate_limited += 1
            raw_retry_after = _coerce_int(result.get("telegram_retry_after_seconds"))
            if raw_retry_after is not None:
                telegram_retry_after_seconds = max(telegram_retry_after_seconds or 0, raw_retry_after)
            stop_repair_loop = True
        findings.append({**_candidate_payload(candidate, result=result.get("result", "unknown")), **result})
        if stop_repair_loop:
            break
        if (
            not dry_run
            and result.get("telegram_send_attempted")
            and candidate_index < len(candidates) - 1
            and telegram_send_spacing_seconds > 0
        ):
            await asyncio.sleep(max(0.0, float(telegram_send_spacing_seconds)))

    backlog = PublicationReconciliationBacklog()
    if collect_observability:
        backlog = await publication_reconciliation_backlog(db, server_mode=mode)

    if failed:
        status = "partial"
    elif gated:
        status = "gated"
    elif repaired:
        status = "repaired"
    elif candidates:
        status = "action_required"
    else:
        status = "ok"
    return {
        "status": status,
        "server_mode": mode,
        "dry_run": dry_run,
        "processed": len(findings),
        "repaired": repaired,
        "failed": failed,
        "gated": gated,
        "skipped_locked": skipped_locked,
        "backlog_total": backlog.total,
        "backlog_due": backlog.due,
        "backlog_oldest_age_seconds": backlog.oldest_age_seconds,
        "backlog_oldest_due_age_seconds": backlog.oldest_due_age_seconds,
        "telegram_rate_limited": telegram_rate_limited,
        "telegram_retry_after_seconds": telegram_retry_after_seconds,
        "telegram_response_counts": telegram_response_counts,
        "findings": findings,
    }


def _candidate_payload(candidate: PublicationReconciliationCandidate, *, result: str) -> dict[str, Any]:
    offer = candidate.offer
    state = candidate.state
    return {
        "issue": candidate.issue,
        "surface": candidate.surface.value,
        "result": result,
        "offer_id": getattr(offer, "id", None),
        "offer_public_id": getattr(offer, "offer_public_id", None),
        "offer_status": _enum_value(getattr(offer, "status", None)) or None,
        "publication_state_id": getattr(state, "id", None),
        "publication_status": _enum_value(getattr(state, "status", None)) or None,
        "telegram_message_id": (
            _coerce_int(getattr(state, "telegram_message_id", None))
            or _coerce_int(getattr(offer, "channel_message_id", None))
        ),
    }


async def _repair_candidate(
    db: AsyncSession,
    candidate: PublicationReconciliationCandidate,
    *,
    server_mode: str,
    send_offer_to_channel: SendOfferToChannel | None,
    allow_active_publication: bool,
) -> dict[str, Any]:
    if not allow_active_publication and candidate.issue in ACTIVE_PUBLICATION_REPAIR_ISSUES:
        return {"result": "gated", "reason": "active_publication_gate_enabled"}
    if server_mode == "foreign":
        return await _repair_foreign_telegram_candidate(
            db,
            candidate,
            send_offer_to_channel=send_offer_to_channel,
        )
    if server_mode == "iran":
        return await _repair_iran_webapp_candidate(db, candidate)
    return {"result": "failed", "reason": "unsupported_server_mode"}


async def _repair_foreign_telegram_candidate(
    db: AsyncSession,
    candidate: PublicationReconciliationCandidate,
    *,
    send_offer_to_channel: SendOfferToChannel | None,
) -> dict[str, Any]:
    offer = candidate.offer
    if candidate.state is None:
        candidate.state = await get_or_create_telegram_publication_state(db, offer)

    if candidate.issue == "offer_missing_legacy_message_id":
        message_id = apply_existing_telegram_publication_to_offer(offer, candidate.state)
        if message_id:
            return {"result": "repaired", "reason": "legacy_offer_message_id_backfilled", "message_id": message_id}
        return {"result": "failed", "reason": "publication_state_message_id_missing"}

    if send_offer_to_channel is None and not _coerce_int(getattr(offer, "channel_message_id", None)):
        return {"result": "failed", "reason": "telegram_send_callback_required"}

    user = getattr(offer, "user", None)
    if user is None and not _coerce_int(getattr(offer, "channel_message_id", None)):
        return {"result": "failed", "reason": "offer_user_missing"}

    result = await publish_offer_to_telegram_channel_once(
        db,
        offer,
        user,
        send_offer_to_channel=send_offer_to_channel or _no_telegram_send_callback,
    )
    if result.message_id:
        return {
            "result": "repaired",
            "reason": result.skipped_reason or "telegram_publication_repaired",
            "message_id": result.message_id,
            "telegram_send_attempted": bool(getattr(result, "send_attempted", False)),
            "telegram_response_class": getattr(result, "response_class", None),
            "telegram_retry_after_seconds": getattr(result, "retry_after_seconds", None),
        }
    return {
        "result": "failed",
        "reason": result.error_code or result.skipped_reason or "telegram_publication_not_repaired",
        "telegram_send_attempted": bool(getattr(result, "send_attempted", False)),
        "telegram_response_class": getattr(result, "response_class", None),
        "telegram_retry_after_seconds": getattr(result, "retry_after_seconds", None),
    }


async def _no_telegram_send_callback(_offer: Any, _user: Any) -> None:
    return None


async def _repair_iran_webapp_candidate(
    db: AsyncSession,
    candidate: PublicationReconciliationCandidate,
) -> dict[str, Any]:
    offer = candidate.offer
    state = candidate.state
    if state is None:
        state = build_offer_publication_state(
            offer,
            OfferPublicationSurface.WEBAPP_MARKET,
            status=OfferPublicationStatus.PENDING,
            now=utc_now_naive(),
        )
        add = getattr(db, "add", None)
        if add is not None:
            add(state)
        candidate.state = state

    requested_status = (
        OfferPublicationStatus.DISABLED
        if candidate.issue == "stale_terminal_webapp_state"
        else OfferPublicationStatus.VISIBLE
    )
    result = apply_publication_state_update(
        state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=getattr(offer, "version_id", None),
        requested_status=requested_status,
        now=utc_now_naive(),
    )
    if result.applied:
        return {
            "result": "repaired",
            "reason": result.reason,
            "publication_status": normalize_publication_status(state.status).value,
        }
    return {
        "result": "failed",
        "reason": result.reason,
        "publication_status": normalize_publication_status(state.status).value,
    }
