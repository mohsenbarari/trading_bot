#!/usr/bin/env python3
"""Fail a required unittest gate when its retained log reports skipped tests."""
from __future__ import annotations

import argparse
from pathlib import Path
import re


SKIPPED_PATTERN = re.compile(r"(?:skipped=|\bskipped\s+)([1-9]\d*)")


def skipped_test_count(output: str) -> int:
    return sum(int(match.group(1)) for match in SKIPPED_PATTERN.finditer(output))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log_path")
    args = parser.parse_args(argv)
    path = Path(args.log_path)
    if not path.is_file():
        print(f"required unittest log is missing: {path}")
        return 2
    skipped = skipped_test_count(path.read_text(encoding="utf-8", errors="replace"))
    if skipped:
        print(f"required unittest proof skipped {skipped} test(s): {path}")
        return 2
    print(f"required unittest proof has zero skips: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
