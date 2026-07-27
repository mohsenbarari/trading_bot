#!/usr/bin/env python3
"""Validate one production-shadow host request without executing it.

This file is deliberately standalone: a deployed copy does not import the
application repository or trust ambient ``PYTHONPATH``.  It reads one fixed,
root-only, manifest-bound request contract; verifies its own artifact hash and
the local host address; validates the complete request; and then stops because
production operation implementations remain hard-disabled in this slice.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import struct
import subprocess
import sys
from typing import Any
from uuid import UUID


AGENT_SCHEMA = "production-shadow-host-agent-request-v1"
CONTRACT_SCHEMA = "production-shadow-host-agent-contract-v1"
FIXED_CONTRACT_PATH = Path(
    "/etc/trading-bot-production-shadow/host-agent-contract.json"
)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{7,62}$")
IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
DOCKER_RUNTIME_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
IMAGE_ARTIFACT_FIELDS = frozenset(
    {
        "archive_sha256",
        "archive_bytes",
        "config_digest",
        "content_descriptor",
        "content_identity",
    }
)
CONTENT_DESCRIPTOR_FIELDS = frozenset(
    {
        "architecture",
        "os",
        "created",
        "config_sha256",
        "rootfs_type",
        "rootfs_layers",
    }
)
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
OBJECT_STORAGE_TRANSPORT = "object-storage-private-versioned-age"
BUSINESS_WRITE_FORBIDDEN = "forbid"
BUSINESS_WRITE_FORWARD_ONLY = "allow-after-forward-only-commit"
PRECOMMIT_JOURNAL_STATUS = "rollback-eligible-precommit"
POSTCOMMIT_JOURNAL_STATUS = "forward-only-committed"
NGINX_GENERATION_ARTIFACT_FIELDS = {
    "legacy-normal": "nginx_rollback_generation_sha256",
    "legacy-frozen": "nginx_freeze_generation_sha256",
    "shadow-readonly": "nginx_shadow_readonly_generation_sha256",
    "shadow-writable": "nginx_shadow_writable_generation_sha256",
}
PRECOMMIT_MANIFEST_SCHEMA = "production-shadow-precommit-operation-v1"
PRECOMMIT_SECRET_ROOT = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
PRECOMMIT_PROJECT_ROOT = Path(
    "/srv/trading-bot-three-site-production-shadow"
)
PYTHON3 = "/usr/bin/python3"
PRECOMMIT_ACTIONS = frozenset(
    {
        "verify-installation",
        "bootstrap-database",
        "restore-shadow",
        "prepare-shadow",
        "readonly-acceptance",
    }
)
PRECOMMIT_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "approval_sha256",
        "role_material_sha256",
        "canonical_compose_sha256",
        "role_compose_sha256",
        "environment_sha256",
        "worker_sha256",
        "acceptance_producer_sha256",
        "image_artifacts",
        "runtime_image_ids",
        "artifacts",
        "source_database",
        "target_migration_revision",
        "postgres_runtime_uid",
        "postgres_runtime_gid",
    }
)
PRECOMMIT_IMAGE_FIELDS = frozenset(IMAGE_KINDS)
PRECOMMIT_ARTIFACT_KINDS = frozenset(
    {
        "release-bundle",
        "role-material",
        "app-image-archive",
        "postgres-image-archive",
        "redis-image-archive",
        "nginx-image-archive",
        "database-backup",
        "uploads-archive",
        "audit-archive",
    }
)
PRECOMMIT_ARTIFACT_FIELDS = frozenset(
    {"sha256", "bytes", "restored_tree_sha256"}
)
PRECOMMIT_SOURCE_DATABASE_FIELDS = frozenset(
    {
        "alembic_revision",
        "fingerprint_algorithm",
        "database_fingerprint_sha256",
        "row_count",
        "table_count",
    }
)
_EXEC_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONNOUSERSITE": "1",
}

REQUEST_FIELDS = frozenset(
    {
        "schema",
        "operation",
        "role",
        "expected_host",
        "campaign_id",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "manifest_sha256",
        "approval_sha256",
        "release_bundle_sha256",
        "release_bundle_bytes",
        "role_material_sha256",
        "role_material_bytes",
        "role_material_format",
        "image_artifacts",
        "runtime_image_ids",
        "shadow_compose_sha256",
        "postgres_runtime_uid",
        "postgres_runtime_gid",
        "shadow_project",
        "shadow_root",
        "production_vhosts",
        "nginx_freeze_generation_sha256",
        "nginx_rollback_generation_sha256",
        "nginx_shadow_readonly_generation_sha256",
        "nginx_shadow_writable_generation_sha256",
        "legacy_redis_policy",
        "shadow_redis_policy",
        "postcommit_executor_contract_sha256",
        "phase_evidence_schema_sha256",
        "host_agent_sha256",
        "host_agent_contract",
        "host_agent_contract_sha256",
        "business_write_policy",
        "required_journal_status",
        "payload_transport",
    }
)
CONTRACT_FIELDS = frozenset(
    {
        "schema",
        "production_vhosts",
        "topology",
        "policies",
        "operations",
    }
)
TOPOLOGY_FIELDS = frozenset(
    {"role", "host", "ssh_user", "ssh_port", "transport"}
)
POLICY_FIELDS = frozenset(
    {
        "legacy_redis",
        "shadow_redis",
        "precommit_journal_status",
        "postcommit_journal_status",
        "business_write_forbidden",
        "business_write_forward_only",
    }
)
OPERATION_FIELDS = frozenset(
    {
        "operation",
        "roles",
        "forward_only",
        "business_write_allowed",
        "required_journal_status",
        "nginx_generations",
    }
)
EXPECTED_ROLES = frozenset({"bot_fi", "webapp_fi", "webapp_ir", "witness"})
EXPECTED_VHOST_ROLES = frozenset({"bot_fi", "webapp_fi"})
EXPECTED_TRANSPORTS = frozenset(
    {
        "local-controller",
        "ssh-control",
        "ssh-control-object-storage-payload-only",
    }
)


class HostAgentError(RuntimeError):
    """Raised when a host request differs from the bounded contract."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HostAgentError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _decode_urlsafe_json(raw: str, *, label: str) -> dict[str, Any]:
    try:
        encoded = raw.encode("ascii")
        payload = base64.b64decode(
            encoded,
            altchars=b"-_",
            validate=True,
        )
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (
        UnicodeError,
        ValueError,
        binascii.Error,
        json.JSONDecodeError,
        HostAgentError,
    ) as exc:
        raise HostAgentError(f"{label} argument is invalid") from exc
    if (
        not isinstance(document, dict)
        or base64.urlsafe_b64encode(payload).decode("ascii") != raw
        or payload != _canonical_json(document)
    ):
        raise HostAgentError(f"{label} argument is not canonical")
    return document


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise HostAgentError(f"{label} must be a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise HostAgentError(f"{label} must be a canonical UUID") from exc
    if str(parsed) != value or parsed.version not in {1, 2, 3, 4, 5}:
        raise HostAgentError(f"{label} must be a canonical UUID")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise HostAgentError(f"{label} must be a nonzero SHA-256")
    return value


def _validate_content_descriptor(
    descriptor: Any,
    *,
    expected_identity: Any,
    label: str,
) -> None:
    if (
        not isinstance(descriptor, dict)
        or set(descriptor) != CONTENT_DESCRIPTOR_FIELDS
        or descriptor["architecture"] != "amd64"
        or descriptor["os"] != "linux"
        or not isinstance(descriptor["created"], str)
        or not descriptor["created"]
        or not isinstance(descriptor["config_sha256"], str)
        or IMAGE_ID_RE.fullmatch(descriptor["config_sha256"]) is None
        or descriptor["rootfs_type"] != "layers"
        or not isinstance(descriptor["rootfs_layers"], list)
        or not descriptor["rootfs_layers"]
        or any(
            not isinstance(layer, str)
            or IMAGE_ID_RE.fullmatch(layer) is None
            for layer in descriptor["rootfs_layers"]
        )
        or not isinstance(expected_identity, str)
        or IMAGE_ID_RE.fullmatch(expected_identity) is None
        or expected_identity
        != "sha256:" + hashlib.sha256(_canonical_json(descriptor)).hexdigest()
    ):
        raise HostAgentError(f"{label} content descriptor is invalid")


def _read_stable_file(
    path: Path,
    *,
    label: str,
    require_mode: int | None,
    max_size: int,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HostAgentError(f"cannot securely open {label}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > max_size
            or mode & 0o022
            or (require_mode is not None and mode != require_mode)
        ):
            raise HostAgentError(f"{label} ownership, mode, link, or size is unsafe")
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
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
        )
        if len(payload) > max_size or any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            raise HostAgentError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def validate_contract(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != CONTRACT_FIELDS:
        raise HostAgentError("host-agent contract fields are not exact")
    if document["schema"] != CONTRACT_SCHEMA:
        raise HostAgentError("host-agent contract schema is invalid")

    topology = document["topology"]
    if not isinstance(topology, dict) or set(topology) != EXPECTED_ROLES:
        raise HostAgentError("host-agent contract topology roles are not exact")
    hosts: set[str] = set()
    for role in sorted(EXPECTED_ROLES):
        row = topology[role]
        if not isinstance(row, dict) or set(row) != TOPOLOGY_FIELDS:
            raise HostAgentError(f"host-agent topology {role} fields are not exact")
        try:
            host = str(ipaddress.IPv4Address(row["host"]))
        except (ipaddress.AddressValueError, ValueError) as exc:
            raise HostAgentError(f"host-agent topology {role} host is invalid") from exc
        if (
            row["role"] != role
            or host != row["host"]
            or host in hosts
            or row["transport"] not in EXPECTED_TRANSPORTS
        ):
            raise HostAgentError(f"host-agent topology {role} is invalid")
        hosts.add(host)
        if row["transport"] == "local-controller":
            if row["ssh_user"] is not None or row["ssh_port"] is not None:
                raise HostAgentError("local-controller topology must not specify SSH")
        elif (
            row["ssh_user"] != "root"
            or type(row["ssh_port"]) is not int
            or not 1 <= row["ssh_port"] <= 65535
        ):
            raise HostAgentError(f"host-agent topology {role} SSH pin is invalid")

    vhosts = document["production_vhosts"]
    if not isinstance(vhosts, dict) or set(vhosts) != EXPECTED_VHOST_ROLES:
        raise HostAgentError("host-agent contract vhost roles are not exact")
    all_vhosts: list[str] = []
    for role in sorted(EXPECTED_VHOST_ROLES):
        values = vhosts[role]
        if (
            not isinstance(values, list)
            or not values
            or any(
                not isinstance(value, str)
                or HOSTNAME_RE.fullmatch(value) is None
                for value in values
            )
        ):
            raise HostAgentError(f"host-agent contract vhosts for {role} are invalid")
        all_vhosts.extend(values)
    if len(all_vhosts) != 3 or len(all_vhosts) != len(set(all_vhosts)):
        raise HostAgentError("host-agent contract must contain three unique vhosts")

    policies = document["policies"]
    if not isinstance(policies, dict) or set(policies) != POLICY_FIELDS:
        raise HostAgentError("host-agent contract policies are not exact")
    expected_policies = {
        "legacy_redis": "sealed-rollback-evidence-only",
        "shadow_redis": "pristine-empty-no-restore",
        "precommit_journal_status": PRECOMMIT_JOURNAL_STATUS,
        "postcommit_journal_status": POSTCOMMIT_JOURNAL_STATUS,
        "business_write_forbidden": BUSINESS_WRITE_FORBIDDEN,
        "business_write_forward_only": BUSINESS_WRITE_FORWARD_ONLY,
    }
    if policies != expected_policies:
        raise HostAgentError("host-agent contract policies are invalid")

    operations = document["operations"]
    if not isinstance(operations, list) or len(operations) != 40:
        raise HostAgentError("host-agent contract operation count is invalid")
    names: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) != OPERATION_FIELDS:
            raise HostAgentError("host-agent contract operation fields are not exact")
        name = operation["operation"]
        roles = operation["roles"]
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or not isinstance(roles, list)
            or not roles
            or len(roles) != len(set(roles))
            or any(role not in EXPECTED_ROLES for role in roles)
            or type(operation["forward_only"]) is not bool
            or type(operation["business_write_allowed"]) is not bool
        ):
            raise HostAgentError("host-agent contract operation is invalid")
        names.add(name)
        forward_only = operation["forward_only"]
        required_status = operation["required_journal_status"]
        nginx_generations = operation["nginx_generations"]
        if (
            forward_only
            != (required_status == POSTCOMMIT_JOURNAL_STATUS)
            or operation["business_write_allowed"] != forward_only
        ):
            raise HostAgentError(
                "host-agent contract operation has unsafe write/journal policy"
            )
        if (
            not isinstance(nginx_generations, list)
            or len(nginx_generations) != len(set(nginx_generations))
            or any(
                state not in NGINX_GENERATION_ARTIFACT_FIELDS
                for state in nginx_generations
            )
            or nginx_generations
            != [
                state
                for state in NGINX_GENERATION_ARTIFACT_FIELDS
                if state in nginx_generations
            ]
        ):
            raise HostAgentError(
                "host-agent contract operation Nginx generation bindings are invalid"
            )
        if "shadow-writable" in nginx_generations and not (
            name == "verify-pre-first-write-acceptance" or forward_only
        ):
            raise HostAgentError(
                "host-agent contract binds writable Nginx before the commit boundary"
            )
    return document


def contract_sha256(document: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(validate_contract(document))).hexdigest()


def read_contract(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    if path != FIXED_CONTRACT_PATH:
        raise HostAgentError("host-agent contract path is not fixed")
    payload = _read_stable_file(
        path,
        label="host-agent contract",
        require_mode=0o600,
        max_size=1024 * 1024,
    )
    observed = hashlib.sha256(payload).hexdigest()
    _nonzero_sha256(expected_sha256, label="host-agent contract")
    if observed != expected_sha256:
        raise HostAgentError("host-agent contract file differs from manifest")
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostAgentError("host-agent contract is not strict UTF-8 JSON") from exc
    validated = validate_contract(document)
    if contract_sha256(validated) != expected_sha256:
        raise HostAgentError(
            "host-agent contract bytes are not canonical manifest-bound JSON"
        )
    return validated


def hash_agent_artifact(path: Path) -> str:
    payload = _read_stable_file(
        path,
        label="host-agent executable",
        require_mode=None,
        max_size=4 * 1024 * 1024,
    )
    return hashlib.sha256(payload).hexdigest()


def observe_local_ipv4_addresses() -> set[str]:
    addresses: set[str] = set()
    try:
        interfaces = socket.if_nameindex()
        handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        raise HostAgentError("cannot inspect local host network identity") from exc
    try:
        for _, name in interfaces:
            try:
                packed = struct.pack("256s", name.encode("ascii")[:15])
                result = fcntl.ioctl(handle.fileno(), 0x8915, packed)
            except (OSError, UnicodeEncodeError):
                continue
            addresses.add(socket.inet_ntoa(result[20:24]))
    finally:
        handle.close()
    if not addresses:
        raise HostAgentError("local host has no observable IPv4 identity")
    return addresses


def _operation_map(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(operation["operation"]): operation
        for operation in contract["operations"]
    }


def _normalize_vhosts(
    value: Any,
    *,
    contract: dict[str, Any],
) -> dict[str, list[str]]:
    expected = contract["production_vhosts"]
    if not isinstance(value, dict) or value != expected:
        raise HostAgentError("production vhost mapping differs from contract")
    return {role: list(vhosts) for role, vhosts in value.items()}


def validate_request(
    document: Any,
    *,
    contract: dict[str, Any],
    observed_agent_sha256: str,
) -> dict[str, Any]:
    contract = validate_contract(contract)
    if not isinstance(document, dict) or set(document) != REQUEST_FIELDS:
        raise HostAgentError("host-agent request fields are not exact")
    if document["schema"] != AGENT_SCHEMA:
        raise HostAgentError("host-agent request schema is invalid")
    if document["host_agent_contract"] != str(FIXED_CONTRACT_PATH):
        raise HostAgentError("host-agent request contract path is not fixed")
    expected_contract_sha256 = _nonzero_sha256(
        document["host_agent_contract_sha256"],
        label="host-agent contract",
    )
    if contract_sha256(contract) != expected_contract_sha256:
        raise HostAgentError("host-agent request contract digest is invalid")
    expected_agent_sha256 = _nonzero_sha256(
        document["host_agent_sha256"],
        label="host-agent executable",
    )
    if observed_agent_sha256 != expected_agent_sha256:
        raise HostAgentError("host-agent executable differs from manifest")

    operation_name = str(document["operation"])
    operation = _operation_map(contract).get(operation_name)
    if operation is None:
        raise HostAgentError("host-agent operation is not allowlisted")
    role = str(document["role"])
    if role not in operation["roles"]:
        raise HostAgentError("host-agent role is not allowed for this operation")
    topology = contract["topology"][role]
    if document["expected_host"] != topology["host"]:
        raise HostAgentError("expected host differs from contract topology")

    campaign_id = _canonical_uuid(document["campaign_id"], label="campaign_id")
    operation_id = _canonical_uuid(document["operation_id"], label="operation_id")
    if campaign_id == operation_id:
        raise HostAgentError("operation_id must differ from campaign_id")
    release_sha = str(document["release_sha"])
    legacy_release_sha = str(document["legacy_release_sha"])
    if (
        SHA40_RE.fullmatch(release_sha) is None
        or SHA40_RE.fullmatch(legacy_release_sha) is None
        or release_sha == legacy_release_sha
    ):
        raise HostAgentError("release identities are invalid")

    for field in (
        "manifest_sha256",
        "approval_sha256",
        "release_bundle_sha256",
        "role_material_sha256",
        "shadow_compose_sha256",
        "nginx_freeze_generation_sha256",
        "nginx_rollback_generation_sha256",
        "nginx_shadow_readonly_generation_sha256",
        "nginx_shadow_writable_generation_sha256",
        "postcommit_executor_contract_sha256",
        "phase_evidence_schema_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    generation_digests = {
        document[field]
        for field in NGINX_GENERATION_ARTIFACT_FIELDS.values()
    }
    if len(generation_digests) != len(NGINX_GENERATION_ARTIFACT_FIELDS):
        raise HostAgentError(
            "Nginx generation digests must be distinct across semantic states"
        )
    for field in (
        "release_bundle_bytes",
        "role_material_bytes",
    ):
        value = document[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 64 * 1024 * 1024 * 1024
        ):
            raise HostAgentError(f"{field} is outside its size bound")
    image_artifacts = document["image_artifacts"]
    if (
        not isinstance(image_artifacts, dict)
        or set(image_artifacts) != set(IMAGE_KINDS)
    ):
        raise HostAgentError("image artifact inventory is not exact")
    for kind in IMAGE_KINDS:
        row = image_artifacts[kind]
        if (
            not isinstance(row, dict)
            or set(row) != IMAGE_ARTIFACT_FIELDS
        ):
            raise HostAgentError(
                f"image artifact {kind} fields are not exact"
            )
        _nonzero_sha256(
            row["archive_sha256"],
            label=f"image artifact {kind} archive",
        )
        _nonzero_sha256(
            row["content_identity"].removeprefix("sha256:")
            if isinstance(row["content_identity"], str)
            else row["content_identity"],
            label=f"image artifact {kind} content identity",
        )
        _bounded_positive_int(
            row["archive_bytes"],
            label=f"image artifact {kind} bytes",
        )
        value = row["config_digest"]
        if (
            not isinstance(value, str)
            or IMAGE_ID_RE.fullmatch(value) is None
            or value == "sha256:" + "0" * 64
        ):
            raise HostAgentError(
                f"image artifact {kind} config digest is invalid"
            )
        _validate_content_descriptor(
            row["content_descriptor"],
            expected_identity=row["content_identity"],
            label=f"image artifact {kind}",
        )
    for field in ("archive_sha256", "config_digest", "content_identity"):
        if len(
            {image_artifacts[kind][field] for kind in IMAGE_KINDS}
        ) != len(IMAGE_KINDS):
            raise HostAgentError(
                f"all four image {field} values must be distinct"
            )

    runtime_image_ids = document["runtime_image_ids"]
    expected_runtime_keys = (
        set(IMAGE_KINDS) if role in DOCKER_RUNTIME_ROLES else set()
    )
    if (
        not isinstance(runtime_image_ids, dict)
        or set(runtime_image_ids) != expected_runtime_keys
        or any(
            not isinstance(value, str)
            or IMAGE_ID_RE.fullmatch(value) is None
            or value == "sha256:" + "0" * 64
            for value in runtime_image_ids.values()
        )
        or len(set(runtime_image_ids.values())) != len(runtime_image_ids)
    ):
        raise HostAgentError(
            f"runtime image inventory for {role} is invalid"
        )
    if (
        document["postgres_runtime_uid"] != 70
        or document["postgres_runtime_gid"] != 70
    ):
        raise HostAgentError(
            "PostgreSQL runtime UID/GID differs from the image contract"
        )
    expected_material_format = (
        "production-shadow-witness-material-tar"
        if role == "witness"
        else "production-shadow-role-material-tar"
    )
    if document["role_material_format"] != expected_material_format:
        raise HostAgentError("role material format differs from the role contract")

    expected_project = f"tb3p-{operation_id.replace('-', '')}"
    expected_root = (
        f"/srv/trading-bot-three-site-production-shadow/{operation_id}"
    )
    if (
        document["shadow_project"] != expected_project
        or PROJECT_RE.fullmatch(str(document["shadow_project"])) is None
    ):
        raise HostAgentError("shadow project is not operation-derived")
    if document["shadow_root"] != expected_root:
        raise HostAgentError("shadow root is not operation-derived")
    shadow_root = PurePosixPath(str(document["shadow_root"]))
    if (
        not shadow_root.is_absolute()
        or ".." in shadow_root.parts
        or "current" in shadow_root.parts
        or "staging" in shadow_root.parts
    ):
        raise HostAgentError("shadow root is unsafe")

    normalized_vhosts = _normalize_vhosts(
        document["production_vhosts"],
        contract=contract,
    )
    policies = contract["policies"]
    if document["legacy_redis_policy"] != policies["legacy_redis"]:
        raise HostAgentError("legacy Redis policy is not exact")
    if document["shadow_redis_policy"] != policies["shadow_redis"]:
        raise HostAgentError("shadow Redis policy is not exact")
    expected_business_policy = (
        policies["business_write_forward_only"]
        if operation["business_write_allowed"]
        else policies["business_write_forbidden"]
    )
    if document["business_write_policy"] != expected_business_policy:
        raise HostAgentError("business-write policy differs from operation contract")
    if document["required_journal_status"] != operation["required_journal_status"]:
        raise HostAgentError("journal-state requirement differs from operation contract")

    expected_transport = (
        OBJECT_STORAGE_TRANSPORT
        if topology["transport"] == "ssh-control-object-storage-payload-only"
        else None
    )
    if document["payload_transport"] != expected_transport:
        raise HostAgentError("payload transport differs from the contracted role")

    validated = dict(document)
    validated["production_vhosts"] = normalized_vhosts
    return validated


def request_sha256(
    document: dict[str, Any],
    *,
    contract: dict[str, Any],
    observed_agent_sha256: str,
) -> str:
    validated = validate_request(
        document,
        contract=contract,
        observed_agent_sha256=observed_agent_sha256,
    )
    return hashlib.sha256(_canonical_json(validated)).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--legacy-release-sha", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--approval-sha256", required=True)
    parser.add_argument("--release-bundle-sha256", required=True)
    parser.add_argument("--release-bundle-bytes", type=int, required=True)
    parser.add_argument("--role-material-sha256", required=True)
    parser.add_argument("--role-material-bytes", type=int, required=True)
    parser.add_argument("--role-material-format", required=True)
    parser.add_argument("--image-artifacts-b64", required=True)
    parser.add_argument("--runtime-image-ids-b64", required=True)
    parser.add_argument("--shadow-compose-sha256", required=True)
    parser.add_argument("--postgres-runtime-uid", type=int, required=True)
    parser.add_argument("--postgres-runtime-gid", type=int, required=True)
    parser.add_argument("--shadow-project", required=True)
    parser.add_argument("--shadow-root", required=True)
    parser.add_argument("--production-vhosts-b64", required=True)
    parser.add_argument("--nginx-freeze-generation-sha256", required=True)
    parser.add_argument("--nginx-rollback-generation-sha256", required=True)
    parser.add_argument("--nginx-shadow-readonly-generation-sha256", required=True)
    parser.add_argument("--nginx-shadow-writable-generation-sha256", required=True)
    parser.add_argument("--legacy-redis-policy", required=True)
    parser.add_argument("--shadow-redis-policy", required=True)
    parser.add_argument("--postcommit-executor-contract-sha256", required=True)
    parser.add_argument("--phase-evidence-schema-sha256", required=True)
    parser.add_argument("--host-agent-sha256", required=True)
    parser.add_argument("--host-agent-contract", type=Path, required=True)
    parser.add_argument("--host-agent-contract-sha256", required=True)
    parser.add_argument(
        "--business-write-policy",
        choices=(BUSINESS_WRITE_FORBIDDEN, BUSINESS_WRITE_FORWARD_ONLY),
        required=True,
    )
    parser.add_argument(
        "--required-journal-status",
        choices=(PRECOMMIT_JOURNAL_STATUS, POSTCOMMIT_JOURNAL_STATUS),
        required=True,
    )
    parser.add_argument("--payload-transport")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Reserved for the future bounded executor; currently always blocked.",
    )
    return parser


def request_from_args(args: argparse.Namespace) -> dict[str, Any]:
    vhosts = _decode_urlsafe_json(
        args.production_vhosts_b64,
        label="production vhost",
    )
    image_artifacts = _decode_urlsafe_json(
        args.image_artifacts_b64,
        label="image artifact",
    )
    runtime_image_ids = _decode_urlsafe_json(
        args.runtime_image_ids_b64,
        label="runtime image",
    )
    return {
        "schema": AGENT_SCHEMA,
        "operation": args.operation,
        "role": args.role,
        "expected_host": args.expected_host,
        "campaign_id": args.campaign_id,
        "operation_id": args.operation_id,
        "release_sha": args.release_sha,
        "legacy_release_sha": args.legacy_release_sha,
        "manifest_sha256": args.manifest_sha256,
        "approval_sha256": args.approval_sha256,
        "release_bundle_sha256": args.release_bundle_sha256,
        "release_bundle_bytes": args.release_bundle_bytes,
        "role_material_sha256": args.role_material_sha256,
        "role_material_bytes": args.role_material_bytes,
        "role_material_format": args.role_material_format,
        "image_artifacts": image_artifacts,
        "runtime_image_ids": runtime_image_ids,
        "shadow_compose_sha256": args.shadow_compose_sha256,
        "postgres_runtime_uid": args.postgres_runtime_uid,
        "postgres_runtime_gid": args.postgres_runtime_gid,
        "shadow_project": args.shadow_project,
        "shadow_root": args.shadow_root,
        "production_vhosts": vhosts,
        "nginx_freeze_generation_sha256": args.nginx_freeze_generation_sha256,
        "nginx_rollback_generation_sha256": args.nginx_rollback_generation_sha256,
        "nginx_shadow_readonly_generation_sha256": (
            args.nginx_shadow_readonly_generation_sha256
        ),
        "nginx_shadow_writable_generation_sha256": (
            args.nginx_shadow_writable_generation_sha256
        ),
        "legacy_redis_policy": args.legacy_redis_policy,
        "shadow_redis_policy": args.shadow_redis_policy,
        "postcommit_executor_contract_sha256": args.postcommit_executor_contract_sha256,
        "phase_evidence_schema_sha256": args.phase_evidence_schema_sha256,
        "host_agent_sha256": args.host_agent_sha256,
        "host_agent_contract": str(args.host_agent_contract),
        "host_agent_contract_sha256": args.host_agent_contract_sha256,
        "business_write_policy": args.business_write_policy,
        "required_journal_status": args.required_journal_status,
        "payload_transport": args.payload_transport,
    }


def parse_request_argv(
    argv: list[str],
    *,
    contract: dict[str, Any],
    observed_agent_sha256: str,
) -> tuple[dict[str, Any], bool]:
    args = build_parser().parse_args(argv)
    return (
        validate_request(
            request_from_args(args),
            contract=contract,
            observed_agent_sha256=observed_agent_sha256,
        ),
        bool(args.execute),
    )


def _precommit_manifest_path(request: dict[str, Any]) -> Path:
    role_path = str(request["role"]).replace("_", "-")
    return (
        PRECOMMIT_SECRET_ROOT
        / str(request["operation_id"])
        / role_path
        / "precommit-operation.json"
    )


def _precommit_worker_path(request: dict[str, Any]) -> Path:
    return (
        PRECOMMIT_PROJECT_ROOT
        / str(request["operation_id"])
        / "releases"
        / str(request["release_sha"])
        / "scripts"
        / "production_shadow_precommit_worker.py"
    )


def _precommit_acceptance_path(request: dict[str, Any]) -> Path:
    return _precommit_worker_path(request).with_name(
        "produce_production_shadow_readonly_acceptance.py"
    )


def _bounded_positive_int(value: Any, *, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 64 * 1024 * 1024 * 1024
    ):
        raise HostAgentError(f"{label} is outside its size bound")
    return value


def _load_precommit_manifest(request: dict[str, Any]) -> dict[str, Any]:
    path = _precommit_manifest_path(request)
    payload = _read_stable_file(
        path,
        label="precommit operation manifest",
        require_mode=0o600,
        max_size=2 * 1024 * 1024,
    )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise HostAgentError("precommit operation manifest is invalid JSON") from exc
    if (
        not isinstance(document, dict)
        or set(document) != PRECOMMIT_MANIFEST_FIELDS
        or document.get("schema") != PRECOMMIT_MANIFEST_SCHEMA
        or payload != _canonical_json(document) + b"\n"
    ):
        raise HostAgentError(
            "precommit operation manifest fields or canonical bytes are not exact"
        )
    if (
        document["operation_id"] != request["operation_id"]
        or document["role"] != request["role"]
        or document["release_sha"] != request["release_sha"]
        or document["controller_manifest_sha256"] != request["manifest_sha256"]
        or document["approval_sha256"] != request["approval_sha256"]
        or document["role_material_sha256"]
        != request["role_material_sha256"]
        or document["canonical_compose_sha256"]
        != request["shadow_compose_sha256"]
        or document["postgres_runtime_uid"]
        != request["postgres_runtime_uid"]
        or document["postgres_runtime_gid"]
        != request["postgres_runtime_gid"]
        or not isinstance(document["release_tree_sha"], str)
        or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
    ):
        raise HostAgentError("precommit operation manifest differs from the request")
    for field in (
        "role_compose_sha256",
        "environment_sha256",
        "worker_sha256",
        "acceptance_producer_sha256",
    ):
        _nonzero_sha256(document[field], label=f"precommit {field}")

    image_artifacts = document["image_artifacts"]
    if (
        not isinstance(image_artifacts, dict)
        or set(image_artifacts) != PRECOMMIT_IMAGE_FIELDS
        or image_artifacts != request["image_artifacts"]
    ):
        raise HostAgentError(
            "precommit image artifact inventory differs from the request"
        )
    runtime_image_ids = document["runtime_image_ids"]
    if (
        not isinstance(runtime_image_ids, dict)
        or set(runtime_image_ids) != PRECOMMIT_IMAGE_FIELDS
        or runtime_image_ids != request["runtime_image_ids"]
    ):
        raise HostAgentError(
            "precommit runtime image inventory differs from the request"
        )

    artifacts = document["artifacts"]
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != PRECOMMIT_ARTIFACT_KINDS
    ):
        raise HostAgentError("precommit artifact inventory is not exact")
    expected_bindings = {
        "release-bundle": (
            request["release_bundle_sha256"],
            request["release_bundle_bytes"],
        ),
        "role-material": (
            request["role_material_sha256"],
            request["role_material_bytes"],
        ),
        "app-image-archive": (
            request["image_artifacts"]["app"]["archive_sha256"],
            request["image_artifacts"]["app"]["archive_bytes"],
        ),
        "postgres-image-archive": (
            request["image_artifacts"]["postgres"]["archive_sha256"],
            request["image_artifacts"]["postgres"]["archive_bytes"],
        ),
        "redis-image-archive": (
            request["image_artifacts"]["redis"]["archive_sha256"],
            request["image_artifacts"]["redis"]["archive_bytes"],
        ),
        "nginx-image-archive": (
            request["image_artifacts"]["nginx"]["archive_sha256"],
            request["image_artifacts"]["nginx"]["archive_bytes"],
        ),
    }
    for kind, row in artifacts.items():
        if not isinstance(row, dict) or set(row) != PRECOMMIT_ARTIFACT_FIELDS:
            raise HostAgentError("precommit artifact fields are not exact")
        _nonzero_sha256(row["sha256"], label=f"precommit {kind}")
        _bounded_positive_int(row["bytes"], label=f"precommit {kind} bytes")
        if kind in {"uploads-archive", "audit-archive"}:
            _nonzero_sha256(
                row["restored_tree_sha256"],
                label=f"precommit {kind} restored tree",
            )
        elif row["restored_tree_sha256"] is not None:
            raise HostAgentError(
                f"precommit {kind} must not bind a restored tree"
            )
        expected = expected_bindings.get(kind)
        if expected is not None and (row["sha256"], row["bytes"]) != expected:
            raise HostAgentError(
                f"precommit {kind} differs from the host request"
            )

    source = document["source_database"]
    if (
        not isinstance(source, dict)
        or set(source) != PRECOMMIT_SOURCE_DATABASE_FIELDS
        or source["fingerprint_algorithm"]
        != "pg-copy-jsonl-sha256-canonical-session-v1"
        or not isinstance(source["alembic_revision"], str)
        or not source["alembic_revision"]
        or not isinstance(document["target_migration_revision"], str)
        or not document["target_migration_revision"]
    ):
        raise HostAgentError("precommit source database binding is invalid")
    _nonzero_sha256(
        source["database_fingerprint_sha256"],
        label="precommit source database fingerprint",
    )
    for field, minimum in (("row_count", 0), ("table_count", 1)):
        value = source[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= 10**15
        ):
            raise HostAgentError(
                f"precommit source database {field} is outside its bound"
            )

    for source_path, expected_sha256, label in (
        (
            _precommit_worker_path(request),
            document["worker_sha256"],
            "precommit worker",
        ),
        (
            _precommit_acceptance_path(request),
            document["acceptance_producer_sha256"],
            "precommit acceptance producer",
        ),
    ):
        observed = hashlib.sha256(
            _read_stable_file(
                source_path,
                label=label,
                require_mode=None,
                max_size=4 * 1024 * 1024,
            )
        ).hexdigest()
        if observed != expected_sha256:
            raise HostAgentError(f"{label} differs from the fixed manifest")
    return document


def execute_precommit_request(request: dict[str, Any]) -> dict[str, Any]:
    operation = str(request["operation"])
    if operation not in PRECOMMIT_ACTIONS:
        raise HostAgentError(
            "production execution is hard-disabled for this operation"
        )
    manifest = _load_precommit_manifest(request)
    confirmation = (
        f"prepare-precommit:{request['operation_id']}:{request['role']}:"
        f"{operation}:{request['release_sha']}"
    )
    argv = [
        PYTHON3,
        str(_precommit_worker_path(request)),
        "--manifest",
        str(_precommit_manifest_path(request)),
        "--action",
        operation,
        "--apply",
        "--confirm",
        confirmation,
    ]
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=4 * 60 * 60,
            env=_EXEC_ENV,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostAgentError("precommit worker could not be executed") from exc
    if (
        completed.returncode != 0
        or len(completed.stdout) > 2 * 1024 * 1024
        or len(completed.stderr) > 2 * 1024 * 1024
    ):
        raise HostAgentError("precommit worker failed closed")
    try:
        result = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise HostAgentError("precommit worker returned invalid JSON") from exc
    if (
        not isinstance(result, dict)
        or result.get("status") not in {"completed", "already-completed"}
        or result.get("action") != operation
        or result.get("operation_id") != request["operation_id"]
        or result.get("role") != request["role"]
    ):
        raise HostAgentError("precommit worker result binding is invalid")
    return {
        "schema": "production-shadow-host-agent-execution-v1",
        "status": "executed-precommit",
        "operation": operation,
        "role": request["role"],
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "manifest_sha256": hashlib.sha256(
            _canonical_json(manifest)
        ).hexdigest(),
        "worker_result": result,
        "business_write_allowed": False,
        "freeze_performed": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "production_contacted": True,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        if os.geteuid() != 0:
            raise HostAgentError("production-shadow host agent must run as root")
        args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
        if args.host_agent_contract != FIXED_CONTRACT_PATH:
            raise HostAgentError("host-agent contract path is not fixed")
        agent_sha256 = hash_agent_artifact(Path(__file__).resolve())
        contract = read_contract(
            args.host_agent_contract,
            expected_sha256=args.host_agent_contract_sha256,
        )
        request = validate_request(
            request_from_args(args),
            contract=contract,
            observed_agent_sha256=agent_sha256,
        )
        local_addresses = observe_local_ipv4_addresses()
        if request["expected_host"] not in local_addresses:
            raise HostAgentError(
                "local host identity differs from the manifest-bound role"
            )
        if args.execute:
            result = execute_precommit_request(request)
            print(json.dumps(result, sort_keys=True))
            return 0
        payload = {
            "schema": "production-shadow-host-agent-validation-v1",
            "status": "validated-request",
            "request_sha256": request_sha256(
                request,
                contract=contract,
                observed_agent_sha256=agent_sha256,
            ),
            "operation": request["operation"],
            "role": request["role"],
            "campaign_id": request["campaign_id"],
            "operation_id": request["operation_id"],
            "app_release_sha": request["release_sha"],
            "manifest_sha256": request["manifest_sha256"],
            "approval_sha256": request["approval_sha256"],
            "expected_host": request["expected_host"],
            "observed_host": request["expected_host"],
            "required_journal_status": request["required_journal_status"],
            "business_write_policy": request["business_write_policy"],
            "agent_artifact_sha256": agent_sha256,
            "host_agent_contract_sha256": contract_sha256(contract),
            "transport": contract["topology"][request["role"]]["transport"],
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "host_identity_observed": True,
            "execution_supported": request["operation"] in PRECOMMIT_ACTIONS,
            "production_contacted": False,
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    except (HostAgentError, SystemExit) as exc:
        payload = {
            "status": "blocked",
            "error": str(exc),
            "error_class": type(exc).__name__,
            "execution_supported": False,
            "production_contacted": False,
        }
        print(json.dumps(payload, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
