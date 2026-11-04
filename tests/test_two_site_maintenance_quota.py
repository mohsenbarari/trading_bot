"""Quota continuation mechanics; these unit fixtures confer no live authority."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from scripts import two_site_maintenance_quota as quota
from scripts import two_site_qoder_maintenance as maintenance
from scripts import two_site_ai_config as config
from scripts import two_site_proposal_runner as runner


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if type(value) is bytes else (json.dumps(value) + "\n").encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


@pytest.fixture
def fx(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    reset = (now - timedelta(minutes=5)).replace(microsecond=0)
    started = reset - timedelta(minutes=5)
    model = "gemini-synthetic-reviewer"
    auth = {"kind": maintenance.KIND, "job_id": "maint-" + "a" * 32, "sha256": "b" * 64}
    task = {"task_id": "explicit-unit-fixture"}
    sources = {"file.py": {"content": "x=1\n", "sha256": "c" * 64}}
    head = "d" * 40
    base = auth["job_id"] + "-reviewer-0"
    attempts = (base, base + "-retry-1", base + "-retry-2")
    directory = tmp_path / ".local/two-site-refactor/proposals" / base
    init = {"event": "init", "conversation_id": "synthetic-quota-only", "init": {"model": model}}
    error = {"event": "result", "result": {"conversation_id": init["conversation_id"],
        "status": "ERROR", "usage": {"input_tokens": 0, "output_tokens": 0},
        "error": "RESOURCE_EXHAUSTED quota reset at " + reset.strftime("%Y-%m-%dT%H:%M:%SZ")}}
    raw = save(directory / "stream.jsonl", (json.dumps(init) + "\n" + json.dumps(error) + "\n").encode())
    evidence = quota.classify_gemini_quota_stream(raw.decode(), model, started)
    assert evidence["kind"] == "QUOTA_RESET_KNOWN"
    save(directory / "stderr.txt", b"")
    snapshot = {"task": task, "sources": sources, "source_head": head, "maintenance_authorization": auth}
    save(directory / "task.json", snapshot)
    status = {"state": "BLOCKED", "role": "verifier", "head": head, "run_id": base,
        "controller_run_id": "synthetic-maintenance-controller",
        "task_id": task["task_id"], "model": model, "started_at": started.timestamp(),
        "duration_seconds": 1, "quota_evidence": evidence,
        "failure_retained_outputs": {"stream.jsonl": {"utf8_bytes": len(raw), "sha256": quota._digest(raw)},
            "stderr.txt": {"utf8_bytes": 0, "sha256": quota._digest(b"")}}}
    status_raw = save(directory / "status.json", status)
    (tmp_path / maintenance.JOBS_REL / auth["job_id"]).mkdir(parents=True)
    # Scope/job authorization is exercised by the real maintenance integration
    # suite; this fixture isolates the due/capture/finite-slot decision.
    authorization_calls = []
    def authorize(*a, **k):
        authorization_calls.append((a, k))
        return {"controller_run_id": "synthetic-maintenance-controller"}
    monkeypatch.setattr(maintenance, "validate_maintenance_authorization", authorize)
    monkeypatch.setattr(config, "role_settings", lambda *_: {"model": model})
    monkeypatch.setattr(runner, "read_task_snapshot", lambda _r, p: (p.read_bytes(), json.loads(p.read_bytes())))
    kwargs = dict(task=task, sources=sources, expected_head=head, base_run_id=base,
        attempt_id=base, attempts=attempts, quota_evidence=evidence,
        quota_attempt={"role": "verifier", "run_id": base, "status_sha256": quota._digest(status_raw)})
    return tmp_path, auth, kwargs, directory, status, authorization_calls


def run(fx):
    root, auth, kwargs, *_ = fx
    return quota.maintenance_quota_resume_due(root, auth, **kwargs)


def test_due_real_zero_effect_stream_consumes_one_idempotent_slot_without_changing_originals(fx):
    root, auth, kwargs, directory, _, calls = fx
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert run(fx) is True
    receipt = list((root / maintenance.JOBS_REL / auth["job_id"]).glob("quota-resume-*.json"))
    assert len(receipt) == 1
    frozen = receipt[0].read_bytes()
    assert run(fx) is True and receipt[0].read_bytes() == frozen
    assert len(calls) == 2 and calls[0][1]["source_head"] == kwargs["expected_head"]
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == original
    assert not (directory.parent / kwargs["attempts"][1]).exists()


def test_due_intent_never_authorizes_a_second_quota_response_or_new_run_namespace(fx):
    root, auth, kwargs, directory, status, _ = fx
    assert run(fx)
    second = directory.parent / kwargs["attempts"][1]
    for p in directory.iterdir():
        save(second / p.name, p.read_bytes())
    status["run_id"] = second.name
    raw = save(second / "status.json", status)
    kwargs.update(attempt_id=second.name,
        quota_attempt={"role": "verifier", "run_id": second.name, "status_sha256": quota._digest(raw)})
    assert run(fx) is False
    assert not (directory.parent / kwargs["attempts"][2]).exists()


@pytest.mark.parametrize("fault", ["stream", "status", "task", "stderr", "result", "transport-completion.json", "delta-result.json", "controller", "session", "linked-stream", "foreign-next", "terminal-time", "role", "namespace", "auth"])
def test_drift_ambiguity_existing_effect_or_wrong_authority_cannot_authorize(fx, monkeypatch, fault):
    _, _, kwargs, directory, status, _ = fx
    if fault == "stream":
        save(directory / "stream.jsonl", (directory / "stream.jsonl").read_bytes().replace(b"RESOURCE_EXHAUSTED", b"OTHER"))
    elif fault == "status":
        save(directory / "status.json", {})
    elif fault == "task":
        value = json.loads((directory / "task.json").read_bytes());value["source_head"] = "e" * 40
        save(directory / "task.json", value)
    elif fault == "stderr":
        save(directory / "stderr.txt", b"unknown diagnostic")
    elif fault == "result":
        save(directory / "result.json", {})
    elif fault in {"transport-completion.json", "delta-result.json"}:
        save(directory / fault, {})
    elif fault in {"controller", "session"}:
        status["controller_run_id" if fault == "controller" else "conversation_id"] = "foreign-session-or-controller"
        raw = save(directory / "status.json", status)
        kwargs["quota_attempt"]["status_sha256"] = quota._digest(raw)
    elif fault == "linked-stream":
        import os
        os.link(directory / "stream.jsonl", directory.parent / "alias-stream")
    elif fault == "foreign-next":
        (directory.parent / kwargs["attempts"][1]).mkdir()
    elif fault == "terminal-time":
        status["duration_seconds"] = True
        raw = save(directory / "status.json", status)
        kwargs["quota_attempt"]["status_sha256"] = quota._digest(raw)
    elif fault == "role":
        kwargs["quota_attempt"]["role"] = "worker"
    elif fault == "namespace":
        kwargs["attempts"] = (kwargs["attempt_id"], "new-arbitrary-id")
    elif fault == "auth":
        def reject(*_a, **_k):
            raise maintenance.QoderMaintenanceError("STOP_OR_SCOPE_DRIFT")
        monkeypatch.setattr(maintenance, "validate_maintenance_authorization", reject)
    with pytest.raises((maintenance.QoderMaintenanceError, runner.ProposalError)):
        run(fx)


def test_future_known_reset_waits_without_an_intent(fx, monkeypatch):
    root, auth, *_ = fx
    class BeforeReset(datetime):
        @classmethod
        def now(cls, tz):
            return datetime.now(tz) - timedelta(days=1)
    monkeypatch.setattr(quota, "datetime", BeforeReset)
    assert run(fx) is False
    assert not list((root / maintenance.JOBS_REL / auth["job_id"]).glob("quota-resume-*.json"))


def test_last_existing_slot_never_extends_retry_budget(fx):
    _, _, kwargs, *_ = fx
    kwargs["attempt_id"] = kwargs["attempts"][-1]
    assert run(fx) is False


def test_unknown_reset_is_observation_only_even_after_time_passes(fx):
    root, auth, kwargs, directory, status, _ = fx
    raw = (directory / "stream.jsonl").read_bytes()
    event = [json.loads(line) for line in raw.splitlines()]
    event[1]["result"]["error"] = "RESOURCE_EXHAUSTED quota, no reliable reset"
    raw = save(directory / "stream.jsonl", b"\n".join(json.dumps(e).encode() for e in event) + b"\n")
    evidence = quota.classify_gemini_quota_stream(raw.decode(), status["model"],
        datetime.fromtimestamp(status["started_at"], timezone.utc))
    assert evidence["kind"] == "QUOTA_RESET_UNKNOWN"
    status["quota_evidence"] = kwargs["quota_evidence"] = evidence
    status["failure_retained_outputs"]["stream.jsonl"] = {"utf8_bytes": len(raw), "sha256": quota._digest(raw)}
    status_raw = save(directory / "status.json", status)
    kwargs["quota_attempt"]["status_sha256"] = quota._digest(status_raw)
    assert run(fx) is False
    assert not list((root / maintenance.JOBS_REL / auth["job_id"]).glob("quota-resume-*.json"))
