#!/usr/bin/env python3
"""Create separate owner-only origin and controller Basic Auth material."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes  # noqa: E402


USER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,31}\Z")


class BasicAuthMaterialError(RuntimeError):
    pass


def create(*, username: str, server_file: Path, client_file: Path) -> dict[str, str]:
    if USER_RE.fullmatch(username) is None:
        raise BasicAuthMaterialError("Basic Auth username is invalid")
    for path in (server_file, client_file):
        if path.exists() or path.is_symlink() or not path.is_absolute():
            raise BasicAuthMaterialError("Basic Auth output already exists or is unsafe")
    password = secrets.token_urlsafe(48)
    result = subprocess.run(
        ["/usr/bin/openssl", "passwd", "-6", "-stdin"],
        input=password + "\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    hashed = result.stdout.strip()
    if result.returncode != 0 or result.stderr or not hashed.startswith("$6$"):
        raise BasicAuthMaterialError("password hashing failed")
    write_secure_atomic_bytes(
        server_file,
        f"{username}:{hashed}\n".encode("utf-8"),
        label="Full Matrix Basic Auth server file",
        mode=0o600,
        max_size=16 * 1024,
    )
    write_secure_atomic_bytes(
        client_file,
        f"{username}:{password}\n".encode("utf-8"),
        label="Full Matrix Basic Auth client file",
        mode=0o600,
        max_size=16 * 1024,
    )
    os.chmod(server_file, 0o600)
    os.chmod(client_file, 0o600)
    return {
        "status": "created",
        "username": username,
        "server_file": str(server_file),
        "client_file": str(client_file),
        "secret_printed": "false",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="matrix_operator")
    parser.add_argument("--server-file", required=True, type=Path)
    parser.add_argument("--client-file", required=True, type=Path)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            create(
                username=args.username,
                server_file=args.server_file,
                client_file=args.client_file,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error_class": type(exc).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
