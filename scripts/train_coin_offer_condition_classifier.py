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
from hashlib import blake2b, sha256
import json
import os
from pathlib import Path
import re
import sqlite3
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
from core.market_intelligence.coin_groups import (
    CoinGroupMessageInput,
    parse_coin_group_offers,
)


TRAINER_VERSION = "coin-offer-condition-trainer-v2"
ARTIFACT_VERSION = "coin-offer-condition-research-artifact-v2"
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
    digest = blake2b(digest_size=20, person=b"coin-cond-row-v1")
    for value in (group_code, event_time_utc, settlement_term, trade_form, model_text):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


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
    evaluation: Sequence[TrainingRow],
) -> tuple[LogisticRegression | None, float | None, dict[str, Any]]:
    train_features, y_train, _ = _span_examples(training)
    eval_features, y_eval, row_ranges = _span_examples(evaluation)
    positive = int(y_train.sum())
    negative = int(len(y_train) - positive)
    if positive < 20 or negative < 20 or not len(y_eval):
        return None, None, {
            "status": "RULE_ONLY_INSUFFICIENT_TOKEN_SUPPORT",
            "train_positive_tokens": positive,
            "train_negative_tokens": negative,
        }
    vectorizer = _span_vectorizer()
    train_matrix = vectorizer.transform(train_features)
    eval_matrix = vectorizer.transform(eval_features)
    model = LogisticRegression(
        class_weight="balanced",
        C=1.5,
        max_iter=1_000,
        random_state=1729,
        solver="liblinear",
    )
    model.fit(train_matrix, y_train)
    probabilities = model.predict_proba(eval_matrix)[:, 1]
    candidates = [
        _span_metric(y_eval, probabilities, row_ranges, value)
        for value in np.arange(0.20, 0.81, 0.05)
    ]
    high_precision = [row for row in candidates if row["precision"] >= 0.90]
    best = max(
        high_precision or candidates,
        key=lambda row: (row["f1"], row["recall"], row["row_exact_match_rate"], row["threshold"]),
    )
    best.update(
        {
            "status": "TRAINED",
            "precision_gate": 0.90,
            "precision_gate_passed": bool(best["precision"] >= 0.90),
            "train_positive_tokens": positive,
            "train_negative_tokens": negative,
            "evaluation_positive_tokens": int(y_eval.sum()),
            "evaluation_negative_tokens": int(len(y_eval) - y_eval.sum()),
        }
    )
    return model, float(best["threshold"]), best


def _fit_final_span_model(rows: Sequence[TrainingRow]) -> LogisticRegression | None:
    features, target, _ = _span_examples(rows)
    if int(target.sum()) < 20 or int(len(target) - target.sum()) < 20:
        return None
    matrix = _span_vectorizer().transform(features)
    model = LogisticRegression(
        class_weight="balanced",
        C=1.5,
        max_iter=1_000,
        random_state=1729,
        solver="liblinear",
    )
    model.fit(matrix, target)
    return model


def _evaluate_span_group_transfer(rows: Sequence[TrainingRow]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for source, target in (("group_1", "group_2"), ("group_2", "group_1")):
        training = [row for row in rows if row.group_code == source]
        evaluation = [row for row in rows if row.group_code == target]
        _, _, metric = _fit_span_split(training, evaluation)
        output[f"{source}_to_{target}"] = {
            "training_count": len(training),
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
    candidates = [_metric(y_true, probabilities, value) for value in np.arange(0.20, 0.81, 0.05)]
    high_precision = [row for row in candidates if row["precision"] >= 0.90]
    pool = high_precision or candidates
    best = max(pool, key=lambda row: (row["f1"], row["recall"], row["precision"], row["threshold"]))
    return float(best["threshold"]), best


def _fit_split(
    training: Sequence[TrainingRow],
    evaluation: Sequence[TrainingRow],
    *,
    labels: Sequence[str],
    min_label_support: int,
) -> tuple[dict[str, LogisticRegression], dict[str, float], dict[str, Any]]:
    vectorizer = _vectorizer()
    train_matrix = vectorizer.transform([row.model_text for row in training])
    eval_matrix = vectorizer.transform([row.model_text for row in evaluation])
    models: dict[str, LogisticRegression] = {}
    thresholds: dict[str, float] = {}
    metrics: dict[str, Any] = {}
    for label in labels:
        y_train = np.asarray([_label_value(row, label) for row in training], dtype=np.int8)
        y_eval = np.asarray([_label_value(row, label) for row in evaluation], dtype=np.int8)
        positives = int(y_train.sum())
        negatives = int(len(y_train) - positives)
        if positives < min_label_support or negatives < min_label_support:
            metrics[label] = {
                "status": "RULE_ONLY_INSUFFICIENT_TRAIN_SUPPORT",
                "train_positive": positives,
                "train_negative": negatives,
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
        probabilities = model.predict_proba(eval_matrix)[:, 1]
        threshold, metric = _select_threshold(y_eval, probabilities)
        metric["status"] = "TRAINED"
        metric["precision_gate"] = 0.90
        metric["precision_gate_passed"] = bool(metric["precision"] >= 0.90)
        metric["train_positive"] = positives
        metric["train_negative"] = negatives
        metrics[label] = metric
        models[label] = model
        thresholds[label] = threshold
    return models, thresholds, metrics


def _aggregate_metrics(metrics: Mapping[str, Any]) -> dict[str, float | int | None]:
    trained = [row for row in metrics.values() if row.get("status") == "TRAINED"]
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
        training = [row for row in rows if row.group_code == source]
        evaluation = [row for row in rows if row.group_code == target]
        _, _, metrics = _fit_split(
            training,
            evaluation,
            labels=labels,
            min_label_support=min_label_support,
        )
        output[f"{source}_to_{target}"] = {
            "training_count": len(training),
            "evaluation_count": len(evaluation),
            "labels": metrics,
            "aggregate": _aggregate_metrics(metrics),
        }
    return output


def _fit_final_models(
    rows: Sequence[TrainingRow],
    *,
    labels: Sequence[str],
    min_label_support: int,
    thresholds: Mapping[str, float],
) -> dict[str, LogisticRegression]:
    matrix = _vectorizer().transform([row.model_text for row in rows])
    output: dict[str, LogisticRegression] = {}
    for label in labels:
        if label not in thresholds:
            continue
        target = np.asarray([_label_value(row, label) for row in rows], dtype=np.int8)
        if int(target.sum()) < min_label_support or int(len(target) - target.sum()) < min_label_support:
            continue
        model = LogisticRegression(
            class_weight="balanced",
            C=2.0,
            max_iter=1_000,
            random_state=1729,
            solver="liblinear",
        )
        model.fit(matrix, target)
        output[label] = model
    return output


def _review_candidates(
    rows: Sequence[TrainingRow],
    models: Mapping[str, LogisticRegression],
    thresholds: Mapping[str, float],
    *,
    limit: int = 250,
) -> list[dict[str, Any]]:
    condition_model = models.get("HAS_CONDITION")
    if condition_model is None:
        return []
    matrix = _vectorizer().transform([row.model_text for row in rows])
    probabilities = condition_model.predict_proba(matrix)[:, 1]
    threshold = float(thresholds.get("HAS_CONDITION", 0.5))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row, probability in zip(rows, probabilities):
        disagreement = (
            (not row.has_condition and probability >= max(0.65, threshold))
            or (row.has_condition and probability <= min(0.35, threshold))
            or abs(float(probability) - threshold) <= 0.08
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
    split_index = min(len(rows) - 1, max(1, int(len(rows) * 0.80)))
    training, evaluation = rows[:split_index], rows[split_index:]
    labels = ("HAS_CONDITION", *CONDITION_FAMILIES)
    _, thresholds, temporal_metrics = _fit_split(
        training,
        evaluation,
        labels=labels,
        min_label_support=args.min_label_support,
    )
    final_models = _fit_final_models(
        rows,
        labels=labels,
        min_label_support=args.min_label_support,
        thresholds=thresholds,
    )
    _, span_threshold, temporal_span_metrics = _fit_span_split(training, evaluation)
    final_span_model = _fit_final_span_model(rows)
    quality_gate_passed_labels = sorted(
        label
        for label, metric in temporal_metrics.items()
        if metric.get("status") == "TRAINED" and metric.get("precision_gate_passed")
    )
    quality_gate_blocked_labels = sorted(set(final_models) - set(quality_gate_passed_labels))
    candidates = _review_candidates(rows, final_models, thresholds)
    source_digest = sha256()
    for row in rows:
        source_digest.update(bytes.fromhex(row.opaque_digest))
    created_at = _utc_now()
    artifact = {
        "artifact_version": ARTIFACT_VERSION,
        "trainer_version": TRAINER_VERSION,
        "taxonomy_version": CONDITION_TAXONOMY_VERSION,
        "created_at_utc": created_at,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "vectorizer": _vectorizer(),
        "models": final_models,
        "thresholds": thresholds,
        "trained_labels": sorted(final_models),
        "quality_gate_passed_labels": quality_gate_passed_labels,
        "quality_gate_blocked_labels": quality_gate_blocked_labels,
        "condition_span_vectorizer": _span_vectorizer(),
        "condition_span_model": final_span_model,
        "condition_span_threshold": span_threshold,
        "condition_span_status": "TRAINED" if final_span_model is not None else "RULE_ONLY",
        "market_open_minute": args.market_open_minute,
        "market_close_minute": args.market_close_minute,
        "source_fingerprint": source_digest.hexdigest(),
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
        "schema_version": "coin-offer-condition-training-report-v1",
        "created_at_utc": created_at,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "trainer_version": TRAINER_VERSION,
        "taxonomy_version": CONDITION_TAXONOMY_VERSION,
        "sklearn_version": sklearn.__version__,
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
            "label_source": "HIGH_PRECISION_WEAK_SUPERVISION_NOT_HUMAN_GROUND_TRUTH",
            "temporal_split": {
                "training_count": len(training),
                "evaluation_count": len(evaluation),
                "training_end_utc": training[-1].event_time_utc,
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
        "artifact": {
            "filename": artifact_path.name,
            "sha256": artifact_sha,
            "trained_labels": sorted(final_models),
            "quality_gate_passed_labels": quality_gate_passed_labels,
            "quality_gate_blocked_labels": quality_gate_blocked_labels,
            "rule_only_labels": sorted(set(labels) - set(final_models)),
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
        "trained_labels": sorted(final_models),
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
