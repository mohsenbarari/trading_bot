#!/usr/bin/env python3
"""Export audit logs from a local Loki instance as JSONL."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export audit logs from Loki query_range.")
    parser.add_argument("--loki-url", default="http://127.0.0.1:3100", help="Base Loki URL.")
    parser.add_argument("--query", default='{log_class="audit"}', help="LogQL stream query.")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum rows returned by Loki.")
    parser.add_argument("--output", default="", help="Output JSONL path.")
    return parser.parse_args()


def _query_loki(args: argparse.Namespace) -> dict[str, Any]:
    end_ns = int(time.time() * 1_000_000_000)
    start_ns = end_ns - int(args.hours * 3600 * 1_000_000_000)
    params = urllib.parse.urlencode(
        {
            "query": args.query,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(args.limit),
            "direction": "backward",
        }
    )
    url = f"{args.loki_url.rstrip('/')}/loki/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _default_output_path() -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return Path("tmp") / "audit-log-exports" / f"audit-logs-{stamp}.jsonl"


def main() -> int:
    args = _parse_args()
    payload = _query_loki(args)
    output = Path(args.output) if args.output else _default_output_path()
    output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for stream in payload.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for timestamp_ns, line in stream.get("values", []):
                record = {"timestamp_ns": timestamp_ns, "labels": labels, "line": line}
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                count += 1

    print(json.dumps({"output": str(output), "records": count}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
