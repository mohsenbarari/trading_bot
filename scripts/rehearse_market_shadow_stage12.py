#!/usr/bin/env python3
"""Exercise Stage 12 parity classification without touching a live authority."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.private_pipeline_contracts import content_hash
from core.market_intelligence.shadow_parity import (
    compare_shadow_lanes,
    sign_parity_report,
    verify_parity_report,
    write_private_json,
)


class Stage12RehearsalError(RuntimeError):
    pass


START = datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc)
MODEL_HASH = sha256(b"stage12-same-model-artifact").hexdigest()


def _dimensions(index: int) -> dict[str, Any]:
    source_index = index % 6
    if source_index in {0, 1}:
        return {
            "instrument": "COIN_IMAM",
            "event_type": "OFFER",
            "side": "SELL" if index % 2 else "BUY",
            "settlement": "TOMORROW" if index % 5 == 0 else "CASH",
            "trade_form": "PHYSICAL",
            "price_value": str(188000 + index),
            "price_unit": "PROJECT_THOUSAND_TOMAN",
            "quantity_value": str(index % 20 + 1),
            "quantity_unit": "COIN",
        }
    if source_index == 2:
        return {
            "instrument": "MELTED_GOLD_PRIVATE",
            "event_type": "OFFER",
            "side": "SELL",
            "settlement": "CASH",
            "trade_form": "PHYSICAL",
            "price_value": str(52_000_000 + index * 1000),
            "price_unit": "TOMAN_PER_MESGHAL_750",
            "quantity_value": str(index % 25 + 1),
            "quantity_unit": "MESGHAL",
        }
    if source_index == 3:
        return {
            "instrument": "USD_HERAT",
            "event_type": "QUOTE",
            "side": "MID",
            "settlement": "SPOT",
            "trade_form": "NOT_APPLICABLE",
            "price_value": str(95_000 + index),
            "price_unit": "TOMAN_PER_USD",
            "quantity_value": None,
            "quantity_unit": None,
        }
    if source_index == 4:
        return {
            "instrument": "XAUUSD",
            "event_type": "QUOTE",
            "side": "MID",
            "settlement": "SPOT",
            "trade_form": "NOT_APPLICABLE",
            "price_value": str(3400 + index / 100),
            "price_unit": "USD_PER_TROY_OUNCE",
            "quantity_value": None,
            "quantity_unit": None,
        }
    return {
        "instrument": "USDT_IRT",
        "event_type": "QUOTE",
        "side": "MID",
        "settlement": "SPOT",
        "trade_form": "NOT_APPLICABLE",
        "price_value": str(96_000 + index),
        "price_unit": "TOMAN_PER_USDT",
        "quantity_value": None,
        "quantity_unit": None,
    }


def _lane(role: str, count: int) -> dict[str, Any]:
    source_codes = (
        "GROUP_1",
        "GROUP_2",
        "PRIVATE_GOLD_CHANNEL",
        "USD_HERAT",
        "XAUUSD",
        "WALLEX_PUBLIC_API",
    )
    captures: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for index in range(count):
        key = f"{index + 1:064x}"
        occurred = START + timedelta(seconds=index)
        available = occurred + timedelta(milliseconds=20)
        dimensions = _dimensions(index)
        captures.append(
            {
                "event_key": key,
                "source_code": source_codes[index % 6],
                "occurred_at_utc": occurred.isoformat(),
                "available_at_utc": available.isoformat(),
            }
        )
        facts.append(
            {
                "event_key": key,
                "source_code": source_codes[index % 6],
                "eligible": True,
                "dimensions": dimensions,
                "parser_fingerprint": content_hash(dimensions),
                "lifecycle_state": (
                    "ACTIVE" if dimensions["event_type"] == "OFFER" else "OBSERVED"
                ),
                "occurred_at_utc": occurred.isoformat(),
                "available_at_utc": available.isoformat(),
                "parsed_at_utc": (occurred + timedelta(milliseconds=100)).isoformat(),
                "transferred_at_utc": (occurred + timedelta(milliseconds=250)).isoformat(),
                "next_snapshot_at_utc": (
                    occurred + timedelta(seconds=4 + (index % 20) / 10)
                ).isoformat(),
            }
        )
    evaluation = START + timedelta(seconds=count + 10)
    features = [
        {
            "evaluation_at_utc": evaluation.isoformat(),
            "component": "XAUUSD",
            "point_value": "3400.25",
            "mean_value": "3400.20",
            "unit": "USD_PER_TROY_OUNCE",
            "sample_count": 8,
            "source_event_key": f"{5:064x}",
            "freshness": "FRESH",
        },
        {
            "evaluation_at_utc": evaluation.isoformat(),
            "component": "USDT_IRT",
            "point_value": "96000",
            "mean_value": "95990",
            "unit": "TOMAN_PER_USDT",
            "sample_count": 9,
            "source_event_key": f"{6:064x}",
            "freshness": "FRESH",
        },
    ]
    input_hash = content_hash(features)
    return {
        "contract": "market_shadow_lane/1.0",
        "lane": role,
        "window_start_utc": START.isoformat(),
        "window_end_utc": evaluation.isoformat(),
        "capture_manifest_complete": True,
        "model_artifact_hash": MODEL_HASH,
        "captures": captures,
        "facts": facts,
        "features": features,
        "estimates": [
            {
                "evaluation_at_utc": evaluation.isoformat(),
                "model_artifact_hash": MODEL_HASH,
                "input_snapshot_hash": input_hash,
                "instrument": "COIN_IMAM",
                "settlement": "CASH",
                "value": "188700",
                "lower_bound": "188000",
                "upper_bound": "189000",
            }
        ],
        "transport": {
            "unresolved_sequence_gap_count": 0,
            "duplicate_eligible_fact_count": 0,
            "rejected_delivery_count": 0,
            "receiver_checkpoint_count": 6 if role == "PRIVATE_SHADOW" else 0,
        },
    }


def _soak() -> dict[str, Any]:
    return {
        "contract": "market_failure_soak/1.0",
        "evidence_mode": "HISTORICAL_REPLAY",
        "started_at_utc": START.isoformat(),
        "completed_at_utc": (START + timedelta(hours=2)).isoformat(),
        "full_market_session": False,
        "receiver_restart_passed": True,
        "route_partition_passed": True,
        "lost_ack_passed": True,
        "rollback_passed": True,
        "disk_failure_passed": True,
    }


def _compare(legacy: dict[str, Any], private: dict[str, Any], labels=()):
    return compare_shadow_lanes(
        legacy, private, soak_value=_soak(), labels_value=labels
    )


def run(count: int) -> dict[str, Any]:
    legacy = _lane("LEGACY", count)
    private = _lane("PRIVATE_SHADOW", count)
    clean = _compare(legacy, private)
    if clean["severity_1_count"] or clean["severity_2_count"]:
        raise Stage12RehearsalError("clean_shadow_parity_failed")
    if clean["promotion_recommendation"] != "HOLD_LIVE_OPEN_MARKET_REQUIRED":
        raise Stage12RehearsalError("offline_evidence_was_promoted")
    if clean["source_to_snapshot_p95_seconds"] > 7:
        raise Stage12RehearsalError("latency_gate_failed")

    key = os.urandom(48)
    signed = sign_parity_report(clean, key=key, key_id="stage12-rehearsal:v1")
    if not verify_parity_report(signed, key=key):
        raise Stage12RehearsalError("signed_report_verification_failed")
    with tempfile.TemporaryDirectory() as directory:
        report_path = Path(directory) / "parity-report.json"
        write_private_json(report_path, signed)
        if report_path.stat().st_mode & 0o077:
            raise Stage12RehearsalError("report_permissions_invalid")
        serialized = report_path.read_text(encoding="utf-8").lower()
        for forbidden in ("raw_text", "telegram_id", "https://", "t.me/"):
            if forbidden in serialized:
                raise Stage12RehearsalError("sensitive_report_material_detected")

    scenarios: dict[str, set[str]] = {}
    candidate = deepcopy(private)
    candidate["captures"] = candidate["captures"][1:]
    scenarios["capture"] = {
        item["category"] for item in _compare(legacy, candidate)["issues"]
    }

    candidate = deepcopy(private)
    prior_side = candidate["facts"][0]["dimensions"]["side"]
    candidate["facts"][0]["dimensions"]["side"] = (
        "SELL" if prior_side == "BUY" else "BUY"
    )
    candidate["facts"][0]["parser_fingerprint"] = content_hash(
        candidate["facts"][0]["dimensions"]
    )
    parser_report = _compare(legacy, candidate)
    scenarios["parser"] = {item["category"] for item in parser_report["issues"]}
    labels = [
        {
            "event_key": candidate["facts"][0]["event_key"],
            "resolution": "PRIVATE_CORRECT",
            "label_id_hash": "a" * 64,
            "approved_at_utc": START.isoformat(),
        }
    ]
    if _compare(legacy, candidate, labels)["severity_2_count"]:
        raise Stage12RehearsalError("approved_parser_label_not_honored")

    candidate = deepcopy(private)
    candidate["facts"][0]["lifecycle_state"] = "TRADE_CONFIRMED"
    scenarios["lifecycle"] = {
        item["category"] for item in _compare(legacy, candidate)["issues"]
    }

    candidate = deepcopy(private)
    candidate["facts"][0]["dimensions"]["price_unit"] = "TOMAN_PER_COIN"
    candidate["facts"][0]["parser_fingerprint"] = content_hash(
        candidate["facts"][0]["dimensions"]
    )
    scenarios["unit"] = {
        item["category"] for item in _compare(legacy, candidate)["issues"]
    }

    candidate = deepcopy(private)
    for fact in candidate["facts"]:
        occurred = datetime.fromisoformat(fact["occurred_at_utc"])
        fact["next_snapshot_at_utc"] = (occurred + timedelta(seconds=8)).isoformat()
    scenarios["timing"] = {
        item["category"] for item in _compare(legacy, candidate)["issues"]
    }

    candidate = deepcopy(private)
    candidate["transport"]["unresolved_sequence_gap_count"] = 1
    scenarios["transport"] = {
        item["category"] for item in _compare(legacy, candidate)["issues"]
    }

    candidate = deepcopy(private)
    candidate["estimates"][0]["value"] = "188800"
    scenarios["estimator"] = {
        item["category"] for item in _compare(legacy, candidate)["issues"]
    }

    expected = {
        "capture": "CAPTURE",
        "parser": "PARSER",
        "lifecycle": "LIFECYCLE",
        "unit": "UNIT",
        "timing": "TIMING",
        "transport": "TRANSPORT",
        "estimator": "ESTIMATOR",
    }
    if any(expected[name] not in categories for name, categories in scenarios.items()):
        raise Stage12RehearsalError("difference_classification_matrix_failed")
    return {
        "status": "pass",
        "event_count_per_lane": count,
        "capture_loss_count": clean["private_capture_loss_count"],
        "duplicate_eligible_fact_count": clean["duplicate_eligible_fact_count"],
        "unresolved_sequence_gap_count": clean["unresolved_sequence_gap_count"],
        "consumed_external_mismatch_count": clean["consumed_external_mismatch_count"],
        "same_input_estimator_mismatch_count": clean[
            "same_input_estimator_mismatch_count"
        ],
        "source_to_snapshot_p95_seconds": clean["source_to_snapshot_p95_seconds"],
        "report_hash": signed["report_hash"],
        "signature_verified": True,
        "classification_matrix": sorted(expected.values()),
        "approved_parser_label_gate": True,
        "promotion_recommendation": clean["promotion_recommendation"],
        "live_open_market_executed": False,
        "cutover_performed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=1000)
    args = parser.parse_args(argv)
    if not 100 <= args.events <= 100_000:
        parser.error("--events must be between 100 and 100000")
    try:
        report = run(args.events)
    except (OSError, ValueError, Stage12RehearsalError) as exc:
        print(json.dumps({"status": "fail", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
