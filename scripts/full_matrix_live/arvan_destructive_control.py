"""Closed Arvan power primitive for disposable Full Matrix hosts.

This module has no generic URL, host, shell, volume, delete, or rebuild
surface.  It is intentionally limited to the four campaign-owned servers and
the two reviewed, reversible ECC endpoints.  Scenario handlers must still
prove their Writer-fencing preconditions before calling it and must retain the
returned audit-safe evidence for their independent oracle.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import stat
import time
from typing import Any, Callable

from core.canonical_json import canonical_json_bytes
from core.secure_file_io import SecureFileError, append_hash_chained_jsonl
from scripts.provision_arvan_full_matrix_destructive_hosts import (
    ROLE_ORDER,
    ROLE_SPECS,
    STATE_FILE,
    TOKEN_FILE,
    _safe_existing_state,
    _safe_public_ip,
    _verify_server,
)
from scripts.provision_arvan_witness_recovery_vps import (
    ProvisionError,
    api_request,
    read_private_text,
    response_data,
)


SCHEMA = "three-site-full-matrix-arvan-destructive-power-v1"
AUDIT_SCHEMA = "three-site-full-matrix-arvan-destructive-power-audit-v1"
CONTROL_SCHEMA = "three-site-full-matrix-destructive-control-v1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
SCENARIO = re.compile(r"[a-z][a-z0-9_]{2,95}\Z")
SERVER_ID = re.compile(r"[0-9a-f-]{36}\Z")

POWER_ACTIONS: dict[str, dict[str, Any]] = {
    "power-off": {
        "endpoint": "power-off",
        "before": frozenset({"ACTIVE"}),
        "after": frozenset({"SHUTOFF", "STOPPED", "POWERED_OFF", "OFF"}),
        "recovery": "power-on",
    },
    "power-on": {
        "endpoint": "power-on",
        "before": frozenset({"SHUTOFF", "STOPPED", "POWERED_OFF", "OFF"}),
        "after": frozenset({"ACTIVE"}),
        "recovery": None,
    },
}


class ArvanDestructiveControlError(RuntimeError):
    """A disposable-host power action cannot be proven safe."""


RequestFn = Callable[[str, str, str, dict[str, Any] | None], dict[str, Any]]


def _bound_paths(
    control: Any,
    *,
    campaign_id: str,
    gate_group_id: str,
    release_sha: str,
) -> dict[str, Path]:
    """Validate the live-plan binding before provider credentials are read."""

    fields = {
        "schema", "campaign_id", "gate_group_id", "execution_class",
        "release_sha", "enabled", "provider_state_file", "provider_token_file",
        "audit_root",
    }
    if (
        not isinstance(control, dict)
        or set(control) != fields
        or control.get("schema") != CONTROL_SCHEMA
        or control.get("campaign_id") != campaign_id
        or control.get("gate_group_id") != gate_group_id
        or control.get("release_sha") != release_sha
        or control.get("execution_class") != "dedicated-host-destructive"
        or control.get("enabled") is not True
    ):
        raise ArvanDestructiveControlError("destructive provider binding is invalid")
    result = {
        name: Path(str(control[name]))
        for name in ("provider_state_file", "provider_token_file", "audit_root")
    }
    for name, path in result.items():
        if not path.is_absolute() or path.is_symlink():
            raise ArvanDestructiveControlError("destructive provider binding path is unsafe")
        try:
            metadata = path.stat()
        except OSError as exc:
            raise ArvanDestructiveControlError("destructive provider binding path is unavailable") from exc
        if (
            metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or (name == "audit_root" and not stat.S_ISDIR(metadata.st_mode))
            or (name != "audit_root" and not stat.S_ISREG(metadata.st_mode))
        ):
            raise ArvanDestructiveControlError("destructive provider binding is not owner-only")
    return result


def build_bound_power_intent(
    *,
    control: dict[str, Any],
    campaign_id: str,
    gate_group_id: str,
    release_sha: str,
    operation_id: str,
    scenario_id: str,
    role: str,
    action: str,
    request: RequestFn = api_request,
    token: str | None = None,
) -> dict[str, Any]:
    """Create one provider intent using only campaign-bound pointers."""

    paths = _bound_paths(
        control,
        campaign_id=campaign_id,
        gate_group_id=gate_group_id,
        release_sha=release_sha,
    )
    return build_power_intent(
        campaign_id=campaign_id,
        release_sha=release_sha,
        operation_id=operation_id,
        scenario_id=scenario_id,
        role=role,
        action=action,
        request=request,
        token=token,
        state_file=paths["provider_state_file"],
        token_file=paths["provider_token_file"],
    )


def execute_bound_power_intent(
    intent: dict[str, Any],
    *,
    control: dict[str, Any],
    campaign_id: str,
    gate_group_id: str,
    release_sha: str,
    request: RequestFn = api_request,
    token: str | None = None,
    timeout_seconds: float = 180.0,
    poll_seconds: float = 2.0,
) -> dict[str, Any]:
    """Execute only a power intent bound to the same runtime-plan control."""

    paths = _bound_paths(
        control,
        campaign_id=campaign_id,
        gate_group_id=gate_group_id,
        release_sha=release_sha,
    )
    command = _validate_intent(intent)
    if (
        command["campaign_id"] != campaign_id
        or command["release_sha"] != release_sha
    ):
        raise ArvanDestructiveControlError("power intent differs from destructive binding")
    audit_path = paths["audit_root"] / f"{command['operation_id']}-power-audit.jsonl"
    return execute_power_intent(
        command,
        audit_path=audit_path,
        request=request,
        token=token,
        state_file=paths["provider_state_file"],
        token_file=paths["provider_token_file"],
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def _host(
    role: str,
    *,
    request: RequestFn,
    token: str,
    state_file: Path | None = None,
) -> dict[str, Any]:
    if role not in ROLE_ORDER:
        raise ArvanDestructiveControlError("provider power role is outside the campaign")
    state = _safe_existing_state() if state_file is None else _safe_existing_state(state_file)
    if state is None or state.get("status") != "active":
        raise ArvanDestructiveControlError("disposable host state is unavailable")
    hosts = state.get("hosts")
    stored = hosts.get(role) if isinstance(hosts, dict) else None
    spec = ROLE_SPECS[role]
    if not isinstance(stored, dict):
        raise ArvanDestructiveControlError("disposable host identity is unavailable")
    region = str(stored.get("region") or "")
    server_id = str(stored.get("server_id") or "")
    if region != spec["region"] or SERVER_ID.fullmatch(server_id) is None:
        raise ArvanDestructiveControlError("disposable host provider identity is invalid")
    try:
        payload = response_data(
            request("GET", f"/regions/{region}/servers/{server_id}", token),
            f"read {role} for destructive power action",
        )
    except ProvisionError as exc:
        raise ArvanDestructiveControlError("provider host read failed") from exc
    if not isinstance(payload, dict):
        raise ArvanDestructiveControlError("provider host response is invalid")
    _verify_server(role, payload)
    public_ip = _safe_public_ip(payload, role)
    if public_ip != str(stored.get("public_ip") or ""):
        raise ArvanDestructiveControlError("provider host address differs from campaign state")
    status = str(payload.get("status") or "").upper()
    if not status:
        raise ArvanDestructiveControlError("provider host has no lifecycle status")
    return {
        "role": role,
        "region": region,
        "server_id": server_id,
        "name": spec["name"],
        "public_ip": public_ip,
        "status": status,
        "plan_id": spec["plan_id"],
    }


def _fingerprint(host: dict[str, Any]) -> str:
    fields = {"role", "region", "server_id", "name", "public_ip", "plan_id"}
    if set(host) - {"status"} != fields:
        raise ArvanDestructiveControlError("provider host fingerprint fields differ")
    return hashlib.sha256(
        canonical_json_bytes({key: host[key] for key in sorted(fields)})
    ).hexdigest()


def build_power_intent(
    *,
    campaign_id: str,
    release_sha: str,
    operation_id: str,
    scenario_id: str,
    role: str,
    action: str,
    request: RequestFn = api_request,
    token: str | None = None,
    state_file: Path | None = None,
    token_file: Path = TOKEN_FILE,
) -> dict[str, Any]:
    """Read and bind one exact reversible provider action without mutating."""

    if (
        UUID.fullmatch(campaign_id) is None
        or SHA40.fullmatch(release_sha) is None
        or UUID.fullmatch(operation_id) is None
        or SCENARIO.fullmatch(scenario_id) is None
        or action not in POWER_ACTIONS
    ):
        raise ArvanDestructiveControlError("power intent identity is invalid")
    current = _host(
        role,
        request=request,
        token=token or read_private_text(token_file),
        state_file=state_file,
    )
    policy = POWER_ACTIONS[action]
    if current["status"] not in policy["before"]:
        raise ArvanDestructiveControlError("provider power action precondition differs")
    intent = {
        "schema": SCHEMA,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "operation_id": operation_id,
        "scenario_id": scenario_id,
        "role": role,
        "action": action,
        "expected_before_status": current["status"],
        "expected_after_statuses": sorted(policy["after"]),
        "recovery_action": policy["recovery"],
        "provider_endpoint": f"/regions/{current['region']}/servers/{{campaign-host}}/{policy['endpoint']}",
        "host_fingerprint": _fingerprint(current),
    }
    intent["intent_sha256"] = hashlib.sha256(canonical_json_bytes(intent)).hexdigest()
    return intent


def _validate_intent(intent: Any) -> dict[str, Any]:
    fields = {
        "schema", "campaign_id", "release_sha", "operation_id", "scenario_id",
        "role", "action", "expected_before_status", "expected_after_statuses",
        "recovery_action", "provider_endpoint", "host_fingerprint", "intent_sha256",
    }
    if not isinstance(intent, dict) or set(intent) != fields or intent.get("schema") != SCHEMA:
        raise ArvanDestructiveControlError("power intent fields differ")
    if (
        UUID.fullmatch(str(intent.get("campaign_id") or "")) is None
        or SHA40.fullmatch(str(intent.get("release_sha") or "")) is None
        or UUID.fullmatch(str(intent.get("operation_id") or "")) is None
        or SCENARIO.fullmatch(str(intent.get("scenario_id") or "")) is None
        or intent.get("role") not in ROLE_ORDER
        or intent.get("action") not in POWER_ACTIONS
        or not isinstance(intent.get("expected_after_statuses"), list)
        or not all(isinstance(item, str) for item in intent["expected_after_statuses"])
        or intent.get("recovery_action") != POWER_ACTIONS[intent["action"]]["recovery"]
        or SHA256.fullmatch(str(intent.get("host_fingerprint") or "")) is None
        or SHA256.fullmatch(str(intent.get("intent_sha256") or "")) is None
    ):
        raise ArvanDestructiveControlError("power intent values differ")
    unsigned = {key: value for key, value in intent.items() if key != "intent_sha256"}
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != intent["intent_sha256"]:
        raise ArvanDestructiveControlError("power intent hash differs")
    return dict(intent)


def execute_power_intent(
    intent: dict[str, Any],
    *,
    audit_path: Path,
    request: RequestFn = api_request,
    token: str | None = None,
    state_file: Path | None = None,
    token_file: Path = TOKEN_FILE,
    timeout_seconds: float = 180.0,
    poll_seconds: float = 2.0,
) -> dict[str, Any]:
    """Apply a pre-read intent and wait for the exact target power state.

    This has no CLI wrapper.  Only a sealed Full Matrix scenario handler may
    call it after the campaign-level fresh approval and fencing oracle have
    admitted the exact scenario.  If provider acknowledgement is uncertain,
    an audit event is retained and the caller fails closed into recovery.
    """

    command = _validate_intent(intent)
    if (
        not audit_path.is_absolute()
        or audit_path.is_symlink()
        or not 1.0 <= float(timeout_seconds) <= 900.0
        or not 0.1 <= float(poll_seconds) <= 30.0
    ):
        raise ArvanDestructiveControlError("power execution arguments are unsafe")
    current_token = token or read_private_text(token_file)
    before = _host(
        command["role"], request=request, token=current_token, state_file=state_file
    )
    if (
        before["status"] != command["expected_before_status"]
        or _fingerprint(before) != command["host_fingerprint"]
    ):
        raise ArvanDestructiveControlError("provider host changed after power intent")
    endpoint = (
        f"/regions/{before['region']}/servers/{before['server_id']}/"
        f"{POWER_ACTIONS[command['action']]['endpoint']}"
    )
    started = datetime.now(timezone.utc).isoformat()
    response_status = "unacknowledged"
    try:
        response = request("POST", endpoint, current_token, {})
        response_data(response, f"apply {command['action']} to {command['role']}")
        response_status = "acknowledged"
    except ProvisionError as exc:
        event = {
            "schema": AUDIT_SCHEMA,
            "event": "provider_power_action_uncertain",
            "started_at": started,
            "campaign_id": command["campaign_id"],
            "operation_id": command["operation_id"],
            "scenario_id": command["scenario_id"],
            "role": command["role"],
            "action": command["action"],
            "intent_sha256": command["intent_sha256"],
            "provider_response": response_status,
        }
        try:
            append_hash_chained_jsonl(audit_path, event)
        except SecureFileError as audit_exc:
            raise ArvanDestructiveControlError("power action and audit are uncertain") from audit_exc
        raise ArvanDestructiveControlError("provider power action acknowledgement is uncertain") from exc
    deadline = time.monotonic() + float(timeout_seconds)
    observed: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        observed = _host(
            command["role"], request=request, token=current_token, state_file=state_file
        )
        if _fingerprint(observed) != command["host_fingerprint"]:
            raise ArvanDestructiveControlError("provider host identity changed during power action")
        if observed["status"] in set(command["expected_after_statuses"]):
            event = {
                "schema": AUDIT_SCHEMA,
                "event": "provider_power_action_completed",
                "started_at": started,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "campaign_id": command["campaign_id"],
                "operation_id": command["operation_id"],
                "scenario_id": command["scenario_id"],
                "role": command["role"],
                "action": command["action"],
                "intent_sha256": command["intent_sha256"],
                "provider_response": response_status,
                "observed_status": observed["status"],
            }
            try:
                audit_record = append_hash_chained_jsonl(audit_path, event)
            except SecureFileError as exc:
                raise ArvanDestructiveControlError("power action completed without a safe audit") from exc
            return {
                "schema": SCHEMA,
                "status": "passed",
                "intent_sha256": command["intent_sha256"],
                "role": command["role"],
                "action": command["action"],
                "before_status": command["expected_before_status"],
                "after_status": observed["status"],
                "audit_event_hash": audit_record["event_hash"],
            }
        time.sleep(float(poll_seconds))
    raise ArvanDestructiveControlError("provider power action did not reach its target state")
