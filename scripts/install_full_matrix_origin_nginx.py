#!/usr/bin/env python3
"""Render and atomically install the isolated Full Matrix Nginx origin vhost."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


HOST = "app.gold-trading.ir"
ALLOWED_PORTS = {8212, 8213}
TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "deploy/staging/nginx-full-matrix-origin.conf.template"
)
AVAILABLE = Path("/etc/nginx/sites-available/trading-bot-full-matrix-origin")
ENABLED = Path("/etc/nginx/sites-enabled/trading-bot-full-matrix-origin")
TOKEN_RE = re.compile(r"__[A-Z0-9_]+__")


class FullMatrixNginxError(RuntimeError):
    pass


def _regular(path: Path, *, owner_only: bool, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise FullMatrixNginxError(f"{label} path is unsafe")
    metadata = path.stat()
    forbidden = 0o077 if owner_only else 0o022
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & forbidden
    ):
        raise FullMatrixNginxError(f"{label} is not a root-controlled regular file")
    return path.resolve()


def _directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise FullMatrixNginxError(f"{label} path is unsafe")
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise FullMatrixNginxError(f"{label} is not a root-controlled directory")
    return path.resolve()


def _run(argv: list[str]) -> None:
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=30,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
    )
    if result.returncode != 0:
        raise FullMatrixNginxError(f"{argv[0]} validation failed")


def render(
    *,
    host: str,
    port: int,
    frontend_root: Path,
    certificate: Path,
    certificate_key: Path,
    basic_auth_file: Path,
) -> str:
    if host != HOST or port not in ALLOWED_PORTS:
        raise FullMatrixNginxError("origin host or loopback port is outside the closed scope")
    values = {
        "__FULL_MATRIX_HOST__": host,
        "__APP_PORT__": str(port),
        "__FRONTEND_ROOT__": str(_directory(frontend_root, label="frontend root")),
        "__TLS_CERTIFICATE__": str(_regular(certificate, owner_only=False, label="TLS certificate")),
        "__TLS_CERTIFICATE_KEY__": str(
            _regular(certificate_key, owner_only=True, label="TLS private key")
        ),
        "__BASIC_AUTH_FILE__": str(
            _regular(basic_auth_file, owner_only=True, label="Basic Auth file")
        ),
    }
    template = _regular(TEMPLATE, owner_only=False, label="Nginx template").read_text(
        encoding="utf-8"
    )
    for token, value in values.items():
        template = template.replace(token, value)
    if TOKEN_RE.search(template):
        raise FullMatrixNginxError("Nginx template contains an unresolved token")
    if "coin.gold-trade.ir" in template or "X-DEV-API-KEY" in template:
        raise FullMatrixNginxError("Nginx template crosses a production or secret boundary")
    return template


def install(rendered: str) -> None:
    AVAILABLE.parent.mkdir(parents=True, exist_ok=True)
    ENABLED.parent.mkdir(parents=True, exist_ok=True)
    previous = AVAILABLE.read_bytes() if AVAILABLE.exists() else None
    previous_link = ENABLED.is_symlink()
    descriptor, raw_path = tempfile.mkstemp(
        prefix=".full-matrix-origin.", dir=str(AVAILABLE.parent)
    )
    temp = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, rendered.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temp, AVAILABLE)
        if ENABLED.exists() and not ENABLED.is_symlink():
            raise FullMatrixNginxError("enabled Full Matrix site is not a symlink")
        ENABLED.unlink(missing_ok=True)
        ENABLED.symlink_to(AVAILABLE)
        _run(["/usr/sbin/nginx", "-t"])
        _run(["/usr/bin/systemctl", "reload", "nginx"])
    except Exception:
        ENABLED.unlink(missing_ok=True)
        if previous is None:
            AVAILABLE.unlink(missing_ok=True)
        else:
            rollback = AVAILABLE.with_name(".full-matrix-origin.rollback")
            rollback.write_bytes(previous)
            os.chmod(rollback, 0o600)
            os.replace(rollback, AVAILABLE)
            if previous_link:
                ENABLED.symlink_to(AVAILABLE)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--frontend-root", required=True, type=Path)
    parser.add_argument("--certificate", required=True, type=Path)
    parser.add_argument("--certificate-key", required=True, type=Path)
    parser.add_argument("--basic-auth-file", required=True, type=Path)
    parser.add_argument("--render-only", type=Path)
    args = parser.parse_args(argv)
    rendered = render(
        host=args.host,
        port=args.port,
        frontend_root=args.frontend_root,
        certificate=args.certificate,
        certificate_key=args.certificate_key,
        basic_auth_file=args.basic_auth_file,
    )
    if args.render_only is not None:
        args.render_only.write_text(rendered, encoding="utf-8")
    else:
        if os.geteuid() != 0:
            raise FullMatrixNginxError("Nginx installation requires root")
        install(rendered)
    print("full-matrix-origin-nginx=installed" if args.render_only is None else "full-matrix-origin-nginx=rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
