#!/usr/bin/env python3
"""Build and execute the read-only preflight for the real-host Witness matrix.

This tool deliberately stops before fault injection.  It proves that the dark
Witness target, its rollback path, both WebApp hosts, and the source checkout
are safe enough to start the separately approved failure campaign.  It never
issues a Witness transition, changes a firewall, stops a service, changes a
clock, fills a disk, mutates Arvan, or copies credentials into a WebApp runtime.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BRANCH = "feature/arvan-controlled-origin-failover"
WEBAPP_FI = "65.109.220.59"
WEBAPP_IR = "87.236.212.194"
WEBAPP_IR_SSH_PORT = 37067
MATRIX_WITNESS = "185.206.95.94"
ROLLBACK_WITNESS = "185.231.182.6"
SCHEMA_VERSION = "writer_witness_real_host_matrix_preflight_v1"


SOURCE_TESTS = (
    "tests.test_writer_witness",
    "tests.test_writer_witness_client",
    "tests.test_writer_witness_deployment",
    "tests.test_writer_witness_hmac_rotation",
    "tests.test_writer_witness_postgres",
    "tests.test_writer_witness_service",
    "tests.test_webapp_writer_control",
    "tests.test_writer_fencing",
    "tests.test_runtime_identity",
    "tests.test_background_job_authority",
    "tests.test_render_runtime_envs",
    "tests.test_main_lifespan",
    "tests.test_main_public_config",
    "tests.test_arvan_origin_switch",
)


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    command: tuple[str, ...]
    host_role: str
    mutates_state: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ssh_command(host: str, script: str, *, port: int = 22) -> tuple[str, ...]:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if port != 22:
        command.extend(("-p", str(port)))
    command.extend((f"root@{host}", script))
    return tuple(command)


def remote_check_specs(*, include_source_tests: bool = True) -> list[CheckSpec]:
    specs = [
        CheckSpec(
            "git_branch_clean",
            (
                "bash",
                "-lc",
                "test \"$(git branch --show-current)\" = "
                f"\"{EXPECTED_BRANCH}\" && test -z \"$(git status --porcelain)\" "
                "&& git diff --check && printf 'branch=%s\\nhead=%s\\nclean=yes\\n' "
                "\"$(git branch --show-current)\" \"$(git rev-parse HEAD)\"",
            ),
            "control",
        ),
        CheckSpec(
            "webapp_fi_baseline",
            ssh_command(
                WEBAPP_FI,
                "set -Eeuo pipefail; "
                "echo role=webapp_fi; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "for c in trading_bot_app trading_bot_db trading_bot_redis trading_bot_sync_worker; do "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$c\")\" = running; done; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' trading_bot_app)\" = healthy; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' trading_bot_db)\" = healthy; "
                "curl -fsS http://127.0.0.1:8000/api/config >/dev/null; "
                "if grep -Eqs '^(WRITER_WITNESS_REQUIRED|WRITER_WITNESS_AUTO_RENEW_ENABLED|WRITER_WITNESS_SERVICE_ENABLED)=true$' "
                "/srv/trading-bot/current/.env; then exit 41; fi; "
                f"timeout 5 bash -c '</dev/tcp/{MATRIX_WITNESS}/443'; "
                "release=$(docker inspect trading_bot_app --format '{{range .Config.Env}}{{println .}}{{end}}' "
                "| sed -n 's/^RELEASE_SHA=//p' | head -1); test -n \"$release\"; "
                "echo ntp=yes; echo app=healthy; echo db=healthy; echo api=200; "
                "echo witness_flags_enabled=no; echo witness_tcp_443=reachable; echo release_sha=$release",
            ),
            "webapp_fi",
        ),
        CheckSpec(
            "webapp_ir_standby_baseline",
            ssh_command(
                WEBAPP_IR,
                "set -Eeuo pipefail; "
                "echo role=webapp_ir; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "app=$(docker ps -aq --filter label=com.docker.compose.project=current "
                "--filter label=com.docker.compose.service=app); test -n \"$app\"; "
                "sync=$(docker ps -aq --filter label=com.docker.compose.project=current "
                "--filter label=com.docker.compose.service=sync_worker); test -n \"$sync\"; "
                "db=$(docker ps -aq --filter label=com.docker.compose.project=current "
                "--filter label=com.docker.compose.service=db); test -n \"$db\"; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$app\")\" != running; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$sync\")\" != running; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$db\")\" = running; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' \"$db\")\" = healthy; "
                "if grep -Eqs '^(WRITER_WITNESS_REQUIRED|WRITER_WITNESS_AUTO_RENEW_ENABLED|WRITER_WITNESS_SERVICE_ENABLED)=true$' "
                "/srv/trading-bot/current/.env; then exit 42; fi; "
                f"timeout 5 bash -c '</dev/tcp/{MATRIX_WITNESS}/443'; "
                "echo ntp=yes; echo app=stopped; echo sync_worker=stopped; echo db=running; "
                "echo witness_flags_enabled=no; echo witness_tcp_443=reachable",
                port=WEBAPP_IR_SSH_PORT,
            ),
            "webapp_ir",
        ),
        CheckSpec(
            "matrix_witness_dark_baseline",
            ssh_command(
                MATRIX_WITNESS,
                "set -Eeuo pipefail; "
                "echo role=matrix_witness; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "for u in writer-witness postgresql nginx ufw; do test \"$(systemctl is-active \"$u\")\" = active; done; "
                "curl -fsS http://127.0.0.1:8011/health/ready >/dev/null; "
                "state=$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT authority||chr(58)||writer_epoch||chr(58)||lease_status FROM webapp_writer_witness_state;\"); "
                "test \"$state\" = webapp:0:vacant; "
                "receipts=$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT count(*) FROM webapp_writer_witness_receipts;\"); test \"$receipts\" = 0; "
                "test \"$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT version_num FROM writer_witness_schema_version;\")\" = 001; "
                "latest=$(find /var/backups/trading-bot-witness -maxdepth 1 -type f -name 'writer-witness-*.dump' "
                "-printf '%T@|%p\\n' | sort -nr | head -1 | cut -d'|' -f2-); test -n \"$latest\"; "
                "sha256sum --check \"$latest.sha256\" >/dev/null; "
                "age=$(( $(date +%s) - $(stat -c %Y \"$latest\") )); test \"$age\" -le 86400; "
                "rollbacks=$(runuser -u postgres -- psql -XAt -d postgres -c "
                "\"SELECT count(*) FROM pg_database WHERE datname LIKE 'writer_witness_rollback_%' AND NOT datallowconn;\"); "
                "test \"$rollbacks\" -ge 1; test ! -e /run/writer-witness-hmac-rotation; "
                "used=$(df -P / | awk 'NR==2 {gsub(/%/,\"\",$5); print $5}'); test \"$used\" -lt 80; "
                "echo ntp=yes; echo services=active; echo ready=200; echo state=$state; echo receipts=$receipts; "
                "echo backup=$(basename \"$latest\"); echo backup_age_seconds=$age; "
                "echo rollback_databases=$rollbacks; echo disk_used_percent=$used; echo rotation_state=absent",
            ),
            "matrix_witness",
        ),
        CheckSpec(
            "rollback_witness_baseline",
            ssh_command(
                ROLLBACK_WITNESS,
                "set -Eeuo pipefail; "
                "echo role=rollback_witness; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "for u in writer-witness postgresql nginx ufw; do test \"$(systemctl is-active \"$u\")\" = active; done; "
                "curl -fsS http://127.0.0.1:8011/health/ready >/dev/null; "
                "state=$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT authority||chr(58)||writer_epoch||chr(58)||lease_status FROM webapp_writer_witness_state;\"); "
                "receipts=$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT count(*) FROM webapp_writer_witness_receipts;\"); "
                "test \"$state\" = webapp:0:vacant; test \"$receipts\" = 0; "
                "echo ntp=yes; echo services=active; echo ready=200; echo state=$state; echo receipts=$receipts",
            ),
            "rollback_witness",
        ),
    ]
    if include_source_tests:
        specs.insert(
            1,
            CheckSpec(
                "source_regression_gate",
                (sys.executable, "-m", "unittest", *SOURCE_TESTS),
                "control",
            ),
        )
    return specs


def scenario_catalog() -> list[dict[str, object]]:
    return [
        {"id": "RH-001", "name": "concurrent FI and IR acquire", "risk": "dark-witness-write"},
        {"id": "RH-002", "name": "response lost after Witness commit", "risk": "dark-witness-write"},
        {"id": "RH-003", "name": "delayed rejected packet replay", "risk": "dark-witness-write"},
        {"id": "RH-004", "name": "FI to Witness directional partition", "risk": "scoped-network-fault"},
        {"id": "RH-005", "name": "IR to Witness directional partition", "risk": "scoped-network-fault"},
        {"id": "RH-006", "name": "Witness process restart and pause", "risk": "dark-service-fault"},
        {"id": "RH-007", "name": "Witness PostgreSQL pause and restart", "risk": "dark-service-fault"},
        {"id": "RH-008", "name": "Witness VM reboot and temporary host loss", "risk": "dark-host-fault"},
        {"id": "RH-009", "name": "isolated disk-full boundary", "risk": "isolated-filesystem-only"},
        {"id": "RH-010", "name": "clock skew and jump", "risk": "clone-or-time-namespace-only"},
        {"id": "RH-011", "name": "key rotation during one-site partition", "risk": "dark-witness-credential"},
        {"id": "RH-012", "name": "restore exact vacant baseline", "risk": "guarded-dark-restore"},
    ]


def git_metadata() -> dict[str, object]:
    def output(*command: str) -> str:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    branch = output("git", "branch", "--show-current")
    return {
        "branch": branch,
        "head": output("git", "rev-parse", "HEAD"),
        "clean": output("git", "status", "--porcelain") == "",
        "expected_branch": EXPECTED_BRANCH,
        "main_merge_authorized": False,
        "main_integration_claimed": False,
    }


def build_plan(*, include_source_tests: bool = True) -> dict[str, object]:
    checks = remote_check_specs(include_source_tests=include_source_tests)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "scope": "dark_writer_witness_control_plane_only",
        "git": git_metadata(),
        "hosts": {
            "webapp_fi": {"host": WEBAPP_FI, "production_mutation_allowed": False},
            "webapp_ir": {"host": WEBAPP_IR, "ssh_port": WEBAPP_IR_SSH_PORT, "production_mutation_allowed": False},
            "matrix_witness": {"host": MATRIX_WITNESS, "must_start": "webapp:0:vacant"},
            "rollback_witness": {"host": ROLLBACK_WITNESS, "must_remain_unchanged": True},
        },
        "safety_contract": {
            "allowed_before_matrix": [
                "read-only host and service inspection",
                "read-only TCP reachability probes",
                "source regression tests",
                "local Witness backup and isolated restore-smoke",
            ],
            "forbidden_before_matrix": [
                "merge main into the feature branch",
                "merge the feature branch into main",
                "issue a writer lease",
                "enable Witness flags in a WebApp runtime",
                "start WebApp-IR application writers",
                "stop or restart a production WebApp service",
                "change Arvan or CDN routing",
                "inject firewall, process, database, VM, disk, or clock faults",
                "persist a Witness client credential on a WebApp host",
            ],
            "claim_boundary": (
                "Passing this preflight authorizes only the dark-Witness real-host fault matrix. "
                "It does not prove the feature branch is integrated with current main and does not "
                "authorize production writer activation."
            ),
        },
        "preflight_checks": [
            {
                "check_id": spec.check_id,
                "host_role": spec.host_role,
                "mutates_state": spec.mutates_state,
                "command": list(spec.command),
            }
            for spec in checks
        ],
        "matrix_scenarios_after_preflight": scenario_catalog(),
        "entry_criteria": [
            "all preflight checks pass on one retained artifact",
            "matrix Witness is vacant at epoch 0 with zero receipts",
            "fresh checksumed backup restore-smoke passes",
            "disabled rollback database exists and remains connection-blocked",
            "both WebApp sites can reach only the dark Witness control endpoint",
            "WebApp-FI production remains healthy and WebApp-IR writers remain stopped",
            "an operator abort and rollback order is recorded before RH-001",
        ],
    }


def bounded_output(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def execute_preflight(plan: dict[str, object]) -> tuple[dict[str, object], int]:
    git_info = plan["git"]
    if not isinstance(git_info, dict):
        raise RuntimeError("invalid git metadata")
    if git_info.get("branch") != EXPECTED_BRANCH or not git_info.get("clean"):
        plan["status"] = "blocked_git_baseline"
        return plan, 2

    results: list[dict[str, object]] = []
    failed: list[str] = []
    for spec in remote_check_specs():
        completed = subprocess.run(
            spec.command,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        status = "passed" if completed.returncode == 0 else "failed"
        if status == "failed":
            failed.append(spec.check_id)
        results.append(
            {
                "check_id": spec.check_id,
                "host_role": spec.host_role,
                "status": status,
                "return_code": completed.returncode,
                "stdout": bounded_output(completed.stdout),
                "stderr": bounded_output(completed.stderr),
            }
        )
    plan["preflight_results"] = results
    plan["failed_checks"] = failed
    plan["status"] = "preflight_passed" if not failed else "preflight_failed"
    return plan, 0 if not failed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "preflight"), default="plan")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/trading-bot-writer-witness-real-host-matrix/preflight.json"),
    )
    parser.add_argument(
        "--skip-source-tests",
        action="store_true",
        help="Plan-only convenience for unit tests; executed preflight always runs source tests.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "preflight" and args.skip_source_tests:
        raise SystemExit("--skip-source-tests is not allowed in preflight mode")
    plan = build_plan(include_source_tests=not args.skip_source_tests)
    exit_code = 0
    if args.mode == "preflight":
        plan, exit_code = execute_preflight(plan)
    else:
        plan["status"] = "planned"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": plan["status"],
                "scope": plan["scope"],
                "output": str(args.output),
                "scenario_count": len(plan["matrix_scenarios_after_preflight"]),
                "failed_checks": plan.get("failed_checks", []),
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
