#!/usr/bin/env python3
"""Closed local TLS and application-readiness probe for a WebApp origin.

This program runs on the origin host.  It reaches the Nginx vhost through a
loopback TLS socket while retaining the public SNI/hostname, then independently
checks the loopback application readiness endpoint.  It never accepts a URL,
path, credential, command, or destination from the caller.
"""

from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
from typing import Any


SCHEMA = "three-site-full-matrix-origin-local-probe-v1"
PUBLIC_HOST = "app.gold-trading.ir"
ROLE_PORTS = {"webapp_fi": 8212, "webapp_ir": 8213}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}


class OriginProbeError(RuntimeError):
    """The local origin cannot prove the pinned ingress contract."""


class _LoopbackSniConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        raw = socket.create_connection(("127.0.0.1", 443), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=PUBLIC_HOST)


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=SAFE_ENV,
    )
    if result.returncode != 0 or result.stderr:
        raise OriginProbeError("origin release checkout cannot be verified")
    return result.stdout.strip()


def _local_tls_health() -> tuple[int, str]:
    connection = _LoopbackSniConnection(
        PUBLIC_HOST,
        port=443,
        timeout=15,
        context=ssl.create_default_context(),
    )
    try:
        connection.request("GET", "/_full_matrix_origin_health")
        response = connection.getresponse()
        body = response.read(1025)
        cache_control = str(response.getheader("Cache-Control") or "")
    finally:
        connection.close()
    if response.status != 204 or body or "no-store" not in cache_control.lower():
        raise OriginProbeError("local TLS origin health contract is invalid")
    return response.status, cache_control


def _application_ready(*, expected_site: str, port: int) -> dict[str, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port=port, timeout=15)
    try:
        connection.request("GET", "/health/ready")
        response = connection.getresponse()
        raw = response.read(16 * 1024 + 1)
    finally:
        connection.close()
    if response.status != 200 or len(raw) > 16 * 1024:
        raise OriginProbeError("local application readiness endpoint did not pass")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OriginProbeError("local application readiness payload is invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("ready") is not True
        or value.get("database_ok") is not True
        or value.get("redis_ok") is not True
        or value.get("physical_site") != expected_site
        or value.get("reasons") != []
    ):
        raise OriginProbeError("local application readiness does not match origin role")
    return value


def probe(*, site: str, release_sha: str, port: int) -> dict[str, Any]:
    if SHA40.fullmatch(release_sha) is None or ROLE_PORTS.get(site) != port:
        raise OriginProbeError("origin probe identity is invalid")
    repo_root = Path(__file__).resolve().parents[2]
    if _git(repo_root, "rev-parse", "HEAD") != release_sha:
        raise OriginProbeError("origin release differs from probe request")
    if _git(repo_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise OriginProbeError("origin release checkout is dirty")
    tls_status, cache_control = _local_tls_health()
    readiness = _application_ready(expected_site=site, port=port)
    return {
        "schema": SCHEMA,
        "status": "passed",
        "site": site,
        "release_sha": release_sha,
        "origin_tls_status": tls_status,
        "origin_cache_control": cache_control,
        "application_ready": True,
        "application_physical_site": readiness["physical_site"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", choices=sorted(ROLE_PORTS), required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(probe(site=args.site, release_sha=args.release_sha, port=args.port), sort_keys=True))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
