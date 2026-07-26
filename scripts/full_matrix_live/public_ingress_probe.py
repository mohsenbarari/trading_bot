#!/usr/bin/env python3
"""Probe the isolated public Full Matrix ingress without exposing its secret.

The probe has one fixed HTTPS endpoint and uses Basic Auth only from a
root-owned controller file.  It reports an attested, redacted summary of the
response; the credential and response body are never printed or persisted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
from pathlib import Path
import re
import ssl
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_bytes  # noqa: E402


SCHEMA = "three-site-full-matrix-public-ingress-probe-v1"
PUBLIC_HOST = "app.gold-trading.ir"
PATH = "/health/origin-ready?require_global_convergence=true"
CONFIG_PATH = "/api/config"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
AUTH_LINE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,31}:[A-Za-z0-9_-]{32,128}\n\Z")


class PublicIngressProbeError(RuntimeError):
    """The public ingress cannot prove the final active data plane."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PublicIngressProbeError("public ingress JSON has duplicate keys")
        value[key] = item
    return value


def _authorization(*, client_auth_file: Path, expected_sha256: str) -> str:
    if (
        not client_auth_file.is_absolute()
        or client_auth_file.is_symlink()
        or SHA256.fullmatch(expected_sha256) is None
    ):
        raise PublicIngressProbeError("public ingress credential reference is invalid")
    try:
        raw = read_secure_bytes(
            client_auth_file,
            label="Full Matrix ingress Basic Auth client material",
            max_size=16 * 1024,
        )
        credential = raw.decode("ascii")
    except Exception as exc:
        raise PublicIngressProbeError("public ingress credential is unavailable") from exc
    if (
        hashlib.sha256(raw).hexdigest() != expected_sha256
        or AUTH_LINE.fullmatch(credential) is None
    ):
        raise PublicIngressProbeError("public ingress credential differs from its bound digest")
    encoded = base64.b64encode(credential.rstrip("\n").encode("ascii")).decode("ascii")
    return f"Basic {encoded}"


def _request_json(
    *,
    authorization: str | None,
    path: str,
    origin: str | None = None,
) -> tuple[int, bytes, str, str | None, str, str]:
    connection = http.client.HTTPSConnection(
        PUBLIC_HOST,
        port=443,
        timeout=20,
        context=ssl.create_default_context(),
    )
    try:
        headers = {
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        }
        if authorization is not None:
            headers["Authorization"] = authorization
        if origin is not None:
            headers["Origin"] = origin
        connection.request(
            "GET",
            path,
            headers=headers,
        )
        response = connection.getresponse()
        raw = response.read(64 * 1024 + 1)
        return (
            response.status,
            raw,
            str(response.getheader("Cache-Control") or ""),
            response.getheader("Age"),
            str(response.getheader("Access-Control-Allow-Origin") or ""),
            str(response.getheader("WWW-Authenticate") or ""),
        )
    finally:
        connection.close()


def probe(
    *,
    release_sha: str,
    expected_active_origin: str,
    client_auth_file: Path,
    client_auth_sha256: str,
) -> dict[str, Any]:
    if (
        SHA40.fullmatch(release_sha) is None
        or expected_active_origin not in {"webapp_fi", "webapp_ir"}
    ):
        raise PublicIngressProbeError("public ingress identity is invalid")
    authorization = _authorization(
        client_auth_file=client_auth_file,
        expected_sha256=client_auth_sha256,
    )
    status, raw, cache_control, age, _cors, _auth = _request_json(
        authorization=authorization,
        path=PATH,
    )
    repeat_status, repeat_raw, repeat_cache_control, repeat_age, _repeat_cors, _repeat_auth = _request_json(
        authorization=authorization,
        path=PATH,
    )
    config_status, config_raw, config_cache_control, config_age, config_cors, _config_auth = _request_json(
        authorization=authorization,
        path=CONFIG_PATH,
        origin=f"https://{PUBLIC_HOST}",
    )
    root_status, root_raw, _root_cache, _root_age, _root_cors, root_auth = _request_json(
        authorization=None,
        path="/",
    )
    dev_login_status, dev_login_raw, _dev_cache, _dev_age, _dev_cors, _dev_auth = _request_json(
        authorization=None,
        path="/api/auth/dev-login",
    )
    if (
        status != 200
        or len(raw) > 64 * 1024
        or "no-store" not in cache_control.lower()
        or age not in {None, "0"}
        or repeat_status != 200
        or len(repeat_raw) > 64 * 1024
        or "no-store" not in repeat_cache_control.lower()
        or repeat_age not in {None, "0"}
        or config_status != 200
        or len(config_raw) > 64 * 1024
        or "no-store" not in config_cache_control.lower()
        or config_age not in {None, "0"}
        or root_status != 401
        or len(root_raw) > 64 * 1024
        or not root_auth.lower().startswith("basic")
        or dev_login_status != 404
        or len(dev_login_raw) > 64 * 1024
    ):
        raise PublicIngressProbeError("public ingress HTTP/TLS contract did not pass")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
        repeated = json.loads(repeat_raw.decode("utf-8"), object_pairs_hook=_strict_object)
        public_config = json.loads(config_raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicIngressProbeError("public ingress response is invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("origin_ready") is not True
        or value.get("physical_site") != expected_active_origin
        or value.get("runtime_role") != "active"
        or type(value.get("writer_epoch")) is not int
        or value["writer_epoch"] < 1
        or value.get("release_sha") != release_sha
        or not isinstance(value.get("migration_revision"), str)
        or not value["migration_revision"]
        or value.get("database_ok") is not True
        or value.get("redis_ok") is not True
        or value.get("global_convergence_required") is not True
        or value.get("reasons") != []
        or not isinstance(value.get("witness_lease_id"), str)
        or not value["witness_lease_id"]
        or not isinstance(repeated, dict)
        or repeated.get("origin_ready") is not True
        or repeated.get("physical_site") != expected_active_origin
        or repeated.get("runtime_role") != "active"
        or repeated.get("release_sha") != release_sha
        or repeated.get("global_convergence_required") is not True
        or repeated.get("reasons") != []
        or not isinstance(public_config, dict)
        or not isinstance(public_config.get("bot_username"), str)
        or not isinstance(public_config.get("frontend_url"), str)
        or public_config["frontend_url"] != f"https://{PUBLIC_HOST}"
        or config_cors != f"https://{PUBLIC_HOST}"
    ):
        raise PublicIngressProbeError("public ingress data plane does not match final writer state")
    return {
        "schema": SCHEMA,
        "status": "passed",
        "public_host": PUBLIC_HOST,
        "expected_active_origin": expected_active_origin,
        "release_sha": release_sha,
        "http_status": status,
        "origin_ready": True,
        "writer_epoch": value["writer_epoch"],
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "repeated_health_status": repeat_status,
        "dynamic_config_status": config_status,
        "dynamic_cache_no_store": True,
        "health_cache_not_stale": True,
        "canonical_frontend_url": True,
        "canonical_cors_origin": True,
        "basic_auth_enforced": True,
        "dev_login_denied": True,
    }


def probe_safe_unavailable(
    *,
    release_sha: str,
    client_auth_file: Path,
    client_auth_sha256: str,
) -> dict[str, Any]:
    """Prove that the public route has no ready Writer, without bypassing TLS.

    This deliberately does *not* accept a redirect, an unauthenticated
    response, or a successful stale health response.  A powered-off active
    origin can be surfaced by the edge as either an explicit application 503
    or an upstream 502/504; all three mean that no origin was substituted.
    The caller separately proves the surviving standby is lease-fenced.
    """

    if SHA40.fullmatch(release_sha) is None:
        raise PublicIngressProbeError("public unavailable probe release is invalid")
    authorization = _authorization(
        client_auth_file=client_auth_file,
        expected_sha256=client_auth_sha256,
    )
    first = _request_json(authorization=authorization, path=PATH)
    second = _request_json(authorization=authorization, path=PATH)
    statuses = (first[0], second[0])
    if (
        any(status not in {502, 503, 504} for status in statuses)
        or any(len(item[1]) > 64 * 1024 for item in (first, second))
        or any("no-store" not in item[2].lower() for item in (first, second))
        or any(item[3] not in {None, "0"} for item in (first, second))
    ):
        raise PublicIngressProbeError("public ingress did not fail safely while no Writer was ready")
    return {
        "schema": SCHEMA,
        "status": "safe_unavailable",
        "public_host": PUBLIC_HOST,
        "release_sha": release_sha,
        "first_http_status": first[0],
        "second_http_status": second[0],
        "first_response_sha256": hashlib.sha256(first[1]).hexdigest(),
        "second_response_sha256": hashlib.sha256(second[1]).hexdigest(),
        "tls_authenticated_uncached_fail_closed": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument(
        "--expected-active-origin",
        choices=("webapp_fi", "webapp_ir"),
        default="webapp_fi",
    )
    parser.add_argument("--client-auth-file", required=True, type=Path)
    parser.add_argument("--client-auth-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                probe(
                    release_sha=args.release_sha,
                    expected_active_origin=args.expected_active_origin,
                    client_auth_file=args.client_auth_file,
                    client_auth_sha256=args.client_auth_sha256,
                ),
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
