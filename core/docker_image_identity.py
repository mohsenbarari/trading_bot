"""Canonical storage-driver-independent Docker image content identity."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DESCRIPTOR_FIELDS = frozenset(
    {
        "architecture",
        "os",
        "created",
        "config_sha256",
        "rootfs_type",
        "rootfs_layers",
    }
)


class DockerImageIdentityError(RuntimeError):
    """Raised when Docker metadata cannot prove canonical image content."""


def canonical_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DockerImageIdentityError(
            "Docker image metadata is not canonical JSON"
        ) from exc
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def verify_content_descriptor(descriptor: Any) -> str:
    if (
        not isinstance(descriptor, dict)
        or set(descriptor) != DESCRIPTOR_FIELDS
    ):
        raise DockerImageIdentityError(
            "image content descriptor fields are invalid"
        )
    layers = descriptor["rootfs_layers"]
    if (
        not isinstance(descriptor["architecture"], str)
        or not descriptor["architecture"]
        or not isinstance(descriptor["os"], str)
        or not descriptor["os"]
        or not isinstance(descriptor["created"], str)
        or not descriptor["created"]
        or not isinstance(descriptor["config_sha256"], str)
        or CONTENT_HASH_RE.fullmatch(descriptor["config_sha256"]) is None
        or descriptor["rootfs_type"] != "layers"
        or not isinstance(layers, list)
        or not layers
        or any(
            not isinstance(layer, str)
            or CONTENT_HASH_RE.fullmatch(layer) is None
            for layer in layers
        )
    ):
        raise DockerImageIdentityError(
            "image content descriptor is malformed"
        )
    return canonical_sha256(descriptor)


def _descriptor(
    *,
    architecture: Any,
    operating_system: Any,
    created: Any,
    config: Any,
    rootfs_type: Any,
    rootfs_layers: Any,
) -> tuple[dict[str, Any], str]:
    if not isinstance(config, dict):
        raise DockerImageIdentityError(
            "Docker image lacks canonical configuration metadata"
        )
    descriptor = {
        "architecture": str(architecture or ""),
        "os": str(operating_system or ""),
        "created": str(created or ""),
        "config_sha256": canonical_sha256(config),
        "rootfs_type": str(rootfs_type or ""),
        "rootfs_layers": (
            list(rootfs_layers)
            if isinstance(rootfs_layers, list)
            else rootfs_layers
        ),
    }
    return descriptor, verify_content_descriptor(descriptor)


def image_content_descriptor(
    inspected: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Build canonical content identity from ``docker image inspect``."""

    if not isinstance(inspected, dict):
        raise DockerImageIdentityError(
            "Docker image inspection is invalid"
        )
    rootfs = inspected.get("RootFS")
    if not isinstance(rootfs, dict):
        raise DockerImageIdentityError(
            "Docker image lacks canonical rootfs metadata"
        )
    return _descriptor(
        architecture=inspected.get("Architecture"),
        operating_system=inspected.get("Os"),
        created=inspected.get("Created"),
        config=inspected.get("Config"),
        rootfs_type=rootfs.get("Type"),
        rootfs_layers=rootfs.get("Layers"),
    )


def image_content_descriptor_from_archive_config(
    archive_config: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Build the same identity from a Docker-save raw config document."""

    if not isinstance(archive_config, dict):
        raise DockerImageIdentityError(
            "Docker archive config is invalid"
        )
    rootfs = archive_config.get("rootfs")
    if not isinstance(rootfs, dict):
        raise DockerImageIdentityError(
            "Docker archive config lacks canonical rootfs metadata"
        )
    return _descriptor(
        architecture=archive_config.get("architecture"),
        operating_system=archive_config.get("os"),
        created=archive_config.get("created"),
        config=archive_config.get("config"),
        rootfs_type=rootfs.get("type"),
        rootfs_layers=rootfs.get("diff_ids"),
    )
