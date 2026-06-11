#!/usr/bin/env python3
"""Run the Stage P8 non-messenger frontend UX production benchmark."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import (
    DEFAULT_ARTIFACT_ROOT,
    compose_probe_script,
    display_path,
    quote_remote,
    remote_args,
    utc_iso,
    utc_stamp,
)
from scripts.deploy_config import resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_KEYS = {"token", "access_token", "refresh_token", "auth_token"}


@dataclass
class CommandResult:
    name: str
    args: list[str]
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str


class FrontendBenchmarkError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the production frontend UX benchmark.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--route-timeout-ms", type=int, default=60000)
    parser.add_argument("--coworkers", type=int, default=60)
    parser.add_argument("--accountants", type=int, default=12)
    parser.add_argument("--customers", type=int, default=24)
    parser.add_argument("--offers", type=int, default=18)
    parser.add_argument("--trades", type=int, default=8)
    parser.add_argument("--notifications", type=int, default=12)
    parser.add_argument("--json", action="store_true", help="Print the final report JSON to stdout.")
    return parser.parse_args(argv)


def command_display(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


def parse_last_json(stdout: str) -> dict[str, Any]:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise FrontendBenchmarkError("Command did not print a JSON object on stdout")


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def redact_stdout(stdout: str) -> str:
    try:
        payload = parse_last_json(stdout)
    except Exception:
        return stdout
    return json.dumps(redact_payload(payload), ensure_ascii=False, sort_keys=True) + "\n"


class Runner:
    def __init__(self, *, settings: dict[str, str], logs_dir: Path):
        self.settings = settings
        self.logs_dir = logs_dir
        self.results: list[dict[str, Any]] = []

    def run(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 60,
        check: bool = True,
        redact_stdout_log: bool = False,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.perf_counter()
        completed = subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env={**os.environ, **(env or {})},
        )
        duration = round(time.perf_counter() - started, 3)
        result = CommandResult(
            name=name,
            args=args,
            exit_code=completed.returncode,
            duration_seconds=duration,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        self._write_logs(result, stdout_override=redact_stdout(result.stdout) if redact_stdout_log else None)
        if check and result.exit_code != 0:
            raise FrontendBenchmarkError(f"{name} failed with exit code {result.exit_code}")
        return result

    def run_json(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 60,
        check: bool = True,
        redact_stdout_log: bool = False,
    ) -> dict[str, Any]:
        result = self.run(name, args, timeout=timeout, check=check, redact_stdout_log=redact_stdout_log)
        payload = parse_last_json(result.stdout)
        self.results[-1]["json"] = redact_payload(payload)
        return payload

    def _write_logs(self, result: CommandResult, *, stdout_override: str | None = None) -> None:
        stdout_path = self.logs_dir / f"{result.name}.stdout.log"
        stderr_path = self.logs_dir / f"{result.name}.stderr.log"
        stdout_path.write_text(stdout_override if stdout_override is not None else result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        self.results.append(
            {
                "name": result.name,
                "command": command_display(result.args),
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "stdout_path": display_path(stdout_path),
                "stderr_path": display_path(stderr_path),
            }
        )

    def compose_args(self, role: str, body: str) -> list[str]:
        if role == "foreign":
            command = f"cd {shlex.quote(str(REPO_ROOT))} && " + compose_probe_script("docker-compose.yml", body)
            return ["bash", "-lc", command]
        command = f"cd {quote_remote(self.settings['IRAN_PROJECT_DIR'])} && " + compose_probe_script("docker-compose.iran.yml", body)
        return remote_args(self.settings, command)

    def app_args(self, role: str, *script_args: str) -> list[str]:
        body = "exec -T app " + " ".join(
            shlex.quote(part) for part in ("python", "scripts/frontend_ux_probe_worker.py", *script_args)
        )
        return self.compose_args(role, body)

    def app_json(self, role: str, name: str, *script_args: str, timeout: int = 60, redact_stdout_log: bool = False) -> dict[str, Any]:
        return self.run_json(
            f"{role}_{name}",
            self.app_args(role, *script_args),
            timeout=timeout,
            redact_stdout_log=redact_stdout_log,
        )


def health_snapshot(runner: Runner, role: str) -> dict[str, Any]:
    body = "exec -T app " + " ".join(shlex.quote(part) for part in ("python", "scripts/sync_probe_worker.py", "health"))
    return runner.run_json(f"{role}_sync_health", runner.compose_args(role, body), timeout=60)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    scenario = payload.get("scenario") or {}
    frontend = scenario.get("frontend") or {}
    routes = frontend.get("routes") or []
    lines = [
        "# Stage P8 Frontend UX Benchmark",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Started at: `{payload.get('started_at')}`",
        f"- Finished at: `{payload.get('finished_at')}`",
        f"- Prefix: `{payload.get('prefix')}`",
        f"- Base URL: `{frontend.get('base_url', '')}`",
        f"- Route count: `{frontend.get('route_count', 0)}`",
        "",
        "## Route Metrics",
        "",
        "| Route | Status | First usable | DOM nodes | JS heap | Requests | Key counts |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for route in routes:
        dom = ((route.get("counters") or {}).get("dom") or {})
        key_counts = ", ".join(
            [
                f"project={dom.get('projectUserCards', 0)}",
                f"acct={dom.get('publicAccountantCards', 0)}",
                f"cust={dom.get('publicCustomerCards', 0)}",
                f"offers={dom.get('offerCards', 0)}",
                f"trades={dom.get('tradeCards', 0)}",
                f"users={dom.get('userItems', 0)}",
            ]
        )
        lines.append(
            f"| `{route.get('route_id')}` | `{route.get('status')}` | `{route.get('first_usable_ms')}ms` | "
            f"`{dom.get('totalNodes', 0)}` | `{(route.get('counters') or {}).get('js_heap_used_mb', 0)}MB` | "
            f"`{(route.get('network') or {}).get('request_count', 0)}` | {key_counts} |"
        )

    lines.extend(["", "## Action Metrics", ""])
    action_rows = []
    for route in routes:
        for action in route.get("actions") or []:
            action_rows.append((route.get("route_id"), action))
    if action_rows:
        lines.extend([
            "| Route | Action | Duration | Count | Gate |",
            "| --- | --- | ---: | ---: | --- |",
        ])
        for route_id, action in action_rows:
            count = action.get("item_count_after", action.get("item_count", action.get("item_count_before", "")))
            gate = (
                action.get("bounded_page_size_ok")
                if "bounded_page_size_ok" in action
                else action.get("bounded_increment_ok")
                if "bounded_increment_ok" in action
                else action.get("bounded_list_ok", "")
            )
            lines.append(f"| `{route_id}` | `{action.get('label')}` | `{action.get('duration_ms')}ms` | `{count}` | `{gate}` |")
    else:
        lines.append("- None")

    gates = frontend.get("gates") or {}
    lines.extend(["", "## Gates", ""])
    if gates.get("failures"):
        for failure in gates["failures"]:
            lines.append(f"- Failure: {failure}")
    else:
        lines.append("- Failures: `0`")
    if gates.get("warnings"):
        for warning in gates["warnings"]:
            lines.append(f"- Warning: {warning}")
    else:
        lines.append("- Warnings: `0`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def frontend_base_url(settings: dict[str, str]) -> str:
    return (
        settings.get("IRAN_FRONTEND_URL")
        or settings.get("IRAN_SERVER_URL")
        or settings.get("IRAN_HEALTHCHECK_URL", "").removesuffix("/api/config")
    ).rstrip("/")


def sync_health_is_clean(payload: dict[str, Any]) -> bool:
    queues = payload.get("redis_queues") or {}
    return (
        payload.get("status") == "ok"
        and int(payload.get("unsynced_change_log_count") or 0) == 0
        and int(queues.get("sync:outbound") or 0) == 0
        and int(queues.get("sync:retry") or 0) == 0
    )


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    stamp = args.timestamp or utc_stamp()
    artifact_root = Path(args.artifact_root)
    run_dir = artifact_root / stamp
    scenario_dir = run_dir / "frontend-p8"
    logs_dir = scenario_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    settings = resolve_deploy_settings(manifest_path=args.manifest)
    runner = Runner(settings=settings, logs_dir=logs_dir)
    prefix = f"P8_FRONTEND_{stamp}_"
    context_file_path: str | None = None
    started_at = utc_iso()
    status = "passed"
    error: str | None = None
    frontend_payload: dict[str, Any] = {}
    cleanup_payload: dict[str, Any] = {}
    health: dict[str, Any] = {}

    try:
        runner.app_json("iran", "cleanup_before", "cleanup", "--prefix", prefix, timeout=90)
        context_payload = runner.app_json(
            "iran",
            "prepare",
            "prepare",
            "--prefix",
            prefix,
            "--coworkers",
            str(args.coworkers),
            "--accountants",
            str(args.accountants),
            "--customers",
            str(args.customers),
            "--offers",
            str(args.offers),
            "--trades",
            str(args.trades),
            "--notifications",
            str(args.notifications),
            timeout=args.timeout,
            redact_stdout_log=True,
        )

        redacted_context = redact_payload(context_payload)
        write_json(scenario_dir / "context.redacted.json", redacted_context)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="p8-frontend-context-", suffix=".json") as handle:
            json.dump(context_payload, handle, ensure_ascii=False)
            context_file_path = handle.name

        frontend_output = scenario_dir / "frontend-results.json"
        runner.run(
            "frontend_ux_browser",
            [
                "node",
                "scripts/run_frontend_ux_benchmark.mjs",
                "--base-url",
                frontend_base_url(settings),
                "--context",
                context_file_path,
                "--output",
                str(frontend_output),
                "--route-timeout-ms",
                str(args.route_timeout_ms),
            ],
            timeout=max(args.timeout, 180),
            check=True,
        )
        frontend_payload = json.loads(frontend_output.read_text(encoding="utf-8"))
        if frontend_payload.get("status") != "passed":
            status = "failed"
            error = "frontend UX gates failed"
    except Exception as exc:
        status = "failed"
        error = str(exc)
    finally:
        if context_file_path:
            try:
                os.unlink(context_file_path)
            except OSError:
                pass
        try:
            cleanup_payload["iran"] = runner.app_json(
                "iran",
                "cleanup_after",
                "cleanup",
                "--prefix",
                prefix,
                timeout=90,
                redact_stdout_log=True,
            )
        except Exception as exc:
            cleanup_payload["iran"] = {"status": "failed", "error": str(exc)}
            status = "failed"
            error = error or "Iran cleanup failed"
        try:
            cleanup_payload["foreign"] = runner.app_json(
                "foreign",
                "cleanup_after",
                "cleanup",
                "--prefix",
                prefix,
                timeout=90,
                redact_stdout_log=True,
            )
        except Exception as exc:
            cleanup_payload["foreign"] = {"status": "warning", "error": str(exc)}
            status = "failed"
            error = error or "Foreign cleanup failed"
        for role in ("iran", "foreign"):
            try:
                health[role] = health_snapshot(runner, role)
            except Exception as exc:
                health[role] = {"status": "warning", "error": str(exc)}
                status = "failed"
                error = error or f"{role} sync-health failed"
        for role, snapshot in health.items():
            if not sync_health_is_clean(snapshot):
                status = "failed"
                error = error or f"{role} sync-health is not clean"

    payload = {
        "status": status,
        "error": error,
        "started_at": started_at,
        "finished_at": utc_iso(),
        "prefix": prefix,
        "artifact_dir": display_path(scenario_dir),
        "scenario": {
            "frontend": frontend_payload,
            "cleanup": cleanup_payload,
            "health": health,
        },
        "commands": runner.results,
    }
    write_json(scenario_dir / "results.json", payload)
    write_summary(scenario_dir / "summary.md", payload)
    if status != "passed":
        raise FrontendBenchmarkError(error or "Stage P8 frontend UX benchmark failed")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_benchmark(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
