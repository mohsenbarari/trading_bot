#!/usr/bin/env python3
"""Export the current durable audit-trail head as a compact external anchor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the current durable audit-trail head as a compact JSONL anchor.")
    parser.add_argument("--input", default=os.getenv("AUDIT_TRAIL_PATH", ""), help="Durable audit trail JSONL path.")
    parser.add_argument("--output", default="-", help="Output JSONL path, or '-' for stdout.")
    parser.add_argument("--release-id", default=os.getenv("TRADING_BOT_RELEASE_ID", ""), help="Release identifier.")
    parser.add_argument("--host-id", default=os.getenv("TRADING_BOT_HOST_ID", ""), help="Stable host identifier.")
    parser.add_argument("--source-name", default=os.getenv("TRADING_BOT_SERVER_MODE", "unknown"), help="Source/server mode label.")
    return parser.parse_args()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_record_without_event_hash(record: dict[str, Any]) -> str:
    candidate = dict(record)
    candidate.pop("event_hash", None)
    return hashlib.sha256(_canonical_json(candidate).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_host_id() -> str:
    return socket.getfqdn() or socket.gethostname() or "unknown-host"


def load_and_verify_head(input_path: Path) -> dict[str, Any]:
    if not input_path.exists():
        raise FileNotFoundError(f"Audit trail does not exist: {input_path}")
    if input_path.stat().st_size <= 0:
        raise ValueError(f"Audit trail is empty: {input_path}")

    previous_hash: str | None = None
    last_record: dict[str, Any] | None = None
    records = 0
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed audit JSON at line {line_number}: {exc}") from exc
            if record.get("previous_hash") != previous_hash:
                raise ValueError(f"Audit chain previous_hash mismatch at line {line_number}")
            if record.get("event_hash") != _hash_record_without_event_hash(record):
                raise ValueError(f"Audit chain event_hash mismatch at line {line_number}")
            previous_hash = record.get("event_hash")
            last_record = record
            records += 1

    if last_record is None:
        raise ValueError(f"Audit trail has no records: {input_path}")

    return {
        "records": records,
        "head": last_record,
        "trail_sha256": _sha256_file(input_path),
        "input_path": str(input_path),
    }


def build_anchor_record(*, trail_summary: dict[str, Any], release_id: str, host_id: str, source_name: str) -> dict[str, Any]:
    head = trail_summary["head"]
    return {
        "anchor_exported_at": _utc_now(),
        "source_name": source_name or "unknown",
        "host_id": host_id or _default_host_id(),
        "release_id": release_id or "unknown",
        "audit_event_id": head.get("audit_event_id"),
        "audit_recorded_at": head.get("audit_recorded_at"),
        "event_hash": head.get("event_hash"),
        "previous_hash": head.get("previous_hash"),
        "audit_trail_records": trail_summary["records"],
        "audit_trail_sha256": trail_summary["trail_sha256"],
        "audit_trail_path": trail_summary["input_path"],
        "audit_durable": bool(head.get("audit_durable")),
    }


def write_anchor_record(anchor_record: dict[str, Any], output_path: str) -> str:
    line = json.dumps(anchor_record, ensure_ascii=False, sort_keys=True)
    if output_path == "-":
        sys.stdout.write(line + "\n")
        return output_path
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return str(path)


def main() -> int:
    args = _parse_args()
    if not args.input.strip():
        raise SystemExit("AUDIT_TRAIL_PATH or --input is required")

    trail_summary = load_and_verify_head(Path(args.input))
    anchor_record = build_anchor_record(
        trail_summary=trail_summary,
        release_id=args.release_id,
        host_id=args.host_id,
        source_name=args.source_name,
    )
    destination = write_anchor_record(anchor_record, args.output)
    if args.output != "-":
        sys.stdout.write(
            json.dumps(
                {
                    "output": destination,
                    "audit_event_id": anchor_record["audit_event_id"],
                    "event_hash": anchor_record["event_hash"],
                    "audit_trail_records": anchor_record["audit_trail_records"],
                    "source_name": anchor_record["source_name"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
