#!/usr/bin/env python3
"""Verify fresh FI+IR non-secret Writer Witness client attestations together.

Both WebApp sites must first run ``attest_writer_witness_client.py`` locally.
This root-only verifier never reads an HMAC secret, never contacts the Witness,
and does not activate a service.  It only accepts two freshly observed,
TLS-verified receipts whose signed Witness responses bind the exact
control-profile hashes and one root-controlled exact-current credential/trust
rotation policy.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import attest_writer_witness_client as client_attestation  # noqa: E402
from scripts import prepare_writer_witness_immutable_release as control  # noqa: E402
from scripts import writer_witness_rotation_lifecycle as lifecycle  # noqa: E402


PAIRED_ATTESTATION_SCHEMA = "gold-trade-writer-witness-paired-live-attestation-v3"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
POLICY_ID_RE = lifecycle.POLICY_ID_RE
GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DEFAULT_ROTATION_STATE_DIRECTORY = lifecycle.DEFAULT_STATE_DIRECTORY
RECEIPT_FIELDS = {
    "schema",
    "status",
    "site",
    "mode",
    "observed_at",
    "request_id",
    "tls_verified",
    "witness_endpoint_sha256",
    "ca_bundle_sha256",
    "pinned_witness_public_key",
    "runtime_profile_sha256",
    "release_manifest_sha256",
    "profile",
    "witness_attestation",
}


class WriterWitnessPairAttestationError(RuntimeError):
    """The two local proofs do not establish one safe Witness contract."""


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise WriterWitnessPairAttestationError(
            "Writer Witness paired attestation must run as root"
        )


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_receipt(path: Path, *, field: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = control._read_controlled_file(path, field=field, root_only=True)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=client_attestation._strict_object)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        control.WitnessReleasePreparationError,
        client_attestation.WriterWitnessClientAttestationError,
    ) as exc:
        raise WriterWitnessPairAttestationError(f"{field} is invalid") from exc
    if not isinstance(value, dict) or set(value) != RECEIPT_FIELDS:
        raise WriterWitnessPairAttestationError(f"{field} schema is invalid")
    return raw, value


def _parse_time(value: object, *, field: str) -> datetime:
    try:
        return client_attestation._parse_time(value, field=field)
    except client_attestation.WriterWitnessClientAttestationError as exc:
        raise WriterWitnessPairAttestationError(str(exc)) from exc


def _profile_sha256(profile: Mapping[str, Any]) -> str:
    """Hash the validated, semantic control profile rather than file formatting."""

    return hashlib.sha256(control._canonical_json_bytes(profile)).hexdigest()


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise WriterWitnessPairAttestationError(f"{field} is invalid")
    return value


def _require_policy_id(value: object) -> str:
    if not isinstance(value, str) or not POLICY_ID_RE.fullmatch(value):
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy id is invalid"
        )
    return value


def _require_generation(value: object, *, site: str) -> str:
    if not isinstance(value, str) or not GENERATION_RE.fullmatch(value):
        raise WriterWitnessPairAttestationError(
            f"Writer Witness credential rotation {site} generation is invalid"
        )
    return value


def _normalise_time(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise WriterWitnessPairAttestationError(f"{field} is invalid")
    return value.astimezone(timezone.utc)


def _load_rotation_policy(
    raw: bytes,
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate exact immutable policy bytes selected by the lifecycle.

    This function intentionally does not open a caller-selected policy path.
    The lifecycle resolves the committed current selector first, then supplies
    the hash-bound immutable bytes observed through its secure root-only read.
    """

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=client_attestation._strict_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        client_attestation.WriterWitnessClientAttestationError,
    ) as exc:
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy is invalid"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "policy_id",
        "issued_at",
        "not_before",
        "not_after",
        "profile",
        "witness_trust",
        "clients",
    }:
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy schema is invalid"
        )
    if (
        payload.get("schema") != control.CREDENTIAL_ROTATION_POLICY_SCHEMA
        or payload.get("schema") != lifecycle.POLICY_SCHEMA
    ):
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy schema is unsupported"
        )
    if raw != control._canonical_json_bytes(payload) + bytes((10,)):
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy is not canonical"
        )
    policy_id = _require_policy_id(payload.get("policy_id"))
    issued_at = _parse_time(
        payload.get("issued_at"),
        field="Writer Witness credential rotation policy issue time",
    )
    not_before = _parse_time(
        payload.get("not_before"),
        field="Writer Witness credential rotation policy not_before",
    )
    not_after = _parse_time(
        payload.get("not_after"),
        field="Writer Witness credential rotation policy not_after",
    )
    maximum_policy_ttl_seconds = profile["client_credential_rotation"][
        "maximum_policy_ttl_seconds"
    ]
    if (
        not_before != issued_at
        or not_after <= not_before
        or not_after - issued_at
        > timedelta(seconds=maximum_policy_ttl_seconds)
    ):
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy TTL is invalid"
        )
    profile_binding = payload.get("profile")
    if not isinstance(profile_binding, dict) or set(profile_binding) != {
        "release_id",
        "source_commit",
        "source_runtime_profile_sha256",
        "source_release_manifest_sha256",
        "profile_sha256",
    }:
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation profile binding is invalid"
        )
    expected_profile_binding = {
        "release_id": profile["release_id"],
        "source_commit": profile["source_commit"],
        "source_runtime_profile_sha256": profile["source_runtime_profile_sha256"],
        "source_release_manifest_sha256": profile["source_release_manifest_sha256"],
        "profile_sha256": _profile_sha256(profile),
    }
    if profile_binding != expected_profile_binding:
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy is not bound to the trusted control profile"
        )
    witness_trust = payload.get("witness_trust")
    if not isinstance(witness_trust, dict) or set(witness_trust) != {
        "witness_endpoint_sha256",
        "ca_bundle_sha256",
        "witness_public_key_sha256",
    }:
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation trust binding is invalid"
        )
    for key, value in witness_trust.items():
        _require_sha256(value, field=f"Writer Witness credential rotation {key}")
    clients = payload.get("clients")
    if not isinstance(clients, dict) or set(clients) != {"webapp_fi", "webapp_ir"}:
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation client allowlist is invalid"
        )
    parsed_clients: dict[str, dict[str, Any]] = {}
    seen_key_hashes: set[str] = set()
    for site in ("webapp_fi", "webapp_ir"):
        client = clients.get(site)
        if not isinstance(client, dict) or set(client) != {
            "site",
            "key_id_sha256",
            "generation",
        }:
            raise WriterWitnessPairAttestationError(
                f"Writer Witness credential rotation {site} allowlist entry is invalid"
            )
        if client.get("site") != site:
            raise WriterWitnessPairAttestationError(
                f"Writer Witness credential rotation {site} site binding is invalid"
            )
        key_hash = _require_sha256(
            client.get("key_id_sha256"),
            field=f"Writer Witness credential rotation {site} key id hash",
        )
        if key_hash in seen_key_hashes:
            raise WriterWitnessPairAttestationError(
                "Writer Witness credential rotation clients must use distinct identities"
            )
        seen_key_hashes.add(key_hash)
        parsed_clients[site] = {
            "key_id_sha256": key_hash,
            "generation": _require_generation(client.get("generation"), site=site),
        }
    return {
        "policy_id": policy_id,
        "issued_at": issued_at,
        "not_before": not_before,
        "not_after": not_after,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "witness_trust": dict(witness_trust),
        "clients": parsed_clients,
    }


def _validate_receipt_baseline(
    *,
    receipt: Mapping[str, Any],
    profile: Mapping[str, Any],
    expected_client_name: str,
    verification_time: datetime,
    maximum_age_seconds: int,
    expected_key_id_sha256: str | None,
) -> dict[str, Any]:
    """Validate a fresh local receipt before applying the rotation allowlist.

    The policy-creation ceremony uses this same baseline check with no
    pre-existing key allowlist.  It can therefore derive public hashes only
    from receipts whose signatures, site binding, freshness, and release
    binding have already been verified.
    """

    client = profile[expected_client_name]
    site = client["site"]
    label = "WebApp-FI" if site == "webapp_fi" else "WebApp-IR"
    if (
        receipt.get("schema") != client_attestation.CLIENT_ATTESTATION_SCHEMA
        or receipt.get("status") != "attested"
        or receipt.get("site") != site
        or receipt.get("mode") != client["mode"]
        or receipt.get("tls_verified") is not True
    ):
        raise WriterWitnessPairAttestationError(f"{label} live attestation identity is invalid")
    observed_at = _parse_time(receipt.get("observed_at"), field=f"{label} observation time")
    if observed_at > verification_time + timedelta(seconds=5) or verification_time - observed_at > timedelta(
        seconds=maximum_age_seconds
    ):
        raise WriterWitnessPairAttestationError(f"{label} live attestation is stale")
    request_id = receipt.get("request_id")
    if not isinstance(request_id, str) or not client_attestation.REQUEST_ID_RE.fullmatch(request_id):
        raise WriterWitnessPairAttestationError(f"{label} attestation nonce is invalid")
    for key in (
        "witness_endpoint_sha256",
        "ca_bundle_sha256",
        "runtime_profile_sha256",
        "release_manifest_sha256",
    ):
        value = receipt.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise WriterWitnessPairAttestationError(f"{label} {key} is invalid")
    if (
        receipt["runtime_profile_sha256"] != profile["source_runtime_profile_sha256"]
        or receipt["release_manifest_sha256"] != profile["source_release_manifest_sha256"]
        or receipt.get("profile") != profile["witness"]
    ):
        raise WriterWitnessPairAttestationError(
            f"{label} profile or release manifest does not match the control profile"
        )
    pinned_public_key = receipt.get("pinned_witness_public_key")
    try:
        public_key_bytes = client_attestation._decode_public_key(
            pinned_public_key,
            field=f"{label} pinned Witness public key",
        )
    except client_attestation.WriterWitnessClientAttestationError as exc:
        raise WriterWitnessPairAttestationError(str(exc)) from exc
    witness_payload = receipt.get("witness_attestation")
    if not isinstance(witness_payload, dict):
        raise WriterWitnessPairAttestationError(f"{label} signed Witness attestation is invalid")
    try:
        verified_witness = client_attestation._validate_witness_attestation(
            payload=witness_payload,
            expected_profile=profile,
            expected_public_key=pinned_public_key,
            expected_site=site,
            expected_key_id_sha256=expected_key_id_sha256,
            request_id=request_id,
            now=observed_at,
            timeout_seconds=10.0,
        )
    except client_attestation.WriterWitnessClientAttestationError as exc:
        raise WriterWitnessPairAttestationError(str(exc)) from exc
    if (
        verified_witness["runtime_profile_sha256"] != receipt["runtime_profile_sha256"]
        or verified_witness["release_manifest_sha256"] != receipt["release_manifest_sha256"]
        or verified_witness["profile"] != receipt["profile"]
    ):
        raise WriterWitnessPairAttestationError(
            f"{label} receipt duplicates do not match the signed Witness response"
        )
    return {
        "site": site,
        "mode": client["mode"],
        "request_id": request_id,
        "observed_at": observed_at,
        "witness_endpoint_sha256": receipt["witness_endpoint_sha256"],
        "ca_bundle_sha256": receipt["ca_bundle_sha256"],
        "pinned_witness_public_key": pinned_public_key,
        "witness_public_key_sha256": hashlib.sha256(public_key_bytes).hexdigest(),
        "caller_key_id_sha256": verified_witness["caller_key_id_sha256"],
        "witness_time": verified_witness["witness_time"],
    }


def _validate_one(
    *,
    receipt: Mapping[str, Any],
    profile: Mapping[str, Any],
    rotation_policy: Mapping[str, Any],
    expected_client_name: str,
    verification_time: datetime,
    maximum_age_seconds: int,
) -> dict[str, Any]:
    client = profile[expected_client_name]
    site = client["site"]
    label = "WebApp-FI" if site == "webapp_fi" else "WebApp-IR"
    allowed_client = rotation_policy["clients"][site]
    if (
        verification_time < rotation_policy["not_before"]
        or verification_time >= rotation_policy["not_after"]
    ):
        raise WriterWitnessPairAttestationError(
            f"{label} exact-current credential rotation policy is not active"
        )
    verified = _validate_receipt_baseline(
        receipt=receipt,
        profile=profile,
        expected_client_name=expected_client_name,
        verification_time=verification_time,
        maximum_age_seconds=maximum_age_seconds,
        expected_key_id_sha256=allowed_client["key_id_sha256"],
    )
    witness_trust = rotation_policy["witness_trust"]
    if (
        verified["witness_endpoint_sha256"]
        != witness_trust["witness_endpoint_sha256"]
        or verified["ca_bundle_sha256"] != witness_trust["ca_bundle_sha256"]
        or verified["witness_public_key_sha256"]
        != witness_trust["witness_public_key_sha256"]
    ):
        raise WriterWitnessPairAttestationError(
            f"{label} receipt does not match the root-controlled Witness trust binding"
        )
    return {
        **verified,
        "credential_generation": allowed_client["generation"],
        "credential_not_after": rotation_policy["not_after"],
    }


def _require_one_paired_witness_contract(fi: Mapping[str, Any], ir: Mapping[str, Any]) -> None:
    if fi["request_id"] == ir["request_id"]:
        raise WriterWitnessPairAttestationError("FI and IR attestations must use distinct nonces")
    if fi["caller_key_id_sha256"] == ir["caller_key_id_sha256"]:
        raise WriterWitnessPairAttestationError(
            "FI and IR attestations must use distinct authenticated client identities"
        )
    for key in (
        "witness_endpoint_sha256",
        "ca_bundle_sha256",
        "pinned_witness_public_key",
        "witness_public_key_sha256",
    ):
        if fi[key] != ir[key]:
            raise WriterWitnessPairAttestationError(
                "FI and IR do not have one identical TLS/endpoint Witness trust binding"
            )


def verify_paired_attestations(
    *,
    webapp_fi_attestation_path: Path,
    webapp_ir_attestation_path: Path,
    _rotation_state_directory_for_test: Path | None = None,
    _profile_path_for_test: Path | None = None,
    _verification_time_for_test: datetime | None = None,
) -> dict[str, Any]:
    """Return a non-secret pair result only when both receipts are exact/fresh.

    Production callers cannot select a profile, state path, max-age, or
    verification clock.  The underscored substitutions exist solely for the
    unit-test API; the command-line entry point always uses the trusted,
    fixed paths and the local UTC clock.
    """

    _require_root_execution()
    profile = control._load_profile(_profile_path_for_test or control.DEFAULT_PROFILE_PATH)
    now = _normalise_time(
        _verification_time_for_test or datetime.now(timezone.utc),
        field="paired attestation verification time",
    )
    try:
        selected = lifecycle.resolve_current_policy(
            profile_sha256=_profile_sha256(profile),
            state_directory=_rotation_state_directory_for_test,
        )
    except lifecycle.WriterWitnessRotationLifecycleError as exc:
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation lifecycle is invalid"
        ) from exc
    rotation_policy = _load_rotation_policy(selected.policy_raw, profile=profile)
    if (
        rotation_policy["policy_id"] != selected.policy_id
        or rotation_policy["sha256"] != selected.policy_sha256
    ):
        raise WriterWitnessPairAttestationError(
            "Writer Witness current selector does not bind its exact policy"
        )
    if now < rotation_policy["issued_at"]:
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy issue time is in the future"
        )
    maximum_age_seconds = profile["client_credential_rotation"][
        "maximum_attestation_age_seconds"
    ]
    fi_raw, fi_receipt = _parse_receipt(
        webapp_fi_attestation_path,
        field="WebApp-FI client attestation",
    )
    ir_raw, ir_receipt = _parse_receipt(
        webapp_ir_attestation_path,
        field="WebApp-IR client attestation",
    )
    fi = _validate_one(
        receipt=fi_receipt,
        profile=profile,
        rotation_policy=rotation_policy,
        expected_client_name="webapp_fi_client",
        verification_time=now,
        maximum_age_seconds=maximum_age_seconds,
    )
    ir = _validate_one(
        receipt=ir_receipt,
        profile=profile,
        rotation_policy=rotation_policy,
        expected_client_name="webapp_ir_client",
        verification_time=now,
        maximum_age_seconds=maximum_age_seconds,
    )
    _require_one_paired_witness_contract(fi, ir)
    return {
        "schema": PAIRED_ATTESTATION_SCHEMA,
        "status": "verified",
        "verified_at": now.isoformat(),
        "release_id": profile["release_id"],
        "source_commit": profile["source_commit"],
        "source_runtime_profile_sha256": profile["source_runtime_profile_sha256"],
        "source_release_manifest_sha256": profile["source_release_manifest_sha256"],
        "witness_endpoint_sha256": fi["witness_endpoint_sha256"],
        "ca_bundle_sha256": fi["ca_bundle_sha256"],
        "witness_public_key": fi["pinned_witness_public_key"],
        "credential_rotation_policy": {
            "policy_id": rotation_policy["policy_id"],
            "sha256": rotation_policy["sha256"],
            "issued_at": rotation_policy["issued_at"].isoformat(),
            "not_before": rotation_policy["not_before"].isoformat(),
            "not_after": rotation_policy["not_after"].isoformat(),
            "sequence": selected.sequence,
            "selector_sha256": selected.selector_sha256,
            "activation_sha256": selected.activation_sha256,
        },
        "clients": {
            "webapp_fi": {
                "request_id": fi["request_id"],
                "observed_at": fi["observed_at"].isoformat(),
                "witness_time": fi["witness_time"],
                "caller_key_id_sha256": fi["caller_key_id_sha256"],
                "credential_generation": fi["credential_generation"],
                "credential_not_after": fi["credential_not_after"].isoformat(),
                "receipt_sha256": hashlib.sha256(fi_raw).hexdigest(),
            },
            "webapp_ir": {
                "request_id": ir["request_id"],
                "observed_at": ir["observed_at"].isoformat(),
                "witness_time": ir["witness_time"],
                "caller_key_id_sha256": ir["caller_key_id_sha256"],
                "credential_generation": ir["credential_generation"],
                "credential_not_after": ir["credential_not_after"].isoformat(),
                "receipt_sha256": hashlib.sha256(ir_raw).hexdigest(),
            },
        },
        "compatible": True,
    }


def _validated_pair_for_policy_creation(
    *,
    webapp_fi_attestation_path: Path,
    webapp_ir_attestation_path: Path,
    profile: Mapping[str, Any],
    verification_time: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a candidate pair before deriving any rotation-policy field."""

    maximum_age_seconds = profile["client_credential_rotation"][
        "maximum_attestation_age_seconds"
    ]
    _, fi_receipt = _parse_receipt(
        webapp_fi_attestation_path,
        field="WebApp-FI client attestation",
    )
    _, ir_receipt = _parse_receipt(
        webapp_ir_attestation_path,
        field="WebApp-IR client attestation",
    )
    fi = _validate_receipt_baseline(
        receipt=fi_receipt,
        profile=profile,
        expected_client_name="webapp_fi_client",
        verification_time=verification_time,
        maximum_age_seconds=maximum_age_seconds,
        expected_key_id_sha256=None,
    )
    ir = _validate_receipt_baseline(
        receipt=ir_receipt,
        profile=profile,
        expected_client_name="webapp_ir_client",
        verification_time=verification_time,
        maximum_age_seconds=maximum_age_seconds,
        expected_key_id_sha256=None,
    )
    _require_one_paired_witness_contract(fi, ir)
    return fi, ir


def create_rotation_policy(
    *,
    webapp_fi_attestation_path: Path,
    webapp_ir_attestation_path: Path,
    policy_id: str,
    webapp_fi_generation: str,
    webapp_ir_generation: str,
    not_after: datetime,
    _rotation_state_directory_for_test: Path | None = None,
    _profile_path_for_test: Path | None = None,
    _verification_time_for_test: datetime | None = None,
) -> dict[str, Any]:
    """Create and atomically activate one versioned policy from a valid pair.

    Both source receipts are revalidated before any state artifact is written.
    The resulting policy, selector, and activation record are immutable. The
    only mutable file is an atomically replaced current-selector pointer whose
    exact contents are attested by the append-only activation ledger.
    """

    _require_root_execution()
    profile = control._load_profile(_profile_path_for_test or control.DEFAULT_PROFILE_PATH)
    now = _normalise_time(
        _verification_time_for_test or datetime.now(timezone.utc),
        field="credential rotation policy issue time",
    )
    not_after = _normalise_time(
        not_after,
        field="credential rotation policy not_after",
    )
    policy_id = _require_policy_id(policy_id)
    fi_generation = _require_generation(webapp_fi_generation, site="webapp_fi")
    ir_generation = _require_generation(webapp_ir_generation, site="webapp_ir")
    maximum_age_seconds = profile["client_credential_rotation"][
        "maximum_attestation_age_seconds"
    ]
    maximum_policy_ttl_seconds = profile["client_credential_rotation"][
        "maximum_policy_ttl_seconds"
    ]
    if not_after <= now + timedelta(seconds=maximum_age_seconds):
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy not_after is too soon"
        )
    if not_after - now > timedelta(seconds=maximum_policy_ttl_seconds):
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy TTL exceeds the trusted profile ceiling"
        )
    fi, ir = _validated_pair_for_policy_creation(
        webapp_fi_attestation_path=webapp_fi_attestation_path,
        webapp_ir_attestation_path=webapp_ir_attestation_path,
        profile=profile,
        verification_time=now,
    )
    payload: dict[str, Any] = {
        "schema": control.CREDENTIAL_ROTATION_POLICY_SCHEMA,
        "policy_id": policy_id,
        "issued_at": now.isoformat(),
        "not_before": now.isoformat(),
        "not_after": not_after.isoformat(),
        "profile": {
            "release_id": profile["release_id"],
            "source_commit": profile["source_commit"],
            "source_runtime_profile_sha256": profile["source_runtime_profile_sha256"],
            "source_release_manifest_sha256": profile["source_release_manifest_sha256"],
            "profile_sha256": _profile_sha256(profile),
        },
        "witness_trust": {
            "witness_endpoint_sha256": fi["witness_endpoint_sha256"],
            "ca_bundle_sha256": fi["ca_bundle_sha256"],
            "witness_public_key_sha256": fi["witness_public_key_sha256"],
        },
        "clients": {
            "webapp_fi": {
                "site": "webapp_fi",
                "key_id_sha256": fi["caller_key_id_sha256"],
                "generation": fi_generation,
            },
            "webapp_ir": {
                "site": "webapp_ir",
                "key_id_sha256": ir["caller_key_id_sha256"],
                "generation": ir_generation,
            },
        },
    }
    raw = control._canonical_json_bytes(payload) + bytes((10,))
    loaded = _load_rotation_policy(raw, profile=profile)
    if loaded["sha256"] != hashlib.sha256(raw).hexdigest():
        raise WriterWitnessPairAttestationError(
            "Writer Witness credential rotation policy serialization is invalid"
        )
    try:
        selected = lifecycle.install_policy_and_activate(
            policy_id=policy_id,
            policy_raw=raw,
            policy_profile_sha256=_profile_sha256(profile),
            issued_at=now.isoformat(),
            state_directory=_rotation_state_directory_for_test,
        )
    except lifecycle.WriterWitnessRotationLifecycleError as exc:
        raise WriterWitnessPairAttestationError(
            "cannot create and activate Writer Witness credential rotation policy"
        ) from exc
    return {
        "schema": control.CREDENTIAL_ROTATION_POLICY_SCHEMA,
        "status": "activated",
        "policy_id": loaded["policy_id"],
        "policy_sha256": loaded["sha256"],
        "issued_at": loaded["issued_at"].isoformat(),
        "not_before": loaded["not_before"].isoformat(),
        "not_after": not_after.isoformat(),
        "rotation_sequence": selected.sequence,
        "selector_sha256": selected.selector_sha256,
        "activation_sha256": selected.activation_sha256,
        "release_id": profile["release_id"],
        "source_commit": profile["source_commit"],
        "witness_endpoint_sha256": loaded["witness_trust"]["witness_endpoint_sha256"],
        "ca_bundle_sha256": loaded["witness_trust"]["ca_bundle_sha256"],
        "witness_public_key_sha256": loaded["witness_trust"]["witness_public_key_sha256"],
        "clients": {
            "webapp_fi": {
                "caller_key_id_sha256": loaded["clients"]["webapp_fi"]["key_id_sha256"],
                "generation": loaded["clients"]["webapp_fi"]["generation"],
            },
            "webapp_ir": {
                "caller_key_id_sha256": loaded["clients"]["webapp_ir"]["key_id_sha256"],
                "generation": loaded["clients"]["webapp_ir"]["generation"],
            },
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webapp-fi-attestation", type=Path, required=True)
    parser.add_argument("--webapp-ir-attestation", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = verify_paired_attestations(
            webapp_fi_attestation_path=arguments.webapp_fi_attestation,
            webapp_ir_attestation_path=arguments.webapp_ir_attestation,
        )
        if arguments.output is not None:
            control._write_optional_attestation(arguments.output, result)
        print(_canonical_json_bytes(result).decode("utf-8"))
        return 0
    except (WriterWitnessPairAttestationError, control.WitnessReleasePreparationError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
