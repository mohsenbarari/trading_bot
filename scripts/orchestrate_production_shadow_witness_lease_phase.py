#!/usr/bin/env python3
"""Plan the production-shadow Witness-lease phase without enabling execution.

This bridge is intentionally a controller-facing contract, rather than a
wrapper around the individual lease worker.  It describes the exact three-role
evidence closure required for ``witness_lease`` and refuses every apply
attempt until a future sealed manifest binds the added Object Storage control
protocol artifacts.

The WebApp-IR and Witness payload paths are fixed to private, versioned,
age-encrypted Object Storage.  SSH may carry only a bounded control command and
the lease authority frames; it must never carry an application request,
response, release, policy, or other file payload for either role.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402


PHASE = "witness_lease"
OPERATION = "acquire-shadow-writer-witness-lease"
ROLES = ("witness", "webapp_fi", "webapp_ir")
PLAN_SCHEMA = "production-shadow-witness-lease-phase-bridge-plan-v1"
ERROR_SCHEMA = "production-shadow-witness-lease-phase-bridge-error-v1"
APPLY_CONFIRMATION = "ENABLE-SEALED-WITNESS-LEASE-PHASE"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PLAN_FIELDS = frozenset(
    {
        "schema",
        "status",
        "phase",
        "operation",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "role_transport_contracts",
        "required_immutable_bindings",
        "missing_immutable_bindings",
        "required_attestation_inputs",
        "apply_supported",
        "direct_witness_ssh_file_transfer_allowed",
        "production_contacted",
        "journal_mutated",
        "current_mutated",
        "service_mutated",
        "volume_mutated",
        "object_storage_mutated",
    }
)
TRANSPORT_FIELDS = frozenset(
    {
        "role",
        "controller_transport",
        "payload_transport",
        "ssh_application_payload_bytes",
        "direct_ssh_file_transfer_allowed",
        "presigned_url_persisted",
        "required_result_readback",
    }
)
BINDING_FIELDS = frozenset(
    {
        "id",
        "manifest_path",
        "kind",
        "role",
        "purpose",
        "required_transport",
    }
)
ATTESTATION_INPUT_FIELDS = frozenset(
    {
        "role",
        "source",
        "required_binding_ids",
        "payload_transport",
        "required_properties",
    }
)


class WitnessLeasePhaseBridgeError(RuntimeError):
    """The bridge cannot prove that a sealed phase execution is available."""


@dataclass(frozen=True)
class BridgeContext:
    manifest: Mapping[str, Any]
    manifest_sha256: str


@dataclass(frozen=True)
class ImmutableBinding:
    identifier: str
    manifest_path: str
    kind: str
    role: str
    purpose: str
    required_transport: str

    def document(self) -> dict[str, str]:
        document = {
            "id": self.identifier,
            "manifest_path": self.manifest_path,
            "kind": self.kind,
            "role": self.role,
            "purpose": self.purpose,
            "required_transport": self.required_transport,
        }
        if set(document) != BINDING_FIELDS:
            raise WitnessLeasePhaseBridgeError("immutable binding fields differ")
        return document


# Every value below must be sealed into the immutable controller manifest before
# an apply implementation can exist.  Only public references and hashes belong
# there; no private age identity, SSH private key, issuer secret, or token is a
# manifest artifact.
REQUIRED_IMMUTABLE_BINDINGS: tuple[ImmutableBinding, ...] = (
    ImmutableBinding(
        "object-storage-control-transport",
        "artifacts.object_storage_control_transport_sha256",
        "sha256",
        "controller",
        "versioned age request/result protocol source",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "object-storage-control-result-put-capability",
        "artifacts.object_storage_control_result_put_capability_sha256",
        "sha256",
        "controller",
        "provider proof that presigned PUT signs If-None-Match and returns VersionId",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "object-storage-control-result-recovery-contract",
        "artifacts.object_storage_control_result_recovery_contract_sha256",
        "sha256",
        "controller",
        "ambiguous PUT recovery: exact metadata HEAD, returned VersionId and controller decrypt validation",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "object-storage-control-bucket-prefix",
        "deployment.object_storage_control_namespace_sha256",
        "sha256",
        "controller",
        "approved private/versioned bucket and isolated operation prefix",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "controller-result-age-recipient",
        "artifacts.controller_result_age_recipient_sha256",
        "sha256",
        "controller",
        "public controller age recipient used only for result encryption",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "webapp-fi-witness-lease-worker",
        "artifacts.webapp_fi_witness_lease_worker_sha256",
        "sha256",
        "webapp_fi",
        "exact release worker source for acquire or renewal",
        "ssh-control",
    ),
    ImmutableBinding(
        "webapp-fi-witness-lease-role-material",
        "artifacts.webapp_fi_witness_lease_role_material_sha256",
        "sha256",
        "webapp_fi",
        "root-only role manifest and Witness public-key policy input",
        "ssh-control",
    ),
    ImmutableBinding(
        "webapp-fi-witness-lease-dependency-closure",
        "artifacts.webapp_fi_witness_lease_dependency_closure_sha256",
        "sha256",
        "webapp_fi",
        "bootstrap, status, client, contract and authority-protocol source closure",
        "ssh-control",
    ),
    ImmutableBinding(
        "webapp-fi-control-ssh-trust",
        "artifacts.webapp_fi_control_ssh_trust_sha256",
        "sha256",
        "webapp_fi",
        "pinned controller SSH identity and known-hosts trust bundle reference",
        "ssh-control",
    ),
    ImmutableBinding(
        "webapp-ir-control-receiver",
        "artifacts.webapp_ir_object_storage_control_receiver_sha256",
        "sha256",
        "webapp_ir",
        "exact target receiver source with a single allowlisted readback worker",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "webapp-ir-control-receiver-policy",
        "artifacts.webapp_ir_object_storage_control_receiver_policy_sha256",
        "sha256",
        "webapp_ir",
        "root-only receiver policy binding release, role, worker, recipient and prefix",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "webapp-ir-witness-lease-dependency-closure",
        "artifacts.webapp_ir_witness_lease_dependency_closure_sha256",
        "sha256",
        "webapp_ir",
        "fixed readback worker and every exact-release helper source closure",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "webapp-ir-bootstrap-age-recipient",
        "artifacts.webapp_ir_bootstrap_age_recipient_sha256",
        "sha256",
        "webapp_ir",
        "public recipient for request decrypt only; private identity remains local",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "witness-control-receiver",
        "artifacts.witness_object_storage_control_receiver_sha256",
        "sha256",
        "witness",
        "exact target receiver for a separately allowlisted Witness attestation worker",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "witness-control-receiver-policy",
        "artifacts.witness_object_storage_control_receiver_policy_sha256",
        "sha256",
        "witness",
        "root-only policy for the fixed Witness attestation request type",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "witness-bootstrap-age-recipient",
        "artifacts.witness_bootstrap_age_recipient_sha256",
        "sha256",
        "witness",
        "public recipient for Object Storage request decrypt only",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "witness-signed-attestation-worker",
        "artifacts.witness_lease_signed_attestation_worker_sha256",
        "sha256",
        "witness",
        "fresh signed lease/status attestation producer",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "witness-signed-attestation-dependency-closure",
        "artifacts.witness_lease_signed_attestation_dependency_closure_sha256",
        "sha256",
        "witness",
        "fixed attestation worker and issuer/status dependency source closure",
        "object-storage-private-versioned-age",
    ),
    ImmutableBinding(
        "witness-signed-attestation-policy",
        "artifacts.witness_lease_signed_attestation_policy_sha256",
        "sha256",
        "witness",
        "public verification policy and issuer/key identifiers for fresh attestation",
        "object-storage-private-versioned-age",
    ),
)


def _lookup_manifest_path(document: Mapping[str, Any], dotted_path: str) -> Any:
    value: Any = document
    for component in dotted_path.split("."):
        if not isinstance(value, Mapping) or component not in value:
            return None
        value = value[component]
    return value


def _binding_is_present(manifest: Mapping[str, Any], binding: ImmutableBinding) -> bool:
    value = _lookup_manifest_path(manifest, binding.manifest_path)
    if binding.kind == "sha256":
        return (
            isinstance(value, str)
            and SHA256_RE.fullmatch(value) is not None
            and value != "0" * 64
        )
    raise WitnessLeasePhaseBridgeError("immutable binding kind is unsupported")


def role_transport_contracts() -> list[dict[str, Any]]:
    """Return the only transport shapes a future sealed apply may use."""

    rows = [
        {
            "role": "witness",
            "controller_transport": "ssh-control-object-storage-payload-only",
            "payload_transport": "object-storage-private-versioned-age",
            "ssh_application_payload_bytes": 0,
            "direct_ssh_file_transfer_allowed": False,
            "presigned_url_persisted": False,
            "required_result_readback": "exact-version-and-age-decrypt",
        },
        {
            "role": "webapp_fi",
            "controller_transport": "ssh-control",
            "payload_transport": "ssh-control-stdio-authority-only",
            "ssh_application_payload_bytes": "bounded-worker-request-only",
            "direct_ssh_file_transfer_allowed": False,
            "presigned_url_persisted": False,
            "required_result_readback": "root-only-create-only-worker-result",
        },
        {
            "role": "webapp_ir",
            "controller_transport": "ssh-control-object-storage-payload-only",
            "payload_transport": "object-storage-private-versioned-age",
            "ssh_application_payload_bytes": 0,
            "direct_ssh_file_transfer_allowed": False,
            "presigned_url_persisted": False,
            "required_result_readback": "exact-version-and-age-decrypt",
        },
    ]
    if tuple(row["role"] for row in rows) != ROLES:
        raise WitnessLeasePhaseBridgeError("transport role ordering differs")
    for row in rows:
        if set(row) != TRANSPORT_FIELDS:
            raise WitnessLeasePhaseBridgeError("transport contract fields differ")
        if row["role"] in {"witness", "webapp_ir"} and (
            row["payload_transport"] != "object-storage-private-versioned-age"
            or row["ssh_application_payload_bytes"] != 0
            or row["direct_ssh_file_transfer_allowed"] is not False
        ):
            raise WitnessLeasePhaseBridgeError("Object Storage role transport is unsafe")
    return rows


def required_attestation_inputs() -> list[dict[str, Any]]:
    rows = [
        {
            "role": "witness",
            "source": "fresh signed Witness attestation result object",
            "required_binding_ids": [
                "witness-control-receiver",
                "witness-control-receiver-policy",
                "witness-bootstrap-age-recipient",
                "witness-signed-attestation-worker",
                "witness-signed-attestation-dependency-closure",
                "witness-signed-attestation-policy",
                "controller-result-age-recipient",
                "object-storage-control-result-recovery-contract",
            ],
            "payload_transport": "object-storage-private-versioned-age",
            "required_properties": [
                "exact Object Storage VersionId readback",
                "age decrypt with controller root-only identity",
                "fresh signed lease/status proof",
                "no direct SSH file payload",
            ],
        },
        {
            "role": "webapp_fi",
            "source": "exact-release Witness lease worker result",
            "required_binding_ids": [
                "webapp-fi-witness-lease-worker",
                "webapp-fi-witness-lease-role-material",
                "webapp-fi-witness-lease-dependency-closure",
                "webapp-fi-control-ssh-trust",
            ],
            "payload_transport": "ssh-control-stdio-authority-only",
            "required_properties": [
                "journal before Witness mutation",
                "fresh signed proof and authenticated status",
                "root-only create-only result",
                "business writes and app services remain forbidden",
            ],
        },
        {
            "role": "webapp_ir",
            "source": "exact-release fenced readback result object",
            "required_binding_ids": [
                "webapp-ir-control-receiver",
                "webapp-ir-control-receiver-policy",
                "webapp-ir-witness-lease-dependency-closure",
                "webapp-ir-bootstrap-age-recipient",
                "controller-result-age-recipient",
                "object-storage-control-result-recovery-contract",
            ],
            "payload_transport": "object-storage-private-versioned-age",
            "required_properties": [
                "request and result are private/versioned/age encrypted",
                "exact Object Storage VersionId readback",
                "age decrypt with controller root-only identity",
                "fenced non-holder state and no local lease",
            ],
        },
    ]
    if tuple(row["role"] for row in rows) != ROLES:
        raise WitnessLeasePhaseBridgeError("attestation role ordering differs")
    for row in rows:
        if set(row) != ATTESTATION_INPUT_FIELDS:
            raise WitnessLeasePhaseBridgeError("attestation input fields differ")
    return rows


def validate_context(manifest: Mapping[str, Any], manifest_sha256: str) -> BridgeContext:
    """Validate the controller contract without contacting any host."""

    if not isinstance(manifest, Mapping):
        raise WitnessLeasePhaseBridgeError("Witness lease manifest is unavailable")
    try:
        CONTROLLER.validate_manifest(dict(manifest))
    except CONTROLLER.CutoverContractError as exc:
        raise WitnessLeasePhaseBridgeError("Witness lease manifest is invalid") from exc
    if (
        not isinstance(manifest_sha256, str)
        or SHA256_RE.fullmatch(manifest_sha256) is None
        or manifest_sha256 == "0" * 64
    ):
        raise WitnessLeasePhaseBridgeError("Witness lease manifest digest is invalid")
    phase_spec = next(
        (item for item in CONTROLLER.PHASE_SPECS if item.phase == PHASE),
        None,
    )
    if phase_spec is None or phase_spec.operation != OPERATION or phase_spec.roles != ROLES:
        raise WitnessLeasePhaseBridgeError("Witness lease phase contract differs")
    topology = manifest["topology"]
    for role, expected_transport in (
        ("witness", "ssh-control-object-storage-payload-only"),
        ("webapp_fi", "ssh-control"),
        ("webapp_ir", "ssh-control-object-storage-payload-only"),
    ):
        if topology[role]["transport"] != expected_transport:
            raise WitnessLeasePhaseBridgeError(f"{role} transport differs")
    return BridgeContext(manifest=dict(manifest), manifest_sha256=manifest_sha256)


def build_plan(context: BridgeContext) -> dict[str, Any]:
    if not isinstance(context, BridgeContext):
        raise WitnessLeasePhaseBridgeError("Witness lease bridge context is invalid")
    manifest = context.manifest
    bindings = [binding.document() for binding in REQUIRED_IMMUTABLE_BINDINGS]
    missing = [
        binding.identifier
        for binding in REQUIRED_IMMUTABLE_BINDINGS
        if not _binding_is_present(manifest, binding)
    ]
    document = {
        "schema": PLAN_SCHEMA,
        "status": "blocked",
        "phase": PHASE,
        "operation": OPERATION,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "role_transport_contracts": role_transport_contracts(),
        "required_immutable_bindings": bindings,
        "missing_immutable_bindings": missing,
        "required_attestation_inputs": required_attestation_inputs(),
        "apply_supported": False,
        "direct_witness_ssh_file_transfer_allowed": False,
        "production_contacted": False,
        "journal_mutated": False,
        "current_mutated": False,
        "service_mutated": False,
        "volume_mutated": False,
        "object_storage_mutated": False,
    }
    if set(document) != PLAN_FIELDS:
        raise WitnessLeasePhaseBridgeError("Witness lease bridge plan fields differ")
    return document


def apply(context: BridgeContext, *, confirm: str, invoker: Any | None = None) -> None:
    """Fail closed until the required immutable manifest extension is sealed.

    ``invoker`` is intentionally accepted only so callers cannot accidentally
    hide a direct transport implementation behind this planning interface.  It
    is never inspected or invoked in the unsealed state.
    """

    del context, confirm, invoker
    raise WitnessLeasePhaseBridgeError(
        "Witness lease apply is disabled until the immutable Object Storage "
        "receiver, recipient, provider-capability and Witness-attestation "
        "bindings are sealed into the controller manifest"
    )


def load_context(manifest_path: Path) -> BridgeContext:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise WitnessLeasePhaseBridgeError("Witness lease bridge must run as root:root")
    try:
        manifest, manifest_sha256 = CONTROLLER.read_root_only_manifest(manifest_path)
    except CONTROLLER.CutoverContractError as exc:
        raise WitnessLeasePhaseBridgeError("Witness lease manifest cannot be read securely") from exc
    return validate_context(manifest, manifest_sha256)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        context = load_context(args.manifest)
        if args.apply:
            apply(context, confirm=str(args.confirm or ""))
        elif args.confirm is not None:
            raise WitnessLeasePhaseBridgeError("plan mode does not accept confirmation")
        payload = build_plan(context)
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 2
    except WitnessLeasePhaseBridgeError as exc:
        print(
            json.dumps(
                {
                    "schema": ERROR_SCHEMA,
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "production_contacted": False,
                    "journal_mutated": False,
                    "object_storage_mutated": False,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
