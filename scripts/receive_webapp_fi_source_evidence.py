#!/usr/bin/env python3
"""Receive and expand one exact WebApp-FI source-evidence object on controller.

The FI source exchange intentionally has a narrow final evidence route.  This
helper is its controller-only consumer.  It accepts exactly one FI upload
report for ``webapp_fi -> controller/source-evidence``; reads back that exact
private/versioned Object Storage VersionId; decrypts it only with the
campaign-scoped controller identity; and expands the two FI-signed proofs
only after they are rooted in separately supplied controller-local proofs.

The FI envelope is never treated as a carrier for controller authority.  The
controller delivery envelope, signer-enrollment certificate, and static
assets provenance are each read from independent root-only controller files,
verified with the controller's local signing key, and only then copied into a
new local composite-proof directory.  No FI filesystem path is inspected.

``receive`` is a local dry plan unless ``--apply`` is specified.  An apply has
one Object Storage read-only action.  It never starts Docker or a service,
changes ``current``, or changes a volume or application data.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Require a stable root-owned import path before loading a sibling."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - installed-layout invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (mode & 0o022 and not metadata.st_mode & stat.S_ISVTX)
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    """Return one exact root-owned, non-writable sibling source file."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    _require_root_controlled_directory_chain(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:  # pragma: no cover - installed-layout invariant.
        raise RuntimeError(f"cannot inspect {field}") from exc
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or after.st_uid != 0
        or after.st_nlink != 1
        or stat.S_IMODE(after.st_mode) & 0o022
        or after.st_mode & unsafe_bits
    ):
        raise RuntimeError(f"{field} is not a root-owned non-writable regular non-symlink file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    """Load one named controller-release sibling without using ``sys.path``."""

    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise RuntimeError("required sibling filename is not a safe leaf name")
    source = _require_root_controlled_code_file(
        Path(__file__),
        field="controller FI source evidence receiver source",
    )
    path = _require_root_controlled_code_file(
        source.with_name(filename),
        field=f"required sibling {filename}",
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded_path = getattr(module, "__file__", None)
        if not isinstance(loaded_path, str) or Path(loaded_path).resolve(strict=True) != path:
            raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


receiver = _load_exact_sibling(
    "receive_webapp_fi_source_object.py",
    "_webapp_fi_source_evidence_receiver_object",
)
source_evidence = _load_exact_sibling(
    "build_webapp_fi_source_evidence.py",
    "_webapp_fi_source_evidence_receiver_envelope",
)
provenance = _load_exact_sibling(
    "verify_webapp_fi_source_provenance.py",
    "_webapp_fi_source_evidence_receiver_provenance",
)
preparer = _load_exact_sibling(
    "prepare_webapp_fi_source_adoption.py",
    "_webapp_fi_source_evidence_receiver_preparer",
)


def _load_campaign_bound_controller_signer(campaign_binding_path: Path) -> Any:
    """Load the only controller authority selected by this campaign binding."""

    helper = _load_exact_sibling(
        "manage_controller_campaign_signing_key.py",
        "_webapp_fi_source_evidence_campaign_signing_key",
    )
    try:
        return helper.load_verified_campaign_signer(
            campaign_binding_path=Path(campaign_binding_path)
        )
    except Exception as exc:
        raise SourceEvidenceReceiveError(
            "controller evidence verification authority is not bound to the canonical campaign"
        ) from exc


def _campaign_signing_authority_identity(authority: Any) -> tuple[str, str, str, str]:
    try:
        return (
            authority.campaign_binding.campaign_id,
            authority.campaign_binding.binding_sha256,
            authority.signing_key.public_key_base64,
            authority.signing_key.receipt_sha256,
        )
    except (AttributeError, TypeError) as exc:
        raise SourceEvidenceReceiveError(
            "controller evidence verification authority is incomplete"
        ) from exc


SOURCE_EVIDENCE_READBACK_SCHEMA = "gold-trade-webapp-fi-source-evidence-readback-v1"
SOURCE_EVIDENCE_CONSUMPTION_SCHEMA = "gold-trade-webapp-fi-source-evidence-consumption-receipt-v1"
SOURCE_EVIDENCE_PAYLOAD_NAME = "source-evidence-envelope.json"
READBACK_RECORD_NAME = "readback.json"
PROOF_DIRECTORY_NAME = "proofs"
CONSUMPTION_RECEIPT_NAME = "source-evidence-consumption-receipt.json"

FI_ROLE_ATTESTATION_NAME = "source-role-attestation.json"
FI_IMAGE_EXPORT_NAME = "image-export-receipt.json"
CONTROLLER_DELIVERY_NAME = "controller-delivery-envelope.json"
CONTROLLER_CERTIFICATE_NAME = "signer-enrollment-certificate.json"
CONTROLLER_STATIC_NAME = "static-assets-provenance.json"

MAXIMUM_SOURCE_EVIDENCE_BYTES = 20 * 1024 * 1024
MAXIMUM_CONTROLLER_PROOF_BYTES = 8 * 1024 * 1024
# The encrypted envelope already contributes its declared plaintext bytes to
# the shared receiver capacity preflight.  Expanding it retains copies of its
# two FI proofs (at most the whole envelope), three controller-local proofs,
# and one small consumption receipt.
COMPOSITE_PROOF_RESERVE_BYTES = (
    MAXIMUM_SOURCE_EVIDENCE_BYTES
    + 3 * MAXIMUM_CONTROLLER_PROOF_BYTES
    + 1024 * 1024
)


class SourceEvidenceReceiveError(RuntimeError):
    """A source-evidence Object or local controller proof is unsafe."""


@dataclasses.dataclass(frozen=True)
class _EvidenceKind:
    object_kind: str = "source-evidence"
    destination_site: str = "controller"
    recipient_mode: str = "single"
    readback_schema: str = SOURCE_EVIDENCE_READBACK_SCHEMA
    plaintext_name: str = SOURCE_EVIDENCE_PAYLOAD_NAME
    maximum_plaintext_bytes: int = MAXIMUM_SOURCE_EVIDENCE_BYTES


@dataclasses.dataclass(frozen=True)
class EvidenceReceivePlan:
    """All root-controlled inputs for one deterministic evidence candidate."""

    receive_plan: Any
    campaign_binding_payload: bytes
    canonical_release_tree_sha256: str
    controller_public_key_base64: str
    source_signing_public_key_base64: str
    controller_delivery_envelope_payload: bytes
    signer_enrollment_certificate_payload: bytes
    static_assets_provenance_payload: bytes
    verification_time: str
    controller_signing_key_id: str = ""
    controller_signing_key_receipt_sha256: str = ""


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _raise_receiver_error(action: Any, *, message: str) -> Any:
    try:
        return action()
    except Exception as exc:
        receiver_error = getattr(receiver, "SourceObjectReceiveError", RuntimeError)
        if isinstance(exc, receiver_error):
            raise SourceEvidenceReceiveError(message) from exc
        raise


def _raise_transport_error(action: Any, *, message: str) -> Any:
    """Normalize controller-local transport failures at this CLI boundary."""

    try:
        return action()
    except Exception as exc:
        transport_error = getattr(receiver.transport, "SourceTransportError", RuntimeError)
        if isinstance(exc, transport_error):
            raise SourceEvidenceReceiveError(message) from exc
        raise


def _raise_binding_error(action: Any, *, message: str) -> Any:
    try:
        return action()
    except Exception as exc:
        binding_error = getattr(receiver.binding, "CampaignBindingError", RuntimeError)
        if isinstance(exc, binding_error):
            raise SourceEvidenceReceiveError(message) from exc
        raise


def _read_root_private_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    try:
        return receiver.exchange._read_private_file(
            Path(path),
            field=field,
            maximum_bytes=maximum_bytes,
        )
    except Exception as exc:
        exchange_error = getattr(receiver.exchange, "SourceExchangeError", RuntimeError)
        if isinstance(exc, exchange_error):
            raise SourceEvidenceReceiveError(f"{field} is not one root-only canonical file") from exc
        raise


def _exact_evidence_kind(request: Any, *, policy: Any) -> _EvidenceKind:
    if (
        request.source_site != "webapp_fi"
        or request.destination_site != "controller"
        or request.object_kind != receiver.contract.SOURCE_EVIDENCE_OBJECT_KIND
        or request.mode != receiver.contract.SINGLE_MODE
        or tuple(request.recipients) != (policy.controller_age_recipient,)
    ):
        raise SourceEvidenceReceiveError(
            "source evidence receiver supports only the exact WebApp-FI-to-controller source-evidence route"
        )
    return _EvidenceKind()


def _receive_plan(
    *,
    controller_config: Any,
    campaign_binding_path: Path,
    upload_report_path: Path,
) -> tuple[Any, bytes]:
    """Build the shared read-back plan without widening the static/raw receiver."""

    _raise_receiver_error(
        receiver._require_root_execution,
        message="controller source evidence receive operations must run as root",
    )
    controller_config = _raise_transport_error(
        lambda: receiver.transport._validate_controller_config(controller_config),
        message="controller source transport configuration is invalid",
    )
    campaign = _raise_binding_error(
        lambda: receiver.binding.load_campaign_binding(Path(campaign_binding_path)),
        message="canonical campaign binding is invalid",
    )
    controller_config = _raise_transport_error(
        lambda: receiver.transport.require_controller_config_for_campaign(
            controller_config=controller_config,
            campaign_id=campaign.campaign_id,
        ),
        message="controller source transport config does not bind the canonical campaign",
    )
    data_root = _raise_receiver_error(
        receiver._require_controller_receive_data_root,
        message="controller source evidence receive data root is unsafe",
    )
    policy = controller_config.policy
    policy_sha256 = _raise_receiver_error(
        lambda: receiver.policy_binding_sha256(policy),
        message="controller source transport policy is invalid",
    )
    binding_payload = _read_root_private_file(
        Path(campaign_binding_path),
        field="canonical campaign binding",
        maximum_bytes=source_evidence.MAX_BINDING_BYTES,
    )
    if binding_payload != receiver.binding.canonical_json_bytes(
        {
            "schema": receiver.binding.CAMPAIGN_BINDING_SCHEMA,
            "status": "bound",
            "campaign_id": campaign.campaign_id,
            "application": {
                "release_sha": campaign.application_release_sha,
                "release_tree": campaign.application_release_tree,
                "expected_alembic_revision": campaign.expected_alembic_revision,
            },
            "tooling": {
                "control_commit": campaign.control_commit,
                "control_tree": campaign.control_tree,
            },
            "binding_sha256": campaign.binding_sha256,
        }
    ) + b"\n":
        raise SourceEvidenceReceiveError("canonical campaign binding changed while being read")
    verified_identity = _raise_receiver_error(
        lambda: receiver.identity_bootstrap.load_verified_identity(
            campaign_binding_path=Path(campaign_binding_path)
        ),
        message="controller source receive identity or receipt is invalid",
    )
    if verified_identity.recipient != policy.controller_age_recipient:
        raise SourceEvidenceReceiveError(
            "controller source receive identity recipient does not match the pinned controller policy recipient"
        )
    report_payload = _read_root_private_file(
        Path(upload_report_path),
        field="FI source evidence upload report",
        maximum_bytes=receiver.exchange.MAX_JSON_BYTES,
    )
    exchange_policy = _raise_receiver_error(
        lambda: receiver._policy_for_exchange(policy),
        message="controller source transport policy cannot be projected for FI report verification",
    )
    try:
        report = receiver.exchange.verify_upload_report(policy=exchange_policy, payload=report_payload)
    except Exception as exc:
        raise SourceEvidenceReceiveError("FI source evidence upload report is invalid") from exc
    request = _raise_receiver_error(
        lambda: receiver._request_from_verified_report(report, policy=policy),
        message="FI source evidence upload report request is invalid",
    )
    if (
        request.campaign_id != campaign.campaign_id
        or request.release_sha != campaign.application_release_sha
        or request.control_commit != campaign.control_commit
        or request.control_tree != campaign.control_tree
    ):
        raise SourceEvidenceReceiveError(
            "FI source evidence upload report is not bound to the canonical campaign release and control pins"
        )
    kind = _exact_evidence_kind(request, policy=policy)
    maximum_plaintext_bytes = min(policy.maximum_plaintext_bytes, kind.maximum_plaintext_bytes)
    try:
        descriptor = receiver.contract.validate_object_descriptor(
            report.get("object"),
            maximum_plaintext_bytes=maximum_plaintext_bytes,
        )
    except Exception as exc:
        raise SourceEvidenceReceiveError("FI source evidence object exceeds the supported receiver bounds") from exc
    if descriptor["object_key"] != receiver.contract.source_object_key(policy, request):
        raise SourceEvidenceReceiveError("FI source evidence object key is not bound to the canonical request")
    candidate_root, candidate_directory = _raise_receiver_error(
        lambda: receiver._candidate_path(
            data_root=data_root,
            campaign_binding_path=Path(campaign_binding_path),
            campaign_binding=campaign,
            policy_sha256=policy_sha256,
            request=request,
            descriptor=descriptor,
        ),
        message="FI source evidence receive candidate path is invalid",
    )
    _raise_receiver_error(
        lambda: receiver._require_if_present_root_private_directory(
            data_root / campaign.campaign_id,
            field="FI source evidence receive campaign data directory",
        ),
        message="FI source evidence receive campaign data directory is unsafe",
    )
    _raise_receiver_error(
        lambda: receiver._require_if_present_root_private_directory(
            candidate_root,
            field="FI source evidence receive candidate root",
        ),
        message="FI source evidence receive candidate root is unsafe",
    )
    if candidate_directory.exists() or candidate_directory.is_symlink():
        raise SourceEvidenceReceiveError("FI source evidence receive candidate already exists and will not be reused")
    return (
        receiver.ReceivePlan(
            controller_config=controller_config,
            campaign_binding_path=Path(campaign_binding_path),
            upload_report_path=Path(upload_report_path),
            campaign_binding=campaign,
            policy_sha256=policy_sha256,
            request=request,
            descriptor=descriptor,
            kind=kind,
            data_root=data_root,
            candidate_root=candidate_root,
            candidate_directory=candidate_directory,
            age_identity_file=verified_identity.layout.identity_path,
            age_identity_recipient=verified_identity.recipient,
            age_identity_key_id=verified_identity.key_id,
            age_identity_receipt_sha256=verified_identity.receipt_sha256,
        ),
        binding_payload,
    )


def _prepared_package_identity(
    *,
    package_directory: Path,
    preparation_receipt: Path,
    campaign: Any,
) -> str:
    try:
        prepared = preparer.verify_prepared_source_adoption_package(
            package_directory=Path(package_directory),
            preparation_receipt=Path(preparation_receipt),
            expected_control_commit=campaign.control_commit,
            expected_application_release_sha=campaign.application_release_sha,
        )
    except Exception as exc:
        raise SourceEvidenceReceiveError("controller-local source-adoption package is not the canonical campaign package") from exc
    if (
        prepared.get("application")
        != {
            "release_sha": campaign.application_release_sha,
            "expected_alembic_revision": campaign.expected_alembic_revision,
        }
        or prepared.get("tooling")
        != {"control_commit": campaign.control_commit, "control_tree": campaign.control_tree}
    ):
        raise SourceEvidenceReceiveError("controller-local source-adoption package does not match the canonical campaign binding")
    value = prepared.get("canonical_release_tree_sha256")
    try:
        return provenance._sha(value, field="controller-local canonical release tree SHA-256")
    except Exception as exc:
        raise SourceEvidenceReceiveError("controller-local source-adoption package canonical tree is invalid") from exc


def prepare_source_evidence_receive(
    *,
    controller_config: Any,
    campaign_binding_path: Path,
    upload_report_path: Path,
    source_adoption_package_directory: Path,
    source_adoption_preparation_receipt: Path,
    controller_delivery_envelope: Path,
    signer_enrollment_certificate: Path,
    static_assets_provenance: Path,
    verification_time: str,
) -> EvidenceReceivePlan:
    """Verify all controller-local inputs before a client is created or read."""

    receive_plan, binding_payload = _receive_plan(
        controller_config=controller_config,
        campaign_binding_path=Path(campaign_binding_path),
        upload_report_path=Path(upload_report_path),
    )
    campaign = receive_plan.campaign_binding
    signing_authority = _load_campaign_bound_controller_signer(
        Path(campaign_binding_path)
    )
    if (
        signing_authority.campaign_binding.campaign_id != campaign.campaign_id
        or signing_authority.campaign_binding.binding_sha256 != campaign.binding_sha256
        or signing_authority.campaign_binding.application_release_sha
        != campaign.application_release_sha
        or signing_authority.campaign_binding.application_release_tree
        != campaign.application_release_tree
        or signing_authority.campaign_binding.expected_alembic_revision
        != campaign.expected_alembic_revision
        or signing_authority.campaign_binding.control_commit != campaign.control_commit
        or signing_authority.campaign_binding.control_tree != campaign.control_tree
    ):
        raise SourceEvidenceReceiveError(
            "controller evidence verification authority does not match the canonical campaign binding"
        )
    canonical_tree = _prepared_package_identity(
        package_directory=Path(source_adoption_package_directory),
        preparation_receipt=Path(source_adoption_preparation_receipt),
        campaign=campaign,
    )
    controller_public = signing_authority.signing_key.public_key_base64
    try:
        provenance._key(controller_public, field="controller evidence verification public key")
    except Exception as exc:
        raise SourceEvidenceReceiveError(
            "controller evidence verification public key is invalid"
        ) from exc
    delivery_payload = _read_root_private_file(
        Path(controller_delivery_envelope),
        field="controller-local delivery envelope",
        maximum_bytes=MAXIMUM_CONTROLLER_PROOF_BYTES,
    )
    certificate_payload = _read_root_private_file(
        Path(signer_enrollment_certificate),
        field="controller-local signer enrollment certificate",
        maximum_bytes=MAXIMUM_CONTROLLER_PROOF_BYTES,
    )
    static_payload = _read_root_private_file(
        Path(static_assets_provenance),
        field="controller-local static assets provenance",
        maximum_bytes=MAXIMUM_CONTROLLER_PROOF_BYTES,
    )
    try:
        provenance._timestamp(verification_time, field="source evidence verification time")
        delivery = provenance._controller_delivery_envelope(
            payload=delivery_payload,
            pinned_controller_public_key_base64=controller_public,
            expected_campaign_id=campaign.campaign_id,
            expected_application={
                "release_sha": campaign.application_release_sha,
                "expected_alembic_revision": campaign.expected_alembic_revision,
            },
            expected_tooling={"control_commit": campaign.control_commit, "control_tree": campaign.control_tree},
            expected_canonical_release_tree_sha256=canonical_tree,
        )
        certificate_value = provenance._parse(
            certificate_payload,
            field="controller-local signer enrollment certificate",
        )
        source_public = certificate_value.get("source_signing_public_key_base64")
        provenance._key(
            source_public,
            field="controller-local signer enrollment certificate source key",
        )
        certificate = provenance._signer_enrollment_certificate(
            payload=certificate_payload,
            pinned_controller_public_key_base64=controller_public,
            expected_campaign_id=campaign.campaign_id,
            expected_application={
                "release_sha": campaign.application_release_sha,
                "expected_alembic_revision": campaign.expected_alembic_revision,
            },
            expected_tooling={"control_commit": campaign.control_commit, "control_tree": campaign.control_tree},
            expected_canonical_release_tree_sha256=canonical_tree,
            expected_delivery=delivery,
            expected_source_signing_public_key_base64=source_public,
            verification_time=verification_time,
        )
        if certificate["source_signing_public_key_base64"] != source_public:
            raise ValueError("controller-local signer enrollment certificate source key changed while validating")
        provenance._static_assets_provenance(
            payload=static_payload,
            pinned_controller_public_key_base64=controller_public,
            expected_campaign_id=campaign.campaign_id,
            expected_application={
                "release_sha": campaign.application_release_sha,
                "expected_alembic_revision": campaign.expected_alembic_revision,
            },
        )
    except Exception as exc:
        raise SourceEvidenceReceiveError("controller-local evidence authority is not bound to the canonical campaign") from exc
    return EvidenceReceivePlan(
        receive_plan=receive_plan,
        campaign_binding_payload=binding_payload,
        canonical_release_tree_sha256=canonical_tree,
        controller_public_key_base64=controller_public,
        controller_signing_key_id=signing_authority.signing_key.key_id,
        controller_signing_key_receipt_sha256=signing_authority.signing_key.receipt_sha256,
        source_signing_public_key_base64=source_public,
        controller_delivery_envelope_payload=delivery_payload,
        signer_enrollment_certificate_payload=certificate_payload,
        static_assets_provenance_payload=static_payload,
        verification_time=verification_time,
    )


def _verified_evidence_proofs(plan: EvidenceReceivePlan, payload: bytes) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Root FI envelope proofs in independently local controller authority."""

    source_public = plan.source_signing_public_key_base64
    try:
        envelope = source_evidence.verify_source_evidence_envelope_payload(
            payload=payload,
            expected_campaign_binding_payload=plan.campaign_binding_payload,
            pinned_source_signing_public_key_base64=source_public,
            verification_time=plan.verification_time,
        )
        if envelope["evidence_id"] != plan.receive_plan.request.object_id:
            raise ValueError("signed evidence ID does not match the exact requested Object ID")
        role_payload = envelope["role_attestation_payload"]
        image_payload = envelope["image_export_receipt_payload"]
        role_value = provenance._parse(role_payload, field="FI source role attestation from evidence envelope")
        canonical_tree = provenance._sha(
            role_value.get("canonical_release_tree_sha256"),
            field="FI source role attestation canonical tree",
        )
        active_image = role_value.get("active_application_image")
        if not isinstance(active_image, Mapping):
            raise ValueError("active image is absent")
        image_id = active_image.get("image_id")
        image_reference = active_image.get("image_reference")
        if not isinstance(image_id, str) or not isinstance(image_reference, str):
            raise ValueError("active image is malformed")
        campaign = plan.receive_plan.campaign_binding
        expected_application = {
            "release_sha": campaign.application_release_sha,
            "expected_alembic_revision": campaign.expected_alembic_revision,
        }
        expected_tooling = {"control_commit": campaign.control_commit, "control_tree": campaign.control_tree}
        delivery = provenance._controller_delivery_envelope(
            payload=plan.controller_delivery_envelope_payload,
            pinned_controller_public_key_base64=plan.controller_public_key_base64,
            expected_campaign_id=campaign.campaign_id,
            expected_application=expected_application,
            expected_tooling=expected_tooling,
            expected_canonical_release_tree_sha256=plan.canonical_release_tree_sha256,
        )
        certificate = provenance._signer_enrollment_certificate(
            payload=plan.signer_enrollment_certificate_payload,
            pinned_controller_public_key_base64=plan.controller_public_key_base64,
            expected_campaign_id=campaign.campaign_id,
            expected_application=expected_application,
            expected_tooling=expected_tooling,
            expected_canonical_release_tree_sha256=plan.canonical_release_tree_sha256,
            expected_delivery=delivery,
            expected_source_signing_public_key_base64=source_public,
            verification_time=plan.verification_time,
        )
        if canonical_tree != plan.canonical_release_tree_sha256:
            raise ValueError("FI role attestation canonical tree differs from controller package")
        authority = provenance.verify_webapp_fi_source_authority_payloads(
            source_role_attestation_payload=role_payload,
            image_export_receipt_payload=image_payload,
            controller_delivery_envelope_payload=plan.controller_delivery_envelope_payload,
            signer_enrollment_certificate_payload=plan.signer_enrollment_certificate_payload,
            static_assets_provenance_payload=plan.static_assets_provenance_payload,
            pinned_source_signing_public_key_base64=source_public,
            pinned_controller_public_key_base64=plan.controller_public_key_base64,
            expected_campaign_id=campaign.campaign_id,
            expected_application=expected_application,
            expected_control_commit=campaign.control_commit,
            expected_control_tree=campaign.control_tree,
            expected_canonical_release_tree_sha256=plan.canonical_release_tree_sha256,
            expected_app_image_id=image_id,
            expected_app_image_reference=image_reference,
            verification_time=plan.verification_time,
        )
        if (
            certificate.get("source_signing_public_key_base64") != source_public
            or authority.get("proof_sha256", {}).get("source_role_attestation")
            != envelope["role_attestation_sha256"]
            or authority.get("proof_sha256", {}).get("image_export_receipt")
            != envelope["image_export_receipt_sha256"]
        ):
            raise ValueError("authority proof binding is inconsistent")
    except Exception as exc:
        raise SourceEvidenceReceiveError(
            "FI source evidence cannot be rooted in the independently controller-local authority"
        ) from exc
    proofs = {
        FI_ROLE_ATTESTATION_NAME: role_payload,
        FI_IMAGE_EXPORT_NAME: image_payload,
        # These three bytes are read only from controller-local files.  The FI
        # envelope contains no controller-origin proof payload and never
        # influences these output values.
        CONTROLLER_DELIVERY_NAME: plan.controller_delivery_envelope_payload,
        CONTROLLER_CERTIFICATE_NAME: plan.signer_enrollment_certificate_payload,
        CONTROLLER_STATIC_NAME: plan.static_assets_provenance_payload,
    }
    return proofs, {
        "evidence_id": envelope["evidence_id"],
        "source_signing_public_key_base64": source_public,
        "source_signing_key_id": envelope["source_signing_key_id"],
        "controller_key_id": certificate["controller_key_id"],
        "proof_sha256": dict(authority["proof_sha256"]),
    }


def _create_private_directory(parent: Path, name: str, *, field: str) -> Path:
    parent = _raise_receiver_error(
        lambda: receiver.exchange._require_root_private_directory(parent, field=field + " parent"),
        message=f"{field} parent is unsafe",
    )
    if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
        raise SourceEvidenceReceiveError(f"{field} name is invalid")
    destination = parent / name
    if destination.exists() or destination.is_symlink():
        raise SourceEvidenceReceiveError(f"refusing to reuse existing {field}")
    try:
        os.mkdir(destination, 0o700)
        os.chmod(destination, 0o700)
    except OSError as exc:
        raise SourceEvidenceReceiveError(f"cannot create {field}") from exc
    return _raise_receiver_error(
        lambda: receiver.exchange._require_root_private_directory(destination, field=field),
        message=f"created {field} is unsafe",
    )


def _write_new_private_file(path: Path, payload: bytes, *, field: str) -> None:
    if not isinstance(payload, bytes) or not payload:
        raise SourceEvidenceReceiveError(f"{field} payload is invalid")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise SourceEvidenceReceiveError(f"refusing to overwrite {field}") from exc
    except OSError as exc:
        raise SourceEvidenceReceiveError(f"cannot create {field}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - regular file writes do not return zero.
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise SourceEvidenceReceiveError(f"cannot durably write {field}") from exc
    finally:
        os.close(descriptor)
    observed = _read_root_private_file(path, field=field, maximum_bytes=max(len(payload), 1))
    if observed != payload:
        raise SourceEvidenceReceiveError(f"{field} changed after creation")


def _consumption_receipt(
    *,
    plan: EvidenceReceivePlan,
    proof_paths: Mapping[str, Path],
    proof_metadata: Mapping[str, Any],
) -> bytes:
    origins = {
        FI_ROLE_ATTESTATION_NAME: "webapp_fi_source_evidence_envelope",
        FI_IMAGE_EXPORT_NAME: "webapp_fi_source_evidence_envelope",
        CONTROLLER_DELIVERY_NAME: "controller_local_input",
        CONTROLLER_CERTIFICATE_NAME: "controller_local_input",
        CONTROLLER_STATIC_NAME: "controller_local_input",
    }
    files = {
        name: {"sha256": sha256_bytes(path.read_bytes()), "origin": origins[name]}
        for name, path in sorted(proof_paths.items())
    }
    unsigned = {
        "schema": SOURCE_EVIDENCE_CONSUMPTION_SCHEMA,
        "status": "consumed",
        "campaign_id": plan.receive_plan.campaign_binding.campaign_id,
        "evidence_id": proof_metadata["evidence_id"],
        "object": dict(plan.receive_plan.descriptor),
        "campaign_binding_sha256": plan.receive_plan.campaign_binding.binding_sha256,
        "canonical_release_tree_sha256": plan.canonical_release_tree_sha256,
        "source_signing_key_id": proof_metadata["source_signing_key_id"],
        "controller_key_id": proof_metadata["controller_key_id"],
        "proofs": files,
    }
    return canonical_json_bytes({**unsigned, "receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}) + b"\n"


def _require_composite_proof_capacity_reserve() -> None:
    """Keep the shared receiver margin large enough for evidence expansion."""

    margin = receiver.CAPACITY_MARGIN_BYTES
    if isinstance(margin, bool) or not isinstance(margin, int) or margin < COMPOSITE_PROOF_RESERVE_BYTES:
        raise SourceEvidenceReceiveError(
            "controller source receive capacity margin cannot retain the source-evidence composite proofs"
        )


def _expand_verified_evidence(*, plan: EvidenceReceivePlan, candidate: Path, payload: bytes) -> dict[str, Any]:
    proofs, metadata = _verified_evidence_proofs(plan, payload)
    proof_directory = _create_private_directory(candidate, PROOF_DIRECTORY_NAME, field="source evidence proof directory")
    proof_paths: dict[str, Path] = {}
    for name, proof in proofs.items():
        path = proof_directory / name
        _write_new_private_file(path, proof, field=f"source evidence proof {name}")
        proof_paths[name] = path
    receipt_payload = _consumption_receipt(
        plan=plan,
        proof_paths=proof_paths,
        proof_metadata=metadata,
    )
    receipt_path = proof_directory / CONSUMPTION_RECEIPT_NAME
    _write_new_private_file(receipt_path, receipt_payload, field="source evidence consumption receipt")
    receiver._fsync_directory(proof_directory, field="source evidence proof directory")
    return {
        "proof_directory": proof_directory,
        "consumption_receipt": receipt_path,
        "evidence_id": metadata["evidence_id"],
        "source_signing_key_id": metadata["source_signing_key_id"],
        "controller_key_id": metadata["controller_key_id"],
        "proof_paths": proof_paths,
    }


def receive_source_evidence(
    client: Any,
    *,
    plan: EvidenceReceivePlan,
    decryptor: Any = None,
    refreshed_plan_factory: Any | None = None,
) -> dict[str, Any]:
    """Read back, decrypt, verify, and expand one evidence Object exactly once."""

    _raise_receiver_error(
        receiver._require_root_execution,
        message="controller source evidence receive operations must run as root",
    )
    if not isinstance(plan, EvidenceReceivePlan):
        raise SourceEvidenceReceiveError("source evidence receive plan is unsupported")
    if decryptor is None:
        decryptor = receiver._run_age_decrypt
    if not callable(decryptor):
        raise SourceEvidenceReceiveError("source evidence decryptor is unsupported")
    # The caller supplies a complete factory only in the CLI path.  Unit tests
    # can omit it after constructing a closed, fully local plan.
    if refreshed_plan_factory is not None:
        refreshed = refreshed_plan_factory()
        if refreshed != plan:
            raise SourceEvidenceReceiveError("source evidence local inputs changed after preflight")
        plan = refreshed
    _require_composite_proof_capacity_reserve()
    candidate = _raise_receiver_error(
        lambda: receiver._create_candidate(plan.receive_plan),
        message="cannot create source evidence receive candidate",
    )
    capacity = _raise_receiver_error(
        lambda: receiver._capacity_preflight(plan=plan.receive_plan, candidate=candidate),
        message="source evidence receive candidate lacks required staging-volume capacity",
    )
    ciphertext = _raise_receiver_error(
        lambda: receiver._download_exact_ciphertext(client, plan=plan.receive_plan, candidate=candidate),
        message="cannot read back the exact source evidence Object Storage version",
    )
    plaintext = candidate / SOURCE_EVIDENCE_PAYLOAD_NAME
    try:
        decryptor(plan.receive_plan, ciphertext, plaintext)
    except SourceEvidenceReceiveError:
        raise
    except Exception as exc:
        raise SourceEvidenceReceiveError("age decryption of source evidence failed") from exc
    _raise_receiver_error(
        lambda: receiver.exchange._require_root_private_file(
            plaintext,
            field="age-decrypted source evidence payload",
            maximum_bytes=MAXIMUM_SOURCE_EVIDENCE_BYTES,
        ),
        message="age-decrypted source evidence payload is unsafe",
    )
    try:
        observed_sha256, observed_bytes = receiver.exchange._secure_hash_file(
            plaintext,
            field="age-decrypted source evidence payload",
            maximum_bytes=MAXIMUM_SOURCE_EVIDENCE_BYTES,
        )
    except Exception as exc:
        raise SourceEvidenceReceiveError("age-decrypted source evidence payload cannot be verified") from exc
    if (
        observed_sha256 != plan.receive_plan.descriptor["plaintext_sha256"]
        or observed_bytes != plan.receive_plan.descriptor["plaintext_bytes"]
    ):
        raise SourceEvidenceReceiveError("age-decrypted source evidence payload does not match its exact upload report")
    payload = _read_root_private_file(
        plaintext,
        field="age-decrypted source evidence payload",
        maximum_bytes=MAXIMUM_SOURCE_EVIDENCE_BYTES,
    )
    readback_record = receiver._build_readback_record(plan.receive_plan)
    readback_path = candidate / READBACK_RECORD_NAME
    _raise_receiver_error(
        lambda: receiver._write_new_readback_record(readback_path, readback_record),
        message="cannot create source evidence readback record",
    )
    expanded = _raise_receiver_error(
        lambda: _expand_verified_evidence(plan=plan, candidate=candidate, payload=payload),
        message="cannot expand verified source evidence into the controller candidate",
    )
    return {
        "status": "received_and_consumed",
        "campaign_id": plan.receive_plan.campaign_binding.campaign_id,
        "object_kind": plan.receive_plan.request.object_kind,
        "object_id": plan.receive_plan.request.object_id,
        "candidate_directory": str(candidate),
        "payload_path": str(plaintext),
        "readback_record": str(readback_path),
        "proof_directory": str(expanded["proof_directory"]),
        "consumption_receipt": str(expanded["consumption_receipt"]),
        "evidence_id": expanded["evidence_id"],
        "source_signing_key_id": expanded["source_signing_key_id"],
        "controller_key_id": expanded["controller_key_id"],
        "proof_paths": {name: str(path) for name, path in sorted(expanded["proof_paths"].items())},
        "capacity": dict(capacity),
        "object_storage_action": "read_back_exact_version",
        "docker_action": False,
        "service_changed": False,
        "current_changed": False,
        "container_changed": False,
        "volume_changed": False,
        "application_data_changed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--campaign-binding", required=True, type=Path)
    parser.add_argument("--upload-report", required=True, type=Path)
    parser.add_argument("--source-adoption-package-directory", required=True, type=Path)
    parser.add_argument("--source-adoption-preparation-receipt", required=True, type=Path)
    parser.add_argument("--controller-delivery-envelope", required=True, type=Path)
    parser.add_argument("--signer-enrollment-certificate", required=True, type=Path)
    parser.add_argument("--static-assets-provenance", required=True, type=Path)
    parser.add_argument("--verification-time", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        controller_config = _raise_transport_error(
            lambda: receiver.transport.load_controller_config(args.config),
            message="controller source transport configuration is invalid",
        )

        def build_plan() -> EvidenceReceivePlan:
            return prepare_source_evidence_receive(
                controller_config=controller_config,
                campaign_binding_path=args.campaign_binding,
                upload_report_path=args.upload_report,
                source_adoption_package_directory=args.source_adoption_package_directory,
                source_adoption_preparation_receipt=args.source_adoption_preparation_receipt,
                controller_delivery_envelope=args.controller_delivery_envelope,
                signer_enrollment_certificate=args.signer_enrollment_certificate,
                static_assets_provenance=args.static_assets_provenance,
                verification_time=args.verification_time,
            )

        plan = build_plan()
        if not args.apply:
            result: Mapping[str, Any] = {
                "status": "planned",
                "campaign_id": plan.receive_plan.campaign_binding.campaign_id,
                "object_kind": plan.receive_plan.request.object_kind,
                "object_id": plan.receive_plan.request.object_id,
                "candidate_directory": str(plan.receive_plan.candidate_directory),
                "canonical_release_tree_sha256": plan.canonical_release_tree_sha256,
                "object_storage_action": False,
                "docker_action": False,
                "service_changed": False,
                "current_changed": False,
                "container_changed": False,
                "volume_changed": False,
                "application_data_changed": False,
            }
        else:
            client = _raise_transport_error(
                lambda: receiver.transport.create_s3_client(controller_config),
                message="cannot create the controller Object Storage client",
            )
            result = receive_source_evidence(
                client,
                plan=plan,
                refreshed_plan_factory=build_plan,
            )
    except SourceEvidenceReceiveError as exc:
        print(
            canonical_json_bytes(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}
            ).decode("ascii"),
            file=sys.stderr,
        )
        return 2
    print(canonical_json_bytes(dict(result)).decode("ascii"))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper.
    raise SystemExit(main())
