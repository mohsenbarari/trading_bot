"""Database state machine for opaque same-region journal records.

This module intentionally does not know the journal encryption key.  The
Bot-FI receiver may compare immutable metadata and coordinate terminal state,
but it cannot inspect WebApp event payloads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.dr_durability_journal import (
    DurabilityJournalError,
    JournalPrepare,
    JournalResolution,
)
from models.dr_event import DrSameRegionJournal


def _same_prepare(row: DrSameRegionJournal, prepare: JournalPrepare) -> bool:
    return (
        row.transaction_hash == prepare.transaction_hash
        and row.release_sha == prepare.release_sha
        and row.encryption_key_id == prepare.encryption_key_id
        and list(row.event_ids or []) == list(prepare.event_ids)
        and list(row.event_hashes or []) == list(prepare.event_hashes)
        and row.nonce == prepare.nonce
        and row.ciphertext == prepare.ciphertext
        and row.ciphertext_hash == prepare.ciphertext_hash
    )


async def prepare_record(
    session: AsyncSession,
    *,
    prepare: JournalPrepare,
    request_hash: str,
) -> DrSameRegionJournal:
    """Persist or idempotently rediscover an immutable prepared record."""

    key = (prepare.origin_physical_site, prepare.writer_epoch, prepare.transaction_id)
    row = await session.get(DrSameRegionJournal, key, with_for_update=True)
    if row is not None:
        if not _same_prepare(row, prepare):
            raise DurabilityJournalError("journal prepare conflicts with immutable existing record")
        if row.state == "rolled_back":
            raise DurabilityJournalError("journal prepare was already resolved as rolled back")
        return row
    row = DrSameRegionJournal(
        origin_physical_site=prepare.origin_physical_site,
        writer_epoch=prepare.writer_epoch,
        transaction_id=prepare.transaction_id,
        transaction_hash=prepare.transaction_hash,
        release_sha=prepare.release_sha,
        encryption_key_id=prepare.encryption_key_id,
        event_ids=list(prepare.event_ids),
        event_hashes=list(prepare.event_hashes),
        nonce=prepare.nonce,
        ciphertext=prepare.ciphertext,
        ciphertext_hash=prepare.ciphertext_hash,
        prepare_request_hash=request_hash,
        state="prepared",
    )
    session.add(row)
    await session.flush()
    return row


def _assert_resolution_matches(row: DrSameRegionJournal, resolution: JournalResolution) -> None:
    if (
        row.origin_physical_site != resolution.origin_physical_site
        or int(row.writer_epoch) != resolution.writer_epoch
        or row.transaction_id != resolution.transaction_id
        or row.transaction_hash != resolution.transaction_hash
    ):
        raise DurabilityJournalError("journal resolution does not bind the prepared record")


async def commit_record(
    session: AsyncSession,
    *,
    resolution: JournalResolution,
) -> DrSameRegionJournal:
    """Durably decide a prepared record to commit before local COMMIT PREPARED."""

    if not resolution.prepared_transaction_gid:
        raise DurabilityJournalError("journal commit requires the local prepared transaction GID")
    key = (resolution.origin_physical_site, resolution.writer_epoch, resolution.transaction_id)
    row = await session.get(DrSameRegionJournal, key, with_for_update=True)
    if row is None:
        raise DurabilityJournalError("journal commit references a missing prepared record")
    _assert_resolution_matches(row, resolution)
    if row.state == "prepared":
        row.state = "committed"
        row.prepared_transaction_gid = resolution.prepared_transaction_gid
        row.resolved_at = datetime.now(timezone.utc)
        await session.flush()
        return row
    if row.state == "committed" and row.prepared_transaction_gid == resolution.prepared_transaction_gid:
        return row
    raise DurabilityJournalError("journal commit conflicts with the existing terminal outcome")


async def rollback_record(
    session: AsyncSession,
    *,
    resolution: JournalResolution,
) -> DrSameRegionJournal:
    """Resolve only a record whose local transaction never reached PREPARE."""

    if resolution.prepared_transaction_gid is not None:
        raise DurabilityJournalError("journal rollback must not claim a prepared transaction GID")
    key = (resolution.origin_physical_site, resolution.writer_epoch, resolution.transaction_id)
    row = await session.get(DrSameRegionJournal, key, with_for_update=True)
    if row is None:
        raise DurabilityJournalError("journal rollback references a missing prepared record")
    _assert_resolution_matches(row, resolution)
    if row.state == "prepared":
        row.state = "rolled_back"
        row.resolved_at = datetime.now(timezone.utc)
        await session.flush()
        return row
    if row.state == "rolled_back":
        return row
    raise DurabilityJournalError("journal rollback conflicts with a committed record")


async def read_record(
    session: AsyncSession,
    *,
    resolution: JournalResolution,
) -> DrSameRegionJournal | None:
    """Read one opaque coordination record for crash reconciliation."""

    key = (resolution.origin_physical_site, resolution.writer_epoch, resolution.transaction_id)
    row = await session.get(DrSameRegionJournal, key)
    if row is not None:
        _assert_resolution_matches(row, resolution)
    return row


def public_record_state(row: DrSameRegionJournal) -> dict[str, Any]:
    """Return no ciphertext or private payload in operational acknowledgements."""

    return {
        "origin_physical_site": row.origin_physical_site,
        "writer_epoch": int(row.writer_epoch),
        "transaction_id": row.transaction_id,
        "transaction_hash": row.transaction_hash,
        "release_sha": row.release_sha,
        "ciphertext_hash": row.ciphertext_hash,
        "state": row.state,
        "prepared_transaction_gid": row.prepared_transaction_gid,
        "prepared_at": row.prepared_at.astimezone(timezone.utc).isoformat()
        if row.prepared_at is not None
        else None,
        "resolved_at": row.resolved_at.astimezone(timezone.utc).isoformat()
        if row.resolved_at is not None
        else None,
    }
