"""Deterministic probability calibration for condition-model research.

The helpers operate only on aggregate numeric arrays.  They retain no offer
text or identity and deliberately separate calibration from sealed evaluation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, precision_recall_fscore_support
from sklearn.model_selection import StratifiedKFold


_EPSILON = 1e-6


def _logit(probability: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probability, dtype=np.float64), _EPSILON, 1 - _EPSILON)
    return np.log(clipped / (1 - clipped)).reshape(-1, 1)


@dataclass(frozen=True, slots=True)
class PlattCalibrator:
    slope: float
    intercept: float

    def predict(self, probability: np.ndarray) -> np.ndarray:
        score = self.slope * _logit(probability).reshape(-1) + self.intercept
        return 1.0 / (1.0 + np.exp(-np.clip(score, -40.0, 40.0)))

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def _fit_platt(target: np.ndarray, probability: np.ndarray) -> PlattCalibrator:
    model = LogisticRegression(C=1.0, max_iter=1_000, random_state=1729, solver="liblinear")
    model.fit(_logit(probability), np.asarray(target, dtype=np.int8))
    return PlattCalibrator(
        slope=float(model.coef_[0, 0]),
        intercept=float(model.intercept_[0]),
    )


def expected_calibration_error(
    target: np.ndarray,
    probability: np.ndarray,
    *,
    bin_count: int = 10,
) -> float:
    truth = np.asarray(target, dtype=np.int8)
    predicted = np.asarray(probability, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, bin_count + 1)
    total = max(1, len(truth))
    error = 0.0
    for index in range(bin_count):
        lower, upper = edges[index], edges[index + 1]
        mask = (predicted >= lower) & (
            predicted <= upper if index == bin_count - 1 else predicted < upper
        )
        if not bool(mask.any()):
            continue
        error += float(mask.sum()) / total * abs(
            float(predicted[mask].mean()) - float(truth[mask].mean())
        )
    return round(error, 6)


def calibration_metrics(target: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    truth = np.asarray(target, dtype=np.int8)
    predicted = np.asarray(probability, dtype=np.float64)
    return {
        "brier": round(float(brier_score_loss(truth, predicted)), 6),
        "ece_10": expected_calibration_error(truth, predicted, bin_count=10),
    }


def fit_oof_platt_calibrator(
    target: np.ndarray,
    probability: np.ndarray,
    *,
    maximum_folds: int = 5,
) -> tuple[PlattCalibrator | None, np.ndarray | None, dict[str, Any]]:
    truth = np.asarray(target, dtype=np.int8)
    raw = np.asarray(probability, dtype=np.float64)
    positive = int(truth.sum())
    negative = int(len(truth) - positive)
    fold_count = min(maximum_folds, positive, negative)
    if positive < 5 or negative < 20 or fold_count < 2:
        return None, None, {
            "status": "INSUFFICIENT_CALIBRATION_SUPPORT",
            "support_positive": positive,
            "support_negative": negative,
        }
    splitter = StratifiedKFold(n_splits=fold_count, shuffle=True, random_state=1729)
    oof = np.zeros(len(truth), dtype=np.float64)
    for fit_index, validation_index in splitter.split(raw, truth):
        calibrator = _fit_platt(truth[fit_index], raw[fit_index])
        oof[validation_index] = calibrator.predict(raw[validation_index])
    final = _fit_platt(truth, raw)
    return final, oof, {
        "status": "CALIBRATED",
        "method": "PLATT_LOGIT_OOF",
        "fold_count": fold_count,
        "support_positive": positive,
        "support_negative": negative,
        "raw": calibration_metrics(truth, raw),
        "oof_calibrated": calibration_metrics(truth, oof),
        "parameters": final.to_dict(),
    }


def select_abstention_thresholds(
    target: np.ndarray,
    probability: np.ndarray,
    *,
    minimum_positive_precision: float = 0.90,
    minimum_negative_predictive_value: float = 0.98,
    minimum_decision_count: int = 10,
    minimum_abstention_width: float = 0.10,
) -> dict[str, Any]:
    truth = np.asarray(target, dtype=np.int8)
    predicted_probability = np.asarray(probability, dtype=np.float64)
    candidates = np.arange(0.01, 1.0, 0.01)

    positive_rows: list[tuple[float, float, float, int]] = []
    for threshold in candidates:
        selected = predicted_probability >= threshold
        count = int(selected.sum())
        if count < minimum_decision_count:
            continue
        precision = float(truth[selected].mean())
        recall = float(truth[selected].sum() / max(1, truth.sum()))
        if precision >= minimum_positive_precision:
            positive_rows.append((recall, precision, float(threshold), count))
    positive_rows.sort(key=lambda row: (row[0], row[1], -row[2]), reverse=True)

    negative_rows: list[tuple[int, float, float]] = []
    for threshold in candidates:
        selected = predicted_probability <= threshold
        count = int(selected.sum())
        if count < minimum_decision_count:
            continue
        npv = float((truth[selected] == 0).mean())
        if npv >= minimum_negative_predictive_value:
            negative_rows.append((count, npv, float(threshold)))
    negative_rows.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)

    # Select both boundaries jointly.  Independent maximisation can collapse
    # the uncertainty interval to one percentage point, which looks decisive
    # while providing no useful abstention buffer.  Coverage is maximised only
    # among pairs that preserve a material probability gap.
    pairs: list[tuple[int, float, float, tuple[float, float, float, int], tuple[int, float, float]]] = []
    for positive in positive_rows:
        for negative in negative_rows:
            if negative[2] + minimum_abstention_width > positive[2]:
                continue
            pairs.append(
                (
                    positive[3] + negative[0],
                    positive[0],
                    positive[1] + negative[1],
                    positive,
                    negative,
                )
            )
    selected_pair = max(pairs, default=None, key=lambda row: (row[0], row[1], row[2]))
    positive = selected_pair[3] if selected_pair else None
    negative = selected_pair[4] if selected_pair else None
    positive_threshold = positive[2] if positive else None
    negative_threshold = negative[2] if negative else None
    return {
        "status": "READY" if selected_pair else "ABSTAIN_ONLY",
        "positive_threshold": round(positive_threshold, 4)
        if positive_threshold is not None
        else None,
        "negative_threshold": round(negative_threshold, 4)
        if negative_threshold is not None
        else None,
        "minimum_positive_precision": minimum_positive_precision,
        "minimum_negative_predictive_value": minimum_negative_predictive_value,
        "minimum_decision_count": minimum_decision_count,
        "minimum_abstention_width": minimum_abstention_width,
        "positive_calibration_precision": round(positive[1], 6) if positive else None,
        "positive_calibration_recall": round(positive[0], 6) if positive else None,
        "positive_calibration_count": positive[3] if positive else 0,
        "negative_calibration_npv": round(negative[1], 6) if negative else None,
        "negative_calibration_count": negative[0] if negative else 0,
    }


def evaluate_abstention_policy(
    target: np.ndarray,
    probability: np.ndarray,
    policy: dict[str, Any],
) -> dict[str, Any]:
    truth = np.asarray(target, dtype=np.int8)
    predicted_probability = np.asarray(probability, dtype=np.float64)
    positive_threshold = policy.get("positive_threshold")
    negative_threshold = policy.get("negative_threshold")
    confident_positive = (
        predicted_probability >= float(positive_threshold)
        if positive_threshold is not None
        else np.zeros(len(truth), dtype=bool)
    )
    confident_negative = (
        predicted_probability <= float(negative_threshold)
        if negative_threshold is not None
        else np.zeros(len(truth), dtype=bool)
    )
    abstained = ~(confident_positive | confident_negative)
    positive_precision, positive_recall, positive_f1, _ = precision_recall_fscore_support(
        truth,
        confident_positive.astype(np.int8),
        average="binary",
        zero_division=0,
    )
    negative_npv = (
        float((truth[confident_negative] == 0).mean())
        if bool(confident_negative.any())
        else None
    )
    return {
        "confident_positive_count": int(confident_positive.sum()),
        "confident_negative_count": int(confident_negative.sum()),
        "abstain_count": int(abstained.sum()),
        "abstain_rate": round(float(abstained.mean()), 6),
        "positive_precision": round(float(positive_precision), 6),
        "positive_recall": round(float(positive_recall), 6),
        "positive_f1": round(float(positive_f1), 6),
        "negative_predictive_value": round(negative_npv, 6)
        if negative_npv is not None
        else None,
        "calibration": calibration_metrics(truth, predicted_probability),
    }


__all__ = [
    "PlattCalibrator",
    "calibration_metrics",
    "evaluate_abstention_policy",
    "expected_calibration_error",
    "fit_oof_platt_calibrator",
    "select_abstention_thresholds",
]
