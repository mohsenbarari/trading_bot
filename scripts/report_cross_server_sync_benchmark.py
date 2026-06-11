#!/usr/bin/env python3
"""Run the Stage P6 cross-server sync and recovery benchmark.

The benchmark uses synthetic `market_schedule_overrides` rows with a unique P6
note prefix, then deletes them through the normal sync path. It also injects a
single invalid synthetic `change_log` row to prove partial peer failures are not
marked as synced.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
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


class SyncBenchmarkError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the production cross-server sync benchmark.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--timeout", type=int, default=90, help="Seconds to wait for each propagation/recovery probe.")
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
    raise SyncBenchmarkError("Command did not print a JSON object on stdout")


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
            raise SyncBenchmarkError(f"{name} failed with exit code {result.exit_code}")
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
            shlex.quote(part) for part in ("python", "scripts/sync_probe_worker.py", *script_args)
        )
        return self.compose_args(role, body)

    def app_json(self, role: str, name: str, *script_args: str, timeout: int = 60) -> dict[str, Any]:
        return self.run_json(f"{role}_{name}", self.app_args(role, *script_args), timeout=timeout)

    def compose(self, role: str, name: str, body: str, *, timeout: int = 60) -> CommandResult:
        return self.run(f"{role}_{name}", self.compose_args(role, body), timeout=timeout)


def probe_date(stamp: str, offset: int) -> str:
    numeric = sum(ord(char) for char in stamp)
    return (date(2098, 1, 1) + timedelta(days=(numeric % 200) + offset)).isoformat()


def assert_health_clean(payload: dict[str, Any], *, role: str) -> None:
    unsynced = int(payload.get("unsynced_change_log_count") or 0)
    queues = payload.get("redis_queues") or {}
    outbound = int(queues.get("sync:outbound") or 0)
    retry = int(queues.get("sync:retry") or 0)
    if unsynced != 0 or outbound != 0 or retry != 0:
        raise SyncBenchmarkError(f"{role} sync health is not clean: {payload}")


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
    raise SyncBenchmarkError(f"Timed out waiting for {label}; last_payload={last_payload}")


def cleanup_all(runner: Runner, *, stamp: str, note_prefix: str) -> dict[str, Any]:
    cleanup: dict[str, Any] = {}
    for role in ("foreign", "iran"):
        cleanup[role] = runner.app_json(
            role,
            f"cleanup_{stamp}",
            "cleanup-probes",
            "--note-prefix",
            note_prefix,
            "--stamp",
            stamp,
            timeout=60,
        )
    return cleanup


def health_snapshot(runner: Runner, role: str) -> dict[str, Any]:
    return runner.app_json(role, "health", "health", timeout=60)


def wait_health_clean(runner: Runner, role: str, *, timeout: int) -> dict[str, Any]:
    return wait_until(
        label=f"{role} health clean",
        timeout=timeout,
        interval=1.5,
        fn=lambda: _health_clean_attempt(runner, role),
    )


def _health_clean_attempt(runner: Runner, role: str) -> tuple[bool, dict[str, Any]]:
    payload = health_snapshot(runner, role)
    try:
        assert_health_clean(payload, role=role)
        return True, payload
    except SyncBenchmarkError:
        return False, payload


def wait_probe_state(
    runner: Runner,
    *,
    role: str,
    probe_date_value: str,
    note: str,
    exists: bool,
    timeout: int,
) -> dict[str, Any]:
    return wait_until(
        label=f"{role} probe exists={exists}",
        timeout=timeout,
        interval=1.5,
        fn=lambda: _probe_state_attempt(runner, role=role, probe_date_value=probe_date_value, note=note, exists=exists),
    )


def _probe_state_attempt(
    runner: Runner,
    *,
    role: str,
    probe_date_value: str,
    note: str,
    exists: bool,
) -> tuple[bool, dict[str, Any]]:
    payload = runner.app_json(role, f"exists_{role}_{probe_date_value}_{int(exists)}", "exists-probe", "--date", probe_date_value, "--note", note)
    return bool(payload.get("exists")) is exists, payload


def measure_direction(
    runner: Runner,
    *,
    source: str,
    peer: str,
    probe_date_value: str,
    note: str,
    timeout: int,
) -> dict[str, Any]:
    runner.app_json(source, f"insert_{source}_{probe_date_value}", "insert-probe", "--date", probe_date_value, "--note", note)
    insert_peer = wait_probe_state(runner, role=peer, probe_date_value=probe_date_value, note=note, exists=True, timeout=timeout)
    source_health_after_insert = wait_health_clean(runner, source, timeout=timeout)
    runner.app_json(source, f"delete_{source}_{probe_date_value}", "delete-probe", "--date", probe_date_value, "--note", note)
    delete_peer = wait_probe_state(runner, role=peer, probe_date_value=probe_date_value, note=note, exists=False, timeout=timeout)
    source_health_after_delete = wait_health_clean(runner, source, timeout=timeout)
    return {
        "source": source,
        "peer": peer,
        "insert_peer_latency_seconds": insert_peer["duration_seconds"],
        "delete_peer_latency_seconds": delete_peer["duration_seconds"],
        "source_health_after_insert_seconds": source_health_after_insert["duration_seconds"],
        "source_health_after_delete_seconds": source_health_after_delete["duration_seconds"],
    }


def measure_recovery(runner: Runner, *, stamp: str, note_prefix: str, timeout: int) -> dict[str, Any]:
    probe_date_value = probe_date(stamp, 40)
    note = f"{note_prefix}recovery"
    runner.compose("foreign", "stop_sync_worker", "stop sync_worker", timeout=60)
    try:
        runner.app_json("foreign", "insert_recovery_probe", "insert-probe", "--date", probe_date_value, "--note", note)
        dirty = wait_until(
            label="foreign unsynced backlog after stopped sync worker",
            timeout=timeout,
            interval=1.5,
            fn=lambda: _health_dirty_attempt(runner, "foreign"),
        )
        started = time.perf_counter()
        runner.compose("foreign", "start_sync_worker", "up -d --no-deps sync_worker", timeout=90)
        peer_visible = wait_probe_state(runner, role="iran", probe_date_value=probe_date_value, note=note, exists=True, timeout=timeout)
        clean = wait_health_clean(runner, "foreign", timeout=timeout)
        recovery_seconds = round(time.perf_counter() - started, 3)
    finally:
        runner.compose("foreign", "ensure_sync_worker_running", "up -d --no-deps sync_worker", timeout=90)

    runner.app_json("foreign", "delete_recovery_probe", "delete-probe", "--date", probe_date_value, "--note", note)
    wait_probe_state(runner, role="iran", probe_date_value=probe_date_value, note=note, exists=False, timeout=timeout)
    wait_health_clean(runner, "foreign", timeout=timeout)
    return {
        "stopped_worker_dirty_after_seconds": dirty["duration_seconds"],
        "peer_visible_after_restart_seconds": peer_visible["duration_seconds"],
        "health_clean_after_restart_seconds": clean["duration_seconds"],
        "recovery_seconds": recovery_seconds,
    }


def _health_dirty_attempt(runner: Runner, role: str) -> tuple[bool, dict[str, Any]]:
    payload = health_snapshot(runner, role)
    queues = payload.get("redis_queues") or {}
    dirty = int(payload.get("unsynced_change_log_count") or 0) > 0 or int(queues.get("sync:outbound") or 0) > 0
    return dirty, payload


def verify_partial_failure_guard(runner: Runner, *, stamp: str, timeout: int) -> dict[str, Any]:
    record_id = str(900_000_000 + (sum(ord(char) for char in stamp) % 100_000))
    inserted = runner.app_json(
        "foreign",
        "insert_invalid_change_log",
        "insert-invalid-change-log",
        "--stamp",
        stamp,
        "--record-id",
        record_id,
    )
    change_log_id = str(inserted["change_log_id"])
    try:
        dirty = wait_until(
            label="foreign invalid change_log visible",
            timeout=timeout,
            interval=1.0,
            fn=lambda: _change_log_unsynced_attempt(runner, change_log_id),
        )
        resync = runner.app_json("foreign", "resync_invalid_chat_members", "resync-table", "--table", "chat_members", "--limit", "1", timeout=90)
        status = runner.app_json("foreign", "invalid_change_log_status", "change-log-status", "--change-log-id", change_log_id)
        payload = resync.get("payload") or {}
        if bool(status.get("synced")):
            raise SyncBenchmarkError(f"Partial failure guard failed; invalid entry was marked synced: {status}")
        if int(payload.get("errors") or 0) <= 0:
            raise SyncBenchmarkError(f"Partial failure guard did not observe a peer error: {resync}")
    finally:
        runner.app_json("foreign", "delete_invalid_change_log", "delete-change-log", "--change-log-id", change_log_id)
    clean = wait_health_clean(runner, "foreign", timeout=timeout)
    return {
        "dirty_after_seconds": dirty["duration_seconds"],
        "resync_payload": resync,
        "post_resync_status": status,
        "health_clean_after_cleanup_seconds": clean["duration_seconds"],
    }


def _change_log_unsynced_attempt(runner: Runner, change_log_id: str) -> tuple[bool, dict[str, Any]]:
    payload = runner.app_json("foreign", "poll_invalid_change_log", "change-log-status", "--change-log-id", change_log_id)
    return bool(payload.get("exists")) and not bool(payload.get("synced")), payload


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    stamp = args.timestamp or utc_stamp()
    artifact_root = Path(args.artifact_root)
    run_dir = artifact_root / stamp / "sync-p6"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    settings = resolve_deploy_settings(manifest_path=args.manifest)
    runner = Runner(settings=settings, logs_dir=logs_dir)
    note_prefix = f"P6_SYNC_PROBE_{stamp}_"

    summary: dict[str, Any] = {
        "status": "running",
        "started_at": utc_iso(),
        "stamp": stamp,
        "artifact_dir": display_path(run_dir),
        "note_prefix": note_prefix,
        "scenarios": {},
    }
    error: str | None = None
    try:
        summary["cleanup_before"] = cleanup_all(runner, stamp=stamp, note_prefix=note_prefix)

        initial_foreign = health_snapshot(runner, "foreign")
        initial_iran = health_snapshot(runner, "iran")
        assert_health_clean(initial_foreign, role="foreign")
        assert_health_clean(initial_iran, role="iran")
        summary["initial_health"] = {"foreign": initial_foreign, "iran": initial_iran}

        summary["scenarios"]["inspect_iran_shared_state"] = runner.run_json(
            "iran_inspect_shared_state",
            runner.compose_args("iran", "exec -T app python scripts/inspect_shared_sync_state.py --format json"),
            timeout=60,
        )
        summary["scenarios"]["seed_dry_run_foreign_to_iran"] = runner.run_json(
            "foreign_seed_dry_run_to_iran",
            runner.compose_args(
                "foreign",
                "exec -T app python scripts/seed_shared_sync_tables.py --dry-run --target-server iran --table market_schedule_overrides",
            ),
            timeout=120,
        )
        summary["scenarios"]["seed_dry_run_iran_to_foreign"] = runner.run_json(
            "iran_seed_dry_run_to_foreign",
            runner.compose_args(
                "iran",
                "exec -T app python scripts/seed_shared_sync_tables.py --dry-run --target-server foreign --table market_schedule_overrides",
            ),
            timeout=120,
        )

        summary["scenarios"]["foreign_to_iran"] = measure_direction(
            runner,
            source="foreign",
            peer="iran",
            probe_date_value=probe_date(stamp, 10),
            note=f"{note_prefix}foreign_to_iran",
            timeout=args.timeout,
        )
        cleanup_all(runner, stamp=stamp, note_prefix=note_prefix)
        wait_health_clean(runner, "foreign", timeout=args.timeout)
        wait_health_clean(runner, "iran", timeout=args.timeout)

        summary["scenarios"]["iran_to_foreign"] = measure_direction(
            runner,
            source="iran",
            peer="foreign",
            probe_date_value=probe_date(stamp, 20),
            note=f"{note_prefix}iran_to_foreign",
            timeout=args.timeout,
        )
        cleanup_all(runner, stamp=stamp, note_prefix=note_prefix)
        wait_health_clean(runner, "foreign", timeout=args.timeout)
        wait_health_clean(runner, "iran", timeout=args.timeout)

        summary["scenarios"]["partial_failure_guard"] = verify_partial_failure_guard(
            runner,
            stamp=stamp,
            timeout=args.timeout,
        )

        summary["scenarios"]["sync_worker_recovery"] = measure_recovery(
            runner,
            stamp=stamp,
            note_prefix=note_prefix,
            timeout=args.timeout,
        )
        cleanup_all(runner, stamp=stamp, note_prefix=note_prefix)
        wait_health_clean(runner, "foreign", timeout=args.timeout)
        wait_health_clean(runner, "iran", timeout=args.timeout)

        summary["final_health"] = {"foreign": health_snapshot(runner, "foreign"), "iran": health_snapshot(runner, "iran")}
        assert_health_clean(summary["final_health"]["foreign"], role="foreign")
        assert_health_clean(summary["final_health"]["iran"], role="iran")
        summary["status"] = "passed"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        summary["status"] = "failed"
        summary["error"] = error
    finally:
        try:
            runner.compose("foreign", "final_ensure_sync_worker_running", "up -d --no-deps sync_worker", timeout=90)
        except Exception as cleanup_exc:
            summary.setdefault("cleanup_errors", []).append(str(cleanup_exc))
        try:
            summary["cleanup_after"] = cleanup_all(runner, stamp=stamp, note_prefix=note_prefix)
        except Exception as cleanup_exc:
            summary.setdefault("cleanup_errors", []).append(str(cleanup_exc))
        summary["finished_at"] = utc_iso()
        summary["commands"] = runner.results
        write_artifacts(run_dir, summary)
    if error:
        raise SyncBenchmarkError(error)
    return summary


def write_artifacts(run_dir: Path, summary: dict[str, Any]) -> None:
    (run_dir / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    lines = [
        "# Stage P6 Cross-Server Sync Benchmark",
        "",
        f"- Status: `{summary['status']}`",
        f"- Started at: `{summary['started_at']}`",
        f"- Finished at: `{summary.get('finished_at', '')}`",
        f"- Artifact dir: `{summary['artifact_dir']}`",
        "",
        "## Scenarios",
        "",
    ]
    for name, payload in summary.get("scenarios", {}).items():
        if isinstance(payload, dict):
            status = payload.get("status") or payload.get("payload", {}).get("status") or "recorded"
        else:
            status = "recorded"
        lines.append(f"- `{name}`: `{status}`")
    if summary.get("error"):
        lines.extend(["", "## Error", "", f"`{summary['error']}`"])
    lines.append("")
    (run_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = run_benchmark(args)
    except SyncBenchmarkError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({"status": summary["status"], "artifact_dir": summary["artifact_dir"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
