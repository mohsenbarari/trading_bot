#!/usr/bin/env python3
"""Refresh the owner-only disposable WA-IR console URL without printing it."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes  # noqa: E402
from scripts.provision_arvan_full_matrix_destructive_hosts import (  # noqa: E402
    STATE_FILE,
    TOKEN_FILE,
    _safe_existing_state,
)
from scripts.provision_arvan_witness_recovery_vps import (  # noqa: E402
    api_request,
    read_private_text,
    response_data,
)


OUTPUT = Path(
    "/root/secure-envs/arvan/"
    "full-matrix-destructive-20260726.webapp_ir.vnc-url"
)
SERVER_ID = re.compile(r"[0-9a-f-]{36}\Z")


class ConsoleRefreshError(RuntimeError):
    pass


def refresh(*, token_file: Path, state_file: Path, output: Path) -> dict[str, str]:
    if state_file != STATE_FILE:
        raise ConsoleRefreshError("custom destructive state paths are forbidden")
    state = _safe_existing_state()
    if state is None or state.get("status") != "active":
        raise ConsoleRefreshError("destructive host state is unavailable")
    host = (state.get("hosts") or {}).get("webapp_ir")
    if not isinstance(host, dict):
        raise ConsoleRefreshError("WA-IR destructive host is missing")
    region = str(host.get("region") or "")
    server_id = str(host.get("server_id") or "")
    if region != "ir-thr-fr1" or SERVER_ID.fullmatch(server_id) is None:
        raise ConsoleRefreshError("WA-IR console identity is invalid")
    data = response_data(
        api_request(
            "GET",
            f"/regions/{region}/servers/{server_id}/vnc",
            read_private_text(token_file),
        ),
        "WA-IR VNC console",
    )
    if not isinstance(data, dict) or set(data) != {"url"}:
        raise ConsoleRefreshError("WA-IR VNC response is invalid")
    url = str(data["url"])
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, strict_parsing=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "console.arvaniaas.ir"
        or parsed.path != "/ir-thr-fr1/vnc_auto.html"
        or set(query) != {"token"}
        or len(query["token"]) != 1
        or len(query["token"][0]) < 32
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ConsoleRefreshError("WA-IR VNC URL is outside the reviewed origin")
    write_secure_atomic_bytes(
        output,
        (url + "\n").encode("utf-8"),
        label="WA-IR VNC console URL",
        mode=0o600,
        max_size=16 * 1024,
    )
    os.chmod(output, 0o600)
    return {
        "status": "refreshed",
        "role": "webapp_ir",
        "provider": "arvan",
        "url_printed": "false",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            refresh(
                token_file=args.token_file,
                state_file=args.state_file,
                output=args.output,
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
