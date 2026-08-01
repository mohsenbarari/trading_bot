"""Pure fixed-mailbox grammar for the portable V2 Witness ACK roundtrip.

This module defines four *one-way* mailbox records only:

``FI -> Witness``
    one Witness context certificate plus one FI source envelope;
``Witness -> IR``
    the exact same certified envelope, never a freshly reconstructed request;
``IR -> Witness``
    one IR signed durable assertion; and
``Witness -> FI``
    one final Witness signed durable-roundtrip attestation.

The records are deterministic canonical envelopes around already-signed V2
wire artifacts.  They deliberately contain no delivery implementation: no
endpoint, host, URL, client, command, credential, provider, filesystem,
database, or generic send surface exists here.  A later role-local mailbox
runtime may carry only these opaque canonical byte strings after its own
separate review.

Each receiver recomputes every fixed pin from the nested artifact and an
exact public binding.  In particular, route, writer term, recipient key id,
object-pin receipt, expiration, nonce, and predecessor digest cannot be
relabeled by an outer mailbox record.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_contract as _roundtrip


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_SCHEMA",
    "PhysicalWalV2WitnessRoundtripDeliveryBinding",
    "PhysicalWalV2WitnessRoundtripDeliveryConfig",
    "PhysicalWalV2WitnessRoundtripDeliveryError",
    "VerifiedPhysicalWalV2WitnessRoundtripDelivery",
    "build_physical_wal_v2_witness_roundtrip_delivery_binding",
    "build_physical_wal_v2_witness_fi_to_witness_delivery",
    "build_physical_wal_v2_witness_witness_to_ir_delivery",
    "build_physical_wal_v2_witness_ir_to_witness_delivery",
    "build_physical_wal_v2_witness_witness_to_fi_delivery",
    "verify_physical_wal_v2_witness_fi_to_witness_delivery",
    "verify_physical_wal_v2_witness_witness_to_ir_delivery",
    "verify_physical_wal_v2_witness_ir_to_witness_delivery",
    "verify_physical_wal_v2_witness_witness_to_fi_delivery",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-delivery-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_BINDING_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-delivery-binding-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_OBJECT_PIN_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-object-pin-receipt-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_DEFAULT_ENABLED = False
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_VERSION = 1

MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_BYTES = 2 * 1024 * 1024

_FI_TO_WITNESS = "fi-to-witness"
_WITNESS_TO_IR = "witness-to-ir"
_IR_TO_WITNESS = "ir-to-witness"
_WITNESS_TO_FI = "witness-to-fi"
_MAILBOXES = frozenset(
    {_FI_TO_WITNESS, _WITNESS_TO_IR, _IR_TO_WITNESS, _WITNESS_TO_FI}
)
_ROLE_MATRIX = {
    _FI_TO_WITNESS: ("webapp_fi", "witness", "fi-writer-source-outbox", "witness-fi-ingress"),
    _WITNESS_TO_IR: ("witness", "webapp_ir", "witness-ir-egress", "ir-standby-ack-inbox"),
    _IR_TO_WITNESS: ("webapp_ir", "witness", "ir-durable-ack-outbox", "witness-ir-ingress"),
    _WITNESS_TO_FI: ("witness", "webapp_fi", "witness-fi-egress", "fi-writer-ack-inbox"),
}
_ZERO_SHA256 = "0" * 64
_CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_KEY_ID_RE = re.compile(r"^age-recipient-sha256:[0-9a-f]{64}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$",
    re.ASCII,
)

_PIN_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "release_sha",
        "object_storage_namespace",
        "destination_age_recipient",
        "canonical_manifest_sha256",
        "manifest_id",
        "handoff_receipt_id",
        "handoff_receipt_nonce",
        "lineage_sha256",
        "baseline_generation_id",
        "target_lsn",
        "base_backup_scope_sha256",
        "blob_frontier_scope_sha256",
        "blob_owner_coverage_sha256",
        "blob_coverage_id",
        "blob_coverage_nonce",
        "wal_continuity_scope_sha256",
        "wal_continuity_receipt_id",
        "wal_continuity_receipt_nonce",
        "wal_continuity_selector_set_sha256",
        "object_version_set_sha256",
        "coverage_scope_sha256",
        "object_count",
    }
)
_DELIVERY_FIELDS = frozenset(
    {
        "schema",
        "version",
        "mailbox",
        "sender_site",
        "recipient_site",
        "sender_role",
        "recipient_role",
        "delivery_binding_sha256",
        "prior_delivery_sha256",
        "campaign_id",
        "release_sha",
        "context_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "destination_age_recipient",
        "recipient_key_id",
        "writer_term",
        "stream_generation_id",
        "immutable_object_pin_receipt",
        "immutable_object_pin_receipt_sha256",
        "context_witness_sequence",
        "context_witness_ledger_entry_sha256",
        "context_witness_ledger_previous_head_sha256",
        "witness_ledger_binding_sha256",
        "delivery_witness_sequence",
        "delivery_witness_ledger_entry_sha256",
        "delivery_witness_ledger_previous_head_sha256",
        "delivery_nonce",
        "expires_at",
        "context_certificate_base64",
        "source_envelope_base64",
        "ir_durable_assertion_base64",
        "roundtrip_attestation_base64",
    }
)
_TERM_FIELDS = frozenset(
    {"writer_holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"}
)


class PhysicalWalV2WitnessRoundtripDeliveryError(ValueError):
    """A one-way V2 Witness mailbox record is malformed, stale, or foreign."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripDeliveryBinding:
    """Public exact pins shared by the four receiver-local mailbox policies."""

    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    context_sha256: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    destination_age_recipient: str
    recipient_key_id: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    stream_generation_id: str
    immutable_object_pin_receipt_sha256: str
    context_certificate_sha256: str
    context_witness_sequence: int
    context_witness_ledger_entry_sha256: str
    context_witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    roundtrip_configuration_sha256: str


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripDeliveryConfig:
    """Default-off receiver policy for exactly one of the four mailboxes."""

    roundtrip_config: _roundtrip.PhysicalWalV2WitnessRoundtripConfig | None = None
    binding: PhysicalWalV2WitnessRoundtripDeliveryBinding | None = None
    receiver_mailbox: str = ""
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_DEFAULT_ENABLED


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2WitnessRoundtripDelivery:
    """A non-authorizing exact mailbox observation, never transport authority."""

    schema: str
    mailbox: str
    delivery_sha256: str
    prior_delivery_sha256: str
    campaign_id: str
    release_sha: str
    context_sha256: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    recipient_key_id: str
    immutable_object_pin_receipt_sha256: str
    delivery_nonce: str
    expires_at: datetime
    canonical_delivery: bytes = field(repr=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Config:
    roundtrip_config: _roundtrip.PhysicalWalV2WitnessRoundtripConfig
    binding: PhysicalWalV2WitnessRoundtripDeliveryBinding
    binding_sha256: str
    receiver_mailbox: str


@dataclass(frozen=True)
class _Artifacts:
    certificate: _roundtrip.VerifiedPhysicalWalV2WitnessContextCertificate
    envelope: _roundtrip.VerifiedPhysicalWalV2WitnessSourceEnvelope | None
    assertion: _roundtrip.VerifiedPhysicalWalV2WitnessIrDurableAssertion | None
    attestation: _roundtrip.VerifiedPhysicalWalV2WitnessRoundtripAttestation | None
    context: dict[str, Any]
    object_pin_receipt: dict[str, Any]
    object_pin_receipt_sha256: str


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripDeliveryError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_JSON_INVALID")


def _parse_canonical(value: object, *, code: str) -> tuple[dict[str, Any], bytes]:
    if type(value) is not bytes or not 1 <= len(value) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(code) from exc
    if type(parsed) is not dict or _canonical(parsed, code=code) != value:
        _fail(code)
    return dict(parsed), value


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(code) from exc
    result = _utc(parsed, code=code)
    if result.isoformat().replace("+00:00", "Z") != value:
        _fail(code)
    return result


def _render_timestamp(value: datetime) -> str:
    return _utc(value, code="V2_WITNESS_ROUNDTRIP_DELIVERY_TIMESTAMP_INVALID").isoformat().replace(
        "+00:00", "Z"
    )


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, *, permit_none: bool, code: str) -> bytes | None:
    if value is None and permit_none:
        return None
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(code) from exc
    if not 1 <= len(result) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_BYTES:
        _fail(code)
    return result


def _recipient_key_id(recipient: str) -> str:
    return "age-recipient-sha256:" + hashlib.sha256(recipient.encode("ascii", "strict")).hexdigest()


def _term(value: object, *, code: str) -> dict[str, Any]:
    item = _exact_mapping(value, fields=_TERM_FIELDS, code=code)
    if (
        item["writer_holder_site"] != "webapp_fi"
        or type(item["writer_epoch"]) is not int
        or item["writer_epoch"] < 1
        or type(item["writer_lease_id"]) is not str
        or not item["writer_lease_id"]
    ):
        _fail(code)
    _sha256(item["witnessed_term_proof_sha256"], code=code)
    return item


def _context_mapping(value: bytes, *, code: str) -> dict[str, Any]:
    item, raw = _parse_canonical(value, code=code)
    if raw != value:
        _fail(code)
    required = {
        "schema",
        "version",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "object_storage_namespace",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "destination_age_recipient",
        "writer_term",
        "transport_plane",
        "direct_webapp_transport",
        "stream_generation_id",
        "canonical_manifest_sha256",
        "manifest_id",
        "handoff_receipt_id",
        "handoff_receipt_nonce",
        "lineage_sha256",
        "baseline_generation_id",
        "target_lsn",
        "base_backup_scope_sha256",
        "blob_frontier_scope_sha256",
        "blob_owner_coverage_sha256",
        "blob_coverage_id",
        "blob_coverage_nonce",
        "wal_continuity_scope_sha256",
        "wal_continuity_receipt_id",
        "wal_continuity_receipt_nonce",
        "wal_continuity_selector_set_sha256",
        "object_version_set_sha256",
        "coverage_scope_sha256",
        "object_count",
    }
    if not required.issubset(item):
        _fail(code)
    if (
        item["schema"] != "gold-trade-physical-wal-v2-remote-ack-context-v2"
        or item["version"] != 2
        or item["source_site"] != "webapp_fi"
        or item["destination_site"] != "webapp_ir"
        or type(item["campaign_id"]) is not str
        or _CAMPAIGN_RE.fullmatch(item["campaign_id"]) is None
        or type(item["release_sha"]) is not str
        or _RELEASE_RE.fullmatch(item["release_sha"]) is None
        or item["transport_plane"] != "private-versioned-object-storage-witness-mediated-v1"
        or item["direct_webapp_transport"] != "forbidden"
        or type(item["destination_age_recipient"]) is not str
        or not item["destination_age_recipient"].isascii()
        or not item["destination_age_recipient"]
        or type(item["object_count"]) is not int
        or not 1 <= item["object_count"] <= 1_000_000
        or type(item["target_lsn"]) is not str
        or _LSN_RE.fullmatch(item["target_lsn"]) is None
    ):
        _fail(code)
    _term(item["writer_term"], code=code)
    for key in (
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "canonical_manifest_sha256",
        "lineage_sha256",
        "base_backup_scope_sha256",
        "blob_frontier_scope_sha256",
        "blob_owner_coverage_sha256",
        "wal_continuity_scope_sha256",
        "wal_continuity_selector_set_sha256",
        "object_version_set_sha256",
        "coverage_scope_sha256",
    ):
        _sha256(item[key], code=code)
    for key in (
        "manifest_id",
        "handoff_receipt_id",
        "baseline_generation_id",
        "blob_coverage_id",
        "wal_continuity_receipt_id",
    ):
        _identifier(item[key], code=code)
    for key in (
        "handoff_receipt_nonce",
        "blob_coverage_nonce",
        "wal_continuity_receipt_nonce",
    ):
        _nonce(item[key], code=code)
    if type(item["object_storage_namespace"]) is not str or not item["object_storage_namespace"]:
        _fail(code)
    if type(item["stream_generation_id"]) is not str or not item["stream_generation_id"]:
        _fail(code)
    return item


def _object_pin_receipt(context: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_OBJECT_PIN_RECEIPT_SCHEMA,
        "campaign_id": context["campaign_id"],
        "release_sha": context["release_sha"],
        "object_storage_namespace": context["object_storage_namespace"],
        "destination_age_recipient": context["destination_age_recipient"],
        "canonical_manifest_sha256": context["canonical_manifest_sha256"],
        "manifest_id": context["manifest_id"],
        "handoff_receipt_id": context["handoff_receipt_id"],
        "handoff_receipt_nonce": context["handoff_receipt_nonce"],
        "lineage_sha256": context["lineage_sha256"],
        "baseline_generation_id": context["baseline_generation_id"],
        "target_lsn": context["target_lsn"],
        "base_backup_scope_sha256": context["base_backup_scope_sha256"],
        "blob_frontier_scope_sha256": context["blob_frontier_scope_sha256"],
        "blob_owner_coverage_sha256": context["blob_owner_coverage_sha256"],
        "blob_coverage_id": context["blob_coverage_id"],
        "blob_coverage_nonce": context["blob_coverage_nonce"],
        "wal_continuity_scope_sha256": context["wal_continuity_scope_sha256"],
        "wal_continuity_receipt_id": context["wal_continuity_receipt_id"],
        "wal_continuity_receipt_nonce": context["wal_continuity_receipt_nonce"],
        "wal_continuity_selector_set_sha256": context["wal_continuity_selector_set_sha256"],
        "object_version_set_sha256": context["object_version_set_sha256"],
        "coverage_scope_sha256": context["coverage_scope_sha256"],
        "object_count": context["object_count"],
    }
    _exact_mapping(
        result,
        fields=_PIN_RECEIPT_FIELDS,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_OBJECT_PIN_RECEIPT_INVALID",
    )
    return result


def _receipt_sha256(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical(receipt, code="V2_WITNESS_ROUNDTRIP_DELIVERY_OBJECT_PIN_RECEIPT_INVALID")
    ).hexdigest()


def _binding_mapping(value: PhysicalWalV2WitnessRoundtripDeliveryBinding) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_BINDING_SCHEMA,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "context_sha256": value.context_sha256,
        "route_commitment_sha256": value.route_commitment_sha256,
        "four_role_binding_sha256": value.four_role_binding_sha256,
        "destination_age_recipient": value.destination_age_recipient,
        "recipient_key_id": value.recipient_key_id,
        "writer_term": {
            "writer_holder_site": value.writer_holder_site,
            "writer_epoch": value.writer_epoch,
            "writer_lease_id": value.writer_lease_id,
            "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
        },
        "stream_generation_id": value.stream_generation_id,
        "immutable_object_pin_receipt_sha256": value.immutable_object_pin_receipt_sha256,
        "context_certificate_sha256": value.context_certificate_sha256,
        "context_witness_sequence": value.context_witness_sequence,
        "context_witness_ledger_entry_sha256": value.context_witness_ledger_entry_sha256,
        "context_witness_ledger_previous_head_sha256": value.context_witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": value.witness_ledger_binding_sha256,
        "roundtrip_configuration_sha256": value.roundtrip_configuration_sha256,
    }


def _binding(value: object, *, roundtrip_facts: object) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryBinding, str]:
    if type(value) is not PhysicalWalV2WitnessRoundtripDeliveryBinding:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_BINDING_INVALID")
    binding = value
    mapping = _binding_mapping(binding)
    if (
        type(binding.campaign_id) is not str
        or _CAMPAIGN_RE.fullmatch(binding.campaign_id) is None
        or type(binding.release_sha) is not str
        or _RELEASE_RE.fullmatch(binding.release_sha) is None
        or binding.source_site != "webapp_fi"
        or binding.destination_site != "webapp_ir"
        or type(binding.destination_age_recipient) is not str
        or binding.recipient_key_id != _recipient_key_id(binding.destination_age_recipient)
        or _KEY_ID_RE.fullmatch(binding.recipient_key_id) is None
        or type(binding.writer_epoch) is not int
        or binding.writer_epoch < 1
        or type(binding.writer_lease_id) is not str
        or not binding.writer_lease_id
        or type(binding.stream_generation_id) is not str
        or not binding.stream_generation_id
        or type(binding.context_witness_sequence) is not int
        or binding.context_witness_sequence < 1
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_BINDING_INVALID")
    for key in (
        "context_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "witnessed_term_proof_sha256",
        "immutable_object_pin_receipt_sha256",
        "context_certificate_sha256",
        "context_witness_ledger_entry_sha256",
        "context_witness_ledger_previous_head_sha256",
        "witness_ledger_binding_sha256",
        "roundtrip_configuration_sha256",
    ):
        # The delivery grammar can consume a valid portable stage-1 certificate
        # created by the pure V2 contract, whose first witnessed entry uses the
        # explicit all-zero predecessor sentinel.  The durable Witness ledger
        # rejects that sentinel for its own persisted genesis binding; that
        # stricter storage rule belongs to the ledger, not to this pure receiver
        # grammar.  All later packet checks still require the exact signed pin.
        _sha256(
            getattr(binding, key),
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_BINDING_INVALID",
            permit_zero=(key == "context_witness_ledger_previous_head_sha256"),
        )
    if binding.roundtrip_configuration_sha256 != getattr(roundtrip_facts, "configuration_sha256", None):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_BINDING_CONFIG_MISMATCH")
    digest = hashlib.sha256(
        _canonical(mapping, code="V2_WITNESS_ROUNDTRIP_DELIVERY_BINDING_INVALID")
    ).hexdigest()
    return binding, digest


def _config(value: object, *, mailbox: str) -> _Config:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripDeliveryConfig
        or value.enabled is not True
        or value.receiver_mailbox != mailbox
        or mailbox not in _MAILBOXES
        or type(value.roundtrip_config) is not _roundtrip.PhysicalWalV2WitnessRoundtripConfig
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_CONFIG_INVALID")
    try:
        facts = _roundtrip._config(value.roundtrip_config)
    except (AttributeError, TypeError, ValueError, _roundtrip.PhysicalWalV2WitnessRoundtripError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_CONFIG_INVALID"
        ) from exc
    binding, digest = _binding(value.binding, roundtrip_facts=facts)
    return _Config(
        roundtrip_config=value.roundtrip_config,
        binding=binding,
        binding_sha256=digest,
        receiver_mailbox=mailbox,
    )


def build_physical_wal_v2_witness_roundtrip_delivery_binding(
    *,
    context_certificate: bytes,
    roundtrip_config: _roundtrip.PhysicalWalV2WitnessRoundtripConfig,
    now: datetime,
) -> PhysicalWalV2WitnessRoundtripDeliveryBinding:
    """Derive the fixed public mailbox pins from one verified stage-1 certificate."""

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_DELIVERY_CLOCK_INVALID")
    try:
        facts = _roundtrip._config(roundtrip_config)
        certificate = _roundtrip.verify_physical_wal_v2_witness_context_certificate(
            context_certificate,
            config=roundtrip_config,
            now=observed,
        )
    except (AttributeError, TypeError, ValueError, _roundtrip.PhysicalWalV2WitnessRoundtripError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_CONTEXT_CERTIFICATE_INVALID"
        ) from exc
    context = _context_mapping(
        certificate.canonical_context,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_CONTEXT_INVALID",
    )
    receipt = _object_pin_receipt(context)
    term = _term(context["writer_term"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_CONTEXT_INVALID")
    result = PhysicalWalV2WitnessRoundtripDeliveryBinding(
        campaign_id=context["campaign_id"],
        release_sha=context["release_sha"],
        source_site=context["source_site"],
        destination_site=context["destination_site"],
        context_sha256=certificate.context_sha256,
        route_commitment_sha256=context["route_commitment_sha256"],
        four_role_binding_sha256=context["four_role_binding_sha256"],
        destination_age_recipient=context["destination_age_recipient"],
        recipient_key_id=_recipient_key_id(context["destination_age_recipient"]),
        writer_holder_site=term["writer_holder_site"],
        writer_epoch=term["writer_epoch"],
        writer_lease_id=term["writer_lease_id"],
        witnessed_term_proof_sha256=term["witnessed_term_proof_sha256"],
        stream_generation_id=context["stream_generation_id"],
        immutable_object_pin_receipt_sha256=_receipt_sha256(receipt),
        context_certificate_sha256=certificate.certificate_sha256,
        context_witness_sequence=certificate.witness_sequence,
        context_witness_ledger_entry_sha256=certificate.witness_ledger_entry_sha256,
        context_witness_ledger_previous_head_sha256=(
            certificate.witness_ledger_previous_head_sha256
        ),
        witness_ledger_binding_sha256=certificate.witness_ledger_binding_sha256,
        roundtrip_configuration_sha256=facts.configuration_sha256,
    )
    _binding(result, roundtrip_facts=facts)
    return result


def _require_binding(
    *,
    config: _Config,
    certificate: _roundtrip.VerifiedPhysicalWalV2WitnessContextCertificate,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    context = _context_mapping(
        certificate.canonical_context,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_CONTEXT_INVALID",
    )
    receipt = _object_pin_receipt(context)
    receipt_sha = _receipt_sha256(receipt)
    term = _term(context["writer_term"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_CONTEXT_INVALID")
    binding = config.binding
    expected = (
        context["campaign_id"],
        context["release_sha"],
        context["source_site"],
        context["destination_site"],
        certificate.context_sha256,
        context["route_commitment_sha256"],
        context["four_role_binding_sha256"],
        context["destination_age_recipient"],
        _recipient_key_id(context["destination_age_recipient"]),
        term["writer_holder_site"],
        term["writer_epoch"],
        term["writer_lease_id"],
        term["witnessed_term_proof_sha256"],
        context["stream_generation_id"],
        receipt_sha,
        certificate.certificate_sha256,
        certificate.witness_sequence,
        certificate.witness_ledger_entry_sha256,
        certificate.witness_ledger_previous_head_sha256,
        certificate.witness_ledger_binding_sha256,
    )
    actual = (
        binding.campaign_id,
        binding.release_sha,
        binding.source_site,
        binding.destination_site,
        binding.context_sha256,
        binding.route_commitment_sha256,
        binding.four_role_binding_sha256,
        binding.destination_age_recipient,
        binding.recipient_key_id,
        binding.writer_holder_site,
        binding.writer_epoch,
        binding.writer_lease_id,
        binding.witnessed_term_proof_sha256,
        binding.stream_generation_id,
        binding.immutable_object_pin_receipt_sha256,
        binding.context_certificate_sha256,
        binding.context_witness_sequence,
        binding.context_witness_ledger_entry_sha256,
        binding.context_witness_ledger_previous_head_sha256,
        binding.witness_ledger_binding_sha256,
    )
    if expected != actual:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_BINDING_CROSS_PIN_MISMATCH")
    return context, receipt, receipt_sha


def _verify_certificate_envelope(
    *,
    certificate_raw: bytes,
    envelope_raw: bytes,
    config: _Config,
    now: datetime,
) -> _Artifacts:
    try:
        certificate = _roundtrip.verify_physical_wal_v2_witness_context_certificate(
            certificate_raw,
            config=config.roundtrip_config,
            now=now,
        )
        envelope = _roundtrip.verify_physical_wal_v2_witness_source_envelope(
            envelope_raw,
            config=config.roundtrip_config,
            now=now,
        )
    except _roundtrip.PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_SOURCE_ARTIFACT_INVALID"
        ) from exc
    if (
        certificate.canonical_certificate != certificate_raw
        or envelope.canonical_envelope != envelope_raw
        or envelope.canonical_context_certificate != certificate_raw
        or envelope.context_certificate_sha256 != certificate.certificate_sha256
        or envelope.context_sha256 != certificate.context_sha256
        or envelope.expires_at > certificate.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_SOURCE_ARTIFACT_CROSS_PIN_MISMATCH")
    context, receipt, receipt_sha = _require_binding(config=config, certificate=certificate)
    return _Artifacts(
        certificate=certificate,
        envelope=envelope,
        assertion=None,
        attestation=None,
        context=context,
        object_pin_receipt=receipt,
        object_pin_receipt_sha256=receipt_sha,
    )


def _verify_assertion(
    *, assertion_raw: bytes, config: _Config, now: datetime
) -> _Artifacts:
    try:
        assertion = _roundtrip.verify_physical_wal_v2_witness_ir_durable_assertion(
            assertion_raw,
            config=config.roundtrip_config,
            now=now,
        )
        envelope = _roundtrip.verify_physical_wal_v2_witness_source_envelope(
            assertion.canonical_source_envelope,
            config=config.roundtrip_config,
            now=now,
        )
        certificate = _roundtrip.verify_physical_wal_v2_witness_context_certificate(
            envelope.canonical_context_certificate,
            config=config.roundtrip_config,
            now=now,
        )
    except _roundtrip.PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_ASSERTION_INVALID"
        ) from exc
    if (
        assertion.canonical_assertion != assertion_raw
        or assertion.canonical_source_envelope != envelope.canonical_envelope
        or envelope.canonical_context_certificate != certificate.canonical_certificate
        or assertion.context_sha256 != certificate.context_sha256
        or assertion.source_envelope_sha256 != envelope.envelope_sha256
        or assertion.expires_at > envelope.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_ASSERTION_CROSS_PIN_MISMATCH")
    context, receipt, receipt_sha = _require_binding(config=config, certificate=certificate)
    return _Artifacts(
        certificate=certificate,
        envelope=envelope,
        assertion=assertion,
        attestation=None,
        context=context,
        object_pin_receipt=receipt,
        object_pin_receipt_sha256=receipt_sha,
    )


def _verify_attestation(
    *, attestation_raw: bytes, config: _Config, now: datetime
) -> _Artifacts:
    try:
        attestation = _roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            attestation_raw,
            config=config.roundtrip_config,
            now=now,
        )
    except _roundtrip.PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_ATTESTATION_INVALID"
        ) from exc
    nested = _verify_assertion(
        assertion_raw=attestation.canonical_ir_durable_assertion,
        config=config,
        now=now,
    )
    assertion = nested.assertion
    assert assertion is not None
    if (
        attestation.canonical_attestation != attestation_raw
        or attestation.context_certificate_sha256 != nested.certificate.certificate_sha256
        or attestation.context_sha256 != nested.certificate.context_sha256
        or attestation.ir_durable_assertion_sha256 != assertion.assertion_sha256
        or attestation.source_envelope_sha256 != assertion.source_envelope_sha256
        or attestation.witness_ledger_binding_sha256
        != nested.certificate.witness_ledger_binding_sha256
        or attestation.witness_sequence <= nested.certificate.witness_sequence
        or attestation.expires_at > assertion.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_ATTESTATION_CROSS_PIN_MISMATCH")
    return _Artifacts(
        certificate=nested.certificate,
        envelope=nested.envelope,
        assertion=assertion,
        attestation=attestation,
        context=nested.context,
        object_pin_receipt=nested.object_pin_receipt,
        object_pin_receipt_sha256=nested.object_pin_receipt_sha256,
    )


def _role_values(mailbox: str) -> tuple[str, str, str, str]:
    try:
        return _ROLE_MATRIX[mailbox]
    except KeyError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_MAILBOX_INVALID"
        ) from exc


def _packet(
    *, mailbox: str, config: _Config, artifacts: _Artifacts
) -> bytes:
    sender_site, recipient_site, sender_role, recipient_role = _role_values(mailbox)
    certificate = artifacts.certificate
    envelope = artifacts.envelope
    assertion = artifacts.assertion
    attestation = artifacts.attestation
    if mailbox in {_FI_TO_WITNESS, _WITNESS_TO_IR}:
        if envelope is None or assertion is not None or attestation is not None:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_ARTIFACT_SHAPE_INVALID")
        nonce = envelope.outbox_nonce
        expires_at = envelope.expires_at
        current_sequence = certificate.witness_sequence
        current_entry = certificate.witness_ledger_entry_sha256
        current_previous = certificate.witness_ledger_previous_head_sha256
        prior = (
            certificate.certificate_sha256
            if mailbox == _FI_TO_WITNESS
            else hashlib.sha256(
                _packet(
                    mailbox=_FI_TO_WITNESS,
                    config=config,
                    artifacts=artifacts,
                )
            ).hexdigest()
        )
        cert_raw = certificate.canonical_certificate
        envelope_raw = envelope.canonical_envelope
        assertion_raw = None
        attestation_raw = None
    elif mailbox == _IR_TO_WITNESS:
        if envelope is None or assertion is None or attestation is not None:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_ARTIFACT_SHAPE_INVALID")
        nonce = assertion.assertion_nonce
        expires_at = assertion.expires_at
        current_sequence = certificate.witness_sequence
        current_entry = certificate.witness_ledger_entry_sha256
        current_previous = certificate.witness_ledger_previous_head_sha256
        prior = hashlib.sha256(
            _packet(mailbox=_WITNESS_TO_IR, config=config, artifacts=_Artifacts(
                certificate=certificate,
                envelope=envelope,
                assertion=None,
                attestation=None,
                context=artifacts.context,
                object_pin_receipt=artifacts.object_pin_receipt,
                object_pin_receipt_sha256=artifacts.object_pin_receipt_sha256,
            ))
        ).hexdigest()
        cert_raw = None
        envelope_raw = None
        assertion_raw = assertion.canonical_assertion
        attestation_raw = None
    else:
        if envelope is None or assertion is None or attestation is None:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_ARTIFACT_SHAPE_INVALID")
        nonce = attestation.attestation_nonce
        expires_at = attestation.expires_at
        current_sequence = attestation.witness_sequence
        current_entry = attestation.witness_ledger_entry_sha256
        current_previous = attestation.witness_ledger_previous_head_sha256
        prior = hashlib.sha256(
            _packet(mailbox=_IR_TO_WITNESS, config=config, artifacts=_Artifacts(
                certificate=certificate,
                envelope=envelope,
                assertion=assertion,
                attestation=None,
                context=artifacts.context,
                object_pin_receipt=artifacts.object_pin_receipt,
                object_pin_receipt_sha256=artifacts.object_pin_receipt_sha256,
            ))
        ).hexdigest()
        cert_raw = None
        envelope_raw = None
        assertion_raw = None
        attestation_raw = attestation.canonical_attestation
    term = _term(artifacts.context["writer_term"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_CONTEXT_INVALID")
    payload: dict[str, Any] = {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_SCHEMA,
        "version": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_VERSION,
        "mailbox": mailbox,
        "sender_site": sender_site,
        "recipient_site": recipient_site,
        "sender_role": sender_role,
        "recipient_role": recipient_role,
        "delivery_binding_sha256": config.binding_sha256,
        "prior_delivery_sha256": prior,
        "campaign_id": artifacts.context["campaign_id"],
        "release_sha": artifacts.context["release_sha"],
        "context_sha256": certificate.context_sha256,
        "route_commitment_sha256": artifacts.context["route_commitment_sha256"],
        "four_role_binding_sha256": artifacts.context["four_role_binding_sha256"],
        "destination_age_recipient": artifacts.context["destination_age_recipient"],
        "recipient_key_id": _recipient_key_id(artifacts.context["destination_age_recipient"]),
        "writer_term": term,
        "stream_generation_id": artifacts.context["stream_generation_id"],
        "immutable_object_pin_receipt": artifacts.object_pin_receipt,
        "immutable_object_pin_receipt_sha256": artifacts.object_pin_receipt_sha256,
        "context_witness_sequence": certificate.witness_sequence,
        "context_witness_ledger_entry_sha256": certificate.witness_ledger_entry_sha256,
        "context_witness_ledger_previous_head_sha256": (
            certificate.witness_ledger_previous_head_sha256
        ),
        "witness_ledger_binding_sha256": certificate.witness_ledger_binding_sha256,
        "delivery_witness_sequence": current_sequence,
        "delivery_witness_ledger_entry_sha256": current_entry,
        "delivery_witness_ledger_previous_head_sha256": current_previous,
        "delivery_nonce": nonce,
        "expires_at": _render_timestamp(expires_at),
        "context_certificate_base64": None if cert_raw is None else _b64(cert_raw),
        "source_envelope_base64": None if envelope_raw is None else _b64(envelope_raw),
        "ir_durable_assertion_base64": None if assertion_raw is None else _b64(assertion_raw),
        "roundtrip_attestation_base64": None if attestation_raw is None else _b64(attestation_raw),
    }
    _exact_mapping(payload, fields=_DELIVERY_FIELDS, code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_INVALID")
    raw = _canonical(payload, code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_INVALID")
    if len(raw) > MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_BYTES:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_TOO_LARGE")
    return raw


def _artifacts_for_packet(
    *, mailbox: str, item: Mapping[str, Any], config: _Config, now: datetime
) -> _Artifacts:
    certificate_raw = _unb64(
        item["context_certificate_base64"],
        permit_none=True,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_ARTIFACT_INVALID",
    )
    envelope_raw = _unb64(
        item["source_envelope_base64"],
        permit_none=True,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_ARTIFACT_INVALID",
    )
    assertion_raw = _unb64(
        item["ir_durable_assertion_base64"],
        permit_none=True,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_ARTIFACT_INVALID",
    )
    attestation_raw = _unb64(
        item["roundtrip_attestation_base64"],
        permit_none=True,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_ARTIFACT_INVALID",
    )
    if mailbox in {_FI_TO_WITNESS, _WITNESS_TO_IR}:
        if certificate_raw is None or envelope_raw is None or assertion_raw is not None or attestation_raw is not None:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_ARTIFACT_SHAPE_INVALID")
        return _verify_certificate_envelope(
            certificate_raw=certificate_raw,
            envelope_raw=envelope_raw,
            config=config,
            now=now,
        )
    if mailbox == _IR_TO_WITNESS:
        if certificate_raw is not None or envelope_raw is not None or assertion_raw is None or attestation_raw is not None:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_ARTIFACT_SHAPE_INVALID")
        return _verify_assertion(assertion_raw=assertion_raw, config=config, now=now)
    if certificate_raw is not None or envelope_raw is not None or assertion_raw is not None or attestation_raw is None:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_ARTIFACT_SHAPE_INVALID")
    return _verify_attestation(attestation_raw=attestation_raw, config=config, now=now)


def _verify_packet(
    *, value: bytes, config_value: PhysicalWalV2WitnessRoundtripDeliveryConfig, mailbox: str, now: datetime
) -> VerifiedPhysicalWalV2WitnessRoundtripDelivery:
    config = _config(config_value, mailbox=mailbox)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_DELIVERY_CLOCK_INVALID")
    item, raw = _parse_canonical(value, code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_INVALID")
    item = _exact_mapping(item, fields=_DELIVERY_FIELDS, code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_INVALID")
    sender_site, recipient_site, sender_role, recipient_role = _role_values(mailbox)
    if (
        item["schema"] != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_SCHEMA
        or item["version"] != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_VERSION
        or item["mailbox"] != mailbox
        or (item["sender_site"], item["recipient_site"], item["sender_role"], item["recipient_role"])
        != (sender_site, recipient_site, sender_role, recipient_role)
        or _sha256(item["delivery_binding_sha256"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_INVALID")
        != config.binding_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_ROUTE_INVALID")
    artifacts = _artifacts_for_packet(mailbox=mailbox, item=item, config=config, now=observed)
    expected = _packet(mailbox=mailbox, config=config, artifacts=artifacts)
    if raw != expected:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_CROSS_PIN_MISMATCH")
    expected_item, _ = _parse_canonical(expected, code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_INVALID")
    expiry = _timestamp(expected_item["expires_at"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_INVALID")
    if expiry <= observed:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_PACKET_EXPIRED")
    return VerifiedPhysicalWalV2WitnessRoundtripDelivery(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_SCHEMA,
        mailbox=mailbox,
        delivery_sha256=hashlib.sha256(raw).hexdigest(),
        prior_delivery_sha256=expected_item["prior_delivery_sha256"],
        campaign_id=expected_item["campaign_id"],
        release_sha=expected_item["release_sha"],
        context_sha256=expected_item["context_sha256"],
        route_commitment_sha256=expected_item["route_commitment_sha256"],
        four_role_binding_sha256=expected_item["four_role_binding_sha256"],
        recipient_key_id=expected_item["recipient_key_id"],
        immutable_object_pin_receipt_sha256=expected_item[
            "immutable_object_pin_receipt_sha256"
        ],
        delivery_nonce=expected_item["delivery_nonce"],
        expires_at=expiry,
        canonical_delivery=raw,
    )


def _build_packet(
    *, mailbox: str, config_value: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
    context_certificate: bytes | None = None, source_envelope: bytes | None = None,
    ir_durable_assertion: bytes | None = None, roundtrip_attestation: bytes | None = None,
) -> bytes:
    config = _config(config_value, mailbox=mailbox)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_DELIVERY_CLOCK_INVALID")
    if mailbox in {_FI_TO_WITNESS, _WITNESS_TO_IR}:
        if type(context_certificate) is not bytes or type(source_envelope) is not bytes:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_BUILD_INPUT_INVALID")
        artifacts = _verify_certificate_envelope(
            certificate_raw=context_certificate,
            envelope_raw=source_envelope,
            config=config,
            now=observed,
        )
    elif mailbox == _IR_TO_WITNESS:
        if type(ir_durable_assertion) is not bytes:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_BUILD_INPUT_INVALID")
        artifacts = _verify_assertion(assertion_raw=ir_durable_assertion, config=config, now=observed)
    else:
        if type(roundtrip_attestation) is not bytes:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_BUILD_INPUT_INVALID")
        artifacts = _verify_attestation(
            attestation_raw=roundtrip_attestation,
            config=config,
            now=observed,
        )
    return _packet(mailbox=mailbox, config=config, artifacts=artifacts)


def build_physical_wal_v2_witness_fi_to_witness_delivery(
    *, context_certificate: bytes, source_envelope: bytes,
    config: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
) -> bytes:
    return _build_packet(
        mailbox=_FI_TO_WITNESS, config_value=config, now=now,
        context_certificate=context_certificate, source_envelope=source_envelope,
    )


def verify_physical_wal_v2_witness_fi_to_witness_delivery(
    delivery: bytes, *, config: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripDelivery:
    return _verify_packet(value=delivery, config_value=config, mailbox=_FI_TO_WITNESS, now=now)


def build_physical_wal_v2_witness_witness_to_ir_delivery(
    *, context_certificate: bytes, source_envelope: bytes,
    config: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
) -> bytes:
    return _build_packet(
        mailbox=_WITNESS_TO_IR, config_value=config, now=now,
        context_certificate=context_certificate, source_envelope=source_envelope,
    )


def verify_physical_wal_v2_witness_witness_to_ir_delivery(
    delivery: bytes, *, config: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripDelivery:
    return _verify_packet(value=delivery, config_value=config, mailbox=_WITNESS_TO_IR, now=now)


def build_physical_wal_v2_witness_ir_to_witness_delivery(
    *, ir_durable_assertion: bytes,
    config: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
) -> bytes:
    return _build_packet(
        mailbox=_IR_TO_WITNESS, config_value=config, now=now,
        ir_durable_assertion=ir_durable_assertion,
    )


def verify_physical_wal_v2_witness_ir_to_witness_delivery(
    delivery: bytes, *, config: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripDelivery:
    return _verify_packet(value=delivery, config_value=config, mailbox=_IR_TO_WITNESS, now=now)


def build_physical_wal_v2_witness_witness_to_fi_delivery(
    *, roundtrip_attestation: bytes,
    config: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
) -> bytes:
    return _build_packet(
        mailbox=_WITNESS_TO_FI, config_value=config, now=now,
        roundtrip_attestation=roundtrip_attestation,
    )


def verify_physical_wal_v2_witness_witness_to_fi_delivery(
    delivery: bytes, *, config: PhysicalWalV2WitnessRoundtripDeliveryConfig, now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripDelivery:
    return _verify_packet(value=delivery, config_value=config, mailbox=_WITNESS_TO_FI, now=now)
