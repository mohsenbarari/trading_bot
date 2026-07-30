#!/usr/bin/env python3
"""Compose one fresh WA-IR preparation from verified local immutable inputs.

The controller adoption path produces a final isolated Docker image bundle and
its manifest, but the existing provenance builder also requires an application
preparation receipt that binds those images to the immutable application Git
bundle.  This local-only primitive fills that narrow format gap.

It copies the already-verified application ``release.bundle`` from an existing
preparation and the already-adopted ``images.tar`` and ``image-manifest.json``
into a fresh root-only candidate.  It never contacts Object Storage, SSH,
Docker, age, or a service.  The original preparation and all input files remain
unchanged.  A receipt is written last and is immediately accepted by the same
``_preparation_receipt`` verifier used by the later control-artifact builder.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


def _load_sibling_module(name: str) -> Any:
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("_adopted_preparation_" + name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError("cannot load required sibling module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preparer = _load_sibling_module("prepare_webapp_ir_artifact_bundle")
provenance = _load_sibling_module("manage_webapp_ir_release_provenance")


IMAGE_BUNDLE_NAME = "images.tar"
IMAGE_MANIFEST_NAME = "image-manifest.json"
RELEASE_BUNDLE_NAME = "release.bundle"
PREPARATION_RECEIPT_NAME = "preparation-receipt.json"
MAXIMUM_ARTIFACT_BYTES = min(preparer.MAXIMUM_ARTIFACT_BYTES, provenance.MAX_ARTIFACT_BYTES)
MAXIMUM_JSON_BYTES = min(preparer.MAX_DOCKER_ARCHIVE_MANIFEST_BYTES, provenance.MAX_JSON_BYTES)
RECEIPT_RESERVE_BYTES = provenance.MAX_JSON_BYTES
REQUIRED_ADOPTED_IMAGE_COUNT = 3


class AdoptedPreparationCompositionError(RuntimeError):
    """A fresh adopted-image preparation cannot be safely composed."""


@dataclass(frozen=True)
class InputFile:
    path: Path
    sha256: str
    bytes: int


DiskUsage = Callable[[Path], Any]


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise AdoptedPreparationCompositionError("adopted preparation composition must run as root")


def _require_root_only_directory(path: Path, *, field: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise AdoptedPreparationCompositionError(f"{field} must be an absolute path")
    try:
        resolved = preparer.require_root_only_directory(path, field=field)
    except Exception as exc:
        raise AdoptedPreparationCompositionError(f"{field} must be a root-only non-symlink directory") from exc
    if resolved != path:
        raise AdoptedPreparationCompositionError(f"{field} must be canonical without symlink ancestors")
    current = resolved
    while True:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise AdoptedPreparationCompositionError(f"cannot inspect {field} ancestor") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
        ):
            raise AdoptedPreparationCompositionError(f"{field} has an unsafe ancestor")
        # A root-owned sticky directory such as /tmp can retain a root-owned
        # child safely: an unprivileged user cannot rename or remove that
        # child.  Any other writable ancestor could replace the checked path.
        if stat.S_IMODE(metadata.st_mode) & 0o022 and not metadata.st_mode & stat.S_ISVTX:
            raise AdoptedPreparationCompositionError(f"{field} has a writable non-sticky ancestor")
        if current.parent == current:
            break
        current = current.parent
    return resolved


def _require_private_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise AdoptedPreparationCompositionError(f"{field} must be an absolute path")
    _require_root_only_directory(path.parent, field=f"{field} parent")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise AdoptedPreparationCompositionError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o077
        or resolved_metadata.st_nlink != 1
        or not 1 <= resolved_metadata.st_size <= maximum_bytes
    ):
        raise AdoptedPreparationCompositionError(f"{field} must be a bounded root-only non-symlink file")
    return resolved


def _canonical_private_json(path: Path, *, field: str) -> tuple[dict[str, Any], InputFile]:
    path = _require_private_file(path, field=field, maximum_bytes=MAXIMUM_JSON_BYTES)
    before = path.lstat()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AdoptedPreparationCompositionError(f"cannot read {field}") from exc
    try:
        after = path.lstat()
    except OSError as exc:
        raise AdoptedPreparationCompositionError(f"cannot recheck {field}") from exc
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or len(raw) != before.st_size
    ):
        raise AdoptedPreparationCompositionError(f"{field} changed while being read")
    try:
        value = preparer._strict_json_loads(raw, field=field)
    except Exception as exc:
        raise AdoptedPreparationCompositionError(f"{field} is not strict JSON") from exc
    if not isinstance(value, dict) or raw != preparer.canonical_json_bytes(value) + b"\n":
        raise AdoptedPreparationCompositionError(f"{field} must use canonical JSON")
    return value, InputFile(path=path, sha256=preparer.sha256_bytes(raw), bytes=len(raw))


def _require_exact_file(input_file: InputFile, *, field: str, maximum_bytes: int) -> None:
    path = _require_private_file(input_file.path, field=field, maximum_bytes=maximum_bytes)
    try:
        digest, bytes_value = preparer.sha256_file(path)
    except OSError as exc:
        raise AdoptedPreparationCompositionError(f"cannot hash {field}") from exc
    if (digest, bytes_value) != (input_file.sha256, input_file.bytes):
        raise AdoptedPreparationCompositionError(f"{field} changed from its verified input binding")


def _copy_exact_file(
    *,
    source: InputFile,
    destination: Path,
    field: str,
    maximum_bytes: int,
) -> InputFile:
    """Copy one exact private file to a create-only destination while hashing it."""

    source_path = _require_private_file(source.path, field=field, maximum_bytes=maximum_bytes)
    _require_root_only_directory(destination.parent, field=f"{field} destination parent")
    if destination.exists() or destination.is_symlink():
        raise AdoptedPreparationCompositionError(f"refusing to overwrite {field} destination")
    before = source_path.lstat()
    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        source_descriptor = os.open(source_path, source_flags)
    except OSError as exc:
        raise AdoptedPreparationCompositionError(f"cannot securely open {field}") from exc
    destination_descriptor: int | None = None
    digest = hashlib.sha256()
    bytes_value = 0
    try:
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_nlink != 1
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise AdoptedPreparationCompositionError(f"{field} changed while being opened")
        destination_descriptor = os.open(destination, destination_flags, 0o600)
        os.fchmod(destination_descriptor, 0o600)
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            bytes_value += len(chunk)
            if bytes_value > maximum_bytes:
                raise AdoptedPreparationCompositionError(f"{field} exceeds its size bound while copying")
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:  # pragma: no cover - os.write cannot normally return zero here.
                    raise OSError("short destination write")
                view = view[written:]
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_nlink != opened.st_nlink
        ):
            raise AdoptedPreparationCompositionError(f"{field} changed while being copied")
    except OSError as exc:
        raise AdoptedPreparationCompositionError(f"cannot copy {field}") from exc
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        os.close(source_descriptor)
    observed = (digest.hexdigest(), bytes_value)
    if observed != (source.sha256, source.bytes):
        raise AdoptedPreparationCompositionError(f"{field} changed from its verified input binding")
    destination = _require_private_file(destination, field=f"{field} destination", maximum_bytes=maximum_bytes)
    try:
        destination_digest, destination_bytes = preparer.sha256_file(destination)
    except OSError as exc:
        raise AdoptedPreparationCompositionError(f"cannot hash copied {field}") from exc
    if (destination_digest, destination_bytes) != observed:
        raise AdoptedPreparationCompositionError(f"copied {field} does not match its verified input binding")
    return InputFile(path=destination, sha256=destination_digest, bytes=destination_bytes)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise AdoptedPreparationCompositionError("cannot synchronize adopted preparation directory") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise AdoptedPreparationCompositionError("cannot synchronize adopted preparation directory") from exc
    finally:
        os.close(descriptor)


def _prepared_images(images: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        preparer.PreparedImage(
            source_ref=str(image["source_ref"]),
            image_id=str(image["image_id"]),
            repo_digests=tuple(image["repo_digests"]),
            repo_tags=tuple(image["repo_tags"]),
            size_bytes=int(image["size_bytes"]),
            archive_tag=str(image["archive_tag"]),
        )
        for image in images
    )


def _load_adopted_inputs(
    *,
    image_bundle: Path,
    image_manifest: Path,
    campaign_id: str,
    release_sha: str,
    maximum_artifact_bytes: int,
) -> tuple[InputFile, InputFile, tuple[dict[str, Any], ...], dict[str, Any]]:
    value, manifest_input = _canonical_private_json(image_manifest, field="adopted image manifest")
    expected_fields = {"schema", "status", "campaign_id", "release_sha", "archive", "image_set_sha256", "images"}
    if (
        set(value) != expected_fields
        or value.get("schema") != preparer.IMAGE_MANIFEST_SCHEMA
        or value.get("status") != "prepared"
        or value.get("campaign_id") != campaign_id
        or value.get("release_sha") != release_sha
    ):
        raise AdoptedPreparationCompositionError("adopted image manifest is not bound to the application preparation")
    try:
        images = provenance._image_values(value.get("images"), field="adopted image manifest images")
        provenance._validate_isolated_archive_tags(
            images,
            campaign_id=campaign_id,
            release_sha=release_sha,
            field="adopted image manifest",
        )
    except Exception as exc:
        raise AdoptedPreparationCompositionError("adopted image manifest images are invalid") from exc
    if len(images) != REQUIRED_ADOPTED_IMAGE_COUNT:
        raise AdoptedPreparationCompositionError("adopted image manifest must contain the operational three-image set")
    if any(int(image["size_bytes"]) > maximum_artifact_bytes for image in images):
        raise AdoptedPreparationCompositionError("adopted image manifest image size exceeds the configured bound")
    archive_value = value.get("archive")
    if not isinstance(archive_value, Mapping):
        raise AdoptedPreparationCompositionError("adopted image manifest archive is invalid")
    archive = dict(archive_value)
    try:
        provenance._fields(
            archive,
            expected={"bytes", "sha256", "image_ids", "repo_tags"},
            field="adopted image manifest archive",
        )
        archive_sha256 = provenance._require_sha256(archive.get("sha256"), field="adopted image archive SHA-256")
    except Exception as exc:
        raise AdoptedPreparationCompositionError("adopted image manifest archive is invalid") from exc
    archive_bytes = archive.get("bytes")
    if isinstance(archive_bytes, bool) or not isinstance(archive_bytes, int) or not 1 <= archive_bytes <= maximum_artifact_bytes:
        raise AdoptedPreparationCompositionError("adopted image manifest archive byte count is invalid")
    expected_ids = sorted(str(image["image_id"]) for image in images)
    expected_tags = sorted(str(image["archive_tag"]) for image in images)
    if archive.get("image_ids") != expected_ids or archive.get("repo_tags") != expected_tags:
        raise AdoptedPreparationCompositionError("adopted image manifest archive does not bind all isolated images")
    if value.get("image_set_sha256") != preparer.sha256_bytes(preparer.canonical_json_bytes(list(images))):
        raise AdoptedPreparationCompositionError("adopted image manifest image set hash is invalid")
    bundle_path = _require_private_file(
        image_bundle,
        field="adopted image bundle",
        maximum_bytes=maximum_artifact_bytes,
    )
    bundle_input = InputFile(path=bundle_path, sha256=archive_sha256, bytes=archive_bytes)
    _require_exact_file(bundle_input, field="adopted image bundle", maximum_bytes=maximum_artifact_bytes)
    try:
        provenance._image_manifest(
            manifest_input.path,
            campaign_id=campaign_id,
            release_sha=release_sha,
            images=images,
            archive=archive,
        )
        preparer.verify_docker_image_archive(
            path=bundle_path,
            images=_prepared_images(images),
            require_isolated_tags=True,
        )
    except Exception as exc:
        raise AdoptedPreparationCompositionError("adopted image bundle is not a verified isolated Docker archive") from exc
    return bundle_input, manifest_input, images, archive


def _capacity_preflight(
    *,
    output_parent: Path,
    release_bundle: InputFile,
    image_bundle: InputFile,
    image_manifest: InputFile,
    images: Sequence[Mapping[str, Any]],
    disk_usage: DiskUsage,
) -> dict[str, int]:
    try:
        usage = disk_usage(output_parent)
        free_bytes = usage.free
    except Exception as exc:
        raise AdoptedPreparationCompositionError("cannot inspect adopted preparation output capacity") from exc
    if isinstance(free_bytes, bool) or not isinstance(free_bytes, int) or free_bytes < 0:
        raise AdoptedPreparationCompositionError("adopted preparation output capacity is invalid")
    image_logical_bytes = sum(int(image["size_bytes"]) for image in images)
    output_required_bytes = (
        release_bundle.bytes
        + image_bundle.bytes
        + image_manifest.bytes
        + RECEIPT_RESERVE_BYTES
        + preparer.CAPACITY_MARGIN_BYTES
    )
    if free_bytes < output_required_bytes:
        raise AdoptedPreparationCompositionError("insufficient free space for a new adopted preparation candidate")
    return {
        "image_logical_bytes": image_logical_bytes,
        "output_required_bytes": output_required_bytes,
        "output_free_bytes": free_bytes,
        # This primitive streams directly into its new candidate and creates
        # no retained workspace or temporary image copy.
        "workspace_required_bytes": 0,
        "workspace_free_bytes": free_bytes,
    }


def _artifact_descriptor(name: str, file: InputFile, bindings: Mapping[str, str]) -> dict[str, Any]:
    return {
        "bindings": dict(sorted(bindings.items())),
        "bytes": file.bytes,
        "name": name,
        "path": str(file.path),
        "sha256": file.sha256,
    }


def _stage_publish(artifacts: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    artifact_values: list[str] = []
    binding_values: list[str] = []
    for artifact in sorted(artifacts, key=lambda item: str(item["name"])):
        name = str(artifact["name"])
        artifact_values.append(name + "=" + str(artifact["path"]))
        bindings = artifact["bindings"]
        if not isinstance(bindings, Mapping):  # pragma: no cover - local invariant.
            raise AdoptedPreparationCompositionError("prepared artifact bindings are invalid")
        for key, value in sorted(bindings.items()):
            binding_values.append(name + "=" + str(key) + "=" + str(value))
    return {"artifact": artifact_values, "artifact_binding": binding_values}


def compose_adopted_preparation(
    *,
    application_preparation_receipt: Path,
    adopted_image_bundle: Path,
    adopted_image_manifest: Path,
    output_parent: Path,
    preparation_id: str | None = None,
    maximum_artifact_bytes: int = MAXIMUM_ARTIFACT_BYTES,
    now: dt.datetime | None = None,
    disk_usage: DiskUsage = shutil.disk_usage,
) -> dict[str, Any]:
    """Create a new receipt-compatible preparation using adopted controller images."""

    _require_root_execution()
    if (
        isinstance(maximum_artifact_bytes, bool)
        or not isinstance(maximum_artifact_bytes, int)
        or not 1 <= maximum_artifact_bytes <= MAXIMUM_ARTIFACT_BYTES
    ):
        raise AdoptedPreparationCompositionError("maximum_artifact_bytes is invalid")
    application_preparation_receipt = _require_private_file(
        application_preparation_receipt,
        field="existing application preparation receipt",
        maximum_bytes=MAXIMUM_JSON_BYTES,
    )
    try:
        source_preparation = provenance._preparation_receipt(application_preparation_receipt)
    except Exception as exc:
        raise AdoptedPreparationCompositionError("existing application preparation receipt is not verified") from exc
    source_release = source_preparation.artifacts[provenance.APPLICATION_BUNDLE_ARTIFACT]
    if source_release.bytes > maximum_artifact_bytes:
        raise AdoptedPreparationCompositionError("existing application release bundle exceeds the configured bound")
    release_input = InputFile(
        path=source_release.path,
        sha256=source_release.sha256,
        bytes=source_release.bytes,
    )
    _require_exact_file(
        release_input,
        field="existing application release bundle",
        maximum_bytes=maximum_artifact_bytes,
    )
    bundle_input, manifest_input, images, image_archive = _load_adopted_inputs(
        image_bundle=adopted_image_bundle,
        image_manifest=adopted_image_manifest,
        campaign_id=source_preparation.campaign_id,
        release_sha=source_preparation.release_sha,
        maximum_artifact_bytes=maximum_artifact_bytes,
    )
    output_parent = _require_root_only_directory(output_parent, field="output_parent")
    now = now or preparer.utc_now()
    try:
        prepared_at = preparer.utc_iso(now)
    except Exception as exc:
        raise AdoptedPreparationCompositionError("prepared_at timestamp is invalid") from exc
    try:
        preparation_id = preparer.require_id(
            preparation_id or preparer.generate_preparation_id(now),
            field="preparation_id",
            pattern=provenance.BUNDLE_ID_RE,
        )
    except Exception as exc:
        raise AdoptedPreparationCompositionError("preparation_id is invalid") from exc
    capacity_preflight = _capacity_preflight(
        output_parent=output_parent,
        release_bundle=release_input,
        image_bundle=bundle_input,
        image_manifest=manifest_input,
        images=images,
        disk_usage=disk_usage,
    )
    candidate = preparer.candidate_directory(
        output_parent,
        release_sha=source_preparation.release_sha,
        preparation_id=preparation_id,
    )
    if candidate.exists() or candidate.is_symlink():
        raise AdoptedPreparationCompositionError("refusing to overwrite an adopted preparation candidate")
    try:
        candidate.mkdir(mode=0o700)
    except OSError as exc:
        raise AdoptedPreparationCompositionError("cannot create adopted preparation candidate") from exc
    _require_root_only_directory(candidate, field="adopted preparation candidate")
    _fsync_directory(output_parent)

    receipt_path = candidate / PREPARATION_RECEIPT_NAME
    copied_release = _copy_exact_file(
        source=release_input,
        destination=candidate / RELEASE_BUNDLE_NAME,
        field="existing application release bundle",
        maximum_bytes=maximum_artifact_bytes,
    )
    copied_bundle = _copy_exact_file(
        source=bundle_input,
        destination=candidate / IMAGE_BUNDLE_NAME,
        field="adopted image bundle",
        maximum_bytes=maximum_artifact_bytes,
    )
    copied_manifest = _copy_exact_file(
        source=manifest_input,
        destination=candidate / IMAGE_MANIFEST_NAME,
        field="adopted image manifest",
        maximum_bytes=MAXIMUM_JSON_BYTES,
    )
    _require_exact_file(
        release_input,
        field="existing application release bundle",
        maximum_bytes=maximum_artifact_bytes,
    )
    _require_exact_file(bundle_input, field="adopted image bundle", maximum_bytes=maximum_artifact_bytes)
    _require_exact_file(manifest_input, field="adopted image manifest", maximum_bytes=MAXIMUM_JSON_BYTES)
    _load_adopted_inputs(
        image_bundle=copied_bundle.path,
        image_manifest=copied_manifest.path,
        campaign_id=source_preparation.campaign_id,
        release_sha=source_preparation.release_sha,
        maximum_artifact_bytes=maximum_artifact_bytes,
    )

    image_values = list(images)
    image_set_sha256 = preparer.sha256_bytes(preparer.canonical_json_bytes(image_values))
    image_ids_sha256 = preparer.sha256_bytes(
        preparer.canonical_json_bytes([str(image["image_id"]) for image in images])
    )
    artifacts = [
        _artifact_descriptor(
            provenance.IMAGE_BUNDLE_ARTIFACT,
            copied_bundle,
            {
                "artifact_sha256": copied_bundle.sha256,
                "image_count": str(len(images)),
                "image_ids_sha256": image_ids_sha256,
                "image_manifest_sha256": copied_manifest.sha256,
                "image_set_sha256": image_set_sha256,
                "release_sha": source_preparation.release_sha,
            },
        ),
        _artifact_descriptor(
            provenance.IMAGE_MANIFEST_ARTIFACT,
            copied_manifest,
            {
                "artifact_sha256": copied_manifest.sha256,
                "image_set_sha256": image_set_sha256,
                "release_sha": source_preparation.release_sha,
            },
        ),
        _artifact_descriptor(
            provenance.APPLICATION_BUNDLE_ARTIFACT,
            copied_release,
            {
                "artifact_sha256": copied_release.sha256,
                "git_commit": source_preparation.release_sha,
                "git_tree": source_preparation.release_tree,
                "release_sha": source_preparation.release_sha,
            },
        ),
    ]
    receipt: dict[str, Any] = {
        "artifacts": artifacts,
        "campaign_id": source_preparation.campaign_id,
        "capacity_preflight": capacity_preflight,
        "image_archive": copy.deepcopy(image_archive),
        "images": image_values,
        "output_directory": str(candidate),
        "preparation_id": preparation_id,
        "release_bundle": {
            "bytes": copied_release.bytes,
            "git_commit": source_preparation.release_sha,
            "git_tree": source_preparation.release_tree,
            "sha256": copied_release.sha256,
        },
        "release_sha": source_preparation.release_sha,
        "schema": preparer.PREPARATION_SCHEMA,
        "stage_publish": _stage_publish(artifacts),
        "status": "prepared",
        "prepared_at": prepared_at,
    }
    receipt["receipt_sha256"] = preparer.sha256_bytes(preparer.canonical_json_bytes(receipt))
    try:
        preparer.write_new_private_json(receipt_path, receipt)
        _fsync_directory(candidate)
    except Exception as exc:
        raise AdoptedPreparationCompositionError("cannot write adopted preparation receipt") from exc
    try:
        verified = provenance._preparation_receipt(receipt_path)
    except Exception as exc:
        # The new candidate is intentionally retained verbatim.  Retrying or
        # cleaning it up would violate create-only forensic semantics.
        raise AdoptedPreparationCompositionError("new adopted preparation is not accepted by the provenance verifier") from exc
    return {
        "status": "prepared",
        "output_directory": str(candidate),
        "preparation_receipt": str(receipt_path),
        "campaign_id": verified.campaign_id,
        "release_sha": verified.release_sha,
        "image_count": len(verified.images),
        "capacity_preflight": capacity_preflight,
        "stage_publish": receipt["stage_publish"],
        "object_storage_action": False,
        "ssh_action": False,
        "docker_command_invoked": False,
        "service_changed": False,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-preparation-receipt", type=Path, required=True)
    parser.add_argument("--adopted-image-bundle", type=Path, required=True)
    parser.add_argument("--adopted-image-manifest", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    parser.add_argument("--preparation-id", default=None)
    parser.add_argument("--maximum-artifact-bytes", type=int, default=MAXIMUM_ARTIFACT_BYTES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        result = compose_adopted_preparation(
            application_preparation_receipt=args.application_preparation_receipt,
            adopted_image_bundle=args.adopted_image_bundle,
            adopted_image_manifest=args.adopted_image_manifest,
            output_parent=args.output_parent,
            preparation_id=args.preparation_id,
            maximum_artifact_bytes=args.maximum_artifact_bytes,
        )
    except AdoptedPreparationCompositionError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch.
    raise SystemExit(main())
