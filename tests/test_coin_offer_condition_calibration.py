from __future__ import annotations

import numpy as np

from scripts.coin_offer_condition_calibration import (
    evaluate_abstention_policy,
    fit_oof_platt_calibrator,
    select_abstention_thresholds,
)


def test_oof_calibration_is_deterministic_and_out_of_fold() -> None:
    target = np.asarray(([0] * 80) + ([1] * 20), dtype=np.int8)
    raw = np.linspace(0.02, 0.98, len(target))

    first, first_oof, first_report = fit_oof_platt_calibrator(target, raw)
    second, second_oof, second_report = fit_oof_platt_calibrator(target, raw)

    assert first is not None and second is not None
    assert first_oof is not None and second_oof is not None
    np.testing.assert_allclose(first_oof, second_oof)
    assert first == second
    assert first_report == second_report
    assert first_report["fold_count"] == 5


def test_sparse_calibration_support_fails_closed() -> None:
    target = np.asarray(([0] * 30) + ([1] * 4), dtype=np.int8)
    raw = np.linspace(0.01, 0.99, len(target))

    calibrator, oof, report = fit_oof_platt_calibrator(target, raw)

    assert calibrator is None
    assert oof is None
    assert report["status"] == "INSUFFICIENT_CALIBRATION_SUPPORT"


def test_abstention_policy_separates_confident_decisions() -> None:
    target = np.asarray(([0] * 50) + [0, 0, 0, 1] + ([1] * 16), dtype=np.int8)
    probability = np.asarray(
        [0.01] * 45 + [0.45] * 5 + [0.55] * 4 + [0.99] * 16,
        dtype=np.float64,
    )

    policy = select_abstention_thresholds(
        target,
        probability,
        minimum_negative_predictive_value=0.99,
    )
    evaluation = evaluate_abstention_policy(target, probability, policy)

    assert policy["status"] == "READY"
    assert policy["negative_threshold"] < policy["positive_threshold"]
    assert policy["positive_threshold"] - policy["negative_threshold"] >= 0.10
    assert evaluation["confident_positive_count"] == 16
    assert evaluation["confident_negative_count"] == 50
    assert evaluation["abstain_count"] == 4
    assert evaluation["positive_precision"] == 1.0
    assert evaluation["negative_predictive_value"] == 1.0
