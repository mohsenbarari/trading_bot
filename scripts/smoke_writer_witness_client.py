#!/usr/bin/env python3
"""Read-only authenticated smoke test for a staged writer-witness client."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import ssl
import sys
import time
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


STATUS_PATH = "/v1/writer-witness/status"
TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
ACTIVE_VENV_PYTHON = Path("/opt/trading-bot-witness/active/venv/bin/python")


def _require_isolated_runtime() -> None:
    if not (
        sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.ignore_environment
        and sys.flags.dont_write_bytecode
        and getattr(sys.flags, "safe_path", False)
        and sys.flags.utf8_mode == 1
        and sys.pycache_prefix == "/dev/null"
    ):
        raise SystemExit(
            "writer witness smoke client requires isolated no-site Python startup"
        )
    # CPython 3.12 deliberately reports the base prefix when started with -S.
    # Bind the exact launcher path instead; resolving only the path would also
    # accept an arbitrary symlink to the system interpreter.
    executable = Path(sys.executable)
    if executable != ACTIVE_VENV_PYTHON or executable.resolve(strict=True) != (
        ACTIVE_VENV_PYTHON.resolve(strict=True)
    ):
        raise SystemExit("writer witness smoke client is outside the active venv launcher")
    allowed = {"PATH": TRUSTED_PATH}
    if os.environ.get("LC_CTYPE") == "C.UTF-8":
        allowed["LC_CTYPE"] = "C.UTF-8"
    if dict(os.environ) != allowed:
        raise SystemExit("writer witness smoke client environment is not clean")


def _settings(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"invalid settings line in {path}")
        values[key.strip()] = value.strip()
    return values


def _canonical(
    *, timestamp: int, request_id: str, site: str, body: bytes = b""
) -> bytes:
    return "\n".join(
        (
            "writer-witness-auth-v1",
            "GET",
            STATUS_PATH,
            str(timestamp),
            request_id,
            site,
            hashlib.sha256(body).hexdigest(),
        )
    ).encode("utf-8")


def main() -> int:
    _require_isolated_runtime()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--ca-bundle", type=Path, required=True)
    parser.add_argument("--site", choices=("webapp_fi", "webapp_ir"), required=True)
    parser.add_argument("--expect-http-status", type=int, choices=(200, 401), default=200)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    settings = _settings(args.env_file)
    base_url = settings.get("WRITER_WITNESS_INTERNAL_URL", "").rstrip("/")
    key_id = settings.get("WRITER_WITNESS_CLIENT_KEY_ID", "")
    secret = settings.get("WRITER_WITNESS_CLIENT_SECRET", "")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path:
        raise SystemExit("writer witness smoke requires a root HTTPS internal URL")
    if not key_id or len(key_id) > 64 or len(secret.encode("utf-8")) < 32:
        raise SystemExit("writer witness smoke client credential is invalid")
    if not args.ca_bundle.is_file():
        raise SystemExit("writer witness CA bundle is missing")

    timestamp = int(time.time())
    request_id = f"smoke-{args.site}-{uuid4()}"
    signature = hmac.new(
        secret.encode("utf-8"),
        _canonical(timestamp=timestamp, request_id=request_id, site=args.site),
        hashlib.sha256,
    ).hexdigest()
    request = Request(
        f"{base_url}{STATUS_PATH}",
        method="GET",
        headers={
            "X-Writer-Witness-Key-Id": key_id,
            "X-Writer-Witness-Site": args.site,
            "X-Writer-Witness-Timestamp": str(timestamp),
            "X-Writer-Witness-Request-Id": request_id,
            "X-Writer-Witness-Signature": signature,
        },
    )
    context = ssl.create_default_context(cafile=str(args.ca_bundle))
    try:
        with urlopen(request, timeout=max(0.1, args.timeout), context=context) as response:
            payload = json.loads(response.read())
            status_code = response.status
    except HTTPError as exc:
        status_code = exc.code
        payload = {}
    if status_code != args.expect_http_status:
        raise SystemExit(
            f"writer witness smoke returned HTTP {status_code}, expected {args.expect_http_status}"
        )
    if args.expect_http_status == 401:
        print(
            json.dumps(
                {
                    "status": "passed",
                    "site": args.site,
                    "http_status": status_code,
                },
                sort_keys=True,
            )
        )
        return 0
    if (
        payload.get("accepted") is not True
        or payload.get("contract_version") != 1
        or payload.get("request_id") != request_id
    ):
        raise SystemExit("writer witness authenticated smoke response is invalid")
    print(
        json.dumps(
            {
                "status": "passed",
                "site": args.site,
                "http_status": status_code,
                "witness_time": payload.get("witness_time"),
                "state": payload.get("state"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
