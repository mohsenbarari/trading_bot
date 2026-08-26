#!/usr/bin/env python3
"""Preflight and inventory tooling for the Stage 3 market Docker foundation.

The tool never deploys services.  ``prepare-paths`` is dry-run by default and
requires two explicit flags before it may create or repair a host path.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_BASE = REPO_ROOT / "deploy" / "market-data" / "compose.yml"
COMPOSE_ROLE = {
    "web": REPO_ROOT / "deploy" / "market-data" / "compose.web.yml",
    "bot": REPO_ROOT / "deploy" / "market-data" / "compose.bot.yml",
}
EXPECTED_SERVICES = {
    "web": {
        "market-database",
        "market-migration",
        "market-capture-account1",
        "market-capture-account2",
        "market-processor",
        "market-fact-sync-worker",
        "estimator-snapshot-receiver",
    },
    "bot": {
        "market-fact-receiver",
        "market-store-adapter",
        "coin-estimator",
        "estimator-snapshot-sender",
    },
}
EXPECTED_RECEIVER = {
    "web": "estimator-snapshot-receiver",
    "bot": "market-fact-receiver",
}
RELEASE_SHA = re.compile(r"^[0-9a-f]{8,64}$")
IMMUTABLE_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


class Stage3Error(RuntimeError):
    pass


@dataclass(frozen=True)
class PathContract:
    relative: str
    uid: int
    gid: int
    mode: int = 0o700


PATH_CONTRACTS = {
    "web": (
        PathContract("postgres", 70, 70),
        PathContract("capture", 10001, 10001),
        PathContract("capture/account1", 10001, 10001),
        PathContract("capture/account2", 10001, 10001),
        PathContract("sessions", 10001, 10001),
        PathContract("sessions/account1", 10001, 10001),
        PathContract("sessions/account2", 10001, 10001),
        PathContract("snapshots", 10001, 10001),
        PathContract("state", 10001, 10001),
        PathContract("state/market-migration", 10001, 10001),
        PathContract("state/market-capture-account1", 10001, 10001),
        PathContract("state/market-capture-account2", 10001, 10001),
        PathContract("state/market-processor", 10001, 10001),
        PathContract("state/market-fact-sync-worker", 10001, 10001),
        PathContract("state/estimator-snapshot-receiver", 10001, 10001),
        PathContract("backups-staging", 0, 0),
    ),
    "bot": (
        PathContract("state", 10001, 10001),
        PathContract("state/market-fact-receiver", 10001, 10001),
        PathContract("state/market-store-adapter", 10001, 10001),
        PathContract("state/coin-estimator", 10001, 10001),
        PathContract("state/estimator-snapshot-sender", 10001, 10001),
        PathContract("market-store", 10001, 10001),
        PathContract("models", 10001, 10001),
        PathContract("snapshots", 10001, 10001),
    ),
}


def json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def run(arguments: Sequence[str], *, label: str) -> str:
    result = subprocess.run(
        list(arguments),
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise Stage3Error(f"{label}_failed_rc_{result.returncode}")
    return result.stdout


def validate_data_root(root: Path) -> Path:
    if not root.is_absolute():
        raise Stage3Error("data_root_must_be_absolute")
    resolved_parent = root.parent.resolve(strict=False)
    normalized = resolved_parent / root.name
    if normalized in {Path("/"), Path("/srv"), Path("/root"), Path("/tmp")}:
        raise Stage3Error("data_root_too_broad")
    fixture_root = (
        normalized.parent == Path("/tmp")
        and normalized.name.startswith("market-stage3-")
    )
    if len(normalized.parts) < 4 and not fixture_root:
        raise Stage3Error("data_root_too_broad")
    if root.exists() and root.is_symlink():
        raise Stage3Error("data_root_symlink_forbidden")
    return normalized


def inspect_path_contract(root: Path, role: str) -> list[dict[str, Any]]:
    root = validate_data_root(root)
    findings: list[dict[str, Any]] = []
    for contract in PATH_CONTRACTS[role]:
        path = root / contract.relative
        if not path.exists():
            findings.append({"path": contract.relative, "status": "missing"})
            continue
        info = path.lstat()
        actual_mode = stat.S_IMODE(info.st_mode)
        status = "ok"
        if not stat.S_ISDIR(info.st_mode):
            status = "not_directory"
        elif path.is_symlink():
            status = "symlink_forbidden"
        elif (info.st_uid, info.st_gid) != (contract.uid, contract.gid):
            status = "owner_mismatch"
        elif actual_mode != contract.mode:
            status = "mode_mismatch"
        findings.append(
            {
                "path": contract.relative,
                "status": status,
                "expected_uid": contract.uid,
                "expected_gid": contract.gid,
                "expected_mode": format(contract.mode, "04o"),
            }
        )
    return findings


def prepare_path_contract(root: Path, role: str) -> list[dict[str, Any]]:
    root = validate_data_root(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise Stage3Error("data_root_symlink_forbidden")
    os.chmod(root, 0o700)
    for contract in PATH_CONTRACTS[role]:
        path = root / contract.relative
        if path.exists() and (path.is_symlink() or not path.is_dir()):
            raise Stage3Error("unsafe_existing_path")
        path.mkdir(mode=contract.mode, parents=True, exist_ok=True)
        os.chown(path, contract.uid, contract.gid)
        os.chmod(path, contract.mode)
    return inspect_path_contract(root, role)


def validate_bind_ip(value: str, *, fixture: bool) -> str:
    address = ipaddress.ip_address(value)
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        raise Stage3Error("receiver_bind_ip_unsafe")
    if fixture and address.is_loopback:
        return str(address)
    if address.is_loopback or not address.is_private:
        raise Stage3Error("receiver_bind_ip_must_be_provider_private")
    return str(address)


def compose_command(role: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_BASE),
        "-f",
        str(COMPOSE_ROLE[role]),
        "--profile",
        role,
    ]


def render_compose(role: str) -> dict[str, Any]:
    output = run(
        [*compose_command(role), "config", "--format", "json"],
        label=f"compose_{role}_config",
    )
    document = json.loads(output)
    audit_compose(document, role=role, fixture=os.getenv("MARKET_PIPELINE_MODE") == "fixture")
    return document


def audit_compose(document: Mapping[str, Any], *, role: str, fixture: bool) -> None:
    services = document.get("services")
    if not isinstance(services, dict) or set(services) != EXPECTED_SERVICES[role]:
        raise Stage3Error("compose_service_inventory_mismatch")
    receiver = EXPECTED_RECEIVER[role]
    for name, raw_service in services.items():
        service = dict(raw_service)
        user = str(service.get("user", ""))
        if user.split(":", 1)[0] in {"", "0", "root"}:
            raise Stage3Error("compose_root_runtime_forbidden")
        if service.get("read_only") is not True:
            raise Stage3Error("compose_read_only_required")
        if service.get("privileged") is True:
            raise Stage3Error("compose_privileged_forbidden")
        if "ALL" not in service.get("cap_drop", []):
            raise Stage3Error("compose_cap_drop_all_required")
        security = service.get("security_opt", [])
        if "no-new-privileges:true" not in security:
            raise Stage3Error("compose_no_new_privileges_required")
        profiles = set(service.get("profiles", []))
        if role not in profiles:
            raise Stage3Error("compose_role_profile_missing")
        ports = service.get("ports", [])
        if name != receiver and ports:
            raise Stage3Error("compose_unexpected_published_port")
        if name == receiver:
            if len(ports) != 1:
                raise Stage3Error("compose_receiver_port_missing")
            host_ip = str(ports[0].get("host_ip", ""))
            validate_bind_ip(host_ip, fixture=fixture)
            if int(ports[0].get("target", 0)) != 9443:
                raise Stage3Error("compose_receiver_target_port_mismatch")
        environment = service.get("environment", {}) or {}
        for key, value in environment.items():
            upper = str(key).upper()
            if any(marker in upper for marker in ("TOKEN", "PASSWORD", "SECRET")):
                if value not in {None, ""} and not upper.endswith("_FILE"):
                    raise Stage3Error("compose_plaintext_secret_environment_forbidden")


def image_metadata(image: str, release_sha: str, *, fixture: bool) -> dict[str, Any]:
    if not fixture and not IMMUTABLE_IMAGE.fullmatch(image):
        raise Stage3Error("release_image_must_be_digest_pinned")
    output = run(
        ["docker", "image", "inspect", image],
        label="image_inspect",
    )
    document = json.loads(output)[0]
    labels = document.get("Config", {}).get("Labels", {}) or {}
    if labels.get("org.opencontainers.image.revision") != release_sha:
        raise Stage3Error("image_release_sha_label_mismatch")
    if document.get("Config", {}).get("User") != "10001:10001":
        raise Stage3Error("image_nonroot_user_mismatch")
    if document.get("Architecture") != "amd64" or document.get("Os") != "linux":
        raise Stage3Error("image_platform_mismatch")
    environment = document.get("Config", {}).get("Env", []) or []
    if any(
        re.match(r"(?i)^(?:.*(?:TOKEN|PASSWORD|SECRET).*)=.+", str(item))
        for item in environment
    ):
        raise Stage3Error("image_environment_contains_secret")
    return {
        "image_id": document.get("Id"),
        "repo_digests": sorted(document.get("RepoDigests") or []),
        "platform": f"{document.get('Os')}/{document.get('Architecture')}",
        "revision": labels.get("org.opencontainers.image.revision"),
        "version": labels.get("org.opencontainers.image.version"),
        "runtime_user": document.get("Config", {}).get("User"),
    }


def inventory(
    document: Mapping[str, Any], *, role: str, image: Mapping[str, Any]
) -> dict[str, Any]:
    rendered_services = []
    for name, raw_service in sorted(document["services"].items()):
        service = dict(raw_service)
        rendered_services.append(
            {
                "name": name,
                "image": service.get("image"),
                "command": service.get("command", []),
                "profiles": sorted(service.get("profiles", [])),
                "user": service.get("user"),
                "read_only": service.get("read_only"),
                "cap_drop": sorted(service.get("cap_drop", [])),
                "security_opt": sorted(service.get("security_opt", [])),
                "ports": service.get("ports", []),
                "networks": sorted((service.get("networks") or {}).keys()),
                "mount_targets": sorted(
                    volume.get("target")
                    for volume in service.get("volumes", [])
                    if isinstance(volume, dict) and volume.get("target")
                ),
                "secret_names": sorted(
                    secret.get("source") if isinstance(secret, dict) else str(secret)
                    for secret in service.get("secrets", [])
                ),
            }
        )
    return {
        "schema": "market_pipeline_inventory/1.0",
        "role": role,
        "project_name": document.get("name"),
        "image": dict(image),
        "services": rendered_services,
        "networks": sorted(document.get("networks", {})),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-paths")
    prepare.add_argument("--role", choices=sorted(PATH_CONTRACTS), required=True)
    prepare.add_argument("--root", type=Path, required=True)
    prepare.add_argument("--apply", action="store_true")
    prepare.add_argument("--acknowledge-host-mutation", action="store_true")
    render = commands.add_parser("inventory")
    render.add_argument("--role", choices=sorted(COMPOSE_ROLE), required=True)
    render.add_argument("--image", required=True)
    render.add_argument("--release-sha", required=True)
    render.add_argument("--fixture", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare-paths":
            if args.apply:
                if not args.acknowledge_host_mutation:
                    raise Stage3Error("prepare_requires_host_mutation_ack")
                findings = prepare_path_contract(args.root, args.role)
            else:
                findings = inspect_path_contract(args.root, args.role)
            status = "pass" if all(item["status"] == "ok" for item in findings) else "fail"
            print(json_text({"status": status, "role": args.role, "findings": findings}))
            return 0 if status == "pass" else 1
        if args.command == "inventory":
            release_sha = args.release_sha.lower()
            if not RELEASE_SHA.fullmatch(release_sha):
                raise Stage3Error("release_sha_invalid")
            if args.fixture:
                os.environ["MARKET_PIPELINE_MODE"] = "fixture"
            document = render_compose(args.role)
            metadata = image_metadata(args.image, release_sha, fixture=args.fixture)
            print(json_text(inventory(document, role=args.role, image=metadata)))
            return 0
    except (OSError, ValueError, json.JSONDecodeError, Stage3Error) as exc:
        print(json_text({"status": "fail", "reason_code": str(exc)}), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
