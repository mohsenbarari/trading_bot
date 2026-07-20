#!/usr/bin/env python3
"""Prove the one-commit, one-line Queue activation transition for Full Matrix."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes


GIT = "/usr/bin/git"
TARGET = "core/telegram_delivery_runtime_policy.py"
DISABLED = b"TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = False"
ENABLED = b"TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = True"
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


class QueueActivationTransitionError(RuntimeError):
    pass


def _git(repo: Path, *arguments: str, text: bool = True):  # noqa: ANN202
    result = subprocess.run(
        [GIT, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *arguments],
        capture_output=True,
        check=False,
        text=text,
        stdin=subprocess.DEVNULL,
        timeout=30,
        env=SAFE_ENV,
    )
    if result.returncode != 0:
        raise QueueActivationTransitionError("Git transition verification failed closed")
    return result.stdout


def _exact_sha(repo: Path, value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise QueueActivationTransitionError("transition SHA must be a full lowercase Git SHA")
    resolved = _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}").strip()
    if resolved != value:
        raise QueueActivationTransitionError("transition SHA does not resolve exactly")
    return value


def verify_transition(
    repo: Path,
    *,
    baseline_sha: str,
    activation_sha: str,
    require_checkout: bool,
) -> dict[str, str]:
    baseline = _exact_sha(repo, baseline_sha)
    activation = _exact_sha(repo, activation_sha)
    parents = _git(repo, "show", "-s", "--format=%P", activation).strip().split()
    if parents != [baseline]:
        raise QueueActivationTransitionError(
            "activation must be one non-merge commit directly above the reviewed baseline"
        )
    changed = [
        line
        for line in _git(
            repo, "diff-tree", "--no-commit-id", "--name-only", "-r", activation
        ).splitlines()
        if line
    ]
    if changed != [TARGET]:
        raise QueueActivationTransitionError(
            "activation commit changes files outside the one reviewed runtime gate"
        )
    before = _git(repo, "show", f"{baseline}:{TARGET}", text=False)
    after = _git(repo, "show", f"{activation}:{TARGET}", text=False)
    if before.count(DISABLED) != 1 or ENABLED in before:
        raise QueueActivationTransitionError("baseline Queue gate is not exactly disabled")
    if after != before.replace(DISABLED, ENABLED, 1):
        raise QueueActivationTransitionError(
            "activation bytes differ from the single False-to-True gate change"
        )
    _git(repo, "diff", "--check", baseline, activation)
    if require_checkout:
        if _git(repo, "rev-parse", "HEAD").strip() != activation:
            raise QueueActivationTransitionError("worktree is not checked out at activation SHA")
        if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
            raise QueueActivationTransitionError("activation worktree is not clean")
    diff = _git(repo, "diff", "--binary", baseline, activation, "--", TARGET, text=False)
    return {
        "schema": "three-site-staging-queue-activation-transition-v1",
        "status": "verified",
        "baseline_sha": baseline,
        "activation_sha": activation,
        "changed_path": TARGET,
        "transition_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--activation-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-detached-verification", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify_transition(
            args.repo,
            baseline_sha=args.baseline_sha,
            activation_sha=args.activation_sha,
            require_checkout=not args.allow_detached_verification,
        )
        write_secure_atomic_bytes(
            args.output,
            (json.dumps(result, sort_keys=True, indent=2) + "\n").encode(),
            label="Queue activation transition evidence",
            mode=0o600,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
