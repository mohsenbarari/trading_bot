"""Pinned SSH retrieval of the one selector-free Witness evidence envelope.

This is intentionally a different adapter from the normal receipt SSH
adapter.  It accepts only the controller's ``webapp_ir`` request, contacts
only the separately source-pinned Witness identity, invokes one literal
remote command, verifies the dual-signed evidence with locally root-pinned
public material, and returns only the existing inner v2 WA-IR receipt.

It has no WA-IR network route, Object Storage client, ingress, signer, age
identity, generic SSH command, shell, or runtime configuration file reader.
The concrete process runner and fixed root-owned verifier configuration are
separate boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import Any, Protocol

from core import dedicated_host_preflight_ir_witness_attestation as _attestation
from core import dedicated_host_preflight_pinned_ssh_delivery as _ssh
from core.dedicated_host_preflight_controller import (
    AGENT_DELIVERY_RESPONSE_SCHEMA,
    DELIVERY_CONTRACT_BY_ROLE,
    RECEIPT_PATH_BY_ROLE,
    DedicatedHostTarget,
)
from core.dedicated_host_preflight_receipt import MAX_RECEIPT_BYTES, canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, READONLY_REQUEST_SCHEMA


__all__ = (
    "FIXED_WITNESS_EVIDENCE_REMOTE_COMMAND",
    "FIXED_WITNESS_EVIDENCE_SSH_USER",
    "MAX_WITNESS_EVIDENCE_BYTES",
    "PINNED_SSH_WITNESS_EVIDENCE_DELIVERY_DEFAULT_ENABLED",
    "PINNED_SSH_WITNESS_EVIDENCE_DELIVERY_SCHEMA",
    "DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError",
    "PinnedSshWitnessEvidenceAgentDelivery",
    "PinnedSshWitnessEvidenceDeliveryConfig",
    "PinnedSshWitnessEvidenceInvocation",
    "PinnedSshWitnessEvidenceRunner",
    "PinnedSshWitnessEvidenceRunnerResult",
    "validate_witness_evidence_ssh_invocation",
)


PINNED_SSH_WITNESS_EVIDENCE_DELIVERY_SCHEMA = (
    "three-site-dedicated-host-preflight-pinned-ssh-witness-evidence-delivery-v1"
)
PINNED_SSH_WITNESS_EVIDENCE_DELIVERY_DEFAULT_ENABLED = False

FIXED_WITNESS_EVIDENCE_SSH_USER = "preflight-witness-evidence"
FIXED_WITNESS_EVIDENCE_REMOTE_COMMAND = "collect-wa-ir-witness-preflight-evidence"
MAX_WITNESS_EVIDENCE_BYTES = _attestation.MAX_WA_IR_WITNESS_ATTESTATION_BYTES * 2

_IR_ROLE = "webapp_ir"
_WITNESS_ROLE = "witness"
_IR_ROUTE = "witness-dual-signed-preflight-evidence"
_IR_PHASE = FIXED_WITNESS_EVIDENCE_REMOTE_COMMAND
_FIXED_PORT = 22
_FIXED_CONNECT_TIMEOUT_SECONDS = 5
_EXPECTED_STDIN = b""
_CAPABILITY = object()


class DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(ValueError):
    """A fixed-code refusal from the central Witness-evidence read adapter."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PinnedSshWitnessEvidenceDeliveryConfig:
    """Root-provisioned public verification pins; no host/command selector."""

    expected_request: _attestation.ParsedWaIrWitnessAttestationRequest | None = None
    expected_wa_ir_public_key: bytes = b""
    expected_witness_public_key: bytes = b""
    enabled: bool = PINNED_SSH_WITNESS_EVIDENCE_DELIVERY_DEFAULT_ENABLED
    connect_timeout_seconds: int = _FIXED_CONNECT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class PinnedSshWitnessEvidenceInvocation:
    """One immutable no-input SSH invocation for the Witness evidence read."""

    ssh_binary: Path
    arguments: tuple[str, ...]
    stdin_bytes: bytes
    environment: tuple[tuple[str, str], ...]
    known_hosts: Path
    identity_file: Path
    witness_host_key_sha256: str
    expected_readonly_request_sha256: str


@dataclass(frozen=True)
class PinnedSshWitnessEvidenceRunnerResult:
    """The runner exposes only exit status and bounded evidence stdout."""

    exit_code: int
    stdout_bytes: bytes


class PinnedSshWitnessEvidenceRunner(Protocol):
    async def run(
        self,
        *,
        invocation: PinnedSshWitnessEvidenceInvocation,
    ) -> PinnedSshWitnessEvidenceRunnerResult: ...


@dataclass(frozen=True)
class _Facts:
    request: _attestation.ParsedWaIrWitnessAttestationRequest
    wa_ir_public_key: bytes
    witness_public_key: bytes


@dataclass(frozen=True)
class _IrTargetFacts:
    host_key_sha256: str


@dataclass(frozen=True)
class _WitnessTargetFacts:
    host_key_sha256: str
    public_ipv4: str


def _fail(code: str) -> None:
    raise DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(code)


def _validated_request(value: object, *, code: str) -> _attestation.ParsedWaIrWitnessAttestationRequest:
    if type(value) is not _attestation.ParsedWaIrWitnessAttestationRequest:
        _fail(code)
    try:
        parsed = _attestation.parse_wa_ir_witness_attestation_request(value.canonical_request)
    except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
        raise DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(code) from exc
    if parsed != value:
        _fail(code)
    return parsed


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, TypeError, ValueError):
        _fail(code)
    return value


def _config_facts(value: object) -> _Facts:
    if type(value) is not PinnedSshWitnessEvidenceDeliveryConfig:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_DISABLED")
    if type(value.connect_timeout_seconds) is not int or value.connect_timeout_seconds != _FIXED_CONNECT_TIMEOUT_SECONDS:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_CONFIG_INVALID")
    try:
        root = os.geteuid() == 0
    except OSError:
        root = False
    if not root:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_ROOT_RUNTIME_REQUIRED")
    return _Facts(
        request=_validated_request(
            value.expected_request,
            code="PINNED_SSH_WITNESS_EVIDENCE_CONFIG_INVALID",
        ),
        wa_ir_public_key=_public_key(
            value.expected_wa_ir_public_key,
            code="PINNED_SSH_WITNESS_EVIDENCE_CONFIG_INVALID",
        ),
        witness_public_key=_public_key(
            value.expected_witness_public_key,
            code="PINNED_SSH_WITNESS_EVIDENCE_CONFIG_INVALID",
        ),
    )


def _ir_target(value: object) -> _IrTargetFacts:
    if type(value) is not DedicatedHostTarget or value.role != _IR_ROLE:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_IR_TARGET_INVALID")
    expected = EXPECTED_HOSTS[_IR_ROLE]
    if (
        value.instance_id != expected["instance_id"]
        or value.public_ipv4 != expected["public_ip"]
        or value.region != expected["region"]
        or value.delivery_route != _IR_ROUTE
        or value.delivery_phase != _IR_PHASE
    ):
        _fail("PINNED_SSH_WITNESS_EVIDENCE_IR_TARGET_INVALID")
    try:
        host_key_sha256 = _ssh._sha256(  # type: ignore[attr-defined]
            value.host_key_sha256,
            code="PINNED_SSH_WITNESS_EVIDENCE_IR_TARGET_INVALID",
        )
    except _ssh.DedicatedHostPreflightPinnedSshDeliveryError as exc:
        raise DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(
            "PINNED_SSH_WITNESS_EVIDENCE_IR_TARGET_INVALID"
        ) from exc
    return _IrTargetFacts(host_key_sha256=host_key_sha256)


def _witness_target_facts(value: object) -> _WitnessTargetFacts:
    """Admit only the normal source-pinned Witness SSH identity."""

    try:
        facts = _ssh._target_facts(value)  # type: ignore[attr-defined]
    except Exception as exc:
        raise DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(
            "PINNED_SSH_WITNESS_EVIDENCE_WITNESS_TARGET_INVALID"
        ) from exc
    if facts.role != _WITNESS_ROLE:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_WITNESS_TARGET_INVALID")
    return _WitnessTargetFacts(
        host_key_sha256=facts.host_key_sha256,
        public_ipv4=facts.public_ipv4,
    )


def _validate_requested_receipt(
    *,
    request_bytes: object,
    request_sha256: object,
    receipt_path: object,
    facts: _Facts,
) -> None:
    if (
        type(request_bytes) is not bytes
        or request_bytes != facts.request.readonly_request_bytes
        or type(request_sha256) is not str
        or request_sha256 != facts.request.readonly_request_sha256
        or hashlib.sha256(request_bytes).hexdigest() != request_sha256
        or receipt_path != RECEIPT_PATH_BY_ROLE[_IR_ROLE]
    ):
        _fail("PINNED_SSH_WITNESS_EVIDENCE_REQUEST_INVALID")
    try:
        request_value = json_loads_canonical_readonly_request(request_bytes)
    except ValueError as exc:
        raise DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(
            "PINNED_SSH_WITNESS_EVIDENCE_REQUEST_INVALID"
        ) from exc
    if request_value != facts.request.readonly_request:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_REQUEST_INVALID")


def json_loads_canonical_readonly_request(raw: bytes) -> dict[str, str]:
    """Parse only the exact v2 request embedded in the verifier policy."""

    import json

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if type(key) is not str or key in result:
                raise ValueError("invalid request")
            result[key] = item
        return result

    def reject_constant(_: str) -> None:
        raise ValueError("invalid request")

    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid request") from exc
    if (
        type(value) is not dict
        or set(value) != {
            "schema",
            "campaign_id",
            "operation_id",
            "release_sha",
            "role",
            "manifest_sha256",
        }
        or value.get("schema") != READONLY_REQUEST_SCHEMA
        or value.get("role") != _IR_ROLE
        or any(type(item) is not str for item in value.values())
        or canonical_json_bytes(value) + b"\n" != raw
    ):
        raise ValueError("invalid request")
    return dict(value)


def _arguments(*, ssh_binary: Path, known_hosts: Path, identity_file: Path, witness: _WitnessTargetFacts) -> tuple[str, ...]:
    return (
        str(ssh_binary),
        "-F",
        "/dev/null",
        "-i",
        str(identity_file),
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "ChallengeResponseAuthentication=no",
        "-o",
        "PubkeyAuthentication=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + str(known_hosts),
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "HashKnownHosts=no",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "RequestTTY=no",
        "-o",
        "ConnectTimeout=" + str(_FIXED_CONNECT_TIMEOUT_SECONDS),
        "-o",
        "ConnectionAttempts=1",
        "-p",
        str(_FIXED_PORT),
        FIXED_WITNESS_EVIDENCE_SSH_USER + "@" + witness.public_ipv4,
        FIXED_WITNESS_EVIDENCE_REMOTE_COMMAND,
    )


def _read_local_ssh_paths(witness: _WitnessTargetFacts) -> tuple[Path, Path, Path]:
    """Reuse the existing root-owned known-hosts/identity checks verbatim."""

    route, phase = DELIVERY_CONTRACT_BY_ROLE[_WITNESS_ROLE]
    expected = EXPECTED_HOSTS[_WITNESS_ROLE]
    target = DedicatedHostTarget(
        role=_WITNESS_ROLE,
        instance_id=expected["instance_id"],
        public_ipv4=expected["public_ip"],
        region=expected["region"],
        host_key_sha256=witness.host_key_sha256,
        delivery_route=route,
        delivery_phase=phase,
    )
    try:
        target_facts = _ssh._target_facts(target)  # type: ignore[attr-defined]
        known_hosts = _ssh._known_hosts_for_target(target_facts)  # type: ignore[attr-defined]
        identity_file = _ssh._identity_file()  # type: ignore[attr-defined]
        ssh_binary = _ssh._validate_ssh_binary()  # type: ignore[attr-defined]
    except Exception as exc:
        raise DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(
            "PINNED_SSH_WITNESS_EVIDENCE_LOCAL_SSH_PIN_INVALID"
        ) from exc
    return ssh_binary, known_hosts, identity_file


def validate_witness_evidence_ssh_invocation(
    invocation: object,
) -> tuple[Path, Path, Path, _WitnessTargetFacts]:
    """Re-derive the only process invocation a concrete runner may execute."""

    if type(invocation) is not PinnedSshWitnessEvidenceInvocation:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_INVOCATION_INVALID")
    if (
        invocation.stdin_bytes != _EXPECTED_STDIN
        or invocation.environment != ()
        or type(invocation.witness_host_key_sha256) is not str
        or type(invocation.expected_readonly_request_sha256) is not str
    ):
        _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_INVOCATION_INVALID")
    try:
        host_key_sha256 = _ssh._sha256(  # type: ignore[attr-defined]
            invocation.witness_host_key_sha256,
            code="PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_INVOCATION_INVALID",
        )
        request_sha256 = _ssh._sha256(  # type: ignore[attr-defined]
            invocation.expected_readonly_request_sha256,
            code="PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_INVOCATION_INVALID",
        )
    except _ssh.DedicatedHostPreflightPinnedSshDeliveryError as exc:
        raise DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(
            "PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_INVOCATION_INVALID"
        ) from exc
    # A root process runner cannot reconstruct the central public verifier
    # policy, but it can prove the invocation carries a non-empty canonical
    # digest and has no stdin/selector.  The adapter independently checks the
    # exact digest against that policy before constructing this object.
    if request_sha256 == "0" * 64:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_INVOCATION_INVALID")
    witness = _WitnessTargetFacts(
        host_key_sha256=host_key_sha256,
        public_ipv4=EXPECTED_HOSTS[_WITNESS_ROLE]["public_ip"],
    )
    ssh_binary, known_hosts, identity_file = _read_local_ssh_paths(witness)
    expected_arguments = _arguments(
        ssh_binary=ssh_binary,
        known_hosts=known_hosts,
        identity_file=identity_file,
        witness=witness,
    )
    if (
        invocation.ssh_binary != ssh_binary
        or invocation.known_hosts != known_hosts
        or invocation.identity_file != identity_file
        or invocation.arguments != expected_arguments
    ):
        _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_INVOCATION_INVALID")
    return ssh_binary, known_hosts, identity_file, witness


class PinnedSshWitnessEvidenceAgentDelivery:
    """Read WA-IR evidence only through the literal Witness endpoint."""

    def __init__(
        self,
        *,
        config: PinnedSshWitnessEvidenceDeliveryConfig = PinnedSshWitnessEvidenceDeliveryConfig(),
        witness_target: DedicatedHostTarget | None = None,
        runner: PinnedSshWitnessEvidenceRunner | None = None,
    ) -> None:
        self._config = config
        self._witness_target = witness_target
        self._runner = runner

    async def collect_readonly_receipt(
        self,
        *,
        target: DedicatedHostTarget,
        request_bytes: bytes,
        request_sha256: str,
        receipt_path: str,
    ) -> Mapping[str, Any]:
        facts = _config_facts(self._config)
        ir_target = _ir_target(target)
        witness = _witness_target_facts(self._witness_target)
        _validate_requested_receipt(
            request_bytes=request_bytes,
            request_sha256=request_sha256,
            receipt_path=receipt_path,
            facts=facts,
        )
        if self._runner is None or not callable(getattr(self._runner, "run", None)):
            _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNNER_REQUIRED")
        ssh_binary, known_hosts, identity_file = _read_local_ssh_paths(witness)
        invocation = PinnedSshWitnessEvidenceInvocation(
            ssh_binary=ssh_binary,
            arguments=_arguments(
                ssh_binary=ssh_binary,
                known_hosts=known_hosts,
                identity_file=identity_file,
                witness=witness,
            ),
            stdin_bytes=_EXPECTED_STDIN,
            environment=(),
            known_hosts=known_hosts,
            identity_file=identity_file,
            witness_host_key_sha256=witness.host_key_sha256,
            expected_readonly_request_sha256=facts.request.readonly_request_sha256,
        )
        try:
            result = await self._runner.run(invocation=invocation)
        except Exception:
            _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNNER_FAILED")
        if (
            type(result) is not PinnedSshWitnessEvidenceRunnerResult
            or type(result.exit_code) is not int
            or result.exit_code != 0
            or type(result.stdout_bytes) is not bytes
            or not 1 <= len(result.stdout_bytes) <= MAX_WITNESS_EVIDENCE_BYTES
        ):
            _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNNER_FAILED")
        try:
            verified = _attestation.verify_witness_preflight_evidence(
                canonical_evidence=result.stdout_bytes,
                expected_request=facts.request,
                expected_wa_ir_public_key=facts.wa_ir_public_key,
                expected_witness_public_key=facts.witness_public_key,
                now=datetime.now(timezone.utc),
            )
        except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
            raise DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError(
                "PINNED_SSH_WITNESS_EVIDENCE_INVALID"
            ) from exc
        raw_receipt = verified.canonical_receipt
        if not 1 <= len(raw_receipt) <= MAX_RECEIPT_BYTES:
            _fail("PINNED_SSH_WITNESS_EVIDENCE_INVALID")
        return {
            "schema": AGENT_DELIVERY_RESPONSE_SCHEMA,
            "role": _IR_ROLE,
            "delivery_route": _IR_ROUTE,
            "delivery_phase": _IR_PHASE,
            # This remains the source-pinned WA-IR target key pin.  The
            # separately validated Witness transport pin is never presented
            # as a WA-IR host key and is bound before the SSH process starts.
            "host_key_sha256": ir_target.host_key_sha256,
            "request_sha256": facts.request.readonly_request_sha256,
            "receipt_path": RECEIPT_PATH_BY_ROLE[_IR_ROLE],
            "receipt_sha256": hashlib.sha256(raw_receipt).hexdigest(),
            "receipt_bytes": raw_receipt,
        }
