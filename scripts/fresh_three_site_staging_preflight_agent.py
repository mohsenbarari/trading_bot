#!/usr/bin/env python3
"""Stage one fresh role and run its no-current, no-service host preflight.

This standalone bootstrap agent is intentionally narrower than the retired
WA-IR agent.  It never writes ``current``, runtime secret roots, Docker,
systemd, volumes, or Object Storage.  It accepts one role-scoped package,
installs it below a new campaign-owned secure directory, and runs the checked
out release's ``fresh-preflight`` verifier against that checkout directly.
"""

from __future__ import annotations

import argparse
import base64
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
import urllib.parse
import urllib.request
from uuid import UUID


sys.dont_write_bytecode = True

SCHEMA = "three-site-fresh-role-preflight-v1"
PACKAGE_SCHEMA = "three-site-fresh-role-package-v1"
ROLES = frozenset({"bot-fi", "webapp-fi", "webapp-ir", "witness"})
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
DEPLOYMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]+$")
ARVAN_HOST = "s3.ir-thr-at1.arvanstorage.ir"
RELEASE_ROOT = Path("/srv/trading-bot-three-site/releases")
SECURE_ROOT = Path("/root/secure-envs/trading-bot/three-site")
WORK_ROOT = Path("/tmp/three-site-fresh-preflight")
MAX_RELEASE_BYTES = 2 * 1024 * 1024 * 1024
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class FreshPreflightAgentError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FreshPreflightAgentError("JSON contains duplicate fields")
        result[key] = value
    return result


def _strict_json(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        result = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, FreshPreflightAgentError):
        raise FreshPreflightAgentError(f"{label} is not strict JSON") from None
    if not isinstance(result, dict):
        raise FreshPreflightAgentError(f"{label} must be an object")
    return result


def _safe_child(path: Path, base: Path) -> bool:
    return (
        path.is_absolute()
        and ".." not in path.parts
        and Path(os.path.normpath(path)) == path
        and path != base
        and path.parent != path
        and str(path).startswith(f"{base}/")
    )


def _require_root_private_ancestors(path: Path) -> None:
    """Bind bootstrap storage to root-owned, non-link directories only."""

    if not path.is_absolute() or ".." in path.parts:
        raise FreshPreflightAgentError("secure path is not normalized")
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError:
            raise FreshPreflightAgentError("secure path ancestor is unavailable") from None
        if (
            current.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise FreshPreflightAgentError("secure path ancestor is unsafe")


def _canonical_campaign(value: Any) -> str:
    try:
        canonical = str(UUID(str(value)))
    except ValueError:
        raise FreshPreflightAgentError("campaign ID is not canonical") from None
    if canonical != value:
        raise FreshPreflightAgentError("campaign ID is not canonical")
    return canonical


def _validate_url(value: Any, *, label: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(value))
    except ValueError:
        raise FreshPreflightAgentError(f"{label} URL is malformed") from None
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not (hostname == ARVAN_HOST or hostname.endswith("." + ARVAN_HOST))
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path or parsed.fragment
    ):
        raise FreshPreflightAgentError(f"{label} URL is not an approved Arvan HTTPS URL")
    return str(value)


def _artifact(value: Any, *, label: str, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "url", "plaintext_sha256", "plaintext_bytes", "ciphertext_sha256", "ciphertext_bytes", "age_recipient"
    }:
        raise FreshPreflightAgentError(f"{label} artifact fields are invalid")
    if (
        SHA256_RE.fullmatch(str(value["plaintext_sha256"])) is None
        or SHA256_RE.fullmatch(str(value["ciphertext_sha256"])) is None
        or not isinstance(value["plaintext_bytes"], int)
        or not isinstance(value["ciphertext_bytes"], int)
        or not 1 <= value["plaintext_bytes"] <= limit
        or not 1 <= value["ciphertext_bytes"] <= limit + 65536
        or AGE_RECIPIENT_RE.fullmatch(str(value["age_recipient"])) is None
    ):
        raise FreshPreflightAgentError(f"{label} artifact identity is invalid")
    _validate_url(value["url"], label=label)
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError
        payload = _strict_json(path.read_bytes(), label="bootstrap manifest")
    except (OSError, ValueError):
        raise FreshPreflightAgentError("bootstrap manifest is unavailable or unsafe") from None
    required = {
        "schema", "role", "campaign_id", "deployment_id", "release_sha",
        "age_identity", "secure_dir", "release_bundle", "role_package", "evidence_output",
    }
    if set(payload) != required or payload.get("schema") != SCHEMA or payload.get("role") not in ROLES:
        raise FreshPreflightAgentError("bootstrap manifest shape is invalid")
    campaign = _canonical_campaign(payload["campaign_id"])
    deployment = str(payload["deployment_id"])
    role = str(payload["role"])
    release = str(payload["release_sha"])
    if DEPLOYMENT_RE.fullmatch(deployment) is None or RELEASE_RE.fullmatch(release) is None:
        raise FreshPreflightAgentError("bootstrap campaign identity is invalid")
    secure = Path(str(payload["secure_dir"]))
    expected_secure = SECURE_ROOT / campaign / deployment / role
    if secure != expected_secure or not _safe_child(secure, SECURE_ROOT):
        raise FreshPreflightAgentError("bootstrap secure directory is not canonical")
    identity = Path(str(payload["age_identity"]))
    if identity != secure / "bootstrap.agekey":
        raise FreshPreflightAgentError("bootstrap age identity is not canonical")
    evidence = Path(str(payload["evidence_output"]))
    if evidence != secure / "evidence" / "fresh-preflight.json":
        raise FreshPreflightAgentError("preflight evidence path is not canonical")
    _artifact(payload["release_bundle"], label="release", limit=MAX_RELEASE_BYTES)
    _artifact(payload["role_package"], label="role package", limit=MAX_PACKAGE_BYTES)
    return payload


def _digest(path: Path, *, limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise FreshPreflightAgentError("artifact exceeds its fixed bound")
            digest.update(chunk)
    if not size:
        raise FreshPreflightAgentError("artifact is empty")
    return digest.hexdigest(), size


def _download(artifact: dict[str, Any], *, output: Path, label: str, limit: int) -> Path:
    request = urllib.request.Request(str(artifact["url"]), method="GET")
    temporary = output.with_name(f".{output.name}.download")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o600)
        digest, size = _digest(temporary, limit=limit + 65536)
        if digest != artifact["ciphertext_sha256"] or size != artifact["ciphertext_bytes"]:
            raise FreshPreflightAgentError(f"{label} ciphertext differs from manifest")
        os.replace(temporary, output)
        return output
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _decrypt(source: Path, *, artifact: dict[str, Any], identity: Path, output: Path, limit: int) -> Path:
    try:
        metadata = identity.lstat()
    except OSError:
        raise FreshPreflightAgentError("bootstrap age identity is unavailable") from None
    if (
        identity.is_symlink() or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0 or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise FreshPreflightAgentError("bootstrap age identity is unsafe")
    age = shutil.which("age", path=SAFE_ENV["PATH"])
    if age is None:
        raise FreshPreflightAgentError("age is unavailable")
    result = subprocess.run(
        [age, "--decrypt", "--identity", str(identity), "--output", str(output), str(source)],
        check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=1800, env=SAFE_ENV,
    )
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise FreshPreflightAgentError("age decryption failed")
    output.chmod(0o600)
    digest, size = _digest(output, limit=limit)
    if digest != artifact["plaintext_sha256"] or size != artifact["plaintext_bytes"]:
        output.unlink(missing_ok=True)
        raise FreshPreflightAgentError("decrypted artifact differs from manifest")
    return output


def _run(arguments: list[str], *, timeout: int = 1800) -> str:
    result = subprocess.run(
        arguments, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, timeout=timeout, env=SAFE_ENV,
    )
    if result.returncode != 0:
        raise FreshPreflightAgentError(f"required command failed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _install_release(bundle: Path, *, release: str) -> Path:
    destination = RELEASE_ROOT / release
    if destination.exists():
        if destination.is_symlink() or _run(["git", "-C", str(destination), "rev-parse", "HEAD"]) != release:
            raise FreshPreflightAgentError("existing release does not match the approved SHA")
        if _run(["git", "-C", str(destination), "status", "--porcelain=v1", "--untracked-files=all"]):
            raise FreshPreflightAgentError("existing release checkout is not clean")
        return destination
    RELEASE_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
    _run(["git", "clone", "--no-checkout", str(bundle), str(destination)])
    _run(["git", "-C", str(destination), "checkout", "--detach", release])
    if _run(["git", "-C", str(destination), "status", "--porcelain=v1", "--untracked-files=all"]):
        raise FreshPreflightAgentError("new release checkout is not clean")
    return destination


def _safe_members(archive: tarfile.TarFile, *, expected: set[str]) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    names = {member.name for member in members}
    if names != expected or len(members) != len(names):
        raise FreshPreflightAgentError("role package member closure differs")
    total = 0
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts or not member.isfile() or member.issym() or member.islnk() or member.isdev():
            raise FreshPreflightAgentError("role package contains an unsafe member")
        total += member.size
        if member.size <= 0 or total > MAX_PACKAGE_BYTES:
            raise FreshPreflightAgentError("role package exceeds its fixed bound")
    return members


def _package_manifest(archive: tarfile.TarFile) -> dict[str, Any]:
    member = archive.getmember("role-package-manifest.json")
    handle = archive.extractfile(member)
    if handle is None:
        raise FreshPreflightAgentError("role package manifest is unavailable")
    return _strict_json(handle.read(), label="role package manifest")


def _verify_installed_role_closure(
    *, secure: Path, role: str, campaign: str, deployment: str, files: set[str]
) -> None:
    forbidden = {"secrets/staging-dr-ca.key"}
    if role in {"bot-fi", "witness"}:
        forbidden.update(
            {"secrets/staging-dr-blob-s3.json", "secrets/staging-dr-blob-keyring.json"}
        )
    if forbidden & files:
        raise FreshPreflightAgentError("role package assigns forbidden secret authority")
    env_path = secure / "roles" / f"{role}.env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise FreshPreflightAgentError("role environment is unavailable") from None
    runtime_root = f"/etc/trading-bot-three-site/campaigns/{campaign}/{deployment}/secrets/"
    referenced: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not name or "\x00" in value:
            raise FreshPreflightAgentError("role environment is malformed")
        if value.startswith(runtime_root):
            filename = value.removeprefix(runtime_root)
            if not filename or "/" in filename:
                raise FreshPreflightAgentError("role environment secret reference is unsafe")
            referenced.add(f"secrets/{filename}")
    packaged_secrets = {name for name in files if name.startswith("secrets/")}
    if packaged_secrets != referenced:
        raise FreshPreflightAgentError("role package secret closure differs from its environment")


def _install_role_package(package: Path, *, manifest: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    secure = Path(str(manifest["secure_dir"]))
    identity = Path(str(manifest["age_identity"]))
    _require_root_private_ancestors(secure)
    if not secure.exists() or secure.is_symlink():
        raise FreshPreflightAgentError("campaign secure directory is unavailable")
    metadata = secure.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or {child.name for child in secure.iterdir()} != {identity.name}
    ):
        raise FreshPreflightAgentError("campaign secure directory has unexpected residue")
    with tarfile.open(package, "r:*") as archive:
        package_manifest = _package_manifest(archive)
        required = {"schema", "role", "campaign_id", "deployment_id", "release_sha", "files"}
        if set(package_manifest) != required or package_manifest.get("schema") != PACKAGE_SCHEMA:
            raise FreshPreflightAgentError("role package manifest shape is invalid")
        for key in ("role", "campaign_id", "deployment_id", "release_sha"):
            if package_manifest.get(key) != manifest[key]:
                raise FreshPreflightAgentError("role package identity differs from bootstrap manifest")
        files = package_manifest.get("files")
        if not isinstance(files, dict) or "role-package-manifest.json" in files:
            raise FreshPreflightAgentError("role package file list is invalid")
        expected = set(files) | {"role-package-manifest.json"}
        required_control = {
            "planned-inventory.json", "planned-inventory-approval.json",
            "human-approval-policy.json", f"roles/{manifest['role']}.compose.yml",
            f"roles/{manifest['role']}.env",
        }
        if not required_control <= set(files) or any(
            not (
                name in required_control
                or name.startswith("secrets/") and name.count("/") == 1
            )
            for name in files
        ):
            raise FreshPreflightAgentError("role package file names are invalid")
        members = _safe_members(archive, expected=expected)
        for member in members:
            target = secure / member.name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise FreshPreflightAgentError("role package would overwrite existing material")
            source = archive.extractfile(member)
            if source is None:
                raise FreshPreflightAgentError("role package member is unreadable")
            payload = source.read(MAX_PACKAGE_BYTES + 1)
            if len(payload) != member.size:
                raise FreshPreflightAgentError("role package member was truncated")
            if member.name != "role-package-manifest.json":
                metadata = files[member.name]
                if not isinstance(metadata, dict) or set(metadata) != {"sha256", "mode", "bytes"}:
                    raise FreshPreflightAgentError("role package file metadata is invalid")
                if hashlib.sha256(payload).hexdigest() != metadata["sha256"] or len(payload) != metadata["bytes"]:
                    raise FreshPreflightAgentError("role package member digest differs")
                mode = metadata["mode"]
                if mode not in {0o600, 0o640, 0o644}:
                    raise FreshPreflightAgentError("role package member mode is invalid")
            else:
                mode = 0o600
            with target.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(mode)
    _verify_installed_role_closure(
        secure=secure,
        role=str(manifest["role"]),
        campaign=str(manifest["campaign_id"]),
        deployment=str(manifest["deployment_id"]),
        files=set(files),
    )
    return secure, package_manifest


def execute(manifest: dict[str, Any]) -> dict[str, Any]:
    role = str(manifest["role"])
    identity = Path(str(manifest["age_identity"]))
    WORK_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=WORK_ROOT) as raw:
        work = Path(raw)
        release_cipher = _download(_artifact(manifest["release_bundle"], label="release", limit=MAX_RELEASE_BYTES), output=work / "release.age", label="release", limit=MAX_RELEASE_BYTES)
        package_cipher = _download(_artifact(manifest["role_package"], label="role package", limit=MAX_PACKAGE_BYTES), output=work / "package.age", label="role package", limit=MAX_PACKAGE_BYTES)
        release = _decrypt(release_cipher, artifact=manifest["release_bundle"], identity=identity, output=work / "release.bundle", limit=MAX_RELEASE_BYTES)
        package = _decrypt(package_cipher, artifact=manifest["role_package"], identity=identity, output=work / "package.tar", limit=MAX_PACKAGE_BYTES)
        release_dir = _install_release(release, release=str(manifest["release_sha"]))
        secure, package_manifest = _install_role_package(package, manifest=manifest)
        output = Path(str(manifest["evidence_output"]))
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        command = [
            "python3", str(release_dir / "scripts/verify_three_site_staging_host_identity.py"),
            "--role", role, "--stage", "fresh-preflight", "--repo", str(release_dir),
            "--canonical-compose", str(release_dir / "deploy/staging/docker-compose.three-site.yml"),
            "--role-compose", str(secure / f"roles/{role}.compose.yml"),
            "--env-file", str(secure / f"roles/{role}.env"),
            "--inventory", str(secure / "planned-inventory.json"),
            "--approval", str(secure / "planned-inventory-approval.json"),
            "--approval-policy", str(secure / "human-approval-policy.json"),
            "--snapshot-output", str(output),
        ]
        response = _run(command, timeout=180)
        try:
            result = _strict_json(response.encode("utf-8"), label="fresh preflight response")
        except FreshPreflightAgentError:
            raise FreshPreflightAgentError("fresh preflight returned invalid evidence") from None
        if result.get("status") != "verified" or result.get("role") != role:
            raise FreshPreflightAgentError("fresh preflight did not verify the expected role")
    evidence_digest, evidence_bytes = _digest(Path(str(manifest["evidence_output"])), limit=16 * 1024 * 1024)
    return {
        "status": "fresh-role-preflight-complete", "role": role,
        "campaign_id": manifest["campaign_id"], "deployment_id": manifest["deployment_id"],
        "release_sha": manifest["release_sha"], "role_package_sha256": hashlib.sha256(
            json.dumps(package_manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(), "evidence_sha256": evidence_digest, "evidence_bytes": evidence_bytes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = execute(load_manifest(args.manifest))
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
