#!/usr/bin/env python3
"""Closed database observations used by exact live Full Matrix recipes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db import engine  # noqa: E402
from core.config import settings  # noqa: E402
from core.runtime_identity import resolve_runtime_identity  # noqa: E402
from core.sync_parity import (  # noqa: E402
    build_database_parity_snapshot,
    business_snapshot_fingerprint,
)
from models.dr_event import (  # noqa: E402
    DrBlobManifest,
    DrConflictQuarantine,
    DrDestinationCursor,
    DrEffectOutbox,
    DrEvent,
    DrEventDelivery,
    DrStreamCheckpoint,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord  # noqa: E402
from models.webapp_writer_state import (  # noqa: E402
    WebappWriterState,
    WebappWriterTransition,
)
from core.webapp_writer_control import (  # noqa: E402
    load_writer_snapshot,
    snapshot_is_local_active,
)
from scripts.collect_three_site_staging_convergence_snapshot import (  # noqa: E402
    _blob_records,
    _stream_transaction_hash,
)
from scripts.verify_three_site_staging_secret_boundaries import verify_compose  # noqa: E402


SCHEMA = "three-site-full-matrix-site-probe-v1"
ROLES = frozenset({"bot_fi", "webapp_fi", "webapp_ir", "witness"})
OPERATIONS = frozenset(
    {
        "migration_state",
        "observer_privileges",
        "convergence_state",
        "secret_boundary_state",
        "writer_lease_state",
        "timing_snapshot",
    }
)
ROLE_USER = re.compile(
    r"(bot_fi|webapp_fi|webapp_ir)_(app|migration|observer|receiver|delivery|"
    r"projection|blob|effect|control)\Z"
)
ProbeSession = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
ConvergenceSession = async_sessionmaker(
    engine.execution_options(
        isolation_level="REPEATABLE READ",
        postgresql_readonly=True,
    ),
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class SiteProbeError(RuntimeError):
    """The site observation failed closed."""


def _hash_rows(rows: list[tuple[Any, ...]]) -> str:
    return hashlib.sha256(
        json.dumps(
            [[None if value is None else str(value) for value in row] for row in rows],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def _database_state() -> dict[str, Any]:
    async with ProbeSession() as db:
        try:
            current_user = str(await db.scalar(text("SELECT current_user")))
            revisions = sorted(
                str(value)
                for value in (
                    await db.execute(
                        text("SELECT version_num FROM alembic_version ORDER BY version_num")
                    )
                ).scalars()
            )
            schema_rows = list(
                (
                    await db.execute(
                        text(
                            "SELECT table_name,column_name,data_type,is_nullable "
                            "FROM information_schema.columns "
                            "WHERE table_schema='public' "
                            "ORDER BY table_name,ordinal_position"
                        )
                    )
                ).all()
            )
            runtime_rows = list(
                (
                    await db.execute(
                        text(
                            "SELECT physical_site,logical_authority,release_sha,producer_epoch "
                            "FROM dr_database_runtime ORDER BY physical_site"
                        )
                    )
                ).all()
            )
            unresolved_conflicts = int(
                await db.scalar(
                    text(
                        "SELECT count(*) FROM dr_conflict_quarantine "
                        "WHERE resolved_at IS NULL"
                    )
                )
                or 0
            )
            permissions = list(
                (
                    await db.execute(
                        text(
                            "SELECT privilege_type,count(*) "
                            "FROM information_schema.role_table_grants "
                            "WHERE grantee=current_user "
                            "GROUP BY privilege_type ORDER BY privilege_type"
                        )
                    )
                ).all()
            )
        finally:
            await db.rollback()
    if not revisions or not schema_rows or not runtime_rows:
        raise SiteProbeError("database migration/runtime state is incomplete")
    return {
        "current_user": current_user,
        "alembic_revisions": revisions,
        "schema_sha256": _hash_rows(schema_rows),
        "schema_column_count": len(schema_rows),
        "runtime_sha256": _hash_rows(runtime_rows),
        "runtime_row_count": len(runtime_rows),
        "unresolved_conflict_count": unresolved_conflicts,
        "permission_counts": {
            str(name): int(count) for name, count in permissions
        },
    }


async def _observer_privileges() -> dict[str, Any]:
    state = await _database_state()
    user = state["current_user"]
    if not user.endswith("_observer") or ROLE_USER.fullmatch(user) is None:
        raise SiteProbeError("observer probe is not using the dedicated observer identity")
    privilege_counts = state["permission_counts"]
    forbidden = {
        name: count
        for name, count in privilege_counts.items()
        if name not in {"SELECT"} and count
    }
    async with ProbeSession() as db:
        try:
            can_set_role = bool(
                await db.scalar(
                    text(
                        "SELECT EXISTS("
                        "SELECT 1 FROM pg_roles r "
                        "WHERE r.rolname<>current_user AND pg_has_role(current_user,r.oid,'MEMBER')"
                        ")"
                    )
                )
            )
            can_create_db = bool(
                await db.scalar(
                    text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user")
                )
            )
            can_bypass_rls = bool(
                await db.scalar(
                    text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname=current_user")
                )
            )
        finally:
            await db.rollback()
    if forbidden or can_set_role or can_create_db or can_bypass_rls:
        raise SiteProbeError("observer identity has a forbidden privilege")
    return {
        **state,
        "only_select_table_grants": True,
        "set_role_denied": True,
        "create_database_denied": True,
        "bypass_rls_denied": True,
    }


def _hash_value(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def _status_counts(db: AsyncSession, table, column) -> dict[str, int]:
    rows = (
        await db.execute(
            select(column, func.count()).select_from(table).group_by(column).order_by(column)
        )
    ).all()
    return {
        str(getattr(status, "value", status)): int(count)
        for status, count in rows
    }


async def _source_streams(db: AsyncSession, *, site: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(DrDestinationCursor)
            .where(DrDestinationCursor.origin_physical_site == site)
            .order_by(
                DrDestinationCursor.producer_epoch,
                DrDestinationCursor.destination_site,
            )
        )
    ).scalars().all()
    output: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for row in rows:
        epoch = int(row.producer_epoch)
        destination = str(row.destination_site)
        sequence = int(row.last_sequence)
        key = (epoch, destination)
        if (
            epoch < 1
            or sequence < 0
            or destination not in ROLES - {"witness", site}
            or key in seen
        ):
            raise SiteProbeError("source stream cursor is invalid")
        seen.add(key)
        output.append(
            {
                "origin_site": site,
                "producer_epoch": epoch,
                "destination_site": destination,
                "source_sequence": sequence,
                "source_transaction_hash": await _stream_transaction_hash(
                    db,
                    origin_site=site,
                    producer_epoch=epoch,
                    destination_site=destination,
                    destination_sequence=sequence,
                ),
            }
        )
    local_epochs = {
        int(value)
        for value in (
            await db.execute(
                select(DrEvent.producer_epoch)
                .where(DrEvent.origin_physical_site == site)
                .distinct()
            )
        ).scalars()
    }
    cursor_epochs = {item["producer_epoch"] for item in output}
    if not local_epochs.issubset(cursor_epochs):
        raise SiteProbeError("local producer event epoch has no destination cursor")
    return output


async def _destination_streams(
    db: AsyncSession,
    *,
    site: str,
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(DrStreamCheckpoint)
            .where(DrStreamCheckpoint.destination_site == site)
            .order_by(
                DrStreamCheckpoint.origin_physical_site,
                DrStreamCheckpoint.producer_epoch,
            )
        )
    ).scalars().all()
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        origin = str(row.origin_physical_site)
        epoch = int(row.producer_epoch)
        received = int(row.contiguous_received_sequence)
        applied = int(row.contiguous_applied_sequence)
        key = (origin, epoch)
        if (
            origin not in ROLES - {"witness", site}
            or epoch < 1
            or applied < 0
            or received < applied
            or key in seen
        ):
            raise SiteProbeError("destination stream checkpoint is invalid")
        seen.add(key)
        output.append(
            {
                "origin_site": origin,
                "producer_epoch": epoch,
                "destination_site": site,
                "received_sequence": received,
                "applied_sequence": applied,
                "received_transaction_hash": await _stream_transaction_hash(
                    db,
                    origin_site=origin,
                    producer_epoch=epoch,
                    destination_site=site,
                    destination_sequence=received,
                ),
                "applied_transaction_hash": await _stream_transaction_hash(
                    db,
                    origin_site=origin,
                    producer_epoch=epoch,
                    destination_site=site,
                    destination_sequence=applied,
                ),
            }
        )
    return output


async def _convergence_state() -> dict[str, Any]:
    identity = resolve_runtime_identity()
    site = str(identity.physical_site)
    async with ConvergenceSession() as db:
        try:
            parity = await build_database_parity_snapshot(
                db,
                mode="deep",
                max_rows_per_table=100_000,
            )
            tables = parity.get("tables")
            if not isinstance(tables, dict) or any(
                bool(table.get("truncated")) for table in tables.values()
            ):
                raise SiteProbeError("deep database parity snapshot is incomplete")
            business_sha256 = business_snapshot_fingerprint(parity)
            table_set = sorted(str(name) for name in tables)
            row_count = sum(int(table.get("row_count") or 0) for table in tables.values())
            source_streams = await _source_streams(db, site=site)
            destination_streams = await _destination_streams(db, site=site)
            unresolved_conflicts = int(
                await db.scalar(
                    select(func.count(DrConflictQuarantine.quarantine_id)).where(
                        DrConflictQuarantine.resolved_at.is_(None)
                    )
                )
                or 0
            )
            blobs = await _blob_records(db, site=site)
            blob_payload = [
                {
                    "content_hash": str(row["content_hash"]),
                    "size_bytes": int(row["size_bytes"]),
                    "object_version_id": str(row["object_version_id"]),
                    "object_ciphertext_hash": str(row["object_ciphertext_hash"]),
                    "object_ciphertext_size": int(row["object_ciphertext_size"]),
                    "encryption_key_id": str(row["encryption_key_id"]),
                    "encryption_algorithm": str(row["encryption_algorithm"]),
                }
                for row in blobs
            ]
            delivery_counts = await _status_counts(
                db,
                DrEventDelivery,
                DrEventDelivery.status,
            )
            effect_counts = await _status_counts(
                db,
                DrEffectOutbox,
                DrEffectOutbox.status,
            )
            queue_counts = await _status_counts(
                db,
                TelegramDeliveryJobRecord,
                TelegramDeliveryJobRecord.state,
            )
            writer_rows = list(
                (
                    await db.execute(
                        select(
                            WebappWriterState.authority,
                            WebappWriterState.active_site,
                            WebappWriterState.writer_epoch,
                            WebappWriterState.control_state,
                            WebappWriterState.transition_id,
                        ).order_by(WebappWriterState.authority)
                    )
                ).all()
            )
            manifest_count = int(
                await db.scalar(select(func.count(DrBlobManifest.content_hash))) or 0
            )
        finally:
            await db.rollback()
    if site not in {"webapp_fi", "webapp_ir"} and (blobs or manifest_count):
        raise SiteProbeError("non-WebApp site contains a WebApp blob replica")
    return {
        "database_business_sha256": business_sha256,
        "database_table_set_sha256": _hash_value(table_set),
        "database_table_count": len(table_set),
        "database_row_count": row_count,
        "source_streams": source_streams,
        "destination_streams": destination_streams,
        "unresolved_conflict_count": unresolved_conflicts,
        "blob_set_sha256": _hash_value(blob_payload),
        "blob_count": len(blob_payload),
        "blob_manifest_count": manifest_count,
        "blob_readback_count": len(blobs),
        "event_delivery_status_counts": delivery_counts,
        "effect_status_counts": effect_counts,
        "telegram_job_status_counts": queue_counts,
        "writer_state_sha256": _hash_rows(writer_rows),
        "writer_state": [
            {
                "authority": str(authority),
                "active_site": None if active_site is None else str(active_site),
                "writer_epoch": int(writer_epoch),
                "control_state": str(control_state),
                "transition_id": str(transition_id),
            }
            for authority, active_site, writer_epoch, control_state, transition_id in writer_rows
        ],
        "runtime_producer_epoch": int(settings.dr_producer_epoch),
    }


async def _writer_lease_state() -> dict[str, Any]:
    identity = resolve_runtime_identity()
    if not identity.is_webapp_site or not identity.is_webapp_authority:
        raise SiteProbeError("writer lease observation requires a WebApp site")
    async with ConvergenceSession() as db:
        try:
            snapshot = await load_writer_snapshot(db)
            refresh_count = int(
                await db.scalar(
                    select(func.count(WebappWriterTransition.transition_id)).where(
                        WebappWriterTransition.action == "lease_refresh",
                        WebappWriterTransition.new_epoch == snapshot.writer_epoch,
                    )
                )
                or 0
            )
            database_now = await db.scalar(text("SELECT clock_timestamp()"))
        finally:
            await db.rollback()
    active, reasons = snapshot_is_local_active(
        identity,
        snapshot,
        require_witness_lease=True,
    )
    lease_id = str(snapshot.witness_lease_id or "")
    if (
        snapshot.writer_epoch < 1
        or snapshot.witness_lease_issued_at is None
        or snapshot.witness_lease_expires_at is None
        or not lease_id
        or database_now is None
    ):
        raise SiteProbeError("writer lease evidence is incomplete")
    return {
        "active_site": snapshot.active_site,
        "writer_epoch": snapshot.writer_epoch,
        "control_state": snapshot.control_state,
        "transition_id": snapshot.transition_id,
        "witness_lease_id_sha256": hashlib.sha256(lease_id.encode()).hexdigest(),
        "witness_lease_issued_at": _utc(snapshot.witness_lease_issued_at),
        "witness_lease_expires_at": _utc(snapshot.witness_lease_expires_at),
        "witness_proof_hash": snapshot.witness_proof_hash,
        "lease_refresh_count_for_epoch": refresh_count,
        "database_now": _utc(database_now),
        "local_active_with_witness_lease": active,
        "local_active_reasons": list(reasons),
    }


def _secret_boundary_state() -> dict[str, Any]:
    compose = REPO_ROOT / "deploy/staging/docker-compose.three-site.yml"
    result = verify_compose(compose)
    if result.get("status") != "verified":
        raise SiteProbeError("release Compose secret boundaries are not verified")
    return {
        "release_compose_sha256": hashlib.sha256(compose.read_bytes()).hexdigest(),
        "service_count": int(result["service_count"]),
        "managed_network_count": int(result["managed_network_count"]),
        "secret_values_emitted": False,
    }


async def collect(
    operation: str,
    *,
    correlation_prefix: str | None = None,
    clock_evidence_base64: str | None = None,
) -> dict[str, Any]:
    identity = resolve_runtime_identity()
    role = str(identity.physical_site)
    if role not in ROLES:
        raise SiteProbeError("runtime physical site is outside the Full Matrix topology")
    if operation == "migration_state":
        result = await _database_state()
    elif operation == "observer_privileges":
        result = await _observer_privileges()
    elif operation == "secret_boundary_state":
        result = _secret_boundary_state()
    elif operation == "writer_lease_state":
        result = await _writer_lease_state()
    elif operation == "timing_snapshot":
        if not correlation_prefix or not clock_evidence_base64:
            raise SiteProbeError("timing snapshot arguments are required")
        from scripts.collect_three_site_sync_timing_snapshot import (
            _clock_from_base64,
            collect as collect_timing_snapshot,
        )

        result = await collect_timing_snapshot(
            correlation_prefix,
            clock=_clock_from_base64(clock_evidence_base64),
        )
    else:
        result = await _convergence_state()
    return {
        "schema": SCHEMA,
        "status": "passed",
        "operation": operation,
        "role": role,
        "result": result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=sorted(OPERATIONS), required=True)
    parser.add_argument("--correlation-prefix")
    parser.add_argument("--clock-evidence-base64")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                asyncio.run(
                    collect(
                        args.operation,
                        correlation_prefix=args.correlation_prefix,
                        clock_evidence_base64=args.clock_evidence_base64,
                    )
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
