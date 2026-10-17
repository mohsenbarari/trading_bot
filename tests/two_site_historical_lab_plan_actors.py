from __future__ import annotations
import copy
import hashlib
import json
from scripts import two_site_proposal_runner as sdk
from scripts.two_site_semantic_task import build_identity
from scripts.two_site_task_context import preparation_notes
from tests import two_site_historical_lab_plan_data as data
def digest(raw):
    return hashlib.sha256(raw).hexdigest()
def prescribe_actors(add, entry, task, sources, proposal, native, count, codex, package):
    """Construct the Subject's finite laboratory actor artifacts."""
    stem = task["task_id"]
    reviewer = stem + "-reviewer-" + str(count)
    controller = "lab-controller-" + stem
    directory = ".local/two-site-refactor/proposals/" + reviewer + "/"
    review = {"status": "VERIFIED", "summary": "LABORATORY independent review envelope", "findings": [],
              "covered_requirements": list(range(1, len(task["requirements"]) + 1)), "changes": []}
    result = add(directory + "result.json", review)
    snapshot = {"task": task, "source_head": data.BEFORE, "sources": sources, "proposal": sdk.serialize_json(proposal).decode(), "test_evidence": native}
    actor = {"kind": "CODEX" if codex else "GEMINI", "controller_run_id": controller,
             "reviewer_snapshot": add(directory + "task.json", snapshot), "reviewer_status": None, "reviewer_result": result}
    status = {"run_id": reviewer, "task_id": stem, "role": "verifier", "state": "VERIFIED", "head": data.BEFORE,
        "model": "gemini-4-argon-high", "effort": "high", "controller_run_id": controller,
        "conversation_id": reviewer + "-session", "result_sha256": result["sha256"],
        "input_test_evidence_sha256": entry["native"]["pre"]["sha256"]}

    def stream(value, model, conversation):
        body = copy.deepcopy(value)
        for change in body["changes"]:
            change["replacements"] = []
        return json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}) + "\n" + json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "denied_actions": [], "structured_output": body}}) + "\n"

    if not codex:
        add(directory + "prompt.txt", "LABORATORY review scaffolding; no Provider call.\n" + sdk.serialize_json(snapshot).decode())
        ancestors, prior = [], None
        for n in range(count + 1):
            run = stem + "-worker-" + str(n)
            path = ".local/two-site-refactor/proposals/" + run + "/"
            value = copy.deepcopy(proposal)
            if n < count:
                value["summary"] = "LABORATORY rejected correction " + str(n)
                value["changes"][-1]["content"] += "# rejected correction " + str(n) + "\n"
            ref = add(path + "result.json", value)
            ws = {"task": task, "source_head": data.BEFORE, "sources": sources}
            st = {"run_id": run, "role": "worker", "state": "PROPOSAL_READY", "head": data.BEFORE,
                "model": "gemini-4-argon-medium", "effort": "medium", "controller_run_id": controller,
                "conversation_id": run + "-session", "result_sha256": ref["sha256"]}
            feedback_ref = None
            if prior is not None:
                feedback = {"reviewer": "test-gate", "reviewed_proposal_sha256": prior[1],
                    "findings": ["LABORATORY correction needed"], "test_evidence": ["disclosed scaffolding"]}
                feedback_ref = add(".local/feedback-" + str(n) + ".json", feedback)
                ws.update(proposal=prior[2], feedback=feedback)
                st.update(input_worker_run_id=prior[0], input_proposal_sha256=prior[1],
                          input_worker_conversation_id=prior[3], input_feedback_sha256=feedback_ref["sha256"])
            worker = {"run_id": run, "snapshot": add(path + "task.json", ws), "status": add(path + "status.json", st), "result": ref, "feedback": feedback_ref}
            add(path + "stream.jsonl", stream(value, st["model"], st["conversation_id"]))
            prior = (run, ref["sha256"], sdk.serialize_json(value).decode(), st["conversation_id"])
            if n < count:
                ancestors.append(worker)
        entry.update(task_snapshot=worker["snapshot"], proposal=worker["result"])
        actor.update(worker_run_id=run, reviewer_run_id=reviewer, worker_status=worker["status"], correction_chain=ancestors)
        status.update(input_worker_run_id=run, input_proposal_sha256=entry["proposal"]["sha256"], input_worker_conversation_id=prior[3])
        root_value = {"schema_version": "1.0", "state": "CONTINUE", "disposition": "NONE", "category": "NONE",
            "summary": "LABORATORY not live Root acceptance", "commit": entry["head_after"], "next_action": "LABORATORY only", "blockers": [],
            "gemini_worker_run_id": run, "gemini_reviewer_run_id": reviewer, "proposal_sha256": entry["proposal"]["sha256"]}
        kind = "CYCLE_RESULT"
    else:
        codex_recipe(add, entry, actor, status, result, task, sources, proposal, native, package, directory)
        root_value, kind = actor.pop("_root"), "ROOT_EXACT_RECEIPT"
    actor["reviewer_status"] = add(directory + "status.json", status)
    sr = add(directory + "stream.jsonl", stream(review, status["model"], status["conversation_id"]))
    if codex:
        actor["reviewer_stream"] = sr
    entry.update(actor=actor, root_acceptance={"kind": kind, "artifact": add(".local/root.json", root_value, 0o644)})


def codex_recipe(add, entry, actor, status, result, task, sources, proposal, native, package, directory):
    original = {p: v["sha256"] for p, v in sources.items()}
    after = {c["path"]: digest(c["content"].encode()) for c in proposal["changes"]}
    blob = hashlib.sha1(b"blob " + str(len(data.ANCHOR_TEXT.encode())).encode() + b"\0" + data.ANCHOR_TEXT.encode()).hexdigest()
    repository = {"head": data.BEFORE, "status": "", "protected_refs": {
        "refs/heads/candidate/wa-ir-standby-v1": data.BEFORE, "refs/heads/main": data.BEFORE, "refs/remotes/origin/main": data.BEFORE},
        "control_blobs": {data.ANCHOR: blob}, "assignment_scope": {"assignment_id": entry["scope"]["assignment_id"],
        "stage_id": "P1-00b", "sha256": entry["scope"]["assignment_sha256"], "expires_at": "2099-01-01T00:00:00Z",
        "allowed_paths": task["allowed_files"], "forbidden_paths": ["main.py"]}}
    transport = sdk.build_review_transport(proposal, sources, entry["proposal"]["sha256"])
    projection = {k: v for k, v in transport.items() if k != "unified_diff"}
    material = {"task": task, "source_head": data.BEFORE, "sources": sources, "proposal": projection,
        "pre_review_test_evidence": native, "preparation_notes": preparation_notes(task, sources),
        "canonical_source_excerpt_package": package, "complete_materialized_after_files": proposal["changes"],
        "original_source_manifest": original,
        "exact_original_source_excerpt": {"path": data.ANCHOR, "coverage": "LABORATORY complete three-line anchor"},
        "authoritative_physical_utf8_bytes": {p: len(s["content"].encode()) for p, s in sources.items() if s["content"] is not None},
        "review_representation_note": "LABORATORY projection, not original execution", "byte_fact_rule": "SHA256 of exact UTF8 bytes including final LF"}
    material_sha = digest(sdk.serialize_json(material))
    template = "LABORATORY tool-free review scaffolding; no live acceptance.\n"
    prompt = template + "CONTEXT_JSON:\n" + sdk.serialize_json(material).decode()
    add(directory + "material.json", material)
    add(directory + "prompt.txt", prompt)
    response_schema = sdk.schema("verifier", task, sources)
    add(directory + "response-schema.json", response_schema)
    usage = {k: 0 for k in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")}
    status.update(review_transport_sha256=digest(sdk.serialize_json(transport)), response_schema_sha256=digest(sdk.serialize_json(response_schema)),
        started_at=0.0, retain_until=0.0, authority="PROPOSAL_ONLY_NOT_ACCEPTANCE",
        cli={"path": "LABORATORY_NO_CLI_EXECUTED", "version": "1.4.0", "sha256": "60e72242f07c131f75afcd9a9e425d6ee097a1a1b3190ea98ae78fa4180bbfa8"},
        cli_source={"source_path": "LABORATORY metadata only", "sha256": digest(template.encode())},
        session_gate={"state": "NOT_REQUESTED", "activation_reason": "LABORATORY", "root_count": 0, "required_symbol_count": 0, "query_operation_count": 0},
        runtime_profile={"conversation_id": status["conversation_id"], "name": "LABORATORY", "native_tools": 0,
                         "command_policy": "DENY_ALL", "actual_executor_checked": False}, cli_pid=0, duration_seconds=0.0, usage=usage)
    prompt_check = {"prompt_utf8_bytes": len(prompt.encode()), "prompt_sha256": digest(prompt.encode())}
    preflight = add(".local/preflight.json", {"kind": "WHOLE_REVIEW_PROMPT_PHYSICALLY_BOUND", "task_sha256": entry["task_snapshot"]["sha256"],
        "proposal_sha256": entry["proposal"]["sha256"], "receipt_sha256": entry["native"]["pre"]["sha256"],
        "packets": {"laboratory": {"prompt_path": directory + "prompt.txt", "prompt_bytes": len(prompt.encode()), "prompt_sha256": prompt_check["prompt_sha256"]}},
        "Source_applied": False, "Provider_called": False}, 0o644)
    notice = add(".local/lab-authority.json", {"state": "LABORATORY_INPUT_ONLY", "authority": "No Owner/Root acceptance; no driver, Provider or runtime executed"}, 0o644)
    template_ref = add(".local/lab-template.txt", template, 0o644)
    ready = {"kind": "CODEX_AUTHORED_REVIEW_ONLY_READY", "author": "CODEX", "head": data.BEFORE,
        "assignment_id": entry["scope"]["assignment_id"], "assignment_sha256": entry["scope"]["assignment_sha256"],
        "task_path": entry["task_snapshot"]["path"], "task_sha256": entry["task_snapshot"]["sha256"],
        "proposal_path": entry["proposal"]["path"], "proposal_sha256": entry["proposal"]["sha256"],
        "test_evidence_path": entry["native"]["pre"]["path"], "test_evidence_sha256": entry["native"]["pre"]["sha256"],
        "controller_run_id": actor["controller_run_id"], "reviewer_run_id": status["run_id"], "original_source_manifest": original, "after_source_manifest": after,
        "paired_preflight_path": preflight["path"], "paired_preflight_sha256": preflight["sha256"],
        "accepted684_receipt_path": notice["path"], "accepted684_receipt_sha256": notice["sha256"], "retained_sha256": {notice["path"]: notice["sha256"]},
        "driver_sha256": notice["sha256"], "timeout": 600, "packet": "laboratory", "role_template_helper_sha256": template_ref["sha256"],
        "projection_helper_sha256": material_sha, "original_template_sha256": template_ref["sha256"]}
    actor["ready"] = add(".local/ready.json", ready, 0o644)
    custody = {"kind": "CODEX_AUTHORED_INDEPENDENT_REVIEW_ONLY", "author": "CODEX", "head": data.BEFORE,
        "assignment_id": ready["assignment_id"], "assignment_sha256": ready["assignment_sha256"], "proposal_sha256": ready["proposal_sha256"],
        "test_evidence_sha256": ready["test_evidence_sha256"], "original_source_manifest": original, "after_source_manifest": after,
        "reviewer_run_id": status["run_id"], "ready_sha256": actor["ready"]["sha256"], "owner_authority_sha256": notice["sha256"], "prompt_check": prompt_check,
        "projection_check": {"packet": "laboratory", "material_sha256": material_sha, "original_manifest": original,
            "excerpt_original_path": data.ANCHOR, "derived_template_sha256": digest(template.encode())}, "error_type": None,
        "Worker_run_created": False, "Worker_conversation_created": False, "Source_Git_changed": False,
        "whole_controller_replayed": False, "acceptance_granted": False, "AP_Broker_post_guard_passed": False}
    actor["review_custody"] = add(".local/review-custody.json", custody, 0o644)
    independent = {"run_id": status["run_id"], "result_sha256": result["sha256"], "conversation_id": status["conversation_id"], "usage": usage, "prompt": prompt_check}
    sizes = {c["path"]: len(c["content"].encode()) for c in proposal["changes"]}
    physical = {"after_utf8_bytes": sizes, "unique_joint": sum(sizes.values()), "active_family_totals": {
        family: {"shared_plus_family": sum(sizes.values()), "source_core_pair": 0, "fixture_core_pair": 0, "all_active": sum(sizes.values())}
        for family in ("B194", "C176")}, "shared_charged_to_both_families": True, "historical_A_B_C_aggregate_claimed_to_fit_active_caps": False}
    actor["preaction"] = add(".local/preaction.json", {"kind": "CODEX_AUTHORED_COMPLETE_ORIGINAL607_PREACTION", "repository": repository,
        "proposal_sha256": ready["proposal_sha256"], "task_sha256": ready["task_sha256"], "actual_pre_review_receipt_sha256": ready["test_evidence_sha256"],
        "after_sha256": after, "selectors": task["test_selectors"], "independent_review": independent, "physical": physical,
        "retained_evidence_sha256": {notice["path"]: notice["sha256"]}, "Gemini_Worker_claim": False, "Supervisor_started": False, "full_goal_complete": False}, 0o644)
    actor["post_validation"] = add(".local/post-validation.json", {"native_exit_code": 0, "failed": 0, "errors": 0, "passed": len(task["test_selectors"]),
        "actual_native_receipt_sha256": entry["native"]["post"]["sha256"], "task_sha256": ready["task_sha256"], "selectors": task["test_selectors"],
        "after_sha256": after, "git_diff_check": True, "HOST_actual_bwrap_no_adapter": True, "after_utf8_bytes": sizes,
        "session_id": 0, "terminal_chunk": "LABORATORY retained post-native DATA", "terminal_summary": "LABORATORY no fresh native invocation"}, 0o644)
    after_repository = copy.deepcopy(repository)
    after_repository["head"] = after_repository["protected_refs"]["refs/heads/main"] = entry["head_after"]
    actor["_root"] = {"kind": "CODEX_AUTHORED_COMPLETE_ORIGINAL607_INDEPENDENT_GEMINI_ROOT_ACCEPTED", "parent": data.BEFORE, "head": entry["head_after"],
        "repository": after_repository, "proposal_sha256": ready["proposal_sha256"], "validation_sha256": actor["post_validation"]["sha256"],
        "Gemini_Worker_claim": False, "after_sha256": after, "independent_review": independent, "original_sha256": {p: original[p] for p in after},
        "physical": physical, "retained_evidence_sha256": {notice["path"]: notice["sha256"]}, "complete_original_normalizer_607_accepted": False,
        "accepted_behavior_denominator_authority": False, "business_Stage_claim": False, "efficiency_savings_claim": False,
        "Source_implementation_changed": False, "runtime_activation": False, "Supervisor_started": False, "full_goal_complete": False}
