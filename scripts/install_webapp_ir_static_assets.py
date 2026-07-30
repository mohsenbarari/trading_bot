#!/usr/bin/env python3
"""Install a verified WebApp-FI static archive into an immutable WA-IR root.

This is deliberately a local filesystem primitive.  The caller supplies an
already downloaded and age-decrypted archive plus its exact-version read-back
record.  The helper revalidates the staged five-artifact provenance, checks
the controller-signed static descriptor embedded in it, then extracts only
the signed deterministic files into one fresh immutable static root.  It does
not contact Object Storage, invoke age, SSH, Docker, Nginx, or any service.

The output root intentionally lives outside the Git application release.  The
release checkout therefore remains clean while a later listener can bind the
separately verified static root to the exact application release.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import tempfile
from typing import Any, Mapping, Sequence


def _load_sibling(module_name: str) -> Any:
    module_path = Path(__file__).with_name(module_name + ".py")
    spec = importlib.util.spec_from_file_location("_wa_ir_static_" + module_name, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


provenance = _load_sibling("manage_webapp_ir_release_provenance")
portable = _load_sibling("verify_webapp_fi_source_provenance")


STATIC_RECEIVE_RECEIPT_SCHEMA = "gold-trade-wa-ir-static-assets-receive-v1"
STATIC_INSTALL_RECEIPT_SCHEMA = "gold-trade-wa-ir-static-assets-install-receipt-v1"
STATIC_TRANSPORT = {
    "transport": "private_versioned_age_only",
    "create_only": True,
    "read_back_same_version_id": True,
    "provider_side_sse": False,
}
STATIC_AGE_DECRYPTION = {
    "algorithm": "age-v1",
    "wa_ir_identity_scope": "root_only",
    "ciphertext_sha256_verified_before_decrypt": True,
    "plaintext_sha256_verified_after_decrypt": True,
}
MAX_STATIC_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_STATIC_FILE_BYTES = 100 * 1024 * 1024
MAX_STATIC_FILES = 100_000
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")


class StaticAssetInstallError(RuntimeError):
    """The detached static archive cannot be safely installed."""


class _StaticTarInfo(tarfile.TarInfo):
    """Refuse tar extensions before tarfile can buffer their payloads."""

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type in {
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            tarfile.GNUTYPE_SPARSE,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
        }:
            raise tarfile.ReadError("static archive must not contain extended tar headers")
        return super()._proc_member(archive)


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_root() -> None:
    if os.geteuid() != 0:
        raise StaticAssetInstallError("static asset installation must run as root")


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise StaticAssetInstallError(f"{field} must be a lowercase SHA-256")
    return value


def _require_absolute(path: Path, *, field: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise StaticAssetInstallError(f"{field} must be a safe absolute path")
    return path


def _require_directory(path: Path, *, field: str, private: bool) -> Path:
    _require_absolute(path, field=field)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise StaticAssetInstallError(f"cannot inspect {field}") from exc
    disallowed = 0o077 if private else 0o022
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & disallowed
    ):
        qualifier = "root-only" if private else "root-owned and not group/world writable"
        raise StaticAssetInstallError(f"{field} must be a {qualifier} directory")
    return path


def _open_checked_file(path: Path, *, field: str, private: bool, maximum: int) -> tuple[int, os.stat_result]:
    _require_absolute(path, field=field)
    try:
        before = path.lstat()
    except OSError as exc:
        raise StaticAssetInstallError(f"cannot inspect {field}") from exc
    disallowed = 0o077 if private else 0o022
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) & disallowed
        or before.st_nlink != 1
        or not 1 <= before.st_size <= maximum
    ):
        qualifier = "root-only" if private else "root-owned and not group/world writable"
        raise StaticAssetInstallError(f"{field} must be a bounded {qualifier} regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StaticAssetInstallError(f"cannot securely open {field}") from exc
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != 0
        or stat.S_IMODE(opened.st_mode) & disallowed
        or opened.st_nlink != 1
        or not 1 <= opened.st_size <= maximum
        or opened.st_dev != before.st_dev
        or opened.st_ino != before.st_ino
    ):
        os.close(descriptor)
        raise StaticAssetInstallError(f"{field} changed while being opened")
    return descriptor, opened


def _read_checked_file(path: Path, *, field: str, private: bool, maximum: int) -> bytes:
    descriptor, opened = _open_checked_file(path, field=field, private=private, maximum=maximum)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise StaticAssetInstallError(f"{field} exceeds its size bound")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if after.st_size != opened.st_size or after.st_dev != opened.st_dev or after.st_ino != opened.st_ino:
            raise StaticAssetInstallError(f"{field} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def sha256_checked_file(path: Path, *, field: str, private: bool, maximum: int) -> tuple[str, int]:
    descriptor, opened = _open_checked_file(path, field=field, private=private, maximum=maximum)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise StaticAssetInstallError(f"{field} exceeds its size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if after.st_size != opened.st_size or after.st_dev != opened.st_dev or after.st_ino != opened.st_ino:
            raise StaticAssetInstallError(f"{field} changed while being read")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def _strict_json(raw: bytes, *, field: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate key")
            output[key] = value
        return output

    def reject_constant(value: str) -> None:
        raise ValueError(f"unsupported constant {value}")

    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise StaticAssetInstallError(f"{field} is not strict JSON") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value) + b"\n":
        raise StaticAssetInstallError(f"{field} must use canonical JSON")
    if b"://" in raw.lower() or b'"url"' in raw.lower() or b'"presigned"' in raw.lower():
        raise StaticAssetInstallError(f"{field} must not persist a URL")
    return value


def _read_private_json(path: Path, *, field: str, maximum: int = MAX_RECEIPT_BYTES) -> tuple[dict[str, Any], bytes]:
    raw = _read_checked_file(path, field=field, private=True, maximum=maximum)
    return _strict_json(raw, field=field), raw


def _fields(value: Mapping[str, Any], *, expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise StaticAssetInstallError(f"{field} fields are invalid")


def _relative_asset_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512:
        raise StaticAssetInstallError("static asset path is invalid")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise StaticAssetInstallError("static asset path must be printable ASCII")
    pure = PurePosixPath(value)
    if (
        pure.as_posix() != value
        or pure.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise StaticAssetInstallError("static asset path is invalid")
    return value


def _proof_files(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_STATIC_FILES:
        raise StaticAssetInstallError("static assets proof files are invalid")
    result: list[dict[str, Any]] = []
    prior = ""
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "bytes"}:
            raise StaticAssetInstallError("static assets proof file is invalid")
        path = _relative_asset_path(item.get("path"))
        digest = _require_sha256(item.get("sha256"), field="static asset file SHA-256")
        bytes_value = item.get("bytes")
        if (
            isinstance(bytes_value, bool)
            or not isinstance(bytes_value, int)
            or not 0 <= bytes_value <= MAX_STATIC_FILE_BYTES
            or prior and path <= prior
        ):
            raise StaticAssetInstallError("static assets proof file is invalid")
        result.append({"path": path, "sha256": digest, "bytes": bytes_value})
        prior = path
    return result


def _load_receive_record(
    *,
    path: Path,
    expected_campaign_id: str,
    expected_object: Mapping[str, Any],
) -> None:
    value, _ = _read_private_json(path, field="static asset exact-version receive receipt")
    _fields(
        value,
        expected={"schema", "status", "campaign_id", "source_site", "destination_site", "object", "transport", "age_decryption"},
        field="static asset exact-version receive receipt",
    )
    if (
        value.get("schema") != STATIC_RECEIVE_RECEIPT_SCHEMA
        or value.get("status") != "read_back"
        or value.get("campaign_id") != expected_campaign_id
        or value.get("source_site") != "webapp_fi"
        or value.get("destination_site") != "webapp_ir"
        or value.get("transport") != STATIC_TRANSPORT
        or value.get("age_decryption") != STATIC_AGE_DECRYPTION
    ):
        raise StaticAssetInstallError("static asset exact-version receive receipt is unsupported")
    try:
        observed_object = portable._object_descriptor(
            value.get("object"),
            field="static asset exact-version Object Storage object",
            maximum_plaintext_bytes=MAX_STATIC_ARCHIVE_BYTES,
        )
    except Exception as exc:
        raise StaticAssetInstallError("static asset exact-version Object Storage object is invalid") from exc
    if observed_object != dict(expected_object):
        raise StaticAssetInstallError("static asset exact-version receive receipt does not match the signed static object")


def _static_claim_from_stage(*, stage_receipt_path: Path, bootstrap_receipt_path: Path) -> dict[str, Any]:
    try:
        bootstrap = provenance.load_bootstrap_receive_receipt(bootstrap_receipt_path)
        stage, staged_provenance = provenance.verify_staged_provenance(stage_receipt_path, bootstrap=bootstrap)
    except provenance.ReleaseProvenanceError as exc:
        raise StaticAssetInstallError("staged release provenance is invalid") from exc
    proof = staged_provenance.webapp_fi_source_provenance["proofs"].get("static_assets_provenance")
    if not isinstance(proof, Mapping):  # pragma: no cover - composite verifier already guards this.
        raise StaticAssetInstallError("staged release lacks a static asset proof")
    proof_value = dict(proof)
    proof_payload = canonical_json_bytes(proof_value) + b"\n"
    controller_key = base64.b64encode(bootstrap.webapp_fi_controller_authorization_public_key).decode("ascii")
    source_claim = staged_provenance.webapp_fi_source_provenance
    try:
        static_claim = portable._static_assets_provenance(
            payload=proof_payload,
            pinned_controller_public_key_base64=controller_key,
            expected_campaign_id=source_claim["campaign_id"],
            expected_application=source_claim["application"],
        )
    except Exception as exc:  # pragma: no cover - composite verifier already guards this.
        raise StaticAssetInstallError("staged static asset proof is invalid") from exc
    files = _proof_files(proof_value.get("files"))
    if proof_value.get("files_sha256") != sha256_bytes(canonical_json_bytes(files)):
        raise StaticAssetInstallError("staged static asset proof file hash is invalid")
    if static_claim["files_sha256"] != proof_value["files_sha256"] or static_claim["file_count"] != len(files):
        raise StaticAssetInstallError("staged static asset proof is inconsistent")
    return {
        "campaign_id": source_claim["campaign_id"],
        "application": source_claim["application"],
        "stage": {
            "source_site": stage.source_site,
            "destination_site": stage.destination_site,
            "release_sha": stage.release_sha,
            "bundle_id": stage.bundle_id,
            "receipt_sha256": stage.receipt_sha256,
        },
        "static_assets_provenance": proof_value,
        "static_assets_provenance_sha256": sha256_bytes(proof_payload),
        "static_object": static_claim["artifact"],
        "files": files,
        "files_sha256": static_claim["files_sha256"],
        "controller_public_key_base64": controller_key,
    }


def _static_destination(*, static_release_parent: Path, claim: Mapping[str, Any]) -> Path:
    static_release_parent = _require_directory(
        static_release_parent,
        field="static release parent",
        private=False,
    )
    campaign_id = claim["campaign_id"]
    application = claim["application"]
    files_sha256 = claim["files_sha256"]
    if (
        not isinstance(campaign_id, str)
        or not CAMPAIGN_RE.fullmatch(campaign_id)
        or not isinstance(application, Mapping)
        or not isinstance(application.get("release_sha"), str)
        or not isinstance(files_sha256, str)
        or not SHA256_RE.fullmatch(files_sha256)
    ):
        raise StaticAssetInstallError("static asset destination identity is invalid")
    release_sha = application["release_sha"]
    try:
        release_sha = portable._application(application, field="static application")["release_sha"]
    except Exception as exc:
        raise StaticAssetInstallError("static asset destination application is invalid") from exc
    return static_release_parent / campaign_id / release_sha / files_sha256


def verify_static_install_inputs(
    *,
    stage_receipt_path: Path,
    bootstrap_receipt_path: Path,
    static_archive: Path,
    static_receive_receipt: Path,
    static_release_parent: Path,
) -> dict[str, Any]:
    """Read-only validation before making a WA-IR static root."""

    _require_root()
    claim = _static_claim_from_stage(
        stage_receipt_path=stage_receipt_path,
        bootstrap_receipt_path=bootstrap_receipt_path,
    )
    _load_receive_record(
        path=static_receive_receipt,
        expected_campaign_id=claim["campaign_id"],
        expected_object=claim["static_object"],
    )
    archive_sha256, archive_bytes = sha256_checked_file(
        static_archive,
        field="age-decrypted static asset archive",
        private=True,
        maximum=MAX_STATIC_ARCHIVE_BYTES,
    )
    if (
        archive_sha256 != claim["static_object"]["plaintext_sha256"]
        or archive_bytes != claim["static_object"]["plaintext_bytes"]
    ):
        raise StaticAssetInstallError("age-decrypted static archive does not match the signed Object Storage binding")
    destination = _static_destination(static_release_parent=static_release_parent, claim=claim)
    return {
        **claim,
        "static_archive": {"sha256": archive_sha256, "bytes": archive_bytes},
        "static_root": str(destination),
        "object_storage_action": False,
        "age_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
    }


def _write_file_from_tar(
    *,
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    expected: Mapping[str, Any],
    output_root: Path,
) -> None:
    path = _relative_asset_path(member.name)
    if path != expected["path"]:
        raise StaticAssetInstallError("static archive file order does not match the signed file manifest")
    if (
        not member.isreg()
        or member.issparse()
        or member.size != expected["bytes"]
        or member.size < 0
        or member.mode != 0o644
        or member.uid != 0
        or member.gid != 0
        or member.uname
        or member.gname
        or member.mtime != 0
        or member.pax_headers
        or member.linkname
    ):
        raise StaticAssetInstallError("static archive member is not deterministic or does not match the signed file manifest")
    target = output_root.joinpath(*PurePosixPath(path).parts)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = output_root
    for component in target.relative_to(output_root).parts[:-1]:
        current = current / component
        _require_directory(current, field="static extraction directory", private=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as exc:
        raise StaticAssetInstallError("cannot create extracted static asset") from exc
    payload = archive.extractfile(member)
    if payload is None:
        os.close(descriptor)
        raise StaticAssetInstallError("cannot read static archive member")
    digest = hashlib.sha256()
    written = 0
    try:
        while True:
            chunk = payload.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > member.size:
                raise StaticAssetInstallError("static archive member exceeds its signed size")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                count = os.write(descriptor, view)
                if count <= 0:  # pragma: no cover - os.write should not return zero.
                    raise OSError("short static asset write")
                view = view[count:]
        if written != member.size or digest.hexdigest() != expected["sha256"]:
            raise StaticAssetInstallError("static archive member does not match the signed file hash")
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    except OSError as exc:
        raise StaticAssetInstallError("cannot write extracted static asset") from exc
    finally:
        payload.close()
        os.close(descriptor)


def _finalize_static_tree(root: Path) -> None:
    directories: list[Path] = []
    for path, children, files in os.walk(root, topdown=True, followlinks=False):
        directory = Path(path)
        directories.append(directory)
        for name in children:
            child = directory / name
            metadata = child.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0:
                raise StaticAssetInstallError("static extraction tree contains an unsafe directory")
        for name in files:
            child = directory / name
            metadata = child.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o644
                or metadata.st_nlink != 1
            ):
                raise StaticAssetInstallError("static extraction tree contains an unsafe file")
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        os.chmod(directory, 0o755)
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _create_only_json(path: Path, value: Mapping[str, Any], *, field: str) -> bytes:
    if path.exists() or path.is_symlink():
        raise StaticAssetInstallError(f"refusing to overwrite {field}")
    payload = canonical_json_bytes(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise StaticAssetInstallError(f"cannot create {field}") from exc
    return payload


def install_verified_static_assets(
    *,
    stage_receipt_path: Path,
    bootstrap_receipt_path: Path,
    static_archive: Path,
    static_receive_receipt: Path,
    static_release_parent: Path,
    receipt_path: Path,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Extract one immutable verified static root and emit a root-only receipt."""

    verified = verify_static_install_inputs(
        stage_receipt_path=stage_receipt_path,
        bootstrap_receipt_path=bootstrap_receipt_path,
        static_archive=static_archive,
        static_receive_receipt=static_receive_receipt,
        static_release_parent=static_release_parent,
    )
    static_root = Path(verified["static_root"])
    receipt_path = _require_absolute(Path(receipt_path), field="static install receipt path")
    _require_directory(receipt_path.parent, field="static install receipt parent", private=True)
    if static_root.exists() or static_root.is_symlink():
        raise StaticAssetInstallError("refusing to overwrite an immutable static release root")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise StaticAssetInstallError("refusing to overwrite a static install receipt")
    static_root.parent.mkdir(parents=True, mode=0o755, exist_ok=False)
    _require_directory(static_root.parent.parent, field="static campaign release directory", private=False)
    _require_directory(static_root.parent, field="static application release directory", private=False)
    candidate = Path(tempfile.mkdtemp(prefix=".incoming-static-", dir=str(static_root.parent)))
    candidate.chmod(0o700)
    _require_directory(candidate, field="static extraction candidate", private=True)
    try:
        descriptor, opened = _open_checked_file(
            static_archive,
            field="age-decrypted static asset archive",
            private=True,
            maximum=MAX_STATIC_ARCHIVE_BYTES,
        )
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                with tarfile.open(fileobj=handle, mode="r|", tarinfo=_StaticTarInfo) as archive:
                    for expected in verified["files"]:
                        member = archive.next()
                        if member is None:
                            raise StaticAssetInstallError("static archive ends before its signed file manifest")
                        _write_file_from_tar(archive=archive, member=member, expected=expected, output_root=candidate)
                    if archive.next() is not None:
                        raise StaticAssetInstallError("static archive contains files absent from its signed file manifest")
            after = os.fstat(descriptor)
            if after.st_size != opened.st_size or after.st_dev != opened.st_dev or after.st_ino != opened.st_ino:
                raise StaticAssetInstallError("age-decrypted static archive changed while being extracted")
        finally:
            os.close(descriptor)
        _finalize_static_tree(candidate)
        # ``os.rename`` is non-overwriting because the destination was checked
        # absent and its parent is root-owned/non-writable by other users.
        os.rename(candidate, static_root)
        parent_descriptor = os.open(static_root.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        installed_at = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        unsigned = {
            "schema": STATIC_INSTALL_RECEIPT_SCHEMA,
            "status": "installed",
            "installed_at": installed_at,
            "campaign_id": verified["campaign_id"],
            "application": verified["application"],
            "stage": verified["stage"],
            "static_root": str(static_root),
            "archive": verified["static_archive"],
            "static_object": verified["static_object"],
            "files_sha256": verified["files_sha256"],
            "file_count": len(verified["files"]),
            "static_assets_provenance": verified["static_assets_provenance"],
            "static_assets_provenance_sha256": verified["static_assets_provenance_sha256"],
        }
        receipt = {**unsigned, "receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}
        _create_only_json(receipt_path, receipt, field="static install receipt")
        return {
            "status": "installed",
            "static_root": str(static_root),
            "receipt_path": str(receipt_path),
            "files_sha256": verified["files_sha256"],
            "file_count": len(verified["files"]),
            "object_storage_action": False,
            "age_action": False,
            "ssh_action": False,
            "docker_action": False,
            "service_changed": False,
        }
    except Exception:
        # Preserve a fresh root-only candidate for audit.  Removing it would
        # erase evidence of the exact failed input and needs a later decision.
        raise


def verify_installed_static_assets(
    *,
    receipt_path: Path,
    expected_application_release_sha: str,
    pinned_controller_public_key_base64: str,
) -> dict[str, Any]:
    """Revalidate a static install receipt and every readable static file."""

    _require_root()
    value, raw = _read_private_json(receipt_path, field="static install receipt")
    _fields(
        value,
        expected={
            "schema", "status", "installed_at", "campaign_id", "application", "stage", "static_root", "archive",
            "static_object", "files_sha256", "file_count", "static_assets_provenance",
            "static_assets_provenance_sha256", "receipt_sha256",
        },
        field="static install receipt",
    )
    if value.get("schema") != STATIC_INSTALL_RECEIPT_SCHEMA or value.get("status") != "installed":
        raise StaticAssetInstallError("static install receipt schema or status is unsupported")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value.get("receipt_sha256") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise StaticAssetInstallError("static install receipt hash is invalid")
    if raw != canonical_json_bytes(value) + b"\n":  # defensive clarity for this public entrypoint.
        raise StaticAssetInstallError("static install receipt is not canonical")
    application = value.get("application")
    try:
        application = portable._application(application, field="installed static application")
    except Exception as exc:
        raise StaticAssetInstallError("installed static application is invalid") from exc
    if application["release_sha"] != expected_application_release_sha:
        raise StaticAssetInstallError("installed static application release does not match the listener release")
    campaign_id = value.get("campaign_id")
    if not isinstance(campaign_id, str) or not CAMPAIGN_RE.fullmatch(campaign_id):
        raise StaticAssetInstallError("installed static campaign is invalid")
    proof = value.get("static_assets_provenance")
    if not isinstance(proof, Mapping):
        raise StaticAssetInstallError("installed static proof is invalid")
    proof_value = dict(proof)
    proof_payload = canonical_json_bytes(proof_value) + b"\n"
    if value.get("static_assets_provenance_sha256") != sha256_bytes(proof_payload):
        raise StaticAssetInstallError("installed static proof hash is invalid")
    try:
        static_claim = portable._static_assets_provenance(
            payload=proof_payload,
            pinned_controller_public_key_base64=pinned_controller_public_key_base64,
            expected_campaign_id=campaign_id,
            expected_application=application,
        )
    except Exception as exc:
        raise StaticAssetInstallError("installed static proof signature is invalid") from exc
    files = _proof_files(proof_value.get("files"))
    if (
        static_claim["artifact"] != value.get("static_object")
        or static_claim["files_sha256"] != value.get("files_sha256")
        or static_claim["file_count"] != value.get("file_count")
        or value.get("files_sha256") != sha256_bytes(canonical_json_bytes(files))
    ):
        raise StaticAssetInstallError("installed static receipt does not match its signed proof")
    static_root = _require_absolute(Path(value.get("static_root", "")), field="installed static root")
    expected_root = _static_destination(
        static_release_parent=static_root.parents[2],
        claim={"campaign_id": campaign_id, "application": application, "files_sha256": value["files_sha256"]},
    )
    if static_root != expected_root:
        raise StaticAssetInstallError("installed static root is not receipt-bound")
    _require_directory(static_root, field="installed static root", private=False)
    expected_paths = {item["path"] for item in files}
    observed_paths: set[str] = set()
    for root, directories, filenames in os.walk(static_root, topdown=True, followlinks=False):
        current = Path(root)
        _require_directory(current, field="installed static directory", private=False)
        for name in directories:
            metadata = (current / name).lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise StaticAssetInstallError("installed static tree contains a symlink")
        for name in filenames:
            path = current / name
            relative = path.relative_to(static_root).as_posix()
            metadata = path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o644
                or metadata.st_nlink != 1
            ):
                raise StaticAssetInstallError("installed static tree contains an unsafe file")
            observed_paths.add(relative)
    if observed_paths != expected_paths:
        raise StaticAssetInstallError("installed static tree file set does not match the signed proof")
    for item in files:
        digest, bytes_value = sha256_checked_file(
            static_root.joinpath(*PurePosixPath(item["path"]).parts),
            field="installed static file",
            private=False,
            maximum=MAX_STATIC_FILE_BYTES,
        )
        if (digest, bytes_value) != (item["sha256"], item["bytes"]):
            raise StaticAssetInstallError("installed static file does not match the signed proof")
    return {
        "status": "verified",
        "campaign_id": campaign_id,
        "application": application,
        "static_root": str(static_root),
        "files_sha256": value["files_sha256"],
        "file_count": len(files),
        "object_storage_action": False,
        "age_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "install"):
        command = subparsers.add_parser(name)
        command.add_argument("--stage-receipt", type=Path, required=True)
        command.add_argument("--bootstrap-receipt", type=Path, required=True)
        command.add_argument("--static-archive", type=Path, required=True)
        command.add_argument("--static-receive-receipt", type=Path, required=True)
        command.add_argument("--static-release-parent", type=Path, required=True)
        if name == "install":
            command.add_argument("--receipt", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_static_install_inputs(
                stage_receipt_path=args.stage_receipt,
                bootstrap_receipt_path=args.bootstrap_receipt,
                static_archive=args.static_archive,
                static_receive_receipt=args.static_receive_receipt,
                static_release_parent=args.static_release_parent,
            )
            result = {key: value for key, value in result.items() if key not in {"files", "static_assets_provenance"}}
            result["status"] = "verified"
        else:
            result = install_verified_static_assets(
                stage_receipt_path=args.stage_receipt,
                bootstrap_receipt_path=args.bootstrap_receipt,
                static_archive=args.static_archive,
                static_receive_receipt=args.static_receive_receipt,
                static_release_parent=args.static_release_parent,
                receipt_path=args.receipt,
            )
    except StaticAssetInstallError as exc:
        print(json.dumps({"error": str(exc), "error_class": type(exc).__name__, "status": "blocked"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
