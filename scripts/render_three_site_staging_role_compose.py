#!/usr/bin/env python3
"""Render one deterministic, secret-scoped Compose manifest per staging role."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

import yaml


ROLE_PREFIXES = {
    "bot-fi": "bot_fi_",
    "webapp-fi": "webapp_fi_",
    "webapp-ir": "webapp_ir_",
    "witness": "witness_",
}


class RoleComposeError(RuntimeError):
    pass


ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ENV_REFERENCE_RE = re.compile(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)")


def _service_networks(service: dict[str, Any]) -> set[str]:
    value = service.get("networks", [])
    if isinstance(value, list):
        return {str(item) for item in value}
    if isinstance(value, dict):
        return {str(item) for item in value}
    raise RoleComposeError("service networks must be a list or object")


def _named_volume(value: Any) -> str | None:
    if isinstance(value, str):
        source = value.split(":", 1)[0]
        if source and not source.startswith(('.', '/', '${')):
            return source
        return None
    if isinstance(value, dict) and value.get("type", "volume") == "volume":
        source = str(value.get("source") or "")
        return source or None
    return None


def render_role_compose(payload: dict[str, Any], *, role: str) -> dict[str, Any]:
    if role not in ROLE_PREFIXES:
        raise RoleComposeError("unknown three-site staging role")
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), dict):
        raise RoleComposeError("canonical Compose services are missing")
    selected: dict[str, dict[str, Any]] = {}
    for name, raw_service in payload["services"].items():
        if not isinstance(raw_service, dict):
            raise RoleComposeError(f"invalid canonical service: {name}")
        profiles = raw_service.get("profiles")
        expected_role = next(
            (
                candidate
                for candidate, prefix in ROLE_PREFIXES.items()
                if str(name).startswith(prefix)
            ),
            None,
        )
        if expected_role is None or profiles != [expected_role]:
            raise RoleComposeError(f"canonical service has no exact role profile: {name}")
        if expected_role != role:
            continue
        service = dict(raw_service)
        service.pop("profiles", None)
        selected[str(name)] = service
    if not selected:
        raise RoleComposeError("selected staging role has no services")
    prefix = ROLE_PREFIXES[role]
    if any(not name.startswith(prefix) for name in selected):
        raise RoleComposeError("role Compose contains a cross-role service")
    for name, service in selected.items():
        depends_on = service.get("depends_on", {})
        dependencies = (
            set(map(str, depends_on))
            if isinstance(depends_on, (dict, list))
            else set()
        )
        missing = dependencies - set(selected)
        if missing:
            raise RoleComposeError(
                f"role service {name} depends on cross-role services: {sorted(missing)}"
            )

    referenced_networks: set[str] = set()
    referenced_volumes: set[str] = set()
    for service in selected.values():
        referenced_networks.update(_service_networks(service))
        for volume in service.get("volumes", []) or []:
            name = _named_volume(volume)
            if name:
                referenced_volumes.add(name)
    canonical_networks = payload.get("networks", {})
    canonical_volumes = payload.get("volumes", {})
    if (
        not isinstance(canonical_networks, dict)
        or not isinstance(canonical_volumes, dict)
        or referenced_networks - set(canonical_networks)
        or referenced_volumes - set(canonical_volumes)
    ):
        raise RoleComposeError("role Compose references an undeclared network or volume")
    result: dict[str, Any] = {
        "name": f"trading-bot-three-site-staging-{role}",
        "services": selected,
    }
    if referenced_networks:
        result["networks"] = {
            name: canonical_networks[name] for name in sorted(referenced_networks)
        }
    if referenced_volumes:
        result["volumes"] = {
            name: canonical_volumes[name] for name in sorted(referenced_volumes)
        }
    return result


def canonical_role_compose_bytes(payload: dict[str, Any]) -> bytes:
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    ).encode("utf-8")


def referenced_environment_names(payload: dict[str, Any]) -> frozenset[str]:
    material = json.dumps(payload, sort_keys=True)
    return frozenset(ENV_REFERENCE_RE.findall(material))


def parse_env_values(source: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not ENV_NAME_RE.fullmatch(name) or name in values:
            raise RoleComposeError("environment source contains an invalid/duplicate entry")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise RoleComposeError("environment source contains an unsafe value")
        values[name] = value
    return values


def canonical_role_env_bytes(
    values: dict[str, str], *, required_names: frozenset[str]
) -> bytes:
    missing = required_names - set(values)
    if missing:
        raise RoleComposeError(
            f"environment source lacks role variables: {sorted(missing)}"
        )
    return (
        "# Generated from the canonical three-site staging manifest.\n"
        "# Replace every CHANGE_ME value; keep this file mode 0600.\n"
        + "".join(f"{name}={values[name]}\n" for name in sorted(required_names))
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if path.is_symlink():
        raise RoleComposeError("role Compose output cannot be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        if os.write(descriptor, content) != len(content):
            raise RoleComposeError("short role Compose write")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != mode:
        raise RoleComposeError("role Compose output mode is unsafe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(ROLE_PREFIXES), required=True)
    parser.add_argument(
        "--compose",
        type=Path,
        default=Path("deploy/staging/docker-compose.three-site.yml"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-source", type=Path)
    parser.add_argument("--env-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if (args.env_source is None) != (args.env_output is None):
            raise RoleComposeError("--env-source and --env-output must be supplied together")
        source = yaml.safe_load(args.compose.read_text(encoding="utf-8"))
        role_payload = render_role_compose(source, role=args.role)
        rendered = canonical_role_compose_bytes(role_payload)
        _atomic_write(args.output, rendered, mode=0o640)
        env_rendered = None
        if args.env_source is not None:
            env_rendered = canonical_role_env_bytes(
                parse_env_values(args.env_source.read_text(encoding="utf-8")),
                required_names=referenced_environment_names(role_payload),
            )
            _atomic_write(args.env_output, env_rendered, mode=0o600)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "rendered",
                "role": args.role,
                "output": str(args.output),
                "sha256": hashlib.sha256(rendered).hexdigest(),
                "environment_output": str(args.env_output) if args.env_output else None,
                "environment_sha256": (
                    hashlib.sha256(env_rendered).hexdigest()
                    if env_rendered is not None
                    else None
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
