#!/usr/bin/env python3
"""Assemble four canonical redacted blob receipts into one local reference.

This is a deliberately narrow controller-side bridge.  It accepts only the
four already-redacted canonical collector-input payloads defined by
``production_shadow_convergence_blob_roundtrip_collector``.  Each payload is
bound to the caller-supplied exact release identity before the existing pure
blob reducer accepts it.  The resulting observation is installed only through
the existing create-only reference contract.

The module never opens collector-input paths, contacts Object Storage or a
peer, invokes a subprocess, or changes source-set/gate/observer state.  A
future trusted receiver must obtain the four payload bytes independently; this
module must not be used to claim that such a receiver or collector exists.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts import production_shadow_convergence_blob_roundtrip as BLOB
from scripts import production_shadow_convergence_blob_roundtrip_collector as COLLECTOR
from scripts import production_shadow_convergence_blob_roundtrip_reference as REFERENCE


REQUIRED_INPUT_KEYS = tuple(
    (source_site, target_site, role)
    for source_site, target_site in BLOB.PAIRS
    for role in (source_site, target_site)
)


class BlobRoundtripAssemblerError(RuntimeError):
    """Canonical collector inputs cannot safely become local evidence."""


def _identity(value: Mapping[str, Any]) -> dict[str, str]:
    try:
        return BLOB._identity_from_mapping(value)  # noqa: SLF001
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripAssemblerError("blob assembler identity differs") from exc


def _payloads(value: Sequence[bytes]) -> Sequence[bytes]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise BlobRoundtripAssemblerError("blob collector input payload collection is invalid")
    if len(value) != len(REQUIRED_INPUT_KEYS):
        raise BlobRoundtripAssemblerError("blob collector input coverage is incomplete")
    return value


def _input_key(document: Mapping[str, Any]) -> tuple[str, str, str]:
    source_site = document.get("source_site")
    target_site = document.get("target_site")
    role = document.get("role")
    if not all(isinstance(item, str) for item in (source_site, target_site, role)):
        raise BlobRoundtripAssemblerError("blob collector input identity is invalid")
    return source_site, target_site, role


def assemble_observation(
    *,
    collector_input_payloads: Sequence[bytes],
    identity: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Reduce exactly four canonical exact-release receipts to one observation.

    No payload is read from a filesystem or persisted here.  The only accepted
    input is canonical ASCII bytes from the established redacted collector
    contract, one for every directed pair and every role in that pair.
    """

    bound_identity = _identity(identity)
    payloads = _payloads(collector_input_payloads)
    expected = set(REQUIRED_INPUT_KEYS)
    proofs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for payload in payloads:
        try:
            document = COLLECTOR.parse_collector_input_payload(payload)
        except COLLECTOR.BlobRoundtripCollectorContractError as exc:
            raise BlobRoundtripAssemblerError(
                "blob collector input payload is not canonical"
            ) from exc
        key = _input_key(document)
        if key not in expected or key in proofs:
            raise BlobRoundtripAssemblerError("blob collector input coverage differs")
        source_site, target_site, role = key
        try:
            proofs[key] = COLLECTOR.build_role_proof(
                document,
                identity=bound_identity,
                source_site=source_site,
                target_site=target_site,
                role=role,
                now=now,
            )
        except COLLECTOR.BlobRoundtripCollectorContractError as exc:
            raise BlobRoundtripAssemblerError("blob collector input is invalid") from exc
    if set(proofs) != expected:
        raise BlobRoundtripAssemblerError("blob collector input coverage differs")
    try:
        return BLOB.build_observation(
            **bound_identity,
            role_proofs=[proofs[key] for key in REQUIRED_INPUT_KEYS],
            now=now,
        )
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripAssemblerError("blob collector inputs are not gate-compatible") from exc


def assemble_and_install(
    *,
    collector_input_payloads: Sequence[bytes],
    evidence_root: Path,
    identity: Mapping[str, Any],
    now: datetime,
) -> tuple[REFERENCE.BlobRoundtripObservationReference, str]:
    """Assemble canonical bytes and install only through the reference contract."""

    bound_identity = _identity(identity)
    observation = assemble_observation(
        collector_input_payloads=collector_input_payloads,
        identity=bound_identity,
        now=now,
    )
    try:
        return REFERENCE.install_observation(
            observation,
            evidence_root=evidence_root,
            identity=bound_identity,
            now=now,
        )
    except REFERENCE.BlobRoundtripObservationReferenceError as exc:
        raise BlobRoundtripAssemblerError("blob observation reference installation failed") from exc
