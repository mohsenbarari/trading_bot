#!/usr/bin/env python3
"""Execute closed, host-local staging failover operations for the typed backend."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dr_command_orchestration_adapter import load_typed_operation_manifest
from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import (
    DrOrchestrationError,
    _validate_source_tail_boundary,
    load_approver_policy,
    parse_plan,
    validate_plan_freshness,
    verify_two_person_approvals,
)
from core.secure_file_io import read_secure_text, write_secure_atomic_bytes
from scripts.render_three_site_staging_role_compose import parse_env_values
from scripts.run_three_site_staging_source_backup import DOCKER, SAFE_ENV
from scripts.verify_three_site_staging_role_bundle import _verify_bundle_source


ROLE_DB = {
    "webapp_fi": ("webapp_fi_db", "WEBAPP_FI_POSTGRES_USER", "WEBAPP_FI_POSTGRES_DB", "webapp_fi_app"),
    "webapp_ir": ("webapp_ir_db", "WEBAPP_IR_POSTGRES_USER", "WEBAPP_IR_POSTGRES_DB", "webapp_ir_app"),
}
ROLE_PUBLIC = {
    "webapp_fi": ("webapp_fi_api", "webapp_fi_effects"),
    "webapp_ir": ("webapp_ir_api", "webapp_ir_effects"),
}
ROLE_LOOPBACK_PORT = {"webapp_fi": 8212, "webapp_ir": 8213}


class StagingSiteOperationError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise StagingSiteOperationError("local readiness redirect is forbidden")


def _strict_json(path: Path, *, label: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in values:
            if key in result:
                raise StagingSiteOperationError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            read_secure_text(path, label=label, max_size=1024 * 1024),
            object_pairs_hook=pairs,
        )
    except Exception as exc:
        raise StagingSiteOperationError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise StagingSiteOperationError(f"{label} must be an object")
    return value


def _run(arguments: list[str], *, timeout: int = 120) -> str:
    result = subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=timeout,
        env=SAFE_ENV,
    )
    if result.returncode != 0:
        raise StagingSiteOperationError(
            f"closed staging site operation failed: {Path(arguments[0]).name}"
        )
    return result.stdout.strip()


def _compose(args: argparse.Namespace) -> list[str]:
    return [DOCKER, "compose", "-f", str(args.role_compose), "--env-file", str(args.env_file)]


def _psql(args: argparse.Namespace, env: dict[str, str], sql: str) -> str:
    db_service, user_key, database_key, _app_user = ROLE_DB[args.role]
    return _run(
        [
            *_compose(args), "exec", "-T", db_service, "psql",
            "-v", "ON_ERROR_STOP=1", "-U", env[user_key],
            "-d", env[database_key], "-Atqc", sql,
        ],
        timeout=60,
    )


def _common(args: argparse.Namespace):  # noqa: ANN202
    plan = parse_plan(_strict_json(args.plan, label="failover plan"))
    policy = load_approver_policy(args.approver_policy)
    verify_two_person_approvals(plan, policy)
    if args.action != "safe-fence":
        validate_plan_freshness(plan)
    load_typed_operation_manifest(args.command_manifest, plan=plan)
    compose_bytes = _verify_bundle_source(args.role_compose, expected_mode=0o640)
    env_bytes = _verify_bundle_source(args.env_file, expected_mode=0o600)
    env = parse_env_values(env_bytes.decode("utf-8"))
    if env.get("STAGING_RELEASE_SHA") != plan.release_sha or env.get("PHYSICAL_SITE") not in {None, args.role}:
        raise StagingSiteOperationError("role bundle release/site differs from the approved plan")
    if args.action in {"source-fenced", "source-connections-drained"}:
        if args.role != plan.source_site:
            raise StagingSiteOperationError("source operation is running on the wrong role")
    elif args.action != "safe-fence" and args.role != plan.target_site:
        raise StagingSiteOperationError("target operation is running on the wrong role")
    return plan, env, hashlib.sha256(compose_bytes).hexdigest(), hashlib.sha256(env_bytes).hexdigest()


def _writer_state(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    raw = _psql(
        args,
        env,
        "SELECT json_build_object("
        "'active_site', active_site, 'writer_epoch', writer_epoch, "
        "'control_state', control_state, 'witness_lease_id', witness_lease_id, "
        "'witness_proof_hash', witness_proof_hash, "
        "'lease_seconds_remaining', CASE WHEN witness_lease_expires_at IS NULL THEN NULL "
        "ELSE floor(extract(epoch FROM (witness_lease_expires_at-clock_timestamp())))::bigint END"
        ")::text FROM webapp_writer_state WHERE authority='webapp'",
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StagingSiteOperationError("Writer state is unreadable") from exc
    if not isinstance(value, dict):
        raise StagingSiteOperationError("Writer state is missing")
    return value


def _with_evidence_hash(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "evidence_hash": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }


def source_fenced(args: argparse.Namespace, plan, env: dict[str, str]) -> dict[str, Any]:  # noqa: ANN001
    state = _writer_state(args, env)
    if (
        state.get("active_site") != plan.source_site
        or state.get("writer_epoch") != plan.expected_epoch
        or state.get("control_state") != "active"
        or not state.get("witness_lease_id")
        or type(state.get("lease_seconds_remaining")) is not int
        or state["lease_seconds_remaining"] < 30
    ):
        raise StagingSiteOperationError("source has no live exact Writer term before fencing")
    _run([*_compose(args), "stop", "--timeout", "30", *ROLE_PUBLIC[args.role]], timeout=180)
    if any(_run([*_compose(args), "ps", "--status", "running", "-q", service]) for service in ROLE_PUBLIC[args.role]):
        raise StagingSiteOperationError("source public mutation service remained running")
    destination = plan.target_site
    raw_tail = _psql(
        args,
        env,
        "SELECT (destination_streams -> '"
        + destination
        + "' ->> 'sequence') || '|' || "
        "(destination_streams -> '"
        + destination
        + "' ->> 'transaction_hash') "
        "FROM dr_events WHERE origin_physical_site = '"
        + plan.source_site
        + "' AND producer_epoch = "
        + str(plan.expected_epoch)
        + " AND destination_streams ? '"
        + destination
        + "' ORDER BY (destination_streams -> '"
        + destination
        + "' ->> 'sequence')::bigint DESC LIMIT 1",
    )
    if raw_tail:
        sequence_raw, separator, transaction_hash = raw_tail.partition("|")
        if (
            not separator
            or not sequence_raw.isdigit()
            or re.fullmatch(r"[0-9a-f]{64}", transaction_hash) is None
            or transaction_hash == "0" * 64
        ):
            raise StagingSiteOperationError("source event tail is malformed")
        final_sequence = int(sequence_raw)
    else:
        final_sequence = 0
        transaction_hash = "0" * 64
    unsigned_boundary = {
        "mode": "proven",
        "origin_site": plan.source_site,
        "target_site": plan.target_site,
        "producer_epoch": plan.expected_epoch,
        "final_sequence": final_sequence,
        "final_transaction_hash": transaction_hash,
        "estimated_unreplicated_events": 0,
    }
    boundary = {
        **unsigned_boundary,
        "boundary_hash": hashlib.sha256(canonical_json_bytes(unsigned_boundary)).hexdigest(),
    }
    _validate_source_tail_boundary(boundary, plan=plan)
    return _with_evidence_hash(
        {
            "status": "ok", "operation_id": plan.operation_id,
            "source_site": plan.source_site, "fenced": True,
            "fence_mode": "all-public-mutators-stopped-live-witness-retained",
            "source_tail_boundary": boundary,
        }
    )


def source_connections_drained(args: argparse.Namespace, plan, env: dict[str, str]) -> dict[str, Any]:  # noqa: ANN001
    for service in ROLE_PUBLIC[args.role]:
        if _run([*_compose(args), "ps", "--status", "running", "-q", service]):
            raise StagingSiteOperationError("source mutation service resumed before drain proof")
    app_user = ROLE_DB[args.role][3]
    raw = _psql(
        args,
        env,
        "SELECT count(*) FROM pg_stat_activity WHERE usename = '" + app_user + "'",
    )
    if not raw.isdigit() or int(raw) != 0:
        raise StagingSiteOperationError("source application database connections are not drained")
    return _with_evidence_hash(
        {
            "status": "ok", "operation_id": plan.operation_id,
            "source_site": plan.source_site, "active_connections": 0,
        }
    )


def _readiness_url(plan, boundary: dict[str, Any], recovery: dict[str, Any] | None) -> str:  # noqa: ANN001
    query: dict[str, str] = {
        "action": plan.action,
        "expected_writer_epoch": str(plan.target_epoch),
        "source_tail_mode": str(boundary["mode"]),
        "source_tail_origin_site": str(boundary["origin_site"]),
        "source_tail_producer_epoch": str(boundary["producer_epoch"]),
        "source_tail_final_sequence": str(boundary["final_sequence"]),
        "source_tail_final_transaction_hash": str(boundary["final_transaction_hash"]),
        "source_tail_estimated_unreplicated_events": str(boundary["estimated_unreplicated_events"]),
        "source_tail_boundary_hash": str(boundary["boundary_hash"]),
    }
    if plan.action == "failback_fi":
        if recovery is None or set(recovery) != {
            "expected_database_fingerprint_hash", "expected_database_row_count",
            "expected_blob_set_hash", "expected_blob_count",
        }:
            raise StagingSiteOperationError("failback requires exact recovery-manifest inputs")
        query.update({key: str(value) for key, value in recovery.items()})
    return (
        f"http://127.0.0.1:{ROLE_LOOPBACK_PORT[plan.target_site]}/health/promotion-ready?"
        + urllib.parse.urlencode(query)
    )


def target_ready(args: argparse.Namespace, plan, env: dict[str, str]) -> dict[str, Any]:  # noqa: ANN001
    if args.source_tail is None and args.source_tail_json is None:
        raise StagingSiteOperationError("target readiness requires source-tail evidence")
    if args.source_tail is not None and args.source_tail_json is not None:
        raise StagingSiteOperationError("target readiness accepts only one source-tail input")
    if args.source_tail is not None:
        source = _strict_json(args.source_tail, label="source-tail evidence")
    else:
        import base64

        try:
            raw = base64.urlsafe_b64decode(str(args.source_tail_json) + "===")
            source = json.loads(raw)
        except Exception as exc:
            raise StagingSiteOperationError("encoded source-tail evidence is invalid") from exc
        if not isinstance(source, dict) or len(raw) > 128 * 1024:
            raise StagingSiteOperationError("encoded source-tail evidence is invalid")
    boundary = _validate_source_tail_boundary(source.get("source_tail_boundary"), plan=plan)
    recovery = (
        _strict_json(args.recovery_input, label="failback recovery input")
        if args.recovery_input is not None
        else None
    )
    key = env.get("ORIGIN_READINESS_API_KEY", "")
    if not key:
        raise StagingSiteOperationError("target role lacks origin readiness key")
    request = urllib.request.Request(
        _readiness_url(plan, boundary, recovery),
        headers={"X-Origin-Readiness-Key": key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(1024 * 1024)
            status_code = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read(1024 * 1024)
        status_code = exc.code
    except urllib.error.URLError as exc:
        raise StagingSiteOperationError("target readiness endpoint is unreachable") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StagingSiteOperationError("target readiness endpoint returned invalid JSON") from exc
    if (
        status_code != 200
        or not isinstance(result, dict)
        or result.get("promotion_ready") is not True
        or result.get("physical_site") != plan.target_site
        or result.get("expected_writer_epoch") != plan.target_epoch
        or result.get("readiness_hash") != plan.readiness_hash
        or not isinstance(result.get("readiness_evidence"), dict)
        or result.get("source_tail_boundary") != boundary
    ):
        raise StagingSiteOperationError("target did not prove approved promotion readiness")
    return _with_evidence_hash(
        {
            "schema": "three-site-staging-target-readiness-v1",
            "status": "ok", "operation_id": plan.operation_id,
            "release_sha": plan.release_sha, "target_site": plan.target_site,
            "target_epoch": plan.target_epoch,
            "readiness_hash": plan.readiness_hash,
            "source_tail_boundary_hash": boundary["boundary_hash"],
            "target_applied_sequence": result["target_applied_sequence"],
            "target_applied_through_boundary": True,
            "readiness_evidence": result["readiness_evidence"],
        }
    )


def target_term_attested(
    args: argparse.Namespace,
    plan,
    env: dict[str, str],
) -> dict[str, Any]:  # noqa: ANN001
    previous = str(args.previous_proof_hash or "")
    if re.fullmatch(r"[0-9a-f]{64}", previous) is None:
        raise StagingSiteOperationError("target attestation requires the acquired proof hash")
    writer_control = f"{args.role}_writer_control"
    for _attempt in range(45):
        container = _run([*_compose(args), "ps", "-q", writer_control])
        running = bool(container) and _run(
            [DOCKER, "inspect", "--format", "{{.State.Running}}", container]
        ) == "true"
        state = _writer_state(args, env)
        remaining = state.get("lease_seconds_remaining")
        proof_hash = state.get("witness_proof_hash")
        if (
            running
            and state.get("active_site") == plan.target_site
            and state.get("writer_epoch") == plan.target_epoch
            and state.get("control_state") == "active"
            and isinstance(state.get("witness_lease_id"), str)
            and bool(state["witness_lease_id"])
            and isinstance(proof_hash, str)
            and re.fullmatch(r"[0-9a-f]{64}", proof_hash) is not None
            and proof_hash != previous
            and type(remaining) is int
            and remaining >= 30
        ):
            return _with_evidence_hash(
                {
                    "status": "ok",
                    "operation_id": plan.operation_id,
                    "holder_site": plan.target_site,
                    "writer_epoch": plan.target_epoch,
                    "lease_id": state["witness_lease_id"],
                    "proof_hash": proof_hash,
                    "lease_seconds_remaining": remaining,
                    "control_agent_running": True,
                }
            )
        time.sleep(1)
    raise StagingSiteOperationError(
        "target Writer control agent did not prove a post-acquisition renewal"
    )


def safe_fence(args: argparse.Namespace, plan, env: dict[str, str]) -> dict[str, Any]:  # noqa: ANN001
    writer_control = f"{args.role}_writer_control"
    _run(
        [
            *_compose(args), "stop", "--timeout", "30",
            *ROLE_PUBLIC[args.role], writer_control,
        ],
        timeout=180,
    )
    state = _writer_state(args, env)
    if state.get("control_state") == "active" and state.get("active_site") == args.role:
        epoch = state.get("writer_epoch")
        if type(epoch) is not int or epoch < 1:
            raise StagingSiteOperationError("active rollback target has an invalid epoch")
        _run(
            [
                *_compose(args), "run", "--rm", "--no-deps", "-T",
                writer_control, "python", "scripts/manage_webapp_writer.py", "fence",
                "--expected-epoch", str(epoch), "--expected-active-site", args.role,
                "--operator", f"failover-rollback:{plan.operation_id}",
                "--reason", "fail-closed staging orchestration rollback",
                "--apply", "--confirm", f"writer:fence:{args.role}:{epoch}:{epoch}",
            ],
            timeout=300,
        )
        state = _writer_state(args, env)
    if state.get("control_state") != "fenced" or state.get("active_site") is not None:
        raise StagingSiteOperationError("rollback role is not locally fenced")
    for service in (*ROLE_PUBLIC[args.role], writer_control):
        if _run([*_compose(args), "ps", "--status", "running", "-q", service]):
            raise StagingSiteOperationError("rollback role service remained running")
    app_user = ROLE_DB[args.role][3]
    connections = _psql(
        args,
        env,
        "SELECT count(*) FROM pg_stat_activity WHERE usename = '" + app_user + "'",
    )
    if not connections.isdigit() or int(connections) != 0:
        raise StagingSiteOperationError("rollback target application connections remain")
    return _with_evidence_hash(
        {
            "status": "ok", "operation_id": plan.operation_id,
            "role": args.role, "fenced": True, "active_connections": 0,
            "writer_epoch": state.get("writer_epoch"),
        }
    )


def confirmation_phrase(plan, role: str, action: str) -> str:  # noqa: ANN001
    return f"staging-site-op:{plan.operation_id}:{role}:{action}:{plan.plan_hash}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=(
            "source-fenced", "source-connections-drained", "target-ready",
            "target-term-attested", "safe-fence"
        )
    )
    parser.add_argument("--role", choices=tuple(ROLE_DB), required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--command-manifest", type=Path, required=True)
    parser.add_argument("--approver-policy", type=Path, required=True)
    parser.add_argument("--role-compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--source-tail", type=Path)
    parser.add_argument("--source-tail-json")
    parser.add_argument("--recovery-input", type=Path)
    parser.add_argument("--previous-proof-hash")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        plan, env, compose_hash, env_hash = _common(args)
        required = confirmation_phrase(plan, args.role, args.action)
        if not args.apply:
            result = {
                "status": "planned", "operation_id": plan.operation_id,
                "role": args.role, "action": args.action,
                "role_compose_sha256": compose_hash, "role_env_sha256": env_hash,
                "required_confirmation": required,
            }
        else:
            if args.confirm != required:
                raise StagingSiteOperationError("site operation confirmation mismatch")
            operation = {
                "source-fenced": source_fenced,
                "source-connections-drained": source_connections_drained,
                "target-ready": target_ready,
                "target-term-attested": target_term_attested,
                "safe-fence": safe_fence,
            }[args.action]
            result = operation(args, plan, env)
            write_secure_atomic_bytes(
                args.output,
                (json.dumps(result, sort_keys=True, indent=2) + "\n").encode(),
                label="staging failover site evidence",
                mode=0o600,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
