#!/usr/bin/env python3
"""Build, compare and verify redacted Stage 12 shadow parity evidence."""

from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import stat
import sys
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.shadow_parity import (
    ShadowParityError,
    build_lane_evidence_from_market_store,
    compare_shadow_lanes,
    sign_parity_report,
    verify_parity_report,
    write_private_json,
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ShadowParityError("parity_cli_timestamp_timezone_required")
    return parsed


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _secret(path: Path) -> bytes:
    info = path.stat()
    mode = stat.S_IMODE(info.st_mode)
    if mode not in {0o400, 0o440, 0o600, 0o640} or mode & 0o007:
        raise ShadowParityError("parity_signing_key_permissions_invalid")
    value = path.read_bytes().strip()
    if len(value) < 32:
        raise ShadowParityError("parity_signing_key_too_short")
    return value


def _snapshot_times(path: Path | None) -> dict[str, datetime]:
    if path is None:
        return {}
    document = _json(path)
    if not isinstance(document, dict):
        raise ShadowParityError("snapshot_timeline_object_required")
    return {str(key): _time(str(value)) for key, value in document.items()}


def command_build(args: argparse.Namespace) -> dict[str, Any]:
    capture = _json(args.capture_manifest) if args.capture_manifest else None
    if capture is not None and not isinstance(capture, list):
        raise ShadowParityError("capture_manifest_array_required")
    lane = build_lane_evidence_from_market_store(
        market_store_path=args.market_store,
        lane=args.lane,
        window_start_utc=_time(args.window_start),
        window_end_utc=_time(args.window_end),
        model_artifact_hash=_hash_file(args.model_artifact),
        capture_manifest=capture,
        snapshot_times=_snapshot_times(args.snapshot_timeline),
    )
    document = lane.model_dump(mode="json")
    write_private_json(args.output, document)
    return {
        "status": "pass",
        "lane": lane.lane,
        "capture_count": len(lane.captures),
        "fact_count": len(lane.facts),
        "feature_count": len(lane.features),
        "estimate_count": len(lane.estimates),
        "capture_manifest_complete": lane.capture_manifest_complete,
        "cutover_performed": False,
    }


def command_compare(args: argparse.Namespace) -> dict[str, Any]:
    labels = _json(args.labels) if args.labels else []
    if not isinstance(labels, list):
        raise ShadowParityError("parity_labels_array_required")
    report = compare_shadow_lanes(
        _json(args.legacy),
        _json(args.private),
        soak_value=_json(args.soak),
        labels_value=labels,
    )
    signed = sign_parity_report(
        report,
        key=_secret(args.signing_key),
        key_id=args.signing_key_id,
    )
    write_private_json(args.output, signed)
    return {
        "status": "pass",
        "report_hash": signed["report_hash"],
        "severity_1_count": signed["severity_1_count"],
        "severity_2_count": signed["severity_2_count"],
        "promotion_recommendation": signed["promotion_recommendation"],
        "cutover_performed": False,
    }


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    document = _json(args.report)
    valid = verify_parity_report(document, key=_secret(args.signing_key))
    if not valid:
        raise ShadowParityError("parity_report_signature_invalid")
    return {
        "status": "pass",
        "report_hash": document.get("report_hash"),
        "signature_key_id": document.get("signature_key_id"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--market-store", type=Path, required=True)
    build.add_argument("--lane", choices=("LEGACY", "PRIVATE_SHADOW"), required=True)
    build.add_argument("--window-start", required=True)
    build.add_argument("--window-end", required=True)
    build.add_argument("--model-artifact", type=Path, required=True)
    build.add_argument("--capture-manifest", type=Path)
    build.add_argument("--snapshot-timeline", type=Path)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--acknowledge-no-cutover", action="store_true", required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("--legacy", type=Path, required=True)
    compare.add_argument("--private", type=Path, required=True)
    compare.add_argument("--soak", type=Path, required=True)
    compare.add_argument("--labels", type=Path)
    compare.add_argument("--signing-key", type=Path, required=True)
    compare.add_argument("--signing-key-id", required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--acknowledge-no-cutover", action="store_true", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--signing-key", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            result = command_build(args)
        elif args.command == "compare":
            result = command_compare(args)
        else:
            result = command_verify(args)
    except (OSError, ValueError, ShadowParityError) as exc:
        print(json.dumps({"status": "fail", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
