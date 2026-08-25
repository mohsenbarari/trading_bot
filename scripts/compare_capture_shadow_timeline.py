#!/usr/bin/env python3
"""Compare capture-input shadow rates with the current-model timeline."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]


class ComparisonError(RuntimeError):
    pass


def _external_file(value: str, *, field: str) -> Path:
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ComparisonError(f"{field}_inside_repository")
    if path.is_symlink() or not path.is_file():
        raise ComparisonError(f"{field}_unavailable")
    return path


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("points"), list):
        raise ComparisonError("timeline_payload_invalid")
    return payload


def _capture_input_evidence(
    candidate: dict[str, object],
    *,
    report_path: str | None,
) -> tuple[dict[str, object] | None, str, str | None]:
    embedded = candidate.get("input")
    if isinstance(embedded, dict):
        return embedded, "candidate_timeline", None
    if not report_path:
        return None, "missing", None
    report = json.loads(
        _external_file(report_path, field="candidate_input_report").read_text(
            encoding="utf-8"
        )
    )
    if (
        not isinstance(report, dict)
        or report.get("schema") != "capture_shadow_replay"
        or not isinstance(report.get("input"), dict)
        or not str(report.get("adapter_version") or "").strip()
    ):
        raise ComparisonError("candidate_input_report_invalid")
    return (
        report["input"],  # type: ignore[return-value]
        "capture_shadow_replay",
        str(report["adapter_version"]),
    )


def _rates(payload: dict[str, object]) -> dict[tuple[str, str, str], dict[str, object]]:
    result: dict[tuple[str, str, str], dict[str, object]] = {}
    for point in payload["points"]:  # type: ignore[index]
        if not isinstance(point, dict):
            continue
        stamp = str(point.get("as_of_utc") or "")
        for rate in point.get("rates") or []:
            if not isinstance(rate, dict):
                continue
            key = (stamp, str(rate.get("commodity_code") or ""), str(rate.get("settlement_term") or ""))
            result[key] = rate
    return result


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return float(ordered[index])


def _run(args: argparse.Namespace) -> int:
    baseline = _load(_external_file(args.baseline, field="baseline"))
    candidate = _load(_external_file(args.candidate, field="candidate"))
    if baseline.get("engine_version") != candidate.get("engine_version"):
        raise ComparisonError("engine_version_mismatch")
    base = _rates(baseline)
    cand = _rates(candidate)
    keys = sorted(set(base) | set(cand))
    if not keys:
        raise ComparisonError("no_comparable_rates")
    paired_deltas: list[float] = []
    underlying_age_deltas: list[float] = []
    gains = losses = paired = invalid_magnitude = 0
    by_book: dict[str, list[float]] = defaultdict(list)
    for key in keys:
        left = base.get(key, {})
        right = cand.get(key, {})
        left_ok = left.get("status") == "ESTIMATED" and left.get("estimated_project_price") is not None
        right_ok = right.get("status") == "ESTIMATED" and right.get("estimated_project_price") is not None
        gains += int(right_ok and not left_ok)
        losses += int(left_ok and not right_ok)
        if not (left_ok and right_ok):
            continue
        paired += 1
        baseline_price = float(left["estimated_project_price"])
        candidate_price = float(right["estimated_project_price"])
        if not 10_000 <= candidate_price <= 500_000:
            invalid_magnitude += 1
        delta = abs(candidate_price - baseline_price) / max(baseline_price, 1.0)
        paired_deltas.append(delta)
        by_book[f"{key[1]}|{key[2]}"].append(delta)
        left_age = left.get("underlying_age_seconds")
        right_age = right.get("underlying_age_seconds")
        if left_age is not None and right_age is not None:
            underlying_age_deltas.append(float(right_age) - float(left_age))
    p95 = _percentile(paired_deltas, 0.95)
    maximum = max(paired_deltas) if paired_deltas else None
    mean = statistics.fmean(paired_deltas) if paired_deltas else None
    median_age_delta = statistics.median(underlying_age_deltas) if underlying_age_deltas else None
    coverage_gate = losses <= max(2, math.floor(0.01 * len(keys)))
    parity_gate = bool(paired_deltas) and p95 is not None and p95 <= 0.02 and maximum is not None and maximum <= 0.05
    magnitude_gate = invalid_magnitude == 0
    input_payload, input_evidence, adapter_version = _capture_input_evidence(
        candidate,
        report_path=getattr(args, "candidate_input_report", None),
    )
    rejection_rate = None
    if input_payload is not None:
        seen = int(input_payload.get("records_seen") or 0)
        rejected = int(input_payload.get("records_rejected") or 0)
        rejection_rate = rejected / seen if seen else None
    contract_gate = rejection_rate is not None and rejection_rate <= 0.10
    promote = coverage_gate and parity_gate and magnitude_gate and contract_gate
    report = {
        "schema": "capture_shadow_comparison",
        "schema_version": "1.0",
        "engine_version": baseline.get("engine_version"),
        "comparable_rate_slots": len(keys),
        "paired_estimates": paired,
        "candidate_coverage_gains": gains,
        "candidate_coverage_losses": losses,
        "candidate_invalid_magnitudes": invalid_magnitude,
        "mean_absolute_relative_delta": mean,
        "p95_absolute_relative_delta": p95,
        "max_absolute_relative_delta": maximum,
        "median_underlying_age_delta_seconds": median_age_delta,
        "candidate_input_rejection_rate": rejection_rate,
        "candidate_input_evidence": input_evidence,
        "candidate_adapter_version": adapter_version,
        "by_book": {
            key: {
                "paired": len(values),
                "mean_absolute_relative_delta": statistics.fmean(values),
                "p95_absolute_relative_delta": _percentile(values, 0.95),
                "max_absolute_relative_delta": max(values),
            }
            for key, values in sorted(by_book.items())
        },
        "gates": {
            "coverage": coverage_gate,
            "price_parity": parity_gate,
            "magnitude": magnitude_gate,
            "capture_contract": contract_gate,
        },
        "recommendation": "PROMOTE_CAPTURE_INPUT" if promote else "KEEP_SHADOW",
    }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if promote else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument(
        "--candidate-input-report",
        help="optional causal replay report supplying capture contract counters",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(build_parser().parse_args(argv))
    except (ComparisonError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": "capture_shadow_comparison", "recommendation": "KEEP_SHADOW", "reason": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
