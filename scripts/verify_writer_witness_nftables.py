#!/usr/bin/env python3
"""Attest the complete effective nftables policy of the dedicated Writer Witness.

The caller supplies the JSON emitted by ``nft -j list ruleset`` on standard
input.  Runtime-only handles, metainfo, and anonymous-counter totals are
normalized before the policy is hashed; every policy-bearing field and its
position remain bound by the expected digest.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import re
import sys
from typing import BinaryIO, Iterable, Mapping, Sequence, TextIO


MAXIMUM_INPUT_BYTES = 8 * 1024 * 1024
MAXIMUM_JSON_DEPTH = 64
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_TABLES = frozenset({("ip", "filter"), ("ip6", "filter")})
ENTRY_KINDS = frozenset({"metainfo", "table", "chain", "rule"})
TABLE_KEYS = frozenset({"family", "name", "handle", "flags", "comment"})
CHAIN_KEYS = frozenset(
    {
        "family",
        "table",
        "name",
        "handle",
        "type",
        "hook",
        "prio",
        "policy",
        "dev",
        "devices",
        "flags",
        "comment",
    }
)
RULE_KEYS = frozenset({"family", "table", "chain", "handle", "expr", "comment"})


class NftablesAttestationError(RuntimeError):
    """The supplied ruleset cannot be proven to match the pinned policy."""


@dataclass(frozen=True)
class PolicySnapshot:
    canonical_json: bytes
    policy_sha256: str
    table_count: int
    chain_count: int
    rule_count: int
    expression_count: int
    counter_count: int

    def result(self) -> dict[str, object]:
        return {
            "chain_count": self.chain_count,
            "counter_count": self.counter_count,
            "expression_count": self.expression_count,
            "nftables_policy_attested": "yes",
            "policy_sha256": self.policy_sha256,
            "rule_count": self.rule_count,
            "table_count": self.table_count,
        }


def _unique_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NftablesAttestationError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise NftablesAttestationError(f"non-finite JSON number is forbidden: {value}")


def read_bounded_input(
    stream: BinaryIO, *, maximum_bytes: int = MAXIMUM_INPUT_BYTES
) -> bytes:
    if maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be positive")
    try:
        value = stream.read(maximum_bytes + 1)
    except (OSError, ValueError) as exc:
        raise NftablesAttestationError("cannot safely read nftables JSON from stdin") from exc
    if not isinstance(value, bytes):
        raise NftablesAttestationError("nftables stdin must be a binary byte stream")
    if len(value) > maximum_bytes:
        raise NftablesAttestationError("nftables JSON exceeds its safe size limit")
    if not value:
        raise NftablesAttestationError("nftables JSON input is empty")
    return value


def load_ruleset(value: bytes) -> Mapping[str, object]:
    if len(value) > MAXIMUM_INPUT_BYTES:
        raise NftablesAttestationError("nftables JSON exceeds its safe size limit")
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NftablesAttestationError("nftables JSON is not valid UTF-8") from exc
    if decoded.startswith("\ufeff"):
        raise NftablesAttestationError("nftables JSON must not contain a UTF-8 BOM")
    try:
        payload = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite,
        )
    except NftablesAttestationError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise NftablesAttestationError("nftables input is not valid bounded JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"nftables"}:
        raise NftablesAttestationError(
            "nftables JSON root must contain exactly the nftables array"
        )
    return payload


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if depth > MAXIMUM_JSON_DEPTH:
        raise NftablesAttestationError("nftables JSON exceeds its safe nesting depth")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise NftablesAttestationError("floating-point nftables values are forbidden")
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise NftablesAttestationError("nftables JSON contains an invalid Unicode surrogate")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):  # Defensive: json.loads always gives string keys.
                raise NftablesAttestationError("nftables JSON object keys must be strings")
            _validate_json_value(key, depth=depth + 1)
            _validate_json_value(item, depth=depth + 1)
        return
    raise NftablesAttestationError("nftables JSON contains an unsupported value type")


def _require_nonempty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise NftablesAttestationError(f"{field} must be a non-empty string")
    return value


def _require_nonnegative_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NftablesAttestationError(f"{field} must be a non-negative integer")
    return value


def _validate_optional_string(body: Mapping[str, object], key: str, *, subject: str) -> None:
    if key in body:
        _require_nonempty_string(body[key], field=f"{subject}.{key}")


def _validate_optional_string_list(
    body: Mapping[str, object], key: str, *, subject: str
) -> None:
    if key not in body:
        return
    value = body[key]
    if not isinstance(value, list):
        raise NftablesAttestationError(f"{subject}.{key} must be an array")
    for index, item in enumerate(value):
        _require_nonempty_string(item, field=f"{subject}.{key}[{index}]")


def _validate_allowed_keys(
    body: Mapping[str, object], allowed: frozenset[str], *, subject: str
) -> None:
    unexpected = sorted(set(body) - allowed)
    if unexpected:
        raise NftablesAttestationError(
            f"{subject} contains unexpected fields: {', '.join(unexpected)}"
        )


def _table_identity(body: Mapping[str, object], *, subject: str) -> tuple[str, str]:
    family = _require_nonempty_string(body.get("family"), field=f"{subject}.family")
    table = _require_nonempty_string(
        body.get("table", body.get("name")), field=f"{subject}.table"
    )
    return family, table


def _normalize_counter_payload(value: object) -> tuple[object, int]:
    if value is None:
        return None, 1
    if not isinstance(value, dict):
        raise NftablesAttestationError("counter expression must be null or an object")
    result: dict[str, object] = {}
    observed_runtime_total = False
    for key, item in value.items():
        if key in {"packets", "bytes"}:
            _require_nonnegative_integer(item, field=f"counter.{key}")
            result[key] = 0
            observed_runtime_total = True
        else:
            normalized, _ = _normalize_json_value(item)
            result[key] = normalized
    if observed_runtime_total and not {"packets", "bytes"}.issubset(value):
        raise NftablesAttestationError(
            "counter runtime totals must contain both packets and bytes"
        )
    return result, 1


def _normalize_json_value(value: object) -> tuple[object, int]:
    """Normalize only anonymous counter totals; preserve all other JSON exactly."""
    if isinstance(value, list):
        normalized: list[object] = []
        counter_count = 0
        for item in value:
            normalized_item, item_counters = _normalize_json_value(item)
            normalized.append(normalized_item)
            counter_count += item_counters
        return normalized, counter_count
    if isinstance(value, dict):
        normalized_object: dict[str, object] = {}
        counter_count = 0
        for key, item in value.items():
            if key == "counter":
                normalized_item, item_counters = _normalize_counter_payload(item)
            else:
                normalized_item, item_counters = _normalize_json_value(item)
            normalized_object[key] = normalized_item
            counter_count += item_counters
        return normalized_object, counter_count
    return value, 0


def _normalize_entry_body(body: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in body.items() if key != "handle"}


def inspect_policy(payload: Mapping[str, object]) -> PolicySnapshot:
    _validate_json_value(payload)
    entries = payload.get("nftables")
    if not isinstance(entries, list) or not entries:
        raise NftablesAttestationError("nftables must be a non-empty array")

    normalized_entries: list[dict[str, object]] = []
    declared_tables: set[tuple[str, str]] = set()
    declared_chains: set[tuple[str, str, str]] = set()
    referenced_tables: set[tuple[str, str]] = set()
    rules_per_table: dict[tuple[str, str], int] = {}
    table_count = chain_count = rule_count = expression_count = counter_count = 0
    metainfo_count = 0

    for index, entry in enumerate(entries):
        subject = f"nftables[{index}]"
        if not isinstance(entry, dict) or len(entry) != 1:
            raise NftablesAttestationError(
                f"{subject} must be a single-key nftables object"
            )
        kind, raw_body = next(iter(entry.items()))
        if kind not in ENTRY_KINDS:
            raise NftablesAttestationError(f"{subject} has unexpected object form: {kind}")
        if not isinstance(raw_body, dict):
            raise NftablesAttestationError(f"{subject}.{kind} must be an object")

        if kind == "metainfo":
            metainfo_count += 1
            if index != 0 or metainfo_count != 1:
                raise NftablesAttestationError(
                    "nftables metainfo must appear exactly once as the first entry"
                )
            # nft versions add runtime/build fields here.  The complete object is
            # deliberately ignored, but it still had to be valid JSON above.
            continue
        if metainfo_count != 1:
            raise NftablesAttestationError(
                "nftables metainfo must appear exactly once as the first entry"
            )

        if "handle" in raw_body:
            _require_nonnegative_integer(raw_body["handle"], field=f"{subject}.{kind}.handle")

        if kind == "table":
            _validate_allowed_keys(raw_body, TABLE_KEYS, subject=f"{subject}.table")
            identity = _table_identity(raw_body, subject=f"{subject}.table")
            if identity in declared_tables:
                raise NftablesAttestationError("duplicate nftables table declaration")
            declared_tables.add(identity)
            table_count += 1
            _validate_optional_string(raw_body, "comment", subject=f"{subject}.table")
            _validate_optional_string_list(raw_body, "flags", subject=f"{subject}.table")
        elif kind == "chain":
            _validate_allowed_keys(raw_body, CHAIN_KEYS, subject=f"{subject}.chain")
            family, table = _table_identity(raw_body, subject=f"{subject}.chain")
            name = _require_nonempty_string(
                raw_body.get("name"), field=f"{subject}.chain.name"
            )
            identity = (family, table, name)
            if identity in declared_chains:
                raise NftablesAttestationError("duplicate nftables chain declaration")
            declared_chains.add(identity)
            referenced_tables.add((family, table))
            chain_count += 1
            for key in ("type", "hook", "policy", "dev", "comment"):
                _validate_optional_string(raw_body, key, subject=f"{subject}.chain")
            for key in ("devices", "flags"):
                _validate_optional_string_list(raw_body, key, subject=f"{subject}.chain")
            if "prio" in raw_body and (
                isinstance(raw_body["prio"], bool) or not isinstance(raw_body["prio"], int)
            ):
                raise NftablesAttestationError(f"{subject}.chain.prio must be an integer")
        else:
            _validate_allowed_keys(raw_body, RULE_KEYS, subject=f"{subject}.rule")
            family, table = _table_identity(raw_body, subject=f"{subject}.rule")
            chain = _require_nonempty_string(
                raw_body.get("chain"), field=f"{subject}.rule.chain"
            )
            referenced_tables.add((family, table))
            expressions = raw_body.get("expr")
            if not isinstance(expressions, list) or not expressions:
                raise NftablesAttestationError(f"{subject}.rule.expr must be a non-empty array")
            for expression_index, expression in enumerate(expressions):
                if not isinstance(expression, dict) or len(expression) != 1:
                    raise NftablesAttestationError(
                        f"{subject}.rule.expr[{expression_index}] must be a single-key expression"
                    )
                expression_name = next(iter(expression))
                _require_nonempty_string(
                    expression_name,
                    field=f"{subject}.rule.expr[{expression_index}] name",
                )
            _validate_optional_string(raw_body, "comment", subject=f"{subject}.rule")
            rule_count += 1
            expression_count += len(expressions)
            rules_per_table[(family, table)] = rules_per_table.get((family, table), 0) + 1

        normalized_body = _normalize_entry_body(raw_body)
        normalized_body, body_counters = _normalize_json_value(normalized_body)
        assert isinstance(normalized_body, dict)
        counter_count += body_counters
        normalized_entries.append({kind: normalized_body})

    if metainfo_count != 1:
        raise NftablesAttestationError(
            "nftables metainfo must appear exactly once as the first entry"
        )
    if declared_tables != EXPECTED_TABLES:
        observed = ", ".join(f"{family} {table}" for family, table in sorted(declared_tables))
        raise NftablesAttestationError(
            "the only declared tables must be exactly ip filter and ip6 filter"
            + (f"; observed: {observed}" if observed else "")
        )
    if not referenced_tables.issubset(declared_tables):
        raise NftablesAttestationError("a chain or rule references an undeclared nftables table")
    for chain in declared_chains:
        if chain[:2] not in declared_tables:
            raise NftablesAttestationError("a chain references an undeclared nftables table")
    for index, entry in enumerate(normalized_entries):
        if "rule" in entry:
            body = entry["rule"]
            assert isinstance(body, dict)
            identity = (body["family"], body["table"], body["chain"])
            if identity not in declared_chains:
                raise NftablesAttestationError(
                    f"normalized rule {index} references an undeclared nftables chain"
                )
    if rule_count == 0:
        raise NftablesAttestationError("nftables policy contains no rules")
    if set(rules_per_table) != EXPECTED_TABLES:
        raise NftablesAttestationError("both dedicated nftables tables must contain rules")

    canonical_json = json.dumps(
        {"nftables": normalized_entries},
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return PolicySnapshot(
        canonical_json=canonical_json,
        policy_sha256=hashlib.sha256(canonical_json).hexdigest(),
        table_count=table_count,
        chain_count=chain_count,
        rule_count=rule_count,
        expression_count=expression_count,
        counter_count=counter_count,
    )


def inspect_ruleset(value: bytes) -> PolicySnapshot:
    return inspect_policy(load_ruleset(value))


def attest_ruleset(value: bytes, expected_policy_sha256: str) -> dict[str, object]:
    if not SHA256_PATTERN.fullmatch(expected_policy_sha256):
        raise NftablesAttestationError(
            "expected policy SHA-256 must be 64 lowercase hex characters"
        )
    snapshot = inspect_ruleset(value)
    if snapshot.policy_sha256 != expected_policy_sha256:
        raise NftablesAttestationError("effective nftables policy digest does not match expected")
    return snapshot.result()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-policy-sha256", required=True)
    return parser.parse_args(argv)


def _binary_stdin(stream: TextIO | BinaryIO) -> BinaryIO:
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        return binary
    if isinstance(stream, BinaryIO):  # pragma: no cover - protocol runtime fallback.
        return stream
    raise NftablesAttestationError("nftables stdin is not available as a binary stream")


def main(argv: Sequence[str] | None = None, *, stdin: BinaryIO | None = None) -> int:
    args = parse_args(argv)
    try:
        value = read_bounded_input(stdin if stdin is not None else _binary_stdin(sys.stdin))
        result = attest_ruleset(value, args.expected_policy_sha256)
    except NftablesAttestationError as exc:
        print(f"nftables policy attestation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
