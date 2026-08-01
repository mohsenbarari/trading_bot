#!/usr/bin/env python3
"""Publish one sealed Emergency WA-IR artifact campaign to Arvan Object Storage.

This is deliberately the publisher counterpart to the bounded receiver
bootstrap.  It is not a deployment tool: it never contacts WA-IR, decrypts
an artifact, restores a database, loads an image, starts a container, changes
Nginx, or changes a firewall.

The input plan names exactly four *already age-encrypted* local files.  Their
plaintext and ciphertext hashes/sizes are checked locally, the ciphertexts
are uploaded to a private, versioned Arvan bucket and read back by their
immutable VersionIds, and the existing Emergency manifest helper signs the
result.  A small pinned-key receiver bundle, sealed manifest and transient
presigned URL map are then published.  The only URL-bearing output is a
root-only descriptor consumed by ``run_emergency_ir_object_storage_receive``.

No network operation is reachable without both ``--apply`` and the exact
``--confirm`` phrase printed by the prior dry run.  Standard output never
contains presigned URLs or credentials.

Operational invocation is deliberately limited to the direct isolated form
``python3 -I -B /absolute/path/publish_emergency_ir_object_storage.py ...``.
The CLI rejects a non-isolated interpreter before parsing a campaign.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import types

REPO_ROOT = Path(__file__).resolve().parents[1]
# The fetched receiver bundle executes on WA-IR as root.  It must therefore be
# rendered only from these exact tracked source files in this publisher's own
# repository; a caller never gets to choose another checkout.
PUBLISHER_SOURCE_PATHS = (
    "scripts/publish_emergency_ir_object_storage.py",
    "scripts/build_emergency_ir_receiver_bundle.py",
    "scripts/emergency_ir_object_storage_manifest.py",
    "scripts/emergency_ir_object_storage_receiver.py",
    "scripts/run_emergency_ir_object_storage_receive.py",
    "deploy/emergency-ir/run_object_storage_receiver.py",
    "scripts/emergency_ir_standalone_activate.py",
)
GIT_REVISION_RE = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$", re.ASCII)
MAX_BOOTSTRAP_SOURCE_BYTES = 4 * 1024 * 1024
WA_IR_AGE_RECIPIENT = "age1hxt7paq6kp3cr4ey6tp0ne2dpvmz7az9h7jh09vfr9gpsm30fa7qa8zmkt"
WA_IR_AGE_RECIPIENT_KEY_ID = "age-recipient-sha256:8ab221e2abb62642e85960a38ba07f2de379d1744c222a44efcc922cf435418d"


class EmergencyPublisherError(RuntimeError):
    """A publish precondition or immutable transfer verification failed."""


def _fail(message: str) -> None:
    raise EmergencyPublisherError(message)


def _publisher_git_environment() -> dict[str, str]:
    """Return a deterministic environment for the pre-import Git boundary."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": os.defpath,
        }
    )
    return environment


def _run_publisher_git(*arguments: str) -> str:
    """Run a local Git primitive with ambient Git state scrubbed."""

    try:
        completed = subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.preloadIndex=false",
                "-c",
                f"core.worktree={REPO_ROOT}",
                "-C",
                str(REPO_ROOT),
                *arguments,
            ],
            text=True,
            capture_output=True,
            check=False,
            env=_publisher_git_environment(),
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyPublisherError("publisher source provenance cannot be inspected") from exc
    if completed.returncode != 0:
        _fail("publisher source provenance cannot be inspected")
    return completed.stdout


def _publisher_head_blob(*, revision: str, relative: str) -> bytes:
    """Read one bounded bootstrap source blob from the captured revision."""

    try:
        completed = subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.preloadIndex=false",
                "-c",
                f"core.worktree={REPO_ROOT}",
                "-C",
                str(REPO_ROOT),
                "show",
                f"{revision}:{relative}",
            ],
            capture_output=True,
            check=False,
            env=_publisher_git_environment(),
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyPublisherError("publisher source provenance cannot be inspected") from exc
    if completed.returncode != 0 or not 1 <= len(completed.stdout) <= MAX_BOOTSTRAP_SOURCE_BYTES:
        _fail("publisher bootstrap source blob is unavailable at its fixed revision")
    return bytes(completed.stdout)


def _source_file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
    )


def _read_publisher_worktree_blob(relative: str) -> bytes:
    """Read only a stable, owner-controlled executable bootstrap source file."""

    path = REPO_ROOT / relative
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyPublisherError("publisher bootstrap source cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or not 1 <= before.st_size <= MAX_BOOTSTRAP_SOURCE_BYTES
    ):
        _fail("publisher bootstrap source is not one bounded owner-controlled regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if _source_file_identity(opened) != _source_file_identity(before):
            _fail("publisher bootstrap source changed while being opened")
        payload = bytearray()
        while len(payload) <= MAX_BOOTSTRAP_SOURCE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_BOOTSTRAP_SOURCE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) != opened.st_size
            or len(payload) > MAX_BOOTSTRAP_SOURCE_BYTES
            or _source_file_identity(after) != _source_file_identity(opened)
        ):
            _fail("publisher bootstrap source changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise EmergencyPublisherError("publisher bootstrap source cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _assert_preimport_scripts_surface(*, repo_root: Path = REPO_ROOT) -> Path:
    """Return one safe implicit namespace before importing any ``scripts`` code.

    ``scripts`` is intentionally an implicit namespace package in this
    bootstrap.  An untracked ``scripts/__init__.py`` would otherwise execute
    merely by evaluating a later ``from scripts import ...`` statement, before
    Git can establish the clean checkout identity.
    """

    scripts_root = repo_root / "scripts"
    try:
        directory = scripts_root.lstat()
    except OSError as exc:
        raise EmergencyPublisherError("publisher scripts directory cannot be inspected") from exc
    if (
        stat.S_ISLNK(directory.st_mode)
        or not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.geteuid()
        or stat.S_IMODE(directory.st_mode) & 0o022
    ):
        _fail("publisher scripts directory is not a safe import surface")
    initializer = scripts_root / "__init__.py"
    try:
        initializer.lstat()
    except FileNotFoundError:
        return scripts_root
    except OSError as exc:
        raise EmergencyPublisherError("publisher scripts package initializer cannot be inspected") from exc
    _fail("publisher source contains an unsupported scripts package initializer")


def _install_pinned_scripts_namespace(*, repo_root: Path = REPO_ROOT) -> None:
    """Prevent a later regular ``scripts`` package from shadowing this checkout.

    An implicit namespace directory in ``repo_root`` by itself is not enough:
    Python can keep searching ``sys.path`` and select a regular package from a
    system site directory.  Install one synthetic namespace only after the
    local directory and initializer boundary have been verified.
    """

    scripts_root = _assert_preimport_scripts_surface(repo_root=repo_root)
    expected = str(scripts_root)
    present = sys.modules.get("scripts")
    if present is not None:
        paths = getattr(present, "__path__", None)
        if (
            getattr(present, "__file__", None) is not None
            or paths is None
            or [str(item) for item in paths] != [expected]
        ):
            _fail("publisher scripts namespace was preloaded from an ambient path")
        return
    namespace = types.ModuleType("scripts")
    namespace.__package__ = "scripts"
    namespace.__path__ = [expected]  # type: ignore[attr-defined]
    sys.modules["scripts"] = namespace


def _fixed_publisher_source_revision() -> str:
    """Return the revision only if executable bootstrap bytes equal it exactly."""

    observed_root = Path(_run_publisher_git("rev-parse", "--show-toplevel").strip())
    try:
        if observed_root.resolve() != REPO_ROOT:
            _fail("publisher Git worktree differs from the executing checkout")
    except OSError as exc:
        raise EmergencyPublisherError("publisher Git worktree cannot be resolved") from exc
    tracked = tuple(line for line in _run_publisher_git(
        "ls-files", "--error-unmatch", "--", *PUBLISHER_SOURCE_PATHS
    ).splitlines() if line)
    if len(tracked) != len(PUBLISHER_SOURCE_PATHS) or set(tracked) != set(PUBLISHER_SOURCE_PATHS):
        _fail("publisher bootstrap source paths are not exactly tracked")
    changed = _run_publisher_git("status", "--porcelain=v1", "--untracked-files=all", "--")
    if changed:
        _fail("publisher checkout is not clean at its fixed revision")
    revision = _run_publisher_git("rev-parse", "--verify", "HEAD^{commit}").strip()
    if GIT_REVISION_RE.fullmatch(revision) is None:
        _fail("publisher source revision is unsafe")
    # ``git status`` can be made to ignore a changed ``skip-worktree`` file.
    # Compare every actual executable source byte with the captured immutable
    # Git object instead; the source revision in signed provenance then cannot
    # describe a different bundle input.
    for relative in PUBLISHER_SOURCE_PATHS:
        expected = _publisher_head_blob(revision=revision, relative=relative)
        actual = _read_publisher_worktree_blob(relative)
        if not hmac.compare_digest(actual, expected):
            _fail("publisher bootstrap source differs from its fixed revision")
    return revision


def _require_isolated_cli() -> None:
    """Require the direct, isolated interpreter contract for real CLI work."""

    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        _fail("Emergency publisher CLI must be invoked as: python3 -I -B <absolute-script-path> ...")


# This guard must run before every import from ``scripts``.  The actual
# revision is deliberately derived only after both its filesystem and Git
# boundaries have been validated.
_install_pinned_scripts_namespace()
if __name__ == "__main__":
    try:
        _require_isolated_cli()
    except EmergencyPublisherError as exc:
        sys.stderr.write(f"blocked: {exc}\n")
        raise SystemExit(2) from exc
_fixed_publisher_source_revision()


import argparse
import dataclasses
import hashlib
import json
from typing import Any, BinaryIO, Callable, Mapping
from urllib.parse import parse_qs, quote, urlsplit

# The pinned namespace above is the only admissible ``scripts`` source.  Do
# not leave implicit-namespace resolution to arbitrary system-site paths.

from scripts import build_emergency_ir_receiver_bundle as receiver_bundle
from scripts import emergency_ir_object_storage_manifest as manifest
from scripts import emergency_ir_object_storage_receiver as receiver
from scripts import run_emergency_ir_object_storage_receive as receiver_bootstrap


PUBLISH_PLAN_SCHEMA = "gold-trade-emergency-ir-object-storage-publish-plan-v1"
PUBLISH_PLAN_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "bucket",
        "prefix",
        "created_at",
        "destination_age_recipient_key_id",
        "artifacts",
    }
)
ARTIFACT_DESCRIPTOR_FIELDS = frozenset(
    {
        "kind",
        "ciphertext_path",
        "plaintext_sha256",
        "plaintext_bytes",
        "ciphertext_sha256",
        "ciphertext_bytes",
    }
)
MAX_PUBLISH_PLAN_BYTES = 128 * 1024
MAX_CREDENTIALS_BYTES = 8 * 1024
MAX_CONTROL_ARTIFACT_BYTES = 4 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
AGE_HEADER = b"age-encryption.org/v1\n"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
CONTROL_FILENAMES = {
    "receiver_bundle": "receiver-bootstrap.tar.gz",
    "manifest": "sealed-manifest.json",
    "url_map": "presigned-urls.json",
}
PRESIGNED_QUERY_FIELDS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
        "versionId",
    }
)
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NoSuchVersion", "NotFound"})
OWNER_ONLY_ACL_PERMISSION = "FULL_CONTROL"
# Object Storage is the only approved payload transport for Emergency, but it
# must still be a direct connection to the fixed private Arvan endpoint.  Do
# not allow a signed request, S3 credentials, or a presigned URL to traverse a
# controller-wide proxy accidentally inherited from the shell environment.
PROXY_ENVIRONMENT_KEYS = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
)
# Botocore may honor ambient CA-bundle overrides when ``verify`` is not pinned
# by the caller.  A campaign must use the platform trust store directly; a
# custom bundle is a separate security decision and is intentionally outside
# this Emergency transfer path.
TLS_CA_OVERRIDE_ENVIRONMENT_KEYS = (
    "AWS_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "aws_ca_bundle",
    "requests_ca_bundle",
    "curl_ca_bundle",
)
@dataclasses.dataclass(frozen=True)
class FileStamp:
    """Stable identity for a locally inspected regular file."""

    dev: int
    ino: int
    mode: int
    uid: int
    gid: int
    nlink: int
    size: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "FileStamp":
        return cls(
            dev=value.st_dev,
            ino=value.st_ino,
            mode=value.st_mode,
            uid=value.st_uid,
            gid=value.st_gid,
            nlink=value.st_nlink,
            size=value.st_size,
        )


@dataclasses.dataclass(frozen=True)
class ArtifactDescriptor:
    kind: str
    ciphertext_path: Path
    plaintext_sha256: str
    plaintext_bytes: int
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclasses.dataclass(frozen=True)
class PublishPlan:
    campaign_id: str
    bucket: str
    prefix: str
    created_at: str
    destination_age_recipient_key_id: str
    artifacts: tuple[ArtifactDescriptor, ...]


@dataclasses.dataclass(frozen=True)
class VerifiedLocalArtifact:
    descriptor: ArtifactDescriptor
    stamp: FileStamp


@dataclasses.dataclass(frozen=True)
class UploadedObject:
    key: str
    version_id: str
    sha256: str
    bytes: int


@dataclasses.dataclass(frozen=True)
class PublishOutputs:
    receiver_bundle: Path
    sealed_manifest: Path
    url_map: Path
    descriptor: Path


@dataclasses.dataclass(frozen=True)
class BootstrapProvenance:
    """The signed, human-confirmed identity of the executable receiver bundle."""

    publisher_source_revision: str
    receiver_bundle_sha256: str
    receiver_bundle_bytes: int
    signer_key_id: str

    def as_manifest(self) -> dict[str, Any]:
        return {
            "schema": manifest.BOOTSTRAP_PROVENANCE_SCHEMA,
            "publisher_source_revision": self.publisher_source_revision,
            "receiver_bundle_sha256": self.receiver_bundle_sha256,
            "receiver_bundle_bytes": self.receiver_bundle_bytes,
            "signer_key_id": self.signer_key_id,
        }

def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("publish input contains a duplicate field")
        result[key] = value
    return result


def _read_owner_regular(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    require_private: bool,
) -> bytes:
    """Read a stable local input without following links.

    Artifact descriptors identify files that may contain encrypted credentials
    or database data, so the publisher accepts only caller-owned regular files
    that are not writable by another account.  Secret-bearing files additionally
    need 0600-like permissions.
    """

    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (require_private and stat.S_IMODE(before.st_mode) & 0o077)
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail(f"{label} must be one bounded owner-controlled regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if FileStamp.from_stat(opened) != FileStamp.from_stat(before):
            _fail(f"{label} changed while being opened")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) != opened.st_size
            or len(payload) > maximum_bytes
            or FileStamp.from_stat(after) != FileStamp.from_stat(opened)
        ):
            _fail(f"{label} changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_json(path: Path, *, label: str, maximum_bytes: int, private: bool) -> dict[str, Any]:
    payload = _read_owner_regular(
        path, label=label, maximum_bytes=maximum_bytes, require_private=private
    )
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except EmergencyPublisherError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyPublisherError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _require_sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(f"{field} must be a lowercase SHA-256")
    return value


def _require_positive(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        _fail(f"{field} must be a positive bounded integer")
    return value


def _parse_descriptor(value: object, *, expected_kind: str) -> ArtifactDescriptor:
    if not isinstance(value, Mapping) or set(value) != ARTIFACT_DESCRIPTOR_FIELDS:
        _fail("artifact descriptor fields are unsupported")
    if value.get("kind") != expected_kind:
        _fail("artifact descriptors must be complete, unique, and in fixed order")
    path_value = value.get("ciphertext_path")
    if not isinstance(path_value, str) or not path_value or "\x00" in path_value:
        _fail("artifact ciphertext_path is invalid")
    ciphertext_path = Path(path_value)
    if not ciphertext_path.is_absolute():
        _fail("artifact ciphertext_path must be absolute")
    plaintext_sha256 = _require_sha(value.get("plaintext_sha256"), field="artifact plaintext_sha256")
    plaintext_bytes = _require_positive(
        value.get("plaintext_bytes"),
        field="artifact plaintext_bytes",
        maximum=manifest.MAX_ARTIFACT_BYTES,
    )
    ciphertext_sha256 = _require_sha(value.get("ciphertext_sha256"), field="artifact ciphertext_sha256")
    ciphertext_bytes = _require_positive(
        value.get("ciphertext_bytes"),
        field="artifact ciphertext_bytes",
        maximum=manifest.MAX_CIPHERTEXT_BYTES,
    )
    if ciphertext_bytes <= plaintext_bytes or ciphertext_sha256 == plaintext_sha256:
        _fail("artifact ciphertext descriptor is not a plausible age ciphertext")
    return ArtifactDescriptor(
        kind=expected_kind,
        ciphertext_path=ciphertext_path,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
    )


def _bootstrap_provenance(*, signing_public_key_path: Path) -> BootstrapProvenance:
    """Preflight the fixed source and deterministic bundle without writing it."""

    source_revision = _fixed_publisher_source_revision()
    signer_key_id = _load_public_key_id(signing_public_key_path)
    try:
        bundle_sha256, bundle_bytes = receiver_bundle.bundle_digest(
            signing_public_key=signing_public_key_path,
            source_revision=source_revision,
        )
    except receiver_bundle.ReceiverBundleError as exc:
        raise EmergencyPublisherError("pinned-key receiver bootstrap bundle cannot be preflighted") from exc
    provenance = BootstrapProvenance(
        publisher_source_revision=source_revision,
        receiver_bundle_sha256=bundle_sha256,
        receiver_bundle_bytes=bundle_bytes,
        signer_key_id=signer_key_id,
    )
    try:
        normalized = manifest.validate_bootstrap_provenance(provenance.as_manifest())
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("receiver bootstrap provenance is invalid") from exc
    return BootstrapProvenance(
        publisher_source_revision=str(normalized["publisher_source_revision"]),
        receiver_bundle_sha256=str(normalized["receiver_bundle_sha256"]),
        receiver_bundle_bytes=int(normalized["receiver_bundle_bytes"]),
        signer_key_id=str(normalized["signer_key_id"]),
    )


def _placeholder_bootstrap_provenance() -> BootstrapProvenance:
    """Supply a structural placeholder while validating a plan before dry-run."""

    return BootstrapProvenance(
        publisher_source_revision="0" * 40,
        receiver_bundle_sha256="0" * 64,
        receiver_bundle_bytes=1,
        signer_key_id="ed25519-sha256:" + "0" * 64,
    )


def _unsigned_manifest(
    plan: PublishPlan,
    *,
    version_ids: Mapping[str, str],
    bootstrap_provenance: BootstrapProvenance,
) -> dict[str, Any]:
    """Build the exact unsigned v2 manifest from a validated publish plan."""

    if set(version_ids) != set(manifest.ARTIFACT_ORDER):
        _fail("immutable VersionIds must cover exactly the fixed artifact set")
    artifacts: list[dict[str, Any]] = []
    for descriptor in plan.artifacts:
        artifacts.append(
            {
                "kind": descriptor.kind,
                "format": manifest.ARTIFACT_CONTRACTS[descriptor.kind]["format"],
                "object_key": manifest.expected_object_key(
                    prefix=plan.prefix, campaign_id=plan.campaign_id, kind=descriptor.kind
                ),
                "version_id": version_ids[descriptor.kind],
                "plaintext_sha256": descriptor.plaintext_sha256,
                "plaintext_bytes": descriptor.plaintext_bytes,
                "ciphertext_sha256": descriptor.ciphertext_sha256,
                "ciphertext_bytes": descriptor.ciphertext_bytes,
                "encryption": {
                    "algorithm": "age-v1",
                    "recipient_key_id": plan.destination_age_recipient_key_id,
                },
                "target_path": manifest.expected_target_path(
                    campaign_id=plan.campaign_id, kind=descriptor.kind
                ),
            }
        )
    return {
        "schema": manifest.MANIFEST_SCHEMA,
        "campaign_id": plan.campaign_id,
        "source_site": manifest.SOURCE_SITE,
        "destination_site": manifest.DESTINATION_SITE,
        "endpoint": manifest.APPROVED_ARVAN_ENDPOINT,
        "region": manifest.APPROVED_ARVAN_REGION,
        "bucket": plan.bucket,
        "prefix": plan.prefix,
        "created_at": plan.created_at,
        "destination_age_recipient_key_id": plan.destination_age_recipient_key_id,
        "bootstrap_provenance": bootstrap_provenance.as_manifest(),
        "artifacts": artifacts,
    }


def load_publish_plan(path: Path) -> PublishPlan:
    """Load a local-only, strict four-artifact Emergency publish plan."""

    raw = _load_json(path, label="Emergency publish plan", maximum_bytes=MAX_PUBLISH_PLAN_BYTES, private=False)
    if set(raw) != PUBLISH_PLAN_FIELDS or raw.get("schema") != PUBLISH_PLAN_SCHEMA:
        _fail("Emergency publish plan fields or schema are unsupported")
    raw_artifacts = raw.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(manifest.ARTIFACT_ORDER):
        _fail("Emergency publish plan must contain exactly four artifacts")
    descriptors = tuple(
        _parse_descriptor(value, expected_kind=kind)
        for value, kind in zip(raw_artifacts, manifest.ARTIFACT_ORDER, strict=True)
    )
    if len({str(item.ciphertext_path) for item in descriptors}) != len(descriptors):
        _fail("each Emergency artifact must use a distinct ciphertext path")
    provisional = PublishPlan(
        campaign_id=str(raw.get("campaign_id")),
        bucket=str(raw.get("bucket")),
        prefix=str(raw.get("prefix")),
        created_at=str(raw.get("created_at")),
        destination_age_recipient_key_id=str(raw.get("destination_age_recipient_key_id")),
        artifacts=descriptors,
    )
    # Reuse the sealed receiver's exact structural contract before any S3
    # client is constructed.  Placeholder VersionIds are valid only locally;
    # real VersionIds are required again before signing.
    try:
        normalized = manifest.validate_unsigned_manifest(
            _unsigned_manifest(
                provisional,
                version_ids={kind: f"planned-version-{index + 1}" for index, kind in enumerate(manifest.ARTIFACT_ORDER)},
                bootstrap_provenance=_placeholder_bootstrap_provenance(),
            )
        )
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("Emergency publish plan does not satisfy the sealed manifest contract") from exc
    if normalized["destination_age_recipient_key_id"] != WA_IR_AGE_RECIPIENT_KEY_ID:
        _fail("Emergency publish plan must use the fixed WA-IR age recipient")
    return PublishPlan(
        campaign_id=str(normalized["campaign_id"]),
        bucket=str(normalized["bucket"]),
        prefix=str(normalized["prefix"]),
        created_at=str(normalized["created_at"]),
        destination_age_recipient_key_id=str(normalized["destination_age_recipient_key_id"]),
        artifacts=descriptors,
    )


def _plan_identity(
    plan: PublishPlan,
    *,
    bootstrap_provenance: BootstrapProvenance,
    ttl_seconds: int,
) -> str:
    """Return a non-secret digest binding the required human confirmation."""

    value = {
        "schema": PUBLISH_PLAN_SCHEMA,
        "campaign_id": plan.campaign_id,
        "bucket": plan.bucket,
        "prefix": plan.prefix,
        "created_at": plan.created_at,
        "destination_age_recipient": WA_IR_AGE_RECIPIENT,
        "destination_age_recipient_key_id": plan.destination_age_recipient_key_id,
        "bootstrap_provenance": bootstrap_provenance.as_manifest(),
        "presigned_ttl_seconds": ttl_seconds,
        "artifacts": [
            {
                "kind": item.kind,
                "plaintext_sha256": item.plaintext_sha256,
                "plaintext_bytes": item.plaintext_bytes,
                "ciphertext_sha256": item.ciphertext_sha256,
                "ciphertext_bytes": item.ciphertext_bytes,
            }
            for item in plan.artifacts
        ],
    }
    return hashlib.sha256(manifest.canonical_json_bytes(value)).hexdigest()


def confirmation_phrase(
    plan: PublishPlan,
    *,
    bootstrap_provenance: BootstrapProvenance,
    ttl_seconds: int,
) -> str:
    return (
        f"publish-emergency-ir:{plan.campaign_id}:"
        f"{_plan_identity(plan, bootstrap_provenance=bootstrap_provenance, ttl_seconds=ttl_seconds)}"
    )


def _confirmation_scope(
    plan: PublishPlan,
    *,
    bootstrap_provenance: BootstrapProvenance,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Return the non-secret exact facts a human approval is binding."""

    return {
        "source_site": manifest.SOURCE_SITE,
        "destination_site": manifest.DESTINATION_SITE,
        "campaign_id": plan.campaign_id,
        "bucket": plan.bucket,
        "prefix": plan.prefix,
        "created_at": plan.created_at,
        "presigned_ttl_seconds": ttl_seconds,
        "destination_age_recipient": WA_IR_AGE_RECIPIENT,
        "destination_age_recipient_key_id": plan.destination_age_recipient_key_id,
        "bootstrap": bootstrap_provenance.as_manifest(),
        "artifacts": [
            {
                "kind": item.kind,
                "plaintext_sha256": item.plaintext_sha256,
                "plaintext_bytes": item.plaintext_bytes,
                "ciphertext_sha256": item.ciphertext_sha256,
                "ciphertext_bytes": item.ciphertext_bytes,
            }
            for item in plan.artifacts
        ],
    }


def _verify_local_ciphertext(descriptor: ArtifactDescriptor) -> VerifiedLocalArtifact:
    """Check hash, exact size and age header before any external request."""

    path = descriptor.ciphertext_path
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyPublisherError(f"{descriptor.kind} ciphertext cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o077
        or before.st_size != descriptor.ciphertext_bytes
    ):
        _fail(f"{descriptor.kind} ciphertext must be a root-only sealed regular file")
    descriptor_fd: int | None = None
    try:
        descriptor_fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor_fd)
        if FileStamp.from_stat(opened) != FileStamp.from_stat(before):
            _fail(f"{descriptor.kind} ciphertext changed while being opened")
        digest = hashlib.sha256()
        observed = 0
        header = bytearray()
        while True:
            chunk = os.read(descriptor_fd, HASH_CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > descriptor.ciphertext_bytes:
                _fail(f"{descriptor.kind} ciphertext exceeds its declared size")
            if len(header) < len(AGE_HEADER):
                header.extend(chunk[: len(AGE_HEADER) - len(header)])
            digest.update(chunk)
        after = os.fstat(descriptor_fd)
        if (
            observed != descriptor.ciphertext_bytes
            or digest.hexdigest() != descriptor.ciphertext_sha256
            or not bytes(header).startswith(AGE_HEADER)
            or FileStamp.from_stat(after) != FileStamp.from_stat(opened)
        ):
            _fail(f"{descriptor.kind} ciphertext does not match its sealed age descriptor")
        return VerifiedLocalArtifact(descriptor=descriptor, stamp=FileStamp.from_stat(opened))
    except OSError as exc:
        raise EmergencyPublisherError(f"{descriptor.kind} ciphertext cannot be read") from exc
    finally:
        if descriptor_fd is not None:
            os.close(descriptor_fd)


def _ensure_safe_output(path: Path, *, label: str) -> None:
    if not path.is_absolute() or not path.name or path.name in {".", ".."}:
        _fail(f"{label} output path must be absolute")
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} output directory cannot be inspected") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        _fail(f"{label} output directory is not owner-controlled")
    try:
        state = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} output path cannot be inspected") from exc
    if state:
        _fail(f"refusing to overwrite existing {label} output")


def _write_create_only(path: Path, payload: bytes, *, label: str) -> None:
    _ensure_safe_output(path, label=label)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        remainder = memoryview(payload)
        while remainder:
            written = os.write(descriptor, remainder)
            if written <= 0:  # pragma: no cover - os.write does not normally return zero.
                raise OSError("short output write")
            remainder = remainder[written:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencyPublisherError(f"refusing to overwrite existing {label} output") from exc
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} output cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _inspect_created_file(path: Path, *, label: str, maximum_bytes: int) -> tuple[FileStamp, str, int]:
    payload = _read_owner_regular(path, label=label, maximum_bytes=maximum_bytes, require_private=True)
    state = path.lstat()
    return FileStamp.from_stat(state), hashlib.sha256(payload).hexdigest(), len(payload)


def _open_verified_source(path: Path, *, expected: FileStamp, label: str) -> BinaryIO:
    """Open the same private file whose hash was just preflighted."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} cannot be re-opened") from exc
    if FileStamp.from_stat(before) != expected:
        _fail(f"{label} changed after local verification")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} cannot be opened for upload") from exc
    opened = os.fstat(descriptor)
    if FileStamp.from_stat(opened) != expected:
        os.close(descriptor)
        _fail(f"{label} changed while being opened for upload")
    return os.fdopen(descriptor, "rb", closefd=True)


def _s3_error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, Mapping):
        return None
    error = response.get("Error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("Code")
    return str(code) if code is not None else None


def _require_immutable_version(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value == "null" or len(value) > 1024:
        _fail(f"{label} did not return an immutable Object Storage VersionId")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        _fail(f"{label} returned an unsafe Object Storage VersionId")
    return value


def _require_owner_only_acl(
    value: object,
    *,
    label: str,
    expected_owner_id: str | None = None,
) -> str:
    """Require an exact canonical-owner-only ACL, not merely a non-public one."""

    if not isinstance(value, Mapping):
        _fail(f"{label} ACL cannot be verified")
    owner = value.get("Owner")
    owner_id = owner.get("ID") if isinstance(owner, Mapping) else None
    if (
        not isinstance(owner_id, str)
        or not owner_id
        or len(owner_id) > 1024
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in owner_id)
    ):
        _fail(f"{label} ACL owner cannot be verified")
    if expected_owner_id is not None and owner_id != expected_owner_id:
        _fail(f"{label} ACL owner differs from the dedicated Emergency bucket owner")
    grants = value.get("Grants")
    if not isinstance(grants, list) or not grants:
        _fail(f"{label} ACL grants cannot be verified")
    for grant in grants:
        if not isinstance(grant, Mapping):
            _fail(f"{label} ACL grant is invalid")
        grantee = grant.get("Grantee")
        permission = grant.get("Permission")
        if (
            not isinstance(grantee, Mapping)
            or grantee.get("Type") != "CanonicalUser"
            or grantee.get("ID") != owner_id
            or grantee.get("URI") is not None
            or grantee.get("EmailAddress") is not None
            or permission != OWNER_ONLY_ACL_PERMISSION
        ):
            _fail(f"{label} ACL must grant full control only to the dedicated bucket owner")
    return owner_id


def _require_private_versioned_bucket(client: Any, *, bucket: str) -> str:
    """Fail closed unless all bucket privacy and versioning controls are visible."""

    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
        if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
            _fail("Arvan bucket versioning must be Enabled")
        public_access = client.get_public_access_block(Bucket=bucket)
        configuration = public_access.get("PublicAccessBlockConfiguration") if isinstance(public_access, Mapping) else None
        required = {
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        }
        if not isinstance(configuration, Mapping) or any(
            configuration.get(key) is not expected for key, expected in required.items()
        ):
            _fail("Arvan bucket public-access block must be fully enabled")
        try:
            client.get_bucket_policy(Bucket=bucket)
        except Exception as exc:  # No policy is safe; every other unknown response is not.
            if _s3_error_code(exc) != "NoSuchBucketPolicy":
                raise
        else:
            _fail("dedicated Emergency bucket must not have a bucket policy")
        acl = client.get_bucket_acl(Bucket=bucket)
        return _require_owner_only_acl(acl, label="Arvan bucket")
    except EmergencyPublisherError:
        raise
    except Exception as exc:
        raise EmergencyPublisherError("Arvan bucket privacy/versioning cannot be verified") from exc


def _assert_key_unused(client: Any, *, bucket: str, key: str) -> None:
    """Reject every pre-existing version before the later conditional write.

    ``IfNoneMatch=*`` prevents a concurrent current-object replacement.  The
    manifest still binds the returned immutable VersionId because versioning
    alone cannot make a key permanently single-write without Object Lock.
    """

    try:
        listing = client.list_object_versions(Bucket=bucket, Prefix=key)
        if not isinstance(listing, Mapping) or listing.get("IsTruncated") is True:
            _fail("Object Storage version history cannot be completely verified")
        versions = listing.get("Versions") or []
        delete_markers = listing.get("DeleteMarkers") or []
        if not isinstance(versions, list) or not isinstance(delete_markers, list):
            _fail("Object Storage version history cannot be verified")
        for item in [*versions, *delete_markers]:
            if not isinstance(item, Mapping):
                _fail("Object Storage version history cannot be verified")
            if item.get("Key") == key:
                _fail("refusing to publish over an existing Emergency campaign object version")
    except EmergencyPublisherError:
        raise
    except Exception as exc:
        raise EmergencyPublisherError("Object Storage version history cannot be verified") from exc
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _s3_error_code(exc) in _NOT_FOUND_CODES:
            return
        raise EmergencyPublisherError("Object Storage key availability cannot be verified") from exc
    _fail("refusing to publish over an existing Emergency campaign object")


def _readback_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_sha256: str,
    expected_bytes: int,
    expected_bucket_owner_id: str,
) -> None:
    """Verify the object stream selected by the returned immutable VersionId."""

    try:
        head = client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
        if (
            not isinstance(head, Mapping)
            or head.get("ContentLength") != expected_bytes
            or head.get("VersionId") != version_id
        ):
            _fail("Object Storage immutable head readback differs from the uploaded object")
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        if not isinstance(response, Mapping) or response.get("ContentLength") != expected_bytes:
            _fail("Object Storage immutable GET readback differs from the uploaded object")
        body = response.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            _fail("Object Storage immutable GET body is unavailable")
        digest = hashlib.sha256()
        observed = 0
        try:
            while True:
                chunk = body.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    _fail("Object Storage immutable GET returned a non-bytes body")
                observed += len(chunk)
                if observed > expected_bytes:
                    _fail("Object Storage immutable GET exceeds its expected size")
                digest.update(chunk)
        finally:
            closer = getattr(body, "close", None)
            if callable(closer):
                closer()
        if observed != expected_bytes or digest.hexdigest() != expected_sha256:
            _fail("Object Storage immutable GET hash/size differs from the uploaded object")
        _require_owner_only_acl(
            client.get_object_acl(Bucket=bucket, Key=key, VersionId=version_id),
            label="Emergency object",
            expected_owner_id=expected_bucket_owner_id,
        )
    except EmergencyPublisherError:
        raise
    except Exception as exc:
        raise EmergencyPublisherError("Object Storage immutable readback failed") from exc


def _upload_and_readback(
    client: Any,
    *,
    bucket: str,
    key: str,
    source_path: Path,
    source_stamp: FileStamp,
    expected_sha256: str,
    expected_bytes: int,
    label: str,
    expected_bucket_owner_id: str,
) -> UploadedObject:
    """Create one fresh object version then verify exactly that version's body."""

    _assert_key_unused(client, bucket=bucket, key=key)
    source = _open_verified_source(source_path, expected=source_stamp, label=label)
    try:
        response = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=source,
            ContentType="application/octet-stream",
            Metadata={"sha256": expected_sha256, "emergency-artifact": label},
            ACL="private",
            IfNoneMatch="*",
        )
        version_id = _require_immutable_version(
            response.get("VersionId") if isinstance(response, Mapping) else None,
            label=label,
        )
        uploaded_state = os.fstat(source.fileno())
        if FileStamp.from_stat(uploaded_state) != source_stamp:
            _fail(f"{label} changed while being uploaded")
    except EmergencyPublisherError:
        raise
    except Exception as exc:
        raise EmergencyPublisherError("Object Storage upload failed") from exc
    finally:
        source.close()
    _readback_object(
        client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
        expected_bucket_owner_id=expected_bucket_owner_id,
    )
    return UploadedObject(key=key, version_id=version_id, sha256=expected_sha256, bytes=expected_bytes)


def _control_key(plan: PublishPlan, kind: str) -> str:
    try:
        filename = CONTROL_FILENAMES[kind]
    except KeyError as exc:  # pragma: no cover - internal callers use constants.
        raise EmergencyPublisherError("unknown Emergency control artifact") from exc
    return "/".join((plan.prefix, plan.campaign_id, "control", filename))


def _generate_presigned_get(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str,
    ttl_seconds: int,
) -> str:
    try:
        value = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key, "VersionId": version_id},
            ExpiresIn=ttl_seconds,
            HttpMethod="GET",
        )
    except Exception as exc:
        raise EmergencyPublisherError("Object Storage presigned URL generation failed") from exc
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > receiver.MAX_URL_BYTES:
        _fail("Object Storage presigned URL generation returned an invalid URL")
    _validate_presigned_get(
        value,
        bucket=bucket,
        key=key,
        version_id=version_id,
        ttl_seconds=ttl_seconds,
    )
    return value


def _validate_presigned_get(
    url: str,
    *,
    bucket: str,
    key: str,
    version_id: str,
    ttl_seconds: int,
) -> None:
    """Ensure generated URLs satisfy the receiver's fixed S3 receive shape."""

    try:
        parsed = urlsplit(url)
        endpoint = urlsplit(manifest.APPROVED_ARVAN_ENDPOINT)
    except ValueError as exc:
        raise EmergencyPublisherError("generated Object Storage URL is malformed") from exc
    approved_hosts = {endpoint.hostname, f"{bucket}.{endpoint.hostname}"}
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in approved_hosts
        or parsed.port is not None
        or parsed.fragment
    ):
        _fail("generated Object Storage URL endpoint is outside the approved Arvan endpoint")
    encoded_key = quote(key, safe="/")
    expected_path = (
        "/" + quote(bucket, safe="") + "/" + encoded_key
        if parsed.hostname == endpoint.hostname
        else "/" + encoded_key
    )
    if parsed.path != expected_path:
        _fail("generated Object Storage URL does not bind the expected object key")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise EmergencyPublisherError("generated Object Storage URL query is malformed") from exc
    if not PRESIGNED_QUERY_FIELDS.issubset(query) or any(len(values) != 1 for values in query.values()):
        _fail("generated Object Storage URL is not one strict presigned request")
    if query["X-Amz-Algorithm"][0] != "AWS4-HMAC-SHA256" or query["versionId"][0] != version_id:
        _fail("generated Object Storage URL does not bind its immutable VersionId")
    try:
        observed_ttl = int(query["X-Amz-Expires"][0], 10)
    except ValueError as exc:
        raise EmergencyPublisherError("generated Object Storage URL expiry is invalid") from exc
    if observed_ttl != ttl_seconds or not receiver.MIN_PRESIGNED_TTL_SECONDS <= observed_ttl <= receiver.MAX_PRESIGNED_TTL_SECONDS:
        _fail("generated Object Storage URL expiry is outside the Emergency bound")


def _build_url_map(
    *,
    client: Any,
    plan: PublishPlan,
    signed_manifest: bytes,
    public_key: Any,
    uploaded_artifacts: Mapping[str, UploadedObject],
    ttl_seconds: int,
) -> bytes:
    """Build the root-only map consumed by the existing sealed receiver."""

    verified = manifest.verify_manifest_bytes(signed_manifest, public_key=public_key)
    receive_plan = verified.as_receive_plan()
    entries: list[dict[str, str]] = []
    for artifact in receive_plan["artifacts"]:
        kind = str(artifact["kind"])
        uploaded = uploaded_artifacts.get(kind)
        if uploaded is None or uploaded.key != artifact["object_key"] or uploaded.version_id != artifact["version_id"]:
            _fail("uploaded artifact versions do not match the sealed manifest")
        url = _generate_presigned_get(
            client,
            bucket=plan.bucket,
            key=uploaded.key,
            version_id=uploaded.version_id,
            ttl_seconds=ttl_seconds,
        )
        # Use the receiver's own validator before emitting any URL-bearing
        # output; publication cannot create a map the receiver would reject.
        receiver._validate_presigned_url(url=url, plan=receive_plan, artifact=artifact)
        entries.append({"kind": kind, "url": url})
    value = {
        "schema": receiver.URL_MAP_SCHEMA,
        "manifest_sha256": receive_plan["manifest_sha256"],
        "artifacts": entries,
    }
    payload = manifest.canonical_json_bytes(value)
    receiver._parse_url_map(payload, manifest_sha256=receive_plan["manifest_sha256"])
    return payload


def _build_bootstrap_descriptor(
    *,
    plan: PublishPlan,
    bootstrap_provenance: BootstrapProvenance,
    ttl_seconds: int,
    client: Any,
    receiver_bundle_object: UploadedObject,
    manifest_object: UploadedObject,
    url_map_object: UploadedObject,
) -> bytes:
    """Return the only local file that contains short-lived presigned URLs."""

    control = {
        "receiver_bundle": receiver_bundle_object,
        "manifest": manifest_object,
        "url_map": url_map_object,
    }
    entries: dict[str, dict[str, Any]] = {}
    for kind, uploaded in control.items():
        entries[kind] = {
            "url": _generate_presigned_get(
                client,
                bucket=plan.bucket,
                key=uploaded.key,
                version_id=uploaded.version_id,
                ttl_seconds=ttl_seconds,
            ),
            "sha256": uploaded.sha256,
            "bytes": uploaded.bytes,
        }
    payload = {
        "schema": receiver_bootstrap.SCHEMA,
        "campaign_id": plan.campaign_id,
        "expires_in_seconds": ttl_seconds,
        "bootstrap_provenance": bootstrap_provenance.as_manifest(),
        **entries,
    }
    return manifest.canonical_json_bytes(payload)


def _check_outputs(outputs: PublishOutputs) -> None:
    paths = {
        "receiver bundle": outputs.receiver_bundle,
        "sealed manifest": outputs.sealed_manifest,
        "presigned URL map": outputs.url_map,
        "bootstrap descriptor": outputs.descriptor,
    }
    if len({str(path) for path in paths.values()}) != len(paths):
        _fail("Emergency publisher outputs must use distinct paths")
    for label, path in paths.items():
        _ensure_safe_output(path, label=label)


def publish(
    *,
    client: Any,
    plan: PublishPlan,
    signing_private_key_path: Path,
    signing_public_key_path: Path,
    bootstrap_provenance: BootstrapProvenance,
    outputs: PublishOutputs,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Perform the guarded publish flow after CLI confirmation has succeeded."""

    if not receiver.MIN_PRESIGNED_TTL_SECONDS <= ttl_seconds <= receiver.MAX_PRESIGNED_TTL_SECONDS:
        _fail("presigned URL TTL is outside the Emergency receiver bound")
    _check_outputs(outputs)
    try:
        private_key = manifest.load_private_key(signing_private_key_path)
        public_key = manifest.load_public_key(signing_public_key_path)
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("Emergency signing keys are unavailable or unsafe") from exc
    if manifest.signer_key_id(private_key.public_key()) != manifest.signer_key_id(public_key):
        _fail("Emergency signing private/public keys do not form the pinned keypair")
    if manifest.signer_key_id(public_key) != bootstrap_provenance.signer_key_id:
        _fail("receiver bootstrap provenance signer does not match the pinned signing key")
    if _bootstrap_provenance(signing_public_key_path=signing_public_key_path) != bootstrap_provenance:
        _fail("receiver bootstrap provenance changed after confirmation")
    verified_artifacts = tuple(_verify_local_ciphertext(item) for item in plan.artifacts)
    bucket_owner_id = _require_private_versioned_bucket(client, bucket=plan.bucket)

    # Verify all campaign locations are unused before writing a local control
    # artifact or uploading the first byte.  Each later result is still bound
    # to its exact returned VersionId to protect against races and later writes.
    keys = [
        *(manifest.expected_object_key(prefix=plan.prefix, campaign_id=plan.campaign_id, kind=item.kind) for item in plan.artifacts),
        *(_control_key(plan, kind) for kind in CONTROL_FILENAMES),
    ]
    for key in keys:
        _assert_key_unused(client, bucket=plan.bucket, key=key)

    try:
        receiver_bundle.build_bundle(
            signing_public_key=signing_public_key_path,
            output=outputs.receiver_bundle,
            source_revision=bootstrap_provenance.publisher_source_revision,
        )
    except receiver_bundle.ReceiverBundleError as exc:
        raise EmergencyPublisherError("pinned-key receiver bootstrap bundle cannot be built") from exc
    bundle_stamp, bundle_hash, bundle_bytes = _inspect_created_file(
        outputs.receiver_bundle,
        label="receiver bootstrap bundle",
        maximum_bytes=MAX_CONTROL_ARTIFACT_BYTES,
    )
    if (
        bundle_hash != bootstrap_provenance.receiver_bundle_sha256
        or bundle_bytes != bootstrap_provenance.receiver_bundle_bytes
        or _fixed_publisher_source_revision() != bootstrap_provenance.publisher_source_revision
    ):
        _fail("receiver bootstrap bundle changed after provenance preflight")
    bundle_object = _upload_and_readback(
        client,
        bucket=plan.bucket,
        key=_control_key(plan, "receiver_bundle"),
        source_path=outputs.receiver_bundle,
        source_stamp=bundle_stamp,
        expected_sha256=bundle_hash,
        expected_bytes=bundle_bytes,
        label="receiver bootstrap bundle",
        expected_bucket_owner_id=bucket_owner_id,
    )

    uploaded_artifacts: dict[str, UploadedObject] = {}
    for item in verified_artifacts:
        descriptor = item.descriptor
        uploaded_artifacts[descriptor.kind] = _upload_and_readback(
            client,
            bucket=plan.bucket,
            key=manifest.expected_object_key(
                prefix=plan.prefix, campaign_id=plan.campaign_id, kind=descriptor.kind
            ),
            source_path=descriptor.ciphertext_path,
            source_stamp=item.stamp,
            expected_sha256=descriptor.ciphertext_sha256,
            expected_bytes=descriptor.ciphertext_bytes,
            label=f"Emergency artifact {descriptor.kind}",
            expected_bucket_owner_id=bucket_owner_id,
        )

    unsigned = _unsigned_manifest(
        plan,
        version_ids={kind: uploaded_artifacts[kind].version_id for kind in manifest.ARTIFACT_ORDER},
        bootstrap_provenance=bootstrap_provenance,
    )
    try:
        signed = manifest.sign_manifest(unsigned, private_key=private_key)
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("sealed Emergency manifest cannot be signed") from exc
    sealed_manifest = manifest.canonical_json_bytes(signed)
    _write_create_only(outputs.sealed_manifest, sealed_manifest, label="sealed manifest")
    manifest_stamp, manifest_hash, manifest_bytes = _inspect_created_file(
        outputs.sealed_manifest,
        label="sealed manifest",
        maximum_bytes=manifest.MAX_MANIFEST_BYTES,
    )
    manifest_object = _upload_and_readback(
        client,
        bucket=plan.bucket,
        key=_control_key(plan, "manifest"),
        source_path=outputs.sealed_manifest,
        source_stamp=manifest_stamp,
        expected_sha256=manifest_hash,
        expected_bytes=manifest_bytes,
        label="sealed manifest",
        expected_bucket_owner_id=bucket_owner_id,
    )

    url_map_payload = _build_url_map(
        client=client,
        plan=plan,
        signed_manifest=sealed_manifest,
        public_key=public_key,
        uploaded_artifacts=uploaded_artifacts,
        ttl_seconds=ttl_seconds,
    )
    _write_create_only(outputs.url_map, url_map_payload, label="presigned URL map")
    map_stamp, map_hash, map_bytes = _inspect_created_file(
        outputs.url_map,
        label="presigned URL map",
        maximum_bytes=receiver.MAX_URL_MAP_BYTES,
    )
    url_map_object = _upload_and_readback(
        client,
        bucket=plan.bucket,
        key=_control_key(plan, "url_map"),
        source_path=outputs.url_map,
        source_stamp=map_stamp,
        expected_sha256=map_hash,
        expected_bytes=map_bytes,
        label="presigned URL map",
        expected_bucket_owner_id=bucket_owner_id,
    )

    descriptor_payload = _build_bootstrap_descriptor(
        plan=plan,
        bootstrap_provenance=bootstrap_provenance,
        ttl_seconds=ttl_seconds,
        client=client,
        receiver_bundle_object=bundle_object,
        manifest_object=manifest_object,
        url_map_object=url_map_object,
    )
    _write_create_only(outputs.descriptor, descriptor_payload, label="bootstrap descriptor")
    # This deliberately validates the exact on-disk 0600 descriptor using the
    # existing controller-side bootstrap contract without invoking SSH.
    receiver_bootstrap.load_descriptor(outputs.descriptor)
    return {
        "status": "published-sealed",
        "campaign_id": plan.campaign_id,
        "manifest_sha256": manifest_hash,
        "signer_key_id": manifest.signer_key_id(public_key),
        "artifact_count": len(uploaded_artifacts),
        "payload_transport": "private-arvan-object-storage-only",
        "descriptor": str(outputs.descriptor),
        "receiver_bundle": str(outputs.receiver_bundle),
        "sealed_manifest": str(outputs.sealed_manifest),
        "url_map": str(outputs.url_map),
    }


def _load_credentials(path: Path) -> dict[str, str]:
    """Read local S3 credentials only after the apply confirmation succeeds."""

    raw = _load_json(
        path,
        label="Arvan Object Storage credentials",
        maximum_bytes=MAX_CREDENTIALS_BYTES,
        private=True,
    )
    if set(raw) not in ({"access_key_id", "secret_access_key"}, {"access_key_id", "secret_access_key", "session_token"}):
        _fail("Arvan Object Storage credential fields are unsupported")
    result: dict[str, str] = {}
    for field in ("access_key_id", "secret_access_key", "session_token"):
        value = raw.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
            _fail("Arvan Object Storage credentials are invalid")
        result[field] = value
    if set(result) < {"access_key_id", "secret_access_key"}:
        _fail("Arvan Object Storage credentials are incomplete")
    return result


def _require_direct_object_storage_transport() -> None:
    """Fail before credentials/client construction when a proxy is configured."""

    if any(str(os.environ.get(key) or "").strip() for key in PROXY_ENVIRONMENT_KEYS):
        _fail("direct private Arvan transport requires proxy environment variables to be unset")
    if any(str(os.environ.get(key) or "").strip() for key in TLS_CA_OVERRIDE_ENVIRONMENT_KEYS):
        _fail("direct private Arvan transport requires CA override environment variables to be unset")


def make_s3_client(credentials_path: Path) -> Any:
    """Create the one fixed-endpoint S3 client lazily, after confirmation."""

    _require_direct_object_storage_transport()
    credentials = _load_credentials(credentials_path)
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise EmergencyPublisherError("boto3 with botocore is required for the guarded publish step") from exc
    try:
        return boto3.session.Session(
            aws_access_key_id=credentials["access_key_id"],
            aws_secret_access_key=credentials["secret_access_key"],
            aws_session_token=credentials.get("session_token"),
            region_name=manifest.APPROVED_ARVAN_REGION,
        ).client(
            "s3",
            endpoint_url=manifest.APPROVED_ARVAN_ENDPOINT,
            region_name=manifest.APPROVED_ARVAN_REGION,
            verify=True,
            # Redundant with the fail-closed environment check above: retain
            # the direct-only invariant in botocore's own request config.
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                proxies={},
            ),
        )
    except Exception as exc:
        raise EmergencyPublisherError("Arvan Object Storage client cannot be initialized") from exc


def _load_public_key_id(path: Path) -> str:
    try:
        return manifest.signer_key_id(manifest.load_public_key(path))
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("pinned Emergency signing public key is unavailable or unsafe") from exc


def execute(
    args: argparse.Namespace,
    *,
    client_factory: Callable[[Path], Any] = make_s3_client,
) -> dict[str, Any]:
    plan = load_publish_plan(args.plan)
    if not receiver.MIN_PRESIGNED_TTL_SECONDS <= args.ttl_seconds <= receiver.MAX_PRESIGNED_TTL_SECONDS:
        _fail("presigned URL TTL is outside the Emergency receiver bound")
    # There is intentionally no caller-selectable source tree.  This checks
    # the publisher's fixed checkout before dry-run output is shown, and binds
    # the exact deterministic receiver bundle to the human confirmation.
    bootstrap_provenance = _bootstrap_provenance(signing_public_key_path=args.signing_public_key)
    expected_confirmation = confirmation_phrase(
        plan, bootstrap_provenance=bootstrap_provenance, ttl_seconds=args.ttl_seconds
    )
    if not args.apply:
        return {
            "status": "planned-no-network",
            "campaign_id": plan.campaign_id,
            "artifact_count": len(plan.artifacts),
            "payload_transport": "private-arvan-object-storage-only",
            "required_confirmation": expected_confirmation,
            "bootstrap_provenance": bootstrap_provenance.as_manifest(),
            "confirmation_scope": _confirmation_scope(
                plan,
                bootstrap_provenance=bootstrap_provenance,
                ttl_seconds=args.ttl_seconds,
            ),
        }
    if args.confirm != expected_confirmation:
        _fail("Emergency Object Storage publish confirmation mismatch")
    if args.credentials is None:
        _fail("Arvan Object Storage credentials are required with --apply")
    outputs = PublishOutputs(
        receiver_bundle=args.receiver_bundle_output,
        sealed_manifest=args.sealed_manifest_output,
        url_map=args.url_map_output,
        descriptor=args.descriptor_output,
    )
    client = client_factory(args.credentials)
    return publish(
        client=client,
        plan=plan,
        signing_private_key_path=args.signing_private_key,
        signing_public_key_path=args.signing_public_key,
        bootstrap_provenance=bootstrap_provenance,
        outputs=outputs,
        ttl_seconds=args.ttl_seconds,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--signing-private-key", type=Path, required=True)
    parser.add_argument("--signing-public-key", type=Path, required=True)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--receiver-bundle-output", type=Path, required=True)
    parser.add_argument("--sealed-manifest-output", type=Path, required=True)
    parser.add_argument("--url-map-output", type=Path, required=True)
    parser.add_argument("--descriptor-output", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=300)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        _require_isolated_cli()
        args = parse_args(argv)
        result = execute(args)
    except EmergencyPublisherError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    except Exception:
        # Do not leak provider URLs, credentials, or provider exception text.
        print(json.dumps({"status": "blocked", "error": "Emergency Object Storage publish failed", "error_class": "UnexpectedError"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
