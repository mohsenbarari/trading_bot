#!/usr/bin/env python3
"""Render, but never execute, one verified normal WA-IR artifact-stage consume.

The normal artifact-stage publish receipt includes a short-lived, version-bound
manifest URL.  This controller-local helper accepts that receipt only from
stdin, validates its non-secret bindings against a root-only consumer config,
and emits one strictly quoted SSH command.  The URL is the final transient
remote argument; this helper never writes it, invokes SSH, or transfers a
payload directly between hosts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import sys
from typing import Any, Mapping, Sequence


def _load_stage_primitives() -> Any:
    try:
        import manage_webapp_ir_artifact_stage as stage  # type: ignore[import-not-found]

        return stage
    except ModuleNotFoundError:
        module_path = Path(__file__).with_name("manage_webapp_ir_artifact_stage.py")
        spec = importlib.util.spec_from_file_location("_webapp_ir_stage_primitives", module_path)
        if spec is None or spec.loader is None:  # pragma: no cover - local repository invariant.
            raise RuntimeError("cannot load WA-IR artifact-stage primitives")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


stage = _load_stage_primitives()


REMOTE_HOSTNAME = "95.38.164.29"
REMOTE_HOST = "root@" + REMOTE_HOSTNAME
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes")
SOURCE_SITE = "webapp_fi"
DESTINATION_SITE = "webapp_ir"
WA_IR_CAMPAIGN_IDENTITY_ROOT = "/etc/trading-bot-three-site/campaigns"
WA_IR_BOOTSTRAP_IDENTITY_SUFFIX = "webapp-ir/bootstrap.agekey"
WA_IR_BOOTSTRAP_ROOT = "/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap"
WA_IR_STAGING_ROOT = "/srv/trading-bot-three-site-staging-data/wa-ir-standby/artifact-stage"
REMOTE_CONSUMER_SCRIPT = "scripts/manage_webapp_ir_artifact_stage.py"
REMOTE_CONSUMER_CONFIG = "config/consumer.json"
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_URL_BYTES = 8192
MAX_MANIFEST_CIPHERTEXT_BYTES = 1024 * 1024
AGE_CIPHERTEXT_MARGIN_BYTES = 1024 * 1024
MAX_KNOWN_HOSTS_BYTES = 256 * 1024
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
RELEASE_RE = re.compile(r"^[a-f0-9]{40,64}$")
BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
BOOTSTRAP_CANDIDATE_RE = re.compile(
    r"^/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap/received-"
    r"[a-f0-9]{40}-[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
EXPECTED_ARTIFACT_NAMES = (
    "control-release-bundle",
    "image-bundle",
    "image-manifest",
    "release-bundle",
    "release-provenance",
)


class NormalStageRenderError(RuntimeError):
    """The transient normal-stage request cannot be rendered safely."""


class _SafeArgumentParser(argparse.ArgumentParser):
    """Avoid reflecting transient argv values to direct CLI stderr."""

    def error(self, _message: str) -> None:
        raise NormalStageRenderError("invalid command-line input")


def wa_ir_bootstrap_identity_file(campaign_id: object) -> str:
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise NormalStageRenderError("campaign ID is invalid for the WA-IR bootstrap age identity")
    path = PurePosixPath(WA_IR_CAMPAIGN_IDENTITY_ROOT) / campaign_id / WA_IR_BOOTSTRAP_IDENTITY_SUFFIX
    value = path.as_posix()
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise NormalStageRenderError("campaign WA-IR bootstrap age identity path is invalid")
    return value


def _read_root_controlled_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    if not path.is_absolute():
        raise NormalStageRenderError(f"{field} path must be absolute")
    try:
        before_lstat = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise NormalStageRenderError(f"cannot inspect {field}") from exc
    if resolved != path or stat.S_ISLNK(before_lstat.st_mode) or not stat.S_ISREG(before_lstat.st_mode):
        raise NormalStageRenderError(f"{field} must be one canonical regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NormalStageRenderError(f"cannot securely open {field}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum_bytes
            or before.st_mode & 0o022
            or before.st_dev != before_lstat.st_dev
            or before.st_ino != before_lstat.st_ino
        ):
            raise NormalStageRenderError(f"{field} has unsafe ownership, mode, or size")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(result) != before.st_size or any(getattr(before, name) != getattr(after, name) for name in identity):
            raise NormalStageRenderError(f"{field} changed while being read")
        return result
    finally:
        os.close(descriptor)


def _require_pinned_known_hosts(path: Path) -> Path:
    payload = _read_root_controlled_file(
        Path(path),
        field="pinned WA-IR SSH known_hosts",
        maximum_bytes=MAX_KNOWN_HOSTS_BYTES,
    )
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise NormalStageRenderError("pinned WA-IR SSH known_hosts is not ASCII") from exc
    expected_hosts = {REMOTE_HOSTNAME, f"[{REMOTE_HOSTNAME}]:22"}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3 or fields[0].startswith("@"):
            continue
        hosts, key_type, encoded_key = fields[:3]
        if (
            expected_hosts.intersection(hosts.split(","))
            and (key_type.startswith("ssh-") or key_type.startswith("ecdsa-"))
            and re.fullmatch(r"[A-Za-z0-9+/=]{16,16384}", encoded_key)
        ):
            return Path(path)
    raise NormalStageRenderError("pinned WA-IR SSH known_hosts lacks the exact WA-IR host key")


def _render_pinned_ssh(*, known_hosts: Path, remote_arguments: Sequence[str]) -> str:
    if not remote_arguments or any(not isinstance(item, str) or not item for item in remote_arguments):
        raise NormalStageRenderError("WA-IR SSH remote command is invalid")
    pin = _require_pinned_known_hosts(known_hosts)
    return shlex.join(
        [
            "ssh",
            *SSH_OPTIONS,
            "-o",
            "UserKnownHostsFile=" + str(pin),
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            REMOTE_HOST,
            shlex.join(list(remote_arguments)),
        ]
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NormalStageRenderError("publish receipt contains duplicate JSON keys")
        result[key] = value
    return result


def _read_publish_receipt_stdin() -> bytes:
    """Read one bounded receipt without creating a durable URL-bearing file."""

    stream = getattr(sys.stdin, "buffer", sys.stdin)
    try:
        payload = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError as exc:
        raise NormalStageRenderError("cannot read normal stage publish receipt from stdin") from exc
    if not isinstance(payload, bytes):
        raise NormalStageRenderError("normal stage publish receipt stdin must be binary")
    if not payload:
        raise NormalStageRenderError("normal stage publish receipt stdin is empty")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise NormalStageRenderError("normal stage publish receipt stdin exceeds the fixed size bound")
    return payload


def _parse_receipt(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NormalStageRenderError("normal stage publish receipt is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise NormalStageRenderError("normal stage publish receipt must be a JSON object")
    return value


def _require_text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise NormalStageRenderError(f"{field} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise NormalStageRenderError(f"{field} contains control characters")
    return value


def _require_release(value: object, *, field: str) -> str:
    result = _require_text(value, field=field, maximum=64)
    if not RELEASE_RE.fullmatch(result):
        raise NormalStageRenderError(f"{field} is invalid")
    return result


def _require_bundle_id(value: object, *, field: str) -> str:
    result = _require_text(value, field=field, maximum=128)
    if not BUNDLE_ID_RE.fullmatch(result):
        raise NormalStageRenderError(f"{field} is invalid")
    return result


def _require_sha256(value: object, *, field: str) -> str:
    result = _require_text(value, field=field, maximum=64)
    if not SHA256_RE.fullmatch(result):
        raise NormalStageRenderError(f"{field} is invalid")
    return result


def _require_positive_size(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise NormalStageRenderError(f"{field} is invalid")
    return value


def _require_version_id(value: object, *, field: str) -> str:
    try:
        return stage.require_version_id(value, field)
    except stage.ArtifactStageError as exc:
        raise NormalStageRenderError(f"{field} is invalid") from exc


def _require_remote_path(value: object, *, field: str) -> str:
    result = _require_text(value, field=field, maximum=1024)
    path = PurePosixPath(result)
    if (
        not path.is_absolute()
        or path.as_posix() != result
        or len(path.parts) < 2
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise NormalStageRenderError(f"{field} must be one canonical absolute path")
    return result


def _parse_published_at(value: object) -> str:
    result = _require_text(value, field="normal stage publish receipt published_at", maximum=64)
    if not result.endswith("Z"):
        raise NormalStageRenderError("normal stage publish receipt published_at must be UTC")
    try:
        parsed = dt.datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NormalStageRenderError("normal stage publish receipt published_at is invalid") from exc
    if parsed.tzinfo is None:
        raise NormalStageRenderError("normal stage publish receipt published_at is invalid")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _artifact_base(*, prefix: str, release_sha: str, bundle_id: str) -> str:
    return "/".join(
        (
            prefix,
            "release-artifacts",
            "v1",
            SOURCE_SITE,
            DESTINATION_SITE,
            release_sha,
            bundle_id,
        )
    )


def _validate_artifact(
    value: object,
    *,
    name: str,
    base: str,
    maximum_artifact_bytes: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NormalStageRenderError("normal stage publish receipt artifact is invalid")
    artifact = dict(value)
    expected_fields = {
        "name",
        "sha256",
        "bytes",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "bindings",
    }
    if set(artifact) != expected_fields or artifact.get("name") != name:
        raise NormalStageRenderError("normal stage publish receipt artifact fields are unsupported")
    expected_key = base + "/artifacts/" + name + ".age"
    if artifact.get("object_key") != expected_key:
        raise NormalStageRenderError("normal stage publish receipt artifact is outside its immutable namespace")
    bindings = artifact.get("bindings")
    try:
        normalized_bindings = stage.normalize_artifact_bindings(
            bindings,
            field=f"normal stage publish receipt {name} bindings",
        )
    except stage.ArtifactStageError as exc:
        raise NormalStageRenderError("normal stage publish receipt artifact bindings are invalid") from exc
    return {
        "name": name,
        "sha256": _require_sha256(artifact.get("sha256"), field=f"normal stage publish receipt {name} sha256"),
        "bytes": _require_positive_size(
            artifact.get("bytes"),
            field=f"normal stage publish receipt {name} bytes",
            maximum=maximum_artifact_bytes,
        ),
        "object_key": expected_key,
        "version_id": _require_version_id(
            artifact.get("version_id"),
            field=f"normal stage publish receipt {name} version_id",
        ),
        "ciphertext_sha256": _require_sha256(
            artifact.get("ciphertext_sha256"),
            field=f"normal stage publish receipt {name} ciphertext_sha256",
        ),
        "ciphertext_bytes": _require_positive_size(
            artifact.get("ciphertext_bytes"),
            field=f"normal stage publish receipt {name} ciphertext_bytes",
            maximum=maximum_artifact_bytes + AGE_CIPHERTEXT_MARGIN_BYTES,
        ),
        "bindings": normalized_bindings,
    }


def _load_consumer_config(path: Path) -> Any:
    try:
        config = stage.load_consumer_config(path)
    except (stage.ArtifactStageError, stage.snapshot.SnapshotTransportError) as exc:
        raise NormalStageRenderError("normal stage consumer config is unsafe") from exc
    if config.source_site != SOURCE_SITE:
        raise NormalStageRenderError("normal stage consumer config source site is invalid")
    if not CAMPAIGN_ID_RE.fullmatch(config.campaign_id):
        raise NormalStageRenderError("normal stage consumer config campaign_id is invalid")
    if str(config.age_identity_file) != wa_ir_bootstrap_identity_file(config.campaign_id):
        raise NormalStageRenderError("normal stage consumer config does not pin the campaign WA-IR bootstrap identity")
    if not stage.snapshot.AGE_RECIPIENT_RE.fullmatch(config.age_recipient):
        raise NormalStageRenderError("normal stage consumer config age_recipient is invalid")
    return config


def _validate_manifest_url(
    value: object,
    *,
    consumer: Any,
    object_key: str,
    version_id: str,
) -> str:
    url = _require_text(value, field="normal stage manifest presigned_url", maximum=MAX_URL_BYTES)
    if any(character.isspace() for character in url):
        raise NormalStageRenderError("normal stage manifest presigned_url contains whitespace")
    try:
        url = stage.require_version_bound_presigned_url(
            url,
            endpoint=consumer.endpoint,
            bucket=consumer.bucket,
            object_key=object_key,
            version_id=version_id,
        )
    except (ValueError, stage.ArtifactStageError) as exc:
        raise NormalStageRenderError("normal stage manifest URL is not safely bound") from exc
    return url


def _validate_publish_receipt(
    value: object,
    *,
    expected_release_sha: str,
    consumer: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NormalStageRenderError("normal stage publish receipt must be an object")
    receipt = dict(value)
    expected_fields = {
        "schema",
        "status",
        "source_site",
        "destination_site",
        "release_sha",
        "bundle_id",
        "published_at",
        "artifacts",
        "manifest",
    }
    if set(receipt) != expected_fields or receipt.get("schema") != stage.PUBLISH_RECEIPT_SCHEMA or receipt.get("status") != "published":
        raise NormalStageRenderError("normal stage publish receipt is unsupported")
    if receipt.get("source_site") != SOURCE_SITE or receipt.get("destination_site") != DESTINATION_SITE:
        raise NormalStageRenderError("normal stage publish receipt site binding is invalid")
    release_sha = _require_release(receipt.get("release_sha"), field="normal stage publish receipt release_sha")
    if release_sha != expected_release_sha:
        raise NormalStageRenderError("normal stage publish receipt release does not match the requested release")
    bundle_id = _require_bundle_id(receipt.get("bundle_id"), field="normal stage publish receipt bundle_id")
    published_at = _parse_published_at(receipt.get("published_at"))
    base = _artifact_base(prefix=consumer.prefix, release_sha=release_sha, bundle_id=bundle_id)
    raw_artifacts = receipt.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(EXPECTED_ARTIFACT_NAMES):
        raise NormalStageRenderError("normal stage publish receipt artifact set is invalid")
    artifacts = [
        _validate_artifact(
            raw,
            name=name,
            base=base,
            maximum_artifact_bytes=consumer.maximum_artifact_bytes,
        )
        for name, raw in zip(EXPECTED_ARTIFACT_NAMES, raw_artifacts, strict=True)
    ]
    if [artifact["name"] for artifact in artifacts] != list(EXPECTED_ARTIFACT_NAMES):  # pragma: no cover - construction is positional.
        raise NormalStageRenderError("normal stage publish receipt artifacts are not sorted")
    manifest = receipt.get("manifest")
    expected_manifest_fields = {
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "presigned_url",
    }
    if not isinstance(manifest, Mapping) or set(manifest) != expected_manifest_fields:
        raise NormalStageRenderError("normal stage publish receipt manifest descriptor is unsupported")
    manifest_key = base + "/manifest.json.age"
    if manifest.get("object_key") != manifest_key:
        raise NormalStageRenderError("normal stage manifest is outside its immutable namespace")
    manifest_version_id = _require_version_id(
        manifest.get("version_id"),
        field="normal stage manifest version_id",
    )
    manifest_sha256 = _require_sha256(
        manifest.get("ciphertext_sha256"),
        field="normal stage manifest ciphertext_sha256",
    )
    manifest_bytes = _require_positive_size(
        manifest.get("ciphertext_bytes"),
        field="normal stage manifest ciphertext_bytes",
        maximum=MAX_MANIFEST_CIPHERTEXT_BYTES,
    )
    url = _validate_manifest_url(
        manifest.get("presigned_url"),
        consumer=consumer,
        object_key=manifest_key,
        version_id=manifest_version_id,
    )
    return {
        "release_sha": release_sha,
        "bundle_id": bundle_id,
        "published_at": published_at,
        "artifacts": artifacts,
        "manifest": {
            "object_key": manifest_key,
            "version_id": manifest_version_id,
            "ciphertext_sha256": manifest_sha256,
            "ciphertext_bytes": manifest_bytes,
            "presigned_url": url,
        },
    }


def _validate_bootstrap_candidate(value: object) -> str:
    result = _require_remote_path(value, field="bootstrap candidate")
    if not BOOTSTRAP_CANDIDATE_RE.fullmatch(result):
        raise NormalStageRenderError("bootstrap candidate is outside the fixed WA-IR bootstrap namespace")
    return result


def _validate_staging_root(value: object) -> str:
    result = _require_remote_path(value, field="staging root")
    if result != WA_IR_STAGING_ROOT:
        raise NormalStageRenderError("staging root is outside the fixed WA-IR artifact-stage namespace")
    return result


def render_consume_command(
    *,
    publish_receipt_bytes: bytes,
    consumer_config: Path,
    wa_ir_known_hosts: Path,
    bootstrap_candidate: str,
    staging_root: str,
    expected_release_sha: str,
) -> str:
    """Return one SSH command only after all URL-bearing inputs are bound."""

    if not isinstance(publish_receipt_bytes, bytes) or not publish_receipt_bytes or len(publish_receipt_bytes) > MAX_RECEIPT_BYTES:
        raise NormalStageRenderError("normal stage publish receipt bytes are invalid")
    expected_release_sha = _require_release(expected_release_sha, field="expected release_sha")
    candidate = _validate_bootstrap_candidate(bootstrap_candidate)
    root = _validate_staging_root(staging_root)
    consumer = _load_consumer_config(consumer_config)
    published = _validate_publish_receipt(
        _parse_receipt(publish_receipt_bytes),
        expected_release_sha=expected_release_sha,
        consumer=consumer,
    )
    manifest = published["manifest"]
    remote_argv = [
        "/usr/bin/python3",
        "-I",
        "-B",
        candidate + "/" + REMOTE_CONSUMER_SCRIPT,
        "consume",
        "--config",
        candidate + "/" + REMOTE_CONSUMER_CONFIG,
        "--destination-site",
        DESTINATION_SITE,
        "--release-sha",
        published["release_sha"],
        "--bundle-id",
        published["bundle_id"],
        "--manifest-version-id",
        manifest["version_id"],
        "--manifest-ciphertext-sha256",
        manifest["ciphertext_sha256"],
        "--manifest-ciphertext-bytes",
        str(manifest["ciphertext_bytes"]),
        "--staging-root",
        root,
        "--manifest-url",
        manifest["presigned_url"],
    ]
    # The URL is deliberately the final remote argv item, not a configuration file or shell fragment.
    return _render_pinned_ssh(known_hosts=Path(wa_ir_known_hosts), remote_arguments=remote_argv)


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish-receipt-stdin",
        action="store_true",
        required=True,
        help="read one just-published normal stage receipt from stdin without creating a file",
    )
    parser.add_argument("--consumer-config", required=True, type=Path)
    parser.add_argument("--wa-ir-known-hosts", required=True, type=Path)
    parser.add_argument("--bootstrap-candidate", required=True)
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--expected-release-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _parser().parse_args(argv)
        # The returned control command has a live URL as its final argument.
        # A direct CLI cannot hand that capability to an executor without
        # serializing it, so only the in-process renderer API is usable.
        raise NormalStageRenderError(
            "direct CLI rendering of the URL-bearing WA-IR normal-stage control is disabled"
        )
    except NormalStageRenderError as exc:
        print(json.dumps({"status": "blocked", "error": "normal stage command was not rendered", "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    except Exception:
        print(json.dumps({"status": "blocked", "error": "normal stage command was not rendered"}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
