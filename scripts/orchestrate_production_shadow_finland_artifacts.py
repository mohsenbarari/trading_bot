#!/usr/bin/env python3
"""Stage one production-shadow release on Bot-FI and WebApp-FI only.

The controller defaults to a non-mutating plan.  Apply mode copies the exact
bootstrap, per-role manifest, Git bundle, and four Docker archives into
create-only incoming paths, then invokes the bounded host agent locally and
through pinned trusted SSH.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import select
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import production_shadow_finland_stage as STAGE  # noqa: E402


PLAN_SCHEMA = "production-shadow-finland-artifact-orchestrator-plan-v1"
EVIDENCE_SCHEMA = "production-shadow-finland-artifact-orchestrator-v1"
JOURNAL_SCHEMA = "production-shadow-finland-artifact-orchestrator-journal-v1"
STAGE_BINDINGS_SCHEMA = "production-shadow-image-stage-bindings-v1"
ROLE_BINDING_SCHEMA = "production-shadow-role-image-stage-binding-v1"
RELEASE_CLOSURE_SCHEMA = "production-shadow-release-artifact-closure-v2"

BOT_FI_HOST = "65.109.216.187"
WEBAPP_FI_HOST = "65.109.220.59"
WEBAPP_FI_USER = "root"
WEBAPP_FI_PORT = 37067
ROLES = ("bot_fi", "webapp_fi")
ROLE_PATHS = {"bot_fi": "bot-fi", "webapp_fi": "webapp-fi"}
ROLE_HOSTS = {"bot_fi": BOT_FI_HOST, "webapp_fi": WEBAPP_FI_HOST}
ROLE_TRANSPORTS = {
    "bot_fi": "local-controller",
    "webapp_fi": "trusted-ssh-scp",
}
WA_IR_HOST = "95.38.164.29"
WITNESS_HOST = "37.152.191.11"

SSH = "/usr/bin/ssh"
SCP = "/usr/bin/scp"
PYTHON = "/usr/bin/python3"
KNOWN_HOSTS = Path("/root/.ssh/known_hosts")
DEFAULT_SSH_IDENTITY = Path("/root/.ssh/id_ed25519")
DEFAULT_STAGE_AGENT = Path(__file__).with_name(
    "production_shadow_finland_stage.py"
)
CONTROLLER_SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
CONTROLLER_DIRECTORY = "controller"
CONTROLLER_JOURNAL_FILENAME = "finland-image-stage-controller-journal.json"
CONTROLLER_EVIDENCE_FILENAME = "finland-image-stage-evidence.json"
CONTROLLER_LOCK_FILENAME = "finland-image-stage-controller.lock"
CONTROLLER_AGENT_FILENAME = "production-shadow-finland-stage.py"
ROLE_MANIFEST_FILENAMES = {
    role: f"image-stage-manifest-{ROLE_PATHS[role]}.json" for role in ROLES
}

CONTROLLER_CONFIRMATION_PREFIX = (
    "STAGE-PRODUCTION-SHADOW-FINLAND-ARTIFACTS"
)
MAX_INPUT_BYTES = 64 * 1024 * 1024 * 1024
MAX_CONTROL_BYTES = 1024 * 1024
MAX_AGENT_BYTES = STAGE.MAX_AGENT_BYTES
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
PROCESS_GROUP_TERM_SECONDS = 1.0
PROCESS_TREE_QUIESCENCE_SECONDS = 0.25
CONTROLLER_LIVENESS_GRACE_SECONDS = (
    STAGE.DOCKER_LOAD_RECONCILE_SECONDS + 5.0
)
PR_SET_CHILD_SUBREAPER = 36
ZERO_SHA256 = "0" * 64
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

CLOSURE_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release",
        "images",
        "source_engine_observations",
        "verified_image_contracts",
        "constraints",
    }
)
CLOSURE_RELEASE_FIELDS = frozenset({"commit_sha", "tree_sha", "bundle"})
CLOSURE_BUNDLE_FIELDS = frozenset({"filename", "sha256", "bytes"})
CLOSURE_CONSTRAINT_FIELDS = frozenset(
    {
        "source_backup_included",
        "role_material_included",
        "secrets_included",
        "network_transfer_performed",
        "container_runtime_changed",
    }
)
HOST_RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "operation_manifest_sha256",
        "stage_attestation_sha256",
        "stage_attestation_path",
        "runtime_image_ids",
        "containers_started",
        "services_started",
        "networks_created",
        "volumes_created",
        "current_mutated",
        "data_mutated",
    }
)
VERSION_FIELDS = frozenset(
    {"schema", "version", "agent_sha256", "agent_bytes"}
)
ROLE_BINDING_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "role",
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
        "runtime_image_ids",
    }
)
JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "closure_sha256",
        "agent_sha256",
        "status",
        "completed_roles",
        "current_role",
        "role_results",
        "state_sha256",
    }
)
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class FinlandArtifactOrchestratorError(RuntimeError):
    """Raised when controller inputs or a host result fail closed."""


class FinlandArtifactOrchestratorCancellation(
    FinlandArtifactOrchestratorError
):
    """Raised once when controller execution authority is lost."""


class BoundedControllerRunnerError(FinlandArtifactOrchestratorError):
    """Raised when a controller subprocess cannot be bounded."""


Runner = Callable[..., subprocess.CompletedProcess[bytes]]
Checkpoint = Callable[[str], None]


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FinlandArtifactOrchestratorError(
            "value is not canonical JSON"
        ) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise FinlandArtifactOrchestratorError(
            f"{label} is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise FinlandArtifactOrchestratorError(
            f"{label} must contain one JSON object"
        )
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FinlandArtifactOrchestratorError(
            f"{label} must be a nonzero SHA-256"
        )
    return value


def _bounded_size(value: Any, *, label: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise FinlandArtifactOrchestratorError(
            f"{label} is outside its size bound"
        )
    return value


def confirmation_phrase(operation_id: str, release_sha: str) -> str:
    STAGE._canonical_uuid4(operation_id, label="operation_id")
    if SHA40_RE.fullmatch(release_sha) is None:
        raise FinlandArtifactOrchestratorError("release SHA is invalid")
    return f"{CONTROLLER_CONFIRMATION_PREFIX}:{operation_id}:{release_sha}"


@contextmanager
def _held_source(
    path: Path,
    *,
    maximum: int,
    required_uid: int,
    allowed_modes: frozenset[int],
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != required_uid
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            raise FinlandArtifactOrchestratorError(
                "controller source file ownership or mode is unsafe"
            )
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream, before
        after = os.fstat(stream.fileno())
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise FinlandArtifactOrchestratorError(
                "controller source changed while being read"
            )
    except FinlandArtifactOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "controller source file is unavailable"
        ) from exc
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)


def _read_source(
    path: Path,
    *,
    maximum: int,
    required_uid: int,
    allowed_modes: frozenset[int],
) -> bytes:
    with _held_source(
        path,
        maximum=maximum,
        required_uid=required_uid,
        allowed_modes=allowed_modes,
    ) as (stream, _metadata):
        payload = stream.read(maximum + 1)
    if not 1 <= len(payload) <= maximum:
        raise FinlandArtifactOrchestratorError(
            "controller source file is empty or oversized"
        )
    return payload


def _hash_source(
    path: Path,
    *,
    maximum: int,
    required_uid: int,
    allowed_modes: frozenset[int],
) -> tuple[str, int]:
    with _held_source(
        path,
        maximum=maximum,
        required_uid=required_uid,
        allowed_modes=allowed_modes,
    ) as (stream, _metadata):
        digest = hashlib.sha256()
        size = 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise FinlandArtifactOrchestratorError(
                    "controller source exceeds its size bound"
                )
            digest.update(chunk)
    return digest.hexdigest(), size


def _validate_closure(
    document: dict[str, Any],
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
) -> dict[str, Any]:
    if set(document) != CLOSURE_FIELDS:
        raise FinlandArtifactOrchestratorError(
            "release closure fields are not exact"
        )
    if (
        document["schema"] != RELEASE_CLOSURE_SCHEMA
        or document["operation_id"] != operation_id
    ):
        raise FinlandArtifactOrchestratorError(
            "release closure operation identity differs"
        )
    release = document["release"]
    if (
        not isinstance(release, dict)
        or set(release) != CLOSURE_RELEASE_FIELDS
        or release["commit_sha"] != release_sha
        or release["tree_sha"] != release_tree_sha
    ):
        raise FinlandArtifactOrchestratorError(
            "release closure commit or tree differs"
        )
    bundle = release["bundle"]
    if (
        not isinstance(bundle, dict)
        or set(bundle) != CLOSURE_BUNDLE_FIELDS
        or bundle["filename"] != STAGE.ARTIFACT_FILENAMES["release-bundle"]
    ):
        raise FinlandArtifactOrchestratorError(
            "release closure bundle identity is invalid"
        )
    _nonzero_sha256(bundle["sha256"], label="release bundle SHA-256")
    _bounded_size(
        bundle["bytes"],
        label="release bundle bytes",
        maximum=MAX_INPUT_BYTES,
    )
    images = document["images"]
    if not isinstance(images, dict) or set(images) != set(STAGE.IMAGE_ROLES):
        raise FinlandArtifactOrchestratorError(
            "release closure image roles are not exact"
        )
    for image_role in STAGE.IMAGE_ROLES:
        row = images[image_role]
        if (
            not isinstance(row, dict)
            or set(row) != STAGE.IMAGE_ARTIFACT_FIELDS
        ):
            raise FinlandArtifactOrchestratorError(
                f"release closure {image_role} fields are not exact"
            )
        _nonzero_sha256(
            row["archive_sha256"],
            label=f"{image_role} archive SHA-256",
        )
        _bounded_size(
            row["archive_bytes"],
            label=f"{image_role} archive bytes",
            maximum=MAX_INPUT_BYTES,
        )
        for field in ("config_digest", "content_identity"):
            if (
                not isinstance(row[field], str)
                or IMAGE_ID_RE.fullmatch(row[field]) is None
                or row[field] == "sha256:" + ZERO_SHA256
            ):
                raise FinlandArtifactOrchestratorError(
                    f"release closure {image_role} {field} is invalid"
                )
        if STAGE.verify_content_descriptor(row["content_descriptor"]) != row[
            "content_identity"
        ]:
            raise FinlandArtifactOrchestratorError(
                f"release closure {image_role} content identity differs"
            )
    for field in ("archive_sha256", "config_digest", "content_identity"):
        if len({images[role][field] for role in STAGE.IMAGE_ROLES}) != len(
            STAGE.IMAGE_ROLES
        ):
            raise FinlandArtifactOrchestratorError(
                f"release closure image {field} values are not distinct"
            )
    observations = document["source_engine_observations"]
    if not isinstance(observations, dict) or set(observations) != set(
        STAGE.IMAGE_ROLES
    ):
        raise FinlandArtifactOrchestratorError(
            "source engine observations are not exact"
        )
    for image_role in STAGE.IMAGE_ROLES:
        observation = observations[image_role]
        if (
            not isinstance(observation, dict)
            or set(observation) != {"image_id", "informational_only"}
            or not isinstance(observation["image_id"], str)
            or IMAGE_ID_RE.fullmatch(observation["image_id"]) is None
            or observation["informational_only"] is not True
        ):
            raise FinlandArtifactOrchestratorError(
                "source engine observation is invalid"
            )
    contracts = document["verified_image_contracts"]
    if not isinstance(contracts, dict) or set(contracts) != set(
        STAGE.IMAGE_ROLES
    ):
        raise FinlandArtifactOrchestratorError(
            "verified image contracts are not exact"
        )
    for image_role in STAGE.IMAGE_ROLES:
        contract = contracts[image_role]
        expected_fields = {"os", "architecture", "repo_tags", "oci_revision"}
        if image_role == "postgres":
            expected_fields.add("runtime_user")
        if (
            not isinstance(contract, dict)
            or set(contract) != expected_fields
            or contract["os"] != "linux"
            or contract["architecture"] != "amd64"
            or contract["repo_tags"] != []
            or contract["oci_revision"]
            != (
                release_sha
                if image_role in STAGE.RELEASE_BOUND_IMAGE_ROLES
                else None
            )
        ):
            raise FinlandArtifactOrchestratorError(
                f"verified image contract for {image_role} is invalid"
            )
    runtime_user = contracts["postgres"]["runtime_user"]
    if (
        not isinstance(runtime_user, dict)
        or runtime_user
        != {
            "uid": 70,
            "gid": 70,
            "uid_label": STAGE.POSTGRES_RUNTIME_UID_LABEL,
            "gid_label": STAGE.POSTGRES_RUNTIME_GID_LABEL,
        }
    ):
        raise FinlandArtifactOrchestratorError(
            "PostgreSQL runtime contract differs"
        )
    constraints = document["constraints"]
    if (
        not isinstance(constraints, dict)
        or set(constraints) != CLOSURE_CONSTRAINT_FIELDS
        or any(value is not False for value in constraints.values())
    ):
        raise FinlandArtifactOrchestratorError(
            "release closure constraints are invalid"
        )
    return document


def load_release_closure(
    path: Path,
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    required_uid: int = 0,
) -> tuple[dict[str, Any], bytes, str]:
    try:
        root_metadata = path.parent.stat(follow_symlinks=False)
        resolved_root = path.parent.resolve(strict=True)
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "release artifact root is unavailable"
        ) from exc
    if (
        resolved_root != path.parent
        or not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != required_uid
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
    ):
        raise FinlandArtifactOrchestratorError(
            "release artifact root is not a private canonical directory"
        )
    raw = _read_source(
        path,
        maximum=STAGE.MAX_MANIFEST_BYTES,
        required_uid=required_uid,
        allowed_modes=frozenset({0o600}),
    )
    document = _strict_json(raw, label="release artifact closure")
    _validate_closure(
        document,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
    )
    return document, raw, hashlib.sha256(raw).hexdigest()


def _artifact_sources(
    closure_path: Path,
    closure: Mapping[str, Any],
    *,
    required_uid: int,
) -> dict[str, Path]:
    root = closure_path.parent
    sources: dict[str, Path] = {
        "release-bundle": root / closure["release"]["bundle"]["filename"]
    }
    for image_role in STAGE.IMAGE_ROLES:
        sources[f"{image_role}-image-archive"] = (
            root / STAGE.ARTIFACT_FILENAMES[f"{image_role}-image-archive"]
        )
    for kind, source in sources.items():
        expected = (
            closure["release"]["bundle"]
            if kind == "release-bundle"
            else closure["images"][kind.removesuffix("-image-archive")]
        )
        expected_sha = (
            expected["sha256"]
            if kind == "release-bundle"
            else expected["archive_sha256"]
        )
        expected_bytes = (
            expected["bytes"]
            if kind == "release-bundle"
            else expected["archive_bytes"]
        )
        if _hash_source(
            source,
            maximum=MAX_INPUT_BYTES,
            required_uid=required_uid,
            allowed_modes=frozenset({0o600}),
        ) != (expected_sha, expected_bytes):
            raise FinlandArtifactOrchestratorError(
                f"source artifact {kind} differs from the closure"
            )
    return sources


def build_stage_manifest(
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    role: str,
    closure: Mapping[str, Any],
    agent_sha256: str,
) -> dict[str, Any]:
    paths = STAGE.canonical_paths(operation_id, release_sha, role)
    artifacts: dict[str, dict[str, Any]] = {}
    for kind in STAGE.ARTIFACT_KINDS:
        if kind == "release-bundle":
            source = closure["release"]["bundle"]
            sha256 = source["sha256"]
            size = source["bytes"]
        else:
            image_role = kind.removesuffix("-image-archive")
            source = closure["images"][image_role]
            sha256 = source["archive_sha256"]
            size = source["archive_bytes"]
        artifacts[kind] = {
            "kind": kind,
            "filename": STAGE.ARTIFACT_FILENAMES[kind],
            "sha256": sha256,
            "bytes": size,
            "format": STAGE.ARTIFACT_FORMATS[kind],
        }
    document = {
        "schema": STAGE.MANIFEST_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "role": role,
        "project_name": paths["project_name"],
        "project_root": str(paths["project_root"]),
        "release_root": str(paths["release_root"]),
        "incoming_root": str(paths["incoming_root"]),
        "secret_role_root": str(paths["secret_role_root"]),
        "bootstrap_sha256": agent_sha256,
        "artifacts": artifacts,
        "image_artifacts": {
            image_role: closure["images"][image_role]
            for image_role in STAGE.IMAGE_ROLES
        },
        "postgres_runtime_uid": 70,
        "postgres_runtime_gid": 70,
        "pull_policy": "never",
    }
    return STAGE.validate_manifest(document)


def build_stage_request(
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    agent_sha256: str,
) -> dict[str, Any]:
    document = {
        "schema": STAGE.REQUEST_SCHEMA,
        "action": "stage",
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "role": manifest["role"],
        "operation_manifest_sha256": manifest_sha256,
        "agent_sha256": agent_sha256,
        "pull_policy": "never",
    }
    STAGE._decode_request(STAGE.encode_request(document), bootstrap=False)
    return document


def build_bootstrap_request(
    *,
    operation_id: str,
    role: str,
    agent_sha256: str,
) -> dict[str, Any]:
    document = {
        "schema": STAGE.BOOTSTRAP_REQUEST_SCHEMA,
        "action": "install-bootstrap",
        "operation_id": operation_id,
        "role": role,
        "agent_sha256": agent_sha256,
    }
    STAGE._decode_request(STAGE.encode_request(document), bootstrap=True)
    return document


def _validate_transport_token(token: str) -> None:
    if (
        not token
        or "\x00" in token
        or "\n" in token
        or "\r" in token
        or WA_IR_HOST in token
        or WITNESS_HOST in token
        or "webapp-ir" in token.lower()
        or "webapp_ir" in token.lower()
        or "witness" in token.lower()
        or "/current" in token
        or "object-storage" in token.lower()
        or "arvan" in token.lower()
    ):
        raise FinlandArtifactOrchestratorError(
            "rendered transport token is outside the Finland boundary"
        )


def _remote_command(arguments: list[str]) -> str:
    if not arguments or any(not isinstance(value, str) for value in arguments):
        raise FinlandArtifactOrchestratorError("remote command argv is invalid")
    for token in arguments:
        _validate_transport_token(token)
    command = shlex.join(arguments)
    if "\n" in command or "\r" in command:
        raise FinlandArtifactOrchestratorError(
            "remote command contains a line break"
        )
    return command


def ssh_arguments(
    ssh_identity: Path,
    *,
    remote_arguments: list[str],
) -> list[str]:
    identity = str(ssh_identity)
    _validate_transport_token(identity)
    if (
        not ssh_identity.is_absolute()
        or ".." in ssh_identity.parts
        or ":" in identity
    ):
        raise FinlandArtifactOrchestratorError("SSH identity path is invalid")
    return [
        SSH,
        "-T",
        "-p",
        str(WEBAPP_FI_PORT),
        "-i",
        identity,
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-o",
        "ControlMaster=no",
        "-o",
        "RequestTTY=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "AddressFamily=inet",
        f"{WEBAPP_FI_USER}@{WEBAPP_FI_HOST}",
        _remote_command(remote_arguments),
    ]


def scp_arguments(
    ssh_identity: Path,
    *,
    source: Path,
    destination: Path,
) -> list[str]:
    for token in (str(ssh_identity), str(source), str(destination)):
        _validate_transport_token(token)
    if (
        not ssh_identity.is_absolute()
        or ".." in ssh_identity.parts
        or ":" in str(ssh_identity)
        or not source.is_absolute()
        or ".." in source.parts
        or ":" in str(source)
    ):
        raise FinlandArtifactOrchestratorError(
            "SCP local source or identity path is invalid"
        )
    if (
        not destination.is_absolute()
        or ".." in destination.parts
        or not destination.name.endswith(".transfer")
        or not destination.name.startswith(".")
    ):
        raise FinlandArtifactOrchestratorError(
            "SCP destination is not a fixed transfer partial"
        )
    return [
        SCP,
        "-q",
        "-p",
        "-P",
        str(WEBAPP_FI_PORT),
        "-i",
        str(ssh_identity),
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-o",
        "ControlMaster=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "AddressFamily=inet",
        "--",
        str(source),
        f"{WEBAPP_FI_USER}@{WEBAPP_FI_HOST}:{destination}",
    ]


def _bootstrap_install_arguments(
    *,
    operation_id: str,
    role: str,
    agent_sha256: str,
) -> list[str]:
    paths = STAGE.canonical_paths(operation_id, "0" * 40, role)
    partial = STAGE.transfer_partial_path(paths["agent"])  # type: ignore[arg-type]
    request = build_bootstrap_request(
        operation_id=operation_id,
        role=role,
        agent_sha256=agent_sha256,
    )
    return [
        PYTHON,
        "-I",
        "-B",
        str(partial),
        "--install-bootstrap-request-b64",
        STAGE.encode_request(request),
        "--pull",
        "never",
    ]


def _version_arguments(
    *,
    operation_id: str,
    release_sha: str,
    role: str,
    agent_sha256: str,
) -> list[str]:
    paths = STAGE.canonical_paths(operation_id, release_sha, role)
    return [
        PYTHON,
        "-I",
        "-B",
        str(paths["agent"]),
        "--version",
        "--expected-agent-sha256",
        agent_sha256,
        "--pull",
        "never",
    ]


def _stage_arguments(
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    agent_sha256: str,
) -> list[str]:
    paths = STAGE.canonical_paths(
        str(manifest["operation_id"]),
        str(manifest["release_sha"]),
        str(manifest["role"]),
    )
    request = build_stage_request(
        manifest,
        manifest_sha256=manifest_sha256,
        agent_sha256=agent_sha256,
    )
    return [
        PYTHON,
        "-I",
        "-B",
        str(paths["agent"]),
        "--request-b64",
        STAGE.encode_request(request),
        "--expected-agent-sha256",
        agent_sha256,
        "--pull",
        "never",
    ]


def _directory_prepare_arguments(
    *,
    operation_id: str,
    release_sha: str,
    role: str,
) -> list[str]:
    paths = STAGE.canonical_paths(operation_id, release_sha, role)
    return [
        "/usr/bin/install",
        "-d",
        "-m",
        "0700",
        str(paths["project_root"]),
        str(paths["project_root"] / "releases"),  # type: ignore[operator]
        str(paths["project_root"] / "incoming"),  # type: ignore[operator]
        str(paths["incoming_root"]),
        str(STAGE.SECRET_ROOT_PREFIX / operation_id),
        str(paths["secret_role_root"]),
    ]


def render_plan(
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    closure_sha256: str,
    closure: Mapping[str, Any],
    sources: Mapping[str, Path],
    stage_agent: Path,
    agent_sha256: str,
    agent_bytes: int,
    ssh_identity: Path,
) -> dict[str, Any]:
    controller_paths = _controller_paths(operation_id)
    roles: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        manifest = build_stage_manifest(
            operation_id=operation_id,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            role=role,
            closure=closure,
            agent_sha256=agent_sha256,
        )
        manifest_bytes = canonical_json(manifest)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        paths = STAGE.canonical_paths(operation_id, release_sha, role)
        incoming_files = [
            {
                "kind": "bootstrap-agent",
                "filename": STAGE.AGENT_FILENAME,
                "sha256": agent_sha256,
                "bytes": agent_bytes,
                "mode": "0700",
            },
            {
                "kind": "operation-manifest",
                "filename": STAGE.MANIFEST_FILENAME,
                "sha256": manifest_sha256,
                "bytes": len(manifest_bytes),
                "mode": "0600",
            },
            *[
                {
                    "kind": kind,
                    "filename": manifest["artifacts"][kind]["filename"],
                    "sha256": manifest["artifacts"][kind]["sha256"],
                    "bytes": manifest["artifacts"][kind]["bytes"],
                    "mode": "0600",
                }
                for kind in STAGE.ARTIFACT_KINDS
            ],
        ]
        local_commands = {
            "bootstrap_install": _bootstrap_install_arguments(
                operation_id=operation_id,
                role=role,
                agent_sha256=agent_sha256,
            ),
            "version": _version_arguments(
                operation_id=operation_id,
                release_sha=release_sha,
                role=role,
                agent_sha256=agent_sha256,
            ),
            "stage": _stage_arguments(
                manifest,
                manifest_sha256=manifest_sha256,
                agent_sha256=agent_sha256,
            ),
        }
        if role == "webapp_fi":
            commands: dict[str, Any] = {
                "prepare": ssh_arguments(
                    ssh_identity,
                    remote_arguments=_directory_prepare_arguments(
                        operation_id=operation_id,
                        release_sha=release_sha,
                        role=role,
                    ),
                ),
                "bootstrap_transfer": scp_arguments(
                    ssh_identity,
                    source=controller_paths["agent"],
                    destination=STAGE.transfer_partial_path(
                        paths["agent"]  # type: ignore[arg-type]
                    ),
                ),
                "bootstrap_install": ssh_arguments(
                    ssh_identity,
                    remote_arguments=local_commands["bootstrap_install"],
                ),
                "version": ssh_arguments(
                    ssh_identity,
                    remote_arguments=local_commands["version"],
                ),
                "manifest_transfer": scp_arguments(
                    ssh_identity,
                    source=controller_paths[f"manifest_{role}"],
                    destination=STAGE.transfer_partial_path(
                        paths["manifest"]  # type: ignore[arg-type]
                    ),
                ),
                "artifact_transfers": {
                    kind: scp_arguments(
                        ssh_identity,
                        source=sources[kind],
                        destination=STAGE.transfer_partial_path(
                            paths["incoming_root"]  # type: ignore[arg-type]
                            / STAGE.ARTIFACT_FILENAMES[kind]
                        ),
                    )
                    for kind in STAGE.ARTIFACT_KINDS
                },
                "stage": ssh_arguments(
                    ssh_identity,
                    remote_arguments=local_commands["stage"],
                ),
            }
        else:
            commands = local_commands
        roles[role] = {
            "host": ROLE_HOSTS[role],
            "transport": ROLE_TRANSPORTS[role],
            "project_root": str(paths["project_root"]),
            "release_root": str(paths["release_root"]),
            "incoming_root": str(paths["incoming_root"]),
            "secret_role_root": str(paths["secret_role_root"]),
            "operation_manifest_sha256": manifest_sha256,
            "operation_manifest_bytes": len(manifest_bytes),
            "incoming_files": incoming_files,
            "commands": commands,
        }
    document = {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "closure_sha256": closure_sha256,
        "roles": roles,
        "required_confirmation": confirmation_phrase(operation_id, release_sha),
        "pull_policy": "never",
        "object_storage_used": False,
        "arvan_endpoint_contacted": False,
        "containers_created": False,
        "containers_started": False,
        "services_started": False,
        "networks_created": False,
        "volumes_created": False,
        "current_mutated": False,
        "data_mutated": False,
    }
    _validate_plan_boundary(document)
    return document


def _validate_plan_boundary(plan: Mapping[str, Any]) -> None:
    rendered = json.dumps(plan, sort_keys=True)
    lowered = rendered.lower()
    if (
        WA_IR_HOST in rendered
        or WITNESS_HOST in rendered
        or "webapp_ir" in lowered
        or "webapp-ir" in lowered
        or "witness" in lowered
        or "/current" in lowered
        or "docker build" in lowered
        or "docker pull" in lowered
        or "docker run" in lowered
        or "docker compose" in lowered
        or "service start" in lowered
        or "volume create" in lowered
    ):
        raise FinlandArtifactOrchestratorError(
            "rendered plan crossed the stage/load-only boundary"
        )
    if set(plan["roles"]) != set(ROLES):
        raise FinlandArtifactOrchestratorError(
            "rendered plan roles are not exact"
        )


def _anonymous_read_pipe_identity(
    descriptor: int,
    *,
    label: str,
) -> tuple[int, int]:
    if type(descriptor) is not int or descriptor < 0:
        raise FinlandArtifactOrchestratorError(
            f"{label} descriptor is invalid"
        )
    try:
        metadata = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            f"{label} descriptor is unavailable"
        ) from exc
    if (
        not stat.S_ISFIFO(metadata.st_mode)
        or flags & os.O_ACCMODE != os.O_RDONLY
        or target != f"pipe:[{metadata.st_ino}]"
    ):
        raise FinlandArtifactOrchestratorError(
            f"{label} must be an anonymous read-only pipe"
        )
    try:
        entries = tuple(Path("/proc/self/fd").iterdir())
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            f"{label} descriptor closure cannot be inspected"
        ) from exc
    for entry in entries:
        if not entry.name.isdecimal() or int(entry.name, 10) == descriptor:
            continue
        candidate = int(entry.name, 10)
        try:
            observed = os.fstat(candidate)
            observed_flags = fcntl.fcntl(candidate, fcntl.F_GETFL)
        except OSError:
            continue
        if (
            (observed.st_dev, observed.st_ino)
            == (metadata.st_dev, metadata.st_ino)
            and observed_flags & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}
        ):
            raise FinlandArtifactOrchestratorError(
                f"{label} writer end is held by the controller process"
            )
    return metadata.st_dev, metadata.st_ino


class ExecutionAuthority:
    """Deliver one cancellation while controller work is active."""

    _WAKE_SIGNAL = signal.SIGUSR1
    _HANDLED_SIGNALS = (
        signal.SIGHUP,
        signal.SIGTERM,
        signal.SIGINT,
        _WAKE_SIGNAL,
    )

    def __init__(self, control_fd: int | None) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise FinlandArtifactOrchestratorError(
                "Finland artifact orchestration must run in the main thread"
            )
        self._fd: int | None = None
        if control_fd is not None:
            _anonymous_read_pipe_identity(
                control_fd,
                label="controller liveness",
            )
            try:
                self._fd = os.dup(control_fd)
                os.set_inheritable(self._fd, False)
                os.set_blocking(self._fd, False)
            except OSError as exc:
                raise FinlandArtifactOrchestratorError(
                    "controller liveness pipe cannot be secured"
                ) from exc
        self._cancelled = threading.Event()
        self._exception_delivered = threading.Event()
        self._stopping = threading.Event()
        self._reason = "controller execution authority was lost"
        self._old_handlers: dict[int, Any] = {}
        self._monitor: threading.Thread | None = None

    def _cancel(self, reason: str, *, wake_main: bool) -> None:
        if self._cancelled.is_set():
            return
        self._reason = reason
        self._cancelled.set()
        if wake_main:
            main_ident = threading.main_thread().ident
            if main_ident is not None:
                try:
                    signal.pthread_kill(main_ident, self._WAKE_SIGNAL)
                except (OSError, RuntimeError):
                    pass

    def _sample(self) -> None:
        if self._fd is None:
            return
        ready, _write, _error = select.select([self._fd], [], [], 0)
        if not ready:
            return
        try:
            payload = os.read(self._fd, 1)
        except BlockingIOError:
            return
        self._cancel(
            (
                "controller liveness pipe reached EOF"
                if payload == b""
                else "controller liveness pipe carried forbidden data"
            ),
            wake_main=False,
        )
        self.check()

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        if signum == self._WAKE_SIGNAL and self._cancelled.is_set():
            pass
        else:
            self._cancel(
                f"Finland artifact controller received signal {signum}",
                wake_main=False,
            )
        self.check()

    def _monitor_control(self) -> None:
        assert self._fd is not None
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            while not self._stopping.is_set():
                if not selector.select(0.05):
                    continue
                try:
                    payload = os.read(self._fd, 1)
                except BlockingIOError:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        return
                    payload = b""
                self._cancel(
                    (
                        "controller liveness pipe reached EOF"
                        if payload == b""
                        else "controller liveness pipe carried forbidden data"
                    ),
                    wake_main=True,
                )
                return
        finally:
            selector.close()

    def __enter__(self) -> ExecutionAuthority:
        try:
            for signum in self._HANDLED_SIGNALS:
                self._old_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
            self._sample()
            if self._fd is not None:
                self._monitor = threading.Thread(
                    target=self._monitor_control,
                    name="finland-artifact-controller-liveness",
                    daemon=True,
                )
                self._monitor.start()
            self.check()
            return self
        except BaseException:
            self._restore()
            raise

    def check(self) -> None:
        if (
            self._cancelled.is_set()
            and not self._exception_delivered.is_set()
        ):
            self._exception_delivered.set()
            raise FinlandArtifactOrchestratorCancellation(self._reason)

    def _restore(self) -> None:
        self._stopping.set()
        if self._monitor is not None:
            self._monitor.join(timeout=1)
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        for signum, handler in self._old_handlers.items():
            signal.signal(signum, handler)
        self._old_handlers.clear()

    def __exit__(self, error_type: Any, _value: Any, _traceback: Any) -> None:
        deliver_after_restore = (
            self._cancelled.is_set()
            and error_type is None
            and not self._exception_delivered.is_set()
        )
        reason = self._reason
        self._exception_delivered.set()
        self._restore()
        if deliver_after_restore:
            raise FinlandArtifactOrchestratorCancellation(reason)


_ACTIVE_EXECUTION_AUTHORITY: ExecutionAuthority | None = None


@contextmanager
def _execution_authority(
    control_fd: int | None = None,
) -> Iterator[ExecutionAuthority]:
    global _ACTIVE_EXECUTION_AUTHORITY
    if _ACTIVE_EXECUTION_AUTHORITY is not None:
        if control_fd is not None:
            raise FinlandArtifactOrchestratorError(
                "controller execution authority is already active"
            )
        yield _ACTIVE_EXECUTION_AUTHORITY
        return
    authority = ExecutionAuthority(control_fd)
    with authority:
        _ACTIVE_EXECUTION_AUTHORITY = authority
        try:
            yield authority
        finally:
            _ACTIVE_EXECUTION_AUTHORITY = None


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise BoundedControllerRunnerError(
            f"controller child subreaper setup failed with errno {error}"
        )


@dataclass(frozen=True)
class ProcessIdentity:
    process_id: int
    parent_id: int
    process_group: int
    session_id: int
    starttime: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.process_id, self.starttime


def _proc_identity(process_id: int) -> tuple[int, int, int, int, str]:
    try:
        payload = Path(f"/proc/{process_id}/stat").read_text(
            encoding="ascii"
        )
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            raise ValueError("short process stat")
        state = fields[0]
        parent = int(fields[1], 10)
        group = int(fields[2], 10)
        session = int(fields[3], 10)
        starttime = int(fields[19], 10)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BoundedControllerRunnerError(
            "controller subprocess identity is unavailable"
        ) from exc
    return parent, group, session, starttime, state


def _process_snapshot() -> dict[int, ProcessIdentity]:
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as exc:
        raise BoundedControllerRunnerError(
            "controller process closure cannot be enumerated"
        ) from exc
    observed: dict[int, ProcessIdentity] = {}
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        process_id = int(entry.name, 10)
        try:
            parent, group, session, starttime, state = _proc_identity(
                process_id
            )
        except BoundedControllerRunnerError:
            continue
        observed[process_id] = ProcessIdentity(
            process_id=process_id,
            parent_id=parent,
            process_group=group,
            session_id=session,
            starttime=starttime,
            state=state,
        )
    return observed


def _direct_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _process_snapshot().values()
        if identity.parent_id == owner
    )


def _owned_processes(
    root_process_id: int,
    *,
    baseline_children: frozenset[tuple[int, int]],
    include_zombies: bool = False,
) -> tuple[ProcessIdentity, ...]:
    snapshot = _process_snapshot()
    owned_ids = {root_process_id}
    changed = True
    while changed:
        changed = False
        for identity in snapshot.values():
            if (
                identity.process_id not in owned_ids
                and identity.parent_id in owned_ids
            ):
                owned_ids.add(identity.process_id)
                changed = True
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.parent_id == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.process_id)
    return tuple(
        identity
        for process_id, identity in snapshot.items()
        if process_id in owned_ids
        and (include_zombies or identity.state != "Z")
    )


def _signal_process_identity(
    identity: ProcessIdentity,
    signum: int,
) -> None:
    try:
        current = _proc_identity(identity.process_id)
    except BoundedControllerRunnerError:
        return
    if current[3] != identity.starttime:
        return
    try:
        descriptor = os.pidfd_open(identity.process_id, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BoundedControllerRunnerError(
            "controller identity-bound process handle cannot be opened"
        ) from exc
    try:
        refreshed = _proc_identity(identity.process_id)
        if refreshed[3] != identity.starttime:
            return
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BoundedControllerRunnerError(
            "controller identity-bound process signal failed"
        ) from exc
    finally:
        os.close(descriptor)


def _reap_owned_zombies(
    root_process_id: int,
    *,
    baseline_children: frozenset[tuple[int, int]],
) -> None:
    owner = os.getpid()
    while True:
        reaped = False
        for identity in _owned_processes(
            root_process_id,
            baseline_children=baseline_children,
            include_zombies=True,
        ):
            if (
                identity.process_id == root_process_id
                or identity.parent_id != owner
                or identity.state != "Z"
            ):
                continue
            try:
                waited, _status = os.waitpid(
                    identity.process_id,
                    os.WNOHANG,
                )
            except (ChildProcessError, ProcessLookupError):
                continue
            except OSError as exc:
                raise BoundedControllerRunnerError(
                    "controller adopted child could not be reaped"
                ) from exc
            reaped |= waited == identity.process_id
        if not reaped:
            return


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    baseline_children: frozenset[tuple[int, int]],
    allow_liveness_grace: bool,
) -> None:
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass
    if allow_liveness_grace:
        grace_deadline = (
            time.monotonic() + CONTROLLER_LIVENESS_GRACE_SECONDS
        )
        while (
            _owned_processes(
                process.pid,
                baseline_children=baseline_children,
            )
            and time.monotonic() < grace_deadline
        ):
            process.poll()
            _reap_owned_zombies(
                process.pid,
                baseline_children=baseline_children,
            )
            time.sleep(
                min(0.01, max(0.0, grace_deadline - time.monotonic()))
            )
    for identity in reversed(
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
    ):
        _signal_process_identity(identity, signal.SIGTERM)
    deadline = time.monotonic() + PROCESS_GROUP_TERM_SECONDS
    while (
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
        and time.monotonic() < deadline
    ):
        process.poll()
        _reap_owned_zombies(
            process.pid,
            baseline_children=baseline_children,
        )
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    for identity in reversed(
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
    ):
        _signal_process_identity(identity, signal.SIGKILL)
    try:
        process.wait(timeout=PROCESS_GROUP_TERM_SECONDS)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=PROCESS_GROUP_TERM_SECONDS)
    absence_deadline = (
        time.monotonic()
        + PROCESS_GROUP_TERM_SECONDS
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        _reap_owned_zombies(
            process.pid,
            baseline_children=baseline_children,
        )
        owned = _owned_processes(
            process.pid,
            baseline_children=baseline_children,
            include_zombies=True,
        )
        if owned:
            stable_since = None
            for identity in reversed(owned):
                if identity.state != "Z":
                    _signal_process_identity(identity, signal.SIGKILL)
        else:
            if stable_since is None:
                stable_since = time.monotonic()
            elif (
                time.monotonic() - stable_since
                >= PROCESS_TREE_QUIESCENCE_SECONDS
            ):
                return
        time.sleep(0.01)
    _reap_owned_zombies(
        process.pid,
        baseline_children=baseline_children,
    )
    if _owned_processes(
        process.pid,
        baseline_children=baseline_children,
        include_zombies=True,
    ):
        raise BoundedControllerRunnerError(
            "controller subprocess descendants survived forced cleanup"
        )


def _default_runner(
    arguments: Sequence[str],
    *,
    input: bytes | None,
    capture_output: bool,
    check: bool,
    timeout: int | float,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    if (
        input is not None
        or capture_output is not True
        or check is not False
        or type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise BoundedControllerRunnerError(
            "controller subprocess execution options are invalid"
        )
    if _ACTIVE_EXECUTION_AUTHORITY is None:
        with _execution_authority():
            return _default_runner(
                arguments,
                input=input,
                capture_output=capture_output,
                check=check,
                timeout=timeout,
                env=env,
            )
    _ACTIVE_EXECUTION_AUTHORITY.check()
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    group_cleaned = False
    try:
        process = subprocess.Popen(  # noqa: S603
            list(arguments),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            close_fds=True,
            shell=False,
            start_new_session=True,
        )
        if (
            process.stdin is None
            or process.stdout is None
            or process.stderr is None
        ):
            raise BoundedControllerRunnerError(
                "controller subprocess pipes are unavailable"
            )
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            _ACTIVE_EXECUTION_AUTHORITY.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedControllerRunnerError(
                    "controller subprocess timed out"
                )
            events = selector.select(min(0.1, remaining))
            if not events:
                if process.poll() is not None and not group_cleaned:
                    _terminate_process_group(
                        process,
                        baseline_children=baseline_children,
                        allow_liveness_grace=False,
                    )
                    group_cleaned = True
                continue
            for key, _mask in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                if len(buffer) + len(chunk) > MAX_COMMAND_OUTPUT_BYTES:
                    raise BoundedControllerRunnerError(
                        f"controller subprocess {key.data} is oversized"
                    )
                buffer.extend(chunk)
            if process.poll() is not None and not group_cleaned:
                _terminate_process_group(
                    process,
                    baseline_children=baseline_children,
                    allow_liveness_grace=False,
                )
                group_cleaned = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoundedControllerRunnerError(
                "controller subprocess timed out"
            )
        returncode = process.wait(timeout=remaining)
        return subprocess.CompletedProcess(
            args=list(arguments),
            returncode=returncode,
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    except (
        FinlandArtifactOrchestratorCancellation,
        BoundedControllerRunnerError,
    ):
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise BoundedControllerRunnerError(
            "controller subprocess execution failed"
        ) from exc
    finally:
        original_error = sys.exception()
        selector.close()
        cleanup_error: BaseException | None = None
        if process is not None:
            try:
                if not group_cleaned:
                    executable = (
                        str(process.args[0])
                        if isinstance(process.args, (list, tuple))
                        and process.args
                        else ""
                    )
                    _terminate_process_group(
                        process,
                        baseline_children=baseline_children,
                        allow_liveness_grace=(
                            original_error is not None
                            and executable in {PYTHON, SSH}
                        ),
                    )
            except BaseException as exc:
                cleanup_error = exc
            finally:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
        if cleanup_error is not None:
            if original_error is not None:
                raise original_error from cleanup_error
            raise cleanup_error


def _run_command(
    arguments: list[str],
    *,
    runner: Runner | None,
    timeout: int,
) -> bytes:
    if not arguments or any(not isinstance(value, str) or not value for value in arguments):
        raise FinlandArtifactOrchestratorError("controller command argv is invalid")
    for token in arguments:
        _validate_transport_token(token)
    if arguments[0] not in {SSH, SCP, PYTHON}:
        raise FinlandArtifactOrchestratorError(
            "controller command is outside the executable allowlist"
        )
    active_runner = _default_runner if runner is None else runner
    try:
        completed = active_runner(
            arguments,
            input=None,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinlandArtifactOrchestratorError(
            f"controller command is unavailable: {Path(arguments[0]).name}"
        ) from exc
    if (
        len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(completed.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise FinlandArtifactOrchestratorError(
            "controller command output exceeded its bound"
        )
    if completed.returncode != 0:
        raise FinlandArtifactOrchestratorError(
            f"controller command failed closed: {Path(arguments[0]).name}"
        )
    return completed.stdout


def _parse_command_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not 1 <= len(raw) <= MAX_CONTROL_BYTES:
        raise FinlandArtifactOrchestratorError(
            f"{label} output is empty or oversized"
        )
    stripped = raw.strip()
    document = _strict_json(stripped, label=label)
    if stripped != canonical_json(document):
        raise FinlandArtifactOrchestratorError(
            f"{label} output is not canonical JSON"
        )
    return document


def _validate_version(
    document: dict[str, Any],
    *,
    agent_sha256: str,
    agent_bytes: int,
) -> None:
    if (
        set(document) != VERSION_FIELDS
        or document["schema"] != STAGE.VERSION_SCHEMA
        or document["version"] != STAGE.AGENT_VERSION
        or document["agent_sha256"] != agent_sha256
        or document["agent_bytes"] != agent_bytes
    ):
        raise FinlandArtifactOrchestratorError(
            "host stage agent version/hash readback differs"
        )


def _validate_host_result(
    document: dict[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
) -> dict[str, Any]:
    if (
        set(document) != HOST_RESULT_FIELDS
        or document["schema"] != STAGE.RESULT_SCHEMA
        or document["status"] != "staged"
        or document["operation_id"] != manifest["operation_id"]
        or document["release_sha"] != manifest["release_sha"]
        or document["release_tree_sha"] != manifest["release_tree_sha"]
        or document["role"] != manifest["role"]
        or document["operation_manifest_sha256"] != manifest_sha256
    ):
        raise FinlandArtifactOrchestratorError(
            "host stage result identity differs"
        )
    paths = STAGE.canonical_paths(
        manifest["operation_id"],
        manifest["release_sha"],
        manifest["role"],
    )
    if document["stage_attestation_path"] != str(paths["attestation"]):
        raise FinlandArtifactOrchestratorError(
            "host stage attestation path differs"
        )
    _nonzero_sha256(
        document["stage_attestation_sha256"],
        label="stage attestation SHA-256",
    )
    runtime = document["runtime_image_ids"]
    if (
        not isinstance(runtime, dict)
        or set(runtime) != set(STAGE.IMAGE_ROLES)
        or any(
            not isinstance(value, str)
            or IMAGE_ID_RE.fullmatch(value) is None
            or value == "sha256:" + ZERO_SHA256
            for value in runtime.values()
        )
        or len(set(runtime.values())) != len(STAGE.IMAGE_ROLES)
    ):
        raise FinlandArtifactOrchestratorError(
            "host runtime image inventory is invalid"
        )
    for field in (
        "containers_started",
        "services_started",
        "networks_created",
        "volumes_created",
        "current_mutated",
        "data_mutated",
    ):
        if document[field] is not False:
            raise FinlandArtifactOrchestratorError(
                f"host result {field} is not false"
            )
    return document


def _assert_directory(path: Path, *, required_uid: int, private: bool) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "controller directory is unavailable"
        ) from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != required_uid
        or (private and mode != 0o700)
        or (not private and mode & 0o022)
    ):
        raise FinlandArtifactOrchestratorError(
            "controller directory ownership or mode is unsafe"
        )


def _fsync_directory(path: Path) -> None:
    descriptor = -1
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
        raise FinlandArtifactOrchestratorError(
            "controller directory could not be synchronized"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _ensure_private_directory(path: Path, *, required_uid: int) -> None:
    if path.exists() or path.is_symlink():
        _assert_directory(path, required_uid=required_uid, private=True)
        return
    try:
        path.mkdir(mode=0o700)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "controller directory could not be created"
        ) from exc
    _assert_directory(path, required_uid=required_uid, private=True)


def _controller_paths(operation_id: str) -> dict[str, Path]:
    operation_root = CONTROLLER_SECRET_ROOT_PREFIX / operation_id
    controller_root = operation_root / CONTROLLER_DIRECTORY
    return {
        "operation_root": operation_root,
        "controller_root": controller_root,
        "journal": controller_root / CONTROLLER_JOURNAL_FILENAME,
        "evidence": controller_root / CONTROLLER_EVIDENCE_FILENAME,
        "lock": controller_root / CONTROLLER_LOCK_FILENAME,
        "agent": controller_root / CONTROLLER_AGENT_FILENAME,
        **{
            f"manifest_{role}": controller_root / ROLE_MANIFEST_FILENAMES[role]
            for role in ROLES
        },
    }


def _ensure_controller_directories(
    operation_id: str,
    *,
    required_uid: int,
) -> dict[str, Path]:
    _assert_directory(
        CONTROLLER_SECRET_ROOT_PREFIX,
        required_uid=required_uid,
        private=False,
    )
    paths = _controller_paths(operation_id)
    _ensure_private_directory(
        paths["operation_root"],
        required_uid=required_uid,
    )
    _ensure_private_directory(
        paths["controller_root"],
        required_uid=required_uid,
    )
    return paths


def _write_create_only(
    destination: Path,
    payload: bytes,
    *,
    required_uid: int,
    mode: int = 0o600,
    maximum: int = MAX_EVIDENCE_BYTES,
) -> str:
    if not 1 <= len(payload) <= maximum:
        raise FinlandArtifactOrchestratorError(
            "controller create-only payload is empty or oversized"
        )
    expected = hashlib.sha256(payload).hexdigest()
    temporary = destination.with_name(f".{destination.name}.materializing")
    if temporary.exists() or temporary.is_symlink():
        try:
            temporary_metadata = temporary.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandArtifactOrchestratorError(
                "controller create-only temporary is unsafe"
            ) from exc
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_uid != required_uid
            or stat.S_IMODE(temporary_metadata.st_mode) != mode
            or temporary_metadata.st_nlink not in {1, 2}
            or not 0 <= temporary_metadata.st_size <= maximum
        ):
            raise FinlandArtifactOrchestratorError(
                "controller create-only temporary is unsafe"
            )
        if temporary_metadata.st_nlink == 2:
            try:
                destination_metadata = destination.stat(follow_symlinks=False)
            except OSError as exc:
                raise FinlandArtifactOrchestratorError(
                    "controller temporary link identity is ambiguous"
                ) from exc
            if (
                not stat.S_ISREG(destination_metadata.st_mode)
                or temporary_metadata.st_dev != destination_metadata.st_dev
                or temporary_metadata.st_ino != destination_metadata.st_ino
            ):
                raise FinlandArtifactOrchestratorError(
                    "controller temporary link identity is ambiguous"
                )
        temporary.unlink()
        _fsync_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        if _hash_source(
            destination,
            maximum=maximum,
            required_uid=required_uid,
            allowed_modes=frozenset({mode}),
        ) != (expected, len(payload)):
            raise FinlandArtifactOrchestratorError(
                "controller create-only destination differs"
            )
        return expected
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short controller write")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            if _hash_source(
                destination,
                maximum=maximum,
                required_uid=required_uid,
                allowed_modes=frozenset({mode}),
            ) != (expected, len(payload)):
                raise FinlandArtifactOrchestratorError(
                    "controller create-only destination differs"
                )
        _fsync_directory(destination.parent)
        temporary.unlink()
        _fsync_directory(destination.parent)
    except FinlandArtifactOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "controller create-only write failed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return expected


def _copy_to_partial(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    source_modes: frozenset[int],
    destination_mode: int,
    required_uid: int,
) -> None:
    partial = STAGE.transfer_partial_path(destination)
    if partial.exists() or partial.is_symlink():
        try:
            observed = STAGE.hash_secure_file(
                partial,
                required_uid=required_uid,
                expected_mode=destination_mode,
                maximum=max(expected_bytes, 1),
                allow_two_links=True,
            )
        except STAGE.FinlandStageError as exc:
            raise FinlandArtifactOrchestratorError(
                "local transfer partial is unsafe"
            ) from exc
        if observed == (expected_sha256, expected_bytes):
            return
        metadata = partial.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or stat.S_IMODE(metadata.st_mode) != destination_mode
            or metadata.st_nlink != 1
        ):
            raise FinlandArtifactOrchestratorError(
                "local transfer partial cannot be reconciled"
            )
        partial.unlink()
        _fsync_directory(partial.parent)
    descriptor = -1
    try:
        descriptor = os.open(
            partial,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            destination_mode,
        )
        digest = hashlib.sha256()
        size = 0
        with _held_source(
            source,
            maximum=MAX_INPUT_BYTES,
            required_uid=required_uid,
            allowed_modes=source_modes,
        ) as (stream, _metadata):
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_INPUT_BYTES:
                    raise FinlandArtifactOrchestratorError(
                        "local transfer source is oversized"
                    )
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short local transfer write")
                    view = view[written:]
        if (digest.hexdigest(), size) != (expected_sha256, expected_bytes):
            raise FinlandArtifactOrchestratorError(
                "local transfer source changed from its binding"
            )
        os.fchmod(descriptor, destination_mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(partial.parent)
    except FinlandArtifactOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "local transfer partial could not be written"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _state_sha256(journal: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(
            {key: value for key, value in journal.items() if key != "state_sha256"}
        )
    ).hexdigest()


def _validate_journal(journal: Any) -> dict[str, Any]:
    if not isinstance(journal, dict) or set(journal) != JOURNAL_FIELDS:
        raise FinlandArtifactOrchestratorError(
            "controller journal fields are not exact"
        )
    if (
        journal["schema"] != JOURNAL_SCHEMA
        or journal["status"] not in {"active", "complete"}
        or journal["state_sha256"] != _state_sha256(journal)
    ):
        raise FinlandArtifactOrchestratorError(
            "controller journal state hash is invalid"
        )
    STAGE._canonical_uuid4(journal["operation_id"], label="operation_id")
    if (
        SHA40_RE.fullmatch(str(journal["release_sha"])) is None
        or SHA40_RE.fullmatch(str(journal["release_tree_sha"])) is None
    ):
        raise FinlandArtifactOrchestratorError(
            "controller journal release identity is invalid"
        )
    _nonzero_sha256(journal["closure_sha256"], label="closure_sha256")
    _nonzero_sha256(journal["agent_sha256"], label="agent_sha256")
    completed = journal["completed_roles"]
    if (
        not isinstance(completed, list)
        or completed != list(ROLES[: len(completed)])
        or journal["current_role"]
        not in ({None} if len(completed) == len(ROLES) else {None, ROLES[len(completed)]})
        or not isinstance(journal["role_results"], dict)
        or set(journal["role_results"]) != set(completed)
        or (journal["status"] == "complete") != (completed == list(ROLES))
    ):
        raise FinlandArtifactOrchestratorError(
            "controller journal role prefix is invalid"
        )
    return journal


def _reconcile_journal_temporaries(
    path: Path,
    *,
    required_uid: int,
) -> None:
    pattern = re.compile(
        rf"^\.{re.escape(path.name)}\.[1-9][0-9]*\.[0-9a-f]{{16}}\.tmp$"
    )
    try:
        candidates = [
            path.parent / entry.name
            for entry in os.scandir(path.parent)
            if pattern.fullmatch(entry.name)
        ]
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "controller journal temporary inventory is unavailable"
        ) from exc
    if len(candidates) > 64:
        raise FinlandArtifactOrchestratorError(
            "controller journal temporary inventory is excessive"
        )
    changed = False
    for candidate in candidates:
        try:
            metadata = candidate.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandArtifactOrchestratorError(
                "controller journal temporary is unsafe"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink not in {1, 2}
            or not 0 <= metadata.st_size <= MAX_JOURNAL_BYTES
        ):
            raise FinlandArtifactOrchestratorError(
                "controller journal temporary is unsafe"
            )
        if metadata.st_nlink == 2:
            try:
                published = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise FinlandArtifactOrchestratorError(
                    "controller journal temporary link identity is ambiguous"
                ) from exc
            if (
                not stat.S_ISREG(published.st_mode)
                or metadata.st_dev != published.st_dev
                or metadata.st_ino != published.st_ino
            ):
                raise FinlandArtifactOrchestratorError(
                    "controller journal temporary link identity is ambiguous"
                )
        candidate.unlink()
        changed = True
    if changed:
        _fsync_directory(path.parent)


def _write_journal(
    path: Path,
    journal: dict[str, Any],
    *,
    create: bool,
    required_uid: int,
) -> None:
    _reconcile_journal_temporaries(path, required_uid=required_uid)
    journal["state_sha256"] = _state_sha256(journal)
    _validate_journal(journal)
    payload = canonical_json(journal)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short controller journal write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if create:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise FinlandArtifactOrchestratorError(
                    "controller journal already exists"
                ) from exc
        else:
            if not path.exists() or path.is_symlink():
                raise FinlandArtifactOrchestratorError(
                    "controller journal disappeared"
                )
            os.replace(temporary, path)
        _fsync_directory(path.parent)
    except FinlandArtifactOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "controller journal could not be persisted"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists() and not temporary.is_symlink():
            metadata = temporary.stat(follow_symlinks=False)
            if (
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_uid == required_uid
                and stat.S_IMODE(metadata.st_mode) == 0o600
                and metadata.st_nlink in {1, 2}
            ):
                temporary.unlink()


def _load_or_create_journal(
    path: Path,
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    closure_sha256: str,
    agent_sha256: str,
    required_uid: int,
) -> dict[str, Any]:
    _reconcile_journal_temporaries(path, required_uid=required_uid)
    bindings = {
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "closure_sha256": closure_sha256,
        "agent_sha256": agent_sha256,
    }
    if not path.exists() and not path.is_symlink():
        journal: dict[str, Any] = {
            "schema": JOURNAL_SCHEMA,
            **bindings,
            "status": "active",
            "completed_roles": [],
            "current_role": None,
            "role_results": {},
            "state_sha256": "",
        }
        _write_journal(
            path,
            journal,
            create=True,
            required_uid=required_uid,
        )
        return journal
    raw = _read_source(
        path,
        maximum=MAX_JOURNAL_BYTES,
        required_uid=required_uid,
        allowed_modes=frozenset({0o600}),
    )
    journal = _validate_journal(_strict_json(raw, label="controller journal"))
    if any(journal[key] != value for key, value in bindings.items()):
        raise FinlandArtifactOrchestratorError(
            "existing controller journal has different bindings"
        )
    return journal


@contextmanager
def _controller_lock(path: Path, *, required_uid: int) -> Iterator[None]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise FinlandArtifactOrchestratorError(
                "controller lock is unsafe"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FinlandArtifactOrchestratorError(
                "another Finland controller holds the lock"
            ) from exc
        yield
    except FinlandArtifactOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "controller lock could not be acquired"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_manifest_source(
    path: Path,
    payload: bytes,
    *,
    required_uid: int,
) -> None:
    _write_create_only(
        path,
        payload,
        required_uid=required_uid,
        mode=0o600,
        maximum=STAGE.MAX_MANIFEST_BYTES,
    )


def _remote_transfer(
    source: Path,
    destination: Path,
    *,
    ssh_identity: Path,
    runner: Runner,
) -> None:
    _run_command(
        scp_arguments(
            ssh_identity,
            source=source,
            destination=STAGE.transfer_partial_path(destination),
        ),
        runner=runner,
        timeout=7200,
    )


def _stage_local_role(
    *,
    manifest: Mapping[str, Any],
    manifest_path: Path,
    manifest_sha256: str,
    sources: Mapping[str, Path],
    stage_agent: Path,
    agent_sha256: str,
    agent_bytes: int,
    required_uid: int,
    runner: Runner,
) -> dict[str, Any]:
    paths = STAGE.ensure_operation_directories(
        manifest["operation_id"],
        manifest["release_sha"],
        manifest["role"],
        required_uid=required_uid,
    )
    _copy_to_partial(
        stage_agent,
        paths["agent"],  # type: ignore[arg-type]
        expected_sha256=agent_sha256,
        expected_bytes=agent_bytes,
        source_modes=frozenset({0o700, 0o755}),
        destination_mode=0o700,
        required_uid=required_uid,
    )
    bootstrap_output = _run_command(
        _bootstrap_install_arguments(
            operation_id=manifest["operation_id"],
            role=manifest["role"],
            agent_sha256=agent_sha256,
        ),
        runner=runner,
        timeout=60,
    )
    bootstrap = _parse_command_json(
        bootstrap_output,
        label="local bootstrap install",
    )
    if (
        bootstrap.get("schema") != STAGE.VERSION_SCHEMA
        or bootstrap.get("version") != STAGE.AGENT_VERSION
        or bootstrap.get("agent_sha256") != agent_sha256
        or bootstrap.get("installed_path") != str(paths["agent"])
    ):
        raise FinlandArtifactOrchestratorError(
            "local bootstrap installation readback differs"
        )
    version = _parse_command_json(
        _run_command(
            _version_arguments(
                operation_id=manifest["operation_id"],
                release_sha=manifest["release_sha"],
                role=manifest["role"],
                agent_sha256=agent_sha256,
            ),
            runner=runner,
            timeout=60,
        ),
        label="local host agent version",
    )
    _validate_version(
        version,
        agent_sha256=agent_sha256,
        agent_bytes=agent_bytes,
    )
    _copy_to_partial(
        manifest_path,
        paths["manifest"],  # type: ignore[arg-type]
        expected_sha256=manifest_sha256,
        expected_bytes=manifest_path.stat().st_size,
        source_modes=frozenset({0o600}),
        destination_mode=0o600,
        required_uid=required_uid,
    )
    for kind in STAGE.ARTIFACT_KINDS:
        row = manifest["artifacts"][kind]
        _copy_to_partial(
            sources[kind],
            paths["incoming_root"] / row["filename"],  # type: ignore[operator]
            expected_sha256=row["sha256"],
            expected_bytes=row["bytes"],
            source_modes=frozenset({0o600}),
            destination_mode=0o600,
            required_uid=required_uid,
        )
    result = _parse_command_json(
        _run_command(
            _stage_arguments(
                manifest,
                manifest_sha256=manifest_sha256,
                agent_sha256=agent_sha256,
            ),
            runner=runner,
            timeout=7200,
        ),
        label="local host stage",
    )
    return _validate_host_result(
        result,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
    )


def _stage_remote_role(
    *,
    manifest: Mapping[str, Any],
    manifest_path: Path,
    manifest_sha256: str,
    sources: Mapping[str, Path],
    stage_agent: Path,
    agent_sha256: str,
    agent_bytes: int,
    ssh_identity: Path,
    runner: Runner,
) -> dict[str, Any]:
    paths = STAGE.canonical_paths(
        manifest["operation_id"],
        manifest["release_sha"],
        manifest["role"],
    )
    _run_command(
        ssh_arguments(
            ssh_identity,
            remote_arguments=_directory_prepare_arguments(
                operation_id=manifest["operation_id"],
                release_sha=manifest["release_sha"],
                role=manifest["role"],
            ),
        ),
        runner=runner,
        timeout=60,
    )
    _remote_transfer(
        stage_agent,
        paths["agent"],  # type: ignore[arg-type]
        ssh_identity=ssh_identity,
        runner=runner,
    )
    bootstrap = _parse_command_json(
        _run_command(
            ssh_arguments(
                ssh_identity,
                remote_arguments=_bootstrap_install_arguments(
                    operation_id=manifest["operation_id"],
                    role=manifest["role"],
                    agent_sha256=agent_sha256,
                ),
            ),
            runner=runner,
            timeout=60,
        ),
        label="remote bootstrap install",
    )
    if (
        bootstrap.get("schema") != STAGE.VERSION_SCHEMA
        or bootstrap.get("version") != STAGE.AGENT_VERSION
        or bootstrap.get("agent_sha256") != agent_sha256
        or bootstrap.get("installed_path") != str(paths["agent"])
    ):
        raise FinlandArtifactOrchestratorError(
            "remote bootstrap installation readback differs"
        )
    version = _parse_command_json(
        _run_command(
            ssh_arguments(
                ssh_identity,
                remote_arguments=_version_arguments(
                    operation_id=manifest["operation_id"],
                    release_sha=manifest["release_sha"],
                    role=manifest["role"],
                    agent_sha256=agent_sha256,
                ),
            ),
            runner=runner,
            timeout=60,
        ),
        label="remote host agent version",
    )
    _validate_version(
        version,
        agent_sha256=agent_sha256,
        agent_bytes=agent_bytes,
    )
    _remote_transfer(
        manifest_path,
        paths["manifest"],  # type: ignore[arg-type]
        ssh_identity=ssh_identity,
        runner=runner,
    )
    for kind in STAGE.ARTIFACT_KINDS:
        _remote_transfer(
            sources[kind],
            paths["incoming_root"]  # type: ignore[operator]
            / manifest["artifacts"][kind]["filename"],
            ssh_identity=ssh_identity,
            runner=runner,
        )
    result = _parse_command_json(
        _run_command(
            ssh_arguments(
                ssh_identity,
                remote_arguments=_stage_arguments(
                    manifest,
                    manifest_sha256=manifest_sha256,
                    agent_sha256=agent_sha256,
                ),
            ),
            runner=runner,
            timeout=7200,
        ),
        label="remote host stage",
    )
    return _validate_host_result(
        result,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
    )


def _controller_evidence(
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    closure_sha256: str,
    agent_sha256: str,
    manifests: Mapping[str, tuple[Mapping[str, Any], bytes, str]],
    role_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    binding_summaries = {
        role: {
            "schema": ROLE_BINDING_SCHEMA,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "role": role,
            "stage_operation_manifest_sha256": manifests[role][2],
            "stage_attestation_sha256": role_results[role][
                "stage_attestation_sha256"
            ],
            "runtime_image_ids": {
                image_role: role_results[role]["runtime_image_ids"][
                    image_role
                ]
                for image_role in STAGE.IMAGE_ROLES
            },
        }
        for role in ROLES
    }
    if any(
        set(summary) != ROLE_BINDING_FIELDS
        for summary in binding_summaries.values()
    ):
        raise FinlandArtifactOrchestratorError(
            "internal role binding summary fields are not exact"
        )
    stage_bindings = {
        "schema": STAGE_BINDINGS_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "roles": {
            role: {
                "stage_operation_manifest_sha256": manifests[role][2],
                "stage_attestation_sha256": role_results[role][
                    "stage_attestation_sha256"
                ],
                "runtime_image_ids": {
                    image_role: role_results[role]["runtime_image_ids"][
                        image_role
                    ]
                    for image_role in STAGE.IMAGE_ROLES
                },
            }
            for role in ROLES
        },
    }
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": "staged",
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "closure_sha256": closure_sha256,
        "agent_sha256": agent_sha256,
        "roles": {
            role: {
                "host": ROLE_HOSTS[role],
                "transport": ROLE_TRANSPORTS[role],
                "stage_operation_manifest_sha256": manifests[role][2],
                "stage_attestation_sha256": role_results[role][
                    "stage_attestation_sha256"
                ],
                "stage_attestation_path": role_results[role][
                    "stage_attestation_path"
                ],
                "runtime_image_ids": role_results[role]["runtime_image_ids"],
            }
            for role in ROLES
        },
        "binding_summaries": binding_summaries,
        "stage_bindings": stage_bindings,
        "pull_policy": "never",
        "object_storage_used": False,
        "arvan_endpoint_contacted": False,
        "containers_created": False,
        "containers_started": False,
        "services_started": False,
        "networks_created": False,
        "volumes_created": False,
        "current_mutated": False,
        "data_mutated": False,
    }


def orchestrate(
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    closure_manifest: Path,
    stage_agent: Path = DEFAULT_STAGE_AGENT,
    ssh_identity: Path = DEFAULT_SSH_IDENTITY,
    apply: bool = False,
    confirm: str | None = None,
    required_uid: int = 0,
    runner: Runner | None = None,
    checkpoint: Checkpoint | None = None,
    observed_host_addresses: set[str] | None = None,
    control_fd: int | None = None,
) -> dict[str, Any]:
    callback = checkpoint if checkpoint is not None else (lambda _name: None)
    STAGE._canonical_uuid4(operation_id, label="operation_id")
    if (
        SHA40_RE.fullmatch(release_sha) is None
        or SHA40_RE.fullmatch(release_tree_sha) is None
    ):
        raise FinlandArtifactOrchestratorError(
            "release commit or tree identity is invalid"
        )
    closure, _closure_raw, closure_sha256 = load_release_closure(
        closure_manifest,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        required_uid=required_uid,
    )
    sources = _artifact_sources(
        closure_manifest,
        closure,
        required_uid=required_uid,
    )
    agent_sha256, agent_bytes = _hash_source(
        stage_agent,
        maximum=MAX_AGENT_BYTES,
        required_uid=required_uid,
        allowed_modes=frozenset({0o700, 0o755}),
    )
    plan = render_plan(
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        closure_sha256=closure_sha256,
        closure=closure,
        sources=sources,
        stage_agent=stage_agent,
        agent_sha256=agent_sha256,
        agent_bytes=agent_bytes,
        ssh_identity=ssh_identity,
    )
    if not apply:
        if control_fd is not None:
            raise FinlandArtifactOrchestratorError(
                "controller liveness is valid only in apply mode"
            )
        return plan
    if confirm != confirmation_phrase(operation_id, release_sha):
        raise FinlandArtifactOrchestratorError(
            "Finland production-shadow stage confirmation mismatch"
        )
    if _ACTIVE_EXECUTION_AUTHORITY is None:
        with _execution_authority(control_fd):
            return orchestrate(
                operation_id=operation_id,
                release_sha=release_sha,
                release_tree_sha=release_tree_sha,
                closure_manifest=closure_manifest,
                stage_agent=stage_agent,
                ssh_identity=ssh_identity,
                apply=apply,
                confirm=confirm,
                required_uid=required_uid,
                runner=runner,
                checkpoint=checkpoint,
                observed_host_addresses=observed_host_addresses,
            )
    if control_fd is not None:
        raise FinlandArtifactOrchestratorError(
            "controller execution authority is already active"
        )
    _ACTIVE_EXECUTION_AUTHORITY.check()
    if os.geteuid() != required_uid or required_uid != 0:
        raise FinlandArtifactOrchestratorError(
            "Finland artifact controller must run as root"
        )
    STAGE._verify_role_host(
        "bot_fi",
        observed_host_addresses=observed_host_addresses,
    )
    if (
        not ssh_identity.is_absolute()
        or ssh_identity.is_symlink()
        or not ssh_identity.is_file()
    ):
        raise FinlandArtifactOrchestratorError("SSH identity path is unsafe")
    try:
        identity_metadata = ssh_identity.stat(follow_symlinks=False)
    except OSError as exc:
        raise FinlandArtifactOrchestratorError(
            "SSH identity path is unsafe"
        ) from exc
    if (
        not stat.S_ISREG(identity_metadata.st_mode)
        or identity_metadata.st_uid != required_uid
        or stat.S_IMODE(identity_metadata.st_mode) != 0o600
        or identity_metadata.st_nlink != 1
    ):
        raise FinlandArtifactOrchestratorError("SSH identity path is unsafe")

    controller_paths = _ensure_controller_directories(
        operation_id,
        required_uid=required_uid,
    )
    agent_payload = _read_source(
        stage_agent,
        maximum=MAX_AGENT_BYTES,
        required_uid=required_uid,
        allowed_modes=frozenset({0o700, 0o755}),
    )
    if (
        hashlib.sha256(agent_payload).hexdigest() != agent_sha256
        or len(agent_payload) != agent_bytes
    ):
        raise FinlandArtifactOrchestratorError(
            "stage agent changed after plan validation"
        )
    _write_create_only(
        controller_paths["agent"],
        agent_payload,
        required_uid=required_uid,
        mode=0o700,
        maximum=MAX_AGENT_BYTES,
    )
    manifests: dict[str, tuple[Mapping[str, Any], bytes, str]] = {}
    for role in ROLES:
        manifest = build_stage_manifest(
            operation_id=operation_id,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            role=role,
            closure=closure,
            agent_sha256=agent_sha256,
        )
        payload = canonical_json(manifest)
        digest = hashlib.sha256(payload).hexdigest()
        manifest_path = controller_paths[f"manifest_{role}"]
        _write_manifest_source(
            manifest_path,
            payload,
            required_uid=required_uid,
        )
        manifests[role] = (manifest, payload, digest)

    with _controller_lock(
        controller_paths["lock"],
        required_uid=required_uid,
    ):
        journal = _load_or_create_journal(
            controller_paths["journal"],
            operation_id=operation_id,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            closure_sha256=closure_sha256,
            agent_sha256=agent_sha256,
            required_uid=required_uid,
        )
        for role in ROLES:
            manifest, _payload, manifest_sha256 = manifests[role]
            if role in journal["completed_roles"]:
                _validate_host_result(
                    journal["role_results"][role],
                    manifest=manifest,
                    manifest_sha256=manifest_sha256,
                )
                continue
            if journal["current_role"] not in {None, role}:
                raise FinlandArtifactOrchestratorError(
                    "controller journal current role is invalid"
                )
            if journal["current_role"] is None:
                journal["current_role"] = role
                _write_journal(
                    controller_paths["journal"],
                    journal,
                    create=False,
                    required_uid=required_uid,
                )
            callback(f"before-role:{role}")
            if role == "bot_fi":
                result = _stage_local_role(
                    manifest=manifest,
                    manifest_path=controller_paths[f"manifest_{role}"],
                    manifest_sha256=manifest_sha256,
                    sources=sources,
                    stage_agent=controller_paths["agent"],
                    agent_sha256=agent_sha256,
                    agent_bytes=agent_bytes,
                    required_uid=required_uid,
                    runner=runner,
                )
            else:
                result = _stage_remote_role(
                    manifest=manifest,
                    manifest_path=controller_paths[f"manifest_{role}"],
                    manifest_sha256=manifest_sha256,
                    sources=sources,
                    stage_agent=controller_paths["agent"],
                    agent_sha256=agent_sha256,
                    agent_bytes=agent_bytes,
                    ssh_identity=ssh_identity,
                    runner=runner,
                )
            callback(f"after-role-command:{role}")
            journal["role_results"][role] = result
            journal["completed_roles"].append(role)
            journal["current_role"] = None
            if journal["completed_roles"] == list(ROLES):
                journal["status"] = "complete"
            _write_journal(
                controller_paths["journal"],
                journal,
                create=False,
                required_uid=required_uid,
            )
            callback(f"after-role:{role}")

        evidence = _controller_evidence(
            operation_id=operation_id,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            closure_sha256=closure_sha256,
            agent_sha256=agent_sha256,
            manifests=manifests,
            role_results=journal["role_results"],
        )
        evidence_payload = canonical_json(evidence)
        evidence_sha256 = _write_create_only(
            controller_paths["evidence"],
            evidence_payload,
            required_uid=required_uid,
            mode=0o600,
            maximum=MAX_EVIDENCE_BYTES,
        )
        return {
            **evidence,
            "evidence_path": str(controller_paths["evidence"]),
            "evidence_sha256": evidence_sha256,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree-sha", required=True)
    parser.add_argument("--closure-manifest", type=Path, required=True)
    parser.add_argument("--stage-agent", type=Path, default=DEFAULT_STAGE_AGENT)
    parser.add_argument("--ssh-identity", type=Path, default=DEFAULT_SSH_IDENTITY)
    parser.add_argument("--pull", choices=("never",), default="never")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.pull != "never":
            raise FinlandArtifactOrchestratorError(
                "only --pull never is supported"
            )
        result = orchestrate(
            operation_id=args.operation_id,
            release_sha=args.release_sha,
            release_tree_sha=args.release_tree_sha,
            closure_manifest=args.closure_manifest,
            stage_agent=args.stage_agent,
            ssh_identity=args.ssh_identity,
            apply=args.apply,
            confirm=args.confirm,
            required_uid=0,
        )
        print(canonical_json(result).decode("ascii"))
        return 0
    except (
        FinlandArtifactOrchestratorError,
        STAGE.FinlandStageError,
    ) as exc:
        print(
            canonical_json(
                {
                    "schema": EVIDENCE_SCHEMA,
                    "status": "blocked",
                    "error": str(exc),
                }
            ).decode("ascii"),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
