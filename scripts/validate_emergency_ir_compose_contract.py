#!/usr/bin/env python3
"""Verify the immutable JSON Compose source for Emergency IR Standalone.

The Emergency Compose files are intentionally canonical JSON rather than
YAML.  This removes YAML aliases, merge keys, duplicate-key ambiguity, and
parser-dependent coercions from the activation trust boundary.  The validator
accepts only an exact canonical byte representation whose SHA-256 is pinned
below.  That digest covers every nested service, environment value, volume,
network, port, capability, and profile field; it is therefore a deep-exact
contract rather than a collection of permissive substring checks.

This module is read-only.  It never invokes Docker or Compose and never
emits source bytes or runtime settings.  It is suitable for a sealed package
preflight once the caller has already independently bound this script to the
package RELEASE manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence


SCHEMA = "gold-trade-emergency-ir-compose-contract-v1"
BASE_SHA256 = "6e903485d47fa0e40e6e79de3d46342157b019ad072a53ef29c9a69b1669e78f"
SMS_SHA256 = "2c44926852f4e437c599527e6b1318c8670ae6b60d468fcfaa6ddab2afb378ce"
MAX_COMPOSE_BYTES = 4 * 1024 * 1024
_PROFILE_VALUES = frozenset({"telegram-only", "sms-otp"})


class EmergencyComposeContractError(RuntimeError):
    """The immutable Emergency Compose source is not safe to use."""


def _fail(message: str) -> None:
    raise EmergencyComposeContractError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("Compose JSON contains a duplicate object key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    _fail("Compose JSON constants are unsupported")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise EmergencyComposeContractError("Compose JSON cannot be canonicalized") from exc


def _absolute(path: Path | str, *, label: str) -> Path:
    raw = str(path)
    candidate = Path(raw)
    if not raw or "\x00" in raw or not candidate.is_absolute() or raw.startswith("//") or raw != os.path.normpath(raw):
        _fail(f"{label} path is invalid")
    return candidate


def _safe_directory_chain(path: Path, *, label: str) -> None:
    path = _absolute(path, label=label)
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        try:
            item = current.lstat()
        except OSError as exc:
            raise EmergencyComposeContractError(f"{label} parent cannot be inspected") from exc
        mode = stat.S_IMODE(item.st_mode)
        sticky_tmp = (
            current == Path("/tmp")
            and stat.S_ISDIR(item.st_mode)
            and item.st_uid == 0
            and bool(mode & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(item.st_mode)
            or not stat.S_ISDIR(item.st_mode)
            or item.st_uid != 0
            or ((mode & 0o022) and not sticky_tmp)
        ):
            _fail(f"{label} parent is not root-controlled")


def _read_canonical_compose(path: Path, *, label: str) -> tuple[dict[str, Any], str, int]:
    path = _absolute(path, label=label)
    _safe_directory_chain(path.parent, label=label)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("Compose contract validation requires O_NOFOLLOW")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow)
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or mode & 0o022
            or not 1 <= before.st_size <= MAX_COMPOSE_BYTES
        ):
            _fail(f"{label} must be one bounded root-controlled regular file")
        payload = bytearray()
        while len(payload) <= MAX_COMPOSE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_COMPOSE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        identity = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            len(payload) != before.st_size
            or len(payload) > MAX_COMPOSE_BYTES
            or any(getattr(before, field) != getattr(after, field) for field in identity)
        ):
            _fail(f"{label} changed while being read")
    except OSError as exc:
        raise EmergencyComposeContractError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        value = json.loads(
            bytes(payload).decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except EmergencyComposeContractError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyComposeContractError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        _fail(f"{label} must be one JSON object")
    canonical = _canonical_json(value)
    if canonical != bytes(payload):
        _fail(f"{label} is not canonical JSON")
    return value, hashlib.sha256(canonical).hexdigest(), len(canonical)


def _require_exact_contract(path: Path, *, label: str, expected_sha256: str) -> tuple[str, int]:
    value, digest, size = _read_canonical_compose(path, label=label)
    # Parsing and re-canonicalizing first means the fixed digest is a typed,
    # recursively exact Compose contract, not a hash over parser-dependent
    # YAML source text.  The object check also rejects a top-level scalar.
    if not value or digest != expected_sha256:
        _fail(f"{label} differs from its immutable deep-exact contract")
    return digest, size


def validate_contract(*, base: Path, profile: str, sms: Path | None = None) -> dict[str, object]:
    """Read only canonical JSON files and return non-secret identity evidence."""

    if profile not in _PROFILE_VALUES:
        _fail("Emergency Compose profile is invalid")
    base_sha256, base_bytes = _require_exact_contract(
        base,
        label="Emergency standalone Compose source",
        expected_sha256=BASE_SHA256,
    )
    result: dict[str, object] = {
        "schema": SCHEMA,
        "status": "verified-local-only",
        "profile": profile,
        "base_sha256": base_sha256,
        "base_bytes": base_bytes,
        "docker_or_service_changed": False,
        "network_action": False,
    }
    if profile == "sms-otp":
        if sms is None:
            _fail("SMS OTP Compose contract requires its sealed overlay")
        sms_sha256, sms_bytes = _require_exact_contract(
            sms,
            label="Emergency SMS OTP Compose source",
            expected_sha256=SMS_SHA256,
        )
        result.update({"sms_sha256": sms_sha256, "sms_bytes": sms_bytes})
    elif sms is not None:
        _fail("telegram-only Compose contract refuses an SMS OTP overlay")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--profile", choices=tuple(sorted(_PROFILE_VALUES)), default="telegram-only")
    parser.add_argument("--sms", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
            _fail("Compose contract validator must be launched with python3 -I -B")
        arguments = _parser().parse_args(argv)
        print(json.dumps(validate_contract(base=arguments.base, profile=arguments.profile, sms=arguments.sms), sort_keys=True))
        return 0
    except EmergencyComposeContractError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
