#!/usr/bin/env python3
"""Train a CPU-bounded Persian encoder probe for coin-offer conditions.

This research command freezes a pinned Persian Transformer and trains only
small condition-boundary and multi-label family heads.  It consumes transient
private text in memory, writes no text/vocabulary/identity, and cannot promote
or install a runtime model.  Full encoder fine-tuning is deliberately deferred
until an owner-reviewed ground-truth set exists.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import statistics
import time
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import safetensors
import sklearn
import sentencepiece
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_fscore_support
import torch
import transformers
from transformers import AutoModel, AutoTokenizer

from core.market_intelligence.coin_offer_conditions import (
    CONDITION_FAMILIES,
    CONDITION_TAXONOMY_VERSION,
)
from scripts.train_coin_offer_condition_classifier import (
    TrainingRow,
    _safe_output_dir,
    chronological_three_way_split,
    chronological_train_calibration_split,
    load_training_rows,
)
from scripts.coin_offer_condition_calibration import (
    PlattCalibrator,
    calibration_metrics,
    evaluate_abstention_policy,
    fit_oof_platt_calibrator,
    select_abstention_thresholds,
)


TRAINER_VERSION = "coin-offer-condition-neural-probe-v3"
ARTIFACT_VERSION = "coin-offer-condition-neural-probe-artifact-v3"
DEFAULT_MODEL_ID = "HooshvareLab/distilbert-fa-zwnj-base"
DEFAULT_MODEL_REVISION = "e8b934b8c81b17c5e4a1a90325f5f25ced94e8d6"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def _metric(y_true: np.ndarray, probability: np.ndarray, threshold: float) -> dict[str, Any]:
    predicted = (probability >= threshold).astype(np.int8)
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
        "precision_gate": 0.90,
        "precision_gate_passed": bool(precision >= 0.90),
    }


def _select_threshold(y_true: np.ndarray, probability: np.ndarray) -> tuple[float, dict[str, Any]]:
    candidates = [_metric(y_true, probability, value) for value in np.arange(0.20, 0.951, 0.025)]
    precision_gated = [row for row in candidates if row["precision_gate_passed"]]
    best = max(
        precision_gated or candidates,
        key=lambda row: (row["f1"], row["recall"], row["precision"], row["threshold"]),
    )
    return float(best["threshold"]), best


def _load_encoder(model_id: str, revision: str, cache_dir: Path):
    tokenizer = AutoTokenizer.from_pretrained(
        model_id,
        revision=revision,
        cache_dir=cache_dir,
        trust_remote_code=False,
        use_fast=True,
    )
    model = AutoModel.from_pretrained(
        model_id,
        revision=revision,
        cache_dir=cache_dir,
        trust_remote_code=False,
    )
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return tokenizer, model


def _encode_rows(
    rows: Sequence[TrainingRow],
    *,
    tokenizer,
    model,
    batch_size: int,
    max_length: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[int, int]]]:
    sentence_vectors: list[np.ndarray] = []
    token_vectors: list[np.ndarray] = []
    token_targets: list[int] = []
    row_ranges: list[tuple[int, int]] = []
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    with torch.inference_mode():
        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start : batch_start + batch_size]
            encoding = tokenizer(
                [list(row.span_tokens) for row in batch],
                is_split_into_words=True,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            hidden = model(**encoding).last_hidden_state.detach().cpu().numpy()
            sentence_vectors.extend(hidden[:, 0, :])
            for batch_index, row in enumerate(batch):
                start = len(token_vectors)
                word_ids = encoding.word_ids(batch_index=batch_index)
                seen: set[int] = set()
                for token_index, word_index in enumerate(word_ids):
                    if word_index is None or word_index in seen or word_index >= len(row.span_targets):
                        continue
                    seen.add(word_index)
                    token_vectors.append(hidden[batch_index, token_index, :])
                    token_targets.append(int(row.span_targets[word_index]))
                row_ranges.append((start, len(token_vectors)))
    return (
        np.asarray(sentence_vectors, dtype=np.float32),
        np.asarray(token_vectors, dtype=np.float32),
        np.asarray(token_targets, dtype=np.int8),
        row_ranges,
    )


def _row_token_indices(
    selected_rows: Sequence[int],
    row_ranges: Sequence[tuple[int, int]],
) -> tuple[np.ndarray, list[tuple[int, int]]]:
    indices: list[int] = []
    selected_ranges: list[tuple[int, int]] = []
    for row_index in selected_rows:
        start = len(indices)
        source_start, source_end = row_ranges[row_index]
        indices.extend(range(source_start, source_end))
        selected_ranges.append((start, len(indices)))
    return np.asarray(indices, dtype=np.int64), selected_ranges


def _fit_binary(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_calibration: np.ndarray,
    y_calibration: np.ndarray,
    x_eval: np.ndarray,
    y_eval: np.ndarray,
    *,
    minimum_support: int,
) -> tuple[
    LogisticRegression | None,
    PlattCalibrator | None,
    dict[str, Any] | None,
    float | None,
    dict[str, Any],
]:
    positive = int(y_train.sum())
    negative = int(len(y_train) - positive)
    calibration_positive = int(y_calibration.sum())
    calibration_negative = int(len(y_calibration) - calibration_positive)
    if (
        positive < minimum_support
        or negative < minimum_support
        or calibration_positive < 1
        or calibration_negative < 1
        or not len(y_eval)
    ):
        return None, None, None, None, {
            "status": "INSUFFICIENT_SUPPORT",
            "train_positive": positive,
            "train_negative": negative,
            "calibration_positive": calibration_positive,
            "calibration_negative": calibration_negative,
            "evaluation_positive": int(y_eval.sum()),
        }
    model = LogisticRegression(
        class_weight="balanced",
        C=1.0,
        max_iter=1_000,
        random_state=1729,
        solver="liblinear",
    )
    model.fit(x_train, y_train)
    raw_calibration_probability = model.predict_proba(x_calibration)[:, 1]
    calibrator, oof_probability, calibration_report = fit_oof_platt_calibrator(
        y_calibration, raw_calibration_probability
    )
    if calibrator is None or oof_probability is None:
        return None, None, None, None, {
            "status": "RULE_ONLY_INSUFFICIENT_CALIBRATION_SUPPORT",
            "train_positive": positive,
            "train_negative": negative,
            "calibration_positive": calibration_positive,
            "calibration_negative": calibration_negative,
            "evaluation_positive": int(y_eval.sum()),
            "calibration": calibration_report,
        }
    policy = select_abstention_thresholds(y_calibration, oof_probability)
    raw_evaluation_probability = model.predict_proba(x_eval)[:, 1]
    evaluation_probability = calibrator.predict(raw_evaluation_probability)
    threshold = policy.get("positive_threshold")
    metric = _metric(
        y_eval,
        evaluation_probability,
        float(threshold) if threshold is not None else 1.0001,
    )
    metric.update(
        {
            "status": "CALIBRATED"
            if policy["status"] == "READY"
            else "CALIBRATED_ABSTAIN_ONLY",
            "precision_gate_passed": bool(
                policy["status"] == "READY"
                and metric["precision"] >= 0.90
                and int(y_eval.sum()) > 0
            ),
            "train_positive": positive,
            "train_negative": negative,
            "calibration_positive": calibration_positive,
            "calibration_negative": calibration_negative,
            "calibration": calibration_report,
            "abstention_policy": policy,
            "evaluation_abstention": evaluate_abstention_policy(
                y_eval, evaluation_probability, policy
            ),
            "evaluation_probability_calibration": {
                "raw": calibration_metrics(y_eval, raw_evaluation_probability),
                "calibrated": calibration_metrics(y_eval, evaluation_probability),
            },
        }
    )
    return model, calibrator, policy, float(threshold) if threshold is not None else None, metric


def _span_metric(
    y_true: np.ndarray,
    probability: np.ndarray,
    row_ranges: Sequence[tuple[int, int]],
    threshold: float,
) -> dict[str, Any]:
    metric = _metric(y_true, probability, threshold)
    predicted = (probability >= threshold).astype(np.int8)
    exact = sum(
        int(np.array_equal(y_true[start:end], predicted[start:end]))
        for start, end in row_ranges
    )
    metric.update(
        {
            "row_count": len(row_ranges),
            "row_exact_match_count": exact,
            "row_exact_match_rate": round(exact / max(1, len(row_ranges)), 6),
        }
    )
    return metric


def _fit_span(
    token_vectors: np.ndarray,
    token_targets: np.ndarray,
    row_ranges: Sequence[tuple[int, int]],
    train_rows: Sequence[int],
    calibration_rows: Sequence[int],
    eval_rows: Sequence[int],
) -> tuple[LogisticRegression, float, dict[str, Any]]:
    train_indices, _ = _row_token_indices(train_rows, row_ranges)
    calibration_indices, calibration_ranges = _row_token_indices(
        calibration_rows, row_ranges
    )
    eval_indices, eval_ranges = _row_token_indices(eval_rows, row_ranges)
    model = LogisticRegression(
        class_weight="balanced",
        C=1.0,
        max_iter=1_000,
        random_state=1729,
        solver="liblinear",
    )
    model.fit(token_vectors[train_indices], token_targets[train_indices])
    calibration_probability = model.predict_proba(
        token_vectors[calibration_indices]
    )[:, 1]
    candidates = [
        _span_metric(
            token_targets[calibration_indices],
            calibration_probability,
            calibration_ranges,
            value,
        )
        for value in np.arange(0.20, 0.951, 0.025)
    ]
    precision_gated = [row for row in candidates if row["precision_gate_passed"]]
    best = max(
        precision_gated or candidates,
        key=lambda row: (row["f1"], row["recall"], row["row_exact_match_rate"], row["threshold"]),
    )
    threshold = float(best["threshold"])
    evaluation_probability = model.predict_proba(token_vectors[eval_indices])[:, 1]
    metric = _span_metric(
        token_targets[eval_indices], evaluation_probability, eval_ranges, threshold
    )
    metric.update(
        {
            "status": "TRAINED"
            if int(token_targets[eval_indices].sum()) > 0
            and int(len(eval_indices) - token_targets[eval_indices].sum()) > 0
            else "TRAINED_EVALUATION_SUPPORT_LIMITED",
            "precision_gate_passed": bool(
                metric["precision"] >= 0.90
                and int(token_targets[eval_indices].sum()) > 0
            ),
            "train_positive_tokens": int(token_targets[train_indices].sum()),
            "train_negative_tokens": int(len(train_indices) - token_targets[train_indices].sum()),
            "calibration_positive_tokens": int(
                token_targets[calibration_indices].sum()
            ),
            "calibration_negative_tokens": int(
                len(calibration_indices) - token_targets[calibration_indices].sum()
            ),
            "evaluation_positive_tokens": int(token_targets[eval_indices].sum()),
            "evaluation_negative_tokens": int(len(eval_indices) - token_targets[eval_indices].sum()),
            "calibration_metrics": best,
        }
    )
    return model, threshold, metric


def _family_target(rows: Sequence[TrainingRow], label: str) -> np.ndarray:
    if label == "HAS_CONDITION":
        return np.asarray([int(row.has_condition) for row in rows], dtype=np.int8)
    return np.asarray([int(label in row.families) for row in rows], dtype=np.int8)


def _fit_family_heads(
    rows: Sequence[TrainingRow],
    vectors: np.ndarray,
    train_rows: Sequence[int],
    calibration_rows: Sequence[int],
    eval_rows: Sequence[int],
    *,
    minimum_support: int,
) -> tuple[
    dict[str, LogisticRegression],
    dict[str, PlattCalibrator],
    dict[str, dict[str, Any]],
    dict[str, float],
    dict[str, Any],
]:
    models: dict[str, LogisticRegression] = {}
    calibrators: dict[str, PlattCalibrator] = {}
    policies: dict[str, dict[str, Any]] = {}
    thresholds: dict[str, float] = {}
    metrics: dict[str, Any] = {}
    train_index = np.asarray(train_rows, dtype=np.int64)
    calibration_index = np.asarray(calibration_rows, dtype=np.int64)
    eval_index = np.asarray(eval_rows, dtype=np.int64)
    for label in ("HAS_CONDITION", *CONDITION_FAMILIES):
        target = _family_target(rows, label)
        model, calibrator, policy, threshold, metric = _fit_binary(
            vectors[train_index],
            target[train_index],
            vectors[calibration_index],
            target[calibration_index],
            vectors[eval_index],
            target[eval_index],
            minimum_support=minimum_support,
        )
        metrics[label] = metric
        if model is not None and calibrator is not None and policy is not None:
            models[label] = model
            calibrators[label] = calibrator
            policies[label] = policy
            if threshold is not None:
                thresholds[label] = threshold
    return models, calibrators, policies, thresholds, metrics


def _aggregate(metrics: Mapping[str, Any]) -> dict[str, Any]:
    trained = [
        value
        for value in metrics.values()
        if value.get("status") in {"CALIBRATED", "CALIBRATED_ABSTAIN_ONLY"}
    ]
    return {
        "trained_label_count": len(trained),
        "precision_gate_passed_count": sum(bool(value["precision_gate_passed"]) for value in trained),
        "macro_precision": round(statistics.fmean(value["precision"] for value in trained), 6)
        if trained
        else None,
        "macro_recall": round(statistics.fmean(value["recall"] for value in trained), 6)
        if trained
        else None,
        "macro_f1": round(statistics.fmean(value["f1"] for value in trained), 6) if trained else None,
    }


def _benchmark_encoder(rows: Sequence[TrainingRow], tokenizer, model, *, max_length: int) -> dict[str, Any]:
    samples = [list(row.span_tokens) for row in rows[-min(40, len(rows)) :]]
    durations: list[float] = []
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    with torch.inference_mode():
        for index, sample in enumerate(samples):
            encoding = tokenizer(
                [sample],
                is_split_into_words=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            started = time.perf_counter()
            model(**encoding)
            elapsed = (time.perf_counter() - started) * 1_000
            if index >= 5:
                durations.append(elapsed)
    ordered = sorted(durations)
    return {
        "device": "cpu",
        "thread_count": torch.get_num_threads(),
        "warmup_count": 5,
        "measured_count": len(durations),
        "latency_ms_p50": round(statistics.median(ordered), 3),
        "latency_ms_p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
    }


def train(args: argparse.Namespace) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[1]
    output_dir = _safe_output_dir(args.output_dir, repository_root=repository_root)
    cache_dir = args.cache_dir.expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(cache_dir, 0o700)
    rows = load_training_rows(
        args.conversation_db,
        staging_database=args.staging_db,
        market_open_minute=args.market_open_minute,
        market_close_minute=args.market_close_minute,
    )
    if len(rows) < 100:
        raise RuntimeError("condition_neural_probe_sample_count_too_small")
    tokenizer, encoder = _load_encoder(args.model_id, args.model_revision, cache_dir)
    sentence_vectors, token_vectors, token_targets, row_ranges = _encode_rows(
        rows,
        tokenizer=tokenizer,
        model=encoder,
        batch_size=args.batch_size,
        max_length=args.max_length,
    )
    training_partition, calibration_partition, evaluation_partition = (
        chronological_three_way_split(rows)
    )
    training_count = len(training_partition)
    calibration_count = len(calibration_partition)
    train_rows = list(range(training_count))
    calibration_rows = list(
        range(training_count, training_count + calibration_count)
    )
    eval_rows = list(range(training_count + calibration_count, len(rows)))
    (
        family_models,
        family_calibrators,
        family_policies,
        family_thresholds,
        family_metrics,
    ) = _fit_family_heads(
        rows,
        sentence_vectors,
        train_rows,
        calibration_rows,
        eval_rows,
        minimum_support=args.min_label_support,
    )
    span_model, span_threshold, span_metrics = _fit_span(
        token_vectors,
        token_targets,
        row_ranges,
        train_rows,
        calibration_rows,
        eval_rows,
    )
    group_transfer: dict[str, Any] = {}
    span_transfer: dict[str, Any] = {}
    for source, target in (("group_1", "group_2"), ("group_2", "group_1")):
        source_row_objects = [row for row in rows if row.group_code == source]
        source_training_objects, _ = (
            chronological_train_calibration_split(source_row_objects)
        )
        source_indices = [index for index, row in enumerate(rows) if row.group_code == source]
        source_training_count = len(source_training_objects)
        source_rows = source_indices[:source_training_count]
        source_calibration_rows = source_indices[source_training_count:]
        target_rows = [index for index, row in enumerate(rows) if row.group_code == target]
        _, _, _, _, metrics = _fit_family_heads(
            rows,
            sentence_vectors,
            source_rows,
            source_calibration_rows,
            target_rows,
            minimum_support=args.min_label_support,
        )
        _, _, span_metric = _fit_span(
            token_vectors,
            token_targets,
            row_ranges,
            source_rows,
            source_calibration_rows,
            target_rows,
        )
        group_transfer[f"{source}_to_{target}"] = {
            "training_count": len(source_rows),
            "calibration_count": len(source_calibration_rows),
            "evaluation_count": len(target_rows),
            "labels": metrics,
            "aggregate": _aggregate(metrics),
        }
        span_transfer[f"{source}_to_{target}"] = span_metric

    source_digest = sha256()
    for row in rows:
        source_digest.update(bytes.fromhex(row.opaque_digest))
    created_at = _utc_now()
    model_commit = str(getattr(encoder.config, "_commit_hash", None) or args.model_revision)
    implementation_paths = (
        Path(__file__).resolve(),
        repository_root / "scripts/coin_offer_condition_calibration.py",
        repository_root / "scripts/train_coin_offer_condition_classifier.py",
        repository_root / "core/market_intelligence/coin_offer_conditions.py",
        repository_root / "apps/coin_rate_estimator/requirements-condition-research.txt",
    )
    implementation_sources = {
        str(path.relative_to(repository_root)): sha256(path.read_bytes()).hexdigest()
        for path in implementation_paths
    }
    runtime_versions = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scikit_learn": sklearn.__version__,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "sentencepiece": sentencepiece.__version__,
        "safetensors": safetensors.__version__,
        "joblib": joblib.__version__,
    }
    quality_passed = sorted(
        label
        for label, metric in family_metrics.items()
        if metric.get("status") == "CALIBRATED" and metric.get("precision_gate_passed")
    )
    artifact = {
        "artifact_version": ARTIFACT_VERSION,
        "trainer_version": TRAINER_VERSION,
        "taxonomy_version": CONDITION_TAXONOMY_VERSION,
        "created_at_utc": created_at,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "encoder": {
            "model_id": args.model_id,
            "requested_revision": args.model_revision,
            "resolved_revision": model_commit,
            "max_length": args.max_length,
            "weights_embedded": False,
            "fine_tuned": False,
        },
        "family_heads": family_models,
        "family_probability_calibrators": family_calibrators,
        "family_abstention_policies": family_policies,
        "family_thresholds": family_thresholds,
        "condition_span_head": span_model,
        "condition_span_threshold": span_threshold,
        "fit_partition": "TEMPORAL_TRAINING_ONLY",
        "threshold_partition": "TEMPORAL_CALIBRATION_ONLY",
        "evaluation_partition_used_for_fit_or_threshold": False,
        "quality_gate_passed_labels": quality_passed,
        "quality_gate_blocked_labels": sorted(set(family_models) - set(quality_passed)),
        "source_fingerprint": source_digest.hexdigest(),
        "implementation_sources": implementation_sources,
        "runtime_versions": runtime_versions,
        "privacy": {
            "raw_text_retained": False,
            "message_ids_retained": False,
            "sender_identity_retained": False,
            "encoder_vocabulary_embedded": False,
        },
    }
    artifact_path = output_dir / "coin-offer-condition-neural-probe.joblib"
    joblib.dump(artifact, artifact_path, compress=3)
    os.chmod(artifact_path, 0o600)
    artifact_sha = sha256(artifact_path.read_bytes()).hexdigest()
    report = {
        "schema_version": "coin-offer-condition-neural-probe-report-v3",
        "created_at_utc": created_at,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "trainer_version": TRAINER_VERSION,
        "taxonomy_version": CONDITION_TAXONOMY_VERSION,
        "source": {
            "row_count_after_deduplication": len(rows),
            "source_fingerprint": source_digest.hexdigest(),
            "raw_text_retained": False,
        },
        "encoder": artifact["encoder"],
        "implementation_sources": implementation_sources,
        "runtime_versions": runtime_versions,
        "distribution": {
            "groups": dict(sorted(Counter(row.group_code for row in rows).items())),
            "settlements": dict(sorted(Counter(row.settlement_term for row in rows).items())),
            "session_phases": dict(sorted(Counter(row.session_phase for row in rows).items())),
            "condition_families": dict(
                sorted(Counter(family for row in rows for family in row.families).items())
            ),
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
                "training_count": len(train_rows),
                "calibration_count": len(calibration_rows),
                "evaluation_count": len(eval_rows),
                "training_end_utc": training_partition[-1].event_time_utc,
                "calibration_start_utc": calibration_partition[0].event_time_utc,
                "calibration_end_utc": calibration_partition[-1].event_time_utc,
                "evaluation_start_utc": evaluation_partition[0].event_time_utc,
                "family_labels": family_metrics,
                "family_aggregate": _aggregate(family_metrics),
                "condition_span": span_metrics,
            },
            "cross_group_family": group_transfer,
            "cross_group_span": span_transfer,
        },
        "cpu_benchmark": _benchmark_encoder(rows, tokenizer, encoder, max_length=args.max_length),
        "artifact": {
            "filename": artifact_path.name,
            "sha256": artifact_sha,
            "encoder_weights_embedded": False,
            "quality_gate_passed_labels": quality_passed,
            "quality_gate_blocked_labels": sorted(set(family_models) - set(quality_passed)),
        },
        "limitations": [
            "Encoder is frozen; only linear heads are trained in this first neural baseline.",
            "Weak labels and spans are not owner-reviewed ground truth.",
            "Sentence-family probabilities use OOF Platt calibration and an explicit abstention interval.",
            "Token-span boundaries retain calibration-only threshold selection; no row-leaking token OOF calibrator is claimed.",
            "Sparse families remain insufficient for a learned production decision.",
            "No runtime model, tolerance policy, live database, staging, or production was modified.",
        ],
    }
    report_path = output_dir / "coin-offer-condition-neural-probe-report.json"
    _write_json(report_path, report)
    return {
        "report": str(report_path),
        "artifact": str(artifact_path),
        "report_sha256": sha256(report_path.read_bytes()).hexdigest(),
        "artifact_sha256": artifact_sha,
        "row_count": len(rows),
        "status": report["status"],
    }


def _parse_minute(value: str) -> int:
    hour_text, minute_text = value.split(":", 1)
    hour, minute = int(hour_text), int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("market time must be HH:MM")
    return hour * 60 + minute


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation-db", type=Path, required=True)
    parser.add_argument("--staging-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--market-open", dest="market_open_minute", type=_parse_minute, default=600)
    parser.add_argument("--market-close", dest="market_close_minute", type=_parse_minute, default=900)
    parser.add_argument("--min-label-support", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-length", type=int, default=96)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.min_label_support < 5:
        raise SystemExit("--min-label-support must be >= 5")
    if not 32 <= args.max_length <= 256:
        raise SystemExit("--max-length must be between 32 and 256")
    result = train(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
