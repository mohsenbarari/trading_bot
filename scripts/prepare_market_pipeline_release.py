#!/usr/bin/env python3
"""Render and verify release-bound Market Pipeline environment pairs.

This tool is deliberately non-operational: it never connects to a host, starts a
container, creates a data directory, or reads a secret value.  It turns two
root-owned topology files into role-specific Compose env files bound to one Git
release and one content-addressed Docker image, then writes a secret-free
receipt.  Host path/secret existence and Compose rendering remain the job of
``manage_market_pipeline_stage3.py preflight`` on each target host.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Mapping, Sequence


RELEASE_SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
PORT = re.compile(r"^[1-9][0-9]{0,4}$")
SAFE_ENV_VALUE = re.compile(r"^[A-Za-z0-9_./:@+,-]*$")

DYNAMIC_VALUES = {
    "MARKET_PIPELINE_IMAGE",
    "MARKET_PIPELINE_RELEASE_SHA",
    "MARKET_PIPELINE_MODE",
    "MARKET_PIPELINE_PROJECT_NAME",
    "MARKET_PIPELINE_FEED_MODE",
    "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY",
    "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE",
}
COMMON_REQUIRED = {
    "MARKET_PRIVATE_BIND_IP",
    "MARKET_WEB_PRIVATE_IP",
    "MARKET_BOT_PRIVATE_IP",
    "MARKET_TRANSPORT_CA_FILE",
    "MARKET_HMAC_ACTIVE_FILE",
    "MARKET_HMAC_PREVIOUS_FILE",
}
ROLE_REQUIRED = {
    "web": {
        "MARKET_WEB_DATA_ROOT",
        "MARKET_POSTGRES_PASSWORD_FILE",
        "MARKET_CAPTURE_ACCOUNT1_CONFIG_FILE",
        "MARKET_CAPTURE_ACCOUNT2_CONFIG_FILE",
        "MARKET_CAPTURE_ACCOUNT2_HMAC_FILE",
        "MARKET_WEB_TRANSPORT_CERT_FILE",
        "MARKET_WEB_TRANSPORT_KEY_FILE",
    },
    "bot": {
        "MARKET_BOT_DATA_ROOT",
        "MARKET_BOT_TRANSPORT_CERT_FILE",
        "MARKET_BOT_TRANSPORT_KEY_FILE",
    },
}
SECRET_PATH_KEYS = {
    "web": {
        "MARKET_POSTGRES_PASSWORD_FILE",
        "MARKET_CAPTURE_ACCOUNT1_CONFIG_FILE",
        "MARKET_CAPTURE_ACCOUNT2_CONFIG_FILE",
        "MARKET_CAPTURE_ACCOUNT2_HMAC_FILE",
        "MARKET_TRANSPORT_CA_FILE",
        "MARKET_WEB_TRANSPORT_CERT_FILE",
        "MARKET_WEB_TRANSPORT_KEY_FILE",
        "MARKET_HMAC_ACTIVE_FILE",
        "MARKET_HMAC_PREVIOUS_FILE",
    },
    "bot": {
        "MARKET_TRANSPORT_CA_FILE",
        "MARKET_BOT_TRANSPORT_CERT_FILE",
        "MARKET_BOT_TRANSPORT_KEY_FILE",
        "MARKET_HMAC_ACTIVE_FILE",
        "MARKET_HMAC_PREVIOUS_FILE",
    },
}
TOPOLOGY_KEYS = {
    "MARKET_WEB_PRIVATE_IP",
    "MARKET_BOT_PRIVATE_IP",
    "MARKET_WEB_SNAPSHOT_RECEIVER_PORT",
    "MARKET_BOT_FACT_RECEIVER_PORT",
}


class ReleaseContractError(RuntimeError):
    """A stable, non-sensitive release-contract failure."""


@dataclass(frozen=True)
class RenderedRole:
    role: str
    source_sha256: str
    output_sha256: str
    data_root: str
    bind_ip: str
    peer_ip: str
    receiver_port: int
    secret_path_keys: tuple[str, ...]


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _validate_secure_input(path: Path) -> None:
    try:
        info = path.lstat()
        parent = path.parent.lstat()
    except OSError as exc:
        raise ReleaseContractError("source_env_unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ReleaseContractError("source_env_regular_file_required")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ReleaseContractError("source_env_owner_mode_invalid")
    if path.parent.is_symlink() or not stat.S_ISDIR(parent.st_mode):
        raise ReleaseContractError("source_env_parent_invalid")
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ReleaseContractError("source_env_parent_owner_mode_invalid")
    if info.st_size <= 0 or info.st_size > 64 * 1024:
        raise ReleaseContractError("source_env_size_invalid")


def parse_env(path: Path, *, secure_input: bool) -> dict[str, str]:
    if secure_input:
        _validate_secure_input(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseContractError("env_read_failed") from exc
    values: dict[str, str] = {}
    for number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export ") or "=" not in stripped:
            raise ReleaseContractError(f"env_syntax_invalid_line_{number}")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not ENV_KEY.fullmatch(key) or key in values:
            raise ReleaseContractError(f"env_key_invalid_line_{number}")
        # No shell interpolation or quoting is accepted.  This makes the same
        # bytes safe for both Python validation and Docker Compose --env-file.
        if value != value.strip() or any(marker in value for marker in ("\n", "\r", "\x00")):
            raise ReleaseContractError(f"env_value_invalid_line_{number}")
        if value.startswith(("'", '"')) or value.endswith(("'", '"')):
            raise ReleaseContractError(f"env_quoting_forbidden_line_{number}")
        if "$" in value or "`" in value:
            raise ReleaseContractError(f"env_expansion_forbidden_line_{number}")
        if not SAFE_ENV_VALUE.fullmatch(value):
            raise ReleaseContractError(f"env_shell_metacharacter_forbidden_line_{number}")
        values[key] = value
    return values


def _private_ip(value: str, *, field: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ReleaseContractError(f"{field}_invalid") from exc
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_unspecified
        or address.is_multicast
    ):
        raise ReleaseContractError(f"{field}_must_be_provider_private_ipv4")
    return str(address)


def _absolute_host_path(value: str, *, field: str) -> str:
    path = Path(value)
    if not value or not path.is_absolute() or ".." in path.parts:
        raise ReleaseContractError(f"{field}_must_be_absolute")
    normalized = Path(os.path.normpath(value))
    if normalized in {Path("/"), Path("/root"), Path("/srv"), Path("/tmp"), Path("/var/tmp")}:
        raise ReleaseContractError(f"{field}_too_broad")
    if normalized == Path("/tmp") or Path("/tmp") in normalized.parents:
        raise ReleaseContractError(f"{field}_tmp_forbidden")
    return str(normalized)


def _port(values: Mapping[str, str], key: str) -> int:
    raw = values.get(key, "9443")
    if not PORT.fullmatch(raw) or not 1 <= int(raw) <= 65535:
        raise ReleaseContractError(f"{key.lower()}_invalid")
    return int(raw)


def validate_source(role: str, values: Mapping[str, str]) -> dict[str, object]:
    forbidden = sorted(DYNAMIC_VALUES.intersection(values))
    if forbidden:
        raise ReleaseContractError("source_env_contains_release_controlled_keys")
    missing = sorted((COMMON_REQUIRED | ROLE_REQUIRED[role]).difference(values))
    if missing:
        raise ReleaseContractError("source_env_required_keys_missing")
    for key, value in values.items():
        upper = key.upper()
        if any(token in upper for token in ("PASSWORD", "TOKEN", "SECRET")) and not upper.endswith("_FILE"):
            raise ReleaseContractError("plaintext_secret_key_forbidden")
        if any(character.isspace() for character in value):
            raise ReleaseContractError("env_value_whitespace_forbidden")

    web_ip = _private_ip(values["MARKET_WEB_PRIVATE_IP"], field="market_web_private_ip")
    bot_ip = _private_ip(values["MARKET_BOT_PRIVATE_IP"], field="market_bot_private_ip")
    bind_ip = _private_ip(values["MARKET_PRIVATE_BIND_IP"], field="market_private_bind_ip")
    if web_ip == bot_ip:
        raise ReleaseContractError("market_private_peer_ips_must_differ")
    expected_bind = web_ip if role == "web" else bot_ip
    if bind_ip != expected_bind:
        raise ReleaseContractError("market_private_bind_ip_role_mismatch")

    for key in SECRET_PATH_KEYS[role]:
        _absolute_host_path(values[key], field=key.lower())
    data_key = "MARKET_WEB_DATA_ROOT" if role == "web" else "MARKET_BOT_DATA_ROOT"
    data_root = _absolute_host_path(values[data_key], field=data_key.lower())
    receiver_key = (
        "MARKET_WEB_SNAPSHOT_RECEIVER_PORT"
        if role == "web"
        else "MARKET_BOT_FACT_RECEIVER_PORT"
    )
    return {
        "data_root": data_root,
        "bind_ip": bind_ip,
        "peer_ip": bot_ip if role == "web" else web_ip,
        "receiver_port": _port(values, receiver_key),
    }


def _validate_pair(web: Mapping[str, str], bot: Mapping[str, str]) -> None:
    for key in TOPOLOGY_KEYS:
        web_value = web.get(key, "9443" if key.endswith("_PORT") else "")
        bot_value = bot.get(key, "9443" if key.endswith("_PORT") else "")
        if web_value != bot_value:
            raise ReleaseContractError("cross_role_topology_mismatch")


def _write_atomic(path: Path, payload: bytes, *, exclusive: bool) -> None:
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError as exc:
        raise ReleaseContractError("output_parent_unavailable") from exc
    if parent.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise ReleaseContractError("output_parent_invalid")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ReleaseContractError("output_parent_owner_mode_invalid")
    if path.exists() or path.is_symlink():
        if exclusive:
            raise ReleaseContractError("output_already_exists")
        existing = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(existing.st_mode):
            raise ReleaseContractError("output_existing_file_invalid")
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _render_values(
    source: Mapping[str, str], *, release_sha: str, image_id: str, project_name: str
) -> dict[str, str]:
    rendered = dict(source)
    rendered.update(
        {
            "MARKET_PIPELINE_PROJECT_NAME": project_name,
            "MARKET_PIPELINE_IMAGE": image_id,
            "MARKET_PIPELINE_RELEASE_SHA": release_sha,
            "MARKET_PIPELINE_MODE": "live",
            "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
            "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
            "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
        }
    )
    return rendered


def _encode_env(values: Mapping[str, str]) -> bytes:
    return ("".join(f"{key}={values[key]}\n" for key in sorted(values))).encode("utf-8")


def _render_role(
    role: str,
    source_path: Path,
    output_path: Path,
    *,
    release_sha: str,
    image_id: str,
    project_name: str,
) -> RenderedRole:
    source = parse_env(source_path, secure_input=True)
    summary = validate_source(role, source)
    payload = _encode_env(
        _render_values(
            source,
            release_sha=release_sha,
            image_id=image_id,
            project_name=project_name,
        )
    )
    _write_atomic(output_path, payload, exclusive=True)
    return RenderedRole(
        role=role,
        source_sha256=_digest(source_path),
        output_sha256=sha256(payload).hexdigest(),
        data_root=str(summary["data_root"]),
        bind_ip=str(summary["bind_ip"]),
        peer_ip=str(summary["peer_ip"]),
        receiver_port=int(summary["receiver_port"]),
        secret_path_keys=tuple(sorted(SECRET_PATH_KEYS[role])),
    )


def render_pair(
    *,
    web_source: Path,
    bot_source: Path,
    web_output: Path,
    bot_output: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    project_name: str,
) -> dict[str, object]:
    if not RELEASE_SHA.fullmatch(release_sha):
        raise ReleaseContractError("release_sha_invalid")
    if not RELEASE_SHA.fullmatch(release_tree):
        raise ReleaseContractError("release_tree_invalid")
    if not IMAGE_ID.fullmatch(image_id):
        raise ReleaseContractError("image_id_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", image_input_signature):
        raise ReleaseContractError("image_input_signature_invalid")
    if not PROJECT_NAME.fullmatch(project_name):
        raise ReleaseContractError("project_name_invalid")
    if len({web_output, bot_output, receipt}) != 3:
        raise ReleaseContractError("output_paths_must_be_distinct")

    web_source_values = parse_env(web_source, secure_input=True)
    bot_source_values = parse_env(bot_source, secure_input=True)
    validate_source("web", web_source_values)
    validate_source("bot", bot_source_values)
    _validate_pair(web_source_values, bot_source_values)

    web = _render_role(
        "web", web_source, web_output,
        release_sha=release_sha, image_id=image_id, project_name=project_name,
    )
    try:
        bot = _render_role(
            "bot", bot_source, bot_output,
            release_sha=release_sha, image_id=image_id, project_name=project_name,
        )
        document: dict[str, object] = {
            "schema": "market_pipeline_release_pair/1.0",
            "release_sha": release_sha,
            "release_tree": release_tree,
            "image_id": image_id,
            "image_input_signature": image_input_signature,
            "project_name": project_name,
            "authority": {
                "feed_mode": "PRIVATE_SHADOW",
                "product_authority_changed": False,
                "private_primary_allowed": False,
                "telegram_capture_cutover_authorized": False,
            },
            "roles": {
                item.role: {
                    "source_sha256": item.source_sha256,
                    "output_sha256": item.output_sha256,
                    "data_root": item.data_root,
                    "bind_ip": item.bind_ip,
                    "peer_ip": item.peer_ip,
                    "receiver_port": item.receiver_port,
                    "secret_path_keys": list(item.secret_path_keys),
                }
                for item in (web, bot)
            },
            "secrets_disclosed": False,
        }
        _write_atomic(
            receipt,
            (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
            exclusive=True,
        )
        return document
    except Exception:
        for output in (web_output, bot_output):
            if output.exists() and not output.is_symlink():
                output.unlink()
        raise


def check_sources(*, web_source: Path, bot_source: Path) -> dict[str, object]:
    web_values = parse_env(web_source, secure_input=True)
    bot_values = parse_env(bot_source, secure_input=True)
    web = validate_source("web", web_values)
    bot = validate_source("bot", bot_values)
    _validate_pair(web_values, bot_values)
    return {
        "schema": "market_pipeline_release_sources/1.0",
        "roles": {
            "web": {
                "source_sha256": _digest(web_source),
                "data_root": web["data_root"],
                "bind_ip": web["bind_ip"],
                "peer_ip": web["peer_ip"],
                "receiver_port": web["receiver_port"],
                "secret_path_keys": sorted(SECRET_PATH_KEYS["web"]),
            },
            "bot": {
                "source_sha256": _digest(bot_source),
                "data_root": bot["data_root"],
                "bind_ip": bot["bind_ip"],
                "peer_ip": bot["peer_ip"],
                "receiver_port": bot["receiver_port"],
                "secret_path_keys": sorted(SECRET_PATH_KEYS["bot"]),
            },
        },
        "secrets_disclosed": False,
    }


def verify_pair(
    *,
    web_source: Path,
    bot_source: Path,
    web_output: Path,
    bot_output: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
) -> dict[str, object]:
    try:
        document = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError("release_pair_receipt_invalid") from exc
    expected_identity = {
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
    }
    if document.get("schema") != "market_pipeline_release_pair/1.0" or any(
        document.get(key) != value for key, value in expected_identity.items()
    ):
        raise ReleaseContractError("release_pair_identity_mismatch")
    authority = document.get("authority")
    if authority != {
        "feed_mode": "PRIVATE_SHADOW",
        "product_authority_changed": False,
        "private_primary_allowed": False,
        "telegram_capture_cutover_authorized": False,
    } or document.get("secrets_disclosed") is not False:
        raise ReleaseContractError("release_pair_authority_invalid")
    roles = document.get("roles")
    if not isinstance(roles, dict) or set(roles) != {"web", "bot"}:
        raise ReleaseContractError("release_pair_roles_invalid")
    for role, output in (("web", web_output), ("bot", bot_output)):
        source = web_source if role == "web" else bot_source
        values = parse_env(output, secure_input=False)
        if values.get("MARKET_PIPELINE_RELEASE_SHA") != release_sha:
            raise ReleaseContractError("rendered_release_sha_mismatch")
        if values.get("MARKET_PIPELINE_IMAGE") != image_id:
            raise ReleaseContractError("rendered_image_id_mismatch")
        if values.get("MARKET_PIPELINE_MODE") != "live":
            raise ReleaseContractError("rendered_mode_mismatch")
        if values.get("MARKET_PIPELINE_FEED_MODE") != "PRIVATE_SHADOW":
            raise ReleaseContractError("rendered_feed_mode_mismatch")
        if values.get("MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY") != "0":
            raise ReleaseContractError("rendered_primary_gate_mismatch")
        if values.get("MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE") != "PRIVATE_SHADOW":
            raise ReleaseContractError("rendered_snapshot_lane_mismatch")
        validate_source(role, {key: value for key, value in values.items() if key not in DYNAMIC_VALUES})
        role_receipt = roles.get(role)
        if (
            not isinstance(role_receipt, dict)
            or role_receipt.get("output_sha256") != _digest(output)
            or role_receipt.get("source_sha256") != _digest(source)
        ):
            raise ReleaseContractError("rendered_env_digest_mismatch")
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sources = commands.add_parser("check-sources")
    sources.add_argument("--web-source", type=Path, required=True)
    sources.add_argument("--bot-source", type=Path, required=True)
    for name in ("render-pair", "verify-pair"):
        command = commands.add_parser(name)
        command.add_argument("--web-env", type=Path, required=True)
        command.add_argument("--bot-env", type=Path, required=True)
        command.add_argument("--receipt", type=Path, required=True)
        command.add_argument("--release-sha", required=True)
        command.add_argument("--release-tree", required=True)
        command.add_argument("--image-id", required=True)
        command.add_argument("--image-input-signature", required=True)
        if name == "render-pair":
            command.add_argument("--web-source", type=Path, required=True)
            command.add_argument("--bot-source", type=Path, required=True)
            command.add_argument(
                "--project-name", default="market-private-pipeline-production"
            )
        else:
            command.add_argument("--web-source", type=Path, required=True)
            command.add_argument("--bot-source", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check-sources":
            document = check_sources(
                web_source=args.web_source, bot_source=args.bot_source
            )
            print(
                json.dumps(
                    {
                        "status": "pass",
                        "schema": document["schema"],
                        "roles": sorted(document["roles"]),
                        "secrets_disclosed": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        common = {
            "web_output": args.web_env,
            "bot_output": args.bot_env,
            "receipt": args.receipt,
            "release_sha": args.release_sha,
            "release_tree": args.release_tree,
            "image_id": args.image_id,
            "image_input_signature": args.image_input_signature,
        }
        if args.command == "render-pair":
            document = render_pair(
                web_source=args.web_source,
                bot_source=args.bot_source,
                project_name=args.project_name,
                **common,
            )
        else:
            document = verify_pair(
                web_source=args.web_source,
                bot_source=args.bot_source,
                **common,
            )
        print(
            json.dumps(
                {
                    "status": "pass",
                    "schema": document["schema"],
                    "release_sha": document["release_sha"],
                    "image_id": document["image_id"],
                    "feed_mode": document["authority"]["feed_mode"],
                    "telegram_capture_cutover_authorized": False,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, ReleaseContractError) as exc:
        print(
            json.dumps(
                {"status": "fail", "reason_code": str(exc), "secrets_disclosed": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
