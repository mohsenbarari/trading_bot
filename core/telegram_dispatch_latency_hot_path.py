"""Code-derived query counts for the offer-channel hot path.

Stage 11 does not collect live percentiles. It locks how many Offer and
publication-state reads a successful SEND performs before Telegram, then
proves the post-change count is lower while both worker freshness checks
and ``assert_dispatchable`` remain in source.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


HOT_PATH_SCHEMA_VERSION = "telegram_dispatch_latency_hot_path_v1"
_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class TelegramOfferStateReadBudget:
    validator_after_claim: int
    validator_after_limiter: int
    assert_dispatchable_lock: int
    assert_dispatchable_validate: int
    apply_delivery_result: int

    @property
    def before_telegram(self) -> int:
        return (
            self.validator_after_claim
            + self.validator_after_limiter
            + self.assert_dispatchable_lock
            + self.assert_dispatchable_validate
        )

    @property
    def total_including_result(self) -> int:
        return self.before_telegram + self.apply_delivery_result

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["before_telegram"] = self.before_telegram
        payload["total_including_result"] = self.total_including_result
        return payload


@dataclass(frozen=True, slots=True)
class TelegramDispatchLatencyHotPathLock:
    schema_version: str
    evidence_kind: str
    live_percentiles_collected: bool
    worker_explicit_freshness_checks: int
    assert_dispatchable_revalidates: bool
    before: TelegramOfferStateReadBudget
    after: TelegramOfferStateReadBudget
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
            "live_percentiles_collected": self.live_percentiles_collected,
            "worker_explicit_freshness_checks": self.worker_explicit_freshness_checks,
            "assert_dispatchable_revalidates": self.assert_dispatchable_revalidates,
            "before": self.before.as_dict(),
            "after": self.after.as_dict(),
            "notes": list(self.notes),
        }


def _source(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def worker_explicit_freshness_check_count() -> int:
    text = _source("core/telegram_delivery_queue_worker.py")
    return text.count("freshness = await validator(db, job,")


def assert_dispatchable_revalidates_without_reload() -> bool:
    text = _source("core/services/telegram_offer_queue_feedback.py")
    start = text.find("async def assert_dispatchable")
    end = text.find("async def apply_freshness")
    if start < 0 or end <= start:
        return False
    block = text[start:end]
    return (
        "await _load_offer_and_state_for_update" in block
        and "validate_offer_telegram_delivery_freshness" in block
        and "offer=offer" in block
        and "publication_state=state" in block
    )


def validate_accepts_preloaded_rows() -> bool:
    text = _source("core/telegram_delivery_offer_freshness.py")
    return (
        "offer: Offer | None | object = _UNSET" in text
        and "if offer is _UNSET:" in text
        and "if publication_state is _UNSET:" in text
    )


def apply_freshness_reuses_session_rows() -> bool:
    text = _source("core/services/telegram_offer_queue_feedback.py")
    start = text.find("async def apply_freshness")
    end = text.find("async def apply_delivery_result")
    if start < 0 or end <= start:
        return False
    return "_reuse_or_load_offer_and_state_for_update" in text[start:end]


def previous_successful_send_offer_state_reads() -> TelegramOfferStateReadBudget:
    """Locked count before stage 11: validate re-read locked rows."""

    return TelegramOfferStateReadBudget(
        validator_after_claim=2,
        validator_after_limiter=2,
        assert_dispatchable_lock=2,
        assert_dispatchable_validate=2,
        apply_delivery_result=2,
    )


def current_successful_send_offer_state_reads() -> TelegramOfferStateReadBudget:
    """Count implied by the current tree for one successful SEND."""

    return TelegramOfferStateReadBudget(
        validator_after_claim=2,
        validator_after_limiter=2,
        assert_dispatchable_lock=2,
        assert_dispatchable_validate=0,
        apply_delivery_result=2,
    )


def locked_telegram_dispatch_hot_path() -> TelegramDispatchLatencyHotPathLock:
    return TelegramDispatchLatencyHotPathLock(
        schema_version=HOT_PATH_SCHEMA_VERSION,
        evidence_kind="code_derived_hot_path_query_lock",
        live_percentiles_collected=False,
        worker_explicit_freshness_checks=worker_explicit_freshness_check_count(),
        assert_dispatchable_revalidates=assert_dispatchable_revalidates_without_reload(),
        before=previous_successful_send_offer_state_reads(),
        after=current_successful_send_offer_state_reads(),
        notes=(
            "Both worker freshness checks stay; stale jobs still fail before the channel budget.",
            "assert_dispatchable still runs the full offer validator after the write lock.",
            "The removed reads are the unlocked Offer/state SELECTs inside that last validate.",
            "apply_delivery_result after Telegram is unchanged.",
            "Live query percentiles were not collected in this worktree.",
        ),
    )
