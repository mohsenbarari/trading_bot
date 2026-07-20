"""Read-only readiness contract for schema-preserving Telegram queue rollback."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.telegram_delivery_queue_contract import (
    FINAL_DELIVERY_STATES,
    TelegramDeliveryState,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    resolve_telegram_delivery_runtime,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_delivery_resume_operation import (
    ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES,
    TelegramDeliveryResumeOperation,
)
from models.telegram_delivery_runtime_gate import (
    ACTIVE_TELEGRAM_RUNTIME_GATE_STATES,
    TelegramDeliveryRuntimeGate,
)

from .telegram_delivery_queue_service import _require_foreign


async def inspect_telegram_delivery_forward_rollback_readiness(
    db: AsyncSession,
    *,
    current_server: str,
    expected_schema_head: str,
    execution_owner: str,
    queue_worker_enabled: bool,
    cutover_ready: bool,
    producer_quiesced: bool,
    migration_stage_skipped: bool,
    backup_manifest_verified: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a forward rollback without changing schema, queue, or runtime."""
    _require_foreign(current_server)
    current_time = now or utc_now()
    blockers: list[str] = []
    try:
        runtime = resolve_telegram_delivery_runtime(
            execution_owner=execution_owner,
            queue_worker_enabled=queue_worker_enabled,
            cutover_ready=cutover_ready,
        )
    except Exception:
        runtime = None
        blockers.append("legacy_runtime_config_invalid")
    if runtime is not None and (
        runtime.mode != TelegramDeliveryRuntimeMode.LEGACY
        or not runtime.legacy_workers_enabled
        or runtime.queue_worker_enabled
    ):
        blockers.append("legacy_runtime_config_invalid")
    if not producer_quiesced:
        blockers.append("producer_quiescence_not_confirmed")
    if not migration_stage_skipped:
        blockers.append("rollback_migration_stage_not_skipped")
    if not backup_manifest_verified:
        blockers.append("rollback_backup_manifest_not_verified")

    schema_revisions = tuple(
        sorted(
            str(value)
            for value in (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalars()
        )
    )
    if schema_revisions != (str(expected_schema_head),):
        blockers.append("schema_head_mismatch")

    active_states = tuple(
        state for state in TelegramDeliveryState if state not in FINAL_DELIVERY_STATES
    )
    (
        active_job_count,
        leased_job_count,
        unresolved_job_count,
    ) = (
        int(value or 0)
        for value in (
            await db.execute(
                select(
                    func.count(TelegramDeliveryJobRecord.id).filter(
                        TelegramDeliveryJobRecord.state.in_(active_states)
                    ),
                    func.count(TelegramDeliveryJobRecord.id).filter(
                        TelegramDeliveryJobRecord.state == TelegramDeliveryState.LEASED
                    ),
                    func.count(TelegramDeliveryJobRecord.id).filter(
                        TelegramDeliveryJobRecord.state.in_(
                            (
                                TelegramDeliveryState.AMBIGUOUS,
                                TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
                                TelegramDeliveryState.PENDING_RECONCILE,
                            )
                        )
                    ),
                )
            )
        ).one()
    )
    pending_provider_outcome_count = int(
        (
            await db.execute(
                select(func.count(TelegramDeliveryProviderOutcomeRecord.id)).where(
                    TelegramDeliveryProviderOutcomeRecord.apply_state
                    == TELEGRAM_PROVIDER_OUTCOME_PENDING
                )
            )
        ).scalar_one()
        or 0
    )
    incomplete_resume_count = int(
        (
            await db.execute(
                select(func.count(TelegramDeliveryResumeOperation.id)).where(
                    TelegramDeliveryResumeOperation.state.in_(
                        ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES
                    )
                )
            )
        ).scalar_one()
        or 0
    )
    active_runtime_gate_count = int(
        (
            await db.execute(
                select(func.count(TelegramDeliveryRuntimeGate.gate_key)).where(
                    TelegramDeliveryRuntimeGate.state.in_(
                        ACTIVE_TELEGRAM_RUNTIME_GATE_STATES
                    )
                )
            )
        ).scalar_one()
        or 0
    )
    for code, value in (
        ("active_queue_jobs_present", active_job_count),
        ("leased_queue_jobs_present", leased_job_count),
        ("unresolved_queue_jobs_present", unresolved_job_count),
        ("pending_provider_outcomes_present", pending_provider_outcome_count),
        ("incomplete_resume_operations_present", incomplete_resume_count),
        ("active_runtime_gates_present", active_runtime_gate_count),
    ):
        if value:
            blockers.append(code)

    unique_blockers = sorted(set(blockers))
    return {
        "schema_version": 1,
        "observed_at": current_time.isoformat(),
        "decision": "ready" if not unique_blockers else "blocked",
        "blockers": unique_blockers,
        "schema_strategy": "preserve_current_schema",
        "schema_downgrade_allowed": False,
        "migration_stage_must_be_skipped": True,
        "schema_head_matches": schema_revisions == (str(expected_schema_head),),
        "schema_revision_count": len(schema_revisions),
        "target_runtime_mode": "legacy",
        "legacy_workers_enabled": bool(runtime and runtime.legacy_workers_enabled),
        "queue_worker_enabled": bool(runtime and runtime.queue_worker_enabled),
        "producer_quiesced": bool(producer_quiesced),
        "migration_stage_skipped": bool(migration_stage_skipped),
        "backup_manifest_verified": bool(backup_manifest_verified),
        "active_job_count": active_job_count,
        "leased_job_count": leased_job_count,
        "unresolved_job_count": unresolved_job_count,
        "pending_provider_outcome_count": pending_provider_outcome_count,
        "incomplete_resume_count": incomplete_resume_count,
        "active_runtime_gate_count": active_runtime_gate_count,
        "provider_network_calls": 0,
        "database_mutations": 0,
    }
