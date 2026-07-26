#!/usr/bin/env python3
"""Build the immutable Writer-transition schedule for one Full Matrix class."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes  # noqa: E402
from core.dr_full_matrix_failover_schedule import (  # noqa: E402
    build_schedule,
)
from core.secure_file_io import write_secure_atomic_bytes  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--gate-group-id", required=True)
    parser.add_argument("--execution-class", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        payload = build_schedule(
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            execution_class=args.execution_class,
            release_sha=args.release_sha,
        )
        raw = canonical_json_bytes(payload) + b"\n"
        write_secure_atomic_bytes(
            args.output,
            raw,
            label="Full Matrix failover schedule",
            mode=0o600,
            max_size=4 * 1024 * 1024,
        )
        print(
            json.dumps(
                {
                    "status": "built",
                    "output": str(args.output),
                    "entry_count": len(payload["entries"]),
                    "sha256": hashlib.sha256(
                        canonical_json_bytes(payload)
                    ).hexdigest(),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
