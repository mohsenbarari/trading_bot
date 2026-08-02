#!/usr/bin/env python3
"""Seal non-secret Writer Witness live receipts for a versioned object channel.

This is a local contract only. It deliberately does not create an Object
Storage client, read credentials, make an HTTP request, or use SSH. It creates
an immutable envelope containing one already non-secret client receipt, then a
separate immutable publish receipt that binds the envelope hash to an external
Object Storage VersionId.

The external publisher/receiver gate must upload the envelope under the exact
content-addressed object key with create-only semantics, private access,
versioning enabled, and exact-VersionId read-back. The receiver below accepts
only the downloaded exact envelope plus its URL-free publish receipt.
"""

from __future__ import annotations

import argparse
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

from scripts import attest_writer_witness_client as client  # noqa: E402
from scripts import prepare_writer_witness_immutable_release as control  # noqa: E402
from scripts import verify_writer_witness_paired_attestation as paired  # noqa: E402
from scripts import writer_witness_rotation_lifecycle as lifecycle  # noqa: E402


ENVELOPE_SCHEMA = "gold-trade-writer-witness-live-attestation-envelope-v1"
PUBLISH_RECEIPT_SCHEMA = "gold-trade-writer-witness-live-attestation-publish-receipt-v1"
OBJECT_PREFIX = "writer-witness-live-attestations-v1"
MAXIMUM_BYTES = 256 * 1024
VERSION_ID_RE = re.compile(r"^[!-~]{1,1024}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NL = bytes((10,))


class WriterWitnessAttestationTransportError(RuntimeError):
    """A non-secret receipt cannot safely cross the object control channel."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WriterWitnessAttestationTransportError(
                "Writer Witness transport JSON contains duplicate keys"
            )
        result[key] = value
    return result


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise WriterWitnessAttestationTransportError(f"{field} is invalid")
    return value


def _require_site(value: object) -> str:
    if value not in {"webapp_fi", "webapp_ir"}:
        raise WriterWitnessAttestationTransportError("Writer Witness receipt site is invalid")
    return str(value)


def _require_version_id(value: object) -> str:
    """Accept an opaque Object Storage VersionId, never a URL-like value.

    A VersionId is echoed into the local publish receipt for exact-object
    read-back.  Reject URL syntax rather than risking a future caller treating
    that field as a transport location or printing a credential-bearing URL.
    """

    if (
        not isinstance(value, str)
        or not VERSION_ID_RE.fullmatch(value)
        or "://" in value
    ):
        raise WriterWitnessAttestationTransportError(
            "Writer Witness Object Storage VersionId is invalid"
        )
    return value


def _object_key(site: str, receipt_sha256: str) -> str:
    return f"{OBJECT_PREFIX}/{site}/{receipt_sha256}.json"


def _envelope_filename(site: str, receipt_sha256: str) -> str:
    return f"writer-witness-attestation-envelope-{site}-{receipt_sha256}.json"


def _publish_filename(site: str, receipt_sha256: str) -> str:
    return f"writer-witness-attestation-publish-{site}-{receipt_sha256}.json"


def _read_root_only(path: Path, *, field: str) -> bytes:
    try:
        raw = control._read_controlled_file(path, field=field, root_only=True)
    except control.WitnessReleasePreparationError as exc:
        raise WriterWitnessAttestationTransportError(f"{field} is invalid") from exc
    if not raw or len(raw) > MAXIMUM_BYTES:
        raise WriterWitnessAttestationTransportError(f"{field} has an unsafe size")
    return raw


def _parse_canonical(raw: bytes, *, field: str) -> dict[str, Any]:
    if not raw.endswith(NL) or len(raw) > MAXIMUM_BYTES:
        raise WriterWitnessAttestationTransportError(f"{field} is not canonical")
    try:
        value = json.loads(raw[:-1].decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WriterWitnessAttestationTransportError(f"{field} is invalid") from exc
    if not isinstance(value, dict) or _canonical(value) + NL != raw:
        raise WriterWitnessAttestationTransportError(f"{field} is not canonical")
    return value


def _assert_receipt_is_nonsecret(receipt: Mapping[str, Any]) -> None:
    if set(receipt) != paired.RECEIPT_FIELDS:
        raise WriterWitnessAttestationTransportError(
            "Writer Witness transport receipt schema is invalid"
        )
    encoded = _canonical(receipt).lower()
    for forbidden in (
        b"https:",
        b"http:",
        b"secret",
        b"password",
        b"access_key",
        b"private_key",
    ):
        if forbidden in encoded:
            raise WriterWitnessAttestationTransportError(
                "Writer Witness transport receipt is not non-secret"
            )
    _require_site(receipt.get("site"))
    for field in (
        "witness_endpoint_sha256",
        "ca_bundle_sha256",
        "runtime_profile_sha256",
        "release_manifest_sha256",
    ):
        _require_sha256(receipt.get(field), field=f"Writer Witness transport {field}")


def _require_private_directory(path: Path, *, field: str) -> Path:
    try:
        return lifecycle._require_private_directory(path, field=field)
    except lifecycle.WriterWitnessRotationLifecycleError as exc:
        raise WriterWitnessAttestationTransportError(f"{field} is invalid") from exc


def _write_sealed(destination: Path, raw: bytes, *, field: str) -> None:
    _require_private_directory(destination.parent, field=f"{field} parent")
    try:
        lifecycle._write_immutable(destination, raw, field=field)
    except lifecycle.WriterWitnessRotationLifecycleError as exc:
        raise WriterWitnessAttestationTransportError(f"cannot create {field}") from exc


def seal_receipt(
    *,
    attestation_path: Path,
    destination_directory: Path,
) -> dict[str, Any]:
    """Create one immutable content-addressed envelope from a local receipt."""

    raw = _read_root_only(attestation_path, field="Writer Witness client attestation")
    receipt = _parse_canonical(raw, field="Writer Witness client attestation")
    _assert_receipt_is_nonsecret(receipt)
    site = _require_site(receipt["site"])
    receipt_sha256 = _sha256(raw)
    envelope: dict[str, Any] = {
        "schema": ENVELOPE_SCHEMA,
        "source_site": site,
        "object_key": _object_key(site, receipt_sha256),
        "object_version_id": None,
        "object_create_only": True,
        "receipt_sha256": receipt_sha256,
        "receipt_bytes": len(raw),
        "receipt": receipt,
    }
    envelope_raw = _canonical(envelope) + NL
    destination = _require_private_directory(
        destination_directory,
        field="Writer Witness transport destination directory",
    ) / _envelope_filename(site, receipt_sha256)
    _write_sealed(
        destination,
        envelope_raw,
        field="Writer Witness sealed attestation envelope",
    )
    return {
        "schema": ENVELOPE_SCHEMA,
        "status": "sealed",
        "source_site": site,
        "object_key": envelope["object_key"],
        "envelope_sha256": _sha256(envelope_raw),
        "receipt_sha256": receipt_sha256,
        "receipt_bytes": len(raw),
    }


def _load_envelope(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = _read_root_only(path, field="Writer Witness sealed attestation envelope")
    envelope = _parse_canonical(raw, field="Writer Witness sealed attestation envelope")
    if set(envelope) != {
        "schema",
        "source_site",
        "object_key",
        "object_version_id",
        "object_create_only",
        "receipt_sha256",
        "receipt_bytes",
        "receipt",
    } or envelope.get("schema") != ENVELOPE_SCHEMA:
        raise WriterWitnessAttestationTransportError(
            "Writer Witness sealed attestation envelope schema is invalid"
        )
    site = _require_site(envelope.get("source_site"))
    receipt_sha256 = _require_sha256(
        envelope.get("receipt_sha256"),
        field="Writer Witness transport receipt hash",
    )
    if (
        envelope.get("object_key") != _object_key(site, receipt_sha256)
        or envelope.get("object_version_id") is not None
        or envelope.get("object_create_only") is not True
    ):
        raise WriterWitnessAttestationTransportError(
            "Writer Witness sealed attestation envelope object binding is invalid"
        )
    receipt = envelope.get("receipt")
    if not isinstance(receipt, dict):
        raise WriterWitnessAttestationTransportError(
            "Writer Witness sealed attestation envelope receipt is invalid"
        )
    _assert_receipt_is_nonsecret(receipt)
    receipt_raw = _canonical(receipt) + NL
    if (
        receipt.get("site") != site
        or _sha256(receipt_raw) != receipt_sha256
        or envelope.get("receipt_bytes") != len(receipt_raw)
    ):
        raise WriterWitnessAttestationTransportError(
            "Writer Witness sealed attestation envelope receipt binding is invalid"
        )
    return raw, envelope


def bind_published_version(
    *,
    envelope_path: Path,
    object_version_id: str,
    destination_directory: Path,
) -> dict[str, Any]:
    """Create a URL-free immutable receipt after an external exact upload."""

    object_version_id = _require_version_id(object_version_id)
    envelope_raw, envelope = _load_envelope(envelope_path)
    site = _require_site(envelope["source_site"])
    receipt_sha256 = _require_sha256(
        envelope["receipt_sha256"],
        field="Writer Witness transport receipt hash",
    )
    publish: dict[str, Any] = {
        "schema": PUBLISH_RECEIPT_SCHEMA,
        "status": "published",
        "source_site": site,
        "object_key": envelope["object_key"],
        "object_version_id": object_version_id,
        "envelope_sha256": _sha256(envelope_raw),
        "receipt_sha256": receipt_sha256,
        "receipt_bytes": envelope["receipt_bytes"],
    }
    raw = _canonical(publish) + NL
    destination = _require_private_directory(
        destination_directory,
        field="Writer Witness publish receipt destination directory",
    ) / _publish_filename(site, receipt_sha256)
    _write_sealed(
        destination,
        raw,
        field="Writer Witness sealed attestation publish receipt",
    )
    return {
        "schema": PUBLISH_RECEIPT_SCHEMA,
        "status": "bound",
        "source_site": site,
        "object_key": publish["object_key"],
        "object_version_id": object_version_id,
        "envelope_sha256": publish["envelope_sha256"],
        "receipt_sha256": receipt_sha256,
    }


def _load_publish_receipt(path: Path) -> dict[str, Any]:
    raw = _read_root_only(path, field="Writer Witness attestation publish receipt")
    value = _parse_canonical(raw, field="Writer Witness attestation publish receipt")
    if set(value) != {
        "schema",
        "status",
        "source_site",
        "object_key",
        "object_version_id",
        "envelope_sha256",
        "receipt_sha256",
        "receipt_bytes",
    } or value.get("schema") != PUBLISH_RECEIPT_SCHEMA or value.get("status") != "published":
        raise WriterWitnessAttestationTransportError(
            "Writer Witness attestation publish receipt schema is invalid"
        )
    site = _require_site(value.get("source_site"))
    receipt_sha256 = _require_sha256(
        value.get("receipt_sha256"),
        field="Writer Witness publish receipt hash",
    )
    if (
        value.get("object_key") != _object_key(site, receipt_sha256)
        or not isinstance(value.get("receipt_bytes"), int)
        or value["receipt_bytes"] < 1
        or value["receipt_bytes"] > MAXIMUM_BYTES
    ):
        raise WriterWitnessAttestationTransportError(
            "Writer Witness attestation publish receipt object binding is invalid"
        )
    _require_version_id(value.get("object_version_id"))
    _require_sha256(value.get("envelope_sha256"), field="Writer Witness publish envelope hash")
    return value


def receive_sealed_receipt(
    *,
    envelope_path: Path,
    publish_receipt_path: Path,
    expected_site: str,
    destination: Path,
) -> dict[str, Any]:
    """Verify a downloaded exact object and import its receipt create-only."""

    expected_site = _require_site(expected_site)
    envelope_raw, envelope = _load_envelope(envelope_path)
    publish = _load_publish_receipt(publish_receipt_path)
    if (
        envelope["source_site"] != expected_site
        or publish["source_site"] != expected_site
        or publish["object_key"] != envelope["object_key"]
        or publish["envelope_sha256"] != _sha256(envelope_raw)
        or publish["receipt_sha256"] != envelope["receipt_sha256"]
        or publish["receipt_bytes"] != envelope["receipt_bytes"]
    ):
        raise WriterWitnessAttestationTransportError(
            "Writer Witness attestation transport receipt binding is invalid"
        )
    receipt_raw = _canonical(envelope["receipt"]) + NL
    _write_sealed(
        destination,
        receipt_raw,
        field="Writer Witness imported live attestation",
    )
    return {
        "schema": PUBLISH_RECEIPT_SCHEMA,
        "status": "received",
        "source_site": expected_site,
        "object_key": publish["object_key"],
        "object_version_id": publish["object_version_id"],
        "receipt_sha256": publish["receipt_sha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal")
    seal.add_argument("--attestation", type=Path, required=True)
    seal.add_argument("--destination-directory", type=Path, required=True)
    bind = commands.add_parser("bind-published-version")
    bind.add_argument("--envelope", type=Path, required=True)
    bind.add_argument("--object-version-id", required=True)
    bind.add_argument("--destination-directory", type=Path, required=True)
    receive = commands.add_parser("receive")
    receive.add_argument("--envelope", type=Path, required=True)
    receive.add_argument("--publish-receipt", type=Path, required=True)
    receive.add_argument("--expected-site", required=True)
    receive.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "seal":
            result = seal_receipt(
                attestation_path=arguments.attestation,
                destination_directory=arguments.destination_directory,
            )
        elif arguments.command == "bind-published-version":
            result = bind_published_version(
                envelope_path=arguments.envelope,
                object_version_id=arguments.object_version_id,
                destination_directory=arguments.destination_directory,
            )
        else:
            result = receive_sealed_receipt(
                envelope_path=arguments.envelope,
                publish_receipt_path=arguments.publish_receipt,
                expected_site=arguments.expected_site,
                destination=arguments.destination,
            )
        print(_canonical(result).decode("utf-8"))
        return 0
    except WriterWitnessAttestationTransportError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
