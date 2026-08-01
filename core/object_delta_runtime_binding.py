"""Trusted local binding for one Object-delta source stream.

The Object Storage delta plane has two distinct inputs: untrusted payload
bytes and a local, release-bound stream configuration.  This module handles
only the latter.  It never opens a database connection, starts a worker,
contacts Object Storage, reads an age identity, or creates a presigned URL.

The binding is deliberately root-only and default-off.  A future source
adapter may use it only together with an active application Writer Witness
term.  Keeping the controller/transport material outside this file makes it
impossible to accidentally place credentials or transient URLs in an
application container configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    STREAM_GENERATION_ID_RE,
    WEBAPP_SITES,
)
from core.append_only_sync_delta_payload import REGISTRY_FINGERPRINT_RE


OBJECT_DELTA_SOURCE_BINDING_SCHEMA = "gold-trade-object-delta-source-binding-v1"
MAX_BINDING_BYTES = 16 * 1024
OBJECT_DELTA_SOURCE_BINDING_FIELDS = frozenset(
    {
        "schema",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "expected_registry_fingerprint",
    }
)
SOURCE_SERVER_BY_SITE = {
    "webapp_fi": "foreign",
    "webapp_ir": "iran",
}


class ObjectDeltaRuntimeBindingError(RuntimeError):
    """Raised when a local Object-delta source binding is unsafe."""


def _require_text(value: object, *, label: str, pattern: object) -> str:
    if not isinstance(value, str) or not hasattr(pattern, "fullmatch") or pattern.fullmatch(value) is None:
        raise ObjectDeltaRuntimeBindingError(f"object-delta {label} is invalid")
    return value


@dataclass(frozen=True)
class ObjectDeltaSourceRuntimeBinding:
    """Non-secret release-bound identity for one unidirectional source stream."""

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    expected_registry_fingerprint: str

    def __post_init__(self) -> None:
        if self.source_site not in WEBAPP_SITES:
            raise ObjectDeltaRuntimeBindingError("object-delta source site is invalid")
        if self.destination_site not in WEBAPP_SITES or self.destination_site == self.source_site:
            raise ObjectDeltaRuntimeBindingError("object-delta destination site is invalid")
        _require_text(self.campaign_id, label="campaign id", pattern=CAMPAIGN_ID_RE)
        _require_text(self.release_sha, label="release sha", pattern=RELEASE_SHA_RE)
        _require_text(
            self.stream_generation_id,
            label="stream generation id",
            pattern=STREAM_GENERATION_ID_RE,
        )
        _require_text(
            self.expected_registry_fingerprint,
            label="registry fingerprint",
            pattern=REGISTRY_FINGERPRINT_RE,
        )

    @property
    def source_server(self) -> str:
        return SOURCE_SERVER_BY_SITE[self.source_site]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectDeltaRuntimeBindingError("object-delta binding contains duplicate JSON fields")
        value[key] = item
    return value


def _validate_root_controlled_ancestors(path: Path) -> None:
    """Require root-owned, non-replaceable ancestors before opening a binding."""

    if not path.is_absolute():
        raise ObjectDeltaRuntimeBindingError("object-delta binding path must be absolute")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise ObjectDeltaRuntimeBindingError("object-delta binding path validation is unavailable")
    flags = os.O_RDONLY | os.O_CLOEXEC | nofollow | directory
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            info = os.fstat(descriptor)
            mode = stat.S_IMODE(info.st_mode)
            sticky_root_parent = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or (mode & 0o022 and not sticky_root_parent)
            ):
                raise ObjectDeltaRuntimeBindingError(
                    "object-delta binding parent is not root controlled"
                )
    except ObjectDeltaRuntimeBindingError:
        raise
    except OSError as exc:
        raise ObjectDeltaRuntimeBindingError("object-delta binding parent is unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_root_only_binding_bytes(path: Path) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ObjectDeltaRuntimeBindingError("object-delta binding path must be absolute")
    _validate_root_controlled_ancestors(path)
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ObjectDeltaRuntimeBindingError("object-delta binding cannot be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ObjectDeltaRuntimeBindingError("object-delta binding is not a root-only regular file")
        chunks: list[bytes] = []
        remaining = MAX_BINDING_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    except ObjectDeltaRuntimeBindingError:
        raise
    except OSError as exc:
        raise ObjectDeltaRuntimeBindingError("object-delta binding cannot be read safely") from exc
    finally:
        os.close(descriptor)
    if not payload or len(payload) > MAX_BINDING_BYTES:
        raise ObjectDeltaRuntimeBindingError("object-delta binding size is invalid")
    return payload


def parse_object_delta_source_binding(value: object) -> ObjectDeltaSourceRuntimeBinding:
    """Validate a decoded, non-secret source binding without filesystem I/O."""

    if not isinstance(value, Mapping) or set(value) != OBJECT_DELTA_SOURCE_BINDING_FIELDS:
        raise ObjectDeltaRuntimeBindingError("object-delta binding fields are invalid")
    if value.get("schema") != OBJECT_DELTA_SOURCE_BINDING_SCHEMA:
        raise ObjectDeltaRuntimeBindingError("object-delta binding schema is invalid")
    return ObjectDeltaSourceRuntimeBinding(
        source_site=value["source_site"],
        destination_site=value["destination_site"],
        campaign_id=value["campaign_id"],
        release_sha=value["release_sha"],
        stream_generation_id=value["stream_generation_id"],
        expected_registry_fingerprint=value["expected_registry_fingerprint"],
    )


def load_object_delta_source_binding(path: Path) -> ObjectDeltaSourceRuntimeBinding:
    """Load one root-only JSON binding and reject duplicate or non-UTF-8 JSON."""

    raw = _read_root_only_binding_bytes(path)
    try:
        decoded = raw.decode("utf-8", "strict")
        value = json.loads(decoded, object_pairs_hook=_strict_object)
    except ObjectDeltaRuntimeBindingError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObjectDeltaRuntimeBindingError("object-delta binding JSON is invalid") from exc
    return parse_object_delta_source_binding(value)


def validate_object_delta_source_runtime(
    binding: ObjectDeltaSourceRuntimeBinding,
    *,
    current_server: str,
    current_release_sha: str,
    current_registry_fingerprint: str,
) -> ObjectDeltaSourceRuntimeBinding:
    """Bind a loaded file to the exact local writer role and installed release."""

    if not isinstance(binding, ObjectDeltaSourceRuntimeBinding):
        raise ObjectDeltaRuntimeBindingError("object-delta source binding is invalid")
    if current_server != binding.source_server:
        raise ObjectDeltaRuntimeBindingError("object-delta binding source does not match this server")
    _require_text(
        current_release_sha,
        label="current release sha",
        pattern=RELEASE_SHA_RE,
    )
    if current_release_sha != binding.release_sha:
        raise ObjectDeltaRuntimeBindingError(
            "object-delta binding release sha does not match this release"
        )
    _require_text(
        current_registry_fingerprint,
        label="current registry fingerprint",
        pattern=REGISTRY_FINGERPRINT_RE,
    )
    if current_registry_fingerprint != binding.expected_registry_fingerprint:
        raise ObjectDeltaRuntimeBindingError(
            "object-delta binding registry fingerprint does not match this release"
        )
    return binding


def binding_from_settings(settings: object) -> ObjectDeltaSourceRuntimeBinding | None:
    """Load the binding only when the paired writer/data-plane runtime is on.

    The disabled path reads no file.  An enabled projector is intentionally
    coupled to the existing single-writer and application-term switches, so a
    legacy two-site process cannot accidentally emit a new append-only stream.
    """

    enabled = getattr(settings, "object_delta_source_outbox_enabled", False)
    if enabled is False:
        return None
    if enabled is not True:
        raise ObjectDeltaRuntimeBindingError("object-delta source runtime flag is invalid")
    if getattr(settings, "single_writer_runtime_enabled", False) is not True:
        raise ObjectDeltaRuntimeBindingError("object-delta source runtime requires single-writer mode")
    if getattr(settings, "application_writer_term_enforced", False) is not True:
        raise ObjectDeltaRuntimeBindingError("object-delta source runtime requires application writer terms")
    receiver_enabled = getattr(settings, "object_delta_receiver_delivery_enabled", False)
    if receiver_enabled is not False:
        raise ObjectDeltaRuntimeBindingError(
            "object-delta source runtime requires receiver delivery to be disabled"
        )
    raw_path = getattr(settings, "object_delta_source_binding_file", None)
    if raw_path is None or raw_path == "":
        raise ObjectDeltaRuntimeBindingError("object-delta source binding file is required")
    path = raw_path if isinstance(raw_path, Path) else Path(raw_path)
    current_release_sha = getattr(settings, "release_sha", None)
    binding = load_object_delta_source_binding(path)
    from core.server_routing import current_server
    from core.sync_protocol import current_sync_registry_fingerprint

    return validate_object_delta_source_runtime(
        binding,
        current_server=current_server(),
        current_release_sha=current_release_sha,
        current_registry_fingerprint=current_sync_registry_fingerprint(),
    )
