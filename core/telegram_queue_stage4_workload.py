"""Deterministic Stage 4 workload contract for the Telegram delivery queue.

The module is side-effect free.  It creates the exact ten-minute business trace
consumed by the separately safety-gated staging harness and validates every
product quota before a driver can touch Telegram or staging state.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import random
from typing import Any, Iterable, Mapping, Sequence


STAGE4_DURATION_SECONDS = 600
STAGE4_OFFER_EXPIRY_SECONDS = 120
STAGE4_EVIDENCE_TIMELINE_SECONDS = (
    STAGE4_DURATION_SECONDS + STAGE4_OFFER_EXPIRY_SECONDS
)
STAGE4_VALID_OFFER_COUNT = 1800
STAGE4_INVALID_OFFER_COUNT = 400
STAGE4_OWNER_COUNT = 80
STAGE4_MINIMUM_ACTIVE_OWNERS = 70
STAGE4_MANUAL_EXPIRY_COUNT = 180
STAGE4_TRADE_TARGET_COUNT = 540
STAGE4_CONCURRENT_TRADE_OFFER_COUNT = 162
STAGE4_ADMIN_JOB_COUNT = 125
STAGE4_MARKET_NOTICE_COUNT = 2

LOT_SHAPE_QUOTAS = {"none": 900, "two": 450, "three": 450}
INVALID_FAMILY_QUOTAS = {
    "trade_and_settlement": 70,
    "commodity": 50,
    "quantity": 60,
    "price": 60,
    "lots": 120,
    "text_and_notes": 40,
}
CONCURRENT_REQUEST_QUOTAS = {2: 65, 3: 49, 4: 32, 5: 16}
TRADE_MODE_QUOTAS = {"normal": 270, "concurrent": 162, "partial_then_complete": 108}


class Stage4WorkloadValidationError(ValueError):
    """Raised before execution when a workload or fixture violates the contract."""


def stage4_authoritative_result_constraints(
    events: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return a reference state-machine contract for every trace event.

    Concurrent callers may win in any order, so the contract describes
    allowed per-event results plus group invariants instead of fabricating a
    winner from input ordering.  All other transitions have one reachable
    authoritative result.
    """
    rows = tuple(events)
    valid_by_offer = {
        str(row["business_event_id"]): row
        for row in rows
        if row.get("event_type") == "offer_submit_valid"
    }
    constraints: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_id = str(row["event_id"])
        event_type = str(row["event_type"])
        offer = valid_by_offer.get(str(row["business_event_id"]))
        allowed: tuple[str, ...]
        group_id: str | None = None
        group_rule: str | None = None
        terminal_results: tuple[str, ...] = ()
        if event_type == "offer_submit_valid":
            allowed = ("offer_accepted",)
        elif event_type == "offer_submit_invalid":
            allowed = ("offer_rejected",)
        elif event_type == "offer_confirm":
            allowed = ("confirmation_committed",)
        elif event_type == "admin_broadcast_delivery":
            allowed = ("delivery_requested",)
        elif event_type == "market_status_notice":
            allowed = ("notice_requested",)
        elif event_type == "automatic_expiry":
            allowed = ("expiry_committed",)
            terminal_results = ("expiry_committed",)
        elif event_type == "manual_expiry_request":
            if row.get("expiry_trade_race") is True:
                group_id = f"expiry-trade:{row['business_event_id']}"
                group_rule = str(row.get("race_expected_winner") or "")
                allowed = (
                    ("expiry_committed",)
                    if group_rule == "expiry"
                    else ("rejected_conflict",)
                )
            else:
                allowed = ("expiry_committed",)
            terminal_results = ("expiry_committed",)
        elif event_type == "trade_request" and offer is not None:
            mode = str(offer.get("trade_mode") or "")
            if mode == "partial_then_complete":
                allowed = (
                    ("partial_trade_committed",)
                    if row.get("partial") is True
                    else ("trade_committed",)
                )
            elif mode == "normal":
                allowed = ("trade_committed",)
            elif mode == "concurrent":
                group_id = (
                    f"expiry-trade:{row['business_event_id']}"
                    if row.get("expiry_trade_race") is True
                    else f"concurrent-trade:{row['business_event_id']}"
                )
                expected = str(row.get("race_expected_winner") or "")
                group_rule = expected or "exactly_one_trade"
                allowed = (
                    ("rejected_conflict",)
                    if expected == "expiry"
                    else ("trade_committed", "rejected_conflict")
                )
            else:
                raise Stage4WorkloadValidationError(
                    "stage4_trade_mode_reference_invalid"
                )
            terminal_results = ("trade_committed",)
        else:
            raise Stage4WorkloadValidationError(
                f"stage4_reference_event_type_invalid:{event_type}"
            )
        constraints[event_id] = {
            "allowed_authoritative_results": list(allowed),
            "authoritative_group_id": group_id,
            "authoritative_group_rule": group_rule,
            "terminal_authoritative_results": list(terminal_results),
        }
    return constraints


@dataclass(frozen=True, slots=True)
class Stage4Workload:
    seed: int
    events: tuple[dict[str, Any], ...]
    peak_windows: tuple[tuple[int, int], ...]
    trace_sha256: str
    fixture_sha256: str


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _fixture_commodities(fixture: Mapping[str, Any]) -> tuple[str, ...]:
    if int(fixture.get("schema_version", 0)) != 1:
        raise Stage4WorkloadValidationError("stage4_fixture_schema_invalid")
    raw = fixture.get("commodities")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise Stage4WorkloadValidationError("stage4_fixture_commodities_required")
    commodities: list[str] = []
    for row in raw:
        if not isinstance(row, Mapping) or row.get("active") is not True:
            continue
        key = str(row.get("key") or "").strip()
        if not key or len(key) > 80 or any(ord(char) < 32 for char in key):
            raise Stage4WorkloadValidationError("stage4_fixture_commodity_key_invalid")
        commodities.append(key)
    if not commodities or len(set(commodities)) != len(commodities):
        raise Stage4WorkloadValidationError(
            "stage4_fixture_active_commodities_missing_or_duplicate"
        )
    forbidden_fields = {
        "user_id",
        "actor_user_id",
        "telegram_id",
        "phone",
        "name",
        "notes",
        "channel_message_id",
        "offer_public_id",
        "idempotency_key",
    }
    rendered_keys = {
        str(key).lower()
        for row in raw
        if isinstance(row, Mapping)
        for key in row.keys()
    }
    if rendered_keys & forbidden_fields:
        raise Stage4WorkloadValidationError("stage4_fixture_contains_identity_field")
    return tuple(commodities)


def _peak_windows(seed: int) -> tuple[tuple[int, int], ...]:
    # Both halves contain consecutive 3-8 second peaks.  Small seed shifts keep
    # the three canonical traces distinct without changing the quota contract.
    shift = abs(int(seed)) % 5
    starts_and_lengths = (
        (25 + shift, 5),
        (88 - shift, 7),
        (151 + shift, 4),
        (236 - shift, 8),
        (326 + shift, 6),
        (407 - shift, 5),
        (492 + shift, 8),
        (558 - shift, 4),
    )
    return tuple((start, start + length) for start, length in starts_and_lengths)


def _valid_counts_by_second(
    rng: random.Random,
    peak_windows: Sequence[tuple[int, int]],
) -> list[int]:
    counts = [0] * STAGE4_DURATION_SECONDS
    peak_seconds = {
        second
        for start, end in peak_windows
        for second in range(start, end)
    }
    for second in peak_seconds:
        counts[second] = rng.randint(8, 12)
    remaining = STAGE4_VALID_OFFER_COUNT - sum(counts)
    normal_seconds = [
        second for second in range(STAGE4_DURATION_SECONDS) if second not in peak_seconds
    ]
    weights = []
    for second in normal_seconds:
        if second % 53 in range(0, 8):
            weights.append(0.25)  # quiet interval
        elif second % 47 in range(30, 39):
            weights.append(1.55)  # natural shoulder around activity
        else:
            weights.append(1.0)
    weight_total = sum(weights)
    fractional: list[tuple[float, int]] = []
    for second, weight in zip(normal_seconds, weights, strict=True):
        exact = remaining * weight / weight_total
        allocated = min(7, int(math.floor(exact)))
        counts[second] = allocated
        fractional.append((exact - allocated, second))
    unallocated = STAGE4_VALID_OFFER_COUNT - sum(counts)
    ranked = [second for _fraction, second in sorted(fractional, reverse=True)]
    cursor = 0
    while unallocated:
        second = ranked[cursor % len(ranked)]
        cursor += 1
        if counts[second] >= 7:
            continue
        counts[second] += 1
        unallocated -= 1
    if sum(counts) != STAGE4_VALID_OFFER_COUNT:
        raise Stage4WorkloadValidationError("stage4_valid_timeline_total_invalid")
    if any(counts[second] not in range(8, 13) for second in peak_seconds):
        raise Stage4WorkloadValidationError("stage4_peak_rate_invalid")
    return counts


def _expanded_quota(quota: Mapping[Any, int]) -> list[Any]:
    return [value for value, count in quota.items() for _ in range(int(count))]


def build_stage4_workload(
    *,
    seed: int,
    fixture: Mapping[str, Any],
) -> Stage4Workload:
    rng = random.Random(int(seed))
    commodities = _fixture_commodities(fixture)
    fixture_sha256 = hashlib.sha256(
        _canonical_json(fixture).encode("utf-8")
    ).hexdigest()
    peak_windows = _peak_windows(seed)
    counts_by_second = _valid_counts_by_second(rng, peak_windows)

    # Materialize submission instants before selecting lifecycle targets.  A
    # trade/expiry target must have enough timeline remaining for its dependent
    # event; clamping a late target to the end of the run could otherwise put a
    # dependent event before its own offer submission.
    submission_slots: list[int] = []
    for second, count in enumerate(counts_by_second):
        # Leave 700 ms for the confirmation event in the last second.
        offset_domain = (
            range(300)
            if second == STAGE4_DURATION_SECONDS - 1
            else range(1000)
        )
        submission_slots.extend(
            second * 1000 + offset
            for offset in sorted(rng.sample(offset_domain, count))
        )
    if len(submission_slots) != STAGE4_VALID_OFFER_COUNT:
        raise Stage4WorkloadValidationError("stage4_submission_slot_quota_mismatch")

    lot_shapes = _expanded_quota(LOT_SHAPE_QUOTAS)
    rng.shuffle(lot_shapes)
    offer_ids = [f"valid-{index + 1:04d}" for index in range(STAGE4_VALID_OFFER_COUNT)]
    lot_shape_by_offer = dict(zip(offer_ids, lot_shapes, strict=True))
    submission_at_by_offer = dict(zip(offer_ids, submission_slots, strict=True))
    lifecycle_eligible_ids = [
        offer_id
        for offer_id in offer_ids
        if submission_at_by_offer[offer_id] <= 480_000
    ]
    if len(lifecycle_eligible_ids) < STAGE4_TRADE_TARGET_COUNT:
        raise Stage4WorkloadValidationError("stage4_lifecycle_target_pool_too_small")
    # Partial-then-complete is meaningful only for multi-lot offers.  Select
    # that quota first, then draw disjoint normal/concurrent targets.
    partial_candidates = [
        offer_id
        for offer_id in lifecycle_eligible_ids
        if lot_shape_by_offer[offer_id] in {"two", "three"}
    ]
    rng.shuffle(partial_candidates)
    partial_ids = partial_candidates[
        : TRADE_MODE_QUOTAS["partial_then_complete"]
    ]
    if len(partial_ids) != TRADE_MODE_QUOTAS["partial_then_complete"]:
        raise Stage4WorkloadValidationError(
            "stage4_partial_trade_multilot_pool_too_small"
        )
    partial_id_set = set(partial_ids)
    remaining_trade_candidates = [
        offer_id
        for offer_id in lifecycle_eligible_ids
        if offer_id not in partial_id_set
    ]
    rng.shuffle(remaining_trade_candidates)
    normal_count = TRADE_MODE_QUOTAS["normal"]
    concurrent_count = TRADE_MODE_QUOTAS["concurrent"]
    trade_modes: dict[str, str] = {
        offer_id: "partial_then_complete" for offer_id in partial_ids
    }
    trade_modes.update(
        {
            offer_id: "normal"
            for offer_id in remaining_trade_candidates[:normal_count]
        }
    )
    trade_modes.update(
        {
            offer_id: "concurrent"
            for offer_id in remaining_trade_candidates[
                normal_count : normal_count + concurrent_count
            ]
        }
    )
    concurrent_ids = [
        offer_id for offer_id, mode in trade_modes.items() if mode == "concurrent"
    ]
    concurrent_counts = _expanded_quota(CONCURRENT_REQUEST_QUOTAS)
    rng.shuffle(concurrent_counts)
    concurrent_request_count = dict(zip(concurrent_ids, concurrent_counts, strict=True))

    # Ordinary manual expiry must never be assigned to a trade target.  The
    # only intentional expiry/trade overlap is the explicitly barriered race
    # population below.
    nontrade_ids = [
        offer_id
        for offer_id in lifecycle_eligible_ids
        if offer_id not in trade_modes
    ]
    rng.shuffle(nontrade_ids)
    if len(nontrade_ids) < 162:
        raise Stage4WorkloadValidationError(
            "stage4_manual_expiry_nontrade_pool_too_small"
        )
    concurrent_expiry_races = set(rng.sample(concurrent_ids, 18))
    race_expected_winner = {
        offer_id: ("trade" if index % 2 == 0 else "expiry")
        for index, offer_id in enumerate(sorted(concurrent_expiry_races))
    }
    manual_expiry_ids = set(nontrade_ids[:162]) | concurrent_expiry_races

    valid_events: list[dict[str, Any]] = []
    owner_cycle = [f"stage4-user-{index + 1:02d}" for index in range(STAGE4_OWNER_COUNT)]
    rng.shuffle(owner_cycle)
    combination_cycle = [
        (commodity, offer_type, settlement)
        for commodity in commodities
        for offer_type in ("buy", "sell")
        for settlement in ("cash", "tomorrow")
    ]
    for offer_index, at_ms in enumerate(submission_slots):
        offer_id = offer_ids[offer_index]
        commodity, offer_type, settlement = combination_cycle[
            offer_index % len(combination_cycle)
        ]
        lot_shape = lot_shapes[offer_index]
        owner = owner_cycle[offer_index % len(owner_cycle)]
        trade_mode = trade_modes.get(offer_id)
        event = {
            "event_id": f"submit-{offer_id}",
            "event_type": "offer_submit_valid",
            "at_ms": at_ms,
            "business_event_id": offer_id,
            "owner_key": owner,
            "commodity_key": commodity,
            "offer_type": offer_type,
            "settlement_type": settlement,
            "lot_shape": lot_shape,
            "lot_count": {"none": 1, "two": 2, "three": 3}[lot_shape],
            "trade_mode": trade_mode,
            "manual_expiry_requested": offer_id in manual_expiry_ids,
            "expiry_trade_race": offer_id in concurrent_expiry_races,
            "race_expected_winner": race_expected_winner.get(offer_id),
            "synthetic_notes": bool(offer_index % 3 == 0),
            "response_catalog_id": "offer.valid.preview",
        }
        valid_events.append(event)
        valid_events.append(
            {
                "event_id": f"confirm-{offer_id}",
                "event_type": "offer_confirm",
                "at_ms": event["at_ms"] + rng.randint(100, 700),
                "business_event_id": offer_id,
                "actor_key": owner,
                "response_catalog_id": "offer.valid.confirmed",
            }
        )

    by_offer = {
        row["business_event_id"]: row
        for row in valid_events
        if row["event_type"] == "offer_submit_valid"
    }
    lifecycle_events: list[dict[str, Any]] = []
    trade_release_at_by_offer: dict[str, int] = {}
    for offer_id, mode in trade_modes.items():
        submit = by_offer[offer_id]
        requester_count = concurrent_request_count.get(offer_id, 1)
        base_at = int(submit["at_ms"]) + rng.randint(5_000, 90_000)
        trade_release_at_by_offer[offer_id] = base_at
        race_winner = race_expected_winner.get(offer_id)
        trade_at = base_at + (25 if race_winner == "expiry" else 0)
        for request_index in range(requester_count):
            lifecycle_events.append(
                {
                    "event_id": f"trade-{offer_id}-{request_index + 1}",
                    "event_type": "trade_request",
                    "at_ms": trade_at,
                    "business_event_id": offer_id,
                    "actor_key": owner_cycle[
                        (offer_ids.index(offer_id) + request_index + 1)
                        % len(owner_cycle)
                    ],
                    "concurrency_barrier": (
                        (
                            f"expiry-trade-barrier-{offer_id}"
                            if offer_id in concurrent_expiry_races
                            else f"trade-barrier-{offer_id}"
                        )
                        if mode == "concurrent"
                        else None
                    ),
                    "race_expected_winner": race_winner,
                    "expiry_trade_race": offer_id in concurrent_expiry_races,
                    "partial": mode == "partial_then_complete",
                    "response_catalog_id": "trade.request.result",
                }
            )
        if mode == "partial_then_complete":
            lifecycle_events.append(
                {
                    "event_id": f"trade-{offer_id}-completion",
                    "event_type": "trade_request",
                    "at_ms": base_at + rng.randint(500, 5_000),
                    "business_event_id": offer_id,
                    "actor_key": owner_cycle[
                        (offer_ids.index(offer_id) + 2) % len(owner_cycle)
                    ],
                    "concurrency_barrier": None,
                    "partial": False,
                    "response_catalog_id": "trade.partial.completion",
                }
            )
    for offer_id in sorted(manual_expiry_ids):
        submit = by_offer[offer_id]
        race_winner = race_expected_winner.get(offer_id)
        expiry_at = (
            trade_release_at_by_offer[offer_id]
            + (25 if race_winner == "trade" else 0)
            if race_winner is not None
            else int(submit["at_ms"]) + rng.randint(10_000, 110_000)
        )
        lifecycle_events.append(
            {
                "event_id": f"manual-expiry-{offer_id}",
                "event_type": "manual_expiry_request",
                "at_ms": expiry_at,
                "business_event_id": offer_id,
                "actor_key": submit["owner_key"],
                "expiry_trade_race": offer_id in concurrent_expiry_races,
                "concurrency_barrier": (
                    f"expiry-trade-barrier-{offer_id}"
                    if race_winner is not None
                    else None
                ),
                "race_expected_winner": race_winner,
                "response_catalog_id": "offer.manual_expiry.result",
            }
        )

    # Every accepted offer has an explicit reachable terminal transition.
    # Offers completed by trade or manual expiry do not later receive an
    # automatic-expiry event.  Late submissions expire during the bounded
    # post-load drain window rather than being silently treated as terminal at
    # confirmation time.
    automatic_expiry_ids = [
        offer_id
        for offer_id in offer_ids
        if offer_id not in trade_modes and offer_id not in manual_expiry_ids
    ]
    lifecycle_events.extend(
        {
            "event_id": f"automatic-expiry-{offer_id}",
            "event_type": "automatic_expiry",
            "at_ms": int(by_offer[offer_id]["at_ms"])
            + STAGE4_OFFER_EXPIRY_SECONDS * 1000,
            "business_event_id": offer_id,
            "actor_key": "stage4-system-expiry",
            "response_catalog_id": "offer.automatic_expiry.result",
        }
        for offer_id in automatic_expiry_ids
    )

    invalid_families = _expanded_quota(INVALID_FAMILY_QUOTAS)
    rng.shuffle(invalid_families)
    invalid_events = [
        {
            "event_id": f"invalid-{index + 1:03d}",
            "event_type": "offer_submit_invalid",
            "at_ms": rng.randrange(STAGE4_DURATION_SECONDS * 1000),
            "business_event_id": f"invalid-{index + 1:03d}",
            "owner_key": owner_cycle[index % len(owner_cycle)],
            "invalid_family": family,
            "response_catalog_id": f"offer.invalid.{family}",
        }
        for index, family in enumerate(invalid_families)
    ]

    peak_seconds = [second for start, end in peak_windows for second in range(start, end)]
    burst_seconds = rng.sample(peak_seconds, 5)
    admin_events = [
        {
            "event_id": f"admin-{burst + 1}-{recipient + 1:02d}",
            "event_type": "admin_broadcast_delivery",
            "at_ms": burst_seconds[burst] * 1000 + recipient,
            "business_event_id": f"admin-burst-{burst + 1}",
            "recipient_key": owner_cycle[recipient % len(owner_cycle)],
            "response_catalog_id": "admin.broadcast.delivery",
        }
        for burst in range(5)
        for recipient in range(25)
    ]
    market_notice_events = [
        {
            "event_id": "market-transition-opened",
            "event_type": "market_status_notice",
            "at_ms": peak_windows[1][0] * 1000 + 50,
            "business_event_id": "market-transition-opened",
            "transition": "opened",
            "response_catalog_id": "market.channel.opened",
        },
        {
            "event_id": "market-transition-closed",
            "event_type": "market_status_notice",
            "at_ms": peak_windows[-2][0] * 1000 + 50,
            "business_event_id": "market-transition-closed",
            "transition": "closed",
            "response_catalog_id": "market.channel.closed",
        },
    ]

    events = tuple(
        sorted(
            (
                *valid_events,
                *invalid_events,
                *lifecycle_events,
                *admin_events,
                *market_notice_events,
            ),
            key=lambda row: (int(row["at_ms"]), str(row["event_id"])),
        )
    )
    trace_sha256 = hashlib.sha256(
        ("\n".join(_canonical_json(event) for event in events) + "\n").encode("utf-8")
    ).hexdigest()
    workload = Stage4Workload(
        seed=int(seed),
        events=events,
        peak_windows=tuple(peak_windows),
        trace_sha256=trace_sha256,
        fixture_sha256=fixture_sha256,
    )
    validate_stage4_workload(workload, commodities=commodities)
    return workload


def validate_stage4_workload(
    workload: Stage4Workload,
    *,
    commodities: Iterable[str],
) -> dict[str, Any]:
    events = tuple(workload.events)
    event_ids = [str(row.get("event_id") or "") for row in events]
    if not all(event_ids) or len(set(event_ids)) != len(event_ids):
        raise Stage4WorkloadValidationError("stage4_event_identity_invalid")
    canonical_trace = "\n".join(_canonical_json(event) for event in events) + "\n"
    if hashlib.sha256(canonical_trace.encode("utf-8")).hexdigest() != str(
        workload.trace_sha256
    ):
        raise Stage4WorkloadValidationError("stage4_trace_hash_mismatch")
    valid = [row for row in events if row.get("event_type") == "offer_submit_valid"]
    invalid = [row for row in events if row.get("event_type") == "offer_submit_invalid"]
    manual = [row for row in events if row.get("event_type") == "manual_expiry_request"]
    admin = [row for row in events if row.get("event_type") == "admin_broadcast_delivery"]
    market_notices = [
        row for row in events if row.get("event_type") == "market_status_notice"
    ]
    automatic_expiries = [
        row for row in events if row.get("event_type") == "automatic_expiry"
    ]
    trade_requests = [
        row for row in events if row.get("event_type") == "trade_request"
    ]
    if events != tuple(
        sorted(
            events,
            key=lambda row: (int(row["at_ms"]), str(row["event_id"])),
        )
    ):
        raise Stage4WorkloadValidationError("stage4_event_order_invalid")
    if len(valid) != STAGE4_VALID_OFFER_COUNT:
        raise Stage4WorkloadValidationError("stage4_valid_offer_quota_mismatch")
    if len(invalid) != STAGE4_INVALID_OFFER_COUNT:
        raise Stage4WorkloadValidationError("stage4_invalid_offer_quota_mismatch")
    if Counter(row["lot_shape"] for row in valid) != Counter(LOT_SHAPE_QUOTAS):
        raise Stage4WorkloadValidationError("stage4_lot_shape_quota_mismatch")
    if Counter(row["invalid_family"] for row in invalid) != Counter(INVALID_FAMILY_QUOTAS):
        raise Stage4WorkloadValidationError("stage4_invalid_family_quota_mismatch")
    if Counter(row.get("trade_mode") for row in valid if row.get("trade_mode")) != Counter(
        TRADE_MODE_QUOTAS
    ):
        raise Stage4WorkloadValidationError("stage4_trade_mode_quota_mismatch")
    if any(
        row.get("trade_mode") == "partial_then_complete"
        and int(row.get("lot_count", 0)) < 2
        for row in valid
    ):
        raise Stage4WorkloadValidationError(
            "stage4_partial_trade_requires_multiple_lots"
        )
    concurrent_requests = Counter(
        sum(
            1
            for row in events
            if row.get("event_type") == "trade_request"
            and row.get("business_event_id") == offer["business_event_id"]
        )
        for offer in valid
        if offer.get("trade_mode") == "concurrent"
    )
    if concurrent_requests != Counter(CONCURRENT_REQUEST_QUOTAS):
        raise Stage4WorkloadValidationError(
            "stage4_concurrent_request_quota_mismatch"
        )
    if len(manual) != STAGE4_MANUAL_EXPIRY_COUNT:
        raise Stage4WorkloadValidationError("stage4_manual_expiry_quota_mismatch")
    if sum(bool(row.get("expiry_trade_race")) for row in manual) != 18:
        raise Stage4WorkloadValidationError("stage4_expiry_race_quota_mismatch")
    valid_by_offer = {str(row["business_event_id"]): row for row in valid}
    if len(valid_by_offer) != len(valid):
        raise Stage4WorkloadValidationError("stage4_valid_business_identity_duplicate")
    confirmations = [row for row in events if row.get("event_type") == "offer_confirm"]
    if len(confirmations) != len(valid) or Counter(
        str(row["business_event_id"]) for row in confirmations
    ) != Counter(valid_by_offer.keys()):
        raise Stage4WorkloadValidationError("stage4_confirmation_coverage_mismatch")
    for row in (*confirmations, *manual):
        source = valid_by_offer.get(str(row["business_event_id"]))
        if source is None or int(row["at_ms"]) <= int(source["at_ms"]):
            raise Stage4WorkloadValidationError(
                "stage4_lifecycle_event_precedes_submission"
            )
    for row in trade_requests:
        source = valid_by_offer.get(str(row["business_event_id"]))
        if source is None or int(row["at_ms"]) <= int(source["at_ms"]):
            raise Stage4WorkloadValidationError(
                "stage4_trade_event_precedes_submission"
            )
    trade_offer_ids = {
        str(row["business_event_id"]) for row in trade_requests
    }
    manual_offer_ids = {str(row["business_event_id"]) for row in manual}
    race_manual = {
        str(row["business_event_id"]): row
        for row in manual
        if row.get("expiry_trade_race") is True
    }
    undeclared_overlap = (trade_offer_ids & manual_offer_ids) - set(race_manual)
    if undeclared_overlap:
        raise Stage4WorkloadValidationError(
            "stage4_undeclared_trade_expiry_overlap"
        )
    for offer_id, expiry in race_manual.items():
        race_trades = [
            row
            for row in trade_requests
            if str(row["business_event_id"]) == offer_id
        ]
        if len(race_trades) < 2:
            raise Stage4WorkloadValidationError(
                "stage4_expiry_race_competing_trade_count_invalid"
            )
        barrier = f"expiry-trade-barrier-{offer_id}"
        race_rows = [expiry, *race_trades]
        if any(row.get("concurrency_barrier") != barrier for row in race_rows):
            raise Stage4WorkloadValidationError(
                "stage4_expiry_race_barrier_mismatch"
            )
        planned_times = [int(row["at_ms"]) for row in race_rows]
        if max(planned_times) - min(planned_times) > 50:
            raise Stage4WorkloadValidationError(
                "stage4_expiry_race_planned_skew_exceeded"
            )
        expected_winners = {row.get("race_expected_winner") for row in race_rows}
        if expected_winners not in ({"trade"}, {"expiry"}):
            raise Stage4WorkloadValidationError(
                "stage4_expiry_race_expected_winner_invalid"
            )
    if len(admin) != STAGE4_ADMIN_JOB_COUNT:
        raise Stage4WorkloadValidationError("stage4_admin_job_quota_mismatch")
    if len(market_notices) != STAGE4_MARKET_NOTICE_COUNT or {
        row.get("transition") for row in market_notices
    } != {"opened", "closed"}:
        raise Stage4WorkloadValidationError("stage4_market_notice_quota_mismatch")
    expected_automatic_expiry_ids = set(valid_by_offer) - trade_offer_ids - manual_offer_ids
    if {
        str(row["business_event_id"]) for row in automatic_expiries
    } != expected_automatic_expiry_ids or any(
        int(row["at_ms"])
        != int(valid_by_offer[str(row["business_event_id"])]["at_ms"])
        + STAGE4_OFFER_EXPIRY_SECONDS * 1000
        for row in automatic_expiries
    ):
        raise Stage4WorkloadValidationError(
            "stage4_automatic_expiry_coverage_mismatch"
        )
    if len({str(row["business_event_id"]) for row in trade_requests}) != (
        STAGE4_TRADE_TARGET_COUNT
    ):
        raise Stage4WorkloadValidationError("stage4_trade_target_quota_mismatch")
    valid_rates = Counter(int(row["at_ms"]) // 1000 for row in valid)
    if any(
        not 8 <= valid_rates[second] <= 12
        for start, end in workload.peak_windows
        for second in range(start, end)
    ):
        raise Stage4WorkloadValidationError("stage4_peak_rate_mismatch")
    owners = {row["owner_key"] for row in (*valid, *invalid)}
    if len(owners) < STAGE4_MINIMUM_ACTIVE_OWNERS or len(owners) > STAGE4_OWNER_COUNT:
        raise Stage4WorkloadValidationError("stage4_owner_quota_mismatch")
    commodity_set = {str(value) for value in commodities}
    if {row["commodity_key"] for row in valid} != commodity_set:
        raise Stage4WorkloadValidationError("stage4_commodity_coverage_mismatch")
    combinations = Counter(
        (row["commodity_key"], row["offer_type"], row["settlement_type"])
        for row in valid
    )
    if any(
        combinations[(commodity, offer_type, settlement)] < 1
        for commodity in commodity_set
        for offer_type in ("buy", "sell")
        for settlement in ("cash", "tomorrow")
    ):
        raise Stage4WorkloadValidationError("stage4_combination_coverage_mismatch")
    if any(
        not 0 <= int(row["at_ms"]) < STAGE4_EVIDENCE_TIMELINE_SECONDS * 1000
        for row in events
    ):
        raise Stage4WorkloadValidationError("stage4_event_outside_timeline")
    reference_constraints = stage4_authoritative_result_constraints(events)
    if set(reference_constraints) != set(event_ids):
        raise Stage4WorkloadValidationError(
            "stage4_reference_state_machine_coverage_mismatch"
        )
    lifecycle_by_offer: dict[str, list[Mapping[str, Any]]] = {}
    for row in (*trade_requests, *manual, *automatic_expiries):
        lifecycle_by_offer.setdefault(str(row["business_event_id"]), []).append(row)
    for offer_id, source in valid_by_offer.items():
        lifecycle = lifecycle_by_offer.get(offer_id, [])
        mode = source.get("trade_mode")
        if not lifecycle:
            raise Stage4WorkloadValidationError(
                "stage4_offer_without_terminal_transition"
            )
        confirmation = next(
            row for row in confirmations if str(row["business_event_id"]) == offer_id
        )
        if any(int(row["at_ms"]) <= int(confirmation["at_ms"]) for row in lifecycle):
            raise Stage4WorkloadValidationError(
                "stage4_terminal_transition_precedes_confirmation"
            )
        if mode == "partial_then_complete":
            ordered_trades = sorted(
                (row for row in lifecycle if row["event_type"] == "trade_request"),
                key=lambda row: (int(row["at_ms"]), str(row["event_id"])),
            )
            if (
                len(ordered_trades) != 2
                or ordered_trades[0].get("partial") is not True
                or ordered_trades[1].get("partial") is not False
            ):
                raise Stage4WorkloadValidationError(
                    "stage4_partial_completion_sequence_invalid"
                )
    # This is a conservative upper bound: assume every accepted offer remains
    # active for the full two-minute staging lifetime.  Business trades and
    # manual expiry can only reduce the real active count.
    for owner in {row["owner_key"] for row in valid}:
        timestamps = sorted(
            int(row["at_ms"]) for row in valid if row["owner_key"] == owner
        )
        left = 0
        for right, timestamp in enumerate(timestamps):
            while timestamp - timestamps[left] >= 120_000:
                left += 1
            if right - left + 1 > 10:
                raise Stage4WorkloadValidationError(
                    "stage4_owner_active_offer_soft_guard_exceeded"
                )
    return {
        "valid_offers": len(valid),
        "invalid_offers": len(invalid),
        "lot_shapes": dict(sorted(Counter(row["lot_shape"] for row in valid).items())),
        "invalid_families": dict(
            sorted(Counter(row["invalid_family"] for row in invalid).items())
        ),
        "trade_modes": dict(
            sorted(
                Counter(row.get("trade_mode") for row in valid if row.get("trade_mode")).items()
            )
        ),
        "manual_expiry_requests": len(manual),
        "expiry_trade_races": sum(bool(row.get("expiry_trade_race")) for row in manual),
        "admin_jobs": len(admin),
        "market_notices": len(market_notices),
        "automatic_expiries": len(automatic_expiries),
        "active_owners": len(owners),
        "commodity_count": len(commodity_set),
        "event_count": len(events),
    }
