#!/usr/bin/env python3
"""Verify fresh FI+IR non-secret Writer Witness client attestations together.

Both WebApp sites must first run ``attest_writer_witness_client.py`` locally.
This verifier never reads an HMAC secret, never contacts the Witness, and does
not activate a service.  It only accepts two freshly observed, TLS-verified
receipts whose signed Witness responses bind the exact control-profile hashes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import attest_writer_witness_client as client_attestation  # noqa: E402
from scripts import prepare_writer_witness_immutable_release as control  # noqa: E402


PAIRED_ATTESTATION_SCHEMA = "gold-trade-writer-witness-paired-live-attestation-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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


def _validate_one(
    *,
    receipt: Mapping[str, Any],
    profile: Mapping[str, Any],
    expected_client_name: str,
    verification_time: datetime,
    maximum_age_seconds: int,
) -> dict[str, Any]:
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
        client_attestation._decode_public_key(
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
            expected_key_id_sha256=None,
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
        "caller_key_id_sha256": verified_witness["caller_key_id_sha256"],
        "witness_time": verified_witness["witness_time"],
    }


def verify_paired_attestations(
    *,
    webapp_fi_attestation_path: Path,
    webapp_ir_attestation_path: Path,
    profile_path: Path = control.DEFAULT_PROFILE_PATH,
    verification_time: datetime | None = None,
    maximum_age_seconds: int = 60,
) -> dict[str, Any]:
    """Return a non-secret pair result only when both receipts are exact/fresh."""

    if isinstance(maximum_age_seconds, bool) or not 15 <= maximum_age_seconds <= 300:
        raise WriterWitnessPairAttestationError("maximum attestation age is invalid")
    profile = control._load_profile(profile_path)
    now = (verification_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
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
        expected_client_name="webapp_fi_client",
        verification_time=now,
        maximum_age_seconds=maximum_age_seconds,
    )
    ir = _validate_one(
        receipt=ir_receipt,
        profile=profile,
        expected_client_name="webapp_ir_client",
        verification_time=now,
        maximum_age_seconds=maximum_age_seconds,
    )
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
    ):
        if fi[key] != ir[key]:
            raise WriterWitnessPairAttestationError(
                "FI and IR do not have one identical TLS/endpoint Witness trust binding"
            )
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
        "clients": {
            "webapp_fi": {
                "request_id": fi["request_id"],
                "observed_at": fi["observed_at"].isoformat(),
                "witness_time": fi["witness_time"],
                "caller_key_id_sha256": fi["caller_key_id_sha256"],
                "receipt_sha256": hashlib.sha256(fi_raw).hexdigest(),
            },
            "webapp_ir": {
                "request_id": ir["request_id"],
                "observed_at": ir["observed_at"].isoformat(),
                "witness_time": ir["witness_time"],
                "caller_key_id_sha256": ir["caller_key_id_sha256"],
                "receipt_sha256": hashlib.sha256(ir_raw).hexdigest(),
            },
        },
        "compatible": True,
    }


def _parse_cli_time(value: str) -> datetime:
    return _parse_time(value, field="verification time")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webapp-fi-attestation", type=Path, required=True)
    parser.add_argument("--webapp-ir-attestation", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=control.DEFAULT_PROFILE_PATH)
    parser.add_argument("--verification-time", type=_parse_cli_time)
    parser.add_argument("--maximum-age-seconds", type=int, default=60)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = verify_paired_attestations(
            webapp_fi_attestation_path=arguments.webapp_fi_attestation,
            webapp_ir_attestation_path=arguments.webapp_ir_attestation,
            profile_path=arguments.profile,
            verification_time=arguments.verification_time,
            maximum_age_seconds=arguments.maximum_age_seconds,
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
