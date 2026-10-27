"""Read-only commodity ranking from an atomically published rate snapshot."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from core.pack_commodities import (
    PACK_BASE_RATE_CODE_TO_COMMODITY_CODE,
    PACK_COMMODITY_NAME_BY_CODE,
)

from .coin_rate_engine import COIN_RATE_ENGINE_VERSION, COIN_SPECS
from .market_contracts import normalize_utc
from .market_snapshot import AtomicMarketSnapshotProvider, MarketSnapshotUnavailable, validate_market_snapshot
from .product_snapshot_reader import (
    ProductSnapshotReader,
    ProductSnapshotUnavailable,
)


COIN_INFERENCE_VERSION = "coin-inference-v4"
COIN_INFERENCE_NEARBY_PRICE_RANGE_PERCENT = 10
CANONICAL_COMMODITY_NAMES = {
    "IMAM": "امام",
    "BAHAR": "بهار",
    "HALF_BAHAR": "نیم بهار",
    "QUARTER_BAHAR": "ربع بهار",
    "HALF_LOW_DATE": "نیم تاریخ پایین",
    "QUARTER_LOW_DATE": "ربع تاریخ پایین",
    "ONE_GRAM": "یک گرمی",
    **PACK_COMMODITY_NAME_BY_CODE,
}

# A price can be uncertain between date variants of the *same denomination*,
# never between a full coin, half coin, quarter coin, or one-gram coin.  If a
# malformed/stale rate artifact creates a cross-denomination overlap, fail
# closed instead of presenting an implausible choice to the user.
COIN_CANDIDATE_FAMILY_BY_CODE = {
    "IMAM": "FULL",
    "BAHAR": "FULL",
    "HALF_BAHAR": "HALF",
    "HALF_LOW_DATE": "HALF",
    "QUARTER_BAHAR": "QUARTER",
    "QUARTER_LOW_DATE": "QUARTER",
    "ONE_GRAM": "ONE_GRAM",
    "PACK_FULL": "PACK",
    "PACK_HALF": "PACK",
    "PACK_QUARTER": "PACK",
}

# A standalone optional ``پ`` in an offer is a user-provided hint that the
# intended coin is a low-date variant.  The ranker must therefore never offer
# a normal-date candidate for that particular request.  The scope is recorded
# with the audit decision and applied again at final offer submission.
COIN_INFERENCE_CANDIDATE_SCOPE_ALL = "ALL"
COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY = "LOW_DATE_ONLY"
COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY = "PACK_ONLY"
COIN_INFERENCE_CANDIDATE_SCOPES = frozenset(
    {
        COIN_INFERENCE_CANDIDATE_SCOPE_ALL,
        COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
        COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
    }
)
COIN_LOW_DATE_COMMODITY_CODES = frozenset({"BAHAR", "HALF_LOW_DATE", "QUARTER_LOW_DATE"})


@dataclass(frozen=True, slots=True)
class CoinCommodityCandidate:
    commodity_code: str
    commodity_name: str
    center_project_price: int
    lower_project_price: int
    upper_project_price: int
    confidence: str
    distance_to_center_relative: float


@dataclass(frozen=True, slots=True)
class CoinCommodityInference:
    status: str
    settlement_term: str
    candidates: tuple[CoinCommodityCandidate, ...]
    snapshot_generated_at_utc: str | None
    snapshot_receipt: str | None
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "candidates": [asdict(item) for item in self.candidates],
        }


def _utc(value: datetime | str, *, name: str) -> datetime:
    normalized = normalize_utc(value, field_name=name)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _receipt(snapshot: Mapping[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(payload).hexdigest()


def normalize_coin_inference_candidate_scope(value: str | None) -> str:
    """Return one explicit candidate scope or reject an unknown constraint."""

    scope = str(value or COIN_INFERENCE_CANDIDATE_SCOPE_ALL).strip().upper()
    if scope not in COIN_INFERENCE_CANDIDATE_SCOPES:
        raise ValueError("coin_inference_candidate_scope_invalid")
    return scope


def _abstain(settlement: str, reason: str, snapshot: Mapping[str, Any] | None = None) -> CoinCommodityInference:
    return CoinCommodityInference(
        status="ABSTAIN",
        settlement_term=settlement,
        candidates=(),
        snapshot_generated_at_utc=(str(snapshot.get("generated_at_utc")) if snapshot else None),
        snapshot_receipt=(_receipt(snapshot) if snapshot else None),
        reason=reason,
    )


def infer_coin_commodity(
    snapshot: Mapping[str, Any],
    *,
    price_project_thousand_toman: int,
    settlement_term: str,
    now_utc: datetime | str,
    maximum_snapshot_age_seconds: int = 120,
    candidate_scope: str = COIN_INFERENCE_CANDIDATE_SCOPE_ALL,
) -> CoinCommodityInference:
    """Return AUTO_SELECT/CONFIRM/ABSTAIN; never a product database ID."""

    settlement = str(settlement_term or "").upper()
    if settlement not in {"CASH", "TOMORROW"}:
        raise ValueError("coin_inference_settlement_unsupported")
    try:
        price = int(price_project_thousand_toman)
    except (TypeError, ValueError) as exc:
        raise ValueError("coin_inference_price_invalid") from exc
    if price <= 0 or maximum_snapshot_age_seconds <= 0:
        raise ValueError("coin_inference_input_invalid")
    scope = normalize_coin_inference_candidate_scope(candidate_scope)
    try:
        validate_market_snapshot(snapshot)
    except Exception:
        return _abstain(settlement, "SNAPSHOT_INVALID")
    rates = snapshot.get("rates")
    if not isinstance(rates, Mapping) or str(rates.get("engine_version") or "") != COIN_RATE_ENGINE_VERSION:
        return _abstain(settlement, "SNAPSHOT_NOT_RATE_READY", snapshot)
    now = _utc(now_utc, name="coin_inference_now_utc")
    generated = _utc(str(snapshot.get("generated_at_utc") or ""), name="coin_inference_snapshot_generated_at_utc")
    age = (now - generated).total_seconds()
    if age < 0 or age > maximum_snapshot_age_seconds:
        return _abstain(settlement, "SNAPSHOT_STALE_OR_FUTURE", snapshot)
    published_candidates: list[CoinCommodityCandidate] = []
    for item in rates.get("items") or []:
        if not isinstance(item, Mapping) or item.get("status") != "ESTIMATED":
            continue
        rate_code = str(item.get("commodity_code") or "")
        if rate_code not in COIN_SPECS or str(item.get("settlement_term") or "") != settlement:
            continue
        if (
            scope == COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY
            and rate_code not in COIN_LOW_DATE_COMMODITY_CODES
        ):
            continue
        if scope == COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY:
            code = PACK_BASE_RATE_CODE_TO_COMMODITY_CODE.get(rate_code)
            if code is None:
                continue
        else:
            code = rate_code
        center = item.get("estimated_project_price")
        lower = item.get("lower_project_price")
        upper = item.get("upper_project_price")
        if not all(isinstance(value, int) and value > 0 for value in (center, lower, upper)):
            continue
        published_candidates.append(
            CoinCommodityCandidate(
                commodity_code=code,
                commodity_name=CANONICAL_COMMODITY_NAMES[code],
                center_project_price=int(center),
                lower_project_price=int(lower),
                upper_project_price=int(upper),
                confidence=str(item.get("confidence") or "NONE"),
                distance_to_center_relative=round(abs(price - int(center)) / int(center), 6),
            )
        )
    candidates = [
        candidate
        for candidate in published_candidates
        if candidate.lower_project_price <= price <= candidate.upper_project_price
    ]
    used_nearby_fallback = False
    if not candidates:
        nearby_candidates = [
            candidate
            for candidate in published_candidates
            if (
                abs(candidate.center_project_price - price) * 100
                <= price * COIN_INFERENCE_NEARBY_PRICE_RANGE_PERCENT
            )
        ]
        nearby_candidates.sort(
            key=lambda item: (
                abs(item.center_project_price - price),
                item.commodity_code,
            )
        )
        if not nearby_candidates:
            return _abstain(settlement, "PRICE_OUTSIDE_PUBLISHED_RANGES", snapshot)
        nearby_families = {
            COIN_CANDIDATE_FAMILY_BY_CODE[candidate.commodity_code]
            for candidate in nearby_candidates
        }
        if len(nearby_families) != 1:
            return _abstain(settlement, "CROSS_DENOMINATION_NEARBY_CANDIDATES", snapshot)
        nearest_distance = abs(nearby_candidates[0].center_project_price - price)
        equally_near = [
            candidate
            for candidate in nearby_candidates
            if abs(candidate.center_project_price - price) == nearest_distance
        ]
        # A unique nearest center is a suggestion, never an automatic choice.
        # If nearest centers tie, expose the complete nearby same-family set so
        # the user makes the decision explicitly.
        candidates = equally_near if len(equally_near) == 1 else nearby_candidates
        used_nearby_fallback = True
    candidates.sort(key=lambda item: (item.distance_to_center_relative, item.commodity_code))
    candidate_families = {
        COIN_CANDIDATE_FAMILY_BY_CODE[candidate.commodity_code]
        for candidate in candidates
    }
    if len(candidate_families) != 1:
        return _abstain(settlement, "CROSS_DENOMINATION_CANDIDATES", snapshot)
    # A unique high/medium rate can be selected; low paper fallback remains a
    # visible user confirmation until its production quality is demonstrated.
    status = (
        "AUTO_SELECT"
        if (
            not used_nearby_fallback
            and len(candidates) == 1
            and candidates[0].confidence in {"HIGH", "MEDIUM"}
        )
        else "CONFIRM"
    )
    reason = None
    if used_nearby_fallback:
        reason = "NEAREST_CENTER_FALLBACK_REQUIRES_CONFIRMATION"
    elif status == "CONFIRM":
        reason = "MULTIPLE_OR_LOW_CONFIDENCE_CANDIDATES"
    return CoinCommodityInference(
        status=status,
        settlement_term=settlement,
        candidates=tuple(candidates),
        snapshot_generated_at_utc=str(snapshot["generated_at_utc"]),
        snapshot_receipt=_receipt(snapshot),
        reason=reason,
    )


def infer_coin_commodity_from_published_snapshot(
    snapshot_path: Path | str,
    *,
    price_project_thousand_toman: int,
    settlement_term: str,
    now_utc: datetime | str,
    maximum_snapshot_age_seconds: int = 120,
    candidate_scope: str = COIN_INFERENCE_CANDIDATE_SCOPE_ALL,
    snapshot_reader: ProductSnapshotReader | None = None,
) -> CoinCommodityInference:
    """Load once atomically and rank against that exact immutable snapshot."""

    try:
        snapshot = (
            snapshot_reader.load(
                now_utc=_utc(now_utc, name="coin_inference_now_utc")
            ).snapshot
            if snapshot_reader is not None
            else AtomicMarketSnapshotProvider(snapshot_path).load()
        )
    except (ProductSnapshotUnavailable, MarketSnapshotUnavailable):
        settlement = str(settlement_term or "").upper()
        return _abstain(settlement, "SNAPSHOT_UNAVAILABLE")
    return infer_coin_commodity(
        snapshot,
        price_project_thousand_toman=price_project_thousand_toman,
        settlement_term=settlement_term,
        now_utc=now_utc,
        maximum_snapshot_age_seconds=(
            snapshot_reader.maximum_age_seconds
            if snapshot_reader is not None
            else maximum_snapshot_age_seconds
        ),
        candidate_scope=candidate_scope,
    )
