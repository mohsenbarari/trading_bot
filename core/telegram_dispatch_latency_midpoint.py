"""Code-derived midpoint after low-risk Telegram latency stages 1–5.

Compares the locked stage-0 floors with the control-flow costs implied by the
current tree. These numbers are not observed staging percentiles.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.config import Settings
from core.telegram_dispatch_latency_baseline import (
    TelegramDispatchLatencyFloors,
    code_derived_latency_floors,
)


MIDPOINT_SCHEMA_VERSION = "telegram_dispatch_latency_midpoint_v1"
MIDPOINT_AFTER_STAGES = (1, 2, 3, 4, 5)
_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class TelegramDispatchLatencyCurrentFloors:
    evidence_kind: str
    destination_min_interval_seconds: float
    publisher_idle_poll_interval_seconds: float
    b2b_dispatch_interval_seconds: float
    b2b_dispatch_batch_size: int
    telegram_calls_per_channel_job: int
    dead_wait_after_ack_seconds_max: float
    shared_destination_gate: bool
    gateway_reuses_http_client: bool
    b2b_dispatch_skips_auth_middleware: bool
    claim_index_covers_sent: bool
    retention_purges_terminal_commands: bool
    full_batch_cycle_sleep_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TelegramDispatchLatencyComparisonRow:
    metric: str
    baseline: Any
    current: Any
    changed: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TelegramDispatchLatencyMidpoint:
    schema_version: str
    evidence_kind: str
    compared_after_stages: tuple[int, ...]
    live_percentiles_collected: bool
    baseline: TelegramDispatchLatencyFloors
    current: TelegramDispatchLatencyCurrentFloors
    rows: tuple[TelegramDispatchLatencyComparisonRow, ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
            "compared_after_stages": list(self.compared_after_stages),
            "live_percentiles_collected": self.live_percentiles_collected,
            "baseline": self.baseline.as_dict(),
            "current": self.current.as_dict(),
            "rows": [row.as_dict() for row in self.rows],
            "notes": list(self.notes),
        }


def _default(name: str) -> Any:
    return Settings.model_fields[name].default


def _source(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def ack_path_emits_wakeup() -> bool:
    text = _source("core/services/telegram_publisher_dispatch_service.py")
    return text.count("await emit_delivery_queue_wakeup") >= 2 and "ACKNOWLEDGED" in text


def gateway_reuses_http_client() -> bool:
    text = _source("core/telegram_gateway.py")
    return (
        "async def get_telegram_http_client" in text
        and "client = await get_telegram_http_client()" in text
        and "max_keepalive_connections=8" in text
    )


def dispatcher_uses_configured_batch() -> bool:
    text = _source("run_bot.py")
    return (
        "telegram_b2b_dispatch_batch_size" in text
        and "limit=batch_limit" in text
        and "if claimed >= limit:" in text
        and "return 0.0" in text
    )


def b2b_dispatch_skips_auth_middleware() -> bool:
    text = _source("run_bot.py")
    start = text.find("def configured_publisher_b2b_pollers")
    end = text.find("\nasync def supervise_pollers")
    if start < 0 or end <= start:
        return False
    block = text[start:end]
    return (
        "TelegramBotIdentityMiddleware" in block
        and "AuthMiddleware" not in block
        and "CallbackReceiptMiddleware" not in block
        and "TradeContentionGateMiddleware" not in block
    )


def claim_index_covers_sent() -> bool:
    text = _source("models/telegram_publisher_dispatch_command.py")
    return (
        "ix_telegram_publisher_dispatch_commands_claim" in text
        and "state IN ('pending', 'retry_due', 'sent')" in text
    )


def retention_purges_terminal_commands() -> bool:
    text = _source("core/services/telegram_delivery_retention_service.py")
    return (
        "async def _purge_publisher_dispatch_command_for_job" in text
        and "dispatch_blocked_due" in text
    )


def b2b_dispatch_acknowledges_locally() -> bool:
    """True when the durable command is the handoff and no B2B hop is sent."""
    dispatcher = _source("run_bot.py")
    service = _source("core/services/telegram_publisher_dispatch_service.py")
    return (
        "run_co_located_telegram_publisher_dispatch_cycle" in dispatcher
        and "acknowledge_claimed_telegram_publisher_dispatch_locally" in service
    )


def current_code_derived_latency_floors() -> TelegramDispatchLatencyCurrentFloors:
    idle = float(
        _default("telegram_delivery_queue_publisher_idle_poll_interval_seconds")
    )
    return TelegramDispatchLatencyCurrentFloors(
        evidence_kind="code_derived_current_floors",
        destination_min_interval_seconds=float(
            _default("telegram_delivery_queue_destination_min_interval_seconds")
        ),
        publisher_idle_poll_interval_seconds=idle,
        b2b_dispatch_interval_seconds=float(
            _default("telegram_b2b_dispatch_interval_seconds")
        ),
        b2b_dispatch_batch_size=int(_default("telegram_b2b_dispatch_batch_size")),
        telegram_calls_per_channel_job=1 if b2b_dispatch_acknowledges_locally() else 3,
        dead_wait_after_ack_seconds_max=0.0 if ack_path_emits_wakeup() else idle,
        shared_destination_gate=True,
        gateway_reuses_http_client=gateway_reuses_http_client(),
        b2b_dispatch_skips_auth_middleware=b2b_dispatch_skips_auth_middleware(),
        claim_index_covers_sent=claim_index_covers_sent(),
        retention_purges_terminal_commands=retention_purges_terminal_commands(),
        full_batch_cycle_sleep_seconds=0.0,
    )


def _row(metric: str, baseline: Any, current: Any) -> TelegramDispatchLatencyComparisonRow:
    return TelegramDispatchLatencyComparisonRow(
        metric=metric,
        baseline=baseline,
        current=current,
        changed=baseline != current,
    )


def code_derived_latency_midpoint() -> TelegramDispatchLatencyMidpoint:
    """Return the locked baseline beside the post-stage-5 code floors."""
    if not dispatcher_uses_configured_batch():
        raise RuntimeError("telegram_b2b_dispatch_batch_not_wired")
    baseline = code_derived_latency_floors()
    current = current_code_derived_latency_floors()
    rows = (
        _row(
            "destination_min_interval_seconds",
            baseline.destination_min_interval_seconds,
            current.destination_min_interval_seconds,
        ),
        _row(
            "publisher_idle_poll_interval_seconds",
            baseline.publisher_idle_poll_interval_seconds,
            current.publisher_idle_poll_interval_seconds,
        ),
        _row(
            "b2b_dispatch_interval_seconds",
            baseline.b2b_dispatch_interval_seconds,
            current.b2b_dispatch_interval_seconds,
        ),
        _row(
            "b2b_dispatch_batch_size",
            baseline.b2b_dispatch_batch_size,
            current.b2b_dispatch_batch_size,
        ),
        _row(
            "telegram_calls_per_channel_job",
            baseline.telegram_calls_per_channel_job,
            current.telegram_calls_per_channel_job,
        ),
        _row(
            "dead_wait_after_ack_seconds_max",
            baseline.dead_wait_after_ack_seconds_max,
            current.dead_wait_after_ack_seconds_max,
        ),
        _row(
            "shared_destination_gate",
            baseline.shared_destination_gate,
            current.shared_destination_gate,
        ),
        _row("gateway_reuses_http_client", False, current.gateway_reuses_http_client),
        _row(
            "b2b_dispatch_skips_auth_middleware",
            False,
            current.b2b_dispatch_skips_auth_middleware,
        ),
        _row("claim_index_covers_sent", False, current.claim_index_covers_sent),
        _row(
            "retention_purges_terminal_commands",
            False,
            current.retention_purges_terminal_commands,
        ),
        _row(
            "full_batch_cycle_sleep_seconds",
            baseline.b2b_dispatch_interval_seconds,
            current.full_batch_cycle_sleep_seconds,
        ),
    )
    return TelegramDispatchLatencyMidpoint(
        schema_version=MIDPOINT_SCHEMA_VERSION,
        evidence_kind="code_derived_midpoint",
        compared_after_stages=MIDPOINT_AFTER_STAGES,
        live_percentiles_collected=False,
        baseline=baseline,
        current=current,
        rows=rows,
        notes=(
            "Dead wait after acknowledgement is now a wakeup, not the idle poll.",
            "Gateway async calls reuse one keepalive client; sync posts stay one-shot.",
            "B2B dispatch claims a serial batch; a full batch sleeps zero seconds.",
            "Telegram hops per channel job read one: the durable command is the handoff.",
            "Observed staging percentiles were not collected in this worktree.",
        ),
    )
