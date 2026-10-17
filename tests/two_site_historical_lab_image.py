from __future__ import annotations
import copy
import hashlib
import json
from scripts import two_site_proposal_runner as sdk
from scripts.two_site_semantic_task import build_identity
from scripts.two_site_task_context import preparation_notes
from tests import two_site_historical_lab_plan_data as data
from tests.two_site_historical_lab_plan_actors import digest, prescribe_actors
def behavior(code, method):
    return {"domain": "P1_BEHAVIOR", "behavior": {
        "stage_id": "P1-00b", "gap_reference": data.GAP, "source_path": data.HANDLER, "symbol": "get_users",
        "scenario_key": "GET-USERS-" + code, "test_selector": data.TEST + "::TestLab::" + method}}


def prescribe(root, case):
    """Construct this Subject's finite PLAN recipe from pinned DATA."""
    label, family = case.split("-", 1)
    index = ("unchanged403", "changed404", "alias403", "batch403404").index(label)
    codex = family == "CODEX"
    count = 0 if codex else int(family[-1])
    files, modes = {}, {}

    def add(path, value, mode=0o600):
        raw = value.encode() if type(value) is str else sdk.serialize_json(value)
        assert path not in files
        files[path], modes[path] = raw, mode
        return {"path": path, "sha256": digest(raw)}

    before = {}
    before.update({"api/__init__.py": b'"""Disclosed laboratory API."""\n',
        "tests/__init__.py": b'"""Disclosed laboratory tests."""\n',
        data.HANDLER: b"def get_users(status):\n    return status\n", data.ANCHOR: data.ANCHOR_TEXT.encode(),
        "docs/refactor/two-server/execution-order.yaml": b'{"stages":{"P1-00b":{"state":"IN_PROGRESS"}}}\n'})
    mapping = {"get-users": "LABORATORY 403/404 characterization gap; not a production seed",
               "403": behavior("403", "test_403"), "404": behavior("404", "test_404"),
               "alias403": behavior("403", "test_403_alias")}
    before["docs/behavior.json"] = sdk.serialize_json(mapping)
    for p, v in before.items():
        add(p, v.decode(), 0o644)
    final = dict(before, **{data.TEST: data.PROBE.encode()})
    changes = [{"path": data.TEST, "original_sha256": None, "content": data.PROBE}]
    if index in (1, 2):
        adjective = "changed-handler" if index == 1 else "alias"
        text = 'def get_users(status):\n    """Laboratory ' + adjective + ' revision; behavior remains unchanged."""\n    return status\n'
        changes.append({"path": data.HANDLER, "original_sha256": digest(before[data.HANDLER]), "content": text})
        final[data.HANDLER] = text.encode()
    pointers = ("403", "404") if index == 3 else (("403",), ("404",), ("alias403",))[index]
    chosen = [mapping[p]["behavior"] for p in pointers]
    assignment = "WA-P1-00B-FREEZE-20260904-001" if index == 3 else "WA-AE686-LAB-" + label
    allowed = [c["path"] for c in changes]
    contract = {"allowed_paths": allowed, "assignment_id": assignment, "expires_at": "2099-01-01T00:00:00Z",
        "external_access": "NONE", "forbidden_paths": ["main.py"], "integration_branch": "main",
        "role": "WORKER", "stage_id": "P1-00b", "status": "READY"}
    md = "# LABORATORY " + ("batch " if index == 3 else "") + "scope\n\n```yaml\n" + json.dumps(contract, indent=2) + "\n```\n"
    assert digest(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode() + b"\n" + md.encode()) == data.PINS[index][0]
    scope = {"kind": "HISTORICAL_MD", "assignment_id": assignment, "assignment_sha256": data.PINS[index][0],
             "markdown": add(".local/scope.md", md, 0o644),
             "canonical_contract": add(".local/contract.json", contract, 0o644)}
    add(".local/two-site-refactor/assignments/" + assignment + ".md", md, 0o644)
    add(".local/two-site-refactor/worktrees.yaml", {"integration": {
        "path": str(root), "branch": "main", "active_assignment_id": assignment}}, 0o644)
    manifest = {p: None if p == data.TEST else digest(before[p]) for p in sorted((data.HANDLER, data.ANCHOR, "docs/behavior.json", data.TEST))}
    scenario = "P1-00B-BATCH-V1.GET-USERS-403404" if index == 3 else chosen[0]["scenario_key"]
    identity = build_identity("P1-00b", data.GAP, scenario, [{"path": data.ANCHOR, "symbols": ["lab_anchor"]}], manifest)
    excerpt = {"path": data.ANCHOR, "sha256": digest(before[data.ANCHOR]), "symbols": ["lab_anchor"],
               "ranges": [{"start": 1, "end": 3, "text": data.ANCHOR_TEXT}]}
    package = {"head": data.BEFORE, "gap_reference": data.GAP, "excerpts": [excerpt], "semantic_identity": identity}
    requirements = ["[HANDLER] " + data.HANDLER + ":get_users | selector=" + b["test_selector"] + " | scenario=" + b["scenario_key"] for b in chosen]
    requirements += ["PROPOSAL_UTF8_BUDGET_V1:" + json.dumps({"version": "1.0", "file_limits": {
        p: 8000 for p in sorted(allowed)}, "total_limit": 16000}, sort_keys=True, separators=(",", ":"))]
    task = {"task_id": "ae686-lab-" + label, "goal": assignment + "\nLABORATORY only; not live provider evidence."
        + "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" + json.dumps(package, ensure_ascii=False),
        "context_files": ["docs/behavior.json"] + ([data.HANDLER] if data.HANDLER not in allowed else []),
        "allowed_files": allowed, "requirements": requirements, "test_selectors": [b["test_selector"] for b in chosen]}
    task_ref = add(".local/task.json", task)
    assert task_ref["sha256"] == data.PINS[index][1]
    proposal = {"status": "PROPOSAL_READY", "summary": "LABORATORY exact " + ("batch " if index == 3 else "")
        + "characterization bytes", "findings": [], "covered_requirements": list(range(1, len(requirements) + 1)), "changes": changes}
    proposal_ref = add(".local/proposal.json", proposal)
    assert proposal_ref["sha256"] == data.PINS[index][2]
    native = sdk.unique_json(data.NATIVE[index][0])
    pre = add(".local/pre.json", data.NATIVE[index][0], 0o644)
    post = add(".local/post.json", data.NATIVE[index][1], 0o644)
    assert (pre["sha256"], post["sha256"]) == data.PINS[index][3:]
    who = "AE lab <ae@example.invalid> 946684800 +0000"
    for phase, tree, parent in (("before", "51d3eebe3fc7c2e137dd96258d66642434445042", ""), ("after", data.TREES[index], "parent " + data.BEFORE + "\n")):
        add(".local/commit-" + phase, "tree " + tree + "\n" + parent + "author " + who + "\ncommitter " + who + "\n\nLABORATORY offline acceptance fixture\n", 0o644)
    for p, value in final.items():
        files[p], modes[p] = value, 0o644
    sources = {p: {"sha256": None if p == data.TEST else digest(before[p]), "content": None if p == data.TEST else before[p].decode()}
               for p in dict.fromkeys(task["context_files"] + task["allowed_files"])}
    entry = {"domain": "P1_BEHAVIOR", "behavior": chosen[0], "mapping": {"commit": data.BEFORE, "path": "docs/behavior.json",
        "sha256": digest(before["docs/behavior.json"]), "json_pointer": "/" + pointers[0]}, "head_before": data.BEFORE,
        "head_after": data.AFTER[index], "scope": scope, "task_snapshot": task_ref, "proposal": proposal_ref,
        "native": {"pre": pre, "post": post if codex else None}}
    prescribe_actors(add, entry, task, sources, proposal, native, count, codex, package)
    entries = []
    for b, pointer in zip(chosen, pointers):
        item = copy.deepcopy(entry)
        item["behavior"] = copy.deepcopy(b)
        item["mapping"]["json_pointer"] = "/" + pointer
        entries.append(item)
    expected = {"schema_version": 1, "state": "LABORATORY_INPUT_ONLY", "case_id": case, "root": str(root),
        "entries": entries, "native_refs": copy.deepcopy({"pre": pre, "post": post}), "provenance": {
        "recipe_version": 1, "sdk_head": "d51f0c87e8009b482d94d2ad31c394a5de0d6992", "data_origins": copy.deepcopy(data.ORIGINS),
        "head_before": data.BEFORE, "head_after": data.AFTER[index], "source_sha256": {p: digest(final[p]) for p in sorted(final)},
        "task_sha256": data.PINS[index][1], "proposal_sha256": data.PINS[index][2], "scope_sha256": data.PINS[index][0],
        "artifact_sha256": {p: digest(files[p]) for p in sorted(files)}, "live_provider_claim": False, "root_acceptance_claim": False}}
    return expected, files, modes, before, task, sources

def render_image(root_label, case_id):
    """Finite pure DATA: support text is an explicit pinned SDK Source reference."""
    if type(root_label) is not str or type(case_id) is not str or case_id not in data.CASES:
        raise ValueError("invalid historical lab image")
    try:
        encoded_label = root_label.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid historical lab image") from None
    if (not root_label.startswith("/") or len(encoded_label) > 1024 or "\x00" in root_label
            or "\\" in root_label
            or any(part in ("", ".", "..") for part in root_label.split("/")[1:])):
        raise ValueError("invalid historical lab image")
    result, files, modes, before, task, sources = prescribe(root_label, case_id)
    for name, expected_sha in data.SUPPORT.items():
        result["provenance"]["source_sha256"][name] = expected_sha
        result["provenance"]["artifact_sha256"][name] = expected_sha
    for field in ("source_sha256", "artifact_sha256"):
        result["provenance"][field] = dict(sorted(result["provenance"][field].items()))
    def source_record(name, raw, mode):
        if name in data.SUPPORT:
            return {"kind": "ROOT_SOURCE", "mode": mode, "sha256": data.SUPPORT[name]}
        return {"kind": "UTF8", "mode": mode, "sha256": digest(raw), "text": raw.decode()}
    image_files = {name: source_record(name, raw, modes[name]) for name, raw in files.items()}
    image_before = {name: source_record(name, raw, 0o644) for name, raw in before.items()}
    for name in data.SUPPORT:
        image_files[name] = source_record(name, None, 0o644)
        image_before[name] = source_record(name, None, 0o644)
    return {"schema_version": 1, "state": "LABORATORY_IMAGE_ONLY", "case_id": case_id,
            "root": root_label, "head_before": data.BEFORE, "head_after": result["provenance"]["head_after"],
            "before_files": dict(sorted(image_before.items())), "files": dict(sorted(image_files.items())),
            "public_return": result, "task": copy.deepcopy(task), "sources": copy.deepcopy(sources)}
