"""Offline task preparation and evidence/byte checks. Never applies or approves code."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess

try:
    from scripts import two_site_proposal_runner as runner
except ModuleNotFoundError:
    import two_site_proposal_runner as runner
try:
    from scripts.two_site_semantic_task_builder import (
        build_task_identity,
        validate_task_identity,
    )
except ModuleNotFoundError:
    from two_site_semantic_task_builder import (
        build_task_identity,
        validate_task_identity,
    )

MARKER = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
P1_00B_ASSIGNMENT = "WA-P1-00B-FREEZE-20260904-001"
P1_00B_BATCH_PREFIX = "P1-00B-BATCH-V1."
P1_00B_HANDLER_REQUIREMENT = re.compile(
    r"^\[HANDLER\] "
    r"(?P<source>(?:api|bot|core|main|models)/[A-Za-z0-9_./-]+\.py):"
    r"(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)"
    r" \| selector=(?P<selector>tests/test_p1_00b_characterization_[a-z0-9_]+\.py::"
    r"[A-Za-z_][A-Za-z0-9_]*::test_[A-Za-z0-9_]+)"
    r" \| scenario=(?P<scenario>[A-Za-z0-9._-]+)$"
)


def validate_p1_00b_batch_spec(task: object, scenario_key: object) -> None:
    """Validate the compact P1-00b package shape before model contact.

    The package is deliberately narrow: a shared, hermetic characterization module
    proves two to four exact handlers from one source module.  It is an efficiency
    boundary, not a way to aggregate evidence: every handler retains an individual
    requirement and pytest selector for Worker, Reviewer, Codex, and the ledger.
    """
    if not isinstance(task, dict) or not isinstance(scenario_key, str):
        raise runner.ProposalError("invalid P1-00b batch task")
    goal = task.get("goal")
    if not isinstance(goal, str) or P1_00B_ASSIGNMENT not in goal:
        return
    if not scenario_key.startswith(P1_00B_BATCH_PREFIX):
        return

    requirements = task.get("requirements")
    selectors = task.get("test_selectors")
    allowed_files = task.get("allowed_files")
    if not (
        isinstance(requirements, list)
        and isinstance(selectors, list)
        and isinstance(allowed_files, list)
        and len(allowed_files) == 1
        and isinstance(allowed_files[0], str)
        and re.fullmatch(r"tests/test_p1_00b_characterization_[a-z0-9_]+\.py", allowed_files[0])
    ):
        raise runner.ProposalError(
            "P1-00b batch must change exactly one canonical characterization test module"
        )

    parsed = []
    for requirement in requirements:
        if not isinstance(requirement, str):
            continue
        match = P1_00B_HANDLER_REQUIREMENT.fullmatch(requirement)
        if match is not None:
            parsed.append(match.groupdict())
    if not 2 <= len(parsed) <= 4 or len(parsed) != len(requirements):
        raise runner.ProposalError(
            "P1-00b batch must contain exactly two to four explicit handler requirements"
        )
    handler_keys = [(item["source"], item["symbol"], item["scenario"]) for item in parsed]
    if len(set(handler_keys)) != len(handler_keys):
        raise runner.ProposalError("P1-00b batch repeats a handler scenario")
    if len({item["source"] for item in parsed}) != 1:
        raise runner.ProposalError("P1-00b batch handlers must share one source module")
    requirement_selectors = [item["selector"] for item in parsed]
    if selectors != requirement_selectors or len(set(selectors)) != len(selectors):
        raise runner.ProposalError(
            "P1-00b batch selectors must exactly match the explicit handler requirements"
        )


def excerpt(root: Path, name: str, symbols: list[str]) -> dict:
    """Complete top-level definitions plus imports; dependency closure is reviewer-owned."""
    path = runner.relative_file(root, name)
    if (path.suffix != ".py" or not isinstance(symbols, list) or not 1 <= len(symbols) <= 32
            or any(not isinstance(x, str) or not x.isidentifier() for x in symbols)
            or len(set(symbols)) != len(symbols)):
        raise runner.ProposalError("excerpt needs unique Python top-level symbol names")
    runner.git(root, "ls-files", "--error-unmatch", "--", name)
    data = runner.bounded_file_bytes(root, path, runner.MAX_CONTEXT, "excerpt source")
    text = data.decode()
    if runner.SECRET.search(text):
        raise runner.ProposalError("credential-like excerpt source")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    selected = []
    found = set()
    for node in tree.body:
        names = {node.name} if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else set()
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {child.id for target in targets for child in ast.walk(target)
                     if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)}
        wanted = names & set(symbols)
        if not wanted and not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        found.update(wanted)
        start = min([node.lineno] + [x.lineno for x in getattr(node, "decorator_list", [])])
        end = node.end_lineno
        selected.append({"start": start, "end": end, "text": "".join(lines[start - 1:end])})
    if found != set(symbols):
        raise runner.ProposalError("missing top-level excerpt symbols: " + ",".join(sorted(set(symbols) - found)))
    return {"path": name, "sha256": runner.sha(data), "symbols": symbols, "ranges": selected}


def validate_excerpts(root: Path, task: dict) -> None:
    goal = task["goal"]
    if MARKER not in goal:
        return
    if goal.count(MARKER) != 1:
        raise runner.ProposalError("duplicate excerpt marker")
    package = runner.unique_json(goal.split(MARKER, 1)[1])
    if (not isinstance(package, dict) or set(package) != {"head", "gap_reference", "excerpts", "semantic_identity"}
            or package["head"] != runner.git(root, "rev-parse", "HEAD")
            or not isinstance(package["excerpts"], list) or len(package["excerpts"]) > 32):
        raise runner.ProposalError("stale or invalid source excerpt package")
    gap = package["gap_reference"]
    if not isinstance(gap, str) or not gap or len(gap) > 500:
        raise runner.ProposalError("missing tracked gap reference")
    gap_path = gap.split("#", 1)[0]
    runner.relative_file(root, gap_path)
    runner.git(root, "ls-files", "--error-unmatch", "--", gap_path)
    seen = set(task["allowed_files"] + task["context_files"])
    for item in package["excerpts"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or item["path"] in seen:
            raise runner.ProposalError("duplicate or writable excerpt source")
        if item != excerpt(root, item["path"], item.get("symbols")):
            raise runner.ProposalError("excerpt provenance mismatch")
        seen.add(item["path"])
    identity = package.get("semantic_identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("scenario_key"), str):
        raise runner.ProposalError("invalid semantic identity package")
    try:
        validate_task_identity(
            root,
            gap,
            identity["scenario_key"],
            task.get("context_files", []),
            task.get("allowed_files", []),
            package["excerpts"],
            identity,
        )
    except Exception as exc:
        raise runner.ProposalError(f"semantic identity validation failed: {exc}") from exc
    validate_p1_00b_batch_spec(task, identity["scenario_key"])


def build(root: Path, spec_path: Path, output: Path) -> dict:
    spec = runner.unique_json(runner.bounded_file_text(root, spec_path, runner.MAX_CONTEXT, "task spec"))
    if not isinstance(spec, dict) or set(spec) != {"task", "snippets", "gap_reference", "scenario_key"}:
        raise runner.ProposalError("spec needs task, snippets, gap_reference and scenario_key")
    task = spec["task"]
    if not isinstance(task, dict) or not isinstance(task.get("goal"), str) or MARKER in task["goal"]:
        raise runner.ProposalError("invalid base task")
    validate_p1_00b_batch_spec(task, spec["scenario_key"])
    if not isinstance(spec["snippets"], list) or len(spec["snippets"]) > 32:
        raise runner.ProposalError("invalid snippets")
    snippets = []
    for item in spec["snippets"]:
        if not isinstance(item, dict) or set(item) != {"path", "symbols"}:
            raise runner.ProposalError("snippet needs path and symbols")
        snippets.append(excerpt(root, item["path"], item["symbols"]))
    try:
        identity = build_task_identity(
            root,
            spec["gap_reference"],
            spec["scenario_key"],
            task.get("context_files", []),
            task.get("allowed_files", []),
            snippets,
        )
    except Exception as exc:
        raise runner.ProposalError(f"failed to build semantic identity: {exc}") from exc
    package = {"head": runner.git(root, "rev-parse", "HEAD"),
               "gap_reference": spec["gap_reference"], "excerpts": snippets,
               "semantic_identity": identity}
    task = {**task, "goal": task["goal"] + MARKER + json.dumps(package, ensure_ascii=False)}
    payload = (json.dumps(task, ensure_ascii=False, indent=2) + "\n").encode()
    expected_parent = root / ".local/two-site-refactor/tasks"
    if (output.parent != expected_parent or output.resolve() != output
            or output.suffix != ".json" or len(payload) > runner.MAX_CONTEXT):
        raise runner.ProposalError("output must be a fresh bounded ignored task JSON")
    expected_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        result = runner.check(root, output)
    except BaseException:
        output.unlink(missing_ok=True)  # Only the task just created by this invocation.
        raise
    result.update(task_path=str(output), gap_reference=package["gap_reference"],
                  excerpt_count=len(snippets),
                  semantic_task_fingerprint=identity["fingerprint"],
                  sizing_note="Budget the reviewer too: template + task/sources + full canonical proposal; target <160000 bytes, not only Worker input.")
    return result


def review(root: Path, task_path: Path, worker: str, reviewer: str, digest: str,
           applied: bool = False) -> dict:
    try:
        from scripts import two_site_codex_autopilot as autopilot
        from scripts.two_site_proposal_pipeline import validate_run
    except ModuleNotFoundError:
        import two_site_codex_autopilot as autopilot
        from two_site_proposal_pipeline import validate_run
    head = runner.git(root, "rev-parse", "HEAD")
    worker_dir = autopilot.proposal_run_dir(root, worker)
    reviewer_dir = autopilot.proposal_run_dir(root, reviewer)
    snapshot = runner.unique_json(runner.bounded_file_text(root, worker_dir / "task.json", 2 * runner.MAX_CONTEXT, "worker snapshot"))
    if not applied:
        if runner.git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise runner.ProposalError("pre-apply review requires clean Git")
        task, sources = runner.load_task(root, task_path)
    else:
        task, sources = snapshot["task"], snapshot["sources"]
        current_task = runner.unique_json(runner.bounded_file_text(root, task_path, runner.MAX_CONTEXT, "task"))
        if current_task != task:
            raise runner.ProposalError("task changed after review")
    binding = {"gemini_worker_run_id": worker, "gemini_reviewer_run_id": reviewer, "proposal_sha256": digest}
    proposal = autopilot.validate_gemini_chain(root, head, binding)
    conversations: set[str] = set()
    prior = None if "proposal" not in snapshot else _prior_worker(root, snapshot, worker_dir)
    if prior is not None:
        conversations.add(prior[3])
    result, status, result_sha, result_text = validate_run(
        root, worker_dir, "worker", worker, head, task, sources, conversations,
        prior_worker=prior,
    )
    reviewer_snapshot = runner.unique_json(runner.bounded_file_text(
        root, reviewer_dir / "task.json", 2 * runner.MAX_CONTEXT, "reviewer snapshot"
    ))
    reviewer_status = runner.unique_json(runner.bounded_file_text(
        root, reviewer_dir / "status.json", runner.MAX_CONTEXT, "reviewer status"
    ))
    if not isinstance(reviewer_snapshot, dict) or not isinstance(reviewer_status, dict):
        raise runner.ProposalError("invalid reviewer metadata")
    has_evidence = "test_evidence" in reviewer_snapshot
    has_evidence_sha = "input_test_evidence_sha256" in reviewer_status
    if has_evidence != has_evidence_sha:
        raise runner.ProposalError("reviewer test evidence and SHA must be provided together")
    if has_evidence:
        test_evidence = reviewer_snapshot["test_evidence"]
        if not isinstance(test_evidence, dict):
            raise runner.ProposalError("reviewer test evidence must be a dict")
        expected_test_evidence_sha = runner.sha(runner.serialize_json(test_evidence))
        if expected_test_evidence_sha != reviewer_status["input_test_evidence_sha256"]:
            raise runner.ProposalError("reviewer test evidence SHA mismatch")
        expected_test_receipt = test_evidence
    else:
        expected_test_receipt = None
        expected_test_evidence_sha = None
    validate_run(
        root, reviewer_dir, "verifier", reviewer, head, task, sources, conversations,
        prior_worker=(worker, result_sha, result_text, status["conversation_id"]),
        expected_test_receipt=expected_test_receipt,
        expected_test_evidence_sha=expected_test_evidence_sha,
    )
    if applied:
        changes = {x["path"]: x for x in result["changes"]}
        staged = runner.git(root, "diff", "--cached", "--name-only")
        if staged:
            raise runner.ProposalError("post-apply check must precede staging")
        dirty = set(runner.git(root, "diff", "--name-only").splitlines())
        dirty.update(runner.git(root, "ls-files", "--others", "--exclude-standard").splitlines())
        if dirty != set(changes):
            raise runner.ProposalError("dirty paths differ from exact proposal")
        for name, source in sources.items():
            path = runner.relative_file(root, name)
            expected = changes[name]["content"] if name in changes else source["content"]
            if expected is None:
                if os.path.lexists(path):
                    raise runner.ProposalError("unexpected new source file")
                continue
            actual = runner.bounded_file_text(root, path, runner.MAX_CONTEXT, "applied source")
            if actual != expected:
                raise runner.ProposalError("applied/source bytes mismatch: " + name)
            if name in changes:
                mode = "100755" if path.stat().st_mode & 0o111 else "100644"
                old_mode = autopilot.tree_mode(root, head, name)
                if mode != (old_mode or "100644"):
                    raise runner.ProposalError("applied file mode mismatch")
                if source["sha256"] is not None and runner.sha(autopilot.git_blob(root, head, name)) != source["sha256"]:
                    raise runner.ProposalError("original source does not match HEAD")
    return {"state": "BYTES_VALID" if applied else "BINDINGS_VALID", "head": head,
            **binding, "changed_paths": [x["path"] for x in proposal["changes"]],
            "worker_result": str(worker_dir / "result.json"),
            "reviewer_result": str(reviewer_dir / "result.json"),
            "test_selectors": task["test_selectors"],
            "semantic_review": "REQUIRED_BY_CODEX", "tests": "NOT_RUN"}


def _prior_worker(root: Path, snapshot: dict, worker_dir: Path) -> tuple:
    """Validate final correction input using the existing runner's full prior binding."""
    status = runner.unique_json(runner.bounded_file_text(root, worker_dir / "status.json", runner.MAX_CONTEXT, "worker status"))
    prior_id = status.get("input_worker_run_id")
    if not isinstance(prior_id, str) or not runner.re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", prior_id):
        raise runner.ProposalError("invalid prior worker ID")
    path = root / ".local/two-site-refactor/proposals" / prior_id / "result.json"
    if runner.bounded_file_text(root, path, runner.MAX_CONTEXT, "prior result") != snapshot["proposal"]:
        raise runner.ProposalError("prior result bytes mismatch")
    _, prior, digest = runner.load_prior_worker(root, path, snapshot["proposal"], snapshot["task"], snapshot["sources"], snapshot["source_head"])
    if prior.get("run_id") != prior_id or prior.get("model") != runner.WORKER_MODEL:
        raise runner.ProposalError("prior identity mismatch")
    feedback = snapshot.get("feedback")
    if not isinstance(feedback, dict):
        raise runner.ProposalError("correction feedback missing")
    runner.validate_correction_feedback(json.dumps(feedback), digest)
    # The runner hashes original feedback before parsing. This facade validates
    # its parsed contract, not a reconstructed serialization's supposed raw hash.
    return prior_id, digest, snapshot["proposal"], prior["conversation_id"]


_CANONICAL_ROOT = "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
_CANONICAL_BLOCKERS = {
    "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
    "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
}
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def inspect_canonical_evidence(root: Path, behavior_id: str | None = None) -> dict:
    """Classify current canonical P1-00b evidence, bound to the current HEAD."""
    root = Path(root).resolve(strict=True)
    loaded: dict[str, tuple[dict, str]] = {}
    total = 0
    try:
        head = runner.git(root, "rev-parse", "HEAD")
        if not _HEX40.fullmatch(head):
            raise runner.ProposalError("HEAD is not a full lowercase commit SHA")

        def git_blob(commit: str, path: str) -> bytes:
            if not _HEX40.fullmatch(commit) or ":" in path:
                raise runner.ProposalError("unsafe Git blob reference")
            return subprocess.check_output(
                ["/usr/bin/git", "--no-replace-objects", "-C", str(root), "show", f"{commit}:{path}"],
                stderr=subprocess.DEVNULL,
                timeout=20,
            )

        def load(path: str) -> tuple[dict, str]:
            nonlocal total
            if path in loaded:
                return loaded[path]
            runner.git(root, "ls-files", "--error-unmatch", "--", path)
            data = runner.bounded_file_bytes(root, runner.relative_file(root, path), 2_000_000, path)
            total += len(data)
            if total > 8_000_000:
                raise runner.ProposalError("canonical aggregate exceeds 8,000,000 bytes")
            if runner.sha(data) != runner.sha(git_blob(head, path)):
                raise runner.ProposalError(f"live canonical bytes differ from HEAD: {path}")
            doc = runner.unique_json(data.decode("utf-8"))
            if not isinstance(doc, dict):
                raise runner.ProposalError(f"canonical document is not an object: {path}")
            loaded[path] = (doc, runner.sha(data))
            return loaded[path]

        manifest, _ = load(_CANONICAL_ROOT)
        if manifest.get("schema_version") != 1 or manifest.get("stage") != "P1-00b":
            raise runner.ProposalError("invalid canonical manifest schema or stage")
        inventory = manifest.get("inventory")
        sources = manifest.get("evidence_sources")
        counts = manifest.get("counts")
        if not isinstance(inventory, dict) or not isinstance(sources, dict) or not isinstance(counts, dict):
            raise runner.ProposalError("canonical manifest sections missing")
        expected = inventory.get("expected_seed_count")
        surfaces = inventory.get("paths")
        source_keys = {"baseline_path", "base_path", "continuation_path", "continuation_2_path", "continuation_3_path"}
        if type(expected) is not int or not isinstance(surfaces, list) or len(surfaces) != 4 or set(sources) != source_keys:
            raise runner.ProposalError("canonical manifest inventory or sources invalid")
        values = [counts.get(key) for key in ("baseline_evidenced_seed_count", "reconciled_new_seed_count", "evidenced_seed_count", "remaining_seed_count")]
        if any(type(value) is not int or value < 0 for value in values) or values[0] + values[1] != values[2] or values[2] + values[3] != expected:
            raise runner.ProposalError("canonical manifest counts invalid")
        blockers = manifest.get("canonical_blockers")
        if not isinstance(blockers, list) or not _CANONICAL_BLOCKERS.issubset(blockers):
            raise runner.ProposalError("canonical manifest blockers invalid")

        ids: list[str] = []
        known: set[str] = set()
        for path in surfaces:
            if not isinstance(path, str):
                raise runner.ProposalError("surface path invalid")
            surface, _ = load(path)
            if surface.get("schema_version") != "two-server-refactor.surface-behavior-inventory.v1" or not isinstance(surface.get("entries"), list):
                raise runner.ProposalError(f"surface schema invalid: {path}")
            for entry in surface["entries"]:
                bid = entry.get("behavior_id") if isinstance(entry, dict) else None
                if not isinstance(bid, str) or not bid or bid in known:
                    raise runner.ProposalError("surface behavior identifiers invalid")
                known.add(bid)
                ids.append(bid)
        if len(ids) != expected:
            raise runner.ProposalError("surface count does not match expected seed count")

        scenario, _ = load(sources["baseline_path"])
        mapping = scenario.get("seed_mapping")
        registry = scenario.get("blocker_registry")
        expected_sources = ["inventory/surface-api.json", "inventory/surface-bot.json", "inventory/surface-web.json", "inventory/surface-jobs.json"]
        if scenario.get("schema_version") != "two-server-refactor.scenario-records.v2" or not isinstance(mapping, dict) or mapping.get("expected_seed_count") != expected or mapping.get("source_inventories") != expected_sources:
            raise runner.ProposalError("scenario seed mapping invalid")
        section_one = Path("docs/refactor/two-server/section-1")
        if [(section_one / value).as_posix() for value in expected_sources] != surfaces:
            raise runner.ProposalError("scenario source inventories do not match manifest")
        items = registry.get("items") if isinstance(registry, dict) else None
        status_by_blocker = {item.get("blocker_id"): item for item in items if isinstance(item, dict)} if isinstance(items, list) else {}
        if any(status_by_blocker.get(key, {}).get("disposition") != "BLOCKED_EXPLICIT" or status_by_blocker.get(key, {}).get("status") != "BLOCKED_EXPLICIT" for key in _CANONICAL_BLOCKERS):
            raise runner.ProposalError("scenario blockers invalid")

        def ancestor(commit: object) -> bool:
            if not isinstance(commit, str) or not _HEX40.fullmatch(commit):
                return False
            try:
                runner.git(root, "merge-base", "--is-ancestor", commit, head)
                return True
            except subprocess.CalledProcessError:
                return False

        base, base_sha = load(sources["base_path"])
        if base.get("schema_version") != 1 or base.get("stage") != "P1-00b" or not ancestor(base.get("inspected_head")) or not isinstance(base.get("records"), list):
            raise runner.ProposalError("base characterization invalid")
        c1, c1_sha = load(sources["continuation_path"])
        c1_base = c1.get("base_inventory")
        if c1.get("schema_version") != 1 or c1.get("stage") != "P1-00b" or not isinstance(c1.get("records"), list) or not isinstance(c1_base, dict) or c1_base.get("path") != sources["base_path"] or c1_base.get("sha256") != base_sha or not ancestor(c1_base.get("inspected_head")):
            raise runner.ProposalError("continuation 1 invalid")

        continuations = [(sources["continuation_2_path"], sources["continuation_path"], c1_sha), (sources["continuation_3_path"], sources["continuation_2_path"], None)]
        docs = [base, c1]
        for index, (path, predecessor, predecessor_sha) in enumerate(continuations, start=2):
            current, current_sha = load(path)
            if predecessor_sha is None:
                predecessor_sha = load(predecessor)[1]
            link = current.get("predecessor_inventory")
            actual_touch = runner.git(root, "log", "-1", "--format=%H", head, "--", predecessor)
            if current.get("schema_version") != 1 or current.get("stage") != "P1-00b" or not isinstance(current.get("records"), list) or not isinstance(link, dict) or link.get("path") != predecessor or link.get("sha256") != predecessor_sha or link.get("last_commit") != actual_touch or runner.sha(git_blob(actual_touch, predecessor)) != predecessor_sha:
                raise runner.ProposalError(f"continuation {index} invalid")
            docs.append(current)

        proven: set[str] = set()
        unknown: set[str] = set()
        for doc in docs:
            for record in doc["records"]:
                seed_ids = record.get("seed_ids") if isinstance(record, dict) else None
                if not isinstance(seed_ids, list) or any(not isinstance(seed, str) or seed not in known for seed in seed_ids):
                    raise runner.ProposalError("record seed IDs invalid")
                # Origin-matrix records may deliberately have no seed binding.
                # They remain canonical context, but cannot prove or weaken one.
                if not seed_ids:
                    continue
                # Scenario lookup is the normal path.  Avoid re-proving unrelated
                # records while preserving the full-inventory audit when no target
                # behavior is requested.
                if behavior_id is not None and behavior_id not in seed_ids:
                    continue
                valid = isinstance(record.get("path"), str) and isinstance(record.get("sha256"), str) and _HEX64.fullmatch(record["sha256"]) and isinstance(record.get("last_commit"), str) and _HEX40.fullmatch(record["last_commit"]) and isinstance(record.get("test_selectors"), list) and record["test_selectors"] and all(isinstance(selector, str) and selector for selector in record["test_selectors"])
                try:
                    path = record["path"]
                    runner.git(root, "ls-files", "--error-unmatch", "--", path)
                    valid = bool(valid) and record["sha256"] == runner.sha(git_blob(head, path)) and record["last_commit"] == runner.git(root, "log", "-1", "--format=%H", head, "--", path) and ancestor(record["last_commit"])
                except (KeyError, subprocess.CalledProcessError, runner.ProposalError):
                    valid = False
                (proven if valid else unknown).update(seed_ids)
        covered_ids = ids if behavior_id is None else [behavior_id] if behavior_id in known else []
        coverage = {bid: "PROVEN_FOR_REQUESTED_SCENARIO" if bid in proven else "UNKNOWN" if bid in unknown else "PARTIAL_OR_SEED_ONLY" for bid in covered_ids}
        result = {"status": "VALID", "expected_seed_count": expected, "seed_count": len(ids), "coverage": coverage}
        if behavior_id is not None:
            result["requested_coverage"] = coverage.get(behavior_id, "NO_MATCHING_EVIDENCE")
        return result
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError, runner.ProposalError) as exc:
        return {"status": "INVENTORY_INCONSISTENT", "reason": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=runner.ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    builder = sub.add_parser("build")
    builder.add_argument("--spec", type=Path, required=True)
    builder.add_argument("--output", type=Path, required=True)
    reviewer = sub.add_parser("review")
    reviewer.add_argument("--task", type=Path, required=True)
    reviewer.add_argument("--worker", required=True)
    reviewer.add_argument("--reviewer", required=True)
    reviewer.add_argument("--sha", required=True)
    reviewer.add_argument("--applied", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    try:
        if args.command == "build":
            result = build(root, root / args.spec, root / args.output)
        else:
            result = review(root, root / args.task, args.worker, args.reviewer, args.sha, args.applied)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError) as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
