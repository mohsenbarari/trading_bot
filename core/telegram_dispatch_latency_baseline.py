"""Code-derived latency floors for the multi-publisher Telegram path.

These numbers are the wait and hop costs implied by current defaults and
control flow. They are not observed staging percentiles. Live p50/p95/p99
must be collected later with the existing queue health reporter on a
guarded staging database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.config import Settings


BASELINE_SCHEMA_VERSION = "telegram_dispatch_latency_baseline_v1"
BASELINE_COMMIT = "1d5e9286"
BASELINE_CODE_BASE = "44babdc4"


@dataclass(frozen=True, slots=True)
class TelegramDispatchLatencyFloors:
    schema_version: str
    evidence_kind: str
    code_base: str
    roadmap_commit: str
    destination_min_interval_seconds: float
    publisher_idle_poll_interval_seconds: float
    b2b_dispatch_interval_seconds: float
    b2b_dispatch_batch_size: int
    telegram_calls_per_channel_job: int
    dead_wait_after_ack_seconds_max: float
    shared_destination_gate: bool
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["notes"] = list(self.notes)
        return payload


def _default(name: str) -> Any:
    return Settings.model_fields[name].default


def code_derived_latency_floors() -> TelegramDispatchLatencyFloors:
    """Return the pre-change floors implied by Settings defaults and B2B hops."""
    return TelegramDispatchLatencyFloors(
        schema_version=BASELINE_SCHEMA_VERSION,
        evidence_kind="code_derived_floors",
        code_base=BASELINE_CODE_BASE,
        roadmap_commit=BASELINE_COMMIT,
        destination_min_interval_seconds=float(
            _default("telegram_delivery_queue_destination_min_interval_seconds")
        ),
        publisher_idle_poll_interval_seconds=float(
            _default("telegram_delivery_queue_publisher_idle_poll_interval_seconds")
        ),
        b2b_dispatch_interval_seconds=float(
            _default("telegram_b2b_dispatch_interval_seconds")
        ),
        b2b_dispatch_batch_size=1,
        telegram_calls_per_channel_job=3,
        dead_wait_after_ack_seconds_max=float(
            _default("telegram_delivery_queue_publisher_idle_poll_interval_seconds")
        ),
        shared_destination_gate=True,
        notes=(
            "Ack-to-claim has no wakeup; worst dead wait equals publisher idle poll.",
            "Each channel job pays B2B send, B2B ack, then the channel method.",
            "B2B dispatcher claims one command per 0.5s cycle (limit=1).",
            "Observed staging percentiles were not collected in this worktree.",
        ),
    )
