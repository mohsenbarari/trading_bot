#!/usr/bin/env python3
"""Verify that CI unittest shard manifests cover one exact test inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence


SHARD_SCHEMA = "repository-unittest-shard-v1"
VERIFICATION_SCHEMA = "repository-unittest-shard-verification-v1"
MANIFEST_RE = re.compile(r"^backend-shard-([0-9]+)\.json$", re.ASCII)
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "shard_count",
        "shard_index",
        "tests",
        "tests_sha256",
        "selected_test_ids",
        "selected_test_ids_sha256",
        "result",
    }
)
RESULT_FIELDS = frozenset(
    {
        "errors",
        "failures",
        "skipped",
        "successful",
        "tests_run",
        "unexpected_successes",
    }
)


class ShardVerificationError(RuntimeError):
    """A CI shard manifest is absent, malformed, or incomplete."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ShardVerificationError("shard manifest has duplicate fields")
        result[key] = value
    return result


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
        raise ShardVerificationError("shard manifest cannot be canonicalized") from exc


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _strict_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {item}")),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ShardVerificationError("shard manifest is not strict ASCII JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ShardVerificationError("shard manifest is not canonical JSON")
    return value


def _require_integer(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ShardVerificationError(f"{label} is invalid")
    return value


def _require_records(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ShardVerificationError("shard manifest test inventory is invalid")
    records: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "module"}
            or not isinstance(item["id"], str)
            or not item["id"]
            or not isinstance(item["module"], str)
            or not item["module"]
            or "\x00" in item["id"]
            or "\x00" in item["module"]
        ):
            raise ShardVerificationError("shard manifest test inventory is invalid")
        records.append({"id": item["id"], "module": item["module"]})
    identifiers = [record["id"] for record in records]
    if len(set(identifiers)) != len(identifiers):
        raise ShardVerificationError("shard manifest test inventory has duplicate IDs")
    return records


def _shard_index(module: str, shard_count: int) -> int:
    digest = hashlib.sha256(module.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def parse_manifest(path: Path, *, expected_index: int, expected_count: int) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ShardVerificationError(f"cannot read shard manifest {path.name}") from exc
    document = _strict_json(raw)
    if set(document) != MANIFEST_FIELDS or document.get("schema") != SHARD_SCHEMA:
        raise ShardVerificationError(f"shard manifest {path.name} schema differs")
    if document.get("shard_count") != expected_count or document.get("shard_index") != expected_index:
        raise ShardVerificationError(f"shard manifest {path.name} binding differs")
    records = _require_records(document.get("tests"))
    if document.get("tests_sha256") != sha256_hex(records):
        raise ShardVerificationError(f"shard manifest {path.name} inventory digest differs")
    selected = document.get("selected_test_ids")
    if not isinstance(selected, list) or any(not isinstance(item, str) or not item for item in selected):
        raise ShardVerificationError(f"shard manifest {path.name} selected tests differ")
    if len(set(selected)) != len(selected) or document.get("selected_test_ids_sha256") != sha256_hex(selected):
        raise ShardVerificationError(f"shard manifest {path.name} selected digest differs")
    expected_selected = [
        record["id"]
        for record in records
        if _shard_index(record["module"], expected_count) == expected_index
    ]
    if selected != expected_selected:
        raise ShardVerificationError(f"shard manifest {path.name} selected membership differs")
    result = document.get("result")
    if not isinstance(result, dict) or set(result) != RESULT_FIELDS:
        raise ShardVerificationError(f"shard manifest {path.name} result differs")
    for key in RESULT_FIELDS - {"successful"}:
        _require_integer(result.get(key), label=f"shard manifest {path.name} result {key}")
    if type(result.get("successful")) is not bool or result["tests_run"] != len(selected):
        raise ShardVerificationError(f"shard manifest {path.name} result differs")
    if not result["successful"]:
        raise ShardVerificationError(f"shard manifest {path.name} test execution failed")
    return {"records": records, "selected": selected}


def verify_manifest_directory(directory: Path, *, shard_count: int) -> dict[str, object]:
    if type(shard_count) is not int or shard_count < 1:
        raise ShardVerificationError("expected shard count is invalid")
    candidates: dict[int, Path] = {}
    try:
        paths = list(directory.iterdir())
    except OSError as exc:
        raise ShardVerificationError("cannot enumerate shard manifest directory") from exc
    for path in paths:
        match = MANIFEST_RE.fullmatch(path.name)
        if match is None:
            continue
        index = int(match.group(1))
        candidates[index] = path
    if set(candidates) != set(range(shard_count)):
        raise ShardVerificationError("shard manifest set is incomplete")
    parsed = [
        parse_manifest(candidates[index], expected_index=index, expected_count=shard_count)
        for index in range(shard_count)
    ]
    records = parsed[0]["records"]
    for item in parsed[1:]:
        if item["records"] != records:
            raise ShardVerificationError("shard test inventories differ")
    all_selected: list[str] = []
    for item in parsed:
        selected = item["selected"]
        all_selected.extend(selected)
    expected_ids = [record["id"] for record in records]
    if len(set(all_selected)) != len(all_selected) or set(all_selected) != set(expected_ids):
        raise ShardVerificationError("shard test union differs from the full inventory")
    return {
        "schema": VERIFICATION_SCHEMA,
        "passed": True,
        "shard_count": shard_count,
        "test_count": len(expected_ids),
        "test_inventory_sha256": sha256_hex(records),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-directory", required=True)
    parser.add_argument("--shard-count", required=True, type=int)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify_manifest_directory(
            Path(args.manifest_directory), shard_count=args.shard_count
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical_json_bytes(result))
    except ShardVerificationError as exc:
        print(f"repository unittest shard verification refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
