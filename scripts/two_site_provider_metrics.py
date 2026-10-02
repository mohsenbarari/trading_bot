"""Bounded observed-provider telemetry, not billing or an acceptance gate."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

try:
    from scripts.two_site_proposal_runner import MAX_CONTEXT, bounded_file_text, unique_json
except ModuleNotFoundError:
    from two_site_proposal_runner import MAX_CONTEXT, bounded_file_text, unique_json

try:
    from scripts.two_site_semantic_task import validate_identity
except ModuleNotFoundError:
    try:
        from two_site_semantic_task import validate_identity
    except ModuleNotFoundError:
        validate_identity = None

SUFFIX = r"(?:-(?:codex-)?(?:worker|reviewer)-\d+(?:-retry-\d+)?|-codex-correction-\d+-(?:worker|reviewer))"
FINAL = re.compile(r"(.+?)(?:-(?:codex-)?worker-\d+(?:-retry-\d+)?|-codex-correction-\d+-worker)")
TERMINAL = {"PROPOSAL_READY", "VERIFIED", "CHANGES_REQUIRED", "BLOCKED"}
TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens")
INITIAL_WORKER_RUN = re.compile(r".+-worker-0(?:-retry-\d+)?$")
INITIAL_REVIEWER_RUN = re.compile(r".+-reviewer-0$")
TASK_SNAPSHOT_LIMIT = 3 * MAX_CONTEXT
TASK_GOAL_LIMIT = MAX_CONTEXT
CANONICAL_EXCERPTS_LIMIT = MAX_CONTEXT


SESSION_GATE_KEYS = {"state", "activation_reason", "root_count", "required_symbol_count", "query_operation_count"}


def _session_gate_empty(state: str) -> dict[str, object]:
    return {
        "state": state,
        "activation_reason": None,
        "root_count": None,
        "required_symbol_count": None,
        "query_operation_count": None,
    }


def _rework_taxonomy_empty(state: str) -> dict[str, str]:
    """Return the fixed, source-free shape for rework classification."""
    return {
        "state": state,
        "initial_outcome": "UNKNOWN",
        "rework_path": "UNKNOWN",
        "terminal_outcome": "UNKNOWN",
    }


def unavailable() -> dict:
    return {"state": "UNAVAILABLE", "first_pass": None,
            "usage": dict.fromkeys(TOKEN_KEYS),
            "phase_seconds": {"provider_reported": None, "outside_provider_unattributed": None},
            "task_metadata": None,
            "semantic_identity": None,
            "session_gate": _session_gate_empty("UNAVAILABLE"),
            "rework_taxonomy": _rework_taxonomy_empty("UNAVAILABLE")}


def _validate_session_gate(gate: object) -> dict[str, object] | None:
    if not isinstance(gate, dict):
        return None
    if set(gate.keys()) != SESSION_GATE_KEYS:
        return None
    state = gate.get("state")
    reason = gate.get("activation_reason")
    root_count = gate.get("root_count")
    req_count = gate.get("required_symbol_count")
    query_count = gate.get("query_operation_count")

    if type(root_count) is not int or isinstance(root_count, bool):
        return None
    if type(req_count) is not int or isinstance(req_count, bool):
        return None
    if type(query_count) is not int or isinstance(query_count, bool):
        return None

    if state == "REQUESTED_AND_PASSED":
        if reason not in ("EXPLICIT_DECLARATION", "LEGACY_HEURISTIC"):
            return None
        if not (0 <= root_count <= 8):
            return None
        if not (0 <= req_count <= 48):
            return None
        if not (0 <= query_count <= 24):
            return None
        return {
            "state": "REQUESTED_AND_PASSED",
            "activation_reason": reason,
            "root_count": root_count,
            "required_symbol_count": req_count,
            "query_operation_count": query_count,
        }
    if state == "NOT_REQUESTED":
        if reason != "NOT_REQUESTED":
            return None
        if root_count != 0 or req_count != 0 or query_count != 0:
            return None
        return {
            "state": "NOT_REQUESTED",
            "activation_reason": "NOT_REQUESTED",
            "root_count": 0,
            "required_symbol_count": 0,
            "query_operation_count": 0,
        }
    return None


def _aggregate_session_gate(attempts: list[tuple[dict, dict | None]]) -> dict[str, object]:
    if not attempts:
        return _session_gate_empty("UNKNOWN")
    first_gate: dict[str, object] | None = None
    for status, _ in attempts:
        if "session_gate" not in status:
            return _session_gate_empty("UNKNOWN")
        validated = _validate_session_gate(status["session_gate"])
        if validated is None:
            return _session_gate_empty("UNKNOWN")
        if first_gate is None:
            first_gate = validated
        elif validated != first_gate:
            return _session_gate_empty("UNKNOWN")
    return first_gate if first_gate is not None else _session_gate_empty("UNKNOWN")


def _number(value) -> bool:
    return type(value) in (int, float) and 0 <= value <= 10**12 and math.isfinite(value)


def _usage(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    if any(type(value.get(k)) is not int or not 0 <= value[k] <= 10**12 for k in TOKEN_KEYS):
        return None
    if value["total_tokens"] != value["input_tokens"] + value["output_tokens"]:
        return None
    return {k: value[k] for k in TOKEN_KEYS}  # thinking is already included in output


def _stream_usage(root: Path, path: Path) -> dict | None:
    raw = bounded_file_text(root, path, 2_000_000, "provider metrics stream")
    if not raw.endswith("\n"):
        return None
    found = []
    for line in raw.splitlines():
        if len(line.encode()) > 1_000_000:
            return None
        item = unique_json(line)
        if not isinstance(item, dict):
            return None
        if item.get("event") == "result":
            result = item.get("result")
            found.append(_usage(result.get("usage")) if isinstance(result, dict) else None)
            if len(found) > 1:
                return None
    return found[0] if len(found) == 1 else None


def _extract_task_metadata(root: Path, path: Path, task_id: str, head_before: str) -> tuple[str, dict] | None:
    task_file = path / "task.json"
    try:
        raw = bounded_file_text(root, task_file, TASK_SNAPSHOT_LIMIT, "task snapshot")
        data = unique_json(raw)
        if not isinstance(data, dict):
            return None
        if data.get("source_head") != head_before:
            return None
        task_dict = data.get("task") if isinstance(data.get("task"), dict) else data
        if not isinstance(task_dict, dict):
            return None
        if task_dict.get("task_id") != task_id and data.get("task_id") != task_id:
            return None

        task_obj = data.get("task") if isinstance(data.get("task"), dict) else task_dict
        task_sha = hashlib.sha256(json.dumps(task_obj, sort_keys=True).encode("utf-8")).hexdigest()

        reqs = task_dict.get("requirements")
        selectors = task_dict.get("test_selectors")

        goal = task_dict.get("goal")
        if not isinstance(goal, str) or len(goal.encode("utf-8")) > TASK_GOAL_LIMIT:
            return None
        marker = "CANONICAL_SOURCE_EXCERPTS_JSON:"
        if goal.count(marker) != 1:
            return None
        payload_str = goal.split(marker, 1)[1].strip()
        if not payload_str or len(payload_str.encode("utf-8")) > CANONICAL_EXCERPTS_LIMIT:
            return None
        payload = unique_json(payload_str)
        if not isinstance(payload, dict):
            return None
        gap_ref = payload.get("gap_reference")
        if not gap_ref and isinstance(payload.get("semantic_identity"), dict):
            gap_ref = payload["semantic_identity"].get("gap_reference")
        if not isinstance(gap_ref, str) or not (1 <= len(gap_ref) <= 500) or any(c in gap_ref for c in ("\n", "\r", "\0")):
            return None

        meta = {
            "task_id": task_id,
            "task_sha256": task_sha,
            "declared_requirement_count": len(reqs) if isinstance(reqs, list) else 0,
            "focused_selector_count": len(selectors) if isinstance(selectors, list) else 0,
            "gap_reference": gap_ref,
        }
        return task_sha, meta
    except (OSError, ValueError, TypeError):
        return None


def _compute_work_lineage(validated: dict) -> dict[str, object] | None:
    try:
        keys = ("stage_id", "gap_reference", "scenario_key", "symbol_groups")
        for k in keys:
            if k not in validated:
                return None
        work_payload = {
            "gap_reference": validated["gap_reference"],
            "kind": "two-site-logical-work",
            "scenario_key": validated["scenario_key"],
            "schema_version": 1,
            "stage_id": validated["stage_id"],
            "symbol_groups": validated["symbol_groups"],
        }
        raw_work_json = json.dumps(
            work_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return {
            "schema_version": 1,
            "fingerprint": hashlib.sha256(raw_work_json).hexdigest(),
        }
    except (TypeError, ValueError):
        return None


def _extract_identity_bundle(root: Path, path: Path, task_id: str, head_before: str) -> tuple[dict[str, str], dict[str, object]] | None:
    task_file = path / "task.json"
    try:
        raw = bounded_file_text(root, task_file, TASK_SNAPSHOT_LIMIT, "task snapshot")
        data = unique_json(raw)
        if not isinstance(data, dict):
            return None
        if data.get("source_head") != head_before:
            return None
        task_dict = data.get("task") if isinstance(data.get("task"), dict) else data
        if not isinstance(task_dict, dict):
            return None
        if task_dict.get("task_id") != task_id and data.get("task_id") != task_id:
            return None

        goal = task_dict.get("goal")
        if not isinstance(goal, str) or len(goal.encode("utf-8")) > TASK_GOAL_LIMIT:
            return None
        canonical_marker = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
        if goal.count(canonical_marker) != 1 or goal.count("CANONICAL_SOURCE_EXCERPTS_JSON:") != 1:
            return None
        payload_str = goal.split(canonical_marker, 1)[1].strip()
        if not payload_str or len(payload_str.encode("utf-8")) > CANONICAL_EXCERPTS_LIMIT:
            return None
        payload = unique_json(payload_str)
        if not isinstance(payload, dict):
            return None

        if validate_identity is None or not callable(validate_identity):
            return None
        raw_identity = payload.get("semantic_identity")
        if not isinstance(raw_identity, dict):
            return None
        try:
            validated = validate_identity(raw_identity)
        except Exception:
            return None
        if not validated:
            return None

        payload_gap_ref = payload.get("gap_reference")
        val_gap_ref = validated.get("gap_reference") if isinstance(validated, dict) else getattr(validated, "gap_reference", None)
        if not isinstance(payload_gap_ref, str) or not payload_gap_ref or any(c in payload_gap_ref for c in ("\n", "\r", "\0")):
            return None
        if payload_gap_ref != val_gap_ref:
            return None

        keys = ("fingerprint", "source_sha256", "stage_id", "gap_reference", "scenario_key")
        projected = {}
        for k in keys:
            val = validated.get(k) if isinstance(validated, dict) else getattr(validated, k, None)
            if not isinstance(val, str) or not val:
                return None
            projected[k] = val

        work_lineage = _compute_work_lineage(validated)
        if work_lineage is None:
            return None

        return projected, work_lineage
    except (OSError, ValueError, TypeError):
        return None


def _extract_semantic_identity(root: Path, path: Path, task_id: str, head_before: str) -> dict[str, str] | None:
    bundle = _extract_identity_bundle(root, path, task_id, head_before)
    return bundle[0] if bundle is not None else None


def _rework_taxonomy(
    attempts: list[tuple[dict, dict | None]], *, complete: bool, first_pass: bool | None,
    verified_chain: bool,
) -> dict[str, str]:
    """Classify only structural rework evidence, never provider prose.

    Attempt statuses are already validated before this function receives them. Even
    so, this deliberately emits only fixed labels derived from role, terminal state,
    SHA-shaped correction linkage and run topology. It must never become a channel
    for prompts, sources, findings, errors, commands, or task text.
    """
    if not complete or not attempts:
        return _rework_taxonomy_empty("UNKNOWN")

    statuses = [status for status, _ in attempts]
    ready_workers = {
        status["run_id"] for status in statuses
        if status["role"] == "worker" and status["state"] == "PROPOSAL_READY"
    }
    initial_workers = [
        status for status in statuses
        if status["role"] == "worker" and INITIAL_WORKER_RUN.fullmatch(status["run_id"])
    ]
    initial_reviewers = [
        status for status in statuses
        if status["role"] == "verifier" and INITIAL_REVIEWER_RUN.fullmatch(status["run_id"])
    ]
    reviewer_changes = any(
        status["state"] == "CHANGES_REQUIRED"
        and status.get("input_worker_run_id") in ready_workers
        for status in statuses if status["role"] == "verifier"
    )
    attempt_blocked = any(status["state"] == "BLOCKED" for status in statuses)
    correction_without_reviewer_change = any(
        status["role"] == "worker"
        and status["state"] == "PROPOSAL_READY"
        and re.fullmatch(r"[a-f0-9]{64}", str(status.get("input_proposal_sha256", "")))
        for status in statuses
    ) and not reviewer_changes

    initial_reviewer_change = any(
        status["state"] == "CHANGES_REQUIRED"
        and status.get("input_worker_run_id") in ready_workers
        for status in initial_reviewers
    )
    initial_blocked = any(status["state"] == "BLOCKED" for status in initial_workers + initial_reviewers)

    if first_pass is True:
        initial_outcome = "FIRST_PASS"
    elif initial_reviewer_change:
        initial_outcome = "REVIEWER_CHANGES_REQUIRED"
    elif initial_blocked:
        initial_outcome = "ATTEMPT_BLOCKED"
    elif correction_without_reviewer_change:
        initial_outcome = "POST_REVIEW_CORRECTION"
    elif first_pass is False:
        initial_outcome = "UNCLASSIFIED_REWORK"
    else:
        initial_outcome = "UNKNOWN"

    triggers = []
    if reviewer_changes:
        triggers.append("REVIEWER_CHANGES_REQUIRED")
    if attempt_blocked:
        triggers.append("ATTEMPT_BLOCKED")
    if correction_without_reviewer_change:
        triggers.append("POST_REVIEW_CORRECTION")
    if not triggers:
        rework_path = "NONE" if first_pass is True else "UNCLASSIFIED_REWORK" if first_pass is False else "UNKNOWN"
    elif len(triggers) == 1:
        rework_path = triggers[0]
    else:
        rework_path = "MIXED"

    terminal_outcome = "VERIFIED" if verified_chain else "ATTEMPT_BLOCKED" if attempt_blocked else "NO_VERIFIED_CHAIN"
    return {
        "state": "OBSERVED",
        "initial_outcome": initial_outcome,
        "rework_path": rework_path,
        "terminal_outcome": terminal_outcome,
    }


def provider_metrics(root: Path, task_id: str | None = None, head_before: str = "",
                     final_worker_id: str | None = None, cycle_duration: float = 0.0,
                     controller_run_id: str | None = None) -> dict:
    """Aggregate <=32 matching attempts. Unknown is never zero.

    Timing covers matching provider attempts only. The residual is unprofiled
    controller/test/review time, not evidence of waste or distinct phase timing.
    """
    result = unavailable()
    if not isinstance(head_before, str) or not re.fullmatch(r"[a-f0-9]{40}", head_before or ""):
        return result

    controller_mode = controller_run_id is not None
    if controller_mode:
        if not isinstance(controller_run_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", controller_run_id):
            return result
        if task_id is not None and (
            not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", task_id)
        ):
            return result
        if final_worker_id is not None and (
            not isinstance(final_worker_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", final_worker_id)
        ):
            return result
    else:
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", task_id or ""):
            return result
        if not isinstance(final_worker_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", final_worker_id or ""):
            return result
        match = FINAL.fullmatch(final_worker_id)
        if not match:
            return result
        prefix = match.group(1)
        pattern = re.compile(re.escape(prefix) + SUFFIX)

    folder = root / ".local/two-site-refactor/proposals"
    try:
        if folder.resolve() != folder or not folder.is_relative_to(root):
            return result
        if not folder.is_dir():
            return result
    except OSError:
        return result

    attempts, complete = [], True
    task_metadata = None
    semantic_identity = None
    work_lineage = None

    if not controller_mode:
        paths = []
        try:
            with os.scandir(folder) as entries:
                for count, entry in enumerate(entries):
                    if count >= 8192:
                        return result
                    if pattern.fullmatch(entry.name):
                        paths.append(Path(entry.path))
                        if len(paths) > 32:
                            return result
        except OSError:
            return result

        for path in sorted(paths):
            try:
                status = unique_json(bounded_file_text(root, path / "status.json", 32_000, "provider status"))
                if not isinstance(status, dict):
                    raise ValueError("invalid status")
                if status.get("task_id") != task_id or status.get("head") != head_before:
                    complete = False
                    continue
                if (status.get("run_id") != path.name or status.get("role") not in {"worker", "verifier"}
                        or status.get("state") not in TERMINAL):
                    complete = False
                    continue
                usage = _usage(status.get("usage"))
                if status.get("usage") is None:
                    try:
                        usage = _stream_usage(root, path / "stream.jsonl")
                    except (OSError, ValueError, TypeError):
                        usage = None
                attempts.append((status, usage))
            except (OSError, ValueError, TypeError):
                complete = False

        names = {s["run_id"] for s, _ in attempts}
        complete = complete and {prefix + "-worker-0", prefix + "-reviewer-0", final_worker_id} <= names
        complete = complete and any(s["role"] == "verifier" and s["state"] == "VERIFIED"
                                   and s.get("input_worker_run_id") == final_worker_id for s, _ in attempts)
        task_snaps = [_extract_task_metadata(root, path, task_id, head_before) for path in sorted(paths)]
        if task_snaps and all(s is not None for s in task_snaps) and len({s[0] for s in task_snaps}) == 1:
            task_metadata = task_snaps[0][1]
        if complete and paths:
            bundles = [_extract_identity_bundle(root, path, task_id, head_before) for path in sorted(paths)]
            if all(b is not None for b in bundles) and len(bundles) > 0 and all(b == bundles[0] for b in bundles):
                semantic_identity = bundles[0][0]
                work_lineage = bundles[0][1]
            else:
                semantic_identity = None
                work_lineage = None
        else:
            semantic_identity = None
            work_lineage = None
    else:
        matched_entries = []
        seen_names = set()
        task_ids = set()
        saw_controller_record = False
        try:
            with os.scandir(folder) as entries:
                for count, entry in enumerate(entries):
                    if count >= 8192:
                        return result
                    status_file = Path(entry.path) / "status.json"
                    try:
                        if entry.is_symlink() or status_file.is_symlink():
                            continue
                        if not status_file.is_file():
                            continue
                        raw_status = bounded_file_text(root, status_file, 32_000, "provider status")
                    except (OSError, ValueError, TypeError):
                        continue

                    try:
                        status = unique_json(raw_status)
                        if not isinstance(status, dict):
                            continue
                    except (ValueError, TypeError):
                        continue

                    if status.get("controller_run_id") != controller_run_id:
                        continue

                    saw_controller_record = True
                    valid = True

                    if entry.name in seen_names:
                        complete = False
                        valid = False
                    seen_names.add(entry.name)

                    if status.get("run_id") != entry.name:
                        complete = False
                        valid = False

                    if status.get("head") != head_before:
                        complete = False
                        valid = False

                    role = status.get("role")
                    state_val = status.get("state")
                    if role not in {"worker", "verifier"} or state_val not in TERMINAL:
                        complete = False
                        valid = False

                    att_task_id = status.get("task_id")
                    if not isinstance(att_task_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", att_task_id):
                        complete = False
                        valid = False
                    elif task_id is not None and att_task_id != task_id:
                        complete = False
                        valid = False
                    else:
                        task_ids.add(att_task_id)

                    if not valid:
                        continue

                    matched_entries.append((Path(entry.path), status))
                    if len(matched_entries) > 32:
                        return result
        except OSError:
            return result

        matched_entries.sort(key=lambda x: x[0].name)
        for path, status in matched_entries:
            usage = _usage(status.get("usage"))
            if status.get("usage") is None:
                try:
                    usage = _stream_usage(root, path / "stream.jsonl")
                except (OSError, ValueError, TypeError):
                    usage = None
            attempts.append((status, usage))

        if complete and len(task_ids) == 1 and (task_id is None or next(iter(task_ids)) == task_id):
            effective_task_id = next(iter(task_ids))
            task_snaps = [_extract_task_metadata(root, path, effective_task_id, head_before) for path, _ in matched_entries]
            if not attempts or any(s is None for s in task_snaps) or len({s[0] for s in task_snaps}) != 1:
                task_metadata = None
                complete = False
            else:
                task_metadata = task_snaps[0][1]
            if complete and matched_entries:
                bundles = [_extract_identity_bundle(root, path, effective_task_id, head_before) for path, _ in matched_entries]
                if all(b is not None for b in bundles) and len(bundles) > 0 and all(b == bundles[0] for b in bundles):
                    semantic_identity = bundles[0][0]
                    work_lineage = bundles[0][1]
                else:
                    semantic_identity = None
                    work_lineage = None
            else:
                semantic_identity = None
                work_lineage = None
        else:
            task_metadata = None
            semantic_identity = None
            work_lineage = None
            if len(task_ids) != 1 or (task_id is not None and (not task_ids or next(iter(task_ids)) != task_id)):
                complete = False

    if not attempts and not (controller_mode and saw_controller_record):
        return result

    usages = [u for _, u in attempts if u is not None]
    totals = {k: sum(u[k] for u in usages) for k in TOKEN_KEYS}
    corrections = {s["input_proposal_sha256"] for s, _ in attempts
                   if s["role"] == "worker" and s["state"] == "PROPOSAL_READY"
                   and re.fullmatch(r"[a-f0-9]{64}", str(s.get("input_proposal_sha256", "")))}
    durations = [s.get("duration_seconds") for s, _ in attempts]
    time_known = complete and all(_number(x) for x in durations) and _number(cycle_duration)
    provider_time = sum(durations) if time_known else None
    if provider_time is not None and provider_time > cycle_duration:
        time_known = False

    verified_chain = any(
        status["role"] == "verifier" and status["state"] == "VERIFIED"
        and any(
            worker["role"] == "worker" and worker["state"] == "PROPOSAL_READY"
            and status.get("input_worker_run_id") == worker["run_id"]
            for worker, _ in attempts
        )
        for status, _ in attempts
    )
    if controller_mode and final_worker_id is not None:
        verified_chain = verified_chain and any(
            status["role"] == "verifier" and status["state"] == "VERIFIED"
            and status.get("input_worker_run_id") == final_worker_id
            for status, _ in attempts
        )

    if not controller_mode:
        first_pass = (len(attempts) == 2 and not corrections
                      and {s["state"] for s, _ in attempts} == {"PROPOSAL_READY", "VERIFIED"}) if complete else None
        state = "OBSERVED_MATCHING_ATTEMPTS" if complete else "PARTIAL"
    else:
        worker_0 = [s for s, _ in attempts if s["role"] == "worker" and s["run_id"].endswith("worker-0")]
        reviewer_0 = [s for s, _ in attempts if s["role"] == "verifier" and s["run_id"].endswith("reviewer-0")]
        is_initial_pair = (
            len(attempts) == 2
            and len(worker_0) == 1 and worker_0[0]["state"] == "PROPOSAL_READY"
            and len(reviewer_0) == 1 and reviewer_0[0]["state"] == "VERIFIED"
            and reviewer_0[0].get("input_worker_run_id") == worker_0[0]["run_id"]
            and len(corrections) == 0
            and (final_worker_id is None or final_worker_id == worker_0[0]["run_id"])
        )
        if not complete or not verified_chain:
            first_pass = None
        elif is_initial_pair:
            first_pass = True
        else:
            first_pass = False
        state = "OBSERVED_CONTROLLER_ATTEMPTS" if complete and len(attempts) >= 1 else "PARTIAL"

    result.update({
        "state": state,
        "attempts": len(attempts),
        "worker_attempts": sum(s["role"] == "worker" for s, _ in attempts),
        "reviewer_attempts": sum(s["role"] == "verifier" for s, _ in attempts),
        "failed_attempts": sum(s["state"] == "BLOCKED" for s, _ in attempts),
        "successful_correction_rounds": len(corrections),
        "first_pass": first_pass,
        "usage": totals if complete and len(usages) == len(attempts) else dict.fromkeys(TOKEN_KEYS),
        "observed_usage_subtotal": totals,
        "usage_observed_attempts": len(usages),
        "timing_coverage": "MATCHING_PROVIDER_ATTEMPTS_ONLY" if time_known else "UNKNOWN",
        "phase_seconds": {"provider_reported": provider_time if time_known else None,
                          "outside_provider_unattributed": cycle_duration - provider_time if time_known else None},
        "task_metadata": task_metadata,
        "semantic_identity": semantic_identity if complete else None,
        "session_gate": _aggregate_session_gate(attempts),
        "rework_taxonomy": _rework_taxonomy(
            attempts, complete=complete, first_pass=first_pass, verified_chain=verified_chain,
        ),
    })
    if complete and semantic_identity is not None and work_lineage is not None:
        result["work_lineage"] = work_lineage
    return result
