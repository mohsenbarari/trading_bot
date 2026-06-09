#!/usr/bin/env python3
"""Export audit logs from Loki or the durable audit trail as JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export audit logs from Loki or a durable audit-trail JSONL file.")
    parser.add_argument("--source", choices=["loki", "file"], default="loki", help="Audit source.")
    parser.add_argument("--loki-url", default="http://127.0.0.1:3100", help="Base Loki URL.")
    parser.add_argument("--query", default='{log_class="audit"}', help="LogQL stream query.")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum rows exported.")
    parser.add_argument("--page-size", type=int, default=5000, help="Loki query_range page size.")
    parser.add_argument("--input", default="", help="Input audit-trail JSONL path when --source=file.")
    parser.add_argument("--output", default="", help="Output JSONL path.")
    parser.add_argument("--manifest-output", default="", help="Output manifest JSON path.")
    return parser.parse_args()


def _query_loki(args: argparse.Namespace, *, start_ns: int, end_ns: int, limit: int) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "query": args.query,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(limit),
            "direction": "backward",
        }
    )
    url = f"{args.loki_url.rstrip('/')}/loki/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _default_output_path() -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return Path("tmp") / "audit-log-exports" / f"audit-logs-{stamp}.jsonl"


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_record_hash(record: dict[str, Any]) -> str:
    candidate = dict(record)
    candidate.pop("event_hash", None)
    return hashlib.sha256(_canonical_json(candidate).encode("utf-8")).hexdigest()


def _verify_audit_chain(input_path: Path) -> dict[str, Any]:
    previous_hash: str | None = None
    records = 0
    first_event_id: str | None = None
    last_event_id: str | None = None
    for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        records += 1
        event_id = record.get("audit_event_id")
        if first_event_id is None and event_id:
            first_event_id = str(event_id)
        if event_id:
            last_event_id = str(event_id)
        if record.get("previous_hash") != previous_hash:
            return {"integrity_ok": False, "records": records, "error": "previous_hash_mismatch", "line": line_number}
        if record.get("event_hash") != _audit_record_hash(record):
            return {"integrity_ok": False, "records": records, "error": "event_hash_mismatch", "line": line_number}
        previous_hash = record.get("event_hash")
    return {
        "integrity_ok": True,
        "records": records,
        "first_event_id": first_event_id,
        "last_event_id": last_event_id,
        "last_event_hash": previous_hash,
    }


def _iter_loki_records(args: argparse.Namespace):
    now_ns = int(time.time() * 1_000_000_000)
    start_ns = now_ns - int(args.hours * 3600 * 1_000_000_000)
    end_ns = now_ns
    emitted = 0
    seen_oldest: int | None = None
    while emitted < args.limit and end_ns >= start_ns:
        page_limit = max(1, min(args.page_size, args.limit - emitted))
        payload = _query_loki(args, start_ns=start_ns, end_ns=end_ns, limit=page_limit)
        values: list[tuple[dict[str, Any], str, str]] = []
        for stream in payload.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for timestamp_ns, line in stream.get("values", []):
                values.append((labels, timestamp_ns, line))
        if not values:
            break
        oldest = min(int(timestamp_ns) for _, timestamp_ns, _ in values)
        if seen_oldest is not None and oldest >= seen_oldest:
            break
        seen_oldest = oldest
        for labels, timestamp_ns, line in values:
            yield {"timestamp_ns": timestamp_ns, "labels": labels, "line": line}
            emitted += 1
            if emitted >= args.limit:
                break
        if len(values) < page_limit:
            break
        end_ns = oldest - 1


def _copy_file_source(args: argparse.Namespace, output: Path) -> dict[str, Any]:
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Audit input file does not exist: {input_path}")
    integrity = _verify_audit_chain(input_path)
    count = 0
    with input_path.open("r", encoding="utf-8") as source, output.open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            target.write(line)
            count += 1
            if count >= args.limit:
                break
    integrity["records_exported"] = count
    return integrity


def _write_manifest(
    *,
    args: argparse.Namespace,
    output: Path,
    manifest_path: Path,
    records: int,
    integrity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": args.source,
        "output": str(output),
        "output_sha256": _sha256_file(output),
        "records": records,
        "limit": args.limit,
    }
    if args.source == "loki":
        manifest.update({"loki_url": args.loki_url, "query": args.query, "hours": args.hours, "page_size": args.page_size})
    if args.source == "file":
        manifest["input"] = args.input
    if integrity:
        manifest["integrity"] = integrity
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    args = _parse_args()
    output = Path(args.output) if args.output else _default_output_path()
    manifest_output = Path(args.manifest_output) if args.manifest_output else output.with_suffix(output.suffix + ".manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    integrity: dict[str, Any] | None = None
    if args.source == "file":
        integrity = _copy_file_source(args, output)
        count = int(integrity["records_exported"])
    else:
        with output.open("w", encoding="utf-8") as handle:
            for record in _iter_loki_records(args):
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                count += 1
    manifest = _write_manifest(args=args, output=output, manifest_path=manifest_output, records=count, integrity=integrity)

    print(json.dumps({"manifest": str(manifest_output), "output": str(output), "records": count, "source": args.source, "sha256": manifest["output_sha256"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
