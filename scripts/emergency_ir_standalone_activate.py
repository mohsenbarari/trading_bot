#!/usr/bin/env python3
"""Fail-closed local activation for the isolated Emergency IR standalone.

This tool deliberately has no Object Storage, SSH, registry, or DNS client.
It can consume only the four *already received* age-encrypted artifacts from
the fixed Emergency inbox.  Before it writes a release, loads an image, or
starts a container it re-verifies the pinned signed manifest, both ciphertext
and plaintext digests, the local age identity, and the package/settings tar
layouts.  Every mutable stage is create-only and separately confirmed.

The tool is also placed in the tiny, pinned receiver bootstrap bundle so it
can validate and extract the sealed package before trusting code from that
package.  It intentionally does not remove a file, volume, container, image,
Nginx site, firewall rule, or release directory.  A failed stage is therefore
forensic and fail-closed rather than "helpfully" destructive.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
import types
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


MODULE_ROOT = Path(__file__).resolve().parents[1]


def _install_pinned_scripts_namespace() -> None:
    """Resolve ``scripts`` only from the verified bundle/package root."""

    scripts_root = MODULE_ROOT / "scripts"
    try:
        state = scripts_root.lstat()
    except OSError as exc:
        raise RuntimeError("Emergency activation scripts directory cannot be inspected") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != os.geteuid()
        or stat.S_IMODE(state.st_mode) & 0o022
    ):
        raise RuntimeError("Emergency activation scripts directory is not safe")
    try:
        (scripts_root / "__init__.py").lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError("Emergency activation scripts initializer cannot be inspected") from exc
    else:
        raise RuntimeError("Emergency activation scripts initializer is unsupported")
    expected = str(scripts_root)
    present = sys.modules.get("scripts")
    if present is not None:
        paths = getattr(present, "__path__", None)
        if (
            getattr(present, "__file__", None) is not None
            or paths is None
            or [str(item) for item in paths] != [expected]
        ):
            raise RuntimeError("Emergency activation scripts namespace was preloaded from an ambient path")
        return
    namespace = types.ModuleType("scripts")
    namespace.__package__ = "scripts"
    namespace.__path__ = [expected]  # type: ignore[attr-defined]
    sys.modules["scripts"] = namespace


_install_pinned_scripts_namespace()

from scripts import emergency_ir_object_storage_manifest as manifest  # noqa: E402


SOURCE_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
PACKAGE_ROOT_NAME = "emergency-ir-standalone"
PACKAGE_RELEASE_SCHEMA = "gold-trade-emergency-ir-release-package-v1"
STATE_SCHEMA = "gold-trade-emergency-ir-activation-state-v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
TAG_SUFFIX_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$", re.ASCII)
SMS_PARAMETER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$", re.ASCII)
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)

MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_SETTINGS_BYTES = 4 * 1024 * 1024
MAX_SETTINGS_MEMBER_BYTES = 4 * 1024 * 1024
MAX_SECRET_BYTES = 4096
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024 * 1024
MAX_IMAGE_BYTES = 100 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_KERNEL_TOGGLE_BYTES = 16
DISK_HEADROOM_BYTES = 256 * 1024 * 1024

# The two protected staging listeners have distinct network scopes.  The API
# listener is intentionally loopback-only; the TLS staging listener is bound
# to WA-IR's public self address so it cannot be mistaken for an arbitrary
# local service during prearm.
EMERGENCY_WA_IR_PUBLIC_IPV4 = "95.38.164.29"
IPV4_NONLOCAL_BIND_PATH = Path("/proc/sys/net/ipv4/ip_nonlocal_bind")
STAGING_LOOPBACK_PORT = 8213
STAGING_PUBLIC_PORT = 8443
STAGING_LOOPBACK_ENDPOINT = ("127.0.0.1", STAGING_LOOPBACK_PORT)
STAGING_PUBLIC_ENDPOINT = (EMERGENCY_WA_IR_PUBLIC_IPV4, STAGING_PUBLIC_PORT)

AGE_BINARY = "/usr/bin/age"
AGE_KEYGEN_BINARY = "/usr/bin/age-keygen"
DOCKER_BINARY = "/usr/bin/docker"
NGINX_BINARY = "/usr/sbin/nginx"
SYSTEMCTL_BINARY = "/usr/bin/systemctl"
UFW_BINARY = "/usr/sbin/ufw"


class EmergencyActivationError(RuntimeError):
    """A local Emergency activation invariant was not satisfied."""


@dataclasses.dataclass(frozen=True)
class ActivationPaths:
    """All mutable paths are fixed under the Emergency-only namespace."""

    emergency_root: Path = Path("/srv/trading-bot-emergency")
    inbox_root: Path = Path("/srv/trading-bot-emergency/inbox")
    bootstrap_root: Path = Path("/run/trading-bot-emergency-bootstrap")
    activation_root: Path = Path("/srv/trading-bot-emergency/activation")
    releases_root: Path = Path("/srv/trading-bot-emergency/releases")
    current_link: Path = Path("/srv/trading-bot-emergency/current")
    age_identity: Path = Path("/etc/trading-bot-emergency/standalone/age/identity.txt")
    runtime_env: Path = Path("/etc/trading-bot-emergency/standalone/runtime.env")
    nginx_available: Path = Path("/etc/nginx/sites-available/trading-bot-emergency-ir")
    nginx_enabled: Path = Path("/etc/nginx/sites-enabled/trading-bot-emergency-ir")
    nginx_default: Path = Path("/etc/nginx/sites-enabled/default")
    nginx_backup_root: Path = Path("/etc/trading-bot-emergency/standalone/nginx-backups")
    nginx_sms_rate_limit: Path = Path("/etc/nginx/conf.d/trading-bot-emergency-ir-sms-rate-limit.conf")
    sms_preflight_receipt: Path = Path("/etc/trading-bot-emergency/standalone/sms-provider-preflight.json")


@dataclasses.dataclass(frozen=True)
class VerifiedCampaign:
    campaign_id: str
    manifest_sha256: str
    plan: Mapping[str, Any]
    artifacts: Mapping[str, Mapping[str, Any]]


@dataclasses.dataclass(frozen=True)
class PackageIdentity:
    source_release_sha: str
    emergency_patch_sha: str
    package_root: Path


@dataclasses.dataclass(frozen=True)
class ImageEntry:
    kind: str
    tag: str
    config_id: str


@dataclasses.dataclass(frozen=True)
class SettingsBundle:
    trading_settings: bytes
    webapp_initdata_token: str
    smsir_api_key: str | None = None
    smsir_otp_template_id: str | None = None
    smsir_otp_template_parameter: str | None = None


def _fail(message: str) -> None:
    raise EmergencyActivationError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON contains a duplicate field")
        result[key] = value
    return result


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError) as exc:
        raise EmergencyActivationError("activation JSON cannot be canonicalized") from exc


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("Emergency activation must run as root")


def _safe_relative(value: str, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        _fail(f"{label} path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        _fail(f"{label} path escapes its package")
    return path


def _secure_directory(path: Path, *, create: bool = False) -> None:
    """Require an absolute root-owned, non-link directory tree."""

    if not path.is_absolute():
        _fail("Emergency path must be absolute")
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        try:
            item = current.lstat()
        except FileNotFoundError:
            if not create:
                _fail("Emergency directory is missing")
            try:
                current.mkdir(mode=0o700)
            except OSError as exc:
                raise EmergencyActivationError("Emergency directory cannot be created") from exc
            item = current.lstat()
        except OSError as exc:
            raise EmergencyActivationError("Emergency directory cannot be inspected") from exc
        writable_by_group_or_other = bool(stat.S_IMODE(item.st_mode) & 0o022)
        # /tmp is sticky/root-owned on the controller and is useful for the
        # local test harness.  Any descendant still has to be root-owned and
        # non-writable before it is trusted.  Do not generalize this to other
        # group/world-writable ancestors.
        permitted_sticky_tmp = (
            current == Path("/tmp")
            and item.st_uid == 0
            and bool(stat.S_IMODE(item.st_mode) & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(item.st_mode)
            or not stat.S_ISDIR(item.st_mode)
            or item.st_uid != 0
            or (writable_by_group_or_other and not permitted_sticky_tmp)
        ):
            _fail("Emergency directory is not root-controlled")


def _root_regular(path: Path, *, label: str, maximum_bytes: int, private: bool = True) -> os.stat_result:
    try:
        item = path.lstat()
    except OSError as exc:
        raise EmergencyActivationError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(item.st_mode)
        or not stat.S_ISREG(item.st_mode)
        or item.st_uid != 0
        or item.st_nlink != 1
        or item.st_size < 1
        or item.st_size > maximum_bytes
        or stat.S_IMODE(item.st_mode) & 0o022
        or (private and stat.S_IMODE(item.st_mode) & 0o077)
    ):
        _fail(f"{label} must be one bounded root-only regular file")
    return item


def _read_root_regular(path: Path, *, label: str, maximum_bytes: int, private: bool = True) -> bytes:
    before = _root_regular(path, label=label, maximum_bytes=maximum_bytes, private=private)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail(f"{label} changed while being opened")
        result = bytearray()
        while len(result) <= maximum_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
        after = os.fstat(descriptor)
        if len(result) != opened.st_size or len(result) > maximum_bytes:
            _fail(f"{label} changed while being read")
        if any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail(f"{label} changed while being read")
        return bytes(result)
    except OSError as exc:
        raise EmergencyActivationError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _hash_root_regular(path: Path, *, label: str, maximum_bytes: int) -> tuple[str, int]:
    before = _root_regular(path, label=label, maximum_bytes=maximum_bytes)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail(f"{label} changed while being opened")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum_bytes:
                _fail(f"{label} exceeds its allowed size")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if size != opened.st_size or any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail(f"{label} changed while being read")
        return digest.hexdigest(), size
    except OSError as exc:
        raise EmergencyActivationError(f"{label} cannot be hashed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_create_only(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    _secure_directory(path.parent, create=True)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
    except FileExistsError as exc:
        raise EmergencyActivationError("refusing to overwrite an Emergency path") from exc
    except OSError as exc:
        raise EmergencyActivationError("Emergency file cannot be created") from exc
    try:
        offset = 0
        view = memoryview(payload)
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                _fail("Emergency file write made no progress")
            offset += written
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_create_only(source: Path, destination: Path, *, maximum_bytes: int) -> None:
    payload = _read_root_regular(source, label="Emergency package member", maximum_bytes=maximum_bytes)
    _write_create_only(destination, payload)


def _safe_tar_members(archive: tarfile.TarFile, *, expected: set[str], maximum_member_bytes: int) -> dict[str, tarfile.TarInfo]:
    members = archive.getmembers()
    names = [member.name for member in members]
    if len(names) != len(set(names)) or set(names) != expected:
        _fail("archive members do not exactly match the sealed allowlist")
    result: dict[str, tarfile.TarInfo] = {}
    for member in members:
        _safe_relative(member.name, label="archive member")
        if (
            not member.isreg()
            or member.issym()
            or member.islnk()
            or member.size < 1
            or member.size > maximum_member_bytes
        ):
            _fail("archive contains a link, device, directory, or oversized member")
        result[member.name] = member
    return result


def _read_tar_member(archive: tarfile.TarFile, member: tarfile.TarInfo, *, maximum_bytes: int) -> bytes:
    if member.size < 1 or member.size > maximum_bytes:
        _fail("archive member exceeds its exact size bound")
    source = archive.extractfile(member)
    if source is None:
        _fail("archive member cannot be read")
    payload = source.read(maximum_bytes + 1)
    if len(payload) != member.size or len(payload) > maximum_bytes:
        _fail("archive member changed while being read")
    return payload


def _parse_strict_json(payload: bytes, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if not 1 <= len(payload) <= maximum_bytes:
        _fail(f"{label} size is invalid")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyActivationError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _artifact_maximum(kind: str) -> int:
    return {
        "image_bundle": MAX_IMAGE_BYTES,
        "package_tar": MAX_PACKAGE_BYTES,
        "snapshot": MAX_SNAPSHOT_BYTES,
        "settings": MAX_SETTINGS_BYTES,
    }[kind]


def _plain_filename(kind: str) -> str:
    return {
        "image_bundle": "images.tar",
        "package_tar": "package.tar.gz",
        "snapshot": "snapshot.dump",
        "settings": "settings.tar",
    }[kind]


def _campaign_bootstrap_paths(paths: ActivationPaths, campaign_id: str) -> tuple[Path, Path]:
    root = paths.bootstrap_root / campaign_id / "receiver"
    return paths.bootstrap_root / campaign_id / "sealed-manifest.json", root / "signing-public.key"


def _verify_age_identity(identity: Path, *, expected_recipient_key_id: str, runner: Callable[..., Any] = subprocess.run) -> None:
    _root_regular(identity, label="Emergency age identity", maximum_bytes=16 * 1024)
    if (
        not expected_recipient_key_id.startswith("age-recipient-sha256:")
        or not SHA256_RE.fullmatch(expected_recipient_key_id.removeprefix("age-recipient-sha256:"))
    ):
        _fail("manifest age recipient key identity is invalid")
    try:
        result = runner(
            [AGE_KEYGEN_BINARY, "-y", str(identity)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError("cannot derive the Emergency age public key") from exc
    if getattr(result, "returncode", 1) != 0:
        _fail("cannot derive the Emergency age public key")
    recipient = str(getattr(result, "stdout", "")).strip()
    if not re.fullmatch(r"age1[ac-hj-np-z02-9]{10,200}", recipient):
        _fail("derived Emergency age public key is invalid")
    actual = "age-recipient-sha256:" + hashlib.sha256(recipient.encode("ascii")).hexdigest()
    if actual != expected_recipient_key_id:
        _fail("Emergency age identity does not match the signed manifest recipient")


def verify_campaign(
    *,
    campaign_id: str,
    paths: ActivationPaths = ActivationPaths(),
    runner: Callable[..., Any] = subprocess.run,
) -> VerifiedCampaign:
    """Re-verify the pinned signed manifest and every received ciphertext."""

    _require_root()
    if CAMPAIGN_RE.fullmatch(campaign_id) is None:
        _fail("Emergency campaign identity is invalid")
    manifest_path, public_key_path = _campaign_bootstrap_paths(paths, campaign_id)
    manifest_bytes = _read_root_regular(
        manifest_path, label="sealed Emergency manifest", maximum_bytes=manifest.MAX_MANIFEST_BYTES
    )
    _root_regular(
        public_key_path,
        label="pinned Emergency manifest public key",
        maximum_bytes=manifest.MAX_KEY_FILE_BYTES,
        private=False,
    )
    try:
        public_key = manifest.load_public_key(public_key_path)
        verified = manifest.verify_manifest_bytes(manifest_bytes, public_key=public_key)
    except manifest.EmergencyManifestError as exc:
        raise EmergencyActivationError("sealed Emergency manifest cannot be verified") from exc
    plan = verified.as_receive_plan()
    if plan["campaign_id"] != campaign_id:
        _fail("sealed manifest campaign differs from the requested activation")
    _verify_age_identity(
        paths.age_identity,
        expected_recipient_key_id=str(plan["destination_age_recipient_key_id"]),
        runner=runner,
    )
    artifacts: dict[str, Mapping[str, Any]] = {}
    for item in plan["artifacts"]:
        kind = str(item["kind"])
        target = Path(str(item["target_path"]))
        expected = Path(manifest.expected_target_path(campaign_id=campaign_id, kind=kind))
        if target != expected or not target.is_relative_to(paths.inbox_root):
            _fail("manifest artifact target is outside the Emergency inbox")
        digest, size = _hash_root_regular(
            target, label=f"received {kind} ciphertext", maximum_bytes=int(item["ciphertext_bytes"])
        )
        if digest != item["ciphertext_sha256"] or size != item["ciphertext_bytes"]:
            _fail(f"received {kind} ciphertext differs from the signed manifest")
        artifacts[kind] = dict(item)
    return VerifiedCampaign(
        campaign_id=campaign_id,
        manifest_sha256=str(plan["manifest_sha256"]),
        plan=plan,
        artifacts=artifacts,
    )


def confirmation_phrase(campaign: VerifiedCampaign, *, stage: str, profile: str) -> str:
    if stage not in {"prepare", "images", "database", "api", "prearm"}:
        _fail("activation stage is invalid")
    if profile not in {"telegram-only", "sms-otp"}:
        _fail("activation profile is invalid")
    return f"activate-emergency-ir:{campaign.campaign_id}:{campaign.manifest_sha256}:{profile}:{stage}"


def activation_plan(campaign: VerifiedCampaign, *, profile: str) -> dict[str, Any]:
    """Return a non-mutating activation plan and each exact confirmation phrase."""

    return {
        "status": "planned-local-only",
        "campaign_id": campaign.campaign_id,
        "manifest_sha256": campaign.manifest_sha256,
        "profile": profile,
        "transport": "already-received-private-object-storage-artifacts-only",
        "stages": [
            {
                "stage": "prepare",
                "does": "decrypt/hash-check/extract sealed package and strict settings without starting Docker",
                "confirm": confirmation_phrase(campaign, stage="prepare", profile=profile),
            },
            {
                "stage": "images",
                "does": "load only the prepared image bundle after its archive/tag/provenance checks",
                "confirm": confirmation_phrase(campaign, stage="images", profile=profile),
            },
            {
                "stage": "database",
                "does": "create fresh isolated volumes, restore, reset inherited sessions, and migrate",
                "confirm": confirmation_phrase(campaign, stage="database", profile=profile),
            },
            {
                "stage": "api",
                "does": "start only the isolated API and require local health",
                "confirm": confirmation_phrase(campaign, stage="api", profile=profile),
            },
            {
                "stage": "prearm",
                "does": "recoverably switch host Nginx and add only TCP 80/443 UFW allows after local checks",
                "confirm": confirmation_phrase(campaign, stage="prearm", profile=profile),
            },
        ],
        "never": [
            "Object Storage download", "SSH", "registry pull", "DNS mutation", "volume deletion", "container deletion",
            "image deletion", "Nginx/default-site deletion", "UFW rule deletion", "three-site path or Compose use",
        ],
    }


def _activation_campaign_root(paths: ActivationPaths, campaign_id: str) -> Path:
    return paths.activation_root / campaign_id


def _plain_path(paths: ActivationPaths, campaign_id: str, kind: str) -> Path:
    return _activation_campaign_root(paths, campaign_id) / "plaintext" / _plain_filename(kind)


def _receipt_path(paths: ActivationPaths, campaign_id: str, stage: str) -> Path:
    return _activation_campaign_root(paths, campaign_id) / f"{stage}.json"


def _write_receipt(paths: ActivationPaths, campaign: VerifiedCampaign, *, stage: str, payload: Mapping[str, Any]) -> None:
    result = {
        "schema": STATE_SCHEMA,
        "campaign_id": campaign.campaign_id,
        "manifest_sha256": campaign.manifest_sha256,
        "stage": stage,
        "payload": dict(payload),
    }
    _write_create_only(_receipt_path(paths, campaign.campaign_id, stage), _canonical_json(result))


def _read_receipt(paths: ActivationPaths, campaign: VerifiedCampaign, *, stage: str) -> dict[str, Any]:
    payload = _read_root_regular(
        _receipt_path(paths, campaign.campaign_id, stage),
        label=f"Emergency {stage} receipt",
        maximum_bytes=MAX_JSON_BYTES,
    )
    value = _parse_strict_json(payload, label=f"Emergency {stage} receipt", maximum_bytes=MAX_JSON_BYTES)
    if _canonical_json(value) != payload:
        _fail(f"Emergency {stage} receipt is not canonical")
    if (
        set(value) != {"schema", "campaign_id", "manifest_sha256", "stage", "payload"}
        or value.get("schema") != STATE_SCHEMA
        or value.get("campaign_id") != campaign.campaign_id
        or value.get("manifest_sha256") != campaign.manifest_sha256
        or value.get("stage") != stage
        or not isinstance(value.get("payload"), dict)
    ):
        _fail(f"Emergency {stage} receipt is not bound to this campaign")
    return dict(value["payload"])


def _decrypt_artifact(
    *,
    campaign: VerifiedCampaign,
    paths: ActivationPaths,
    kind: str,
    runner: Callable[..., Any] = subprocess.run,
) -> Path:
    artifact = campaign.artifacts[kind]
    source = Path(str(artifact["target_path"]))
    target = _plain_path(paths, campaign.campaign_id, kind)
    _secure_directory(target.parent, create=True)
    if target.exists() or target.is_symlink():
        _fail("refusing to overwrite an existing decrypted Emergency artifact")
    expected_bytes = int(artifact["plaintext_bytes"])
    maximum = _artifact_maximum(kind)
    if expected_bytes > maximum:
        _fail(f"signed {kind} plaintext exceeds the local Emergency bound")
    free = shutil.disk_usage(target.parent).free
    if free < expected_bytes + DISK_HEADROOM_BYTES:
        _fail("insufficient free space for the decrypted Emergency artifact")
    source_state = _root_regular(source, label=f"received {kind} ciphertext", maximum_bytes=int(artifact["ciphertext_bytes"]))
    descriptor: int | None = None
    input_descriptor: int | None = None
    input_file = None
    output_file = None
    try:
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        input_descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_source = os.fstat(input_descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(source_state, field) != getattr(opened_source, field) for field in fields):
            _fail(f"received {kind} ciphertext changed while being opened")
        input_file = os.fdopen(input_descriptor, "rb", buffering=0, closefd=False)
        output_file = os.fdopen(descriptor, "wb", buffering=0, closefd=False)
        result = runner(
            [AGE_BINARY, "-d", "-i", str(paths.age_identity)],
            check=False,
            stdin=input_file,
            stdout=output_file,
            stderr=subprocess.PIPE,
            timeout=7200,
        )
        output_file.flush()
        os.fsync(descriptor)
        if getattr(result, "returncode", 1) != 0:
            _fail(f"cannot decrypt signed {kind} artifact")
        after_opened = os.fstat(input_descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(opened_source, field) != getattr(after_opened, field) for field in fields):
            _fail(f"received {kind} ciphertext changed while being decrypted")
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError(f"cannot decrypt signed {kind} artifact") from exc
    finally:
        if output_file is not None:
            output_file.close()
        if input_file is not None:
            input_file.close()
        if input_descriptor is not None:
            try:
                os.close(input_descriptor)
            except OSError:
                pass
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    digest, size = _hash_root_regular(target, label=f"decrypted {kind}", maximum_bytes=maximum)
    if digest != artifact["plaintext_sha256"] or size != expected_bytes:
        _fail(f"decrypted {kind} plaintext differs from the signed manifest")
    # Guard against a caller replacing the source after the receiver's hash
    # check and before age reads it.  The source must still be unchanged.
    after_state = source.lstat()
    if (after_state.st_dev, after_state.st_ino, after_state.st_size, after_state.st_mtime_ns) != (
        source_state.st_dev,
        source_state.st_ino,
        source_state.st_size,
        source_state.st_mtime_ns,
    ):
        _fail(f"received {kind} ciphertext changed during local decrypt")
    return target


def _require_prepare_disk_budget(*, campaign: VerifiedCampaign, paths: ActivationPaths) -> None:
    """Require room for the complete plaintext set before the first decrypt.

    Each individual artifact is still checked again in ``_decrypt_artifact``.
    This aggregate preflight prevents a fresh, create-only activation from
    writing some plaintext artifacts and then failing midway through the
    four-artifact set solely because the remaining free space was consumed by
    earlier decrypts.
    """

    required = DISK_HEADROOM_BYTES
    for kind in manifest.ARTIFACT_ORDER:
        artifact = campaign.artifacts.get(kind)
        if not isinstance(artifact, Mapping):
            _fail("verified Emergency artifact set is incomplete")
        expected = artifact.get("plaintext_bytes")
        maximum = _artifact_maximum(kind)
        if isinstance(expected, bool) or not isinstance(expected, int) or not 1 <= expected <= maximum:
            _fail("verified Emergency artifact plaintext size is invalid")
        required += expected
    # ``verify_campaign`` already required the inbox below this same
    # Emergency root, so its parent exists on a fresh host without creating a
    # mutable activation directory merely to inspect capacity.
    try:
        free = shutil.disk_usage(paths.activation_root.parent).free
    except OSError as exc:
        raise EmergencyActivationError("cannot inspect aggregate Emergency activation disk space") from exc
    if free < required:
        _fail("insufficient aggregate free space for all decrypted Emergency artifacts")


def _release_file_specs(release: Mapping[str, Any]) -> list[dict[str, Any]]:
    if set(release) != {"schema", "source_release_sha", "emergency_patch_sha", "files"}:
        _fail("package RELEASE.json fields are unsupported")
    if release.get("schema") != PACKAGE_RELEASE_SCHEMA:
        _fail("package RELEASE.json schema is unsupported")
    source_sha = release.get("source_release_sha")
    patch_sha = release.get("emergency_patch_sha")
    if source_sha != SOURCE_RELEASE_SHA or not isinstance(patch_sha, str) or SHA_RE.fullmatch(patch_sha) is None:
        _fail("package release identities do not match the Emergency base contract")
    files = release.get("files")
    if not isinstance(files, list) or not files:
        _fail("package RELEASE.json file list is invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            _fail("package RELEASE.json file entry is invalid")
        path = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("bytes")
        if not isinstance(path, str) or not (
            path.startswith("deploy/emergency-ir/") or path.startswith("scripts/")
        ):
            _fail("package RELEASE.json file is outside the Emergency allowlist")
        _safe_relative(path, label="package RELEASE.json")
        if path in seen or not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            _fail("package RELEASE.json file identity is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 4 * 1024 * 1024:
            _fail("package RELEASE.json file size is invalid")
        seen.add(path)
        normalized.append({"path": path, "sha256": digest, "bytes": size})
    required = {
        "deploy/emergency-ir/docker-compose.standalone.yml",
        "deploy/emergency-ir/nginx.standalone.conf.template",
        "deploy/emergency-ir/reset-emergency-sessions.sql",
        "scripts/render_emergency_ir_standalone_env.py",
        "scripts/verify_emergency_ir_standalone.py",
        "scripts/verify_emergency_ir_image_provenance.py",
        "scripts/emergency_ir_standalone_activate.py",
    }
    if not required.issubset(seen):
        _fail("package RELEASE.json omits a mandatory Emergency activation control")
    return normalized


def extract_and_verify_package(
    *,
    package_tar: Path,
    releases_root: Path,
) -> PackageIdentity:
    """Validate every package member before create-only extraction."""

    _root_regular(package_tar, label="decrypted package tar", maximum_bytes=MAX_PACKAGE_BYTES)
    try:
        with tarfile.open(package_tar, mode="r:gz") as archive:
            members = archive.getmembers()
            release_name = f"{PACKAGE_ROOT_NAME}/RELEASE.json"
            # RELEASE is read first only after all names/types have been
            # bounded.  The exact full member set is validated below.
            if len(members) < 2 or release_name not in {item.name for item in members}:
                _fail("package tar omits RELEASE.json")
            release_info = next(item for item in members if item.name == release_name)
            if not release_info.isreg() or release_info.size > MAX_JSON_BYTES:
                _fail("package RELEASE.json member is unsafe")
            release_payload = _read_tar_member(archive, release_info, maximum_bytes=MAX_JSON_BYTES)
            release = _parse_strict_json(release_payload, label="package RELEASE.json", maximum_bytes=MAX_JSON_BYTES)
            if _canonical_json(release) != release_payload:
                _fail("package RELEASE.json is not canonical")
            file_specs = _release_file_specs(release)
            expected = {release_name, *(f"{PACKAGE_ROOT_NAME}/{item['path']}" for item in file_specs)}
            members_by_name = _safe_tar_members(archive, expected=expected, maximum_member_bytes=4 * 1024 * 1024)
            payloads: list[tuple[str, bytes]] = []
            for item in file_specs:
                name = f"{PACKAGE_ROOT_NAME}/{item['path']}"
                payload = _read_tar_member(archive, members_by_name[name], maximum_bytes=int(item["bytes"]))
                if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
                    _fail("package file differs from RELEASE.json identity")
                payloads.append((str(item["path"]), payload))
    except (tarfile.TarError, OSError) as exc:
        raise EmergencyActivationError("decrypted package tar is invalid") from exc
    patch_sha = str(release["emergency_patch_sha"])
    release_root = releases_root / patch_sha
    if release_root.exists() or release_root.is_symlink():
        _fail("refusing to overwrite an existing Emergency release directory")
    _secure_directory(releases_root, create=True)
    try:
        release_root.mkdir(mode=0o700)
    except OSError as exc:
        raise EmergencyActivationError("Emergency release directory cannot be created") from exc
    package_root = release_root / PACKAGE_ROOT_NAME
    try:
        package_root.mkdir(mode=0o700)
    except OSError as exc:
        raise EmergencyActivationError("Emergency package root cannot be created") from exc
    _write_create_only(package_root / "RELEASE.json", release_payload)
    for relative, payload in payloads:
        _write_create_only(package_root / Path(relative), payload)
    return PackageIdentity(
        source_release_sha=str(release["source_release_sha"]),
        emergency_patch_sha=patch_sha,
        package_root=package_root,
    )


def _one_line_secret(payload: bytes, *, label: str) -> str:
    if not 1 <= len(payload) <= MAX_SECRET_BYTES:
        _fail(f"{label} is invalid")
    try:
        value = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise EmergencyActivationError(f"{label} is invalid") from exc
    if not value or any(ord(character) < 33 or ord(character) > 126 for character in value):
        _fail(f"{label} is invalid")
    return value


def read_settings_bundle(*, settings_tar: Path, profile: str) -> SettingsBundle:
    """Read the exact small settings layout; secrets remain in memory only."""

    if profile not in {"telegram-only", "sms-otp"}:
        _fail("activation profile is invalid")
    _root_regular(settings_tar, label="decrypted settings tar", maximum_bytes=MAX_SETTINGS_BYTES)
    expected = {"trading_settings.json", "webapp_initdata_token"}
    if profile == "sms-otp":
        expected.update({"smsir_api_key", "smsir_otp_template_id", "smsir_otp_template_parameter"})
    try:
        with tarfile.open(settings_tar, mode="r:") as archive:
            members = _safe_tar_members(archive, expected=expected, maximum_member_bytes=MAX_SETTINGS_MEMBER_BYTES)
            settings = _read_tar_member(archive, members["trading_settings.json"], maximum_bytes=MAX_SETTINGS_MEMBER_BYTES)
            parsed_settings = _parse_strict_json(settings, label="trading settings", maximum_bytes=MAX_SETTINGS_MEMBER_BYTES)
            if not parsed_settings:
                _fail("trading settings must not be empty")
            token = _one_line_secret(
                _read_tar_member(archive, members["webapp_initdata_token"], maximum_bytes=MAX_SECRET_BYTES),
                label="WebApp initData token",
            )
            if profile == "telegram-only":
                return SettingsBundle(trading_settings=settings, webapp_initdata_token=token)
            api_key = _one_line_secret(
                _read_tar_member(archive, members["smsir_api_key"], maximum_bytes=MAX_SECRET_BYTES),
                label="SMS.ir API key",
            )
            template_id = _one_line_secret(
                _read_tar_member(archive, members["smsir_otp_template_id"], maximum_bytes=MAX_SECRET_BYTES),
                label="SMS.ir template ID",
            )
            if not template_id.isdecimal() or not 0 < int(template_id) <= 2_147_483_647:
                _fail("SMS.ir template ID is invalid")
            parameter = _one_line_secret(
                _read_tar_member(archive, members["smsir_otp_template_parameter"], maximum_bytes=MAX_SECRET_BYTES),
                label="SMS.ir template parameter",
            )
            if SMS_PARAMETER_RE.fullmatch(parameter) is None:
                _fail("SMS.ir template parameter is invalid")
            return SettingsBundle(
                trading_settings=settings,
                webapp_initdata_token=token,
                smsir_api_key=api_key,
                smsir_otp_template_id=template_id,
                smsir_otp_template_parameter=parameter,
            )
    except (tarfile.TarError, OSError) as exc:
        raise EmergencyActivationError("decrypted settings tar is invalid") from exc


def _validate_app_image_config(config: Mapping[str, Any], *, source_sha: str, patch_sha: str, tag: str) -> None:
    if tag != f"trading_bot_emergency_ir_app:{patch_sha}":
        _fail("image archive application tag does not match the Emergency patch")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        _fail("image archive application labels are missing")
    expected = {
        "org.opencontainers.image.revision": patch_sha,
        "org.goldtrade.emergency.base-revision": source_sha,
        "org.goldtrade.emergency.scope": "ir-standalone",
        "org.goldtrade.emergency.auth": "webapp-initdata-and-local-sms-otp",
    }
    if any(labels.get(key) != value for key, value in expected.items()):
        _fail("image archive application provenance labels are invalid")
    env = config.get("Env")
    if not isinstance(env, list) or any(not isinstance(item, str) for item in env):
        _fail("image archive application environment is invalid")
    forbidden = {
        "BOT_TOKEN", "SYNC_API_KEY", "PEER_SERVER_URL", "IRAN_SERVER_URL", "GERMANY_SERVER_URL",
        "FOREIGN_SERVER_URL", "SMSIR_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "WEB_PUSH_VAPID_PRIVATE_KEY", "WRITER_WITNESS_CLIENT_SECRET",
    }
    if any(item.partition("=")[0] in forbidden for item in env):
        _fail("image archive application embeds a forbidden credential or proxy")


def inspect_image_bundle(
    *,
    image_tar: Path,
    source_sha: str,
    patch_sha: str,
    profile: str,
) -> list[ImageEntry]:
    """Validate tags/configs before ``docker load`` can mutate local tags."""

    _root_regular(image_tar, label="decrypted Docker image archive", maximum_bytes=MAX_IMAGE_BYTES)
    try:
        with tarfile.open(image_tar, mode="r:") as archive:
            try:
                manifest_member = archive.getmember("manifest.json")
            except KeyError as exc:
                raise EmergencyActivationError("Docker image archive omits manifest.json") from exc
            if not manifest_member.isreg() or manifest_member.size > MAX_JSON_BYTES:
                _fail("Docker image archive manifest is unsafe")
            payload = _read_tar_member(archive, manifest_member, maximum_bytes=MAX_JSON_BYTES)
            try:
                values = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                raise EmergencyActivationError("Docker image archive manifest is invalid") from exc
            if not isinstance(values, list):
                _fail("Docker image archive manifest must be a list")
            expected_count = 4 if profile == "sms-otp" else 3
            if len(values) != expected_count:
                _fail("Docker image archive has an unexpected number of images")
            entries: list[ImageEntry] = []
            seen_kinds: set[str] = set()
            seen_tags: set[str] = set()
            for value in values:
                if not isinstance(value, dict) or set(value) != {"Config", "RepoTags", "Layers"}:
                    _fail("Docker image archive manifest entry is unsupported")
                config_name = value.get("Config")
                tags = value.get("RepoTags")
                layers = value.get("Layers")
                if not isinstance(config_name, str) or not re.fullmatch(r"[a-f0-9]{64}\.json", config_name):
                    _fail("Docker image archive config identity is invalid")
                if not isinstance(tags, list) or len(tags) != 1 or not isinstance(tags[0], str):
                    _fail("Docker image archive must provide exactly one tag per image")
                if not isinstance(layers, list) or not layers:
                    _fail("Docker image archive layers are invalid")
                for layer in layers:
                    if not isinstance(layer, str):
                        _fail("Docker image archive layer is invalid")
                    _safe_relative(layer, label="Docker image layer")
                    try:
                        layer_member = archive.getmember(layer)
                    except KeyError as exc:
                        raise EmergencyActivationError("Docker image archive omits a declared layer") from exc
                    if not layer_member.isreg() or layer_member.issym() or layer_member.islnk() or layer_member.size < 1:
                        _fail("Docker image archive layer is unsafe")
                tag = tags[0]
                if tag in seen_tags or "staging" in tag.lower() or "three_site" in tag.lower():
                    _fail("Docker image archive has a duplicate or forbidden tag")
                if tag == f"trading_bot_emergency_ir_app:{patch_sha}":
                    kind = "app"
                elif tag.startswith("trading_bot_emergency_ir_postgres:") and TAG_SUFFIX_RE.fullmatch(tag.partition(":")[2]):
                    kind = "postgres"
                elif tag.startswith("trading_bot_emergency_ir_redis:") and TAG_SUFFIX_RE.fullmatch(tag.partition(":")[2]):
                    kind = "redis"
                elif tag == f"trading_bot_emergency_ir_sms_egress:{patch_sha}":
                    kind = "sms-egress"
                else:
                    _fail("Docker image archive tag is outside the Emergency allowlist")
                if kind in seen_kinds:
                    _fail("Docker image archive has duplicate Emergency image kinds")
                config_member = archive.getmember(config_name)
                if not config_member.isreg() or config_member.issym() or config_member.islnk():
                    _fail("Docker image config is unsafe")
                config_raw = _read_tar_member(archive, config_member, maximum_bytes=MAX_JSON_BYTES)
                config_json = _parse_strict_json(config_raw, label="Docker image config", maximum_bytes=MAX_JSON_BYTES)
                config = config_json.get("config")
                if not isinstance(config, dict):
                    _fail("Docker image config omits its runtime configuration")
                if kind == "app":
                    _validate_app_image_config(config, source_sha=source_sha, patch_sha=patch_sha, tag=tag)
                if kind == "sms-egress":
                    labels = config.get("Labels")
                    if not isinstance(labels, dict) or labels.get("org.opencontainers.image.revision") != patch_sha:
                        _fail("SMS relay image provenance is invalid")
                seen_tags.add(tag)
                seen_kinds.add(kind)
                entries.append(ImageEntry(kind=kind, tag=tag, config_id="sha256:" + config_name[:-5]))
    except (tarfile.TarError, OSError) as exc:
        raise EmergencyActivationError("decrypted Docker image archive is invalid") from exc
    expected_kinds = {"app", "postgres", "redis"} | ({"sms-egress"} if profile == "sms-otp" else set())
    if {item.kind for item in entries} != expected_kinds:
        _fail("Docker image archive does not contain the complete Emergency image set")
    return sorted(entries, key=lambda item: item.kind)


def _prepare_payload(
    *,
    campaign: VerifiedCampaign,
    identity: PackageIdentity,
    entries: Sequence[ImageEntry],
    profile: str,
    paths: ActivationPaths,
) -> dict[str, Any]:
    return {
        "profile": profile,
        "source_release_sha": identity.source_release_sha,
        "emergency_patch_sha": identity.emergency_patch_sha,
        "package_root": str(identity.package_root),
        "plain_artifacts": {
            kind: {
                "path": str(_plain_path(paths, campaign.campaign_id, kind)),
                "sha256": campaign.artifacts[kind]["plaintext_sha256"],
                "bytes": campaign.artifacts[kind]["plaintext_bytes"],
            }
            for kind in manifest.ARTIFACT_ORDER
        },
        "images": [dataclasses.asdict(item) for item in entries],
    }


def prepare(
    *,
    campaign: VerifiedCampaign,
    paths: ActivationPaths,
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Decrypt, checksum, and extract; do not invoke Docker or host ingress."""

    if _receipt_path(paths, campaign.campaign_id, "prepared").exists():
        _fail("Emergency prepare receipt already exists; refusing to overwrite a prior activation")
    _require_prepare_disk_budget(campaign=campaign, paths=paths)
    decrypted = {
        kind: _decrypt_artifact(campaign=campaign, paths=paths, kind=kind, runner=runner)
        for kind in manifest.ARTIFACT_ORDER
    }
    identity = extract_and_verify_package(package_tar=decrypted["package_tar"], releases_root=paths.releases_root)
    settings = read_settings_bundle(settings_tar=decrypted["settings"], profile=profile)
    _write_create_only(identity.package_root / "trading_settings.json", settings.trading_settings)
    entries = inspect_image_bundle(
        image_tar=decrypted["image_bundle"],
        source_sha=identity.source_release_sha,
        patch_sha=identity.emergency_patch_sha,
        profile=profile,
    )
    snapshot_header = _read_root_regular(
        decrypted["snapshot"], label="decrypted PostgreSQL snapshot", maximum_bytes=MAX_SNAPSHOT_BYTES
    )[:5]
    if snapshot_header != b"PGDMP":
        _fail("decrypted snapshot is not a PostgreSQL custom dump")
    payload = _prepare_payload(
        campaign=campaign, identity=identity, entries=entries, profile=profile, paths=paths
    )
    _write_receipt(paths, campaign, stage="prepared", payload=payload)
    return payload


def _require_prepare(paths: ActivationPaths, campaign: VerifiedCampaign, *, profile: str) -> dict[str, Any]:
    prepared = _read_receipt(paths, campaign, stage="prepared")
    if prepared.get("profile") != profile:
        _fail("prepared activation profile differs from the requested stage")
    patch = prepared.get("emergency_patch_sha")
    package_root = prepared.get("package_root")
    if not isinstance(patch, str) or SHA_RE.fullmatch(patch) is None or not isinstance(package_root, str):
        _fail("prepared activation receipt is malformed")
    expected_root = paths.releases_root / patch / PACKAGE_ROOT_NAME
    if Path(package_root) != expected_root:
        _fail("prepared package path is outside the Emergency releases root")
    _secure_directory(expected_root, create=False)
    return prepared


def _docker_result(command: Sequence[str], *, runner: Callable[..., Any] = subprocess.run, input_data: bytes | None = None, timeout: int = 300) -> Any:
    try:
        result = runner(
            list(command), check=False, capture_output=True, input=input_data, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError("local Docker command failed to execute") from exc
    if getattr(result, "returncode", 1) != 0:
        _fail("local Docker command failed")
    return result


def load_images(
    *,
    campaign: VerifiedCampaign,
    paths: ActivationPaths,
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Load only pristine allowlisted tags, then recheck the loaded IDs."""

    if _receipt_path(paths, campaign.campaign_id, "images-loaded").exists():
        _fail("Emergency image receipt already exists; refusing to overwrite a prior image load")
    prepared = _require_prepare(paths, campaign, profile=profile)
    raw_entries = prepared.get("images")
    if not isinstance(raw_entries, list) or not raw_entries:
        _fail("prepared image plan is invalid")
    entries = [ImageEntry(**item) for item in raw_entries if isinstance(item, dict)]
    if len(entries) != len(raw_entries):
        _fail("prepared image plan is invalid")
    for item in entries:
        result = runner(
            [DOCKER_BINARY, "image", "inspect", item.tag], check=False, capture_output=True, text=True, timeout=30
        )
        if getattr(result, "returncode", 1) == 0:
            _fail("refusing to load an image tag that already exists locally")
        if getattr(result, "returncode", 1) not in {1}:
            _fail("cannot determine whether an Emergency image tag already exists")
    image_tar = _plain_path(paths, campaign.campaign_id, "image_bundle")
    _docker_result([DOCKER_BINARY, "load", "--input", str(image_tar)], runner=runner, timeout=7200)
    observed: list[dict[str, str]] = []
    for item in entries:
        result = _docker_result(
            [DOCKER_BINARY, "image", "inspect", item.tag, "--format", "{{json .}}"], runner=runner, timeout=30
        )
        try:
            payload = json.loads(bytes(getattr(result, "stdout", b"")).decode("utf-8") if isinstance(getattr(result, "stdout", b""), bytes) else str(getattr(result, "stdout", "")).strip())
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EmergencyActivationError("loaded Docker image inspection is invalid") from exc
        if not isinstance(payload, dict) or payload.get("Id") != item.config_id:
            _fail("loaded Docker image ID differs from the sealed image archive")
        tags = payload.get("RepoTags")
        if not isinstance(tags, list) or item.tag not in tags or any(
            not isinstance(tag, str) or "staging" in tag.lower() or "three_site" in tag.lower() for tag in tags
        ):
            _fail("loaded Docker image tags differ from the Emergency image plan")
        observed.append(dataclasses.asdict(item))
    payload = {"profile": profile, "images": observed}
    _write_receipt(paths, campaign, stage="images-loaded", payload=payload)
    return payload


def _compose_command(
    *,
    package_root: Path,
    runtime_env: Path,
    profile: str,
    trailing: Iterable[str],
) -> list[str]:
    command = [
        DOCKER_BINARY,
        "compose",
        "--project-directory",
        str(package_root),
        "--env-file",
        str(runtime_env),
        "-f",
        str(package_root / "deploy/emergency-ir/docker-compose.standalone.yml"),
    ]
    if profile == "sms-otp":
        command.extend(["-f", str(package_root / "deploy/emergency-ir/docker-compose.sms-otp.yml"), "--profile", "sms-otp"])
    command.extend(trailing)
    return command


def _ensure_current_link(*, paths: ActivationPaths, package_root: Path) -> None:
    _secure_directory(paths.current_link.parent, create=True)
    try:
        paths.current_link.lstat()
    except FileNotFoundError:
        pass
    else:
        _fail("refusing to replace the Emergency current release link")
    try:
        os.symlink(str(package_root), paths.current_link)
    except OSError as exc:
        raise EmergencyActivationError("Emergency current release link cannot be created") from exc


def _run_renderer(
    *,
    package_root: Path,
    paths: ActivationPaths,
    prepared: Mapping[str, Any],
    profile: str,
    settings: SettingsBundle,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    try:
        paths.runtime_env.lstat()
    except FileNotFoundError:
        pass
    else:
        _fail("refusing to replace an existing Emergency runtime environment")
    images = {str(item["kind"]): str(item["tag"]) for item in prepared["images"]}
    command = [
        sys.executable,
        "-I",
        "-B",
        str(package_root / "scripts/render_emergency_ir_standalone_env.py"),
        "--output",
        str(paths.runtime_env),
        "--source-release-sha",
        str(prepared["source_release_sha"]),
        "--emergency-patch-sha",
        str(prepared["emergency_patch_sha"]),
        "--app-image",
        images["app"],
        "--postgres-image",
        images["postgres"],
        "--redis-image",
        images["redis"],
    ]
    if profile == "sms-otp":
        if not all((settings.smsir_api_key, settings.smsir_otp_template_id, settings.smsir_otp_template_parameter)):
            _fail("strict SMS settings artifact is incomplete")
        command.extend(
            [
                "--enable-sms-otp",
                "--sms-otp-secrets-stdin",
                "--smsir-otp-template-id",
                str(settings.smsir_otp_template_id),
                "--smsir-otp-template-parameter",
                str(settings.smsir_otp_template_parameter),
                "--sms-egress-image",
                images["sms-egress"],
            ]
        )
        input_data = json.dumps(
            {"webapp_initdata_token": settings.webapp_initdata_token, "smsir_api_key": settings.smsir_api_key},
            separators=(",", ":"),
        ).encode("ascii")
    else:
        command.append("--webapp-initdata-token-stdin")
        input_data = settings.webapp_initdata_token.encode("ascii")
    _docker_result(command, runner=runner, input_data=input_data, timeout=60)


def _assert_fresh_docker_resources(*, runner: Callable[..., Any] = subprocess.run, profile: str) -> None:
    names = [
        "trading-bot-emergency-ir-postgres", "trading-bot-emergency-ir-redis",
        "trading-bot-emergency-ir-uploads", "trading-bot-emergency-ir-audit",
        "trading-bot-emergency-ir-net",
    ]
    if profile == "sms-otp":
        names.extend(["trading-bot-emergency-ir-sms-relay", "trading-bot-emergency-ir-sms-egress"])
    for name in names[:4]:
        result = runner([DOCKER_BINARY, "volume", "inspect", name], check=False, capture_output=True, timeout=30)
        if getattr(result, "returncode", 1) == 0:
            _fail("fresh Emergency database stage refuses an existing Docker volume")
        if getattr(result, "returncode", 1) not in {1}:
            _fail("cannot inspect an Emergency Docker volume")
    for name in names[4:]:
        result = runner([DOCKER_BINARY, "network", "inspect", name], check=False, capture_output=True, timeout=30)
        if getattr(result, "returncode", 1) == 0:
            _fail("fresh Emergency database stage refuses an existing Docker network")
        if getattr(result, "returncode", 1) not in {1}:
            _fail("cannot inspect an Emergency Docker network")
    result = runner(
        [DOCKER_BINARY, "ps", "-a", "--filter", "label=com.docker.compose.project=trading-bot-emergency-ir", "--format", "{{.ID}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if getattr(result, "returncode", 1) != 0:
        _fail("cannot inspect Emergency Docker containers")
    if str(getattr(result, "stdout", "")).strip():
        _fail("fresh Emergency database stage refuses existing Docker containers")


def _wait_service_health(command: Sequence[str], service: str, *, runner: Callable[..., Any] = subprocess.run) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        result = runner([*command, "ps", "-q", service], check=False, capture_output=True, text=True, timeout=30)
        container_id = str(getattr(result, "stdout", "")).strip()
        if getattr(result, "returncode", 1) == 0 and container_id:
            health = runner(
                [DOCKER_BINARY, "inspect", "--format", "{{.State.Health.Status}}", container_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if getattr(health, "returncode", 1) == 0 and str(getattr(health, "stdout", "")).strip() == "healthy":
                return
        time.sleep(2)
    _fail(f"Emergency {service} service did not become healthy")


def _restore_snapshot(
    *,
    compose: Sequence[str],
    snapshot: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    _root_regular(snapshot, label="decrypted PostgreSQL snapshot", maximum_bytes=MAX_SNAPSHOT_BYTES)
    try:
        with open(snapshot, "rb", buffering=0) as source:
            result = runner(
                [*compose, "exec", "-T", "db", "pg_restore", "-U", "emergency_webapp", "-d", "trading_bot_emergency", "--no-owner", "--no-privileges"],
                check=False,
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=7200,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError("Emergency PostgreSQL snapshot restore could not run") from exc
    if getattr(result, "returncode", 1) != 0:
        _fail("Emergency PostgreSQL snapshot restore failed")


def database(
    *,
    campaign: VerifiedCampaign,
    paths: ActivationPaths,
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Restore only into fresh isolated volumes, reset sessions, then migrate."""

    if _receipt_path(paths, campaign.campaign_id, "database-ready").exists():
        _fail("Emergency database receipt already exists; refusing to overwrite a prior database stage")
    prepared = _require_prepare(paths, campaign, profile=profile)
    _read_receipt(paths, campaign, stage="images-loaded")
    package_root = Path(str(prepared["package_root"]))
    settings = read_settings_bundle(settings_tar=_plain_path(paths, campaign.campaign_id, "settings"), profile=profile)
    _assert_fresh_docker_resources(runner=runner, profile=profile)
    _ensure_current_link(paths=paths, package_root=package_root)
    _run_renderer(
        package_root=package_root, paths=paths, prepared=prepared, profile=profile, settings=settings, runner=runner
    )
    compose = _compose_command(package_root=package_root, runtime_env=paths.runtime_env, profile=profile, trailing=[])
    _docker_result([*compose, "config", "--quiet"], runner=runner, timeout=60)
    _docker_result([*compose, "up", "-d", "--pull", "never", "db", "redis"], runner=runner, timeout=300)
    _wait_service_health(compose, "db", runner=runner)
    _wait_service_health(compose, "redis", runner=runner)
    _restore_snapshot(compose=compose, snapshot=_plain_path(paths, campaign.campaign_id, "snapshot"), runner=runner)
    reset_sql = _read_root_regular(
        package_root / "deploy/emergency-ir/reset-emergency-sessions.sql",
        label="Emergency session reset SQL",
        maximum_bytes=MAX_JSON_BYTES,
    )
    _docker_result(
        [*compose, "exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "emergency_webapp", "-d", "trading_bot_emergency"],
        runner=runner,
        input_data=reset_sql,
        timeout=300,
    )
    _docker_result([*compose, "up", "--pull", "never", "migration"], runner=runner, timeout=1800)
    payload = {"profile": profile, "package_root": str(package_root), "session_reset_before_migration": True}
    _write_receipt(paths, campaign, stage="database-ready", payload=payload)
    return payload


def _local_api_health() -> None:
    opener = build_opener(ProxyHandler({}))
    try:
        response = opener.open(Request("http://127.0.0.1:18000/api/config", method="GET"), timeout=10)
        with response:
            if getattr(response, "status", 200) != 200:
                _fail("Emergency API local health endpoint returned an unexpected status")
            response.read(4096)
    except (URLError, OSError) as exc:
        raise EmergencyActivationError("Emergency API local health endpoint is unavailable") from exc


def api(
    *,
    campaign: VerifiedCampaign,
    paths: ActivationPaths,
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if _receipt_path(paths, campaign.campaign_id, "api-ready").exists():
        _fail("Emergency API receipt already exists; refusing to overwrite a prior API stage")
    prepared = _require_prepare(paths, campaign, profile=profile)
    _read_receipt(paths, campaign, stage="database-ready")
    package_root = Path(str(prepared["package_root"]))
    compose = _compose_command(package_root=package_root, runtime_env=paths.runtime_env, profile=profile, trailing=[])
    _docker_result([*compose, "up", "-d", "--pull", "never", "api"], runner=runner, timeout=600)
    _wait_service_health(compose, "api", runner=runner)
    _local_api_health()
    payload = {"profile": profile, "local_api": "http://127.0.0.1:18000/api/config"}
    _write_receipt(paths, campaign, stage="api-ready", payload=payload)
    return payload


def _require_sms_preflight(paths: ActivationPaths, campaign: VerifiedCampaign) -> None:
    raw = _read_root_regular(
        paths.sms_preflight_receipt, label="SMS provider preflight receipt", maximum_bytes=MAX_JSON_BYTES
    )
    value = _parse_strict_json(raw, label="SMS provider preflight receipt", maximum_bytes=MAX_JSON_BYTES)
    if _canonical_json(value) != raw:
        _fail("SMS provider preflight receipt is not canonical")
    if set(value) != {"schema", "campaign_id", "status", "performed_at"} or value.get("schema") != "gold-trade-emergency-ir-sms-provider-preflight-v1" or value.get("campaign_id") != campaign.campaign_id or value.get("status") != "passed" or not isinstance(value.get("performed_at"), str):
        _fail("SMS provider preflight receipt is not an explicit pass for this campaign")


def _require_ipv4_nonlocal_bind_disabled() -> None:
    """Fail closed unless the kernel forbids binding a non-local IPv4 source."""

    path = IPV4_NONLOCAL_BIND_PATH
    _secure_directory(path.parent, create=False)
    descriptor: int | None = None
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            _fail("IPv4 nonlocal-bind kernel control is not root-controlled")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail("IPv4 nonlocal-bind kernel control changed while being opened")
        payload = bytearray()
        while len(payload) <= MAX_KERNEL_TOGGLE_BYTES:
            chunk = os.read(descriptor, MAX_KERNEL_TOGGLE_BYTES + 1 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) > MAX_KERNEL_TOGGLE_BYTES:
            _fail("IPv4 nonlocal-bind kernel control is oversized")
        if any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail("IPv4 nonlocal-bind kernel control changed while being read")
    except OSError as exc:
        raise EmergencyActivationError("IPv4 nonlocal-bind kernel control cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if bytes(payload) != b"0\n":
        _fail("IPv4 nonlocal bind must be disabled before public staging listener probe")


def _check_staging_listener(port: int) -> None:
    if port == STAGING_LOOPBACK_PORT:
        endpoint = STAGING_LOOPBACK_ENDPOINT
    elif port == STAGING_PUBLIC_PORT:
        _require_ipv4_nonlocal_bind_disabled()
        endpoint = STAGING_PUBLIC_ENDPOINT
    else:
        _fail("protected three-site staging listener port is not permitted")
    try:
        # Avoid the resolver convenience API: this must be a direct IPv4
        # self-probe and must not route toward another host.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_DONTROUTE, 1)
            connection.settimeout(3)
            connection.bind((endpoint[0], 0))
            connection.connect(endpoint)
            peer = connection.getpeername()
            local = connection.getsockname()
    except OSError as exc:
        raise EmergencyActivationError("protected three-site staging listener is not healthy") from exc
    if peer != endpoint:
        _fail("protected three-site staging listener peer endpoint did not remain pinned")
    if (
        not isinstance(local, tuple)
        or len(local) != 2
        or local[0] != endpoint[0]
        or not isinstance(local[1], int)
        or not 1 <= local[1] <= 65535
    ):
        _fail("protected three-site staging listener local endpoint did not remain pinned")


def _nginx_static_contract(package_root: Path, *, profile: str) -> Path:
    if profile == "sms-otp":
        candidate = package_root / "deploy/emergency-ir/nginx.sms-otp.conf.template"
        rate = package_root / "deploy/emergency-ir/nginx.sms-otp.rate-limit.conf"
        _root_regular(rate, label="Emergency SMS Nginx rate limit", maximum_bytes=MAX_JSON_BYTES)
    else:
        candidate = package_root / "deploy/emergency-ir/nginx.standalone.conf.template"
    payload = _read_root_regular(candidate, label="Emergency Nginx configuration", maximum_bytes=MAX_JSON_BYTES)
    required = (b"server_name coin.gold-trade.ir", b"proxy_pass http://127.0.0.1:18000", b"ssl_certificate ")
    if any(value not in payload for value in required):
        _fail("Emergency Nginx configuration does not satisfy the static ingress contract")
    return candidate


def _restore_default_nginx_after_failed_prearm(
    *,
    paths: ActivationPaths,
    backup: Path,
    failed: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Preserve the failed Emergency link and restore the original default.

    This is deliberately rename-only: a failed prearm must leave both the
    original default site and the failed Emergency candidate available for
    forensic review, without deleting either configuration.  The same helper
    covers a failed syntax test, a failed reload, and a failed bounded UFW
    change so none of those paths can leave a later daemon reload pointing at
    the Emergency site.
    """

    try:
        os.rename(paths.nginx_enabled, failed)
        os.rename(backup, paths.nginx_default)
    except OSError as exc:
        raise EmergencyActivationError("Nginx prearm failed and the default site could not be restored") from exc
    restored_test = runner([NGINX_BINARY, "-t"], check=False, capture_output=True, timeout=60)
    if getattr(restored_test, "returncode", 1) != 0:
        _fail("Nginx prearm failed; the default site was restored but its configuration test failed")
    restored_reload = runner([SYSTEMCTL_BINARY, "reload", "nginx"], check=False, capture_output=True, timeout=60)
    if getattr(restored_reload, "returncode", 1) != 0:
        _fail("Nginx prearm failed; the default site was restored but could not be reloaded")


def _prearm_nginx(
    *,
    paths: ActivationPaths,
    campaign: VerifiedCampaign,
    package_root: Path,
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    source = _nginx_static_contract(package_root, profile=profile)
    _copy_create_only(source, paths.nginx_available, maximum_bytes=MAX_JSON_BYTES)
    if profile == "sms-otp":
        _copy_create_only(
            package_root / "deploy/emergency-ir/nginx.sms-otp.rate-limit.conf",
            paths.nginx_sms_rate_limit,
            maximum_bytes=MAX_JSON_BYTES,
        )
    _secure_directory(paths.nginx_backup_root, create=True)
    backup = paths.nginx_backup_root / f"default.before-{campaign.campaign_id}"
    failed = paths.nginx_backup_root / f"emergency-site.failed-{campaign.campaign_id}"
    for candidate in (paths.nginx_enabled, backup, failed):
        if candidate.exists() or candidate.is_symlink():
            _fail("refusing to overwrite an existing Nginx Emergency recovery path")
    try:
        default = paths.nginx_default.lstat()
    except OSError as exc:
        raise EmergencyActivationError("existing Nginx default site cannot be inspected") from exc
    if not stat.S_ISLNK(default.st_mode) or default.st_uid != 0:
        _fail("existing Nginx default site must be a root-owned symlink for recoverable prearm")
    try:
        os.rename(paths.nginx_default, backup)
        os.symlink(str(paths.nginx_available), paths.nginx_enabled)
    except OSError as exc:
        raise EmergencyActivationError("recoverable Nginx prearm cannot move the default site") from exc
    tested = runner([NGINX_BINARY, "-t"], check=False, capture_output=True, timeout=60)
    if getattr(tested, "returncode", 1) != 0:
        _restore_default_nginx_after_failed_prearm(
            paths=paths, backup=backup, failed=failed, runner=runner
        )
        _fail("Nginx test failed; the previous default site was restored")
    reloaded = runner([SYSTEMCTL_BINARY, "reload", "nginx"], check=False, capture_output=True, timeout=60)
    if getattr(reloaded, "returncode", 1) != 0:
        _restore_default_nginx_after_failed_prearm(
            paths=paths, backup=backup, failed=failed, runner=runner
        )
        _fail("Nginx configuration could not be reloaded; the previous default site was restored")
    # A single UFW transaction avoids an 80-only partial success.  If it
    # fails, restore Nginx rather than leaving a future reload/cutover armed.
    allowed = runner(
        [
            UFW_BINARY,
            "allow",
            "proto",
            "tcp",
            "from",
            "any",
            "to",
            "any",
            "port",
            "80,443",
            "comment",
            "trading-bot-emergency-ir",
        ],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if getattr(allowed, "returncode", 1) != 0:
        _restore_default_nginx_after_failed_prearm(
            paths=paths, backup=backup, failed=failed, runner=runner
        )
        _fail("bounded Emergency UFW rule could not be added; the previous default site was restored")


def prearm(
    *,
    campaign: VerifiedCampaign,
    paths: ActivationPaths,
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if _receipt_path(paths, campaign.campaign_id, "prearmed").exists():
        _fail("Emergency prearm receipt already exists; refusing to overwrite prior ingress state")
    prepared = _require_prepare(paths, campaign, profile=profile)
    _read_receipt(paths, campaign, stage="api-ready")
    if profile == "sms-otp":
        _require_sms_preflight(paths, campaign)
    _check_staging_listener(8213)
    _check_staging_listener(8443)
    _prearm_nginx(
        paths=paths,
        campaign=campaign,
        package_root=Path(str(prepared["package_root"])),
        profile=profile,
        runner=runner,
    )
    payload = {
        "profile": profile,
        "nginx": "prearmed",
        "ufw_rule_added": "allow proto tcp from any to any port 80,443 comment trading-bot-emergency-ir",
    }
    _write_receipt(paths, campaign, stage="prearmed", payload=payload)
    return payload


def execute(
    *,
    campaign_id: str,
    profile: str,
    stage: str | None,
    apply: bool,
    confirm: str | None,
    paths: ActivationPaths = ActivationPaths(),
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    campaign = verify_campaign(campaign_id=campaign_id, paths=paths, runner=runner)
    if not apply:
        return activation_plan(campaign, profile=profile)
    if stage is None:
        _fail("an explicit activation stage is required with --apply")
    required_confirmation = confirmation_phrase(campaign, stage=stage, profile=profile)
    if confirm != required_confirmation:
        _fail("Emergency activation confirmation does not exactly match the planned stage")
    handlers = {
        "prepare": prepare,
        "images": load_images,
        "database": database,
        "api": api,
        "prearm": prearm,
    }
    payload = handlers[stage](campaign=campaign, paths=paths, profile=profile, runner=runner)
    return {
        "status": "applied-local-stage",
        "campaign_id": campaign.campaign_id,
        "manifest_sha256": campaign.manifest_sha256,
        "profile": profile,
        "stage": stage,
        "payload": payload,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--profile", choices=("telegram-only", "sms-otp"), default="telegram-only")
    parser.add_argument("--stage", choices=("prepare", "images", "database", "api", "prearm"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = execute(
            campaign_id=args.campaign,
            profile=args.profile,
            stage=args.stage,
            apply=args.apply,
            confirm=args.confirm,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
