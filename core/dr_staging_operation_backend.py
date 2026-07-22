"""Concrete, closed SSH/Arvan backend for the isolated three-site staging domain."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from uuid import NAMESPACE_URL, uuid4, uuid5

from core.dr_connectivity_classifier import classify_connectivity, load_connectivity_policy
from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import FailoverPlan, DrOrchestrationError, validate_plan_freshness
from core.secure_file_io import read_secure_text
from core.writer_witness_auth import WitnessClientCredential
from core.writer_witness_client import WriterWitnessClientConfig
from scripts.arvan_origin_switch import (
    append_audit_event,
    confirmation_phrase as arvan_confirmation_phrase,
    inspect_or_switch,
    load_token,
)
from scripts.update_dr_connectivity_state import load_rounds
from scripts.verify_three_site_staging_inventory import verify_signed_inventory


PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
ROLE_SERVICE = {
    "webapp_fi": "webapp_fi_writer_control",
    "webapp_ir": "webapp_ir_writer_control",
}


class StagingOperationBackendError(DrOrchestrationError):
    pass


@dataclass(frozen=True)
class StagingHost:
    role: str
    host_ip: str
    ssh_port: int
    ssh_user: str
    ssh_identity_file: Path
    ssh_known_hosts_file: Path
    repo_root: str
    role_compose: str
    env_file: str
    plan_path: str
    command_manifest_path: str
    approver_policy_path: str
    evidence_dir: str
    recovery_input_path: str | None


@dataclass(frozen=True)
class StagingBackendConfig:
    campaign_id: str
    release_sha: str
    connectivity_policy: Path
    connectivity_evidence: Path
    arvan_token_file: Path
    arvan_audit_log: Path
    origin_readiness_key_file: Path
    rollback_wait_seconds: int
    hosts: dict[str, StagingHost]
    witness_config: WriterWitnessClientConfig
    witness_public_key: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StagingOperationBackendError("staging backend config contains a duplicate key")
        result[key] = value
    return result


def _secure_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            read_secure_text(path, label="staging failover backend config", max_size=512 * 1024),
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise StagingOperationBackendError("staging failover backend config is invalid") from exc
    if not isinstance(value, dict):
        raise StagingOperationBackendError("staging failover backend config must be an object")
    return value


def _absolute(value: Any, *, label: str) -> str:
    text = str(value or "")
    if not PATH_RE.fullmatch(text) or ".." in Path(text).parts:
        raise StagingOperationBackendError(f"{label} must be one closed absolute path")
    return text


def load_staging_backend_config(
    path: Path,
    *,
    inventory: dict[str, Any],
    inventory_approval: dict[str, Any],
    inventory_signer_policy: dict[str, Any],
) -> StagingBackendConfig:
    payload = _secure_json(path)
    fields = {
        "schema", "campaign_id", "release_sha", "domain", "record",
        "connectivity_policy", "connectivity_evidence", "arvan_token_file",
        "arvan_audit_log", "origin_readiness_key_file", "rollback_wait_seconds",
        "hosts", "witness",
    }
    if (
        set(payload) != fields
        or payload.get("schema") != "three-site-staging-failover-backend-v1"
        or payload.get("domain") != "gold-trading.ir"
        or payload.get("record") != "app"
    ):
        raise StagingOperationBackendError("staging backend scope/schema is invalid")
    verified = verify_signed_inventory(
        inventory,
        approval=inventory_approval,
        signer_policy=inventory_signer_policy,
        host_destructive=True,
    )
    if (
        verified["inventory_stage"] != "provisioned"
        or payload["campaign_id"] != verified["campaign_id"]
        or payload["release_sha"] != verified["release_sha"]
    ):
        raise StagingOperationBackendError("staging backend differs from signed inventory")
    inventory_roles = {row["role"]: row for row in inventory["roles"]}
    raw_hosts = payload["hosts"]
    if not isinstance(raw_hosts, dict) or set(raw_hosts) != set(ROLE_SERVICE):
        raise StagingOperationBackendError("staging backend requires exactly two WebApp hosts")
    hosts: dict[str, StagingHost] = {}
    host_fields = {
        "host_ip", "ssh_port", "ssh_user", "ssh_identity_file",
        "ssh_known_hosts_file", "repo_root", "role_compose", "env_file",
        "plan_path", "command_manifest_path", "approver_policy_path",
        "evidence_dir", "recovery_input_path",
    }
    for role, raw in raw_hosts.items():
        try:
            normalized_ip = str(ipaddress.ip_address(str(raw["host_ip"])))
        except (TypeError, ValueError, KeyError) as exc:
            raise StagingOperationBackendError("staging backend host IP is invalid") from exc
        if (
            not isinstance(raw, dict)
            or set(raw) != host_fields
            or normalized_ip != inventory_roles[role]["host_ip"]
            or type(raw["ssh_port"]) is not int
            or not 1 <= raw["ssh_port"] <= 65535
            or raw["ssh_user"] != "root"
        ):
            raise StagingOperationBackendError("staging backend host identity is invalid")
        identity_file = Path(_absolute(raw["ssh_identity_file"], label="SSH identity"))
        known_hosts = Path(_absolute(raw["ssh_known_hosts_file"], label="known-hosts"))
        # Read through the secure primitive now so symlinks, broad modes, hard
        # links, or a later missing key fail before any orchestration mutation.
        read_secure_text(identity_file, label=f"{role} SSH identity", max_size=128 * 1024)
        read_secure_text(known_hosts, label=f"{role} SSH known-hosts", max_size=1024 * 1024)
        hosts[role] = StagingHost(
            role=role,
            host_ip=normalized_ip,
            ssh_port=raw["ssh_port"],
            ssh_user="root",
            ssh_identity_file=identity_file,
            ssh_known_hosts_file=known_hosts,
            repo_root=_absolute(raw["repo_root"], label="repo root"),
            role_compose=_absolute(raw["role_compose"], label="role Compose"),
            env_file=_absolute(raw["env_file"], label="role env"),
            plan_path=_absolute(raw["plan_path"], label="remote plan"),
            command_manifest_path=_absolute(
                raw["command_manifest_path"], label="remote command manifest"
            ),
            approver_policy_path=_absolute(
                raw["approver_policy_path"], label="remote approver policy"
            ),
            evidence_dir=_absolute(raw["evidence_dir"], label="remote evidence directory"),
            recovery_input_path=(
                _absolute(raw["recovery_input_path"], label="recovery input")
                if raw["recovery_input_path"] is not None
                else None
            ),
        )
    wait_seconds = payload["rollback_wait_seconds"]
    if type(wait_seconds) is not int or not 30 <= wait_seconds <= 600:
        raise StagingOperationBackendError("rollback wait must be between 30 and 600 seconds")
    witness = payload["witness"]
    witness_fields = {
        "base_url", "key_id", "site", "secret_file", "ca_bundle",
        "public_key", "timeout_seconds",
    }
    if (
        not isinstance(witness, dict)
        or set(witness) != witness_fields
        or witness["site"] != "webapp_fi"
        or witness["key_id"] != "staging-webapp-fi-v1"
    ):
        raise StagingOperationBackendError("staging Witness controller config is invalid")
    parsed = urlparse.urlsplit(str(witness["base_url"]))
    if (
        parsed.scheme != "https"
        or parsed.hostname != "witness-dr.staging.internal"
        or parsed.port != 8444
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise StagingOperationBackendError("staging Witness URL is invalid")
    secret = read_secure_text(
        Path(_absolute(witness["secret_file"], label="Witness secret")),
        label="staging Witness controller secret",
        max_size=16 * 1024,
    ).strip()
    if len(secret.encode()) < 32:
        raise StagingOperationBackendError("staging Witness controller secret is too short")
    ca_bundle = _absolute(witness["ca_bundle"], label="Witness CA bundle")
    read_secure_text(
        Path(ca_bundle), label="staging Witness CA bundle", max_size=1024 * 1024
    )
    timeout = witness["timeout_seconds"]
    if not isinstance(timeout, (int, float)) or not 0.1 <= float(timeout) <= 10:
        raise StagingOperationBackendError("staging Witness timeout is invalid")
    public_key = str(witness["public_key"])
    try:
        if len(base64.b64decode(public_key, validate=True)) != 32:
            raise ValueError
    except (ValueError, binascii.Error) as exc:
        raise StagingOperationBackendError("staging Witness public key is invalid") from exc
    return StagingBackendConfig(
        campaign_id=payload["campaign_id"],
        release_sha=payload["release_sha"],
        connectivity_policy=Path(_absolute(payload["connectivity_policy"], label="connectivity policy")),
        connectivity_evidence=Path(_absolute(payload["connectivity_evidence"], label="connectivity evidence")),
        arvan_token_file=Path(_absolute(payload["arvan_token_file"], label="Arvan token")),
        arvan_audit_log=Path(_absolute(payload["arvan_audit_log"], label="Arvan audit log")),
        origin_readiness_key_file=Path(
            _absolute(payload["origin_readiness_key_file"], label="origin readiness key")
        ),
        rollback_wait_seconds=wait_seconds,
        hosts=hosts,
        witness_config=WriterWitnessClientConfig(
            base_url=str(witness["base_url"]),
            credential=WitnessClientCredential(
                key_id=str(witness["key_id"]),
                site="webapp_fi",
                secret=secret,
            ),
            timeout_seconds=float(timeout),
            verify=ca_bundle,
        ),
        witness_public_key=public_key,
    )


def _result_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class StagingTypedOperationBackend:
    def __init__(self, config: StagingBackendConfig) -> None:
        self.config = config

    def _ssh_raw(
        self,
        host: StagingHost,
        command: list[str],
        *,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess[str]:
        remote = shlex.join(command)
        result = subprocess.run(
            [
                "/usr/bin/ssh", "-i", str(host.ssh_identity_file),
                "-p", str(host.ssh_port), "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={host.ssh_known_hosts_file}",
                "-o", "ConnectTimeout=10", f"{host.ssh_user}@{host.host_ip}",
                remote,
            ],
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        if result.returncode != 0:
            raise StagingOperationBackendError("closed staging SSH operation failed")
        return result

    def validate_plan_scope(self, plan: FailoverPlan) -> None:
        if (
            plan.release_sha != self.config.release_sha
            or plan.domain != "gold-trading.ir"
            or plan.record != "app"
            or plan.expected_current_ip != self.config.hosts[plan.source_site].host_ip
            or plan.target_ip != self.config.hosts[plan.target_site].host_ip
        ):
            raise StagingOperationBackendError(
                "failover plan differs from the signed staging topology"
            )

    def preflight(self, plan: FailoverPlan) -> None:
        self.validate_plan_scope(plan)
        policy = load_connectivity_policy(self.config.connectivity_policy)
        classification = classify_connectivity(
            load_rounds(self.config.connectivity_evidence), policy=policy
        )
        if any(
            getattr(classification, key) != plan.classification[key]
            for key in (
                "mode", "confidence", "consecutive_rounds", "evidence_hash",
                "campaign_id", "policy_hash",
            )
        ):
            raise StagingOperationBackendError(
                "fresh connectivity evidence differs from the signed failover plan"
            )
        load_token(self.config.arvan_token_file)
        readiness_key = read_secure_text(
            self.config.origin_readiness_key_file,
            label="origin readiness key",
            max_size=16 * 1024,
        ).strip()
        if len(readiness_key.encode()) < 32:
            raise StagingOperationBackendError("origin readiness key is too short")

    def _ssh(self, host: StagingHost, command: list[str], *, timeout: int = 300) -> dict[str, Any]:
        result = self._ssh_raw(host, command, timeout=timeout)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise StagingOperationBackendError("staging site agent returned ambiguous output")
        try:
            payload = json.loads(lines[0], object_pairs_hook=_strict_object)
        except Exception as exc:
            raise StagingOperationBackendError("staging site agent returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") in {"blocked", "error"}:
            raise StagingOperationBackendError("staging site agent rejected the operation")
        return payload

    @staticmethod
    def _site_evidence_path(host: StagingHost, plan: FailoverPlan, action: str) -> str:
        # The path is a pure function of signed operation identity.  A restarted
        # controller can therefore resume after target-ready without relying on
        # volatile process memory.
        return f"{host.evidence_dir}/{plan.operation_id}-{action}.json"

    def _site_agent(
        self,
        plan: FailoverPlan,
        *,
        role: str,
        action: str,
        source_tail: dict[str, Any] | None = None,
        previous_proof_hash: str | None = None,
    ) -> dict[str, Any]:
        host = self.config.hosts[role]
        output = self._site_evidence_path(host, plan, action)
        command = [
            "/usr/bin/python3", f"{host.repo_root}/scripts/run_three_site_staging_failover_site_agent.py",
            action, "--role", role, "--plan", host.plan_path,
            "--command-manifest", host.command_manifest_path,
            "--approver-policy", host.approver_policy_path,
            "--role-compose", host.role_compose, "--env-file", host.env_file,
            "--output", output,
        ]
        if source_tail is not None:
            encoded = base64.urlsafe_b64encode(canonical_json_bytes(source_tail)).decode().rstrip("=")
            command.extend(["--source-tail-json", encoded])
        if plan.action == "failback_fi" and action == "target-ready":
            if host.recovery_input_path is None:
                raise StagingOperationBackendError("failback target lacks recovery input")
            command.extend(["--recovery-input", host.recovery_input_path])
        if previous_proof_hash is not None:
            command.extend(["--previous-proof-hash", previous_proof_hash])
        command.extend(
            [
                "--apply", "--confirm",
                f"staging-site-op:{plan.operation_id}:{role}:{action}:{plan.plan_hash}",
            ]
        )
        return self._ssh(host, command)

    def _compose_run(self, host: StagingHost, service: str, command: list[str], *, volume: tuple[str, str] | None = None) -> dict[str, Any]:
        args = [
            "/usr/bin/docker", "compose", "-f", host.role_compose,
            "--env-file", host.env_file, "run", "--rm", "--no-deps", "-T",
        ]
        if volume is not None:
            args.extend(["-v", f"{volume[0]}:{volume[1]}:ro"])
        args.extend([service, *command])
        return self._ssh(host, args, timeout=420)

    def _compose_service(self, host: StagingHost, verb: str, services: list[str]) -> None:
        command = [
            "/usr/bin/docker", "compose", "-f", host.role_compose,
            "--env-file", host.env_file, verb,
        ]
        if verb == "up":
            command.extend(["-d", "--no-deps"])
        elif verb == "stop":
            command.extend(["--timeout", "30"])
        command.extend(services)
        self._ssh_raw(host, command, timeout=300)

    async def classification_verified(self, plan: FailoverPlan) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        policy = load_connectivity_policy(self.config.connectivity_policy)
        classification = classify_connectivity(
            load_rounds(self.config.connectivity_evidence), policy=policy
        )
        return {
            "status": "ok", "operation_id": plan.operation_id,
            "mode": classification.mode, "confidence": classification.confidence,
            "consecutive_rounds": classification.consecutive_rounds,
            "evidence_hash": classification.evidence_hash,
            "campaign_id": classification.campaign_id,
            "policy_hash": classification.policy_hash,
        }

    async def source_fenced(self, plan: FailoverPlan) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        # Stop renewals and durably put the Witness lease into draining before
        # the site agent closes the local database Writer term.  Once the
        # local term is fenced, no late/reconnecting app session can extend the
        # source tail while the target catches up.
        host = self.config.hosts[plan.source_site]
        await asyncio.to_thread(
            self._compose_service,
            host,
            "stop",
            [ROLE_SERVICE[plan.source_site]],
        )
        drain = await asyncio.to_thread(self._drain_source_lease, plan)
        result = await asyncio.to_thread(
            self._site_agent, plan, role=plan.source_site, action="source-fenced"
        )
        payload = {
            **{key: value for key, value in result.items() if key != "evidence_hash"},
            "witness_drain_request_id": drain["request_id"],
            "witness_drain_receipt_hash": drain["witness_receipt_hash"],
        }
        return {**payload, "evidence_hash": _result_hash(payload)}

    async def source_connections_drained(self, plan: FailoverPlan) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        return await asyncio.to_thread(
            self._site_agent,
            plan,
            role=plan.source_site,
            action="source-connections-drained",
        )

    async def target_ready(
        self,
        plan: FailoverPlan,
        *,
        source_tail_boundary: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        result = await asyncio.to_thread(
            self._site_agent,
            plan,
            role=plan.target_site,
            action="target-ready",
            source_tail={"source_tail_boundary": source_tail_boundary},
        )
        return result

    def _derived_id(self, plan: FailoverPlan, label: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"{plan.operation_id}:{label}"))

    def _drain_source_lease(self, plan: FailoverPlan) -> dict[str, Any]:
        host = self.config.hosts[plan.source_site]
        service = ROLE_SERVICE[plan.source_site]
        self._compose_service(host, "stop", [service])
        request_id = self._derived_id(plan, "source-drain")
        return self._compose_run(
            host,
            service,
            [
                "python", "scripts/drain_three_site_staging_writer_lease.py",
                "--operation-id", plan.operation_id, "--request-id", request_id,
                "--expected-epoch", str(plan.expected_epoch),
                "--expected-release-sha", plan.release_sha, "--apply", "--confirm",
                f"drain-writer:{plan.operation_id}:{request_id}:{plan.expected_epoch}",
            ],
        )

    def _inspect_witness(self, plan: FailoverPlan, *, role: str) -> dict[str, Any]:
        host = self.config.hosts[role]
        request_id = str(uuid4())
        return self._compose_run(
            host,
            ROLE_SERVICE[role],
            [
                "python", "scripts/inspect_three_site_staging_writer_witness.py",
                "--request-id", request_id,
                "--expected-release-sha", plan.release_sha,
            ],
        )

    async def target_term_acquired(self, plan: FailoverPlan) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        deadline = asyncio.get_running_loop().time() + min(
            self.config.rollback_wait_seconds, 300
        )
        while True:
            validate_plan_freshness(plan)
            status = await asyncio.to_thread(
                self._inspect_witness, plan, role=plan.target_site
            )
            if status.get("lease_live") is False:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise StagingOperationBackendError("predecessor Witness lease did not expire")
            await asyncio.sleep(2)
        host = self.config.hosts[plan.target_site]
        readiness = self._site_evidence_path(host, plan, "target-ready")
        status_id = self._derived_id(plan, "target-status")
        acquire_id = self._derived_id(plan, "target-acquire")
        activated = await asyncio.to_thread(
            self._compose_run,
            host,
            ROLE_SERVICE[plan.target_site],
            [
                "python", "scripts/activate_three_site_staging_failover_target.py",
                "--operation-id", plan.operation_id,
                "--status-request-id", status_id,
                "--acquire-request-id", acquire_id,
                "--target-epoch", str(plan.target_epoch),
                "--expected-release-sha", plan.release_sha,
                "--readiness-evidence", "/run/failover/readiness.json",
                "--apply", "--confirm",
                f"activate-target:{plan.operation_id}:{acquire_id}:{plan.target_epoch}",
            ],
            volume=(readiness, "/run/failover/readiness.json"),
        )
        await asyncio.to_thread(
            self._compose_service,
            host,
            "up",
            [ROLE_SERVICE[plan.target_site]],
        )
        attested = await asyncio.to_thread(
            self._site_agent,
            plan,
            role=plan.target_site,
            action="target-term-attested",
            previous_proof_hash=activated["proof_hash"],
        )
        payload = {
            "status": "ok", "operation_id": plan.operation_id,
            "holder_site": attested["holder_site"],
            "writer_epoch": attested["writer_epoch"],
            "lease_id": attested["lease_id"],
            "proof_hash": attested["proof_hash"],
            "control_agent_running": True,
            "lease_seconds_remaining": attested["lease_seconds_remaining"],
            "acquisition_proof_hash": activated["proof_hash"],
            "renewal_evidence_hash": attested["evidence_hash"],
        }
        return {**payload, "evidence_hash": _result_hash(payload)}

    async def route_switched(self, plan: FailoverPlan) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        token = load_token(self.config.arvan_token_file)
        result = await asyncio.to_thread(
            inspect_or_switch,
            domain=plan.domain,
            record_name=plan.record,
            target_ip=plan.target_ip,
            token=token,
            expected_current_ip=plan.expected_current_ip,
            apply=True,
            confirmation=arvan_confirmation_phrase(plan.domain, plan.record, plan.target_ip),
        )
        if result.get("after", {}).get("origin_ip") != plan.target_ip:
            raise StagingOperationBackendError("Arvan readback did not retain the target origin")
        append_audit_event(
            self.config.arvan_audit_log,
            {
                "event": "arvan.origin_switch.applied.by_typed_staging_backend",
                "operation_id": plan.operation_id,
                "plan_hash": plan.plan_hash,
                "result": result,
            },
        )
        payload = {
            "status": "ok", "operation_id": plan.operation_id,
            "origin_ip": plan.target_ip, "domain": plan.domain, "record": plan.record,
            "provider_result_sha256": _result_hash(result),
        }
        return {**payload, "evidence_hash": _result_hash(payload)}

    async def public_route_verified(self, plan: FailoverPlan) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        key = read_secure_text(
            self.config.origin_readiness_key_file,
            label="origin readiness key",
            max_size=16 * 1024,
        ).strip()
        host = f"{plan.record}.{plan.domain}"
        url = f"https://{host}/health/origin-ready?require_global_convergence=true&operation_id={plan.operation_id}"
        request = urlrequest.Request(
            url,
            headers={"Accept": "application/json", "X-Origin-Readiness-Key": key},
            method="GET",
        )
        try:
            with urlrequest.urlopen(request, timeout=20) as response:
                raw = response.read(1024 * 1024)
                status_code = response.status
        except (urlerror.URLError, urlerror.HTTPError) as exc:
            raise StagingOperationBackendError("public target origin verification failed") from exc
        try:
            result = json.loads(raw, object_pairs_hook=_strict_object)
        except Exception as exc:
            raise StagingOperationBackendError("public target origin returned invalid JSON") from exc
        if (
            status_code != 200
            or result.get("origin_ready") is not True
            or result.get("physical_site") != plan.target_site
            or result.get("runtime_role") != "active"
            or result.get("writer_epoch") != plan.target_epoch
            or result.get("release_sha") != plan.release_sha
        ):
            raise StagingOperationBackendError("public route did not reach the approved active target")
        payload = {
            "status": "ok", "operation_id": plan.operation_id,
            "origin_ip": plan.target_ip, "domain": plan.domain, "record": plan.record,
            "response_sha256": _result_hash(result),
        }
        return {**payload, "evidence_hash": _result_hash(payload)}

    async def rollback(
        self,
        plan: FailoverPlan,
        *,
        failed_step: str,
        completed_steps: tuple[str, ...],
    ) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        # Availability is secondary here: both WebApp sites and all public
        # mutators are stopped/fenced, and any live lease is allowed to expire.
        # A separate signed recovery plan may later acquire a fresh term.
        safe_results = {}
        for role in (plan.source_site, plan.target_site):
            safe_results[role] = await asyncio.to_thread(
                self._site_agent, plan, role=role, action="safe-fence"
            )
        deadline = asyncio.get_running_loop().time() + self.config.rollback_wait_seconds
        while True:
            status = await asyncio.to_thread(
                self._inspect_witness, plan, role=plan.target_site
            )
            if status.get("lease_live") is False:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise StagingOperationBackendError("rollback could not prove Witness lease expiry")
            await asyncio.sleep(2)
        token = load_token(self.config.arvan_token_file)
        route = await asyncio.to_thread(
            inspect_or_switch,
            domain=plan.domain,
            record_name=plan.record,
            target_ip=plan.target_ip,
            token=token,
            expected_current_ip=None,
            apply=False,
            confirmation=None,
        )
        origin_ip = route["before"]["origin_ip"]
        payload = {
            "status": "ok", "operation_id": plan.operation_id,
            "source_site": plan.source_site, "target_site": plan.target_site,
            "target_fenced": True,
            "target_active_connections": safe_results[plan.target_site]["active_connections"],
            "source_fenced": True,
            "source_active_connections": safe_results[plan.source_site]["active_connections"],
            "holder_site": status.get("holder_site"),
            "witness_lease_live": False,
            "witness_receipt_hash": status["witness_receipt_hash"],
            "witness_request_id": status["request_id"],
            "rollback_state": "safe_fenced",
            "origin_ip": origin_ip, "domain": plan.domain, "record": plan.record,
            "failed_step": failed_step, "completed_steps": list(completed_steps),
        }
        return {**payload, "evidence_hash": _result_hash(payload)}
