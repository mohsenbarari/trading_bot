#!/usr/bin/env python3
"""Guarded one-scenario-at-a-time executor for the dark Witness real-host Matrix."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPT = ROOT / "scripts/writer_witness_matrix_client.py"
PREFLIGHT_SCHEMA = "writer_witness_real_host_matrix_preflight_v1"
RUNNER_SCHEMA = "writer_witness_real_host_matrix_runner_v1"
APPROVAL_SCHEMA = "writer_witness_real_host_matrix_observer_approval_v1"
EXPECTED_BRANCH = "feature/arvan-controlled-origin-failover"
CONFIRM_ENV = "WRITER_WITNESS_REAL_HOST_MATRIX_CONFIRM"
CONFIRM_VALUE = "execute-dark-witness-real-host-matrix"
SCENARIO_CONFIRM_ENV = "WRITER_WITNESS_REAL_HOST_MATRIX_SCENARIO"
OBSERVER_CONFIRM_ENV = "WRITER_WITNESS_REAL_HOST_MATRIX_OBSERVER_CONFIRM"
OBSERVER_CONFIRM_VALUE = "approve-one-dark-witness-scenario"
DEFAULT_ARTIFACT_ROOT = Path("/tmp/trading-bot-writer-witness-real-host-matrix/runs")
LOCK_PATH = Path("/tmp/trading-bot-writer-witness-real-host-matrix/active.lock")


@dataclass(frozen=True)
class Host:
    role: str
    address: str
    port: int = 22


HOSTS = {
    "webapp_fi": Host("webapp_fi", "65.109.220.59"),
    "webapp_ir": Host("webapp_ir", "87.236.212.194", 37067),
    "matrix_witness": Host("matrix_witness", "185.206.95.94"),
    "rollback_witness": Host("rollback_witness", "185.231.182.6"),
}


SCENARIOS: dict[str, tuple[str, ...]] = {
    "RH-001": ("stage_pairwise_clients", "concurrent_acquire", "assert_one_winner"),
    "RH-002": ("stage_fi_client", "close_after_commit", "exact_success_replay"),
    "RH-003": ("establish_live_fi_lease", "durable_ir_rejection", "replay_after_expiry"),
    "RH-004": ("establish_live_fi_lease", "partition_fi_to_witness", "ir_next_epoch"),
    "RH-005": ("establish_live_ir_lease", "partition_ir_to_witness", "fi_next_epoch"),
    "RH-006": ("service_stop_start", "service_sigstop_cont", "durable_state"),
    "RH-007": ("postgres_stop_start", "postgres_sigstop_cont", "durable_state"),
    "RH-008": ("record_state", "reboot_replacement_witness", "prove_reboot_persistence"),
    "RH-009": ("isolated_tmpfs_postgres", "force_disk_full", "prove_production_untouched"),
    "RH-010": ("client_clock_boundaries", "reject_past_and_future", "accept_inside_window"),
    "RH-011": ("partition_ir", "rotate_fi_overlap", "cross_key_replay_and_rollback"),
    "RH-012": ("acquire_epoch_one", "fault_each_restore_phase", "restore_exact_epoch_zero"),
}


class MatrixError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"cannot read Matrix JSON artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise MatrixError("Matrix JSON artifact must be an object")
    return payload


def parse_last_json(value: str) -> dict[str, Any]:
    for line in reversed(value.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise MatrixError("command output did not contain a JSON object")


def validate_preflight(payload: dict[str, Any], expected_head: str) -> dict[str, dict[str, str]]:
    if payload.get("schema_version") != PREFLIGHT_SCHEMA or payload.get("status") != "preflight_passed":
        raise MatrixError("a passing real-host preflight artifact is required")
    try:
        generated_at = datetime.fromisoformat(
            str(payload.get("generated_at")).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise MatrixError("preflight artifact timestamp is invalid") from exc
    age_seconds = (
        datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)
    ).total_seconds()
    if age_seconds < -30 or age_seconds > 1800:
        raise MatrixError("preflight artifact is outside the 30-minute execution window")
    git = payload.get("git")
    bundle = payload.get("run_bundle")
    baseline = payload.get("observed_baseline")
    if not isinstance(git, dict) or not isinstance(bundle, dict) or not isinstance(baseline, dict):
        raise MatrixError("preflight artifact lacks its frozen run bundle or observed baseline")
    if git.get("head") != expected_head or git.get("expected_commit") != expected_head:
        raise MatrixError("preflight artifact is not pinned to the current exact commit")
    if bundle.get("expected_commit") != expected_head:
        raise MatrixError("preflight run bundle commit mismatch")
    if payload.get("failed_checks"):
        raise MatrixError("preflight artifact contains failed checks")
    required = {
        "webapp_fi_baseline",
        "webapp_ir_standby_baseline",
        "matrix_witness_dark_baseline",
        "rollback_witness_baseline",
    }
    if not required.issubset(baseline) or not all(isinstance(baseline[item], dict) for item in required):
        raise MatrixError("preflight observed baseline is incomplete")
    matrix = baseline["matrix_witness_dark_baseline"]
    rollback = baseline["rollback_witness_baseline"]
    if matrix.get("state") != "webapp:0:vacant" or matrix.get("receipts") != "0":
        raise MatrixError("replacement Witness baseline is not epoch-zero vacant")
    if rollback.get("state") != "webapp:0:vacant" or rollback.get("receipts") != "0":
        raise MatrixError("original Witness baseline is not epoch-zero vacant")
    for key in ("manifest_sha256", "cert_sha256", "backup_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(matrix.get(key, ""))):
            raise MatrixError(f"replacement Witness baseline has invalid {key}")
    for key in ("manifest_sha256", "cert_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(rollback.get(key, ""))):
            raise MatrixError(f"original Witness baseline has invalid {key}")
    if not re.fullmatch(r"writer-witness-[0-9]{8}T[0-9]{6}Z\.dump", str(matrix.get("backup", ""))):
        raise MatrixError("replacement Witness baseline backup identity is invalid")
    source = next(
        (item for item in payload.get("preflight_results", []) if item.get("check_id") == "source_regression_gate"),
        None,
    )
    source_output = str((source or {}).get("stdout") or "")
    for marker in ('"guarded_postgres_tests":4', '"skipped":0', '"four_database_drill":true'):
        if marker not in source_output:
            raise MatrixError("preflight source gate is not zero-skip and four-database complete")
    return baseline  # type: ignore[return-value]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_approval(
    payload: dict[str, Any],
    *,
    scenario: str,
    expected_head: str,
    preflight_sha256: str,
    operator: str,
) -> tuple[str, str]:
    if payload.get("schema_version") != APPROVAL_SCHEMA or payload.get("status") != "approved":
        raise MatrixError("independent observer approval is missing or invalid")
    if payload.get("scenario") != scenario or payload.get("expected_commit") != expected_head:
        raise MatrixError("observer approval is for a different scenario or commit")
    if payload.get("preflight_sha256") != preflight_sha256:
        raise MatrixError("observer approval is not bound to the supplied preflight")
    observer = str(payload.get("observer") or "").strip()
    commander = str(payload.get("incident_commander") or "").strip()
    if not observer or not commander or operator in {observer, commander}:
        raise MatrixError("operator must be distinct from observer and incident commander")
    if not all(
        payload.get(field) is True
        for field in (
            "out_of_band_console_ready",
            "alternate_communications_ready",
            "maintenance_window_confirmed",
            "dpi_budget_confirmed",
            "restore_authorized",
        )
    ):
        raise MatrixError("observer approval lacks an operational safety assertion")
    try:
        expires = datetime.fromisoformat(str(payload.get("expires_at")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise MatrixError("observer approval expiry is invalid") from exc
    if expires <= datetime.now(timezone.utc):
        raise MatrixError("observer approval has expired")
    return observer, commander


class Controller:
    def __init__(
        self,
        *,
        scenario: str,
        operator: str,
        observer: str,
        incident_commander: str,
        reason: str,
        expected_head: str,
        preflight: dict[str, Any],
        baseline: dict[str, dict[str, str]],
        artifact_dir: Path,
    ) -> None:
        self.scenario = scenario
        self.operator = operator
        self.observer = observer
        self.incident_commander = incident_commander
        self.reason = reason
        self.expected_head = expected_head
        self.preflight = preflight
        self.baseline = baseline
        seed = f"{scenario}|{operator}|{observer}|{utc_now()}".encode()
        self.tag = "wwm_" + hashlib.sha256(seed).hexdigest()[:12]
        self.remote_root = f"/run/writer-witness-matrix/{self.tag}"
        self.artifact_dir = artifact_dir
        self.events_path = artifact_dir / "events.jsonl"
        self.summary_path = artifact_dir / "summary.json"
        self.local_secret_root = Path(tempfile.mkdtemp(prefix=f"{self.tag}-", dir="/tmp"))
        os.chmod(self.local_secret_root, 0o700)
        self._event_lock = threading.Lock()
        self.network_fault_sites: set[str] = set()
        self.staged_sites: set[str] = set()
        self.witness_mutated = False
        self.rotation_site: str | None = None
        self.control_requests = 0
        self.started_at = utc_now()

    def event(self, event: str, **fields: Any) -> None:
        payload = {
            "schema_version": RUNNER_SCHEMA,
            "at": utc_now(),
            "scenario": self.scenario,
            "tag": self.tag,
            "event": event,
            **fields,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        with self._event_lock:
            descriptor = os.open(self.events_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, encoded.encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    @staticmethod
    def ssh_args(host: Host, command: str) -> list[str]:
        args = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes",
        ]
        if host.port != 22:
            args.extend(("-p", str(host.port)))
        args.extend((f"root@{host.address}", command))
        return args

    @staticmethod
    def scp_args(host: Host, source: str, destination: str, *, from_remote: bool) -> list[str]:
        args = ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes"]
        if host.port != 22:
            args.extend(("-P", str(host.port)))
        if from_remote:
            args.extend((f"root@{host.address}:{source}", destination))
        else:
            args.extend((source, f"root@{host.address}:{destination}"))
        return args

    def command(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 30,
        check: bool = True,
        record_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self.event("command.start", name=name, command_sha256=hashlib.sha256("\0".join(args).encode()).hexdigest())
        completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        stdout = completed.stdout[-12000:] if record_output else "[redacted transient transfer]"
        stderr = completed.stderr[-12000:] if record_output else "[redacted transient transfer]"
        self.event(
            "command.end", name=name, return_code=completed.returncode,
            stdout=stdout, stderr=stderr,
        )
        if check and completed.returncode != 0:
            raise MatrixError(f"{name} failed with exit code {completed.returncode}")
        return completed

    def remote(
        self,
        role: str,
        name: str,
        command: str,
        *,
        timeout: int = 30,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.command(name, self.ssh_args(HOSTS[role], command), timeout=timeout, check=check)

    def transfer_from(self, role: str, remote_path: str, local_path: Path, name: str) -> None:
        self.command(
            name,
            self.scp_args(HOSTS[role], remote_path, str(local_path), from_remote=True),
            timeout=30,
            record_output=False,
        )
        os.chmod(local_path, 0o600)

    def transfer_to(self, role: str, local_path: Path, remote_path: str, name: str) -> None:
        self.command(
            name,
            self.scp_args(HOSTS[role], str(local_path), remote_path, from_remote=False),
            timeout=30,
            record_output=False,
        )

    def stage_site(self, site: str, *, remote_name: str = "client.env") -> None:
        if site not in {"webapp_fi", "webapp_ir"}:
            raise MatrixError("unsupported WebApp Matrix site")
        short = site.removeprefix("webapp_")
        local_env = self.local_secret_root / f"{site}-{remote_name}"
        local_ca = self.local_secret_root / "witness-ca.crt"
        self.transfer_from(
            "matrix_witness",
            f"/root/writer-witness-client-material/webapp-{short}.env",
            local_env,
            f"fetch_{site}_{remote_name}",
        )
        if not local_ca.exists():
            self.transfer_from(
                "matrix_witness",
                "/root/writer-witness-client-material/witness-ca.crt",
                local_ca,
                "fetch_witness_ca",
            )
        self.remote(
            site,
            f"prepare_{site}_runtime_dir",
            f"install -d -m 0700 -o root -g root {shlex.quote(self.remote_root)}",
        )
        self.transfer_to(site, local_env, f"{self.remote_root}/{remote_name}", f"stage_{site}_{remote_name}")
        self.transfer_to(site, local_ca, f"{self.remote_root}/witness-ca.crt", f"stage_{site}_ca")
        self.transfer_to(site, CLIENT_SCRIPT, f"{self.remote_root}/client.py", f"stage_{site}_client")
        self.remote(
            site,
            f"secure_{site}_runtime_files",
            f"chmod 0600 {shlex.quote(self.remote_root)}/client.env {shlex.quote(self.remote_root)}/witness-ca.crt 2>/dev/null || true; "
            f"chmod 0600 {shlex.quote(self.remote_root)}/{shlex.quote(remote_name)}; chmod 0700 {shlex.quote(self.remote_root)}/client.py",
        )
        self.staged_sites.add(site)

    def client(
        self,
        site: str,
        command: str,
        *,
        request_id: str,
        env_name: str = "client.env",
        expected_statuses: tuple[int, ...] = (200,),
        action: str | None = None,
        expected_epoch: int | None = None,
        expected_lease_id: str | None = None,
        reason: str | None = None,
        lease_seconds: int = 30,
        timestamp_offset: int = 0,
        check: bool = True,
        timeout: int = 15,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
        self.control_requests += 1
        if self.control_requests > 100:
            raise MatrixError("scenario exceeded the approved 100-request control-plane budget")
        parts = [
            "python3", f"{self.remote_root}/client.py", command,
            "--env-file", f"{self.remote_root}/{env_name}",
            "--ca-bundle", f"{self.remote_root}/witness-ca.crt",
            "--site", site,
            "--request-id", request_id,
            "--timeout", "5",
            "--timestamp-offset-seconds", str(timestamp_offset),
        ]
        for status_code in expected_statuses:
            parts.extend(("--expect-http-status", str(status_code)))
        if action is not None:
            parts.extend(("--action", action))
        if expected_epoch is not None:
            parts.extend(("--expected-epoch", str(expected_epoch)))
        if expected_lease_id is not None:
            parts.extend(("--expected-lease-id", expected_lease_id))
        if reason is not None:
            parts.extend(("--reason", reason))
        if command != "status":
            parts.extend(("--lease-duration-seconds", str(lease_seconds)))
        completed = self.remote(site, f"client_{site}_{request_id}", shlex.join(parts), timeout=timeout, check=check)
        if completed.returncode != 0:
            return completed, None
        return completed, parse_last_json(completed.stdout)

    def transition(
        self,
        site: str,
        *,
        action: str,
        expected_epoch: int,
        request_id: str,
        expected_lease_id: str | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        env_name: str = "client.env",
        fire_and_close: bool = False,
    ) -> dict[str, Any]:
        self.witness_mutated = True
        _, payload = self.client(
            site,
            "fire-and-close" if fire_and_close else "transition",
            request_id=request_id,
            env_name=env_name,
            expected_statuses=expected_statuses,
            action=action,
            expected_epoch=expected_epoch,
            expected_lease_id=expected_lease_id,
            reason=f"{self.tag} {self.scenario} {action}",
        )
        if payload is None:
            raise MatrixError("transition did not produce a result")
        return payload

    def witness_state(self, role: str = "matrix_witness") -> dict[str, Any]:
        command = (
            "set -Eeuo pipefail; "
            "state=$(runuser -u postgres -- psql -XAt writer_witness -c \"SELECT json_build_object(" 
            "'holder_site',holder_site,'writer_epoch',writer_epoch,'lease_id',lease_id," 
            "'lease_status',lease_status,'issued_at',issued_at,'expires_at',expires_at," 
            "'transition_id',transition_id)::text FROM webapp_writer_witness_state\"); "
            "receipts=$(runuser -u postgres -- psql -XAt writer_witness -c 'SELECT count(*) FROM webapp_writer_witness_receipts'); "
            "manifest=$(/usr/local/sbin/writer-witness-state-manifest); "
            "printf '{\"state\":%s,\"receipts\":%s,\"manifest_sha256\":\"%s\"}\\n' \"$state\" \"$receipts\" \"$manifest\""
        )
        completed = self.remote(role, f"inspect_{role}_state", command)
        return parse_last_json(completed.stdout)

    def acquire(self, site: str, request_id: str) -> dict[str, Any]:
        payload = self.transition(site, action="acquire", expected_epoch=0, request_id=request_id)
        response = payload.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("state"), dict):
            raise MatrixError("accepted acquisition response lacks Witness state")
        return response["state"]

    def wait_until_expired(self, state: dict[str, Any]) -> None:
        value = state.get("expires_at")
        if not isinstance(value, str):
            raise MatrixError("lease expiration is missing")
        expires = datetime.fromisoformat(value.replace("Z", "+00:00"))
        seconds = max(0.0, (expires - datetime.now(timezone.utc)).total_seconds() + 2.0)
        if seconds > 40:
            raise MatrixError("lease expiry wait exceeds the Matrix 30-second term")
        self.event("lease.wait", seconds=seconds)
        time.sleep(seconds)

    def add_partition(self, site: str) -> None:
        table = self.tag
        command = (
            f"set -Eeuo pipefail; nft list table inet {table} >/dev/null 2>&1 && exit 31 || true; "
            f"nft add table inet {table}; "
            f"nft 'add chain inet {table} output {{ type filter hook output priority -150; policy accept; }}'; "
            f"nft add rule inet {table} output ip daddr {HOSTS['matrix_witness'].address} tcp dport 443 "
            f"counter drop comment {shlex.quote(self.tag)}; nft list table inet {table}"
        )
        self.remote(site, f"partition_{site}_to_witness", command)
        self.network_fault_sites.add(site)

    def remove_partition(self, site: str) -> None:
        table = self.tag
        self.remote(
            site,
            f"remove_partition_{site}",
            f"set -Eeuo pipefail; nft list table inet {table} >/dev/null 2>&1 && nft delete table inet {table} || true; "
            f"! nft list table inet {table} >/dev/null 2>&1",
        )
        self.network_fault_sites.discard(site)

    def assert_unreachable(self, site: str, request_id: str) -> None:
        completed, _ = self.client(site, "status", request_id=request_id, check=False, timeout=12)
        if completed.returncode == 0:
            raise MatrixError(f"{site} unexpectedly reached the Witness during its partition")

    def run_scenario(self) -> None:
        handler = getattr(self, f"scenario_{self.scenario.replace('-', '_')}", None)
        if handler is None:
            raise MatrixError(f"scenario handler is missing for {self.scenario}")
        self.event("scenario.start", steps=list(SCENARIOS[self.scenario]))
        handler()
        self.event("scenario.assertions_passed")

    def scenario_RH_001(self) -> None:
        self.stage_site("webapp_fi")
        self.stage_site("webapp_ir")
        def attempt(site: str) -> dict[str, Any]:
            return self.transition(
                site, action="acquire", expected_epoch=0,
                request_id=f"{self.tag}-{site[-2:]}-race", expected_statuses=(200, 409),
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, ("webapp_fi", "webapp_ir")))
        codes = [int(item["http_status"]) for item in outcomes]
        if sorted(codes) != [200, 409]:
            raise MatrixError(f"concurrent acquisition was not exclusive: {codes}")
        state = self.witness_state()
        if state["state"].get("writer_epoch") != 1 or state["receipts"] != 2:
            raise MatrixError("concurrent acquisition did not persist one term and two receipts")

    def scenario_RH_002(self) -> None:
        self.stage_site("webapp_fi")
        request_id = f"{self.tag}-lost-response"
        self.transition("webapp_fi", action="acquire", expected_epoch=0, request_id=request_id, fire_and_close=True)
        for _ in range(20):
            state = self.witness_state()
            if state["receipts"] == 1:
                break
            time.sleep(0.25)
        else:
            raise MatrixError("fire-and-close request did not durably commit")
        replay = self.transition("webapp_fi", action="acquire", expected_epoch=0, request_id=request_id)
        if replay.get("response", {}).get("replayed") is not True:
            raise MatrixError("lost committed response was not exactly replayed")

    def scenario_RH_003(self) -> None:
        self.stage_site("webapp_fi")
        self.stage_site("webapp_ir")
        lease = self.acquire("webapp_fi", f"{self.tag}-fi-term")
        request_id = f"{self.tag}-delayed-ir"
        first = self.transition(
            "webapp_ir", action="acquire", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=request_id,
            expected_statuses=(409,),
        )
        if first.get("response", {}).get("replayed") is not False:
            raise MatrixError("first live-lease rejection was unexpectedly a replay")
        before = self.witness_state()["receipts"]
        self.wait_until_expired(lease)
        replay = self.transition(
            "webapp_ir", action="acquire", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=request_id,
            expected_statuses=(409,),
        )
        if replay.get("response", {}).get("replayed") is not True:
            raise MatrixError("durable negative receipt did not remain one-shot")
        if self.witness_state()["receipts"] != before:
            raise MatrixError("negative replay created a second receipt")

    def directional_partition(self, holder: str, next_site: str) -> None:
        self.stage_site(holder)
        self.stage_site(next_site)
        lease = self.acquire(holder, f"{self.tag}-{holder[-2:]}-term")
        self.add_partition(holder)
        self.assert_unreachable(holder, f"{self.tag}-blocked-status")
        self.wait_until_expired(lease)
        promoted = self.transition(
            next_site, action="acquire", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=f"{self.tag}-{next_site[-2:]}-epoch2",
        )
        if promoted.get("response", {}).get("state", {}).get("writer_epoch") != 2:
            raise MatrixError("reachable peer did not acquire the next epoch after expiry")

    def scenario_RH_004(self) -> None:
        self.directional_partition("webapp_fi", "webapp_ir")

    def scenario_RH_005(self) -> None:
        self.directional_partition("webapp_ir", "webapp_fi")

    def scenario_RH_006(self) -> None:
        self.stage_site("webapp_fi")
        lease = self.acquire("webapp_fi", f"{self.tag}-service-term")
        self.remote("matrix_witness", "stop_witness_service", "systemctl stop writer-witness.service")
        self.assert_unreachable("webapp_fi", f"{self.tag}-service-stopped")
        self.remote("matrix_witness", "start_witness_service", "systemctl start writer-witness.service")
        self.client("webapp_fi", "status", request_id=f"{self.tag}-service-restarted")
        self.remote(
            "matrix_witness", "pause_witness_service",
            "pid=$(systemctl show -p MainPID --value writer-witness.service); test \"$pid\" -gt 1; kill -STOP \"$pid\"",
        )
        self.assert_unreachable("webapp_fi", f"{self.tag}-service-paused")
        self.remote(
            "matrix_witness", "resume_witness_service",
            "pid=$(systemctl show -p MainPID --value writer-witness.service); test \"$pid\" -gt 1; kill -CONT \"$pid\"",
        )
        renewed = self.transition(
            "webapp_fi", action="renew", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=f"{self.tag}-renew-after-service",
        )
        if renewed.get("response", {}).get("state", {}).get("lease_id") != lease["lease_id"]:
            raise MatrixError("service restart changed the durable lease identity")

    def scenario_RH_007(self) -> None:
        self.stage_site("webapp_fi")
        lease = self.acquire("webapp_fi", f"{self.tag}-postgres-term")
        self.remote("matrix_witness", "stop_witness_postgres", "systemctl stop postgresql.service", timeout=30)
        self.assert_unreachable("webapp_fi", f"{self.tag}-postgres-stopped")
        self.remote(
            "matrix_witness", "start_witness_postgres",
            "systemctl start postgresql.service; systemctl start writer-witness.service",
        )
        self.client("webapp_fi", "status", request_id=f"{self.tag}-postgres-restarted")
        self.remote(
            "matrix_witness", "pause_witness_postgres",
            "pid=$(head -1 /var/lib/postgresql/*/main/postmaster.pid); test \"$pid\" -gt 1; kill -STOP \"$pid\"",
        )
        self.assert_unreachable("webapp_fi", f"{self.tag}-postgres-paused")
        self.remote(
            "matrix_witness", "resume_witness_postgres",
            "pid=$(head -1 /var/lib/postgresql/*/main/postmaster.pid); test \"$pid\" -gt 1; kill -CONT \"$pid\"",
        )
        renewed = self.transition(
            "webapp_fi", action="renew", expected_epoch=1,
            expected_lease_id=str(lease["lease_id"]), request_id=f"{self.tag}-renew-after-postgres",
        )
        if renewed.get("response", {}).get("state", {}).get("lease_id") != lease["lease_id"]:
            raise MatrixError("PostgreSQL restart changed the durable lease identity")

    def wait_for_ssh_after_reboot(self) -> None:
        saw_down = False
        for _ in range(36):
            completed = subprocess.run(
                self.ssh_args(HOSTS["matrix_witness"], "true"),
                cwd=ROOT, capture_output=True, text=True, timeout=12,
            )
            if completed.returncode != 0:
                saw_down = True
            elif saw_down:
                self.event("reboot.ssh_restored")
                return
            time.sleep(5)
        raise MatrixError("replacement Witness did not complete a bounded reboot")

    def scenario_RH_008(self) -> None:
        self.stage_site("webapp_fi")
        self.acquire("webapp_fi", f"{self.tag}-reboot-term")
        before = self.witness_state()
        self.remote("matrix_witness", "reboot_replacement_witness", "systemctl reboot", check=False, timeout=12)
        self.wait_for_ssh_after_reboot()
        self.remote(
            "matrix_witness", "verify_services_after_reboot",
            "for unit in postgresql nginx writer-witness; do test \"$(systemctl is-active \"$unit\")\" = active; done",
        )
        after = self.witness_state()
        if after != before:
            raise MatrixError("replacement Witness durable state changed across reboot")

    def scenario_RH_009(self) -> None:
        self.remote(
            "matrix_witness", "isolated_postgres_disk_full",
            f"/usr/local/sbin/writer-witness-matrix-host-faults disk-full --tag {self.tag}",
            timeout=180,
        )
        current = self.witness_state()
        expected = self.baseline["matrix_witness_dark_baseline"]
        if current["manifest_sha256"] != expected["manifest_sha256"]:
            raise MatrixError("isolated disk-full scenario touched the production Witness database")

    def scenario_RH_010(self) -> None:
        self.stage_site("webapp_fi")
        cases = ((-17, 401), (7, 401), (-14, 200), (4, 200))
        for offset, status_code in cases:
            _, payload = self.client(
                "webapp_fi", "status", request_id=f"{self.tag}-clock-{offset:+d}",
                expected_statuses=(status_code,), timestamp_offset=offset,
            )
            if payload is None or payload["http_status"] != status_code:
                raise MatrixError(f"clock boundary {offset} did not return {status_code}")

    def scenario_RH_011(self) -> None:
        self.stage_site("webapp_fi")
        self.stage_site("webapp_ir")
        self.remote(
            "webapp_fi", "retain_old_fi_client",
            f"cp {self.remote_root}/client.env {self.remote_root}/old.env; chmod 0600 {self.remote_root}/old.env",
        )
        self.add_partition("webapp_ir")
        self.remote(
            "matrix_witness", "prepare_fi_hmac_rotation",
            "/usr/local/sbin/writer-witness-rotate-hmac prepare --site webapp_fi --expected-epoch 0",
        )
        self.rotation_site = "webapp_fi"
        self.stage_site("webapp_fi", remote_name="client.env")
        self.client("webapp_fi", "status", env_name="old.env", request_id=f"{self.tag}-old-overlap")
        self.client("webapp_fi", "status", request_id=f"{self.tag}-new-overlap")
        request_id = f"{self.tag}-cross-key-replay"
        old = self.transition(
            "webapp_fi", action="acquire", expected_epoch=99, request_id=request_id,
            expected_statuses=(409,), env_name="old.env",
        )
        new = self.transition(
            "webapp_fi", action="acquire", expected_epoch=99, request_id=request_id,
            expected_statuses=(409,), env_name="client.env",
        )
        if old.get("response", {}).get("replayed") is not False or new.get("response", {}).get("replayed") is not True:
            raise MatrixError("exact durable request did not replay across HMAC key generations")
        self.remote(
            "matrix_witness", "revoke_old_fi_hmac",
            "/usr/local/sbin/writer-witness-rotate-hmac revoke --site webapp_fi --expected-epoch 0",
        )
        self.client(
            "webapp_fi", "status", env_name="old.env", request_id=f"{self.tag}-old-revoked",
            expected_statuses=(401,),
        )
        self.client("webapp_fi", "status", request_id=f"{self.tag}-new-active")
        self.remote(
            "matrix_witness", "rollback_fi_hmac_rotation",
            "/usr/local/sbin/writer-witness-rotate-hmac rollback --site webapp_fi --expected-epoch 0",
        )
        self.remote(
            "matrix_witness", "finish_fi_hmac_rotation_rollback",
            "/usr/local/sbin/writer-witness-rotate-hmac finish --site webapp_fi --expected-epoch 0",
        )
        self.rotation_site = None
        self.client("webapp_fi", "status", env_name="old.env", request_id=f"{self.tag}-old-restored")

    def restore_once(self, *, fail_after: str | None = None, expect_failure: bool = False) -> None:
        baseline = self.baseline["matrix_witness_dark_baseline"]
        current = self.witness_state()
        backup = str(baseline["backup"])
        if not re.fullmatch(r"writer-witness-[0-9]{8}T[0-9]{6}Z\.dump", backup):
            raise MatrixError("pinned restore backup filename is unsafe")
        environment = {
            "WRITER_WITNESS_RESTORE_EXPECTED_STATE": "webapp:0:vacant",
            "WRITER_WITNESS_RESTORE_EXPECTED_RECEIPTS": "0",
            "WRITER_WITNESS_RESTORE_EXPECTED_MANIFEST_SHA256": baseline["manifest_sha256"],
            "WRITER_WITNESS_RESTORE_EXPECTED_BACKUP_SHA256": baseline["backup_sha256"],
            "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE": (
                f"webapp:{current['state']['writer_epoch']}:{current['state']['lease_status']}"
            ),
            "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS": str(current["receipts"]),
            "WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256": current["manifest_sha256"],
        }
        if fail_after:
            environment["WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER"] = fail_after
        command = (
            "env " + " ".join(f"{key}={shlex.quote(str(value))}" for key, value in sorted(environment.items()))
            + " /usr/local/sbin/writer-witness-live-restore --apply-from-stdin"
            + f" < /var/backups/trading-bot-witness/{backup}"
        )
        completed = self.remote(
            "matrix_witness", f"restore_baseline_{fail_after or 'success'}", command,
            timeout=180, check=not expect_failure,
        )
        if expect_failure:
            if completed.returncode == 0:
                raise MatrixError(f"restore fault point {fail_after} unexpectedly succeeded")
            recovered = self.witness_state()
            if recovered != current:
                raise MatrixError(f"restore recovery after {fail_after} did not preserve the prior OID state")

    def scenario_RH_012(self) -> None:
        self.stage_site("webapp_fi")
        self.acquire("webapp_fi", f"{self.tag}-restore-term")
        for point in (
            "service_stopped", "current_disabled", "current_renamed",
            "candidate_promoted", "candidate_enabled", "service_started",
        ):
            self.restore_once(fail_after=point, expect_failure=True)
        self.restore_once()
        self.witness_mutated = False
        final = self.witness_state()
        if final["manifest_sha256"] != self.baseline["matrix_witness_dark_baseline"]["manifest_sha256"]:
            raise MatrixError("final restore did not reproduce the pinned epoch-zero manifest")

    def capture_pre_recovery(self) -> None:
        for role in ("matrix_witness", "rollback_witness", "webapp_fi", "webapp_ir"):
            witness_manifest = (
                "/usr/local/sbin/writer-witness-state-manifest --json; "
                if role in {"matrix_witness", "rollback_witness"}
                else ""
            )
            evidence_command = (
                "set -Eeuo pipefail; date -u +%FT%TZ; "
                "systemctl is-active writer-witness postgresql nginx 2>/dev/null || true; "
                "nft list ruleset 2>/dev/null | sha256sum; "
                + witness_manifest
                + "find /var/lib/trading-bot-witness/restore-state -maxdepth 2 -type f "
                "-printf '%p %m %s\\n' 2>/dev/null || true; "
                "find /run/writer-witness-matrix -maxdepth 3 -type f "
                "-printf '%p %m\\n' 2>/dev/null || true; "
                "echo evidence_capture_complete"
            )
            self.remote(
                role,
                f"capture_{role}_pre_recovery",
                evidence_command,
            )
        self.event("evidence.pre_recovery_retained")

    def stop_and_remove_requesters(self) -> None:
        for site in sorted(self.staged_sites):
            pattern = self.remote_root + "/[c]lient.py"
            self.remote(
                site,
                f"stop_requesters_{site}",
                f"pids=$(pgrep -f {shlex.quote(pattern)} || true); "
                "test -z \"$pids\" || kill $pids; "
                "for i in $(seq 1 20); do pgrep -f "
                f"{shlex.quote(pattern)} >/dev/null || break; sleep 0.1; done; "
                f"! pgrep -f {shlex.quote(pattern)}; rm -rf {shlex.quote(self.remote_root)}",
            )
        self.staged_sites.clear()

    def recover_rotation(self) -> None:
        if self.rotation_site:
            site = self.rotation_site
            command = (
                "set -Eeuo pipefail; "
                f"if test -f /var/lib/trading-bot-witness/hmac-rotation/{site}/metadata.json; then "
                f"/usr/local/sbin/writer-witness-rotate-hmac rollback --site {site} --expected-epoch 0; "
                f"/usr/local/sbin/writer-witness-rotate-hmac finish --site {site} --expected-epoch 0; fi; "
                f"test ! -e /var/lib/trading-bot-witness/hmac-rotation/{site}"
            )
            self.remote("matrix_witness", "recover_hmac_rotation", command)
            self.rotation_site = None

    def resume_witness_runtime(self) -> None:
        self.remote(
            "matrix_witness", "resume_replacement_runtime",
            "set -Eeuo pipefail; "
            "pid=$(systemctl show -p MainPID --value writer-witness.service); test \"${pid:-0}\" -le 1 || kill -CONT \"$pid\"; "
            "for file in /var/lib/postgresql/*/main/postmaster.pid; do test -f \"$file\" || continue; pid=$(head -1 \"$file\"); kill -CONT \"$pid\" 2>/dev/null || true; done; "
            "systemctl start postgresql.service nginx.service writer-witness.service; "
            "test \"$(systemctl is-active postgresql.service)\" = active; "
            "test \"$(systemctl is-active nginx.service)\" = active; "
            "test \"$(systemctl is-active writer-witness.service)\" = active",
            timeout=45,
        )

    def remove_isolated_pressure(self) -> None:
        self.remote(
            "matrix_witness", "remove_isolated_pressure",
            f"set +e; root=/run/{self.tag}-disk; "
            "test ! -d \"$root\" || { mountpoint -q \"$root\" && umount \"$root\" || true; rm -rf \"$root\"; }; "
            "test ! -e \"$root\"",
        )

    def verify_complete_baseline(self) -> None:
        matrix = self.witness_state("matrix_witness")
        original = self.witness_state("rollback_witness")
        if matrix["manifest_sha256"] != self.baseline["matrix_witness_dark_baseline"]["manifest_sha256"]:
            raise MatrixError("replacement Witness did not return to its pinned full manifest")
        if original["manifest_sha256"] != self.baseline["rollback_witness_baseline"]["manifest_sha256"]:
            raise MatrixError("original Witness changed during the Matrix scenario")
        for role in ("webapp_fi", "webapp_ir"):
            self.remote(
                role,
                f"verify_no_hidden_state_{role}",
                f"test ! -e {shlex.quote(self.remote_root)}; "
                f"nft list table inet {self.tag} >/dev/null 2>&1 && exit 1 || true",
            )
        self.event("baseline.verified", replacement_manifest=matrix["manifest_sha256"], original_manifest=original["manifest_sha256"])

    def cleanup(self) -> None:
        self.event("cleanup.start", order=[
            "stop_and_join_requesters", "revoke_transient_capability", "retain_pre_recovery_evidence",
            "resume_paused_runtime", "remove_isolated_pressure", "remove_scoped_network_faults",
            "restore_vacant_baseline", "verify_complete_baseline",
        ])
        errors: list[str] = []
        ordered_actions = (
            ("stop_and_join_requesters", self.stop_and_remove_requesters),
            ("revoke_transient_capability", self.recover_rotation),
            ("retain_pre_recovery_evidence", self.capture_pre_recovery),
            ("resume_paused_runtime", self.resume_witness_runtime),
            ("remove_isolated_pressure", self.remove_isolated_pressure),
        )
        for name, action in ordered_actions:
            try:
                action()
                self.event("cleanup.step", step=name, status="passed")
            except Exception as exc:
                errors.append(f"{name}:{exc}")
                self.event("cleanup.step", step=name, status="failed", error=str(exc))
                break
        if errors:
            shutil.rmtree(self.local_secret_root, ignore_errors=True)
            raise MatrixError(
                "cleanup stopped before reconnect/restore to preserve fault isolation: "
                + "; ".join(errors)
            )
        for site in list(self.network_fault_sites):
            try:
                self.remove_partition(site)
            except Exception as exc:
                errors.append(f"remove_scoped_network_faults:{site}:{exc}")
        try:
            if self.witness_mutated:
                self.restore_once()
                self.witness_mutated = False
            self.event("cleanup.step", step="restore_vacant_baseline", status="passed")
        except Exception as exc:
            errors.append(f"restore_vacant_baseline:{exc}")
            self.event("cleanup.step", step="restore_vacant_baseline", status="failed", error=str(exc))
        try:
            self.verify_complete_baseline()
            self.event("cleanup.step", step="verify_complete_baseline", status="passed")
        except Exception as exc:
            errors.append(f"verify_complete_baseline:{exc}")
            self.event("cleanup.step", step="verify_complete_baseline", status="failed", error=str(exc))
        shutil.rmtree(self.local_secret_root, ignore_errors=True)
        if errors:
            raise MatrixError("; ".join(errors))
        self.event("cleanup.complete")

    def write_summary(self, *, status: str, error: str | None = None) -> None:
        payload = {
            "schema_version": RUNNER_SCHEMA,
            "status": status,
            "scenario": self.scenario,
            "tag": self.tag,
            "operator": self.operator,
            "observer": self.observer,
            "incident_commander": self.incident_commander,
            "reason": self.reason,
            "expected_commit": self.expected_head,
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "error": error,
            "cleanup_verified": status == "passed",
            "arvan_changed": False,
            "webapp_writer_flags_changed": False,
            "original_witness_used_for_transition": False,
            "control_requests": self.control_requests,
            "control_request_budget": 100,
        }
        temporary = self.summary_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, self.summary_path)


def git_value(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def acquire_local_lock(scenario: str, expected_head: str) -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(LOCK_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise MatrixError(f"another Matrix run owns {LOCK_PATH}") from exc
    os.write(descriptor, f"pid={os.getpid()}\nscenario={scenario}\nhead={expected_head}\n".encode())
    os.fsync(descriptor)
    return descriptor


def build_plan(args: argparse.Namespace, expected_head: str) -> dict[str, Any]:
    return {
        "schema_version": RUNNER_SCHEMA,
        "status": "planned",
        "mutates_state": False,
        "scenario": args.scenario,
        "steps": list(SCENARIOS[args.scenario]),
        "expected_commit": expected_head,
        "safety": {
            "one_scenario_per_process": True,
            "requires_passing_pinned_preflight": True,
            "requires_distinct_operator_observer": True,
            "requires_preflight_bound_observer_approval": True,
            "requires_out_of_band_console_and_restore_authorization": True,
            "keeps_network_isolation_until_requesters_and_credentials_are_gone": True,
            "retains_evidence_before_restore": True,
            "uses_only_replacement_witness_for_transitions": True,
            "arvan_changes_forbidden": True,
            "webapp_writer_activation_forbidden": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "approve", "execute"), default="plan")
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--operator")
    parser.add_argument("--observer")
    parser.add_argument("--incident-commander")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--reason")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected_head = args.expected_commit.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise MatrixError("--expected-commit must be one exact lowercase Git SHA")
    if args.mode == "plan":
        payload = build_plan(args, expected_head)
        output = args.output or Path(f"/tmp/{args.scenario.lower()}-writer-witness-plan.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(output, 0o600)
        print(json.dumps({"status": "planned", "scenario": args.scenario, "output": str(output)}, sort_keys=True))
        return 0

    if args.mode == "approve":
        if os.getenv(OBSERVER_CONFIRM_ENV) != OBSERVER_CONFIRM_VALUE:
            raise MatrixError("observer approval confirmation is missing")
        if not args.preflight or not args.observer or not args.incident_commander or not args.reason:
            raise MatrixError("approve mode requires preflight, observer, incident commander, and reason")
        if args.observer.strip() == args.incident_commander.strip():
            raise MatrixError("abort observer and incident commander must be distinct")
        preflight = safe_json(args.preflight)
        validate_preflight(preflight, expected_head)
        output = args.output or Path(f"/tmp/{args.scenario.lower()}-writer-witness-approval.json")
        payload = {
            "schema_version": APPROVAL_SCHEMA,
            "status": "approved",
            "scenario": args.scenario,
            "expected_commit": expected_head,
            "preflight_sha256": file_sha256(args.preflight),
            "observer": args.observer.strip(),
            "incident_commander": args.incident_commander.strip(),
            "reason": args.reason.strip(),
            "approved_at": utc_now(),
            "expires_at": datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat().replace("+00:00", "Z"),
            "out_of_band_console_ready": True,
            "alternate_communications_ready": True,
            "maintenance_window_confirmed": True,
            "dpi_budget_confirmed": True,
            "restore_authorized": True,
            "max_control_requests": 100,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        print(json.dumps({"status": "approved", "scenario": args.scenario, "output": str(output)}, sort_keys=True))
        return 0

    if os.getenv(CONFIRM_ENV) != CONFIRM_VALUE or os.getenv(SCENARIO_CONFIRM_ENV) != args.scenario:
        raise MatrixError("live Matrix execution confirmation is missing or does not match the one scenario")
    if not args.preflight or not args.approval or not args.operator or not args.reason:
        raise MatrixError("execute mode requires preflight, approval, operator, and reason")
    if git_value("branch", "--show-current") != EXPECTED_BRANCH:
        raise MatrixError("Matrix runner is on the wrong branch")
    if git_value("rev-parse", "HEAD") != expected_head or git_value("status", "--porcelain"):
        raise MatrixError("Matrix runner requires the exact clean frozen commit")
    preflight = safe_json(args.preflight)
    baseline = validate_preflight(preflight, expected_head)
    approval = safe_json(args.approval)
    observer, incident_commander = validate_approval(
        approval,
        scenario=args.scenario,
        expected_head=expected_head,
        preflight_sha256=file_sha256(args.preflight),
        operator=args.operator.strip(),
    )

    artifact_dir = args.artifact_dir or (DEFAULT_ARTIFACT_ROOT / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{args.scenario.lower()}")
    artifact_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    lock_descriptor = acquire_local_lock(args.scenario, expected_head)
    controller = Controller(
        scenario=args.scenario,
        operator=args.operator.strip(),
        observer=observer,
        incident_commander=incident_commander,
        reason=args.reason.strip(),
        expected_head=expected_head,
        preflight=preflight,
        baseline=baseline,
        artifact_dir=artifact_dir,
    )
    error: str | None = None
    try:
        controller.run_scenario()
    except Exception as exc:
        error = str(exc)
        controller.event("scenario.failed", error=error)
    try:
        controller.cleanup()
    except Exception as exc:
        error = f"{error + '; ' if error else ''}cleanup:{exc}"
    status = "passed" if error is None else "failed"
    controller.write_summary(status=status, error=error)
    os.close(lock_descriptor)
    LOCK_PATH.unlink(missing_ok=True)
    print(json.dumps({"status": status, "scenario": args.scenario, "artifact_dir": str(artifact_dir), "error": error}, sort_keys=True))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MatrixError, OSError, subprocess.SubprocessError) as exc:
        LOCK_PATH.unlink(missing_ok=True)
        raise SystemExit(f"writer witness real-host Matrix failed closed: {exc}") from exc
