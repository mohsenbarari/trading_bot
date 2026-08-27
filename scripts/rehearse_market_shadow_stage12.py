#!/usr/bin/env python3
"""Exercise Stage 12 parity classification without touching a live authority."""

from __future__ import annotations

import argparse
from collections import Counter
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
from core.market_intelligence.coin_rate_engine import COIN_SPECS
from core.market_intelligence.shadow_parity import (
    CAPTURE_SOURCE_INVENTORY,
    FAILURE_DRILLS,
    FALLBACK_CAPTURE_SOURCES,
    capture_manifest_hash,
    compare_shadow_lanes,
    feature_evidence_snapshot_hash,
    sign_parity_report,
    verify_parity_report,
    write_private_json,
)


class Stage12RehearsalError(RuntimeError):
    pass


START = datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=2)
SESSION_ID = "STAGE12_OFFLINE_REHEARSAL"
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
    features: list[dict[str, Any]] = []
    estimates: list[dict[str, Any]] = []
    snapshot_versions: list[dict[str, Any]] = []
    for index in range(count):
        key = f"{index + 1:064x}"
        offset = (int((END - START).total_seconds()) - 6) * index // max(1, count - 1)
        occurred = START + timedelta(seconds=offset)
        available = occurred + timedelta(milliseconds=20)
        dimensions = _dimensions(index)
        snapshot_at = occurred + timedelta(seconds=4 + (index % 20) / 10)
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
                "next_snapshot_at_utc": snapshot_at.isoformat(),
            }
        )
        snapshot_versions.append(
            {
                "snapshot_version": index + 1,
                "evaluation_at_utc": snapshot_at.isoformat(),
            }
        )
        snapshot_features = [
            {
                "evaluation_at_utc": snapshot_at.isoformat(),
                "component": "XAUUSD",
                "point_value": "3400.25",
                "mean_value": "3400.20",
                "unit": "USD_PER_TROY_OUNCE",
                "sample_count": 8,
                "source_event_key": key,
                "freshness": "FRESH",
            },
            {
                "evaluation_at_utc": snapshot_at.isoformat(),
                "component": "USDT_IRT",
                "point_value": "96000",
                "mean_value": "95990",
                "unit": "TOMAN_PER_USDT",
                "sample_count": 9,
                "source_event_key": key,
                "freshness": "FRESH",
            },
        ]
        features.extend(snapshot_features)
        input_hash = feature_evidence_snapshot_hash(snapshot_features)
        for rate_index, instrument in enumerate(COIN_SPECS):
            for settlement in ("CASH", "TOMORROW"):
                no_data = instrument == "ONE_GRAM" and settlement == "TOMORROW"
                center = 188_700 + rate_index * 100
                estimates.append(
                    {
                        "evaluation_at_utc": snapshot_at.isoformat(),
                        "model_artifact_hash": MODEL_HASH,
                        "input_snapshot_hash": input_hash,
                        "instrument": "COIN_" + instrument,
                        "settlement": settlement,
                        "status": "NO_DATA" if no_data else "ESTIMATED",
                        "value": None if no_data else str(center),
                        "lower_bound": None if no_data else str(center - 700),
                        "upper_bound": None if no_data else str(center + 300),
                        "reason_code": "NO_SAFE_ANCHOR" if no_data else None,
                        "unit": "PROJECT_THOUSAND_TOMAN",
                        "confidence": "NONE" if no_data else "HIGH",
                        "method": "NO_SAFE_ANCHOR" if no_data else "WEIGHTED_BOOK",
                        "underlying_source": (
                            None if no_data else "PRIVATE_PHYSICAL_TODAY"
                        ),
                        "underlying_age_seconds": None if no_data else 1.0,
                        "anchor_age_seconds": None if no_data else 30.0,
                        "market_regime": "NORMAL",
                    }
                )
    capture_counts = Counter(item["source_code"] for item in captures)
    capture_inventory = [
        {
            "source_code": source,
            "captured_event_count": capture_counts.get(source, 0),
            "healthy": True,
            "observed_at_utc": END.isoformat(),
            "zero_event_reason": (
                "FALLBACK_NOT_SELECTED"
                if source in FALLBACK_CAPTURE_SOURCES and not capture_counts.get(source, 0)
                else None
            ),
        }
        for source in sorted(CAPTURE_SOURCE_INVENTORY)
    ]
    return {
        "contract": "market_shadow_lane/1.0",
        "lane": role,
        "session_id": SESSION_ID,
        "window_start_utc": START.isoformat(),
        "window_end_utc": END.isoformat(),
        "capture_manifest_complete": True,
        "model_artifact_hash": MODEL_HASH,
        "captures": captures,
        "capture_prefix": {
            "contract": "market_immutable_capture_prefix/1.0",
            "capture_authority": "NEW_SINGLE_OWNER_CAPTURE",
            "session_id": SESSION_ID,
            "pinned_at_utc": (START - timedelta(minutes=1)).isoformat(),
            "sealed_at_utc": (END + timedelta(minutes=1)).isoformat(),
            "byte_range_start": 0,
            "byte_range_end": len(captures) * 100,
            "prefix_event_count": len(captures),
            "ordered_manifest_hash": capture_manifest_hash(captures),
            "seed_receipt_hash": content_hash({"session_id": SESSION_ID, "seed": 1}),
            "sealed_byte_range_hash": content_hash(
                {"session_id": SESSION_ID, "events": len(captures)}
            ),
            "single_owner_receipt_hash": content_hash(
                {"session_id": SESSION_ID, "owners": 1}
            ),
            "sequence_health_receipt_hash": content_hash(
                {"session_id": SESSION_ID, "sequence_gaps": 0}
            ),
            "reconciliation_receipt_hash": content_hash(
                {"session_id": SESSION_ID, "reconciliation_gaps": 0}
            ),
            "unresolved_sequence_gap_count": 0,
            "unresolved_reconciliation_gap_count": 0,
        },
        "capture_inventory": capture_inventory,
        "facts": facts,
        "features": features,
        "snapshot_versions": snapshot_versions,
        "estimates": estimates,
        "transport": {
            "unresolved_sequence_gap_count": 0,
            "duplicate_eligible_fact_count": 0 if role == "PRIVATE_SHADOW" else None,
            "duplicate_evidence": (
                "DELIVERY_LEDGER" if role == "PRIVATE_SHADOW" else "NOT_APPLICABLE"
            ),
            "duplicate_evidence_receipt_hash": (
                content_hash({"session_id": SESSION_ID, "ledger_rows": len(facts)})
                if role == "PRIVATE_SHADOW"
                else None
            ),
            "duplicate_evidence_row_count": (
                len(facts) if role == "PRIVATE_SHADOW" else None
            ),
            "rejected_delivery_count": 0,
            "receiver_checkpoint_count": 6 if role == "PRIVATE_SHADOW" else 0,
        },
    }


def _soak() -> dict[str, Any]:
    receipts = []
    for index, drill in enumerate(sorted(FAILURE_DRILLS)):
        started = START + timedelta(seconds=30 + index * 10)
        receipts.append(
            {
                "drill": drill,
                "session_id": SESSION_ID,
                "receipt_hash": sha256(drill.encode("ascii")).hexdigest(),
                "started_at_utc": started.isoformat(),
                "completed_at_utc": (started + timedelta(seconds=5)).isoformat(),
                "passed": True,
            }
        )
    return {
        "contract": "market_failure_soak/1.0",
        "session_id": SESSION_ID,
        "evidence_mode": "HISTORICAL_REPLAY",
        "started_at_utc": START.isoformat(),
        "completed_at_utc": END.isoformat(),
        "full_market_session": False,
        "receiver_restart_passed": True,
        "route_partition_passed": True,
        "lost_ack_passed": True,
        "rollback_passed": True,
        "disk_failure_passed": True,
        "market_schedule": {
            "contract": "market_session_schedule/1.0",
            "session_id": SESSION_ID,
            "schedule_id": "TEHRAN_MARKET_REHEARSAL",
            "schedule_version": "SCHEDULE_V1",
            "timezone_name": "Asia/Tehran",
            "official_open_at_utc": START.isoformat(),
            "official_close_at_utc": END.isoformat(),
            "schedule_receipt_hash": sha256(
                f"{START.isoformat()}:{END.isoformat()}".encode("ascii")
            ).hexdigest(),
        },
        "drill_receipts": receipts,
    }


def _compare(legacy: dict[str, Any], private: dict[str, Any], labels=()):
    return compare_shadow_lanes(
        legacy, private, soak_value=_soak(), labels_value=labels
    )


def run(count: int) -> dict[str, Any]:
    legacy = _lane("REFERENCE_PROJECTION", count)
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
