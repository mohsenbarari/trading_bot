#!/usr/bin/env python3
"""Certbot manual DNS hook scoped to app.gold-trading.ir.

Only the exact ACME TXT record is created.  Cleanup deletes only the immutable
record id retained by the matching authentication invocation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.parse
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    read_secure_text,
    write_secure_atomic_bytes,
)
from scripts.arvan_origin_switch import (  # noqa: E402
    DEFAULT_API_BASE,
    api_request,
)


ROOT_DOMAIN = "gold-trading.ir"
CERT_DOMAIN = "app.gold-trading.ir"
RECORD_NAME = "_acme-challenge.app"
TOKEN_FILE = Path("/root/secure-envs/trading-bot/arvan-cdn-token")
STATE_FILE = Path("/var/lib/letsencrypt/arvan-full-matrix-acme.json")
VALIDATION_RE = re.compile(r"[A-Za-z0-9_-]{32,255}\Z")
ID_RE = re.compile(r"[A-Za-z0-9-]{8,128}\Z")


class ArvanAcmeHookError(RuntimeError):
    pass


def _validation() -> str:
    if os.environ.get("CERTBOT_DOMAIN") != CERT_DOMAIN:
        raise ArvanAcmeHookError("Certbot domain is outside the Full Matrix scope")
    value = str(os.environ.get("CERTBOT_VALIDATION") or "")
    if VALIDATION_RE.fullmatch(value) is None:
        raise ArvanAcmeHookError("Certbot validation value is invalid")
    return value


def _token(path: Path) -> str:
    value = read_secure_text(path, label="Arvan API token", max_size=16 * 1024).strip()
    if len(value) < 16:
        raise ArvanAcmeHookError("Arvan API token is malformed")
    return value


def _state(path: Path) -> dict:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ArvanAcmeHookError("ACME hook state is unsafe")
    value = json.loads(
        read_secure_text(path, label="ACME hook state", max_size=64 * 1024)
    )
    if not isinstance(value, dict):
        raise ArvanAcmeHookError("ACME hook state is invalid")
    return value


def _records_url() -> str:
    return f"{DEFAULT_API_BASE}/domains/{ROOT_DOMAIN}/dns-records"


def _wait_public(value: str, *, timeout: int = 240) -> None:
    deadline = time.monotonic() + timeout
    query = urllib.parse.urlencode(
        {"name": f"_acme-challenge.{CERT_DOMAIN}", "type": "TXT"}
    )
    while time.monotonic() < deadline:
        request = urllib.request.Request(
            f"https://dns.google/resolve?{query}",
            headers={"Accept": "application/dns-json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
            answers = payload.get("Answer") if isinstance(payload, dict) else None
            if isinstance(answers, list) and any(
                str(item.get("data") or "").strip('"') == value
                for item in answers
                if isinstance(item, dict)
            ):
                return
        except Exception:
            pass
        time.sleep(5)
    raise ArvanAcmeHookError("ACME TXT record did not become publicly observable")


def authenticate(*, token_file: Path, state_file: Path) -> None:
    value = _validation()
    if state_file.exists():
        raise ArvanAcmeHookError("prior ACME hook state still exists")
    response = api_request(
        "POST",
        _records_url(),
        _token(token_file),
        {
            "type": "txt",
            "name": RECORD_NAME,
            "value": {"text": value},
            "ttl": 120,
        },
    )
    data = response.get("data")
    record_id = str(data.get("id") or "") if isinstance(data, dict) else ""
    if ID_RE.fullmatch(record_id) is None:
        raise ArvanAcmeHookError("Arvan did not return the ACME record id")
    state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = {
        "schema": "arvan-full-matrix-acme-hook-v1",
        "domain": CERT_DOMAIN,
        "record_name": RECORD_NAME,
        "record_id": record_id,
        "validation": value,
    }
    write_secure_atomic_bytes(
        state_file,
        (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        label="ACME hook state",
        mode=0o600,
        max_size=64 * 1024,
    )
    os.chmod(state_file, 0o600)
    _wait_public(value)


def cleanup(*, token_file: Path, state_file: Path) -> None:
    value = _validation()
    state = _state(state_file)
    if (
        state
        != {
            "schema": "arvan-full-matrix-acme-hook-v1",
            "domain": CERT_DOMAIN,
            "record_name": RECORD_NAME,
            "record_id": state.get("record_id"),
            "validation": value,
        }
        or ID_RE.fullmatch(str(state.get("record_id") or "")) is None
    ):
        raise ArvanAcmeHookError("ACME cleanup identity differs")
    api_request(
        "DELETE",
        f"{_records_url()}/{urllib.parse.quote(str(state['record_id']), safe='')}",
        _token(token_file),
        None,
    )
    state_file.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("auth", "cleanup"))
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    args = parser.parse_args(argv)
    if args.action == "auth":
        authenticate(token_file=args.token_file, state_file=args.state_file)
    else:
        cleanup(token_file=args.token_file, state_file=args.state_file)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1)
