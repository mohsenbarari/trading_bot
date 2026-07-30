#!/usr/bin/env python3
"""Apply one bounded, Object-Storage-only WA-IR artifact-stage transaction.

This is intentionally a thin controller wrapper around the already reviewed
bootstrap publisher, normal publisher, and the two controller-local renderers.
It does not prepare source artifacts, install release roots, load images, or
operate a service.  Unless ``--apply`` is supplied it performs no host, SSH,
or Object Storage operation at all.

When applied, this wrapper permits one irreversible sequence only:

1. publish one encrypted bootstrap package and immediately deliver its
   version-bound URL as an SSH control argument;
2. publish exactly five encrypted normal artifacts and one encrypted signed
   manifest; and
3. immediately deliver only the manifest URL as a second SSH control argument.

The URL-bearing publisher receipts and rendered SSH commands are held solely
in process memory.  They are never written or printed.  SSH is invoked as an
argument vector with ``shell=False`` and all remote stdout/stderr is discarded.
If a step fails, this wrapper stops and deliberately makes no retry attempt:
the operator must inspect the immutable versions already created before any
new authorization or later attempt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


def _load_sibling_module(name: str) -> Any:
    """Load an adjacent control module without relying on an ambient PATH."""

    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stage = _load_sibling_module("manage_webapp_ir_artifact_stage")
bootstrap_renderer = _load_sibling_module("render_webapp_ir_stage_bootstrap_receive")
normal_renderer = _load_sibling_module("render_webapp_ir_stage_consume")


EXPECTED_APPLICATION_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
SOURCE_SITE = "webapp_fi"
DESTINATION_SITE = "webapp_ir"
BOOTSTRAP_ROOT = "/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap"
STAGING_ROOT = "/srv/trading-bot-three-site-staging-data/wa-ir-standby/artifact-stage"
SSH_TIMEOUT_SECONDS = 900
EXPECTED_NORMAL_ARTIFACTS = (
    "control-release-bundle",
    "image-bundle",
    "image-manifest",
    "release-bundle",
    "release-provenance",
)


class SevenObjectStageError(RuntimeError):
    """The bounded controller operation cannot continue safely."""

    def __init__(self, message: str, *, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence = dict(evidence) if evidence is not None else None


SshRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[Any]]


def _redact_url_bearing_value(value: object) -> object:
    """Defend the terminal boundary even if a future caller attaches evidence."""

    if isinstance(value, Mapping):
        return {
            str(key): _redact_url_bearing_value(item)
            for key, item in value.items()
            if "url" not in str(key).lower()
        }
    if isinstance(value, list):
        return [_redact_url_bearing_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_url_bearing_value(item) for item in value]
    if isinstance(value, str) and ("https://" in value.lower() or "http://" in value.lower()):
        return "[redacted]"
    return value


def _require_root() -> None:
    if os.geteuid() != 0:
        raise SevenObjectStageError("the seven-object stage controller must run as root")


def _require_mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SevenObjectStageError(f"{field} is malformed")
    return value


def _require_exact_fields(value: Mapping[str, Any], *, expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise SevenObjectStageError(f"{field} has an unsupported schema")


def _require_text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise SevenObjectStageError(f"{field} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise SevenObjectStageError(f"{field} is invalid")
    return value


def _require_positive_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SevenObjectStageError(f"{field} is invalid")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    text = _require_text(value, field=field, maximum=64)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise SevenObjectStageError(f"{field} is invalid")
    return text


def _require_git_sha40(value: object, *, field: str) -> str:
    text = _require_text(value, field=field, maximum=40)
    if len(text) != 40 or any(character not in "0123456789abcdef" for character in text):
        raise SevenObjectStageError(f"{field} is invalid")
    return text


def _require_utc_timestamp(value: object, *, field: str) -> str:
    text = _require_text(value, field=field, maximum=64)
    if not text.endswith("Z"):
        raise SevenObjectStageError(f"{field} is invalid")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SevenObjectStageError(f"{field} is invalid") from None
    if parsed.tzinfo is None:
        raise SevenObjectStageError(f"{field} is invalid")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _require_url(value: object, *, field: str) -> str:
    """Accept a transient URL only long enough for a reviewed renderer to bind it."""

    return _require_text(value, field=field, maximum=8192)


def _validate_bootstrap_publish_receipt(value: object) -> dict[str, Any]:
    """Validate the one-object portion without exposing its transient URL."""

    receipt = dict(_require_mapping(value, field="bootstrap publish receipt"))
    _require_exact_fields(
        receipt,
        expected={
            "schema",
            "status",
            "source_site",
            "destination_site",
            "control_commit",
            "control_tree",
            "bootstrap_id",
            "published_at",
            "bootstrap",
        },
        field="bootstrap publish receipt",
    )
    if (
        receipt.get("schema") != stage.BOOTSTRAP_PUBLISH_RECEIPT_SCHEMA
        or receipt.get("status") != "published"
        or receipt.get("source_site") != SOURCE_SITE
        or receipt.get("destination_site") != DESTINATION_SITE
    ):
        raise SevenObjectStageError("bootstrap publish receipt is not for the fixed WA-IR stage")
    control_commit = _require_git_sha40(receipt.get("control_commit"), field="bootstrap control commit")
    control_tree = _require_git_sha40(receipt.get("control_tree"), field="bootstrap control tree")
    bootstrap_id = _require_text(receipt.get("bootstrap_id"), field="bootstrap ID", maximum=128)
    if not stage.BUNDLE_ID_RE.fullmatch(bootstrap_id):
        raise SevenObjectStageError("bootstrap ID is invalid")
    _require_utc_timestamp(receipt.get("published_at"), field="bootstrap published timestamp")
    bootstrap = dict(_require_mapping(receipt.get("bootstrap"), field="bootstrap object descriptor"))
    _require_exact_fields(
        bootstrap,
        expected={
            "object_key",
            "version_id",
            "ciphertext_sha256",
            "ciphertext_bytes",
            "plaintext_sha256",
            "plaintext_bytes",
            "manifest_sha256",
            "preparation_receipt_sha256",
            "presigned_url",
        },
        field="bootstrap object descriptor",
    )
    _require_sha256(bootstrap.get("plaintext_sha256"), field="bootstrap plaintext SHA-256")
    _require_positive_integer(bootstrap.get("plaintext_bytes"), field="bootstrap plaintext bytes")
    _require_sha256(bootstrap.get("manifest_sha256"), field="bootstrap manifest SHA-256")
    _require_sha256(bootstrap.get("preparation_receipt_sha256"), field="bootstrap preparation receipt SHA-256")
    return {
        "control_commit": control_commit,
        "control_tree": control_tree,
        "bootstrap_id": bootstrap_id,
        "object_key": _require_text(bootstrap.get("object_key"), field="bootstrap object key", maximum=1024),
        "version_id": _require_text(bootstrap.get("version_id"), field="bootstrap version ID", maximum=1024),
        "ciphertext_sha256": _require_sha256(bootstrap.get("ciphertext_sha256"), field="bootstrap ciphertext SHA-256"),
        "ciphertext_bytes": _require_positive_integer(bootstrap.get("ciphertext_bytes"), field="bootstrap ciphertext bytes"),
        "presigned_url": _require_url(bootstrap.get("presigned_url"), field="bootstrap presigned URL"),
    }


def _validate_normal_publish_receipt(value: object, *, release_sha: str) -> dict[str, Any]:
    """Validate the five-artifact plus manifest result before SSH is possible."""

    receipt = dict(_require_mapping(value, field="normal publish receipt"))
    _require_exact_fields(
        receipt,
        expected={
            "schema",
            "status",
            "source_site",
            "destination_site",
            "release_sha",
            "bundle_id",
            "published_at",
            "artifacts",
            "manifest",
        },
        field="normal publish receipt",
    )
    if (
        receipt.get("schema") != stage.PUBLISH_RECEIPT_SCHEMA
        or receipt.get("status") != "published"
        or receipt.get("source_site") != SOURCE_SITE
        or receipt.get("destination_site") != DESTINATION_SITE
        or receipt.get("release_sha") != release_sha
    ):
        raise SevenObjectStageError("normal publish receipt is not for the fixed WA-IR stage")
    bundle_id = _require_text(receipt.get("bundle_id"), field="normal bundle ID", maximum=128)
    if not stage.BUNDLE_ID_RE.fullmatch(bundle_id):
        raise SevenObjectStageError("normal bundle ID is invalid")
    _require_utc_timestamp(receipt.get("published_at"), field="normal published timestamp")
    raw_artifacts = receipt.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(EXPECTED_NORMAL_ARTIFACTS):
        raise SevenObjectStageError("normal publish receipt does not contain exactly five artifacts")
    artifacts: list[dict[str, Any]] = []
    for raw in raw_artifacts:
        descriptor = dict(_require_mapping(raw, field="normal artifact descriptor"))
        _require_exact_fields(
            descriptor,
            expected={
                "name",
                "sha256",
                "bytes",
                "object_key",
                "version_id",
                "ciphertext_sha256",
                "ciphertext_bytes",
                "bindings",
            },
            field="normal artifact descriptor",
        )
        _require_sha256(descriptor.get("sha256"), field="normal artifact plaintext SHA-256")
        _require_positive_integer(descriptor.get("bytes"), field="normal artifact plaintext bytes")
        try:
            stage.normalize_artifact_bindings(descriptor.get("bindings"), field="normal artifact bindings")
        except stage.ArtifactStageError:
            raise SevenObjectStageError("normal artifact bindings are invalid") from None
        artifacts.append(
            {
                "name": _require_text(descriptor.get("name"), field="normal artifact name", maximum=64),
                "object_key": _require_text(descriptor.get("object_key"), field="normal artifact object key", maximum=1024),
                "version_id": _require_text(descriptor.get("version_id"), field="normal artifact version ID", maximum=1024),
                "ciphertext_sha256": _require_sha256(
                    descriptor.get("ciphertext_sha256"), field="normal artifact ciphertext SHA-256"
                ),
                "ciphertext_bytes": _require_positive_integer(
                    descriptor.get("ciphertext_bytes"), field="normal artifact ciphertext bytes"
                ),
            }
        )
    names = tuple(item["name"] for item in artifacts)
    if names != EXPECTED_NORMAL_ARTIFACTS:
        raise SevenObjectStageError("normal publish receipt artifact order or set is invalid")
    manifest = dict(_require_mapping(receipt.get("manifest"), field="normal manifest descriptor"))
    _require_exact_fields(
        manifest,
        expected={"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "presigned_url"},
        field="normal manifest descriptor",
    )
    return {
        "bundle_id": bundle_id,
        "artifacts": artifacts,
        "manifest": {
            "object_key": _require_text(manifest.get("object_key"), field="normal manifest object key", maximum=1024),
            "version_id": _require_text(manifest.get("version_id"), field="normal manifest version ID", maximum=1024),
            "ciphertext_sha256": _require_sha256(
                manifest.get("ciphertext_sha256"), field="normal manifest ciphertext SHA-256"
            ),
            "ciphertext_bytes": _require_positive_integer(
                manifest.get("ciphertext_bytes"), field="normal manifest ciphertext bytes"
            ),
            "presigned_url": _require_url(manifest.get("presigned_url"), field="normal manifest presigned URL"),
        },
    }


def _parse_exact_normal_artifacts(
    *,
    artifact_specs: Sequence[str],
    binding_specs: Sequence[str],
) -> list[Any]:
    """Build only the five pre-approved normal inputs before any object write."""

    try:
        parsed = stage.parse_artifact_specifications(artifact_specs)
        artifacts = stage.apply_artifact_bindings(parsed, binding_specs)
    except stage.ArtifactStageError as exc:
        raise SevenObjectStageError("normal artifact input is invalid") from exc
    names = tuple(item.name for item in artifacts)
    if names != EXPECTED_NORMAL_ARTIFACTS:
        raise SevenObjectStageError("exactly the five approved normal artifacts are required")
    by_name = {item.name: item for item in artifacts}
    for name in ("release-bundle", "image-bundle", "image-manifest", "control-release-bundle"):
        if by_name[name].bindings.get("release_sha") != EXPECTED_APPLICATION_RELEASE_SHA:
            raise SevenObjectStageError("normal artifact release binding is invalid")
    if by_name["release-provenance"].bindings.get("application_release_sha") != EXPECTED_APPLICATION_RELEASE_SHA:
        raise SevenObjectStageError("release provenance application binding is invalid")
    for name in ("control-release-bundle", "release-provenance"):
        control_release_sha = by_name[name].bindings.get("control_release_sha")
        if not isinstance(control_release_sha, str) or not stage.RELEASE_SHA_RE.fullmatch(control_release_sha):
            raise SevenObjectStageError("normal artifact control binding is invalid")
    return artifacts


def _require_bootstrap_control_binding(artifacts: Sequence[Any], *, bootstrap_control_commit: str) -> None:
    """Bind the already preflighted normal inputs to the published bootstrap."""

    by_name = {item.name: item for item in artifacts}
    for name in ("control-release-bundle", "release-provenance"):
        if by_name[name].bindings.get("control_release_sha") != bootstrap_control_commit:
            raise SevenObjectStageError("normal artifact control binding does not match bootstrap control")


def _serialize_transient_receipt(receipt: Mapping[str, Any]) -> bytes:
    """Renderers accept bytes; no URL-bearing receipt file is ever created."""

    return json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _ssh_argv(rendered_command: str) -> list[str]:
    """Turn a reviewed renderer result into argv without handing it to a shell."""

    try:
        argv = shlex.split(rendered_command, posix=True)
    except ValueError as exc:
        raise SevenObjectStageError("renderer returned an unusable SSH command") from exc
    expected_prefix = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
    if len(argv) != len(expected_prefix) + 2 or argv[: len(expected_prefix)] != expected_prefix:
        raise SevenObjectStageError("renderer returned an unexpected SSH command")
    if argv[len(expected_prefix)] != bootstrap_renderer.REMOTE_HOST or not argv[-1]:
        raise SevenObjectStageError("renderer returned an unexpected SSH destination")
    return argv


def _run_ssh_silently(arguments: Sequence[str]) -> subprocess.CompletedProcess[Any]:
    """Run the transient remote command without a shell or URL-bearing output."""

    try:
        return subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=SSH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        raise SevenObjectStageError("WA-IR SSH control command could not be started") from None


def _require_successful_ssh(result: subprocess.CompletedProcess[Any]) -> None:
    if not isinstance(result, subprocess.CompletedProcess) or result.returncode != 0:
        raise SevenObjectStageError("WA-IR SSH control command failed")


def _execute_rendered_ssh(rendered_command: str, *, ssh_runner: SshRunner) -> None:
    """Execute a reviewed render result while suppressing all transient output."""

    try:
        result = ssh_runner(_ssh_argv(rendered_command))
    except Exception:
        raise SevenObjectStageError("WA-IR SSH control command could not be started") from None
    _require_successful_ssh(result)


def _public_evidence(
    *,
    bootstrap: Mapping[str, Any],
    normal: Mapping[str, Any],
) -> dict[str, Any]:
    """Return only non-URL evidence suitable for a terminal or audit record."""

    return {
        "status": "staged",
        "release_sha": EXPECTED_APPLICATION_RELEASE_SHA,
        "object_count": 7,
        "bootstrap": {
            key: bootstrap[key]
            for key in ("object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes")
        },
        "artifacts": [
            {
                key: artifact[key]
                for key in ("name", "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes")
            }
            for artifact in normal["artifacts"]
        ],
        "manifest": {
            key: normal["manifest"][key]
            for key in ("object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes")
        },
    }


def _bootstrap_only_evidence(bootstrap: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve one verified immutable version without retaining its URL."""

    return {
        "object_count": 1,
        "bootstrap": {
            key: bootstrap[key]
            for key in ("object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes")
        },
    }


def run_stage(
    *,
    publisher_config_path: Path,
    consumer_config_path: Path,
    bootstrap_package_directory: Path,
    bootstrap_preparation_receipt: Path,
    artifact_specs: Sequence[str],
    binding_specs: Sequence[str],
    ssh_runner: SshRunner = _run_ssh_silently,
) -> dict[str, Any]:
    """Run exactly one non-retrying seven-object stage sequence.

    The supplied configuration and all prepared artifacts must already exist.
    This function never writes either publisher receipt; both are passed to the
    corresponding renderer from memory only.
    """

    _require_root()
    try:
        publisher_config = stage.load_publisher_config(publisher_config_path)
    except Exception:
        raise SevenObjectStageError("publisher configuration is invalid") from None
    if publisher_config.source_site != SOURCE_SITE:
        raise SevenObjectStageError("publisher configuration source site is not webapp_fi")
    # Read and validate the local, non-secret source of the packaged consumer
    # configuration before the first external write.  The renderers enforce
    # the remaining exact archive and URL bindings.
    try:
        consumer_config = stage.load_consumer_config(consumer_config_path)
    except Exception:
        raise SevenObjectStageError("consumer configuration is invalid") from None
    if (
        consumer_config.source_site != SOURCE_SITE
        or consumer_config.endpoint != publisher_config.endpoint
        or consumer_config.region != publisher_config.region
        or consumer_config.bucket != publisher_config.bucket
        or consumer_config.prefix != publisher_config.prefix
    ):
        raise SevenObjectStageError("publisher and consumer transport configurations do not match")
    try:
        publisher_public_key = stage._publisher_public_key(publisher_config)
    except Exception:
        raise SevenObjectStageError("publisher signing key is invalid") from None
    if publisher_public_key != consumer_config.source_signing_public_key:
        raise SevenObjectStageError("publisher signing key does not match the pinned consumer key")

    # Fail before contacting Object Storage when the input could never satisfy
    # the fixed 1 + 5 + manifest contract.
    artifacts = _parse_exact_normal_artifacts(
        artifact_specs=artifact_specs,
        binding_specs=binding_specs,
    )

    try:
        client = stage.create_s3_client(publisher_config)
        raw_bootstrap_receipt = stage.publish_bootstrap_package(
            client,
            config=publisher_config,
            bootstrap_package_directory=bootstrap_package_directory,
            bootstrap_preparation_receipt=bootstrap_preparation_receipt,
        )
    except Exception:
        # Do not retry: a failed call could already have created one immutable object.
        raise SevenObjectStageError("bootstrap publish did not complete") from None
    bootstrap = _validate_bootstrap_publish_receipt(raw_bootstrap_receipt)
    try:
        bootstrap_command = bootstrap_renderer.render_receive_command(
            publish_receipt_bytes=_serialize_transient_receipt(_require_mapping(raw_bootstrap_receipt, field="bootstrap receipt")),
            bootstrap_package_directory=bootstrap_package_directory,
            preparation_receipt=bootstrap_preparation_receipt,
            bootstrap_root=BOOTSTRAP_ROOT,
        )
    except Exception:
        raise SevenObjectStageError(
            "bootstrap receive command could not be rendered",
            evidence=_bootstrap_only_evidence(bootstrap),
        ) from None
    try:
        _execute_rendered_ssh(bootstrap_command, ssh_runner=ssh_runner)
        _require_bootstrap_control_binding(artifacts, bootstrap_control_commit=bootstrap["control_commit"])
    except SevenObjectStageError:
        raise SevenObjectStageError(
            "bootstrap receipt is available but the stage cannot continue",
            evidence=_bootstrap_only_evidence(bootstrap),
        ) from None
    try:
        raw_normal_receipt = stage.publish_bundle(
            client,
            config=publisher_config,
            destination_site=DESTINATION_SITE,
            release_sha=EXPECTED_APPLICATION_RELEASE_SHA,
            artifacts=artifacts,
        )
    except Exception:
        # Do not retry: a failed call could already have created a subset of immutable objects.
        raise SevenObjectStageError(
            "normal artifact publish did not complete",
            evidence=_bootstrap_only_evidence(bootstrap),
        ) from None
    try:
        normal = _validate_normal_publish_receipt(raw_normal_receipt, release_sha=EXPECTED_APPLICATION_RELEASE_SHA)
    except SevenObjectStageError:
        raise SevenObjectStageError(
            "normal publish receipt cannot be accepted",
            evidence=_bootstrap_only_evidence(bootstrap),
        ) from None
    bootstrap_candidate = "/".join((BOOTSTRAP_ROOT, "received-" + bootstrap["control_commit"] + "-" + bootstrap["bootstrap_id"]))
    try:
        normal_command = normal_renderer.render_consume_command(
            publish_receipt_bytes=_serialize_transient_receipt(_require_mapping(raw_normal_receipt, field="normal receipt")),
            consumer_config=consumer_config_path,
            bootstrap_candidate=bootstrap_candidate,
            staging_root=STAGING_ROOT,
            expected_release_sha=EXPECTED_APPLICATION_RELEASE_SHA,
        )
    except Exception:
        raise SevenObjectStageError(
            "normal stage command could not be rendered",
            evidence=_public_evidence(bootstrap=bootstrap, normal=normal),
        ) from None
    try:
        _execute_rendered_ssh(normal_command, ssh_runner=ssh_runner)
    except SevenObjectStageError:
        raise SevenObjectStageError(
            "normal receipt is available but the stage cannot continue",
            evidence=_public_evidence(bootstrap=bootstrap, normal=normal),
        ) from None
    return _public_evidence(bootstrap=bootstrap, normal=normal)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the one non-retrying seven-object operation")
    parser.add_argument("--publisher-config", type=Path)
    parser.add_argument("--consumer-config", type=Path)
    parser.add_argument("--bootstrap-package-directory", type=Path)
    parser.add_argument("--bootstrap-preparation-receipt", type=Path)
    parser.add_argument("--artifact", action="append", default=[], metavar="NAME=ABSOLUTE_PATH")
    parser.add_argument("--artifact-binding", action="append", default=[], metavar="NAME=KEY=VALUE")
    return parser


def _apply_arguments(arguments: argparse.Namespace) -> dict[str, Any]:
    required = (
        "publisher_config",
        "consumer_config",
        "bootstrap_package_directory",
        "bootstrap_preparation_receipt",
    )
    if any(getattr(arguments, field) is None for field in required):
        raise SevenObjectStageError("one or more required apply inputs are absent")
    return {
        "publisher_config_path": arguments.publisher_config,
        "consumer_config_path": arguments.consumer_config,
        "bootstrap_package_directory": arguments.bootstrap_package_directory,
        "bootstrap_preparation_receipt": arguments.bootstrap_preparation_receipt,
        "artifact_specs": arguments.artifact,
        "binding_specs": arguments.artifact_binding,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.apply:
        print(json.dumps({"status": "not_applied", "object_count": 0}, sort_keys=True))
        return 0
    try:
        evidence = run_stage(**_apply_arguments(arguments))
    except SevenObjectStageError as exc:
        payload: dict[str, Any] = {
            "status": "blocked",
            "error": "WA-IR seven-object stage stopped without a retry; inspect immutable evidence before another attempt",
            "error_class": "SevenObjectStageError",
        }
        if exc.evidence is not None:
            payload["evidence"] = _redact_url_bearing_value(exc.evidence)
        print(json.dumps(payload, sort_keys=True))
        return 2
    except Exception:
        # Never serialize exception text: a lower layer may have handled a
        # transient URL-bearing receipt or rendered SSH command.
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "WA-IR seven-object stage stopped without a retry; inspect immutable evidence before another attempt",
                    "error_class": "SevenObjectStageError",
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
