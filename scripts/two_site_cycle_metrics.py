"""Small bounded telemetry, not an acceptance gate or billing statement."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
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
CONTROLLER_PHASE_KEYS = (
    "repository_validation",
    "task_context",
    "codex_readiness",
    "coordinator",
    "post_coordinator_validation",
)
COMMAND_GROUPS = (
    "startup_orientation",
    "task_discovery",
    "source_inspection",
    "task_preflight",
    "provider",
    "candidate_validation",
    "packet_review",
    "git_verification",
    "other",
)
_SKIPPABLE_OVERSIZED_EVENT = re.compile(
    rb'"type"\s*:\s*"item\.completed".{0,4096}?"type"\s*:\s*"command_execution"',
    re.DOTALL,
)
_TERMINAL_STATES = frozenset({"FAILED", "BLOCKED", "STOP", "ABORTED", "CHANGES_REQUIRED"})
_HEX_40_RE = re.compile(r"[a-f0-9]{40}")
_HEX_64_RE = re.compile(r"[a-f0-9]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")


def _is_gemini_worker_run_id(value: object) -> bool:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        return False
    return "worker" in value and "reviewer" not in value.lower()


def _is_gemini_reviewer_run_id(value: object) -> bool:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        return False
    return "reviewer" in value and "worker" not in value.lower()


def _codex_observed_usage(record: object) -> dict[str, int] | None:
    if not isinstance(record, dict):
        return None
    if record.get("usage_state") != "OBSERVED_COMPLETED_TURNS":
        return None
    keys = ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens")
    extracted: dict[str, int] = {}
    for key in keys:
        val = record.get(key)
        if type(val) is not int or isinstance(val, bool) or val < 0:
            return None
        extracted[key] = val
    if extracted["input_tokens"] != extracted["cached_input_tokens"] + extracted["uncached_input_tokens"]:
        return None
    return {
        "input_tokens": extracted["input_tokens"],
        "cached_input_tokens": extracted["cached_input_tokens"],
        "uncached_input_tokens": extracted["uncached_input_tokens"],
        "output_tokens": extracted["output_tokens"],
    }


def _provider_observed_usage(record: object) -> dict[str, int] | None:
    if not isinstance(record, dict):
        return None
    provider_metrics = record.get("provider_metrics")
    if not isinstance(provider_metrics, dict):
        return None
    usage = provider_metrics.get("usage")
    if not isinstance(usage, dict):
        return None
    if set(usage.keys()) != {"input_tokens", "output_tokens", "total_tokens"}:
        return None
    inp = usage["input_tokens"]
    out = usage["output_tokens"]
    tot = usage["total_tokens"]
    for val in (inp, out, tot):
        if type(val) is not int or isinstance(val, bool) or val < 0:
            return None
    if tot != inp + out:
        return None
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": tot,
    }


def _aggregate_observed_usage(
    records: list,
    canonical_fields: tuple[str, ...],
    extractor: object,
) -> dict:
    if not isinstance(records, list) or not isinstance(canonical_fields, tuple):
        raise TypeError("records must be a list and canonical_fields must be a tuple")
    if not callable(extractor):
        raise TypeError("extractor must be callable")
    subtotals: dict[str, int] = {field: 0 for field in canonical_fields}
    observed_count = 0
    unknown_count = 0
    for record in records:
        usage = extractor(record)
        if (
            isinstance(usage, dict)
            and all(
                k in usage
                and type(usage[k]) is int
                and not isinstance(usage[k], bool)
                and usage[k] >= 0
                for k in canonical_fields
            )
        ):
            observed_count += 1
            for field in canonical_fields:
                subtotals[field] += usage[field]
        else:
            unknown_count += 1
    return {
        "observed_record_count": observed_count,
        "unknown_record_count": unknown_count,
        "observed_subtotals": subtotals,
    }


def command_group(value: object) -> str:
    """Classify a command in memory; retain only one fixed aggregate label.

    Command text and command output may contain source or operational details, so
    neither belongs in durable telemetry.  These groups are intentionally broad:
    they identify repeated coordination work without becoming a source channel.
    """
    if not isinstance(value, str):
        return "other"
    lowered = value.lower()
    if any(token in lowered for token in (
        "agents.md", "docs/memory/", "two-site-refactor/skill.md",
        "proposal_automation.md", "master_plan.md", "execution_plan.md",
    )):
        return "startup_orientation"
    if any(token in lowered for token in (
        "recent-characterization", "scenario-records", "surface-bot",
        "execution-order", "change_ledger",
    )):
        return "task_discovery"
    if "two_site_cycle_tools.py build" in lowered or "two_site_proposal_runner.py check" in lowered:
        return "task_preflight"
    if "two_site_provider_broker.py submit" in lowered:
        return "provider"
    if "two_site_cycle_packet.py" in lowered or "two_site_cycle_tools.py review" in lowered:
        return "packet_review"
    if "pytest" in lowered or "two_site_pre_review_receipt.py" in lowered:
        return "candidate_validation"
    if re.search(r"(?:^|[ /;&|])git(?:[ /;&|]|$)", lowered):
        return "git_verification"
    if any(token in lowered for token in ("bot/", "api/", "core/", "models/", "tests/", "src/")):
        return "source_inspection"
    return "other"


def controller_phase_seconds(meta: object) -> dict[str, float] | None:
    """Return a complete, source-free controller timing breakdown when observed."""
    if not isinstance(meta, dict):
        return None
    raw = meta.get("controller_phase_seconds")
    if not isinstance(raw, dict) or set(raw) != set(CONTROLLER_PHASE_KEYS):
        return None
    normalized: dict[str, float] = {}
    for key in CONTROLLER_PHASE_KEYS:
        value = raw[key]
        if (type(value) not in (int, float) or isinstance(value, bool)
                or not math.isfinite(value) or not 0 <= value <= 10**12):
            return None
        normalized[key] = round(float(value), 3)
    return normalized


def _drain_oversized_event(stream, first: bytes, total: int) -> tuple[int, bool]:
    """Discard one known large command record without buffering its output.

    Codex event streams can contain a command result larger than MAX_LINE while a
    later, small turn.completed record still contains valid usage.  The command
    body is not telemetry and must not make that later accounting disappear.
    Only the well-known command-completion envelope is skippable; all other
    oversized or unterminated records remain invalid.
    """
    if not _SKIPPABLE_OVERSIZED_EVENT.search(first[:8192]):
        return total, False
    chunk = first
    while not chunk.endswith(b"\n"):
        chunk = stream.readline(MAX_LINE + 1)
        if not chunk:
            return total, False
        total += len(chunk)
        if total > MAX_STREAM:
            raise ProposalError("oversized metrics stream")
    return total, True


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
        command_groups = dict.fromkeys(COMMAND_GROUPS, 0)
        turns = commands = count = reasoning = skipped_oversized_events = 0
        reasoning_known = True
        while True:
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            count += len(line)
            if count > MAX_STREAM:
                raise ProposalError("oversized metrics stream")
            if len(line) > MAX_LINE:
                count, skipped = _drain_oversized_event(stream, line, count)
                if not skipped:
                    raise ProposalError("oversized or incomplete metrics line")
                skipped_oversized_events += 1
                continue
            if not line.endswith(b"\n"):
                raise ProposalError("oversized or incomplete metrics line")
            item = unique_json(line.decode("utf-8"))
            if not isinstance(item, dict):
                raise ProposalError("invalid metrics event")
            if item.get("type") == "item.completed" and isinstance(item.get("item"), dict):
                command = item["item"]
                if command.get("type") == "command_execution":
                    commands += 1
                    command_groups[command_group(command.get("command"))] += 1
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
            "completed_commands": commands, "command_groups": command_groups,
            "stream_bytes": count,
            "skipped_oversized_events": skipped_oversized_events}


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


def _call_provider_metrics(*args, **kwargs) -> dict:
    try:
        from scripts.two_site_provider_metrics import provider_metrics
    except ModuleNotFoundError:
        from two_site_provider_metrics import provider_metrics
    return provider_metrics(*args, **kwargs)


def _unavailable_provider_metrics(diagnostic: str | None = None) -> dict:
    """Keep failed telemetry structurally honest without retaining exception text."""
    result = {
        "state": "UNAVAILABLE",
        "first_pass": None,
        "rework_taxonomy": {
            "state": "UNAVAILABLE",
            "initial_outcome": "UNKNOWN",
            "rework_path": "UNKNOWN",
            "terminal_outcome": "UNKNOWN",
        },
    }
    if diagnostic is not None:
        result["diagnostic"] = diagnostic
    return result


def _validated_task_metadata(meta: object) -> dict | None:
    if not isinstance(meta, dict):
        return None
    if set(meta.keys()) != {
        "task_id", "task_sha256", "declared_requirement_count",
        "focused_selector_count", "gap_reference",
    }:
        return None
    task_id = meta["task_id"]
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", task_id):
        return None
    task_sha = meta["task_sha256"]
    if not isinstance(task_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", task_sha):
        return None
    req_count = meta["declared_requirement_count"]
    if type(req_count) is not int or not 0 <= req_count <= 100_000:
        return None
    sel_count = meta["focused_selector_count"]
    if type(sel_count) is not int or not 0 <= sel_count <= 100_000:
        return None
    gap_ref = meta["gap_reference"]
    if (not isinstance(gap_ref, str) or not (1 <= len(gap_ref) <= 500)
            or any(c in gap_ref for c in ("\n", "\r", "\0"))):
        return None
    return {
        "task_id": task_id,
        "task_sha256": task_sha,
        "declared_requirement_count": req_count,
        "focused_selector_count": sel_count,
        "gap_reference": gap_ref,
    }


def semantic_identity(value: object) -> dict[str, str] | None:
    """Validate and project semantic identity telemetry."""
    if not isinstance(value, dict):
        return None
    if set(value.keys()) != {
        "stage_id", "scenario_key", "gap_reference", "source_sha256", "fingerprint",
    }:
        return None
    stage_id = value["stage_id"]
    if not isinstance(stage_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", stage_id):
        return None
    scenario_key = value["scenario_key"]
    if not isinstance(scenario_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", scenario_key):
        return None
    source_sha = value["source_sha256"]
    if not isinstance(source_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", source_sha):
        return None
    fingerprint = value["fingerprint"]
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        return None
    gap_ref = value["gap_reference"]
    if not isinstance(gap_ref, str) or gap_ref != gap_ref.strip():
        return None
    try:
        raw_gap = gap_ref.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if not (1 <= len(raw_gap) <= 500):
        return None
    if any(ord(c) < 32 or ord(c) == 127 or (0x80 <= ord(c) <= 0x9F) or c == "\\" for c in gap_ref):
        return None
    if gap_ref.count("#") != 1:
        return None
    path_part, anchor = gap_ref.split("#", 1)
    if not path_part or not anchor:
        return None
    if path_part.startswith("/") or bool(re.match(r"^[A-Za-z]:", path_part)):
        return None
    segments = path_part.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return None
    return {
        "stage_id": stage_id,
        "scenario_key": scenario_key,
        "gap_reference": gap_ref,
        "source_sha256": source_sha,
        "fingerprint": fingerprint,
    }


_COMPLETE_PROVIDER_STATES = frozenset({"OBSERVED_CONTROLLER_ATTEMPTS", "OBSERVED_MATCHING_ATTEMPTS"})


def work_lineage(value: object) -> dict[str, object] | None:
    """Validate and project work lineage telemetry."""
    if not isinstance(value, dict):
        return None
    if set(value.keys()) != {"schema_version", "fingerprint"}:
        return None
    schema_version = value["schema_version"]
    if type(schema_version) is not int or isinstance(schema_version, bool) or schema_version != 1:
        return None
    fingerprint = value["fingerprint"]
    if not isinstance(fingerprint, str) or not _HEX_64_RE.fullmatch(fingerprint):
        return None
    return {
        "schema_version": schema_version,
        "fingerprint": fingerprint,
    }


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
    phases = controller_phase_seconds(meta)
    if phases is not None:
        record["controller_phase_seconds"] = phases
    prompt = bounded_file_text(root, run_dir / "prompt.txt", 200_000, "metrics prompt")
    record["prompt_sha256"] = sha(prompt.encode())
    # IDs and hashes only: no free-form prompts, summaries, source or command output.
    for key in ("gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256"):
        value = meta.get(key)
        if isinstance(value, str) and len(value) <= 80:
            record[key] = value
    worker_id = record.get("gemini_worker_run_id")
    valid_worker_id = (
        worker_id
        if isinstance(worker_id, str) and bool(re.fullmatch(r"[A-Za-z0-9._-]{1,80}", worker_id))
        else None
    )
    raw_duration = meta.get("duration_seconds")
    cycle_duration = (
        float(raw_duration)
        if isinstance(raw_duration, (int, float)) and not isinstance(raw_duration, bool)
        and 0 <= raw_duration <= 10**12 and math.isfinite(raw_duration)
        else 0.0
    )
    head_before = meta.get("head_before")
    head_before_str = head_before if isinstance(head_before, str) else ""

    record["declared_requirement_count"] = None
    record["focused_selector_count"] = None
    record["provider_metrics"] = _unavailable_provider_metrics()

    try:
        prov = _call_provider_metrics(
            root, None, head_before_str, valid_worker_id, cycle_duration, run_dir.name
        )
        if isinstance(prov, dict):
            record["provider_metrics"] = prov
            trusted_meta = _validated_task_metadata(prov.get("task_metadata"))
            if trusted_meta is not None:
                record.update(trusted_meta)
            identity = semantic_identity(prov.get("semantic_identity"))
            if identity is not None:
                record["semantic_identity"] = identity
            if prov.get("state") in _COMPLETE_PROVIDER_STATES and identity is not None:
                work = work_lineage(prov.get("work_lineage"))
                if work is not None:
                    record["work_lineage"] = work
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        record["provider_metrics"] = _unavailable_provider_metrics(type(exc).__name__)

    if record["provider_metrics"].get("state") == "UNAVAILABLE" and valid_worker_id is not None:
        try:
            snapshot = unique_json(bounded_file_text(
                root, root / ".local/two-site-refactor/proposals" / valid_worker_id / "task.json",
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
                fallback_prov = _call_provider_metrics(
                    root, task_id, meta.get("head_before"), valid_worker_id, meta.get("duration_seconds"))
                record["provider_metrics"] = fallback_prov
                if isinstance(fallback_prov, dict):
                    fallback_ident = semantic_identity(fallback_prov.get("semantic_identity"))
                    if fallback_ident is not None:
                        record["semantic_identity"] = fallback_ident
                    else:
                        record.pop("semantic_identity", None)
                    if fallback_prov.get("state") in _COMPLETE_PROVIDER_STATES and fallback_ident is not None:
                        fallback_work = work_lineage(fallback_prov.get("work_lineage"))
                        if fallback_work is not None:
                            record["work_lineage"] = fallback_work
                        else:
                            record.pop("work_lineage", None)
                    else:
                        record.pop("work_lineage", None)
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
                record["provider_metrics"] = _unavailable_provider_metrics(type(exc).__name__)
                record.pop("semantic_identity", None)
                record.pop("work_lineage", None)
            marker = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
            if marker in task["goal"]:
                gap = unique_json(task["goal"].split(marker, 1)[1])["gap_reference"]
                if not isinstance(gap, str) or len(gap) > 500:
                    raise ProposalError("metrics gap reference invalid")
                record["gap_reference"] = gap
        except (OSError, ValueError, KeyError, TypeError):
            record["task_metadata_state"] = "UNAVAILABLE"
            record.pop("semantic_identity", None)
            record.pop("work_lineage", None)
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


def summarize_efficiency_ledger(records: object) -> dict:
    """Return bounded pure efficiency ledger summary."""
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ProposalError("invalid efficiency ledger records")

    accepted_count = 0
    duplicate_count = 0
    non_accepted_count = 0
    unknown_count = 0

    accepted_lineages: dict[str, dict[str, str]] = {}

    for item in records:
        if not isinstance(item, dict):
            unknown_count += 1
            continue
        state = item.get("state")
        if not isinstance(state, str):
            unknown_count += 1
            continue
        if state == "CONTINUE":
            head_before = item.get("head_before")
            head_after = item.get("head_after")
            worker_id = item.get("gemini_worker_run_id")
            reviewer_id = item.get("gemini_reviewer_run_id")
            proposal_sha = item.get("proposal_sha256")
            ident = semantic_identity(item.get("semantic_identity"))

            is_valid_continue = (
                isinstance(head_before, str)
                and isinstance(head_after, str)
                and head_before != head_after
                and bool(_HEX_40_RE.fullmatch(head_before))
                and bool(_HEX_40_RE.fullmatch(head_after))
                and isinstance(worker_id, str)
                and isinstance(reviewer_id, str)
                and worker_id != reviewer_id
                and _is_gemini_worker_run_id(worker_id)
                and _is_gemini_reviewer_run_id(reviewer_id)
                and isinstance(proposal_sha, str)
                and bool(_HEX_64_RE.fullmatch(proposal_sha))
                and ident is not None
            )

            if is_valid_continue:
                assert ident is not None
                fp = ident["fingerprint"]
                if fp not in accepted_lineages:
                    accepted_lineages[fp] = ident
                    accepted_count += 1
                else:
                    duplicate_count += 1
            else:
                unknown_count += 1
        elif state in _TERMINAL_STATES:
            non_accepted_count += 1
        else:
            unknown_count += 1

    lineage_records: dict[str, list[dict]] = {fp: [] for fp in accepted_lineages}
    for item in records:
        if not isinstance(item, dict):
            continue
        ident = semantic_identity(item.get("semantic_identity"))
        if ident is None:
            continue
        fp = ident["fingerprint"]
        if fp in accepted_lineages and ident == accepted_lineages[fp]:
            lineage_records[fp].append(item)

    codex_fields = (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
    )
    provider_fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
    )

    lineages = []
    for fp in sorted(accepted_lineages.keys()):
        group = lineage_records[fp]
        lineages.append({
            "fingerprint": fp,
            "record_count": len(group),
            "codex": _aggregate_observed_usage(group, codex_fields, _codex_observed_usage),
            "provider": _aggregate_observed_usage(group, provider_fields, _provider_observed_usage),
        })

    return {
        "accepted_count": accepted_count,
        "duplicate_count": duplicate_count,
        "non_accepted_count": non_accepted_count,
        "unknown_count": unknown_count,
        "codex": _aggregate_observed_usage(records, codex_fields, _codex_observed_usage),
        "provider": _aggregate_observed_usage(records, provider_fields, _provider_observed_usage),
        "lineages": lineages,
    }
