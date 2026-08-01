#!/usr/bin/env python3
"""Read-only verifier for a signed, future Release-0 candidate.

This utility has no Docker, Writer Witness, SSH, Object Storage, DNS, CDN,
service-manager, deployment, image-build, or promotion operation.  It proves
only that a root-owned descriptor and two clean local Git release roots agree
on a new candidate's source, future Compose bytes, images, and term contract.
The resulting status is explicitly non-authorizing.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import release0_immutable_candidate as candidate_contract  # noqa: E402


MAX_CONTROL_FILE_BYTES = 64 * 1024
MAX_SOURCE_FILE_BYTES = 4 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 4 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)


class VerifyRelease0ImmutableCandidateError(RuntimeError):
    """The local evidence does not prove a safe immutable candidate."""


def _secure_read(path: Path, *, label: str, max_size: int) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        raise VerifyRelease0ImmutableCandidateError(
            "Release-0 candidate verification requires O_NOFOLLOW"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | no_follow
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VerifyRelease0ImmutableCandidateError(
            f"cannot securely open {label}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > max_size
        ):
            raise VerifyRelease0ImmutableCandidateError(
                f"{label} is not an owner-controlled regular file"
            )
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > max_size:
            raise VerifyRelease0ImmutableCandidateError(f"{label} is oversized")
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise VerifyRelease0ImmutableCandidateError(f"{label} changed while being read")
        return value
    finally:
        os.close(descriptor)


def _authority_from_file(path: Path) -> candidate_contract.Release0CandidateAuthority:
    raw = _secure_read(
        path,
        label="Release-0 candidate authority",
        max_size=MAX_CONTROL_FILE_BYTES,
    )
    try:
        text = raw.decode("ascii")
        if text.rstrip("\n") != text.strip() or text.count("\n") > 1:
            raise ValueError
        public_key = base64.b64decode(text.strip().encode("ascii"), validate=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise VerifyRelease0ImmutableCandidateError(
            "Release-0 candidate authority is not strict base64"
        ) from exc
    if len(public_key) != 32:
        raise VerifyRelease0ImmutableCandidateError(
            "Release-0 candidate authority has an invalid key length"
        )
    return candidate_contract.Release0CandidateAuthority(
        public_key=public_key,
        key_id="ed25519-sha256:" + hashlib.sha256(public_key).hexdigest(),
    )


def load_verified_release0_immutable_candidate(
    *,
    descriptor_path: Path,
    authority_path: Path,
    expected_candidate_sha256: str,
) -> candidate_contract.Release0ImmutableCandidate:
    """Verify the root-owned descriptor without starting or changing anything."""

    if os.geteuid() != 0:
        raise VerifyRelease0ImmutableCandidateError(
            "Release-0 candidate verification must run as root"
        )
    if (
        type(expected_candidate_sha256) is not str
        or SHA256_RE.fullmatch(expected_candidate_sha256) is None
    ):
        raise VerifyRelease0ImmutableCandidateError(
            "Release-0 candidate expected descriptor hash is invalid"
        )
    authority = _authority_from_file(authority_path)
    document = _secure_read(
        descriptor_path,
        label="Release-0 candidate descriptor",
        max_size=MAX_CONTROL_FILE_BYTES,
    )
    if hashlib.sha256(document).hexdigest() != expected_candidate_sha256:
        raise VerifyRelease0ImmutableCandidateError(
            "Release-0 candidate descriptor does not match the expected hash"
        )
    try:
        return candidate_contract.verify_release0_immutable_candidate(
            document,
            authority=authority,
        )
    except candidate_contract.Release0ImmutableCandidateError as exc:
        raise VerifyRelease0ImmutableCandidateError(
            f"Release-0 candidate descriptor is invalid: {exc.code}"
        ) from exc


def _require_owner_directory(path: Path, *, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise VerifyRelease0ImmutableCandidateError(
            f"cannot inspect {label}"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise VerifyRelease0ImmutableCandidateError(
            f"{label} is not an owner-controlled directory"
        )


def _run_git(root: Path, *arguments: str, label: str, max_size: int) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(root), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=20,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "GIT_OPTIONAL_LOCKS": "0",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerifyRelease0ImmutableCandidateError(f"{label} could not be read") from exc
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise VerifyRelease0ImmutableCandidateError(f"{label} was rejected")
    if len(result.stdout.encode("utf-8")) > max_size:
        raise VerifyRelease0ImmutableCandidateError(f"{label} is oversized")
    return result.stdout


def _validate_clean_git_release(
    root: Path,
    *,
    expected_sha: str,
    expected_tree_sha: str,
    label: str,
) -> None:
    _require_owner_directory(root, label=label)
    commit = _run_git(
        root,
        "rev-parse",
        "--verify",
        "HEAD",
        label=f"{label} Git commit",
        max_size=1024,
    ).strip().lower()
    tree = _run_git(
        root,
        "rev-parse",
        "--verify",
        "HEAD^{tree}",
        label=f"{label} Git tree",
        max_size=1024,
    ).strip().lower()
    if (
        SHA40_RE.fullmatch(commit) is None
        or SHA40_RE.fullmatch(tree) is None
        or commit != expected_sha
        or tree != expected_tree_sha
    ):
        raise VerifyRelease0ImmutableCandidateError(
            f"{label} Git identity does not match the signed candidate"
        )
    dirty = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        label=f"{label} Git worktree",
        max_size=MAX_GIT_OUTPUT_BYTES,
    )
    if dirty:
        raise VerifyRelease0ImmutableCandidateError(
            f"{label} Git worktree is not immutable and clean"
        )


def _relative_file(root: Path, relative: str, *, label: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or str(pure) != relative:
        raise VerifyRelease0ImmutableCandidateError(f"{label} relative path is invalid")
    return root.joinpath(*pure.parts)


def _validate_file_hash(
    root: Path,
    *,
    relative: str,
    expected_sha256: str,
    label: str,
    max_size: int = MAX_SOURCE_FILE_BYTES,
) -> None:
    path = _relative_file(root, relative, label=label)
    value = _secure_read(path, label=label, max_size=max_size)
    if hashlib.sha256(value).hexdigest() != expected_sha256:
        raise VerifyRelease0ImmutableCandidateError(
            f"{label} does not match the signed candidate"
        )


def verify_local_release0_immutable_candidate(
    candidate: candidate_contract.Release0ImmutableCandidate,
) -> dict[str, object]:
    """Read the exact local release roots and return non-authorizing evidence."""

    verified = candidate_contract.require_verified_release0_immutable_candidate(candidate)
    application_root = Path(verified.application.release_root)
    control_root = Path(verified.control.release_root)
    _validate_clean_git_release(
        application_root,
        expected_sha=verified.application.release_sha,
        expected_tree_sha=verified.application.tree_sha,
        label="Release-0 application release",
    )
    _validate_clean_git_release(
        control_root,
        expected_sha=verified.control.release_sha,
        expected_tree_sha=verified.control.tree_sha,
        label="Release-0 control release",
    )
    for relative, expected_sha256 in verified.critical_source_files:
        _validate_file_hash(
            application_root,
            relative=relative,
            expected_sha256=expected_sha256,
            label=f"Release-0 critical source {relative}",
        )
    _validate_file_hash(
        control_root,
        relative="deploy/production/docker-compose.webapp-fi-writer-release0.yml",
        expected_sha256=verified.fi_writer_compose_sha256,
        label="Release-0 FI writer Compose",
    )
    _validate_file_hash(
        control_root,
        relative="deploy/production/docker-compose.webapp-ir-promoted-release0.yml",
        expected_sha256=verified.ir_promoted_compose_sha256,
        label="Release-0 WA-IR promoted Compose",
    )
    return {
        "status": "verified-local-non-authorizing",
        "candidate_id": verified.candidate_id,
        "identity_sha256": verified.identity_sha256,
        "application_release_sha": verified.application.release_sha,
        "control_release_sha": verified.control.release_sha,
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
    }


def verify(
    *,
    descriptor_path: Path,
    authority_path: Path,
    expected_candidate_sha256: str,
    check_local_roots: bool,
) -> dict[str, object]:
    """Verify a descriptor, optionally including its immutable local roots."""

    candidate = load_verified_release0_immutable_candidate(
        descriptor_path=descriptor_path,
        authority_path=authority_path,
        expected_candidate_sha256=expected_candidate_sha256,
    )
    if check_local_roots:
        return verify_local_release0_immutable_candidate(candidate)
    return {
        "status": "verified-descriptor-non-authorizing",
        "candidate_id": candidate.candidate_id,
        "identity_sha256": candidate.identity_sha256,
        "application_release_sha": candidate.application.release_sha,
        "control_release_sha": candidate.control.release_sha,
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--authority-public-key", required=True, type=Path)
    parser.add_argument(
        "--expected-candidate-sha256",
        required=True,
        help="root-controlled SHA-256 for the exact signed descriptor",
    )
    parser.add_argument(
        "--check-local-roots",
        action="store_true",
        help="also read clean local Git roots and their signed source/Compose bytes",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        result = verify(
            descriptor_path=arguments.descriptor,
            authority_path=arguments.authority_public_key,
            expected_candidate_sha256=arguments.expected_candidate_sha256,
            check_local_roots=arguments.check_local_roots,
        )
    except VerifyRelease0ImmutableCandidateError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
