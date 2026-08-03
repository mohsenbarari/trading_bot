"""Synchronous client for the opaque Bot-FI durability coordinator.

Calls in this module happen only at the SQLAlchemy commit boundary.  They are
purposefully separate from the asynchronous DR delivery worker: a delivery
ACK cannot be promoted into evidence for a transaction-bound durability ACK.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text

from core.config import settings
from core.dr_durability_gate import FINANCIAL_TABLES, IDENTITY_TABLES, MESSENGER_TABLES
from core.dr_durability_journal import (
    JOURNAL_COMMIT_PATH,
    JOURNAL_PREPARE_PATH,
    JOURNAL_RECOVER_STATUS_PATH,
    JOURNAL_RECOVER_ROLLBACK_PATH,
    JOURNAL_ROLLBACK_PATH,
    JournalPrepare,
    JournalResolution,
    DurabilityJournalError,
    build_prepare,
    parse_resolution,
    recovery_lookup_payload,
    verify_recovery_acknowledgement,
    verify_acknowledgement,
)
from core.dr_event_protocol import canonical_json_bytes, event_envelope_from_record
from core.dr_sync_auth import (
    PairwiseDrKey,
    canonical_request_bytes,
    parse_pairwise_keys,
    sign_request,
)
from core.runtime_identity import resolve_runtime_identity
from core.runtime_sites import SITE_BOT_FI, SITE_WEBAPP_FI
from core.writer_fencing import current_writer_fence_context


class DurabilityJournalClientError(DurabilityJournalError):
    """Raised when a transaction cannot obtain an exact remote journal ACK."""


@dataclass(frozen=True)
class PreparedJournalTransaction:
    prepare: JournalPrepare
    key: PairwiseDrKey


_CRITICAL_TABLES = FINANCIAL_TABLES | IDENTITY_TABLES | MESSENGER_TABLES


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DurabilityJournalClientError("journal response has a duplicate field")
        result[key] = value
    return result


def _source_key() -> PairwiseDrKey:
    try:
        keys = parse_pairwise_keys(settings.dr_same_region_journal_pairwise_keys_json)
    except Exception as exc:
        raise DurabilityJournalClientError("journal pairwise key configuration is invalid") from exc
    matches = [
        key
        for key in keys.values()
        if key.source_site == SITE_WEBAPP_FI and key.destination_site == SITE_BOT_FI
    ]
    if len(matches) != 1:
        raise DurabilityJournalClientError("journal requires exactly one WebApp-FI to Bot-FI key")
    return matches[0]


def _base_url() -> str:
    raw = str(settings.dr_same_region_journal_url or "").rstrip("/")
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DurabilityJournalClientError("journal URL must be a credential-free HTTPS origin")
    return raw


def _verify_tls() -> bool | str:
    if not settings.dr_same_region_journal_verify_tls:
        raise DurabilityJournalClientError("journal TLS verification must remain enabled")
    return str(settings.dr_same_region_journal_ca_bundle) if settings.dr_same_region_journal_ca_bundle else True


def _encryption_secret() -> str:
    value = settings.dr_same_region_journal_encryption_secret
    if value is None:
        raise DurabilityJournalClientError("journal encryption secret is missing")
    return value.get_secret_value()


def _request(
    *,
    path: str,
    payload: dict[str, Any],
    key: PairwiseDrKey,
) -> tuple[dict[str, Any], str]:
    body = canonical_json_bytes(payload)
    timestamp = int(time.time())
    nonce = secrets.token_urlsafe(32)
    request_hash = hashlib.sha256(
        canonical_request_bytes(
            method="POST",
            path=path,
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            key_id=key.key_id,
            source_site=key.source_site,
            destination_site=key.destination_site,
        )
    ).hexdigest()
    headers = {
        "content-type": "application/json",
        "x-dr-protocol": "dr-sync-v1",
        "x-dr-key-id": key.key_id,
        "x-dr-source-site": key.source_site,
        "x-dr-destination-site": key.destination_site,
        "x-dr-timestamp": str(timestamp),
        "x-dr-nonce": nonce,
        "x-dr-signature": sign_request(
            method="POST",
            path=path,
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            key_id=key.key_id,
            source_site=key.source_site,
            destination_site=key.destination_site,
            secret=key.secret,
        ),
    }
    try:
        with httpx.Client(
            timeout=float(settings.dr_same_region_journal_timeout_seconds),
            verify=_verify_tls(),
        ) as client:
            response = client.post(_base_url() + path, content=body, headers=headers)
    except Exception as exc:
        raise DurabilityJournalClientError("journal receiver is unavailable") from exc
    if response.status_code != 200:
        raise DurabilityJournalClientError("journal receiver rejected the transaction")
    try:
        decoded = json.loads(response.text, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, DurabilityJournalClientError) as exc:
        raise DurabilityJournalClientError("journal receiver returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise DurabilityJournalClientError("journal receiver returned a non-object acknowledgement")
    return decoded, request_hash


def _transaction_envelopes(connection, event_ids: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    rows = connection.execute(
        text(
            "SELECT * FROM dr_events WHERE event_id = ANY(:event_ids) "
            "ORDER BY transaction_position FOR UPDATE"
        ),
        {"event_ids": list(event_ids)},
    ).mappings().all()
    if len(rows) != len(event_ids):
        raise DurabilityJournalClientError("journal cannot find every finalized local event")
    try:
        return tuple(event_envelope_from_record(row) for row in rows)
    except Exception as exc:
        raise DurabilityJournalClientError("journal cannot attest the finalized local event bytes") from exc


def prepare_session_journal(session, event_ids: tuple[str, ...]) -> PreparedJournalTransaction | None:  # noqa: ANN001
    """Persist the opaque first phase for a finalized *critical* transaction."""

    if not event_ids:
        return None
    connection = session.connection()
    transaction_handle = connection.get_transaction()
    local_transaction_gid = str(getattr(transaction_handle, "xid", "") or "")
    if not local_transaction_gid:
        raise DurabilityJournalClientError(
            "journal requires a local PostgreSQL two-phase transaction identifier"
        )
    envelopes = _transaction_envelopes(connection, event_ids)
    if not ({str(item["aggregate_type"]) for item in envelopes} & _CRITICAL_TABLES):
        return None
    if not (
        settings.dr_same_region_journal_enabled
        and settings.dr_same_region_journal_two_phase_enabled
    ):
        raise DurabilityJournalClientError("same-region journal two-phase coordination is disabled")
    identity = resolve_runtime_identity(settings)
    fence = current_writer_fence_context()
    if (
        identity.physical_site != SITE_WEBAPP_FI
        or fence is None
        or fence.physical_site != SITE_WEBAPP_FI
    ):
        raise DurabilityJournalClientError("same-region journal requires the active WebApp-FI writer")
    transaction_ids = {str(item["transaction_id"]) for item in envelopes}
    transaction_hashes = {str(item["transaction_hash"]) for item in envelopes}
    if len(transaction_ids) != 1 or len(transaction_hashes) != 1:
        raise DurabilityJournalClientError("journal transaction metadata is inconsistent")
    key = _source_key()
    try:
        prepare = build_prepare(
            envelopes=envelopes,
            origin_physical_site=SITE_WEBAPP_FI,
            writer_epoch=int(fence.writer_epoch),
            transaction_id=next(iter(transaction_ids)),
            transaction_hash=next(iter(transaction_hashes)),
            local_transaction_gid=local_transaction_gid,
            release_sha=str(settings.release_sha or ""),
            encryption_key_id=str(settings.dr_same_region_journal_encryption_key_id or ""),
            encryption_secret=_encryption_secret(),
        )
        acknowledgement, request_hash = _request(
            path=JOURNAL_PREPARE_PATH,
            payload=prepare.as_payload(),
            key=key,
        )
        verify_acknowledgement(
            acknowledgement,
            prepare=prepare,
            request_hash=request_hash,
            shared_secret=key.secret,
            expected_state="prepared",
        )
    except DurabilityJournalError:
        raise
    except Exception as exc:
        raise DurabilityJournalClientError("journal prepare cannot be verified") from exc
    return PreparedJournalTransaction(prepare=prepare, key=key)


def commit_prepared_journal(
    transaction: PreparedJournalTransaction,
    *,
    prepared_transaction_gid: str,
) -> None:
    if prepared_transaction_gid != transaction.prepare.local_transaction_gid:
        raise DurabilityJournalClientError("local prepared transaction GID changed before commit")
    resolution = JournalResolution(
        origin_physical_site=transaction.prepare.origin_physical_site,
        writer_epoch=transaction.prepare.writer_epoch,
        transaction_id=transaction.prepare.transaction_id,
        transaction_hash=transaction.prepare.transaction_hash,
        prepared_transaction_gid=prepared_transaction_gid,
    )
    try:
        payload = parse_resolution(resolution.as_payload(), require_prepared_gid=True).as_payload()
        acknowledgement, request_hash = _request(
            path=JOURNAL_COMMIT_PATH,
            payload=payload,
            key=transaction.key,
        )
        verify_acknowledgement(
            acknowledgement,
            prepare=transaction.prepare,
            request_hash=request_hash,
            shared_secret=transaction.key.secret,
            expected_state="committed",
        )
    except DurabilityJournalError:
        raise
    except Exception as exc:
        raise DurabilityJournalClientError("journal commit cannot be verified") from exc


def rollback_prepared_journal(transaction: PreparedJournalTransaction) -> None:
    resolution = JournalResolution(
        origin_physical_site=transaction.prepare.origin_physical_site,
        writer_epoch=transaction.prepare.writer_epoch,
        transaction_id=transaction.prepare.transaction_id,
        transaction_hash=transaction.prepare.transaction_hash,
    )
    try:
        acknowledgement, request_hash = _request(
            path=JOURNAL_ROLLBACK_PATH,
            payload=resolution.as_payload(),
            key=transaction.key,
        )
        verify_acknowledgement(
            acknowledgement,
            prepare=transaction.prepare,
            request_hash=request_hash,
            shared_secret=transaction.key.secret,
            expected_state="rolled_back",
        )
    except DurabilityJournalError:
        raise
    except Exception as exc:
        raise DurabilityJournalClientError("journal rollback cannot be verified") from exc


def recover_prepared_journal_by_gid(*, local_transaction_gid: str) -> dict[str, Any]:
    """Read a signed opaque recovery state for one PostgreSQL prepared GID."""

    key = _source_key()
    try:
        payload = recovery_lookup_payload(local_transaction_gid=local_transaction_gid)
        acknowledgement, request_hash = _request(
            path=JOURNAL_RECOVER_STATUS_PATH,
            payload=payload,
            key=key,
        )
        return verify_recovery_acknowledgement(
            acknowledgement,
            local_transaction_gid=local_transaction_gid,
            request_hash=request_hash,
            shared_secret=key.secret,
        )
    except DurabilityJournalError:
        raise
    except Exception as exc:
        raise DurabilityJournalClientError("journal recovery status cannot be verified") from exc


def rollback_prepared_journal_by_gid(*, local_transaction_gid: str) -> dict[str, Any]:
    """Durably choose rollback before a recovery ROLLBACK PREPARED."""

    key = _source_key()
    try:
        payload = recovery_lookup_payload(local_transaction_gid=local_transaction_gid)
        acknowledgement, request_hash = _request(
            path=JOURNAL_RECOVER_ROLLBACK_PATH,
            payload=payload,
            key=key,
        )
        verified = verify_recovery_acknowledgement(
            acknowledgement,
            local_transaction_gid=local_transaction_gid,
            request_hash=request_hash,
            shared_secret=key.secret,
        )
        if verified["state"] != "rolled_back":
            raise DurabilityJournalClientError("journal recovery rollback lacks a terminal rollback state")
        return verified
    except DurabilityJournalError:
        raise
    except Exception as exc:
        raise DurabilityJournalClientError("journal recovery rollback cannot be verified") from exc
