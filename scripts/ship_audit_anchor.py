#!/usr/bin/env python3
"""Ship the latest compact audit anchor line to a restricted off-host sink."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "anchor_exported_at",
    "source_name",
    "host_id",
    "release_id",
    "audit_event_id",
    "audit_recorded_at",
    "event_hash",
    "previous_hash",
    "audit_trail_records",
    "audit_trail_sha256",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ship the latest compact audit anchor line to a restricted sink.")
    parser.add_argument("--input", required=True, help="Local audit anchor JSONL path.")
    parser.add_argument("--remote", default="", help="Remote append-only target in the form user@host:/path/file.jsonl.")
    parser.add_argument(
        "--remote-port",
        default=os.getenv("ANCHOR_REMOTE_SSH_PORT", ""),
        help="Optional SSH port for --remote. Defaults to ssh_config/system SSH behavior when omitted.",
    )
    parser.add_argument("--output", default="", help="Optional local append-only output path.")
    return parser.parse_args()


def _load_latest_anchor_line(input_path: Path) -> tuple[str, dict[str, Any]]:
    if not input_path.exists():
        raise FileNotFoundError(f"Audit anchor file does not exist: {input_path}")
    lines = [line.strip() for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"Audit anchor file is empty: {input_path}")
    latest_line = lines[-1]
    try:
        payload = json.loads(latest_line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed audit anchor JSON: {exc}") from exc
    missing = sorted(REQUIRED_FIELDS.difference(payload))
    if missing:
        raise ValueError(f"Audit anchor payload missing required fields: {', '.join(missing)}")
    return latest_line, payload


def _append_local(output_path: Path, line: str) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return str(output_path)


def _parse_remote_target(remote: str) -> tuple[str, str]:
    host, sep, path = remote.partition(":")
    if not host or not sep or not path.strip():
        raise ValueError("Remote target must be in the form user@host:/path/file.jsonl")
    return host, path


def _build_remote_append_command(remote: str, line: str, *, port: str = "") -> list[str]:
    host, path = _parse_remote_target(remote)
    remote_dir = str(Path(path).parent)
    remote_cmd = (
        f"install -d {json.dumps(remote_dir)} "
        f"&& cat >> {json.dumps(path)}"
    )
    command = ["ssh"]
    normalized_port = str(port or "").strip()
    if normalized_port:
        command.extend(["-p", normalized_port])
    command.extend([host, remote_cmd])
    return command


def _append_remote(remote: str, line: str, *, port: str = "") -> str:
    command = _build_remote_append_command(remote, line, port=port)
    subprocess.run(command, input=line + "\n", text=True, check=True)
    return remote


def main() -> int:
    args = _parse_args()
    if not args.remote.strip() and not args.output.strip():
        raise SystemExit("Either --remote or --output is required")

    latest_line, payload = _load_latest_anchor_line(Path(args.input))
    shipped_to: list[str] = []
    if args.output.strip():
        shipped_to.append(_append_local(Path(args.output), latest_line))
    if args.remote.strip():
        shipped_to.append(_append_remote(args.remote, latest_line, port=args.remote_port))

    print(
        json.dumps(
            {
                "audit_event_id": payload["audit_event_id"],
                "event_hash": payload["event_hash"],
                "shipped_to": shipped_to,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
