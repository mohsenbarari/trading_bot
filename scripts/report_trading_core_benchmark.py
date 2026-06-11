#!/usr/bin/env python3
"""Run the Stage P7 trading core, market, and bot production benchmark."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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


@dataclass
class CommandResult:
    name: str
    args: list[str]
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str


class TradingBenchmarkError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the production trading core benchmark.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--parse-iterations", type=int, default=30)
    parser.add_argument("--bot-iterations", type=int, default=5)
    parser.add_argument("--create-iterations", type=int, default=4)
    parser.add_argument("--list-iterations", type=int, default=6)
    parser.add_argument("--expire-iterations", type=int, default=2)
    parser.add_argument("--trade-iterations", type=int, default=3)
    parser.add_argument("--notification-iterations", type=int, default=4)
    parser.add_argument("--race-concurrency", type=int, default=4)
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
    raise TradingBenchmarkError("Command did not print a JSON object on stdout")


class Runner:
    def __init__(self, *, settings: dict[str, str], logs_dir: Path):
        self.settings = settings
        self.logs_dir = logs_dir
        self.results: list[dict[str, Any]] = []

    def run(self, name: str, args: list[str], *, timeout: int = 60, check: bool = True) -> CommandResult:
        started = time.perf_counter()
        completed = subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
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
        self._write_logs(result)
        if check and result.exit_code != 0:
            raise TradingBenchmarkError(f"{name} failed with exit code {result.exit_code}")
        return result

    def run_json(self, name: str, args: list[str], *, timeout: int = 60, check: bool = True) -> dict[str, Any]:
        result = self.run(name, args, timeout=timeout, check=check)
        payload = parse_last_json(result.stdout)
        self.results[-1]["json"] = payload
        return payload

    def _write_logs(self, result: CommandResult) -> None:
        stdout_path = self.logs_dir / f"{result.name}.stdout.log"
        stderr_path = self.logs_dir / f"{result.name}.stderr.log"
        stdout_path.write_text(result.stdout, encoding="utf-8")
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
            shlex.quote(part) for part in ("python", "scripts/trading_core_probe_worker.py", *script_args)
        )
        return self.compose_args(role, body)

    def app_json(self, role: str, name: str, *script_args: str, timeout: int = 60) -> dict[str, Any]:
        return self.run_json(f"{role}_{name}", self.app_args(role, *script_args), timeout=timeout)


def assert_health_clean(payload: dict[str, Any], *, role: str) -> None:
    unsynced = int(payload.get("unsynced_change_log_count") or 0)
    queues = payload.get("redis_queues") or {}
    outbound = int(queues.get("sync:outbound") or 0)
    retry = int(queues.get("sync:retry") or 0)
    if unsynced != 0 or outbound != 0 or retry != 0:
        raise TradingBenchmarkError(f"{role} sync health is not clean: {payload}")


def wait_until(
    *,
    label: str,
    timeout: int,
    interval: float,
    fn: Callable[[], tuple[bool, dict[str, Any]]],
) -> dict[str, Any]:
    started = time.perf_counter()
    last_payload: dict[str, Any] = {}
    while time.perf_counter() - started <= timeout:
        ok, payload = fn()
        last_payload = payload
        if ok:
            return {"duration_seconds": round(time.perf_counter() - started, 3), "payload": payload}
        time.sleep(interval)
    raise TradingBenchmarkError(f"Timed out waiting for {label}; last_payload={last_payload}")


def health_snapshot(runner: Runner, role: str) -> dict[str, Any]:
    script = "scripts/sync_probe_worker.py"
    body = "exec -T app " + " ".join(shlex.quote(part) for part in ("python", script, "health"))
    return runner.run_json(f"{role}_sync_health", runner.compose_args(role, body), timeout=60)


def _health_clean_attempt(runner: Runner, role: str) -> tuple[bool, dict[str, Any]]:
    payload = health_snapshot(runner, role)
    try:
        assert_health_clean(payload, role=role)
        return True, payload
    except TradingBenchmarkError:
        return False, payload


def wait_health_clean(runner: Runner, role: str, *, timeout: int) -> dict[str, Any]:
    return wait_until(
        label=f"{role} sync health clean",
        timeout=timeout,
        interval=1.5,
        fn=lambda: _health_clean_attempt(runner, role),
    )


def cleanup_all(runner: Runner, *, prefix: str) -> dict[str, Any]:
    return {
        role: runner.app_json(role, f"cleanup_{role}", "cleanup", "--prefix", prefix, timeout=90)
        for role in ("foreign", "iran")
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    scenario = payload.get("scenario") or {}
    operations = scenario.get("operations") or {}
    race = scenario.get("race") or {}
    lines = [
        "# Stage P7 Trading Core Benchmark",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Started at: `{payload.get('started_at')}`",
        f"- Finished at: `{payload.get('finished_at')}`",
        f"- Artifact dir: `{payload.get('artifact_dir')}`",
        f"- Prefix: `{payload.get('prefix')}`",
        "",
        "## Operation Latency",
        "",
        "| Operation | Count | p50 | p95 | p99 | Max |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in (
        "bot_text_offer_handler",
        "parse_offer",
        "offer_create",
        "offer_list",
        "offer_expire",
        "trade_execute",
        "notification_fanout",
    ):
        item = operations.get(name) or {}
        lines.append(
            f"| `{name}` | `{item.get('count')}` | `{item.get('p50_ms')}`ms | "
            f"`{item.get('p95_ms')}`ms | `{item.get('p99_ms')}`ms | `{item.get('max_ms')}`ms |"
        )
    lines.extend(
        [
            "",
            "## Race Scenario",
            "",
            f"- Winner count: `{race.get('winner_count')}`",
            f"- Persisted trade count: `{race.get('persisted_trade_count')}`",
            f"- Offer status: `{race.get('offer_status')}`",
            f"- Offer remaining quantity: `{race.get('offer_remaining_quantity')}`",
            f"- Race p95: `{(race.get('latency') or {}).get('p95_ms')}`ms",
            "",
            "## Final Health",
            "",
            f"- Foreign: `{((payload.get('final_health') or {}).get('foreign') or {}).get('status')}`",
            f"- Iran: `{((payload.get('final_health') or {}).get('iran') or {}).get('status')}`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    stamp = args.timestamp or utc_stamp()
    prefix = f"P7_TRADING_{stamp}_"
    artifact_dir = Path(args.artifact_root) / stamp / "trading-p7"
    logs_dir = artifact_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    runner = Runner(settings=resolve_deploy_settings(manifest_path=args.manifest), logs_dir=logs_dir)

    payload: dict[str, Any] = {
        "status": "running",
        "started_at": utc_iso(),
        "artifact_dir": display_path(artifact_dir),
        "stamp": stamp,
        "prefix": prefix,
    }
    scenario: dict[str, Any] | None = None
    try:
        payload["cleanup_before"] = cleanup_all(runner, prefix=prefix)
        payload["initial_health"] = {
            "foreign": wait_health_clean(runner, "foreign", timeout=args.timeout)["payload"],
            "iran": wait_health_clean(runner, "iran", timeout=args.timeout)["payload"],
        }
        scenario = runner.app_json(
            "iran",
            "trading_core_benchmark",
            "run-benchmark",
            "--prefix",
            prefix,
            "--parse-iterations",
            str(args.parse_iterations),
            "--bot-iterations",
            str(args.bot_iterations),
            "--create-iterations",
            str(args.create_iterations),
            "--list-iterations",
            str(args.list_iterations),
            "--expire-iterations",
            str(args.expire_iterations),
            "--trade-iterations",
            str(args.trade_iterations),
            "--notification-iterations",
            str(args.notification_iterations),
            "--race-concurrency",
            str(args.race_concurrency),
            timeout=max(args.timeout, 180),
        )
        payload["scenario"] = scenario
        payload["post_mutation_sync_clean"] = {
            "iran": wait_health_clean(runner, "iran", timeout=args.timeout),
            "foreign": wait_health_clean(runner, "foreign", timeout=args.timeout),
        }
        payload["status"] = "passed"
    finally:
        try:
            payload["cleanup_after"] = cleanup_all(runner, prefix=prefix)
            payload["final_health"] = {
                "foreign": wait_health_clean(runner, "foreign", timeout=args.timeout)["payload"],
                "iran": wait_health_clean(runner, "iran", timeout=args.timeout)["payload"],
            }
        finally:
            payload["commands"] = runner.results
            payload["finished_at"] = utc_iso()
            if payload.get("status") == "running":
                payload["status"] = "failed"
            write_json(artifact_dir / "results.json", payload)
            write_summary(artifact_dir / "summary.md", payload)

    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = run_benchmark(args)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if payload.get("status") == "passed" else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
