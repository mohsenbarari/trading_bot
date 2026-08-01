#!/usr/bin/env python3
"""Render, but never apply, the FI-side preflight receipt-agent assets.

The renderer reads one fixed root-only canonical installation request and can
write a fresh staging tree containing the exact ``sshd``, ``sudoers``,
authorized-key, and root-collector policy files.  It does not create an
account, alter an SSH daemon, copy files into ``/etc``, open a network
connection, or run a collector.  Applying a reviewed staged tree is a later
root-only change window, outside this renderer and this turn.
"""

from __future__ import annotations

import argparse
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


DEFAULT_INSTALLATION_REQUEST_PATH = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/receipt-agent-install-request.json"
)
DEFAULT_RENDER_ROOT = Path(
    "/var/lib/trading-bot/dedicated-host-preflight/receipt-agent-rendered"
)
MAX_INSTALLATION_REQUEST_BYTES = 16 * 1024


class ReceiptAgentRendererError(RuntimeError):
    """A root-only render input/output failure with no path diagnostics."""


def _fail(code: str) -> None:
    raise ReceiptAgentRendererError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_INVALID")


def _require_root() -> None:
    try:
        root = os.geteuid() == 0
    except OSError:
        root = False
    if not root:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_ROOT_REQUIRED")


def _require_root_controlled_ancestors(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_PATH_UNSAFE")
    current = Path("/")
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = os.lstat(current)
        except OSError:
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_PATH_UNSAFE")
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_PATH_UNSAFE")


def _read_root_only_file(path: Path, *, maximum_bytes: int) -> bytes:
    _require_root()
    _require_root_controlled_ancestors(path.parent)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_UNSAFE")
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_UNSAFE")
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
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_UNSAFE")
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
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_UNSAFE")
        return bytes(payload)
    except ReceiptAgentRendererError:
        raise
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_UNSAFE")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def load_root_owned_installation_config(
    path: Path = DEFAULT_INSTALLATION_REQUEST_PATH,
) -> boundary.ReceiptAgentInstallationConfig:
    """Read the only accepted installation request file; no path flag exists."""

    raw = _read_root_only_file(path, maximum_bytes=MAX_INSTALLATION_REQUEST_BYTES)
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ReceiptAgentRendererError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_INVALID")
    if type(value) is not dict or raw != boundary.canonical_json_document(
        value, code="PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_INVALID"
    ) + b"\n":
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_INVALID")
    try:
        return boundary.parse_receipt_agent_installation_config(value)
    except boundary.ReceiptAgentBoundaryError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INPUT_INVALID")


def _fresh_root(path: Path) -> None:
    _require_root()
    _require_root_controlled_ancestors(path.parent)
    try:
        metadata = os.lstat(path)
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_ROOT_UNSAFE")
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_ROOT_UNSAFE")
    try:
        if any(path.iterdir()):
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_ROOT_UNSAFE")
    except ReceiptAgentRendererError:
        raise
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_ROOT_UNSAFE")


def _safe_relative(destination: Path) -> Path:
    if not destination.is_absolute() or ".." in destination.parts:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_OUTPUT_INVALID")
    try:
        relative = destination.relative_to("/")
    except ValueError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_OUTPUT_INVALID")
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_OUTPUT_INVALID")
    return relative


def _write_new_file(root: Path, relative: Path, *, content: bytes, mode: int) -> None:
    parent = root
    for component in relative.parts[:-1]:
        parent = parent / component
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_WRITE_FAILED")
        try:
            metadata = os.lstat(parent)
        except OSError:
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_WRITE_FAILED")
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_WRITE_FAILED")
    target = root / relative
    descriptor = -1
    try:
        descriptor = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            mode,
        )
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_WRITE_FAILED")
            offset += written
        os.fchmod(descriptor, mode)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != mode
            or opened.st_size != len(content)
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_WRITE_FAILED")
        os.fsync(descriptor)
    except ReceiptAgentRendererError:
        raise
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def materialize_fresh_render(
    rendered: boundary.RenderedReceiptAgentAssets,
    *,
    root: Path = DEFAULT_RENDER_ROOT,
) -> None:
    """Write a review staging tree, never the live ``/etc`` destinations."""

    if type(rendered) is not boundary.RenderedReceiptAgentAssets:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_OUTPUT_INVALID")
    _fresh_root(root)
    for item in rendered.files:
        if (
            type(item) is not boundary.RenderedReceiptAgentFile
            or item.mode not in {0o440, 0o600, 0o644, 0o755}
            or not 1 <= len(item.content) <= 64 * 1024
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_OUTPUT_INVALID")
        _write_new_file(
            root,
            _safe_relative(item.destination),
            content=item.content,
            mode=item.mode,
        )
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_WRITE_FAILED")
    try:
        os.fsync(descriptor)
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_WRITE_FAILED")
    finally:
        os.close(descriptor)


def _summary(rendered: boundary.RenderedReceiptAgentAssets) -> bytes:
    return boundary.canonical_json_document(
        {
            "schema": boundary.DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_SCHEMA,
            "status": "rendered-not-applied",
            "enabled": rendered.config.enabled,
            "site_role": rendered.config.site_role,
            "agent_release_sha": rendered.config.agent_release_sha,
            "installation_sha256": rendered.installation_sha256,
            "file_count": len(rendered.files),
            "host_change_applied": False,
            "execution_authorized": False,
            "promotion_authorized": False,
        },
        code="PREFLIGHT_RECEIPT_AGENT_RENDER_OUTPUT_INVALID",
    ) + b"\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--render",
        action="store_true",
        help="write only the fixed fresh review staging tree; never apply it",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rendered = boundary.render_receipt_agent_assets(
            load_root_owned_installation_config()
        )
        if args.render:
            materialize_fresh_render(rendered)
        output = _summary(rendered)
    except (ReceiptAgentRendererError, boundary.ReceiptAgentBoundaryError, OSError):
        return 2
    sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
