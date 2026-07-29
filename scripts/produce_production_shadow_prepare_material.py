#!/usr/bin/env python3
"""Build deterministic, create-only production-shadow prepare role material.

The Docker role archives deliberately contain only the role-local prepare
Compose, its root-only environment, the operation DR CA certificate, and an
internal manifest.  Activation credentials are outside this producer's
contract.

The internal ``operation_manifest_sha256`` field binds the immutable stage
manifest that precedes image loading.  It must never be the later precommit or
controller manifest, because those manifests bind the resulting archive hash.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
from typing import Any, Mapping
import uuid

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import (
    ec,
    ed25519,
    ed448,
    rsa,
)
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_bytes, read_secure_text
from scripts.render_three_site_production_shadow_role_compose import (
    ProductionShadowRoleError,
    canonical_role_compose_bytes,
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
    required_environment_names,
)
from scripts.verify_three_site_production_shadow_compose import (
    collect_source_failures,
)
from scripts import production_shadow_convergence_runtime_targets as runtime_targets


SET_SCHEMA = runtime_targets.PREPARE_MATERIAL_SET_SCHEMA
LEGACY_SET_SCHEMA = runtime_targets.LEGACY_PREPARE_MATERIAL_SET_SCHEMA
STAGE_BINDINGS_SCHEMA = "production-shadow-image-stage-bindings-v1"
FI_FINAL_PREPARE_SCHEMA = "production-shadow-final-prepare-material-v1"
WA_IR_FINAL_PREPARE_SCHEMA = "wa-ir-production-final-prepare-material-v1"
WITNESS_PUBLIC_INPUT_SCHEMA = (
    "production-shadow-witness-public-prepare-input-v1"
)
WITNESS_PREPARE_SCHEMA = "production-shadow-witness-prepare-material-v1"
DR_CA_ATTESTATION_SCHEMA = "production-shadow-dr-ca-attestation-v1"
CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA
)
CONVERGENCE_RUNTIME_TARGETS_FILENAME = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGETS_FILENAME
)

DOCKER_ROLES = runtime_targets.CONVERGENCE_RUNTIME_TARGET_ROLES
ALL_ROLES = (*DOCKER_ROLES, "witness")
IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
ROLE_RENDER_NAMES = {
    "bot_fi": "bot-fi",
    "webapp_fi": "webapp-fi",
    "webapp_ir": "webapp-ir",
}
ROLE_PATH_NAMES = dict(ROLE_RENDER_NAMES)
ROLE_TRANSPORTS = {
    "bot_fi": "local-controller",
    "webapp_fi": "ssh-control",
    "webapp_ir": "ssh-control-object-storage-payload-only",
    "witness": "ssh-control-object-storage-payload-only",
}
ROLE_FORMATS = {
    "bot_fi": "production-shadow-role-material-tar",
    "webapp_fi": "production-shadow-role-material-tar",
    "webapp_ir": "production-shadow-role-material-tar",
    "witness": "production-shadow-witness-material-tar",
}
ROLE_ARCHIVE_NAMES = {
    role: f"role-material-{role.replace('_', '-')}.tar"
    for role in ALL_ROLES
}
FINAL_PREPARE_MANIFEST_NAME = "final-prepare-manifest.json"
WITNESS_MANIFEST_NAME = "witness-prepare-manifest.json"
WITNESS_ATTESTATION_NAME = "witness-public-attestation.json"
CONVERGENCE_RUNTIME_TARGET_SET_FIELDS = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGET_SET_FIELDS
)
CONVERGENCE_RUNTIME_TARGET_ROLE_FIELDS = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGET_ROLE_FIELDS
)
CONVERGENCE_RUNTIME_TARGET_DESCRIPTOR_FIELDS = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGET_DESCRIPTOR_FIELDS
)
CONVERGENCE_RUNTIME_OBSERVER_SERVICES = {
    role: f"{role}_sync_observer" for role in DOCKER_ROLES
}
CONVERGENCE_RUNTIME_IDENTITY_ENV_FIELDS = (
    "TZ",
    "ENVIRONMENT",
    "TOPOLOGY_SCHEMA_VERSION",
    "THREE_SITE_DR_ENABLED",
    "DR_EVENT_PROTOCOL_ENABLED",
    "DR_EVENT_PROTOCOL_STRICT",
    "RELEASE_SHA",
    "SERVER_MODE",
    "LOGICAL_AUTHORITY",
    "PHYSICAL_SITE",
)
CONVERGENCE_RUNTIME_DATABASE_ENV_FIELDS = (
    "DATABASE_URL",
    "SYNC_DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
IMAGE_ENV_NAMES = frozenset(
    {
        "PRODUCTION_SHADOW_APP_IMAGE_ID",
        "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID",
        "PRODUCTION_SHADOW_REDIS_IMAGE_ID",
        "PRODUCTION_SHADOW_NGINX_IMAGE_ID",
    }
)
IMAGE_ENV_BY_KIND = {
    "app": "PRODUCTION_SHADOW_APP_IMAGE_ID",
    "postgres": "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID",
    "redis": "PRODUCTION_SHADOW_REDIS_IMAGE_ID",
    "nginx": "PRODUCTION_SHADOW_NGINX_IMAGE_ID",
}
RUNTIME_IMAGE_COMPOSE_EXTENSION = {
    kind: (
        "${"
        + IMAGE_ENV_BY_KIND[kind]
        + ":?immutable local "
        + kind
        + " image ID is required}"
    )
    for kind in IMAGE_KINDS
}
STAGE_BINDING_FIELDS = frozenset(
    {
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
        "runtime_image_ids",
    }
)
FINAL_PREPARE_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "operation_manifest_sha256",
        "stage_attestation_sha256",
        "role",
        "runtime_image_ids",
        "entries",
        "required_env_keys",
    }
)
FINAL_PREPARE_ENTRY_FIELDS = frozenset(
    {"archive_path", "destination", "sha256", "bytes", "mode"}
)
WITNESS_PUBLIC_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "release_manifest_sha256",
        "health_attestation_sha256",
        "health_attested_at_epoch",
        "ca_sha256",
        "server_cert_sha256",
        "native_release_reused",
        "current_mutated",
        "service_mutated",
        "legacy_secret_material_copied",
    }
)
DR_CA_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "ca_sha256",
        "ca_subject",
        "ca_serial_hex",
        "not_before",
        "not_after",
        "generated_at",
        "private_key_mode",
        "private_key_retained_on_controller",
        "old_tls_material_reused",
    }
)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_INTERPOLATION_RE = re.compile(
    r"\$\{([A-Z][A-Z0-9_]*):\?[^{}]*\}"
)
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 16 * 1024 * 1024
MIN_CA_REMAINING_SECONDS = 30 * 24 * 60 * 60
MAX_CA_ATTESTATION_AGE_SECONDS = 24 * 60 * 60
MAX_CA_ATTESTATION_FUTURE_SKEW_SECONDS = 5 * 60
MAX_CA_CERTIFICATE_BACKDATE_SECONDS = 60 * 60
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)
FORBIDDEN_PREPARE_ENV_FRAGMENTS = (
    "BOT_TOKEN",
    "BOT_USERNAME",
    "CHANNEL_ID",
    "KEYRING",
    "PRIVATE_KEY",
    "VAPID",
    "WITNESS",
    "AWS_",
    "S3_",
    "ARVAN",
    "TELEGRAM",
)


class PrepareMaterialError(RuntimeError):
    """Raised when prepare-only material cannot be proven closed and safe."""


@dataclass(frozen=True)
class RoleArtifact:
    role: str
    filename: str
    sha256: str
    bytes: int
    format: str
    transport: str
    internal_manifest_sha256: str
    stage_operation_manifest_sha256: str
    stage_attestation_sha256: str
    publication: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PrepareMaterialError(f"JSON contains duplicate key: {key}")
        value[key] = item
    return value


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise PrepareMaterialError(f"{label} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise PrepareMaterialError(f"{label} must be a canonical UUID") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise PrepareMaterialError(f"{label} must be a canonical UUIDv4")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise PrepareMaterialError(f"{label} must be a nonzero SHA-256")
    return value


def _read_json_secure(
    path: Path,
    *,
    label: str,
    required_uid: int,
    maximum: int = MAX_INPUT_BYTES,
) -> tuple[dict[str, Any], bytes]:
    raw = read_secure_bytes(
        path,
        label=label,
        owner_uid=required_uid,
        max_size=maximum,
    )
    if not raw:
        raise PrepareMaterialError(f"{label} is empty")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except PrepareMaterialError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrepareMaterialError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise PrepareMaterialError(f"{label} must be a JSON object")
    return value, raw


def _role_destinations(role: str) -> dict[str, str]:
    role_path = ROLE_PATH_NAMES[role]
    return {
        "role-compose.yml": (
            f"rendered/{role_path}/docker-compose.yml"
        ),
        "runtime.env.role": (
            f"secrets/{role_path}/runtime.env.role"
        ),
        "ca.crt": "secrets/tls/ca.crt",
    }


def _operation_values(operation_id: str, release_sha: str) -> dict[str, str]:
    project = f"tb3p-{operation_id.replace('-', '')}"
    project_root = (
        f"/srv/trading-bot-three-site-production-shadow/{operation_id}"
    )
    return {
        "PRODUCTION_SHADOW_OPERATION_ID": operation_id,
        "PRODUCTION_SHADOW_PROJECT": project,
        "PRODUCTION_SHADOW_CGROUP_PARENT": project,
        "PRODUCTION_SHADOW_PROJECT_ROOT": project_root,
        "PRODUCTION_SHADOW_RELEASE_ROOT": (
            f"{project_root}/releases/{release_sha}"
        ),
        "PRODUCTION_SHADOW_DATA_ROOT": (
            "/srv/trading-bot-three-site-production-shadow-data/"
            f"{operation_id}"
        ),
        "PRODUCTION_SHADOW_SECRET_ROOT": (
            "/root/secure-envs/trading-bot/three-site-production-shadow/"
            f"{operation_id}"
        ),
        "PRODUCTION_SHADOW_RELEASE_SHA": release_sha,
    }


def _validate_stage_bindings(
    document: dict[str, Any],
    *,
    operation_id: str,
    release_sha: str,
) -> dict[str, dict[str, Any]]:
    if set(document) != {"schema", "operation_id", "release_sha", "roles"}:
        raise PrepareMaterialError("stage binding fields are not exact")
    if (
        document["schema"] != STAGE_BINDINGS_SCHEMA
        or document["operation_id"] != operation_id
        or document["release_sha"] != release_sha
    ):
        raise PrepareMaterialError("stage binding identity differs")
    roles = document["roles"]
    if not isinstance(roles, dict) or set(roles) != set(ALL_ROLES):
        raise PrepareMaterialError("stage binding roles are not exact")
    validated: dict[str, dict[str, Any]] = {}
    for role in ALL_ROLES:
        row = roles[role]
        if not isinstance(row, dict) or set(row) != STAGE_BINDING_FIELDS:
            raise PrepareMaterialError(
                f"stage binding fields for {role} are not exact"
            )
        stage_manifest = _nonzero_sha256(
            row["stage_operation_manifest_sha256"],
            label=f"{role} stage operation manifest",
        )
        stage_attestation = _nonzero_sha256(
            row["stage_attestation_sha256"],
            label=f"{role} stage attestation",
        )
        runtime_ids = row["runtime_image_ids"]
        expected_keys = set(IMAGE_KINDS) if role in DOCKER_ROLES else set()
        if (
            not isinstance(runtime_ids, dict)
            or set(runtime_ids) != expected_keys
            or any(
                not isinstance(value, str)
                or IMAGE_ID_RE.fullmatch(value) is None
                or value == "sha256:" + "0" * 64
                for value in runtime_ids.values()
            )
            or len(set(runtime_ids.values())) != len(runtime_ids)
        ):
            raise PrepareMaterialError(
                f"runtime image IDs for {role} are invalid"
            )
        validated[role] = {
            "stage_operation_manifest_sha256": stage_manifest,
            "stage_attestation_sha256": stage_attestation,
            "runtime_image_ids": {
                kind: runtime_ids[kind] for kind in IMAGE_KINDS
            }
            if role in DOCKER_ROLES
            else {},
        }
    return validated


def _certificate_timestamp(value: datetime) -> str:
    aware = value.replace(tzinfo=timezone.utc)
    return aware.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_canonical_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PrepareMaterialError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PrepareMaterialError(
            f"{label} must be a canonical UTC timestamp"
        ) from exc
    if (
        parsed.microsecond != 0
        or parsed.tzinfo is None
        or parsed.isoformat(timespec="seconds").replace("+00:00", "Z") != value
    ):
        raise PrepareMaterialError(f"{label} must be a canonical UTC timestamp")
    return parsed


def _validate_ca_certificate(
    raw: bytes,
    *,
    operation_id: str,
    now: datetime | None = None,
) -> x509.Certificate:
    if (
        not raw
        or len(raw) > 1024 * 1024
        or any(marker in raw for marker in PRIVATE_KEY_MARKERS)
        or raw.count(b"-----BEGIN CERTIFICATE-----") != 1
        or raw.count(b"-----END CERTIFICATE-----") != 1
    ):
        raise PrepareMaterialError(
            "DR CA input must contain exactly one certificate and no private key"
        )
    try:
        certificate = x509.load_pem_x509_certificate(raw)
        basic = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
    except (ValueError, x509.ExtensionNotFound) as exc:
        raise PrepareMaterialError("DR CA certificate is invalid") from exc
    if not basic.ca or certificate.subject != certificate.issuer:
        raise PrepareMaterialError(
            "DR CA certificate must be a self-issued CA"
        )
    try:
        certificate.verify_directly_issued_by(certificate)
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise PrepareMaterialError(
            "DR CA certificate self-signature is invalid"
        ) from exc
    common_names = certificate.subject.get_attributes_for_oid(
        x509.NameOID.COMMON_NAME
    )
    if (
        len(common_names) != 1
        or operation_id not in common_names[0].value.lower()
    ):
        raise PrepareMaterialError(
            "DR CA common name is not bound to the operation UUID"
        )
    public_key = certificate.public_key()
    if isinstance(public_key, rsa.RSAPublicKey):
        strong_key = public_key.key_size >= 3072
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        strong_key = public_key.key_size >= 256
    else:
        strong_key = isinstance(
            public_key,
            (
                ed25519.Ed25519PublicKey,
                ed448.Ed448PublicKey,
            ),
        )
    if not strong_key:
        raise PrepareMaterialError("DR CA public key is too weak")
    current = now or datetime.now(timezone.utc)
    not_before = certificate.not_valid_before.replace(tzinfo=timezone.utc)
    not_after = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    if (
        not_before > current
        or (not_after - current).total_seconds() < MIN_CA_REMAINING_SECONDS
    ):
        raise PrepareMaterialError(
            "DR CA certificate is not currently valid for the minimum lifetime"
        )
    return certificate


def _validate_dr_ca_attestation(
    document: dict[str, Any],
    raw: bytes,
    *,
    operation_id: str,
    release_sha: str,
    ca_bytes: bytes,
    certificate: x509.Certificate,
    now: datetime | None = None,
) -> tuple[str, int]:
    if set(document) != DR_CA_ATTESTATION_FIELDS:
        raise PrepareMaterialError("DR CA attestation fields are not exact")
    if raw != _canonical_json(document):
        raise PrepareMaterialError(
            "DR CA attestation must use canonical JSON bytes"
        )
    if (
        document["schema"] != DR_CA_ATTESTATION_SCHEMA
        or document["operation_id"] != operation_id
        or document["release_sha"] != release_sha
    ):
        raise PrepareMaterialError("DR CA attestation identity differs")
    ca_sha256 = hashlib.sha256(ca_bytes).hexdigest()
    if document["ca_sha256"] != ca_sha256:
        raise PrepareMaterialError(
            "DR CA attestation certificate hash differs"
        )
    if (
        document["ca_subject"] != certificate.subject.rfc4514_string()
        or document["ca_serial_hex"] != format(certificate.serial_number, "x")
        or document["not_before"]
        != _certificate_timestamp(certificate.not_valid_before)
        or document["not_after"]
        != _certificate_timestamp(certificate.not_valid_after)
    ):
        raise PrepareMaterialError(
            "DR CA attestation certificate identity differs"
        )
    if (
        document["private_key_mode"] != "0600"
        or document["private_key_retained_on_controller"] is not True
        or document["old_tls_material_reused"] is not False
    ):
        raise PrepareMaterialError(
            "DR CA attestation private-key isolation contract differs"
        )
    generated_at = _parse_canonical_timestamp(
        document["generated_at"],
        label="DR CA generated_at",
    )
    current = now or datetime.now(timezone.utc)
    age = (current - generated_at).total_seconds()
    if age < -MAX_CA_ATTESTATION_FUTURE_SKEW_SECONDS:
        raise PrepareMaterialError("DR CA attestation is from the future")
    if age > MAX_CA_ATTESTATION_AGE_SECONDS:
        raise PrepareMaterialError("DR CA attestation is stale")
    certificate_not_before = certificate.not_valid_before.replace(
        tzinfo=timezone.utc
    )
    certificate_age = (
        generated_at - certificate_not_before
    ).total_seconds()
    if not 0 <= certificate_age <= MAX_CA_CERTIFICATE_BACKDATE_SECONDS:
        raise PrepareMaterialError(
            "DR CA certificate was not freshly generated for this attestation"
        )
    return hashlib.sha256(raw).hexdigest(), int(generated_at.timestamp())


def _validate_witness_public_input(
    document: dict[str, Any],
    *,
    operation_id: str,
    release_sha: str,
) -> None:
    if set(document) != WITNESS_PUBLIC_FIELDS:
        raise PrepareMaterialError(
            "Witness public prepare input fields are not exact"
        )
    if (
        document["schema"] != WITNESS_PUBLIC_INPUT_SCHEMA
        or document["operation_id"] != operation_id
        or document["release_sha"] != release_sha
        or not isinstance(document["release_tree_sha"], str)
        or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
        or document["release_tree_sha"] == "0" * 40
    ):
        raise PrepareMaterialError(
            "Witness public prepare input identity differs"
        )
    for field in (
        "release_manifest_sha256",
        "health_attestation_sha256",
        "ca_sha256",
        "server_cert_sha256",
    ):
        _nonzero_sha256(document[field], label=f"Witness {field}")
    epoch = document["health_attested_at_epoch"]
    if (
        isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or not 1 <= epoch <= 4_102_444_800
    ):
        raise PrepareMaterialError(
            "Witness health attestation epoch is invalid"
        )
    witness_age = datetime.now(timezone.utc).timestamp() - epoch
    if witness_age < -MAX_CA_ATTESTATION_FUTURE_SKEW_SECONDS:
        raise PrepareMaterialError(
            "Witness health attestation is from the future"
        )
    if witness_age > MAX_CA_ATTESTATION_AGE_SECONDS:
        raise PrepareMaterialError("Witness health attestation is stale")
    exact_booleans = {
        "native_release_reused": True,
        "current_mutated": False,
        "service_mutated": False,
        "legacy_secret_material_copied": False,
    }
    if any(document[field] is not expected for field, expected in exact_booleans.items()):
        raise PrepareMaterialError(
            "Witness prepare input does not preserve the native no-secret contract"
        )


def _safe_tar_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or value.endswith("/")
    ):
        raise PrepareMaterialError("archive member path is unsafe")
    path = PurePosixPath(value)
    if path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise PrepareMaterialError("archive member path is unsafe")
    return value


def _tar_bytes(files: Mapping[str, bytes]) -> bytes:
    if not files:
        raise PrepareMaterialError("archive member inventory is empty")
    stream = io.BytesIO()
    with tarfile.open(
        fileobj=stream,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as archive:
        for name in files:
            safe_name = _safe_tar_name(name)
            payload = files[name]
            if (
                not isinstance(payload, bytes)
                or not 1 <= len(payload) <= MAX_ARCHIVE_MEMBER_BYTES
            ):
                raise PrepareMaterialError(
                    f"archive payload {safe_name} is empty or oversized"
                )
            member = tarfile.TarInfo(safe_name)
            member.size = len(payload)
            member.mode = 0o600
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = 0
            member.type = tarfile.REGTYPE
            archive.addfile(member, io.BytesIO(payload))
    result = stream.getvalue()
    if not 1 <= len(result) <= MAX_ARCHIVE_BYTES:
        raise PrepareMaterialError("role material archive is oversized")
    return result


def validate_role_archive_bytes(
    payload: bytes,
    *,
    expected_files: Mapping[str, bytes],
) -> None:
    """Validate exact archive shape, metadata, and member bytes."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_ARCHIVE_BYTES:
        raise PrepareMaterialError("role archive is empty or oversized")
    observed: dict[str, bytes] = {}
    names: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for member in archive:
                name = _safe_tar_name(member.name)
                if name in names:
                    raise PrepareMaterialError(
                        "role archive contains a duplicate member"
                    )
                names.add(name)
                if (
                    not member.isreg()
                    or member.uid != 0
                    or member.gid != 0
                    or stat.S_IMODE(member.mode) != 0o600
                    or member.mtime != 0
                    or member.uname not in {"", None}
                    or member.gname not in {"", None}
                    or member.pax_headers
                    or not 1 <= member.size <= MAX_ARCHIVE_MEMBER_BYTES
                ):
                    raise PrepareMaterialError(
                        "role archive member metadata is unsafe"
                    )
                source = archive.extractfile(member)
                if source is None:
                    raise PrepareMaterialError(
                        "role archive regular member is unreadable"
                    )
                content = source.read(member.size + 1)
                if len(content) != member.size:
                    raise PrepareMaterialError(
                        "role archive member size differs"
                    )
                observed[name] = content
    except PrepareMaterialError:
        raise
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise PrepareMaterialError("role archive is invalid") from exc
    if set(observed) != set(expected_files):
        raise PrepareMaterialError("role archive member set is not exact")
    for name, expected in expected_files.items():
        if observed[name] != expected:
            raise PrepareMaterialError(
                f"role archive member differs: {name}"
            )


def _manifest_entries(
    payloads: Mapping[str, bytes],
    *,
    destinations: Mapping[str, str],
) -> list[dict[str, Any]]:
    if set(payloads) != set(destinations):
        raise PrepareMaterialError(
            "prepare payload and destination inventories differ"
        )
    return [
        {
            "archive_path": archive_path,
            "destination": destinations[archive_path],
            "sha256": hashlib.sha256(payloads[archive_path]).hexdigest(),
            "bytes": len(payloads[archive_path]),
            "mode": "0600",
        }
        for archive_path in ("role-compose.yml", "runtime.env.role", "ca.crt")
    ]


def _forbidden_prepare_environment(names: set[str]) -> list[str]:
    return sorted(
        name
        for name in names
        if any(fragment in name for fragment in FORBIDDEN_PREPARE_ENV_FRAGMENTS)
    )


def _build_docker_role(
    *,
    role: str,
    operation_id: str,
    release_sha: str,
    canonical_payload: dict[str, Any],
    source_values: dict[str, str],
    ca_bytes: bytes,
    ca_attestation_sha256: str,
    ca_attested_at_epoch: int,
    stage_binding: Mapping[str, Any],
) -> tuple[bytes, bytes, dict[str, bytes]]:
    rendered = render_role_compose(
        canonical_payload,
        role=ROLE_RENDER_NAMES[role],
        scope="prepare",
    )
    # The prepare services use only app/PostgreSQL, but the installed role
    # environment is the immutable handoff to later phases.  Keep all four
    # local IDs in the Compose interpolation closure without enabling a
    # Redis, Nginx, private-plane, or public service.
    rendered["x-production-shadow-runtime-image-ids"] = dict(
        RUNTIME_IMAGE_COMPOSE_EXTENSION
    )
    runtime_ids = stage_binding["runtime_image_ids"]
    values = dict(source_values)
    expected_operation_values = _operation_values(operation_id, release_sha)
    for name, expected in expected_operation_values.items():
        if name in values and values[name] != expected:
            raise PrepareMaterialError(
                f"canonical environment {name} differs from operation identity"
            )
        values[name] = expected
    for kind, env_name in IMAGE_ENV_BY_KIND.items():
        values[env_name] = runtime_ids[kind]
    ca_sha256 = hashlib.sha256(ca_bytes).hexdigest()
    if values.get("PRODUCTION_SHADOW_DR_CA_SHA256") != ca_sha256:
        raise PrepareMaterialError(
            "canonical environment DR CA hash differs from the supplied certificate"
        )
    if (
        values.get("PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256")
        != ca_attestation_sha256
    ):
        raise PrepareMaterialError(
            "canonical environment DR TLS attestation hash differs"
        )
    attested_epoch = values.get("PRODUCTION_SHADOW_DR_TLS_ATTESTED_AT_EPOCH")
    if attested_epoch != str(ca_attested_at_epoch):
        raise PrepareMaterialError(
            "canonical environment DR TLS attestation epoch differs"
        )

    runtime_target_source_names = _runtime_target_source_names(
        canonical_payload,
        role=role,
    )
    required = required_environment_names(rendered) | runtime_target_source_names
    optional = (referenced_environment_names(rendered) - required) | IMAGE_ENV_NAMES
    try:
        environment = canonical_role_env_bytes(
            values,
            required_names=required,
            optional_names=optional,
        )
    except ProductionShadowRoleError as exc:
        raise PrepareMaterialError(str(exc)) from exc
    selected_names = set(parse_env_values(environment.decode("ascii")))
    forbidden = _forbidden_prepare_environment(selected_names)
    if forbidden:
        raise PrepareMaterialError(
            "prepare environment contains activation/provider material: "
            + ",".join(forbidden)
        )
    if not IMAGE_ENV_NAMES <= selected_names:
        raise PrepareMaterialError(
            "prepare environment does not bind all four local runtime image IDs"
        )

    compose = canonical_role_compose_bytes(rendered)
    payloads = {
        "role-compose.yml": compose,
        "runtime.env.role": environment,
        "ca.crt": ca_bytes,
    }
    manifest = {
        "schema": (
            WA_IR_FINAL_PREPARE_SCHEMA
            if role == "webapp_ir"
            else FI_FINAL_PREPARE_SCHEMA
        ),
        "operation_id": operation_id,
        "release_sha": release_sha,
        "operation_manifest_sha256": stage_binding[
            "stage_operation_manifest_sha256"
        ],
        "stage_attestation_sha256": stage_binding[
            "stage_attestation_sha256"
        ],
        "role": role,
        "runtime_image_ids": {
            kind: runtime_ids[kind] for kind in IMAGE_KINDS
        },
        "entries": _manifest_entries(
            payloads,
            destinations=_role_destinations(role),
        ),
        "required_env_keys": sorted(selected_names),
    }
    if set(manifest) != FINAL_PREPARE_FIELDS:
        raise PrepareMaterialError(
            "internal final prepare manifest fields are not exact"
        )
    manifest_bytes = _canonical_json(manifest)
    files = {
        FINAL_PREPARE_MANIFEST_NAME: manifest_bytes,
        **payloads,
    }
    archive_bytes = _tar_bytes(files)
    validate_role_archive_bytes(archive_bytes, expected_files=files)
    return archive_bytes, manifest_bytes, files


def _build_witness_role(
    *,
    operation_id: str,
    release_sha: str,
    public_document: dict[str, Any],
    public_bytes: bytes,
    stage_binding: Mapping[str, Any],
) -> tuple[bytes, bytes, dict[str, bytes]]:
    _validate_witness_public_input(
        public_document,
        operation_id=operation_id,
        release_sha=release_sha,
    )
    public_sha256 = hashlib.sha256(public_bytes).hexdigest()
    if public_sha256 != stage_binding["stage_attestation_sha256"]:
        raise PrepareMaterialError(
            "Witness public input differs from its stage attestation binding"
        )
    manifest = {
        "schema": WITNESS_PREPARE_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "operation_manifest_sha256": stage_binding[
            "stage_operation_manifest_sha256"
        ],
        "stage_attestation_sha256": public_sha256,
        "role": "witness",
        "runtime_image_ids": {},
        "entries": [
            {
                "archive_path": WITNESS_ATTESTATION_NAME,
                "destination": (
                    "attestations/witness-public-prepare.json"
                ),
                "sha256": public_sha256,
                "bytes": len(public_bytes),
                "mode": "0600",
            }
        ],
        "required_env_keys": [],
    }
    if set(manifest) != FINAL_PREPARE_FIELDS:
        raise PrepareMaterialError(
            "Witness prepare manifest fields are not exact"
        )
    manifest_bytes = _canonical_json(manifest)
    files = {
        WITNESS_MANIFEST_NAME: manifest_bytes,
        WITNESS_ATTESTATION_NAME: public_bytes,
    }
    archive_bytes = _tar_bytes(files)
    validate_role_archive_bytes(archive_bytes, expected_files=files)
    return archive_bytes, manifest_bytes, files


def _assert_private_directory(path: Path, *, required_uid: int) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrepareMaterialError(
            f"output directory is unavailable: {path}"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != required_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PrepareMaterialError(
            "output directory must be a real owner-only directory"
        )


def _read_existing_output(
    path: Path,
    *,
    required_uid: int,
    maximum: int,
) -> bytes:
    return read_secure_bytes(
        path,
        label="existing prepare material",
        owner_uid=required_uid,
        max_size=maximum,
    )


def _read_release_file(
    path: Path,
    *,
    label: str,
    required_uid: int,
    maximum: int,
) -> bytes:
    descriptor = -1
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
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= maximum
        ):
            raise PrepareMaterialError(
                f"{label} is unavailable or unsafe"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
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
        if (
            len(payload) > maximum
            or any(getattr(before, name) != getattr(after, name) for name in stable)
        ):
            raise PrepareMaterialError(f"{label} changed while being read")
        return payload
    except PrepareMaterialError:
        raise
    except OSError as exc:
        raise PrepareMaterialError(
            f"cannot securely read {label}: {path}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _temporary_path(path: Path, payload: bytes) -> Path:
    digest = hashlib.sha256(payload).hexdigest()
    return path.with_name(f".{path.name}.{digest[:24]}.materializing")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_create_only(
    path: Path,
    payload: bytes,
    *,
    required_uid: int,
    maximum: int,
) -> str:
    """Publish once, or verify/reuse exact output after an interrupted run."""

    _assert_private_directory(path.parent, required_uid=required_uid)
    temporary = _temporary_path(path, payload)
    if path.exists() or path.is_symlink():
        if temporary.exists() or temporary.is_symlink():
            try:
                temporary_metadata = temporary.stat(
                    follow_symlinks=False
                )
                destination_metadata = path.stat(
                    follow_symlinks=False
                )
            except OSError as exc:
                raise PrepareMaterialError(
                    "prepare material crash residue is unsafe"
                ) from exc
            if (
                not stat.S_ISREG(temporary_metadata.st_mode)
                or temporary_metadata.st_uid != required_uid
                or stat.S_IMODE(temporary_metadata.st_mode) != 0o600
                or temporary_metadata.st_nlink not in {1, 2}
            ):
                raise PrepareMaterialError(
                    "prepare material crash residue is unsafe"
                )
            same_inode = (
                temporary_metadata.st_dev == destination_metadata.st_dev
                and temporary_metadata.st_ino == destination_metadata.st_ino
            )
            if temporary_metadata.st_nlink == 2 and not same_inode:
                raise PrepareMaterialError(
                    "prepare material crash residue has an unexpected hard link"
                )
            if temporary_metadata.st_nlink == 1:
                temporary_payload = _read_existing_output(
                    temporary,
                    required_uid=required_uid,
                    maximum=maximum,
                )
                if temporary_payload != payload:
                    temporary.unlink()
                    _fsync_directory(path.parent)
                else:
                    temporary.unlink()
                    _fsync_directory(path.parent)
            else:
                temporary.unlink()
                _fsync_directory(path.parent)
        observed = _read_existing_output(
            path,
            required_uid=required_uid,
            maximum=maximum,
        )
        if observed != payload:
            raise PrepareMaterialError(
                f"refusing to overwrite different prepare material: {path}"
            )
        return "reused"

    if temporary.exists() or temporary.is_symlink():
        try:
            observed = _read_existing_output(
                temporary,
                required_uid=required_uid,
                maximum=maximum,
            )
        except Exception as exc:
            raise PrepareMaterialError(
                "prepare material temporary path is unsafe"
            ) from exc
        if observed != payload:
            temporary.unlink()
            _fsync_directory(path.parent)

    if not temporary.exists():
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(temporary, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(payload)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise PrepareMaterialError(
                        "prepare material write made no progress"
                    )
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)

    try:
        os.link(temporary, path, follow_symlinks=False)
    except FileExistsError:
        observed = _read_existing_output(
            path,
            required_uid=required_uid,
            maximum=maximum,
        )
        if observed != payload:
            raise PrepareMaterialError(
                f"refusing to overwrite raced prepare material: {path}"
            )
    _fsync_directory(path.parent)
    if temporary.exists():
        temporary.unlink()
        _fsync_directory(path.parent)
    observed = _read_existing_output(
        path,
        required_uid=required_uid,
        maximum=maximum,
    )
    if observed != payload:
        raise PrepareMaterialError(
            "published prepare material differs from its payload"
        )
    return "created"


def _validate_canonical_compose(
    raw: bytes,
    *,
    expected_sha256: str,
) -> dict[str, Any]:
    _nonzero_sha256(expected_sha256, label="canonical Compose")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise PrepareMaterialError(
            "canonical Compose hash differs from the release binding"
        )
    try:
        text = raw.decode("utf-8")
        payload = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise PrepareMaterialError("canonical Compose is invalid") from exc
    if not isinstance(payload, dict):
        raise PrepareMaterialError("canonical Compose must be an object")
    failures = collect_source_failures(payload, text)
    if failures:
        raise PrepareMaterialError(
            "canonical Compose contract failed: "
            + "; ".join(dict.fromkeys(failures))
        )
    return payload


def _domain_separated_sha256(label: str, value: Mapping[str, Any]) -> str:
    """Hash only a typed, nonsecret descriptor under a fixed domain label."""

    try:
        return runtime_targets.domain_separated_sha256(label, value)
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise PrepareMaterialError("runtime target digest domain is invalid") from exc


def _resolve_required_interpolations(
    value: Any,
    *,
    source_values: Mapping[str, str],
    label: str,
) -> str:
    """Resolve the narrow Compose interpolation grammar used by target fields."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 0x20 for character in value)
    ):
        raise PrepareMaterialError(f"{label} is invalid")

    def replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = source_values.get(name)
        if (
            not isinstance(resolved, str)
            or not resolved
            or resolved != resolved.strip()
            or "\x00" in resolved
            or any(ord(character) < 0x20 for character in resolved)
        ):
            raise PrepareMaterialError(
                f"{label} interpolation source is invalid"
            )
        return resolved

    resolved = REQUIRED_INTERPOLATION_RE.sub(replacement, value)
    if "$" in resolved or "${" in resolved:
        raise PrepareMaterialError(
            f"{label} contains an unsupported interpolation"
        )
    return resolved


def _observer_service_environment(
    canonical_payload: Mapping[str, Any],
    *,
    role: str,
) -> Mapping[str, Any]:
    """Validate the fixed, one-shot Compose observer service for one role."""

    if role not in DOCKER_ROLES:
        raise PrepareMaterialError("runtime target role is invalid")
    services = canonical_payload.get("services")
    service_name = CONVERGENCE_RUNTIME_OBSERVER_SERVICES[role]
    if not isinstance(services, Mapping):
        raise PrepareMaterialError("canonical Compose observer services are invalid")
    service = services.get(service_name)
    if not isinstance(service, Mapping):
        raise PrepareMaterialError(
            f"canonical Compose observer service for {role} is unavailable"
        )
    try:
        runtime_targets.validate_canonical_observer_service(
            canonical_payload,
            role=role,
            label=f"canonical Compose observer {role}",
        )
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise PrepareMaterialError(
            f"canonical Compose observer service definition for {role} differs"
        ) from exc
    environment = service.get("environment")
    if not isinstance(environment, Mapping):
        raise PrepareMaterialError(
            f"canonical Compose observer environment for {role} is invalid"
        )
    return environment


def _runtime_target_source_names(
    canonical_payload: Mapping[str, Any],
    *,
    role: str,
) -> frozenset[str]:
    """Return only the env names needed to rederive one redacted target row.

    The role archive remains root-only and encrypted in transit.  These values
    are deliberately retained there so the template builder can recompute the
    target set from verified role material; they are never copied into the
    target set, its descriptor, metadata, receipt, or manifest.
    """

    environment = _observer_service_environment(canonical_payload, role=role)
    names: set[str] = set()
    for field in (
        *CONVERGENCE_RUNTIME_DATABASE_ENV_FIELDS,
        *CONVERGENCE_RUNTIME_IDENTITY_ENV_FIELDS,
    ):
        value = environment.get(field)
        if not isinstance(value, str) or not value:
            raise PrepareMaterialError(
                f"canonical Compose observer {field} for {role} is invalid"
            )
        names.update(REQUIRED_INTERPOLATION_RE.findall(value))
        if "$" in REQUIRED_INTERPOLATION_RE.sub("", value):
            raise PrepareMaterialError(
                f"canonical Compose observer {field} for {role} has unsupported interpolation"
            )
    return frozenset(names)


def _parse_observer_database_target(
    value: str,
    *,
    role: str,
    expected_scheme: str,
    label: str,
) -> tuple[dict[str, Any], str]:
    """Parse one canonical PostgreSQL URL without retaining its password."""

    try:
        return runtime_targets.parse_observer_database_target(
            value,
            role=role,
            expected_scheme=expected_scheme,
            label=label,
        )
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise PrepareMaterialError(f"{label} is not a canonical database URL") from exc


def _runtime_identity_for_observer(
    environment: Mapping[str, Any],
    *,
    source_values: Mapping[str, str],
    role: str,
    release_sha: str,
) -> dict[str, str]:
    values = {
        name: _resolve_required_interpolations(
            environment.get(name),
            source_values=source_values,
            label=f"{role} observer {name}",
        )
        for name in CONVERGENCE_RUNTIME_IDENTITY_ENV_FIELDS
    }
    try:
        return runtime_targets.derive_runtime_identity(
            values,
            role=role,
            release_sha=release_sha,
        )
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise PrepareMaterialError(
            f"canonical Compose runtime identity for {role} differs"
        ) from exc


def _observer_target_row(
    canonical_payload: Mapping[str, Any],
    *,
    source_values: Mapping[str, str],
    role: str,
    release_sha: str,
) -> dict[str, str]:
    environment = _observer_service_environment(canonical_payload, role=role)
    resolved = {
        name: _resolve_required_interpolations(
            environment.get(name),
            source_values=source_values,
            label=f"{role} observer {name}",
        )
        for name in CONVERGENCE_RUNTIME_DATABASE_ENV_FIELDS
    }
    identity = _runtime_identity_for_observer(
        environment,
        source_values=source_values,
        role=role,
        release_sha=release_sha,
    )
    try:
        binding = runtime_targets.derive_runtime_target_binding(
            {**resolved, **identity},
            role=role,
            release_sha=release_sha,
            observer_service=runtime_targets.validate_canonical_observer_service(
                canonical_payload,
                role=role,
                label=f"canonical Compose observer {role}",
            ),
        )
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise PrepareMaterialError(
            f"canonical Compose observer database targets for {role} differ"
        ) from exc
    row = binding["runtime_target_row"]
    if not isinstance(row, dict):
        raise PrepareMaterialError("runtime target binding row is invalid")
    return row


def _runtime_target_set_digest(document: Mapping[str, Any]) -> str:
    try:
        return runtime_targets.runtime_target_set_digest(document)
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise PrepareMaterialError("runtime target set digest is invalid") from exc


def _verified_runtime_target_compose(
    canonical_compose_raw: bytes,
) -> tuple[dict[str, Any], str]:
    """Parse and hash the exact raw release Compose used for target derivation."""

    if (
        not isinstance(canonical_compose_raw, bytes)
        or not 1 <= len(canonical_compose_raw) <= MAX_INPUT_BYTES
    ):
        raise PrepareMaterialError(
            "canonical Compose runtime target source is invalid"
        )
    canonical_compose_sha256 = hashlib.sha256(canonical_compose_raw).hexdigest()
    return (
        _validate_canonical_compose(
            canonical_compose_raw,
            expected_sha256=canonical_compose_sha256,
        ),
        canonical_compose_sha256,
    )


def _validated_runtime_target_source_values(
    role_source_values: Mapping[str, Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    if not isinstance(role_source_values, Mapping) or set(role_source_values) != set(
        DOCKER_ROLES
    ):
        raise PrepareMaterialError(
            "runtime target role environment source coverage is invalid"
        )
    normalized: dict[str, Mapping[str, str]] = {}
    for role in DOCKER_ROLES:
        source_values = role_source_values[role]
        if not isinstance(source_values, Mapping) or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in source_values.items()
        ):
            raise PrepareMaterialError(
                f"runtime target environment source for {role} is invalid"
            )
        normalized[role] = source_values
    return normalized


def validate_convergence_runtime_target_set(
    document: Mapping[str, Any],
    *,
    operation_id: str,
    release_sha: str,
    canonical_compose_raw: bytes,
) -> dict[str, Any]:
    """Validate the redacted target set before binding it to a manifest."""

    operation_id = _canonical_uuid(operation_id, label="operation_id")
    if not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise PrepareMaterialError("release_sha must be a full lowercase Git SHA")
    _, canonical_compose_sha256 = _verified_runtime_target_compose(
        canonical_compose_raw
    )
    try:
        return runtime_targets.validate_runtime_target_set(
            document,
            operation_id=operation_id,
            release_sha=release_sha,
            canonical_compose_sha256=canonical_compose_sha256,
            label="convergence runtime target set",
        )
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise PrepareMaterialError(str(exc)) from exc


def build_convergence_runtime_target_set(
    *,
    operation_id: str,
    release_sha: str,
    canonical_compose_raw: bytes,
    role_source_values: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Derive a redacted, controller-only observer target descriptor set.

    Passwords are checked only for async/sync consistency.  They, raw URLs,
    and every unrelated service value are intentionally omitted from the
    result and therefore cannot become an evidence or manifest payload.
    """

    operation_id = _canonical_uuid(operation_id, label="operation_id")
    if not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise PrepareMaterialError("release_sha must be a full lowercase Git SHA")
    canonical_payload, canonical_compose_sha256 = _verified_runtime_target_compose(
        canonical_compose_raw
    )
    normalized_role_sources = _validated_runtime_target_source_values(
        role_source_values
    )
    document: dict[str, Any] = {
        "schema": CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "canonical_compose_sha256": canonical_compose_sha256,
        "roles": {
            role: _observer_target_row(
                canonical_payload,
                source_values=normalized_role_sources[role],
                role=role,
                release_sha=release_sha,
            )
            for role in DOCKER_ROLES
        },
        "target_set_sha256": "0" * 64,
    }
    document["target_set_sha256"] = _runtime_target_set_digest(document)
    return validate_convergence_runtime_target_set(
        document,
        operation_id=operation_id,
        release_sha=release_sha,
        canonical_compose_raw=canonical_compose_raw,
    )


def convergence_runtime_target_descriptor(
    document: Mapping[str, Any],
    *,
    canonical_compose_raw: bytes,
) -> dict[str, Any]:
    """Return the manifest-safe descriptor for a validated target-set file."""

    normalized = validate_convergence_runtime_target_set(
        document,
        operation_id=str(document.get("operation_id", "")),
        release_sha=str(document.get("release_sha", "")),
        canonical_compose_raw=canonical_compose_raw,
    )
    try:
        return runtime_targets.runtime_target_set_descriptor(normalized)
    except (
        runtime_targets.ConvergenceRuntimeTargetDescriptorError,
        runtime_targets.ConvergenceRuntimeTargetBindingError,
    ) as exc:
        raise PrepareMaterialError(
            "producer convergence runtime target descriptor is invalid"
        ) from exc


def produce_prepare_materials(
    *,
    operation_id: str,
    release_sha: str,
    canonical_compose: Path,
    expected_compose_sha256: str,
    environment_source: Path,
    ca_certificate: Path,
    dr_tls_attestation: Path,
    stage_bindings: Path,
    witness_public_input: Path,
    output_directory: Path,
    metadata_output: Path | None = None,
    required_uid: int = 0,
) -> dict[str, Any]:
    operation_id = _canonical_uuid(operation_id, label="operation_id")
    if not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise PrepareMaterialError("release_sha must be a full lowercase Git SHA")
    _assert_private_directory(output_directory, required_uid=required_uid)

    compose_raw = _read_release_file(
        canonical_compose,
        label="canonical production shadow Compose",
        required_uid=required_uid,
        maximum=MAX_INPUT_BYTES,
    )
    canonical_payload = _validate_canonical_compose(
        compose_raw,
        expected_sha256=expected_compose_sha256,
    )
    try:
        source_values = parse_env_values(
            read_secure_text(
                environment_source,
                label="canonical production shadow environment",
                owner_uid=required_uid,
                max_size=MAX_INPUT_BYTES,
            )
        )
    except ProductionShadowRoleError as exc:
        raise PrepareMaterialError(str(exc)) from exc
    ca_bytes = read_secure_bytes(
        ca_certificate,
        label="operation-bound DR CA certificate",
        owner_uid=required_uid,
        max_size=1024 * 1024,
    )
    certificate = _validate_ca_certificate(
        ca_bytes,
        operation_id=operation_id,
    )
    ca_attestation_document, ca_attestation_bytes = _read_json_secure(
        dr_tls_attestation,
        label="operation-bound DR CA attestation",
        required_uid=required_uid,
    )
    (
        ca_attestation_sha256,
        ca_attested_at_epoch,
    ) = _validate_dr_ca_attestation(
        ca_attestation_document,
        ca_attestation_bytes,
        operation_id=operation_id,
        release_sha=release_sha,
        ca_bytes=ca_bytes,
        certificate=certificate,
    )
    stage_document, _ = _read_json_secure(
        stage_bindings,
        label="production shadow image stage bindings",
        required_uid=required_uid,
    )
    bindings = _validate_stage_bindings(
        stage_document,
        operation_id=operation_id,
        release_sha=release_sha,
    )
    witness_document, witness_bytes = _read_json_secure(
        witness_public_input,
        label="Witness public prepare input",
        required_uid=required_uid,
    )
    artifacts: dict[str, RoleArtifact] = {}
    runtime_inventory: dict[str, dict[str, str]] = {}
    runtime_target_role_sources: dict[str, Mapping[str, str]] = {}
    for role in DOCKER_ROLES:
        archive, internal_manifest, files = _build_docker_role(
            role=role,
            operation_id=operation_id,
            release_sha=release_sha,
            canonical_payload=canonical_payload,
            source_values=source_values,
            ca_bytes=ca_bytes,
            ca_attestation_sha256=ca_attestation_sha256,
            ca_attested_at_epoch=ca_attested_at_epoch,
            stage_binding=bindings[role],
        )
        filename = ROLE_ARCHIVE_NAMES[role]
        publication = _publish_create_only(
            output_directory / filename,
            archive,
            required_uid=required_uid,
            maximum=MAX_ARCHIVE_BYTES,
        )
        artifacts[role] = RoleArtifact(
            role=role,
            filename=filename,
            sha256=hashlib.sha256(archive).hexdigest(),
            bytes=len(archive),
            format=ROLE_FORMATS[role],
            transport=ROLE_TRANSPORTS[role],
            internal_manifest_sha256=hashlib.sha256(
                internal_manifest
            ).hexdigest(),
            stage_operation_manifest_sha256=bindings[role][
                "stage_operation_manifest_sha256"
            ],
            stage_attestation_sha256=bindings[role][
                "stage_attestation_sha256"
            ],
            publication=publication,
        )
        runtime_inventory[role] = dict(
            bindings[role]["runtime_image_ids"]
        )
        try:
            runtime_target_role_sources[role] = parse_env_values(
                files["runtime.env.role"].decode("ascii")
            )
        except (KeyError, UnicodeError, ProductionShadowRoleError) as exc:
            raise PrepareMaterialError(
                f"{role} runtime target role environment is invalid"
            ) from exc

    runtime_target_set = build_convergence_runtime_target_set(
        operation_id=operation_id,
        release_sha=release_sha,
        canonical_compose_raw=compose_raw,
        role_source_values=runtime_target_role_sources,
    )
    if runtime_target_set["canonical_compose_sha256"] != expected_compose_sha256:
        raise PrepareMaterialError(
            "runtime target canonical Compose binding differs"
        )
    runtime_target_payload = _canonical_json(runtime_target_set)
    runtime_target_publication = _publish_create_only(
        output_directory / CONVERGENCE_RUNTIME_TARGETS_FILENAME,
        runtime_target_payload,
        required_uid=required_uid,
        maximum=MAX_INPUT_BYTES,
    )
    runtime_target_descriptor = convergence_runtime_target_descriptor(
        runtime_target_set,
        canonical_compose_raw=compose_raw,
    )

    witness_archive, witness_manifest, _ = _build_witness_role(
        operation_id=operation_id,
        release_sha=release_sha,
        public_document=witness_document,
        public_bytes=witness_bytes,
        stage_binding=bindings["witness"],
    )
    witness_filename = ROLE_ARCHIVE_NAMES["witness"]
    witness_publication = _publish_create_only(
        output_directory / witness_filename,
        witness_archive,
        required_uid=required_uid,
        maximum=MAX_ARCHIVE_BYTES,
    )
    artifacts["witness"] = RoleArtifact(
        role="witness",
        filename=witness_filename,
        sha256=hashlib.sha256(witness_archive).hexdigest(),
        bytes=len(witness_archive),
        format=ROLE_FORMATS["witness"],
        transport=ROLE_TRANSPORTS["witness"],
        internal_manifest_sha256=hashlib.sha256(
            witness_manifest
        ).hexdigest(),
        stage_operation_manifest_sha256=bindings["witness"][
            "stage_operation_manifest_sha256"
        ],
        stage_attestation_sha256=bindings["witness"][
            "stage_attestation_sha256"
        ],
        publication=witness_publication,
    )

    if len({artifact.sha256 for artifact in artifacts.values()}) != len(ALL_ROLES):
        raise PrepareMaterialError(
            "role material archive digests must be distinct"
        )
    controller_role_materials = {
        role: {
            "sha256": artifacts[role].sha256,
            "bytes": artifacts[role].bytes,
            "transport": artifacts[role].transport,
            "format": artifacts[role].format,
        }
        for role in ALL_ROLES
    }
    result: dict[str, Any] = {
        "schema": SET_SCHEMA,
        "capabilities": list(runtime_targets.RUNTIME_TARGET_CAPABILITIES),
        "operation_id": operation_id,
        "release_sha": release_sha,
        "canonical_compose_sha256": expected_compose_sha256,
        "dr_ca_sha256": hashlib.sha256(ca_bytes).hexdigest(),
        "dr_tls_attestation_sha256": ca_attestation_sha256,
        "dr_tls_attested_at_epoch": ca_attested_at_epoch,
        "roles": {
            role: {
                "filename": artifacts[role].filename,
                "sha256": artifacts[role].sha256,
                "bytes": artifacts[role].bytes,
                "format": artifacts[role].format,
                "transport": artifacts[role].transport,
                "internal_manifest_sha256": artifacts[
                    role
                ].internal_manifest_sha256,
                "stage_operation_manifest_sha256": artifacts[
                    role
                ].stage_operation_manifest_sha256,
                "stage_attestation_sha256": artifacts[
                    role
                ].stage_attestation_sha256,
            }
            for role in ALL_ROLES
        },
        "controller_bindings": {
            "role_materials": controller_role_materials,
            "role_runtime_image_ids": runtime_inventory,
            "convergence_runtime_targets": runtime_target_descriptor,
        },
        "activation_secrets_included": False,
        "precommit_manifest_bound": False,
        "publication_results": {
            **{role: artifacts[role].publication for role in ALL_ROLES},
            "convergence_runtime_targets": runtime_target_publication,
        },
    }
    if metadata_output is not None:
        if metadata_output.parent != output_directory:
            raise PrepareMaterialError(
                "metadata output must be directly inside the private output directory"
            )
        metadata_document = {
            key: value
            for key, value in result.items()
            if key != "publication_results"
        }
        metadata_payload = _canonical_json(metadata_document)
        metadata_publication = _publish_create_only(
            metadata_output,
            metadata_payload,
            required_uid=required_uid,
            maximum=MAX_INPUT_BYTES,
        )
        result["metadata_output"] = metadata_output.name
        result["metadata_sha256"] = hashlib.sha256(
            metadata_payload
        ).hexdigest()
        result["metadata_publication"] = metadata_publication
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument(
        "--canonical-compose",
        type=Path,
        default=Path(
            "deploy/production/docker-compose.three-site-shadow.yml"
        ),
    )
    parser.add_argument("--expected-compose-sha256", required=True)
    parser.add_argument("--environment-source", type=Path, required=True)
    parser.add_argument("--ca-certificate", type=Path, required=True)
    parser.add_argument("--dr-tls-attestation", type=Path, required=True)
    parser.add_argument("--stage-bindings", type=Path, required=True)
    parser.add_argument("--witness-public-input", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        if os.geteuid() != 0:
            raise PrepareMaterialError(
                "production shadow prepare material producer must run as root"
            )
        args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
        result = produce_prepare_materials(
            operation_id=args.operation_id,
            release_sha=args.release_sha,
            canonical_compose=args.canonical_compose,
            expected_compose_sha256=args.expected_compose_sha256,
            environment_source=args.environment_source,
            ca_certificate=args.ca_certificate,
            dr_tls_attestation=args.dr_tls_attestation,
            stage_bindings=args.stage_bindings,
            witness_public_input=args.witness_public_input,
            output_directory=args.output_directory,
            metadata_output=args.metadata_output,
            required_uid=0,
        )
    except (PrepareMaterialError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "live_io_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "prepared",
                "live_io_performed": False,
                **result,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
