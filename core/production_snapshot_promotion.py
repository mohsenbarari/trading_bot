"""Bounded-RPO snapshot receipt gate for host-level Writer Witness promotion.

The legacy production release does not depend on the later DR-event runtime.
This compact contract accepts only a root-owned restore receipt produced after
an immutable, versioned Object Storage download, age decryption, and hash
verification.  It performs no Object Storage I/O itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any
from uuid import UUID


RESTORE_RECEIPT_SCHEMA = "gold-trade-snapshot-restore-receipt-v1"
PROMOTION_PROOF_SCHEMA = "gold-trade-writer-promotion-proof-v1"
MAX_PROMOTION_SNAPSHOT_AGE_SECONDS = 30
MAX_RECEIPT_FUTURE_SKEW = timedelta(seconds=5)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ASCII_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{0,1023}$")
VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._=+/:@-]{1,512}$")
PROMOTION_PROOF_FIELDS = frozenset(
    {
        "schema",
        "action",
        "operation_id",
        "source_site",
        "target_site",
        "snapshot_id",
        "source_generation",
        "release_sha",
        "alembic_revision",
        "snapshot_age_seconds",
        "source_db_snapshot_started_at",
        "source_capture_completed_at",
        "snapshot_published_at",
        "snapshot_ready_at",
        "snapshot_restore_receipt_sha256",
        "snapshot_stage_receipt_sha256",
        "lease_id",
        "epoch",
        "issued_at",
        "lease_expires_at",
        "witness_proof_sha256",
        "proof_sha256",
    }
)


class SnapshotPromotionError(RuntimeError):
    """Raised when a snapshot cannot safely support a writer transition."""


@dataclass(frozen=True)
class SnapshotRestoreReceipt:
    source_site: str
    destination_site: str
    source_generation: str
    snapshot_id: str
    release_sha: str
    alembic_revision: str
    source_db_snapshot_started_at: datetime
    source_capture_completed_at: datetime
    published_at: datetime
    ready_at: datetime
    restore_verified_at: datetime
    snapshot_age_seconds: int
    stage_receipt_sha256: str
    database_sha256: str
    uploads_sha256: str
    receipt_sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    """Encode the exact JSON byte representation used by receipt/proof hashes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SnapshotPromotionError("snapshot receipt is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotPromotionError("snapshot receipt contains duplicate JSON keys")
        result[key] = value
    return result


def loads_strict_receipt(raw: bytes | str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except Exception as exc:
        raise SnapshotPromotionError("snapshot receipt is invalid JSON") from exc
    if not isinstance(value, dict):
        raise SnapshotPromotionError("snapshot receipt must be an object")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotPromotionError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotPromotionError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise SnapshotPromotionError(f"{label} lacks timezone")
    return parsed.astimezone(timezone.utc)


def _hash(value: Any, *, label: str) -> str:
    normalized = str(value or "").lower()
    if not HASH_RE.fullmatch(normalized):
        raise SnapshotPromotionError(f"{label} must be SHA-256")
    return normalized


def _ascii_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not ASCII_ID_RE.fullmatch(value):
        raise SnapshotPromotionError(f"{label} is invalid")
    return value


def _artifact(value: Any, *, label: str, plaintext: bool) -> dict[str, Any]:
    fields = (
        {"sha256", "bytes", "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes"}
        if plaintext
        else {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes"}
    )
    if not isinstance(value, dict) or set(value) != fields:
        raise SnapshotPromotionError(f"{label} fields are invalid")
    key = value.get("object_key")
    version = value.get("version_id")
    if not isinstance(key, str) or not OBJECT_KEY_RE.fullmatch(key) or ".." in key.split("/"):
        raise SnapshotPromotionError(f"{label} object key is invalid")
    if not isinstance(version, str) or not VERSION_ID_RE.fullmatch(version):
        raise SnapshotPromotionError(f"{label} version id is invalid")
    _hash(value.get("ciphertext_sha256"), label=f"{label} ciphertext_sha256")
    if type(value.get("ciphertext_bytes")) is not int or value["ciphertext_bytes"] < 0:
        raise SnapshotPromotionError(f"{label} ciphertext bytes are invalid")
    if plaintext:
        _hash(value.get("sha256"), label=f"{label} sha256")
        if type(value.get("bytes")) is not int or value["bytes"] < 0:
            raise SnapshotPromotionError(f"{label} bytes are invalid")
    return dict(value)


def _action_sites(action: str) -> tuple[str, str]:
    if action == "promote_ir":
        return "webapp_fi", "webapp_ir"
    if action == "failback_fi":
        return "webapp_ir", "webapp_fi"
    raise SnapshotPromotionError("promotion action is invalid")


def parse_restore_receipt(
    payload: dict[str, Any],
    *,
    action: str,
    now: datetime | None = None,
    expected_source_generation: str | None = None,
) -> SnapshotRestoreReceipt:
    """Validate one explicit, fresh Object Storage restore direction.

    Promotion accepts only a source DB snapshot start no older than 30 seconds.
    Failback additionally pins the final IR source generation, preventing a
    route back to FI based on an earlier IR snapshot.
    """

    expected_fields = {
        "schema",
        "status",
        "source_site",
        "destination_site",
        "source_generation",
        "snapshot_id",
        "release_sha",
        "alembic_revision",
        "source_db_snapshot_started_at",
        "source_capture_completed_at",
        "published_at",
        "ready_at",
        "restored_at",
        "restore_verified_at",
        "stage_receipt_sha256",
        "restored_database_sha256",
        "restored_uploads_sha256",
        "database",
        "uploads",
        "manifest",
        "receipt_sha256",
    }
    if set(payload) != expected_fields or payload.get("schema") != RESTORE_RECEIPT_SCHEMA:
        raise SnapshotPromotionError("snapshot restore receipt schema is invalid")
    if payload.get("status") != "restored_verified":
        raise SnapshotPromotionError("snapshot restore receipt is not verified")
    source_site, destination_site = _action_sites(action)
    if payload.get("source_site") != source_site or payload.get("destination_site") != destination_site:
        raise SnapshotPromotionError("snapshot restore receipt direction is invalid")
    generation = _ascii_id(payload.get("source_generation"), label="source_generation")
    if expected_source_generation is not None and generation != _ascii_id(
        expected_source_generation, label="expected_source_generation"
    ):
        raise SnapshotPromotionError("snapshot restore receipt does not match final source generation")
    snapshot_id = _ascii_id(payload.get("snapshot_id"), label="snapshot_id")
    release_sha = payload.get("release_sha")
    if not isinstance(release_sha, str) or not RELEASE_SHA_RE.fullmatch(release_sha):
        raise SnapshotPromotionError("release_sha is invalid")
    alembic_revision = _ascii_id(payload.get("alembic_revision"), label="alembic_revision")
    source_db_snapshot_started_at = _timestamp(
        payload.get("source_db_snapshot_started_at"), label="source_db_snapshot_started_at"
    )
    source_capture_completed_at = _timestamp(
        payload.get("source_capture_completed_at"), label="source_capture_completed_at"
    )
    published_at = _timestamp(payload.get("published_at"), label="snapshot published_at")
    ready_at = _timestamp(payload.get("ready_at"), label="snapshot ready_at")
    restored_at = _timestamp(payload.get("restored_at"), label="snapshot restored_at")
    restore_verified_at = _timestamp(
        payload.get("restore_verified_at"), label="snapshot restore_verified_at"
    )
    if not (
        source_db_snapshot_started_at
        <= source_capture_completed_at
        <= published_at
        <= ready_at
        <= restored_at
        <= restore_verified_at
    ):
        raise SnapshotPromotionError("snapshot receipt timestamps are inconsistent")
    database = _artifact(payload.get("database"), label="database artifact", plaintext=True)
    uploads = _artifact(payload.get("uploads"), label="uploads artifact", plaintext=True)
    _artifact(payload.get("manifest"), label="manifest artifact", plaintext=False)
    database_hash = _hash(payload.get("restored_database_sha256"), label="restored_database_sha256")
    uploads_hash = _hash(payload.get("restored_uploads_sha256"), label="restored_uploads_sha256")
    if database_hash != _hash(database.get("sha256"), label="database sha256"):
        raise SnapshotPromotionError("restored database hash differs from staged snapshot")
    if uploads_hash != _hash(uploads.get("sha256"), label="uploads sha256"):
        raise SnapshotPromotionError("restored uploads hash differs from staged snapshot")
    stage_hash = _hash(payload.get("stage_receipt_sha256"), label="stage_receipt_sha256")
    receipt_hash = _hash(payload.get("receipt_sha256"), label="receipt_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != receipt_hash:
        raise SnapshotPromotionError("snapshot restore receipt hash is invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if any(
        value > current + MAX_RECEIPT_FUTURE_SKEW
        for value in (
            source_db_snapshot_started_at,
            source_capture_completed_at,
            published_at,
            ready_at,
            restored_at,
            restore_verified_at,
        )
    ):
        raise SnapshotPromotionError("snapshot restore receipt is in the future")
    actual_age = max(0, math.ceil((current - source_db_snapshot_started_at).total_seconds()))
    if actual_age > MAX_PROMOTION_SNAPSHOT_AGE_SECONDS:
        raise SnapshotPromotionError("snapshot restore receipt exceeds the 30 second promotion RPO")
    return SnapshotRestoreReceipt(
        source_site=source_site,
        destination_site=destination_site,
        source_generation=generation,
        snapshot_id=snapshot_id,
        release_sha=release_sha,
        alembic_revision=alembic_revision,
        source_db_snapshot_started_at=source_db_snapshot_started_at,
        source_capture_completed_at=source_capture_completed_at,
        published_at=published_at,
        ready_at=ready_at,
        restore_verified_at=restore_verified_at,
        snapshot_age_seconds=actual_age,
        stage_receipt_sha256=stage_hash,
        database_sha256=database_hash,
        uploads_sha256=uploads_hash,
        receipt_sha256=receipt_hash,
    )


def build_promotion_proof(
    *,
    action: str,
    operation_id: str,
    snapshot: SnapshotRestoreReceipt,
    witness_proof: dict[str, Any],
) -> dict[str, Any]:
    """Produce the non-secret, self-hashed proof that routing binds to activation."""

    source_site, target_site = _action_sites(action)
    try:
        normalized_operation = str(UUID(str(operation_id)))
    except (ValueError, TypeError) as exc:
        raise SnapshotPromotionError("operation_id must be a UUID") from exc
    if normalized_operation != operation_id:
        raise SnapshotPromotionError("operation_id must be canonical")
    if witness_proof.get("holder_site") != target_site or type(witness_proof.get("writer_epoch")) is not int:
        raise SnapshotPromotionError("Witness proof target is invalid")
    lease_id = witness_proof.get("lease_id")
    if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 128:
        raise SnapshotPromotionError("Witness lease id is invalid")
    issued_at = _timestamp(witness_proof.get("issued_at"), label="Witness proof issued_at")
    lease_expires_at = _timestamp(witness_proof.get("expires_at"), label="Witness proof expires_at")
    if lease_expires_at <= issued_at:
        raise SnapshotPromotionError("Witness proof lifetime is invalid")
    proof = {
        "schema": PROMOTION_PROOF_SCHEMA,
        "action": action,
        "operation_id": normalized_operation,
        "source_site": source_site,
        "target_site": target_site,
        "snapshot_id": snapshot.snapshot_id,
        "source_generation": snapshot.source_generation,
        "release_sha": snapshot.release_sha,
        "alembic_revision": snapshot.alembic_revision,
        "snapshot_age_seconds": snapshot.snapshot_age_seconds,
        "source_db_snapshot_started_at": snapshot.source_db_snapshot_started_at.isoformat(),
        "source_capture_completed_at": snapshot.source_capture_completed_at.isoformat(),
        "snapshot_published_at": snapshot.published_at.isoformat(),
        "snapshot_ready_at": snapshot.ready_at.isoformat(),
        "snapshot_restore_receipt_sha256": snapshot.receipt_sha256,
        "snapshot_stage_receipt_sha256": snapshot.stage_receipt_sha256,
        "lease_id": lease_id,
        "epoch": witness_proof["writer_epoch"],
        "issued_at": issued_at.isoformat(),
        "lease_expires_at": lease_expires_at.isoformat(),
        "witness_proof_sha256": hashlib.sha256(canonical_json_bytes(witness_proof)).hexdigest(),
    }
    return {
        **proof,
        "proof_sha256": hashlib.sha256(canonical_json_bytes(proof)).hexdigest(),
    }


def validate_promotion_proof(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the immutable non-secret proof before a routing transition.

    ``now`` additionally requires the Witness term to still be live.  Routing
    and the automatic IR watcher use that mode; archival verification can omit
    it while still checking the exact schema and self-hash.
    """

    if not isinstance(payload, dict) or set(payload) != PROMOTION_PROOF_FIELDS:
        raise SnapshotPromotionError("promotion proof schema is invalid")
    if payload.get("schema") != PROMOTION_PROOF_SCHEMA:
        raise SnapshotPromotionError("promotion proof version is invalid")
    source_site, target_site = _action_sites(payload.get("action"))
    if payload.get("source_site") != source_site or payload.get("target_site") != target_site:
        raise SnapshotPromotionError("promotion proof direction is invalid")
    try:
        operation_id = str(UUID(str(payload.get("operation_id"))))
    except (ValueError, TypeError) as exc:
        raise SnapshotPromotionError("promotion proof operation_id is invalid") from exc
    if operation_id != payload.get("operation_id"):
        raise SnapshotPromotionError("promotion proof operation_id is not canonical")
    _ascii_id(payload.get("snapshot_id"), label="promotion proof snapshot_id")
    _ascii_id(payload.get("source_generation"), label="promotion proof source_generation")
    release_sha = payload.get("release_sha")
    if not isinstance(release_sha, str) or not RELEASE_SHA_RE.fullmatch(release_sha):
        raise SnapshotPromotionError("promotion proof release_sha is invalid")
    _ascii_id(payload.get("alembic_revision"), label="promotion proof alembic_revision")
    if (
        type(payload.get("snapshot_age_seconds")) is not int
        or payload["snapshot_age_seconds"] < 0
        or payload["snapshot_age_seconds"] > MAX_PROMOTION_SNAPSHOT_AGE_SECONDS
    ):
        raise SnapshotPromotionError("promotion proof snapshot age is invalid")
    source_db_snapshot_started_at = _timestamp(
        payload.get("source_db_snapshot_started_at"),
        label="promotion proof source_db_snapshot_started_at",
    )
    source_capture_completed_at = _timestamp(
        payload.get("source_capture_completed_at"),
        label="promotion proof source_capture_completed_at",
    )
    snapshot_published_at = _timestamp(
        payload.get("snapshot_published_at"), label="promotion proof snapshot_published_at"
    )
    snapshot_ready_at = _timestamp(
        payload.get("snapshot_ready_at"), label="promotion proof snapshot_ready_at"
    )
    issued_at = _timestamp(payload.get("issued_at"), label="promotion proof issued_at")
    lease_expires_at = _timestamp(
        payload.get("lease_expires_at"), label="promotion proof lease_expires_at"
    )
    if (
        not (
            source_db_snapshot_started_at
            <= source_capture_completed_at
            <= snapshot_published_at
            <= snapshot_ready_at
        )
        or lease_expires_at <= issued_at
    ):
        raise SnapshotPromotionError("promotion proof timestamps are inconsistent")
    lease_id = payload.get("lease_id")
    if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 128:
        raise SnapshotPromotionError("promotion proof lease_id is invalid")
    if type(payload.get("epoch")) is not int or payload["epoch"] < 1:
        raise SnapshotPromotionError("promotion proof epoch is invalid")
    for field in (
        "snapshot_restore_receipt_sha256",
        "snapshot_stage_receipt_sha256",
        "witness_proof_sha256",
        "proof_sha256",
    ):
        _hash(payload.get(field), label=f"promotion proof {field}")
    unsigned = {key: value for key, value in payload.items() if key != "proof_sha256"}
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != payload["proof_sha256"]:
        raise SnapshotPromotionError("promotion proof hash is invalid")
    if now is not None:
        current = now.astimezone(timezone.utc)
        if (
            any(
                value > current + MAX_RECEIPT_FUTURE_SKEW
                for value in (
                    source_db_snapshot_started_at,
                    source_capture_completed_at,
                    snapshot_published_at,
                    snapshot_ready_at,
                    issued_at,
                )
            )
            or lease_expires_at <= current
        ):
            raise SnapshotPromotionError("promotion proof Witness term is not live")
        actual_age = max(0, math.ceil((current - source_db_snapshot_started_at).total_seconds()))
        if actual_age > MAX_PROMOTION_SNAPSHOT_AGE_SECONDS:
            raise SnapshotPromotionError("promotion proof exceeds the 30 second DB snapshot RPO")
    return dict(payload)
