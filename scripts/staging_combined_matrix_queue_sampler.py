#!/usr/bin/env python3
"""Sample global and run-scoped Telegram queue evidence for combined-matrix."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import heapq
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import Text, and_, cast, func, or_, select

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


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(sorted_values[0], 3)
    rank = (len(sorted_values) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return round(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight, 3)


_PREFIX_BOUNDARIES = ("_", ":", "-", " ")
_SYNTHETIC_PRIVATE_ACTIONS = frozenset(
    {"callback_deadline", "offer_repeat_response", "trade_result"}
)
_DEADLINE_PROMOTED_ACTIONS = frozenset(
    {"trade_result", "partial_offer_edit", "traded_offer_edit"}
)

# Queue sample tuple layout.  The first thirteen columns are intentionally kept
# stable because older evidence helpers and tests consume those offsets.
_ROW_ID = 0
_ROW_CREATED_AT = 1
_ROW_SENT_AT = 2
_ROW_STATE = 3
_ROW_ACTION = 4
_ROW_ATTEMPT_COUNT = 5
_ROW_PROVIDER_ATTEMPT_COUNT = 6
_ROW_LAST_RATE_LIMITED_AT = 7
_ROW_OUTCOME_REASON = 8
_ROW_DISPATCH_STARTED_AT = 9
_ROW_METHOD = 10
_ROW_SOURCE_NATURAL_ID = 11
_ROW_PROVIDER_RESPONSE = 12
_ROW_PRIORITY = 13
_ROW_PRIORITY_RANK = 14
_ROW_ENQUEUED_SEQ = 15
_ROW_ELIGIBLE_AT = 16
_ROW_DELIVERY_DEADLINE_AT = 17
_ROW_DESTINATION_KEY = 18
_ROW_BOT_IDENTITY = 19
_ROW_FEEDER_KIND = 20
_ROW_SOURCE_ORDER_AT = 21
_ROW_NEXT_RETRY_AT = 22
_ROW_DESTINATION_CLASS = 23
_ROW_LAST_RATE_LIMIT_UNTIL = 24
_ROW_PROVIDER_STATUS_CODE = 25
_ROW_PROVIDER_ERROR_CODE = 26


def _notes_match_run_prefix(notes_column, prefix: str):
    """Match offer notes that start with ``prefix`` at a path boundary.

    ``CMB_FOO`` must not match notes for ``CMB_FOO_AG`` / other child lanes.
    """

    notes = func.coalesce(notes_column, "")
    prefix_len = len(prefix)
    return and_(
        func.left(notes, prefix_len) == prefix,
        or_(
            func.length(notes) == prefix_len,
            func.substr(notes, prefix_len + 1, 1).in_(_PREFIX_BOUNDARIES),
        ),
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _enum_text(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).isoformat().replace("+00:00", "Z")


def _effective_priority_at_dispatch(row: tuple[object, ...]) -> tuple[int, int]:
    dispatched = row[_ROW_DISPATCH_STARTED_AT]
    if dispatched is None:
        return int(row[_ROW_PRIORITY] or 0), int(row[_ROW_PRIORITY_RANK] or 0)
    return _effective_priority_at(row, at=_aware(dispatched))


def _effective_priority_at(
    row: tuple[object, ...], *, at: datetime
) -> tuple[int, int]:
    priority = int(row[_ROW_PRIORITY] or 0)
    rank = int(row[_ROW_PRIORITY_RANK] or 0)
    action = _enum_text(row[_ROW_ACTION])
    deadline = row[_ROW_DELIVERY_DEADLINE_AT]
    if (
        action in _DEADLINE_PROMOTED_ACTIONS
        and deadline is not None
        and at >= _aware(deadline)
    ):
        return 0, 1
    return priority, rank


def _is_first_attempt_without_rate_limit(row: tuple[object, ...]) -> bool:
    return (
        int(row[_ROW_ATTEMPT_COUNT] or 0) <= 1
        and int(row[_ROW_PROVIDER_ATTEMPT_COUNT] or 0) <= 1
        and row[_ROW_LAST_RATE_LIMITED_AT] is None
    )


def _provider_started_at(row: tuple[object, ...]) -> datetime | None:
    """Return the exact provider-call boundary, with a legacy fallback."""

    response = row[12] if len(row) > 12 else None
    raw = (
        response.get("_provider_started_at_utc")
        if isinstance(response, dict)
        else None
    )
    if isinstance(raw, str) and raw.strip():
        try:
            return _aware(datetime.fromisoformat(raw.strip().replace("Z", "+00:00")))
        except ValueError:
            pass
    fallback = row[_ROW_DISPATCH_STARTED_AT]
    return _aware(fallback) if fallback is not None else None


def _provider_timestamp_source(row: tuple[object, ...]) -> str:
    response = row[12] if len(row) > 12 else None
    return (
        "provider_started_at_utc"
        if isinstance(response, dict) and response.get("_provider_started_at_utc")
        else "dispatch_started_at_legacy_fallback"
    )


def _dispatch_evidence_payload(
    rows: list[tuple[object, ...]],
    *,
    destination_min_interval_seconds: float,
    destination_burst_idle_seconds: float,
    destination_burst_capacity: int,
    destination_burst_recovery_seconds: float,
    claim_race_grace_seconds: float = 0.5,
    clock_tolerance_seconds: float = 0.03,
) -> dict[str, object]:
    """Build bounded, auditable evidence for order, pacing, burst and 429.

    A terminal job row retains only its latest dispatch timestamp.  Retried or
    rate-limited rows are therefore excluded from strict first-claim ordering,
    but remain in the retry/429 and pacing evidence.  Priority inversions are
    reported only for a higher-priority job that was demonstrably enqueued and
    eligible on the same serialized destination before the lower-priority
    dispatch (with a small claim-to-dispatch race allowance).
    """

    dispatched_rows = [
        row
        for row in rows
        if len(row) > _ROW_PROVIDER_ERROR_CODE
        and row[_ROW_DISPATCH_STARTED_AT] is not None
    ]
    dispatched_rows.sort(
        key=lambda row: (_aware(row[_ROW_DISPATCH_STARTED_AT]), int(row[_ROW_ID]))
    )
    strict_rows = [row for row in dispatched_rows if _is_first_attempt_without_rate_limit(row)]
    strict_ids = {int(row[_ROW_ID]) for row in strict_rows}
    grace = timedelta(seconds=max(0.0, float(claim_race_grace_seconds)))

    priority_inversions: list[dict[str, object]] = []
    fifo_inversions: list[dict[str, object]] = []
    strict_by_destination: dict[str, list[tuple[object, ...]]] = {}
    for row in strict_rows:
        strict_by_destination.setdefault(str(row[_ROW_DESTINATION_KEY] or ""), []).append(row)

    # O(n log n) live audit.  For each serialized destination, rows enter the
    # pending heap only after their creation/eligibility is provably before the
    # current claim boundary. Deadline promotions are represented by a newer
    # heap generation and stale entries are discarded lazily.
    for destination, destination_rows in strict_by_destination.items():
        destination_rows.sort(
            key=lambda row: (_aware(row[_ROW_DISPATCH_STARTED_AT]), int(row[_ROW_ID]))
        )
        available_rows = sorted(
            destination_rows,
            key=lambda row: max(
                _aware(row[_ROW_CREATED_AT]),
                _aware(row[_ROW_ELIGIBLE_AT])
                if row[_ROW_ELIGIBLE_AT] is not None
                else _aware(row[_ROW_CREATED_AT]),
            ),
        )
        deadline_rows = sorted(
            (
                row
                for row in destination_rows
                if _enum_text(row[_ROW_ACTION]) in _DEADLINE_PROMOTED_ACTIONS
                and row[_ROW_DELIVERY_DEADLINE_AT] is not None
            ),
            key=lambda row: _aware(row[_ROW_DELIVERY_DEADLINE_AT]),
        )
        active: set[int] = set()
        dispatched: set[int] = set()
        generation: dict[int, int] = {}
        priority_heap: list[tuple[int, int, int, int, int]] = []
        publish_heaps: dict[tuple[int, int], list[tuple[int, int]]] = {}
        available_index = 0
        deadline_index = 0

        def push(row: tuple[object, ...], priority: tuple[int, int]) -> None:
            job_id = int(row[_ROW_ID])
            generation[job_id] = generation.get(job_id, 0) + 1
            heapq.heappush(
                priority_heap,
                (
                    int(priority[0]),
                    int(priority[1]),
                    int(row[_ROW_ENQUEUED_SEQ]),
                    job_id,
                    generation[job_id],
                ),
            )

        for current in destination_rows:
            current_dispatch = _aware(current[_ROW_DISPATCH_STARTED_AT])
            claim_boundary = current_dispatch - grace
            while available_index < len(available_rows):
                candidate = available_rows[available_index]
                available_at = max(
                    _aware(candidate[_ROW_CREATED_AT]),
                    _aware(candidate[_ROW_ELIGIBLE_AT])
                    if candidate[_ROW_ELIGIBLE_AT] is not None
                    else _aware(candidate[_ROW_CREATED_AT]),
                )
                if available_at > claim_boundary:
                    break
                available_index += 1
                candidate_id = int(candidate[_ROW_ID])
                active.add(candidate_id)
                push(candidate, _effective_priority_at(candidate, at=claim_boundary))
                if _enum_text(candidate[_ROW_ACTION]) == "offer_publish":
                    key = (
                        int(candidate[_ROW_PRIORITY]),
                        int(candidate[_ROW_PRIORITY_RANK]),
                    )
                    heapq.heappush(
                        publish_heaps.setdefault(key, []),
                        (int(candidate[_ROW_ENQUEUED_SEQ]), candidate_id),
                    )
            while deadline_index < len(deadline_rows):
                candidate = deadline_rows[deadline_index]
                if _aware(candidate[_ROW_DELIVERY_DEADLINE_AT]) > claim_boundary:
                    break
                deadline_index += 1
                candidate_id = int(candidate[_ROW_ID])
                if candidate_id in active and candidate_id not in dispatched:
                    push(candidate, (0, 1))

            current_id = int(current[_ROW_ID])
            dispatched.add(current_id)
            active.discard(current_id)
            while priority_heap:
                priority, rank, _seq, candidate_id, candidate_generation = priority_heap[0]
                if (
                    candidate_id in dispatched
                    or candidate_id not in active
                    or generation.get(candidate_id) != candidate_generation
                ):
                    heapq.heappop(priority_heap)
                    continue
                break
            current_priority = _effective_priority_at(current, at=claim_boundary)
            if priority_heap and priority_heap[0][:2] < current_priority:
                priority, rank, _seq, candidate_id, _generation = priority_heap[0]
                priority_inversions.append(
                    {
                        "dispatched_job_id": current_id,
                        "waiting_job_id": candidate_id,
                        "dispatched_at": _iso(current_dispatch),
                        "dispatched_priority": list(current_priority),
                        "waiting_priority": [priority, rank],
                        "destination_sha256": hashlib.sha256(
                            destination.encode("utf-8")
                        ).hexdigest()[:16],
                    }
                )

            if _enum_text(current[_ROW_ACTION]) == "offer_publish":
                key = (
                    int(current[_ROW_PRIORITY]),
                    int(current[_ROW_PRIORITY_RANK]),
                )
                publish_heap = publish_heaps.get(key, [])
                while publish_heap and (
                    publish_heap[0][1] in dispatched
                    or publish_heap[0][1] not in active
                ):
                    heapq.heappop(publish_heap)
                if (
                    publish_heap
                    and publish_heap[0][0] < int(current[_ROW_ENQUEUED_SEQ])
                ):
                    waiting_seq, waiting_id = publish_heap[0]
                    fifo_inversions.append(
                        {
                            "dispatched_job_id": current_id,
                            "waiting_job_id": waiting_id,
                            "dispatched_at": _iso(current_dispatch),
                            "dispatched_priority": list(current_priority),
                            "waiting_priority": list(current_priority),
                            "dispatched_enqueued_seq": int(
                                current[_ROW_ENQUEUED_SEQ]
                            ),
                            "waiting_enqueued_seq": waiting_seq,
                            "destination_sha256": hashlib.sha256(
                                destination.encode("utf-8")
                            ).hexdigest()[:16],
                        }
                    )

    ordered_digest_rows = [
        {
            "job_id": int(row[_ROW_ID]),
            "action": _enum_text(row[_ROW_ACTION]),
            "effective_priority": list(_effective_priority_at_dispatch(row)),
            "enqueued_seq": int(row[_ROW_ENQUEUED_SEQ]),
            "dispatch_started_at": _iso(row[_ROW_DISPATCH_STARTED_AT]),
            "destination_sha256": hashlib.sha256(
                str(row[_ROW_DESTINATION_KEY] or "").encode("utf-8")
            ).hexdigest()[:16],
        }
        for row in dispatched_rows
    ]
    sequence_digest = hashlib.sha256(
        json.dumps(
            ordered_digest_rows,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    by_destination: dict[str, list[tuple[object, ...]]] = {}
    for row in dispatched_rows:
        by_destination.setdefault(str(row[_ROW_DESTINATION_KEY] or ""), []).append(row)
    all_gaps: list[float] = []
    sustained_gaps: list[float] = []
    allowed_burst_gaps: list[dict[str, object]] = []
    spacing_violations: list[dict[str, object]] = []
    tolerance = max(0.0, float(clock_tolerance_seconds))
    minimum_interval = max(0.001, float(destination_min_interval_seconds))
    idle_seconds = max(minimum_interval, float(destination_burst_idle_seconds))
    recovery_seconds = max(0.0, float(destination_burst_recovery_seconds))
    configured_burst_capacity = max(1, int(destination_burst_capacity))

    rate_limit_events_by_destination: dict[str, list[datetime]] = {}
    for row in rows:
        if (
            len(row) > _ROW_LAST_RATE_LIMITED_AT
            and row[_ROW_LAST_RATE_LIMITED_AT] is not None
        ):
            rate_limit_events_by_destination.setdefault(
                str(row[_ROW_DESTINATION_KEY] or ""), []
            ).append(_aware(row[_ROW_LAST_RATE_LIMITED_AT]))
    for events in rate_limit_events_by_destination.values():
        events.sort()
    for destination, destination_rows in by_destination.items():
        destination_rows.sort(
            key=lambda row: (
                _provider_started_at(row) or _aware(row[_ROW_DISPATCH_STARTED_AT]),
                int(row[_ROW_ID]),
            )
        )
        destination_class = _enum_text(destination_rows[0][_ROW_DESTINATION_CLASS])
        capacity = configured_burst_capacity if destination_class == "channel" else 1
        accepts_since_idle = 1
        previous = destination_rows[0]
        for current in destination_rows[1:]:
            previous_at = _provider_started_at(previous)
            current_at = _provider_started_at(current)
            if previous_at is None or current_at is None:
                previous = current
                continue
            gap = max(0.0, (current_at - previous_at).total_seconds())
            all_gaps.append(gap)
            if gap >= idle_seconds - tolerance:
                accepts_since_idle = 1
            else:
                accepts_since_idle += 1
            recent_429 = next(
                (
                    observed
                    for observed in reversed(
                        rate_limit_events_by_destination.get(destination, [])
                    )
                    if observed <= current_at
                    and (current_at - observed).total_seconds() < recovery_seconds
                ),
                None,
            )
            burst_permitted = recent_429 is None and capacity > 1
            short_gap = gap + tolerance < minimum_interval
            if not short_gap:
                sustained_gaps.append(gap)
            elif burst_permitted and accepts_since_idle <= capacity:
                allowed_burst_gaps.append(
                    {
                        "previous_job_id": int(previous[_ROW_ID]),
                        "job_id": int(current[_ROW_ID]),
                        "gap_seconds": round(gap, 3),
                        "timestamp_source": _provider_timestamp_source(current),
                        "accept_number_since_idle": accepts_since_idle,
                        "capacity": capacity,
                    }
                )
            else:
                spacing_violations.append(
                    {
                        "previous_job_id": int(previous[_ROW_ID]),
                        "job_id": int(current[_ROW_ID]),
                        "gap_seconds": round(gap, 3),
                        "timestamp_source": _provider_timestamp_source(current),
                        "capacity": capacity,
                        "burst_disabled_by_recent_429": recent_429 is not None,
                        "destination_sha256": hashlib.sha256(
                            destination.encode("utf-8")
                        ).hexdigest()[:16],
                    }
                )
            previous = current

    all_gaps.sort()
    sustained_gaps.sort()
    rate_limit_rows = [
        row
        for row in rows
        if len(row) > _ROW_PROVIDER_ERROR_CODE
        and row[_ROW_LAST_RATE_LIMITED_AT] is not None
    ]
    retry_gate_violations: list[dict[str, object]] = []
    retry_gate_verified = 0
    for row in rate_limit_rows:
        provider_started_at = _provider_started_at(row)
        retry_until = row[_ROW_LAST_RATE_LIMIT_UNTIL]
        if provider_started_at is None or retry_until is None:
            continue
        retry_gate_verified += 1
        if provider_started_at + timedelta(seconds=tolerance) < _aware(
            retry_until
        ):
            retry_gate_violations.append(
                {
                    "job_id": int(row[_ROW_ID]),
                    "provider_started_at": _iso(provider_started_at),
                    "timestamp_source": _provider_timestamp_source(row),
                    "last_rate_limit_until": _iso(retry_until),
                }
            )

    sample = ordered_digest_rows[:20]
    if len(ordered_digest_rows) > 40:
        sample += ordered_digest_rows[-20:]
    elif len(ordered_digest_rows) > 20:
        sample += ordered_digest_rows[20:]
    return {
        "dispatch_count": len(dispatched_rows),
        "strict_first_attempt_dispatch_count": len(strict_rows),
        "strict_first_attempt_job_ids_sha256": hashlib.sha256(
            ",".join(str(value) for value in sorted(strict_ids)).encode("utf-8")
        ).hexdigest(),
        "retry_rows_excluded_from_strict_order": len(dispatched_rows) - len(strict_rows),
        "dispatch_sequence_sha256": sequence_digest,
        "dispatch_sequence_sample": sample,
        "priority": {
            "ok": not priority_inversions,
            "proven_inversion_count": len(priority_inversions),
            "proven_inversions": priority_inversions[:100],
            "claim_race_grace_seconds": float(claim_race_grace_seconds),
        },
        "offer_publish_fifo": {
            "ok": not fifo_inversions,
            "proven_inversion_count": len(fifo_inversions),
            "proven_inversions": fifo_inversions[:100],
        },
        "spacing": {
            "ok": not spacing_violations,
            "configured_min_interval_seconds": minimum_interval,
            "clock_tolerance_seconds": tolerance,
            "gap_sample_count": len(all_gaps),
            "gap_seconds": {
                "min": round(all_gaps[0], 3) if all_gaps else None,
                "p50": _percentile(all_gaps, 50),
                "p95": _percentile(all_gaps, 95),
                "max": round(all_gaps[-1], 3) if all_gaps else None,
            },
            "sustained_non_burst_gap_seconds": {
                "min": round(sustained_gaps[0], 3) if sustained_gaps else None,
                "p50": _percentile(sustained_gaps, 50),
                "p95": _percentile(sustained_gaps, 95),
            },
            "allowed_burst_gap_count": len(allowed_burst_gaps),
            "allowed_burst_gap_sample": allowed_burst_gaps[:40],
            "violation_count": len(spacing_violations),
            "violations": spacing_violations[:100],
        },
        "rate_limit": {
            "observed_job_count": len(rate_limit_rows),
            "retry_gate_verified_count": retry_gate_verified,
            "retry_gate_respected": not retry_gate_violations,
            "retry_gate_violation_count": len(retry_gate_violations),
            "retry_gate_violations": retry_gate_violations[:100],
            "burst_recovery_seconds": recovery_seconds,
        },
        "provider_outcomes": {
            "status_code_counts": {
                str(code): sum(1 for row in rows if row[_ROW_PROVIDER_STATUS_CODE] == code)
                for code in sorted(
                    {
                        int(row[_ROW_PROVIDER_STATUS_CODE])
                        for row in rows
                        if len(row) > _ROW_PROVIDER_ERROR_CODE
                        and row[_ROW_PROVIDER_STATUS_CODE] is not None
                    }
                )
            },
            "error_code_counts": {
                str(code): sum(1 for row in rows if row[_ROW_PROVIDER_ERROR_CODE] == code)
                for code in sorted(
                    {
                        int(row[_ROW_PROVIDER_ERROR_CODE])
                        for row in rows
                        if len(row) > _ROW_PROVIDER_ERROR_CODE
                        and row[_ROW_PROVIDER_ERROR_CODE] is not None
                    }
                )
            },
        },
    }


def _timing_payload(
    rows: list[tuple[object, ...]], *, since: datetime
) -> dict[str, object]:
    latencies: list[float] = []
    by_minute: dict[str, list[float]] = {}
    for _job_id, created_at, sent_at, _state, _action_kind, *_rest in rows:
        if created_at is None or sent_at is None:
            continue
        created = _aware(created_at)
        sent = _aware(sent_at)
        latency = (sent - created).total_seconds()
        if latency < 0:
            continue
        latencies.append(latency)
        bucket = sent.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        by_minute.setdefault(bucket, []).append(latency)
    latencies.sort()
    minute_means = {
        minute: round(sum(values) / len(values), 3)
        for minute, values in sorted(by_minute.items())
        if values
    }
    best_minute = min(minute_means.items(), key=lambda item: item[1]) if minute_means else None
    return {
        "since_utc": since.isoformat().replace("+00:00", "Z"),
        "sent_sample_count": len(latencies),
        "latency_seconds": {
            "p50": _percentile(latencies, 50),
            "p90": _percentile(latencies, 90),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "max": round(latencies[-1], 3) if latencies else None,
            "mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
        },
        "best_send_minute_utc": best_minute[0] if best_minute else None,
        "best_send_minute_mean_latency_seconds": best_minute[1] if best_minute else None,
        "per_minute_mean_latency_sample": dict(list(minute_means.items())[:80]),
    }


def _provider_timing_payload(
    rows: list[tuple[object, ...]], *, slow_edit_threshold_seconds: float
) -> dict[str, object]:
    latencies: list[float] = []
    edit_latencies: list[float] = []
    for row in rows:
        sent_at = row[2]
        dispatch_started_at = row[9] if len(row) > 9 else None
        method = str(row[10] or "") if len(row) > 10 else ""
        provider_response = row[12] if len(row) > 12 else None
        raw_latency_ms = (
            provider_response.get("_provider_latency_ms")
            if isinstance(provider_response, dict)
            else None
        )
        if isinstance(raw_latency_ms, (int, float)):
            latency = float(raw_latency_ms) / 1000.0
        elif sent_at is not None and dispatch_started_at is not None:
            # Compatibility fallback for rows created before exact monotonic
            # provider latency was persisted.
            latency = (
                _aware(sent_at) - _aware(dispatch_started_at)
            ).total_seconds()
        else:
            continue
        if latency < 0:
            continue
        latencies.append(latency)
        if method in {"editMessageText", "editMessageReplyMarkup"}:
            edit_latencies.append(latency)
    latencies.sort()
    edit_latencies.sort()
    return {
        "sample_count": len(latencies),
        "latency_seconds": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "max": round(latencies[-1], 3) if latencies else None,
        },
        "edit_sample_count": len(edit_latencies),
        "edit_latency_seconds": {
            "p50": _percentile(edit_latencies, 50),
            "p95": _percentile(edit_latencies, 95),
            "max": round(edit_latencies[-1], 3) if edit_latencies else None,
        },
        "slow_edit_threshold_seconds": float(slow_edit_threshold_seconds),
        "slow_edit_count": sum(
            value >= float(slow_edit_threshold_seconds)
            for value in edit_latencies
        ),
    }


def _queue_partition_payload(
    rows: list[tuple[object, ...]],
    *,
    pending_values: set[str],
    failure_values: set[str],
    synthetic_private: bool = False,
) -> dict[str, object]:
    """Summarise one queue partition without mixing public and synthetic-private jobs."""

    def value(item: object) -> str:
        return str(getattr(item, "value", item) or "")

    state_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    sent_action_counts: dict[str, int] = {}
    sent_offer_public_ids: list[str] = []
    retried = 0
    rate_limited = 0
    retry_recovered = 0
    rate_limit_recovered = 0
    expected_failed = 0
    unexpected_failed = 0
    failure_reason_counts: dict[str, int] = {}
    for row in rows:
        state = value(row[3])
        action = value(row[4])
        state_counts[state] = state_counts.get(state, 0) + 1
        action_counts[action] = action_counts.get(action, 0) + 1
        if row[2] is not None:
            sent_action_counts[action] = sent_action_counts.get(action, 0) + 1
            if action == "offer_publish" and len(row) > 11:
                sent_offer_public_ids.append(str(row[11]))
        was_retried = int(row[5] or 0) > 1 or int(row[6] or 0) > 1
        was_rate_limited = row[7] is not None
        terminally_healthy = state not in pending_values and state not in failure_values
        if was_retried:
            retried += 1
            retry_recovered += int(terminally_healthy)
        if was_rate_limited:
            rate_limited += 1
            rate_limit_recovered += int(terminally_healthy)
        if state in failure_values:
            response = row[12] if len(row) > 12 else None
            description = ""
            if isinstance(response, dict):
                description = str(
                    response.get("description") or response.get("error") or ""
                ).lower()
            outcome = str(row[8] or "") if len(row) > 8 else ""
            if "chat not found" in description:
                reason = "telegram_chat_not_found"
            elif "unsupported parse_mode" in description:
                reason = "telegram_unsupported_parse_mode"
            else:
                reason = outcome or "unspecified_terminal_failure"
            failure_reason_counts[reason] = failure_reason_counts.get(reason, 0) + 1
            expected_private_failure = (
                synthetic_private
                and action in {"offer_repeat_response", "trade_result"}
                and reason == "telegram_chat_not_found"
            )
            if expected_private_failure:
                expected_failed += 1
            else:
                unexpected_failed += 1
    return {
        "job_count": len(rows),
        "pending_jobs": sum(
            count for state, count in state_counts.items() if state in pending_values
        ),
        "sent_jobs": sum(1 for row in rows if row[2] is not None),
        "failed_jobs": sum(
            count for state, count in state_counts.items() if state in failure_values
        ),
        "expected_failed_jobs": expected_failed,
        "unexpected_failed_jobs": unexpected_failed,
        "failure_reason_counts": dict(sorted(failure_reason_counts.items())),
        "retried_jobs": retried,
        "retry_recovered_jobs": retry_recovered,
        "rate_limited_jobs": rate_limited,
        "rate_limit_recovered_jobs": rate_limit_recovered,
        "state_counts": dict(sorted(state_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "sent_action_counts": dict(sorted(sent_action_counts.items())),
        "sent_offer_public_ids": sorted(set(sent_offer_public_ids)),
    }


def _parse_since(value: str | None, *, lookback_minutes: int) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return _aware(parsed)
    return datetime.now(timezone.utc) - timedelta(minutes=max(1, int(lookback_minutes)))


async def _sample(
    prefix: str | None,
    *,
    lookback_minutes: int,
    timing: bool,
    since_utc: str | None = None,
) -> dict[str, object]:
    _guard()
    from core.telegram_delivery_queue_contract import TelegramDeliveryState
    from core.telegram_delivery_trade_result_binding import (
        trade_result_queue_job_id_from_receipt,
    )
    from models.offer import Offer
    from models.telegram_delivery_job import TelegramDeliveryJobRecord
    from models.trade import Trade
    from models.trade_delivery_receipt import TradeDeliveryReceipt

    async with AsyncSessionLocal() as session:
        pending_states = (
            TelegramDeliveryState.PENDING,
            TelegramDeliveryState.PENDING_RETRY,
            TelegramDeliveryState.LEASED,
            TelegramDeliveryState.PENDING_RECONCILE,
        )
        global_pending = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TelegramDeliveryJobRecord)
                    .where(TelegramDeliveryJobRecord.state.in_(pending_states))
                )
            ).scalar_one()
        )
        global_total = int(
            (
                await session.execute(select(func.count()).select_from(TelegramDeliveryJobRecord))
            ).scalar_one()
        )
        scoped_rows: list[tuple[object, ...]] = []
        scoped_offer_db_ids: list[int] = []
        scoped_offer_ids: list[str] = []
        scoped_private_job_ids: list[int] = []
        if prefix:
            offer_rows = (
                await session.execute(
                    select(Offer.id, Offer.offer_public_id).where(
                            Offer.offer_public_id.is_not(None),
                            _notes_match_run_prefix(Offer.notes, prefix),
                    )
                )
            ).all()
            scoped_offer_db_ids = [int(row[0]) for row in offer_rows]
            scoped_offer_ids = [str(row[1]) for row in offer_rows]
        if scoped_offer_db_ids:
            scoped_trade_ids = [
                int(row[0])
                for row in (
                    await session.execute(
                        select(Trade.id).where(
                            Trade.offer_id.in_(scoped_offer_db_ids)
                        )
                    )
                ).all()
            ]
            receipt_rows = (
                await session.execute(
                    select(TradeDeliveryReceipt.worker_id).where(
                        or_(
                            TradeDeliveryReceipt.offer_id.in_(
                                scoped_offer_db_ids
                            ),
                            TradeDeliveryReceipt.trade_id.in_(
                                scoped_trade_ids or [-1]
                            ),
                        )
                    )
                )
            ).all()
            for (worker_id,) in receipt_rows:
                job_id = trade_result_queue_job_id_from_receipt(
                    SimpleNamespace(worker_id=worker_id)
                )
                if job_id is not None:
                    scoped_private_job_ids.append(job_id)
        if prefix or scoped_offer_ids or scoped_private_job_ids:
            scope_conditions = []
            if scoped_offer_ids:
                scope_conditions.append(
                    TelegramDeliveryJobRecord.source_natural_id.in_(
                        scoped_offer_ids
                    )
                )
            if scoped_private_job_ids:
                scope_conditions.append(
                    TelegramDeliveryJobRecord.id.in_(scoped_private_job_ids)
                )
            if prefix:
                # autoescape: SQL LIKE treats "_" as a single-char wildcard; without
                # escaping, market notes like FMX_STAGE_CMB-14BURST-... falsely match
                # wave prefix CMB_14BURST_... and block the pre-wave baseline.
                scope_conditions.extend(
                    (
                        TelegramDeliveryJobRecord.source_natural_id.contains(
                            prefix, autoescape=True
                        ),
                        TelegramDeliveryJobRecord.dedupe_key.contains(
                            prefix, autoescape=True
                        ),
                        TelegramDeliveryJobRecord.run_id == prefix,
                        cast(TelegramDeliveryJobRecord.payload, Text).contains(
                            prefix, autoescape=True
                        ),
                    )
                )
            scoped_rows = list(
                (
                    await session.execute(
                        select(
                            TelegramDeliveryJobRecord.id,
                            TelegramDeliveryJobRecord.created_at,
                            TelegramDeliveryJobRecord.sent_at,
                            TelegramDeliveryJobRecord.state,
                            TelegramDeliveryJobRecord.action_kind,
                            TelegramDeliveryJobRecord.attempt_count,
                            TelegramDeliveryJobRecord.provider_attempt_count,
                            TelegramDeliveryJobRecord.last_rate_limited_at,
                            TelegramDeliveryJobRecord.outcome_reason,
                            TelegramDeliveryJobRecord.dispatch_started_at,
                            TelegramDeliveryJobRecord.method,
                            TelegramDeliveryJobRecord.source_natural_id,
                            TelegramDeliveryJobRecord.provider_response,
                            TelegramDeliveryJobRecord.priority,
                            TelegramDeliveryJobRecord.priority_rank,
                            TelegramDeliveryJobRecord.enqueued_seq,
                            TelegramDeliveryJobRecord.eligible_at,
                            TelegramDeliveryJobRecord.delivery_deadline_at,
                            TelegramDeliveryJobRecord.destination_key,
                            TelegramDeliveryJobRecord.bot_identity,
                            TelegramDeliveryJobRecord.feeder_kind,
                            TelegramDeliveryJobRecord.source_order_at,
                            TelegramDeliveryJobRecord.next_retry_at,
                            TelegramDeliveryJobRecord.destination_class,
                            TelegramDeliveryJobRecord.last_rate_limit_until,
                            TelegramDeliveryJobRecord.provider_status_code,
                            TelegramDeliveryJobRecord.provider_error_code,
                        ).where(or_(*scope_conditions))
                    )
                ).all()
            )

        pending_values = {item.value for item in pending_states}
        failure_values = {
            TelegramDeliveryState.AMBIGUOUS.value,
            TelegramDeliveryState.AMBIGUOUS_UNRESOLVED.value,
            TelegramDeliveryState.PERMANENT_UNDELIVERABLE.value,
            TelegramDeliveryState.TERMINAL_FAILED.value,
            TelegramDeliveryState.QUARANTINED.value,
            TelegramDeliveryState.BLOCKED_DESTINATION.value,
            TelegramDeliveryState.BLOCKED_BOT.value,
            TelegramDeliveryState.BLOCKED_GATEWAY.value,
        }
        private_job_ids = set(scoped_private_job_ids)
        private_rows = [
            row
            for row in scoped_rows
            if int(row[0]) in private_job_ids
            or str(getattr(row[4], "value", row[4]) or "")
            in _SYNTHETIC_PRIVATE_ACTIONS
        ]
        private_row_ids = {int(row[0]) for row in private_rows}
        public_rows = [row for row in scoped_rows if int(row[0]) not in private_row_ids]
        scoped_partition = _queue_partition_payload(
            scoped_rows,
            pending_values=pending_values,
            failure_values=failure_values,
        )
        public_partition = _queue_partition_payload(
            public_rows,
            pending_values=pending_values,
            failure_values=failure_values,
        )
        private_partition = _queue_partition_payload(
            private_rows,
            pending_values=pending_values,
            failure_values=failure_values,
            synthetic_private=True,
        )
        since = _parse_since(since_utc, lookback_minutes=lookback_minutes)
        scoped_timing_rows = [
            row for row in public_rows if row[1] is not None and _aware(row[1]) >= since
        ]
        timing_payload = (
            _timing_payload(scoped_timing_rows, since=since) if timing else {}
        )
        provider_timing_payload = (
            _provider_timing_payload(
                scoped_timing_rows,
                slow_edit_threshold_seconds=float(
                    getattr(
                        settings,
                        "telegram_delivery_queue_edit_slow_response_seconds",
                        2.0,
                    )
                ),
            )
            if timing
            else {}
        )
        # Keep public-channel timing on the public partition. The combined
        # runner must never score synthetic private recipients as if they were
        # the real public Telegram queue.
        public_partition["timing"] = timing_payload
        public_partition["provider_timing"] = provider_timing_payload
        public_partition["dispatch_evidence"] = (
            _dispatch_evidence_payload(
                scoped_timing_rows,
                destination_min_interval_seconds=float(
                    getattr(
                        settings,
                        "telegram_delivery_queue_destination_min_interval_seconds",
                        0.9,
                    )
                ),
                destination_burst_idle_seconds=float(
                    getattr(
                        settings,
                        "telegram_delivery_queue_destination_burst_idle_seconds",
                        3.2,
                    )
                ),
                destination_burst_capacity=int(
                    getattr(
                        settings,
                        "telegram_delivery_queue_destination_burst_capacity",
                        2,
                    )
                ),
                destination_burst_recovery_seconds=float(
                    getattr(
                        settings,
                        "telegram_delivery_queue_destination_burst_recovery_seconds",
                        300.0,
                    )
                ),
            )
            if timing
            else {}
        )

        global_timing_payload: dict[str, object] = {}
        if timing:
            global_rows = list(
                (
                    await session.execute(
                        select(
                            TelegramDeliveryJobRecord.id,
                            TelegramDeliveryJobRecord.created_at,
                            TelegramDeliveryJobRecord.sent_at,
                            TelegramDeliveryJobRecord.state,
                            TelegramDeliveryJobRecord.action_kind,
                            TelegramDeliveryJobRecord.attempt_count,
                            TelegramDeliveryJobRecord.provider_attempt_count,
                            TelegramDeliveryJobRecord.last_rate_limited_at,
                            TelegramDeliveryJobRecord.outcome_reason,
                            TelegramDeliveryJobRecord.dispatch_started_at,
                            TelegramDeliveryJobRecord.method,
                            TelegramDeliveryJobRecord.source_natural_id,
                            TelegramDeliveryJobRecord.provider_response,
                        ).where(TelegramDeliveryJobRecord.created_at >= since)
                    )
                ).all()
            )
            global_timing_payload = _timing_payload(global_rows, since=since)

    return {
        "ok": True,
        "at_utc": _utc(),
        "server_mode": getattr(settings, "server_mode", None),
        "prefix": prefix,
        "scoped": {
            "offer_count": len(scoped_offer_ids),
            "offer_public_ids": scoped_offer_ids,
            "private_trade_job_ids": sorted(set(scoped_private_job_ids)),
            "job_count": scoped_partition["job_count"],
            "job_ids": [int(row[0]) for row in scoped_rows],
            "pending_jobs": scoped_partition["pending_jobs"],
            "sent_jobs": scoped_partition["sent_jobs"],
            "failed_jobs": scoped_partition["failed_jobs"],
            "retried_jobs": scoped_partition["retried_jobs"],
            "rate_limited_jobs": scoped_partition["rate_limited_jobs"],
            "state_counts": scoped_partition["state_counts"],
            "action_counts": scoped_partition["action_counts"],
            "sent_action_counts": scoped_partition["sent_action_counts"],
            "sent_offer_public_ids": scoped_partition["sent_offer_public_ids"],
            "public": public_partition,
            "synthetic_private": private_partition,
            "timing": timing_payload,
            "provider_timing": provider_timing_payload,
            "dispatch_evidence": public_partition["dispatch_evidence"],
        },
        "global": {
            "pending_jobs": global_pending,
            "total_jobs": global_total,
            "timing": global_timing_payload,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", default=None)
    parser.add_argument("--timing", action="store_true")
    parser.add_argument("--lookback-minutes", type=int, default=45)
    parser.add_argument(
        "--since-utc",
        default=None,
        help="ISO-8601 lower bound for timing; preferred over rolling lookback",
    )
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(
            _sample(
                args.run_prefix,
                lookback_minutes=args.lookback_minutes,
                timing=bool(args.timing),
                since_utc=args.since_utc,
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
