#!/usr/bin/env python3
"""Generate or verify the deterministic three-site DR data matrix."""

from __future__ import annotations

import argparse
from pathlib import Path

from core.dr_data_matrix import render_three_site_data_matrix


DEFAULT_OUTPUT = Path("docs/three-site-dr-data-classification.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_three_site_data_matrix()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("three-site DR data matrix is stale; regenerate it")
        print(f"verified {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
