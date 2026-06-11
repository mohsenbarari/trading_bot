#!/usr/bin/env python3
"""Report production backup/recoverability readiness.

This report is deliberately operational rather than theoretical: it can run a
fresh backup, optionally do a DB restore smoke test, check live health/sync, and
write a compact artifact for release review.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import DEFAULT_ARTIFACT_ROOT, display_path, run_command, utc_iso, utc_stamp
from scripts.deploy_config import resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNBOOK_PATH = REPO_ROOT / "docs" / "PRODUCTION_RECOVERABILITY_RUNBOOK.md"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the production recoverability readiness report.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--run-backup", action="store_true")
    parser.add_argument("--backup-role", choices={"foreign", "iran", "both"}, default="iran")
    parser.add_argument("--backup-restore-smoke", action="store_true")
    parser.add_argument("--skip-live-checks", action="store_true")
    parser.add_argument("--report-out", default=None)
    return parser.parse_args(argv)


def parse_json_output(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
        if isinstance(payload, dict):
            return payload
    except ValueError:
        pass
    for raw_line in reversed(stdout.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("command did not emit a JSON object")


def command_ok(result: dict[str, Any]) -> bool:
    return int(result.get("exit_code") or 0) == 0 and not result.get("timed_out")


def evaluate_backup(payload: dict[str, Any], *, require_restore_smoke: bool) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if payload.get("status") != "ok":
        failures.append("backup command did not return status=ok")
    for result in payload.get("results") or []:
        files = result.get("files") or []
        kinds = {item.get("kind") for item in files}
        for required_kind in ("db", "redis", "uploads", "audit"):
            if required_kind not in kinds:
                failures.append(f"{result.get('role')} backup is missing {required_kind} artifact")
        for item in files:
            if int(item.get("bytes") or 0) <= 0:
                failures.append(f"{result.get('role')} {item.get('kind')} artifact is empty")
            if not item.get("sha256"):
                failures.append(f"{result.get('role')} {item.get('kind')} artifact has no sha256")
        restore = result.get("restore_smoke") or {}
        if require_restore_smoke and restore.get("status") != "passed":
            failures.append(f"{result.get('role')} DB restore smoke did not pass")
        elif restore.get("status") == "skipped":
            warnings.append(f"{result.get('role')} DB restore smoke was skipped")
    return failures, warnings


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Production Recoverability Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Status: `{payload['status']}`",
        f"- Artifact dir: `{payload['artifact_dir']}`",
        f"- Runbook: `{payload['runbook']['path']}` (`{payload['runbook']['status']}`)",
        "",
        "## Checks",
        "",
    ]
    for check in payload.get("checks") or []:
        lines.append(f"- `{check['name']}`: `{check['status']}`")
    if payload.get("backup"):
        lines.extend(["", "## Backup", ""])
        for result in payload["backup"].get("results") or []:
            lines.append(f"- `{result.get('role')}` manifest: `{result.get('manifest_path')}`")
            lines.append(f"  - restore smoke: `{(result.get('restore_smoke') or {}).get('status')}`")
            for item in result.get("files") or []:
                lines.append(f"  - `{item.get('kind')}`: `{item.get('path')}` ({item.get('bytes')} bytes)")
    if payload.get("failures"):
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {item}" for item in payload["failures"])
    if payload.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in payload["warnings"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stamp = args.timestamp or utc_stamp()
    artifact_dir = Path(args.artifact_root) / stamp / "recoverability"
    logs_dir = artifact_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    settings = resolve_deploy_settings(manifest_path=args.manifest)

    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []
    backup_payload: dict[str, Any] | None = None

    runbook = {"path": display_path(RUNBOOK_PATH), "status": "present" if RUNBOOK_PATH.exists() else "missing"}
    if runbook["status"] != "present":
        failures.append("production recoverability runbook is missing")

    if args.run_backup:
        backup_args = [
            sys.executable,
            "scripts/run_production_backup.py",
            "--manifest",
            settings["DEPLOY_MANIFEST"],
            "--role",
            args.backup_role,
            "--timestamp",
            stamp,
            "--json",
        ]
        if args.backup_restore_smoke:
            backup_args.append("--restore-smoke")
        result = run_command(name="production_backup", args=backup_args, logs_dir=logs_dir, timeout=3600)
        checks.append({"name": "production_backup", "status": "passed" if command_ok(result) else "failed", **result})
        if command_ok(result):
            try:
                backup_payload = parse_json_output((logs_dir / "production_backup.stdout.log").read_text(encoding="utf-8"))
                backup_failures, backup_warnings = evaluate_backup(
                    backup_payload,
                    require_restore_smoke=args.backup_restore_smoke,
                )
                failures.extend(backup_failures)
                warnings.extend(backup_warnings)
            except Exception as exc:
                failures.append(f"backup JSON could not be parsed: {exc}")
        else:
            failures.append("production backup command failed")
    else:
        warnings.append("fresh backup was not run; use --run-backup for release evidence")

    if not args.skip_live_checks:
        for name, command, timeout in (
            ("production_online_health", ["make", "production-online-health"], 240),
            ("sync_health_foreign", ["make", "sync-health"], 90),
            ("sync_health_iran", ["make", "sync-health-iran"], 90),
        ):
            result = run_command(name=name, args=command, logs_dir=logs_dir, timeout=timeout)
            checks.append({"name": name, "status": "passed" if command_ok(result) else "failed", **result})
            if not command_ok(result):
                failures.append(f"{name} failed")

    status = "passed" if not failures else "failed"
    payload: dict[str, Any] = {
        "status": status,
        "generated_at": utc_iso(),
        "artifact_dir": display_path(artifact_dir),
        "manifest": settings["DEPLOY_MANIFEST"],
        "runbook": runbook,
        "checks": checks,
        "backup": backup_payload,
        "warnings": warnings,
        "failures": failures,
    }
    write_json(artifact_dir / "results.json", payload)
    write_markdown(artifact_dir / "summary.md", payload)
    if args.report_out:
        write_markdown(REPO_ROOT / args.report_out, payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Recoverability report {status}: {display_path(artifact_dir / 'summary.md')}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
