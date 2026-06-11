#!/usr/bin/env python3
"""Append a non-sensitive audit event for observability readiness checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.audit_logger import audit_log
from core.config import settings
from core.request_context import clear_request_context, set_request_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a non-sensitive observability audit probe event.")
    parser.add_argument("--probe-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    set_request_context(
        request_id=args.probe_id,
        actor_id=0,
        actor_role="system",
        path="/observability/readiness",
        method="PROBE",
    )
    try:
        audit_log(
            "observability_readiness_probe",
            target_type="observability",
            target_id=args.probe_id,
            result="success",
            actor_id=0,
            actor_role="system",
            extra={"probe_id": args.probe_id, "purpose": "p9_readiness"},
        )
    finally:
        clear_request_context()
    print(
        json.dumps(
            {
                "status": "ok",
                "probe_id": args.probe_id,
                "audit_trail_path_configured": bool(getattr(settings, "audit_trail_path", None)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
