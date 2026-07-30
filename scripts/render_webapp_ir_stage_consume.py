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
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlparse


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


REMOTE_HOST = "root@95.38.164.29"
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes")
SOURCE_SITE = "webapp_fi"
DESTINATION_SITE = "webapp_ir"
WA_IR_BOOTSTRAP_IDENTITY_FILE = "/etc/trading-bot-three-site/wa-ir/artifact-stage-2c08.agekey"
WA_IR_BOOTSTRAP_ROOT = "/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap"
WA_IR_STAGING_ROOT = "/srv/trading-bot-three-site-staging-data/wa-ir-standby/artifact-stage"
REMOTE_CONSUMER_SCRIPT = "scripts/manage_webapp_ir_artifact_stage.py"
REMOTE_CONSUMER_CONFIG = "config/consumer.json"
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_URL_BYTES = 8192
MAX_MANIFEST_CIPHERTEXT_BYTES = 1024 * 1024
AGE_CIPHERTEXT_MARGIN_BYTES = 1024 * 1024
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
        "version_id": _require_text(
            artifact.get("version_id"),
            field=f"normal stage publish receipt {name} version_id",
            maximum=1024,
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
    if str(config.age_identity_file) != WA_IR_BOOTSTRAP_IDENTITY_FILE:
        raise NormalStageRenderError("normal stage consumer config does not pin the WA-IR bootstrap identity")
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
        query = parse_qs(urlparse(url).query, keep_blank_values=True, strict_parsing=True)
    except (ValueError, stage.ArtifactStageError) as exc:
        raise NormalStageRenderError("normal stage manifest URL is not safely bound") from exc
    sigv4_fields = ("X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Signature")
    sigv2_fields = ("AWSAccessKeyId", "Signature", "Expires")
    sigv4 = all(len(query.get(name, [])) == 1 and bool(query[name][0]) for name in sigv4_fields)
    sigv2 = all(len(query.get(name, [])) == 1 and bool(query[name][0]) for name in sigv2_fields)
    if sigv4 == sigv2:
        raise NormalStageRenderError("normal stage manifest URL must contain exactly one signed-request envelope")
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
    manifest_version_id = _require_text(
        manifest.get("version_id"),
        field="normal stage manifest version_id",
        maximum=1024,
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
    remote = shlex.join(remote_argv)
    return shlex.join(["ssh", *SSH_OPTIONS, REMOTE_HOST, remote])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish-receipt-stdin",
        action="store_true",
        required=True,
        help="read one just-published normal stage receipt from stdin without creating a file",
    )
    parser.add_argument("--consumer-config", required=True, type=Path)
    parser.add_argument("--bootstrap-candidate", required=True)
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--expected-release-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(
            render_consume_command(
                publish_receipt_bytes=_read_publish_receipt_stdin(),
                consumer_config=args.consumer_config,
                bootstrap_candidate=args.bootstrap_candidate,
                staging_root=args.staging_root,
                expected_release_sha=args.expected_release_sha,
            )
        )
    except NormalStageRenderError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
