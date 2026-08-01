"""Signed, root-pinned writer-quiescence receipts for release materialization.

This is a deliberately local-only verification boundary.  It does not inspect
Git state, timestamps on files, a worktree, a process table, a lease service,
or a target directory.  In particular, it never infers quiescence from mtime,
filesystem shape, or a clean Git status.  A separate root-owned writer fence
must issue the signed receipt; this module only validates its immutable
binding before a caller is allowed to use an injected materialization adapter.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.append_only_sync_delta_batch import LEASE_ID_RE


__all__ = (
    "DEFAULT_PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_MAX_AGE_SECONDS",
    "PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_DEFAULT_ENABLED",
    "PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_SCHEMA",
    "PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_SIGNATURE_ALGORITHM",
    "PhysicalReleaseCandidateWriterQuiescenceAuthorityPin",
    "PhysicalReleaseCandidateWriterQuiescenceReceiptError",
    "PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy",
    "RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig",
    "VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt",
    "build_signed_physical_release_candidate_writer_quiescence_receipt",
    "derive_physical_release_candidate_writer_quiescence_source_root_policy_sha256",
    "require_verified_physical_release_candidate_writer_quiescence_receipt",
    "validate_root_owned_physical_release_candidate_writer_quiescence_receipt_verifier_config",
    "verify_physical_release_candidate_writer_quiescence_receipt",
)


PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_SCHEMA = (
    "gold-trade-physical-release-candidate-writer-quiescence-receipt-v1"
)
PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_SIGNATURE_ALGORITHM = "ed25519"
PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_MAX_AGE_SECONDS = 120

_SOURCE_ROOT_POLICY_SCHEMA = "gold-trade-physical-release-candidate-source-root-policy-v1"
_RECEIPT_VERSION = 1
_RECEIPT_STATUS = "writer-quiescence-receipt"
_QUIESCED_LEASE_STATE = "quiesced-no-writers"
_SIGNATURE_DOMAIN = b"gold-trade-physical-release-candidate-writer-quiescence-receipt-v1\x00"
_MAX_RECEIPT_BYTES = 16 * 1024
_MAX_RECEIPT_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "status",
        "source_root_policy_sha256",
        "inventory_manifest_sha256",
        "frozen_generation_sha256",
        "quiescence_evidence_sha256",
        "writer_lease_id",
        "writer_lease_state",
        "issued_at",
        "expires_at",
        "authority",
        "signature",
    }
)
_AUTHORITY_FIELDS = frozenset({"algorithm", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_VERIFIED_CAPABILITY = object()


class PhysicalReleaseCandidateWriterQuiescenceReceiptError(ValueError):
    """A signed source-writer quiescence receipt is unusable or unbound."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalReleaseCandidateWriterQuiescenceReceiptError(code)


@dataclass(frozen=True)
class PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy:
    """Static root policy whose digest, not path, appears in a receipt.

    The policy is an explicit root-owned configuration assertion.  It does
    not stat or otherwise inspect the path; a separate source inspector/fence
    owns that live observation.
    """

    source_root: Path | None = None
    required_owner_uid: int = 0
    required_mode: int = 0o750
    ancestors_root_controlled: bool = True
    schema: str = _SOURCE_ROOT_POLICY_SCHEMA


@dataclass(frozen=True)
class PhysicalReleaseCandidateWriterQuiescenceAuthorityPin:
    """One Ed25519 public key pinned by the root-owned verifier config."""

    public_key: bytes = b""
    key_id: str = ""
    algorithm: str = PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_SIGNATURE_ALGORITHM


@dataclass(frozen=True)
class RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig:
    """Default-off verifier policy; it never opens a receipt or a root path."""

    source_root_policy: PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy | None = None
    authority: PhysicalReleaseCandidateWriterQuiescenceAuthorityPin | None = None
    enabled: bool = PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_DEFAULT_ENABLED
    maximum_receipt_age_seconds: int = (
        DEFAULT_PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_MAX_AGE_SECONDS
    )
    direct_site_control: str = "forbidden"
    target_worktree_action: str = "forbidden"


@dataclass(frozen=True)
class VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt:
    """Opaque, revalidatable signed quiescence evidence; never an authority."""

    canonical_receipt: bytes
    receipt_sha256: str
    source_root_policy_sha256: str
    inventory_manifest_sha256: str
    frozen_generation_sha256: str
    quiescence_evidence_sha256: str
    writer_lease_id: str
    issued_at: datetime
    expires_at: datetime
    authority_key_id: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _PolicyFacts:
    source_root: Path
    source_root_policy_sha256: str
    authority_public_key: bytes
    authority_key_id: str
    maximum_receipt_age_seconds: int


@dataclass(frozen=True)
class _ReceiptFacts:
    canonical_receipt: bytes
    receipt_sha256: str
    source_root_policy_sha256: str
    inventory_manifest_sha256: str
    frozen_generation_sha256: str
    quiescence_evidence_sha256: str
    writer_lease_id: str
    issued_at: datetime
    expires_at: datetime
    authority_key_id: str


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError):
        _fail(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("WRITER_QUIESCENCE_RECEIPT_JSON_INVALID")
        result[key] = value
    return result


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = _utc(datetime.fromisoformat(value.replace("Z", "+00:00")), code=code)
    except ValueError:
        _fail(code)
    if _render_timestamp(parsed) != value:
        _fail(code)
    return parsed


def _render_timestamp(value: object) -> str:
    return _utc(value, code="WRITER_QUIESCENCE_RECEIPT_CLOCK_INVALID").isoformat().replace(
        "+00:00", "Z"
    )


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _source_root(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or value == Path("/") or ".." in value.parts:
        _fail(code)
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _public_key(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _signature(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(decoded) != 64:
        _fail(code)
    return decoded


def _policy_mapping(
    policy: PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy,
) -> dict[str, object]:
    return {
        "schema": policy.schema,
        "source_root": str(policy.source_root),
        "required_owner_uid": policy.required_owner_uid,
        "required_mode": policy.required_mode,
        "ancestors_root_controlled": policy.ancestors_root_controlled,
    }


def _source_root_policy(
    value: object,
    *,
    code: str,
) -> tuple[PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy, str]:
    if type(value) is not PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy:
        _fail(code)
    if (
        value.schema != _SOURCE_ROOT_POLICY_SCHEMA
        or value.required_owner_uid != 0
        or value.required_mode not in {0o700, 0o750}
        or value.ancestors_root_controlled is not True
    ):
        _fail(code)
    root = _source_root(value.source_root, code=code)
    normalized = PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy(
        source_root=root,
        required_owner_uid=0,
        required_mode=value.required_mode,
        ancestors_root_controlled=True,
    )
    return normalized, hashlib.sha256(_canonical(_policy_mapping(normalized), code=code)).hexdigest()


def derive_physical_release_candidate_writer_quiescence_source_root_policy_sha256(
    policy: PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy,
) -> str:
    """Return the redacted policy digest a signed receipt must bind."""

    _normalized, digest = _source_root_policy(
        policy,
        code="WRITER_QUIESCENCE_SOURCE_ROOT_POLICY_INVALID",
    )
    return digest


def _authority(
    value: object,
    *,
    code: str,
) -> tuple[bytes, str]:
    if type(value) is not PhysicalReleaseCandidateWriterQuiescenceAuthorityPin:
        _fail(code)
    public_key = _public_key(value.public_key, code=code)
    if (
        value.algorithm != PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_SIGNATURE_ALGORITHM
        or type(value.key_id) is not str
        or _KEY_ID_RE.fullmatch(value.key_id) is None
        or value.key_id != _key_id(public_key)
    ):
        _fail(code)
    return public_key, value.key_id


def _positive_age(value: object, *, code: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_RECEIPT_AGE_SECONDS:
        _fail(code)
    return value


def _config_facts(
    value: object,
    *,
    require_enabled: bool,
) -> _PolicyFacts:
    if type(value) is not RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig:
        _fail("WRITER_QUIESCENCE_VERIFIER_CONFIG_INVALID")
    if (
        type(value.enabled) is not bool
        or (require_enabled and value.enabled is not True)
        or value.direct_site_control != "forbidden"
        or value.target_worktree_action != "forbidden"
    ):
        _fail(
            "WRITER_QUIESCENCE_VERIFIER_DISABLED"
            if require_enabled and value.enabled is not True
            else "WRITER_QUIESCENCE_VERIFIER_CONFIG_INVALID"
        )
    policy, policy_digest = _source_root_policy(
        value.source_root_policy,
        code="WRITER_QUIESCENCE_SOURCE_ROOT_POLICY_INVALID",
    )
    public_key, key_id = _authority(value.authority, code="WRITER_QUIESCENCE_AUTHORITY_PIN_INVALID")
    return _PolicyFacts(
        source_root=policy.source_root,
        source_root_policy_sha256=policy_digest,
        authority_public_key=public_key,
        authority_key_id=key_id,
        maximum_receipt_age_seconds=_positive_age(
            value.maximum_receipt_age_seconds,
            code="WRITER_QUIESCENCE_VERIFIER_CONFIG_INVALID",
        ),
    )


def validate_root_owned_physical_release_candidate_writer_quiescence_receipt_verifier_config(
    config: RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig,
) -> RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig:
    """Validate static root policy only; no path, receipt, or target is opened."""

    _config_facts(config, require_enabled=False)
    return config


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _parse_canonical_receipt(value: object, *, code: str) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, bytes) or not 1 <= len(value) <= _MAX_RECEIPT_BYTES:
        _fail(code)
    try:
        mapping = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: _fail("WRITER_QUIESCENCE_RECEIPT_JSON_INVALID"),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail(code)
    if type(mapping) is not dict or _canonical(mapping, code=code) != value:
        _fail(code)
    return mapping, value


def _receipt_facts(
    value: object,
    *,
    policy: _PolicyFacts,
    source_root: Path,
    inventory_manifest_sha256: str,
    frozen_generation_sha256: str,
    quiescence_evidence_sha256: str,
    now: datetime,
) -> _ReceiptFacts:
    mapping, raw = _parse_canonical_receipt(value, code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    item = _exact_mapping(mapping, fields=_RECEIPT_FIELDS, code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    if (
        item["schema"] != PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_SCHEMA
        or item["version"] != _RECEIPT_VERSION
        or item["status"] != _RECEIPT_STATUS
        or item["source_root_policy_sha256"] != policy.source_root_policy_sha256
        or item["inventory_manifest_sha256"] != inventory_manifest_sha256
        or item["frozen_generation_sha256"] != frozen_generation_sha256
        or item["quiescence_evidence_sha256"] != quiescence_evidence_sha256
        or item["writer_lease_state"] != _QUIESCED_LEASE_STATE
        or source_root != policy.source_root
    ):
        _fail("WRITER_QUIESCENCE_RECEIPT_BINDING_MISMATCH")
    policy_digest = _sha256(
        item["source_root_policy_sha256"],
        code="WRITER_QUIESCENCE_RECEIPT_INVALID",
    )
    inventory_digest = _sha256(item["inventory_manifest_sha256"], code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    generation_digest = _sha256(item["frozen_generation_sha256"], code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    evidence_digest = _sha256(item["quiescence_evidence_sha256"], code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    if type(item["writer_lease_id"]) is not str or LEASE_ID_RE.fullmatch(item["writer_lease_id"]) is None:
        _fail("WRITER_QUIESCENCE_RECEIPT_INVALID")
    issued_at = _timestamp(item["issued_at"], code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    expires_at = _timestamp(item["expires_at"], code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    if (
        expires_at <= issued_at
        or expires_at - issued_at > timedelta(seconds=policy.maximum_receipt_age_seconds)
        or issued_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now - issued_at > timedelta(seconds=policy.maximum_receipt_age_seconds)
    ):
        _fail("WRITER_QUIESCENCE_RECEIPT_STALE_OR_FUTURE")
    if expires_at <= now:
        _fail("WRITER_QUIESCENCE_RECEIPT_EXPIRED")
    authority = _exact_mapping(item["authority"], fields=_AUTHORITY_FIELDS, code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    signature = _exact_mapping(item["signature"], fields=_SIGNATURE_FIELDS, code="WRITER_QUIESCENCE_RECEIPT_INVALID")
    if (
        authority["algorithm"] != PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_SIGNATURE_ALGORITHM
        or authority["key_id"] != policy.authority_key_id
        or signature["algorithm"] != PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_SIGNATURE_ALGORITHM
    ):
        _fail("WRITER_QUIESCENCE_RECEIPT_AUTHORITY_MISMATCH")
    unsigned = dict(item)
    signature_text = _exact_mapping(
        unsigned.pop("signature"),
        fields=_SIGNATURE_FIELDS,
        code="WRITER_QUIESCENCE_RECEIPT_INVALID",
    )["signature_base64"]
    try:
        Ed25519PublicKey.from_public_bytes(policy.authority_public_key).verify(
            _signature(signature_text, code="WRITER_QUIESCENCE_RECEIPT_SIGNATURE_INVALID"),
            _SIGNATURE_DOMAIN + _canonical(unsigned, code="WRITER_QUIESCENCE_RECEIPT_INVALID"),
        )
    except (InvalidSignature, ValueError):
        _fail("WRITER_QUIESCENCE_RECEIPT_SIGNATURE_INVALID")
    return _ReceiptFacts(
        canonical_receipt=raw,
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
        source_root_policy_sha256=policy_digest,
        inventory_manifest_sha256=inventory_digest,
        frozen_generation_sha256=generation_digest,
        quiescence_evidence_sha256=evidence_digest,
        writer_lease_id=item["writer_lease_id"],
        issued_at=issued_at,
        expires_at=expires_at,
        authority_key_id=policy.authority_key_id,
    )


def verify_physical_release_candidate_writer_quiescence_receipt(
    value: object,
    *,
    config: RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig,
    source_root: Path,
    inventory_manifest_sha256: str,
    frozen_generation_sha256: str,
    quiescence_evidence_sha256: str,
    now: datetime,
) -> VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt:
    """Verify one signed, fresh source-quiescence receipt without any I/O."""

    policy = _config_facts(config, require_enabled=True)
    checked_source_root = _source_root(source_root, code="WRITER_QUIESCENCE_RECEIPT_BINDING_MISMATCH")
    facts = _receipt_facts(
        value,
        policy=policy,
        source_root=checked_source_root,
        inventory_manifest_sha256=_sha256(
            inventory_manifest_sha256,
            code="WRITER_QUIESCENCE_RECEIPT_BINDING_MISMATCH",
        ),
        frozen_generation_sha256=_sha256(
            frozen_generation_sha256,
            code="WRITER_QUIESCENCE_RECEIPT_BINDING_MISMATCH",
        ),
        quiescence_evidence_sha256=_sha256(
            quiescence_evidence_sha256,
            code="WRITER_QUIESCENCE_RECEIPT_BINDING_MISMATCH",
        ),
        now=_utc(now, code="WRITER_QUIESCENCE_RECEIPT_CLOCK_INVALID"),
    )
    result = VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt(
        canonical_receipt=facts.canonical_receipt,
        receipt_sha256=facts.receipt_sha256,
        source_root_policy_sha256=facts.source_root_policy_sha256,
        inventory_manifest_sha256=facts.inventory_manifest_sha256,
        frozen_generation_sha256=facts.frozen_generation_sha256,
        quiescence_evidence_sha256=facts.quiescence_evidence_sha256,
        writer_lease_id=facts.writer_lease_id,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        authority_key_id=facts.authority_key_id,
    )
    object.__setattr__(result, "_capability", _VERIFIED_CAPABILITY)
    return result


def require_verified_physical_release_candidate_writer_quiescence_receipt(
    value: object,
    *,
    config: RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig,
    source_root: Path,
    inventory_manifest_sha256: str,
    frozen_generation_sha256: str,
    quiescence_evidence_sha256: str,
    now: datetime,
) -> VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt:
    """Revalidate opaque receipt evidence at the exact materialization gate."""

    if (
        type(value) is not VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt
        or value._capability is not _VERIFIED_CAPABILITY
    ):
        _fail("VERIFIED_WRITER_QUIESCENCE_RECEIPT_REQUIRED")
    checked = verify_physical_release_candidate_writer_quiescence_receipt(
        value.canonical_receipt,
        config=config,
        source_root=source_root,
        inventory_manifest_sha256=inventory_manifest_sha256,
        frozen_generation_sha256=frozen_generation_sha256,
        quiescence_evidence_sha256=quiescence_evidence_sha256,
        now=now,
    )
    if (
        checked.canonical_receipt != value.canonical_receipt
        or checked.receipt_sha256 != value.receipt_sha256
        or checked.source_root_policy_sha256 != value.source_root_policy_sha256
        or checked.inventory_manifest_sha256 != value.inventory_manifest_sha256
        or checked.frozen_generation_sha256 != value.frozen_generation_sha256
        or checked.quiescence_evidence_sha256 != value.quiescence_evidence_sha256
        or checked.writer_lease_id != value.writer_lease_id
        or checked.issued_at != value.issued_at
        or checked.expires_at != value.expires_at
        or checked.authority_key_id != value.authority_key_id
    ):
        _fail("VERIFIED_WRITER_QUIESCENCE_RECEIPT_TAMPERED")
    return value


def _private_signer(value: object) -> tuple[Ed25519PrivateKey, str]:
    if not isinstance(value, Ed25519PrivateKey):
        _fail("WRITER_QUIESCENCE_RECEIPT_SIGNER_INVALID")
    try:
        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError:
        _fail("WRITER_QUIESCENCE_RECEIPT_SIGNER_INVALID")
    return value, _key_id(_public_key(public_key, code="WRITER_QUIESCENCE_RECEIPT_SIGNER_INVALID"))


def build_signed_physical_release_candidate_writer_quiescence_receipt(
    *,
    source_root_policy: PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy,
    inventory_manifest_sha256: str,
    frozen_generation_sha256: str,
    quiescence_evidence_sha256: str,
    writer_lease_id: str,
    issued_at: datetime,
    expires_at: datetime,
    authority_signer: Ed25519PrivateKey,
) -> bytes:
    """Build canonical signed evidence for a separately owned root fence.

    This helper does not discover a writer state.  The caller must already
    possess the private authority key and have independently established the
    quiescent state it signs.
    """

    _policy, policy_digest = _source_root_policy(
        source_root_policy,
        code="WRITER_QUIESCENCE_SOURCE_ROOT_POLICY_INVALID",
    )
    issued = _utc(issued_at, code="WRITER_QUIESCENCE_RECEIPT_CLOCK_INVALID")
    expires = _utc(expires_at, code="WRITER_QUIESCENCE_RECEIPT_CLOCK_INVALID")
    if expires <= issued or expires - issued > timedelta(seconds=_MAX_RECEIPT_AGE_SECONDS):
        _fail("WRITER_QUIESCENCE_RECEIPT_INVALID")
    if type(writer_lease_id) is not str or LEASE_ID_RE.fullmatch(writer_lease_id) is None:
        _fail("WRITER_QUIESCENCE_RECEIPT_INVALID")
    signer, key_id = _private_signer(authority_signer)
    unsigned: dict[str, Any] = {
        "schema": PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_RECEIPT_SCHEMA,
        "version": _RECEIPT_VERSION,
        "status": _RECEIPT_STATUS,
        "source_root_policy_sha256": policy_digest,
        "inventory_manifest_sha256": _sha256(
            inventory_manifest_sha256,
            code="WRITER_QUIESCENCE_RECEIPT_INVALID",
        ),
        "frozen_generation_sha256": _sha256(
            frozen_generation_sha256,
            code="WRITER_QUIESCENCE_RECEIPT_INVALID",
        ),
        "quiescence_evidence_sha256": _sha256(
            quiescence_evidence_sha256,
            code="WRITER_QUIESCENCE_RECEIPT_INVALID",
        ),
        "writer_lease_id": writer_lease_id,
        "writer_lease_state": _QUIESCED_LEASE_STATE,
        "issued_at": _render_timestamp(issued),
        "expires_at": _render_timestamp(expires),
        "authority": {
            "algorithm": PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_SIGNATURE_ALGORITHM,
            "key_id": key_id,
        },
    }
    try:
        signature = signer.sign(_SIGNATURE_DOMAIN + _canonical(unsigned, code="WRITER_QUIESCENCE_RECEIPT_INVALID"))
    except ValueError:
        _fail("WRITER_QUIESCENCE_RECEIPT_SIGNER_INVALID")
    if not isinstance(signature, bytes) or len(signature) != 64:
        _fail("WRITER_QUIESCENCE_RECEIPT_SIGNER_INVALID")
    return _canonical(
        {
            **unsigned,
            "signature": {
                "algorithm": PHYSICAL_RELEASE_CANDIDATE_WRITER_QUIESCENCE_SIGNATURE_ALGORITHM,
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
        },
        code="WRITER_QUIESCENCE_RECEIPT_INVALID",
    )
