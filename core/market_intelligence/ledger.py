"""Persistence boundary for local, non-authoritative Shadow evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, Sequence
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.config import settings
from core.db import AsyncSessionLocal
from core.market_intelligence.contracts import (
    RateShadowPrediction,
    ShadowObservation,
)
from models.coin_intelligence_shadow import (
    CoinIntelligenceShadowFeatureSnapshot,
    CoinIntelligenceShadowOutcome,
    CoinIntelligenceShadowParserResult,
    CoinIntelligenceShadowPrediction,
    CoinIntelligenceShadowQualityDecision,
    CoinIntelligenceShadowReview,
    CoinIntelligenceShadowRun,
)
from core.market_intelligence.quality import (
    QUALITY_POLICY_VERSION,
    REVIEW_ACTIONS,
    QualityDecision,
)


SCORING_POLICY_VERSION = "COIN_SHADOW_SCORING_V1"


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def shadow_subject_fingerprint(kind: str, stable_identifier: str) -> str:
    """Create an opaque local link without persisting a business identifier."""

    normalized_kind = str(kind).strip().upper()
    normalized_identifier = str(stable_identifier).strip()
    if not normalized_kind or not normalized_identifier:
        raise ValueError("shadow subject identity is incomplete")
    return hashlib.sha256(
        f"coin-shadow-v1:{normalized_kind}:{normalized_identifier}".encode(
            "utf-8"
        )
    ).hexdigest()


def _idempotency_key(*parts: object) -> str:
    payload = json.dumps(
        [str(part) for part in parts],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _site_name() -> str | None:
    return (
        str(settings.physical_site).strip()
        if settings.physical_site
        else str(settings.server_mode or "").strip() or None
    )


async def _existing_run_id(session, idempotency_key: str) -> str | None:
    value = await session.scalar(
        select(CoinIntelligenceShadowRun.id).where(
            CoinIntelligenceShadowRun.idempotency_key == idempotency_key
        )
    )
    return str(value) if value is not None else None


async def _commit_run_idempotently(
    session,
    *,
    run_id: str,
    idempotency_key: str,
) -> str:
    try:
        await session.commit()
        return run_id
    except IntegrityError:
        # A concurrent duplicate may win the unique constraint.  Roll back
        # this independent Shadow transaction and return the immutable winner.
        await session.rollback()
        existing = await _existing_run_id(session, idempotency_key)
        if existing is not None:
            return existing
        raise


async def _flush_new_run_idempotently(
    session,
    *,
    idempotency_key: str,
) -> str | None:
    """Flush the parent first for FK order and resolve a concurrent replay."""

    try:
        await session.flush()
        return None
    except IntegrityError:
        await session.rollback()
        existing = await _existing_run_id(session, idempotency_key)
        if existing is not None:
            return existing
        raise


async def persist_parser_observation(
    observation: ShadowObservation,
    *,
    requested_at: datetime | None = None,
    latency_ms: int | None = None,
    source_surface: str = "offer_parser",
    subject_kind: str | None = None,
    subject_fingerprint: str | None = None,
    idempotency_material: str | None = None,
    component: str = "COMMODITY_RANKER",
    candidate_payload: dict | None = None,
    disagreement_fields: Sequence[str] | None = None,
    session_factory: Callable = AsyncSessionLocal,
) -> str:
    """Append one parser comparison in an independent transaction."""

    as_of = _utc(requested_at)
    unique_material = idempotency_material or uuid4().hex
    run_id = str(uuid4())
    idempotency_key = _idempotency_key(
        "parser",
        unique_material,
    )
    run = CoinIntelligenceShadowRun(
        id=run_id,
        idempotency_key=idempotency_key,
        component=str(component).upper()[:64],
        status=str(observation.status)[:40],
        source_surface=str(source_surface)[:32],
        subject_kind=(
            str(subject_kind).upper()[:24] if subject_kind else None
        ),
        subject_fingerprint=subject_fingerprint,
        as_of_utc=as_of,
        physical_site=_site_name(),
        candidate_version=observation.bundle_version,
        snapshot_version=observation.snapshot_version,
        latency_ms=max(0, int(latency_ms)) if latency_ms is not None else None,
        training_eligible=False,
        error_code=(
            None
            if observation.status in {"INFERRED", "AMBIGUOUS"}
            else str(observation.decision_reason)[:96]
        ),
    )
    parser_result = CoinIntelligenceShadowParserResult(
        run_id=run_id,
        current_commodity=str(observation.current_commodity)[:80],
        candidate_commodity=(
            str(observation.inferred_commodity)[:80]
            if observation.inferred_commodity is not None
            else None
        ),
        agrees_with_current=observation.agrees_with_current,
        requires_user_confirmation=bool(
            observation.requires_user_confirmation
        ),
        decision_reason=str(observation.decision_reason)[:160],
        validator_status=str(observation.status)[:40],
        disagreement_fields=(
            [str(item)[:32] for item in disagreement_fields]
            if disagreement_fields is not None
            else (
                ["commodity"]
                if observation.agrees_with_current is False
                else []
            )
        ),
        candidate_payload=dict(candidate_payload or {}),
    )
    async with session_factory() as session:
        existing = await _existing_run_id(session, idempotency_key)
        if existing is not None:
            return existing
        session.add(run)
        concurrent_winner = await _flush_new_run_idempotently(
            session,
            idempotency_key=idempotency_key,
        )
        if concurrent_winner is not None:
            return concurrent_winner
        session.add(parser_result)
        return await _commit_run_idempotently(
            session,
            run_id=run_id,
            idempotency_key=idempotency_key,
        )


async def persist_rate_prediction(
    prediction: RateShadowPrediction,
    *,
    commodity_id: int | None,
    requested_at: datetime,
    source_surface: str,
    subject_kind: str,
    subject_fingerprint: str,
    event_idempotency_key: str,
    observed_project_price: int | None = None,
    latency_ms: int | None = None,
    candidate_predictions: Sequence[RateShadowPrediction] = (),
    quality_decision: QualityDecision | None = None,
    session_factory: Callable = AsyncSessionLocal,
) -> str:
    """Append one current-range observation for later outcome scoring."""

    run_id = str(uuid4())
    idempotency_key = _idempotency_key(
        "rate",
        event_idempotency_key,
    )
    run = CoinIntelligenceShadowRun(
        id=run_id,
        idempotency_key=idempotency_key,
        component="COIN_RANGE_ESTIMATOR",
        status=str(prediction.status)[:40],
        source_surface=str(source_surface)[:32],
        subject_kind=str(subject_kind).upper()[:24],
        subject_fingerprint=subject_fingerprint,
        as_of_utc=_utc(requested_at),
        physical_site=_site_name(),
        primary_version=prediction.bundle_version,
        candidate_version=(
            ",".join(
                sorted(
                    {
                        str(item.bundle_version)
                        for item in candidate_predictions
                        if item.bundle_version
                    }
                )
            )[:160]
            or None
        ),
        feature_schema_version=prediction.feature_schema_version,
        snapshot_version=prediction.snapshot_version,
        latency_ms=max(0, int(latency_ms)) if latency_ms is not None else None,
        training_eligible=False,
        error_code=(
            None
            if prediction.status == "ESTIMATED"
            else str(prediction.decision_reason)[:96]
        ),
    )
    diagnostics = {}
    if observed_project_price is not None:
        # This is context only for an offer and never a training outcome.
        diagnostics["observed_offer_price_project"] = int(
            observed_project_price
        )
    row = CoinIntelligenceShadowPrediction(
        run_id=run_id,
        model_role="PRIMARY_SHADOW",
        candidate_name="CURRENT_DETERMINISTIC_RANGE",
        commodity_id=commodity_id,
        canonical_commodity=str(prediction.commodity)[:80],
        settlement=str(prediction.settlement)[:16],
        trade_form=str(prediction.trade_form)[:16],
        center_project_price=prediction.center_project_price,
        lower_project_price=prediction.lower_project_price,
        upper_project_price=prediction.upper_project_price,
        confidence_label=(
            str(prediction.confidence_label)[:24]
            if prediction.confidence_label
            else None
        ),
        method=(
            str(prediction.method)[:160] if prediction.method else None
        ),
        gate_reason=str(prediction.decision_reason)[:160],
        anchor_kind=(
            str(prediction.anchor_kind)[:64]
            if prediction.anchor_kind
            else None
        ),
        anchor_age_seconds=prediction.anchor_age_seconds,
        is_authoritative=False,
        diagnostics=diagnostics,
    )
    candidate_rows = []
    for candidate in candidate_predictions:
        method = str(candidate.method or "")
        if method.startswith("HYBRID_V2"):
            candidate_name = "HYBRID_V2"
        elif method.startswith("LOW_DATE_"):
            candidate_name = "LOW_DATE_PHYSICAL_V2"
        elif method.startswith("CASH_TOMORROW_BASIS_"):
            candidate_name = "CASH_TOMORROW_BASIS_V2"
        else:
            candidate_name = str(
                candidate.bundle_version or "UNNAMED_CANDIDATE"
            )[:64]
        candidate_rows.append(
            CoinIntelligenceShadowPrediction(
                run_id=run_id,
                model_role="CANDIDATE_SHADOW",
                candidate_name=candidate_name,
                commodity_id=commodity_id,
                canonical_commodity=str(candidate.commodity)[:80],
                settlement=str(candidate.settlement)[:16],
                trade_form=str(candidate.trade_form)[:16],
                center_project_price=candidate.center_project_price,
                lower_project_price=candidate.lower_project_price,
                upper_project_price=candidate.upper_project_price,
                confidence_label=(
                    str(candidate.confidence_label)[:24]
                    if candidate.confidence_label
                    else None
                ),
                method=(
                    str(candidate.method)[:160]
                    if candidate.method
                    else None
                ),
                gate_reason=str(candidate.decision_reason)[:160],
                anchor_kind=(
                    str(candidate.anchor_kind)[:64]
                    if candidate.anchor_kind
                    else None
                ),
                anchor_age_seconds=candidate.anchor_age_seconds,
                is_authoritative=False,
                diagnostics={
                    "status": str(candidate.status),
                    "evidence": dict(candidate.evidence or {}),
                },
            )
        )
    feature_row = None
    if prediction.evidence:
        canonical_evidence = json.dumps(
            prediction.evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sources = prediction.evidence.get("sources") or {}
        feature_row = CoinIntelligenceShadowFeatureSnapshot(
            run_id=run_id,
            schema_version=str(
                prediction.evidence.get("schema_version")
                or prediction.feature_schema_version
                or "UNKNOWN"
            )[:160],
            payload_sha256=hashlib.sha256(
                canonical_evidence.encode("utf-8")
            ).hexdigest(),
            features=dict(prediction.evidence),
            source_ages={
                str(name): value.get("age_seconds")
                for name, value in sources.items()
                if isinstance(value, dict)
            },
            missing_fields=(
                [
                    str(name)
                    for name in prediction.evidence.get(
                        "missing_fields_v2"
                    )
                ]
                if isinstance(
                    prediction.evidence.get("missing_fields_v2"),
                    list,
                )
                else [
                    str(name)
                    for name, value in sources.items()
                    if not isinstance(value, dict)
                    or str(value.get("status") or "NO_DATA")
                    != "OBSERVED"
                ]
            ),
            source_vintages={
                str(name): {
                    "selection": value.get("selection"),
                    "quote_kind": value.get("quote_kind"),
                    "trade_form": value.get("trade_form"),
                }
                for name, value in sources.items()
                if isinstance(value, dict)
            },
        )
    quality_row = None
    if quality_decision is not None:
        quality_row = CoinIntelligenceShadowQualityDecision(
            run_id=run_id,
            policy_version=QUALITY_POLICY_VERSION,
            decision=str(quality_decision.decision)[:32],
            reason_codes=[
                str(item)[:96] for item in quality_decision.reason_codes
            ],
            realtime_weight=max(
                0.0,
                min(1.0, float(quality_decision.realtime_weight)),
            ),
            training_weight=max(
                0.0,
                min(1.0, float(quality_decision.training_weight)),
            ),
            review_required=bool(quality_decision.review_required),
            context=dict(quality_decision.context),
        )
    async with session_factory() as session:
        existing = await _existing_run_id(session, idempotency_key)
        if existing is not None:
            return existing
        session.add(run)
        concurrent_winner = await _flush_new_run_idempotently(
            session,
            idempotency_key=idempotency_key,
        )
        if concurrent_winner is not None:
            return concurrent_winner
        session.add(row)
        for candidate_row in candidate_rows:
            session.add(candidate_row)
        if feature_row is not None:
            session.add(feature_row)
        if quality_row is not None:
            session.add(quality_row)
        return await _commit_run_idempotently(
            session,
            run_id=run_id,
            idempotency_key=idempotency_key,
        )


async def attach_confirmed_trade_outcomes(
    *,
    offer_subject_fingerprint: str,
    actual_commodity_id: int,
    settlement: str,
    trade_form: str,
    actual_project_price: int,
    occurred_at_utc: datetime,
    label_status: str = "UNREVIEWED",
    review_reason: str | None = None,
    session_factory: Callable = AsyncSessionLocal,
) -> int:
    """Score the latest strictly-prior offer run for every candidate role."""

    occurred_at = _utc(occurred_at_utc)
    expected_commodity_id = int(actual_commodity_id)
    if expected_commodity_id <= 0:
        raise ValueError("actual commodity id must be positive")
    expected_settlement = str(settlement).upper()[:16]
    expected_trade_form = str(trade_form).upper()[:16]
    actual = int(actual_project_price)
    if actual <= 0:
        raise ValueError("actual project price must be positive")
    async with session_factory() as session:
        latest_run_id = await session.scalar(
            select(CoinIntelligenceShadowRun.id)
            .where(
                CoinIntelligenceShadowRun.subject_kind == "OFFER",
                CoinIntelligenceShadowRun.subject_fingerprint
                == offer_subject_fingerprint,
                CoinIntelligenceShadowRun.as_of_utc < occurred_at,
            )
            .order_by(CoinIntelligenceShadowRun.as_of_utc.desc())
            .limit(1)
        )
        if latest_run_id is None:
            return 0
        predictions = (
            await session.execute(
                select(CoinIntelligenceShadowPrediction).where(
                    CoinIntelligenceShadowPrediction.run_id == latest_run_id,
                    CoinIntelligenceShadowPrediction.commodity_id
                    == expected_commodity_id,
                    CoinIntelligenceShadowPrediction.settlement
                    == expected_settlement,
                    CoinIntelligenceShadowPrediction.trade_form
                    == expected_trade_form,
                )
            )
        ).scalars().all()
        created = 0
        for prediction in predictions:
            existing = await session.scalar(
                select(CoinIntelligenceShadowOutcome.id).where(
                    CoinIntelligenceShadowOutcome.prediction_id
                    == prediction.id,
                    CoinIntelligenceShadowOutcome.outcome_version == 1,
                )
            )
            if existing is not None:
                continue
            center = prediction.center_project_price
            signed_error = (
                (float(center) - actual) / actual * 100.0
                if center is not None
                else None
            )
            covered = (
                int(prediction.lower_project_price)
                <= actual
                <= int(prediction.upper_project_price)
                if prediction.lower_project_price is not None
                and prediction.upper_project_price is not None
                else None
            )
            session.add(
                CoinIntelligenceShadowOutcome(
                    prediction_id=prediction.id,
                    outcome_version=1,
                    subject_fingerprint=offer_subject_fingerprint,
                    actual_commodity_id=expected_commodity_id,
                    settlement=expected_settlement,
                    trade_form=expected_trade_form,
                    actual_project_price=actual,
                    occurred_at_utc=occurred_at,
                    label_status=str(label_status).upper()[:32],
                    review_reason=(
                        str(review_reason)[:160] if review_reason else None
                    ),
                    absolute_percent_error=(
                        abs(signed_error)
                        if signed_error is not None
                        else None
                    ),
                    signed_percent_error=signed_error,
                    interval_covered=covered,
                    scoring_policy_version=SCORING_POLICY_VERSION,
                )
            )
            created += 1
        if created:
            await session.commit()
        return created


async def append_outcome_review(
    *,
    outcome_id: int,
    action: str,
    reviewer_stable_identifier: str,
    reason_code: str,
    corrected_project_price: int | None = None,
    note_code: str | None = None,
    session_factory: Callable = AsyncSessionLocal,
) -> int:
    """Append coded review evidence without mutating the outcome or run."""

    normalized_action = str(action).strip().upper()
    if normalized_action not in REVIEW_ACTIONS:
        raise ValueError("unsupported shadow review action")
    normalized_reason = str(reason_code).strip().upper()
    if not normalized_reason:
        raise ValueError("shadow review reason is required")
    corrected = (
        int(corrected_project_price)
        if corrected_project_price is not None
        else None
    )
    if normalized_action == "ACCEPT_CORRECTION":
        if corrected is None or corrected <= 0:
            raise ValueError("correction review requires positive price")
    elif corrected is not None:
        raise ValueError("corrected price only valid for correction review")
    reviewer = shadow_subject_fingerprint(
        "SHADOW_REVIEWER",
        reviewer_stable_identifier,
    )
    async with session_factory() as session:
        existing_outcome = await session.scalar(
            select(CoinIntelligenceShadowOutcome.id).where(
                CoinIntelligenceShadowOutcome.id == int(outcome_id)
            ).with_for_update()
        )
        if existing_outcome is None:
            raise ValueError("shadow outcome not found")
        current_version = await session.scalar(
            select(
                func.max(CoinIntelligenceShadowReview.review_version)
            ).where(
                CoinIntelligenceShadowReview.outcome_id == int(outcome_id)
            )
        )
        version = int(current_version or 0) + 1
        session.add(
            CoinIntelligenceShadowReview(
                outcome_id=int(outcome_id),
                review_version=version,
                action=normalized_action,
                reviewer_fingerprint=reviewer,
                corrected_project_price=corrected,
                reason_code=normalized_reason[:96],
                note_code=(
                    str(note_code).strip().upper()[:96]
                    if note_code
                    else None
                ),
            )
        )
        await session.commit()
        return version
