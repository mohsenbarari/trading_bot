"""Synthetic completion artifacts for offline tests, NEVER Provider evidence.

This factory supplies retained bytes and typed test observations so old business
oracles exercise the mandatory production reader. It does not patch that reader
or attest that any real CLI/Provider ran. Negative tests tamper after sealing.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from scripts import two_site_proposal_runner as runner
from scripts.two_site_generation_completion import normal_observations


def install_current_contract(root: Path) -> None:
    for relative in (runner.GEMINI_RUNTIME_CONTRACT_REL,
                     *runner.GEMINI_RUNTIME_REFERENCE_PATHS):
        target = root / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(runner.ROOT / relative, target)


def supported_profile(conversation_id: str) -> dict:
    return {"conversation_id": conversation_id, "name": "two-site-proposal-only",
            "native_tools": 0, "command_policy": "off",
            "actual_executor_checked": True}


def seal_completion_fixture(root: Path, run_dir: Path) -> None:
    """Form an explicitly synthetic, byte-consistent NORMAL_EXIT fixture.

    Do not validate the candidate here: malformed-result tests must reach the
    production consumer. The proof is assembled independently from that reader.
    """
    install_current_contract(root)
    snapshot_bytes = (run_dir / "task.json").read_bytes()
    snapshot = json.loads(snapshot_bytes)
    status = json.loads((run_dir / "status.json").read_bytes())
    result_bytes = (run_dir / "result.json").read_bytes()
    result = json.loads(result_bytes)
    role, task, sources = status["role"], snapshot["task"], snapshot["sources"]
    model = status.setdefault("model", runner.WORKER_MODEL if role == "worker" else runner.MODEL)
    effort = status.setdefault("effort", runner.WORKER_EFFORT if role == "worker" else runner.EFFORT)
    status.setdefault("head", snapshot["source_head"])
    cid = status["conversation_id"]
    active_sources, force_full = sources, ()
    candidate_sha = delta_sha = None
    prior_identity = None
    raw_result = result
    if role == "worker" and "feedback" in snapshot:
        prior = json.loads(snapshot["proposal"])
        active_sources = runner.build_candidate_sources(task["allowed_files"], sources, prior["changes"])
        candidate_sha = runner.sha(runner.serialize_json(active_sources))
        status["candidate_sources_sha256"] = candidate_sha
        force_full = runner.originally_new_paths(task, sources)
        # The persisted result is canonical against ORIGINAL sources. A raw
        # correction instead describes every candidate-to-desired difference,
        # including a real revert when an earlier changed path is now omitted.
        desired = runner.build_candidate_sources(task["allowed_files"], sources, result["changes"])
        raw_result = dict(result)
        raw_result["changes"] = []
        for path in task["allowed_files"]:
            if desired[path]["content"] == active_sources[path]["content"]:
                continue
            if desired[path]["content"] is None:
                raise AssertionError("synthetic correction cannot represent file deletion")
            raw_result["changes"].append({
                "path": path,
                "original_sha256": active_sources[path]["sha256"],
                "content": desired[path]["content"],
            })
        if not raw_result["changes"]:
            raise AssertionError("a no-progress correction has no successful completion proof")
    if role == "worker" and raw_result.get("status") == "PROPOSAL_READY":
        # Match normalize_result's representation without calling the validator:
        # preserve top-level order, but normalize each change's three fields.
        raw_result = dict(raw_result)
        raw_result["changes"] = [
            {"path": change["path"], "original_sha256": change["original_sha256"],
             "content": change["content"]}
            for change in raw_result["changes"]
        ]
    if role == "worker" and "feedback" in snapshot:
        delta_bytes = runner.serialize_json(raw_result)
        (run_dir / "delta-result.json").write_bytes(delta_bytes)
        delta_sha = runner.sha(delta_bytes)
        status["delta_result_sha256"] = delta_sha
        prior_identity = {"run_id": status["input_worker_run_id"],
                          "conversation_id": status["input_worker_conversation_id"],
                          "proposal_sha256": runner.sha(snapshot["proposal"].encode())}
    prompt = b"OFFLINE SYNTHETIC generation fixture; no Provider invocation.\n"
    (run_dir / "prompt.txt").write_bytes(prompt)
    schema_bytes = runner.serialize_json(runner.schema(
        role, task, active_sources, force_full, original_sources=sources,
    ))
    (run_dir / "response-schema.json").write_bytes(schema_bytes)
    # Canonical normalized changes become a valid full transport with no edits.
    transport = json.loads(json.dumps(raw_result))
    for change in transport.get("changes", []):
        change["replacements"] = []
        old = active_sources.get(change["path"], {}).get("content")
        new = change.get("content")
        if type(old) is str and type(new) is str and len(old.encode()) >= 8192 and old != new:
            # Compact only the changed middle, expanding until its target is unique.
            prefix = 0
            while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
                prefix += 1
            suffix = 0
            while (suffix < min(len(old), len(new)) - prefix
                   and old[-1 - suffix] == new[-1 - suffix]):
                suffix += 1
            start, end = prefix, len(old) - suffix
            while not old[start:end] or old.count(old[start:end]) != 1:
                if start:
                    start -= 1
                elif end < len(old):
                    end += 1
                else:
                    break
            change["content"] = None
            change["replacements"] = [{"old": old[start:end],
                                      "new": new[start:len(new) - (len(old) - end)]}]
    events = [
        {"event": "init", "conversation_id": cid, "init": {"model": model}},
        {"event": "result", "result": {"status": "SUCCESS",
         "conversation_id": cid, "num_turns": 1, "structured_output": transport}},
    ]
    stdout = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
    (run_dir / "stream.jsonl").write_bytes(stdout)
    (run_dir / "stderr.txt").write_bytes(b"")
    wire = runner._cli_stream_input(prompt.decode(), is_compact_review=False)
    observations = normal_observations(0, wire, stdout, b"", 25.0)
    contract_sha = runner.sha((root / runner.GEMINI_RUNTIME_CONTRACT_REL).read_bytes())
    profile = supported_profile(cid)
    cli = {"path": "/offline/synthetic-cli", "version": runner.EXPECTED_CLI_VERSION,
           "sha256": runner.EXPECTED_CLI_SHA256}
    status.update(
        completion_policy="FINAL_FRAME_RECOVERY_V1", runtime_contract_sha256=contract_sha,
        compact_review=False, response_schema_sha256=runner.sha(schema_bytes),
        generation_effort_requested=effort,
        prepared_effort=effort, effort_arg_present=True,
        cli_returncode=0, cli_normal_exit_observed=True,
        generation_completion_outcome="NORMAL_EXIT", runtime_profile=profile, cli=cli,
    )
    proof = {
        "schema_version": "1.0", "kind": "GENERATION_COMPLETION_V1",
        "policy": "FINAL_FRAME_RECOVERY_V1", "runtime_contract_sha256": contract_sha,
        "run_id": status["run_id"], "role": role, "head": status["head"],
        "conversation_id": cid, "model": model, "requested_effort": effort,
        "selected_policy_effort": effort, "prepared_effort": effort,
        "effort_arg_present": True, "runtime_profile": profile, "cli": cli,
        "compact_review": False,
        "task_snapshot_sha256": runner.sha(snapshot_bytes),
        "prompt_sha256": runner.sha(prompt),
        "response_schema_sha256": runner.sha(schema_bytes),
        "raw_result_sha256": runner.sha(runner.serialize_json(raw_result)),
        "candidate_sources_sha256": candidate_sha,
        "prior_worker_identity": prior_identity,
        "delta_result_sha256": delta_sha, "result_sha256": runner.sha(result_bytes),
        "observations": observations, "outer_context_exited_normally": True,
    }
    proof_bytes = runner.serialize_json(proof)
    proof_path = run_dir / "transport-completion.json"
    proof_path.write_bytes(proof_bytes)
    proof_path.chmod(0o600)
    status["completion_proof_sha256"] = runner.sha(proof_bytes)
    runner.write_json(run_dir / "status.json", status)
