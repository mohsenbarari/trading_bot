#!/usr/bin/env python3
"""Render deterministic, phase-preserving production Compose material per role."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_text
from scripts.verify_three_site_production_shadow_compose import (
    collect_source_failures,
)


ROLE_PREFIXES = {
    "bot-fi": "bot_fi_",
    "webapp-fi": "webapp_fi_",
    "webapp-ir": "webapp_ir_",
}
COMMON_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "project_root",
        "release_root",
        "data_root",
        "secret_root",
        "dr_ca_sha256",
        "dr_tls_attestation_sha256",
        "dr_tls_attested_at_epoch",
    }
)
ROLE_OPERATION_KEYS = {
    "bot-fi": COMMON_OPERATION_KEYS,
    "webapp-fi": COMMON_OPERATION_KEYS
    | {
        "witness_url",
        "witness_ip",
        "witness_tls_san",
        "witness_ca_sha256",
        "witness_server_cert_sha256",
        "witness_release_sha",
        "witness_release_manifest_sha256",
        "witness_health_attestation_sha256",
        "witness_health_attested_at_epoch",
        "webapp_provider_config_sha256",
        "blob_policy_attestation_sha256",
        "blob_policy_attested_at_epoch",
        "blob_compatibility_attestation_sha256",
        "blob_compatibility_attested_at_epoch",
    },
    "webapp-ir": COMMON_OPERATION_KEYS
    | {
        "witness_url",
        "witness_ip",
        "witness_tls_san",
        "witness_ca_sha256",
        "witness_server_cert_sha256",
        "witness_release_sha",
        "witness_release_manifest_sha256",
        "witness_health_attestation_sha256",
        "witness_health_attested_at_epoch",
        "webapp_provider_config_sha256",
        "blob_policy_attestation_sha256",
        "blob_policy_attested_at_epoch",
        "blob_compatibility_attestation_sha256",
        "blob_compatibility_attested_at_epoch",
    },
}
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ENV_REFERENCE_RE = re.compile(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)")
REQUIRED_ENV_REFERENCE_RE = re.compile(
    r"(?<!\$)\$\{([A-Z][A-Z0-9_]*):\?"
)
UNSAFE_DOTENV_VALUE_RE = re.compile(r"[\x00-\x20\x7f$#'\\`]")
PROJECT_EXPRESSION = (
    "${PRODUCTION_SHADOW_PROJECT:?operation-bound project is required}"
)


class ProductionShadowRoleError(RuntimeError):
    """Raised when role material is not closed over exactly one host."""


def _service_networks(service: dict[str, Any]) -> set[str]:
    value = service.get("networks", [])
    if isinstance(value, list):
        return {str(item) for item in value}
    if isinstance(value, dict):
        return {str(item) for item in value}
    raise ProductionShadowRoleError("service networks must be a list or object")


def _named_volume(value: Any) -> str | None:
    if isinstance(value, str):
        source = value.split(":", 1)[0]
        if source and not source.startswith((".", "/", "${")):
            return source
        return None
    if isinstance(value, dict) and value.get("type", "volume") == "volume":
        source = str(value.get("source") or "")
        return source or None
    return None


def render_role_compose(
    payload: dict[str, Any],
    *,
    role: str,
    scope: str = "full",
) -> dict[str, Any]:
    prefix = ROLE_PREFIXES.get(role)
    if prefix is None or scope not in {"full", "prepare"}:
        raise ProductionShadowRoleError("unknown production shadow role")
    services = payload.get("services")
    operation = payload.get("x-production-shadow-operation")
    if not isinstance(services, dict) or not isinstance(operation, dict):
        raise ProductionShadowRoleError(
            "canonical production Compose contract is incomplete"
        )

    selected: dict[str, dict[str, Any]] = {}
    for name, raw_service in services.items():
        if not isinstance(raw_service, dict):
            raise ProductionShadowRoleError(f"invalid canonical service: {name}")
        matching_roles = [
            candidate
            for candidate, candidate_prefix in ROLE_PREFIXES.items()
            if str(name).startswith(candidate_prefix)
        ]
        if len(matching_roles) != 1:
            raise ProductionShadowRoleError(
                f"canonical service has no unique role owner: {name}"
            )
        profiles = raw_service.get("profiles")
        expected_profile_prefix = f"{matching_roles[0]}-"
        if (
            not isinstance(profiles, list)
            or not profiles
            or any(
                not isinstance(profile, str)
                or not profile.startswith(expected_profile_prefix)
                for profile in profiles
            )
        ):
            raise ProductionShadowRoleError(
                f"canonical service has a cross-role or missing phase profile: {name}"
            )
        if matching_roles[0] == role:
            selected[str(name)] = dict(raw_service)
    if scope == "prepare":
        allowed = {
            f"{prefix}db",
            f"{prefix}restore_tool",
            f"{prefix}db_roles",
            f"{prefix}migration",
            f"{prefix}db_roles_post_migration",
            f"{prefix}db_fencing",
        }
        if role == "webapp-ir":
            allowed.add("webapp_ir_writer_fence")
        selected = {
            name: service
            for name, service in selected.items()
            if name in allowed
        }
    if not selected:
        raise ProductionShadowRoleError("selected role has no services")

    for name, service in selected.items():
        depends_on = service.get("depends_on", {})
        dependencies = (
            set(map(str, depends_on))
            if isinstance(depends_on, (dict, list))
            else set()
        )
        missing = dependencies - set(selected)
        if missing:
            raise ProductionShadowRoleError(
                f"role service {name} depends on foreign services: {sorted(missing)}"
            )

    referenced_networks: set[str] = set()
    referenced_volumes: set[str] = set()
    for service in selected.values():
        referenced_networks.update(_service_networks(service))
        for volume in service.get("volumes", []) or []:
            name = _named_volume(volume)
            if name:
                referenced_volumes.add(name)

    canonical_networks = payload.get("networks")
    canonical_volumes = payload.get("volumes")
    if (
        not isinstance(canonical_networks, dict)
        or not isinstance(canonical_volumes, dict)
        or referenced_networks - set(canonical_networks)
        or referenced_volumes - set(canonical_volumes)
    ):
        raise ProductionShadowRoleError(
            "role Compose references an undeclared network or volume"
        )

    operation_keys = (
        COMMON_OPERATION_KEYS
        if scope == "prepare"
        else ROLE_OPERATION_KEYS[role]
    )
    missing_operation_keys = operation_keys - set(operation)
    if missing_operation_keys:
        raise ProductionShadowRoleError(
            "canonical operation metadata lacks role fields: "
            f"{sorted(missing_operation_keys)}"
        )
    result: dict[str, Any] = {
        "name": f"{PROJECT_EXPRESSION}-{role}",
        "x-production-shadow-operation": {
            name: operation[name]
            for name in sorted(operation_keys)
        },
        "services": selected,
    }
    if referenced_networks:
        result["networks"] = {
            name: canonical_networks[name]
            for name in sorted(referenced_networks)
        }
    if referenced_volumes:
        result["volumes"] = {
            name: canonical_volumes[name]
            for name in sorted(referenced_volumes)
        }
    return result


def canonical_role_compose_bytes(payload: dict[str, Any]) -> bytes:
    return yaml.safe_dump(
        payload,
        allow_unicode=False,
        default_flow_style=False,
        sort_keys=True,
    ).encode("utf-8")


def referenced_environment_names(payload: dict[str, Any]) -> frozenset[str]:
    material = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return frozenset(ENV_REFERENCE_RE.findall(material))


def required_environment_names(payload: dict[str, Any]) -> frozenset[str]:
    material = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return frozenset(REQUIRED_ENV_REFERENCE_RE.findall(material))


def parse_env_values(source: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in source.splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        name, separator, value = raw_line.partition("=")
        if (
            not separator
            or not ENV_NAME_RE.fullmatch(name)
            or name in values
            or not value
            or UNSAFE_DOTENV_VALUE_RE.search(value)
            or (
                '"' in value
                and (
                    value[:1] not in {"{", "["}
                    or value[-1:] not in {"}", "]"}
                    or not _is_canonical_compact_json(value)
                )
            )
        ):
            raise ProductionShadowRoleError(
                "environment source contains an invalid or duplicate entry"
            )
        values[name] = value
    return values


def _is_canonical_compact_json(value: str) -> bool:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return False
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=False,
    ) == value


def canonical_role_env_bytes(
    values: dict[str, str],
    *,
    required_names: frozenset[str],
    optional_names: frozenset[str] = frozenset(),
) -> bytes:
    missing = required_names - set(values)
    if missing:
        raise ProductionShadowRoleError(
            f"environment source lacks role variables: {sorted(missing)}"
        )
    selected_names = required_names | (optional_names & set(values))
    return (
        "# Generated from the canonical production shadow manifest.\n"
        "# Root-only role material; do not share it with another host.\n"
        + "".join(f"{name}={values[name]}\n" for name in sorted(selected_names))
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    parent = path.parent
    try:
        parent_metadata = parent.lstat()
    except FileNotFoundError as exc:
        raise ProductionShadowRoleError(
            "role output parent must already exist"
        ) from exc
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != 0
        or stat.S_IMODE(parent_metadata.st_mode) & 0o077
    ):
        raise ProductionShadowRoleError(
            "role output parent must be a real root-owned root-only directory"
        )

    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if (
            not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != 0
            or stat.S_IMODE(existing.st_mode) != mode
        ):
            raise ProductionShadowRoleError(
                "existing role output is not a root-owned regular file "
                "with the exact mode"
            )
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                existing_content = stream.read()
        finally:
            os.close(descriptor)
        if existing_content != content:
            raise ProductionShadowRoleError(
                "refusing to overwrite different role material"
            )
        return

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise ProductionShadowRoleError("short role material write")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary,
                path,
                follow_symlinks=False,
            )
        except FileExistsError:
            existing = path.lstat()
            if (
                not stat.S_ISREG(existing.st_mode)
                or existing.st_uid != 0
                or stat.S_IMODE(existing.st_mode) != mode
            ):
                raise ProductionShadowRoleError(
                    "refusing to overwrite different role material"
                )
            existing_descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                with os.fdopen(
                    existing_descriptor,
                    "rb",
                    closefd=False,
                ) as stream:
                    existing_content = stream.read()
            finally:
                os.close(existing_descriptor)
            if existing_content != content:
                raise ProductionShadowRoleError(
                    "refusing to overwrite different role material"
                )
        directory_fd = os.open(
            parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise ProductionShadowRoleError("role output mode is unsafe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(ROLE_PREFIXES), required=True)
    parser.add_argument(
        "--scope",
        choices=("full", "prepare"),
        default="full",
    )
    parser.add_argument(
        "--compose",
        type=Path,
        default=Path("deploy/production/docker-compose.three-site-shadow.yml"),
    )
    parser.add_argument("--env-source", type=Path, required=True)
    parser.add_argument("--compose-output", type=Path, required=True)
    parser.add_argument("--env-output", type=Path, required=True)
    parser.add_argument("--expected-compose-sha256", required=True)
    args = parser.parse_args(argv)

    try:
        compose_bytes = args.compose.read_bytes()
        if (
            not re.fullmatch(r"[0-9a-f]{64}", args.expected_compose_sha256)
            or hashlib.sha256(compose_bytes).hexdigest()
            != args.expected_compose_sha256
        ):
            raise ProductionShadowRoleError(
                "canonical Compose hash does not match the release manifest"
            )
        compose_text = compose_bytes.decode("utf-8")
        payload = yaml.safe_load(compose_text)
        if not isinstance(payload, dict):
            raise ProductionShadowRoleError("canonical Compose must be a mapping")
        source_failures = collect_source_failures(payload, compose_text)
        if source_failures:
            raise ProductionShadowRoleError(
                "canonical Compose contract failed: "
                + "; ".join(dict.fromkeys(source_failures))
            )
        rendered = render_role_compose(
            payload,
            role=args.role,
            scope=args.scope,
        )
        values = parse_env_values(
            read_secure_text(
                args.env_source,
                label="production shadow canonical environment",
            )
        )
        role_env = canonical_role_env_bytes(
            values,
            required_names=required_environment_names(rendered),
            optional_names=(
                referenced_environment_names(rendered)
                - required_environment_names(rendered)
            ),
        )
        role_compose = canonical_role_compose_bytes(rendered)
        _atomic_write(args.compose_output, role_compose, mode=0o600)
        _atomic_write(args.env_output, role_env, mode=0o600)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "rendered",
                "role": args.role,
                "scope": args.scope,
                "compose_output": str(args.compose_output),
                "compose_sha256": hashlib.sha256(role_compose).hexdigest(),
                "environment_output": str(args.env_output),
                "environment_sha256": hashlib.sha256(role_env).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
