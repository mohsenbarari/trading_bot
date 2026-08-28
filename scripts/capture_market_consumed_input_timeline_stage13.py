#!/usr/bin/env python3
"""Capture signed, redacted Stage 13 consumed-input timeline evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Mapping, Sequence

from pydantic import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.consumed_input_parity import (
    SAMPLE_CONTRACT,
    ConsumedInputParityError,
    build_report,
    compare_rate_sets,
    compare_signal_sets,
    estimator_inputs_as_signals,
    hmac_reference,
    stamp,
    transition_trace,
    utc,
)
from core.market_intelligence.market_snapshot import (
    AtomicMarketSnapshotProvider,
    MarketSnapshotError,
    MarketSnapshotUnavailable,
    build_market_snapshot,
)
from core.market_intelligence.market_store import (
    MarketStoreError,
    connect_market_store_read_only,
    verify_market_store_read_only,
)
from core.market_intelligence.private_pipeline_contracts import EstimatorSnapshotV2
from core.market_intelligence.shadow_parity import (
    ShadowParityError,
    sign_parity_report,
    verify_parity_report,
    write_private_json,
)
from core.market_intelligence.single_owner_parity import (
    SingleOwnerParityError,
    read_private_key,
)


class TimelineCaptureError(RuntimeError):
    """A content-free capture command failure."""


def _existing_file(path: Path, *, field: str) -> Path:
    supplied = path.expanduser()
    if supplied.is_symlink() or not supplied.is_file():
        raise TimelineCaptureError(f"{field}_unavailable")
    return supplied.resolve()


def _outside_repository(path: Path) -> Path:
    target = path.expanduser().resolve()
    try:
        target.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return target
    raise TimelineCaptureError("timeline_output_inside_repository")


def _load_candidate(path: Path) -> dict[str, Any]:
    try:
        snapshot = EstimatorSnapshotV2.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise TimelineCaptureError("candidate_snapshot_unavailable") from exc
    return snapshot.model_dump(mode="json")


def _load_reference(path: Path) -> dict[str, Any]:
    try:
        return dict(AtomicMarketSnapshotProvider(path).load())
    except MarketSnapshotUnavailable as exc:
        raise TimelineCaptureError("reference_snapshot_unavailable") from exc


def _exact_candidate_snapshot(
    market_store_path: Path, *, as_of_utc: datetime
) -> dict[str, Any]:
    connection = connect_market_store_read_only(market_store_path)
    try:
        verify_market_store_read_only(connection)
        connection.execute("BEGIN")
        snapshot = build_market_snapshot(connection, as_of_utc=as_of_utc)
        connection.rollback()
        return snapshot
    except (MarketSnapshotError, MarketStoreError, sqlite3.Error, ValueError) as exc:
        connection.rollback()
        raise TimelineCaptureError("candidate_exact_snapshot_failed") from exc
    finally:
        connection.close()


def _sample(
    *,
    reference: Mapping[str, Any],
    candidate: Mapping[str, Any],
    exact_candidate: Mapping[str, Any],
    identity_key: bytes,
    observed_at_utc: datetime,
) -> dict[str, Any]:
    reference_at = utc(
        str(reference.get("generated_at_utc")), field="reference_generated_at"
    )
    candidate_at = utc(
        str(candidate.get("generated_at_utc")), field="candidate_generated_at"
    )
    reference_signals = reference.get("signals")
    if not isinstance(reference_signals, Mapping):
        raise TimelineCaptureError("reference_signals_invalid")
    candidate_inputs = candidate.get("inputs")
    if not isinstance(candidate_inputs, list):
        raise TimelineCaptureError("candidate_inputs_invalid")
    exact_signals = exact_candidate.get("signals")
    if not isinstance(exact_signals, Mapping):
        raise TimelineCaptureError("candidate_exact_signals_invalid")
    return {
        "contract": SAMPLE_CONTRACT,
        "observed_at_utc": stamp(observed_at_utc),
        "reference_evaluation_at_utc": stamp(reference_at),
        "candidate_evaluation_at_utc": stamp(candidate_at),
        "pair_skew_seconds": round(abs((candidate_at - reference_at).total_seconds()), 3),
        "reference_snapshot_ref": hmac_reference(
            identity_key,
            namespace=b"consumed-input-reference-snapshot-v1",
            document=reference,
        ),
        "candidate_snapshot_ref": hmac_reference(
            identity_key,
            namespace=b"consumed-input-candidate-snapshot-v1",
            document=candidate,
        ),
        "reference_status": str(reference.get("snapshot_status") or "UNKNOWN"),
        "candidate_status": str(candidate.get("status") or "UNKNOWN"),
        "scheduled_signals": compare_signal_sets(
            reference_signals,
            estimator_inputs_as_signals(candidate_inputs),
        ),
        "exact_as_of_signals": compare_signal_sets(reference_signals, exact_signals),
        "scheduled_rates": compare_rate_sets(
            reference,
            candidate,
            candidate_is_estimator_snapshot=True,
        ),
        "exact_as_of_rates": compare_rate_sets(
            reference,
            exact_candidate,
            candidate_is_estimator_snapshot=False,
        ),
    }


def capture(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.reference_samples <= 120:
        raise TimelineCaptureError("reference_sample_count_invalid")
    if not 0.1 <= args.poll_seconds <= 5:
        raise TimelineCaptureError("timeline_poll_seconds_invalid")
    if not 5 <= args.max_duration_seconds <= 21_600:
        raise TimelineCaptureError("timeline_duration_invalid")
    reference_path = _existing_file(args.reference_snapshot, field="reference_snapshot")
    candidate_path = _existing_file(args.candidate_snapshot, field="candidate_snapshot")
    market_store_path = _existing_file(args.candidate_market_store, field="candidate_market_store")
    output_path = _outside_repository(args.output)
    if output_path.exists():
        raise TimelineCaptureError("timeline_output_exists")
    parent = output_path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise TimelineCaptureError("timeline_output_parent_invalid")
    if os.stat(parent).st_mode & 0o077:
        raise TimelineCaptureError("timeline_output_parent_permissions_invalid")
    identity_key = read_private_key(args.identity_key_file, field="timeline_identity_key")
    signing_key = read_private_key(args.signing_key_file, field="timeline_signing_key")

    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + float(args.max_duration_seconds)
    samples: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    candidate_versions: list[int] = []
    seen_reference_times: set[str] = set()
    if not args.include_existing_reference:
        current_reference = _load_reference(reference_path)
        seen_reference_times.add(
            stamp(str(current_reference.get("generated_at_utc")))
        )
    seen_candidate_versions: set[int] = set()
    seen_sources: set[str] = set()
    latest_candidate: dict[str, Any] | None = None

    while len(samples) < args.reference_samples:
        if time.monotonic() >= deadline:
            raise TimelineCaptureError("timeline_capture_deadline_exceeded")
        candidate = _load_candidate(candidate_path)
        version = int(candidate.get("snapshot_version") or 0)
        if version <= 0:
            raise TimelineCaptureError("candidate_snapshot_version_invalid")
        if candidate_versions and version < candidate_versions[-1]:
            raise TimelineCaptureError("candidate_snapshot_version_regression")
        latest_candidate = candidate
        if version not in seen_candidate_versions:
            baseline = not candidate_versions
            traces = transition_trace(
                candidate,
                identity_key=identity_key,
                baseline=baseline,
            )
            for trace in traces:
                source_ref = str(trace["source_ref"])
                if source_ref in seen_sources:
                    continue
                seen_sources.add(source_ref)
                transitions.append(trace)
            seen_candidate_versions.add(version)
            candidate_versions.append(version)

        reference = _load_reference(reference_path)
        reference_stamp = stamp(str(reference.get("generated_at_utc")))
        if reference_stamp not in seen_reference_times:
            reference_at = utc(reference_stamp, field="reference_generated_at")
            exact = _exact_candidate_snapshot(
                market_store_path,
                as_of_utc=reference_at,
            )
            samples.append(
                _sample(
                    reference=reference,
                    candidate=latest_candidate,
                    exact_candidate=exact,
                    identity_key=identity_key,
                    observed_at_utc=datetime.now(timezone.utc),
                )
            )
            seen_reference_times.add(reference_stamp)
        if len(samples) < args.reference_samples:
            time.sleep(float(args.poll_seconds))

    completed = datetime.now(timezone.utc)
    report = build_report(
        started_at_utc=started,
        completed_at_utc=completed,
        samples=samples,
        candidate_snapshot_versions=candidate_versions,
        transitions=transitions,
    )
    signed = sign_parity_report(
        report,
        key=signing_key,
        key_id=args.signing_key_id,
    )
    if not verify_parity_report(signed, key=signing_key):
        raise TimelineCaptureError("timeline_report_signature_invalid")
    write_private_json(output_path, signed)
    return {
        "status": "pass",
        "report_hash": signed["report_hash"],
        "reference_sample_count": signed["reference_sample_count"],
        "candidate_snapshot_count": signed["candidate_snapshot_count"],
        "snapshot_timeline_complete": signed["snapshot_timeline_complete"],
        "pair_skew_p95_seconds": signed["pair_skew_p95_seconds"],
        "new_source_transition_count": signed["new_source_transition_count"],
        "new_source_transfer_to_snapshot_p95_seconds": signed[
            "new_source_transfer_to_snapshot_p95_seconds"
        ],
        "promotion_recommendation": signed["promotion_recommendation"],
        "cutover_performed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-snapshot", type=Path, required=True)
    parser.add_argument("--candidate-snapshot", type=Path, required=True)
    parser.add_argument("--candidate-market-store", type=Path, required=True)
    parser.add_argument("--identity-key-file", type=Path, required=True)
    parser.add_argument("--signing-key-file", type=Path, required=True)
    parser.add_argument("--signing-key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-samples", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--max-duration-seconds", type=float, default=420)
    parser.add_argument("--include-existing-reference", action="store_true")
    parser.add_argument("--acknowledge-no-cutover", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = capture(args)
    except (
        ConsumedInputParityError,
        TimelineCaptureError,
        OSError,
        ShadowParityError,
        SingleOwnerParityError,
        ValueError,
        sqlite3.Error,
    ) as exc:
        print(json.dumps({"status": "fail", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
