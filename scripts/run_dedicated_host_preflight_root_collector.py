#!/usr/bin/env python3
"""Root-only, role-bound collector behind the preflight sudoers boundary.

This program is not an SSH server command.  The rendered sudoers file permits
only this exact no-argument Python invocation from the unprivileged
``preflight`` account.  It reads a fixed root-only runtime config, accepts one
bounded canonical request from stdin, binds its role to the local host role,
and invokes the existing fixed read-only collector in-process.

There is no WA-IR branch.  A request for WA-IR, a disabled config, an unsafe
path, an argument, a role mismatch, or any collection error returns a nonzero
status with no stdout diagnostic.  Only a canonical redacted receipt is ever
written to stdout on success.
"""

from __future__ import annotations

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

from core import dedicated_host_preflight_receipt_agent_boundary as boundary  # noqa: E402
from core.dedicated_host_preflight_receipt import (  # noqa: E402
    MAX_RECEIPT_BYTES,
    parse_preflight_receipt,
)


MAX_RUNTIME_CONFIG_BYTES = 8 * 1024
MAX_REQUEST_BYTES = 4 * 1024


class RootCollectorError(RuntimeError):
    """Redacted root collector refusal; no error text is sent to SSH stdout."""


def _fail() -> None:
    raise RootCollectorError("root collector rejected")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail()
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail()


def _require_root() -> None:
    try:
        if os.getuid() != 0 or os.geteuid() != 0:
            _fail()
    except OSError:
        _fail()


def _require_root_controlled_ancestors(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        _fail()
    current = Path("/")
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = os.lstat(current)
        except OSError:
            _fail()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            _fail()


def _safe_root_only_read(path: Path, *, maximum_bytes: int) -> bytes:
    _require_root_controlled_ancestors(path.parent)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail()
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail()
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail()
    descriptor = -1
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        if (
            (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mode,
                opened.st_uid,
                opened.st_nlink,
            )
            != identity
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            _fail()
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
                after.st_nlink,
            )
            != identity
        ):
            _fail()
        return bytes(payload)
    except RootCollectorError:
        raise
    except OSError:
        _fail()
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _load_runtime_config() -> boundary.ReceiptAgentRuntimeConfig:
    raw = _safe_root_only_read(
        boundary.FIXED_PREFLIGHT_ROOT_COLLECTOR_CONFIG,
        maximum_bytes=MAX_RUNTIME_CONFIG_BYTES,
    )
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except RootCollectorError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail()
    if type(parsed) is not dict or raw != boundary.canonical_json_document(
        parsed, code="PREFLIGHT_RECEIPT_AGENT_RUNTIME_CONFIG_INVALID"
    ) + b"\n":
        _fail()
    try:
        return boundary.parse_receipt_agent_runtime_config(parsed)
    except boundary.ReceiptAgentBoundaryError:
        _fail()


def _read_bounded_stdin() -> bytes:
    payload = bytearray()
    while len(payload) <= MAX_REQUEST_BYTES:
        try:
            chunk = os.read(0, min(4096, MAX_REQUEST_BYTES + 1 - len(payload)))
        except OSError:
            _fail()
        if not chunk:
            break
        payload.extend(chunk)
    if not 1 <= len(payload) <= MAX_REQUEST_BYTES:
        _fail()
    return bytes(payload)


def _require_root_controlled_source_layout() -> tuple[str, Path]:
    source_script = Path(__file__)
    try:
        resolved_script = source_script.resolve(strict=True)
    except OSError:
        _fail()
    if resolved_script != source_script:
        _fail()
    root = source_script.parents[1]
    try:
        _dispatcher, collector, readonly_collector = boundary.agent_source_paths(root.name)
    except boundary.ReceiptAgentBoundaryError:
        _fail()
    if (
        root.parent != boundary.FIXED_PREFLIGHT_AGENT_RELEASES_ROOT
        or collector != source_script
    ):
        _fail()
    _require_root_controlled_ancestors(root)
    for source in (source_script, readonly_collector):
        _require_root_controlled_ancestors(source.parent)
        try:
            metadata = os.lstat(source)
        except OSError:
            _fail()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_size < 1
        ):
            _fail()
    return root.name, readonly_collector


def _clean_process_environment() -> None:
    # The source collector itself gives every fixed child probe its own clean
    # environment.  Clearing the sudo/SSH environment here prevents future
    # Python-level code from accidentally consulting caller-controlled values.
    os.environ.clear()
    os.environ.update(
        {
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        }
    )


def _collect(
    *,
    runtime: boundary.ReceiptAgentRuntimeConfig,
    request_bytes: bytes,
    readonly_collector: Path,
) -> bytes:
    try:
        request = boundary.parse_receipt_agent_request_payload(request_bytes)
    except boundary.ReceiptAgentBoundaryError:
        _fail()
    if runtime.enabled is not True or request.role != runtime.site_role:
        _fail()
    # The file was checked above at its fixed source-owned path.  Importing it
    # in-process preserves the existing collector's fixed, no-shell probes and
    # avoids introducing a second arbitrary process/argument boundary.
    if readonly_collector != SOURCE_ROOT / "scripts" / "run_dedicated_host_readonly_preflight.py":
        _fail()
    try:
        from scripts import run_dedicated_host_readonly_preflight as collector

        selection = collector.parse_request_payload(request_bytes)
        if selection["role"] != runtime.site_role:
            _fail()
        receipt = collector._collect_normalized_receipt(selection)
        raw = collector.canonical_json_bytes(receipt) + b"\n"
        parse_preflight_receipt(
            raw,
            expected_role=request.role,
            expected_campaign_id=request.campaign_id,
            expected_operation_id=request.operation_id,
            expected_manifest_sha256=request.manifest_sha256,
        )
    except RootCollectorError:
        raise
    except Exception:
        _fail()
    if not 1 <= len(raw) <= MAX_RECEIPT_BYTES:
        _fail()
    return raw


def main(argv: Sequence[str] | None = None) -> int:
    """Perform only the exact local collector handoff; never print errors."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    try:
        if arguments:
            _fail()
        _require_root()
        release_sha, readonly_collector = _require_root_controlled_source_layout()
        runtime = _load_runtime_config()
        if runtime.agent_release_sha != release_sha:
            _fail()
        _clean_process_environment()
        raw = _collect(
            runtime=runtime,
            request_bytes=_read_bounded_stdin(),
            readonly_collector=readonly_collector,
        )
    except (RootCollectorError, boundary.ReceiptAgentBoundaryError):
        return 2
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
