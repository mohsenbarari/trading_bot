#!/usr/bin/env python3
"""Create/verify Stage 4 inputs or run explicitly authorized staging drivers."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from core.telegram_queue_stage4_harness import (
    Stage4HarnessValidationError,
    canonical_json,
    sha256_text,
    stage4_config_binding_sha256,
    validate_stage4_run_config,
    verify_stage4_live_evidence,
    verify_stage4_plan,
    write_stage4_plan,
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_commit(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _git_clean(repo: Path) -> bool:
    return not subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repo, text=True
    ).strip()


def _command(value: Any, *, name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise Stage4HarnessValidationError(f"stage4_{name}_command_invalid")
    executable = Path(value[0])
    if not executable.is_absolute() or not executable.is_file() or executable.is_symlink():
        raise Stage4HarnessValidationError(f"stage4_{name}_executable_invalid")
    return tuple(value)


def _validate_authorization(
    *,
    authorization: Mapping[str, Any],
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
    git_commit: str,
    sender: Sequence[str],
    observer: Sequence[str],
) -> None:
    try:
        expires = datetime.fromisoformat(str(authorization["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise Stage4HarnessValidationError("stage4_authorization_expiry_invalid") from exc
    expected = {
        "schema_version": 1,
        "allow_live_staging": True,
        "run_id": manifest["run_id"],
        "git_commit": git_commit,
        "trace_sha256": manifest["trace_sha256"],
        "config_sha256": stage4_config_binding_sha256(config),
        "driver_commands_sha256": sha256_text(canonical_json({"sender": list(sender), "observer": list(observer)})),
    }
    for key, value in expected.items():
        if authorization.get(key) != value:
            raise Stage4HarnessValidationError(f"stage4_authorization_mismatch:{key}")
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise Stage4HarnessValidationError("stage4_authorization_expired")


def _execute_live(args: argparse.Namespace, repo: Path) -> dict[str, Any]:
    if not args.authorize_live_staging:
        raise Stage4HarnessValidationError("stage4_live_confirmation_flag_required")
    config = _json(args.config)
    validate_stage4_run_config(config, live_execution=True)
    manifest = verify_stage4_plan(args.output_dir)
    commit = _git_commit(repo)
    if not _git_clean(repo) or commit != manifest.get("git_commit"):
        raise Stage4HarnessValidationError("stage4_live_requires_clean_pinned_git_commit")
    if manifest.get("environment") != "staging" or manifest.get("run_id") != config.get("run_id"):
        raise Stage4HarnessValidationError("stage4_live_plan_config_mismatch")
    if manifest.get("config_sha256") != stage4_config_binding_sha256(config):
        raise Stage4HarnessValidationError("stage4_live_plan_config_hash_mismatch")
    drivers = config.get("drivers")
    if not isinstance(drivers, Mapping):
        raise Stage4HarnessValidationError("stage4_live_drivers_missing")
    sender = _command(drivers.get("sender"), name="sender")
    observer = _command(drivers.get("observer"), name="observer")
    _validate_authorization(
        authorization=_json(args.authorization),
        config=config,
        manifest=manifest,
        git_commit=commit,
        sender=sender,
        observer=observer,
    )
    run_env = {
        **os.environ,
        "STAGE4_RUN_DIR": str(args.output_dir.resolve()),
        "STAGE4_MANIFEST": str((args.output_dir / "manifest.json").resolve()),
        "STAGE4_EVENT_TRACE": str((args.output_dir / "event_trace.jsonl").resolve()),
    }
    observer_process = subprocess.Popen(observer, cwd=repo, env=run_env)
    try:
        ready = args.output_dir / "observer.ready.json"
        deadline = time.monotonic() + float(args.observer_ready_timeout)
        while time.monotonic() < deadline and observer_process.poll() is None:
            if ready.is_file():
                receipt = _json(ready)
                if receipt.get("run_id") == manifest["run_id"] and receipt.get("trace_sha256") == manifest["trace_sha256"]:
                    break
            time.sleep(0.1)
        else:
            raise Stage4HarnessValidationError("stage4_observer_not_ready")
        sender_result = subprocess.run(sender, cwd=repo, env=run_env, check=False)
        if sender_result.returncode != 0:
            raise Stage4HarnessValidationError("stage4_sender_failed")
        observer_returncode = observer_process.wait(timeout=float(args.observer_exit_timeout))
        if observer_returncode != 0:
            raise Stage4HarnessValidationError("stage4_observer_failed")
    finally:
        if observer_process.poll() is None:
            observer_process.terminate()
            try:
                observer_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                observer_process.kill()
                observer_process.wait(timeout=5)
    return verify_stage4_live_evidence(args.output_dir)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("plan", "verify-plan", "execute", "verify-live"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--authorize-live-staging", action="store_true")
    parser.add_argument("--observer-ready-timeout", type=float, default=30.0)
    parser.add_argument("--observer-exit-timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        if args.command == "plan":
            if args.config is None or args.fixture is None or args.seed is None:
                raise Stage4HarnessValidationError("stage4_plan_arguments_missing")
            result = write_stage4_plan(
                output_dir=args.output_dir,
                config=_json(args.config),
                fixture=_json(args.fixture),
                seed=args.seed,
                git_commit=_git_commit(repo),
            )
        elif args.command == "verify-plan":
            result = verify_stage4_plan(args.output_dir)
        elif args.command == "execute":
            if args.config is None or args.authorization is None:
                raise Stage4HarnessValidationError("stage4_execute_arguments_missing")
            result = _execute_live(args, repo)
        else:
            result = verify_stage4_live_evidence(args.output_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(canonical_json({"status": "blocked", "reason": str(exc)}))
        return 2
    print(canonical_json({"status": "ok", "result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
