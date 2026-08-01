#!/usr/bin/env python3
"""Leakage-resistant evaluation of group events as short-term coin anchors."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.coin_intelligence_private_ingest.runtime_paths import (
        PIPELINE_ROOT as PIPE,
    )
except ModuleNotFoundError:  # Standalone immutable runtime deployment.
    PIPE = Path(__file__).resolve().parent


DEFAULT_DB = PIPE / "group_training_dataset_shadow.sqlite3"
DEFAULT_JSON = PIPE / "group_anchor_shadow_evaluation.latest.json"
DEFAULT_MARKDOWN = PIPE / "group_anchor_shadow_evaluation.latest.md"
VERSION = "group-anchor-shadow-eval-v2.0-purged-chain-walk-forward"
MIN_PROMOTION_TEST_CHAINS = 30
MIN_PROMOTION_TEST_DAYS = 3


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _weighted_median(points: list[tuple[int, float]]) -> int:
    ordered = sorted(points)
    total = sum(weight for _, weight in ordered)
    cursor = 0.0
    for price, weight in ordered:
        cursor += weight
        if cursor >= total / 2:
            return int(price)
    return int(ordered[-1][0])


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [float(row["absolute_error"]) for row in rows]
    percentage_errors = [float(row["absolute_percentage_error"]) for row in rows]
    signed = [float(row["signed_percentage_error"]) for row in rows]
    return {
        "n": len(rows),
        "mae_project_units": round(statistics.mean(errors), 3) if errors else None,
        "median_ae_project_units": round(statistics.median(errors), 3) if errors else None,
        "p90_ae_project_units": (
            round(float(_percentile(errors, 0.90)), 3) if errors else None
        ),
        "mape_percent": (
            round(statistics.mean(percentage_errors), 6)
            if percentage_errors
            else None
        ),
        "signed_bias_percent": (
            round(statistics.mean(signed), 6) if signed else None
        ),
    }


def _bootstrap_improvement(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> dict[str, Any] | None:
    baseline_by_id = {row["target_chain"]: row for row in baseline}
    pairs = [
        (baseline_by_id[row["target_chain"]], row)
        for row in candidate
        if row["target_chain"] in baseline_by_id
    ]
    if not pairs:
        return None
    deltas = [
        float(base["absolute_percentage_error"])
        - float(challenger["absolute_percentage_error"])
        for base, challenger in pairs
    ]
    generator = random.Random(20260801)
    means: list[float] = []
    for _ in range(3000):
        sample = [deltas[generator.randrange(len(deltas))] for _ in deltas]
        means.append(statistics.mean(sample))
    return {
        "paired_independent_chains": len(pairs),
        "mean_mape_improvement_percentage_points": round(
            statistics.mean(deltas), 6
        ),
        "bootstrap_95pct": [
            round(float(_percentile(means, 0.025)), 6),
            round(float(_percentile(means, 0.975)), 6),
        ],
        "strictly_better": sum(delta > 0 for delta in deltas),
        "strictly_worse": sum(delta < 0 for delta in deltas),
        "ties": sum(delta == 0 for delta in deltas),
    }


def _market_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["commodity"]),
        str(row["settlement"]),
        str(row["trade_form"]),
    )


def _target_chains(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        grouped[int(row["economic_chain_id"])].append(row)
    targets = []
    for chain, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: row["when"])
        prices = [int(row["price"]) for row in ordered]
        targets.append(
            {
                **ordered[0],
                "economic_chain_id": chain,
                "price": int(statistics.median(prices)),
                "fill_count": len(ordered),
                "when": min(float(row["when"]) for row in ordered),
            }
        )
    return sorted(targets, key=lambda row: (row["when"], row["economic_chain_id"]))


def _predict(
    *,
    target: dict[str, Any],
    events: list[dict[str, Any]],
    method: str,
    half_life_minutes: float = 45.0,
    trade_weight: float = 4.0,
    maximum_age_minutes: float = 90.0,
) -> int | None:
    key = _market_key(target)
    prior = [
        row
        for row in events
        if float(row["when"]) < float(target["when"])
        and int(row["economic_chain_id"]) != int(target["economic_chain_id"])
        and _market_key(row) == key
    ]
    if method == "latest_offer":
        candidates = [
            row
            for row in prior
            if row["kind"] == "OFFER"
            and float(target["when"]) - float(row["when"]) <= 6 * 3600
        ]
        return int(max(candidates, key=lambda row: row["when"])["price"]) if candidates else None
    if method == "latest_event":
        return int(max(prior, key=lambda row: row["when"])["price"]) if prior else None
    recent = [
        row
        for row in prior
        if float(target["when"]) - float(row["when"])
        <= maximum_age_minutes * 60
    ]
    if not recent:
        return None
    points: list[tuple[int, float]] = []
    for row in recent:
        age_minutes = (float(target["when"]) - float(row["when"])) / 60.0
        base = trade_weight if row["kind"] == "TRADE" else 1.0
        # A chain may contain several real fills, but its total evidence is
        # capped sub-linearly by the dataset builder.
        base *= float(row.get("training_weight") or 1.0)
        points.append(
            (
                int(row["price"]),
                base * math.exp(-math.log(2) * age_minutes / half_life_minutes),
            )
        )
    return _weighted_median(points)


def _score(
    targets: Iterable[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    method: str,
    parameters: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    parameters = parameters or {}
    target_list = list(targets)
    scored: list[dict[str, Any]] = []
    for target in target_list:
        predicted = _predict(
            target=target,
            events=events,
            method=method,
            **parameters,
        )
        if predicted is None:
            continue
        actual = int(target["price"])
        scored.append(
            {
                "target_chain": int(target["economic_chain_id"]),
                "market": "|".join(_market_key(target)),
                "day": datetime.fromtimestamp(
                    float(target["when"]), timezone.utc
                ).date().isoformat(),
                "absolute_error": abs(predicted - actual),
                "absolute_percentage_error": abs(predicted - actual) / actual * 100,
                "signed_percentage_error": (predicted - actual) / actual * 100,
            }
        )
    summary = _metrics(scored)
    summary["eligible_independent_chains"] = len(target_list)
    summary["coverage_percent"] = (
        round(len(scored) / len(target_list) * 100, 6) if target_list else 0.0
    )
    return scored, summary


def _tune(
    validation: list[dict[str, Any]], events: list[dict[str, Any]]
) -> tuple[dict[str, float], dict[str, Any]]:
    trials = []
    for half_life in (10.0, 20.0, 45.0, 90.0):
        for trade_weight in (1.5, 2.0, 4.0, 6.0):
            for maximum_age in (30.0, 90.0, 180.0, 360.0):
                parameters = {
                    "half_life_minutes": half_life,
                    "trade_weight": trade_weight,
                    "maximum_age_minutes": maximum_age,
                }
                _, score = _score(
                    validation,
                    events,
                    method="weighted_event",
                    parameters=parameters,
                )
                mape = score["mape_percent"]
                coverage = float(score["coverage_percent"])
                objective = float("inf") if mape is None else float(mape) + max(0.0, 80.0 - coverage) * 0.02
                trials.append((objective, parameters, score))
    _, parameters, score = min(
        trials,
        key=lambda item: (
            item[0],
            -float(item[2]["coverage_percent"]),
            item[1]["maximum_age_minutes"],
        ),
    )
    return parameters, score


def evaluate(dataset: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{dataset.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    offer_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(offer_training_examples)")
    }
    if "economic_chain_id" not in offer_columns:
        raise RuntimeError("dataset_v2_economic_chain_id_required")
    offers = [
        dict(row)
        | {
            "kind": "OFFER",
            "when": _timestamp(row["occurred_at_utc"]),
        }
        for row in connection.execute(
            """SELECT * FROM offer_training_examples
            WHERE occurred_at_utc IS NOT NULL ORDER BY occurred_at_utc,id"""
        )
    ]
    trades = [
        dict(row)
        | {
            "kind": "TRADE",
            "when": _timestamp(row["occurred_at_utc"]),
        }
        for row in connection.execute(
            """SELECT * FROM confirmed_trade_training_examples
            WHERE occurred_at_utc IS NOT NULL ORDER BY occurred_at_utc,id"""
        )
    ]
    connection.close()
    targets = _target_chains(trades)
    if len(targets) < 10:
        raise RuntimeError("not_enough_independent_confirmed_trade_chains")
    validation_start = max(1, int(len(targets) * 0.60))
    test_start = max(validation_start + 1, int(len(targets) * 0.80))
    validation = targets[validation_start:test_start]
    test = targets[test_start:]
    events = offers + trades
    tuned_parameters, validation_score = _tune(validation, events)
    baseline_rows, baseline = _score(test, events, method="latest_offer")
    latest_rows, latest = _score(test, events, method="latest_event")
    weighted_rows, weighted = _score(
        test,
        events,
        method="weighted_event",
        parameters=tuned_parameters,
    )
    latest_improvement = _bootstrap_improvement(baseline_rows, latest_rows)
    weighted_improvement = _bootstrap_improvement(baseline_rows, weighted_rows)

    per_market: dict[str, dict[str, Any]] = {}
    markets = sorted({_market_key(row) for row in test})
    for market in markets:
        cohort = [row for row in test if _market_key(row) == market]
        market_name = "|".join(market)
        per_market[market_name] = {}
        for method, parameters in (
            ("latest_offer", None),
            ("latest_event", None),
            ("weighted_event", tuned_parameters),
        ):
            _, score = _score(
                cohort, events, method=method, parameters=parameters
            )
            per_market[market_name][method] = score

    test_days = sorted(
        {
            datetime.fromtimestamp(float(row["when"]), timezone.utc)
            .date()
            .isoformat()
            for row in test
        }
    )
    promotion_reasons = []
    if len(test) < MIN_PROMOTION_TEST_CHAINS:
        promotion_reasons.append("INSUFFICIENT_INDEPENDENT_TEST_CHAINS")
    if len(test_days) < MIN_PROMOTION_TEST_DAYS:
        promotion_reasons.append("INSUFFICIENT_DISTINCT_TEST_DAYS")
    if float(weighted["coverage_percent"]) < 80.0:
        promotion_reasons.append("WEIGHTED_CANDIDATE_COVERAGE_BELOW_80_PERCENT")
    if not weighted_improvement or weighted_improvement["bootstrap_95pct"][0] <= 0:
        promotion_reasons.append("WEIGHTED_IMPROVEMENT_NOT_STATISTICALLY_POSITIVE")

    return {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "dataset_sha256": _digest(dataset),
        "offer_rows": len(offers),
        "confirmed_trade_rows": len(trades),
        "independent_trade_chains": len(targets),
        "split": {
            "method": "chronological_60_20_20_by_economic_chain",
            "fit_chain_count": validation_start,
            "validation_chain_count": len(validation),
            "untouched_test_chain_count": len(test),
            "untouched_test_days": test_days,
            "same_chain_purged_from_features": True,
            "strictly_prior_events_only": True,
        },
        "tuned_weighted_candidate": {
            "selected_on": "VALIDATION_ONLY",
            "parameters": tuned_parameters,
            "validation": validation_score,
        },
        "untouched_test": {
            "baseline_latest_offer": baseline,
            "challenger_latest_event": latest,
            "challenger_weighted_event": weighted,
            "latest_event_vs_baseline": latest_improvement,
            "weighted_event_vs_baseline": weighted_improvement,
            "per_market": per_market,
        },
        "promotion": {
            "status": "SHADOW_NOT_PROMOTED" if promotion_reasons else "ELIGIBLE_FOR_HUMAN_REVIEW",
            "reasons": promotion_reasons,
            "automatic_promotion": False,
        },
    }


def _write_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _markdown(report: dict[str, Any]) -> str:
    test = report["untouched_test"]
    lines = [
        "# Group anchor Shadow evaluation",
        "",
        f"- Independent confirmed-trade chains: {report['independent_trade_chains']}",
        f"- Untouched test chains: {report['split']['untouched_test_chain_count']}",
        f"- Distinct test days: {len(report['split']['untouched_test_days'])}",
        f"- Promotion: {report['promotion']['status']}",
        "",
        "| Method | Coverage | MAPE | MAE | P90 AE |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, key in (
        ("Latest prior offer", "baseline_latest_offer"),
        ("Latest prior event", "challenger_latest_event"),
        ("Validation-tuned weighted event", "challenger_weighted_event"),
    ):
        value = test[key]
        lines.append(
            f"| {label} | {value['coverage_percent']}% | "
            f"{value['mape_percent']}% | {value['mae_project_units']} | "
            f"{value['p90_ae_project_units']} |"
        )
    lines.extend(
        [
            "",
            "Promotion blockers: "
            + (", ".join(report["promotion"]["reasons"]) or "none; human review still required"),
            "",
            "Prices and Telegram identifiers are intentionally absent from this report.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    dataset_sha = _digest(args.dataset)
    if args.json.exists():
        try:
            previous = json.loads(args.json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}
        if (
            previous.get("version") == VERSION
            and previous.get("dataset_sha256") == dataset_sha
        ):
            print(
                json.dumps(
                    {
                        "status": "NO_EVALUATION_DATA_CHANGE",
                        "version": VERSION,
                        "dataset_sha256": dataset_sha,
                    }
                )
            )
            return
    report = evaluate(args.dataset)
    _write_atomic(
        args.json,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    _write_atomic(args.markdown, _markdown(report))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
