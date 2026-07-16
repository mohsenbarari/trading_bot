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
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BRANCH = "feature/arvan-controlled-origin-failover"
WEBAPP_FI = "65.109.220.59"
WEBAPP_IR = "87.236.212.194"
WEBAPP_IR_SSH_PORT = 37067
MATRIX_WITNESS = "185.206.95.94"
ROLLBACK_WITNESS = "185.231.182.6"
SCHEMA_VERSION = "writer_witness_real_host_matrix_preflight_v1"


SOURCE_GATE = ROOT / "scripts/run_writer_witness_preflight_source_gate.sh"
PINNED_SOURCE_PATHS = (
    "scripts/plan_writer_witness_real_host_matrix.py",
    "scripts/run_writer_witness_preflight_source_gate.sh",
    "scripts/run_writer_witness_failure_drill.sh",
    "scripts/run_writer_witness_postgres_gate.py",
    "scripts/run_writer_witness_real_host_matrix.py",
    "scripts/writer_witness_matrix_client.py",
    "writer_witness_app.py",
    "deploy/writer-witness-drill/docker-compose.yml",
    "deploy/writer-witness/writer-witness-live-restore.sh",
    "deploy/writer-witness/writer-witness-matrix-host-faults.sh",
    "deploy/writer-witness/writer-witness-rotate-hmac.py",
    "deploy/writer-witness/writer-witness-state-manifest.sh",
    "deploy/writer-witness/nginx.conf.template",
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


def remote_check_specs(
    *,
    include_source_tests: bool = True,
    expected_commit: str | None = None,
) -> list[CheckSpec]:
    expected_commit = str(expected_commit or "").strip()
    if not expected_commit:
        expected_commit = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    if len(expected_commit) != 40 or any(char not in "0123456789abcdef" for char in expected_commit):
        raise ValueError("expected_commit must be one lowercase 40-character Git SHA")
    specs = [
        CheckSpec(
            "git_branch_clean",
            (
                "bash",
                "-lc",
                "test \"$(git branch --show-current)\" = "
                f"\"{EXPECTED_BRANCH}\" && test \"$(git rev-parse HEAD)\" = \"{expected_commit}\" "
                "&& test -z \"$(git status --porcelain)\" "
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
                "test \"$(findmnt -n -o FSTYPE -T /run)\" = tmpfs; "
                "curl -fsS http://127.0.0.1:8000/api/config >/dev/null; "
                "if { sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p' "
                "/srv/trading-bot/current/.env; docker inspect trading_bot_app --format "
                "'{{range .Config.Env}}{{println .}}{{end}}' | sed -n "
                "'/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p'; } "
                "| awk -F= '{v=tolower($2); gsub(/[[:space:]]/,\"\",v); "
                "if (v ~ /^(1|true|t|yes|y|on)$/) found=1} END {exit found ? 0 : 1}'; then exit 41; fi; "
                "! grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))=' /srv/trading-bot/current/.env; "
                "! docker inspect trading_bot_app --format '{{range .Config.Env}}{{println .}}{{end}}' "
                "| grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))='; "
                "test -z \"$(find /run/writer-witness-matrix -mindepth 1 -maxdepth 2 -print -quit 2>/dev/null)\"; "
                f"timeout 5 bash -c '</dev/tcp/{MATRIX_WITNESS}/443'; "
                f"cert=$(timeout 8 openssl s_client -connect {MATRIX_WITNESS}:443 "
                "-servername writer-witness.internal </dev/null 2>/dev/null "
                "| openssl x509 -outform DER | sha256sum | awk '{print $1}'); "
                "test ${#cert} -eq 64; "
                f"unsigned=$(curl -k -sS -o /dev/null -w '%{{http_code}}' https://{MATRIX_WITNESS}/v1/writer-witness/status); "
                "test \"$unsigned\" = 401; "
                "release=$(docker inspect trading_bot_app --format '{{range .Config.Env}}{{println .}}{{end}}' "
                "| sed -n 's/^RELEASE_SHA=//p' | head -1); test -n \"$release\"; "
                "echo ntp=yes; echo app=healthy; echo db=healthy; echo api=200; "
                "echo witness_flags_enabled=no; echo witness_tcp_443=reachable; "
                "echo witness_unsigned_status=401; echo client_credentials_installed=no; echo witness_cert_sha256=$cert; echo release_sha=$release",
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
                "test \"$(findmnt -n -o FSTYPE -T /run)\" = tmpfs; "
                "if { sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p' "
                "/srv/trading-bot/current/.env; docker inspect \"$app\" --format "
                "'{{range .Config.Env}}{{println .}}{{end}}' | sed -n "
                "'/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p'; } "
                "| awk -F= '{v=tolower($2); gsub(/[[:space:]]/,\"\",v); "
                "if (v ~ /^(1|true|t|yes|y|on)$/) found=1} END {exit found ? 0 : 1}'; then exit 42; fi; "
                "! grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))=' /srv/trading-bot/current/.env; "
                "! docker inspect \"$app\" --format '{{range .Config.Env}}{{println .}}{{end}}' "
                "| grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))='; "
                "test -z \"$(find /run/writer-witness-matrix -mindepth 1 -maxdepth 2 -print -quit 2>/dev/null)\"; "
                f"timeout 5 bash -c '</dev/tcp/{MATRIX_WITNESS}/443'; "
                f"cert=$(timeout 8 openssl s_client -connect {MATRIX_WITNESS}:443 "
                "-servername writer-witness.internal </dev/null 2>/dev/null "
                "| openssl x509 -outform DER | sha256sum | awk '{print $1}'); "
                "test ${#cert} -eq 64; "
                f"unsigned=$(curl -k -sS -o /dev/null -w '%{{http_code}}' https://{MATRIX_WITNESS}/v1/writer-witness/status); "
                "test \"$unsigned\" = 401; "
                "echo ntp=yes; echo app=stopped; echo sync_worker=stopped; echo db=running; "
                "echo witness_flags_enabled=no; echo witness_tcp_443=reachable; "
                "echo witness_unsigned_status=401; echo client_credentials_installed=no; echo witness_cert_sha256=$cert",
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
                "backup_sha=$(awk '{print $1}' \"$latest.sha256\"); test ${#backup_sha} -eq 64; "
                "age=$(( $(date +%s) - $(stat -c %Y \"$latest\") )); test \"$age\" -le 86400; "
                "rollbacks=$(runuser -u postgres -- psql -XAt -d postgres -c "
                "\"SELECT count(*) FROM pg_database WHERE datname LIKE 'writer_witness_rollback_%' AND NOT datallowconn;\"); "
                "test \"$rollbacks\" -ge 1; "
                "test ! -e /var/lib/trading-bot-witness/hmac-rotation/webapp_fi; "
                "test ! -e /var/lib/trading-bot-witness/hmac-rotation/webapp_ir; "
                "test ! -e /var/lib/trading-bot-witness/restore-state/active.env; "
                "test ! -e /var/lib/trading-bot-witness/matrix-campaign/active; "
                "test -z \"$(find /var/backups/trading-bot-witness -maxdepth 1 -name '.replacement-restore.*.dump' -print -quit)\"; "
                "credential_bundle_sha=$(sha256sum /etc/trading-bot-witness/runtime.env "
                "/root/writer-witness-client-material/webapp-fi.env "
                "/root/writer-witness-client-material/webapp-ir.env "
                "/root/writer-witness-client-material/witness-ca.crt | sha256sum | awk '{print $1}'); "
                "test ${#credential_bundle_sha} -eq 64; "
                "cert=$(openssl x509 -in /etc/trading-bot-witness/tls/server.crt -outform DER "
                "| sha256sum | awk '{print $1}'); test ${#cert} -eq 64; "
                "cert_end=$(date -d \"$(openssl x509 -in /etc/trading-bot-witness/tls/server.crt "
                "-noout -enddate | cut -d= -f2-)\" +%s); test \"$cert_end\" -gt $(( $(date +%s) + 604800 )); "
                "for site in webapp_fi webapp_ir; do short=${site#webapp_}; "
                f"url=$(sed -n 's/^WRITER_WITNESS_INTERNAL_URL=//p' /root/writer-witness-client-material/webapp-$short.env); test \"$url\" = https://{MATRIX_WITNESS}; "
                "/usr/local/sbin/writer-witness-smoke-client "
                "--env-file /root/writer-witness-client-material/webapp-$short.env "
                "--ca-bundle /etc/trading-bot-witness/tls/ca.crt --site \"$site\" >/dev/null; done; "
                "unsigned=$(curl --cacert /etc/trading-bot-witness/tls/ca.crt -sS -o /dev/null "
                f"-w '%{{http_code}}' https://{MATRIX_WITNESS}/v1/writer-witness/status); test \"$unsigned\" = 401; "
                "manifest=$(/usr/local/sbin/writer-witness-state-manifest); test ${#manifest} -eq 64; "
                "release=$(readlink -f /srv/trading-bot-witness/current); "
                "pid=$(systemctl show -p MainPID --value writer-witness.service); test \"$pid\" -gt 1; "
                "test \"$(readlink -f /proc/$pid/cwd)\" = \"$release\"; "
                "for pair in "
                "writer-witness-live-restore:deploy/writer-witness/writer-witness-live-restore.sh "
                "writer-witness-rotate-hmac:deploy/writer-witness/writer-witness-rotate-hmac.py "
                "writer-witness-matrix-host-faults:deploy/writer-witness/writer-witness-matrix-host-faults.sh "
                "writer-witness-state-manifest:deploy/writer-witness/writer-witness-state-manifest.sh "
                "writer-witness-smoke-client:scripts/smoke_writer_witness_client.py; do "
                "installed=${pair%%:*}; source=${pair#*:}; "
                "test \"$(sha256sum /usr/local/sbin/$installed | awk '{print $1}')\" = "
                "\"$(sha256sum $release/$source | awk '{print $1}')\"; done; "
                "test \"$(sha256sum /etc/systemd/system/writer-witness.service | awk '{print $1}')\" = "
                "\"$(sha256sum $release/deploy/writer-witness/writer-witness.service | awk '{print $1}')\"; "
                "effective_nginx=$(mktemp); trap 'rm -f \"$effective_nginx\"' EXIT; nginx -T >\"$effective_nginx\" 2>/dev/null; "
                f"for rule in 'allow {WEBAPP_FI};' 'allow {WEBAPP_IR};' 'allow {MATRIX_WITNESS};' "
                "'allow 127.0.0.1;' 'allow ::1;' 'deny all;' 'client_max_body_size 16k;' "
                "'location = /v1/writer-witness/status {'; do "
                "grep -F -- \"$rule\" \"$effective_nginx\" >/dev/null; done; "
                "grep -F -- \"location = /v1/writer-witness/\"\"transitions {\" \"$effective_nginx\" >/dev/null; "
                "ufw_status=$(ufw status verbose); printf '%s' \"$ufw_status\" | grep -F 'Default: deny (incoming), allow (outgoing)' >/dev/null; "
                f"printf '%s' \"$ufw_status\" | grep -E '443/tcp.*ALLOW IN.*{WEBAPP_FI}' >/dev/null; "
                f"printf '%s' \"$ufw_status\" | grep -E '443/tcp.*ALLOW IN.*{WEBAPP_IR}' >/dev/null; "
                "inventory=$(runuser -u postgres -- psql -XAt postgres -c \"SELECT datname||':'||oid||':'||datallowconn FROM pg_database WHERE datname='writer_witness' OR datname LIKE 'writer_witness_candidate_%' OR datname LIKE 'writer_witness_failed_%' OR datname LIKE 'writer_witness_rollback_%' ORDER BY datname\"); "
                "bad=$(runuser -u postgres -- psql -XAt postgres -c \"SELECT count(*) FROM pg_database WHERE datname<>'writer_witness' AND (datname LIKE 'writer_witness_candidate_%' OR datname LIKE 'writer_witness_failed_%' OR datname LIKE 'writer_witness_rollback_%') AND datallowconn\"); test \"$bad\" = 0; "
                "orphans=$(runuser -u postgres -- psql -XAt postgres -c \"SELECT count(*) FROM pg_database WHERE datname LIKE 'writer_witness_candidate_%' OR datname LIKE 'writer_witness_failed_%'\"); test \"$orphans\" = 0; "
                "inventory_sha=$(printf '%s' \"$inventory\" | sha256sum | awk '{print $1}'); "
                "nginx_sha=$(nginx -T 2>/dev/null | sha256sum | awk '{print $1}'); "
                "firewall_sha=$(nft list ruleset | sha256sum | awk '{print $1}'); "
                "release_manifest_sha=$(sha256sum /srv/trading-bot-witness/current/release-manifest.json | awk '{print $1}'); "
                "used=$(df -P / | awk 'NR==2 {gsub(/%/,\"\",$5); print $5}'); test \"$used\" -lt 80; "
                "echo ntp=yes; echo services=active; echo ready=200; echo state=$state; echo receipts=$receipts; "
                "echo backup=$(basename \"$latest\"); echo backup_sha256=$backup_sha; echo backup_age_seconds=$age; "
                "echo rollback_databases=$rollbacks; echo disk_used_percent=$used; echo rotation_state=absent; "
                "echo restore_state=absent; echo signed_status_fi=200; echo signed_status_ir=200; "
                "echo unsigned_status=401; echo cert_sha256=$cert; echo cert_not_after_epoch=$cert_end; "
                "echo manifest_sha256=$manifest; echo nginx_sha256=$nginx_sha; echo firewall_sha256=$firewall_sha; "
                "echo release_manifest_sha256=$release_manifest_sha; echo installed_helpers_match=yes; "
                "echo running_release_match=yes; echo network_policy_semantics_match=yes; echo database_inventory_sha256=$inventory_sha; echo connection_enabled_aux_databases=0; echo orphan_candidate_failed_databases=0; "
                "echo campaign_state=absent; echo credential_bundle_sha256=$credential_bundle_sha",
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
                "manifest=$(/usr/local/sbin/writer-witness-state-manifest); test ${#manifest} -eq 64; "
                "cert=$(openssl x509 -in /etc/trading-bot-witness/tls/server.crt -outform DER "
                "| sha256sum | awk '{print $1}'); test ${#cert} -eq 64; "
                "echo ntp=yes; echo services=active; echo ready=200; echo state=$state; "
                "echo receipts=$receipts; echo manifest_sha256=$manifest; echo cert_sha256=$cert",
            ),
            "rollback_witness",
        ),
    ]
    if include_source_tests:
        specs.insert(
            1,
            CheckSpec(
                "source_regression_gate",
                ("bash", str(SOURCE_GATE)),
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


def abort_and_rollback_contract() -> dict[str, object]:
    return {
        "abort_conditions": [
            "WebApp-FI app, database, Redis, sync worker, or API loses baseline health",
            "WebApp-IR application or sync worker starts unexpectedly",
            "either WebApp Witness enable flag becomes true",
            "original rollback Witness changes from webapp:0:vacant or gains a receipt",
            "a fault escapes the exact replacement-Witness IP, port, service, or isolated filesystem scope",
            "replacement Witness state differs from the scenario's expected epoch, holder, lease, or receipt count",
            "NTP is unsynchronized before a scenario that does not explicitly test clock behavior",
            "backup checksum, rollback database, SSH access, or cleanup trap is unavailable",
            "Arvan/CDN, public DNS, product database, or product container state changes",
        ],
        "ordered_steps": [
            {
                "order": 1,
                "step_id": "stop_and_join_requesters",
                "requirement": "while isolation remains active, stop every matrix requester and prove no retry process or socket remains",
            },
            {
                "order": 2,
                "step_id": "revoke_transient_capability",
                "requirement": "remove every transient client credential and revoke scenario-only overlap capability before reconnecting any path",
            },
            {
                "order": 3,
                "step_id": "retain_pre_recovery_evidence",
                "requirement": "capture, redact, hash, and copy scenario evidence to the controller before restoring the database",
            },
            {
                "order": 4,
                "step_id": "resume_paused_runtime",
                "requirement": "CONT/unpause and start only replacement Witness PostgreSQL, service, Nginx, and NTP components affected by the scenario",
            },
            {
                "order": 5,
                "step_id": "remove_isolated_pressure",
                "requirement": "unmount and delete only matrix-owned tmpfs, loop, and clone artifacts",
            },
            {
                "order": 6,
                "step_id": "remove_scoped_network_faults",
                "requirement": "delete only matrix-owned firewall/tc objects after all requesters and capabilities are gone",
            },
            {
                "order": 7,
                "step_id": "restore_vacant_baseline",
                "requirement": "use the journaled live-restore path and the pinned checksumed full-manifest epoch-0 backup on replacement Witness",
            },
            {
                "order": 8,
                "step_id": "verify_complete_baseline",
                "requirement": "prove both Witness manifests, FI health, IR stopped writers, disabled flags, restored paths, no hidden resources, and retained failure status",
            },
        ],
        "success_barrier": [
            "every scenario-specific cleanup passes",
            "the same baseline preflight passes again",
            "replacement Witness is restored to webapp:0:vacant with zero receipts",
            "original Witness remains byte-for-business-state unchanged",
            "no transient credential, network rule, pause, mount, or clock override remains",
        ],
    }


def git_metadata(expected_commit: str | None = None) -> dict[str, object]:
    def output(*command: str) -> str:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    branch = output("git", "branch", "--show-current")
    head = output("git", "rev-parse", "HEAD")
    pinned = str(expected_commit or head).strip()
    if len(pinned) != 40 or any(char not in "0123456789abcdef" for char in pinned):
        raise ValueError("expected_commit must be one lowercase 40-character Git SHA")
    return {
        "branch": branch,
        "head": head,
        "clean": output("git", "status", "--porcelain") == "",
        "expected_branch": EXPECTED_BRANCH,
        "expected_commit": pinned,
        "exact_commit_matches": head == pinned,
        "main_merge_authorized": False,
        "main_integration_claimed": False,
    }


def source_manifest() -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in PINNED_SOURCE_PATHS:
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"matrix source artifact is missing: {relative}")
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def witness_release_manifest_sha256() -> str:
    with tempfile.TemporaryDirectory(prefix="writer-witness-matrix-release-") as parent:
        destination = Path(parent) / "release"
        subprocess.check_call(
            ("bash", str(ROOT / "scripts/build_writer_witness_release.sh"), str(destination)),
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return hashlib.sha256((destination / "release-manifest.json").read_bytes()).hexdigest()


def build_plan(
    *,
    include_source_tests: bool = True,
    expected_commit: str | None = None,
) -> dict[str, object]:
    git = git_metadata(expected_commit)
    checks = remote_check_specs(
        include_source_tests=include_source_tests,
        expected_commit=str(git["expected_commit"]),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "scope": "dark_writer_witness_control_plane_only",
        "git": git,
        "run_bundle": {
            "expected_commit": git["expected_commit"],
            "source_sha256": source_manifest(),
            "witness_release_manifest_sha256": witness_release_manifest_sha256(),
            "source_gate_requires_zero_skips": True,
            "source_gate_requires_guarded_postgres_tests": 4,
            "source_gate_requires_four_database_drill": True,
        },
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
        "abort_and_rollback": abort_and_rollback_contract(),
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


def parse_key_value_output(value: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in value.splitlines():
        line = raw_line.strip()
        key, separator, item = line.partition("=")
        if (
            separator
            and key
            and all(character.isalnum() or character == "_" for character in key)
        ):
            parsed[key] = item.strip()
    return parsed


def execute_preflight(plan: dict[str, object]) -> tuple[dict[str, object], int]:
    git_info = plan["git"]
    if not isinstance(git_info, dict):
        raise RuntimeError("invalid git metadata")
    if (
        git_info.get("branch") != EXPECTED_BRANCH
        or not git_info.get("clean")
        or git_info.get("head") != git_info.get("expected_commit")
    ):
        plan["status"] = "blocked_git_baseline"
        return plan, 2

    bundle = plan.get("run_bundle")
    if not isinstance(bundle, dict) or bundle.get("source_sha256") != source_manifest():
        plan["status"] = "blocked_source_bundle_drift"
        return plan, 2

    results: list[dict[str, object]] = []
    failed: list[str] = []
    for spec in remote_check_specs(expected_commit=str(git_info["expected_commit"])):
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
    parsed = {
        result["check_id"]: parse_key_value_output(str(result.get("stdout") or ""))
        for result in results
    }
    matrix_certificate = parsed.get("matrix_witness_dark_baseline", {}).get("cert_sha256")
    for check_id in ("webapp_fi_baseline", "webapp_ir_standby_baseline"):
        if not matrix_certificate or parsed.get(check_id, {}).get("witness_cert_sha256") != matrix_certificate:
            failed.append(f"{check_id}_certificate_identity")
    matrix_manifest = parsed.get("matrix_witness_dark_baseline", {}).get("manifest_sha256")
    rollback_manifest = parsed.get("rollback_witness_baseline", {}).get("manifest_sha256")
    if not matrix_manifest or not rollback_manifest:
        failed.append("witness_baseline_manifest_missing")
    expected_release_manifest = bundle.get("witness_release_manifest_sha256")
    if (
        not expected_release_manifest
        or parsed.get("matrix_witness_dark_baseline", {}).get("release_manifest_sha256")
        != expected_release_manifest
    ):
        failed.append("matrix_witness_release_manifest_mismatch")
    source_stdout = str(
        next(
            (item.get("stdout") for item in results if item.get("check_id") == "source_regression_gate"),
            "",
        )
        or ""
    )
    if (
        '"guarded_postgres_tests":4' not in source_stdout
        or '"skipped":0' not in source_stdout
        or '"four_database_drill":true' not in source_stdout
    ):
        failed.append("source_regression_gate_zero_skip_contract")
    failed = list(dict.fromkeys(failed))
    plan["preflight_results"] = results
    plan["observed_baseline"] = parsed
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
    parser.add_argument(
        "--expected-commit",
        help="Exact 40-character feature-branch commit authorized for this run bundle.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "preflight" and args.skip_source_tests:
        raise SystemExit("--skip-source-tests is not allowed in preflight mode")
    if args.mode == "preflight" and not args.expected_commit:
        raise SystemExit("--expected-commit is mandatory in preflight mode")
    plan = build_plan(
        include_source_tests=not args.skip_source_tests,
        expected_commit=args.expected_commit,
    )
    exit_code = 0
    if args.mode == "preflight":
        plan, exit_code = execute_preflight(plan)
    else:
        plan["status"] = "planned"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output.chmod(0o600)
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
