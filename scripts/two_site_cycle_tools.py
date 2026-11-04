"""Offline task preparation and evidence/byte checks. Never applies or approves code."""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

if __package__:
    from scripts.two_site_agent_sandbox import SandboxError, _git_read, _run_bounded_process
    from scripts.two_site_inventory_display import (
        error_report,
        format_inventory,
        valid_request,
    )
    from scripts.two_site_preparation_io import (
        emit_preparation_telemetry,
        load_preparation_startup,
        verify_preparation_startup,
    )
    import scripts.two_site_proposal_runner as runner
    from scripts.two_site_ai_config import AI_CONFIG_REL, load_ai_config
    from scripts.two_site_semantic_task_builder import (
        build_task_identity,
        validate_task_identity,
    )
    from scripts.two_site_source_excerpts import extract_bundle
    from scripts.two_site_task_preflight import prepare_task
else:
    from two_site_agent_sandbox import SandboxError, _git_read, _run_bounded_process
    from two_site_inventory_display import (
        error_report,
        format_inventory,
        valid_request,
    )
    from two_site_preparation_io import (
        emit_preparation_telemetry,
        load_preparation_startup,
        verify_preparation_startup,
    )
    import two_site_proposal_runner as runner
    from two_site_ai_config import AI_CONFIG_REL, load_ai_config
    from two_site_semantic_task_builder import (
        build_task_identity,
        validate_task_identity,
    )
    from two_site_source_excerpts import extract_bundle
    from two_site_task_preflight import prepare_task

try:
    from scripts.two_site_proposal_byte_profile import (
        PREFIX as BYTE_PROFILE_PREFIX, ProposalByteProfileError,
        parse_proposal_utf8_budget_profile, require_proposal_utf8_budget_profile,
    )
except ImportError:
    from two_site_proposal_byte_profile import (
        PREFIX as BYTE_PROFILE_PREFIX, ProposalByteProfileError,
        parse_proposal_utf8_budget_profile, require_proposal_utf8_budget_profile,
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

    # A validated machine profile is transport metadata, not another handler.
    try:
        parse_proposal_utf8_budget_profile(requirements, allowed_files, allowed_files)
    except ProposalByteProfileError:
        raise runner.ProposalError("invalid P1-00b batch byte profile") from None
    handler_requirements = [
        entry for entry in requirements
        if not (isinstance(entry, str) and entry.startswith(BYTE_PROFILE_PREFIX))
    ]
    parsed = []
    for requirement in handler_requirements:
        if not isinstance(requirement, str):
            continue
        match = P1_00B_HANDLER_REQUIREMENT.fullmatch(requirement)
        if match is not None:
            parsed.append(match.groupdict())
    if not 2 <= len(parsed) <= 4 or len(parsed) != len(handler_requirements):
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
    records, _ = extract_bundle(root, [{"path": name, "symbols": symbols}])
    return records[0]


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
    requests = []
    for item in package["excerpts"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or item["path"] in seen:
            raise runner.ProposalError("duplicate or writable excerpt source")
        requests.append({"path": item["path"], "symbols": item.get("symbols")})
        seen.add(item["path"])
    regenerated, _ = extract_bundle(root, requests)
    if regenerated != package["excerpts"]:
        raise runner.ProposalError("excerpt provenance mismatch")
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


_DIRECT_SCRIPT = re.compile(r"^scripts/two_site_[a-z0-9_]+\.py$")


def validate_direct_script_tests(root: Path, task: dict) -> None:
    """Ensure tracked direct tests are selected when modifying two-site scripts."""
    if not isinstance(task, dict):
        return
    allowed = task.get("allowed_files")
    if not isinstance(allowed, list):
        return
    selectors = task.get("test_selectors")
    selected = {s.split("::", 1)[0] for s in (selectors if isinstance(selectors, list) else []) if isinstance(s, str)}
    seen = set()
    for entry in allowed:
        if isinstance(entry, str) and entry not in seen:
            seen.add(entry)
            if _DIRECT_SCRIPT.fullmatch(entry):
                direct = f"tests/test_{Path(entry).stem}.py"
                if direct not in selected and runner._is_tracked_test(root, direct):
                    raise runner.ProposalError(f"missing direct test selector: {direct}")


def build(root: Path, spec_path: Path, output: Path) -> dict:
    t_start = None
    try:
        t_start = time.monotonic()
    except Exception:
        pass

    startup = load_preparation_startup(root)

    build_raised = True
    payload_len = None
    result = None
    try:
        spec = runner.unique_json(runner.bounded_file_text(root, spec_path, runner.MAX_CONTEXT, "task spec"))
        if not isinstance(spec, dict) or set(spec) != {"task", "snippets", "gap_reference", "scenario_key"}:
            raise runner.ProposalError("spec needs task, snippets, gap_reference and scenario_key")
        task = spec["task"]
        if not isinstance(task, dict) or not isinstance(task.get("goal"), str) or MARKER in task["goal"]:
            raise runner.ProposalError("invalid base task")
        # New Tasks bind the actual public model policy as a real source input.
        # Existing issued Tasks are never rewritten or implicitly rebound.
        if os.path.lexists(root / AI_CONFIG_REL):
            load_ai_config(root)
            context = task.get("context_files")
            if not isinstance(context, list) or any(not isinstance(path, str) for path in context):
                raise runner.ProposalError("invalid context_files for model-policy source")
            policy_path = AI_CONFIG_REL.as_posix()
            if policy_path not in context:
                if len(context) >= 32:
                    raise runner.ProposalError("model-policy source requires one context slot; maximum context_files is 32")
                task = {**task, "context_files": [*context, policy_path]}
        if startup is not None and startup.route == "one_shot":
            try:
                require_proposal_utf8_budget_profile(
                    task.get("requirements"), task.get("allowed_files"),
                )
            except ProposalByteProfileError:
                raise runner.ProposalError("normal task requires an explicit valid UTF-8 budget profile") from None
        validate_p1_00b_batch_spec(task, spec["scenario_key"])
        validate_direct_script_tests(root, task)
        if not isinstance(spec["snippets"], list) or len(spec["snippets"]) > 32:
            raise runner.ProposalError("invalid snippets")
        for item in spec["snippets"]:
            if not isinstance(item, dict) or set(item) != {"path", "symbols"}:
                raise runner.ProposalError("snippet needs path and symbols")
        snippets, report = extract_bundle(root, spec["snippets"])
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
        payload_len = len(payload)
        expected_parent = root / ".local/two-site-refactor/tasks"
        if (output.parent != expected_parent or output.resolve() != output
                or output.suffix != ".json" or len(payload) > runner.MAX_CONTEXT):
            raise runner.ProposalError("output must be a fresh bounded ignored task JSON")
        expected_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            if startup is None or startup.route == "one_shot":
                result = prepare_task(root, output)
            elif startup.route == "legacy":
                result = runner.check(root, output)
            else:
                raise runner.ProposalError(f"unsupported preparation route: {startup.route}")
            verify_preparation_startup(root, startup)
        except BaseException:
            output.unlink(missing_ok=True)  # Only the task just created by this invocation.
            raise
        if type(result) is dict and result.get("state") == "TASK_READY":
            result.update(task_path=str(output), gap_reference=package["gap_reference"],
                          excerpt_count=len(snippets),
                          semantic_task_fingerprint=identity["fingerprint"],
                          sizing_note="Budget the reviewer too: template + task/sources + full canonical proposal; target <160000 bytes, not only Worker input.",
                          context_budget=report)
            build_raised = False
            return result
        output.unlink(missing_ok=True)
        build_raised = False
        if type(result) is dict and result.get("state") == "TASK_BLOCKED":
            return result
        return {"schema_version": 1, "state": "TASK_BLOCKED", "issues": []}
    finally:
        if startup is not None:
            try:
                t_end = None
                try:
                    t_end = time.monotonic()
                except Exception:
                    pass
                elapsed_seconds = None
                if t_start is not None and t_end is not None:
                    delta = t_end - t_start
                    if isinstance(delta, (int, float)) and math.isfinite(delta) and delta >= 0:
                        elapsed_seconds = delta

                if build_raised:
                    tel_outcome = "ERROR"
                    tel_sfc = None
                    tel_issues = None
                elif type(result) is dict and result.get("state") == "TASK_READY":
                    tel_outcome = "TASK_READY"
                    raw_sfc = result.get("source_file_count")
                    tel_sfc = raw_sfc if type(raw_sfc) is int and not isinstance(raw_sfc, bool) else None
                    tel_issues = 0
                elif type(result) is dict and result.get("state") == "TASK_BLOCKED":
                    tel_outcome = "TASK_BLOCKED"
                    tel_sfc = None
                    raw_issues = result.get("issues")
                    tel_issues = len(raw_issues) if type(raw_issues) is list else None
                else:
                    tel_outcome = "UNKNOWN"
                    tel_sfc = None
                    tel_issues = None

                emit_preparation_telemetry(
                    startup,
                    outcome=tel_outcome,
                    elapsed_seconds=elapsed_seconds,
                    source_file_count=tel_sfc,
                    source_bytes=None,
                    task_bytes=payload_len,
                    issue_count=tel_issues,
                )
            except Exception:
                pass


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
    snapshot = runner.unique_json(runner.bounded_file_text(root, worker_dir / "task.json", 3 * runner.MAX_CONTEXT, "worker snapshot"))
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
        root, reviewer_dir / "task.json", 3 * runner.MAX_CONTEXT, "reviewer snapshot"
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
    try:
        from scripts.two_site_proposal_pipeline import expected_bound_role_model
    except ModuleNotFoundError:
        from two_site_proposal_pipeline import expected_bound_role_model
    status = runner.unique_json(runner.bounded_file_text(root, worker_dir / "status.json", runner.MAX_CONTEXT, "worker status"))
    prior_id = status.get("input_worker_run_id")
    if not isinstance(prior_id, str) or not runner.re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", prior_id):
        raise runner.ProposalError("invalid prior worker ID")
    path = root / ".local/two-site-refactor/proposals" / prior_id / "result.json"
    if runner.bounded_file_text(root, path, runner.MAX_CONTEXT, "prior result") != snapshot["proposal"]:
        raise runner.ProposalError("prior result bytes mismatch")
    _, prior, digest = runner.load_prior_worker(root, path, snapshot["proposal"], snapshot["task"], snapshot["sources"], snapshot["source_head"])
    expected_model = expected_bound_role_model(root, "worker", prior_id, snapshot["source_head"],
        snapshot["task"], snapshot["sources"], prior)
    if prior.get("run_id") != prior_id or prior.get("model") != expected_model:
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
CANONICAL_READ_LIMIT_BYTES = 2_000_000
CANONICAL_TOTAL_BUDGET_BYTES = 8_000_000
_BLOB_FAILED = object()


# Exact immutable historical owner DATA accepted before the standard
# supplemental envelope. No general namespace or new-receipt exemption.
# Raw SHA admission preserves each frozen original envelope and its gates; all
# records still enter the ordinary ancestry, Source SHA and AST proof loop.
_HISTORICAL_OWNER_RECEIPTS = {
    'business_768_market_overrides_a2_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-market-overrides-a2.json',
        '0d0309ad5e1fc40f4124fd82cdf968bb2894f45767fb952b3ce4eb5f3888dec2',
    ),
    'business_768_market_overrides_b3_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-market-overrides-b3.json',
        'af9f4670001c071f76cfb75f99ce1b6929e6f6742354bf057cf2ff1bdcb111ab',
    ),
    'business_768_live786_manual783_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-manual783-provenance.json',
        '685032585be7452977b4fda3bf4dbb2cbaf37c127131743a69c1fdf233ac9a5f',
    ),
    'business_768_live786_five_source_closeout_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-five-source-closeout-manual.json',
        '46b231dbcf808f556bb5f798c50fc9444a4e6fc7ce1dfa8ec5e6d8855567a4d3',
    ),
    'business_768_live786_reset_execute_singleton_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-reset-execute-singleton.json',
        '4ef4ffe40f33dfe4328573bdca380b220e33306e1bee5f18d5baf54aef7170a2',
    ),
    'business_768_live786_replace_execute_singleton_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-replace-execute-singleton.json',
        'cf251e42b72c41490e205499dbc269704d75ba422dbf730ef1027d29101d579c',
    ),
    'business_768_live786_verifier_only_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-verifier-only.json',
        '09c03b798d7c268a987874c1ebe3e834ed534edc97eade4be9b68e097d27f960',
    ),
    'business_768_live786_authority_registration_after_security_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-authority-registration-security.json',
        '676426367cb4fd92ace6cd9f2bd04c90fb70bc8f24e357c3fe4293004f56c113',
    ),
    'business_768_current_session_body_registration_path': (
        'docs/refactor/two-server/section-1/inventory/current-session-body-registration.json',
        '9ef13f20a2c85cc813e79e6e209398cddb25c6f60a66eb12e47013d63cc49049',
    ),
    'business_768_current_session_group_union_path': (
        'docs/refactor/two-server/section-1/inventory/current-session-five-source-union.json',
        '5d9734c108edd3269cd56c264bed8079ded467d5c63a648c84db6eb639f6ab82',
    ),
    'business_768_current_session_group_closeout_path': (
        'docs/refactor/two-server/section-1/inventory/current-session-five-source-closeout.json',
        '8eabc7b2fde077e6404eaee184a359ad2dc79add1a4a7b5ab1a2ed2d5c6b31b2',
    ),
    'business_768_post_security_five_registration_path': (
        'docs/refactor/two-server/section-1/inventory/post-security-five-source-registration.json',
        '56f5d4e76c4cd9edc97689fda6025e9fae9e059a80f3c8a995f457fa7d709bbe',
    ),
    'business_768_post_security_five_closeout_path': (
        'docs/refactor/two-server/section-1/inventory/post-security-five-source-closeout.json',
        '2bfe90e91579d0457b7a3d8008733b53f90d6af5033c4111e33f238e28d934fd',
    ),
}

# The two frozen wrappers describe this one same physical proof; their scenario
# labels/provenance remain distinct metadata. No new or third owner may alias it.
_HISTORICAL_PROOF_ALIAS_OWNERS = frozenset({
    "business_768_live786_manual783_path",
    "business_768_live786_five_source_closeout_path",
})
_HISTORICAL_PROOF_FIELDS = (
    "path", "sha256", "utf8_bytes", "mode", "last_commit", "parent",
    "seed_ids", "test_selectors",
)


def _selector_bindings(scope: ast.Module | ast.ClassDef) -> dict[str, ast.AST | None]:
    """Index only direct definitions; same-scope writes invalidate a name."""
    cells = {}

    def bind(name, definition=None):
        cells[name] = None if name in cells else definition

    def invalidate_class_globals(node):
        # Executed class scopes can write the module namespace; methods cannot.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        if isinstance(node, ast.Global):
            cells.update(dict.fromkeys(node.names))
        for child in ast.iter_child_nodes(node):
            invalidate_class_globals(child)

    def visit(node, direct=False):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(node.name, node if direct else None)
            if isinstance(scope, ast.Module) and isinstance(node, ast.ClassDef):
                invalidate_class_globals(node)
            body_nodes = set(node.body)
            for child in ast.iter_child_nodes(node):
                if child not in body_nodes:
                    visit(child)
            return
        if isinstance(node, ast.Lambda):
            visit(node.args)
            return
        if isinstance(node, ast.comprehension):
            for child in [node.iter, *node.ifs]:
                visit(child)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bind(node.id)
        elif isinstance(scope, ast.ClassDef) and isinstance(node, (ast.Global, ast.Nonlocal)):
            cells.update(dict.fromkeys(node.names))
        elif isinstance(node, ast.alias):
            if node.name == "*":
                cells.update(dict.fromkeys(cells))
            else:
                bind(node.asname or node.name.split(".")[0])
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if node.name:
                bind(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bind(node.rest)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in scope.body:
        visit(statement, True)
    return cells


def _resolve_selectors_in_source(cells: dict, selectors: list[str], path: str) -> bool:
    for selector in selectors:
        parts = selector.split("::")
        if not (2 <= len(parts) <= 3 and parts[0] == path
                and all(name.isidentifier() for name in parts[1:])
                and parts[-1].startswith("test_")):
            return False
        definition = cells.get(parts[1])
        if len(parts) == 3:
            if not isinstance(definition, ast.ClassDef):
                return False
            definition = _selector_bindings(definition).get(parts[2])
        if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
    return True


def inspect_canonical_evidence(root: Path, behavior_id: str | None = None) -> dict:
    """Classify current canonical P1-00b evidence, bound to the current HEAD."""
    root = Path(root).resolve(strict=True)
    loaded: dict[str, tuple[dict, str]] = {}
    historical_origins: dict[int, str] = {}
    blobs: dict[tuple, object] = {}
    definitions: dict[tuple[str, str], dict] = {}
    consumed_bytes = 0
    try:
        head = runner.git(root, "rev-parse", "HEAD")
        if not _HEX40.fullmatch(head):
            raise runner.ProposalError("HEAD is not a full lowercase commit SHA")

        def _validate_safe_path(path: object) -> str:
            if (not isinstance(path, str) or not path or path.startswith("/")
                    or any(char in path for char in ("\x00", "\\", ":"))):
                raise runner.ProposalError("unsafe source path")
            runner.relative_file(root, path)
            return path

        def bounded(read, maximum: int, label: str, *, process: bool = False):
            nonlocal consumed_bytes
            limit = min(maximum, CANONICAL_TOTAL_BUDGET_BYTES - consumed_bytes)
            if limit <= 0:
                raise runner.ProposalError("canonical content budget exhausted")
            try:
                result = read(limit)
            except (SandboxError, runner.ProposalError, OSError, subprocess.SubprocessError) as exc:
                consumed_bytes += limit
                raise runner.ProposalError(label) from exc
            consumed_bytes += len(result.stdout) + len(result.stderr) if process else len(result)
            return result

        def git_blob(commit: str, path: str, *, metadata: bool = False):
            if not isinstance(commit, str) or not _HEX40.fullmatch(commit):
                raise runner.ProposalError("unsafe Git commit reference")
            _validate_safe_path(path)
            key = (commit, path, metadata)
            if key not in blobs:
                blobs[key] = _BLOB_FAILED
                if metadata:
                    result = bounded(
                        lambda limit: _run_bounded_process(
                            ["/usr/bin/git", "--no-replace-objects", "-C", str(root),
                             "--literal-pathspecs", "ls-tree", "-z", commit, "--", path],
                            cwd=root, timeout=30, output_limit=limit,
                            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
                                 "GIT_NO_LAZY_FETCH": "1", "GIT_CONFIG_NOSYSTEM": "1"}),
                        4096, "bounded Git blob read failed", process=True)
                    entry = re.fullmatch(
                        rb"(100644 blob|100755 blob|120000 blob|040000 tree|160000 commit) "
                        rb"[0-9a-f]{40}\t([^\0]+)\0", result.stdout)
                    if result.returncode or entry is None or entry[2] != path.encode("utf-8"):
                        raise runner.ProposalError("invalid Git tree metadata")
                    value = tuple(entry[1].split())
                else:
                    raw = bounded(lambda limit: _git_read(
                        root, ("show", f"{commit}:{path}"), output_limit=limit),
                        CANONICAL_READ_LIMIT_BYTES, "bounded Git blob read failed")
                    value = (raw, runner.sha(raw))
                blobs[key] = value
            if blobs[key] is _BLOB_FAILED:
                raise runner.ProposalError("bounded Git blob read failed")
            return blobs[key]

        def load(path: str) -> tuple[dict, str]:
            _validate_safe_path(path)
            if path not in loaded:
                runner.git(root, "ls-files", "--error-unmatch", "--", path)
                rel_path = runner.relative_file(root, path)
                data = bounded(lambda limit: runner.bounded_file_bytes(
                    root, rel_path, limit, path), CANONICAL_READ_LIMIT_BYTES,
                    "canonical file read failed")
                data_sha = runner.sha(data)
                if data_sha != git_blob(head, path)[1]:
                    raise runner.ProposalError(f"live canonical bytes differ from HEAD: {path}")
                doc = runner.unique_json(data.decode("utf-8"))
                if not isinstance(doc, dict):
                    raise runner.ProposalError(f"canonical document is not an object: {path}")
                loaded[path] = (doc, data_sha)
            return loaded[path]

        def canonical_stage(document: dict, *, records: bool = False) -> bool:
            return (document.get("schema_version") == 1 and document.get("stage") == "P1-00b"
                    and (not records or isinstance(document.get("records"), list)))

        manifest, _ = load(_CANONICAL_ROOT)
        if not canonical_stage(manifest):
            raise runner.ProposalError("invalid canonical manifest schema or stage")
        inventory = manifest.get("inventory")
        sources = manifest.get("evidence_sources")
        counts = manifest.get("counts")
        if any(not isinstance(section, dict) for section in (inventory, sources, counts)):
            raise runner.ProposalError("canonical manifest sections missing")
        expected = inventory.get("expected_seed_count")
        surfaces = inventory.get("paths")
        n_sources = len(sources) - 2
        required_source_keys = {"baseline_path", "base_path", "continuation_path"}
        numbered_sources = {}
        supplemental_sources = {}
        for key in sources:
            if key in required_source_keys:
                continue
            match = re.fullmatch(r"continuation_([1-9][0-9]?)_path", key) if type(key) is str else None
            if match is not None and 2 <= int(match[1]) <= 64:
                numbered_sources[int(match[1])] = sources[key]
            elif key in _HISTORICAL_OWNER_RECEIPTS:
                if sources[key] != _HISTORICAL_OWNER_RECEIPTS[key][0]:
                    raise runner.ProposalError("canonical historical owner path invalid")
                supplemental_sources[key] = sources[key]
            elif (type(key) is str and not key.startswith("continuation_")
                  and re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*_path", key)
                  and type(sources[key]) is str
                  and re.fullmatch(
                      r"docs/refactor/two-server/section-1/inventory/"
                      r"recent-characterization-(?!continuation(?:-|\.))[a-z0-9]+(?:-[a-z0-9]+)*\.json",
                      sources[key])):
                supplemental_sources[key] = sources[key]
            else:
                raise runner.ProposalError("canonical manifest inventory or sources invalid")
        expected_source_keys = required_source_keys | {
            f"continuation_{i}_path" for i in numbered_sources
        } | set(supplemental_sources)
        if (
            type(expected) is not int
            or not isinstance(surfaces, list)
            or len(surfaces) != 4
            or not (3 <= n_sources <= 64)
            or set(sources) != expected_source_keys
            or any(type(k) is not str or type(v) is not str for k, v in sources.items())
        ):
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
        if any(status_by_blocker.get(key, {}).get(field) != "BLOCKED_EXPLICIT"
               for key in _CANONICAL_BLOCKERS for field in ("disposition", "status")):
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
        if not canonical_stage(base, records=True) or not ancestor(base.get("inspected_head")):
            raise runner.ProposalError("base characterization invalid")
        c1, c1_sha = load(sources["continuation_path"])
        c1_base = c1.get("base_inventory")
        if not canonical_stage(c1, records=True) or not isinstance(c1_base, dict) or c1_base.get("path") != sources["base_path"] or c1_base.get("sha256") != base_sha or not ancestor(c1_base.get("inspected_head")):
            raise runner.ProposalError("continuation 1 invalid")

        # Audit the full predecessor closure; only declared sources prove coverage.
        selected = {1: sources["continuation_path"], **numbered_sources}
        if len(set(sources.values())) != len(sources):
            raise runner.ProposalError("canonical source paths conflict")
        path_indexes = {path: index for index, path in selected.items()}
        audited = {1: (sources["continuation_path"], c1, c1_sha)}
        auditing = set()

        def audit_continuation(index: int, path: str) -> tuple[dict, str]:
            if index in audited:
                old_path, document, digest = audited[index]
                if old_path != path:
                    raise runner.ProposalError("canonical predecessor identity conflict")
                return document, digest
            if index in auditing or len(audited) + len(auditing) >= 64:
                raise runner.ProposalError("canonical predecessor graph invalid")
            auditing.add(index)
            current, current_sha = load(path)
            link = current.get("predecessor_inventory")
            if not canonical_stage(current, records=True) or not isinstance(link, dict):
                raise runner.ProposalError(f"continuation {index} invalid")
            predecessor = link.get("path")
            predecessor_index = path_indexes.get(predecessor) if type(predecessor) is str else None
            if predecessor_index is None:
                match = re.fullmatch(
                    r"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-([1-9][0-9]?)\.json",
                    predecessor,
                ) if type(predecessor) is str else None
                if match is None or not 2 <= int(match[1]) <= 64:
                    if set(numbered_sources) != set(range(2, max(numbered_sources) + 1)):
                        raise runner.ProposalError("canonical manifest inventory or sources invalid")
                    raise runner.ProposalError(f"continuation {index} invalid")
                predecessor_index = int(match[1])
                if predecessor_index in selected and selected[predecessor_index] != predecessor:
                    raise runner.ProposalError("canonical predecessor identity conflict")
            previous_selected = max(number for number in selected if number < index)
            if not previous_selected <= predecessor_index < index:
                raise runner.ProposalError(f"continuation {index} invalid")
            _, predecessor_sha = audit_continuation(predecessor_index, predecessor)
            actual_touch = runner.git(root, "log", "-1", "--format=%H", head, "--", predecessor)
            if link.get("sha256") != predecessor_sha or link.get("last_commit") != actual_touch or git_blob(actual_touch, predecessor)[1] != predecessor_sha:
                raise runner.ProposalError(f"continuation {index} invalid")
            auditing.remove(index)
            audited[index] = (path, current, current_sha)
            return current, current_sha

        docs = [base, c1]
        for index in sorted(numbered_sources):
            current, _ = audit_continuation(index, numbered_sources[index])
            docs.append(current)

        # Supplements share the existing continuation allowance, including hidden
        # predecessors, but cannot impersonate a base or a linked continuation.
        if len(audited) + len(supplemental_sources) > 64:
            raise runner.ProposalError("canonical predecessor graph invalid")
        for key, path in supplemental_sources.items():
            document, document_sha = load(path)
            historical = _HISTORICAL_OWNER_RECEIPTS.get(key)
            if historical is not None:
                if document_sha != historical[1]:
                    raise runner.ProposalError(f"immutable historical owner receipt invalid: {path}")
                historical_origins[id(document)] = key
                docs.append(document)
                continue
            status = document.get("status")
            assignment = document.get("assignment_id")
            high_blockers = document.get("canonical_blockers")
            source = document.get("source")
            if (
                type(document.get("schema_version")) is not int
                or not canonical_stage(document, records=True)
                or not isinstance(assignment, str) or not assignment.strip()
                or assignment != assignment.strip()
                or status not in ("PARTIAL_CANONICAL_INTEGRATION", "PARTIAL_RECONCILIATION")
                or (status == "PARTIAL_CANONICAL_INTEGRATION"
                    and (document.get("receipt_type") != status
                         or not isinstance(source, dict)
                         or not ancestor(source.get("accepted_source_commit"))
                         or not ancestor(source.get("accepted_parent"))))
                or ("receipt_type" in document and document["receipt_type"] != status)
                or (status == "PARTIAL_RECONCILIATION"
                    and not ancestor(document.get("inspected_head")))
                or ("inspected_head" in document and not ancestor(document["inspected_head"]))
                or document.get("coverage_status") != "PARTIAL"
                or document.get("seed_acceptance_status") != "FULL_SEED_ACCEPTANCE_NOT_ASSESSED"
                or document.get("closures_authorized") is not False
                or not isinstance(high_blockers, list)
                or any(type(item) is not str for item in high_blockers)
                or not _CANONICAL_BLOCKERS.issubset(high_blockers)
                or document.get("stages") != {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"}
                or "base_inventory" in document or "predecessor_inventory" in document
            ):
                raise runner.ProposalError(f"independent supplemental receipt invalid: {path}")
            docs.append(document)

        current_seen: set[tuple[str, str]] = set()
        current_proofs: dict[tuple[str, str], tuple[str | None, dict]] = {}
        aliased_current: set[tuple[str, str]] = set()
        proven: set[str] = set()
        invalid_current: set[str] = set()
        unknown: set[str] = set()
        for doc in docs:
            for record in doc["records"]:
                seed_ids = record.get("seed_ids") if isinstance(record, dict) else None
                if not isinstance(seed_ids, list) or any(not isinstance(seed, str) or seed not in known for seed in seed_ids):
                    raise runner.ProposalError("record seed IDs invalid")
                # Unbound origin records cannot affect seed coverage.
                if not seed_ids:
                    continue
                # Targeted lookup skips unrelated records, never graph validation.
                if behavior_id is not None and behavior_id not in seed_ids:
                    continue
                selectors = record.get("test_selectors")
                valid = (
                    isinstance(record.get("path"), str)
                    and all(isinstance(record.get(key), str) and pattern.fullmatch(record[key])
                            for key, pattern in (("sha256", _HEX64), ("last_commit", _HEX40)))
                    and isinstance(selectors, list) and bool(selectors)
                    and all(isinstance(item, str) and item for item in selectors)
                )
                if not valid:
                    unknown.update(seed_ids)
                    continue
                try:
                    path = _validate_safe_path(record["path"])
                    runner.git(root, "ls-files", "--error-unmatch", "--", path)
                    last_commit = record["last_commit"]
                    latest_touch = runner.git(root, "log", "-1", "--format=%H", head, "--", path)
                except subprocess.CalledProcessError:
                    unknown.update(seed_ids)
                    continue

                current = last_commit == latest_touch
                if current:
                    for seed in seed_ids:
                        cell = (seed, path)
                        origin = historical_origins.get(id(doc))
                        if cell in current_seen:
                            previous_origin, previous_record = current_proofs[cell]
                            # Typed JSON preserves list order and bool/int distinctions.
                            # Both instances still execute the ordinary proof below;
                            # proven is a set, so this alias never adds another credit.
                            if (cell in aliased_current
                                    or frozenset((origin, previous_origin)) != _HISTORICAL_PROOF_ALIAS_OWNERS
                                    or json.dumps([record.get(field) for field in _HISTORICAL_PROOF_FIELDS],
                                                  ensure_ascii=False, separators=(",", ":"))
                                    != json.dumps([previous_record.get(field) for field in _HISTORICAL_PROOF_FIELDS],
                                                  ensure_ascii=False, separators=(",", ":"))):
                                raise runner.ProposalError(f"conflicting current evidence: {seed}")
                            aliased_current.add(cell)
                        else:
                            current_proofs[cell] = (origin, record)
                        current_seen.add(cell)
                proof_commit = head if current else last_commit
                try:
                    if not ancestor(last_commit):
                        raise runner.ProposalError("historical record invalid")
                    mode, kind = git_blob(proof_commit, path, metadata=True)
                    supported = mode in (b"100644", b"100755") and kind == b"blob" and path.endswith(".py")
                    if supported:
                        raw, digest = git_blob(proof_commit, path)
                        if digest != record["sha256"]:
                            raise runner.ProposalError("historical record invalid")
                        cache_key = (proof_commit, path)
                        if cache_key not in definitions:
                            try:
                                definitions[cache_key] = _selector_bindings(ast.parse(raw.decode("utf-8")))
                            except (SyntaxError, UnicodeError, ValueError):
                                definitions[cache_key] = {}
                        supported = _resolve_selectors_in_source(
                            definitions[cache_key], record["test_selectors"], path)
                except runner.ProposalError as exc:
                    if not current:
                        raise runner.ProposalError("historical record invalid") from exc
                    unknown.update(seed_ids)
                    continue
                if not supported:
                    if not current:
                        raise runner.ProposalError("historical record invalid")
                    invalid_current.update(seed_ids)
                elif current:
                    proven.update(seed_ids)
                unknown.update(seed_ids)
        unknown.difference_update(proven)
        unknown.update(invalid_current)
        proven.difference_update(invalid_current)
        covered_ids = ids if behavior_id is None else [behavior_id] if behavior_id in known else []
        coverage = {bid: "PROVEN_FOR_REQUESTED_SCENARIO" if bid in proven else "UNKNOWN" if bid in unknown else "PARTIAL_OR_SEED_ONLY" for bid in covered_ids}
        result = {"status": "VALID", "expected_seed_count": expected, "seed_count": len(ids), "coverage": coverage}
        if behavior_id is not None:
            result["requested_coverage"] = coverage.get(behavior_id, "NO_MATCHING_EVIDENCE")
        return result
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError, runner.ProposalError) as exc:
        return {"status": "INVENTORY_INCONSISTENT", "reason": str(exc)}


def inventory_view(
    root: Path | str,
    behavior_id: str | None = None,
    *,
    offset: int = 0,
    limit: int = 64,
) -> dict:
    """Bounded, validated presentation view over canonical inventory evidence."""
    if not valid_request(behavior_id, offset, limit):
        return error_report("invalid request parameters")
    try:
        resolved_root = Path(root).resolve(strict=True)
    except (ValueError, OSError, RuntimeError):
        return error_report("invalid repository root")
    try:
        raw = inspect_canonical_evidence(resolved_root, behavior_id)
    except Exception:
        return error_report("classifier execution failed")
    try:
        return format_inventory(raw, behavior_id, offset=offset, limit=limit)
    except Exception:
        return error_report("inventory formatting failed")


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
    inv = sub.add_parser("inventory")
    inv.add_argument("--behavior-id", default=None)
    inv.add_argument("--offset", type=int, default=0)
    inv.add_argument("--limit", type=int, default=64)
    args = parser.parse_args()
    if args.command == "inventory":
        result = inventory_view(
            args.root, args.behavior_id, offset=args.offset, limit=args.limit
        )
        print(json.dumps(result))
        return 0 if (type(result) is dict and result.get("status") == "VALID") else 1
    root = args.root.resolve(strict=True)
    try:
        if args.command == "build":
            result = build(root, root / args.spec, root / args.output)
            print(json.dumps(result))
            if type(result) is dict and result.get("state") == "TASK_READY":
                return 0
            return 1
        result = review(root, root / args.task, args.worker, args.reviewer, args.sha, args.applied)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError) as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
