from __future__ import annotations

from scripts.compare_coin_offer_condition_models import compare_reports


def _report(*, neural: bool, fingerprint: str = "same") -> dict:
    metric = {
        "status": "CALIBRATED",
        "precision": 0.95 if not neural else 0.96,
        "recall": 0.95 if not neural else 0.90,
        "f1": 0.95 if not neural else 0.928,
        "precision_gate_passed": True,
    }
    return {
        "taxonomy_version": "v2",
        "source": {"source_fingerprint": fingerprint, "row_count_after_deduplication": 100},
        "evaluation": {
            "temporal_split": {
                ("family_labels" if neural else "labels"): {"HAS_CONDITION": metric}
            }
        },
        "cpu_benchmark": {"latency_ms_p50": 300 if neural else 3},
    }


def test_comparison_prefers_f1_and_records_latency() -> None:
    result = compare_reports(_report(neural=False), _report(neural=True))

    assert result["field_comparison"]["HAS_CONDITION"]["metric_winner_ignoring_runtime_cost"] == "classic"
    assert result["cpu_latency"]["neural_to_classic_ratio"] == 100.0
    assert result["recommendation"]["runtime_install"] is False


def test_comparison_fails_on_source_mismatch() -> None:
    try:
        compare_reports(_report(neural=False), _report(neural=True, fingerprint="other"))
    except ValueError as exc:
        assert str(exc) == "condition_comparison_source_fingerprint_mismatch"
    else:
        raise AssertionError("expected source mismatch")
