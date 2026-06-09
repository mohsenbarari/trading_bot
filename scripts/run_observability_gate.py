from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

OBSERVABILITY_TEST_MODULES = (
    "tests.test_logging_foundation",
    "tests.test_request_logging",
    "tests.test_error_tracking",
    "tests.test_job_logging",
    "tests.test_sync_worker",
    "tests.test_audit_logger",
    "tests.test_audit_export",
    "tests.test_metrics",
    "tests.test_main_metrics_guard",
    "tests.test_sync_health_endpoint",
    "tests.test_sync_health_monitor",
    "tests.test_observability_config",
)

OBSERVABILITY_PATH_PREFIXES = (
    "observability/",
    "docs/OBSERVABILITY",
)

OBSERVABILITY_PATHS = {
    ".github/workflows/merge-gate.yml",
    ".github/workflows/pre-release-gate.yml",
    "Makefile",
    "docker-compose.observability.yml",
    "main.py",
    "core/error_tracking.py",
    "core/job_logging.py",
    "core/log_redaction.py",
    "core/logging_config.py",
    "core/metrics.py",
    "core/request_logging.py",
    "core/sync_worker.py",
    "bot/middlewares/logging_context.py",
    "api/routers/sync.py",
    "scripts/run_observability_gate.py",
}


def is_observability_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in OBSERVABILITY_PATHS:
        return True
    return normalized.startswith(OBSERVABILITY_PATH_PREFIXES)


def get_changed_paths(base_ref: str, head_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}..{head_ref}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git diff failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def run_observability_tests() -> int:
    command = [sys.executable, "-m", "unittest", *OBSERVABILITY_TEST_MODULES]
    result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    return result.returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the focused observability regression gate.")
    parser.add_argument("--base-ref", help="Optional git base ref for diff-aware observability change notes.")
    parser.add_argument("--head-ref", default="HEAD", help="Optional git head ref for diff-aware observability change notes.")
    parser.add_argument("--list", action="store_true", help="Print the focused test modules and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.list:
        for module in OBSERVABILITY_TEST_MODULES:
            print(module)
        return 0

    if args.base_ref:
        try:
            changed_paths = get_changed_paths(args.base_ref, args.head_ref)
        except RuntimeError as exc:
            print(f"[observability-gate] unable to inspect git diff: {exc}", file=sys.stderr)
            return 2

        observability_changes = sorted(path for path in changed_paths if is_observability_path(path))
        if observability_changes:
            print("[observability-gate] observability-related changes detected:")
            for path in observability_changes:
                print(f"  - {path}")
        else:
            print("[observability-gate] no observability-specific paths changed in the inspected diff.")

    return run_observability_tests()


if __name__ == "__main__":
    raise SystemExit(main())
