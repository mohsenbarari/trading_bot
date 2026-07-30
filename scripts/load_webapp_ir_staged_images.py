#!/usr/bin/env python3
"""Verify and, only when explicitly invoked, safely load staged WA-IR images.

This is deliberately separate from artifact staging and release-root
installation.  Its ``verify`` command is read-only.  Its ``load`` command is
for a later, separately authorised operation only: it rejects every shared or
noncanonical tag before invoking Docker, refuses an already-present target
tag, then proves every loaded tag resolves to the signed immutable image ID.

The archive and manifest must already be an exact private/versioned
``webapp_fi -> webapp_ir`` staged candidate.  This script has no Object
Storage, SSH, service, container, volume, routing, or ``current`` operation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


def _load_sibling(module_name: str) -> Any:
    module_path = Path(__file__).with_name(module_name + ".py")
    spec = importlib.util.spec_from_file_location("_wa_ir_image_loader_" + module_name, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preparer = _load_sibling("prepare_webapp_ir_artifact_bundle")
provenance = _load_sibling("manage_webapp_ir_release_provenance")
image_contract = _load_sibling("webapp_ir_image_archive_contract")


MAX_MANIFEST_BYTES = 1024 * 1024
DOCKER_TIMEOUT_SECONDS = 900
MISSING_IMAGE_MARKERS = ("No such image", "No such object")


class StagedImageLoadError(RuntimeError):
    """A staged image archive cannot safely be loaded on a shared host."""


CommandRunner = Callable[[Sequence[str], Path | None, int], subprocess.CompletedProcess[str]]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StagedImageLoadError("staged image manifest contains duplicate JSON keys")
        result[key] = value
    return result


def _read_private_json(path: Path, *, field: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise StagedImageLoadError(f"cannot inspect {field}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o077
        or metadata.st_size < 1
        or metadata.st_size > MAX_MANIFEST_BYTES
    ):
        raise StagedImageLoadError(f"{field} must be a root-only bounded regular file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise StagedImageLoadError(f"cannot read {field}") from exc
    if len(raw) != metadata.st_size:
        raise StagedImageLoadError(f"{field} changed while being read")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StagedImageLoadError(f"{field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise StagedImageLoadError(f"{field} must be a JSON object")
    return value


def _require_fields(value: Mapping[str, Any], *, expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise StagedImageLoadError(f"{field} has unsupported fields")


def _prepared_images_from_manifest(
    *,
    path: Path,
    expected_release_sha: str,
    expected_archive_sha256: str,
) -> tuple[str, list[Any]]:
    value = _read_private_json(path, field="staged image manifest")
    _require_fields(
        value,
        expected={"schema", "status", "campaign_id", "release_sha", "archive", "image_set_sha256", "images"},
        field="staged image manifest",
    )
    if value.get("schema") != provenance.IMAGE_MANIFEST_SCHEMA or value.get("status") != "prepared":
        raise StagedImageLoadError("staged image manifest schema is unsupported")
    campaign_id = value.get("campaign_id")
    release_sha = value.get("release_sha")
    try:
        campaign_id = image_contract.require_campaign_id(campaign_id)
        release_sha = image_contract.require_release_sha(release_sha)
    except image_contract.ImageArchiveContractError as exc:
        raise StagedImageLoadError("staged image manifest campaign or release is invalid") from exc
    if release_sha != expected_release_sha:
        raise StagedImageLoadError("staged image manifest release does not match its stage receipt")
    archive = value.get("archive")
    if not isinstance(archive, Mapping) or archive.get("sha256") != expected_archive_sha256:
        raise StagedImageLoadError("staged image manifest archive does not match its stage receipt")
    images_value = value.get("images")
    if not isinstance(images_value, list) or not images_value:
        raise StagedImageLoadError("staged image manifest images are invalid")
    result: list[Any] = []
    observed_ids: set[str] = set()
    observed_tags: set[str] = set()
    for item in images_value:
        if not isinstance(item, Mapping):
            raise StagedImageLoadError("staged image manifest image is invalid")
        _require_fields(
            item,
            expected={"archive_tag", "image_id", "repo_digests", "repo_tags", "size_bytes", "source_ref"},
            field="staged image manifest image",
        )
        image_id = item.get("image_id")
        archive_tag = item.get("archive_tag")
        try:
            image_id = image_contract.require_image_id(image_id)
            archive_tag = image_contract.require_canonical_archive_tag(
                archive_tag,
                campaign_id=campaign_id,
                release_sha=release_sha,
                image_id=image_id,
            )
        except image_contract.ImageArchiveContractError as exc:
            raise StagedImageLoadError("staged image manifest contains a shared or noncanonical image tag") from exc
        if image_id in observed_ids or archive_tag in observed_tags:
            raise StagedImageLoadError("staged image manifest contains duplicate image identities")
        source_ref = item.get("source_ref")
        repo_digests = item.get("repo_digests")
        repo_tags = item.get("repo_tags")
        size_bytes = item.get("size_bytes")
        if (
            not isinstance(source_ref, str)
            or not source_ref
            or not isinstance(repo_digests, list)
            or not all(isinstance(entry, str) for entry in repo_digests)
            or not isinstance(repo_tags, list)
            or not all(isinstance(entry, str) for entry in repo_tags)
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
        ):
            raise StagedImageLoadError("staged image manifest image metadata is invalid")
        observed_ids.add(image_id)
        observed_tags.add(archive_tag)
        result.append(
            preparer.PreparedImage(
                source_ref=source_ref,
                image_id=image_id,
                repo_digests=tuple(repo_digests),
                repo_tags=tuple(repo_tags),
                size_bytes=size_bytes,
                archive_tag=archive_tag,
            )
        )
    if result != sorted(result, key=lambda image: image.source_ref):
        raise StagedImageLoadError("staged image manifest images are not deterministically sorted")
    return campaign_id, result


def verify_staged_image_archive(*, stage_receipt_path: Path) -> dict[str, Any]:
    """Validate the signed stage and its load-safe Docker archive without Docker I/O."""

    try:
        stage, _verified_provenance = provenance.verify_staged_provenance(stage_receipt_path)
    except provenance.ReleaseProvenanceError as exc:
        raise StagedImageLoadError("staged release provenance is invalid") from exc
    try:
        bundle = stage.artifacts[provenance.IMAGE_BUNDLE_ARTIFACT]
        manifest = stage.artifacts[provenance.IMAGE_MANIFEST_ARTIFACT]
    except KeyError as exc:  # pragma: no cover - verified provenance enforces the exact set.
        raise StagedImageLoadError("staged release is missing an image artifact") from exc
    digest, bytes_value = preparer.sha256_file(bundle.path)
    if digest != bundle.sha256 or bytes_value != bundle.bytes:
        raise StagedImageLoadError("staged image archive changed after provenance verification")
    campaign_id, images = _prepared_images_from_manifest(
        path=manifest.path,
        expected_release_sha=stage.release_sha,
        expected_archive_sha256=bundle.sha256,
    )
    try:
        archive = preparer.verify_docker_image_archive(
            path=bundle.path,
            images=images,
            require_isolated_tags=True,
        )
    except preparer.ArtifactPreparationError as exc:
        raise StagedImageLoadError("staged Docker image archive is not safe for a shared host") from exc
    expected_tags = sorted(image.archive_tag for image in images)
    if archive["repo_tags"] != expected_tags:
        raise StagedImageLoadError("staged Docker image archive tag set is not receipt-bound")
    return {
        "archive_path": str(bundle.path),
        "archive_sha256": bundle.sha256,
        "bundle_id": stage.bundle_id,
        "campaign_id": campaign_id,
        "images": [
            {"archive_tag": image.archive_tag, "image_id": image.image_id}
            for image in images
        ],
        "release_sha": stage.release_sha,
        "status": "verified",
    }


def _inspect_tag(
    *,
    docker_binary: Path,
    tag: str,
    runner: CommandRunner,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            [str(docker_binary), "image", "inspect", "--format", "{{.Id}}", tag],
            None,
            DOCKER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StagedImageLoadError("Docker image inspection could not be started") from exc


def _require_tag_absent(*, docker_binary: Path, tag: str, runner: CommandRunner) -> None:
    result = _inspect_tag(docker_binary=docker_binary, tag=tag, runner=runner)
    if result.returncode == 0:
        raise StagedImageLoadError("refusing to overwrite an existing isolated Docker image tag")
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    if not any(marker in stderr for marker in MISSING_IMAGE_MARKERS):
        raise StagedImageLoadError("cannot prove the isolated Docker image tag is absent")


def _require_loaded_id(*, docker_binary: Path, tag: str, expected_id: str, runner: CommandRunner) -> None:
    result = _inspect_tag(docker_binary=docker_binary, tag=tag, runner=runner)
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise StagedImageLoadError("loaded isolated Docker image tag cannot be inspected")
    actual_id = result.stdout.strip()
    try:
        actual_id = image_contract.require_image_id(actual_id, field="loaded Docker image ID")
    except image_contract.ImageArchiveContractError as exc:
        raise StagedImageLoadError("loaded Docker image ID is invalid") from exc
    if actual_id != expected_id:
        raise StagedImageLoadError("loaded Docker image does not match its immutable staged image ID")


def load_verified_staged_images(
    *,
    stage_receipt_path: Path,
    docker_binary: Path = Path("/usr/bin/docker"),
    runner: CommandRunner = preparer.default_command_runner,
) -> dict[str, Any]:
    """Perform the future explicit load only after all no-overwrite checks pass."""

    verified = verify_staged_image_archive(stage_receipt_path=stage_receipt_path)
    docker_binary = preparer.require_trusted_executable(docker_binary, field="docker_binary")
    for item in verified["images"]:
        _require_tag_absent(docker_binary=docker_binary, tag=item["archive_tag"], runner=runner)
    try:
        result = runner(
            [str(docker_binary), "image", "load", "--input", verified["archive_path"]],
            None,
            DOCKER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StagedImageLoadError("Docker image load could not be started") from exc
    if result.returncode != 0:
        raise StagedImageLoadError("Docker image load failed")
    for item in verified["images"]:
        _require_loaded_id(
            docker_binary=docker_binary,
            tag=item["archive_tag"],
            expected_id=item["image_id"],
            runner=runner,
        )
    return {**verified, "status": "loaded"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("verify", "read-only verify a staged shared-host-safe image archive"),
        ("load", "future explicit Docker load after no-overwrite checks"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--stage-receipt", type=Path, required=True)
        command.add_argument("--docker-binary", type=Path, default=Path("/usr/bin/docker"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise StagedImageLoadError("this command must run as root")
        if args.command == "verify":
            result = verify_staged_image_archive(stage_receipt_path=args.stage_receipt)
        else:
            result = load_verified_staged_images(
                stage_receipt_path=args.stage_receipt,
                docker_binary=args.docker_binary,
            )
    except StagedImageLoadError as exc:
        print(json.dumps({"error": str(exc), "error_class": type(exc).__name__, "status": "blocked"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
