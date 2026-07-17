#!/usr/bin/env python3
"""Attest one Writer Witness activation's dynamic runtime provenance.

The provenance file is installation-specific root-owned evidence.  It is
accepted only when its complete runtime object exactly equals a fresh result
from ``verify_writer_witness_runtime.py`` and all release-bound digests match
the values supplied by the caller.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Iterable, Mapping, Sequence


MAXIMUM_JSON_BYTES = 1024 * 1024
PROVENANCE_SCHEMA_VERSION = "writer_witness_runtime_provenance_v2"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
PYTHON_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z",
    re.ASCII,
)
TRUSTED_SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
PROVENANCE_FIELDS = frozenset(
    {
        "release_manifest_sha256",
        "requirements_lock_sha256",
        "runtime",
        "schema_version",
        "system_runtime_manifest_sha256",
        "wheelhouse_manifest_sha256",
    }
)
RUNTIME_FIELDS = frozenset(
    {
        "bootstrap_extra_count",
        "implementation",
        "installed_file_count",
        "installed_files_sha256",
        "installed_package_count",
        "lock_package_count",
        "python_sha256",
        "python_version",
        "requirements_lock_sha256",
        "runtime_attested",
        "runtime_sha256",
        "runtime_structure_count",
        "runtime_structure_sha256",
        "runtime_tree_entry_count",
        "runtime_tree_sha256",
        "system_elf_closure_sha256",
        "system_elf_object_count",
        "system_os_release_sha256",
        "system_package_count",
        "system_package_set_sha256",
        "system_runtime_attested",
        "system_runtime_manifest_sha256",
        "system_runtime_sha256",
        "system_stdlib_entry_count",
        "system_stdlib_tree_sha256",
        "venv_elf_closure_sha256",
        "venv_elf_object_count",
    }
)


class RuntimeProvenanceAttestationError(RuntimeError):
    """The activation's runtime provenance cannot be proven trustworthy."""


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
        raise RuntimeProvenanceAttestationError(
            "runtime provenance verifier requires -I -S -B -X utf8 "
            "-X pycache_prefix=/dev/null"
        )
    environment = dict(os.environ)
    if environment.get("PATH") != TRUSTED_SYSTEM_PATH:
        raise RuntimeProvenanceAttestationError(
            "runtime provenance verifier PATH is not the trusted clean value"
        )
    allowed = {"PATH": TRUSTED_SYSTEM_PATH}
    if environment.get("LC_CTYPE") == "C.UTF-8":
        allowed["LC_CTYPE"] = "C.UTF-8"
    if environment != allowed:
        raise RuntimeProvenanceAttestationError(
            "runtime provenance verifier did not start with a clean environment"
        )


def _metadata_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _non_negative_identifier(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("owner id must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("owner id must be a non-negative integer")
    return parsed


def _sha256_argument(value: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "SHA-256 must be 64 lowercase hexadecimal characters"
        )
    return value


def _python_version_argument(value: str) -> str:
    if not PYTHON_VERSION_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("version must be an exact X.Y.Z value")
    return value


def _unique_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeProvenanceAttestationError(
                "runtime provenance JSON contains a duplicate object key"
            )
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise RuntimeProvenanceAttestationError(
        "runtime provenance JSON contains a non-finite number"
    )


def _parse_json_object(value: bytes | str, *, label: str) -> Mapping[str, object]:
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RuntimeProvenanceAttestationError(
                f"{label} is not valid UTF-8"
            ) from exc
    else:
        encoded = value
    if not encoded:
        raise RuntimeProvenanceAttestationError(f"{label} is empty")
    if len(encoded) > MAXIMUM_JSON_BYTES:
        raise RuntimeProvenanceAttestationError(f"{label} exceeds its safe size limit")
    try:
        decoded = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeProvenanceAttestationError(f"{label} is not valid UTF-8") from exc
    if decoded.startswith("\ufeff"):
        raise RuntimeProvenanceAttestationError(f"{label} must not contain a UTF-8 BOM")
    try:
        document = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite,
        )
    except RuntimeProvenanceAttestationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise RuntimeProvenanceAttestationError(f"{label} is not valid bounded JSON") from exc
    if not isinstance(document, dict):
        raise RuntimeProvenanceAttestationError(f"{label} root must be an object")
    return document


def _read_secure_provenance(
    path: Path, *, expected_uid: int, expected_gid: int
) -> tuple[bytes, str]:
    if (
        isinstance(expected_uid, bool)
        or not isinstance(expected_uid, int)
        or expected_uid < 0
        or isinstance(expected_gid, bool)
        or not isinstance(expected_gid, int)
        or expected_gid < 0
    ):
        raise RuntimeProvenanceAttestationError(
            "expected provenance owner ids must be non-negative"
        )
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise RuntimeProvenanceAttestationError(
            "secure runtime provenance open flags are unavailable"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeProvenanceAttestationError(
            "cannot safely open runtime provenance"
        ) from exc
    try:
        try:
            before = os.fstat(descriptor)
        except OSError as exc:
            raise RuntimeProvenanceAttestationError(
                "cannot safely inspect runtime provenance"
            ) from exc
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeProvenanceAttestationError(
                "runtime provenance is not a regular file"
            )
        if before.st_nlink != 1:
            raise RuntimeProvenanceAttestationError(
                "runtime provenance must have exactly one hard link"
            )
        if before.st_uid != expected_uid or before.st_gid != expected_gid:
            raise RuntimeProvenanceAttestationError(
                "runtime provenance has an unexpected owner"
            )
        if stat.S_IMODE(before.st_mode) != 0o644:
            raise RuntimeProvenanceAttestationError(
                "runtime provenance mode must be exactly 0644"
            )
        if before.st_size <= 0:
            raise RuntimeProvenanceAttestationError("runtime provenance is empty")
        if before.st_size > MAXIMUM_JSON_BYTES:
            raise RuntimeProvenanceAttestationError(
                "runtime provenance exceeds its safe size limit"
            )

        chunks: list[bytes] = []
        remaining = MAXIMUM_JSON_BYTES + 1
        try:
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b"".join(chunks)
            after = os.fstat(descriptor)
        except OSError as exc:
            raise RuntimeProvenanceAttestationError(
                "cannot safely read runtime provenance"
            ) from exc
        if len(value) > MAXIMUM_JSON_BYTES:
            raise RuntimeProvenanceAttestationError(
                "runtime provenance exceeds its safe size limit"
            )
        if len(value) != before.st_size:
            raise RuntimeProvenanceAttestationError(
                "runtime provenance length changed during attestation"
            )
        if _metadata_signature(before) != _metadata_signature(after):
            raise RuntimeProvenanceAttestationError(
                "runtime provenance changed during attestation"
            )
        return value, hashlib.sha256(value).hexdigest()
    finally:
        os.close(descriptor)


def _require_exact_fields(
    document: Mapping[str, object], expected: frozenset[str], *, label: str
) -> None:
    observed = set(document)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise RuntimeProvenanceAttestationError(
            f"{label} schema fields differ ({'; '.join(details)})"
        )


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise RuntimeProvenanceAttestationError(
            f"{field} must be 64 lowercase hexadecimal characters"
        )
    return value


def _require_positive_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeProvenanceAttestationError(f"{field} must be a positive integer")
    return value


def _validate_runtime(
    document: Mapping[str, object],
    *,
    expected_requirements_lock_sha256: str,
    expected_python_version: str,
    expected_python_sha256: str,
    expected_system_runtime_manifest_sha256: str,
) -> None:
    _require_exact_fields(document, RUNTIME_FIELDS, label="runtime attestation")
    if document["runtime_attested"] != "yes":
        raise RuntimeProvenanceAttestationError("runtime_attested must equal yes")
    if document["implementation"] != "CPython":
        raise RuntimeProvenanceAttestationError("runtime implementation must be CPython")
    if document["system_runtime_attested"] != "yes":
        raise RuntimeProvenanceAttestationError("system_runtime_attested must equal yes")
    bootstrap_extra_count = document["bootstrap_extra_count"]
    if (
        isinstance(bootstrap_extra_count, bool)
        or not isinstance(bootstrap_extra_count, int)
        or bootstrap_extra_count != 0
    ):
        raise RuntimeProvenanceAttestationError("bootstrap_extra_count must equal zero")
    if document["python_version"] != expected_python_version:
        raise RuntimeProvenanceAttestationError(
            "runtime Python version differs from its release binding"
        )
    if (
        _require_sha256(
            document["system_runtime_manifest_sha256"],
            field="system_runtime_manifest_sha256",
        )
        != expected_system_runtime_manifest_sha256
    ):
        raise RuntimeProvenanceAttestationError(
            "system runtime manifest SHA-256 differs from its release binding"
        )
    if _require_sha256(document["python_sha256"], field="python_sha256") != expected_python_sha256:
        raise RuntimeProvenanceAttestationError(
            "runtime Python SHA-256 differs from its release binding"
        )
    if (
        _require_sha256(
            document["requirements_lock_sha256"], field="requirements_lock_sha256"
        )
        != expected_requirements_lock_sha256
    ):
        raise RuntimeProvenanceAttestationError(
            "runtime requirements SHA-256 differs from its release binding"
        )
    _require_sha256(document["runtime_sha256"], field="runtime_sha256")
    _require_sha256(
        document["installed_files_sha256"], field="installed_files_sha256"
    )
    lock_packages = _require_positive_integer(
        document["lock_package_count"], field="lock_package_count"
    )
    installed_packages = _require_positive_integer(
        document["installed_package_count"], field="installed_package_count"
    )
    installed_files = _require_positive_integer(
        document["installed_file_count"], field="installed_file_count"
    )
    runtime_structure_count = _require_positive_integer(
        document["runtime_structure_count"], field="runtime_structure_count"
    )
    runtime_tree_entry_count = _require_positive_integer(
        document["runtime_tree_entry_count"], field="runtime_tree_entry_count"
    )
    _require_sha256(
        document["runtime_structure_sha256"], field="runtime_structure_sha256"
    )
    _require_sha256(document["runtime_tree_sha256"], field="runtime_tree_sha256")
    for field in (
        "system_elf_closure_sha256",
        "system_os_release_sha256",
        "system_package_set_sha256",
        "system_runtime_sha256",
        "system_stdlib_tree_sha256",
        "venv_elf_closure_sha256",
    ):
        _require_sha256(document[field], field=field)
    _require_positive_integer(
        document["system_elf_object_count"], field="system_elf_object_count"
    )
    _require_positive_integer(
        document["system_package_count"], field="system_package_count"
    )
    _require_positive_integer(
        document["system_stdlib_entry_count"], field="system_stdlib_entry_count"
    )
    _require_positive_integer(
        document["venv_elf_object_count"], field="venv_elf_object_count"
    )
    if lock_packages != installed_packages:
        raise RuntimeProvenanceAttestationError(
            "lock and installed package counts must be equal without bootstrap extras"
        )
    if installed_files < installed_packages:
        raise RuntimeProvenanceAttestationError(
            "installed file count cannot be smaller than installed package count"
        )
    if runtime_tree_entry_count < installed_files + runtime_structure_count:
        raise RuntimeProvenanceAttestationError(
            "runtime tree entry count is smaller than its claimed closed inventory"
        )


def attest_runtime_provenance(
    provenance_path: Path,
    runtime_attestation_json: str,
    *,
    expected_release_manifest_sha256: str,
    expected_wheelhouse_manifest_sha256: str,
    expected_requirements_lock_sha256: str,
    expected_python_version: str,
    expected_python_sha256: str,
    expected_system_runtime_manifest_sha256: str,
    expected_uid: int = 0,
    expected_gid: int = 0,
    require_clean_startup: bool = False,
) -> dict[str, object]:
    if require_clean_startup:
        _require_clean_startup()
    for value, field in (
        (expected_release_manifest_sha256, "expected release manifest SHA-256"),
        (expected_wheelhouse_manifest_sha256, "expected wheelhouse manifest SHA-256"),
        (expected_requirements_lock_sha256, "expected requirements lock SHA-256"),
        (expected_python_sha256, "expected Python SHA-256"),
        (
            expected_system_runtime_manifest_sha256,
            "expected system runtime manifest SHA-256",
        ),
    ):
        _require_sha256(value, field=field)
    if not isinstance(expected_python_version, str) or not PYTHON_VERSION_PATTERN.fullmatch(
        expected_python_version
    ):
        raise RuntimeProvenanceAttestationError(
            "expected Python version must be an exact X.Y.Z value"
        )

    provenance_raw, provenance_sha256 = _read_secure_provenance(
        provenance_path, expected_uid=expected_uid, expected_gid=expected_gid
    )
    provenance = _parse_json_object(provenance_raw, label="runtime provenance")
    fresh_runtime = _parse_json_object(
        runtime_attestation_json, label="fresh runtime attestation"
    )
    _require_exact_fields(provenance, PROVENANCE_FIELDS, label="runtime provenance")
    if provenance["schema_version"] != PROVENANCE_SCHEMA_VERSION:
        raise RuntimeProvenanceAttestationError(
            "runtime provenance schema version is unsupported"
        )
    for key, expected in (
        ("release_manifest_sha256", expected_release_manifest_sha256),
        ("wheelhouse_manifest_sha256", expected_wheelhouse_manifest_sha256),
        ("requirements_lock_sha256", expected_requirements_lock_sha256),
        (
            "system_runtime_manifest_sha256",
            expected_system_runtime_manifest_sha256,
        ),
    ):
        if _require_sha256(provenance[key], field=key) != expected:
            raise RuntimeProvenanceAttestationError(
                f"{key} differs from its release binding"
            )
    stored_runtime = provenance["runtime"]
    if not isinstance(stored_runtime, dict):
        raise RuntimeProvenanceAttestationError(
            "runtime provenance runtime field must be an object"
        )
    _validate_runtime(
        stored_runtime,
        expected_requirements_lock_sha256=expected_requirements_lock_sha256,
        expected_python_version=expected_python_version,
        expected_python_sha256=expected_python_sha256,
        expected_system_runtime_manifest_sha256=(
            expected_system_runtime_manifest_sha256
        ),
    )
    _validate_runtime(
        fresh_runtime,
        expected_requirements_lock_sha256=expected_requirements_lock_sha256,
        expected_python_version=expected_python_version,
        expected_python_sha256=expected_python_sha256,
        expected_system_runtime_manifest_sha256=(
            expected_system_runtime_manifest_sha256
        ),
    )
    if stored_runtime != fresh_runtime:
        raise RuntimeProvenanceAttestationError(
            "stored runtime provenance differs from fresh runtime attestation"
        )

    return {
        "installed_file_count": fresh_runtime["installed_file_count"],
        "installed_files_sha256": fresh_runtime["installed_files_sha256"],
        "installed_package_count": fresh_runtime["installed_package_count"],
        "provenance_sha256": provenance_sha256,
        "release_manifest_sha256": expected_release_manifest_sha256,
        "requirements_lock_sha256": expected_requirements_lock_sha256,
        "runtime_provenance_attested": "yes",
        "runtime_sha256": fresh_runtime["runtime_sha256"],
        "runtime_structure_sha256": fresh_runtime["runtime_structure_sha256"],
        "runtime_tree_sha256": fresh_runtime["runtime_tree_sha256"],
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "system_runtime_manifest_sha256": expected_system_runtime_manifest_sha256,
        "system_runtime_sha256": fresh_runtime["system_runtime_sha256"],
        "system_stdlib_tree_sha256": fresh_runtime["system_stdlib_tree_sha256"],
        "system_elf_closure_sha256": fresh_runtime["system_elf_closure_sha256"],
        "system_package_set_sha256": fresh_runtime["system_package_set_sha256"],
        "venv_elf_closure_sha256": fresh_runtime["venv_elf_closure_sha256"],
        "venv_elf_object_count": fresh_runtime["venv_elf_object_count"],
        "wheelhouse_manifest_sha256": expected_wheelhouse_manifest_sha256,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--runtime-attestation-json", required=True)
    parser.add_argument(
        "--expected-release-manifest-sha256", type=_sha256_argument, required=True
    )
    parser.add_argument(
        "--expected-wheelhouse-manifest-sha256", type=_sha256_argument, required=True
    )
    parser.add_argument(
        "--expected-requirements-lock-sha256", type=_sha256_argument, required=True
    )
    parser.add_argument(
        "--expected-python-version", type=_python_version_argument, required=True
    )
    parser.add_argument(
        "--expected-python-sha256", type=_sha256_argument, required=True
    )
    parser.add_argument(
        "--expected-system-runtime-manifest-sha256",
        type=_sha256_argument,
        required=True,
    )
    parser.add_argument("--expected-uid", type=_non_negative_identifier, default=0)
    parser.add_argument("--expected-gid", type=_non_negative_identifier, default=0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = attest_runtime_provenance(
            args.provenance,
            args.runtime_attestation_json,
            expected_release_manifest_sha256=args.expected_release_manifest_sha256,
            expected_wheelhouse_manifest_sha256=args.expected_wheelhouse_manifest_sha256,
            expected_requirements_lock_sha256=args.expected_requirements_lock_sha256,
            expected_python_version=args.expected_python_version,
            expected_python_sha256=args.expected_python_sha256,
            expected_system_runtime_manifest_sha256=(
                args.expected_system_runtime_manifest_sha256
            ),
            expected_uid=args.expected_uid,
            expected_gid=args.expected_gid,
            require_clean_startup=True,
        )
    except RuntimeProvenanceAttestationError as exc:
        print(f"Writer Witness runtime provenance attestation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
