#!/usr/bin/env python3
"""Compare raw-free condition-model reports with fail-closed field decisions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping


def _metric(report: Mapping[str, Any], label: str, *, neural: bool) -> Mapping[str, Any]:
    split = report["evaluation"]["temporal_split"]
    key = "family_labels" if neural else "labels"
    return split[key].get(label, {"status": "MISSING"})


def compare_reports(classic: Mapping[str, Any], neural: Mapping[str, Any]) -> dict[str, Any]:
    for field in ("taxonomy_version",):
        if classic[field] != neural[field]:
            raise ValueError(f"condition_comparison_{field}_mismatch")
    classic_source = classic["source"]
    neural_source = neural["source"]
    if classic_source["source_fingerprint"] != neural_source["source_fingerprint"]:
        raise ValueError("condition_comparison_source_fingerprint_mismatch")
    if classic_source["row_count_after_deduplication"] != neural_source["row_count_after_deduplication"]:
        raise ValueError("condition_comparison_row_count_mismatch")

    classic_labels = classic["evaluation"]["temporal_split"]["labels"]
    neural_labels = neural["evaluation"]["temporal_split"]["family_labels"]
    labels = sorted(set(classic_labels) | set(neural_labels))
    fields: dict[str, Any] = {}
    for label in labels:
        c = _metric(classic, label, neural=False)
        n = _metric(neural, label, neural=True)
        c_ready = c.get("status") == "CALIBRATED" and bool(c.get("precision_gate_passed"))
        n_ready = n.get("status") == "CALIBRATED" and bool(n.get("precision_gate_passed"))
        if c_ready and n_ready:
            winner = "classic" if (
                float(c.get("f1", 0)), float(c.get("precision", 0))
            ) >= (
                float(n.get("f1", 0)), float(n.get("precision", 0))
            ) else "neural"
            status = "COMPARABLE_CALIBRATED"
        elif c_ready:
            winner, status = "classic", "CLASSIC_ONLY_GATE_PASS"
        elif n_ready:
            winner, status = "neural", "NEURAL_ONLY_GATE_PASS"
        else:
            winner, status = None, "NO_MODEL_GATE_PASS_RULE_OR_ABSTAIN"
        fields[label] = {
            "status": status,
            "metric_winner_ignoring_runtime_cost": winner,
            "classic": {
                key: c.get(key)
                for key in ("status", "precision", "recall", "f1", "precision_gate_passed")
            },
            "neural": {
                key: n.get(key)
                for key in ("status", "precision", "recall", "f1", "precision_gate_passed")
            },
        }

    classic_latency = classic["cpu_benchmark"]["latency_ms_p50"]
    neural_latency = neural["cpu_benchmark"]["latency_ms_p50"]
    return {
        "schema_version": "coin-offer-condition-model-comparison-v1",
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "OWNER_REVIEW_REQUIRED_NOT_PROMOTED",
        "taxonomy_version": classic["taxonomy_version"],
        "source": {
            "row_count": classic_source["row_count_after_deduplication"],
            "source_fingerprint": classic_source["source_fingerprint"],
            "same_sealed_temporal_protocol": True,
        },
        "cpu_latency": {
            "classic_p50_ms": classic_latency,
            "neural_p50_ms": neural_latency,
            "neural_to_classic_ratio": round(float(neural_latency) / max(float(classic_latency), 1e-9), 2),
        },
        "field_comparison": fields,
        "recommendation": {
            "candidate": "CLASSIC_CALIBRATED_HASHED_FEATURE_MODEL",
            "reasons": [
                "best sealed HAS_CONDITION precision/F1 balance",
                "best or comparable result on most gate-passing families",
                "materially lower CPU latency",
                "neural PAYMENT_ACCOUNT advantage is too small to justify encoder latency before owner truth",
            ],
            "neural_disposition": "RETAIN_AS_OFFLINE_CHALLENGER",
            "full_fine_tune": "BLOCKED_UNTIL_OWNER_REVIEWED_GROUND_TRUTH",
            "runtime_install": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classic-report", type=Path, required=True)
    parser.add_argument("--neural-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    classic = json.loads(args.classic_report.read_text(encoding="utf-8"))
    neural = json.loads(args.neural_report.read_text(encoding="utf-8"))
    comparison = compare_reports(classic, neural)
    output = args.output.expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    try:
        output.relative_to(repository)
    except ValueError:
        pass
    else:
        raise SystemExit("condition_comparison_output_must_be_external")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(output, 0o600)
    print(json.dumps({
        "output": str(output),
        "sha256": sha256(output.read_bytes()).hexdigest(),
        "status": comparison["status"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
