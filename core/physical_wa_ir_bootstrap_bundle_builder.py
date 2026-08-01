"""Local-only, fail-closed preparation for a fresh WA-IR bootstrap descriptor.

This module prepares *metadata only* for the first encrypted WA-IR artifact
stage bootstrap.  It deliberately cannot read a release archive, encrypt
anything, publish an Object, call S3, generate a URL, contact WA-IR or WA-FI,
open SSH, run Docker, or execute a shell command.  Its sole durable effect is
a root-only one-use freshness marker made after all local inputs validate.

The normal path is intentionally narrow::

    sealed exact release + fresh campaign recipient + new exact Object locator
        -> prepared local descriptor (not a publish or execution permit)

Every input is public metadata.  In particular, neither an age private key nor
an identity filename is accepted anywhere in this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import UUID

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE


__all__ = (
    "DEFAULT_PHYSICAL_WA_IR_BOOTSTRAP_FRESHNESS_AGE_SECONDS",
    "MAX_PHYSICAL_WA_IR_BOOTSTRAP_CIPHERTEXT_BYTES",
    "MAX_PHYSICAL_WA_IR_BOOTSTRAP_PLAINTEXT_BYTES",
    "PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_DEFAULT_ENABLED",
    "PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_SCHEMA",
    "PHYSICAL_WA_IR_BOOTSTRAP_DESCRIPTOR_SCHEMA",
    "PHYSICAL_WA_IR_BOOTSTRAP_EXACT_RELEASE_BINDING_SCHEMA",
    "PHYSICAL_WA_IR_BOOTSTRAP_FRESH_RECIPIENT_SCHEMA",
    "PHYSICAL_WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_SCHEMA",
    "PreparedPhysicalWaIrBootstrapDescriptor",
    "PhysicalWaIrBootstrapBundleBuilderConfig",
    "PhysicalWaIrBootstrapBundleBuilderError",
    "SealedWaIrBootstrapExactReleaseBinding",
    "VerifiedWaIrBootstrapFreshAgeRecipient",
    "VerifiedWaIrBootstrapImmutableObjectLocatorExpectation",
    "WaIrBootstrapExactReleaseBinding",
    "WaIrBootstrapFreshAgeRecipient",
    "WaIrBootstrapImmutableObjectLocatorExpectation",
    "expected_wa_ir_bootstrap_object_key",
    "prepare_fresh_wa_ir_bootstrap_descriptor",
    "require_prepared_physical_wa_ir_bootstrap_descriptor",
    "require_sealed_wa_ir_bootstrap_exact_release_binding",
    "require_verified_wa_ir_bootstrap_fresh_age_recipient",
    "require_verified_wa_ir_bootstrap_immutable_locator_expectation",
    "review_fresh_wa_ir_bootstrap_descriptor",
    "seal_wa_ir_bootstrap_exact_release_binding",
    "validate_physical_wa_ir_bootstrap_bundle_builder_config",
    "verify_wa_ir_bootstrap_fresh_age_recipient",
    "verify_wa_ir_bootstrap_immutable_locator_expectation",
)


PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_SCHEMA = (
    "gold-trade-physical-wa-ir-bootstrap-bundle-builder-v1"
)
PHYSICAL_WA_IR_BOOTSTRAP_DESCRIPTOR_SCHEMA = (
    "gold-trade-physical-wa-ir-bootstrap-descriptor-v1"
)
PHYSICAL_WA_IR_BOOTSTRAP_EXACT_RELEASE_BINDING_SCHEMA = (
    "gold-trade-physical-wa-ir-bootstrap-exact-release-binding-v1"
)
PHYSICAL_WA_IR_BOOTSTRAP_FRESH_RECIPIENT_SCHEMA = (
    "gold-trade-physical-wa-ir-bootstrap-fresh-recipient-v1"
)
PHYSICAL_WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_SCHEMA = (
    "gold-trade-physical-wa-ir-bootstrap-immutable-locator-v1"
)
PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_DEFAULT_ENABLED = False

DEFAULT_PHYSICAL_WA_IR_BOOTSTRAP_FRESHNESS_AGE_SECONDS = 180
MAX_PHYSICAL_WA_IR_BOOTSTRAP_FRESHNESS_AGE_SECONDS = 300
MAX_PHYSICAL_WA_IR_BOOTSTRAP_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_WA_IR_BOOTSTRAP_PLAINTEXT_BYTES = 8 * 1024 * 1024
MAX_PHYSICAL_WA_IR_BOOTSTRAP_CIPHERTEXT_BYTES = (
    MAX_PHYSICAL_WA_IR_BOOTSTRAP_PLAINTEXT_BYTES + 2 * 1024 * 1024
)

_SOURCE_SITE = "webapp_fi"
_DESTINATION_SITE = "webapp_ir"
_EXPECTED_ENCRYPTION = "age-v1"
_EXPECTED_IMMUTABILITY = "versioned-create-only-readback-v1"
_STATUS_PREPARED = "prepared-local-only"
_FRESHNESS_DIRECTORY = "physical-wa-ir-bootstrap-bundle-builder"
_FIXED_OBJECT_SUFFIX = "stage-bootstrap.tar.age"

_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,255}$", re.ASCII)
_MUTABLE_ALIASES = frozenset({"alias", "current", "head", "latest", "pointer", "null", "undefined"})
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_SEALED_RELEASE_CAPABILITY = object()
_VERIFIED_RECIPIENT_CAPABILITY = object()
_VERIFIED_LOCATOR_CAPABILITY = object()
_PREPARED_DESCRIPTOR_CAPABILITY = object()

_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "control_release_sha",
        "sealed_release_binding_sha256",
        "release_bundle_sha256",
        "image_set_sha256",
        "release_provenance_sha256",
        "age_recipient",
        "recipient_public_sha256",
        "recipient_generation_id",
        "bootstrap_id",
        "locator_id",
        "locator_sha256",
        "object",
        "prepared_at",
        "direct_fi_to_ir_control",
        "publish_authorized",
        "execution_authorized",
        "descriptor_sha256",
    }
)
class PhysicalWaIrBootstrapBundleBuilderError(ValueError):
    """A fixed-code refusal from the local WA-IR bootstrap preparation seam."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WaIrBootstrapExactReleaseBinding:
    """Public, exact release facts that must be explicitly sealed first."""

    campaign_id: str
    release_sha: str
    control_release_sha: str
    release_bundle_sha256: str
    image_set_sha256: str
    release_provenance_sha256: str
    source_site: str
    destination_site: str
    seal_id: UUID
    sealed_at: datetime


@dataclass(frozen=True)
class SealedWaIrBootstrapExactReleaseBinding:
    """Opaque local seal for one exact public release projection."""

    canonical_binding: bytes
    binding_sha256: str
    campaign_id: str
    release_sha: str
    control_release_sha: str
    release_bundle_sha256: str
    image_set_sha256: str
    release_provenance_sha256: str
    seal_id: UUID
    sealed_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class WaIrBootstrapFreshAgeRecipient:
    """Public campaign identity projection; no private identity data is accepted."""

    campaign_id: str
    recipient: str
    recipient_public_sha256: str
    generation_id: UUID
    issued_at: datetime


@dataclass(frozen=True)
class VerifiedWaIrBootstrapFreshAgeRecipient:
    """Opaque validation result for one fresh public WA-IR recipient."""

    canonical_recipient: bytes
    recipient_sha256: str
    campaign_id: str
    recipient: str
    recipient_public_sha256: str
    generation_id: UUID
    issued_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class WaIrBootstrapImmutableObjectLocatorExpectation:
    """Public expectation for one new encrypted, exact Object version."""

    campaign_id: str
    release_sha: str
    sealed_release_binding_sha256: str
    source_site: str
    destination_site: str
    bootstrap_id: UUID
    locator_id: UUID
    locator_nonce: str
    issued_at: datetime
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int
    encryption: str
    immutability: str
    age_recipient: str


@dataclass(frozen=True)
class VerifiedWaIrBootstrapImmutableObjectLocatorExpectation:
    """Opaque local capability for one exact new immutable Object expectation."""

    canonical_locator: bytes
    locator_sha256: str
    campaign_id: str
    release_sha: str
    sealed_release_binding_sha256: str
    bootstrap_id: UUID
    locator_id: UUID
    locator_nonce: str
    issued_at: datetime
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int
    age_recipient: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWaIrBootstrapBundleBuilderConfig:
    """Root-only local policy; it cannot carry endpoints, buckets, or secrets."""

    schema: str = PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_SCHEMA
    state_root: Path | None = None
    enabled: bool = PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_DEFAULT_ENABLED
    maximum_freshness_age_seconds: int = DEFAULT_PHYSICAL_WA_IR_BOOTSTRAP_FRESHNESS_AGE_SECONDS
    maximum_ciphertext_bytes: int = MAX_PHYSICAL_WA_IR_BOOTSTRAP_CIPHERTEXT_BYTES
    maximum_plaintext_bytes: int = MAX_PHYSICAL_WA_IR_BOOTSTRAP_PLAINTEXT_BYTES
    denied_historic_recipient_public_sha256s: tuple[str, ...] = ()
    direct_fi_to_ir_control: str = "forbidden"
    operation_mode: str = "prepare-review-only"


@dataclass(frozen=True)
class PreparedPhysicalWaIrBootstrapDescriptor:
    """Opaque local descriptor; never a publish, transfer, or execution permit."""

    canonical_descriptor: bytes
    descriptor_sha256: str
    campaign_id: str
    release_sha: str
    recipient_public_sha256: str
    bootstrap_id: UUID
    locator_sha256: str
    prepared_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ConfigFacts:
    state_root: Path
    maximum_freshness_age_seconds: int
    maximum_ciphertext_bytes: int
    maximum_plaintext_bytes: int
    denied_historic_recipient_public_sha256s: frozenset[str]


@dataclass(frozen=True)
class _ReleaseFacts:
    canonical: bytes
    binding_sha256: str
    campaign_id: str
    release_sha: str
    control_release_sha: str
    release_bundle_sha256: str
    image_set_sha256: str
    release_provenance_sha256: str
    seal_id: UUID
    sealed_at: datetime


@dataclass(frozen=True)
class _RecipientFacts:
    canonical: bytes
    recipient_sha256: str
    campaign_id: str
    recipient: str
    recipient_public_sha256: str
    generation_id: UUID
    issued_at: datetime


@dataclass(frozen=True)
class _LocatorFacts:
    canonical: bytes
    locator_sha256: str
    campaign_id: str
    release_sha: str
    sealed_release_binding_sha256: str
    bootstrap_id: UUID
    locator_id: UUID
    locator_nonce: str
    issued_at: datetime
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int
    age_recipient: str


def _fail(code: str) -> None:
    raise PhysicalWaIrBootstrapBundleBuilderError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail(code)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _safe_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        _fail(code)
    if pattern.fullmatch(value) is None or _URL_OR_SECRET_RE.search(value) is not None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    digest = _safe_text(value, pattern=SHA256_RE, code=code)
    if digest == "0" * 64:
        _fail(code)
    return digest


def _uuid(value: object, *, code: str) -> UUID:
    if type(value) is not UUID or value.int == 0:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _fresh(
    value: object,
    *,
    now: datetime,
    maximum_age_seconds: int,
    code: str,
) -> datetime:
    observed = _utc(value, code=code)
    if observed > now + timedelta(seconds=MAX_PHYSICAL_WA_IR_BOOTSTRAP_FUTURE_SKEW_SECONDS):
        _fail(code)
    if observed < now - timedelta(seconds=maximum_age_seconds):
        _fail(code)
    return observed


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _private_root(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    try:
        metadata = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _config_facts(value: object, *, require_enabled: bool) -> _ConfigFacts:
    if type(value) is not PhysicalWaIrBootstrapBundleBuilderConfig:
        _fail("WA_IR_BOOTSTRAP_CONFIG_INVALID")
    if value.schema != PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_SCHEMA:
        _fail("WA_IR_BOOTSTRAP_CONFIG_INVALID")
    if type(value.enabled) is not bool:
        _fail("WA_IR_BOOTSTRAP_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("WA_IR_BOOTSTRAP_BUILDER_DISABLED")
    if os.geteuid() != 0:
        _fail("WA_IR_BOOTSTRAP_ROOT_RUNTIME_REQUIRED")
    if value.direct_fi_to_ir_control != "forbidden" or value.operation_mode != "prepare-review-only":
        _fail("WA_IR_BOOTSTRAP_DIRECTION_POLICY_INVALID")
    if (
        type(value.maximum_freshness_age_seconds) is not int
        or not 1 <= value.maximum_freshness_age_seconds <= MAX_PHYSICAL_WA_IR_BOOTSTRAP_FRESHNESS_AGE_SECONDS
    ):
        _fail("WA_IR_BOOTSTRAP_CONFIG_INVALID")
    ciphertext_maximum = _positive_int(
        value.maximum_ciphertext_bytes,
        maximum=MAX_PHYSICAL_WA_IR_BOOTSTRAP_CIPHERTEXT_BYTES,
        code="WA_IR_BOOTSTRAP_CONFIG_INVALID",
    )
    plaintext_maximum = _positive_int(
        value.maximum_plaintext_bytes,
        maximum=MAX_PHYSICAL_WA_IR_BOOTSTRAP_PLAINTEXT_BYTES,
        code="WA_IR_BOOTSTRAP_CONFIG_INVALID",
    )
    if isinstance(value.denied_historic_recipient_public_sha256s, (str, bytes)) or not isinstance(
        value.denied_historic_recipient_public_sha256s, tuple
    ):
        _fail("WA_IR_BOOTSTRAP_CONFIG_INVALID")
    denied = frozenset(
        _sha256(item, code="WA_IR_BOOTSTRAP_CONFIG_INVALID")
        for item in value.denied_historic_recipient_public_sha256s
    )
    if len(denied) != len(value.denied_historic_recipient_public_sha256s):
        _fail("WA_IR_BOOTSTRAP_CONFIG_INVALID")
    return _ConfigFacts(
        state_root=_private_root(value.state_root, code="WA_IR_BOOTSTRAP_STATE_ROOT_UNSAFE"),
        maximum_freshness_age_seconds=value.maximum_freshness_age_seconds,
        maximum_ciphertext_bytes=ciphertext_maximum,
        maximum_plaintext_bytes=plaintext_maximum,
        denied_historic_recipient_public_sha256s=denied,
    )


def validate_physical_wa_ir_bootstrap_bundle_builder_config(
    config: PhysicalWaIrBootstrapBundleBuilderConfig,
) -> PhysicalWaIrBootstrapBundleBuilderConfig:
    """Validate one inert root-only bootstrap-descriptor policy."""

    facts = _config_facts(config, require_enabled=False)
    return PhysicalWaIrBootstrapBundleBuilderConfig(
        schema=PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_SCHEMA,
        state_root=facts.state_root,
        enabled=config.enabled,
        maximum_freshness_age_seconds=facts.maximum_freshness_age_seconds,
        maximum_ciphertext_bytes=facts.maximum_ciphertext_bytes,
        maximum_plaintext_bytes=facts.maximum_plaintext_bytes,
        denied_historic_recipient_public_sha256s=tuple(
            sorted(facts.denied_historic_recipient_public_sha256s)
        ),
        direct_fi_to_ir_control="forbidden",
        operation_mode="prepare-review-only",
    )


def _release_facts(value: object, *, code: str) -> _ReleaseFacts:
    if type(value) is not WaIrBootstrapExactReleaseBinding:
        _fail(code)
    campaign_id = _safe_text(value.campaign_id, pattern=CAMPAIGN_ID_RE, code=code)
    release_sha = _safe_text(value.release_sha, pattern=RELEASE_SHA_RE, code=code)
    control_release_sha = _safe_text(value.control_release_sha, pattern=RELEASE_SHA_RE, code=code)
    if value.source_site != _SOURCE_SITE or value.destination_site != _DESTINATION_SITE:
        _fail(code)
    seal_id = _uuid(value.seal_id, code=code)
    sealed_at = _utc(value.sealed_at, code=code)
    unsigned = {
        "schema": PHYSICAL_WA_IR_BOOTSTRAP_EXACT_RELEASE_BINDING_SCHEMA,
        "status": "sealed",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "control_release_sha": control_release_sha,
        "release_bundle_sha256": _sha256(value.release_bundle_sha256, code=code),
        "image_set_sha256": _sha256(value.image_set_sha256, code=code),
        "release_provenance_sha256": _sha256(value.release_provenance_sha256, code=code),
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "seal_id": str(seal_id),
        "sealed_at": _timestamp(sealed_at),
    }
    canonical = _canonical(unsigned, code=code)
    return _ReleaseFacts(
        canonical=canonical,
        binding_sha256=hashlib.sha256(canonical).hexdigest(),
        campaign_id=campaign_id,
        release_sha=release_sha,
        control_release_sha=control_release_sha,
        release_bundle_sha256=unsigned["release_bundle_sha256"],
        image_set_sha256=unsigned["image_set_sha256"],
        release_provenance_sha256=unsigned["release_provenance_sha256"],
        seal_id=seal_id,
        sealed_at=sealed_at,
    )


def seal_wa_ir_bootstrap_exact_release_binding(
    binding: WaIrBootstrapExactReleaseBinding,
) -> SealedWaIrBootstrapExactReleaseBinding:
    """Explicitly seal public exact release metadata; no artifact is read."""

    facts = _release_facts(binding, code="WA_IR_BOOTSTRAP_RELEASE_BINDING_INVALID")
    result = SealedWaIrBootstrapExactReleaseBinding(
        canonical_binding=facts.canonical,
        binding_sha256=facts.binding_sha256,
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        control_release_sha=facts.control_release_sha,
        release_bundle_sha256=facts.release_bundle_sha256,
        image_set_sha256=facts.image_set_sha256,
        release_provenance_sha256=facts.release_provenance_sha256,
        seal_id=facts.seal_id,
        sealed_at=facts.sealed_at,
    )
    object.__setattr__(result, "_capability", _SEALED_RELEASE_CAPABILITY)
    return result


def require_sealed_wa_ir_bootstrap_exact_release_binding(
    value: object,
    *,
    now: datetime,
    maximum_freshness_age_seconds: int,
) -> SealedWaIrBootstrapExactReleaseBinding:
    """Require a live opaque exact release seal, not a raw release structure."""

    if (
        type(value) is not SealedWaIrBootstrapExactReleaseBinding
        or value._capability is not _SEALED_RELEASE_CAPABILITY
    ):
        _fail("WA_IR_BOOTSTRAP_RELEASE_SEAL_REQUIRED")
    raw = WaIrBootstrapExactReleaseBinding(
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        control_release_sha=value.control_release_sha,
        release_bundle_sha256=value.release_bundle_sha256,
        image_set_sha256=value.image_set_sha256,
        release_provenance_sha256=value.release_provenance_sha256,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        seal_id=value.seal_id,
        sealed_at=value.sealed_at,
    )
    facts = _release_facts(raw, code="WA_IR_BOOTSTRAP_RELEASE_SEAL_TAMPERED")
    if (
        value.canonical_binding != facts.canonical
        or value.binding_sha256 != facts.binding_sha256
        or value.campaign_id != facts.campaign_id
        or value.release_sha != facts.release_sha
        or value.control_release_sha != facts.control_release_sha
        or value.release_bundle_sha256 != facts.release_bundle_sha256
        or value.image_set_sha256 != facts.image_set_sha256
        or value.release_provenance_sha256 != facts.release_provenance_sha256
        or value.seal_id != facts.seal_id
        or value.sealed_at != facts.sealed_at
    ):
        _fail("WA_IR_BOOTSTRAP_RELEASE_SEAL_TAMPERED")
    _fresh(
        facts.sealed_at,
        now=_utc(now, code="WA_IR_BOOTSTRAP_CLOCK_INVALID"),
        maximum_age_seconds=maximum_freshness_age_seconds,
        code="WA_IR_BOOTSTRAP_RELEASE_SEAL_STALE",
    )
    return value


def _recipient_facts(value: object, *, code: str) -> _RecipientFacts:
    if type(value) is not WaIrBootstrapFreshAgeRecipient:
        _fail(code)
    campaign_id = _safe_text(value.campaign_id, pattern=CAMPAIGN_ID_RE, code=code)
    recipient = _safe_text(value.recipient, pattern=AGE_RECIPIENT_RE, code=code)
    public_sha256 = _sha256(value.recipient_public_sha256, code=code)
    if public_sha256 != hashlib.sha256(recipient.encode("ascii")).hexdigest():
        _fail(code)
    generation_id = _uuid(value.generation_id, code=code)
    issued_at = _utc(value.issued_at, code=code)
    mapping = {
        "schema": PHYSICAL_WA_IR_BOOTSTRAP_FRESH_RECIPIENT_SCHEMA,
        "campaign_id": campaign_id,
        "recipient": recipient,
        "recipient_public_sha256": public_sha256,
        "generation_id": str(generation_id),
        "issued_at": _timestamp(issued_at),
    }
    canonical = _canonical(mapping, code=code)
    return _RecipientFacts(
        canonical=canonical,
        recipient_sha256=hashlib.sha256(canonical).hexdigest(),
        campaign_id=campaign_id,
        recipient=recipient,
        recipient_public_sha256=public_sha256,
        generation_id=generation_id,
        issued_at=issued_at,
    )


def verify_wa_ir_bootstrap_fresh_age_recipient(
    recipient: WaIrBootstrapFreshAgeRecipient,
) -> VerifiedWaIrBootstrapFreshAgeRecipient:
    """Verify public recipient syntax and binding; private identity is absent."""

    facts = _recipient_facts(recipient, code="WA_IR_BOOTSTRAP_RECIPIENT_INVALID")
    result = VerifiedWaIrBootstrapFreshAgeRecipient(
        canonical_recipient=facts.canonical,
        recipient_sha256=facts.recipient_sha256,
        campaign_id=facts.campaign_id,
        recipient=facts.recipient,
        recipient_public_sha256=facts.recipient_public_sha256,
        generation_id=facts.generation_id,
        issued_at=facts.issued_at,
    )
    object.__setattr__(result, "_capability", _VERIFIED_RECIPIENT_CAPABILITY)
    return result


def require_verified_wa_ir_bootstrap_fresh_age_recipient(
    value: object,
    *,
    now: datetime,
    maximum_freshness_age_seconds: int,
) -> VerifiedWaIrBootstrapFreshAgeRecipient:
    """Require a fresh opaque recipient validation, never a private identity."""

    if (
        type(value) is not VerifiedWaIrBootstrapFreshAgeRecipient
        or value._capability is not _VERIFIED_RECIPIENT_CAPABILITY
    ):
        _fail("WA_IR_BOOTSTRAP_FRESH_RECIPIENT_REQUIRED")
    raw = WaIrBootstrapFreshAgeRecipient(
        campaign_id=value.campaign_id,
        recipient=value.recipient,
        recipient_public_sha256=value.recipient_public_sha256,
        generation_id=value.generation_id,
        issued_at=value.issued_at,
    )
    facts = _recipient_facts(raw, code="WA_IR_BOOTSTRAP_FRESH_RECIPIENT_TAMPERED")
    if (
        value.canonical_recipient != facts.canonical
        or value.recipient_sha256 != facts.recipient_sha256
        or value.campaign_id != facts.campaign_id
        or value.recipient != facts.recipient
        or value.recipient_public_sha256 != facts.recipient_public_sha256
        or value.generation_id != facts.generation_id
        or value.issued_at != facts.issued_at
    ):
        _fail("WA_IR_BOOTSTRAP_FRESH_RECIPIENT_TAMPERED")
    _fresh(
        facts.issued_at,
        now=_utc(now, code="WA_IR_BOOTSTRAP_CLOCK_INVALID"),
        maximum_age_seconds=maximum_freshness_age_seconds,
        code="WA_IR_BOOTSTRAP_FRESH_RECIPIENT_STALE",
    )
    return value


def expected_wa_ir_bootstrap_object_key(
    *,
    campaign_id: str,
    release_sha: str,
    sealed_release_binding_sha256: str,
    bootstrap_id: UUID,
) -> str:
    """Return the only non-wildcard immutable bootstrap object coordinate."""

    campaign = _safe_text(
        campaign_id,
        pattern=CAMPAIGN_ID_RE,
        code="WA_IR_BOOTSTRAP_OBJECT_KEY_INVALID",
    )
    release = _safe_text(
        release_sha,
        pattern=RELEASE_SHA_RE,
        code="WA_IR_BOOTSTRAP_OBJECT_KEY_INVALID",
    )
    binding = _sha256(
        sealed_release_binding_sha256,
        code="WA_IR_BOOTSTRAP_OBJECT_KEY_INVALID",
    )
    bootstrap = _uuid(bootstrap_id, code="WA_IR_BOOTSTRAP_OBJECT_KEY_INVALID")
    return "/".join(
        (
            "physical-wa-ir-bootstrap",
            "v1",
            campaign,
            release,
            binding,
            str(bootstrap),
            _FIXED_OBJECT_SUFFIX,
        )
    )


def _safe_object_key(value: object, *, code: str) -> str:
    if type(value) is not str or OBJECT_KEY_RE.fullmatch(value) is None:
        _fail(code)
    parts = value.split("/")
    if (
        not parts
        or any(
            part in {"", ".", ".."}
            or _COMPONENT_RE.fullmatch(part) is None
            or part.lower() in _MUTABLE_ALIASES
            or any(character in part for character in "*?[]{}")
            for part in parts
        )
    ):
        _fail(code)
    return value


def _safe_version_id(value: object, *, code: str) -> str:
    if type(value) is not str or VERSION_ID_RE.fullmatch(value) is None:
        _fail(code)
    if (
        value.lower() in _MUTABLE_ALIASES
        or any(component.lower() in _MUTABLE_ALIASES for component in value.split("/"))
        or any(character in value for character in "*?[]{}")
    ):
        _fail(code)
    return value


def _locator_facts(value: object, *, code: str) -> _LocatorFacts:
    if type(value) is not WaIrBootstrapImmutableObjectLocatorExpectation:
        _fail(code)
    campaign_id = _safe_text(value.campaign_id, pattern=CAMPAIGN_ID_RE, code=code)
    release_sha = _safe_text(value.release_sha, pattern=RELEASE_SHA_RE, code=code)
    binding_sha256 = _sha256(value.sealed_release_binding_sha256, code=code)
    if value.source_site != _SOURCE_SITE or value.destination_site != _DESTINATION_SITE:
        _fail(code)
    bootstrap_id = _uuid(value.bootstrap_id, code=code)
    locator_id = _uuid(value.locator_id, code=code)
    if locator_id == bootstrap_id:
        _fail(code)
    nonce = _safe_text(value.locator_nonce, pattern=_NONCE_RE, code=code)
    issued_at = _utc(value.issued_at, code=code)
    expected_key = expected_wa_ir_bootstrap_object_key(
        campaign_id=campaign_id,
        release_sha=release_sha,
        sealed_release_binding_sha256=binding_sha256,
        bootstrap_id=bootstrap_id,
    )
    object_key = _safe_object_key(value.object_key, code=code)
    if object_key != expected_key:
        _fail(code)
    version_id = _safe_version_id(value.version_id, code=code)
    ciphertext_sha256 = _sha256(value.ciphertext_sha256, code=code)
    plaintext_sha256 = _sha256(value.plaintext_sha256, code=code)
    ciphertext_bytes = _positive_int(
        value.ciphertext_bytes,
        maximum=MAX_PHYSICAL_WA_IR_BOOTSTRAP_CIPHERTEXT_BYTES,
        code=code,
    )
    plaintext_bytes = _positive_int(
        value.plaintext_bytes,
        maximum=MAX_PHYSICAL_WA_IR_BOOTSTRAP_PLAINTEXT_BYTES,
        code=code,
    )
    if value.encryption != _EXPECTED_ENCRYPTION or value.immutability != _EXPECTED_IMMUTABILITY:
        _fail(code)
    recipient = _safe_text(value.age_recipient, pattern=AGE_RECIPIENT_RE, code=code)
    mapping = {
        "schema": PHYSICAL_WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_SCHEMA,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "sealed_release_binding_sha256": binding_sha256,
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "bootstrap_id": str(bootstrap_id),
        "locator_id": str(locator_id),
        "locator_nonce": nonce,
        "issued_at": _timestamp(issued_at),
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": ciphertext_bytes,
        "plaintext_sha256": plaintext_sha256,
        "plaintext_bytes": plaintext_bytes,
        "encryption": _EXPECTED_ENCRYPTION,
        "immutability": _EXPECTED_IMMUTABILITY,
        "age_recipient": recipient,
    }
    canonical = _canonical(mapping, code=code)
    return _LocatorFacts(
        canonical=canonical,
        locator_sha256=hashlib.sha256(canonical).hexdigest(),
        campaign_id=campaign_id,
        release_sha=release_sha,
        sealed_release_binding_sha256=binding_sha256,
        bootstrap_id=bootstrap_id,
        locator_id=locator_id,
        locator_nonce=nonce,
        issued_at=issued_at,
        object_key=object_key,
        version_id=version_id,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
        age_recipient=recipient,
    )


def verify_wa_ir_bootstrap_immutable_locator_expectation(
    locator: WaIrBootstrapImmutableObjectLocatorExpectation,
) -> VerifiedWaIrBootstrapImmutableObjectLocatorExpectation:
    """Verify one local, exact, immutable Object expectation without S3."""

    facts = _locator_facts(locator, code="WA_IR_BOOTSTRAP_LOCATOR_INVALID")
    result = VerifiedWaIrBootstrapImmutableObjectLocatorExpectation(
        canonical_locator=facts.canonical,
        locator_sha256=facts.locator_sha256,
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        sealed_release_binding_sha256=facts.sealed_release_binding_sha256,
        bootstrap_id=facts.bootstrap_id,
        locator_id=facts.locator_id,
        locator_nonce=facts.locator_nonce,
        issued_at=facts.issued_at,
        object_key=facts.object_key,
        version_id=facts.version_id,
        ciphertext_sha256=facts.ciphertext_sha256,
        ciphertext_bytes=facts.ciphertext_bytes,
        plaintext_sha256=facts.plaintext_sha256,
        plaintext_bytes=facts.plaintext_bytes,
        age_recipient=facts.age_recipient,
    )
    object.__setattr__(result, "_capability", _VERIFIED_LOCATOR_CAPABILITY)
    return result


def require_verified_wa_ir_bootstrap_immutable_locator_expectation(
    value: object,
    *,
    now: datetime,
    maximum_freshness_age_seconds: int,
) -> VerifiedWaIrBootstrapImmutableObjectLocatorExpectation:
    """Require a fresh opaque exact locator expectation, not a raw selector."""

    if (
        type(value) is not VerifiedWaIrBootstrapImmutableObjectLocatorExpectation
        or value._capability is not _VERIFIED_LOCATOR_CAPABILITY
    ):
        _fail("WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_REQUIRED")
    raw = WaIrBootstrapImmutableObjectLocatorExpectation(
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        sealed_release_binding_sha256=value.sealed_release_binding_sha256,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        bootstrap_id=value.bootstrap_id,
        locator_id=value.locator_id,
        locator_nonce=value.locator_nonce,
        issued_at=value.issued_at,
        object_key=value.object_key,
        version_id=value.version_id,
        ciphertext_sha256=value.ciphertext_sha256,
        ciphertext_bytes=value.ciphertext_bytes,
        plaintext_sha256=value.plaintext_sha256,
        plaintext_bytes=value.plaintext_bytes,
        encryption=_EXPECTED_ENCRYPTION,
        immutability=_EXPECTED_IMMUTABILITY,
        age_recipient=value.age_recipient,
    )
    facts = _locator_facts(raw, code="WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_TAMPERED")
    if (
        value.canonical_locator != facts.canonical
        or value.locator_sha256 != facts.locator_sha256
        or value.campaign_id != facts.campaign_id
        or value.release_sha != facts.release_sha
        or value.sealed_release_binding_sha256 != facts.sealed_release_binding_sha256
        or value.bootstrap_id != facts.bootstrap_id
        or value.locator_id != facts.locator_id
        or value.locator_nonce != facts.locator_nonce
        or value.issued_at != facts.issued_at
        or value.object_key != facts.object_key
        or value.version_id != facts.version_id
        or value.ciphertext_sha256 != facts.ciphertext_sha256
        or value.ciphertext_bytes != facts.ciphertext_bytes
        or value.plaintext_sha256 != facts.plaintext_sha256
        or value.plaintext_bytes != facts.plaintext_bytes
        or value.age_recipient != facts.age_recipient
    ):
        _fail("WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_TAMPERED")
    _fresh(
        facts.issued_at,
        now=_utc(now, code="WA_IR_BOOTSTRAP_CLOCK_INVALID"),
        maximum_age_seconds=maximum_freshness_age_seconds,
        code="WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_STALE",
    )
    return value


def _input_facts(
    *,
    config: PhysicalWaIrBootstrapBundleBuilderConfig,
    sealed_release: object,
    fresh_recipient: object,
    locator_expectation: object,
    now: datetime,
    require_enabled: bool,
) -> tuple[_ConfigFacts, _ReleaseFacts, _RecipientFacts, _LocatorFacts, datetime]:
    config_facts = _config_facts(config, require_enabled=require_enabled)
    observed_now = _utc(now, code="WA_IR_BOOTSTRAP_CLOCK_INVALID")
    sealed = require_sealed_wa_ir_bootstrap_exact_release_binding(
        sealed_release,
        now=observed_now,
        maximum_freshness_age_seconds=config_facts.maximum_freshness_age_seconds,
    )
    recipient = require_verified_wa_ir_bootstrap_fresh_age_recipient(
        fresh_recipient,
        now=observed_now,
        maximum_freshness_age_seconds=config_facts.maximum_freshness_age_seconds,
    )
    locator = require_verified_wa_ir_bootstrap_immutable_locator_expectation(
        locator_expectation,
        now=observed_now,
        maximum_freshness_age_seconds=config_facts.maximum_freshness_age_seconds,
    )
    release_facts = _release_facts(
        WaIrBootstrapExactReleaseBinding(
            campaign_id=sealed.campaign_id,
            release_sha=sealed.release_sha,
            control_release_sha=sealed.control_release_sha,
            release_bundle_sha256=sealed.release_bundle_sha256,
            image_set_sha256=sealed.image_set_sha256,
            release_provenance_sha256=sealed.release_provenance_sha256,
            source_site=_SOURCE_SITE,
            destination_site=_DESTINATION_SITE,
            seal_id=sealed.seal_id,
            sealed_at=sealed.sealed_at,
        ),
        code="WA_IR_BOOTSTRAP_RELEASE_SEAL_TAMPERED",
    )
    recipient_facts = _recipient_facts(
        WaIrBootstrapFreshAgeRecipient(
            campaign_id=recipient.campaign_id,
            recipient=recipient.recipient,
            recipient_public_sha256=recipient.recipient_public_sha256,
            generation_id=recipient.generation_id,
            issued_at=recipient.issued_at,
        ),
        code="WA_IR_BOOTSTRAP_FRESH_RECIPIENT_TAMPERED",
    )
    locator_facts = _locator_facts(
        WaIrBootstrapImmutableObjectLocatorExpectation(
            campaign_id=locator.campaign_id,
            release_sha=locator.release_sha,
            sealed_release_binding_sha256=locator.sealed_release_binding_sha256,
            source_site=_SOURCE_SITE,
            destination_site=_DESTINATION_SITE,
            bootstrap_id=locator.bootstrap_id,
            locator_id=locator.locator_id,
            locator_nonce=locator.locator_nonce,
            issued_at=locator.issued_at,
            object_key=locator.object_key,
            version_id=locator.version_id,
            ciphertext_sha256=locator.ciphertext_sha256,
            ciphertext_bytes=locator.ciphertext_bytes,
            plaintext_sha256=locator.plaintext_sha256,
            plaintext_bytes=locator.plaintext_bytes,
            encryption=_EXPECTED_ENCRYPTION,
            immutability=_EXPECTED_IMMUTABILITY,
            age_recipient=locator.age_recipient,
        ),
        code="WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_TAMPERED",
    )
    if (
        release_facts.campaign_id != recipient_facts.campaign_id
        or release_facts.campaign_id != locator_facts.campaign_id
        or release_facts.release_sha != locator_facts.release_sha
        or release_facts.binding_sha256 != locator_facts.sealed_release_binding_sha256
        or recipient_facts.recipient != locator_facts.age_recipient
    ):
        _fail("WA_IR_BOOTSTRAP_INPUT_BINDING_MISMATCH")
    if recipient_facts.recipient_public_sha256 in config_facts.denied_historic_recipient_public_sha256s:
        _fail("WA_IR_BOOTSTRAP_HISTORIC_RECIPIENT_FORBIDDEN")
    if len(
        {
            release_facts.seal_id,
            recipient_facts.generation_id,
            locator_facts.bootstrap_id,
            locator_facts.locator_id,
        }
    ) != 4:
        _fail("WA_IR_BOOTSTRAP_FRESHNESS_IDENTITIES_COLLIDE")
    if (
        locator_facts.ciphertext_bytes > config_facts.maximum_ciphertext_bytes
        or locator_facts.plaintext_bytes > config_facts.maximum_plaintext_bytes
    ):
        _fail("WA_IR_BOOTSTRAP_OBJECT_BOUNDS_EXCEEDED")
    return config_facts, release_facts, recipient_facts, locator_facts, observed_now


def _descriptor_unsigned(
    *,
    release: _ReleaseFacts,
    recipient: _RecipientFacts,
    locator: _LocatorFacts,
    prepared_at: datetime,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WA_IR_BOOTSTRAP_DESCRIPTOR_SCHEMA,
        "status": _STATUS_PREPARED,
        "campaign_id": release.campaign_id,
        "release_sha": release.release_sha,
        "control_release_sha": release.control_release_sha,
        "sealed_release_binding_sha256": release.binding_sha256,
        "release_bundle_sha256": release.release_bundle_sha256,
        "image_set_sha256": release.image_set_sha256,
        "release_provenance_sha256": release.release_provenance_sha256,
        "age_recipient": recipient.recipient,
        "recipient_public_sha256": recipient.recipient_public_sha256,
        "recipient_generation_id": str(recipient.generation_id),
        "bootstrap_id": str(locator.bootstrap_id),
        "locator_id": str(locator.locator_id),
        "locator_sha256": locator.locator_sha256,
        "object": {
            "object_key": locator.object_key,
            "version_id": locator.version_id,
            "ciphertext_sha256": locator.ciphertext_sha256,
            "ciphertext_bytes": locator.ciphertext_bytes,
            "plaintext_sha256": locator.plaintext_sha256,
            "plaintext_bytes": locator.plaintext_bytes,
            "encryption": _EXPECTED_ENCRYPTION,
            "immutability": _EXPECTED_IMMUTABILITY,
        },
        "prepared_at": _timestamp(prepared_at),
        "direct_fi_to_ir_control": "forbidden",
        "publish_authorized": False,
        "execution_authorized": False,
    }


def _descriptor_mapping(
    *,
    release: _ReleaseFacts,
    recipient: _RecipientFacts,
    locator: _LocatorFacts,
    prepared_at: datetime,
) -> tuple[dict[str, Any], bytes, str]:
    unsigned = _descriptor_unsigned(
        release=release,
        recipient=recipient,
        locator=locator,
        prepared_at=prepared_at,
    )
    digest = hashlib.sha256(_canonical(unsigned, code="WA_IR_BOOTSTRAP_DESCRIPTOR_INVALID")).hexdigest()
    mapping = {**unsigned, "descriptor_sha256": digest}
    return mapping, _canonical(mapping, code="WA_IR_BOOTSTRAP_DESCRIPTOR_INVALID") + b"\n", digest


def _secure_freshness_directory(root: Path) -> Path:
    path = root / _FRESHNESS_DIRECTORY
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
    return resolved


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError:
            _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
        if type(written) is not int or written <= 0:
            _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
        offset += written


def _sync_directory(directory: Path) -> None:
    """Persist one newly-created marker directory entry before proceeding."""

    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
    descriptor = -1
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
        os.fsync(descriptor)
    except PhysicalWaIrBootstrapBundleBuilderError:
        raise
    except OSError:
        _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")


def _claim_marker(directory: Path, *, name: str, digest: str) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
    descriptor = -1
    try:
        descriptor = os.open(
            directory / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
        _write_all(
            descriptor,
            (
                "gold-trade-physical-wa-ir-bootstrap-freshness-marker-v1:" + digest + "\n"
            ).encode("ascii"),
        )
        os.fsync(descriptor)
        _sync_directory(directory)
    except FileExistsError:
        _fail("WA_IR_BOOTSTRAP_HISTORIC_REUSE_REJECTED")
    except PhysicalWaIrBootstrapBundleBuilderError:
        raise
    except OSError:
        _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                _fail("WA_IR_BOOTSTRAP_FRESHNESS_GUARD_FAILED")


def _claim_freshness(
    *,
    config: _ConfigFacts,
    release: _ReleaseFacts,
    recipient: _RecipientFacts,
    locator: _LocatorFacts,
) -> None:
    directory = _secure_freshness_directory(config.state_root)
    campaign_release = hashlib.sha256(
        (release.campaign_id + "\x00" + release.release_sha).encode("ascii")
    ).hexdigest()
    values = (
        ("campaign-release-" + campaign_release, campaign_release),
        ("recipient-" + recipient.recipient_public_sha256, recipient.recipient_public_sha256),
        (
            "recipient-generation-" + hashlib.sha256(str(recipient.generation_id).encode("ascii")).hexdigest(),
            hashlib.sha256(str(recipient.generation_id).encode("ascii")).hexdigest(),
        ),
        ("locator-" + locator.locator_sha256, locator.locator_sha256),
        (
            "bootstrap-" + hashlib.sha256(str(locator.bootstrap_id).encode("ascii")).hexdigest(),
            hashlib.sha256(str(locator.bootstrap_id).encode("ascii")).hexdigest(),
        ),
    )
    for name, digest in values:
        _claim_marker(directory, name=name, digest=digest)


def _descriptor_facts(
    value: object,
    *,
    release: _ReleaseFacts,
    recipient: _RecipientFacts,
    locator: _LocatorFacts,
    now: datetime,
    maximum_freshness_age_seconds: int,
) -> tuple[dict[str, Any], bytes, str, datetime]:
    if (
        type(value) is not PreparedPhysicalWaIrBootstrapDescriptor
        or value._capability is not _PREPARED_DESCRIPTOR_CAPABILITY
    ):
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_REQUIRED")
    raw = value.canonical_descriptor
    if type(raw) is not bytes or not raw or len(raw) > 64 * 1024:
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    try:
        parsed = json.loads(raw.decode("ascii", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    item = _exact_mapping(parsed, fields=_DESCRIPTOR_FIELDS, code="WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    if raw != _canonical(item, code="WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED") + b"\n":
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    prepared_at_text = item.get("prepared_at")
    if type(prepared_at_text) is not str or not prepared_at_text.endswith("Z"):
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    try:
        prepared_at = datetime.fromisoformat(prepared_at_text[:-1] + "+00:00")
    except ValueError:
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    prepared_at = _fresh(
        prepared_at,
        now=now,
        maximum_age_seconds=maximum_freshness_age_seconds,
        code="WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_STALE",
    )
    expected, canonical, digest = _descriptor_mapping(
        release=release,
        recipient=recipient,
        locator=locator,
        prepared_at=prepared_at,
    )
    if item != expected or raw != canonical or value.descriptor_sha256 != digest:
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    if (
        value.campaign_id != release.campaign_id
        or value.release_sha != release.release_sha
        or value.recipient_public_sha256 != recipient.recipient_public_sha256
        or value.bootstrap_id != locator.bootstrap_id
        or value.locator_sha256 != locator.locator_sha256
        or value.prepared_at != prepared_at
    ):
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    return expected, canonical, digest, prepared_at


def prepare_fresh_wa_ir_bootstrap_descriptor(
    *,
    config: PhysicalWaIrBootstrapBundleBuilderConfig,
    sealed_release: SealedWaIrBootstrapExactReleaseBinding,
    fresh_recipient: VerifiedWaIrBootstrapFreshAgeRecipient,
    locator_expectation: VerifiedWaIrBootstrapImmutableObjectLocatorExpectation,
    now: datetime,
) -> PreparedPhysicalWaIrBootstrapDescriptor:
    """Prepare one fresh descriptor and consume its local non-reuse markers.

    This function does not touch a release archive or Object Storage.  It does
    not encrypt, publish, download, transfer, execute, or activate anything.
    """

    config_facts, release, recipient, locator, observed_now = _input_facts(
        config=config,
        sealed_release=sealed_release,
        fresh_recipient=fresh_recipient,
        locator_expectation=locator_expectation,
        now=now,
        require_enabled=True,
    )
    _claim_freshness(
        config=config_facts,
        release=release,
        recipient=recipient,
        locator=locator,
    )
    _mapping, canonical, digest = _descriptor_mapping(
        release=release,
        recipient=recipient,
        locator=locator,
        prepared_at=observed_now,
    )
    result = PreparedPhysicalWaIrBootstrapDescriptor(
        canonical_descriptor=canonical,
        descriptor_sha256=digest,
        campaign_id=release.campaign_id,
        release_sha=release.release_sha,
        recipient_public_sha256=recipient.recipient_public_sha256,
        bootstrap_id=locator.bootstrap_id,
        locator_sha256=locator.locator_sha256,
        prepared_at=observed_now,
    )
    object.__setattr__(result, "_capability", _PREPARED_DESCRIPTOR_CAPABILITY)
    return result


def require_prepared_physical_wa_ir_bootstrap_descriptor(
    value: object,
    *,
    config: PhysicalWaIrBootstrapBundleBuilderConfig,
    sealed_release: SealedWaIrBootstrapExactReleaseBinding,
    fresh_recipient: VerifiedWaIrBootstrapFreshAgeRecipient,
    locator_expectation: VerifiedWaIrBootstrapImmutableObjectLocatorExpectation,
    now: datetime,
) -> PreparedPhysicalWaIrBootstrapDescriptor:
    """Revalidate a prepared descriptor without publishing or claiming again."""

    config_facts, release, recipient, locator, observed_now = _input_facts(
        config=config,
        sealed_release=sealed_release,
        fresh_recipient=fresh_recipient,
        locator_expectation=locator_expectation,
        now=now,
        require_enabled=True,
    )
    del config_facts
    _descriptor_facts(
        value,
        release=release,
        recipient=recipient,
        locator=locator,
        now=observed_now,
        maximum_freshness_age_seconds=_config_facts(config, require_enabled=True).maximum_freshness_age_seconds,
    )
    return value


def review_fresh_wa_ir_bootstrap_descriptor(
    descriptor: PreparedPhysicalWaIrBootstrapDescriptor,
    *,
    config: PhysicalWaIrBootstrapBundleBuilderConfig,
    sealed_release: SealedWaIrBootstrapExactReleaseBinding,
    fresh_recipient: VerifiedWaIrBootstrapFreshAgeRecipient,
    locator_expectation: VerifiedWaIrBootstrapImmutableObjectLocatorExpectation,
    now: datetime,
) -> dict[str, Any]:
    """Return a canonical redacted review mapping; it has no publish authority."""

    verified = require_prepared_physical_wa_ir_bootstrap_descriptor(
        descriptor,
        config=config,
        sealed_release=sealed_release,
        fresh_recipient=fresh_recipient,
        locator_expectation=locator_expectation,
        now=now,
    )
    try:
        parsed = json.loads(verified.canonical_descriptor.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):  # pragma: no cover - checked above.
        _fail("WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED")
    # The descriptor has no endpoint, bucket, URL, private identity, secret,
    # credential, transfer command, or execution capability by schema.
    return dict(parsed)
