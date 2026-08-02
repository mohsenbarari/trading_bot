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
from datetime import datetime, timedelta, timezone
import hmac
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


# The bootstrap bundle executes this module with ``scripts`` one directory
# below sys.path.  The installed package uses the same layout.  Do not rely on
# the caller's current directory for a security boundary.
MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

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
MAX_CERTIFICATE_BYTES = 1024 * 1024
DISK_HEADROOM_BYTES = 256 * 1024 * 1024
CERTIFICATE_MIN_REMAINING = timedelta(days=7)
EMERGENCY_DOMAIN = "coin.gold-trade.ir"
PREARM_ATTEMPT_ID_RE = re.compile(r"^[a-f0-9]{32}$", re.ASCII)
PREARM_ATTEMPT_STAGE_RE = re.compile(
    r"^prearm-attempt-([a-f0-9]{32})-(intent|ufw-pending|aborted|armed)$", re.ASCII
)
PREARM_ATTEMPT_INTENT_SCHEMA = "gold-trade-emergency-ir-prearm-attempt-intent-v2"
PREARM_ATTEMPT_PENDING_SCHEMA = "gold-trade-emergency-ir-prearm-ufw-pending-v2"
PREARM_ATTEMPT_ABORTED_SCHEMA = "gold-trade-emergency-ir-prearm-attempt-aborted-v2"
PREARM_ATTEMPT_ARMED_SCHEMA = "gold-trade-emergency-ir-prearm-armed-v2"
UFW_BASELINE_SCHEMA = "gold-trade-emergency-ir-ufw-baseline-v1"
FIREWALL_ATTESTATION_SCHEMA = "gold-trade-emergency-ir-firewall-attestation-v1"
UFW_VERSION_RE = re.compile(r"^ufw 0\.36(?:\.\d+)?$", re.ASCII)
NFT_COUNTER_RE = re.compile(r"\bcounter packets \d+ bytes \d+\b")
UFW_RULE_COMMENT = "trading-bot-emergency-ir"
UFW_RULE_COMMAND_TEXT = "allow proto tcp from any to any port 80,443 comment trading-bot-emergency-ir"
UFW_SHOW_ADDED_OWNED_RULE = "ufw allow 80,443/tcp comment 'trading-bot-emergency-ir'"
UFW_CONTROL_RULE_COMMENT = "three-site-wa-ir-control"
UFW_CONTROL_SHOW_ADDED_RULE = "ufw allow 22/tcp comment 'three-site-wa-ir-control'"
UFW_DEFAULT_POLICY_RE = re.compile(
    r"^Default:\s+deny\s+\(incoming\),\s+allow\s+\(outgoing\),\s+(?:deny|disabled)\s+\(routed\)$"
)
IPTABLES_COUNTER_RE = re.compile(rb"\[\d+:\d+\]")
IPTABLES_RULE_RE = re.compile(r"^-A\s+(?P<chain>\S+)\s+(?P<body>.+)$")
IPTABLES_DPORT_RE = re.compile(r"(?:--dport|--dports|--destination-port)\s+(?P<ports>[0-9,:-]+)")
IPTABLES_TARGET_RE = re.compile(r"(?:-j|--jump|--goto)\s+(?P<target>\S+)")
IPTABLES_LOOPBACK_RE = re.compile(
    r"(?:-(?:i|s)\s+lo\b|--(?:in-interface|source)\s+(?:lo|127(?:\.\d{1,3}){3}(?:/\d{1,2})?|::1(?:/128)?))"
)
IPTABLES_PRIVATE_DESTINATION_RE = re.compile(
    r"(?:-(?:d)\s+|--destination\s+)(?:10(?:\.\d{1,3}){3}(?:/\d{1,2})?|"
    r"127(?:\.\d{1,3}){3}(?:/\d{1,2})?|192\.168(?:\.\d{1,3}){2}(?:/\d{1,2})?|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}(?:/\d{1,2})?|"
    r"::1(?:/128)?|f[cd][0-9a-f:]+(?:/\d{1,3})?)\b",
    re.IGNORECASE,
)
NFT_DPORT_RE = re.compile(
    r"\b(?:tcp|th)\s+dport\s+(?P<ports>\{[^}]+\}|[0-9]+(?:[:-][0-9]+)?(?:\s*,\s*[0-9]+(?:[:-][0-9]+)?)*)(?=\s|$)",
    re.IGNORECASE,
)
NFT_LOOPBACK_RE = re.compile(r"\b(?:iif|iifname)\s+(?:lo|\"lo\")\b", re.IGNORECASE)
NFT_PRIVATE_DESTINATION_RE = re.compile(
    r"\b(?:ip|ip6)\s+daddr\s+(?:10\.|127\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|::1\b|f[cd][0-9a-f:])",
    re.IGNORECASE,
)

AGE_BINARY = "/usr/bin/age"
AGE_KEYGEN_BINARY = "/usr/bin/age-keygen"
DOCKER_BINARY = "/usr/bin/docker"
NGINX_BINARY = "/usr/sbin/nginx"
SYSTEMCTL_BINARY = "/usr/bin/systemctl"
UFW_BINARY = "/usr/sbin/ufw"
IPTABLES_SAVE_BINARY = "/usr/sbin/iptables-save"
IP6TABLES_SAVE_BINARY = "/usr/sbin/ip6tables-save"
NFT_BINARY = "/usr/sbin/nft"
PYTHON_BINARY = "/usr/bin/python3"


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
    tls_source_fullchain: Path = Path(
        "/etc/trading-bot-emergency/acme/config/live/emergency-coin-gold-trade-ir/fullchain.pem"
    )
    tls_source_privkey: Path = Path(
        "/etc/trading-bot-emergency/acme/config/live/emergency-coin-gold-trade-ir/privkey.pem"
    )
    tls_source_archive_root: Path = Path(
        "/etc/trading-bot-emergency/acme/config/archive/emergency-coin-gold-trade-ir"
    )
    tls_pinned_fullchain: Path = Path("/etc/trading-bot-emergency/standalone/tls/fullchain.pem")
    tls_pinned_privkey: Path = Path("/etc/trading-bot-emergency/standalone/tls/privkey.pem")
    ufw_defaults: Path = Path("/etc/default/ufw")
    ufw_before_rules: Path = Path("/etc/ufw/before.rules")
    ufw_after_rules: Path = Path("/etc/ufw/after.rules")
    ufw_before6_rules: Path = Path("/etc/ufw/before6.rules")
    ufw_after6_rules: Path = Path("/etc/ufw/after6.rules")


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


@dataclasses.dataclass(frozen=True)
class NginxLifecycle:
    enabled: bool
    active: bool


@dataclasses.dataclass
class NginxLifecycleChanges:
    enable_attempted: bool = False
    start_attempted: bool = False


@dataclasses.dataclass(frozen=True)
class UfwRuleState:
    rule_present: bool
    ipv6_rule_present: bool
    baseline: Mapping[str, Any]


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


def _hash_root_regular(
    path: Path, *, label: str, maximum_bytes: int, private: bool = True
) -> tuple[str, int]:
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


def _fsync_directory(path: Path) -> None:
    """Persist a newly-created immutable receipt name before host mutation."""

    _secure_directory(path, create=False)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise EmergencyActivationError("Emergency receipt directory cannot be synchronized") from exc
    finally:
        if descriptor is not None:
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
    if stage not in {"prepare", "images", "database", "api", "tls", "firewall", "prearm"}:
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
                "stage": "tls",
                "does": "verify the local certbot source and create a root-only pinned certificate/key receipt",
                "confirm": confirmation_phrase(campaign, stage="tls", profile=profile),
            },
            {
                "stage": "firewall",
                "does": "record a human-confirmed raw firewall baseline and strict UFW ingress contract before any Nginx switch",
                "confirm": confirmation_phrase(campaign, stage="firewall", profile=profile),
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
    receipt_path = _receipt_path(paths, campaign.campaign_id, stage)
    _write_create_only(receipt_path, _canonical_json(result))
    # fsyncing the file preserves its bytes; fsyncing the containing directory
    # makes the immutable receipt name durable before a subsequent host action.
    _fsync_directory(receipt_path.parent)


def _read_receipt(paths: ActivationPaths, campaign: VerifiedCampaign, *, stage: str) -> dict[str, Any]:
    _secure_directory(_activation_campaign_root(paths, campaign.campaign_id), create=False)
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
        "scripts/verify_emergency_ir_sms_egress_image.py",
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


def _packaged_control_file(*, package_root: Path, relative: str, label: str) -> Path:
    """Return one sealed, extracted control file only after a fresh safe read check."""

    path = package_root / relative
    _root_regular(path, label=label, maximum_bytes=MAX_JSON_BYTES)
    return path


def _run_packaged_control(
    command: Sequence[str], *, label: str, runner: Callable[..., Any] = subprocess.run, timeout: int = 60
) -> None:
    """Execute one checked package control before Compose is allowed to mutate state."""

    try:
        result = runner(list(command), check=False, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError(f"{label} could not run") from exc
    if getattr(result, "returncode", 1) != 0:
        _fail(f"{label} failed")


def _verify_rendered_emergency_semantics(
    *,
    package_root: Path,
    paths: ActivationPaths,
    prepared: Mapping[str, Any],
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Run sealed semantic verifiers before any ``docker compose up``.

    The renderer only writes the private runtime environment.  This gate is
    deliberately between rendering and every Docker/Compose operation, so a
    malformed or accidentally broadened isolated configuration cannot create
    a volume, network, or container.  The verifier scripts are themselves
    read again as root-only regular files from the sealed extracted package.
    """

    source_sha = prepared.get("source_release_sha")
    patch_sha = prepared.get("emergency_patch_sha")
    if not isinstance(source_sha, str) or SHA_RE.fullmatch(source_sha) is None:
        _fail("prepared Emergency source release identity is invalid")
    if not isinstance(patch_sha, str) or SHA_RE.fullmatch(patch_sha) is None:
        _fail("prepared Emergency patch identity is invalid")
    if profile not in {"telegram-only", "sms-otp"}:
        _fail("activation profile is invalid")

    standalone_verifier = _packaged_control_file(
        package_root=package_root,
        relative="scripts/verify_emergency_ir_standalone.py",
        label="Emergency standalone semantic verifier",
    )
    compose = _packaged_control_file(
        package_root=package_root,
        relative="deploy/emergency-ir/docker-compose.standalone.yml",
        label="Emergency standalone Compose configuration",
    )
    nginx_name = "nginx.sms-otp.conf.template" if profile == "sms-otp" else "nginx.standalone.conf.template"
    nginx = _packaged_control_file(
        package_root=package_root,
        relative=f"deploy/emergency-ir/{nginx_name}",
        label="Emergency Nginx configuration template",
    )
    session_reset = _packaged_control_file(
        package_root=package_root,
        relative="deploy/emergency-ir/reset-emergency-sessions.sql",
        label="Emergency session-reset SQL",
    )
    _root_regular(paths.runtime_env, label="Emergency rendered runtime environment", maximum_bytes=MAX_JSON_BYTES)
    command = [
        PYTHON_BINARY,
        "-I",
        "-B",
        str(standalone_verifier),
        "--profile",
        profile,
        "--env",
        str(paths.runtime_env),
        "--compose",
        str(compose),
        "--nginx",
        str(nginx),
        "--session-reset",
        str(session_reset),
    ]
    if profile == "sms-otp":
        sms_compose = _packaged_control_file(
            package_root=package_root,
            relative="deploy/emergency-ir/docker-compose.sms-otp.yml",
            label="Emergency SMS Compose configuration",
        )
        sms_relay = _packaged_control_file(
            package_root=package_root,
            relative="deploy/emergency-ir/sms-egress.nginx.conf",
            label="Emergency SMS relay configuration",
        )
        nginx_rate_limit = _packaged_control_file(
            package_root=package_root,
            relative="deploy/emergency-ir/nginx.sms-otp.rate-limit.conf",
            label="Emergency SMS Nginx rate-limit configuration",
        )
        command.extend(
            [
                "--sms-compose",
                str(sms_compose),
                "--sms-relay",
                str(sms_relay),
                "--nginx-rate-limit",
                str(nginx_rate_limit),
            ]
        )
    _run_packaged_control(command, label="Emergency standalone semantic verification", runner=runner)

    app_verifier = _packaged_control_file(
        package_root=package_root,
        relative="scripts/verify_emergency_ir_image_provenance.py",
        label="Emergency application image verifier",
    )
    _run_packaged_control(
        [
            PYTHON_BINARY,
            "-I",
            "-B",
            str(app_verifier),
            "--image",
            f"trading_bot_emergency_ir_app:{patch_sha}",
            "--source-release-sha",
            source_sha,
            "--emergency-patch-sha",
            patch_sha,
        ],
        label="Emergency application image provenance verification",
        runner=runner,
    )
    if profile == "sms-otp":
        sms_verifier = _packaged_control_file(
            package_root=package_root,
            relative="scripts/verify_emergency_ir_sms_egress_image.py",
            label="Emergency SMS relay image verifier",
        )
        _run_packaged_control(
            [
                PYTHON_BINARY,
                "-I",
                "-B",
                str(sms_verifier),
                "--image",
                f"trading_bot_emergency_ir_sms_egress:{patch_sha}",
                "--source-release-sha",
                source_sha,
                "--emergency-patch-sha",
                patch_sha,
            ],
            label="Emergency SMS relay image provenance verification",
            runner=runner,
        )


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
    _ensure_current_link(paths=paths, package_root=package_root)
    _run_renderer(
        package_root=package_root, paths=paths, prepared=prepared, profile=profile, settings=settings, runner=runner
    )
    _verify_rendered_emergency_semantics(
        package_root=package_root, paths=paths, prepared=prepared, profile=profile, runner=runner
    )
    _assert_fresh_docker_resources(runner=runner, profile=profile)
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
    _verify_rendered_emergency_semantics(
        package_root=package_root, paths=paths, prepared=prepared, profile=profile, runner=runner
    )
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


def _check_staging_listener(port: int) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return
    except OSError as exc:
        raise EmergencyActivationError("protected three-site staging listener is not healthy") from exc


def _pinned_tls_paths(paths: ActivationPaths) -> tuple[Path, Path]:
    return paths.tls_pinned_fullchain, paths.tls_pinned_privkey


def _read_certbot_tls_source(
    *,
    source: Path,
    archive_root: Path,
    label: str,
    private: bool,
) -> bytes:
    """Read a certbot ``live`` terminal symlink only through its trusted archive.

    Certbot's live leaf is normally a symlink.  It is a source only: the
    Nginx contract below uses a create-only regular-file snapshot instead.
    """

    _secure_directory(source.parent, create=False)
    _secure_directory(archive_root, create=False)
    try:
        before = source.lstat()
    except OSError as exc:
        raise EmergencyActivationError(f"{label} source cannot be inspected") from exc
    if not stat.S_ISLNK(before.st_mode):
        _fail(f"{label} source must be a root-controlled symlink into the trusted certbot archive")
    if before.st_uid != 0 or before.st_nlink != 1:
        _fail(f"{label} source symlink is not root-controlled")
    try:
        target = source.resolve(strict=True)
    except OSError as exc:
        raise EmergencyActivationError(f"{label} source symlink cannot be resolved") from exc
    if not target.is_relative_to(archive_root):
        _fail(f"{label} source symlink escapes the trusted certbot archive")
    _secure_directory(target.parent, create=False)
    payload = _read_root_regular(
        target, label=f"{label} archive target", maximum_bytes=MAX_CERTIFICATE_BYTES, private=private
    )
    try:
        after = source.lstat()
        resolved_after = source.resolve(strict=True)
    except OSError as exc:
        raise EmergencyActivationError(f"{label} source changed while being read") from exc
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields) or resolved_after != target:
        _fail(f"{label} source changed while being read")
    return payload


def _validate_tls_material(*, fullchain: bytes, private_key: bytes) -> None:
    """Check one local certificate/key pair without contacting any network."""

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover - manifest already requires cryptography in production.
        raise EmergencyActivationError("local X.509 verification dependency is unavailable") from exc
    try:
        certificates = x509.load_pem_x509_certificates(fullchain)
        if not certificates:
            _fail("pinned Emergency certificate chain is empty")
        leaf = certificates[0]
        key = serialization.load_pem_private_key(private_key, password=None)
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = tuple(san.get_values_for_type(x509.DNSName))
        if names != (EMERGENCY_DOMAIN,):
            _fail("Emergency certificate SAN is not exactly the configured domain")
        not_before = leaf.not_valid_before
        not_after = leaf.not_valid_after
        if not_before.tzinfo is None:
            not_before = not_before.replace(tzinfo=timezone.utc)
        if not_after.tzinfo is None:
            not_after = not_after.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if not_before > now:
            _fail("Emergency certificate is not valid yet")
        if not_after <= now + CERTIFICATE_MIN_REMAINING:
            _fail("Emergency certificate does not meet the minimum remaining validity")
        certificate_public = leaf.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        private_public = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    except EmergencyActivationError:
        raise
    except Exception as exc:
        raise EmergencyActivationError("Emergency certificate/key material is invalid") from exc
    if not hmac.compare_digest(certificate_public, private_public):
        _fail("Emergency certificate private key does not match the leaf certificate")


def _pin_tls_file(*, destination: Path, payload: bytes, label: str) -> None:
    """Create one immutable TLS snapshot file, or reuse an identical one."""

    try:
        destination.lstat()
    except FileNotFoundError:
        _write_create_only(destination, payload)
        return
    except OSError as exc:
        raise EmergencyActivationError(f"{label} snapshot cannot be inspected") from exc
    existing = _read_root_regular(
        destination, label=f"{label} snapshot", maximum_bytes=MAX_CERTIFICATE_BYTES
    )
    if not hmac.compare_digest(existing, payload):
        _fail(f"{label} snapshot already exists with different content")


def _validate_pinned_tls(*, paths: ActivationPaths) -> tuple[Path, Path]:
    fullchain_path, private_key_path = _pinned_tls_paths(paths)
    fullchain = _read_root_regular(
        fullchain_path, label="pinned Emergency certificate", maximum_bytes=MAX_CERTIFICATE_BYTES
    )
    private_key = _read_root_regular(
        private_key_path, label="pinned Emergency certificate private key", maximum_bytes=MAX_CERTIFICATE_BYTES
    )
    _validate_tls_material(fullchain=fullchain, private_key=private_key)
    return fullchain_path, private_key_path


def pin_tls(
    *,
    campaign: VerifiedCampaign,
    paths: ActivationPaths,
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Snapshot the checked local certbot pair to root-only regular files."""

    del runner
    if _receipt_path(paths, campaign.campaign_id, "tls-pinned").exists():
        _fail("Emergency TLS receipt already exists; refusing to overwrite a prior TLS pin")
    _require_prepare(paths, campaign, profile=profile)
    _read_receipt(paths, campaign, stage="api-ready")
    fullchain = _read_certbot_tls_source(
        source=paths.tls_source_fullchain,
        archive_root=paths.tls_source_archive_root,
        label="Emergency certificate",
        private=False,
    )
    private_key = _read_certbot_tls_source(
        source=paths.tls_source_privkey,
        archive_root=paths.tls_source_archive_root,
        label="Emergency certificate private key",
        private=True,
    )
    _validate_tls_material(fullchain=fullchain, private_key=private_key)
    fullchain_path, private_key_path = _pinned_tls_paths(paths)
    _secure_directory(fullchain_path.parent, create=True)
    _pin_tls_file(destination=fullchain_path, payload=fullchain, label="Emergency certificate")
    _pin_tls_file(destination=private_key_path, payload=private_key, label="Emergency certificate private key")
    _validate_pinned_tls(paths=paths)
    payload = {
        "profile": profile,
        "domain": EMERGENCY_DOMAIN,
        "fullchain_path": str(fullchain_path),
        "private_key_path": str(private_key_path),
        "minimum_remaining_seconds": int(CERTIFICATE_MIN_REMAINING.total_seconds()),
    }
    _write_receipt(paths, campaign, stage="tls-pinned", payload=payload)
    return payload


def _require_pinned_tls(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, profile: str
) -> tuple[Path, Path]:
    payload = _read_receipt(paths, campaign, stage="tls-pinned")
    fullchain_path, private_key_path = _pinned_tls_paths(paths)
    expected = {
        "profile": profile,
        "domain": EMERGENCY_DOMAIN,
        "fullchain_path": str(fullchain_path),
        "private_key_path": str(private_key_path),
        "minimum_remaining_seconds": int(CERTIFICATE_MIN_REMAINING.total_seconds()),
    }
    if payload != expected:
        _fail("Emergency TLS receipt is not bound to the pinned certificate contract")
    return _validate_pinned_tls(paths=paths)


def _render_nginx_configuration(
    *, package_root: Path, profile: str, fullchain_path: Path, private_key_path: Path
) -> bytes:
    if profile == "sms-otp":
        candidate = package_root / "deploy/emergency-ir/nginx.sms-otp.conf.template"
        rate = package_root / "deploy/emergency-ir/nginx.sms-otp.rate-limit.conf"
        _root_regular(rate, label="Emergency SMS Nginx rate limit", maximum_bytes=MAX_JSON_BYTES)
    else:
        candidate = package_root / "deploy/emergency-ir/nginx.standalone.conf.template"
    payload = _read_root_regular(candidate, label="Emergency Nginx configuration", maximum_bytes=MAX_JSON_BYTES)
    fullchain_token = b"__EMERGENCY_TLS_FULLCHAIN__"
    private_key_token = b"__EMERGENCY_TLS_PRIVATE_KEY__"
    required = (b"server_name coin.gold-trade.ir", b"proxy_pass http://127.0.0.1:18000", fullchain_token, private_key_token)
    if any(value not in payload for value in required) or payload.count(fullchain_token) != 2 or payload.count(private_key_token) != 2:
        _fail("Emergency Nginx configuration does not satisfy the pinned TLS ingress contract")
    rendered = payload.replace(fullchain_token, str(fullchain_path).encode("ascii")).replace(
        private_key_token, str(private_key_path).encode("ascii")
    )
    if fullchain_token in rendered or private_key_token in rendered:
        _fail("Emergency Nginx TLS configuration was not fully rendered")
    return rendered


def _result_text(result: Any) -> str:
    raw = getattr(result, "stdout", "")
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return ""
    return str(raw).strip()


def _path_exists_or_symlink(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise EmergencyActivationError("Emergency path cannot be inspected") from exc
    return True


def _root_owned_symlink_target(path: Path, *, label: str) -> str:
    _secure_directory(path.parent, create=False)
    try:
        item = path.lstat()
        target = os.readlink(path)
    except OSError as exc:
        raise EmergencyActivationError(f"{label} cannot be inspected") from exc
    if not stat.S_ISLNK(item.st_mode) or item.st_uid != 0 or item.st_nlink != 1 or not target or "\x00" in target:
        _fail(f"{label} must be one root-owned symlink")
    return target


def _require_absent(path: Path, *, label: str) -> None:
    if _path_exists_or_symlink(path):
        _fail(f"{label} must be absent")


def _ufw_query(
    arguments: Sequence[str], *, runner: Callable[..., Any] = subprocess.run
) -> str:
    environment = dict(os.environ)
    environment.update({"LANG": "C", "LANGUAGE": "C", "LC_ALL": "C"})
    try:
        result = runner(
            [UFW_BINARY, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError("Emergency UFW state cannot be inspected") from exc
    if getattr(result, "returncode", 1) != 0:
        _fail("Emergency UFW state cannot be inspected")
    return _result_text(result)


def _normalized_ufw_line(value: str) -> str:
    return " ".join(value.strip().split())


def _ufw_numbered_rows(text: str) -> tuple[str, ...]:
    lines = [_normalized_ufw_line(line) for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "Status: active":
        _fail("Emergency UFW must be active before ingress can be armed")
    rows: list[str] = []
    for line in lines[1:]:
        if line == "To Action From" or re.fullmatch(r"-+ -+ -+", line):
            continue
        matched = re.fullmatch(r"\[\s*\d+\]\s+(.+)", line)
        if matched is None:
            _fail("Emergency UFW numbered-rule output is unrecognized")
        rows.append(matched.group(1))
    if len(rows) != len(set(rows)):
        _fail("Emergency UFW numbered rules are duplicated")
    return tuple(sorted(rows))


def _ufw_verbose_baseline(text: str, *, expect_emergency_rule: bool) -> tuple[str, ...]:
    lines = [_normalized_ufw_line(line) for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "Status: active":
        _fail("Emergency UFW must be active before ingress can be armed")
    defaults = [line for line in lines if line.startswith("Default:")]
    if len(defaults) != 1 or UFW_DEFAULT_POLICY_RE.fullmatch(defaults[0]) is None:
        _fail("Emergency UFW must retain an incoming-deny default policy")
    owned = [line for line in lines if UFW_RULE_COMMENT in line]
    expected_owned = {
        "80,443/tcp ALLOW IN Anywhere # trading-bot-emergency-ir",
        "80,443/tcp (v6) ALLOW IN Anywhere (v6) # trading-bot-emergency-ir",
    }
    if expect_emergency_rule:
        if set(owned) != expected_owned or len(owned) != len(expected_owned):
            _fail("Emergency UFW verbose state lacks the exact owned ingress rules")
    elif owned:
        _fail("Emergency UFW already exposes owned Emergency ingress before prearm")
    return tuple(line for line in lines if line not in owned)


def _ufw_baseline_payload(*, verbose: tuple[str, ...], numbered: tuple[str, ...], added: tuple[str, ...]) -> dict[str, Any]:
    return {
        "schema": UFW_BASELINE_SCHEMA,
        "status_verbose": list(verbose),
        "status_numbered": list(numbered),
        "show_added": list(added),
    }


def _validate_ufw_baseline(value: Any) -> dict[str, Any]:
    required = {"schema", "status_verbose", "status_numbered", "show_added"}
    if not isinstance(value, dict) or set(value) != required or value.get("schema") != UFW_BASELINE_SCHEMA:
        _fail("Emergency UFW baseline is malformed")
    normalized: dict[str, Any] = {"schema": UFW_BASELINE_SCHEMA}
    for key in ("status_verbose", "status_numbered", "show_added"):
        rows = value.get(key)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, str) or not row for row in rows):
            _fail("Emergency UFW baseline is malformed")
        normalized[key] = list(rows)
    return normalized


def _ufw_baseline_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _require_ufw_baseline(actual: UfwRuleState, expected: Any) -> None:
    baseline = _validate_ufw_baseline(expected)
    if dict(actual.baseline) != baseline:
        _fail("Emergency UFW state drifted from the recorded safe baseline")


def _capture_ufw_rule_state(
    *, runner: Callable[..., Any] = subprocess.run, expect_emergency_rule: bool
) -> UfwRuleState:
    """Require the explicit safe UFW contract and capture its non-Emergency baseline.

    Fresh prearm permits only the audited WA-IR control SSH rule under an
    incoming-deny policy.  That prevents an old profile/range/broad rule from
    making the new Nginx candidate public before its local probes.  The owned
    80/443 rule is accepted only after the durable UFW-pending journal exists.
    """

    verbose = _ufw_verbose_baseline(
        _ufw_query(("status", "verbose"), runner=runner), expect_emergency_rule=expect_emergency_rule
    )
    numbered = _ufw_numbered_rows(_ufw_query(("status", "numbered"), runner=runner))
    expected_numbered = {
        "22/tcp ALLOW IN Anywhere # three-site-wa-ir-control",
        "22/tcp (v6) ALLOW IN Anywhere (v6) # three-site-wa-ir-control",
    }
    emergency_numbered = {
        "80,443/tcp ALLOW IN Anywhere # trading-bot-emergency-ir",
        "80,443/tcp (v6) ALLOW IN Anywhere (v6) # trading-bot-emergency-ir",
    }
    if set(numbered) != expected_numbered | (emergency_numbered if expect_emergency_rule else set()):
        _fail("Emergency UFW numbered rules do not match the safe ingress contract")

    added_lines = [_normalized_ufw_line(line) for line in _ufw_query(("show", "added"), runner=runner).splitlines() if line.strip()]
    if not added_lines or added_lines[0] != "Added user rules (see 'ufw status' for running firewall):":
        _fail("Emergency UFW added-rule state is unrecognized")
    added = tuple(sorted(added_lines[1:]))
    expected_added = {UFW_CONTROL_SHOW_ADDED_RULE}
    if expect_emergency_rule:
        expected_added.add(UFW_SHOW_ADDED_OWNED_RULE)
    if set(added) != expected_added or len(added) != len(expected_added):
        _fail("Emergency UFW stored rules do not match the safe ingress contract")

    return UfwRuleState(
        rule_present=expect_emergency_rule,
        ipv6_rule_present=expect_emergency_rule,
        baseline=_ufw_baseline_payload(verbose=verbose, numbered=numbered if not expect_emergency_rule else tuple(sorted(expected_numbered)), added=added if not expect_emergency_rule else (UFW_CONTROL_SHOW_ADDED_RULE,)),
    )


def _firewall_command_output(
    command: Sequence[str], *, runner: Callable[..., Any] = subprocess.run
) -> bytes:
    environment = dict(os.environ)
    environment.update({"LANG": "C", "LANGUAGE": "C", "LC_ALL": "C"})
    try:
        result = runner(
            list(command), check=False, capture_output=True, timeout=60, env=environment
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError("Emergency raw firewall state cannot be inspected") from exc
    if getattr(result, "returncode", 1) != 0:
        _fail("Emergency raw firewall state cannot be inspected")
    output = getattr(result, "stdout", b"")
    stderr = getattr(result, "stderr", b"")
    if isinstance(output, str):
        output = output.encode("utf-8")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8")
    if not isinstance(output, bytes) or not output or len(output) > MAX_JSON_BYTES:
        _fail("Emergency raw firewall state is invalid")
    if not isinstance(stderr, bytes) or len(stderr) > MAX_JSON_BYTES:
        _fail("Emergency raw firewall diagnostics are invalid")
    return output


def _normalized_nft_ruleset(payload: bytes) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EmergencyActivationError("Emergency nft ruleset is not UTF-8") from exc
    # nft emits live packet/byte counters in otherwise-identical rules.  Only
    # those two decimal values are normalized; every chain, rule and token is
    # retained in the attested representation.
    return NFT_COUNTER_RE.sub("counter packets <n> bytes <n>", text).encode("utf-8")


def _normalized_iptables_save(payload: bytes) -> bytes:
    """Normalize only iptables-save's live ``[packets:bytes]`` counters."""

    return IPTABLES_COUNTER_RE.sub(b"[<n>:<n>]", payload)


def _port_expression_contains_http_port(value: str) -> bool:
    """Return true only when a literal/range/set includes TCP 80 or 443."""

    normalized = value.strip().strip("{}").replace(" ", "")
    if not normalized:
        return False
    for component in normalized.split(","):
        if not component:
            return False
        if ":" in component:
            lower_raw, upper_raw = component.split(":", 1)
        elif "-" in component:
            lower_raw, upper_raw = component.split("-", 1)
        else:
            if not component.isdecimal():
                return False
            if int(component) in {80, 443}:
                return True
            continue
        if not lower_raw.isdecimal() or not upper_raw.isdecimal():
            return False
        lower, upper = int(lower_raw), int(upper_raw)
        if lower > upper or lower < 1 or upper > 65535:
            return False
        if lower <= 80 <= upper or lower <= 443 <= upper:
            return True
    return False


def _iptables_raw_exposure(*, payload: bytes, family: str) -> None:
    """Reject an unambiguous public TCP 80/443 path in raw iptables state.

    A Docker forward rule for a private container's *post-DNAT* port 443 is
    not a host public-443 publication.  It is therefore allowed only when the
    rule carries a private destination; the corresponding NAT rule is judged
    by its original destination port (for example 8443 is allowed, 443 is
    rejected).
    """

    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise EmergencyActivationError("Emergency raw firewall state is not UTF-8") from exc
    table = ""
    for raw in lines:
        if raw.startswith("*"):
            table = raw[1:]
            continue
        matched = IPTABLES_RULE_RE.fullmatch(raw)
        if matched is None:
            continue
        body = matched.group("body")
        dport = IPTABLES_DPORT_RE.search(body)
        if dport is None or not _port_expression_contains_http_port(dport.group("ports")):
            continue
        if IPTABLES_LOOPBACK_RE.search(body) is not None:
            continue
        target_match = IPTABLES_TARGET_RE.search(body)
        target = target_match.group("target").upper() if target_match is not None else ""
        if target in {"DROP", "REJECT"}:
            continue
        # The private container accept after a non-public host-port DNAT is
        # not a new public listener.  Never grant this exception to INPUT or
        # nat, where the packet still represents a host-facing port decision.
        if table != "nat" and matched.group("chain") != "INPUT" and IPTABLES_PRIVATE_DESTINATION_RE.search(body):
            continue
        _fail(
            f"Emergency raw {family} firewall exposes or routes TCP 80/443 before ingress prearm"
        )


def _nft_raw_exposure(*, payload: bytes) -> None:
    """Apply the same bounded public-port rule to nft syntax."""

    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise EmergencyActivationError("Emergency nft ruleset is not UTF-8") from exc
    for raw in lines:
        line = raw.split("#", 1)[0]
        matched = NFT_DPORT_RE.search(line)
        if matched is None or not _port_expression_contains_http_port(matched.group("ports")):
            continue
        lowered = line.lower()
        if NFT_LOOPBACK_RE.search(line) is not None or " drop" in lowered or " reject" in lowered:
            continue
        # nftables' Docker forward allow is post-DNAT and bound to the
        # private container destination.  A direct input/NAT dport rule has
        # no such exemption and is rejected.
        if NFT_PRIVATE_DESTINATION_RE.search(line) is not None and " dnat " not in lowered and " redirect" not in lowered:
            continue
        _fail("Emergency raw nft firewall exposes or routes TCP 80/443 before ingress prearm")


def _assert_no_raw_external_http_exposure(*, iptables: bytes, ip6tables: bytes, nft: bytes) -> None:
    """Require a closed raw 80/443 baseline before the explicit UFW PONR."""

    _iptables_raw_exposure(payload=iptables, family="IPv4")
    _iptables_raw_exposure(payload=ip6tables, family="IPv6")
    _nft_raw_exposure(payload=nft)


def _ufw_static_hashes(paths: ActivationPaths) -> dict[str, str]:
    values = {
        "defaults": paths.ufw_defaults,
        "before_rules": paths.ufw_before_rules,
        "after_rules": paths.ufw_after_rules,
        "before6_rules": paths.ufw_before6_rules,
        "after6_rules": paths.ufw_after6_rules,
    }
    result: dict[str, str] = {}
    for name, path in values.items():
        digest, _ = _hash_root_regular(
            path, label=f"Emergency UFW static {name}", maximum_bytes=MAX_JSON_BYTES, private=False
        )
        result[name] = digest
    defaults = _read_root_regular(
        paths.ufw_defaults, label="Emergency UFW defaults", maximum_bytes=MAX_JSON_BYTES, private=False
    )
    if not any(line.strip() == b"IPV6=yes" for line in defaults.splitlines()):
        _fail("Emergency UFW must have IPV6=yes for paired ingress rules")
    return result


def _capture_firewall_attestation(
    *, paths: ActivationPaths, runner: Callable[..., Any] = subprocess.run
) -> dict[str, Any]:
    version_text = _firewall_command_output((UFW_BINARY, "--version"), runner=runner).decode("utf-8", "strict").splitlines()
    if not version_text or UFW_VERSION_RE.fullmatch(version_text[0].strip()) is None:
        _fail("Emergency UFW version is outside the attested 0.36 contract")
    ufw_state = _capture_ufw_rule_state(runner=runner, expect_emergency_rule=False)
    iptables = _firewall_command_output((IPTABLES_SAVE_BINARY,), runner=runner)
    ip6tables = _firewall_command_output((IP6TABLES_SAVE_BINARY,), runner=runner)
    nft = _firewall_command_output((NFT_BINARY, "list", "ruleset"), runner=runner)
    _assert_no_raw_external_http_exposure(iptables=iptables, ip6tables=ip6tables, nft=nft)
    return {
        "schema": FIREWALL_ATTESTATION_SCHEMA,
        "ufw_version": version_text[0].strip(),
        "ufw_baseline": dict(ufw_state.baseline),
        "ufw_static_sha256": _ufw_static_hashes(paths),
        "iptables_save_sha256": hashlib.sha256(_normalized_iptables_save(iptables)).hexdigest(),
        "ip6tables_save_sha256": hashlib.sha256(_normalized_iptables_save(ip6tables)).hexdigest(),
        "nft_ruleset_sha256": hashlib.sha256(_normalized_nft_ruleset(nft)).hexdigest(),
    }


def _validate_firewall_attestation(value: Any) -> dict[str, Any]:
    required = {
        "schema",
        "ufw_version",
        "ufw_baseline",
        "ufw_static_sha256",
        "iptables_save_sha256",
        "ip6tables_save_sha256",
        "nft_ruleset_sha256",
    }
    if not isinstance(value, dict) or set(value) != required or value.get("schema") != FIREWALL_ATTESTATION_SCHEMA:
        _fail("Emergency firewall attestation is malformed")
    version = value.get("ufw_version")
    if not isinstance(version, str) or UFW_VERSION_RE.fullmatch(version) is None:
        _fail("Emergency firewall attestation has an unsupported UFW version")
    static_hashes = value.get("ufw_static_sha256")
    expected_static = {"defaults", "before_rules", "after_rules", "before6_rules", "after6_rules"}
    if (
        not isinstance(static_hashes, dict)
        or set(static_hashes) != expected_static
        or any(not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None for digest in static_hashes.values())
    ):
        _fail("Emergency firewall static attestation is malformed")
    normalized = {
        "schema": FIREWALL_ATTESTATION_SCHEMA,
        "ufw_version": version,
        "ufw_baseline": _validate_ufw_baseline(value.get("ufw_baseline")),
        "ufw_static_sha256": {name: static_hashes[name] for name in sorted(expected_static)},
    }
    for name in ("iptables_save_sha256", "ip6tables_save_sha256", "nft_ruleset_sha256"):
        digest = value.get(name)
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            _fail("Emergency raw firewall attestation is malformed")
        normalized[name] = digest
    return normalized


def firewall_attest(
    *,
    campaign: VerifiedCampaign,
    paths: ActivationPaths,
    profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Record a human-confirmed, immutable raw firewall baseline before prearm."""

    if _path_exists_or_symlink(_receipt_path(paths, campaign.campaign_id, "firewall-attested")):
        _fail("Emergency firewall attestation already exists; refusing to overwrite it")
    _require_prepare(paths, campaign, profile=profile)
    _read_receipt(paths, campaign, stage="api-ready")
    _require_pinned_tls(paths=paths, campaign=campaign, profile=profile)
    payload = {
        "profile": profile,
        "attestation": _validate_firewall_attestation(
            _capture_firewall_attestation(paths=paths, runner=runner)
        ),
    }
    _write_receipt(paths, campaign, stage="firewall-attested", payload=payload)
    return payload


def _require_firewall_attestation(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, profile: str, runner: Callable[..., Any]
) -> dict[str, Any]:
    payload = _read_receipt(paths, campaign, stage="firewall-attested")
    if set(payload) != {"profile", "attestation"} or payload.get("profile") != profile:
        _fail("Emergency firewall attestation is not bound to this profile")
    recorded = _validate_firewall_attestation(payload.get("attestation"))
    observed = _validate_firewall_attestation(_capture_firewall_attestation(paths=paths, runner=runner))
    if _canonical_json(recorded) != _canonical_json(observed):
        _fail("Emergency raw firewall state differs from the confirmed attestation")
    return recorded


def _capture_nginx_lifecycle(*, runner: Callable[..., Any] = subprocess.run) -> NginxLifecycle:
    try:
        enabled = runner(
            [SYSTEMCTL_BINARY, "is-enabled", "nginx"], check=False, capture_output=True, text=True, timeout=60
        )
        active = runner(
            [SYSTEMCTL_BINARY, "is-active", "nginx"], check=False, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError("Nginx lifecycle cannot be inspected") from exc
    enabled_text = _result_text(enabled)
    active_text = _result_text(active)
    if enabled_text == "enabled" and getattr(enabled, "returncode", 1) == 0:
        is_enabled = True
    elif enabled_text == "disabled" and getattr(enabled, "returncode", 0) != 0:
        is_enabled = False
    else:
        _fail("Nginx enabled lifecycle state is unsupported")
    if active_text == "active" and getattr(active, "returncode", 1) == 0:
        is_active = True
    elif active_text == "inactive" and getattr(active, "returncode", 0) != 0:
        is_active = False
    else:
        _fail("Nginx active lifecycle state is unsupported")
    return NginxLifecycle(enabled=is_enabled, active=is_active)


def _systemctl_action(
    action: str, *, runner: Callable[..., Any] = subprocess.run
) -> Any:
    try:
        return runner(
            [SYSTEMCTL_BINARY, action, "nginx"], check=False, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError(f"Nginx {action} command could not run") from exc


def _activate_candidate_nginx(
    *, lifecycle: NginxLifecycle, changes: NginxLifecycleChanges, runner: Callable[..., Any]
) -> str:
    if not lifecycle.enabled:
        changes.enable_attempted = True
        if getattr(_systemctl_action("enable", runner=runner), "returncode", 1) != 0:
            _fail("Emergency Nginx configuration tested but could not be enabled for reboot")
    if lifecycle.active:
        if getattr(_systemctl_action("reload", runner=runner), "returncode", 1) != 0:
            _fail("Emergency Nginx configuration tested but could not be reloaded")
        return "reloaded"
    changes.start_attempted = True
    if getattr(_systemctl_action("start", runner=runner), "returncode", 1) != 0:
        _fail("Emergency Nginx configuration tested but could not be started")
    return "enabled-and-started" if not lifecycle.enabled else "started"


def _restore_default_nginx_after_failed_prearm(
    *,
    paths: ActivationPaths,
    backup: Path,
    failed: Path,
    lifecycle: NginxLifecycle,
    changes: NginxLifecycleChanges,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Restore both Nginx configuration and the lifecycle captured prearm.

    Every config change is a rename.  A prearm that enabled/started Nginx from
    an inactive state reverses only the lifecycle actions it attempted; it
    never deletes an unrelated service, rule, container, or volume.
    """

    try:
        os.rename(paths.nginx_enabled, failed)
        os.rename(backup, paths.nginx_default)
    except OSError as exc:
        raise EmergencyActivationError("Nginx prearm failed and the default site could not be restored") from exc
    failures: list[str] = []

    def restore_action(action: str, failure: str) -> None:
        try:
            result = _systemctl_action(action, runner=runner)
        except EmergencyActivationError:
            failures.append(failure)
            return
        if getattr(result, "returncode", 1) != 0:
            failures.append(failure)

    if not lifecycle.active and changes.start_attempted:
        restore_action("stop", "could not return Nginx to its prior inactive state")
    try:
        restored_test = runner([NGINX_BINARY, "-t"], check=False, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        failures.append("the restored default configuration test could not run")
    else:
        if getattr(restored_test, "returncode", 1) != 0:
            failures.append("the restored default configuration test failed")
    if lifecycle.active:
        restore_action("reload", "the restored default site could not be reloaded")
    if changes.enable_attempted:
        restore_action("disable", "Nginx could not be returned to its prior disabled state")
    if failures:
        _fail("Nginx prearm failed; the default site was restored but " + "; ".join(failures))


def _local_tls_probe() -> None:
    """Use direct localhost TLS/SNI only; DNS and outbound proxy are absent."""

    context = ssl.create_default_context()

    def request_status(path: str) -> int:
        with socket.create_connection(("127.0.0.1", 443), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=EMERGENCY_DOMAIN) as connection:
                connection.sendall(
                    (
                        f"GET {path} HTTP/1.1\r\nHost: {EMERGENCY_DOMAIN}\r\n"
                        "Connection: close\r\nAccept: application/json\r\n\r\n"
                    ).encode("ascii")
                )
                response = bytearray()
                while b"\r\n" not in response and len(response) < 16 * 1024:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
        first_line = bytes(response).split(b"\r\n", 1)[0].split()
        if len(first_line) < 2 or first_line[0] != b"HTTP/1.1" or not first_line[1].isdigit():
            _fail("Emergency local TLS ingress probe returned an invalid HTTP response")
        return int(first_line[1])

    try:
        if request_status("/api/config") != 200:
            _fail("Emergency local TLS ingress probe did not reach the API health endpoint")
        if request_status("/api/sync") != 404:
            _fail("Emergency local TLS ingress probe did not block the sync route")
    except EmergencyActivationError:
        raise
    except (OSError, ssl.SSLError) as exc:
        raise EmergencyActivationError("Emergency local TLS ingress probe failed") from exc


NGINX_DUMP_FILE_RE = re.compile(r"^# configuration file (?P<path>[^:]+):$")
NGINX_PUBLIC_LISTEN_RE = re.compile(
    r"(?:^|[;{}])\s*listen\s+(?:(?:\[[^\]]+\]|[^\s;{}]+):)?(?P<port>80|443)(?=\s|;)",
    re.ASCII,
)


def _assert_nginx_public_listener_inventory(
    *,
    paths: ActivationPaths,
    allowed_config: Path,
    required_ports: frozenset[int],
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Fail closed if an uncontrolled Nginx file can compete on public ports.

    The installed Debian default vhost legitimately has only port 80 before
    Emergency prearm.  It is safe only when it is the *sole* effective public
    listener and it is the exact symlink that the transaction journals and
    restores.  The rendered Emergency candidate must instead own both ports.
    """

    if not required_ports or not required_ports.issubset({80, 443}):
        _fail("Emergency Nginx listener inventory request is invalid")

    try:
        result = runner([NGINX_BINARY, "-T"], check=False, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyActivationError("Emergency Nginx listener inventory cannot be inspected") from exc
    if getattr(result, "returncode", 1) != 0:
        _fail("Emergency Nginx listener inventory cannot be inspected")
    output = _result_text(result)
    current: Path | None = None
    listeners: dict[int, set[Path]] = {80: set(), 443: set()}
    for raw in output.splitlines():
        header = NGINX_DUMP_FILE_RE.fullmatch(raw.strip())
        if header is not None:
            current = Path(header.group("path"))
            continue
        for matched in NGINX_PUBLIC_LISTEN_RE.finditer(raw.split("#", 1)[0]):
            if current is None:
                _fail("Emergency Nginx listener inventory has an unbound public listener")
            listeners[int(matched.group("port"))].add(current)
    actual_ports = {port for port, source_files in listeners.items() if source_files}
    if (
        not required_ports.issubset(actual_ports)
        or any(source_files != {allowed_config} for source_files in listeners.values() if source_files)
    ):
        _fail("Emergency Nginx has an uncontrolled public 80/443 listener")


@dataclasses.dataclass(frozen=True)
class PrearmAttempt:
    attempt_id: str
    backup: Path
    failed: Path


def _attempt_stage(attempt_id: str, state: str) -> str:
    if PREARM_ATTEMPT_ID_RE.fullmatch(attempt_id) is None or state not in {"intent", "ufw-pending", "aborted", "armed"}:
        _fail("Emergency prearm attempt identity is invalid")
    return f"prearm-attempt-{attempt_id}-{state}"


def _attempt_paths(paths: ActivationPaths, campaign: VerifiedCampaign, attempt_id: str) -> PrearmAttempt:
    _attempt_stage(attempt_id, "intent")
    return PrearmAttempt(
        attempt_id=attempt_id,
        backup=paths.nginx_backup_root / f"default.before-{campaign.campaign_id}.{attempt_id}",
        failed=paths.nginx_backup_root / f"emergency-site.failed-{campaign.campaign_id}.{attempt_id}",
    )


def _journal_lifecycle(value: Any, *, label: str) -> NginxLifecycle:
    if not isinstance(value, dict) or set(value) != {"enabled", "active"}:
        _fail(f"{label} is malformed")
    if type(value["enabled"]) is not bool or type(value["active"]) is not bool:
        _fail(f"{label} is malformed")
    return NginxLifecycle(enabled=value["enabled"], active=value["active"])


def _prearm_action(lifecycle: NginxLifecycle) -> str:
    if lifecycle.active:
        return "reloaded"
    if lifecycle.enabled:
        return "started"
    return "enabled-and-started"


def _ensure_create_or_identical(path: Path, payload: bytes, *, label: str) -> None:
    if not _path_exists_or_symlink(path):
        _write_create_only(path, payload)
        return
    existing = _read_root_regular(path, label=label, maximum_bytes=MAX_JSON_BYTES)
    if not hmac.compare_digest(existing, payload):
        _fail(f"{label} already exists with different content")


def _prearm_attempt_intent_payload(
    *,
    paths: ActivationPaths,
    campaign: VerifiedCampaign,
    profile: str,
    attempt: PrearmAttempt,
    lifecycle: NginxLifecycle,
    ufw_baseline: Mapping[str, Any],
) -> dict[str, Any]:
    nginx_sha256, _ = _hash_root_regular(
        paths.nginx_available,
        label="Emergency rendered Nginx configuration",
        maximum_bytes=MAX_JSON_BYTES,
    )
    baseline = _validate_ufw_baseline(dict(ufw_baseline))
    return {
        "schema": PREARM_ATTEMPT_INTENT_SCHEMA,
        "profile": profile,
        "attempt_id": attempt.attempt_id,
        "nginx_available_path": str(paths.nginx_available),
        "nginx_available_sha256": nginx_sha256,
        "nginx_enabled_path": str(paths.nginx_enabled),
        "nginx_enabled_target": str(paths.nginx_available),
        "nginx_default_path": str(paths.nginx_default),
        "nginx_default_backup_path": str(attempt.backup),
        "nginx_default_target": _root_owned_symlink_target(
            paths.nginx_default, label="existing Nginx default site"
        ),
        "nginx_failed_path": str(attempt.failed),
        "initial_lifecycle": dataclasses.asdict(lifecycle),
        "expected_lifecycle": {"enabled": True, "active": True},
        "ufw_baseline": baseline,
        "ufw_baseline_sha256": _ufw_baseline_digest(baseline),
    }


def _prearm_attempt_intent_sha256(intent: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(intent)).hexdigest()


def _read_prearm_attempt_intent(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, profile: str, attempt: PrearmAttempt
) -> dict[str, Any]:
    payload = _read_receipt(paths, campaign, stage=_attempt_stage(attempt.attempt_id, "intent"))
    required = {
        "schema", "profile", "attempt_id", "nginx_available_path", "nginx_available_sha256",
        "nginx_enabled_path", "nginx_enabled_target", "nginx_default_path", "nginx_default_backup_path",
        "nginx_default_target", "nginx_failed_path", "initial_lifecycle", "expected_lifecycle",
        "ufw_baseline", "ufw_baseline_sha256",
    }
    expected_paths = {
        "nginx_available_path": str(paths.nginx_available),
        "nginx_enabled_path": str(paths.nginx_enabled),
        "nginx_enabled_target": str(paths.nginx_available),
        "nginx_default_path": str(paths.nginx_default),
        "nginx_default_backup_path": str(attempt.backup),
        "nginx_failed_path": str(attempt.failed),
    }
    if (
        set(payload) != required
        or payload.get("schema") != PREARM_ATTEMPT_INTENT_SCHEMA
        or payload.get("profile") != profile
        or payload.get("attempt_id") != attempt.attempt_id
        or any(payload.get(key) != value for key, value in expected_paths.items())
    ):
        _fail("Emergency prearm attempt intent is malformed")
    if not isinstance(payload.get("nginx_default_target"), str) or not payload["nginx_default_target"] or "\x00" in payload["nginx_default_target"]:
        _fail("Emergency prearm attempt intent has an invalid default-site target")
    if not isinstance(payload.get("nginx_available_sha256"), str) or SHA256_RE.fullmatch(payload["nginx_available_sha256"]) is None:
        _fail("Emergency prearm attempt intent has an invalid Nginx digest")
    _journal_lifecycle(payload.get("initial_lifecycle"), label="Emergency prearm attempt initial lifecycle")
    if _journal_lifecycle(payload.get("expected_lifecycle"), label="Emergency prearm attempt final lifecycle") != NginxLifecycle(True, True):
        _fail("Emergency prearm attempt intent has an invalid final lifecycle")
    baseline = _validate_ufw_baseline(payload.get("ufw_baseline"))
    if payload.get("ufw_baseline_sha256") != _ufw_baseline_digest(baseline):
        _fail("Emergency prearm attempt intent has an invalid UFW baseline digest")
    return dict(payload)


def _prearm_pending_payload(*, attempt: PrearmAttempt, intent: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PREARM_ATTEMPT_PENDING_SCHEMA,
        "attempt_id": attempt.attempt_id,
        "intent_sha256": _prearm_attempt_intent_sha256(intent),
        "ufw_baseline_sha256": intent["ufw_baseline_sha256"],
    }


def _read_prearm_pending(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, attempt: PrearmAttempt, intent: Mapping[str, Any]
) -> None:
    payload = _read_receipt(paths, campaign, stage=_attempt_stage(attempt.attempt_id, "ufw-pending"))
    if payload != _prearm_pending_payload(attempt=attempt, intent=intent):
        _fail("Emergency prearm UFW-pending journal is malformed")


def _prearm_aborted_payload(*, attempt: PrearmAttempt, intent: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PREARM_ATTEMPT_ABORTED_SCHEMA,
        "attempt_id": attempt.attempt_id,
        "intent_sha256": _prearm_attempt_intent_sha256(intent),
        "rollback": "proven-before-ufw-pending",
    }


def _write_prearm_aborted(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, attempt: PrearmAttempt, intent: Mapping[str, Any]
) -> None:
    stage = _attempt_stage(attempt.attempt_id, "aborted")
    payload = _prearm_aborted_payload(attempt=attempt, intent=intent)
    receipt_path = _receipt_path(paths, campaign.campaign_id, stage)
    if _path_exists_or_symlink(receipt_path):
        if _read_receipt(paths, campaign, stage=stage) != payload:
            _fail("Emergency prearm aborted journal disagrees with the proven rollback")
        return
    _write_receipt(paths, campaign, stage=stage, payload=payload)


def _prearmed_payload(
    *, profile: str, attempt: PrearmAttempt, intent: Mapping[str, Any], final_lifecycle: NginxLifecycle,
    final_ufw: UfwRuleState,
) -> dict[str, Any]:
    initial_lifecycle = _journal_lifecycle(intent["initial_lifecycle"], label="Emergency prearm attempt initial lifecycle")
    if final_lifecycle != NginxLifecycle(True, True) or not final_ufw.rule_present or not final_ufw.ipv6_rule_present:
        _fail("Emergency final ingress state cannot be recorded")
    _require_ufw_baseline(final_ufw, intent["ufw_baseline"])
    return {
        "profile": profile,
        "attempt_id": attempt.attempt_id,
        "nginx": "prearmed",
        "nginx_lifecycle": {
            "before": dataclasses.asdict(initial_lifecycle),
            "after": dataclasses.asdict(final_lifecycle),
            "action": _prearm_action(initial_lifecycle),
        },
        "ufw": {
            "command": UFW_RULE_COMMAND_TEXT,
            "action": "added",
            "ipv6_rule_present_final": True,
            "baseline_sha256": intent["ufw_baseline_sha256"],
        },
        "transaction": {
            "intent_stage": _attempt_stage(attempt.attempt_id, "intent"),
            "pending_stage": _attempt_stage(attempt.attempt_id, "ufw-pending"),
            "armed_stage": _attempt_stage(attempt.attempt_id, "armed"),
        },
    }


def _prearm_armed_payload(*, attempt: PrearmAttempt, intent: Mapping[str, Any], prearmed_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PREARM_ATTEMPT_ARMED_SCHEMA,
        "attempt_id": attempt.attempt_id,
        "intent_sha256": _prearm_attempt_intent_sha256(intent),
        "prearmed_payload": dict(prearmed_payload),
    }


def _read_prearm_armed(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, attempt: PrearmAttempt, intent: Mapping[str, Any]
) -> dict[str, Any]:
    payload = _read_receipt(paths, campaign, stage=_attempt_stage(attempt.attempt_id, "armed"))
    expected = _prearm_armed_payload(attempt=attempt, intent=intent, prearmed_payload=payload.get("prearmed_payload", {}))
    if payload != expected or not isinstance(payload.get("prearmed_payload"), dict):
        _fail("Emergency armed ingress journal is malformed")
    return dict(payload["prearmed_payload"])


def _verify_attempt_configuration(paths: ActivationPaths, intent: Mapping[str, Any]) -> None:
    _secure_directory(paths.nginx_available.parent, create=False)
    digest, _ = _hash_root_regular(
        paths.nginx_available, label="Emergency rendered Nginx configuration", maximum_bytes=MAX_JSON_BYTES
    )
    if digest != intent["nginx_available_sha256"]:
        _fail("Emergency prearm attempt has a different Nginx configuration")


def _assert_attempt_original_state(
    *, paths: ActivationPaths, attempt: PrearmAttempt, intent: Mapping[str, Any], runner: Callable[..., Any]
) -> None:
    _verify_attempt_configuration(paths, intent)
    if _root_owned_symlink_target(paths.nginx_default, label="Emergency original Nginx default site") != intent["nginx_default_target"]:
        _fail("Emergency prearm attempt did not restore the original default site")
    _require_absent(paths.nginx_enabled, label="Emergency original enabled Nginx path")
    _require_absent(attempt.backup, label="Emergency original Nginx backup path")
    if _path_exists_or_symlink(attempt.failed) and _root_owned_symlink_target(
        attempt.failed, label="Emergency failed Nginx recovery path"
    ) != intent["nginx_enabled_target"]:
        _fail("Emergency failed Nginx recovery path is not bound to the candidate")
    if _capture_nginx_lifecycle(runner=runner) != _journal_lifecycle(intent["initial_lifecycle"], label="Emergency prearm attempt initial lifecycle"):
        _fail("Emergency prearm attempt did not restore the original Nginx lifecycle")
    _require_ufw_baseline(
        _capture_ufw_rule_state(runner=runner, expect_emergency_rule=False), intent["ufw_baseline"]
    )


def _assert_attempt_candidate_state(
    *, paths: ActivationPaths, attempt: PrearmAttempt, intent: Mapping[str, Any], runner: Callable[..., Any],
    expect_emergency_rule: bool = False,
) -> None:
    _verify_attempt_configuration(paths, intent)
    if _root_owned_symlink_target(paths.nginx_enabled, label="Emergency enabled Nginx site") != intent["nginx_enabled_target"]:
        _fail("Emergency prearm attempt has a different enabled Nginx site")
    if _root_owned_symlink_target(attempt.backup, label="Emergency Nginx default-site backup") != intent["nginx_default_target"]:
        _fail("Emergency prearm attempt has a different default-site backup")
    _require_absent(paths.nginx_default, label="Emergency candidate Nginx default-site path")
    _require_absent(attempt.failed, label="Emergency failed Nginx recovery path")
    if _capture_nginx_lifecycle(runner=runner) != NginxLifecycle(True, True):
        _fail("Emergency prearm attempt does not have Nginx enabled and active")
    _require_ufw_baseline(
        _capture_ufw_rule_state(runner=runner, expect_emergency_rule=expect_emergency_rule), intent["ufw_baseline"]
    )


def _verify_prearm_final_state(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, attempt: PrearmAttempt, intent: Mapping[str, Any],
    profile: str, runner: Callable[..., Any], tls_probe: Callable[[], None], staging_listener: Callable[[int], None],
) -> tuple[NginxLifecycle, UfwRuleState]:
    """Verify an armed transaction without mutating Nginx, UFW, or Docker."""

    current_intent = _read_prearm_attempt_intent(
        paths=paths, campaign=campaign, profile=profile, attempt=attempt
    )
    if current_intent != dict(intent):
        _fail("Emergency prearm attempt journal changed during recovery")
    _read_prearm_pending(paths=paths, campaign=campaign, attempt=attempt, intent=intent)
    _assert_attempt_candidate_state(
        paths=paths, attempt=attempt, intent=intent, runner=runner, expect_emergency_rule=True
    )
    _assert_nginx_public_listener_inventory(
        paths=paths, allowed_config=paths.nginx_enabled, required_ports=frozenset({80, 443}), runner=runner
    )
    tls_probe()
    staging_listener(8213)
    staging_listener(8443)
    ufw_state = _capture_ufw_rule_state(runner=runner, expect_emergency_rule=True)
    _require_ufw_baseline(ufw_state, intent["ufw_baseline"])
    return NginxLifecycle(True, True), ufw_state


def _new_attempt(paths: ActivationPaths, campaign: VerifiedCampaign) -> PrearmAttempt:
    for _ in range(16):
        attempt = _attempt_paths(paths, campaign, secrets.token_hex(16))
        if not _path_exists_or_symlink(_receipt_path(paths, campaign.campaign_id, _attempt_stage(attempt.attempt_id, "intent"))):
            return attempt
    _fail("could not allocate an immutable Emergency prearm attempt")


def _list_open_prearm_attempts(paths: ActivationPaths, campaign: VerifiedCampaign) -> list[PrearmAttempt]:
    root = _activation_campaign_root(paths, campaign.campaign_id)
    if not _path_exists_or_symlink(root):
        return []
    _secure_directory(root, create=False)
    states: dict[str, set[str]] = {}
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise EmergencyActivationError("Emergency prearm journals cannot be listed") from exc
    for item in entries:
        matched = PREARM_ATTEMPT_STAGE_RE.fullmatch(item.stem)
        if matched is None:
            continue
        _read_receipt(paths, campaign, stage=item.stem)
        states.setdefault(matched.group(1), set()).add(matched.group(2))
    open_attempts: list[PrearmAttempt] = []
    for attempt_id, attempt_states in states.items():
        if (
            "intent" not in attempt_states
            or ("aborted" in attempt_states and len(attempt_states) != 2)
            or ("armed" in attempt_states and "ufw-pending" not in attempt_states)
            or ("aborted" in attempt_states and "ufw-pending" in attempt_states)
        ):
            _fail("Emergency prearm attempt journal sequence is invalid")
        if "aborted" not in attempt_states:
            open_attempts.append(_attempt_paths(paths, campaign, attempt_id))
    if len(open_attempts) > 1:
        _fail("multiple unresolved Emergency prearm attempts require manual review")
    return open_attempts


def _mark_rollback_proven(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, profile: str, attempt: PrearmAttempt,
    intent: Mapping[str, Any], runner: Callable[..., Any],
) -> None:
    _assert_attempt_original_state(paths=paths, attempt=attempt, intent=intent, runner=runner)
    _require_firewall_attestation(paths=paths, campaign=campaign, profile=profile, runner=runner)
    _write_prearm_aborted(paths=paths, campaign=campaign, attempt=attempt, intent=intent)


def _rollback_pre_pending_attempt(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, profile: str, attempt: PrearmAttempt,
    intent: Mapping[str, Any], lifecycle: NginxLifecycle, changes: NginxLifecycleChanges,
    runner: Callable[..., Any],
) -> None:
    _restore_default_nginx_after_failed_prearm(
        paths=paths, backup=attempt.backup, failed=attempt.failed, lifecycle=lifecycle, changes=changes, runner=runner
    )
    _mark_rollback_proven(
        paths=paths, campaign=campaign, profile=profile, attempt=attempt, intent=intent, runner=runner
    )


def _recover_pre_pending_attempt(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, profile: str, attempt: PrearmAttempt,
    runner: Callable[..., Any],
) -> None:
    intent = _read_prearm_attempt_intent(paths=paths, campaign=campaign, profile=profile, attempt=attempt)
    try:
        _assert_attempt_original_state(paths=paths, attempt=attempt, intent=intent, runner=runner)
    except EmergencyActivationError:
        original = False
    else:
        original = True
    if original:
        _require_firewall_attestation(paths=paths, campaign=campaign, profile=profile, runner=runner)
        _write_prearm_aborted(paths=paths, campaign=campaign, attempt=attempt, intent=intent)
        return
    try:
        _assert_attempt_candidate_state(paths=paths, attempt=attempt, intent=intent, runner=runner)
    except EmergencyActivationError as exc:
        raise EmergencyActivationError(
            "Emergency prearm attempt is neither rollback-proven nor an exact unarmed candidate; manual recovery is required"
        ) from exc
    initial_lifecycle = _journal_lifecycle(
        intent["initial_lifecycle"], label="Emergency prearm attempt initial lifecycle"
    )
    _rollback_pre_pending_attempt(
        paths=paths, campaign=campaign, profile=profile, attempt=attempt, intent=intent,
        lifecycle=initial_lifecycle,
        changes=NginxLifecycleChanges(
            enable_attempted=not initial_lifecycle.enabled,
            start_attempted=not initial_lifecycle.active,
        ),
        runner=runner,
    )


def _recover_pending_prearm(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, profile: str, attempt: PrearmAttempt,
    runner: Callable[..., Any], tls_probe: Callable[[], None], staging_listener: Callable[[int], None],
) -> dict[str, Any]:
    intent = _read_prearm_attempt_intent(paths=paths, campaign=campaign, profile=profile, attempt=attempt)
    final_lifecycle, final_ufw = _verify_prearm_final_state(
        paths=paths, campaign=campaign, attempt=attempt, intent=intent, profile=profile, runner=runner,
        tls_probe=tls_probe, staging_listener=staging_listener,
    )
    expected = _prearmed_payload(
        profile=profile, attempt=attempt, intent=intent, final_lifecycle=final_lifecycle, final_ufw=final_ufw
    )
    armed_stage = _attempt_stage(attempt.attempt_id, "armed")
    if _path_exists_or_symlink(_receipt_path(paths, campaign.campaign_id, armed_stage)):
        if _read_prearm_armed(paths=paths, campaign=campaign, attempt=attempt, intent=intent) != expected:
            _fail("Emergency armed ingress journal disagrees with the verified final state")
    else:
        _write_receipt(
            paths, campaign, stage=armed_stage, payload=_prearm_armed_payload(
                attempt=attempt, intent=intent, prearmed_payload=expected
            )
        )
    final_path = _receipt_path(paths, campaign.campaign_id, "prearmed")
    if _path_exists_or_symlink(final_path):
        recorded = _read_receipt(paths, campaign, stage="prearmed")
        if recorded != expected:
            _fail("Emergency prearm receipt disagrees with the verified final state")
        return recorded
    _write_receipt(paths, campaign, stage="prearmed", payload=expected)
    return expected


def _prearm_nginx(
    *, paths: ActivationPaths, campaign: VerifiedCampaign, package_root: Path, profile: str,
    runner: Callable[..., Any] = subprocess.run, tls_probe: Callable[[], None] | None = None,
    staging_listener: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """Start one fresh attempt; only a durable UFW-pending receipt is a PONR."""

    if tls_probe is None:
        tls_probe = _local_tls_probe
    if staging_listener is None:
        staging_listener = _check_staging_listener
    fullchain_path, private_key_path = _require_pinned_tls(paths=paths, campaign=campaign, profile=profile)
    rendered = _render_nginx_configuration(
        package_root=package_root, profile=profile, fullchain_path=fullchain_path, private_key_path=private_key_path
    )
    _ensure_create_or_identical(paths.nginx_available, rendered, label="Emergency rendered Nginx configuration")
    if profile == "sms-otp":
        _ensure_create_or_identical(
            paths.nginx_sms_rate_limit,
            _read_root_regular(
                package_root / "deploy/emergency-ir/nginx.sms-otp.rate-limit.conf",
                label="Emergency SMS Nginx rate limit", maximum_bytes=MAX_JSON_BYTES,
            ),
            label="Emergency SMS Nginx rate-limit configuration",
        )
    _secure_directory(paths.nginx_backup_root, create=True)
    _require_absent(paths.nginx_enabled, label="Emergency enabled Nginx path")
    _assert_nginx_public_listener_inventory(
        paths=paths, allowed_config=paths.nginx_default, required_ports=frozenset({80}), runner=runner
    )
    lifecycle = _capture_nginx_lifecycle(runner=runner)
    attestation = _require_firewall_attestation(paths=paths, campaign=campaign, profile=profile, runner=runner)
    attempt = _new_attempt(paths, campaign)
    _require_absent(attempt.backup, label="Emergency Nginx backup path")
    _require_absent(attempt.failed, label="Emergency Nginx failed path")
    intent = _prearm_attempt_intent_payload(
        paths=paths, campaign=campaign, profile=profile, attempt=attempt, lifecycle=lifecycle,
        ufw_baseline=_validate_ufw_baseline(attestation["ufw_baseline"]),
    )
    _write_receipt(paths, campaign, stage=_attempt_stage(attempt.attempt_id, "intent"), payload=intent)
    changes = NginxLifecycleChanges()
    default_moved = False
    try:
        os.rename(paths.nginx_default, attempt.backup)
        default_moved = True
        os.symlink(str(paths.nginx_available), paths.nginx_enabled)
    except OSError as exc:
        if default_moved:
            try:
                os.rename(attempt.backup, paths.nginx_default)
            except OSError as restore_exc:
                raise EmergencyActivationError(
                    "Emergency prearm could not create the candidate and the default site could not be restored"
                ) from restore_exc
        _mark_rollback_proven(
            paths=paths, campaign=campaign, profile=profile, attempt=attempt, intent=intent, runner=runner
        )
        raise EmergencyActivationError("Emergency prearm cannot create the recoverable Nginx candidate") from exc
    try:
        tested = runner([NGINX_BINARY, "-t"], check=False, capture_output=True, timeout=60)
        if getattr(tested, "returncode", 1) != 0:
            _fail("Emergency Nginx candidate configuration test failed")
        action = _activate_candidate_nginx(lifecycle=lifecycle, changes=changes, runner=runner)
        if _capture_nginx_lifecycle(runner=runner) != NginxLifecycle(True, True) or action != _prearm_action(lifecycle):
            _fail("Emergency Nginx did not reach the recorded candidate lifecycle")
        _assert_nginx_public_listener_inventory(
            paths=paths, allowed_config=paths.nginx_enabled, required_ports=frozenset({80, 443}), runner=runner
        )
        tls_probe()
        staging_listener(8213)
        staging_listener(8443)
        latest_attestation = _require_firewall_attestation(
            paths=paths, campaign=campaign, profile=profile, runner=runner
        )
        if _validate_ufw_baseline(latest_attestation["ufw_baseline"]) != intent["ufw_baseline"]:
            _fail("Emergency UFW baseline drifted before the pending journal")
    except (EmergencyActivationError, OSError, subprocess.SubprocessError) as exc:
        _rollback_pre_pending_attempt(
            paths=paths, campaign=campaign, profile=profile, attempt=attempt, intent=intent,
            lifecycle=lifecycle, changes=changes, runner=runner,
        )
        message = str(exc) if isinstance(exc, EmergencyActivationError) else "Emergency prearm command could not run"
        raise EmergencyActivationError(f"{message}; the previous default site was restored and the attempt was aborted") from exc
    pending_stage = _attempt_stage(attempt.attempt_id, "ufw-pending")
    try:
        _write_receipt(
            paths, campaign, stage=pending_stage, payload=_prearm_pending_payload(attempt=attempt, intent=intent)
        )
    except EmergencyActivationError as exc:
        if _path_exists_or_symlink(_receipt_path(paths, campaign.campaign_id, pending_stage)):
            raise EmergencyActivationError(
                "Emergency UFW-pending journal may exist but is not durable; candidate is preserved for fail-closed manual recovery"
            ) from exc
        _rollback_pre_pending_attempt(
            paths=paths, campaign=campaign, profile=profile, attempt=attempt, intent=intent,
            lifecycle=lifecycle, changes=changes, runner=runner,
        )
        raise EmergencyActivationError(
            "Emergency UFW-pending journal could not be written; the attempt was rolled back and aborted"
        ) from exc
    # From this exact durable receipt onward a UFW invocation may have a side
    # effect.  No subsequent path restores Nginx or changes/deletes UFW.
    try:
        allowed = runner(
            [UFW_BINARY, "allow", "proto", "tcp", "from", "any", "to", "any", "port", "80,443", "comment", UFW_RULE_COMMENT],
            check=False, capture_output=True, timeout=60,
        )
        if getattr(allowed, "returncode", 1) != 0:
            _fail("bounded Emergency UFW rule outcome could not be confirmed")
        final_lifecycle, final_ufw = _verify_prearm_final_state(
            paths=paths, campaign=campaign, attempt=attempt, intent=intent, profile=profile, runner=runner,
            tls_probe=tls_probe, staging_listener=staging_listener,
        )
    except (EmergencyActivationError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, EmergencyActivationError) else "Emergency UFW command outcome could not be confirmed"
        raise EmergencyActivationError(
            f"{message}; Emergency ingress remains UFW-pending for verification-only recovery"
        ) from exc
    prearmed = _prearmed_payload(
        profile=profile, attempt=attempt, intent=intent, final_lifecycle=final_lifecycle, final_ufw=final_ufw
    )
    try:
        _write_receipt(
            paths, campaign, stage=_attempt_stage(attempt.attempt_id, "armed"),
            payload=_prearm_armed_payload(attempt=attempt, intent=intent, prearmed_payload=prearmed),
        )
        _write_receipt(paths, campaign, stage="prearmed", payload=prearmed)
    except EmergencyActivationError as exc:
        raise EmergencyActivationError(
            "Emergency ingress is armed but its final receipt could not be registered; rerun the same confirmed prearm stage for verification-only recovery"
        ) from exc
    return prearmed


def prearm(
    *, campaign: VerifiedCampaign, paths: ActivationPaths, profile: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if _path_exists_or_symlink(_receipt_path(paths, campaign.campaign_id, "prearmed")):
        _fail("Emergency prearm receipt already exists; refusing to overwrite prior ingress state")
    prepared = _require_prepare(paths, campaign, profile=profile)
    _read_receipt(paths, campaign, stage="api-ready")
    if profile == "sms-otp":
        _require_sms_preflight(paths, campaign)
    open_attempts = _list_open_prearm_attempts(paths, campaign)
    if open_attempts:
        attempt = open_attempts[0]
        pending_path = _receipt_path(paths, campaign.campaign_id, _attempt_stage(attempt.attempt_id, "ufw-pending"))
        if _path_exists_or_symlink(pending_path):
            return _recover_pending_prearm(
                paths=paths, campaign=campaign, profile=profile, attempt=attempt, runner=runner,
                tls_probe=_local_tls_probe, staging_listener=_check_staging_listener,
            )
        _recover_pre_pending_attempt(
            paths=paths, campaign=campaign, profile=profile, attempt=attempt, runner=runner
        )
    _check_staging_listener(8213)
    _check_staging_listener(8443)
    return _prearm_nginx(
        paths=paths, campaign=campaign, package_root=Path(str(prepared["package_root"])), profile=profile, runner=runner
    )


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
        "tls": pin_tls,
        "firewall": firewall_attest,
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
    parser.add_argument("--stage", choices=("prepare", "images", "database", "api", "tls", "firewall", "prearm"))
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
