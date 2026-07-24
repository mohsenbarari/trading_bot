#!/usr/bin/env python3
"""Fail closed unless a deployed Writer Witness release exactly matches its manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Iterable, Sequence


MANIFEST_NAME = "release-manifest.json"
TRUSTED_SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
DIRECTORY_MODE = 0o755
REGULAR_FILE_MODE = 0o644
# This is deliberately a closed, reviewed set.  These are the only release
# files installed with mode 0755 by build_writer_witness_release.sh.  Runtime
# helper scripts under deploy/writer-witness remain inert release data (0644)
# until provisioning installs selected helpers into their final locations.
EXECUTABLE_RELEASE_PATHS = frozenset(
    {
        "scripts/provision_writer_witness_host.sh",
        "scripts/run_writer_witness_clock_jump_probe.py",
        "scripts/smoke_writer_witness_client.py",
        "scripts/verify_writer_witness_nftables.py",
        "scripts/verify_writer_witness_host_toolchain.py",
        "scripts/verify_writer_witness_release.py",
        "scripts/verify_writer_witness_runtime.py",
        "scripts/verify_writer_witness_runtime_provenance.py",
        "scripts/verify_writer_witness_process_maps.py",
        "scripts/verify_writer_witness_wheelhouse.py",
    }
)


class AttestationError(RuntimeError):
    """The release tree cannot be proven to match its bound manifest."""


def _require_isolated_startup() -> None:
    flags = sys.flags
    if (
        not flags.isolated
        or not flags.no_site
        or not flags.dont_write_bytecode
        or flags.utf8_mode != 1
        or sys.pycache_prefix != "/dev/null"
    ):
        raise AttestationError("release verifier requires isolated clean Python startup")
    allowed = {"PATH": TRUSTED_SYSTEM_PATH}
    if os.environ.get("LC_CTYPE") == "C.UTF-8":
        allowed["LC_CTYPE"] = "C.UTF-8"
    if dict(os.environ) != allowed:
        raise AttestationError("release verifier did not start with a clean environment")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Return every security/replacement-sensitive field we require stable."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _assert_owner(
    path: Path,
    metadata: os.stat_result,
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        raise AttestationError(
            f"release entry owner mismatch: {path} "
            f"(expected {expected_uid}:{expected_gid}, "
            f"observed {metadata.st_uid}:{metadata.st_gid})"
        )


def _assert_directory_metadata(
    path: Path,
    metadata: os.stat_result,
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise AttestationError(f"release entry is not a directory: {path}")
    _assert_owner(path, metadata, expected_uid=expected_uid, expected_gid=expected_gid)
    observed_mode = stat.S_IMODE(metadata.st_mode)
    if observed_mode != DIRECTORY_MODE:
        raise AttestationError(
            f"release directory mode mismatch: {path} "
            f"(expected {DIRECTORY_MODE:04o}, observed {observed_mode:04o})"
        )


def _expected_file_mode(relative: str) -> int:
    return 0o755 if relative in EXECUTABLE_RELEASE_PATHS else REGULAR_FILE_MODE


def _assert_file_metadata(
    path: Path,
    metadata: os.stat_result,
    *,
    relative: str,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise AttestationError(f"release entry is not a regular file: {relative}")
    _assert_owner(path, metadata, expected_uid=expected_uid, expected_gid=expected_gid)
    expected_mode = _expected_file_mode(relative)
    observed_mode = stat.S_IMODE(metadata.st_mode)
    if observed_mode != expected_mode:
        raise AttestationError(
            f"release file mode mismatch: {relative} "
            f"(expected {expected_mode:04o}, observed {observed_mode:04o})"
        )
    if metadata.st_nlink != 1:
        raise AttestationError(
            f"release file has an unsafe hard-link count: {relative} "
            f"(expected 1, observed {metadata.st_nlink})"
        )


def _open_regular_file(
    path: Path,
    *,
    relative: str,
    expected_uid: int,
    expected_gid: int,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AttestationError(f"cannot safely open release file: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        _assert_file_metadata(
            path,
            before,
            relative=relative,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, before


def _assert_stable_file(path: Path, before: os.stat_result, after: os.stat_result) -> None:
    if _metadata_identity(before) != _metadata_identity(after):
        raise AttestationError(f"release file changed during attestation: {path.name}")


def _read_regular_file(
    path: Path,
    *,
    relative: str,
    expected_uid: int,
    expected_gid: int,
    maximum_bytes: int | None = None,
) -> bytes:
    descriptor, before = _open_regular_file(
        path,
        relative=relative,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    try:
        if maximum_bytes is not None and before.st_size > maximum_bytes:
            raise AttestationError(f"release file exceeds its safe size limit: {path.name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        _assert_stable_file(path, before, after)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _sha256_regular_file(
    path: Path,
    *,
    relative: str,
    expected_uid: int,
    expected_gid: int,
) -> str:
    descriptor, before = _open_regular_file(
        path,
        relative=relative,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    digest = hashlib.sha256()
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        _assert_stable_file(path, before, os.fstat(descriptor))
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _unique_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AttestationError(f"duplicate release manifest entry: {key}")
        result[key] = value
    return result


def _load_manifest(value: bytes) -> dict[str, str]:
    try:
        decoded = value.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttestationError("release manifest is not valid canonical UTF-8 JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise AttestationError("release manifest must be a non-empty JSON object")
    result: dict[str, str] = {}
    for relative, digest in payload.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise AttestationError("release manifest entries must map paths to SHA-256 strings")
        pure = PurePosixPath(relative)
        if (
            not relative
            or relative == MANIFEST_NAME
            or relative.startswith("/")
            or "\\" in relative
            or pure.as_posix() != relative
            or any(part in ("", ".", "..") for part in pure.parts)
        ):
            raise AttestationError(f"unsafe or non-canonical release manifest path: {relative}")
        if not SHA256_PATTERN.fullmatch(digest):
            raise AttestationError(f"invalid SHA-256 for release manifest entry: {relative}")
        result[relative] = digest
    return result


def _scan_tree(
    root: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> tuple[dict[str, tuple[int, ...]], dict[str, tuple[int, ...]]]:
    files: dict[str, tuple[int, ...]] = {}
    directories: dict[str, tuple[int, ...]] = {}
    try:
        paths = root.rglob("*")
        for path in paths:
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise AttestationError(f"release tree contains a symlink: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                _assert_directory_metadata(
                    path,
                    metadata,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                )
                directories[relative] = _metadata_identity(metadata)
            elif stat.S_ISREG(metadata.st_mode):
                _assert_file_metadata(
                    path,
                    metadata,
                    relative=relative,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                )
                files[relative] = _metadata_identity(metadata)
            else:
                raise AttestationError(f"release tree contains a special file: {relative}")
    except OSError as exc:
        raise AttestationError("cannot safely scan the release tree") from exc
    return files, directories


def _expected_directories(paths: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for relative in paths:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() != ".":
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def attest_release(
    root: Path,
    expected_manifest_sha256: str,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
) -> dict[str, object]:
    if not SHA256_PATTERN.fullmatch(expected_manifest_sha256):
        raise AttestationError("expected manifest SHA-256 must be 64 lowercase hex characters")
    if expected_uid is None:
        expected_uid = os.geteuid()
    if expected_gid is None:
        expected_gid = os.getegid()
    if expected_uid < 0 or expected_gid < 0:
        raise AttestationError("expected release uid and gid must be non-negative integers")
    try:
        resolved_root = root.resolve(strict=True)
        root_metadata = root.lstat()
    except OSError as exc:
        raise AttestationError("release root cannot be safely inspected") from exc
    if root != resolved_root or stat.S_ISLNK(root_metadata.st_mode):
        raise AttestationError("release root must be one canonical real directory")
    _assert_directory_metadata(
        root,
        root_metadata,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    root = resolved_root
    root_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    root_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        root_descriptor = os.open(root, root_flags)
    except OSError as exc:
        raise AttestationError("release root cannot be safely opened") from exc
    try:
        root_before = os.fstat(root_descriptor)
        _assert_directory_metadata(
            root,
            root_before,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        if _metadata_identity(root_metadata) != _metadata_identity(root_before):
            raise AttestationError("release root changed before attestation")
    except Exception:
        os.close(root_descriptor)
        raise

    manifest_path = root / MANIFEST_NAME
    try:
        manifest_bytes = _read_regular_file(
            manifest_path,
            relative=MANIFEST_NAME,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            maximum_bytes=1024 * 1024,
        )
        observed_manifest_sha256 = _sha256_bytes(manifest_bytes)
        if observed_manifest_sha256 != expected_manifest_sha256:
            raise AttestationError("release manifest does not match the expected build manifest")
        manifest = _load_manifest(manifest_bytes)
        missing_executables = sorted(EXECUTABLE_RELEASE_PATHS - set(manifest))
        if missing_executables:
            raise AttestationError(
                "release manifest is missing reviewed executable files: "
                + ", ".join(missing_executables)
            )

        files_before, directories_before = _scan_tree(
            root,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        expected_files = set(manifest) | {MANIFEST_NAME}
        missing = sorted(expected_files - set(files_before))
        extra = sorted(set(files_before) - expected_files)
        if missing:
            raise AttestationError(
                f"release tree is missing manifested files: {', '.join(missing)}"
            )
        if extra:
            raise AttestationError(f"release tree contains unmanifested files: {', '.join(extra)}")
        expected_directories = _expected_directories(expected_files)
        missing_directories = sorted(expected_directories - set(directories_before))
        extra_directories = sorted(set(directories_before) - expected_directories)
        if missing_directories:
            raise AttestationError(
                f"release tree is missing expected directories: {', '.join(missing_directories)}"
            )
        if extra_directories:
            raise AttestationError(
                f"release tree contains unmanifested directories: {', '.join(extra_directories)}"
            )

        for relative, expected_digest in sorted(manifest.items()):
            observed_digest = _sha256_regular_file(
                root / relative,
                relative=relative,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            if observed_digest != expected_digest:
                raise AttestationError(f"release file hash mismatch: {relative}")

        if (
            _read_regular_file(
                manifest_path,
                relative=MANIFEST_NAME,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
                maximum_bytes=1024 * 1024,
            )
            != manifest_bytes
        ):
            raise AttestationError("release manifest changed during attestation")
        files_after, directories_after = _scan_tree(
            root,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        if files_after != files_before or directories_after != directories_before:
            raise AttestationError("release tree metadata changed during attestation")
        root_after = os.fstat(root_descriptor)
        _assert_directory_metadata(
            root,
            root_after,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        try:
            root_path_after = root.lstat()
        except OSError as exc:
            raise AttestationError("release root changed during attestation") from exc
        if (
            _metadata_identity(root_after) != _metadata_identity(root_before)
            or _metadata_identity(root_path_after) != _metadata_identity(root_before)
        ):
            raise AttestationError("release root metadata changed during attestation")
        return {
            "release_manifest_attested": "yes",
            "release_metadata_attested": "yes",
            "release_manifest_entries": len(manifest),
            "release_manifest_sha256": observed_manifest_sha256,
            "release_expected_uid": expected_uid,
            "release_expected_gid": expected_gid,
            "release_executable_entries": len(EXECUTABLE_RELEASE_PATHS),
        }
    finally:
        os.close(root_descriptor)


def _non_negative_integer(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-uid", type=_non_negative_integer, default=os.geteuid())
    parser.add_argument("--expected-gid", type=_non_negative_integer, default=os.getegid())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _require_isolated_startup()
        args = parse_args(argv)
        result = attest_release(
            args.release_root,
            args.expected_manifest_sha256,
            expected_uid=args.expected_uid,
            expected_gid=args.expected_gid,
        )
    except AttestationError as exc:
        print(f"release attestation failed: {exc}", file=sys.stderr)
        return 1
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
