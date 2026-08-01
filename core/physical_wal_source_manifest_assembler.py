"""Pure, default-off source assembly for signed physical base/WAL manifests.

The initial base is explicitly bootstrapped from a pinned completed base-backup
record *before* any WAL receipt exists.  That API emits canonical signed base
bytes and their hash.  A separate initial-WAL API then accepts exactly those
raw base bytes under an explicit hash pin plus already-uploaded WAL receipts.
This removes a bootstrap circularity without accepting caller-supplied current
frontier values.  It has no filesystem, database, transport, encryption, or
publication adapter.

A blob-inventory frontier is intentionally not constructed here.  It remains a
separate signed artifact because physical database/WAL continuity cannot prove
the required blob frontier by itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_object_manifest import (
    MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES,
    MAX_PHYSICAL_WAL_OBJECT_CIPHERTEXT_BYTES,
    MAX_PHYSICAL_WAL_SEGMENTS_PER_MANIFEST,
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
    PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES,
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    PhysicalWalObjectManifestError,
    build_physical_wal_base_backup_manifest,
    build_physical_wal_segment_manifest,
    verify_physical_wal_base_backup_manifest,
    verify_physical_wal_segment_manifest,
)


__all__ = (
    "MAX_PHYSICAL_WAL_SOURCE_RECORD_BYTES",
    "MAX_PHYSICAL_WAL_SOURCE_UPLOAD_MANIFESTS",
    "PHYSICAL_WAL_SOURCE_MANIFEST_ASSEMBLER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_SOURCE_BASE_MANIFEST_BOOTSTRAP_SCHEMA",
    "PHYSICAL_WAL_SOURCE_MANIFEST_ASSEMBLY_SCHEMA",
    "PHYSICAL_WAL_SOURCE_MANIFEST_APPEND_ASSEMBLY_SCHEMA",
    "PHYSICAL_WAL_SOURCE_MANIFEST_BLOB_FRONTIER_REQUIRED",
    "PhysicalWalSourceBaseManifestBootstrap",
    "PhysicalWalSourceBaseManifestBootstrapBinding",
    "PhysicalWalSourceManifestAssemblerBinding",
    "PhysicalWalSourceManifestAppendBinding",
    "PhysicalWalSourceManifestAppendAssembly",
    "PhysicalWalSourceManifestAssembly",
    "PhysicalWalSourceManifestBaseline",
    "PhysicalWalSourceManifestExpectedTerm",
    "PhysicalWalSourceManifestAssemblerError",
    "bootstrap_physical_wal_base_backup_manifest",
    "assemble_physical_wal_source_manifest_chain",
    "append_physical_wal_source_manifest_chain",
)


PHYSICAL_WAL_SOURCE_BASE_MANIFEST_BOOTSTRAP_SCHEMA = (
    "gold-trade-physical-wal-source-base-manifest-bootstrap-v1"
)
PHYSICAL_WAL_SOURCE_MANIFEST_ASSEMBLY_SCHEMA = (
    "gold-trade-physical-wal-source-manifest-assembly-v1"
)
PHYSICAL_WAL_SOURCE_MANIFEST_APPEND_ASSEMBLY_SCHEMA = (
    "gold-trade-physical-wal-source-manifest-append-assembly-v1"
)
PHYSICAL_WAL_SOURCE_MANIFEST_ASSEMBLER_DEFAULT_ENABLED = False
PHYSICAL_WAL_SOURCE_MANIFEST_BLOB_FRONTIER_REQUIRED = (
    "separate-signed-blob-frontier-required"
)
# Input schema strings are intentionally local: importing either spool would
# pull an I/O-capable implementation into this otherwise pure module.
_BASE_BACKUP_COMPLETION_RECORD_SCHEMA = "gold-trade-physical-wal-base-backup-spool-completed-v1"
_WAL_UPLOAD_MANIFEST_SCHEMA = "gold-trade-physical-wal-archive-spool-manifest-v1"
MAX_PHYSICAL_WAL_SOURCE_RECORD_BYTES = 512 * 1024
MAX_PHYSICAL_WAL_SOURCE_UPLOAD_MANIFESTS = 4096
REQUIRED_WAL_SEGMENT_SIZE_BYTES = 16 * 1024 * 1024

_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$")
_WAL_SEGMENT_NAME_RE = re.compile(r"^[0-9A-F]{24}$")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$")
_TRANSITION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_BASE_RECORD_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "handoff_descriptor_sha256",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "baseline_generation_id",
        "route_binding_sha256",
        "object_storage_namespace",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "destination_age_recipient",
        "writer_term",
        "completed_source_artifact",
        "snapshot_sha256",
        "snapshot_bytes",
        "object",
        "not_a_remote_apply_proof",
        "not_a_strict_acknowledgement_proof",
    }
)
_BASE_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "epoch",
        "lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
    }
)
_SOURCE_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_name",
        "plaintext_sha256",
        "plaintext_bytes",
        "completion_attestation_sha256",
    }
)
_WAL_UPLOAD_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "handoff_descriptor_sha256",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "archive_manifest_sha256",
        "route_binding_sha256",
        "object_storage_namespace",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "destination_age_recipient",
        "writer_term",
        "wal_segment_name",
        "segment_ordinal",
        "start_lsn",
        "end_lsn",
        "snapshot_sha256",
        "snapshot_bytes",
        "object",
    }
)
_WAL_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
    }
)
_OBJECT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "object_kind",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "encryption",
        "age_recipient",
        "immutability",
    }
)


class PhysicalWalSourceManifestAssemblerError(ValueError):
    """Supplied local source records cannot safely form one signed chain."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalSourceManifestExpectedTerm:
    """Non-secret expected source Writer-Witness projection."""

    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str


@dataclass(frozen=True)
class PhysicalWalSourceManifestBaseline:
    """Exact physical baseline geometry expected from the trusted source plan."""

    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str


@dataclass(frozen=True)
class PhysicalWalSourceBaseManifestBootstrapBinding:
    """Pins the one completed base record allowed to mint a signed base claim.

    This is intentionally separate from WAL-upload inputs.  The resulting
    canonical base-manifest bytes and SHA-256 become a durable upstream
    artifact before any WAL uploader is allowed to bind its receipts to the
    baseline.  ``source_signer`` remains in-memory only.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    expected_term: PhysicalWalSourceManifestExpectedTerm
    baseline: PhysicalWalSourceManifestBaseline
    destination_age_recipient: str
    base_route_binding_sha256: str
    base_completion_record_sha256: str
    source_public_key: bytes
    source_signer: object
    object_storage_namespace: str = PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE


@dataclass(frozen=True)
class PhysicalWalSourceManifestAssemblerBinding:
    """Explicit, non-secret pins for an initial or appended WAL assembly.

    The exact signed base manifest is supplied as canonical raw bytes to the
    WAL entry point and its SHA-256 must equal ``base_backup_manifest_sha256``.
    It is reverified against this route, term, baseline, recipient, and source
    public key before any WAL receipt is parsed.  ``source_signer`` is only
    used in memory to sign newly emitted WAL links.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    expected_term: PhysicalWalSourceManifestExpectedTerm
    baseline: PhysicalWalSourceManifestBaseline
    destination_age_recipient: str
    base_backup_manifest_sha256: str
    wal_stream_generation_id: str
    wal_archive_manifest_sha256: str
    wal_route_binding_sha256: str
    wal_upload_manifest_sha256es: tuple[str, ...]
    source_public_key: bytes
    source_signer: object
    object_storage_namespace: str = PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE


@dataclass(frozen=True)
class PhysicalWalSourceManifestAppendBinding:
    """Immutable pins for one append batch after a signed WAL frontier.

    ``source_manifest_binding`` carries the route, source term, baseline,
    signer and exact current upload-record hashes.  The two explicit manifest
    hashes prevent this pure boundary from accepting mutable caller-supplied
    base or predecessor frontier values.
    """

    source_manifest_binding: PhysicalWalSourceManifestAssemblerBinding
    base_backup_manifest_sha256: str
    previous_wal_segment_manifest_sha256: str


@dataclass(frozen=True)
class PhysicalWalSourceBaseManifestBootstrap:
    """Canonical signed base claim emitted before initial WAL upload receipts."""

    schema: str
    base_backup_manifest: bytes
    base_backup_manifest_sha256: str


@dataclass(frozen=True)
class PhysicalWalSourceManifestAssembly:
    """Canonical signed base/WAL chain, never an upload or replay claim."""

    schema: str
    base_backup_manifest: bytes
    base_backup_manifest_sha256: str
    wal_segment_manifests: tuple[bytes, ...]
    wal_segment_manifest_sha256es: tuple[str, ...]
    terminal_wal_lsn: str
    blob_frontier_requirement: str


@dataclass(frozen=True)
class PhysicalWalSourceManifestAppendAssembly:
    """New signed WAL links after one reverified immutable predecessor."""

    schema: str
    base_backup_manifest_sha256: str
    previous_wal_segment_manifest_sha256: str
    wal_segment_manifests: tuple[bytes, ...]
    wal_segment_manifest_sha256es: tuple[str, ...]
    terminal_wal_lsn: str
    blob_frontier_requirement: str


@dataclass(frozen=True)
class _TermFacts:
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str


@dataclass(frozen=True)
class _BaselineFacts:
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    baseline_wal_lsn_value: int
    wal_chain_start_lsn: str
    wal_chain_start_lsn_value: int
    base_backup_end_lsn: str
    base_backup_end_lsn_value: int


@dataclass(frozen=True)
class _BindingFacts:
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    term: _TermFacts
    baseline: _BaselineFacts
    destination_age_recipient: str
    object_storage_namespace: str
    base_backup_manifest_sha256: str
    wal_stream_generation_id: str
    wal_archive_manifest_sha256: str
    wal_route_binding_sha256: str
    wal_upload_manifest_sha256es: tuple[str, ...]
    source_public_key: bytes
    source_signer: object


@dataclass(frozen=True)
class _BaseBootstrapFacts:
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    term: _TermFacts
    baseline: _BaselineFacts
    destination_age_recipient: str
    object_storage_namespace: str
    base_route_binding_sha256: str
    base_completion_record_sha256: str
    source_public_key: bytes
    source_signer: object


@dataclass(frozen=True)
class _BaseCompletionFacts:
    object_descriptor: dict[str, Any]


@dataclass(frozen=True)
class _WalUploadFacts:
    ordinal: int
    segment_name: str
    start_lsn: str
    start_lsn_value: int
    end_lsn: str
    end_lsn_value: int
    object_descriptor: dict[str, Any]


def _fail(code: str) -> None:
    raise PhysicalWalSourceManifestAssemblerError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("SOURCE_RECORD_DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("SOURCE_RECORD_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (OverflowError, RecursionError, TypeError, ValueError) as exc:
        raise PhysicalWalSourceManifestAssemblerError(code) from exc


def _bounded_record_bytes(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or not 1 <= len(value) <= MAX_PHYSICAL_WAL_SOURCE_RECORD_BYTES:
        _fail(code)
    return value


def _bounded_signed_manifest_bytes(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or not 1 <= len(value) <= MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES:
        _fail(code)
    return value


def _parse_canonical_bytes(value: object, *, code: str) -> dict[str, Any]:
    raw = _bounded_record_bytes(value, code=code)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalSourceManifestAssemblerError:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PhysicalWalSourceManifestAssemblerError(code) from exc
    if not isinstance(parsed, dict) or _canonical(parsed, code=code) != raw:
        _fail(code)
    return parsed


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _sha256(value: object, *, code: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        _fail(code)
    return value


def _site(value: object, *, code: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        _fail(code)
    return value


def _object_storage_namespace(
    value: object,
    *,
    source_site: str,
    destination_site: str,
    code: str,
) -> str:
    if type(value) is not str or value not in PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES:
        _fail(code)
    expected = (
        PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE
        if (source_site, destination_site) == ("webapp_fi", "webapp_ir")
        else PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    )
    if value != expected:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _positive_int(value: object, *, code: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        _fail(code)
    return value


def _nonnegative_int(value: object, *, code: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        _fail(code)
    return value


def _term_from_binding(value: object, *, source_site: str) -> _TermFacts:
    if type(value) is not PhysicalWalSourceManifestExpectedTerm:
        _fail("ASSEMBLER_EXPECTED_TERM_INVALID")
    holder = _site(value.holder_site, code="ASSEMBLER_EXPECTED_TERM_INVALID")
    if holder != source_site:
        _fail("ASSEMBLER_EXPECTED_TERM_INVALID")
    lease = value.writer_lease_id
    transition = value.witness_transition_id
    if not isinstance(lease, str) or LEASE_ID_RE.fullmatch(lease) is None:
        _fail("ASSEMBLER_EXPECTED_TERM_INVALID")
    if not isinstance(transition, str) or _TRANSITION_ID_RE.fullmatch(transition) is None:
        _fail("ASSEMBLER_EXPECTED_TERM_INVALID")
    return _TermFacts(
        holder_site=holder,
        writer_epoch=_positive_int(value.writer_epoch, code="ASSEMBLER_EXPECTED_TERM_INVALID"),
        writer_lease_id=lease,
        witnessed_term_proof_sha256=_sha256(
            value.witnessed_term_proof_sha256, code="ASSEMBLER_EXPECTED_TERM_INVALID"
        ),
        witness_transition_id=transition,
    )


def _baseline_from_binding(value: object) -> _BaselineFacts:
    if type(value) is not PhysicalWalSourceManifestBaseline:
        _fail("ASSEMBLER_BASELINE_INVALID")
    generation = value.baseline_generation_id
    system_identifier = value.database_system_identifier
    if not isinstance(generation, str) or STREAM_GENERATION_ID_RE.fullmatch(generation) is None:
        _fail("ASSEMBLER_BASELINE_INVALID")
    if not isinstance(system_identifier, str) or _SYSTEM_IDENTIFIER_RE.fullmatch(system_identifier) is None:
        _fail("ASSEMBLER_BASELINE_INVALID")
    if type(value.timeline_id) is not int or not 1 <= value.timeline_id <= 0xFFFFFFFF:
        _fail("ASSEMBLER_BASELINE_INVALID")
    if (
        type(value.wal_segment_size_bytes) is not int
        or value.wal_segment_size_bytes != REQUIRED_WAL_SEGMENT_SIZE_BYTES
        or value.wal_segment_size_bytes not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES
    ):
        _fail("ASSEMBLER_BASELINE_INVALID")
    baseline_lsn, baseline_value = _lsn(value.baseline_wal_lsn, code="ASSEMBLER_BASELINE_INVALID")
    chain_start, chain_value = _lsn(value.wal_chain_start_lsn, code="ASSEMBLER_BASELINE_INVALID")
    backup_end, backup_end_value = _lsn(value.base_backup_end_lsn, code="ASSEMBLER_BASELINE_INVALID")
    if (
        backup_end_value <= baseline_value
        or chain_value % value.wal_segment_size_bytes
        or chain_value > baseline_value
        or baseline_value >= chain_value + value.wal_segment_size_bytes
    ):
        _fail("ASSEMBLER_BASELINE_INVALID")
    return _BaselineFacts(
        baseline_generation_id=generation,
        database_system_identifier=system_identifier,
        timeline_id=value.timeline_id,
        wal_segment_size_bytes=value.wal_segment_size_bytes,
        baseline_wal_lsn=baseline_lsn,
        baseline_wal_lsn_value=baseline_value,
        wal_chain_start_lsn=chain_start,
        wal_chain_start_lsn_value=chain_value,
        base_backup_end_lsn=backup_end,
        base_backup_end_lsn_value=backup_end_value,
    )


def _binding_facts(value: object) -> _BindingFacts:
    if type(value) is not PhysicalWalSourceManifestAssemblerBinding:
        _fail("ASSEMBLER_BINDING_INVALID")
    source = _site(value.source_site, code="ASSEMBLER_ROUTE_INVALID")
    destination = _site(value.destination_site, code="ASSEMBLER_ROUTE_INVALID")
    if source == destination:
        _fail("ASSEMBLER_ROUTE_INVALID")
    campaign = value.campaign_id
    release = value.release_sha
    recipient = value.destination_age_recipient
    if not isinstance(campaign, str) or CAMPAIGN_ID_RE.fullmatch(campaign) is None:
        _fail("ASSEMBLER_BINDING_INVALID")
    if not isinstance(release, str) or RELEASE_SHA_RE.fullmatch(release) is None:
        _fail("ASSEMBLER_BINDING_INVALID")
    if not isinstance(recipient, str) or AGE_RECIPIENT_RE.fullmatch(recipient) is None:
        _fail("ASSEMBLER_BINDING_INVALID")
    if not isinstance(value.wal_stream_generation_id, str) or STREAM_GENERATION_ID_RE.fullmatch(
        value.wal_stream_generation_id
    ) is None:
        _fail("ASSEMBLER_BINDING_INVALID")
    if not isinstance(value.source_public_key, bytes) or len(value.source_public_key) != 32:
        _fail("ASSEMBLER_SOURCE_SIGNER_INVALID")
    if (
        type(value.wal_upload_manifest_sha256es) is not tuple
        or not value.wal_upload_manifest_sha256es
        or len(value.wal_upload_manifest_sha256es) > MAX_PHYSICAL_WAL_SOURCE_UPLOAD_MANIFESTS
    ):
        _fail("ASSEMBLER_BINDING_INVALID")
    expected_wal_hashes = tuple(
        _sha256(item, code="ASSEMBLER_BINDING_INVALID")
        for item in value.wal_upload_manifest_sha256es
    )
    if len(set(expected_wal_hashes)) != len(expected_wal_hashes):
        _fail("ASSEMBLER_BINDING_INVALID")
    return _BindingFacts(
        source_site=source,
        destination_site=destination,
        campaign_id=campaign,
        release_sha=release,
        term=_term_from_binding(value.expected_term, source_site=source),
        baseline=_baseline_from_binding(value.baseline),
        destination_age_recipient=recipient,
        object_storage_namespace=_object_storage_namespace(
            value.object_storage_namespace,
            source_site=source,
            destination_site=destination,
            code="ASSEMBLER_BINDING_INVALID",
        ),
        base_backup_manifest_sha256=_sha256(
            value.base_backup_manifest_sha256, code="ASSEMBLER_BINDING_INVALID"
        ),
        wal_stream_generation_id=value.wal_stream_generation_id,
        wal_archive_manifest_sha256=_sha256(
            value.wal_archive_manifest_sha256, code="ASSEMBLER_BINDING_INVALID"
        ),
        wal_route_binding_sha256=_sha256(
            value.wal_route_binding_sha256, code="ASSEMBLER_BINDING_INVALID"
        ),
        wal_upload_manifest_sha256es=expected_wal_hashes,
        source_public_key=value.source_public_key,
        source_signer=value.source_signer,
    )


def _base_bootstrap_binding_facts(value: object) -> _BaseBootstrapFacts:
    if type(value) is not PhysicalWalSourceBaseManifestBootstrapBinding:
        _fail("BASE_BOOTSTRAP_BINDING_INVALID")
    source = _site(value.source_site, code="BASE_BOOTSTRAP_ROUTE_INVALID")
    destination = _site(value.destination_site, code="BASE_BOOTSTRAP_ROUTE_INVALID")
    if source == destination:
        _fail("BASE_BOOTSTRAP_ROUTE_INVALID")
    campaign = value.campaign_id
    release = value.release_sha
    recipient = value.destination_age_recipient
    if not isinstance(campaign, str) or CAMPAIGN_ID_RE.fullmatch(campaign) is None:
        _fail("BASE_BOOTSTRAP_BINDING_INVALID")
    if not isinstance(release, str) or RELEASE_SHA_RE.fullmatch(release) is None:
        _fail("BASE_BOOTSTRAP_BINDING_INVALID")
    if not isinstance(recipient, str) or AGE_RECIPIENT_RE.fullmatch(recipient) is None:
        _fail("BASE_BOOTSTRAP_BINDING_INVALID")
    if not isinstance(value.source_public_key, bytes) or len(value.source_public_key) != 32:
        _fail("BASE_BOOTSTRAP_SOURCE_SIGNER_INVALID")
    return _BaseBootstrapFacts(
        source_site=source,
        destination_site=destination,
        campaign_id=campaign,
        release_sha=release,
        term=_term_from_binding(value.expected_term, source_site=source),
        baseline=_baseline_from_binding(value.baseline),
        destination_age_recipient=recipient,
        object_storage_namespace=_object_storage_namespace(
            value.object_storage_namespace,
            source_site=source,
            destination_site=destination,
            code="BASE_BOOTSTRAP_BINDING_INVALID",
        ),
        base_route_binding_sha256=_sha256(
            value.base_route_binding_sha256, code="BASE_BOOTSTRAP_BINDING_INVALID"
        ),
        base_completion_record_sha256=_sha256(
            value.base_completion_record_sha256, code="BASE_BOOTSTRAP_BINDING_INVALID"
        ),
        source_public_key=value.source_public_key,
        source_signer=value.source_signer,
    )


def _append_binding_facts(value: object) -> tuple[_BindingFacts, str, str]:
    if type(value) is not PhysicalWalSourceManifestAppendBinding:
        _fail("APPEND_BINDING_INVALID")
    facts = _binding_facts(value.source_manifest_binding)
    base_manifest_sha256 = _sha256(value.base_backup_manifest_sha256, code="APPEND_BINDING_INVALID")
    if base_manifest_sha256 != facts.base_backup_manifest_sha256:
        _fail("APPEND_BINDING_INVALID")
    return (
        facts,
        base_manifest_sha256,
        _sha256(value.previous_wal_segment_manifest_sha256, code="APPEND_BINDING_INVALID"),
    )


def _object_descriptor(value: object, *, expected_kind: str, code: str) -> dict[str, Any]:
    item = _exact_mapping(value, fields=_OBJECT_FIELDS, code=code)
    key = item["object_key"]
    version = item["version_id"]
    if (
        item["schema"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION
        or item["object_kind"] != expected_kind
        or item["encryption"] != PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION
        or item["immutability"] != PHYSICAL_WAL_OBJECT_IMMUTABILITY
    ):
        _fail(code)
    if (
        not isinstance(key, str)
        or OBJECT_KEY_RE.fullmatch(key) is None
        or not key.endswith(".age")
        or any(part in {"", ".", ".."} for part in key.split("/"))
        or any(part.casefold() in {"latest", "current", "mutable"} for part in key.split("/"))
    ):
        _fail(code)
    if (
        not isinstance(version, str)
        or VERSION_ID_RE.fullmatch(version) is None
        or version.casefold() in {"null", "none", "latest", "current"}
    ):
        _fail(code)
    if not isinstance(item["age_recipient"], str) or AGE_RECIPIENT_RE.fullmatch(item["age_recipient"]) is None:
        _fail(code)
    return {
        "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
        "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
        "object_kind": expected_kind,
        "object_key": key,
        "version_id": version,
        "ciphertext_sha256": _sha256(item["ciphertext_sha256"], code=code),
        "ciphertext_bytes": _positive_int(
            item["ciphertext_bytes"],
            code=code,
            maximum=MAX_PHYSICAL_WAL_OBJECT_CIPHERTEXT_BYTES,
        ),
        "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        "age_recipient": item["age_recipient"],
        "immutability": PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    }


def _object_key_prefix(binding: _BindingFacts) -> str:
    baseline = binding.baseline
    return "/".join(
        (
            binding.object_storage_namespace,
            binding.campaign_id,
            binding.release_sha,
            baseline.baseline_generation_id,
            f"{binding.source_site}-to-{binding.destination_site}",
            f"timeline-{baseline.timeline_id:08X}",
        )
    )


def _expected_base_backup_object_key(
    *,
    binding: _BindingFacts,
    snapshot_sha256: str,
) -> str:
    return "/".join(
        (
            _object_key_prefix(binding),
            "base-backup",
            f"{snapshot_sha256}.age",
        )
    )


def _expected_wal_object_key(
    *,
    binding: _BindingFacts,
    wal_segment_name: str,
    snapshot_sha256: str,
) -> str:
    return "/".join(
        (
            _object_key_prefix(binding),
            wal_segment_name,
            f"{snapshot_sha256}.age",
        )
    )


def _base_term(value: object, *, code: str) -> _TermFacts:
    item = _exact_mapping(value, fields=_BASE_TERM_FIELDS, code=code)
    holder = _site(item["holder_site"], code=code)
    lease = item["lease_id"]
    transition = item["witness_transition_id"]
    if not isinstance(lease, str) or LEASE_ID_RE.fullmatch(lease) is None:
        _fail(code)
    if not isinstance(transition, str) or _TRANSITION_ID_RE.fullmatch(transition) is None:
        _fail(code)
    return _TermFacts(
        holder_site=holder,
        writer_epoch=_positive_int(item["epoch"], code=code),
        writer_lease_id=lease,
        witnessed_term_proof_sha256=_sha256(item["witnessed_term_proof_sha256"], code=code),
        witness_transition_id=transition,
    )


def _wal_term(value: object, *, code: str) -> tuple[str, int, str, str]:
    item = _exact_mapping(value, fields=_WAL_TERM_FIELDS, code=code)
    holder = _site(item["holder_site"], code=code)
    lease = item["writer_lease_id"]
    if not isinstance(lease, str) or LEASE_ID_RE.fullmatch(lease) is None:
        _fail(code)
    return (
        holder,
        _positive_int(item["writer_epoch"], code=code),
        lease,
        _sha256(item["witnessed_term_proof_sha256"], code=code),
    )


def _match_common(
    item: Mapping[str, Any],
    *,
    binding: _BindingFacts | _BaseBootstrapFacts,
    code: str,
) -> None:
    baseline = binding.baseline
    if (
        item["source_site"] != binding.source_site
        or item["destination_site"] != binding.destination_site
        or item["campaign_id"] != binding.campaign_id
        or item["release_sha"] != binding.release_sha
        or item["baseline_generation_id"] != baseline.baseline_generation_id
        or item["database_system_identifier"] != baseline.database_system_identifier
        or type(item["timeline_id"]) is not int
        or item["timeline_id"] != baseline.timeline_id
        or item["wal_segment_size_bytes"] != baseline.wal_segment_size_bytes
        or item["destination_age_recipient"] != binding.destination_age_recipient
        or item["object_storage_namespace"] != binding.object_storage_namespace
    ):
        _fail(code)


def _base_completion_facts(
    raw: object,
    *,
    binding: _BaseBootstrapFacts,
) -> _BaseCompletionFacts:
    raw = _bounded_record_bytes(raw, code="BASE_COMPLETION_RECORD_INVALID")
    if hashlib.sha256(raw).hexdigest() != binding.base_completion_record_sha256:
        _fail("BASE_COMPLETION_RECORD_TAMPERED")
    item = _parse_canonical_bytes(raw, code="BASE_COMPLETION_RECORD_INVALID")
    item = _exact_mapping(item, fields=_BASE_RECORD_FIELDS, code="BASE_COMPLETION_RECORD_INVALID")
    if (
        item["schema"] != _BASE_BACKUP_COMPLETION_RECORD_SCHEMA
        or item["kind"] != "physical_postgresql_base_backup_uploaded_archive_recovery_only"
        or item["not_a_remote_apply_proof"] is not True
        or item["not_a_strict_acknowledgement_proof"] is not True
        or _sha256(item["handoff_descriptor_sha256"], code="BASE_COMPLETION_RECORD_INVALID")
        != item["handoff_descriptor_sha256"]
        or _sha256(item["route_binding_sha256"], code="BASE_COMPLETION_RECORD_INVALID")
        != binding.base_route_binding_sha256
    ):
        _fail("BASE_COMPLETION_RECORD_INVALID")
    _match_common(item, binding=binding, code="BASE_COMPLETION_RECORD_FOREIGN")
    baseline = binding.baseline
    if (
        item["baseline_wal_lsn"] != baseline.baseline_wal_lsn
        or item["wal_chain_start_lsn"] != baseline.wal_chain_start_lsn
        or item["base_backup_end_lsn"] != baseline.base_backup_end_lsn
        or _base_term(item["writer_term"], code="BASE_COMPLETION_RECORD_TERM_INVALID") != binding.term
    ):
        _fail("BASE_COMPLETION_RECORD_FOREIGN")
    artifact = _exact_mapping(
        item["completed_source_artifact"],
        fields=_SOURCE_ARTIFACT_FIELDS,
        code="BASE_COMPLETION_RECORD_ARTIFACT_INVALID",
    )
    if (
        not isinstance(artifact["artifact_name"], str)
        or _ARTIFACT_NAME_RE.fullmatch(artifact["artifact_name"]) is None
        or _sha256(artifact["plaintext_sha256"], code="BASE_COMPLETION_RECORD_ARTIFACT_INVALID")
        != item["snapshot_sha256"]
        or _positive_int(artifact["plaintext_bytes"], code="BASE_COMPLETION_RECORD_ARTIFACT_INVALID")
        != item["snapshot_bytes"]
    ):
        _fail("BASE_COMPLETION_RECORD_ARTIFACT_INVALID")
    _sha256(artifact["completion_attestation_sha256"], code="BASE_COMPLETION_RECORD_ARTIFACT_INVALID")
    snapshot_sha256 = _sha256(
        item["snapshot_sha256"], code="BASE_COMPLETION_RECORD_ARTIFACT_INVALID"
    )
    _positive_int(item["snapshot_bytes"], code="BASE_COMPLETION_RECORD_ARTIFACT_INVALID")
    object_descriptor = _object_descriptor(
        item["object"],
        expected_kind="physical_postgresql_base_backup",
        code="BASE_COMPLETION_RECORD_OBJECT_INVALID",
    )
    if object_descriptor["age_recipient"] != binding.destination_age_recipient:
        _fail("BASE_COMPLETION_RECORD_RECIPIENT_MISMATCH")
    if object_descriptor["object_key"] != _expected_base_backup_object_key(
        binding=binding,
        snapshot_sha256=snapshot_sha256,
    ):
        _fail("BASE_COMPLETION_RECORD_OBJECT_KEY_MISMATCH")
    return _BaseCompletionFacts(object_descriptor=object_descriptor)


def _verified_bound_base_manifest(
    raw_value: object,
    *,
    facts: _BindingFacts | _BaseBootstrapFacts,
    expected_sha256: str,
    invalid_code: str,
    tampered_code: str,
) -> tuple[bytes, object]:
    """Return one exact, canonical, signed base manifest under all static pins.

    The SHA pin is checked *before* parsing.  The signed base itself is then
    independently reverified so a raw byte string that merely hashes to a
    caller-provided value cannot become an initial WAL lineage.  The manifest
    verifier deliberately has a smaller expected-parameter surface than this
    assembler, so the remaining baseline geometry is compared explicitly.
    """

    raw = _bounded_signed_manifest_bytes(raw_value, code=invalid_code)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _fail(tampered_code)
    try:
        verified = verify_physical_wal_base_backup_manifest(
            raw,
            expected_source_public_key=facts.source_public_key,
            expected_source_site=facts.source_site,
            expected_destination_site=facts.destination_site,
            expected_campaign_id=facts.campaign_id,
            expected_release_sha=facts.release_sha,
            expected_writer_epoch=facts.term.writer_epoch,
            expected_writer_lease_id=facts.term.writer_lease_id,
            expected_witnessed_term_proof_sha256=facts.term.witnessed_term_proof_sha256,
            expected_baseline_generation_id=facts.baseline.baseline_generation_id,
            expected_wal_segment_size_bytes=facts.baseline.wal_segment_size_bytes,
            expected_destination_age_recipient=facts.destination_age_recipient,
        )
    except PhysicalWalObjectManifestError as exc:
        raise PhysicalWalSourceManifestAssemblerError(invalid_code) from exc
    baseline = facts.baseline
    if (
        verified.manifest_sha256 != expected_sha256
        or verified.database_system_identifier != baseline.database_system_identifier
        or verified.timeline_id != baseline.timeline_id
        or verified.baseline_wal_lsn != baseline.baseline_wal_lsn
        or verified.wal_chain_start_lsn != baseline.wal_chain_start_lsn
        or verified.base_backup_end_lsn != baseline.base_backup_end_lsn
    ):
        _fail(invalid_code)
    return raw, verified


def _expected_wal_name(*, timeline_id: int, start_lsn_value: int, segment_size: int) -> str:
    if start_lsn_value % segment_size:
        _fail("WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID")
    segments_per_log = 0x100000000 // segment_size
    segment_number = start_lsn_value // segment_size
    log = segment_number // segments_per_log
    segment = segment_number % segments_per_log
    if log > 0xFFFFFFFF or segment > 0xFFFFFFFF:
        _fail("WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID")
    return f"{timeline_id:08X}{log:08X}{segment:08X}"


def _wal_upload_facts(
    raw: object,
    *,
    binding: _BindingFacts,
    base_manifest_sha256: str,
    expected_upload_manifest_sha256: str,
) -> _WalUploadFacts:
    raw = _bounded_record_bytes(raw, code="WAL_UPLOAD_MANIFEST_INVALID")
    if hashlib.sha256(raw).hexdigest() != expected_upload_manifest_sha256:
        _fail("WAL_UPLOAD_MANIFEST_TAMPERED")
    item = _parse_canonical_bytes(raw, code="WAL_UPLOAD_MANIFEST_INVALID")
    item = _exact_mapping(item, fields=_WAL_UPLOAD_FIELDS, code="WAL_UPLOAD_MANIFEST_INVALID")
    if (
        item["schema"] != _WAL_UPLOAD_MANIFEST_SCHEMA
        or item["kind"] != "physical_wal_segment_uploaded"
        or _sha256(item["handoff_descriptor_sha256"], code="WAL_UPLOAD_MANIFEST_INVALID")
        != item["handoff_descriptor_sha256"]
        or _sha256(item["route_binding_sha256"], code="WAL_UPLOAD_MANIFEST_INVALID")
        != binding.wal_route_binding_sha256
        or _sha256(item["archive_manifest_sha256"], code="WAL_UPLOAD_MANIFEST_INVALID")
        != binding.wal_archive_manifest_sha256
        or item["stream_generation_id"] != binding.wal_stream_generation_id
        or item["baseline_manifest_sha256"] != base_manifest_sha256
    ):
        _fail("WAL_UPLOAD_MANIFEST_INVALID")
    _match_common(item, binding=binding, code="WAL_UPLOAD_MANIFEST_FOREIGN")
    baseline = binding.baseline
    if (
        item["baseline_wal_lsn"] != baseline.baseline_wal_lsn
        or item["wal_chain_start_lsn"] != baseline.wal_chain_start_lsn
        or _wal_term(item["writer_term"], code="WAL_UPLOAD_MANIFEST_TERM_INVALID")
        != (
            binding.term.holder_site,
            binding.term.writer_epoch,
            binding.term.writer_lease_id,
            binding.term.witnessed_term_proof_sha256,
        )
    ):
        _fail("WAL_UPLOAD_MANIFEST_FOREIGN")
    ordinal = _nonnegative_int(item["segment_ordinal"], code="WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID")
    name = item["wal_segment_name"]
    if not isinstance(name, str) or _WAL_SEGMENT_NAME_RE.fullmatch(name) is None:
        _fail("WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID")
    start_lsn, start_value = _lsn(item["start_lsn"], code="WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID")
    end_lsn, end_value = _lsn(item["end_lsn"], code="WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID")
    if (
        name != _expected_wal_name(
            timeline_id=baseline.timeline_id,
            start_lsn_value=start_value,
            segment_size=baseline.wal_segment_size_bytes,
        )
        or ordinal != start_value // baseline.wal_segment_size_bytes
        or end_value != start_value + baseline.wal_segment_size_bytes
        or _positive_int(item["snapshot_bytes"], code="WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID")
        != baseline.wal_segment_size_bytes
    ):
        _fail("WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID")
    snapshot_sha256 = _sha256(
        item["snapshot_sha256"], code="WAL_UPLOAD_MANIFEST_GEOMETRY_INVALID"
    )
    object_descriptor = _object_descriptor(
        item["object"],
        expected_kind="postgresql_wal_segment",
        code="WAL_UPLOAD_MANIFEST_OBJECT_INVALID",
    )
    if object_descriptor["age_recipient"] != binding.destination_age_recipient:
        _fail("WAL_UPLOAD_MANIFEST_RECIPIENT_MISMATCH")
    if object_descriptor["object_key"] != _expected_wal_object_key(
        binding=binding,
        wal_segment_name=name,
        snapshot_sha256=snapshot_sha256,
    ):
        _fail("WAL_UPLOAD_MANIFEST_OBJECT_KEY_MISMATCH")
    return _WalUploadFacts(
        ordinal=ordinal,
        segment_name=name,
        start_lsn=start_lsn,
        start_lsn_value=start_value,
        end_lsn=end_lsn,
        end_lsn_value=end_value,
        object_descriptor=object_descriptor,
    )


def _normalize_upload_sequence(value: object) -> tuple[bytes, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail("WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID")
    try:
        declared_count = len(value)
    except (OverflowError, TypeError) as exc:
        raise PhysicalWalSourceManifestAssemblerError(
            "WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID"
        ) from exc
    if not 1 <= declared_count <= MAX_PHYSICAL_WAL_SOURCE_UPLOAD_MANIFESTS:
        _fail("WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID")
    try:
        # Indexing exactly the prevalidated Sequence length avoids trusting a
        # custom iterator that yields an unbounded number of records despite
        # reporting a small ``len``.
        result = tuple(value[index] for index in range(declared_count))
    except (IndexError, TypeError) as exc:
        raise PhysicalWalSourceManifestAssemblerError(
            "WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID"
        ) from exc
    if any(not isinstance(item, bytes) for item in result):
        _fail("WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID")
    return result


def _validate_contiguous_wal_uploads(
    uploads: Sequence[_WalUploadFacts],
    *,
    binding: _BindingFacts,
    base_object_pair: tuple[str, str],
    previous_end_lsn_value: int,
    previous_segment_ordinal: int,
) -> None:
    prior_end = previous_end_lsn_value
    prior_ordinal = previous_segment_ordinal
    names: set[str] = set()
    objects: set[tuple[str, str]] = set()
    for upload in uploads:
        object_pair = (
            upload.object_descriptor["object_key"],
            upload.object_descriptor["version_id"],
        )
        if (
            upload.ordinal != prior_ordinal + 1
            or upload.start_lsn_value != prior_end
            or upload.segment_name in names
            or object_pair == base_object_pair
            or object_pair in objects
        ):
            _fail("WAL_UPLOAD_MANIFEST_CHAIN_INVALID")
        prior_end = upload.end_lsn_value
        prior_ordinal = upload.ordinal
        names.add(upload.segment_name)
        objects.add(object_pair)
    if prior_end < binding.baseline.base_backup_end_lsn_value:
        _fail("WAL_CHAIN_DOES_NOT_COVER_BASE_BACKUP_END")


def _segments_for_manifest(
    uploads: Sequence[_WalUploadFacts],
    *,
    timeline_id: int,
) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": upload.ordinal,
            "wal_segment_name": upload.segment_name,
            "timeline_id": timeline_id,
            "start_lsn": upload.start_lsn,
            "end_lsn": upload.end_lsn,
            "object": upload.object_descriptor,
        }
        for upload in uploads
    ]


def _build_verified_wal_outputs(
    *,
    uploads: Sequence[_WalUploadFacts],
    facts: _BindingFacts,
    base_manifest_sha256: str,
    verified_base: object,
    previous_manifest_sha256: str,
    previous_end_lsn: str,
    previous_segment_ordinal: int,
) -> tuple[tuple[bytes, ...], tuple[str, ...], str]:
    wal_bytes: list[bytes] = []
    wal_hashes: list[str] = []
    terminal_lsn: str | None = None
    for index in range(0, len(uploads), MAX_PHYSICAL_WAL_SEGMENTS_PER_MANIFEST):
        batch = uploads[index : index + MAX_PHYSICAL_WAL_SEGMENTS_PER_MANIFEST]
        segments = _segments_for_manifest(batch, timeline_id=facts.baseline.timeline_id)
        try:
            manifest = build_physical_wal_segment_manifest(
                source_site=facts.source_site,
                destination_site=facts.destination_site,
                campaign_id=facts.campaign_id,
                release_sha=facts.release_sha,
                writer_epoch=facts.term.writer_epoch,
                writer_lease_id=facts.term.writer_lease_id,
                witnessed_term_proof_sha256=facts.term.witnessed_term_proof_sha256,
                baseline_generation_id=facts.baseline.baseline_generation_id,
                baseline_manifest_sha256=base_manifest_sha256,
                database_system_identifier=facts.baseline.database_system_identifier,
                timeline_id=facts.baseline.timeline_id,
                wal_segment_size_bytes=facts.baseline.wal_segment_size_bytes,
                previous_manifest_sha256=previous_manifest_sha256,
                previous_end_lsn=previous_end_lsn,
                previous_segment_ordinal=previous_segment_ordinal,
                segments=segments,
                source_signer=facts.source_signer,
            )
        except (PhysicalWalObjectManifestError, AttributeError, TypeError, ValueError) as exc:
            raise PhysicalWalSourceManifestAssemblerError("WAL_MANIFEST_ASSEMBLY_INVALID") from exc
        raw_manifest = _canonical(manifest, code="WAL_MANIFEST_ASSEMBLY_INVALID")
        manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
        try:
            verified = verify_physical_wal_segment_manifest(
                raw_manifest,
                expected_source_public_key=facts.source_public_key,
                expected_baseline=verified_base,
                expected_previous_manifest_sha256=previous_manifest_sha256,
                expected_previous_end_lsn=previous_end_lsn,
                expected_previous_segment_ordinal=previous_segment_ordinal,
                expected_destination_age_recipient=facts.destination_age_recipient,
            )
        except PhysicalWalObjectManifestError as exc:
            raise PhysicalWalSourceManifestAssemblerError("WAL_MANIFEST_ASSEMBLY_INVALID") from exc
        wal_bytes.append(raw_manifest)
        wal_hashes.append(manifest_sha256)
        previous_manifest_sha256 = manifest_sha256
        previous_end_lsn = verified.end_lsn
        previous_segment_ordinal = verified.last_segment_ordinal
        terminal_lsn = verified.end_lsn
    if terminal_lsn is None:
        _fail("WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID")
    return tuple(wal_bytes), tuple(wal_hashes), terminal_lsn


def _bootstrap_base_manifest(
    *,
    base_backup_completion_record: object,
    binding: object,
) -> PhysicalWalSourceBaseManifestBootstrap:
    """Mint the immutable initial base claim before WAL receipts exist."""

    facts = _base_bootstrap_binding_facts(binding)
    base = _base_completion_facts(base_backup_completion_record, binding=facts)
    try:
        manifest = build_physical_wal_base_backup_manifest(
            source_site=facts.source_site,
            destination_site=facts.destination_site,
            campaign_id=facts.campaign_id,
            release_sha=facts.release_sha,
            writer_epoch=facts.term.writer_epoch,
            writer_lease_id=facts.term.writer_lease_id,
            witnessed_term_proof_sha256=facts.term.witnessed_term_proof_sha256,
            baseline_generation_id=facts.baseline.baseline_generation_id,
            database_system_identifier=facts.baseline.database_system_identifier,
            timeline_id=facts.baseline.timeline_id,
            wal_segment_size_bytes=facts.baseline.wal_segment_size_bytes,
            baseline_wal_lsn=facts.baseline.baseline_wal_lsn,
            wal_chain_start_lsn=facts.baseline.wal_chain_start_lsn,
            base_backup_end_lsn=facts.baseline.base_backup_end_lsn,
            base_backup_object=base.object_descriptor,
            source_signer=facts.source_signer,
        )
    except (PhysicalWalObjectManifestError, AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalSourceManifestAssemblerError(
            "SOURCE_SIGNER_OR_BASE_MANIFEST_INVALID"
        ) from exc
    raw = _canonical(manifest, code="SOURCE_SIGNER_OR_BASE_MANIFEST_INVALID")
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    _verified_bound_base_manifest(
        raw,
        facts=facts,
        expected_sha256=manifest_sha256,
        invalid_code="SOURCE_SIGNER_OR_BASE_MANIFEST_INVALID",
        tampered_code="SOURCE_SIGNER_OR_BASE_MANIFEST_INVALID",
    )
    return PhysicalWalSourceBaseManifestBootstrap(
        schema=PHYSICAL_WAL_SOURCE_BASE_MANIFEST_BOOTSTRAP_SCHEMA,
        base_backup_manifest=raw,
        base_backup_manifest_sha256=manifest_sha256,
    )


def _assemble(
    *,
    base_backup_manifest: object,
    wal_upload_manifests: object,
    binding: object,
) -> PhysicalWalSourceManifestAssembly:
    facts = _binding_facts(binding)
    base_bytes, verified_base = _verified_bound_base_manifest(
        base_backup_manifest,
        facts=facts,
        expected_sha256=facts.base_backup_manifest_sha256,
        invalid_code="INITIAL_BASE_MANIFEST_INVALID",
        tampered_code="INITIAL_BASE_MANIFEST_TAMPERED",
    )
    base_sha256 = facts.base_backup_manifest_sha256
    raw_uploads = _normalize_upload_sequence(wal_upload_manifests)
    if len(raw_uploads) != len(facts.wal_upload_manifest_sha256es):
        _fail("WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID")
    uploads = tuple(
        _wal_upload_facts(
            raw,
            binding=facts,
            base_manifest_sha256=base_sha256,
            expected_upload_manifest_sha256=expected_hash,
        )
        for raw, expected_hash in zip(raw_uploads, facts.wal_upload_manifest_sha256es)
    )
    _validate_contiguous_wal_uploads(
        uploads,
        binding=facts,
        base_object_pair=(
            verified_base.base_backup_object.object_key,
            verified_base.base_backup_object.version_id,
        ),
        previous_end_lsn_value=facts.baseline.wal_chain_start_lsn_value,
        previous_segment_ordinal=(
            facts.baseline.wal_chain_start_lsn_value
            // facts.baseline.wal_segment_size_bytes
            - 1
        ),
    )
    wal_bytes, wal_hashes, terminal_lsn = _build_verified_wal_outputs(
        uploads=uploads,
        facts=facts,
        base_manifest_sha256=base_sha256,
        verified_base=verified_base,
        previous_manifest_sha256=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
        previous_end_lsn=facts.baseline.wal_chain_start_lsn,
        previous_segment_ordinal=(
            facts.baseline.wal_chain_start_lsn_value
            // facts.baseline.wal_segment_size_bytes
            - 1
        ),
    )
    return PhysicalWalSourceManifestAssembly(
        schema=PHYSICAL_WAL_SOURCE_MANIFEST_ASSEMBLY_SCHEMA,
        base_backup_manifest=base_bytes,
        base_backup_manifest_sha256=base_sha256,
        wal_segment_manifests=wal_bytes,
        wal_segment_manifest_sha256es=wal_hashes,
        terminal_wal_lsn=terminal_lsn,
        blob_frontier_requirement=PHYSICAL_WAL_SOURCE_MANIFEST_BLOB_FRONTIER_REQUIRED,
    )


def _append(
    *,
    base_backup_manifest: object,
    previous_wal_segment_manifest: object,
    wal_upload_manifests: object,
    binding: object,
) -> PhysicalWalSourceManifestAppendAssembly:
    facts, expected_base_sha256, expected_previous_sha256 = _append_binding_facts(binding)
    _base_raw, verified_base = _verified_bound_base_manifest(
        base_backup_manifest,
        facts=facts,
        expected_sha256=expected_base_sha256,
        invalid_code="APPEND_BASE_MANIFEST_INVALID",
        tampered_code="APPEND_BASE_MANIFEST_TAMPERED",
    )

    previous_raw = _bounded_signed_manifest_bytes(
        previous_wal_segment_manifest,
        code="APPEND_PREDECESSOR_MANIFEST_INVALID",
    )
    if hashlib.sha256(previous_raw).hexdigest() != expected_previous_sha256:
        _fail("APPEND_PREDECESSOR_MANIFEST_TAMPERED")
    try:
        verified_previous = verify_physical_wal_segment_manifest(
            previous_raw,
            expected_source_public_key=facts.source_public_key,
            expected_baseline=verified_base,
            expected_destination_age_recipient=facts.destination_age_recipient,
        )
    except PhysicalWalObjectManifestError as exc:
        raise PhysicalWalSourceManifestAssemblerError("APPEND_PREDECESSOR_MANIFEST_INVALID") from exc

    _previous_end, previous_end_value = _lsn(
        verified_previous.end_lsn,
        code="APPEND_PREDECESSOR_MANIFEST_INVALID",
    )
    genesis_predecessor_ordinal = (
        facts.baseline.wal_chain_start_lsn_value
        // facts.baseline.wal_segment_size_bytes
        - 1
    )
    if (
        previous_end_value <= facts.baseline.wal_chain_start_lsn_value
        or verified_previous.last_segment_ordinal <= genesis_predecessor_ordinal
    ):
        _fail("APPEND_PREDECESSOR_FRONTIER_INVALID")
    if verified_previous.previous_manifest_sha256 == PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256 and (
        verified_previous.previous_end_lsn != facts.baseline.wal_chain_start_lsn
        or verified_previous.previous_segment_ordinal != genesis_predecessor_ordinal
    ):
        _fail("APPEND_PREDECESSOR_FRONTIER_INVALID")

    raw_uploads = _normalize_upload_sequence(wal_upload_manifests)
    if len(raw_uploads) != len(facts.wal_upload_manifest_sha256es):
        _fail("WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID")
    uploads = tuple(
        _wal_upload_facts(
            raw,
            binding=facts,
            base_manifest_sha256=expected_base_sha256,
            expected_upload_manifest_sha256=expected_hash,
        )
        for raw, expected_hash in zip(raw_uploads, facts.wal_upload_manifest_sha256es)
    )
    _validate_contiguous_wal_uploads(
        uploads,
        binding=facts,
        base_object_pair=(
            verified_base.base_backup_object.object_key,
            verified_base.base_backup_object.version_id,
        ),
        previous_end_lsn_value=previous_end_value,
        previous_segment_ordinal=verified_previous.last_segment_ordinal,
    )
    wal_bytes, wal_hashes, terminal_lsn = _build_verified_wal_outputs(
        uploads=uploads,
        facts=facts,
        base_manifest_sha256=expected_base_sha256,
        verified_base=verified_base,
        previous_manifest_sha256=expected_previous_sha256,
        previous_end_lsn=verified_previous.end_lsn,
        previous_segment_ordinal=verified_previous.last_segment_ordinal,
    )
    return PhysicalWalSourceManifestAppendAssembly(
        schema=PHYSICAL_WAL_SOURCE_MANIFEST_APPEND_ASSEMBLY_SCHEMA,
        base_backup_manifest_sha256=expected_base_sha256,
        previous_wal_segment_manifest_sha256=expected_previous_sha256,
        wal_segment_manifests=wal_bytes,
        wal_segment_manifest_sha256es=wal_hashes,
        terminal_wal_lsn=terminal_lsn,
        blob_frontier_requirement=PHYSICAL_WAL_SOURCE_MANIFEST_BLOB_FRONTIER_REQUIRED,
    )


def bootstrap_physical_wal_base_backup_manifest(
    *,
    base_backup_completion_record: bytes,
    binding: PhysicalWalSourceBaseManifestBootstrapBinding,
) -> PhysicalWalSourceBaseManifestBootstrap:
    """Mint the exact signed base manifest before initial WAL upload receipts.

    The base completion record is SHA-pinned in the bootstrap binding.  The
    returned canonical signed bytes and hash must be durably pinned before the
    WAL archive uploader creates receipts that reference that base hash.  This
    pure function neither publishes the base manifest nor performs any I/O.
    """

    return _bootstrap_base_manifest(
        base_backup_completion_record=base_backup_completion_record,
        binding=binding,
    )


def assemble_physical_wal_source_manifest_chain(
    *,
    base_backup_manifest: bytes,
    wal_upload_manifests: Sequence[bytes],
    binding: PhysicalWalSourceManifestAssemblerBinding,
) -> PhysicalWalSourceManifestAssembly:
    """Build the initial genesis WAL link from a prior signed base bootstrap.

    The supplied base raw bytes must have the exact explicit hash pin in the
    binding and are reverified against the entire static route/term/baseline
    lineage.  WAL upload receipts then must bind to that same emitted base
    hash.  This entry point is deliberately for a new physical baseline only;
    later WAL batches must use :func:`append_physical_wal_source_manifest_chain`
    so they cannot silently restart genesis.  Neither output publishes
    anything or builds the separate signed blob-frontier artifact required for
    a complete receiver bundle.
    """

    return _assemble(
        base_backup_manifest=base_backup_manifest,
        wal_upload_manifests=wal_upload_manifests,
        binding=binding,
    )


def append_physical_wal_source_manifest_chain(
    *,
    base_backup_manifest: bytes,
    previous_wal_segment_manifest: bytes,
    wal_upload_manifests: Sequence[bytes],
    binding: PhysicalWalSourceManifestAppendBinding,
) -> PhysicalWalSourceManifestAppendAssembly:
    """Build only new signed WAL links after one exact signed predecessor.

    The predecessor's raw canonical bytes and hash pin are reverified before
    its end-LSN and absolute ordinal become the next link's inputs.  A durable
    source-side cursor and atomic publication/CAS remain external adapters;
    this pure boundary neither reads nor updates either one.
    """

    return _append(
        base_backup_manifest=base_backup_manifest,
        previous_wal_segment_manifest=previous_wal_segment_manifest,
        wal_upload_manifests=wal_upload_manifests,
        binding=binding,
    )
