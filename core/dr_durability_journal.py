"""Opaque, transaction-bound contract for the FI same-region durability journal.

The normal three-site event stream is asynchronous and destination-authorized.
It cannot, by itself, be used as evidence that a WebApp-FI critical commit is
durable on an independent Finnish host.  This module deliberately defines a
separate *opaque* journal contract: Bot-FI stores authenticated ciphertext and
transaction metadata, never WebApp-private event payloads.

The contract has two phases.  A later coordinator binds ``prepared`` records
to PostgreSQL prepared transactions and resolves them to a terminal outcome.
Keeping that state machine separate from the existing async delivery worker
avoids treating an ordinary delivery acknowledgement as a commit guarantee.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
from typing import Any, Mapping, Sequence
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.dr_event_protocol import canonical_json_bytes, validate_envelope
from core.dr_sync_auth import acknowledgement_signature_is_valid
from core.runtime_sites import SITE_BOT_FI, SITE_WEBAPP_FI


JOURNAL_SCHEMA = "three-site-same-region-journal-v1"
JOURNAL_PREPARE_PATH = "/api/dr-journal/v1/prepare"
JOURNAL_COMMIT_PATH = "/api/dr-journal/v1/commit"
JOURNAL_ROLLBACK_PATH = "/api/dr-journal/v1/rollback"
JOURNAL_RECOVER_STATUS_PATH = "/api/dr-journal/v1/recover-status"
JOURNAL_RECOVER_ROLLBACK_PATH = "/api/dr-journal/v1/recover-rollback"
JOURNAL_MAX_CIPHERTEXT_BYTES = 8 * 1024 * 1024
JOURNAL_MAX_TRANSACTION_EVENTS = 500
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# PostgreSQL 2PC identifiers created by SQLAlchemy currently begin ``_sa_``.
# The identifier is public coordination metadata only; it never contains a
# business key or encrypted payload.
_PREPARED_GID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._:-]{7,191}$")


class DurabilityJournalError(RuntimeError):
    """Raised when a journal record is malformed, untrusted, or inconsistent."""


@dataclass(frozen=True)
class JournalPrepare:
    """Public metadata plus opaque encrypted transaction envelopes."""

    origin_physical_site: str
    writer_epoch: int
    transaction_id: str
    transaction_hash: str
    local_transaction_gid: str
    release_sha: str
    encryption_key_id: str
    event_ids: tuple[str, ...]
    event_hashes: tuple[str, ...]
    nonce: str
    ciphertext: str
    ciphertext_hash: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema": JOURNAL_SCHEMA,
            "origin_physical_site": self.origin_physical_site,
            "writer_epoch": self.writer_epoch,
            "transaction_id": self.transaction_id,
            "transaction_hash": self.transaction_hash,
            "local_transaction_gid": self.local_transaction_gid,
            "release_sha": self.release_sha,
            "encryption_key_id": self.encryption_key_id,
            "event_ids": list(self.event_ids),
            "event_hashes": list(self.event_hashes),
            "nonce": self.nonce,
            "ciphertext": self.ciphertext,
            "ciphertext_hash": self.ciphertext_hash,
        }


@dataclass(frozen=True)
class JournalResolution:
    """Identity supplied to a terminal coordinator transition."""

    origin_physical_site: str
    writer_epoch: int
    transaction_id: str
    transaction_hash: str
    prepared_transaction_gid: str | None = None

    def as_payload(self) -> dict[str, Any]:
        result = {
            "schema": JOURNAL_SCHEMA,
            "origin_physical_site": self.origin_physical_site,
            "writer_epoch": self.writer_epoch,
            "transaction_id": self.transaction_id,
            "transaction_hash": self.transaction_hash,
        }
        if self.prepared_transaction_gid is not None:
            result["prepared_transaction_gid"] = self.prepared_transaction_gid
        return result


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DurabilityJournalError(f"duplicate journal field: {key}")
        result[key] = value
    return result


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: Any, *, field: str) -> str:
    normalized = str(value or "")
    if not _SHA256_RE.fullmatch(normalized):
        raise DurabilityJournalError(f"journal {field} must be a lowercase SHA-256")
    return normalized


def _require_uuid(value: Any, *, field: str) -> str:
    normalized = str(value or "")
    try:
        parsed = UUID(normalized)
    except (ValueError, AttributeError) as exc:
        raise DurabilityJournalError(f"journal {field} is not a UUID") from exc
    if str(parsed) != normalized:
        raise DurabilityJournalError(f"journal {field} must use canonical UUID form")
    return normalized


def _require_release_sha(value: Any) -> str:
    normalized = str(value or "")
    if not re.fullmatch(r"[0-9a-f]{40,64}", normalized):
        raise DurabilityJournalError("journal release SHA is invalid")
    return normalized


def _derive_encryption_key(secret: str) -> bytes:
    material = str(secret or "").encode("utf-8")
    if len(material) < 32:
        raise DurabilityJournalError("journal encryption secret must be at least 32 bytes")
    return hashlib.sha256(b"trading-bot:same-region-journal:v1\x00" + material).digest()


def _decode_base64(value: Any, *, field: str, expected_length: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise DurabilityJournalError(f"journal {field} is not base64 text")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise DurabilityJournalError(f"journal {field} is malformed base64") from exc
    if expected_length is not None and len(decoded) != expected_length:
        raise DurabilityJournalError(f"journal {field} has an invalid size")
    return decoded


def _aad(payload: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(
        {
            "schema": JOURNAL_SCHEMA,
            "origin_physical_site": payload["origin_physical_site"],
            "writer_epoch": payload["writer_epoch"],
            "transaction_id": payload["transaction_id"],
            "transaction_hash": payload["transaction_hash"],
            "local_transaction_gid": payload["local_transaction_gid"],
            "release_sha": payload["release_sha"],
            "encryption_key_id": payload["encryption_key_id"],
            "event_ids": payload["event_ids"],
            "event_hashes": payload["event_hashes"],
        }
    )


def _validated_transaction_envelopes(
    envelopes: Sequence[Mapping[str, Any]],
    *,
    origin_physical_site: str,
    writer_epoch: int,
    transaction_id: str,
    transaction_hash: str,
) -> list[dict[str, Any]]:
    if not envelopes or len(envelopes) > JOURNAL_MAX_TRANSACTION_EVENTS:
        raise DurabilityJournalError("journal transaction event count is invalid")
    normalized: list[dict[str, Any]] = []
    for envelope in envelopes:
        try:
            validated = validate_envelope(dict(envelope))
        except Exception as exc:
            raise DurabilityJournalError("journal event envelope is invalid") from exc
        item = validated.payload
        if (
            item["origin_physical_site"] != origin_physical_site
            or int(item["writer_epoch"] or 0) != writer_epoch
            or item.get("transaction_id") != transaction_id
            or item.get("transaction_hash") != transaction_hash
        ):
            raise DurabilityJournalError("journal event does not bind the requested transaction")
        normalized.append(item)
    ordered = sorted(normalized, key=lambda item: int(item["transaction_position"]))
    if [int(item["transaction_position"]) for item in ordered] != list(
        range(1, len(ordered) + 1)
    ):
        raise DurabilityJournalError("journal transaction positions are not contiguous")
    if any(int(item["transaction_size"]) != len(ordered) for item in ordered):
        raise DurabilityJournalError("journal transaction size is inconsistent")
    ids = [item["event_id"] for item in ordered]
    if len(set(ids)) != len(ids):
        raise DurabilityJournalError("journal transaction contains a duplicate event id")
    return ordered


def build_prepare(
    *,
    envelopes: Sequence[Mapping[str, Any]],
    origin_physical_site: str,
    writer_epoch: int,
    transaction_id: str,
    transaction_hash: str,
    local_transaction_gid: str,
    release_sha: str,
    encryption_key_id: str,
    encryption_secret: str,
) -> JournalPrepare:
    """Encrypt one finalized WebApp-FI transaction for opaque remote storage."""

    if origin_physical_site != SITE_WEBAPP_FI:
        raise DurabilityJournalError("same-region journal accepts only the WebApp-FI writer")
    if isinstance(writer_epoch, bool) or int(writer_epoch) < 1:
        raise DurabilityJournalError("journal writer epoch must be positive")
    normalized_transaction_id = _require_uuid(transaction_id, field="transaction_id")
    normalized_hash = _require_sha256(transaction_hash, field="transaction_hash")
    normalized_gid = str(local_transaction_gid or "")
    if not _PREPARED_GID_RE.fullmatch(normalized_gid):
        raise DurabilityJournalError("journal local transaction GID is invalid")
    normalized_release = _require_release_sha(release_sha)
    normalized_key_id = str(encryption_key_id or "")
    if not _KEY_ID_RE.fullmatch(normalized_key_id):
        raise DurabilityJournalError("journal encryption key id is invalid")
    events = _validated_transaction_envelopes(
        envelopes,
        origin_physical_site=origin_physical_site,
        writer_epoch=int(writer_epoch),
        transaction_id=normalized_transaction_id,
        transaction_hash=normalized_hash,
    )
    metadata = {
        "origin_physical_site": origin_physical_site,
        "writer_epoch": int(writer_epoch),
        "transaction_id": normalized_transaction_id,
        "transaction_hash": normalized_hash,
        "local_transaction_gid": normalized_gid,
        "release_sha": normalized_release,
        "encryption_key_id": normalized_key_id,
        "event_ids": [item["event_id"] for item in events],
        "event_hashes": [_sha256(canonical_json_bytes(item)) for item in events],
    }
    nonce = os.urandom(12)
    plaintext = canonical_json_bytes({"schema": JOURNAL_SCHEMA, "events": events})
    ciphertext = AESGCM(_derive_encryption_key(encryption_secret)).encrypt(
        nonce, plaintext, _aad(metadata)
    )
    if len(ciphertext) > JOURNAL_MAX_CIPHERTEXT_BYTES:
        raise DurabilityJournalError("journal ciphertext exceeds the reviewed request limit")
    return JournalPrepare(
        origin_physical_site=origin_physical_site,
        writer_epoch=int(writer_epoch),
        transaction_id=normalized_transaction_id,
        transaction_hash=normalized_hash,
        local_transaction_gid=normalized_gid,
        release_sha=normalized_release,
        encryption_key_id=normalized_key_id,
        event_ids=tuple(metadata["event_ids"]),
        event_hashes=tuple(metadata["event_hashes"]),
        nonce=base64.b64encode(nonce).decode("ascii"),
        ciphertext=base64.b64encode(ciphertext).decode("ascii"),
        ciphertext_hash=_sha256(ciphertext),
    )


def parse_prepare(raw: Mapping[str, Any] | str | bytes) -> JournalPrepare:
    """Validate public journal metadata without decrypting its private payload."""

    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw, object_pairs_hook=_strict_object)
        except (json.JSONDecodeError, TypeError, DurabilityJournalError) as exc:
            raise DurabilityJournalError("journal prepare payload is not strict JSON") from exc
    required = {
        "schema", "origin_physical_site", "writer_epoch", "transaction_id",
        "transaction_hash", "local_transaction_gid", "release_sha", "encryption_key_id", "event_ids",
        "event_hashes", "nonce", "ciphertext", "ciphertext_hash",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise DurabilityJournalError("journal prepare fields are invalid")
    if raw["schema"] != JOURNAL_SCHEMA or raw["origin_physical_site"] != SITE_WEBAPP_FI:
        raise DurabilityJournalError("journal prepare source is invalid")
    if isinstance(raw["writer_epoch"], bool) or not isinstance(raw["writer_epoch"], int) or raw["writer_epoch"] < 1:
        raise DurabilityJournalError("journal writer epoch is invalid")
    local_gid = str(raw["local_transaction_gid"] or "")
    if not _PREPARED_GID_RE.fullmatch(local_gid):
        raise DurabilityJournalError("journal local transaction GID is invalid")
    key_id = str(raw["encryption_key_id"] or "")
    if not _KEY_ID_RE.fullmatch(key_id):
        raise DurabilityJournalError("journal encryption key id is invalid")
    if not isinstance(raw["event_ids"], list) or not isinstance(raw["event_hashes"], list):
        raise DurabilityJournalError("journal event identifiers are invalid")
    if not raw["event_ids"] or len(raw["event_ids"]) > JOURNAL_MAX_TRANSACTION_EVENTS:
        raise DurabilityJournalError("journal event identifier count is invalid")
    event_ids = tuple(_require_uuid(value, field="event_id") for value in raw["event_ids"])
    event_hashes = tuple(_require_sha256(value, field="event_hash") for value in raw["event_hashes"])
    if len(event_ids) != len(event_hashes) or len(set(event_ids)) != len(event_ids):
        raise DurabilityJournalError("journal event identities are inconsistent")
    nonce = _decode_base64(raw["nonce"], field="nonce", expected_length=12)
    ciphertext = _decode_base64(raw["ciphertext"], field="ciphertext")
    if not ciphertext or len(ciphertext) > JOURNAL_MAX_CIPHERTEXT_BYTES:
        raise DurabilityJournalError("journal ciphertext size is invalid")
    ciphertext_hash = _require_sha256(raw["ciphertext_hash"], field="ciphertext_hash")
    if not secrets.compare_digest(_sha256(ciphertext), ciphertext_hash):
        raise DurabilityJournalError("journal ciphertext hash mismatch")
    return JournalPrepare(
        origin_physical_site=SITE_WEBAPP_FI,
        writer_epoch=int(raw["writer_epoch"]),
        transaction_id=_require_uuid(raw["transaction_id"], field="transaction_id"),
        transaction_hash=_require_sha256(raw["transaction_hash"], field="transaction_hash"),
        local_transaction_gid=local_gid,
        release_sha=_require_release_sha(raw["release_sha"]),
        encryption_key_id=key_id,
        event_ids=event_ids,
        event_hashes=event_hashes,
        nonce=base64.b64encode(nonce).decode("ascii"),
        ciphertext=base64.b64encode(ciphertext).decode("ascii"),
        ciphertext_hash=ciphertext_hash,
    )


def parse_resolution(
    raw: Mapping[str, Any] | str | bytes,
    *,
    require_prepared_gid: bool,
) -> JournalResolution:
    """Parse a commit/rollback/status identity without exposing ciphertext."""

    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw, object_pairs_hook=_strict_object)
        except (json.JSONDecodeError, TypeError, DurabilityJournalError) as exc:
            raise DurabilityJournalError("journal resolution is not strict JSON") from exc
    required = {
        "schema", "origin_physical_site", "writer_epoch", "transaction_id", "transaction_hash",
    }
    if require_prepared_gid:
        required.add("prepared_transaction_gid")
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise DurabilityJournalError("journal resolution fields are invalid")
    if raw["schema"] != JOURNAL_SCHEMA or raw["origin_physical_site"] != SITE_WEBAPP_FI:
        raise DurabilityJournalError("journal resolution source is invalid")
    if isinstance(raw["writer_epoch"], bool) or not isinstance(raw["writer_epoch"], int) or raw["writer_epoch"] < 1:
        raise DurabilityJournalError("journal resolution writer epoch is invalid")
    gid = None
    if require_prepared_gid:
        gid = str(raw["prepared_transaction_gid"] or "")
        if not _PREPARED_GID_RE.fullmatch(gid):
            raise DurabilityJournalError("journal prepared transaction GID is invalid")
    return JournalResolution(
        origin_physical_site=SITE_WEBAPP_FI,
        writer_epoch=int(raw["writer_epoch"]),
        transaction_id=_require_uuid(raw["transaction_id"], field="transaction_id"),
        transaction_hash=_require_sha256(raw["transaction_hash"], field="transaction_hash"),
        prepared_transaction_gid=gid,
    )


def recovery_lookup_payload(*, local_transaction_gid: str) -> dict[str, Any]:
    """Build a signed recovery lookup without disclosing application data."""

    gid = str(local_transaction_gid or "")
    if not _PREPARED_GID_RE.fullmatch(gid):
        raise DurabilityJournalError("journal recovery GID is invalid")
    return {"schema": JOURNAL_SCHEMA, "local_transaction_gid": gid}


def parse_recovery_lookup(raw: Mapping[str, Any] | str | bytes) -> str:
    """Parse one strict recovery lookup request and return its local GID."""

    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw, object_pairs_hook=_strict_object)
        except (json.JSONDecodeError, TypeError, DurabilityJournalError) as exc:
            raise DurabilityJournalError("journal recovery lookup is not strict JSON") from exc
    if not isinstance(raw, Mapping) or set(raw) != {"schema", "local_transaction_gid"}:
        raise DurabilityJournalError("journal recovery lookup fields are invalid")
    if raw["schema"] != JOURNAL_SCHEMA:
        raise DurabilityJournalError("journal recovery lookup schema is invalid")
    normalized = recovery_lookup_payload(
        local_transaction_gid=str(raw["local_transaction_gid"] or "")
    )
    return str(normalized["local_transaction_gid"])


def decrypt_prepare(prepare: JournalPrepare, *, encryption_secret: str) -> tuple[dict[str, Any], ...]:
    """Decrypt and revalidate a record for a controlled recovery drill only."""

    payload = prepare.as_payload()
    try:
        plaintext = AESGCM(_derive_encryption_key(encryption_secret)).decrypt(
            _decode_base64(prepare.nonce, field="nonce", expected_length=12),
            _decode_base64(prepare.ciphertext, field="ciphertext"),
            _aad(payload),
        )
    except (InvalidTag, ValueError) as exc:
        raise DurabilityJournalError("journal ciphertext cannot be authenticated") from exc
    try:
        decoded = json.loads(plaintext, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, DurabilityJournalError) as exc:
        raise DurabilityJournalError("journal plaintext is not strict JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"schema", "events"}:
        raise DurabilityJournalError("journal plaintext fields are invalid")
    if decoded["schema"] != JOURNAL_SCHEMA or not isinstance(decoded["events"], list):
        raise DurabilityJournalError("journal plaintext schema is invalid")
    events = _validated_transaction_envelopes(
        decoded["events"],
        origin_physical_site=prepare.origin_physical_site,
        writer_epoch=prepare.writer_epoch,
        transaction_id=prepare.transaction_id,
        transaction_hash=prepare.transaction_hash,
    )
    if tuple(item["event_id"] for item in events) != prepare.event_ids:
        raise DurabilityJournalError("journal plaintext event ids do not match metadata")
    hashes = tuple(_sha256(canonical_json_bytes(item)) for item in events)
    if hashes != prepare.event_hashes:
        raise DurabilityJournalError("journal plaintext event hashes do not match metadata")
    return tuple(events)


def acknowledgement_payload(
    *,
    prepare: JournalPrepare,
    state: str,
    request_hash: str,
    receiver_site: str = SITE_BOT_FI,
    resolved_at: datetime | None = None,
    prepared_transaction_gid: str | None = None,
) -> dict[str, Any]:
    """Build the unsigned terminal/prepared acknowledgement identity."""

    if state not in {"prepared", "committed", "rolled_back", "duplicate"}:
        raise DurabilityJournalError("journal acknowledgement state is invalid")
    if receiver_site != SITE_BOT_FI:
        raise DurabilityJournalError("journal acknowledgement receiver site is invalid")
    timestamp = (resolved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = {
        "schema": JOURNAL_SCHEMA,
        "state": state,
        "receiver_site": receiver_site,
        "origin_physical_site": prepare.origin_physical_site,
        "writer_epoch": prepare.writer_epoch,
        "transaction_id": prepare.transaction_id,
        "transaction_hash": prepare.transaction_hash,
        "local_transaction_gid": prepare.local_transaction_gid,
        "ciphertext_hash": prepare.ciphertext_hash,
        "release_sha": prepare.release_sha,
        "request_hash": _require_sha256(request_hash, field="request_hash"),
        "resolved_at": timestamp.isoformat(),
    }
    if state == "committed":
        gid = str(prepared_transaction_gid or "")
        if not _PREPARED_GID_RE.fullmatch(gid):
            raise DurabilityJournalError("journal committed acknowledgement lacks a valid GID")
        result["prepared_transaction_gid"] = gid
    elif prepared_transaction_gid is not None:
        raise DurabilityJournalError("journal non-committed acknowledgement must not expose a GID")
    return result


def verify_acknowledgement(
    raw: Mapping[str, Any],
    *,
    prepare: JournalPrepare,
    request_hash: str,
    shared_secret: str,
    expected_state: str,
) -> dict[str, Any]:
    """Verify that a receiver ACK binds exactly the prepared transaction."""

    required = {
        "schema", "state", "receiver_site", "origin_physical_site", "writer_epoch",
        "transaction_id", "transaction_hash", "local_transaction_gid", "ciphertext_hash", "release_sha",
        "request_hash", "resolved_at", "acknowledgement_mac",
    }
    if expected_state == "committed":
        required.add("prepared_transaction_gid")
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise DurabilityJournalError("journal acknowledgement fields are invalid")
    unsigned = {key: raw[key] for key in required - {"acknowledgement_mac"}}
    if not acknowledgement_signature_is_valid(
        payload=unsigned,
        signature=str(raw["acknowledgement_mac"]),
        secret=str(shared_secret or ""),
    ):
        raise DurabilityJournalError("journal acknowledgement signature is invalid")
    expected = acknowledgement_payload(
        prepare=prepare,
        state=expected_state,
        request_hash=request_hash,
        resolved_at=datetime.fromisoformat(str(raw["resolved_at"]).replace("Z", "+00")),
        prepared_transaction_gid=(
            str(raw["prepared_transaction_gid"])
            if expected_state == "committed"
            else None
        ),
    )
    if any(raw.get(key) != value for key, value in expected.items()):
        raise DurabilityJournalError("journal acknowledgement does not bind the prepared transaction")
    return dict(unsigned)


def verify_recovery_acknowledgement(
    raw: Mapping[str, Any],
    *,
    local_transaction_gid: str,
    request_hash: str,
    shared_secret: str,
) -> dict[str, Any]:
    """Verify only the opaque state necessary to reconcile a local GID.

    Unlike a normal write-path ACK, recovery starts with a PostgreSQL GID and
    therefore cannot know the encrypted prepare payload in advance.  The
    signed response carries no plaintext or ciphertext; it is sufficient to
    determine the one safe terminal action for that exact GID.
    """

    expected_gid = recovery_lookup_payload(local_transaction_gid=local_transaction_gid)[
        "local_transaction_gid"
    ]
    state = raw.get("state") if isinstance(raw, Mapping) else None
    required = {
        "schema", "state", "receiver_site", "origin_physical_site", "writer_epoch",
        "transaction_id", "transaction_hash", "local_transaction_gid", "ciphertext_hash",
        "release_sha", "request_hash", "resolved_at", "acknowledgement_mac",
    }
    if state == "committed":
        required.add("prepared_transaction_gid")
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise DurabilityJournalError("journal recovery acknowledgement fields are invalid")
    unsigned = {key: raw[key] for key in required - {"acknowledgement_mac"}}
    if not acknowledgement_signature_is_valid(
        payload=unsigned,
        signature=str(raw["acknowledgement_mac"]),
        secret=str(shared_secret or ""),
    ):
        raise DurabilityJournalError("journal recovery acknowledgement signature is invalid")
    if raw["schema"] != JOURNAL_SCHEMA or raw["receiver_site"] != SITE_BOT_FI:
        raise DurabilityJournalError("journal recovery acknowledgement source is invalid")
    if raw["local_transaction_gid"] != expected_gid:
        raise DurabilityJournalError("journal recovery acknowledgement GID does not bind the request")
    if raw["state"] not in {"prepared", "committed", "rolled_back"}:
        raise DurabilityJournalError("journal recovery acknowledgement state is invalid")
    if raw["state"] == "committed" and raw["prepared_transaction_gid"] != expected_gid:
        raise DurabilityJournalError("journal recovery acknowledgement commit GID is invalid")
    if raw["request_hash"] != _require_sha256(request_hash, field="request_hash"):
        raise DurabilityJournalError("journal recovery acknowledgement request hash is invalid")
    return dict(unsigned)
