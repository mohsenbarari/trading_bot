#!/usr/bin/env python3
"""Dry-run-first upload/verification of a versioned off-host audit anchor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.config import settings
from core.dr_audit_anchor import (
    DrAuditAnchorError,
    build_audit_anchor,
    store_audit_anchor,
    verify_audit_anchor,
)
from core.dr_blob_worker import DrBlobWorkerError, load_s3_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("anchor", "verify"))
    parser.add_argument("--audit-log", required=True, type=Path)
    parser.add_argument("--site", required=True)
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--version-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    try:
        payload, key = build_audit_anchor(
            args.audit_log,
            site=args.site,
            log_id=args.log_id,
            release_sha=str(settings.release_sha or "").strip().lower(),
        )
        confirmation = f"anchor:{args.site}:{args.log_id}:{payload['tail_event_hash']}"
        if not args.apply:
            result = {
                "status": "planned",
                "object_key": key,
                "record_count": payload["record_count"],
                "tail_event_hash": payload["tail_event_hash"],
                "required_confirmation": confirmation,
            }
        else:
            if args.confirm != confirmation:
                raise DrAuditAnchorError("audit anchor confirmation mismatch")
            config = load_s3_config()
            if args.action == "anchor":
                result = store_audit_anchor(config, payload, key)
            else:
                result = verify_audit_anchor(
                    config,
                    expected_payload=payload,
                    key=key,
                    version_id=str(args.version_id or ""),
                )
    except (DrAuditAnchorError, DrBlobWorkerError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
