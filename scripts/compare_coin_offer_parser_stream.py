#!/usr/bin/env python3
"""Compare production/candidate coin offer parsers over capture JSONL on stdin.

The report is aggregate-only: message text, message IDs, event IDs, sender data,
and per-message hashes are never emitted or persisted.  A candidate passes only
when every production result is economically identical and any added coverage
belongs to an explicitly reviewed regression shape.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import importlib
import json
from pathlib import Path
import re
import sys
import types
from typing import BinaryIO, Sequence


MAX_RECORD_BYTES = 256 * 1024
MAX_RECORDS = 500_000
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_SEVEN_DIGIT = re.compile(r"(?<!\d)(\d{7})(?!\d)")
_LOW_PRICE_FAMILIES = frozenset(
    {"QUARTER_BAHAR", "HALF_BAHAR", "QUARTER_LOW_DATE", "HALF_LOW_DATE", "ONE_GRAM"}
)


class ParserStreamComparisonError(RuntimeError):
    """A content-free refusal or comparison failure."""


@dataclass(frozen=True, slots=True)
class EconomicOffer:
    instrument: str | None
    price: int
    quantity: int
    side: str
    settlement: str
    trade_form: str
    conditional: bool
    quality: str


def _root(value: str, *, field: str) -> Path:
    root = Path(value).expanduser().resolve()
    required = root / "core" / "market_intelligence" / "coin_groups.py"
    if root.is_symlink() or not required.is_file() or required.is_symlink():
        raise ParserStreamComparisonError(f"{field}_invalid")
    return root


def _load_coin_parser(root: Path, *, namespace: str):
    package_path = root / "core" / "market_intelligence"
    package = types.ModuleType(namespace)
    package.__path__ = [str(package_path)]  # type: ignore[attr-defined]
    package.__package__ = namespace
    sys.modules[namespace] = package
    return importlib.import_module(f"{namespace}.coin_groups")


def _economic(values: object) -> tuple[EconomicOffer, ...]:
    return tuple(
        EconomicOffer(
            instrument=item.commodity_code,
            price=int(item.price_project_thousand_toman),
            quantity=int(item.quantity),
            side=str(item.side),
            settlement=str(item.settlement_term),
            trade_form=str(item.trade_form),
            conditional=bool(item.is_conditional),
            quality=str(item.quality_state),
        )
        for item in values  # type: ignore[union-attr]
    )


def classify_change(
    text: str,
    baseline: tuple[EconomicOffer, ...],
    candidate: tuple[EconomicOffer, ...],
    *,
    collapsed_baseline: tuple[EconomicOffer, ...] = (),
    corrected_zero_baselines: Sequence[tuple[EconomicOffer, ...]] = (),
) -> str:
    if baseline == candidate:
        return "EXACT"
    if baseline or not candidate:
        return "UNREVIEWED_ECONOMIC_DRIFT"
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    if len(nonempty_lines) >= 2 and collapsed_baseline == candidate:
        return "REVIEWED_MULTILINE_SINGLE_OFFER_GAIN"
    normalized = text.translate(_DIGITS)
    duplicated_zero = any(
        int(match.group(1)) % 100 == 0 for match in _SEVEN_DIGIT.finditer(normalized)
    )
    if duplicated_zero and candidate in corrected_zero_baselines and all(
        item.instrument in _LOW_PRICE_FAMILIES for item in candidate
    ):
        return "REVIEWED_LOW_PRICE_DUPLICATED_ZERO_GAIN"
    return "UNREVIEWED_COVERAGE_GAIN"


def _event(record: object) -> tuple[int, int, str, str, str, bool] | None:
    if not isinstance(record, dict) or record.get("schema") != "coin_group_event":
        return None
    if str(record.get("schema_version")) not in {"2.0", "2.1"}:
        raise ParserStreamComparisonError("coin_group_schema_unsupported")
    if str(record.get("event_type")) == "message_deleted":
        return None
    source = record.get("source")
    message = record.get("message")
    producer = record.get("producer")
    if not isinstance(source, dict) or not isinstance(message, dict) or not isinstance(producer, dict):
        raise ParserStreamComparisonError("coin_group_event_invalid")
    source_id = str(source.get("source_id") or "")
    if source_id not in {"GROUP_1", "GROUP_2"}:
        raise ParserStreamComparisonError("coin_group_source_invalid")
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        message_id = int(message["message_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParserStreamComparisonError("coin_group_message_id_invalid") from exc
    published = str(message.get("published_at_utc") or "")
    available_value = producer.get("available_at_utc")
    used_available_fallback = not bool(available_value)
    available = str(available_value or record.get("occurred_at_utc") or published or "")
    if not published or not available:
        raise ParserStreamComparisonError("coin_group_timestamp_missing")
    return int(source_id[-1]), message_id, published, available, text, used_available_fallback


def _parse(module, *, group: int, message_id: int, published: str, available: str, text: str):
    return _economic(
        module.parse_coin_group_offers(
            module.CoinGroupMessageInput(
                group_number=group,
                source_event_id=message_id,
                published_at_utc=published,
                available_at_utc=available,
                text=text,
            )
        )
    )


def _single_terminal_zero_corrections(text: str) -> tuple[str, ...]:
    normalized = text.translate(_DIGITS)
    values: list[str] = []
    for match in _SEVEN_DIGIT.finditer(normalized):
        token = match.group(1)
        if int(token) % 100 != 0:
            continue
        values.append(normalized[: match.end() - 1] + normalized[match.end() :])
    return tuple(values)


def _module_sha256(module) -> str:
    return sha256(Path(module.__file__).read_bytes()).hexdigest()


def compare_stream(
    handle: BinaryIO,
    *,
    baseline_module,
    candidate_module,
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    seen_events: set[str] = set()
    for raw in handle:
        counts["records_seen"] += 1
        if counts["records_seen"] > MAX_RECORDS:
            raise ParserStreamComparisonError("record_limit_exceeded")
        if len(raw) > MAX_RECORD_BYTES or not raw.endswith(b"\n"):
            counts["records_rejected"] += 1
            continue
        try:
            record = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            counts["records_rejected"] += 1
            continue
        event_id = str(record.get("event_id") or "") if isinstance(record, dict) else ""
        if event_id and event_id in seen_events:
            counts["duplicate_events"] += 1
            continue
        if event_id:
            seen_events.add(event_id)
        selected = _event(record)
        if selected is None:
            counts["records_ignored"] += 1
            continue
        group, message_id, published, available, text, used_available_fallback = selected
        counts["messages_compared"] += 1
        if used_available_fallback:
            counts["available_timestamp_fallbacks"] += 1
        try:
            baseline = _parse(
                baseline_module,
                group=group,
                message_id=message_id,
                published=published,
                available=available,
                text=text,
            )
            candidate = _parse(
                candidate_module,
                group=group,
                message_id=message_id,
                published=published,
                available=available,
                text=text,
            )
            collapsed_baseline = ()
            if not baseline and candidate and len(text.splitlines()) >= 2:
                collapsed_baseline = _parse(
                    baseline_module,
                    group=group,
                    message_id=message_id,
                    published=published,
                    available=available,
                    text=" ".join(line.strip() for line in text.splitlines() if line.strip()),
                )
            corrected_zero_baselines = tuple(
                _parse(
                    baseline_module,
                    group=group,
                    message_id=message_id,
                    published=published,
                    available=available,
                    text=corrected,
                )
                for corrected in _single_terminal_zero_corrections(text)
            )
        except (TypeError, ValueError) as exc:
            raise ParserStreamComparisonError("parser_execution_failed") from exc
        counts["baseline_offer_candidates"] += len(baseline)
        counts["candidate_offer_candidates"] += len(candidate)
        counts[
            classify_change(
                text,
                baseline,
                candidate,
                collapsed_baseline=collapsed_baseline,
                corrected_zero_baselines=corrected_zero_baselines,
            )
        ] += 1
    compared = counts["messages_compared"]
    exact = counts["EXACT"]
    reviewed_gains = (
        counts["REVIEWED_MULTILINE_SINGLE_OFFER_GAIN"]
        + counts["REVIEWED_LOW_PRICE_DUPLICATED_ZERO_GAIN"]
    )
    unreviewed = counts["UNREVIEWED_ECONOMIC_DRIFT"] + counts["UNREVIEWED_COVERAGE_GAIN"]
    baseline_sha256 = _module_sha256(baseline_module)
    candidate_sha256 = _module_sha256(candidate_module)
    parser_version_collision = (
        baseline_sha256 != candidate_sha256
        and str(baseline_module.COIN_GROUP_PARSER_VERSION)
        == str(candidate_module.COIN_GROUP_PARSER_VERSION)
    )
    status = (
        "PASS"
        if compared > 0
        and unreviewed == 0
        and counts["records_rejected"] == 0
        and not parser_version_collision
        else "FAIL"
    )
    return {
        "schema": "coin_offer_parser_stream_comparison/1.0",
        "status": status,
        "baseline_parser_version": str(baseline_module.COIN_GROUP_PARSER_VERSION),
        "candidate_parser_version": str(candidate_module.COIN_GROUP_PARSER_VERSION),
        "baseline_parser_sha256": baseline_sha256,
        "candidate_parser_sha256": candidate_sha256,
        "parser_version_collision": parser_version_collision,
        "records_seen": counts["records_seen"],
        "records_rejected": counts["records_rejected"],
        "records_ignored": counts["records_ignored"],
        "duplicate_events": counts["duplicate_events"],
        "available_timestamp_fallbacks": counts["available_timestamp_fallbacks"],
        "messages_compared": compared,
        "economically_exact_messages": exact,
        "economic_equivalence_rate": round(exact / compared, 8) if compared else None,
        "baseline_offer_candidates": counts["baseline_offer_candidates"],
        "candidate_offer_candidates": counts["candidate_offer_candidates"],
        "reviewed_coverage_gains": reviewed_gains,
        "reviewed_gain_types": {
            "multiline_single_offer": counts["REVIEWED_MULTILINE_SINGLE_OFFER_GAIN"],
            "low_price_duplicated_zero": counts[
                "REVIEWED_LOW_PRICE_DUPLICATED_ZERO_GAIN"
            ],
        },
        "unreviewed_economic_drift": counts["UNREVIEWED_ECONOMIC_DRIFT"],
        "unreviewed_coverage_gains": counts["UNREVIEWED_COVERAGE_GAIN"],
        "dominance_gate": "EQUAL_OR_REVIEWED_BETTER" if status == "PASS" else "BLOCK",
        "sensitive_payload_emitted": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    args = parser.parse_args(argv)
    try:
        baseline = _load_coin_parser(
            _root(args.baseline_root, field="baseline_root"),
            namespace="_baseline_market_intelligence",
        )
        candidate = _load_coin_parser(
            _root(args.candidate_root, field="candidate_root"),
            namespace="_candidate_market_intelligence",
        )
        report = compare_stream(
            sys.stdin.buffer,
            baseline_module=baseline,
            candidate_module=candidate,
        )
    except (OSError, ParserStreamComparisonError) as exc:
        print(
            json.dumps(
                {
                    "schema": "coin_offer_parser_stream_comparison/1.0",
                    "status": "FAIL",
                    "reason_code": str(exc),
                    "sensitive_payload_emitted": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
