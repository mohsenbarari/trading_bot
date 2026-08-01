"""Pure, default-off deployment manifests for the three-site V2 mailbox plane.

This is a renderer only.  It cannot install a service, read a local file,
 open a socket, resolve an endpoint, contact Object Storage, or handle
 credential material.  Its output is a non-secret per-site service manifest whose
activation state remains explicitly default-off.

The complete fixed topology is intentionally encoded here rather than being
provided by a caller: WA-FI owns its source outbox and final acknowledgement
inbox, WA-IR owns its standby acknowledgement inbox and durable acknowledgement
outbox, and Witness owns the four remaining ingress/egress roles.  Each
artifact contains only that site's paths and a public reference to the signed
full-bundle evidence; it has no peer path, peer credential, peer root, route
endpoint, or generic role-selection surface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEPLOYMENT_PLAN_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEPLOYMENT_PLAN_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SERVICE_MANIFEST_SCHEMA",
    "PhysicalWalV2WitnessRoundtripDeploymentPlanConfig",
    "PhysicalWalV2WitnessRoundtripDeploymentPlanError",
    "PhysicalWalV2WitnessRoundtripLocalServicePlan",
    "PhysicalWalV2WitnessRoundtripPublicFullBundleReference",
    "PhysicalWalV2WitnessRoundtripRenderedDeploymentPlan",
    "PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig",
    "PhysicalWalV2WitnessRoundtripServiceManifest",
    "PhysicalWalV2WitnessRoundtripWaFiLocalServiceConfig",
    "PhysicalWalV2WitnessRoundtripWaIrLocalServiceConfig",
    "PhysicalWalV2WitnessRoundtripWitnessLocalServiceConfig",
    "parse_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest",
    "parse_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest",
    "parse_physical_wal_v2_witness_roundtrip_witness_service_manifest",
    "require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission",
    "require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_admission",
    "require_physical_wal_v2_witness_roundtrip_witness_service_manifest_admission",
    "render_physical_wal_v2_witness_roundtrip_deployment_plan",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEPLOYMENT_PLAN_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-deployment-plan-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SERVICE_MANIFEST_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-service-manifest-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEPLOYMENT_PLAN_DEFAULT_ENABLED = False

_VERSION = 1
_DEFAULT_OFF_ACTIVATION = "default-off-no-install-network-or-start-authority-v1"
_WA_FI = "wa-fi"
_WA_IR = "wa-ir"
_WITNESS = "witness"
_CONFIG_ROOT = "/etc/trading-bot/v2"
_ZERO_SHA256 = "0" * 64
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "site",
        "activation",
        "plan_id",
        "release_sha",
        "full_bundle_reference",
        "services",
        "render_lock_sha256",
    }
)
_UNSIGNED_MANIFEST_FIELDS = _MANIFEST_FIELDS - {"render_lock_sha256"}
_FULL_BUNDLE_FIELDS = frozenset(
    {
        "bundle_id",
        "release_sha",
        "full_bundle_attestation_sha256",
        "deployment_binding_sha256",
        "deployment_authority_public_key_sha256",
        "roundtrip_configuration_sha256",
    }
)
_SERVICE_FIELDS = frozenset(
    {
        "service_id",
        "local_role",
        "dispatcher_entrypoint",
        "local_config_path",
        "local_credential_path",
    }
)


class PhysicalWalV2WitnessRoundtripDeploymentPlanError(ValueError):
    """A non-secret V2 deployment manifest is malformed or unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripPublicFullBundleReference:
    """Public pins for separately verified signed full-bundle evidence."""

    bundle_id: str = ""
    release_sha: str = ""
    full_bundle_attestation_sha256: str = ""
    deployment_binding_sha256: str = ""
    deployment_authority_public_key_sha256: str = ""
    roundtrip_configuration_sha256: str = ""


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripWaFiLocalServiceConfig:
    """Only WA-FI-local path references for its two exact mailbox roles."""

    fi_writer_source_outbox_config_path: str = ""
    fi_writer_source_outbox_credential_path: str = ""
    fi_writer_ack_inbox_config_path: str = ""
    fi_writer_ack_inbox_credential_path: str = ""


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripWaIrLocalServiceConfig:
    """Only WA-IR-local path references for its two exact mailbox roles."""

    ir_standby_ack_inbox_config_path: str = ""
    ir_standby_ack_inbox_credential_path: str = ""
    ir_durable_ack_outbox_config_path: str = ""
    ir_durable_ack_outbox_credential_path: str = ""


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripWitnessLocalServiceConfig:
    """Only Witness-local path references for its four exact mailbox roles."""

    witness_fi_ingress_config_path: str = ""
    witness_fi_ingress_credential_path: str = ""
    witness_ir_egress_config_path: str = ""
    witness_ir_egress_credential_path: str = ""
    witness_ir_ingress_config_path: str = ""
    witness_ir_ingress_credential_path: str = ""
    witness_fi_egress_config_path: str = ""
    witness_fi_egress_credential_path: str = ""


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripDeploymentPlanConfig:
    """Default-off input containing three separate local site descriptions."""

    plan_id: str = ""
    release_sha: str = ""
    full_bundle_reference: PhysicalWalV2WitnessRoundtripPublicFullBundleReference | None = field(
        default=None,
        repr=False,
    )
    wa_fi: PhysicalWalV2WitnessRoundtripWaFiLocalServiceConfig | None = field(
        default=None,
        repr=False,
    )
    wa_ir: PhysicalWalV2WitnessRoundtripWaIrLocalServiceConfig | None = field(
        default=None,
        repr=False,
    )
    witness: PhysicalWalV2WitnessRoundtripWitnessLocalServiceConfig | None = field(
        default=None,
        repr=False,
    )
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEPLOYMENT_PLAN_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig:
    """Root-pinned public expectations for one already-rendered local artifact.

    This is deliberately a local verification input, not a signing key and not
    a peer deployment description.  The caller must source it from its
    root-controlled local release policy before passing an untrusted manifest.
    """

    expected_plan_id: str = ""
    expected_release_sha: str = ""
    expected_full_bundle_reference: PhysicalWalV2WitnessRoundtripPublicFullBundleReference | None = field(
        default=None,
        repr=False,
    )
    expected_manifest_sha256: str = ""
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEPLOYMENT_PLAN_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripLocalServicePlan:
    """One named non-secret local service plan, never a process capability."""

    service_id: str
    local_role: str
    dispatcher_entrypoint: str
    local_config_path: str
    local_credential_path: str


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripServiceManifest:
    """Parsed, non-authorizing service manifest for one fixed site."""

    schema: str
    site: str
    activation: str
    plan_id: str
    release_sha: str
    full_bundle_reference: PhysicalWalV2WitnessRoundtripPublicFullBundleReference
    services: tuple[PhysicalWalV2WitnessRoundtripLocalServicePlan, ...]
    render_lock_sha256: str
    canonical_manifest: bytes = field(repr=False)


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripRenderedDeploymentPlan:
    """Three separately distributable default-off non-secret artifacts."""

    schema: str
    plan_id: str
    release_sha: str
    full_bundle_reference: PhysicalWalV2WitnessRoundtripPublicFullBundleReference
    wa_fi_service_manifest: bytes = field(repr=False)
    wa_ir_service_manifest: bytes = field(repr=False)
    witness_service_manifest: bytes = field(repr=False)
    wa_fi_manifest_sha256: str
    wa_ir_manifest_sha256: str
    witness_manifest_sha256: str


@dataclass(frozen=True)
class _ServiceSpec:
    service_id: str
    local_role: str
    dispatcher_entrypoint: str


_WA_FI_SERVICES = (
    _ServiceSpec(
        "physical-wal-v2-fi-writer-source-outbox",
        "fi-writer-source-outbox",
        "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_dispatcher",
    ),
    _ServiceSpec(
        "physical-wal-v2-fi-writer-ack-inbox",
        "fi-writer-ack-inbox",
        "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_ack_ingress_dispatcher",
    ),
)
_WA_IR_SERVICES = (
    _ServiceSpec(
        "physical-wal-v2-ir-standby-ack-inbox",
        "ir-standby-ack-inbox",
        "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_standby_ingress_dispatcher",
    ),
    _ServiceSpec(
        "physical-wal-v2-ir-durable-ack-outbox",
        "ir-durable-ack-outbox",
        "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_to_witness_publisher_dispatcher",
    ),
)
_WITNESS_SERVICES = (
    _ServiceSpec(
        "physical-wal-v2-witness-fi-ingress",
        "witness-fi-ingress",
        "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_dispatcher",
    ),
    _ServiceSpec(
        "physical-wal-v2-witness-ir-egress",
        "witness-ir-egress",
        "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_ir_publisher_dispatcher",
    ),
    _ServiceSpec(
        "physical-wal-v2-witness-ir-ingress",
        "witness-ir-ingress",
        "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_ir_ingress_dispatcher",
    ),
    _ServiceSpec(
        "physical-wal-v2-witness-fi-egress",
        "witness-fi-egress",
        "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_fi_publisher_dispatcher",
    ),
)


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripDeploymentPlanError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeploymentPlanError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _release(value: object, *, code: str) -> str:
    if type(value) is not str or _RELEASE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INVALID")


def _local_path(value: object, *, site: str, local_role: str, kind: str) -> str:
    expected = f"{_CONFIG_ROOT}/{site}/{kind}/{local_role}.json"
    if type(value) is not str or value != expected:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_LOCAL_PATH_INVALID")
    return expected


def _full_bundle_reference(
    value: object,
) -> PhysicalWalV2WitnessRoundtripPublicFullBundleReference:
    if type(value) is not PhysicalWalV2WitnessRoundtripPublicFullBundleReference:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_FULL_BUNDLE_REFERENCE_INVALID")
    return PhysicalWalV2WitnessRoundtripPublicFullBundleReference(
        bundle_id=_identifier(
            value.bundle_id,
            code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_FULL_BUNDLE_REFERENCE_INVALID",
        ),
        release_sha=_release(
            value.release_sha,
            code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_FULL_BUNDLE_REFERENCE_INVALID",
        ),
        full_bundle_attestation_sha256=_sha256(
            value.full_bundle_attestation_sha256,
            code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_FULL_BUNDLE_REFERENCE_INVALID",
        ),
        deployment_binding_sha256=_sha256(
            value.deployment_binding_sha256,
            code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_FULL_BUNDLE_REFERENCE_INVALID",
        ),
        deployment_authority_public_key_sha256=_sha256(
            value.deployment_authority_public_key_sha256,
            code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_FULL_BUNDLE_REFERENCE_INVALID",
        ),
        roundtrip_configuration_sha256=_sha256(
            value.roundtrip_configuration_sha256,
            code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_FULL_BUNDLE_REFERENCE_INVALID",
        ),
    )


def _full_bundle_mapping(
    value: PhysicalWalV2WitnessRoundtripPublicFullBundleReference,
) -> dict[str, str]:
    return {
        "bundle_id": value.bundle_id,
        "release_sha": value.release_sha,
        "full_bundle_attestation_sha256": value.full_bundle_attestation_sha256,
        "deployment_binding_sha256": value.deployment_binding_sha256,
        "deployment_authority_public_key_sha256": value.deployment_authority_public_key_sha256,
        "roundtrip_configuration_sha256": value.roundtrip_configuration_sha256,
    }


def _service_plan(
    *,
    site: str,
    spec: _ServiceSpec,
    config_path: object,
    credential_path: object,
) -> PhysicalWalV2WitnessRoundtripLocalServicePlan:
    return PhysicalWalV2WitnessRoundtripLocalServicePlan(
        service_id=spec.service_id,
        local_role=spec.local_role,
        dispatcher_entrypoint=spec.dispatcher_entrypoint,
        local_config_path=_local_path(
            config_path,
            site=site,
            local_role=spec.local_role,
            kind="config",
        ),
        local_credential_path=_local_path(
            credential_path,
            site=site,
            local_role=spec.local_role,
            kind="credentials",
        ),
    )


def _wa_fi_plans(
    value: object,
) -> tuple[PhysicalWalV2WitnessRoundtripLocalServicePlan, ...]:
    if type(value) is not PhysicalWalV2WitnessRoundtripWaFiLocalServiceConfig:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_LOCAL_CONFIG_INVALID")
    return (
        _service_plan(
            site=_WA_FI,
            spec=_WA_FI_SERVICES[0],
            config_path=value.fi_writer_source_outbox_config_path,
            credential_path=value.fi_writer_source_outbox_credential_path,
        ),
        _service_plan(
            site=_WA_FI,
            spec=_WA_FI_SERVICES[1],
            config_path=value.fi_writer_ack_inbox_config_path,
            credential_path=value.fi_writer_ack_inbox_credential_path,
        ),
    )


def _wa_ir_plans(
    value: object,
) -> tuple[PhysicalWalV2WitnessRoundtripLocalServicePlan, ...]:
    if type(value) is not PhysicalWalV2WitnessRoundtripWaIrLocalServiceConfig:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_LOCAL_CONFIG_INVALID")
    return (
        _service_plan(
            site=_WA_IR,
            spec=_WA_IR_SERVICES[0],
            config_path=value.ir_standby_ack_inbox_config_path,
            credential_path=value.ir_standby_ack_inbox_credential_path,
        ),
        _service_plan(
            site=_WA_IR,
            spec=_WA_IR_SERVICES[1],
            config_path=value.ir_durable_ack_outbox_config_path,
            credential_path=value.ir_durable_ack_outbox_credential_path,
        ),
    )


def _witness_plans(
    value: object,
) -> tuple[PhysicalWalV2WitnessRoundtripLocalServicePlan, ...]:
    if type(value) is not PhysicalWalV2WitnessRoundtripWitnessLocalServiceConfig:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_LOCAL_CONFIG_INVALID")
    return (
        _service_plan(
            site=_WITNESS,
            spec=_WITNESS_SERVICES[0],
            config_path=value.witness_fi_ingress_config_path,
            credential_path=value.witness_fi_ingress_credential_path,
        ),
        _service_plan(
            site=_WITNESS,
            spec=_WITNESS_SERVICES[1],
            config_path=value.witness_ir_egress_config_path,
            credential_path=value.witness_ir_egress_credential_path,
        ),
        _service_plan(
            site=_WITNESS,
            spec=_WITNESS_SERVICES[2],
            config_path=value.witness_ir_ingress_config_path,
            credential_path=value.witness_ir_ingress_credential_path,
        ),
        _service_plan(
            site=_WITNESS,
            spec=_WITNESS_SERVICES[3],
            config_path=value.witness_fi_egress_config_path,
            credential_path=value.witness_fi_egress_credential_path,
        ),
    )


def _service_mapping(value: PhysicalWalV2WitnessRoundtripLocalServicePlan) -> dict[str, str]:
    return {
        "service_id": value.service_id,
        "local_role": value.local_role,
        "dispatcher_entrypoint": value.dispatcher_entrypoint,
        "local_config_path": value.local_config_path,
        "local_credential_path": value.local_credential_path,
    }


def _render_site_manifest(
    *,
    site: str,
    plan_id: str,
    release_sha: str,
    full_bundle_reference: PhysicalWalV2WitnessRoundtripPublicFullBundleReference,
    services: tuple[PhysicalWalV2WitnessRoundtripLocalServicePlan, ...],
) -> bytes:
    unsigned = {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SERVICE_MANIFEST_SCHEMA,
        "version": _VERSION,
        "site": site,
        "activation": _DEFAULT_OFF_ACTIVATION,
        "plan_id": plan_id,
        "release_sha": release_sha,
        "full_bundle_reference": _full_bundle_mapping(full_bundle_reference),
        "services": [_service_mapping(service) for service in services],
    }
    return _canonical(
        {
            **unsigned,
            "render_lock_sha256": _sha256_bytes(
                _canonical(unsigned, code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_RENDER_INVALID")
            ),
        },
        code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_RENDER_INVALID",
    )


def _config(
    value: object,
) -> tuple[
    str,
    str,
    PhysicalWalV2WitnessRoundtripPublicFullBundleReference,
    tuple[PhysicalWalV2WitnessRoundtripLocalServicePlan, ...],
    tuple[PhysicalWalV2WitnessRoundtripLocalServicePlan, ...],
    tuple[PhysicalWalV2WitnessRoundtripLocalServicePlan, ...],
]:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripDeploymentPlanConfig
        or value.enabled is not True
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_CONFIG_INVALID")
    plan_id = _identifier(value.plan_id, code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_CONFIG_INVALID")
    release_sha = _release(value.release_sha, code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_CONFIG_INVALID")
    full_bundle_reference = _full_bundle_reference(value.full_bundle_reference)
    if full_bundle_reference.release_sha != release_sha:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_RELEASE_CROSS_PIN_MISMATCH")
    wa_fi = _wa_fi_plans(value.wa_fi)
    wa_ir = _wa_ir_plans(value.wa_ir)
    witness = _witness_plans(value.witness)
    return plan_id, release_sha, full_bundle_reference, wa_fi, wa_ir, witness


def _admission_config(
    value: object,
) -> tuple[str, str, PhysicalWalV2WitnessRoundtripPublicFullBundleReference, str]:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig
        or value.enabled is not True
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_ADMISSION_CONFIG_INVALID")
    expected_plan_id = _identifier(
        value.expected_plan_id,
        code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_ADMISSION_CONFIG_INVALID",
    )
    expected_release_sha = _release(
        value.expected_release_sha,
        code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_ADMISSION_CONFIG_INVALID",
    )
    expected_reference = _full_bundle_reference(value.expected_full_bundle_reference)
    # The root-local release policy and the signed-derived public reference are
    # one inseparable pin.  Without this check a caller could supply a matching
    # manifest hash with an outer release label that differs from the release
    # embedded in the FullBundle reference.
    if expected_reference.release_sha != expected_release_sha:
        _fail(
            "V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_ADMISSION_CONFIG_RELEASE_CROSS_PIN_MISMATCH"
        )
    return (
        expected_plan_id,
        expected_release_sha,
        expected_reference,
        _sha256(
            value.expected_manifest_sha256,
            code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_ADMISSION_CONFIG_INVALID",
        ),
    )


def render_physical_wal_v2_witness_roundtrip_deployment_plan(
    *,
    config: PhysicalWalV2WitnessRoundtripDeploymentPlanConfig,
) -> PhysicalWalV2WitnessRoundtripRenderedDeploymentPlan:
    """Render three fixed default-off local manifests without I/O or secrets."""

    plan_id, release_sha, full_bundle_reference, wa_fi, wa_ir, witness = _config(config)
    wa_fi_manifest = _render_site_manifest(
        site=_WA_FI,
        plan_id=plan_id,
        release_sha=release_sha,
        full_bundle_reference=full_bundle_reference,
        services=wa_fi,
    )
    wa_ir_manifest = _render_site_manifest(
        site=_WA_IR,
        plan_id=plan_id,
        release_sha=release_sha,
        full_bundle_reference=full_bundle_reference,
        services=wa_ir,
    )
    witness_manifest = _render_site_manifest(
        site=_WITNESS,
        plan_id=plan_id,
        release_sha=release_sha,
        full_bundle_reference=full_bundle_reference,
        services=witness,
    )
    return PhysicalWalV2WitnessRoundtripRenderedDeploymentPlan(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEPLOYMENT_PLAN_SCHEMA,
        plan_id=plan_id,
        release_sha=release_sha,
        full_bundle_reference=full_bundle_reference,
        wa_fi_service_manifest=wa_fi_manifest,
        wa_ir_service_manifest=wa_ir_manifest,
        witness_service_manifest=witness_manifest,
        wa_fi_manifest_sha256=_sha256_bytes(wa_fi_manifest),
        wa_ir_manifest_sha256=_sha256_bytes(wa_ir_manifest),
        witness_manifest_sha256=_sha256_bytes(witness_manifest),
    )


def _parse_manifest(value: object, *, site: str, specs: tuple[_ServiceSpec, ...]) -> PhysicalWalV2WitnessRoundtripServiceManifest:
    if type(value) is not bytes or not 1 <= len(value) <= 64 * 1024:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INVALID")
    try:
        item = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeploymentPlanError(
            "V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INVALID"
        ) from exc
    if (
        type(item) is not dict
        or set(item) != _MANIFEST_FIELDS
        or _canonical(item, code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INVALID") != value
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INVALID")
    if (
        item["schema"] != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SERVICE_MANIFEST_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != _VERSION
        or item["site"] != site
        or item["activation"] != _DEFAULT_OFF_ACTIVATION
        or type(item["services"]) is not list
        or len(item["services"]) != len(specs)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_TOPOLOGY_INVALID")
    unsigned = {name: item[name] for name in _UNSIGNED_MANIFEST_FIELDS}
    if _sha256(
        item["render_lock_sha256"],
        code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INTEGRITY_INVALID",
    ) != _sha256_bytes(
        _canonical(unsigned, code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INTEGRITY_INVALID")
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INTEGRITY_INVALID")
    reference_item = item["full_bundle_reference"]
    if type(reference_item) is not dict or set(reference_item) != _FULL_BUNDLE_FIELDS:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_FULL_BUNDLE_REFERENCE_INVALID")
    reference = _full_bundle_reference(
        PhysicalWalV2WitnessRoundtripPublicFullBundleReference(**reference_item)
    )
    plan_id = _identifier(item["plan_id"], code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INVALID")
    release_sha = _release(item["release_sha"], code="V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_INVALID")
    # `release_sha` is duplicated only so that the manifest stays directly
    # inspectable.  It must nevertheless be exactly the signed-derived release
    # in the public FullBundle reference; otherwise a canonicalized raw
    # manifest could relabel an otherwise valid bundle.
    if reference.release_sha != release_sha:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_RELEASE_CROSS_PIN_MISMATCH")
    services: list[PhysicalWalV2WitnessRoundtripLocalServicePlan] = []
    for service_item, spec in zip(item["services"], specs, strict=True):
        if type(service_item) is not dict or set(service_item) != _SERVICE_FIELDS:
            _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_TOPOLOGY_INVALID")
        if (
            service_item["service_id"] != spec.service_id
            or service_item["local_role"] != spec.local_role
            or service_item["dispatcher_entrypoint"] != spec.dispatcher_entrypoint
        ):
            _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_TOPOLOGY_INVALID")
        services.append(
            PhysicalWalV2WitnessRoundtripLocalServicePlan(
                service_id=spec.service_id,
                local_role=spec.local_role,
                dispatcher_entrypoint=spec.dispatcher_entrypoint,
                local_config_path=_local_path(
                    service_item["local_config_path"],
                    site=site,
                    local_role=spec.local_role,
                    kind="config",
                ),
                local_credential_path=_local_path(
                    service_item["local_credential_path"],
                    site=site,
                    local_role=spec.local_role,
                    kind="credentials",
                ),
            )
        )
    return PhysicalWalV2WitnessRoundtripServiceManifest(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SERVICE_MANIFEST_SCHEMA,
        site=site,
        activation=_DEFAULT_OFF_ACTIVATION,
        plan_id=plan_id,
        release_sha=release_sha,
        full_bundle_reference=reference,
        services=tuple(services),
        render_lock_sha256=item["render_lock_sha256"],
        canonical_manifest=value,
    )


def parse_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest(
    manifest: bytes,
) -> PhysicalWalV2WitnessRoundtripServiceManifest:
    """Parse only the fixed WA-FI-local two-role artifact."""

    return _parse_manifest(manifest, site=_WA_FI, specs=_WA_FI_SERVICES)


def parse_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest(
    manifest: bytes,
) -> PhysicalWalV2WitnessRoundtripServiceManifest:
    """Parse only the fixed WA-IR-local two-role artifact."""

    return _parse_manifest(manifest, site=_WA_IR, specs=_WA_IR_SERVICES)


def parse_physical_wal_v2_witness_roundtrip_witness_service_manifest(
    manifest: bytes,
) -> PhysicalWalV2WitnessRoundtripServiceManifest:
    """Parse only the fixed Witness-local four-role artifact."""

    return _parse_manifest(manifest, site=_WITNESS, specs=_WITNESS_SERVICES)


def _require_local_manifest_admission(
    manifest: object,
    *,
    config: PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig,
    parser: Callable[[bytes], PhysicalWalV2WitnessRoundtripServiceManifest],
) -> PhysicalWalV2WitnessRoundtripServiceManifest:
    """Check immutable local release pins after the exact named parser."""

    expected_plan_id, expected_release_sha, expected_reference, expected_sha256 = _admission_config(
        config
    )
    if type(manifest) is not bytes or _sha256_bytes(manifest) != expected_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_ADMISSION_CROSS_PIN_MISMATCH")
    try:
        parsed = parser(manifest)
    except PhysicalWalV2WitnessRoundtripDeploymentPlanError as exc:
        raise PhysicalWalV2WitnessRoundtripDeploymentPlanError(
            "V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_ADMISSION_INVALID"
        ) from exc
    if (
        parsed.plan_id != expected_plan_id
        or parsed.release_sha != expected_release_sha
        or parsed.full_bundle_reference != expected_reference
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DEPLOYMENT_MANIFEST_ADMISSION_CROSS_PIN_MISMATCH")
    return parsed


def require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
    manifest: bytes,
    *,
    config: PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig,
) -> PhysicalWalV2WitnessRoundtripServiceManifest:
    """Admit only the exact root-pinned WA-FI artifact; never starts it."""

    return _require_local_manifest_admission(
        manifest,
        config=config,
        parser=parse_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest,
    )


def require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_admission(
    manifest: bytes,
    *,
    config: PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig,
) -> PhysicalWalV2WitnessRoundtripServiceManifest:
    """Admit only the exact root-pinned WA-IR artifact; never starts it."""

    return _require_local_manifest_admission(
        manifest,
        config=config,
        parser=parse_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest,
    )


def require_physical_wal_v2_witness_roundtrip_witness_service_manifest_admission(
    manifest: bytes,
    *,
    config: PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig,
) -> PhysicalWalV2WitnessRoundtripServiceManifest:
    """Admit only the exact root-pinned Witness artifact; never starts it."""

    return _require_local_manifest_admission(
        manifest,
        config=config,
        parser=parse_physical_wal_v2_witness_roundtrip_witness_service_manifest,
    )
