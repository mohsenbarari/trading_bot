#!/usr/bin/env python3
"""Load the fixed controller inputs and emit one non-authorizing outcome.

This local executable defaults to disabled adapters and therefore emits only a
bounded ``blocked`` document.  The optional root-owned runtime boundary is
reachable only through an explicit positive CLI switch *and* one fixed,
canonical, root-owned mode-0600 configuration file.  It still refuses to start
any remote observation unless the fixed central Witness-evidence verifier
runtime can be assembled in-process first.  The CLI has no
caller-selectable host, command, URL, credential, proxy, locator, or FI-to-IR
route.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Sequence


sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from core.dedicated_host_preflight_controller import (  # noqa: E402
    CONTROLLER_RESULT_SCHEMA,
    DisabledAgentDelivery,
    DisabledProviderReadback,
    observe_preflight_controller,
    validate_controller_config,
)
from core import dedicated_host_preflight_runtime_transport as runtime_transport  # noqa: E402
from core.dedicated_host_preflight_receipt import canonical_json_bytes  # noqa: E402
from scripts.dedicated_host_preflight_manifest import parse_manifest_payload  # noqa: E402


__all__ = (
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_RUNTIME_TRANSPORT_CONFIG_PATH",
    "load_root_only_canonical_json",
    "main",
)


DEFAULT_CONFIG_PATH = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/controller-config.json"
)
DEFAULT_MANIFEST_PATH = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/manifest.json"
)
DEFAULT_RUNTIME_TRANSPORT_CONFIG_PATH = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/runtime-transport.json"
)
MAX_CONFIG_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_RUNTIME_TRANSPORT_CONFIG_BYTES = 8 * 1024


class DedicatedHostPreflightControllerCliError(RuntimeError):
    """The fixed local controller inputs cannot be opened safely."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DedicatedHostPreflightControllerCliError(
                "controller input contains duplicate JSON fields"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise DedicatedHostPreflightControllerCliError(
        f"controller input contains unsupported JSON constant: {value}"
    )


def _require_root() -> None:
    if os.geteuid() != 0:
        raise DedicatedHostPreflightControllerCliError(
            "dedicated-host preflight controller must run as root"
        )


def _require_absolute(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise DedicatedHostPreflightControllerCliError(
            f"{label} path must be canonical and absolute"
        )
    return path


def _require_root_controlled_ancestors(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise DedicatedHostPreflightControllerCliError(
                f"{label} ancestor cannot be inspected"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise DedicatedHostPreflightControllerCliError(
                f"{label} ancestor is unsafe"
            )


def _read_root_only_file(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    path = _require_absolute(Path(path), label=label)
    _require_root_controlled_ancestors(path.parent, label=label)
    try:
        before = path.lstat()
    except OSError as exc:
        raise DedicatedHostPreflightControllerCliError(
            f"{label} cannot be inspected"
        ) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= maximum_bytes
    ):
        raise DedicatedHostPreflightControllerCliError(
            f"{label} is not a bounded root-only mode-0600 regular file"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_gid,
            opened.st_nlink,
        )
        expected_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_nlink,
        )
        if (
            identity != expected_identity
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise DedicatedHostPreflightControllerCliError(
                f"{label} changed while being opened"
            )
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(4096, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) > maximum_bytes
            or len(payload) != opened.st_size
            or (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mode,
                after.st_uid,
                after.st_gid,
                after.st_nlink,
            )
            != identity
        ):
            raise DedicatedHostPreflightControllerCliError(
                f"{label} changed while being read"
            )
        return bytes(payload)
    except DedicatedHostPreflightControllerCliError:
        raise
    except OSError as exc:
        raise DedicatedHostPreflightControllerCliError(
            f"{label} cannot be read"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_root_only_canonical_json(
    path: Path, *, label: str, maximum_bytes: int
) -> dict[str, Any]:
    """Read exactly one private canonical JSON object without side effects."""

    raw = _read_root_only_file(path, label=label, maximum_bytes=maximum_bytes)
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DedicatedHostPreflightControllerCliError(
            f"{label} is not canonical ASCII JSON"
        ) from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value) + b"\n":
        raise DedicatedHostPreflightControllerCliError(
            f"{label} is not canonical JSON"
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    # Paths are private root-only input locations, not a way to override any
    # host, command, URL, route, or credential.  The options are retained for
    # controlled recovery/testing of another owner-controlled file location.
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--enable-root-owned-transports",
        action="store_true",
        help=(
            "require the fixed root-owned runtime transport configuration; "
            "the independently fixed central Witness evidence verifier "
            "policy and source-pinned Witness target must also pass "
            "before any remote observation"
        ),
    )
    return parser


async def _observe_from_files(
    config_path: Path,
    manifest_path: Path,
    *,
    enable_root_owned_transports: bool = False,
) -> dict[str, Any]:
    _require_root()
    config = load_root_only_canonical_json(
        config_path, label="controller config", maximum_bytes=MAX_CONFIG_BYTES
    )
    # Validate before the disabled adapters are even constructed.  This keeps
    # a malformed config from reaching a future transport boundary.
    checked_controller = validate_controller_config(config)
    manifest_raw = _read_root_only_file(
        manifest_path, label="preflight manifest", maximum_bytes=MAX_MANIFEST_BYTES
    )
    manifest = parse_manifest_payload(manifest_raw)
    if enable_root_owned_transports:
        # The runtime config path is deliberately not a CLI argument.  It may
        # not choose a host, URL, command, credential, proxy, or route; it
        # merely records the four fixed read-only contracts and explicit opt-in.
        runtime_config = runtime_transport.parse_root_owned_dedicated_host_preflight_runtime_transport_config(
            load_root_only_canonical_json(
                DEFAULT_RUNTIME_TRANSPORT_CONFIG_PATH,
                label="runtime transport config",
                maximum_bytes=MAX_RUNTIME_TRANSPORT_CONFIG_BYTES,
            )
        )
        adapters = runtime_transport.assemble_root_owned_dedicated_host_preflight_runtime_adapters(
            config=runtime_config,
            witness_target=next(
                target
                for target in checked_controller.targets
                if target.role == "witness"
            ),
        )
        provider_readback = adapters.provider_readback
        agent_delivery = adapters.agent_delivery
    else:
        provider_readback = DisabledProviderReadback()
        agent_delivery = DisabledAgentDelivery()
    return await observe_preflight_controller(
        config=config,
        manifest=manifest,
        provider_readback=provider_readback,
        agent_delivery=agent_delivery,
    )


def _blocked() -> dict[str, str]:
    return {
        "schema": CONTROLLER_RESULT_SCHEMA,
        "status": "blocked",
        "observation_mode": "read-only",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(
            _observe_from_files(
                args.config,
                args.manifest,
                enable_root_owned_transports=args.enable_root_owned_transports,
            )
        )
    except (DedicatedHostPreflightControllerCliError, ValueError, OSError):
        result = _blocked()
    sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
    return 0 if result.get("status") == "observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
