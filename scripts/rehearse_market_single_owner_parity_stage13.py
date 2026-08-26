#!/usr/bin/env python3
"""Run the redacted Stage 13 single-owner frozen replay rehearsal.

This command never changes a live feed or publishes a product snapshot.  It
requires two explicit acknowledgements because it makes short-lived protected
copies of sensitive capture and database state before removing the
temporary workspace.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.shadow_parity import (  # noqa: E402
    ShadowParityError,
    verify_parity_report,
)
from core.market_intelligence.single_owner_parity import (  # noqa: E402
    SingleOwnerParityError,
    read_private_key,
    run_single_owner_parity,
)


def _time(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SingleOwnerParityError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SingleOwnerParityError(f"{field}_timezone_required")
    return parsed


def _emit(document: dict[str, object]) -> None:
    print(json.dumps(document, sort_keys=True, separators=(",", ":")), flush=True)


def command_run(args: argparse.Namespace) -> dict[str, object]:
    if not args.acknowledge_no_cutover:
        raise SingleOwnerParityError("no_cutover_acknowledgement_required")
    if not args.confirm_sensitive_ephemeral_copy:
        raise SingleOwnerParityError("sensitive_ephemeral_copy_confirmation_required")
    return run_single_owner_parity(
        repository_root=REPO_ROOT,
        baseline_code_root=args.baseline_code_root,
        candidate_code_root=args.candidate_code_root,
        baseline_market_store=args.baseline_market_store,
        baseline_staging_store=args.baseline_staging_store,
        baseline_writer_lock=args.baseline_writer_lock,
        market_spool_dir=args.market_spool_dir,
        coin_spool_dir=args.coin_spool_dir,
        scratch_root=args.scratch_root,
        artifact_dir=args.artifact_dir,
        identity_key=read_private_key(args.identity_key_file, field="identity_key_file"),
        signing_key=read_private_key(args.signing_key_file, field="signing_key_file"),
        signing_key_id=args.signing_key_id,
        window_start=_time(args.window_start, field="window_start"),
        window_end=_time(args.window_end, field="window_end"),
        python_executable=args.python_executable,
        maximum_records=args.maximum_records,
        lock_timeout_seconds=args.lock_timeout_seconds,
        subprocess_timeout_seconds=args.subprocess_timeout_seconds,
    )


def command_verify(args: argparse.Namespace) -> dict[str, object]:
    try:
        document = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SingleOwnerParityError("report_read_failed") from exc
    if not isinstance(document, dict):
        raise SingleOwnerParityError("report_object_required")
    key = read_private_key(args.signing_key_file, field="signing_key_file")
    if not verify_parity_report(document, key=key):
        raise SingleOwnerParityError("report_signature_invalid")
    return {
        "status": "pass",
        "report_hash": document.get("report_hash"),
        "signature_key_id": document.get("signature_key_id"),
        "promotion_recommendation": document.get("promotion_recommendation"),
        "cutover_performed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run")
    run.add_argument("--baseline-code-root", type=Path, required=True)
    run.add_argument("--candidate-code-root", type=Path, required=True)
    run.add_argument("--baseline-market-store", type=Path, required=True)
    run.add_argument("--baseline-staging-store", type=Path, required=True)
    run.add_argument("--baseline-writer-lock", type=Path, required=True)
    run.add_argument("--market-spool-dir", type=Path, required=True)
    run.add_argument("--coin-spool-dir", type=Path, required=True)
    run.add_argument("--scratch-root", type=Path, required=True)
    run.add_argument("--artifact-dir", type=Path, required=True)
    run.add_argument("--identity-key-file", type=Path, required=True)
    run.add_argument("--signing-key-file", type=Path, required=True)
    run.add_argument("--signing-key-id", required=True)
    run.add_argument("--window-start", required=True)
    run.add_argument("--window-end", required=True)
    run.add_argument("--python-executable", type=Path, default=Path(sys.executable).resolve())
    run.add_argument("--maximum-records", type=int, default=250_000)
    run.add_argument("--lock-timeout-seconds", type=float, default=130.0)
    run.add_argument("--subprocess-timeout-seconds", type=int, default=900)
    run.add_argument("--acknowledge-no-cutover", action="store_true")
    run.add_argument("--confirm-sensitive-ephemeral-copy", action="store_true")
    verify = subcommands.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--signing-key-file", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = command_run(args) if args.command == "run" else command_verify(args)
        _emit(result)
        return 0
    except (SingleOwnerParityError, ShadowParityError, OSError, sqlite3.Error) as exc:
        _emit({"status": "failed", "reason": str(exc), "cutover_performed": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
