#!/usr/bin/env python3
"""Run one deterministic, module-level shard of repository unittest discovery."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence
import unittest


SHARD_SCHEMA = "repository-unittest-shard-v1"


class UnittestShardError(RuntimeError):
    """A discovered unittest inventory cannot be partitioned safely."""


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise UnittestShardError("shard manifest is not canonical JSON") from exc


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def flatten_suite(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten_suite(item)
        elif isinstance(item, unittest.TestCase):
            yield item
        else:
            raise UnittestShardError("unittest discovery returned an unsupported suite item")


def module_name(test: unittest.TestCase) -> str:
    value = getattr(test.__class__, "__module__", None)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise UnittestShardError("discovered test has an invalid module name")
    return value


def test_record(test: unittest.TestCase) -> dict[str, str]:
    identifier = test.id()
    if not isinstance(identifier, str) or not identifier or "\x00" in identifier:
        raise UnittestShardError("discovered test has an invalid identifier")
    return {"id": identifier, "module": module_name(test)}


def shard_index_for_module(module: str, shard_count: int) -> int:
    if not isinstance(module, str) or not module:
        raise UnittestShardError("test module is invalid")
    if type(shard_count) is not int or shard_count < 1:
        raise UnittestShardError("shard count must be a positive integer")
    digest = hashlib.sha256(module.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def select_shard(
    tests: Sequence[unittest.TestCase],
    *,
    shard_index: int,
    shard_count: int,
) -> list[unittest.TestCase]:
    if type(shard_index) is not int or not 0 <= shard_index < shard_count:
        raise UnittestShardError("shard index is outside the shard count")
    return [
        test
        for test in tests
        if shard_index_for_module(module_name(test), shard_count) == shard_index
    ]


def build_manifest(
    tests: Sequence[unittest.TestCase],
    selected: Sequence[unittest.TestCase],
    *,
    shard_index: int,
    shard_count: int,
    result: unittest.TestResult,
) -> dict[str, object]:
    records = [test_record(test) for test in tests]
    identifiers = [record["id"] for record in records]
    if len(set(identifiers)) != len(identifiers):
        raise UnittestShardError("unittest discovery returned duplicate test identifiers")
    selected_identifiers = [test.id() for test in selected]
    expected_selected = [
        record["id"]
        for record in records
        if shard_index_for_module(record["module"], shard_count) == shard_index
    ]
    if selected_identifiers != expected_selected:
        raise UnittestShardError("selected tests do not match the deterministic shard")
    return {
        "schema": SHARD_SCHEMA,
        "shard_count": shard_count,
        "shard_index": shard_index,
        "tests": records,
        "tests_sha256": sha256_hex(canonical_json_bytes(records)),
        "selected_test_ids": selected_identifiers,
        "selected_test_ids_sha256": sha256_hex(canonical_json_bytes(selected_identifiers)),
        "result": {
            "errors": len(result.errors),
            "failures": len(result.failures),
            "skipped": len(result.skipped),
            "successful": result.wasSuccessful(),
            "tests_run": result.testsRun,
            "unexpected_successes": len(result.unexpectedSuccesses),
        },
    }


def write_manifest(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(document))


def discover_tests(start_directory: Path, pattern: str) -> list[unittest.TestCase]:
    if not start_directory.is_dir():
        raise UnittestShardError("unittest start directory does not exist")
    repository_root = str(Path(__file__).resolve().parents[1])
    if repository_root not in sys.path:
        # Match ``python -m unittest discover``: project imports remain
        # available even though this runner itself lives under ``scripts/``.
        sys.path.insert(0, repository_root)
    try:
        suite = unittest.TestLoader().discover(
            start_dir=str(start_directory),
            pattern=pattern,
        )
    except (AssertionError, ImportError, OSError) as exc:
        raise UnittestShardError("unittest discovery failed") from exc
    tests = list(flatten_suite(suite))
    if not tests:
        raise UnittestShardError("unittest discovery returned no tests")
    return tests


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--shard-index", required=True, type=int)
    parser.add_argument("--shard-count", required=True, type=int)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--verbosity", choices=(0, 1, 2), default=2, type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        # Nested CLI tests must not inherit the runner's shard arguments.
        sys.argv = [sys.argv[0]]
        tests = discover_tests(Path(args.start_directory), args.pattern)
        selected = select_shard(
            tests,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
        )
        if not selected:
            raise UnittestShardError("deterministic shard selected no tests")
        result = unittest.TextTestRunner(verbosity=args.verbosity).run(
            unittest.TestSuite(selected)
        )
        manifest = build_manifest(
            tests,
            selected,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
            result=result,
        )
        write_manifest(Path(args.manifest), manifest)
    except UnittestShardError as exc:
        print(f"repository unittest shard refused: {exc}", file=sys.stderr)
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
