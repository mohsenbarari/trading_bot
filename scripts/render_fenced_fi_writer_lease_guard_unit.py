#!/usr/bin/env python3
"""Render the fixed WebApp-FI fenced writer guard systemd unit.

The source configuration is the root-owned, non-secret fenced preflight
configuration.  It supplies only closed paths and reviewed runtime identity;
the unit intentionally never loads the runtime EnvironmentFile because that
file can hold application credentials.  Rendering is pure by default.  The
explicit ``--install`` mode atomically writes only the one approved unit path
and deliberately does not run ``systemctl daemon-reload`` or start a service.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Sequence
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import preflight_fenced_fi_writer as preflight


class FencedFiWriterUnitRenderError(RuntimeError):
    """The pinned unit cannot be rendered or installed safely."""


def render(config_path: Path) -> tuple[preflight.FencedFiWriterPreflightConfig, bytes]:
    """Load the closed-path preflight config and render its exact unit bytes."""

    try:
        config = preflight._load_config(config_path)
        rendered = preflight._render_expected_unit(config)
    except preflight.FencedFiWriterPreflightError as exc:
        raise FencedFiWriterUnitRenderError(str(exc)) from exc
    return config, rendered


def _secure_unit_directory(path: Path) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise FencedFiWriterUnitRenderError("cannot securely open systemd unit directory") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        os.close(descriptor)
        raise FencedFiWriterUnitRenderError("systemd unit directory is not owner controlled")
    return descriptor


def _validate_existing_unit(directory_fd: int, name: str, *, replace_existing: bool) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not replace_existing:
        raise FencedFiWriterUnitRenderError(
            "approved fenced FI unit already exists; --replace-existing is required"
        )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_nlink != 1
    ):
        raise FencedFiWriterUnitRenderError(
            "existing fenced FI unit is not an owner-controlled regular file"
        )


def install(
    config_path: Path,
    *,
    replace_existing: bool,
) -> dict[str, str]:
    """Atomically write one rendered unit; no daemon reload or service action."""

    config, rendered = render(config_path)
    if config.unit_file != preflight.APPROVED_FENCED_UNIT_FILE:
        # Kept as a defense in depth check in case the preflight parser is
        # refactored separately from this installer.
        raise FencedFiWriterUnitRenderError("unit target is not the approved fenced FI guard")
    directory_fd = _secure_unit_directory(config.unit_file.parent)
    temporary_name = f".{config.unit_file.name}.{os.getpid()}.{uuid4().hex}.tmp"
    descriptor = -1
    try:
        _validate_existing_unit(
            directory_fd,
            config.unit_file.name,
            replace_existing=replace_existing,
        )
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o644,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(rendered):
            written = os.write(descriptor, rendered[offset:])
            if written <= 0:
                raise FencedFiWriterUnitRenderError("systemd unit write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            config.unit_file.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    return {
        "status": "rendered",
        "unit_file": str(config.unit_file),
        "service": preflight.FENCED_UNIT_NAME,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--check",
        action="store_true",
        help="validate the config and template without writing a unit",
    )
    actions.add_argument(
        "--install",
        action="store_true",
        help="atomically install the one approved unit path; no systemctl action",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="permit --install to replace an owner-controlled existing approved unit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.replace_existing and not args.install:
        print(json.dumps({"status": "blocked", "reason": "--replace-existing requires --install"}))
        return 2
    try:
        if args.install:
            result = install(args.config, replace_existing=args.replace_existing)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        else:
            config, rendered = render(args.config)
            if args.check:
                print(
                    json.dumps(
                        {
                            "status": "ready",
                            "service": preflight.FENCED_UNIT_NAME,
                            "unit_file": str(config.unit_file),
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            else:
                sys.stdout.buffer.write(rendered)
    except FencedFiWriterUnitRenderError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_class": type(exc).__name__,
                    "reason": str(exc),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
