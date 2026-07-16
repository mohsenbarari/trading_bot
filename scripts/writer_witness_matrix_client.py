#!/usr/bin/env python3
"""Ephemeral real-host Matrix client for signed Witness status/transitions."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


STATUS_PATH = "/v1/writer-witness/status"
TRANSITION_PATH = "/v1/writer-witness/transitions"
SITES = ("webapp_fi", "webapp_ir")


class MatrixClientError(RuntimeError):
    pass


def settings(path: Path) -> dict[str, str]:
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise MatrixClientError("matrix client environment must exist with owner-only mode")
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in result:
            raise MatrixClientError("matrix client environment is invalid")
        result[key] = value.strip()
    return result


def canonical(
    *, method: str, path: str, timestamp: int, request_id: str, site: str, body: bytes
) -> bytes:
    return "\n".join(
        (
            "writer-witness-auth-v1",
            method.upper(),
            path,
            str(timestamp),
            request_id,
            site,
            hashlib.sha256(body).hexdigest(),
        )
    ).encode("utf-8")


def signed_headers(
    *,
    key_id: str,
    secret: str,
    site: str,
    method: str,
    path: str,
    body: bytes,
    request_id: str,
    timestamp: int,
) -> dict[str, str]:
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical(
            method=method,
            path=path,
            timestamp=timestamp,
            request_id=request_id,
            site=site,
            body=body,
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Writer-Witness-Key-Id": key_id,
        "X-Writer-Witness-Site": site,
        "X-Writer-Witness-Timestamp": str(timestamp),
        "X-Writer-Witness-Request-Id": request_id,
        "X-Writer-Witness-Signature": signature,
        "Content-Type": "application/json",
    }


def request_payload(args: argparse.Namespace) -> bytes:
    payload = {
        "contract_version": 1,
        "action": args.action,
        "expected_epoch": args.expected_epoch,
        "expected_lease_id": args.expected_lease_id,
        "request_id": args.request_id,
        "reason": args.reason,
        "lease_duration_seconds": args.lease_duration_seconds,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def perform_http(
    *, url: str, method: str, body: bytes, headers: dict[str, str], context, timeout: float
) -> tuple[int, dict[str, object]]:
    request = Request(url, data=body if method == "POST" else None, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            status_code = response.status
            raw = response.read()
    except HTTPError as exc:
        status_code = exc.code
        raw = exc.read()
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise MatrixClientError("Witness returned a non-JSON response") from exc
    if not isinstance(payload, dict):
        raise MatrixClientError("Witness returned an invalid response shape")
    return status_code, payload


def fire_and_close(
    *, parsed, path: str, body: bytes, headers: dict[str, str], context, timeout: float
) -> None:
    port = parsed.port or 443
    host_header = parsed.hostname if port == 443 else f"{parsed.hostname}:{port}"
    lines = [
        f"POST {path} HTTP/1.1",
        f"Host: {host_header}",
        "Connection: close",
        f"Content-Length: {len(body)}",
        *(f"{key}: {value}" for key, value in headers.items()),
        "",
        "",
    ]
    raw_request = "\r\n".join(lines).encode("ascii") + body
    connection = socket.create_connection((parsed.hostname, port), timeout=timeout)
    tls = context.wrap_socket(connection, server_hostname=parsed.hostname)
    tls.sendall(raw_request)
    descriptor = tls.detach()
    os.close(descriptor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "transition", "fire-and-close"))
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--ca-bundle", type=Path, required=True)
    parser.add_argument("--site", choices=SITES, required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--timestamp-offset-seconds", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--expect-http-status", type=int, action="append")
    parser.add_argument("--action", choices=("acquire", "renew", "drain"))
    parser.add_argument("--expected-epoch", type=int)
    parser.add_argument("--expected-lease-id")
    parser.add_argument("--reason")
    parser.add_argument("--lease-duration-seconds", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    values = settings(args.env_file)
    base_url = values.get("WRITER_WITNESS_INTERNAL_URL", "").rstrip("/")
    parsed = urlparse(base_url)
    key_id = values.get("WRITER_WITNESS_CLIENT_KEY_ID", "")
    secret = values.get("WRITER_WITNESS_CLIENT_SECRET", "")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise MatrixClientError("matrix client requires a root HTTPS Witness URL")
    if not key_id or len(key_id) > 64 or len(secret.encode("utf-8")) < 32:
        raise MatrixClientError("matrix client credential is invalid")
    if not args.ca_bundle.is_file():
        raise MatrixClientError("matrix client CA bundle is missing")
    if not args.request_id or len(args.request_id) > 64:
        raise MatrixClientError("matrix request id is invalid")

    method = "GET" if args.command == "status" else "POST"
    path = STATUS_PATH if method == "GET" else TRANSITION_PATH
    body = b""
    if method == "POST":
        if args.action is None or args.expected_epoch is None or not args.reason:
            raise MatrixClientError("transition command fields are incomplete")
        if args.lease_duration_seconds < 30 or args.lease_duration_seconds > 3600:
            raise MatrixClientError("lease duration is outside the service contract")
        body = request_payload(args)
    timestamp = int(time.time()) + args.timestamp_offset_seconds
    headers = signed_headers(
        key_id=key_id,
        secret=secret,
        site=args.site,
        method=method,
        path=path,
        body=body,
        request_id=args.request_id,
        timestamp=timestamp,
    )
    context = ssl.create_default_context(cafile=str(args.ca_bundle))
    if args.command == "fire-and-close":
        fire_and_close(
            parsed=parsed,
            path=path,
            body=body,
            headers=headers,
            context=context,
            timeout=max(0.1, args.timeout),
        )
        print(
            json.dumps(
                {
                    "status": "request-sent-response-unread",
                    "site": args.site,
                    "request_id": args.request_id,
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                },
                sort_keys=True,
            )
        )
        return 0

    status_code, payload = perform_http(
        url=f"{base_url}{path}",
        method=method,
        body=body,
        headers=headers,
        context=context,
        timeout=max(0.1, args.timeout),
    )
    expected = set(args.expect_http_status or (200,))
    if status_code not in expected:
        raise MatrixClientError(
            f"Witness returned HTTP {status_code}, expected one of {sorted(expected)}"
        )
    if status_code == 200 and payload.get("accepted") is not True:
        raise MatrixClientError("Witness HTTP 200 response was not accepted")
    if payload.get("request_id") not in {None, args.request_id}:
        raise MatrixClientError("Witness response request id mismatch")
    safe_payload = dict(payload)
    safe_payload.pop("proof", None)
    print(
        json.dumps(
            {
                "status": "passed",
                "site": args.site,
                "http_status": status_code,
                "request_id": args.request_id,
                "response": safe_payload,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MatrixClientError, OSError, ssl.SSLError) as exc:
        raise SystemExit(f"writer witness matrix client failed: {exc}") from exc
