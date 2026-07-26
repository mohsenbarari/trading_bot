#!/usr/bin/env python3
"""Build the release-bound, secret-free ingress probe configuration.

The Basic Auth client material stays outside the repository and outside the
JSON configuration.  The configuration records only its owner-controlled path
and digest, so a later campaign can detect credential substitution without
ever copying or printing the credential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    read_secure_bytes,
    sha256_secure_file,
    write_secure_atomic_bytes,
)


SCHEMA = "three-site-full-matrix-ingress-config-v1"
PUBLIC_HOST = "app.gold-trading.ir"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
AUTH_LINE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,31}:[A-Za-z0-9_-]{32,128}\n\Z")


class FullMatrixIngressConfigError(RuntimeError):
    """The ingress probe configuration cannot be safely bound."""


def build(*, release_sha: str, client_auth_file: Path) -> dict[str, Any]:
    if SHA40.fullmatch(release_sha) is None:
        raise FullMatrixIngressConfigError("ingress release SHA is invalid")
    if not client_auth_file.is_absolute() or client_auth_file.is_symlink():
        raise FullMatrixIngressConfigError("ingress client credential path is unsafe")
    try:
        raw = read_secure_bytes(
            client_auth_file,
            label="Full Matrix ingress Basic Auth client material",
            max_size=16 * 1024,
        )
    except Exception as exc:
        raise FullMatrixIngressConfigError("ingress client credential is unsafe") from exc
    try:
        decoded = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FullMatrixIngressConfigError("ingress client credential is not ASCII") from exc
    if AUTH_LINE.fullmatch(decoded) is None:
        raise FullMatrixIngressConfigError("ingress client credential format is invalid")
    digest, size = sha256_secure_file(
        client_auth_file,
        label="Full Matrix ingress Basic Auth client material",
    )
    if size != len(raw):
        raise FullMatrixIngressConfigError("ingress client credential changed while read")
    return {
        "schema": SCHEMA,
        "release_sha": release_sha,
        "public_host": PUBLIC_HOST,
        "public_url": f"https://{PUBLIC_HOST}/health/origin-ready?require_global_convergence=true",
        "expected_active_origin": "webapp_fi",
        "client_auth_file": str(client_auth_file.resolve()),
        "client_auth_sha256": digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--client-auth-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
            raise FullMatrixIngressConfigError("ingress configuration output is unsafe")
        value = build(
            release_sha=args.release_sha,
            client_auth_file=args.client_auth_file,
        )
        raw = (
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        write_secure_atomic_bytes(
            args.output,
            raw,
            label="Full Matrix ingress probe configuration",
            mode=0o600,
            max_size=16 * 1024,
        )
        print(
            json.dumps(
                {
                    "status": "built",
                    "schema": SCHEMA,
                    "output": str(args.output),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
