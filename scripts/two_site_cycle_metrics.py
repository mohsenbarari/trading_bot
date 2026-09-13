"""Small bounded telemetry, not an acceptance gate or billing statement."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

try:
    from scripts.two_site_proposal_runner import (
        ProposalError, bounded_file_text, file_fingerprint, sha, unique_json,
    )
except ModuleNotFoundError:
    from two_site_proposal_runner import (
        ProposalError, bounded_file_text, file_fingerprint, sha, unique_json,
    )

MAX_STREAM = 16_000_000
MAX_LINE = 1_000_000
MAX_RECORDS = 2000
MAX_LEDGER = 2_000_000


def usage_from_stream(root: Path, path: Path) -> dict:
    """Read closed JSONL with bounded readline; never retain model/command text."""
    missing = {"input_tokens": None, "cached_input_tokens": None,
               "uncached_input_tokens": None, "output_tokens": None}
    if path.resolve() != path or not path.is_relative_to(root):
        raise ProposalError("unsafe metrics stream path")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_STREAM:
            raise ProposalError("unsafe or oversized metrics stream")
        totals = dict.fromkeys(missing, 0)
        turns = commands = count = reasoning = 0
        reasoning_known = True
        while True:
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            count += len(line)
            if len(line) > MAX_LINE or count > MAX_STREAM or not line.endswith(b"\n"):
                raise ProposalError("oversized or incomplete metrics line")
            item = unique_json(line.decode("utf-8"))
            if not isinstance(item, dict):
                raise ProposalError("invalid metrics event")
            if item.get("type") == "item.completed" and isinstance(item.get("item"), dict):
                commands += item["item"].get("type") == "command_execution"
            if item.get("type") != "turn.completed":
                continue
            usage = item.get("usage")
            if not isinstance(usage, dict):
                raise ProposalError("missing completed-turn usage")
            values = [usage.get(k) for k in ("input_tokens", "cached_input_tokens", "output_tokens")]
            if any(type(v) is not int or v < 0 for v in values) or values[1] > values[0]:
                raise ProposalError("invalid completed-turn usage")
            total, cached, output = values
            reasoning_value = usage.get("reasoning_output_tokens")
            if reasoning_value is None:
                reasoning_known = False
            elif type(reasoning_value) is not int or not 0 <= reasoning_value <= output:
                raise ProposalError("invalid reasoning usage")
            else:
                reasoning += reasoning_value
            totals["input_tokens"] += total
            totals["cached_input_tokens"] += cached
            totals["uncached_input_tokens"] += total - cached
            totals["output_tokens"] += output
            turns += 1
        after = os.fstat(stream.fileno())
        if (file_fingerprint(before) != file_fingerprint(after)
                or file_fingerprint(after) != file_fingerprint(path.stat(follow_symlinks=False))
                or path.resolve() != path):
            raise ProposalError("metrics stream changed during read")
    return {"usage_state": "OBSERVED_COMPLETED_TURNS" if turns else "UNKNOWN",
            **(totals if turns else missing), "completed_turns": turns,
            "reasoning_output_tokens": reasoning if turns and reasoning_known else None,
            "completed_commands": commands, "stream_bytes": count}


def atomic_json(root: Path, path: Path, value: object, limit: int) -> None:
    """Replace one validated local regular file, never follow a target alias."""
    payload = (json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n").encode()
    if len(payload) > limit or path.resolve() != path or not path.is_relative_to(root):
        raise ProposalError("unsafe or oversized metrics output")
    if os.path.lexists(path):
        bounded_file_text(root, path, limit, "existing metrics")
    fd, name = tempfile.mkstemp(prefix=".metrics-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record_cycle(root: Path, state_root: Path, run_dir: Path, meta: dict,
                 model: str, effort: str) -> dict:
    """Called under supervisor lock before pruning, on both success and failure."""
    if state_root != root / ".local/two-site-refactor/autopilot" or run_dir.parent != state_root / "runs":
        raise ProposalError("unexpected metrics destination")
    try:
        usage = usage_from_stream(root, run_dir / "stream.jsonl")
    except (OSError, ValueError, UnicodeError) as exc:
        usage = {"usage_state": "UNKNOWN", "diagnostic": type(exc).__name__,
                 "input_tokens": None, "cached_input_tokens": None,
                 "uncached_input_tokens": None, "output_tokens": None}
    record = {"run_id": run_dir.name, "model": model, "effort": effort,
              "state": meta["state"], "head_before": meta.get("head_before"),
              "head_after": meta.get("head_after"), "finished_at": meta.get("finished_at"),
              "duration_seconds": meta.get("duration_seconds"), **usage}
    prompt = bounded_file_text(root, run_dir / "prompt.txt", 200_000, "metrics prompt")
    record["prompt_sha256"] = sha(prompt.encode())
    # IDs and hashes only: no free-form prompts, summaries, source or command output.
    for key in ("gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256"):
        value = meta.get(key)
        if isinstance(value, str) and len(value) <= 80:
            record[key] = value
    worker_id = record.get("gemini_worker_run_id")
    record["declared_requirement_count"] = None
    record["focused_selector_count"] = None
    record["provider_metrics"] = {"state": "UNAVAILABLE", "first_pass": None}
    if isinstance(worker_id, str) and all(c.isalnum() or c in "._-" for c in worker_id):
        try:
            snapshot = unique_json(bounded_file_text(
                root, root / ".local/two-site-refactor/proposals" / worker_id / "task.json",
                320_000, "metrics task snapshot"))
            task = snapshot["task"]
            if snapshot["source_head"] != meta.get("head_before"):
                raise ProposalError("metrics source mismatch")
            task_id = task["task_id"]
            if not isinstance(task_id, str) or len(task_id) > 80:
                raise ProposalError("metrics task ID invalid")
            record["task_id"] = task_id
            record["task_sha256"] = sha(json.dumps(task, sort_keys=True).encode())
            for source_key, metric_key in (("requirements", "declared_requirement_count"),
                                           ("test_selectors", "focused_selector_count")):
                values = task.get(source_key)
                if isinstance(values, list) and all(isinstance(v, str) for v in values):
                    record[metric_key] = len(values)  # declarations, not accepted behavior count
            try:
                try:
                    from scripts.two_site_provider_metrics import provider_metrics
                except ModuleNotFoundError:
                    from two_site_provider_metrics import provider_metrics
                record["provider_metrics"] = provider_metrics(
                    root, task_id, meta.get("head_before"), worker_id, meta.get("duration_seconds"))
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
                record["provider_metrics"] = {"state": "UNAVAILABLE", "first_pass": None,
                                              "diagnostic": type(exc).__name__}
            marker = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
            if marker in task["goal"]:
                gap = unique_json(task["goal"].split(marker, 1)[1])["gap_reference"]
                if not isinstance(gap, str) or len(gap) > 500:
                    raise ProposalError("metrics gap reference invalid")
                record["gap_reference"] = gap
        except (OSError, ValueError, KeyError, TypeError):
            record["task_metadata_state"] = "UNAVAILABLE"
    atomic_json(root, run_dir / "metrics.json", record, 8000)
    ledger_path = state_root / "metrics.json"
    ledger = []
    if os.path.lexists(ledger_path):
        ledger = unique_json(bounded_file_text(root, ledger_path, MAX_LEDGER, "metrics ledger"))
        if not isinstance(ledger, list) or any(not isinstance(x, dict) for x in ledger):
            raise ProposalError("invalid metrics ledger")
    ledger = [x for x in ledger if x.get("run_id") != record["run_id"]]
    ledger = (ledger + [record])[-MAX_RECORDS:]
    while len(json.dumps(ledger, ensure_ascii=True, sort_keys=True).encode()) + 1 > MAX_LEDGER:
        ledger.pop(0)
    atomic_json(root, ledger_path, ledger, MAX_LEDGER)
    return record
