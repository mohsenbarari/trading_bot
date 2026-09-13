"""Bounded observed-provider telemetry, not billing or an acceptance gate."""
from __future__ import annotations

import math
import os
from pathlib import Path
import re

try:
    from scripts.two_site_proposal_runner import bounded_file_text, unique_json
except ModuleNotFoundError:
    from two_site_proposal_runner import bounded_file_text, unique_json

SUFFIX = r"(?:-(?:codex-)?(?:worker|reviewer)-\d+(?:-retry-\d+)?|-codex-correction-\d+-(?:worker|reviewer))"
FINAL = re.compile(r"(.+?)(?:-(?:codex-)?worker-\d+(?:-retry-\d+)?|-codex-correction-\d+-worker)")
TERMINAL = {"PROPOSAL_READY", "VERIFIED", "CHANGES_REQUIRED", "BLOCKED"}
TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def unavailable() -> dict:
    return {"state": "UNAVAILABLE", "first_pass": None,
            "usage": dict.fromkeys(TOKEN_KEYS),
            "phase_seconds": {"provider_reported": None, "outside_provider_unattributed": None}}


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


def provider_metrics(root: Path, task_id: str, head_before: str,
                     final_worker_id: str, cycle_duration: float) -> dict:
    """Aggregate <=32 same-prefix/task/head attempts. Unknown is never zero.

    Timing covers matching provider attempts only. The residual is unprofiled
    controller/test/review time, not evidence of waste or distinct phase timing.
    """
    result = unavailable()
    if not isinstance(head_before, str) or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", x)
               for x in (task_id, final_worker_id)) or not re.fullmatch(r"[a-f0-9]{40}", head_before or ""):
        return result
    match = FINAL.fullmatch(final_worker_id)
    if not match:
        return result
    prefix = match.group(1)
    pattern = re.compile(re.escape(prefix) + SUFFIX)
    folder = root / ".local/two-site-refactor/proposals"
    paths = []
    try:
        if folder.resolve() != folder or not folder.is_relative_to(root):
            return result
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
    attempts, complete = [], True
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
    if not attempts:
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
    result.update({
        "state": "OBSERVED_MATCHING_ATTEMPTS" if complete else "PARTIAL",
        "attempts": len(attempts),
        "worker_attempts": sum(s["role"] == "worker" for s, _ in attempts),
        "reviewer_attempts": sum(s["role"] == "verifier" for s, _ in attempts),
        "failed_attempts": sum(s["state"] == "BLOCKED" for s, _ in attempts),
        "successful_correction_rounds": len(corrections),
        "first_pass": (len(attempts) == 2 and not corrections
                       and {s["state"] for s, _ in attempts} == {"PROPOSAL_READY", "VERIFIED"}) if complete else None,
        "usage": totals if complete and len(usages) == len(attempts) else dict.fromkeys(TOKEN_KEYS),
        "observed_usage_subtotal": totals,
        "usage_observed_attempts": len(usages),
        "timing_coverage": "MATCHING_PROVIDER_ATTEMPTS_ONLY" if time_known else "UNKNOWN",
        "phase_seconds": {"provider_reported": provider_time if time_known else None,
                          "outside_provider_unattributed": cycle_duration - provider_time if time_known else None},
    })
    return result
