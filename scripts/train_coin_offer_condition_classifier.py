#!/usr/bin/env python3
"""Train a privacy-bounded weak-supervision condition classifier.

The command reads the two canonical group imports in read-only mode and writes
only a hashed-feature research artifact plus aggregate metrics.  It never
stores source text, message IDs, sender identities, or a reversible vocabulary.
The output directory must be outside both the repository and live runtime.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import statistics
import time
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np
import sklearn
from sklearn.feature_extraction import FeatureHasher
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_fscore_support

from core.market_intelligence.coin_offer_conditions import (
    CONDITION_FAMILIES,
    CONDITION_TAXONOMY_VERSION,
    extract_offer_conditions,
    masked_condition_model_text,
    normalize_offer_text,
)
from core.market_intelligence.coin_condition_review import condition_sample_digest
from core.market_intelligence.coin_groups import (
    CoinGroupMessageInput,
    parse_coin_group_offers,
)
from scripts.coin_offer_condition_calibration import (
    PlattCalibrator,
    calibration_metrics,
    evaluate_abstention_policy,
    fit_oof_platt_calibrator,
    select_abstention_thresholds,
)


TRAINER_VERSION = "coin-offer-condition-trainer-v3"
ARTIFACT_VERSION = "coin-offer-condition-research-artifact-v3"
_LIVE_RUNTIME_ROOT = Path("/srv/trading-bot/production-data").resolve()
_TOKEN_RE = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class TrainingRow:
    opaque_digest: str
    group_code: str
    source_partition: str
    event_time_utc: str
    settlement_term: str
    trade_form: str
    model_text: str
    has_condition: bool
    families: tuple[str, ...]
    session_phase: str
    deadline_bucket: str
    composite_class: str
    span_tokens: tuple[str, ...]
    span_targets: tuple[int, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_output_dir(path: Path, *, repository_root: Path) -> Path:
    output = path.expanduser().resolve()
    for prohibited in (repository_root.resolve(), _LIVE_RUNTIME_ROOT):
        try:
            output.relative_to(prohibited)
        except ValueError:
            continue
        raise ValueError("condition_model_output_must_be_external_research_path")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)
    return output


def _connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"condition_training_database_missing:{resolved.name}")
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _opaque_digest(
    *,
    group_code: str,
    event_time_utc: str,
    settlement_term: str,
    trade_form: str,
    model_text: str,
) -> str:
    return condition_sample_digest(
        group_code=group_code,
        event_time_utc=event_time_utc,
        settlement_term=settlement_term,
        trade_form=trade_form,
        model_text=model_text,
    )


def _condition_token_targets(
    raw_text: str,
    condition_spans: Sequence[tuple[int, int]],
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Create transient hashed-feature inputs and weak token-span targets.

    Tokens exist only in process memory.  The fitted ``FeatureHasher`` keeps no
    vocabulary, and neither tokens nor source spans are written to artifacts.
    """

    normalized = normalize_offer_text(raw_text)[:512]
    tokens: list[str] = []
    targets: list[int] = []
    for match in _TOKEN_RE.finditer(normalized):
        tokens.append(masked_condition_model_text(match.group(0)))
        targets.append(
            int(any(match.start() < end and match.end() > start for start, end in condition_spans))
        )
    return tuple(tokens), tuple(targets)


def load_training_rows(
    database: Path,
    *,
    staging_database: Path | None,
    market_open_minute: int,
    market_close_minute: int,
) -> list[TrainingRow]:
    connection = _connect_read_only(database)
    try:
        source_rows = connection.execute(
            """
            SELECT m.source_html_file AS group_code,
                   m.event_time_utc,
                   o.settlement,
                   o.trade_form,
                   o.source_text
            FROM offers o
            JOIN messages m
              ON m.import_id=o.import_id AND m.message_id=o.message_id
            JOIN imports i ON i.id=o.import_id
            WHERE m.source_html_file IN ('group_1','group_2')
              AND trim(COALESCE(o.source_text,'')) <> ''
              AND i.archive_path <> 'canonical-market-store'
            ORDER BY m.event_time_utc,o.id
            """
        ).fetchall()
    finally:
        connection.close()

    combined_rows: list[tuple[str, str, str, str, str, str]] = [
        (
            str(row["group_code"]),
            "HISTORICAL_IMPORT",
            str(row["event_time_utc"]),
            str(row["settlement"] or "UNKNOWN").upper(),
            str(row["trade_form"] or "UNKNOWN").upper(),
            str(row["source_text"] or ""),
        )
        for row in source_rows
    ]
    if staging_database is not None:
        staging = _connect_read_only(staging_database)
        try:
            staged_messages = staging.execute(
                """
                SELECT group_number,message_id,event_time_utc,available_at_utc,message_text
                FROM coin_group_staged_messages
                ORDER BY event_time_utc,group_number,message_id
                """
            ).fetchall()
        finally:
            staging.close()
        for row in staged_messages:
            raw_message = str(row["message_text"] or "")
            for line_index, line in enumerate(raw_message.splitlines() or [raw_message]):
                if not line.strip():
                    continue
                source = CoinGroupMessageInput(
                    group_number=int(row["group_number"]),
                    source_event_id=f"{int(row['message_id'])}:{line_index}",
                    published_at_utc=str(row["event_time_utc"]),
                    available_at_utc=str(row["available_at_utc"]),
                    text=line,
                )
                for offer in parse_coin_group_offers(source):
                    combined_rows.append(
                        (
                            f"group_{int(row['group_number'])}",
                            "RECENT_PROTECTED_STAGING",
                            str(row["event_time_utc"]),
                            str(offer.settlement_term).upper(),
                            str(offer.trade_form).upper(),
                            line,
                        )
                    )

    # Exact normalized repetitions are retained once per group/book/day.  This
    # prevents copied offers from leaking across the temporal evaluation while
    # preserving genuine recurrence on later market days.
    deduplicated: dict[tuple[str, str, str, str, str], TrainingRow] = {}
    for group, source_partition, event_time, settlement, trade_form, raw_text in combined_rows:
        model_text = masked_condition_model_text(raw_text)
        if not model_text:
            continue
        axes = extract_offer_conditions(
            raw_text,
            event_time_utc=event_time,
            settlement_term=settlement,
            trade_form=trade_form,
            market_open_minute=market_open_minute,
            market_close_minute=market_close_minute,
        )
        span_tokens, span_targets = _condition_token_targets(raw_text, axes.condition_spans)
        sample = TrainingRow(
            opaque_digest=_opaque_digest(
                group_code=group,
                event_time_utc=event_time,
                settlement_term=settlement,
                trade_form=trade_form,
                model_text=model_text,
            ),
            group_code=group,
            source_partition=source_partition,
            event_time_utc=event_time,
            settlement_term=settlement,
            trade_form=trade_form,
            model_text=model_text,
            has_condition=axes.has_condition,
            families=axes.condition_families,
            session_phase=axes.market_session_phase,
            deadline_bucket=axes.deadline_horizon_bucket,
            composite_class=axes.composite_class,
            span_tokens=span_tokens,
            span_targets=span_targets,
        )
        day = event_time[:10]
        key = (group, day, settlement, trade_form, model_text)
        deduplicated.setdefault(key, sample)
    return sorted(deduplicated.values(), key=lambda item: (item.event_time_utc, item.opaque_digest))


def chronological_three_way_split(
    rows: Sequence[TrainingRow],
    *,
    training_fraction: float = 0.70,
    calibration_fraction: float = 0.15,
) -> tuple[list[TrainingRow], list[TrainingRow], list[TrainingRow]]:
    """Return ordered train/calibration/evaluation partitions without overlap."""

    if len(rows) < 3:
        raise ValueError("condition_training_three_way_split_requires_three_rows")
    if not 0 < training_fraction < 1 or not 0 < calibration_fraction < 1:
        raise ValueError("condition_training_split_fraction_invalid")
    if training_fraction + calibration_fraction >= 1:
        raise ValueError("condition_training_split_fraction_exhausts_evaluation")
    training_end = min(len(rows) - 2, max(1, int(len(rows) * training_fraction)))
    calibration_size = max(1, int(len(rows) * calibration_fraction))
    calibration_end = min(len(rows) - 1, training_end + calibration_size)
    return (
        list(rows[:training_end]),
        list(rows[training_end:calibration_end]),
        list(rows[calibration_end:]),
    )


def chronological_train_calibration_split(
    rows: Sequence[TrainingRow],
    *,
    training_fraction: float = 0.80,
) -> tuple[list[TrainingRow], list[TrainingRow]]:
    """Split source-group rows before evaluating unchanged thresholds on another group."""

    if len(rows) < 2:
        raise ValueError("condition_training_calibration_split_requires_two_rows")
    if not 0 < training_fraction < 1:
        raise ValueError("condition_training_split_fraction_invalid")
    training_end = min(len(rows) - 1, max(1, int(len(rows) * training_fraction)))
    return list(rows[:training_end]), list(rows[training_end:])


def _vectorizer() -> HashingVectorizer:
    return HashingVectorizer(
        analyzer="char_wb",
        ngram_range=(3, 5),
        n_features=2**16,
        alternate_sign=False,
        norm="l2",
        lowercase=False,
    )


def _span_vectorizer() -> FeatureHasher:
    return FeatureHasher(n_features=2**16, input_type="dict", alternate_sign=False)


def _span_token_features(tokens: Sequence[str], index: int) -> dict[str, float]:
    token = tokens[index]
    previous = tokens[index - 1] if index else "<BOS>"
    following = tokens[index + 1] if index + 1 < len(tokens) else "<EOS>"
    features: dict[str, float] = {
        "bias": 1.0,
        f"token={token}": 1.0,
        f"previous={previous}": 1.0,
        f"following={following}": 1.0,
        f"previous+token={previous}|{token}": 1.0,
        f"token+following={token}|{following}": 1.0,
        f"length={min(len(token), 12)}": 1.0,
        f"has-number-token={'<NUM>' in token}": 1.0,
    }
    for width in (1, 2, 3):
        if len(token) >= width:
            features[f"prefix-{width}={token[:width]}"] = 1.0
            features[f"suffix-{width}={token[-width:]}"] = 1.0
    return features


def _span_examples(
    rows: Sequence[TrainingRow],
) -> tuple[list[dict[str, float]], np.ndarray, list[tuple[int, int]]]:
    features: list[dict[str, float]] = []
    targets: list[int] = []
    row_ranges: list[tuple[int, int]] = []
    for row in rows:
        start = len(features)
        features.extend(_span_token_features(row.span_tokens, index) for index in range(len(row.span_tokens)))
        targets.extend(row.span_targets)
        row_ranges.append((start, len(features)))
    return features, np.asarray(targets, dtype=np.int8), row_ranges


def _span_metric(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    row_ranges: Sequence[tuple[int, int]],
    threshold: float,
) -> dict[str, Any]:
    metric = _metric(y_true, probabilities, threshold)
    predicted = (probabilities >= threshold).astype(np.int8)
    exact = sum(
        int(np.array_equal(y_true[start:end], predicted[start:end]))
        for start, end in row_ranges
    )
    metric.update(
        {
            "row_count": len(row_ranges),
            "row_exact_match_count": exact,
            "row_exact_match_rate": round(exact / max(1, len(row_ranges)), 6),
            "predicted_condition_token_count": int(predicted.sum()),
        }
    )
    return metric


def _fit_span_split(
    training: Sequence[TrainingRow],
    calibration: Sequence[TrainingRow],
    evaluation: Sequence[TrainingRow],
) -> tuple[LogisticRegression | None, float | None, dict[str, Any]]:
    train_features, y_train, _ = _span_examples(training)
    calibration_features, y_calibration, calibration_row_ranges = _span_examples(calibration)
    eval_features, y_eval, row_ranges = _span_examples(evaluation)
    positive = int(y_train.sum())
    negative = int(len(y_train) - positive)
    calibration_positive = int(y_calibration.sum())
    calibration_negative = int(len(y_calibration) - calibration_positive)
    if (
        positive < 20
        or negative < 20
        or calibration_positive < 1
        or calibration_negative < 1
        or not len(y_eval)
    ):
        return None, None, {
            "status": "RULE_ONLY_INSUFFICIENT_TOKEN_SUPPORT",
            "train_positive_tokens": positive,
            "train_negative_tokens": negative,
            "calibration_positive_tokens": calibration_positive,
            "calibration_negative_tokens": calibration_negative,
        }
    vectorizer = _span_vectorizer()
    train_matrix = vectorizer.transform(train_features)
    calibration_matrix = vectorizer.transform(calibration_features)
    eval_matrix = vectorizer.transform(eval_features)
    model = LogisticRegression(
        class_weight="balanced",
        C=1.5,
        max_iter=1_000,
        random_state=1729,
        solver="liblinear",
    )
    model.fit(train_matrix, y_train)
    calibration_probabilities = model.predict_proba(calibration_matrix)[:, 1]
    candidates = [
        _span_metric(y_calibration, calibration_probabilities, calibration_row_ranges, value)
        for value in np.arange(0.20, 0.951, 0.025)
    ]
    high_precision = [row for row in candidates if row["precision"] >= 0.90]
    best = max(
        high_precision or candidates,
        key=lambda row: (row["f1"], row["recall"], row["row_exact_match_rate"], row["threshold"]),
    )
    threshold = float(best["threshold"])
    evaluation_probabilities = model.predict_proba(eval_matrix)[:, 1]
    metric = _span_metric(y_eval, evaluation_probabilities, row_ranges, threshold)
    metric.update(
        {
            "status": "TRAINED"
            if int(y_eval.sum()) > 0 and int(len(y_eval) - y_eval.sum()) > 0
            else "TRAINED_EVALUATION_SUPPORT_LIMITED",
            "precision_gate": 0.90,
            "precision_gate_passed": bool(
                metric["precision"] >= 0.90 and int(y_eval.sum()) > 0
            ),
            "train_positive_tokens": positive,
            "train_negative_tokens": negative,
            "calibration_positive_tokens": calibration_positive,
            "calibration_negative_tokens": calibration_negative,
            "evaluation_positive_tokens": int(y_eval.sum()),
            "evaluation_negative_tokens": int(len(y_eval) - y_eval.sum()),
            "calibration_metrics": best,
        }
    )
    return model, threshold, metric


def _evaluate_span_group_transfer(rows: Sequence[TrainingRow]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for source, target in (("group_1", "group_2"), ("group_2", "group_1")):
        source_rows = [row for row in rows if row.group_code == source]
        training, calibration = chronological_train_calibration_split(source_rows)
        evaluation = [row for row in rows if row.group_code == target]
        _, _, metric = _fit_span_split(training, calibration, evaluation)
        output[f"{source}_to_{target}"] = {
            "training_count": len(training),
            "calibration_count": len(calibration),
            "evaluation_count": len(evaluation),
            "metrics": metric,
        }
    return output


def _label_value(row: TrainingRow, label: str) -> int:
    if label == "HAS_CONDITION":
        return int(row.has_condition)
    return int(label in row.families)


def _metric(y_true: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict[str, Any]:
    predicted = (probabilities >= threshold).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true,
        predicted,
        average="binary",
        zero_division=0,
    )
    return {
        "threshold": round(float(threshold), 4),
        "precision": round(float(precision), 6),
        "recall": round(float(recall), 6),
        "f1": round(float(f1), 6),
        "support_positive": int(y_true.sum()),
        "support_negative": int(len(y_true) - y_true.sum()),
    }


def _select_threshold(y_true: np.ndarray, probabilities: np.ndarray) -> tuple[float, dict[str, Any]]:
    candidates = [_metric(y_true, probabilities, value) for value in np.arange(0.20, 0.951, 0.025)]
    high_precision = [row for row in candidates if row["precision"] >= 0.90]
    pool = high_precision or candidates
    best = max(pool, key=lambda row: (row["f1"], row["recall"], row["precision"], row["threshold"]))
    return float(best["threshold"]), best


def _fit_split(
    training: Sequence[TrainingRow],
    calibration: Sequence[TrainingRow],
    evaluation: Sequence[TrainingRow],
    *,
    labels: Sequence[str],
    min_label_support: int,
) -> tuple[
    dict[str, LogisticRegression],
    dict[str, PlattCalibrator],
    dict[str, float],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    vectorizer = _vectorizer()
    train_matrix = vectorizer.transform([row.model_text for row in training])
    calibration_matrix = vectorizer.transform([row.model_text for row in calibration])
    eval_matrix = vectorizer.transform([row.model_text for row in evaluation])
    models: dict[str, LogisticRegression] = {}
    calibrators: dict[str, PlattCalibrator] = {}
    thresholds: dict[str, float] = {}
    policies: dict[str, dict[str, Any]] = {}
    metrics: dict[str, Any] = {}
    for label in labels:
        y_train = np.asarray([_label_value(row, label) for row in training], dtype=np.int8)
        y_calibration = np.asarray(
            [_label_value(row, label) for row in calibration], dtype=np.int8
        )
        y_eval = np.asarray([_label_value(row, label) for row in evaluation], dtype=np.int8)
        positives = int(y_train.sum())
        negatives = int(len(y_train) - positives)
        calibration_positive = int(y_calibration.sum())
        calibration_negative = int(len(y_calibration) - calibration_positive)
        if (
            positives < min_label_support
            or negatives < min_label_support
            or calibration_positive < 1
            or calibration_negative < 1
        ):
            metrics[label] = {
                "status": "RULE_ONLY_INSUFFICIENT_TRAIN_OR_CALIBRATION_SUPPORT",
                "train_positive": positives,
                "train_negative": negatives,
                "calibration_positive": calibration_positive,
                "calibration_negative": calibration_negative,
                "evaluation_positive": int(y_eval.sum()),
            }
            continue
        model = LogisticRegression(
            class_weight="balanced",
            C=2.0,
            max_iter=1_000,
            random_state=1729,
            solver="liblinear",
        )
        model.fit(train_matrix, y_train)
        raw_calibration_probability = model.predict_proba(calibration_matrix)[:, 1]
        calibrator, oof_probability, calibration_report = fit_oof_platt_calibrator(
            y_calibration,
            raw_calibration_probability,
        )
        if calibrator is None or oof_probability is None:
            metrics[label] = {
                "status": "RULE_ONLY_INSUFFICIENT_CALIBRATION_SUPPORT",
                "train_positive": positives,
                "train_negative": negatives,
                "calibration_positive": calibration_positive,
                "calibration_negative": calibration_negative,
                "evaluation_positive": int(y_eval.sum()),
                "calibration": calibration_report,
            }
            continue
        policy = select_abstention_thresholds(y_calibration, oof_probability)
        raw_evaluation_probability = model.predict_proba(eval_matrix)[:, 1]
        evaluation_probability = calibrator.predict(raw_evaluation_probability)
        threshold = policy.get("positive_threshold")
        metric = _metric(
            y_eval,
            evaluation_probability,
            float(threshold) if threshold is not None else 1.0001,
        )
        metric["status"] = "CALIBRATED" if policy["status"] == "READY" else "CALIBRATED_ABSTAIN_ONLY"
        metric["precision_gate"] = 0.90
        metric["precision_gate_passed"] = bool(
            policy["status"] == "READY"
            and metric["precision"] >= 0.90
            and int(y_eval.sum()) > 0
        )
        metric["train_positive"] = positives
        metric["train_negative"] = negatives
        metric["calibration_positive"] = calibration_positive
        metric["calibration_negative"] = calibration_negative
        metric["calibration"] = calibration_report
        metric["abstention_policy"] = policy
        metric["evaluation_abstention"] = evaluate_abstention_policy(
            y_eval,
            evaluation_probability,
            policy,
        )
        metric["evaluation_probability_calibration"] = {
            "raw": calibration_metrics(y_eval, raw_evaluation_probability),
            "calibrated": calibration_metrics(y_eval, evaluation_probability),
        }
        metrics[label] = metric
        models[label] = model
        calibrators[label] = calibrator
        policies[label] = policy
        if threshold is not None:
            thresholds[label] = float(threshold)
    return models, calibrators, thresholds, policies, metrics


def _aggregate_metrics(metrics: Mapping[str, Any]) -> dict[str, float | int | None]:
    trained = [
        row
        for row in metrics.values()
        if row.get("status") in {"CALIBRATED", "CALIBRATED_ABSTAIN_ONLY", "TRAINED"}
    ]
    if not trained:
        return {
            "trained_label_count": 0,
            "precision_gate_passed_count": 0,
            "macro_f1": None,
            "macro_precision": None,
            "macro_recall": None,
        }
    return {
        "trained_label_count": len(trained),
        "precision_gate_passed_count": sum(
            bool(row.get("precision_gate_passed")) for row in trained
        ),
        "macro_f1": round(sum(float(row["f1"]) for row in trained) / len(trained), 6),
        "macro_precision": round(sum(float(row["precision"]) for row in trained) / len(trained), 6),
        "macro_recall": round(sum(float(row["recall"]) for row in trained) / len(trained), 6),
    }


def _evaluate_group_transfer(
    rows: Sequence[TrainingRow],
    *,
    labels: Sequence[str],
    min_label_support: int,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for source, target in (("group_1", "group_2"), ("group_2", "group_1")):
        source_rows = [row for row in rows if row.group_code == source]
        training, calibration = chronological_train_calibration_split(source_rows)
        evaluation = [row for row in rows if row.group_code == target]
        _, _, _, _, metrics = _fit_split(
            training,
            calibration,
            evaluation,
            labels=labels,
            min_label_support=min_label_support,
        )
        output[f"{source}_to_{target}"] = {
            "training_count": len(training),
            "calibration_count": len(calibration),
            "evaluation_count": len(evaluation),
            "labels": metrics,
            "aggregate": _aggregate_metrics(metrics),
        }
    return output


def _review_candidates(
    rows: Sequence[TrainingRow],
    models: Mapping[str, LogisticRegression],
    calibrators: Mapping[str, PlattCalibrator],
    policies: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = 250,
) -> list[dict[str, Any]]:
    condition_model = models.get("HAS_CONDITION")
    calibrator = calibrators.get("HAS_CONDITION")
    policy = policies.get("HAS_CONDITION")
    if condition_model is None or calibrator is None or policy is None:
        return []
    matrix = _vectorizer().transform([row.model_text for row in rows])
    probabilities = calibrator.predict(condition_model.predict_proba(matrix)[:, 1])
    positive_threshold = policy.get("positive_threshold")
    negative_threshold = policy.get("negative_threshold")
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row, probability in zip(rows, probabilities):
        disagreement = (
            (
                positive_threshold is not None
                and not row.has_condition
                and probability >= float(positive_threshold)
            )
            or (
                negative_threshold is not None
                and row.has_condition
                and probability <= float(negative_threshold)
            )
            or (
                negative_threshold is not None
                and positive_threshold is not None
                and float(negative_threshold) < probability < float(positive_threshold)
            )
        )
        if not disagreement:
            continue
        score = abs(float(probability) - 0.5)
        ranked.append(
            (
                score,
                {
                    "sample_digest": row.opaque_digest,
                    "group_code": row.group_code,
                    "event_date": row.event_time_utc[:10],
                    "settlement_term": row.settlement_term,
                    "session_phase": row.session_phase,
                    "weak_has_condition": row.has_condition,
                    "predicted_probability": round(float(probability), 6),
                },
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]["sample_digest"]))
    return [item for _, item in ranked[:limit]]


def _benchmark_inference(
    rows: Sequence[TrainingRow],
    models: Mapping[str, LogisticRegression],
    calibrators: Mapping[str, PlattCalibrator],
) -> dict[str, Any]:
    samples = rows[-min(40, len(rows)) :]
    durations: list[float] = []
    vectorizer = _vectorizer()
    for index, row in enumerate(samples):
        started = time.perf_counter()
        matrix = vectorizer.transform([row.model_text])
        for label, model in models.items():
            raw = model.predict_proba(matrix)[:, 1]
            calibrators[label].predict(raw)
        elapsed = (time.perf_counter() - started) * 1_000
        if index >= 5:
            durations.append(elapsed)
    ordered = sorted(durations)
    return {
        "device": "cpu",
        "warmup_count": 5,
        "measured_count": len(durations),
        "trained_head_count": len(models),
        "latency_ms_p50": round(statistics.median(ordered), 3),
        "latency_ms_p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def _parse_minute(value: str) -> int:
    hour_text, minute_text = str(value).split(":", 1)
    hour, minute = int(hour_text), int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("market time must be HH:MM")
    return hour * 60 + minute


def train(args: argparse.Namespace) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[1]
    output_dir = _safe_output_dir(args.output_dir, repository_root=repository_root)
    rows = load_training_rows(
        args.conversation_db,
        staging_database=args.staging_db,
        market_open_minute=args.market_open_minute,
        market_close_minute=args.market_close_minute,
    )
    if len(rows) < 100:
        raise RuntimeError("condition_training_sample_count_too_small")
    training, calibration, evaluation = chronological_three_way_split(rows)
    labels = ("HAS_CONDITION", *CONDITION_FAMILIES)
    temporal_models, calibrators, thresholds, policies, temporal_metrics = _fit_split(
        training,
        calibration,
        evaluation,
        labels=labels,
        min_label_support=args.min_label_support,
    )
    temporal_span_model, span_threshold, temporal_span_metrics = _fit_span_split(
        training, calibration, evaluation
    )
    quality_gate_passed_labels = sorted(
        label
        for label, metric in temporal_metrics.items()
        if metric.get("status") == "CALIBRATED" and metric.get("precision_gate_passed")
    )
    quality_gate_blocked_labels = sorted(
        set(temporal_models) - set(quality_gate_passed_labels)
    )
    candidates = _review_candidates(rows, temporal_models, calibrators, policies)
    source_digest = sha256()
    for row in rows:
        source_digest.update(bytes.fromhex(row.opaque_digest))
    created_at = _utc_now()
    implementation_paths = (
        Path(__file__).resolve(),
        repository_root / "scripts/coin_offer_condition_calibration.py",
        repository_root / "core/market_intelligence/coin_offer_conditions.py",
        repository_root / "apps/coin_rate_estimator/requirements-research.txt",
    )
    implementation_sources = {
        str(path.relative_to(repository_root)): sha256(path.read_bytes()).hexdigest()
        for path in implementation_paths
    }
    runtime_versions = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scikit_learn": sklearn.__version__,
        "joblib": joblib.__version__,
    }
    artifact = {
        "artifact_version": ARTIFACT_VERSION,
        "trainer_version": TRAINER_VERSION,
        "taxonomy_version": CONDITION_TAXONOMY_VERSION,
        "created_at_utc": created_at,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "vectorizer": _vectorizer(),
        "models": temporal_models,
        "probability_calibrators": calibrators,
        "thresholds": thresholds,
        "abstention_policies": policies,
        "trained_labels": sorted(temporal_models),
        "quality_gate_passed_labels": quality_gate_passed_labels,
        "quality_gate_blocked_labels": quality_gate_blocked_labels,
        "condition_span_vectorizer": _span_vectorizer(),
        "condition_span_model": temporal_span_model,
        "condition_span_threshold": span_threshold,
        "condition_span_status": "TRAINED" if temporal_span_model is not None else "RULE_ONLY",
        "fit_partition": "TEMPORAL_TRAINING_ONLY",
        "threshold_partition": "TEMPORAL_CALIBRATION_ONLY",
        "evaluation_partition_used_for_fit_or_threshold": False,
        "market_open_minute": args.market_open_minute,
        "market_close_minute": args.market_close_minute,
        "source_fingerprint": source_digest.hexdigest(),
        "implementation_sources": implementation_sources,
        "runtime_versions": runtime_versions,
        "privacy": {
            "raw_text_retained": False,
            "message_ids_retained": False,
            "sender_identity_retained": False,
            "reversible_vocabulary_retained": False,
        },
    }
    artifact_path = output_dir / "coin-offer-condition-model.joblib"
    joblib.dump(artifact, artifact_path, compress=3)
    os.chmod(artifact_path, 0o600)
    artifact_sha = sha256(artifact_path.read_bytes()).hexdigest()

    group_counts = Counter(row.group_code for row in rows)
    source_partition_counts = Counter(row.source_partition for row in rows)
    settlement_counts = Counter(row.settlement_term for row in rows)
    phase_counts = Counter(row.session_phase for row in rows)
    family_counts = Counter(family for row in rows for family in row.families)
    deadline_counts = Counter(row.deadline_bucket for row in rows)
    report = {
        "schema_version": "coin-offer-condition-training-report-v3",
        "created_at_utc": created_at,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "trainer_version": TRAINER_VERSION,
        "taxonomy_version": CONDITION_TAXONOMY_VERSION,
        "implementation_sources": implementation_sources,
        "runtime_versions": runtime_versions,
        "source": {
            "database_name": args.conversation_db.name,
            "row_count_after_deduplication": len(rows),
            "source_fingerprint": source_digest.hexdigest(),
            "raw_text_retained": False,
        },
        "distribution": {
            "groups": dict(sorted(group_counts.items())),
            "source_partitions": dict(sorted(source_partition_counts.items())),
            "settlements": dict(sorted(settlement_counts.items())),
            "session_phases": dict(sorted(phase_counts.items())),
            "condition_families": dict(sorted(family_counts.items())),
            "deadline_horizons": dict(sorted(deadline_counts.items())),
            "conditional_count": sum(row.has_condition for row in rows),
            "unconditional_count": sum(not row.has_condition for row in rows),
            "unique_composite_class_count": len({row.composite_class for row in rows}),
        },
        "evaluation": {
            "label_source": "AGENT_RULE_AUDITED_SILVER_NOT_OWNER_GROUND_TRUTH",
            "threshold_selection_partition": "calibration",
            "evaluation_threshold_locked": True,
            "cross_group_target_used_for_threshold_selection": False,
            "temporal_split": {
                "training_count": len(training),
                "calibration_count": len(calibration),
                "evaluation_count": len(evaluation),
                "training_end_utc": training[-1].event_time_utc,
                "calibration_start_utc": calibration[0].event_time_utc,
                "calibration_end_utc": calibration[-1].event_time_utc,
                "evaluation_start_utc": evaluation[0].event_time_utc,
                "labels": temporal_metrics,
                "aggregate": _aggregate_metrics(temporal_metrics),
            },
            "cross_group": _evaluate_group_transfer(
                rows,
                labels=labels,
                min_label_support=args.min_label_support,
            ),
            "condition_span_extraction": {
                "label_source": "HIGH_PRECISION_RULE_SPANS_NOT_HUMAN_GROUND_TRUTH",
                "temporal_split": temporal_span_metrics,
                "cross_group": _evaluate_span_group_transfer(rows),
            },
        },
        "cpu_benchmark": _benchmark_inference(
            rows, temporal_models, calibrators
        ),
        "artifact": {
            "filename": artifact_path.name,
            "sha256": artifact_sha,
            "trained_labels": sorted(temporal_models),
            "quality_gate_passed_labels": quality_gate_passed_labels,
            "quality_gate_blocked_labels": quality_gate_blocked_labels,
            "rule_only_labels": sorted(set(labels) - set(temporal_models)),
        },
        "review_queue": {
            "filename": "coin-offer-condition-review-candidates.json",
            "count": len(candidates),
            "raw_text_retained": False,
        },
        "limitations": [
            "Weak labels are not owner-reviewed ground truth.",
            "Condition-span metrics are measured against deterministic weak spans, not human boundaries.",
            "Deadline interpretation maps unqualified hours 1..7 to 13:00..19:00.",
            "Market phase uses the configured historical clock, not retroactive runtime settings.",
            "No runtime model, tolerance policy, or live database was modified.",
        ],
    }
    report_path = output_dir / "coin-offer-condition-training-report.json"
    candidates_path = output_dir / "coin-offer-condition-review-candidates.json"
    _write_json(report_path, report)
    _write_json(
        candidates_path,
        {
            "schema_version": "coin-offer-condition-review-queue-v1",
            "created_at_utc": created_at,
            "source_fingerprint": source_digest.hexdigest(),
            "raw_text_retained": False,
            "candidates": candidates,
        },
    )
    return {
        "report": str(report_path),
        "artifact": str(artifact_path),
        "review_queue": str(candidates_path),
        "artifact_sha256": artifact_sha,
        "row_count": len(rows),
        "trained_labels": sorted(temporal_models),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation-db", type=Path, required=True)
    parser.add_argument(
        "--staging-db",
        type=Path,
        help="Optional protected three-day raw staging DB used read-only for recent offers.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--market-open", dest="market_open_minute", type=_parse_minute, default=DEFAULT_OPEN)
    parser.add_argument("--market-close", dest="market_close_minute", type=_parse_minute, default=DEFAULT_CLOSE)
    parser.add_argument("--min-label-support", type=int, default=20)
    return parser


DEFAULT_OPEN = _parse_minute("10:00")
DEFAULT_CLOSE = _parse_minute("15:00")


def main() -> int:
    args = build_parser().parse_args()
    if args.min_label_support < 5:
        raise SystemExit("--min-label-support must be >= 5")
    result = train(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
