"""Detect and safely repair overtime request lifecycle inconsistencies.

Detection is read-mostly and safe for sync ``/health``. Repairs call only the
existing authoritative transitions (``expire_decision``, ``invalidate_request``,
``invalidate_overtime_requests_for_offer``). Dry-run is the default.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.offer_lifecycle import compute_lifecycle_deadlines, read_overtime_minutes_snapshot
from core.overtime_observability import emit_overtime_signal, log_overtime_event
from core.server_routing import current_server, normalize_server
from core.services.offer_overtime_request_service import (
    OvertimeRequestError,
    expire_decision,
    invalidate_overtime_requests_for_offer,
    invalidate_request,
)
from core.trading_settings import get_trading_settings
from core.utils import utc_now
from models.offer import Offer, OfferStatus
from models.offer_request import (
    OVERTIME_NONTERMINAL_STATUSES,
    OfferRequest,
    OfferRequestStatus,
    OfferRequestWorkflow,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


SILENT_OWNER_WINDOW = timedelta(hours=24)
SILENT_OWNER_TIMEOUT_THRESHOLD = 3
OVERDUE_DELIVERING_GRACE = timedelta(seconds=120)

REPAIRABLE_ISSUES = frozenset(
    {
        "overdue_presented_decision",
        "overdue_delivering",
        "nonterminal_on_inactive_offer",
        "nonterminal_past_final_deadline",
    }
)


@dataclass(frozen=True, slots=True)
class OvertimeReconciliationFinding:
    issue: str
    request_public_id: str | None
    offer_public_id: str | None
    offer_owner_user_id: int | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class OvertimeReconciliationReport:
    dry_run: bool
    findings: tuple[OvertimeReconciliationFinding, ...]
    repaired: tuple[OvertimeReconciliationFinding, ...]
    skipped: tuple[OvertimeReconciliationFinding, ...]
    finding_counts: dict[str, int]
    status_counts: dict[str, int]
    silent_owner_count: int


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _normalized_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _safe_count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _finding(
    issue: str,
    row: OfferRequest,
    *,
    detail: str | None = None,
) -> OvertimeReconciliationFinding:
    return OvertimeReconciliationFinding(
        issue=issue,
        request_public_id=(getattr(row, "request_public_id", None) or None),
        offer_public_id=(getattr(row, "offer_public_id", None) or None),
        offer_owner_user_id=(
            int(getattr(row, "offer_owner_user_id"))
            if getattr(row, "offer_owner_user_id", None) is not None
            else None
        ),
        detail=detail,
    )


async def _nonterminal_status_counts(db: AsyncSession) -> dict[str, int]:
    rows = (
        await db.execute(
            select(OfferRequest.result_status, func.count(OfferRequest.id))
            .where(
                OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
                OfferRequest.result_status.in_(OVERTIME_NONTERMINAL_STATUSES),
            )
            .group_by(OfferRequest.result_status)
        )
    ).all()
    return {_enum_value(status): _safe_count(count) for status, count in rows}


async def _count_silent_owners(db: AsyncSession, *, now: datetime) -> int:
    cutoff = now - SILENT_OWNER_WINDOW
    rows = (
        await db.execute(
            select(
                OfferRequest.offer_owner_user_id,
                func.count(OfferRequest.id),
            )
            .where(
                OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
                OfferRequest.result_status
                == OfferRequestStatus.OVERTIME_DECISION_EXPIRED,
                OfferRequest.terminal_reason == "decision_timeout",
                OfferRequest.decided_at.is_not(None),
                OfferRequest.decided_at >= cutoff,
                OfferRequest.decided_by_user_id.is_(None),
            )
            .group_by(OfferRequest.offer_owner_user_id)
        )
    ).all()
    return sum(1 for _owner, count in rows if _safe_count(count) >= SILENT_OWNER_TIMEOUT_THRESHOLD)


async def collect_overtime_reconciliation_findings(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    normal_lifetime_minutes: int | None = None,
    limit: int = 200,
) -> list[OvertimeReconciliationFinding]:
    clock = _normalized_time(now) or _normalized_time(utc_now()) or datetime.now(timezone.utc)
    lifetime = int(
        normal_lifetime_minutes
        if normal_lifetime_minutes is not None
        else getattr(get_trading_settings(), "offer_expiry_minutes", 0) or 0
    )
    findings: list[OvertimeReconciliationFinding] = []
    cap = max(1, int(limit))

    rows = list(
        (
            await db.execute(
                select(OfferRequest)
                .options(selectinload(OfferRequest.offer))
                .where(
                    OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
                    OfferRequest.result_status.in_(OVERTIME_NONTERMINAL_STATUSES),
                )
                .order_by(OfferRequest.id.asc())
                .limit(cap)
            )
        ).scalars().all()
    )

    owner_occupying: dict[tuple[int, str], list[OfferRequest]] = {}
    for row in rows:
        status = _enum_value(getattr(row, "result_status", None))
        offer = getattr(row, "offer", None)
        if offer is None and getattr(row, "local_offer_id", None) is not None:
            offer = (
                await db.execute(
                    select(Offer).where(Offer.id == int(row.local_offer_id))
                )
            ).scalar_one_or_none()

        if status == OfferRequestStatus.OVERTIME_PRESENTED.value:
            deadline = _normalized_time(getattr(row, "decision_deadline_at", None))
            if deadline is None:
                findings.append(
                    _finding(row=row, issue="presented_without_deadline")
                )
            elif clock >= deadline:
                findings.append(
                    _finding(row=row, issue="overdue_presented_decision")
                )

        if status == OfferRequestStatus.OVERTIME_DELIVERING.value:
            received = _normalized_time(getattr(row, "received_at", None)) or _normalized_time(
                getattr(row, "created_at", None)
            )
            if received is not None and clock >= received + OVERDUE_DELIVERING_GRACE:
                if getattr(row, "telegram_message_id", None) is None:
                    findings.append(
                        _finding(
                            row=row,
                            issue="overdue_delivering",
                            detail="missing_telegram_message_id",
                        )
                    )

        if offer is None:
            findings.append(_finding(row=row, issue="nonterminal_on_missing_offer"))
        else:
            offer_status = _enum_value(getattr(offer, "status", None))
            if offer_status != OfferStatus.ACTIVE.value:
                findings.append(
                    _finding(row=row, issue="nonterminal_on_inactive_offer")
                )
            elif lifetime > 0:
                _normal, final = compute_lifecycle_deadlines(
                    getattr(offer, "created_at", None),
                    normal_lifetime_minutes=lifetime,
                    overtime_minutes_snapshot=read_overtime_minutes_snapshot(offer),
                )
                final_aware = _normalized_time(final)
                if (
                    final_aware is not None
                    and clock >= final_aware
                    and status == OfferRequestStatus.OVERTIME_QUEUED.value
                ):
                    findings.append(
                        _finding(row=row, issue="nonterminal_past_final_deadline")
                    )

        if status in {
            OfferRequestStatus.OVERTIME_DELIVERING.value,
            OfferRequestStatus.OVERTIME_PRESENTED.value,
        }:
            owner_id = getattr(row, "offer_owner_user_id", None)
            home = normalize_server(
                getattr(row, "request_home_server", None),
                current_server(),
            )
            if owner_id is not None:
                owner_occupying.setdefault((int(owner_id), home), []).append(row)

        job_id = getattr(row, "telegram_delivery_job_id", None)
        message_id = getattr(row, "telegram_message_id", None)
        if job_id is not None:
            job = (
                await db.execute(
                    select(TelegramDeliveryJobRecord).where(
                        TelegramDeliveryJobRecord.id == int(job_id)
                    )
                )
            ).scalar_one_or_none()
            if job is None:
                findings.append(
                    _finding(row=row, issue="delivery_job_missing", detail=str(job_id))
                )
            else:
                natural = str(getattr(job, "source_natural_id", "") or "").strip()
                request_id = str(getattr(row, "request_public_id", "") or "").strip()
                if request_id and natural and natural != request_id:
                    findings.append(
                        _finding(
                            row=row,
                            issue="delivery_job_identity_mismatch",
                            detail=natural[:40],
                        )
                    )
                payload = getattr(job, "payload", None)
                if (
                    isinstance(payload, dict)
                    and message_id is not None
                    and payload.get("message_id") not in {None, message_id, int(message_id)}
                ):
                    try:
                        payload_mid = int(payload.get("message_id"))
                    except (TypeError, ValueError):
                        payload_mid = None
                    if payload_mid is not None and payload_mid != int(message_id):
                        findings.append(
                            _finding(
                                row=row,
                                issue="delivery_message_id_mismatch",
                                detail=str(payload_mid),
                            )
                        )

    for (_owner, _home), occupying in owner_occupying.items():
        if len(occupying) > 1:
            for row in occupying:
                findings.append(
                    _finding(
                        row=row,
                        issue="multiple_owner_occupying",
                        detail=str(len(occupying)),
                    )
                )

    completed_bad = list(
        (
            await db.execute(
                select(OfferRequest)
                .where(
                    OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
                    OfferRequest.result_status == OfferRequestStatus.COMPLETED_TRADE,
                    or_(
                        OfferRequest.resulting_trade_id.is_(None),
                        OfferRequest.terminal_reason.is_(None),
                    ),
                )
                .order_by(OfferRequest.id.asc())
                .limit(cap)
            )
        ).scalars().all()
    )
    for row in completed_bad:
        findings.append(
            _finding(row=row, issue="completed_trade_without_trade_id")
        )

    return findings


async def reconcile_overtime_requests(
    db: AsyncSession,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
    normal_lifetime_minutes: int | None = None,
    limit: int = 200,
    flush: bool = True,
) -> OvertimeReconciliationReport:
    clock = _normalized_time(now) or _normalized_time(utc_now()) or datetime.now(timezone.utc)
    lifetime = int(
        normal_lifetime_minutes
        if normal_lifetime_minutes is not None
        else getattr(get_trading_settings(), "offer_expiry_minutes", 0) or 0
    )
    findings = await collect_overtime_reconciliation_findings(
        db,
        now=clock,
        normal_lifetime_minutes=lifetime,
        limit=limit,
    )
    status_counts = await _nonterminal_status_counts(db)
    silent_owner_count = await _count_silent_owners(db, now=clock)
    if silent_owner_count:
        emit_overtime_signal(signal="silent_owner_expiry", count=silent_owner_count)
        log_overtime_event(
            "Overtime silent-owner expiry cluster detected",
            event="silent_owner_expiry",
            result="detected",
            count=silent_owner_count,
        )

    repaired: list[OvertimeReconciliationFinding] = []
    skipped: list[OvertimeReconciliationFinding] = []

    if dry_run:
        skipped.extend(findings)
    else:
        repairable = [
            finding
            for finding in findings
            if finding.issue in REPAIRABLE_ISSUES and finding.request_public_id
        ]
        skipped.extend(
            finding for finding in findings if finding.issue not in REPAIRABLE_ISSUES
        )
        by_request = {f.request_public_id: f for f in repairable}
        if by_request:
            locked_rows = list(
                (
                    await db.execute(
                        select(OfferRequest)
                        .where(
                            OfferRequest.request_public_id.in_(tuple(by_request.keys()))
                        )
                        .with_for_update()
                    )
                ).scalars().all()
            )
            rows_by_id = {
                str(getattr(row, "request_public_id", "") or ""): row
                for row in locked_rows
            }
            for request_id, finding in by_request.items():
                row = rows_by_id.get(request_id)
                if row is None:
                    skipped.append(finding)
                    continue
                try:
                    if finding.issue == "overdue_presented_decision":
                        await expire_decision(
                            db,
                            row,
                            now=clock,
                            flush=False,
                            promote_next=True,
                            normal_lifetime_minutes=lifetime if lifetime > 0 else None,
                        )
                        repaired.append(finding)
                    elif finding.issue == "overdue_delivering":
                        await invalidate_request(
                            db,
                            row,
                            reason="overtime_delivery_reconcile_timeout",
                            now=clock,
                            flush=False,
                        )
                        repaired.append(finding)
                    elif finding.issue in {
                        "nonterminal_on_inactive_offer",
                        "nonterminal_past_final_deadline",
                    }:
                        offer = (
                            await db.execute(
                                select(Offer).where(
                                    Offer.offer_public_id
                                    == str(getattr(row, "offer_public_id", "") or "")
                                )
                            )
                        ).scalar_one_or_none()
                        if offer is None:
                            await invalidate_request(
                                db,
                                row,
                                reason="overtime_reconcile_offer_missing",
                                now=clock,
                                flush=False,
                            )
                        else:
                            await invalidate_overtime_requests_for_offer(
                                db,
                                offer,
                                reason=f"overtime_reconcile_{finding.issue}",
                                now=clock,
                                promote_next=True,
                                normal_lifetime_minutes=(
                                    lifetime if lifetime > 0 else None
                                ),
                                flush=False,
                            )
                        repaired.append(finding)
                    else:
                        skipped.append(finding)
                except OvertimeRequestError:
                    skipped.append(finding)
            if flush:
                await db.flush()

    finding_counts = dict(Counter(item.issue for item in findings))
    log_overtime_event(
        "Overtime reconciliation cycle completed",
        event="reconcile",
        result="success" if dry_run or repaired else "detected",
        count=len(findings),
    )
    return OvertimeReconciliationReport(
        dry_run=bool(dry_run),
        findings=tuple(findings),
        repaired=tuple(repaired),
        skipped=tuple(skipped),
        finding_counts=finding_counts,
        status_counts=status_counts,
        silent_owner_count=silent_owner_count,
    )


async def overtime_observability_summary(
    db: AsyncSession,
    *,
    server_mode: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return sync-health-safe overtime counts without requester identity."""
    clock = _normalized_time(now) or _normalized_time(utc_now()) or datetime.now(timezone.utc)
    try:
        report = await reconcile_overtime_requests(
            db,
            dry_run=True,
            now=clock,
            limit=200,
            flush=False,
        )
    except Exception as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "server_mode": server_mode or current_server(),
        }

    total_findings = sum(report.finding_counts.values())
    status = "ok"
    if total_findings or report.silent_owner_count:
        status = "action_required"
    return {
        "status": status,
        "server_mode": server_mode or current_server(),
        "status_counts": report.status_counts,
        "finding_counts": report.finding_counts,
        "silent_owner_count": report.silent_owner_count,
        "sampled_finding_count": len(report.findings),
    }


async def expire_overdue_presented_decisions(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    normal_lifetime_minutes: int | None = None,
    limit: int = 50,
    flush: bool = True,
) -> int:
    """Sweep overdue presented decisions through ``expire_decision``.

    Intended for the offer-expiry job cycle so decision timeouts do not wait
    solely on an owner click that arrives after the deadline.
    """
    clock = _normalized_time(now) or _normalized_time(utc_now()) or datetime.now(timezone.utc)
    lifetime = int(
        normal_lifetime_minutes
        if normal_lifetime_minutes is not None
        else getattr(get_trading_settings(), "offer_expiry_minutes", 0) or 0
    )
    candidate_rows = list(
        (
            await db.execute(
                select(OfferRequest)
                .where(
                    OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
                    OfferRequest.result_status == OfferRequestStatus.OVERTIME_PRESENTED,
                    OfferRequest.decision_deadline_at.is_not(None),
                )
                .order_by(OfferRequest.decision_deadline_at.asc())
                .limit(max(1, int(limit)))
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
    )
    rows = [
        row
        for row in candidate_rows
        if (_normalized_time(getattr(row, "decision_deadline_at", None)) or clock)
        <= clock
    ]

    repaired = 0
    for row in rows:
        try:
            await expire_decision(
                db,
                row,
                now=clock,
                flush=False,
                promote_next=True,
                normal_lifetime_minutes=lifetime if lifetime > 0 else None,
            )
            repaired += 1
            log_overtime_event(
                "Overtime presented decision expired by sweeper",
                event="decision_timeout",
                result="timeout",
                request_public_id=getattr(row, "request_public_id", None),
                offer_public_id=getattr(row, "offer_public_id", None),
                offer_owner_user_id=getattr(row, "offer_owner_user_id", None),
                request_home_server=getattr(row, "request_home_server", None),
                status=OfferRequestStatus.OVERTIME_DECISION_EXPIRED.value,
                terminal_reason="decision_timeout",
            )
        except OvertimeRequestError:
            continue
    if flush and repaired:
        await db.flush()
    return repaired


async def expire_overdue_delivering_requests(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 50,
    flush: bool = True,
) -> int:
    """Release delivering requests that never received a Telegram message id.

    The owner clock starts only after presentation. A durable job that stays
    delivering without a message id past the official grace is reconciled
    through ``invalidate_request``, matching ``overdue_delivering`` repair.
    """
    clock = _normalized_time(now) or _normalized_time(utc_now()) or datetime.now(timezone.utc)
    candidate_rows = list(
        (
            await db.execute(
                select(OfferRequest)
                .where(
                    OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
                    OfferRequest.result_status == OfferRequestStatus.OVERTIME_DELIVERING,
                    OfferRequest.telegram_message_id.is_(None),
                )
                .order_by(OfferRequest.id.asc())
                .limit(max(1, int(limit)))
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
    )
    rows = []
    for row in candidate_rows:
        received = _normalized_time(getattr(row, "received_at", None)) or _normalized_time(
            getattr(row, "created_at", None)
        )
        if received is not None and clock >= received + OVERDUE_DELIVERING_GRACE:
            rows.append(row)

    repaired = 0
    for row in rows:
        try:
            await invalidate_request(
                db,
                row,
                reason="overtime_delivery_reconcile_timeout",
                now=clock,
                flush=False,
            )
            repaired += 1
            log_overtime_event(
                "Overtime delivering request expired by sweeper",
                event="delivery_timeout",
                result="invalidated",
                request_public_id=getattr(row, "request_public_id", None),
                offer_public_id=getattr(row, "offer_public_id", None),
                offer_owner_user_id=getattr(row, "offer_owner_user_id", None),
                request_home_server=getattr(row, "request_home_server", None),
                status=OfferRequestStatus.OVERTIME_INVALIDATED.value,
                terminal_reason="overtime_delivery_reconcile_timeout",
            )
        except OvertimeRequestError:
            continue
    if flush and repaired:
        await db.flush()
    return repaired
