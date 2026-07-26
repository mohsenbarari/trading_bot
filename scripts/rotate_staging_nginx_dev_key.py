#!/usr/bin/env python3
"""Rotate an exposed staging dev key without ever printing secret material.

The active Nginx vhost and every root-owned staging copy are updated
atomically.  If no matching application env file exists, the dev-login
location is disabled instead of installing a proxy key that no application
process can verify.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile


HEADER_RE = re.compile(
    rb'(?m)^(?P<indent>[ \t]*)proxy_set_header[ \t]+X-DEV-API-KEY[ \t]+"[^"\r\n]*";[ \t]*$'
)
ENV_RE = re.compile(rb"(?m)^DEV_API_KEY=[^\r\n]*$")
DOMAIN_MARKER = b"server_name staging.gold-trade.ir"
DISABLED_LINE = b'return 404; # Full Matrix: dev-login disabled after key rotation'


class RotationError(RuntimeError):
    pass


def _safe_regular(path: Path, *, label: str) -> bytes:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RotationError(f"{label} is not a root-controlled regular file")
    return path.read_bytes()


def _atomic(path: Path, raw: bytes, mode: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.rotate.", dir=path.parent)
    temp = Path(name)
    try:
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise RotationError("short atomic write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temp, path)


def rotate(*, nginx_root: Path, env_paths: list[Path], apply: bool) -> dict[str, int | bool]:
    key = secrets.token_urlsafe(48).encode("ascii")
    env_updates: dict[Path, tuple[bytes, int]] = {}
    for path in env_paths:
        if not path.exists():
            continue
        raw = _safe_regular(path, label="staging environment")
        replaced, count = ENV_RE.subn(b"DEV_API_KEY=" + key, raw)
        if count != 1:
            raise RotationError("staging environment has no unique DEV_API_KEY")
        env_updates[path] = (replaced, stat.S_IMODE(path.stat().st_mode))

    nginx_updates: dict[Path, tuple[bytes, int]] = {}
    for path in sorted(nginx_root.iterdir()):
        if not path.is_file() or path.is_symlink():
            continue
        raw = _safe_regular(path, label="staging Nginx configuration")
        if DOMAIN_MARKER not in raw or b"X-DEV-API-KEY" not in raw:
            continue
        if env_updates:
            replacement = rb'\g<indent>proxy_set_header X-DEV-API-KEY "' + key + b'";'
        else:
            replacement = rb"\g<indent>" + DISABLED_LINE
        replaced, count = HEADER_RE.subn(replacement, raw)
        if count < 1:
            raise RotationError("staging Nginx dev-key directive is malformed")
        nginx_updates[path] = (replaced, stat.S_IMODE(path.stat().st_mode))
    if not nginx_updates:
        raise RotationError("no staging Nginx dev-key directive was found")

    if apply:
        originals = {
            path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            for path in [*env_updates, *nginx_updates]
        }
        try:
            for path, (raw, mode) in env_updates.items():
                _atomic(path, raw, mode)
            for path, (raw, mode) in nginx_updates.items():
                _atomic(path, raw, mode)
            result = subprocess.run(
                ["/usr/sbin/nginx", "-t"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=30,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            )
            if result.returncode != 0:
                raise RotationError("Nginx rejected the rotated configuration")
            result = subprocess.run(
                ["/usr/bin/systemctl", "reload", "nginx"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=30,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            )
            if result.returncode != 0:
                raise RotationError("Nginx reload failed")
        except Exception:
            for path, (raw, mode) in originals.items():
                _atomic(path, raw, mode)
            raise
    return {
        "applied": apply,
        "nginx_files": len(nginx_updates),
        "environment_files": len(env_updates),
        "dev_login_disabled": not bool(env_updates),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nginx-root",
        type=Path,
        default=Path("/etc/nginx/sites-available"),
    )
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise RotationError("rotation requires root")
    result = rotate(
        nginx_root=args.nginx_root,
        env_paths=args.env_file,
        apply=args.apply,
    )
    # Never include the generated key or configuration content.
    print(
        " ".join(
            [
                f"applied={str(result['applied']).lower()}",
                f"nginx_files={result['nginx_files']}",
                f"environment_files={result['environment_files']}",
                f"dev_login_disabled={str(result['dev_login_disabled']).lower()}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
