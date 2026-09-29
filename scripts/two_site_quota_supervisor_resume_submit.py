"""Dormant submission bridge for known-reset quota resume under Supervisor lock."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from scripts.two_site_quota_broker_client import enqueue_quota_continuation_locked
from scripts.two_site_quota_resume_claim_request import (
    compose_and_publish_known_reset_resume_request_locked,
)
from scripts.two_site_quota_resume_lineage_snapshot_storage import (
    publish_expected_lineage_snapshot,
)
from scripts.two_site_quota_supervisor_resume_context import (
    collect_known_reset_resume_context_locked,
)

__all__ = ["submit_known_reset_resume_once_locked"]


def submit_known_reset_resume_once_locked(
    root: Path,
    original_controller_run_id: str,
    continuation_controller_run_id: str,
    resume_request_id: str,
    claimed_at_utc: str,
    timeout: int,
    now_epoch: float,
) -> dict[str, Any]:
    """Execute one-shot known-reset resume context, claim, and enqueue under Supervisor lock."""
    ctx = collect_known_reset_resume_context_locked(
        root,
        original_controller_run_id,
        claimed_at_utc,
    )
    composed = compose_and_publish_known_reset_resume_request_locked(
        root,
        original_controller_run_id,
        ctx["wait_record"],
        ctx["expected_wait"],
        ctx["raw_wait_bytes"],
        ctx["semantic_attempt"],
        ctx["expected_task_id"],
        continuation_controller_run_id,
        resume_request_id,
        claimed_at_utc,
        timeout,
        now_epoch,
    )
    publish_expected_lineage_snapshot(
        root,
        original_controller_run_id,
        ctx["wait_record"],
        ctx["expected_wait"],
        composed["claim"],
        composed["expected_claim"],
        composed["request"],
    )
    intent = enqueue_quota_continuation_locked(
        root,
        composed["request"],
        now_epoch=now_epoch,
    )
    return {
        "claim": copy.deepcopy(composed["claim"]),
        "request": copy.deepcopy(composed["request"]),
        "submission_intent": copy.deepcopy(intent),
        "original_wait_sha256": composed["original_wait_sha256"],
    }
