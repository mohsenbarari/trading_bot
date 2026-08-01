#!/usr/bin/env python3
"""Read-only verifier for a root-owned WA-FI fenced-release identity.

This utility deliberately does not start a Compose project, acquire a Writer
Witness lease, or grant a writer permit.  It is the installation-time gate
used to check a signed identity before a later fenced cutover binds it to the
lease agent.  The authority file contains one strict base64-encoded Ed25519
public key (32 decoded bytes); its key id is derived locally rather than
accepted as an operator-supplied argument.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.fenced_fi_release_identity import (  # noqa: E402
    FencedFiReleaseIdentityAuthority,
    FencedFiReleaseIdentityError,
    verify_fenced_fi_release_identity,
)


MAX_FILE_BYTES = 64 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class VerifyFencedFiReleaseIdentityError(RuntimeError):
    """The host cannot safely read or verify the configured identity."""


def _secure_read(path: Path, *, label: str) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        raise VerifyFencedFiReleaseIdentityError(
            "fenced FI release identity verification requires O_NOFOLLOW"
        )
    # A malicious replacement with a FIFO must fail by metadata validation,
    # never make this root-only preflight wait for a writer.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | no_follow
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VerifyFencedFiReleaseIdentityError(f"cannot securely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > MAX_FILE_BYTES
        ):
            raise VerifyFencedFiReleaseIdentityError(
                f"{label} is not an owner-only regular file"
            )
        value = bytearray()
        while len(value) <= MAX_FILE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - len(value)))
            if not chunk:
                break
            value.extend(chunk)
        if len(value) > MAX_FILE_BYTES:
            raise VerifyFencedFiReleaseIdentityError(f"{label} is oversized")
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise VerifyFencedFiReleaseIdentityError(f"{label} changed while being read")
        return bytes(value)
    finally:
        os.close(descriptor)


def _authority_from_file(path: Path) -> FencedFiReleaseIdentityAuthority:
    raw = _secure_read(path, label="fenced FI release identity authority")
    try:
        text = raw.decode("ascii")
        if text.rstrip("\n") != text.strip() or text.count("\n") > 1:
            raise ValueError
        key = base64.b64decode(text.strip().encode("ascii"), validate=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise VerifyFencedFiReleaseIdentityError(
            "fenced FI release identity authority is not strict base64"
        ) from exc
    if len(key) != 32:
        raise VerifyFencedFiReleaseIdentityError(
            "fenced FI release identity authority has an invalid key length"
        )
    return FencedFiReleaseIdentityAuthority(
        public_key=key,
        key_id="ed25519-sha256:" + hashlib.sha256(key).hexdigest(),
    )


def verify(
    *,
    descriptor_path: Path,
    authority_path: Path,
    expected_identity_sha256: str | None = None,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise VerifyFencedFiReleaseIdentityError(
            "fenced FI release identity verification must run as root"
        )
    authority = _authority_from_file(authority_path)
    try:
        identity = verify_fenced_fi_release_identity(
            _secure_read(descriptor_path, label="fenced FI release identity descriptor"),
            authority=authority,
        )
    except FencedFiReleaseIdentityError as exc:
        raise VerifyFencedFiReleaseIdentityError(
            f"fenced FI release identity is invalid: {exc.code}"
        ) from exc
    if expected_identity_sha256 is not None:
        if (
            type(expected_identity_sha256) is not str
            or SHA256_RE.fullmatch(expected_identity_sha256) is None
            or identity.identity_sha256 != expected_identity_sha256
        ):
            raise VerifyFencedFiReleaseIdentityError(
                "fenced FI release identity does not match the expected descriptor hash"
            )
    return {
        "status": "verified-non-authorizing",
        "identity_sha256": identity.identity_sha256,
        "release_sha": identity.release_sha,
        "release_tree_sha": identity.release_tree_sha,
        "control_release_sha": identity.control_release_sha,
        "control_release_tree_sha": identity.control_release_tree_sha,
        "compose_relative_path": identity.compose_relative_path,
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--authority-public-key", required=True, type=Path)
    parser.add_argument(
        "--expected-identity-sha256",
        required=True,
        help="root-controlled SHA-256 from the installation manifest",
    )
    arguments = parser.parse_args()
    try:
        result = verify(
            descriptor_path=arguments.descriptor,
            authority_path=arguments.authority_public_key,
            expected_identity_sha256=arguments.expected_identity_sha256,
        )
    except VerifyFencedFiReleaseIdentityError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
