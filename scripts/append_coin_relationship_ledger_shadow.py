#!/usr/bin/env python3
"""Append privacy-minimized generated coin labels to a durable Shadow ledger."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.relationship_ledger import append_labels


def _outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("relationship_ledger_runtime_path_inside_repository")
    return resolved


def _load(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--retention-days", type=int, default=180)
    args = parser.parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    if args.retention_days <= 0:
        raise SystemExit("--retention-days must be positive")
    input_path = _outside_repository(args.input)
    ledger_path = _outside_repository(args.ledger)
    report_path = _outside_repository(args.report)
    if not input_path.exists():
        raise SystemExit("--input does not exist")
    result = append_labels(
        ledger_path,
        _load(input_path),
        retention_days=args.retention_days,
    )
    result.update(
        {
            "status": "SHADOW_LEDGER_UPDATED",
            "input_path": str(input_path),
            "ledger_path": str(ledger_path),
            "automatic_promotion": False,
        }
    )
    _write_json_atomic(report_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
