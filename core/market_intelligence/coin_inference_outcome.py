"""Append-only accepted-choice telemetry for the staged inference rollout."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.coin_intelligence_inference_outcome import CoinIntelligenceInferenceOutcome

from .coin_catalog import CatalogCoinCommodityCandidate
from .coin_inference import CANONICAL_COMMODITY_NAMES
from .coin_inference_audit import load_coin_inference_audit


OUTCOME_KIND_OFFER_ACCEPTED_SELECTION = "OFFER_ACCEPTED_SELECTION"
_KEY_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_SURFACES = frozenset({"WEBAPP", "TELEGRAM_BOT"})


class CoinInferenceOutcomeConflictError(ValueError):
    """A deterministic accepted-choice receipt was reused inconsistently."""


@dataclass(frozen=True, slots=True)
class CoinInferenceAcceptedSelection:
    """The minimum non-private fact needed for P7 rollout measurement."""

    decision_key: str
    source_surface: str
    candidate: CatalogCoinCommodityCandidate


def _normalized_key(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _KEY_PATTERN.fullmatch(normalized):
        raise ValueError(f"coin_inference_outcome_{field_name}_invalid")
    return normalized


def _normalized_surface(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in _SURFACES:
        raise ValueError("coin_inference_outcome_surface_invalid")
    return normalized


def _outcome_key(*, decision_key: str, source_surface: str, commodity_id: int) -> str:
    """Stable dedupe key derived only from opaque/product-safe values."""

    payload = "\x00".join(
        (
            "coin-inference-outcome-v1",
            decision_key,
            source_surface,
            OUTCOME_KIND_OFFER_ACCEPTED_SELECTION,
            str(commodity_id),
        )
    ).encode("ascii")
    return sha256(payload).hexdigest()


def _record_values(selection: CoinInferenceAcceptedSelection) -> dict[str, object]:
    decision_key = _normalized_key(selection.decision_key, field_name="decision_key")
    source_surface = _normalized_surface(selection.source_surface)
    candidate = selection.candidate
    try:
        commodity_id = int(candidate.commodity_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("coin_inference_outcome_commodity_invalid") from exc
    if commodity_id <= 0 or CANONICAL_COMMODITY_NAMES.get(candidate.commodity_code) != candidate.commodity_name:
        raise ValueError("coin_inference_outcome_catalog_candidate_invalid")
    return {
        "outcome_key": _outcome_key(
            decision_key=decision_key,
            source_surface=source_surface,
            commodity_id=commodity_id,
        ),
        "decision_key": decision_key,
        "source_surface": source_surface,
        "outcome_kind": OUTCOME_KIND_OFFER_ACCEPTED_SELECTION,
        "selected_commodity_id": commodity_id,
        "selected_commodity_code": candidate.commodity_code,
        "selected_commodity_name": candidate.commodity_name,
    }


def _same_record(record: CoinIntelligenceInferenceOutcome, values: dict[str, object]) -> bool:
    return all(
        getattr(record, field) == values[field]
        for field in (
            "decision_key",
            "source_surface",
            "outcome_kind",
            "selected_commodity_id",
            "selected_commodity_code",
            "selected_commodity_name",
        )
    )


async def append_coin_inference_accepted_selection(
    db: AsyncSession,
    selection: CoinInferenceAcceptedSelection,
) -> CoinIntelligenceInferenceOutcome:
    """Append or exactly replay an accepted inferred commodity selection.

    This function deliberately does not create an Offer and owns no commit.
    Call it only after the authoritative Offer has been accepted.  The caller
    may treat a telemetry failure as non-fatal after that product commit.
    """

    values = _record_values(selection)
    audit = await load_coin_inference_audit(db, decision_key=str(values["decision_key"]))
    if audit is None:
        raise ValueError("coin_inference_outcome_decision_unknown")
    if str(getattr(audit, "source_surface", "")).upper() != values["source_surface"]:
        raise ValueError("coin_inference_outcome_surface_mismatch")
    if str(getattr(audit, "decision_status", "")) not in {"AUTO_SELECT", "CONFIRM"}:
        raise ValueError("coin_inference_outcome_decision_not_selectable")
    if (
        str(getattr(audit, "decision_status", "")) == "AUTO_SELECT"
        and int(getattr(audit, "selected_commodity_id", 0) or 0) != int(values["selected_commodity_id"])
    ):
        raise ValueError("coin_inference_outcome_auto_choice_mismatch")

    result = await db.execute(
        select(CoinIntelligenceInferenceOutcome).where(
            CoinIntelligenceInferenceOutcome.outcome_key == values["outcome_key"]
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        if not _same_record(existing, values):
            raise CoinInferenceOutcomeConflictError("coin_inference_outcome_idempotency_conflict")
        return existing
    record = CoinIntelligenceInferenceOutcome(**values)
    db.add(record)
    await db.flush()
    return record


__all__ = [
    "OUTCOME_KIND_OFFER_ACCEPTED_SELECTION",
    "CoinInferenceAcceptedSelection",
    "CoinInferenceOutcomeConflictError",
    "append_coin_inference_accepted_selection",
]
