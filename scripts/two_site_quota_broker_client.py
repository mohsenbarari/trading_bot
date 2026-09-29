"""Minimal quota continuation broker client."""

from __future__ import annotations

import math
from pathlib import Path
import time

from scripts import two_site_provider_broker as broker
from scripts.two_site_quota_broker_route import _check_pre_dispatch_stop
from scripts.two_site_quota_resume_submission_intent import (
    publish_quota_resume_submission_intent,
)

__all__ = ["enqueue_quota_continuation_locked"]


def enqueue_quota_continuation_locked(
    root: Path,
    request: dict,
    *,
    now_epoch: float | None = None,
) -> dict:
    """Enqueue an admitted quota continuation request under client lock.

    Post-intent crash is ambiguous and manual-only.
    """
    root = broker.canonical_root(root)
    state_root, status_path, requests, _, _ = broker.broker_paths(root)
    with broker.exclusive_broker_lock(state_root / "client.lock"):
        if now_epoch is not None:
            if type(now_epoch) is bool or not isinstance(now_epoch, (int, float)) or not math.isfinite(now_epoch):
                raise broker.BrokerError("now_epoch must be finite numeric nonbool")

        status = broker._read_broker_status(root, status_path)
        if (
            not isinstance(status, dict)
            or status.get("state") != "IDLE"
            or status.get("quota_continuation_ready") is not True
            or type(status.get("pid")) is not int
        ):
            raise broker.BrokerError("broker status is not ready idle")

        updated_at = status.get("updated_at")
        if type(updated_at) is bool or not isinstance(updated_at, (int, float)) or not math.isfinite(updated_at):
            raise broker.BrokerError("broker status updated_at must be finite numeric nonbool")

        current_time = time.time() if now_epoch is None else float(now_epoch)
        age = current_time - updated_at
        if not (0 <= age <= broker.STATUS_MAX_AGE_SECONDS):
            raise broker.BrokerError("broker status is not fresh")

        _check_pre_dispatch_stop(root)

        if now_epoch is None:
            intent = publish_quota_resume_submission_intent(root, request)
        else:
            intent = publish_quota_resume_submission_intent(root, request, now_epoch=now_epoch)

        broker._atomic_new_json(
            requests / f"{intent['request_id']}.json", request, broker.MAX_REQUEST_BYTES
        )
        broker.fsync_private_record_directory(requests)
        return intent
