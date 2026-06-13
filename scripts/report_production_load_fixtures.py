#!/usr/bin/env python3
"""Orchestrate Stage L2 realistic-load fixture creation and cleanup."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
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
SENSITIVE_KEYS = {"token", "access_token", "refresh_token", "authorization"}


class LoadFixtureReportError(RuntimeError):
    pass


@dataclass
class CommandResult:
    name: str
    args: list[str]
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str


def parse_last_json(stdout: str) -> dict[str, Any]:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise LoadFixtureReportError("Command did not print a JSON object on stdout")


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


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command_display(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


class Runner:
    def __init__(self, *, settings: dict[str, str], logs_dir: Path):
        self.settings = settings
        self.logs_dir = logs_dir
        self.commands: list[dict[str, Any]] = []

    def run(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 60,
        check: bool = True,
        redact_stdout_log: bool = False,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.perf_counter()
        completed = subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            text=True,
            input=input_text,
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
        self._write_logs(result, redact_stdout_log=redact_stdout_log)
        if check and result.exit_code != 0:
            raise LoadFixtureReportError(f"{name} failed with exit code {result.exit_code}")
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
        self.commands[-1]["json"] = redact_payload(payload)
        return payload

    def _write_logs(self, result: CommandResult, *, redact_stdout_log: bool) -> None:
        stdout_path = self.logs_dir / f"{result.name}.stdout.log"
        stderr_path = self.logs_dir / f"{result.name}.stderr.log"
        stdout = result.stdout
        if redact_stdout_log:
            try:
                stdout = json.dumps(redact_payload(parse_last_json(result.stdout)), ensure_ascii=False, sort_keys=True) + "\n"
            except Exception:
                stdout = "[REDACTED]\n"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        self.commands.append(
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

    def worker_args(self, role: str, *script_args: str) -> list[str]:
        env_prefix = ""
        if script_args and script_args[0] == "prepare":
            env_prefix = "env TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH=1 "
        body = (
            "exec -T app "
            + env_prefix
            + " ".join(shlex.quote(part) for part in ("python", "scripts/load_fixture_worker.py", *script_args))
        )
        return self.compose_args(role, body)


def build_ssh_opts(*, host_port: str, jump_host: str, jump_port: str) -> list[str]:
    opts = [
        "-p",
        host_port,
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "NumberOfPasswordPrompts=1",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if jump_host:
        jump_spec = jump_host if jump_port == "22" else f"{jump_host}:{jump_port}"
        opts.extend(["-J", jump_spec])
    return opts


def build_scp_opts(*, host_port: str, jump_host: str, jump_port: str) -> list[str]:
    opts = [
        "-P",
        host_port,
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "NumberOfPasswordPrompts=1",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if jump_host:
        jump_spec = jump_host if jump_port == "22" else f"{jump_host}:{jump_port}"
        opts.extend(["-o", f"ProxyJump={jump_spec}"])
    return opts


def ssh_base_command(*, password: str | None) -> list[str]:
    return ["sshpass", "-e", "ssh"] if password else ["ssh"]


def scp_base_command(*, password: str | None) -> list[str]:
    return ["sshpass", "-e", "scp"] if password else ["scp"]


def require_sshpass_if_needed(password: str | None) -> None:
    if not password:
        return
    if subprocess.run(["bash", "-lc", "command -v sshpass >/dev/null 2>&1"], check=False).returncode != 0:
        raise LoadFixtureReportError("LOAD_RUNNER_PASSWORD was provided but local sshpass is not installed")


def upload_auth_pool_to_load_runner(
    runner: Runner,
    *,
    auth_pool: dict[str, Any],
    prefix: str,
    host: str,
    port: str,
    jump_host: str,
    jump_port: str,
    password: str | None,
    remote_dir: str,
) -> dict[str, Any]:
    if not host:
        raise LoadFixtureReportError("LOAD_RUNNER_HOST is required to upload the Stage L2 auth pool")
    require_sshpass_if_needed(password)
    remote_auth_dir = f"{remote_dir.rstrip('/')}/auth"
    remote_auth_path = f"{remote_auth_dir}/{prefix}auth-pool.json"
    ssh_opts = build_ssh_opts(host_port=port, jump_host=jump_host, jump_port=jump_port)
    ssh_cmd = ssh_base_command(password=password)
    env = {"SSHPASS": password} if password else None

    runner.run(
        "load_runner_auth_dir",
        [*ssh_cmd, *ssh_opts, host, f"mkdir -p {shlex.quote(remote_auth_dir)} && chmod 700 {shlex.quote(remote_auth_dir)}"],
        timeout=30,
        env=env,
    )
    raw_payload = json.dumps(auth_pool, ensure_ascii=False, sort_keys=True) + "\n"
    write_cmd = f"umask 077; cat > {shlex.quote(remote_auth_path)}; chmod 600 {shlex.quote(remote_auth_path)}"
    runner.run(
        "load_runner_auth_upload",
        [*ssh_cmd, *ssh_opts, host, write_cmd],
        timeout=45,
        input_text=raw_payload,
        env=env,
    )
    return {
        "status": "uploaded",
        "host": host,
        "jump_host": jump_host or None,
        "remote_path": remote_auth_path,
        "persona_counts": {
            key: len(value) if isinstance(value, list) else 0
            for key, value in auth_pool.get("personas", {}).items()
        },
    }


def remove_auth_pool_from_load_runner(
    runner: Runner,
    *,
    prefix: str,
    host: str,
    port: str,
    jump_host: str,
    jump_port: str,
    password: str | None,
    remote_dir: str,
) -> dict[str, Any]:
    if not host:
        return {"status": "skipped", "reason": "LOAD_RUNNER_HOST not provided"}
    require_sshpass_if_needed(password)
    remote_auth_path = f"{remote_dir.rstrip('/')}/auth/{prefix}auth-pool.json"
    ssh_opts = build_ssh_opts(host_port=port, jump_host=jump_host, jump_port=jump_port)
    ssh_cmd = ssh_base_command(password=password)
    env = {"SSHPASS": password} if password else None
    result = runner.run(
        "load_runner_auth_cleanup",
        [*ssh_cmd, *ssh_opts, host, f"rm -f {shlex.quote(remote_auth_path)}"],
        timeout=30,
        check=False,
        env=env,
    )
    return {"status": "ok" if result.exit_code == 0 else "warning", "remote_path": remote_auth_path}


def run_sync_health(runner: Runner, *, label: str, timeout: int = 60) -> dict[str, Any]:
    foreign = runner.run(f"{label}_sync_health_foreign", ["make", "sync-health"], timeout=timeout, check=False)
    iran = runner.run(f"{label}_sync_health_iran", ["make", "sync-health-iran"], timeout=timeout, check=False)
    return {
        "foreign_exit_code": foreign.exit_code,
        "iran_exit_code": iran.exit_code,
        "clean": foreign.exit_code == 0 and iran.exit_code == 0,
    }


def sync_worker_compose_body(action: str) -> str:
    if action == "pause":
        return "stop sync_worker"
    if action == "resume":
        return "up -d --no-deps sync_worker"
    raise ValueError(f"Unsupported sync worker action: {action}")


def sync_worker_compose_args(runner: Runner, role: str, action: str) -> list[str]:
    if action != "resume":
        return runner.compose_args(role, sync_worker_compose_body(action))
    compose_file = "docker-compose.yml" if role == "foreign" else "docker-compose.iran.yml"
    # Compose v1 can fail recreating stopped containers from newer image metadata
    # with KeyError: ContainerConfig. sync_worker is stateless, so remove before up.
    body = (
        compose_probe_script(compose_file, "rm -sf sync_worker >/dev/null 2>&1 || true")
        + " && "
        + compose_probe_script(compose_file, sync_worker_compose_body(action))
    )
    if role == "foreign":
        return ["bash", "-lc", f"cd {shlex.quote(str(REPO_ROOT))} && {body}"]
    return remote_args(runner.settings, f"cd {quote_remote(runner.settings['IRAN_PROJECT_DIR'])} && {body}")


def set_sync_workers(runner: Runner, *, action: str) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for role in ("foreign", "iran"):
        result = runner.run(
            f"sync_worker_{action}_{role}",
            sync_worker_compose_args(runner, role, action),
            timeout=90,
            check=False,
        )
        results[role] = {
            "exit_code": result.exit_code,
            "status": "ok" if result.exit_code == 0 else "failed",
        }
    if any(item["exit_code"] != 0 for item in results.values()):
        raise LoadFixtureReportError(f"sync_worker {action} failed")
    return {"status": "ok", "action": action, "roles": results}


def wait_sync_clean(runner: Runner, *, label: str, attempts: int, sleep_seconds: float) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    for index in range(max(1, attempts)):
        snapshot = run_sync_health(runner, label=f"{label}_{index + 1}")
        snapshots.append(snapshot)
        if snapshot["clean"]:
            return {"status": "clean", "attempts": index + 1, "snapshots": snapshots}
        if index < attempts - 1:
            time.sleep(max(0.1, sleep_seconds))
    return {"status": "not_clean", "attempts": len(snapshots), "snapshots": snapshots}


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    prepare_counts = ((payload.get("prepare") or {}).get("counts") or {})
    upload = payload.get("auth_pool_upload") or {}
    cleanup = payload.get("cleanup") or {}
    lines = [
        "# Stage L2 Production Load Fixtures",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Action: `{payload.get('action')}`",
        f"- Prefix: `{payload.get('prefix')}`",
        f"- Started at: `{payload.get('started_at')}`",
        f"- Finished at: `{payload.get('finished_at')}`",
        f"- Artifact dir: `{payload.get('artifact_dir')}`",
        "",
        "## Fixture Counts",
        "",
    ]
    if prepare_counts:
        for key, value in sorted(prepare_counts.items()):
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- No prepare counts recorded.")
    lines.extend(
        [
            "",
            "## Auth Pool",
            "",
            f"- Upload status: `{upload.get('status', 'n/a')}`",
            f"- Remote path: `{upload.get('remote_path', 'n/a')}`",
            "",
            "## Cleanup",
            "",
            f"- Iran cleanup: `{(cleanup.get('iran') or {}).get('status', 'n/a')}`",
            f"- Foreign cleanup: `{(cleanup.get('foreign') or {}).get('status', 'n/a')}`",
            f"- Load-runner auth cleanup: `{(cleanup.get('load_runner_auth') or {}).get('status', 'n/a')}`",
            "",
            "## Remaining Stages",
            "",
            "- L3 - k6 Mixed Scenario Harness",
            "- L4 - Observability Sampler During Load",
            "- L5 - Smoke Run",
            "- L6 - Warmup Ramp",
            "- L7 - Official Target Run",
            "- L8 - Spike Run",
            "- L9 - Soak Run",
            "- L10 - Analysis and Bottleneck Classification",
            "- L11 - Release Capacity Decision",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_prepare_args(args: argparse.Namespace, prefix: str) -> list[str]:
    return [
        "prepare",
        "--prefix",
        prefix,
        "--normal-users",
        str(args.normal_users),
        "--middle-admins",
        str(args.middle_admins),
        "--accountants",
        str(args.accountants),
        "--customers",
        str(args.customers),
        "--groups",
        str(args.groups),
        "--channels",
        str(args.channels),
        "--offers",
        str(args.offers),
        "--trades",
        str(args.trades),
        "--notifications",
        str(args.notifications),
        "--direct-pairs",
        str(args.direct_pairs),
        "--token-minutes",
        str(args.token_minutes),
    ]


def run_report(args: argparse.Namespace) -> dict[str, Any]:
    stamp = args.timestamp or utc_stamp()
    prefix = args.prefix or f"loadtest_{stamp}_"
    artifact_root = Path(args.artifact_root)
    run_dir = artifact_root / stamp / "load-fixtures"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    settings = resolve_deploy_settings(manifest_path=args.manifest)
    runner = Runner(settings=settings, logs_dir=logs_dir)
    sync_workers_paused = False
    payload: dict[str, Any] = {
        "status": "running",
        "stage": "L2",
        "action": args.action,
        "prefix": prefix,
        "started_at": utc_iso(),
        "artifact_dir": display_path(run_dir),
    }

    try:
        payload["preflight_sync"] = wait_sync_clean(
            runner,
            label="preflight",
            attempts=args.sync_attempts,
            sleep_seconds=args.sync_sleep_seconds,
        )
        if payload["preflight_sync"]["status"] != "clean":
            raise LoadFixtureReportError("sync-health is not clean before Stage L2 fixture action")

        if args.action in {"prepare", "prepare-and-cleanup"}:
            payload["sync_worker_pause"] = set_sync_workers(runner, action="pause")
            sync_workers_paused = True
            prepare_payload = runner.run_json(
                "iran_prepare",
                runner.worker_args("iran", *build_prepare_args(args, prefix)),
                timeout=args.prepare_timeout,
                redact_stdout_log=True,
            )
            payload["sync_worker_resume"] = set_sync_workers(runner, action="resume")
            sync_workers_paused = False
            auth_pool = prepare_payload.get("auth_pool") or {}
            payload["prepare"] = redact_payload({key: value for key, value in prepare_payload.items() if key != "auth_pool"})
            payload["auth_pool_upload"] = upload_auth_pool_to_load_runner(
                runner,
                auth_pool=auth_pool,
                prefix=prefix,
                host=args.load_runner_host,
                port=args.load_runner_port,
                jump_host=args.load_runner_jump_host,
                jump_port=args.load_runner_jump_port,
                password=args.load_runner_password,
                remote_dir=args.load_runner_remote_dir,
            )
            payload["post_prepare_sync"] = wait_sync_clean(
                runner,
                label="post_prepare",
                attempts=args.sync_attempts,
                sleep_seconds=args.sync_sleep_seconds,
            )
            if payload["post_prepare_sync"]["status"] != "clean":
                raise LoadFixtureReportError("sync-health did not return clean after fixture creation")

        if args.action in {"cleanup", "prepare-and-cleanup"}:
            cleanup_payload: dict[str, Any] = {}
            cleanup_payload["iran"] = runner.run_json(
                "iran_cleanup",
                runner.worker_args("iran", "cleanup", "--prefix", prefix),
                timeout=args.cleanup_timeout,
            )
            cleanup_payload["foreign"] = runner.run_json(
                "foreign_cleanup",
                runner.worker_args("foreign", "cleanup", "--prefix", prefix),
                timeout=args.cleanup_timeout,
                check=False,
            )
            cleanup_payload["load_runner_auth"] = remove_auth_pool_from_load_runner(
                runner,
                prefix=prefix,
                host=args.load_runner_host,
                port=args.load_runner_port,
                jump_host=args.load_runner_jump_host,
                jump_port=args.load_runner_jump_port,
                password=args.load_runner_password,
                remote_dir=args.load_runner_remote_dir,
            )
            payload["cleanup"] = cleanup_payload
            payload["post_cleanup_sync"] = wait_sync_clean(
                runner,
                label="post_cleanup",
                attempts=args.sync_attempts,
                sleep_seconds=args.sync_sleep_seconds,
            )
            if payload["post_cleanup_sync"]["status"] != "clean":
                raise LoadFixtureReportError("sync-health did not return clean after fixture cleanup")

        payload["status"] = "passed"
    except Exception as exc:
        payload["status"] = "failed"
        payload["error"] = str(exc)
        if args.action in {"prepare", "prepare-and-cleanup"} and not args.keep_on_failure:
            try:
                payload["failure_cleanup"] = {
                    "iran": runner.run_json(
                        "failure_iran_cleanup",
                        runner.worker_args("iran", "cleanup", "--prefix", prefix),
                        timeout=args.cleanup_timeout,
                        check=False,
                    ),
                    "foreign": runner.run_json(
                        "failure_foreign_cleanup",
                        runner.worker_args("foreign", "cleanup", "--prefix", prefix),
                        timeout=args.cleanup_timeout,
                        check=False,
                    ),
                    "load_runner_auth": remove_auth_pool_from_load_runner(
                        runner,
                        prefix=prefix,
                        host=args.load_runner_host,
                        port=args.load_runner_port,
                        jump_host=args.load_runner_jump_host,
                        jump_port=args.load_runner_jump_port,
                        password=args.load_runner_password,
                        remote_dir=args.load_runner_remote_dir,
                    ),
                }
            except Exception as cleanup_exc:
                payload["failure_cleanup_error"] = str(cleanup_exc)
        raise
    finally:
        if sync_workers_paused:
            try:
                payload["sync_worker_resume"] = set_sync_workers(runner, action="resume")
            except Exception as resume_exc:
                payload["sync_worker_resume_error"] = str(resume_exc)
        payload["finished_at"] = utc_iso()
        payload["commands"] = runner.commands
        write_json(run_dir / "results.json", redact_payload(payload))
        write_summary(run_dir / "summary.md", redact_payload(payload))
    return redact_payload(payload)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage L2 production realistic-load fixture setup/cleanup.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--action", choices=("prepare", "cleanup", "prepare-and-cleanup"), default="prepare-and-cleanup")
    parser.add_argument("--normal-users", type=int, default=80)
    parser.add_argument("--middle-admins", type=int, default=8)
    parser.add_argument("--accountants", type=int, default=24)
    parser.add_argument("--customers", type=int, default=48)
    parser.add_argument("--groups", type=int, default=12)
    parser.add_argument("--channels", type=int, default=3)
    parser.add_argument("--offers", type=int, default=60)
    parser.add_argument("--trades", type=int, default=20)
    parser.add_argument("--notifications", type=int, default=40)
    parser.add_argument("--direct-pairs", type=int, default=80)
    parser.add_argument("--token-minutes", type=int, default=240)
    parser.add_argument("--prepare-timeout", type=int, default=240)
    parser.add_argument("--cleanup-timeout", type=int, default=180)
    parser.add_argument("--sync-attempts", type=int, default=12)
    parser.add_argument("--sync-sleep-seconds", type=float, default=5.0)
    parser.add_argument("--load-runner-host", default=os.environ.get("LOAD_RUNNER_HOST", ""))
    parser.add_argument("--load-runner-port", default=os.environ.get("LOAD_RUNNER_SSH_PORT", "22"))
    parser.add_argument("--load-runner-jump-host", default=os.environ.get("LOAD_RUNNER_JUMP_HOST", ""))
    parser.add_argument("--load-runner-jump-port", default=os.environ.get("LOAD_RUNNER_JUMP_SSH_PORT", "22"))
    parser.add_argument("--load-runner-password", default=os.environ.get("LOAD_RUNNER_PASSWORD", ""))
    parser.add_argument("--load-runner-remote-dir", default=os.environ.get("LOAD_RUNNER_REMOTE_DIR", "/srv/trading-bot-loadtest"))
    parser.add_argument("--keep-on-failure", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = run_report(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
