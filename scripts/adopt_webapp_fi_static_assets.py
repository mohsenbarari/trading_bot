#!/usr/bin/env python3
"""Verify and adopt a deterministic WebApp-FI ``mini_app_dist`` archive.

This controller-side primitive is intentionally local-only.  It does not
contact Object Storage, invoke age, SSH, Docker, or a service.  The input
archive must already be the controller's root-only age-decrypted result of a
WebApp-FI export published as one private/versioned/create-only object and
read back at its exact VersionId.  The root-only read-back record makes that
external boundary explicit.  This helper verifies the plaintext archive
again, constructs its deterministic file manifest, and signs the existing
static-assets provenance schema.

The archive contains only relative regular files below ``mini_app_dist``; it
does not contain an extraction wrapper, symlink, directory entry, special file,
or an embedded mutable manifest.  Any later consumer must extract a separately
delivered copy into a fresh root-only ``mini_app_dist`` directory and verify
every signed file before use.  A future transfer design may choose an
additional WA-IR recipient for efficiency, but this helper neither requires,
records, stages, nor installs that delivery.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
from typing import Any, Mapping, Sequence


def _load_portable_verifier() -> Any:
    path = Path(__file__).with_name("verify_webapp_fi_source_provenance.py")
    spec = importlib.util.spec_from_file_location("_static_asset_adoption_portable_verifier", path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError("cannot load WebApp-FI source provenance verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


portable = _load_portable_verifier()


STATIC_ASSET_READBACK_SCHEMA = "gold-trade-webapp-fi-static-assets-readback-v1"
STATIC_ASSET_ADOPTION_RECEIPT_SCHEMA = "gold-trade-webapp-fi-static-assets-adoption-receipt-v1"
STATIC_ASSET_PROVENANCE_INPUT_SCHEMA = "gold-trade-webapp-fi-static-assets-provenance-input-v1"
STATIC_ASSET_PROVENANCE_NAME = "static-assets-provenance.json"
STATIC_ASSET_RECEIPT_NAME = "static-assets-adoption-receipt.json"
STATIC_ASSET_INPUT_NAME = "static-assets-provenance-input.json"

MAX_STATIC_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_STATIC_FILE_BYTES = 100 * 1024 * 1024
MAX_STATIC_FILES = 100_000
MAX_STATIC_PATH_BYTES = 512

STATIC_TRANSPORT = {
    "transport": "private_versioned_age_only",
    "create_only": True,
    "read_back_same_version_id": True,
    "provider_side_sse": False,
}
STATIC_ASSET_AGE_DECRYPTION = {
    "algorithm": "age-v1",
    "controller_identity_scope": "root_only",
    "ciphertext_sha256_verified_before_decrypt": True,
    "plaintext_sha256_verified_after_decrypt": True,
}
SOURCE_KIND = "deterministic_2c08_dist_manifest"


class StaticAssetAdoptionError(RuntimeError):
    """The static asset archive cannot be bound to a safe controller proof."""


class _StaticTarInfo(tarfile.TarInfo):
    """Reject tar extensions before tarfile can buffer their payloads."""

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type in {
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            tarfile.GNUTYPE_SPARSE,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
        }:
            raise tarfile.ReadError("static asset archives must not contain extended tar headers")
        return super()._proc_member(archive)


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise StaticAssetAdoptionError("static asset adoption must run as root")


def _require_safe_directory_ancestors(path: Path, *, field: str) -> None:
    if not path.is_absolute():
        raise StaticAssetAdoptionError(f"{field} must be an absolute path")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise StaticAssetAdoptionError(f"cannot inspect {field} ancestor") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
        ):
            raise StaticAssetAdoptionError(f"{field} has an unsafe ancestor")
        mode = stat.S_IMODE(metadata.st_mode)
        if current == path:
            if mode & 0o077:
                raise StaticAssetAdoptionError(f"{field} must be root-only")
        elif mode & 0o022 and not (metadata.st_mode & stat.S_ISVTX):
            raise StaticAssetAdoptionError(f"{field} has a writable non-sticky ancestor")


def _require_private_directory(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise StaticAssetAdoptionError(f"{field} must be an absolute path")
    _require_safe_directory_ancestors(path, field=field)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise StaticAssetAdoptionError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISDIR(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o077
    ):
        raise StaticAssetAdoptionError(f"{field} must be one root-only non-symlink directory")
    return resolved


def _require_private_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    if not path.is_absolute():
        raise StaticAssetAdoptionError(f"{field} must be an absolute path")
    _require_private_directory(path.parent, field=f"{field} parent")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise StaticAssetAdoptionError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o077
        or resolved_metadata.st_nlink != 1
        or not 1 <= resolved_metadata.st_size <= maximum_bytes
    ):
        raise StaticAssetAdoptionError(f"{field} must be a bounded root-only non-symlink file")
    return resolved


def _read_canonical_private_json(path: Path, *, field: str) -> tuple[dict[str, Any], bytes]:
    path = _require_private_file(path, field=field, maximum_bytes=1024 * 1024)
    try:
        payload = path.read_bytes()
        value = portable._parse(payload, field=field)
    except Exception as exc:
        raise StaticAssetAdoptionError(f"{field} is invalid") from exc
    return value, payload


def _load_static_readback(*, path: Path, campaign_id: str) -> dict[str, Any]:
    value, _payload = _read_canonical_private_json(path, field="static asset Object Storage read-back record")
    expected = {
        "schema",
        "status",
        "campaign_id",
        "source_site",
        "consumer_site",
        "object",
        "transport",
        "age_decryption",
    }
    if (
        set(value) != expected
        or value.get("schema") != STATIC_ASSET_READBACK_SCHEMA
        or value.get("status") != "read_back"
        or value.get("campaign_id") != campaign_id
        or value.get("source_site") != "webapp_fi"
        or value.get("consumer_site") != "controller"
        or value.get("transport") != STATIC_TRANSPORT
        or value.get("age_decryption") != STATIC_ASSET_AGE_DECRYPTION
    ):
        raise StaticAssetAdoptionError("static asset Object Storage read-back record is unsupported")
    try:
        return portable._object_descriptor(
            value.get("object"),
            field="static asset Object Storage object",
            maximum_plaintext_bytes=MAX_STATIC_ARCHIVE_BYTES,
        )
    except Exception as exc:
        raise StaticAssetAdoptionError("static asset Object Storage object is invalid") from exc


def _safe_asset_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_STATIC_PATH_BYTES:
        raise StaticAssetAdoptionError("static asset archive path is invalid")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise StaticAssetAdoptionError("static asset archive path must be printable ASCII")
    pure = PurePosixPath(value)
    if (
        pure.as_posix() != value
        or pure.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise StaticAssetAdoptionError("static asset archive path is invalid")
    return value


def _member_payload(archive: tarfile.TarFile, member: tarfile.TarInfo) -> tuple[str, int]:
    if (
        not member.isreg()
        or member.issparse()
        or member.size < 0
        or member.size > MAX_STATIC_FILE_BYTES
        or member.mode != 0o644
        or member.uid != 0
        or member.gid != 0
        or member.uname
        or member.gname
        or member.mtime != 0
        or member.pax_headers
        or member.linkname
    ):
        raise StaticAssetAdoptionError("static asset archive member is not deterministic")
    payload = archive.extractfile(member)
    if payload is None:
        raise StaticAssetAdoptionError("static asset archive member cannot be read")
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := payload.read(1024 * 1024):
            size += len(chunk)
            if size > member.size or size > MAX_STATIC_FILE_BYTES:
                raise StaticAssetAdoptionError("static asset archive member exceeds its bound")
            digest.update(chunk)
    finally:
        payload.close()
    if size != member.size:
        raise StaticAssetAdoptionError("static asset archive member is truncated")
    return digest.hexdigest(), size


def _inspect_static_archive(*, archive_path: Path, object_descriptor: Mapping[str, Any]) -> list[dict[str, Any]]:
    archive_path = _require_private_file(
        archive_path,
        field="age-decrypted static asset plaintext archive",
        maximum_bytes=MAX_STATIC_ARCHIVE_BYTES,
    )
    expected = (object_descriptor.get("plaintext_sha256"), object_descriptor.get("plaintext_bytes"))
    if sha256_file(archive_path) != expected:
        raise StaticAssetAdoptionError("static asset archive differs from the Object Storage read-back plaintext binding")
    files: list[dict[str, Any]] = []
    prior = ""
    count = 0
    try:
        with tarfile.open(archive_path, "r|", tarinfo=_StaticTarInfo) as archive:
            while (member := archive.next()) is not None:
                count += 1
                if count > MAX_STATIC_FILES:
                    raise StaticAssetAdoptionError("static asset archive has too many members")
                path = _safe_asset_path(member.name)
                if prior and path <= prior:
                    raise StaticAssetAdoptionError("static asset archive paths are not deterministically sorted")
                prior = path
                digest, size = _member_payload(archive, member)
                files.append({"path": path, "sha256": digest, "bytes": size})
    except (OSError, tarfile.TarError) as exc:
        raise StaticAssetAdoptionError("static asset archive cannot be validated") from exc
    if not files:
        raise StaticAssetAdoptionError("static asset archive must contain at least one file")
    if sha256_file(archive_path) != expected:
        raise StaticAssetAdoptionError("static asset archive changed while being inspected")
    return files


def _load_controller_signer(path: Path) -> tuple[Any, str]:
    path = _require_private_file(path, field="static asset controller signing private key", maximum_bytes=32)
    raw = path.read_bytes()
    if len(raw) != 32:
        raise StaticAssetAdoptionError("static asset controller signing private key must contain exactly 32 bytes")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        signer = Ed25519PrivateKey.from_private_bytes(raw)
        public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except (ImportError, ValueError) as exc:
        raise StaticAssetAdoptionError("static asset controller signing key is invalid") from exc
    return signer, base64.b64encode(public).decode("ascii")


def _write_new_json(path: Path, value: Mapping[str, Any], *, field: str) -> bytes:
    encoded = canonical_json_bytes(value) + b"\n"
    try:
        portable._reject_persisted_url(encoded, field=field)
    except Exception as exc:
        raise StaticAssetAdoptionError(f"{field} contains a forbidden URL") from exc
    if path.exists() or path.is_symlink():
        raise StaticAssetAdoptionError(f"refusing to overwrite {field}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise StaticAssetAdoptionError(f"cannot create {field}") from exc
    return encoded


def adopt_static_assets(
    *,
    static_archive: Path,
    object_storage_readback: Path,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    pinned_controller_public_key_base64: str,
    controller_signing_private_key: Path,
    output_directory: Path,
    apply: bool,
) -> dict[str, Any]:
    """Produce a signed static-assets provenance descriptor from local bytes."""

    _require_root_execution()
    try:
        campaign_id = portable._campaign(expected_campaign_id, field="expected campaign")
        application = portable._application(expected_application, field="expected application")
    except Exception as exc:
        raise StaticAssetAdoptionError("static asset provenance identity is invalid") from exc
    object_descriptor = _load_static_readback(path=object_storage_readback, campaign_id=campaign_id)
    files = _inspect_static_archive(archive_path=static_archive, object_descriptor=object_descriptor)
    files_sha256 = sha256_bytes(canonical_json_bytes(files))
    signer, controller_public_key_base64 = _load_controller_signer(controller_signing_private_key)
    if controller_public_key_base64 != pinned_controller_public_key_base64:
        raise StaticAssetAdoptionError("static asset signer does not match the pinned controller key")
    output_directory = Path(output_directory)
    if not output_directory.is_absolute():
        raise StaticAssetAdoptionError("output_directory must be an absolute path")
    parent = _require_private_directory(output_directory.parent, field="output_directory parent")
    if output_directory.parent != parent or output_directory.exists() or output_directory.is_symlink():
        raise StaticAssetAdoptionError("output_directory must be a new child of a root-only directory")
    plan = {
        "status": "planned" if not apply else "adopting",
        "campaign_id": campaign_id,
        "application": application,
        "output_directory": str(output_directory),
        "object": {"object_key": object_descriptor["object_key"], "version_id": object_descriptor["version_id"]},
        "files_sha256": files_sha256,
        "file_count": len(files),
        "object_storage_action": False,
        "age_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
    }
    if not apply:
        return plan
    try:
        output_directory.mkdir(mode=0o700)
    except OSError as exc:
        raise StaticAssetAdoptionError("cannot create output_directory") from exc
    _require_private_directory(output_directory, field="output_directory")
    unsigned_provenance = {
        "schema": portable.STATIC_ASSET_PROVENANCE_SCHEMA,
        "status": "verified",
        "campaign_id": campaign_id,
        "application": application,
        "source_kind": SOURCE_KIND,
        "artifact": object_descriptor,
        "files": files,
        "files_sha256": files_sha256,
        "controller_public_key_base64": controller_public_key_base64,
    }
    provenance = {
        **unsigned_provenance,
        "controller_signature": {
            "algorithm": "ed25519",
            "signature_base64": base64.b64encode(
                signer.sign(portable.STATIC_ASSET_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned_provenance))
            ).decode("ascii"),
        },
    }
    provenance_path = output_directory / STATIC_ASSET_PROVENANCE_NAME
    provenance_payload = _write_new_json(provenance_path, provenance, field="static asset provenance")
    try:
        portable._static_assets_provenance(
            payload=provenance_payload,
            pinned_controller_public_key_base64=pinned_controller_public_key_base64,
            expected_campaign_id=campaign_id,
            expected_application=application,
        )
    except Exception as exc:
        raise StaticAssetAdoptionError("generated static asset provenance did not verify") from exc
    receipt_unsigned = {
        "schema": STATIC_ASSET_ADOPTION_RECEIPT_SCHEMA,
        "status": "adopted",
        "campaign_id": campaign_id,
        "application": application,
        "source_site": "webapp_fi",
        "destination_site": "controller",
        "archive": {
            "sha256": object_descriptor["plaintext_sha256"],
            "bytes": object_descriptor["plaintext_bytes"],
            "files_sha256": files_sha256,
            "file_count": len(files),
        },
        "object": object_descriptor,
        "transport": STATIC_TRANSPORT,
        "age_decryption": STATIC_ASSET_AGE_DECRYPTION,
        "static_assets_provenance_sha256": sha256_bytes(provenance_payload),
    }
    receipt = {
        **receipt_unsigned,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_unsigned)),
    }
    receipt_path = output_directory / STATIC_ASSET_RECEIPT_NAME
    receipt_payload = _write_new_json(receipt_path, receipt, field="static asset adoption receipt")
    input_value = {
        "schema": STATIC_ASSET_PROVENANCE_INPUT_SCHEMA,
        "campaign_id": campaign_id,
        "application": application,
        "static_assets_provenance": portable._parse(provenance_payload, field="static asset provenance"),
        "adoption_receipt_sha256": sha256_bytes(receipt_payload),
    }
    input_path = output_directory / STATIC_ASSET_INPUT_NAME
    _write_new_json(input_path, input_value, field="static asset provenance input")
    return {
        "status": "adopted",
        "output_directory": str(output_directory),
        "static_assets_provenance_path": str(provenance_path),
        "adoption_receipt_path": str(receipt_path),
        "provenance_input_path": str(input_path),
        "static_assets_provenance_sha256": sha256_bytes(provenance_payload),
        "files_sha256": files_sha256,
        "file_count": len(files),
        "object": {"object_key": object_descriptor["object_key"], "version_id": object_descriptor["version_id"]},
        "object_storage_action": False,
        "age_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-archive", type=Path, required=True)
    parser.add_argument("--object-storage-readback", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--expected-alembic-revision", required=True)
    parser.add_argument("--pinned-controller-public-key-base64", required=True)
    parser.add_argument("--controller-signing-private-key", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        result = adopt_static_assets(
            static_archive=args.static_archive,
            object_storage_readback=args.object_storage_readback,
            expected_campaign_id=args.campaign_id,
            expected_application={
                "release_sha": args.release_sha,
                "expected_alembic_revision": args.expected_alembic_revision,
            },
            pinned_controller_public_key_base64=args.pinned_controller_public_key_base64,
            controller_signing_private_key=args.controller_signing_private_key,
            output_directory=args.output_directory,
            apply=args.apply,
        )
    except StaticAssetAdoptionError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch.
    raise SystemExit(main())
