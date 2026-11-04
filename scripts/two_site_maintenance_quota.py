"""One due, zero-effect Reviewer quota continuation within an existing job.

This is not general retry authority. The caller retains pipeline/Source locks,
validates completed author/native evidence and uses a fresh Reviewer session.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
import re
from pathlib import Path

from scripts import two_site_qoder_maintenance as maintenance
from scripts.two_site_quota_evidence import classify_gemini_quota_stream

SCHEMA = "QODER_MAINTENANCE_REVIEWER_QUOTA_RESUME_V1"
RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def maintenance_quota_resume_due(root, authorization, *, task, sources,
        expected_head, base_run_id, attempt_id, attempts, quota_evidence,
        quota_attempt):
    """Revalidate real custody; consume at most one existing slot after reset.

    A retained intent is idempotent only for the original blocked attempt. A
    second quota response never receives another reset-based slot in this batch.
    Unknown/future reset remains observational waiting, without global mutation.
    """
    root = Path(root).resolve(strict=True)
    job = maintenance.validate_maintenance_authorization(root, authorization,
        task=task, sources=sources, source_head=expected_head)
    need = maintenance.need
    need(type(base_run_id) is str and RUN.fullmatch(base_run_id)
         and re.fullmatch(re.escape(authorization["job_id"]) + r"-(?:reviewer-[0-2]|v[0-2]r[12])", base_run_id)
         and type(attempts) in (list, tuple)
         and tuple(attempts) == (base_run_id, base_run_id + "-retry-1", base_run_id + "-retry-2")
         and attempt_id in attempts, "MAINTENANCE_QUOTA_ATTEMPT_NAMESPACE")
    index = attempts.index(attempt_id)
    if index == len(attempts) - 1:
        return False
    need(type(quota_attempt) is dict
         and set(quota_attempt) == {"role", "run_id", "status_sha256"}
         and quota_attempt["role"] == "verifier" and quota_attempt["run_id"] == attempt_id,
         "MAINTENANCE_QUOTA_ROLE")
    relative = Path(".local/two-site-refactor/proposals") / attempt_id
    status_raw = maintenance.read(root, relative / "status.json", 160000, private=True)
    need(_digest(status_raw) == quota_attempt["status_sha256"], "MAINTENANCE_QUOTA_STATUS_DRIFT")
    status = maintenance.decode(status_raw)
    need(status.get("state") == "BLOCKED" and status.get("role") == "verifier"
         and status.get("head") == expected_head and status.get("run_id") == attempt_id
         and status.get("task_id") == task["task_id"]
         and status.get("controller_run_id") == job["controller_run_id"]
         and status.get("quota_evidence") == quota_evidence
         and not any(os.path.lexists(root / relative / n)
                     for n in ("result.json", "transport-completion.json", "delta-result.json", "proposal.patch",
                               "generation-completion-proof.json", "completion-proof.json")),
         "MAINTENANCE_QUOTA_NOT_ZERO_EFFECT_FAILURE")
    from scripts.two_site_ai_config import role_settings
    model = role_settings(root, "reviewer")["model"]
    need(status.get("model") == model, "MAINTENANCE_QUOTA_MODEL_DRIFT")
    from scripts.two_site_proposal_runner import read_task_snapshot
    snapshot_raw, snapshot = read_task_snapshot(root, root / relative / "task.json")
    need(snapshot.get("task") == task and snapshot.get("sources") == sources
         and snapshot.get("source_head") == expected_head
         and snapshot.get("maintenance_authorization") == authorization,
         "MAINTENANCE_QUOTA_TASK_DRIFT")
    started = status.get("started_at")
    duration = status.get("duration_seconds")
    need(type(started) in (int, float) and math.isfinite(started) and started > 0
         and type(duration) in (int, float) and math.isfinite(duration) and duration >= 0,
         "MAINTENANCE_QUOTA_TERMINAL_TIME")
    raw = maintenance.read(root, relative / "stream.jsonl", 131072, private=True)
    # Classify at the original invocation time, not after its known reset.
    evidence = classify_gemini_quota_stream(raw.decode("utf-8"), model,
        datetime.fromtimestamp(started, timezone.utc))
    need(evidence == quota_evidence, "MAINTENANCE_QUOTA_STREAM_DRIFT")
    need(status.get("conversation_id") in (None, evidence["conversation_id"]),
         "MAINTENANCE_QUOTA_SESSION_DRIFT")
    outputs = status.get("failure_retained_outputs")
    need(type(outputs) is dict, "MAINTENANCE_QUOTA_CAPTURE_CUSTODY")
    need(outputs.get("stream.jsonl") == {"utf8_bytes": len(raw), "sha256": _digest(raw)},
         "MAINTENANCE_QUOTA_CAPTURE_CUSTODY")
    stderr_path = root / relative / "stderr.txt"
    # The normal reader rejects empty files, so verify the empty stderr inode
    # with the existing Runner's bounded link-safe reader instead.
    from scripts.two_site_proposal_runner import bounded_file_bytes
    stderr = bounded_file_bytes(root, stderr_path, 131072, "quota stderr")
    need(stderr == b"" and outputs.get("stderr.txt") == {"utf8_bytes": 0, "sha256": _digest(b"")},
         "MAINTENANCE_QUOTA_DIAGNOSTIC")
    if evidence["kind"] != "QUOTA_RESET_KNOWN":
        return False
    reset = datetime.fromisoformat(evidence["reset_at_utc"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) < reset + timedelta(seconds=30):
        return False
    value = {"schema": SCHEMA, "authorization": authorization,
        "source_head": expected_head, "task_snapshot_sha256": _digest(snapshot_raw),
        "base_run_id": base_run_id, "quota_attempt": quota_attempt,
        "quota_evidence": evidence, "next_run_id": attempts[index + 1],
        "effect": "ONE_EXISTING_REVIEWER_SLOT_NO_AUTHOR_REPLAY_OR_COUNTER_RESET"}
    relative_intent = maintenance.JOBS_REL / authorization["job_id"] / (
        "quota-resume-" + _digest(base_run_id.encode()) + ".json")
    if os.path.lexists(root / relative_intent):
        retained = maintenance.decode(maintenance.read(root, relative_intent, 160000, private=True))
        if retained != value:
            return False
    else:
        # A different existing run cannot be adopted as the newly authorized
        # session. Subsequent replay of this exact intent may inspect that run.
        need(not os.path.lexists(root / relative.parent / value["next_run_id"]),
             "MAINTENANCE_QUOTA_NEXT_SLOT_ALREADY_EXISTS")
        maintenance.write_new(root, relative_intent, value)
    return True
