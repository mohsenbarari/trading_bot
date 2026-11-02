#!/usr/bin/env python3
"""Write or verify deterministic JSON Schemas for the private market lane."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.private_pipeline_contracts import exported_schemas


DEFAULT_OUTPUT = REPO_ROOT / "contracts" / "market_data"


def render(schema: dict[str, object]) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def run(output: Path, *, write: bool) -> int:
    mismatches: list[str] = []
    for name, schema in sorted(exported_schemas().items()):
        path = output / name
        expected = render(schema)
        if path.exists() and path.read_text(encoding="utf-8") == expected:
            continue
        mismatches.append(name)
        if write:
            output.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    if mismatches and not write:
        print("schema_mismatch=" + ",".join(mismatches), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "written" if write else "current",
                "schema_count": len(exported_schemas()),
                "changed_count": len(mismatches),
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify current schemas (the default; provided for explicit CI use)",
    )
    args = parser.parse_args(argv)
    return run(args.output.resolve(), write=args.write)


if __name__ == "__main__":
    raise SystemExit(main())
