#!/usr/bin/env python3
"""Train a versioned relevance candidate and score group staging rows.

Only the frozen labelled conversation cohort and explicit adjudications are
labels.  Live parser decisions are never fed back as truth.  Repeated loop
execution scores new rows but retrains only when the labelled fingerprint
changes (or ``--force-retrain`` is supplied).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

try:
    from scripts.coin_intelligence_private_ingest.runtime_paths import (
        CONVERSATION_LABEL_DB as LABEL_DB,
        PIPELINE_ROOT as PIPE,
    )
except ModuleNotFoundError:  # Standalone immutable runtime deployment.
    PIPE = Path(__file__).resolve().parent
    LABEL_DB = (
        PIPE.parents[1]
        / "apps/coin-intelligence/data/conversation_events.cleaned_snapshot_20260727.sqlite3"
    )


STAGE = PIPE / "text_staging.sqlite3"
FILTER = PIPE / "group_filter.sqlite3"
MODEL = PIPE / "group_relevance_nb_v2.json"
VERSION = "group-relevance-charword-nb-v2.1-fail-closed-thresholds"
TARGET_KEEP_PRECISION = 0.95
TARGET_REJECT_NPV = 0.98
# A missing validation-qualified threshold means abstention, not a fallback
# decision.  Probabilities are in [0,1], so these values disable the
# corresponding automatic action safely.
DEFAULT_KEEP_THRESHOLD = 1.01
DEFAULT_REJECT_THRESHOLD = 0.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS filter_runs(
 id INTEGER PRIMARY KEY,version TEXT NOT NULL,created_at_utc TEXT NOT NULL,
 metrics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS filter_decisions(
 source_key TEXT NOT NULL,message_id TEXT NOT NULL,
 source_payload_sha256 TEXT NOT NULL,model_probability REAL NOT NULL,
 parser_kind TEXT,decision TEXT NOT NULL,reason TEXT NOT NULL,
 model_version TEXT NOT NULL,updated_at_utc TEXT NOT NULL,
 PRIMARY KEY(source_key,message_id)
);
CREATE INDEX IF NOT EXISTS idx_filter_decision ON filter_decisions(decision);
"""


def now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def norm(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value)
        .translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        .replace("\u200c", " "),
    ).strip().lower()


def feats(text: str) -> set[str]:
    normalized = norm(text)
    words = re.findall(r"[\wآ-ی]+", normalized)
    result = {"w:" + word for word in words}
    compact = re.sub(r"\s+", "_", normalized)
    result |= {
        "c:" + compact[index : index + 3]
        for index in range(max(0, len(compact) - 2))
    }
    return result


def _text_label(row: Sequence) -> tuple[str, int]:
    return str(row[1]), int(row[2])


def fit(rows: Iterable[Sequence]) -> dict:
    documents = Counter()
    counts = {0: Counter(), 1: Counter()}
    for row in rows:
        text, label = _text_label(row)
        documents[label] += 1
        counts[label].update(feats(text))
    return {
        "version": VERSION,
        "docs": dict(documents),
        "positive": dict(counts[1]),
        "negative": dict(counts[0]),
    }


def prob(model: dict, text: str) -> float:
    d0 = int(model["docs"].get("0", model["docs"].get(0, 0)))
    d1 = int(model["docs"].get("1", model["docs"].get(1, 0)))
    positive = Counter(model["positive"])
    negative = Counter(model["negative"])
    a = math.log((d1 + 1) / (d0 + 1))
    b = 0.0
    for feature in feats(text):
        a += math.log((positive[feature] + 1) / (d1 + 2))
        b += math.log((negative[feature] + 1) / (d0 + 2))
    difference = max(-40, min(40, a - b))
    return 1 / (1 + math.exp(-difference))


def labelled(path: Path = LABEL_DB) -> list[tuple[str, str, int, str]]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    query = """WITH positive AS (
        SELECT import_id,message_id FROM offers
        UNION
        SELECT import_id,request_message_id FROM trade_requests
    )
    SELECT m.event_time_utc,m.text,
           CASE WHEN p.message_id IS NULL THEN 0 ELSE 1 END,
           COALESCE(m.source_html_file,'UNKNOWN')
    FROM messages AS m
    LEFT JOIN positive AS p
      ON p.import_id=m.import_id AND p.message_id=m.message_id
    WHERE trim(m.text)<>''
    ORDER BY m.event_time_utc,m.message_id"""
    rows = [tuple(row) for row in connection.execute(query)]
    connection.close()
    return rows


def metrics(
    model: dict, rows: Iterable[Sequence], threshold: float = 0.5
) -> dict:
    tp = fp = tn = fn = 0
    materialized = list(rows)
    for row in materialized:
        text, label = _text_label(row)
        predicted = prob(model, text) >= threshold
        tp += int(predicted and label == 1)
        fp += int(predicted and label == 0)
        tn += int(not predicted and label == 0)
        fn += int(not predicted and label == 1)
    return {
        "holdout_rows": len(materialized),
        "threshold": threshold,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "specificity": tn / (tn + fp) if tn + fp else 0.0,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


def operational_metrics(
    model: dict,
    rows: Iterable[Sequence],
    *,
    keep_threshold: float,
    reject_threshold: float,
) -> dict:
    materialized = list(rows)
    keep_tp = keep_fp = reject_tn = reject_fn = review = 0
    for row in materialized:
        text, label = _text_label(row)
        probability = prob(model, text)
        if probability >= keep_threshold:
            keep_tp += int(label == 1)
            keep_fp += int(label == 0)
        elif probability < reject_threshold:
            reject_tn += int(label == 0)
            reject_fn += int(label == 1)
        else:
            review += 1
    return {
        "rows": len(materialized),
        "keep_threshold": keep_threshold,
        "reject_threshold": reject_threshold,
        "auto_keep_count": keep_tp + keep_fp,
        "auto_keep_precision": (
            keep_tp / (keep_tp + keep_fp) if keep_tp + keep_fp else None
        ),
        "auto_reject_count": reject_tn + reject_fn,
        "auto_reject_negative_predictive_value": (
            reject_tn / (reject_tn + reject_fn)
            if reject_tn + reject_fn
            else None
        ),
        "relevant_rows_auto_rejected": reject_fn,
        "review_count": review,
        "review_rate": review / len(materialized) if materialized else 0.0,
    }


def choose_thresholds(model: dict, rows: Iterable[Sequence]) -> tuple[float, float]:
    materialized = list(rows)
    keep = DEFAULT_KEEP_THRESHOLD
    for threshold in (0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98):
        result = operational_metrics(
            model,
            materialized,
            keep_threshold=threshold,
            reject_threshold=0.0,
        )
        if (
            result["auto_keep_count"] >= 10
            and result["auto_keep_precision"] is not None
            and result["auto_keep_precision"] >= TARGET_KEEP_PRECISION
        ):
            keep = threshold
            break
    reject = DEFAULT_REJECT_THRESHOLD
    valid_rejects = []
    for threshold in (0.01, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20):
        result = operational_metrics(
            model,
            materialized,
            keep_threshold=1.01,
            reject_threshold=threshold,
        )
        if (
            result["auto_reject_count"] >= 10
            and result["auto_reject_negative_predictive_value"] is not None
            and result["auto_reject_negative_predictive_value"]
            >= TARGET_REJECT_NPV
        ):
            valid_rejects.append(threshold)
    if valid_rejects:
        reject = max(valid_rejects)
    return keep, min(reject, keep - 0.01)


def _chronological_split(
    rows: list[tuple[str, str, int, str]],
) -> tuple[list[tuple], list[tuple]]:
    if len(rows) < 10:
        return rows, []
    index = min(len(rows) - 1, max(1, int(len(rows) * 0.80)))
    cutoff = rows[index][0]
    training = [row for row in rows if row[0] < cutoff]
    holdout = [row for row in rows if row[0] >= cutoff]
    if not training or not holdout:
        return rows[:index], rows[index:]
    return training, holdout


def _fingerprint(rows: Iterable[Sequence]) -> str:
    value = hashlib.sha256()
    for row in rows:
        value.update(
            json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        value.update(b"\n")
    return value.hexdigest()


def _atomic_model(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _manual_labels(
    filter_connection: sqlite3.Connection,
    stage_path: Path,
) -> list[tuple[str, str, int, str]]:
    try:
        filter_connection.execute(
            "ATTACH DATABASE ? AS stage", (str(stage_path),)
        )
        rows = [
            ("", str(row[0]), int(row[1]), str(row[2]))
            for row in filter_connection.execute(
                """SELECT t.text,a.label,a.source_key
                FROM adjudicated_labels AS a
                JOIN stage.text_candidates AS t USING(source_key,message_id)
                ORDER BY a.source_key,a.message_id"""
            )
        ]
        filter_connection.execute("DETACH DATABASE stage")
        return rows
    except sqlite3.OperationalError:
        return []


def run(*, force_retrain: bool = False, score_only: bool = False) -> dict:
    filter_connection = sqlite3.connect(FILTER)
    filter_connection.executescript(SCHEMA)
    existing = None
    if MODEL.exists():
        try:
            existing = json.loads(MODEL.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = None
    if score_only and (not existing or existing.get("version") != VERSION):
        raise RuntimeError("score_only_requires_current_versioned_model")
    if score_only:
        frozen_rows: list[tuple[str, str, int, str]] = []
        manual: list[tuple[str, str, int, str]] = []
        labelled_fingerprint = str(existing["labelled_fingerprint_sha256"])
        retrained = False
    else:
        frozen_rows = labelled()
        manual = _manual_labels(filter_connection, STAGE)
        labelled_fingerprint = _fingerprint([*frozen_rows, *manual])
        retrained = bool(
            force_retrain
            or not existing
            or existing.get("version") != VERSION
            or existing.get("labelled_fingerprint_sha256")
            != labelled_fingerprint
        )
    if retrained:
        training_rows, holdout = _chronological_split(frozen_rows)
        validation_model = fit(training_rows)
        keep_threshold, reject_threshold = choose_thresholds(
            validation_model, holdout
        )
        audit = metrics(validation_model, holdout)
        operational = operational_metrics(
            validation_model,
            holdout,
            keep_threshold=keep_threshold,
            reject_threshold=reject_threshold,
        )
        per_source = {}
        grouped: dict[str, list[tuple]] = defaultdict(list)
        for row in holdout:
            grouped[str(row[3])].append(row)
        for source, source_rows in grouped.items():
            per_source[source] = operational_metrics(
                validation_model,
                source_rows,
                keep_threshold=keep_threshold,
                reject_threshold=reject_threshold,
            )
        full = fit([*frozen_rows, *manual])
        artifact_id = hashlib.sha256(
            (labelled_fingerprint + VERSION).encode("utf-8")
        ).hexdigest()[:16]
        existing = {
            "version": VERSION,
            "artifact_id": artifact_id,
            "trained_at_utc": now(),
            "labelled_fingerprint_sha256": labelled_fingerprint,
            "training_rows": len(frozen_rows),
            "adjudicated_rows": len(manual),
            "split": "chronological_80_20_same_timestamp_kept_in_one_side",
            "thresholds": {
                "auto_keep": keep_threshold,
                "auto_reject": reject_threshold,
            },
            "holdout": audit,
            "holdout_operational": operational,
            "holdout_per_source": per_source,
            "model": full,
        }
        _atomic_model(MODEL, existing)
        filter_connection.execute(
            "INSERT INTO filter_runs(version,created_at_utc,metrics_json) VALUES(?,?,?)",
            (
                f"{VERSION}:{artifact_id}",
                now(),
                json.dumps(
                    {
                        "holdout": audit,
                        "operational": operational,
                        "per_source": per_source,
                    }
                ),
            ),
        )

    assert existing is not None
    full = existing["model"]
    keep_threshold = float(existing["thresholds"]["auto_keep"])
    reject_threshold = float(existing["thresholds"]["auto_reject"])
    model_version = f"{VERSION}:{existing['artifact_id']}"
    stage = sqlite3.connect(f"file:{STAGE.resolve()}?mode=ro", uri=True)
    stage.row_factory = sqlite3.Row
    stage_rows = stage.execute(
        """SELECT * FROM text_candidates
        WHERE source_key IN ('account2_group1','account2_group2')"""
    ).fetchall()
    tally = Counter()
    for row in stage_rows:
        override = filter_connection.execute(
            """SELECT label,reason FROM adjudicated_labels
            WHERE source_key=? AND message_id=?""",
            (row["source_key"], row["message_id"]),
        ).fetchone()
        probability = prob(full, row["text"])
        payload = json.loads(row["extracted_json"] or "{}")
        kind = payload.get("kind")
        if override:
            decision, reason = (
                (
                    "KEEP_ADJUDICATED_RELEVANT",
                    "REJECTED_NOISE_ADJUDICATED",
                )[not bool(override[0])],
                override[1],
            )
        elif kind == "OFFER_CANDIDATE":
            decision, reason = "KEEP_OFFER_CANDIDATE", "parser_offer"
        elif str(kind).startswith("REPLY_") and kind not in (
            "REPLY_NEGOTIATION",
            "REPLY_QUESTION",
            "REPLY_QUANTITY_QUESTION",
        ):
            decision, reason = (
                "KEEP_TRADE_REQUEST_CANDIDATE",
                "reply_signal",
            )
        elif probability >= keep_threshold:
            decision, reason = "KEEP_MODEL_CANDIDATE", "model_high_relevance"
        elif probability < reject_threshold:
            decision, reason = (
                "REJECTED_NOISE",
                "model_high_confidence_noise_and_no_parser_signal",
            )
        else:
            decision, reason = "REVIEW", "uncertain"
        filter_connection.execute(
            """INSERT INTO filter_decisions(
              source_key,message_id,source_payload_sha256,model_probability,
              parser_kind,decision,reason,model_version,updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_key,message_id) DO UPDATE SET
              source_payload_sha256=excluded.source_payload_sha256,
              model_probability=excluded.model_probability,
              parser_kind=excluded.parser_kind,decision=excluded.decision,
              reason=excluded.reason,model_version=excluded.model_version,
              updated_at_utc=excluded.updated_at_utc""",
            (
                row["source_key"],
                row["message_id"],
                row["source_payload_sha256"],
                probability,
                kind,
                decision,
                reason,
                model_version,
                now(),
            ),
        )
        tally[decision] += 1
    stage.close()
    filter_connection.commit()
    filter_connection.close()
    return {
        "version": model_version,
        "retrained": retrained,
        "training_rows": int(existing["training_rows"]),
        "adjudicated_rows": int(existing["adjudicated_rows"]),
        "holdout": existing["holdout"],
        "holdout_operational": existing["holdout_operational"],
        "decisions": dict(tally),
        "model": str(MODEL),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-retrain", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    args = parser.parse_args()
    if args.force_retrain and args.score_only:
        parser.error("--force-retrain and --score-only are mutually exclusive")
    print(
        json.dumps(
            run(
                force_retrain=args.force_retrain,
                score_only=args.score_only,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
