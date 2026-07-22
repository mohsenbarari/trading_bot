#!/usr/bin/env python3
"""Pull WA-IR staging preflight inputs from Object Storage and run preflight.

This agent is intended to be copied once to WebApp-IR.  Normal operation should
then be a low-volume command execution only: the agent downloads release and
role materials itself, verifies them locally, installs only staging-owned paths,
executes the official host preflight, and optionally uploads the evidence with
a presigned URL.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


SCHEMA = "three-site-wa-ir-object-storage-preflight-v1"
ROLE = "webapp-ir"
RELEASE_ROOT = Path("/srv/trading-bot-three-site/releases")
CURRENT_LINK = Path("/srv/trading-bot-three-site/current")
SECURE_ROOT_BASE = Path("/root/secure-envs/trading-bot")
WORK_ROOT = Path("/tmp/three-site-wa-ir-preflight")
SSH_FREE_MAX_BYTES = 2 * 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ARVAN_OBJECT_STORAGE_HOST = "s3.ir-thr-at1.arvanstorage.ir"
FILE_TRANSFER_SCHEMA = "three-site-wa-ir-object-storage-file-v1"
FILE_TRANSFER_ROOT = Path("/run/writer-witness-matrix")
FILE_TRANSFER_IDENTITY = Path(
    "/root/secure-envs/trading-bot/wa-ir-object-storage-age-identity.txt"
)
FILE_TRANSFER_NAMES = frozenset(
    {"client.env", "old.env", "witness-ca.crt", "client.py"}
)
FILE_TRANSFER_MAX_BYTES = 1024 * 1024
ROLE_MATERIAL_NAMES = frozenset(
    {
        "planned-inventory.json",
        "planned-inventory-approval.json",
        "inventory-signers.json",
        "roles/webapp-ir.compose.yml",
        "roles/webapp-ir.env",
        "secrets/staging-dr-ca.crt",
        "secrets/webapp-ir-dr.crt",
        "secrets/webapp-ir-dr.key",
        "secrets/staging-dr-blob-s3.json",
        "secrets/staging-dr-blob-keyring.json",
    }
)
RUNTIME_SECRET_ROOT = Path("/etc/trading-bot-three-site/secrets")
RUNTIME_SECRET_MODES = {
    "staging-dr-ca.crt": 0o644,
    "webapp-ir-dr.crt": 0o644,
    "webapp-ir-dr.key": 0o600,
    "staging-dr-blob-s3.json": 0o600,
    "staging-dr-blob-keyring.json": 0o600,
}
ROLE_MATERIAL_MAX_BYTES = 64 * 1024 * 1024


class AgentError(RuntimeError):
    pass


def _strict_object(pairs):  # noqa: ANN001
    result = {}
    for key, value in pairs:
        if key in result:
            raise AgentError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, json.JSONDecodeError, AgentError) as exc:
        raise AgentError("manifest is unreadable or not strict JSON") from exc
    if not isinstance(payload, dict):
        raise AgentError("manifest root must be an object")
    required = {
        "schema",
        "role",
        "release_sha",
        "secure_materials_dir",
        "release_bundle",
        "role_materials",
        "preflight_output",
    }
    optional = {"evidence_upload", "age_identity"}
    if set(payload) - required - optional or not required <= set(payload):
        raise AgentError("manifest fields are invalid")
    if payload["schema"] != SCHEMA or payload["role"] != ROLE:
        raise AgentError("manifest schema/role is invalid")
    if not RELEASE_RE.fullmatch(str(payload["release_sha"])):
        raise AgentError("release_sha must be one exact 40-character Git SHA")
    secure_dir = Path(str(payload["secure_materials_dir"]))
    if not _is_safe_child(secure_dir, SECURE_ROOT_BASE):
        raise AgentError("secure_materials_dir is outside the approved root")
    output = Path(str(payload["preflight_output"]))
    if not _is_safe_child(output, secure_dir) or output.name != "webapp-ir-fresh-preflight.json":
        raise AgentError("preflight_output must be the approved WA-IR evidence path")
    if "age_identity" in payload:
        age_identity = Path(str(payload["age_identity"]))
        if (
            age_identity != FILE_TRANSFER_IDENTITY
            or not _is_safe_child(age_identity, SECURE_ROOT_BASE)
        ):
            raise AgentError("age_identity differs from the pinned WA-IR identity")
    return payload


def _is_safe_child(path: Path, base: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(base.resolve(strict=False))
    except ValueError:
        return False
    return path.is_absolute()


def _sha256(path: Path, *, max_bytes: int = SSH_FREE_MAX_BYTES) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise AgentError("artifact exceeds the maximum approved size")
            digest.update(chunk)
    return digest.hexdigest(), size


def _artifact(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentError(f"{label} artifact must be an object")
    required = {"url", "sha256", "bytes"}
    optional = {"ciphertext_sha256", "ciphertext_bytes", "encrypted", "plaintext_sha256"}
    if set(value) - required - optional or not required <= set(value):
        raise AgentError(f"{label} artifact fields are invalid")
    _validate_object_storage_url(str(value["url"]), label=f"{label} artifact")
    if not SHA256_RE.fullmatch(str(value["sha256"])):
        raise AgentError(f"{label} artifact SHA-256 is invalid")
    if isinstance(value["bytes"], bool) or not isinstance(value["bytes"], int) or value["bytes"] <= 0:
        raise AgentError(f"{label} artifact byte count is invalid")
    if value["bytes"] > SSH_FREE_MAX_BYTES:
        raise AgentError(f"{label} artifact is larger than the agent limit")
    encrypted = bool(value.get("encrypted", False))
    if encrypted:
        if not SHA256_RE.fullmatch(str(value.get("ciphertext_sha256", ""))):
            raise AgentError(f"{label} encrypted artifact lacks ciphertext_sha256")
        if (
            isinstance(value.get("ciphertext_bytes"), bool)
            or not isinstance(value.get("ciphertext_bytes"), int)
            or int(value["ciphertext_bytes"]) <= 0
        ):
            raise AgentError(f"{label} encrypted artifact lacks ciphertext_bytes")
    return value


def _validate_object_storage_url(url: str, *, label: str) -> None:
    """Accept only presigned HTTPS URLs for the fixed Arvan data plane."""
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise AgentError(f"{label} URL is malformed") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    approved_host = hostname == ARVAN_OBJECT_STORAGE_HOST or hostname.endswith(
        "." + ARVAN_OBJECT_STORAGE_HOST
    )
    if (
        parsed.scheme != "https"
        or not approved_host
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or parsed.fragment
    ):
        raise AgentError(
            f"{label} URL must use the approved Arvan Object Storage endpoint"
        )


def download(artifact: dict[str, Any], *, label: str, output: Path) -> Path:
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.download")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(
        str(artifact["url"]),
        headers={"User-Agent": "trading-bot-wa-ir-preflight-agent/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response, temporary.open("xb") as target:
            length = response.headers.get("Content-Length")
            expected_bytes = (
                artifact["ciphertext_bytes"] if artifact.get("encrypted") else artifact["bytes"]
            )
            if length is not None and int(length) != expected_bytes:
                raise AgentError(f"{label} Content-Length differs from manifest")
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > SSH_FREE_MAX_BYTES:
                    raise AgentError(f"{label} exceeds maximum size while downloading")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    except (OSError, urllib.error.URLError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise AgentError(f"{label} download failed") from exc
    os.chmod(temporary, 0o600)
    observed_hash, observed_size = _sha256(temporary)
    expected_hash = artifact["ciphertext_sha256"] if artifact.get("encrypted") else artifact["sha256"]
    expected_size = artifact["ciphertext_bytes"] if artifact.get("encrypted") else artifact["bytes"]
    if observed_hash != expected_hash or observed_size != expected_size:
        temporary.unlink(missing_ok=True)
        raise AgentError(f"{label} downloaded bytes differ from manifest")
    temporary.replace(output)
    return output


def decrypt_if_needed(source: Path, artifact: dict[str, Any], *, identity: Path | None) -> Path:
    if not artifact.get("encrypted"):
        return source
    if identity is None:
        raise AgentError("encrypted artifact requires age_identity")
    try:
        identity_metadata = identity.lstat()
    except OSError as exc:
        raise AgentError("age identity file is missing or has unsafe permissions") from exc
    if (
        identity.is_symlink()
        or not stat.S_ISREG(identity_metadata.st_mode)
        or identity_metadata.st_uid != 0
        or identity_metadata.st_nlink != 1
        or stat.S_IMODE(identity_metadata.st_mode) != 0o600
    ):
        raise AgentError("age identity file is missing or has unsafe permissions")
    age = shutil.which("age", path="/usr/bin:/bin")
    if not age:
        raise AgentError("age executable is unavailable")
    plaintext = source.with_suffix(source.suffix + ".plain")
    plaintext.unlink(missing_ok=True)
    result = subprocess.run(
        [age, "--decrypt", "--identity", str(identity), "--output", str(plaintext), str(source)],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        timeout=1800,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0:
        plaintext.unlink(missing_ok=True)
        raise AgentError("age decryption failed closed")
    os.chmod(plaintext, 0o600)
    observed_hash, observed_size = _sha256(plaintext)
    if observed_hash != artifact["sha256"] or observed_size != artifact["bytes"]:
        plaintext.unlink(missing_ok=True)
        raise AgentError("decrypted artifact differs from manifest")
    return plaintext


def install_release(bundle: Path, *, release_sha: str) -> Path:
    release_dir = RELEASE_ROOT / release_sha
    if release_dir.exists():
        if not (release_dir / ".git").is_dir():
            raise AgentError("existing release path is not a Git checkout")
        current_sha = _run(["git", "-C", str(release_dir), "rev-parse", "HEAD"]).strip()
        clean = _run(["git", "-C", str(release_dir), "status", "--porcelain=v1", "--untracked-files=all"])
        if current_sha != release_sha or clean:
            raise AgentError("existing release checkout is not the approved clean SHA")
    else:
        RELEASE_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
        _run(["git", "clone", str(bundle), str(release_dir)], timeout=1800)
        _run(["git", "-C", str(release_dir), "checkout", "--detach", release_sha], timeout=120)
        clean = _run(["git", "-C", str(release_dir), "status", "--porcelain=v1", "--untracked-files=all"])
        if clean:
            raise AgentError("new release checkout is dirty")
    temporary_link = CURRENT_LINK.with_name(".current.next")
    temporary_link.unlink(missing_ok=True)
    os.symlink(release_dir, temporary_link)
    os.replace(temporary_link, CURRENT_LINK)
    return release_dir


def _safe_tar_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    names = [member.name for member in members]
    total_size = 0
    for member in members:
        target = Path(member.name)
        if target.is_absolute() or ".." in target.parts or member.issym() or member.islnk() or member.isdev():
            raise AgentError("role materials archive contains an unsafe member")
        if not member.isfile():
            raise AgentError("role materials archive contains an unsupported member type")
        total_size += member.size
        if member.size <= 0 or total_size > ROLE_MATERIAL_MAX_BYTES:
            raise AgentError("role materials archive exceeds its fixed content bound")
    if len(names) != len(set(names)) or set(names) != ROLE_MATERIAL_NAMES:
        raise AgentError("role materials archive differs from the exact allowlist")
    return members


def install_role_materials(archive_path: Path, *, secure_dir: Path) -> None:
    secure_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    secure_metadata = secure_dir.lstat()
    if (
        secure_dir.is_symlink()
        or not stat.S_ISDIR(secure_metadata.st_mode)
        or secure_metadata.st_uid != 0
        or stat.S_IMODE(secure_metadata.st_mode) & 0o077
    ):
        raise AgentError("secure role-material directory is not root-only")
    for child_name in ("roles", "secrets"):
        child = secure_dir / child_name
        if child.exists() or child.is_symlink():
            child_metadata = child.lstat()
            if (
                child.is_symlink()
                or not stat.S_ISDIR(child_metadata.st_mode)
                or child_metadata.st_uid != 0
                or stat.S_IMODE(child_metadata.st_mode) & 0o077
            ):
                raise AgentError("secure role-material child directory is unsafe")
        else:
            child.mkdir(mode=0o700)
    with tarfile.open(archive_path, "r:*") as archive:
        members = _safe_tar_members(archive)
        for member in members:
            destination = secure_dir / member.name
            if destination.exists() or destination.is_symlink():
                metadata = destination.lstat()
                if (
                    destination.is_symlink()
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != 0
                    or metadata.st_nlink != 1
                ):
                    raise AgentError("existing role-material destination is unsafe")
        archive.extractall(secure_dir, members=members, filter="data")
    required = [
        secure_dir / "planned-inventory.json",
        secure_dir / "planned-inventory-approval.json",
        secure_dir / "inventory-signers.json",
        secure_dir / "roles" / "webapp-ir.compose.yml",
        secure_dir / "roles" / "webapp-ir.env",
        secure_dir / "secrets" / "staging-dr-ca.crt",
        secure_dir / "secrets" / "webapp-ir-dr.crt",
        secure_dir / "secrets" / "webapp-ir-dr.key",
        secure_dir / "secrets" / "staging-dr-blob-s3.json",
        secure_dir / "secrets" / "staging-dr-blob-keyring.json",
    ]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise AgentError(f"role materials missing required file: {path.name}")
    os.chmod(secure_dir / "roles" / "webapp-ir.compose.yml", 0o640)
    for path in required:
        if path.name != "webapp-ir.compose.yml":
            os.chmod(path, 0o644 if path.suffix == ".crt" else 0o600)

    RUNTIME_SECRET_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if RUNTIME_SECRET_ROOT.is_symlink():
        raise AgentError("runtime secret root must not be a symlink")
    os.chmod(RUNTIME_SECRET_ROOT, 0o700)
    for name, mode in RUNTIME_SECRET_MODES.items():
        source = secure_dir / "secrets" / name
        destination = RUNTIME_SECRET_ROOT / name
        temporary = destination.with_name(f".{name}.object-storage-next")
        temporary.unlink(missing_ok=True)
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=64 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    directory_fd = os.open(
        RUNTIME_SECRET_ROOT, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def run_preflight(*, release_dir: Path, secure_dir: Path, output: Path) -> dict[str, Any]:
    command = [
        "python3",
        str(release_dir / "scripts" / "verify_three_site_staging_host_identity.py"),
        "--role", ROLE,
        "--stage", "fresh-preflight",
        "--repo", str(CURRENT_LINK),
        "--canonical-compose", str(CURRENT_LINK / "deploy" / "staging" / "docker-compose.three-site.yml"),
        "--role-compose", str(secure_dir / "roles" / "webapp-ir.compose.yml"),
        "--env-file", str(secure_dir / "roles" / "webapp-ir.env"),
        "--inventory", str(secure_dir / "planned-inventory.json"),
        "--approval", str(secure_dir / "planned-inventory-approval.json"),
        "--signer-policy", str(secure_dir / "inventory-signers.json"),
        "--snapshot-output", str(output),
    ]
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0:
        raise AgentError(f"WA-IR fresh preflight failed: {result.stdout.strip() or result.stderr.strip()}")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AgentError("WA-IR fresh preflight returned non-JSON output") from exc
    if parsed.get("status") != "verified" or parsed.get("role") != ROLE:
        raise AgentError("WA-IR fresh preflight did not verify the expected role")
    return parsed


def upload_evidence(upload: Any, source: Path) -> dict[str, Any] | None:
    if upload is None:
        return None
    if not isinstance(upload, dict) or set(upload) != {"url", "method", "headers", "expected_status"}:
        raise AgentError("evidence_upload fields are invalid")
    _validate_object_storage_url(str(upload["url"]), label="evidence upload")
    method = str(upload["method"])
    if method not in {"PUT", "POST"}:
        raise AgentError("evidence upload method must be PUT or POST")
    headers = upload["headers"]
    expected = upload["expected_status"]
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise AgentError("evidence upload headers are invalid")
    if any(
        key.lower() in {"authorization", "proxy-authorization", "cookie"}
        for key in headers
    ):
        raise AgentError("evidence upload headers contain forbidden credentials")
    if not isinstance(expected, list) or not expected or any(not isinstance(item, int) for item in expected):
        raise AgentError("evidence upload expected_status is invalid")
    digest, size = _sha256(source, max_bytes=16 * 1024 * 1024)
    body = source.read_bytes()
    request = urllib.request.Request(str(upload["url"]), data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = response.status
            response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.read()
    except urllib.error.URLError as exc:
        raise AgentError("evidence upload failed") from exc
    if status not in expected:
        raise AgentError(f"evidence upload returned unexpected HTTP status {status}")
    return {"status": "uploaded", "http_status": status, "sha256": digest, "bytes": size}


def _run(arguments: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(
        arguments,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0:
        raise AgentError(f"command failed: {Path(arguments[0]).name}")
    return result.stdout


def execute(manifest: dict[str, Any]) -> dict[str, Any]:
    release_sha = str(manifest["release_sha"])
    secure_dir = Path(str(manifest["secure_materials_dir"]))
    output = Path(str(manifest["preflight_output"]))
    age_identity = Path(str(manifest["age_identity"])) if "age_identity" in manifest else None
    WORK_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=WORK_ROOT) as raw:
        run_root = Path(raw)
        release_artifact = _artifact(manifest["release_bundle"], label="release bundle")
        materials_artifact = _artifact(manifest["role_materials"], label="role materials")
        release_download = download(release_artifact, label="release bundle", output=run_root / "release.bundle")
        materials_download = download(materials_artifact, label="role materials", output=run_root / "role-materials.tar")
        release_bundle = decrypt_if_needed(release_download, release_artifact, identity=age_identity)
        role_materials = decrypt_if_needed(materials_download, materials_artifact, identity=age_identity)
        release_dir = install_release(release_bundle, release_sha=release_sha)
        install_role_materials(role_materials, secure_dir=secure_dir)
        preflight = run_preflight(release_dir=release_dir, secure_dir=secure_dir, output=output)
        evidence_upload = upload_evidence(manifest.get("evidence_upload"), output)
    evidence_hash, evidence_size = _sha256(output, max_bytes=16 * 1024 * 1024)
    return {
        "status": "wa-ir-preflight-complete",
        "role": ROLE,
        "release_sha": release_sha,
        "release_dir": str(RELEASE_ROOT / release_sha),
        "secure_materials_dir": str(secure_dir),
        "preflight": preflight,
        "evidence_path": str(output),
        "evidence_sha256": evidence_hash,
        "evidence_bytes": evidence_size,
        "evidence_upload": evidence_upload,
    }


def load_file_transfer_manifest(encoded: str) -> dict[str, Any]:
    if len(encoded) > 32 * 1024:
        raise AgentError("file-transfer manifest exceeds its control-plane bound")
    try:
        raw = base64.b64decode(encoded, validate=True)
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, AgentError) as exc:
        raise AgentError("file-transfer manifest is not strict base64 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "role", "campaign_tag", "destination", "mode", "artifact"
    }:
        raise AgentError("file-transfer manifest fields are invalid")
    if payload["schema"] != FILE_TRANSFER_SCHEMA or payload["role"] != ROLE:
        raise AgentError("file-transfer manifest schema/role is invalid")
    if not re.fullmatch(r"wwm_[0-9a-f]{12}", str(payload["campaign_tag"])):
        raise AgentError("file-transfer campaign tag is invalid")
    destination = Path(str(payload["destination"]))
    campaign_root = FILE_TRANSFER_ROOT / str(payload["campaign_tag"])
    if (
        not _is_safe_child(destination, campaign_root)
        or destination.parent != campaign_root
        or destination.name not in FILE_TRANSFER_NAMES
    ):
        raise AgentError("file-transfer destination is outside the campaign allowlist")
    if payload["mode"] not in {0o600, 0o700}:
        raise AgentError("file-transfer destination mode is invalid")
    _artifact(payload["artifact"], label="file transfer")
    if payload["artifact"].get("encrypted") is not True:
        raise AgentError("file-transfer payload must be age encrypted")
    if (
        int(payload["artifact"]["bytes"]) > FILE_TRANSFER_MAX_BYTES
        or int(payload["artifact"].get("ciphertext_bytes", 0))
        > FILE_TRANSFER_MAX_BYTES + 64 * 1024
    ):
        raise AgentError("file-transfer payload exceeds its fixed size bound")
    return payload


def receive_file_transfer(payload: dict[str, Any]) -> dict[str, Any]:
    destination = Path(str(payload["destination"]))
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise AgentError("file-transfer campaign directory is unavailable or unsafe")
    parent_metadata = destination.parent.stat()
    if parent_metadata.st_uid != 0 or stat.S_IMODE(parent_metadata.st_mode) & 0o077:
        raise AgentError("file-transfer campaign directory is not root-only")
    artifact = _artifact(payload["artifact"], label="file transfer")
    WORK_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="file-", dir=WORK_ROOT) as raw:
        encrypted = download(artifact, label="file transfer", output=Path(raw) / "payload.age")
        plaintext = decrypt_if_needed(
            encrypted,
            artifact,
            identity=FILE_TRANSFER_IDENTITY,
        )
        temporary = destination.with_name(f".{destination.name}.object-storage-next")
        temporary.unlink(missing_ok=True)
        with plaintext.open("rb") as source, temporary.open("xb") as target:
            shutil.copyfileobj(source, target, length=64 * 1024)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, int(payload["mode"]))
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    digest, size = _sha256(destination, max_bytes=16 * 1024 * 1024)
    if digest != artifact["sha256"] or size != artifact["bytes"]:
        raise AgentError("installed file-transfer payload failed final verification")
    return {
        "status": "wa-ir-file-installed",
        "role": ROLE,
        "campaign_tag": payload["campaign_tag"],
        "destination_name": destination.name,
        "sha256": digest,
        "bytes": size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--manifest", type=Path)
    selection.add_argument("--receive-file-json-b64")
    args = parser.parse_args(argv)
    try:
        if args.manifest is not None:
            result = execute(load_manifest(args.manifest))
        else:
            result = receive_file_transfer(
                load_file_transfer_manifest(str(args.receive_file_json_b64))
            )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
