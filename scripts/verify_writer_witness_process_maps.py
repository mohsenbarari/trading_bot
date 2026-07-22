#!/usr/bin/env python3
"""Fail closed unless a live Writer Witness maps only attested native objects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class ProcessMapsError(RuntimeError):
    """The live process is outside the closed native-object trust boundary."""


def _require_clean_startup() -> None:
    if not (
        sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.ignore_environment
        and sys.flags.dont_write_bytecode
        and getattr(sys.flags, "safe_path", False)
        and sys.flags.utf8_mode == 1
        and sys.pycache_prefix == "/dev/null"
    ):
        raise ProcessMapsError(
            "process-maps verifier requires -I -S -B -X utf8 "
            "-X pycache_prefix=/dev/null"
        )
    allowed = {"PATH": TRUSTED_PATH}
    if os.environ.get("LC_CTYPE") == "C.UTF-8":
        allowed["LC_CTYPE"] = "C.UTF-8"
    if dict(os.environ) != allowed:
        raise ProcessMapsError("process-maps verifier did not start in a clean environment")


def _read_regular(path: Path, maximum: int) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1 or before.st_size > maximum:
            raise ProcessMapsError(f"unsafe process-maps input: {path}")
        payload = b""
        while len(payload) < before.st_size:
            chunk = os.read(descriptor, before.st_size - len(payload))
            if not chunk:
                raise ProcessMapsError(f"short process-maps input: {path}")
            payload += chunk
        after = os.fstat(descriptor)
        identity = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_uid,
            item.st_gid,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise ProcessMapsError(f"process-maps input changed during read: {path}")
        return payload
    finally:
        os.close(descriptor)


def _read_proc(path: Path, maximum: int) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > maximum:
                raise ProcessMapsError(f"process metadata exceeds its bound: {path}")
        if not payload:
            raise ProcessMapsError(f"process metadata is empty: {path}")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _is_elf(path: Path) -> bool:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except (FileNotFoundError, OSError):
        return False
    try:
        metadata = os.fstat(descriptor)
        return stat.S_ISREG(metadata.st_mode) and os.read(descriptor, 4) == b"\x7fELF"
    finally:
        os.close(descriptor)


def _system_elf_paths(manifest: Path, expected_sha256: str) -> dict[Path, str]:
    payload = _read_regular(manifest, 4 * 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ProcessMapsError("system runtime manifest differs from its release pin")
    document = json.loads(payload)
    objects = document.get("elf_objects") if isinstance(document, dict) else None
    if not isinstance(objects, list) or not objects:
        raise ProcessMapsError("system runtime manifest has no ELF closure")
    paths: dict[Path, str] = {}
    for item in objects:
        raw = item.get("path") if isinstance(item, dict) else None
        expected_object_sha256 = item.get("sha256") if isinstance(item, dict) else None
        if (
            not isinstance(raw, str)
            or not raw.startswith("/")
            or not isinstance(expected_object_sha256, str)
            or SHA256_RE.fullmatch(expected_object_sha256) is None
        ):
            raise ProcessMapsError("system runtime manifest contains an unsafe ELF path")
        resolved = Path(raw).resolve(strict=True)
        if resolved in paths and paths[resolved] != expected_object_sha256:
            raise ProcessMapsError("system runtime manifest contains conflicting ELF paths")
        paths[resolved] = expected_object_sha256
    return paths


def _stable_identity_and_sha256(path: Path) -> tuple[tuple[int, int], str]:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1:
            raise ProcessMapsError(f"mapped native object is unsafe: {path}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise ProcessMapsError(f"mapped native object changed during read: {path}")
        return (before.st_dev, before.st_ino), digest.hexdigest()
    finally:
        os.close(descriptor)


def _venv_elf_paths(venv: Path) -> dict[Path, str]:
    if venv.is_symlink() or not venv.is_dir() or venv.resolve(strict=True) != venv:
        raise ProcessMapsError("Writer Witness venv must be one canonical real directory")
    paths: dict[Path, str] = {}
    for path in venv.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if _is_elf(path):
            resolved = path.resolve(strict=True)
            _, digest = _stable_identity_and_sha256(resolved)
            paths[resolved] = digest
    return paths


def _process_start_time(stat_payload: bytes) -> bytes:
    closing = stat_payload.rfind(b")")
    if closing < 1:
        raise ProcessMapsError("process stat metadata is malformed")
    fields = stat_payload[closing + 2 :].split()
    if len(fields) < 20:
        raise ProcessMapsError("process stat metadata is incomplete")
    return fields[19]


def attest_process_maps(
    *, pid: int, venv: Path, system_runtime_manifest: Path, expected_manifest_sha256: str
) -> dict[str, object]:
    if pid < 2:
        raise ProcessMapsError("process pid is invalid")
    process_root = Path("/proc") / str(pid)
    before_start = _process_start_time(_read_proc(process_root / "stat", 64 * 1024))
    executable = (process_root / "exe").resolve(strict=True)
    allowed = _system_elf_paths(system_runtime_manifest, expected_manifest_sha256)
    venv_allowed = _venv_elf_paths(venv)
    for path, digest in venv_allowed.items():
        if path in allowed and allowed[path] != digest:
            raise ProcessMapsError("venv ELF path conflicts with the system runtime")
        allowed[path] = digest
    if executable not in allowed:
        raise ProcessMapsError("live process executable is outside the attested closure")
    mapped_native: set[Path] = set()
    maps_payload = _read_proc(process_root / "maps", 16 * 1024 * 1024)
    maps = maps_payload.decode("utf-8")
    for line in maps.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 6:
            continue
        address_range, permissions, _offset, device_text, inode_text, raw = fields
        if raw.startswith("["):
            continue
        if "(deleted)" in raw or "\\" in raw:
            raise ProcessMapsError(f"live process has an unresolvable mapped object: {raw}")
        if not raw.startswith("/"):
            continue
        if "x" not in permissions:
            continue
        path = Path(raw)
        resolved = path.resolve(strict=True)
        mapped_native.add(resolved)
        if resolved not in allowed:
            raise ProcessMapsError(f"live process maps an unattested native object: {resolved}")
        try:
            device_major_text, device_minor_text = device_text.split(":", 1)
            mapped_device = os.makedev(
                int(device_major_text, 16), int(device_minor_text, 16)
            )
            mapped_inode = int(inode_text, 10)
        except (TypeError, ValueError) as exc:
            raise ProcessMapsError(f"live process map identity is malformed: {line}") from exc
        (current_device, current_inode), observed_digest = _stable_identity_and_sha256(
            resolved
        )
        if (mapped_device, mapped_inode) != (current_device, current_inode):
            raise ProcessMapsError(
                f"live process map inode differs from the attested path: {resolved}"
            )
        if observed_digest != allowed[resolved]:
            raise ProcessMapsError(
                f"live process maps changed native bytes: {resolved}"
            )
        # Ensure the address-range field is canonical enough that a malformed
        # proc line cannot be accepted while identity checks are in progress.
        if re.fullmatch(r"[0-9a-f]+-[0-9a-f]+", address_range) is None:
            raise ProcessMapsError("live process map address range is malformed")
    after_maps = _read_proc(process_root / "maps", 16 * 1024 * 1024)
    after_start = _process_start_time(_read_proc(process_root / "stat", 64 * 1024))
    if maps_payload != after_maps or before_start != after_start:
        raise ProcessMapsError("live process identity or native maps changed during attestation")
    if executable not in mapped_native or len(mapped_native) < 2:
        raise ProcessMapsError("live process native-object map is incomplete")
    return {
        "mapped_native_object_count": len(mapped_native),
        "process_maps_attested": "yes",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--system-runtime-manifest", type=Path, required=True)
    parser.add_argument("--expected-system-runtime-manifest-sha256", required=True)
    args = parser.parse_args()
    if SHA256_RE.fullmatch(args.expected_system_runtime_manifest_sha256) is None:
        raise ProcessMapsError("expected system runtime manifest SHA-256 is invalid")
    _require_clean_startup()
    print(
        json.dumps(
            attest_process_maps(
                pid=args.pid,
                venv=args.venv,
                system_runtime_manifest=args.system_runtime_manifest,
                expected_manifest_sha256=args.expected_system_runtime_manifest_sha256,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, ProcessMapsError) as exc:
        raise SystemExit(f"Writer Witness process-maps attestation failed: {exc}") from exc
