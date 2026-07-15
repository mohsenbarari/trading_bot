#!/usr/bin/env python3
"""Fail a required unittest gate when its retained log reports skipped tests."""
from __future__ import annotations

import argparse
from pathlib import Path
import re


SKIPPED_PATTERN = re.compile(r"(?:skipped=|\bskipped\s+)([1-9]\d*)")
TEST_COUNT_PATTERN = re.compile(r"\bRan\s+(\d+)\s+tests?\b")


def skipped_test_count(output: str) -> int:
    return sum(int(match.group(1)) for match in SKIPPED_PATTERN.finditer(output))


def executed_test_count(output: str) -> int | None:
    counts = [int(match.group(1)) for match in TEST_COUNT_PATTERN.finditer(output)]
    return sum(counts) if counts else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log_path")
    args = parser.parse_args(argv)
    path = Path(args.log_path)
    if not path.is_file():
        print(f"required unittest log is missing: {path}")
        return 2
    output = path.read_text(encoding="utf-8", errors="replace")
    executed = executed_test_count(output)
    if executed is None:
        print(f"required unittest proof has no test-count summary: {path}")
        return 2
    if executed <= 0:
        print(f"required unittest proof executed zero tests: {path}")
        return 2
    skipped = skipped_test_count(output)
    if skipped:
        print(f"required unittest proof skipped {skipped} test(s): {path}")
        return 2
    print(f"required unittest proof executed {executed} test(s) with zero skips: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
