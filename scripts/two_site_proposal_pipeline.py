#!/usr/bin/env python3
"""Proposal-only coordinator for fresh Gemini Worker and Reviewer orchestration.

This pipeline is proposal-only and only removes orchestration repetition:
Codex still performs final exact task/source/proposal/review inspection,
exact-byte application, tests and all Git operations.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import copy, fcntl, hashlib, json, os, re, stat, subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT, MODEL, WORKER_MODEL, WORKER_EFFORT, ForbiddenToolAttemptError,
        ProposalError, RetryableProviderOutputError, RetryableProviderSessionError,
        _load_bound_opus_fallback_receipt,
        bounded_file_bytes, bounded_file_text, file_sha256, git, load_task,
        run as runner_run, serialize_json, unique_json,
        validate_correction_feedback, validate_result, write_json,
    )
except ModuleNotFoundError:
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MAX_CONTEXT, MODEL, WORKER_MODEL, WORKER_EFFORT, ForbiddenToolAttemptError,
        ProposalError, RetryableProviderOutputError, RetryableProviderSessionError,
        _load_bound_opus_fallback_receipt,
        bounded_file_bytes, bounded_file_text, file_sha256, git, load_task,
        run as runner_run, serialize_json, unique_json,
        validate_correction_feedback, validate_result, write_json,
    )

try:
    from scripts.two_site_pre_review_receipt import (
        candidate_test_assertion_findings,
        p1_00_characterization_bootstrap_findings,
        run_candidate_tests,
        validate_test_receipt,
    )
except ImportError:
    from two_site_pre_review_receipt import (
        candidate_test_assertion_findings,
        p1_00_characterization_bootstrap_findings,
        run_candidate_tests,
        validate_test_receipt,
    )

try:
    from scripts.two_site_semantic_attempts import reserve_semantic_attempt
except ModuleNotFoundError:
    from two_site_semantic_attempts import reserve_semantic_attempt

try:
    from scripts.two_site_quota_evidence import provider_for_model
except ImportError:
    from two_site_quota_evidence import provider_for_model

try:
    from scripts.two_site_worker_model_routing import (
        DEFAULT_POLICY_PATH,
        load_worker_routing_policy,
        select_worker_model_route,
    )
except ImportError:
    from two_site_worker_model_routing import (
        DEFAULT_POLICY_PATH,
        load_worker_routing_policy,
        select_worker_model_route,
    )

try:
    from scripts.two_site_retry_syntax_hint import extract_syntax_hint
except ImportError:
    from two_site_retry_syntax_hint import extract_syntax_hint

try:
    from scripts.two_site_required_test_gate import required_selector_gate_feedback
except ImportError:
    from two_site_required_test_gate import required_selector_gate_feedback

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "refactor/two-site-architecture"
PROTECTED_REFS = (
    "refs/heads/main",
    "refs/remotes/origin/main",
    "refs/heads/candidate/wa-ir-standby-v1",
)
RUN_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_FALLBACK_FAILED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,76}$")
QUOTA_EVIDENCE_KEYS = frozenset({"kind", "provider", "model", "conversation_id", "reset_at_utc"})
QUOTA_ATTEMPT_KEYS = frozenset({"role", "run_id", "status_sha256"})
_QUOTA_CONTINUATION_KEYS = frozenset({
    "schema_version",
    "request_type",
    "request_id",
    "controller_run_id",
    "original_controller_run_id",
    "original_request_id",
    "claim_sha256",
    "original_wait_sha256",
    "semantic_task_fingerprint",
    "task_id",
    "task_path",
    "task_sha256",
    "source_head",
    "assignment_id",
    "assignment_sha256",
    "run_prefix",
    "role",
    "claimed_at_utc",
    "timeout",
    "created_at",
})


def _derive_verifier_resume_artifacts(
    admitted_request: Mapping[str, Any],
    validated_terminal_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure derivation of verifier resume artifacts from continuation request and terminal receipt."""
    try:
        from scripts.two_site_provider_broker import (
            PIPELINE_QUOTA_KEYS,
            RECEIPT_KEYS,
            REQUEST_KEYS,
        )
    except ImportError:
        from two_site_provider_broker import (
            PIPELINE_QUOTA_KEYS,
            RECEIPT_KEYS,
            REQUEST_KEYS,
        )

    if not isinstance(admitted_request, dict):
        raise ProposalError(f"admitted_request must be dict, got {type(admitted_request).__name__}")
    if not isinstance(validated_terminal_receipt, dict):
        raise ProposalError(
            f"validated_terminal_receipt must be dict, got {type(validated_terminal_receipt).__name__}"
        )

    if set(admitted_request.keys()) != _QUOTA_CONTINUATION_KEYS:
        raise ProposalError("admitted_request keys mismatch with QUOTA_CONTINUATION schema")

    if admitted_request.get("schema_version") != "1.0":
        raise ProposalError("admitted_request schema_version must be '1.0'")
    if admitted_request.get("request_type") != "QUOTA_CONTINUATION":
        raise ProposalError("admitted_request request_type must be 'QUOTA_CONTINUATION'")
    if admitted_request.get("role") != "verifier":
        raise ProposalError("admitted_request role must be 'verifier'")

    if set(validated_terminal_receipt.keys()) != RECEIPT_KEYS:
        raise ProposalError("validated_terminal_receipt keys mismatch with RECEIPT_KEYS")

    receipt_req = validated_terminal_receipt.get("request")
    if not isinstance(receipt_req, dict) or set(receipt_req.keys()) != REQUEST_KEYS:
        raise ProposalError("receipt request keys mismatch with ordinary REQUEST_KEYS")

    lineage_bindings = (
        ("request_id", "original_request_id"),
        ("controller_run_id", "original_controller_run_id"),
        ("task_path", "task_path"),
        ("task_sha256", "task_sha256"),
        ("source_head", "source_head"),
        ("assignment_id", "assignment_id"),
        ("assignment_sha256", "assignment_sha256"),
    )
    for req_field, admitted_field in lineage_bindings:
        req_val = receipt_req.get(req_field)
        admitted_val = admitted_request.get(admitted_field)
        if type(req_val) is not type(admitted_val) or req_val != admitted_val:
            raise ProposalError(
                f"Receipt request lineage field {req_field!r} mismatch: "
                f"{req_val!r} != admitted {admitted_field!r} ({admitted_val!r})"
            )

    orig_run_prefix = receipt_req.get("run_prefix")
    if not isinstance(orig_run_prefix, str) or not RUN_PREFIX_RE.fullmatch(orig_run_prefix):
        raise ProposalError(f"receipt request run_prefix invalid: {orig_run_prefix!r}")

    response = validated_terminal_receipt.get("response")
    if not isinstance(response, dict):
        raise ProposalError("receipt response must be dict")
    stdout = response.get("stdout")
    if not isinstance(stdout, str):
        raise ProposalError("receipt response stdout must be str")

    try:
        pipeline_state = unique_json(stdout)
    except Exception as exc:
        raise ProposalError(f"failed to parse pipeline state from stdout: {exc}") from exc

    if not isinstance(pipeline_state, dict) or set(pipeline_state.keys()) != PIPELINE_QUOTA_KEYS:
        raise ProposalError("pipeline state keys mismatch with PIPELINE_QUOTA_KEYS")

    if pipeline_state.get("state") != "TASK_BLOCKED":
        raise ProposalError(f"pipeline state must be 'TASK_BLOCKED', got {pipeline_state.get('state')!r}")
    if pipeline_state.get("blockers") != ["observational quota, no replay authority"]:
        raise ProposalError(f"pipeline blockers sentinel mismatch: {pipeline_state.get('blockers')!r}")

    for bound_field in ("task_id", "semantic_task_fingerprint", "source_head"):
        state_val = pipeline_state.get(bound_field)
        admitted_val = admitted_request.get(bound_field)
        if type(state_val) is not type(admitted_val) or state_val != admitted_val:
            raise ProposalError(
                f"pipeline state field {bound_field!r} mismatch: {state_val!r} != {admitted_val!r}"
            )

    quota_attempt = pipeline_state.get("quota_attempt")
    if not isinstance(quota_attempt, dict) or set(quota_attempt.keys()) != QUOTA_ATTEMPT_KEYS:
        raise ProposalError("pipeline quota_attempt keys mismatch with QUOTA_ATTEMPT_KEYS")
    if quota_attempt.get("role") != "verifier":
        raise ProposalError(f"quota_attempt.role must be 'verifier', got {quota_attempt.get('role')!r}")

    status_sha = quota_attempt.get("status_sha256")
    if not isinstance(status_sha, str) or not SHA_PATTERN.fullmatch(status_sha):
        raise ProposalError(f"quota_attempt.status_sha256 must be 64 lowercase hex, got {status_sha!r}")

    verifier_run_id = quota_attempt.get("run_id")
    if not isinstance(verifier_run_id, str):
        raise ProposalError(f"quota_attempt.run_id must be str, got {type(verifier_run_id).__name__}")
    verifier_match = re.fullmatch(
        rf"^{re.escape(orig_run_prefix)}-(?:reviewer|verifier)-([0-2])(?:-retry-[12])?$",
        verifier_run_id,
    )
    if not verifier_match:
        raise ProposalError(
            f"quota_attempt.run_id {verifier_run_id!r} does not match original run_prefix "
            f"{orig_run_prefix!r} reviewer/verifier pattern"
        )
    verifier_index = int(verifier_match.group(1))

    correction_count = pipeline_state.get("correction_count")
    if type(correction_count) is not int or type(correction_count) is bool or not (0 <= correction_count <= 2):
        raise ProposalError(f"correction_count must be exact int 0..2, got {correction_count!r}")

    if verifier_index != correction_count:
        raise ProposalError(
            f"verifier index {verifier_index} does not match correction_count {correction_count}"
        )

    final_worker_run_id = pipeline_state.get("final_worker_run_id")
    if not isinstance(final_worker_run_id, str):
        raise ProposalError(f"final_worker_run_id must be str, got {type(final_worker_run_id).__name__}")
    worker_match = re.fullmatch(
        rf"^{re.escape(orig_run_prefix)}-worker-([0-2])(?:-retry-[12])?$",
        final_worker_run_id,
    )
    if not worker_match:
        raise ProposalError(
            f"final_worker_run_id {final_worker_run_id!r} does not match original run_prefix "
            f"{orig_run_prefix!r} worker pattern"
        )
    worker_index = int(worker_match.group(1))
    if worker_index != correction_count:
        raise ProposalError(
            f"final_worker_run_id index {worker_index} does not match correction_count {correction_count}"
        )

    proposal_sha = pipeline_state.get("proposal_sha256")
    if not isinstance(proposal_sha, str) or not SHA_PATTERN.fullmatch(proposal_sha):
        raise ProposalError(f"proposal_sha256 must be 64 lowercase hex, got {proposal_sha!r}")

    proposal_path = pipeline_state.get("proposal_path")
    expected_proposal_path = f".local/two-site-refactor/proposals/{final_worker_run_id}/result.json"
    if proposal_path != expected_proposal_path:
        raise ProposalError(
            f"proposal_path mismatch: {proposal_path!r} != {expected_proposal_path!r}"
        )

    if pipeline_state.get("pre_review_test_state") != "PASSED":
        raise ProposalError(
            f"pre_review_test_state must be 'PASSED', got {pipeline_state.get('pre_review_test_state')!r}"
        )

    test_sha = pipeline_state.get("pre_review_test_sha256")
    if not isinstance(test_sha, str) or not SHA_PATTERN.fullmatch(test_sha):
        raise ProposalError(f"pre_review_test_sha256 must be 64 lowercase hex, got {test_sha!r}")

    test_path = pipeline_state.get("pre_review_test_path")
    expected_test_path = f".local/two-site-refactor/pipeline/{orig_run_prefix}/pre-review-tests-{correction_count}.json"
    if test_path != expected_test_path:
        raise ProposalError(
            f"pre_review_test_path mismatch: {test_path!r} != {expected_test_path!r}"
        )

    if pipeline_state.get("final_reviewer_run_id") is not None:
        raise ProposalError(
            f"final_reviewer_run_id must be None at blocked verifier, got {pipeline_state.get('final_reviewer_run_id')!r}"
        )
    if pipeline_state.get("reviewer_path") is not None:
        raise ProposalError(
            f"reviewer_path must be None at blocked verifier, got {pipeline_state.get('reviewer_path')!r}"
        )

    quota_evidence = pipeline_state.get("quota_evidence")
    if not isinstance(quota_evidence, dict) or set(quota_evidence.keys()) != QUOTA_EVIDENCE_KEYS:
        raise ProposalError("quota_evidence keys mismatch with QUOTA_EVIDENCE_KEYS")

    return {
        "task_id": pipeline_state["task_id"],
        "semantic_task_fingerprint": pipeline_state["semantic_task_fingerprint"],
        "source_head": pipeline_state["source_head"],
        "original_run_prefix": orig_run_prefix,
        "correction_count": correction_count,
        "final_worker_run_id": final_worker_run_id,
        "proposal_path": proposal_path,
        "proposal_sha256": proposal_sha,
        "pre_review_test_path": test_path,
        "pre_review_test_sha256": test_sha,
        "pre_review_test_state": "PASSED",
        "verifier_run_id": verifier_run_id,
        "verifier_status_sha256": status_sha,
        "quota_evidence": json.loads(json.dumps(quota_evidence)),
    }


derive_verifier_resume_artifacts = _derive_verifier_resume_artifacts


def _load_verifier_pre_review_receipt(
    root: Path | str,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    """Load and validate the pre-review test receipt recorded in verifier resume artifacts."""
    if isinstance(root, str):
        root = Path(root)
    elif not isinstance(root, Path):
        raise ProposalError("root must be a Path or str")

    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"project root does not exist: {exc}") from exc
    if not resolved_root.is_dir():
        raise ProposalError("project root must be a directory")

    if not isinstance(artifacts, Mapping) or isinstance(artifacts, (str, bytes)):
        raise ProposalError(f"artifacts must be a mapping, got {type(artifacts).__name__}")

    orig_run_prefix = artifacts.get("original_run_prefix")
    if not isinstance(orig_run_prefix, str) or not RUN_PREFIX_RE.fullmatch(orig_run_prefix):
        raise ProposalError(f"artifacts original_run_prefix invalid: {orig_run_prefix!r}")

    correction_count = artifacts.get("correction_count")
    if type(correction_count) is not int or type(correction_count) is bool or not (0 <= correction_count <= 2):
        raise ProposalError(f"artifacts correction_count must be exact int 0..2, got {correction_count!r}")

    expected_test_path = (
        f".local/two-site-refactor/pipeline/{orig_run_prefix}/pre-review-tests-{correction_count}.json"
    )
    pre_review_test_path = artifacts.get("pre_review_test_path")
    if pre_review_test_path != expected_test_path:
        raise ProposalError(
            f"artifacts pre_review_test_path mismatch: {pre_review_test_path!r} != {expected_test_path!r}"
        )

    proposal_sha = artifacts.get("proposal_sha256")
    if not isinstance(proposal_sha, str) or not SHA_PATTERN.fullmatch(proposal_sha):
        raise ProposalError(f"artifacts proposal_sha256 must be 64 lowercase hex, got {proposal_sha!r}")

    receipt_sha = artifacts.get("pre_review_test_sha256")
    if not isinstance(receipt_sha, str) or not SHA_PATTERN.fullmatch(receipt_sha):
        raise ProposalError(f"artifacts pre_review_test_sha256 must be 64 lowercase hex, got {receipt_sha!r}")

    test_state = artifacts.get("pre_review_test_state")
    if test_state != "PASSED":
        raise ProposalError(f"artifacts pre_review_test_state must be 'PASSED', got {test_state!r}")

    raw_bytes = bounded_file_bytes(
        resolved_root, Path(expected_test_path), MAX_CONTEXT, "pre-review test receipt"
    )

    computed_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_sha != receipt_sha:
        raise ProposalError(
            f"pre-review test receipt SHA mismatch: computed {computed_sha!r} != expected {receipt_sha!r}"
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProposalError("pre-review test receipt is not valid UTF-8") from exc

    try:
        parsed = unique_json(text)
    except Exception as exc:
        raise ProposalError(f"failed to parse pre-review test receipt JSON: {exc}") from exc

    normalized = validate_test_receipt(parsed, proposal_sha, require_passed=True)

    if normalized.get("state") != "PASSED" or normalized.get("state") != test_state:
        raise ProposalError(
            f"normalized receipt state {normalized.get('state')!r} does not match expected {test_state!r}"
        )

    return copy.deepcopy(normalized)


load_verifier_pre_review_receipt = _load_verifier_pre_review_receipt


class QuotaBlockError(ProposalError):
    """Raised when runner halts with validated Gemini quota evidence."""

    def __init__(self, quota_evidence: dict[str, Any], quota_attempt: dict[str, str] | None = None) -> None:
        super().__init__("observational quota, no replay authority")
        self.quota_evidence = dict(quota_evidence)
        self.evidence = self.quota_evidence
        if quota_attempt is not None:
            if not isinstance(quota_attempt, dict) or set(quota_attempt.keys()) != QUOTA_ATTEMPT_KEYS:
                raise ProposalError("invalid quota_attempt structure")
            self.quota_attempt = dict(quota_attempt)
        else:
            self.quota_attempt = None


def validate_quota_evidence(
    evidence: Any,
    expected_model: str,
    expected_conv_id: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(evidence, dict):
        return None
    if set(evidence.keys()) != QUOTA_EVIDENCE_KEYS:
        return None
    expected_provider = provider_for_model(expected_model)
    if expected_provider is None:
        return None
    kind = evidence.get("kind")
    if kind not in ("QUOTA_RESET_KNOWN", "QUOTA_RESET_UNKNOWN"):
        return None
    if evidence.get("provider") != expected_provider:
        return None
    model = evidence.get("model")
    if not isinstance(model, str) or len(model) > 128 or model != expected_model:
        return None
    conv_id = evidence.get("conversation_id")
    if not isinstance(conv_id, str) or not conv_id or len(conv_id) > 128:
        return None
    if not all(33 <= ord(c) <= 126 for c in conv_id):
        return None
    if expected_conv_id is not None and conv_id != expected_conv_id:
        return None
    reset_at = evidence.get("reset_at_utc")
    if kind == "QUOTA_RESET_UNKNOWN":
        if reset_at is not None:
            return None
    else:
        if not isinstance(reset_at, str) or not _CANONICAL_UTC_RE.fullmatch(reset_at):
            return None
        try:
            dt = datetime.fromisoformat(reset_at[:-1] + "+00:00")
            if dt.tzinfo != timezone.utc:
                return None
        except Exception:
            return None
    return {
        "kind": kind,
        "provider": expected_provider,
        "model": model,
        "conversation_id": conv_id,
        "reset_at_utc": reset_at,
    }


def _extract_bound_quota_evidence(
    root: Path,
    run_dir: Path,
    expected_run_id: str,
    role: str,
    expected_head: str,
    expected_task_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]] | None:
    try:
        if run_dir.is_symlink() or os.path.islink(run_dir):
            return None
        status_path = run_dir / "status.json"
        if status_path.is_symlink() or os.path.islink(status_path):
            return None
        raw_bytes = bounded_file_bytes(root, status_path, MAX_CONTEXT, f"{role} status")
        status_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        raw_text = raw_bytes.decode("utf-8")
        status = unique_json(raw_text)
    except Exception:
        return None

    if not isinstance(status, dict):
        return None

    if role not in ("worker", "verifier"):
        return None

    if role == "verifier":
        expected_model = MODEL
    elif role == "worker":
        if isinstance(expected_task_id, str) and expected_task_id.strip():
            if status.get("task_id") != expected_task_id:
                return None
            try:
                expected_model = expected_role_model("worker", expected_task_id)
            except Exception:
                return None
        else:
            try:
                policy = load_worker_routing_policy(ROOT / DEFAULT_POLICY_PATH, base_dir=ROOT)
            except Exception:
                return None
            if policy.enabled:
                return None
            expected_model = WORKER_MODEL
    else:
        return None

    if status.get("run_id") != expected_run_id:
        return None
    if status.get("role") != role:
        return None
    if status.get("head") != expected_head:
        return None
    if status.get("model") != expected_model:
        return None
    if status.get("state") != "BLOCKED":
        return None

    evidence = status.get("quota_evidence")
    expected_conv_id = status.get("conversation_id")
    if expected_conv_id is not None and not isinstance(expected_conv_id, str):
        return None

    valid_evidence = validate_quota_evidence(evidence, expected_model, expected_conv_id)
    if valid_evidence is None:
        return None

    quota_attempt = {
        "role": role,
        "run_id": expected_run_id,
        "status_sha256": status_sha256,
    }
    return valid_evidence, quota_attempt


def ref_snapshot(root: Path) -> dict[str, str | None]:
    result = {}
    for ref in PROTECTED_REFS:
        try:
            result[ref] = git(root, "show-ref", "--verify", "--hash", ref)
        except subprocess.CalledProcessError:
            result[ref] = None
    return result


def check_state(root: Path, head: str, refs: dict[str, str | None], task_path: Path, task: dict, sources: dict) -> None:
    if git(root, "branch", "--show-current") != BRANCH:
        raise ProposalError(f"branch drift: expected {BRANCH}")
    if git(root, "rev-parse", "HEAD") != head:
        raise ProposalError(f"HEAD drift: expected {head}")
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ProposalError("worktree drift: untracked or modified files detected")
    if ref_snapshot(root) != refs:
        raise ProposalError("protected refs drift detected")
    cur_task, cur_sources = load_task(root, task_path)
    if cur_task != task or cur_sources != sources:
        raise ProposalError("task or source files changed during execution")


@contextmanager
def pipeline_lock(root: Path):
    root = root.resolve(strict=True)
    pipeline_dir = root / ".local/two-site-refactor/pipeline"
    if pipeline_dir.resolve() != pipeline_dir and pipeline_dir.exists():
        raise ProposalError("pipeline storage cannot be a symlink")
    pipeline_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if pipeline_dir.resolve(strict=True) != pipeline_dir or not pipeline_dir.is_relative_to(root):
        raise ProposalError("pipeline storage cannot be a symlink")
    lock_path = pipeline_dir / ".pipeline.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ProposalError("cannot safely open pipeline lock") from exc
    owned = False
    try:
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1 or lock_stat.st_uid != os.geteuid():
            raise ProposalError("pipeline lock is not a trusted regular file")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProposalError("another proposal pipeline is already active") from exc
        owned = True
        yield pipeline_dir
    finally:
        if owned:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def normalize_finding(text: str) -> str:
    return " ".join(text.strip().lower().split())


def expected_role_model(role: str, task_id: str) -> str:
    if role == "verifier":
        return MODEL
    if role == "worker":
        policy = load_worker_routing_policy(ROOT / DEFAULT_POLICY_PATH, base_dir=ROOT)
        route = select_worker_model_route(
            task_id,
            policy=policy,
            default_model=WORKER_MODEL,
            default_effort=WORKER_EFFORT,
        )
        return route.model
    raise ProposalError(f"unsupported role: {role!r}")


def expected_bound_role_model(
    root: Path,
    role: str,
    run_id: str,
    expected_head: str,
    expected_task: dict,
    expected_sources: dict,
    status: dict,
) -> str:
    if not isinstance(status, dict):
        raise ProposalError(f"status must be dict, got {type(status).__name__}")
    has_fallback_id = "fallback_from_run_id" in status
    has_fallback_receipt = "fallback_receipt" in status
    if not has_fallback_id and not has_fallback_receipt:
        task_id = expected_task.get("task_id") if isinstance(expected_task, dict) else None
        if isinstance(task_id, str) and task_id.strip():
            return expected_role_model(role, task_id)
        if role == "verifier":
            return expected_role_model(role, "")
        if role == "worker":
            policy = load_worker_routing_policy(ROOT / DEFAULT_POLICY_PATH, base_dir=ROOT)
            if policy.enabled:
                raise ProposalError("missing or invalid top-level task_id in expected_task")
            return WORKER_MODEL
        raise ProposalError(f"unsupported role: {role!r}")

    if not (has_fallback_id and has_fallback_receipt):
        raise ProposalError("asymmetric fallback keys in status")

    failed_id = status.get("fallback_from_run_id")
    status_receipt = status.get("fallback_receipt")
    if failed_id is None or status_receipt is None:
        raise ProposalError("fallback key values cannot be null")
    if not isinstance(failed_id, str) or not isinstance(status_receipt, dict):
        raise ProposalError("invalid fallback key value types")

    if role != "worker":
        raise ProposalError(f"fallback requires role 'worker', got {role!r}")

    task_id = expected_task.get("task_id") if isinstance(expected_task, dict) else None
    if not isinstance(task_id, str) or not task_id.strip():
        raise ProposalError("missing or invalid top-level task_id in expected_task")

    policy = load_worker_routing_policy(ROOT / DEFAULT_POLICY_PATH, base_dir=ROOT)
    if not policy.enabled:
        raise ProposalError("routing policy not enabled for fallback")

    route = select_worker_model_route(
        task_id,
        policy=policy,
        default_model=WORKER_MODEL,
        default_effort=WORKER_EFFORT,
    )
    if route.model != policy.model:
        raise ProposalError("fallback requires selected route model to match policy model exactly")

    if not _FALLBACK_FAILED_ID_RE.fullmatch(failed_id) or failed_id.endswith("-gf"):
        raise ProposalError(f"invalid fallback_from_run_id: {failed_id!r}")

    expected_fallback_run_id = f"{failed_id}-gf"
    if len(expected_fallback_run_id) > 80 or run_id != expected_fallback_run_id:
        raise ProposalError(
            f"run_id mismatch for fallback: expected {expected_fallback_run_id!r}, got {run_id!r}"
        )

    if status.get("run_id") != run_id:
        raise ProposalError(
            f"status run_id mismatch: expected {run_id!r}, got {status.get('run_id')!r}"
        )

    if status.get("model") != WORKER_MODEL:
        raise ProposalError(
            f"status model mismatch: expected {WORKER_MODEL!r}, got {status.get('model')!r}"
        )

    controller_id = status.get("controller_run_id")
    if not isinstance(controller_id, str) or not controller_id.strip():
        raise ProposalError("missing or empty controller_run_id in status")

    canonical_receipt = _load_bound_opus_fallback_receipt(
        root,
        failed_id,
        expected_task,
        expected_sources,
        expected_head,
        controller_id,
    )
    if status_receipt != canonical_receipt:
        raise ProposalError("status fallback_receipt does not match canonical receipt")

    return WORKER_MODEL


def validate_run(
    root: Path, run_dir: Path, role: str, expected_run_id: str, expected_head: str,
    expected_task: dict, expected_sources: dict, seen_convs: set[str],
    prior_worker: tuple[str, str, str, str] | None = None, expected_feedback_sha: str | None = None,
    expected_test_receipt: dict | None = None, expected_test_evidence_sha: str | None = None,
) -> tuple[dict, dict, str, str]:
    status = unique_json(bounded_file_text(root, run_dir / "status.json", MAX_CONTEXT, f"{role} status"))
    snapshot = unique_json(bounded_file_text(root, run_dir / "task.json", 3 * MAX_CONTEXT, f"{role} task"))
    raw_result_bytes = bounded_file_bytes(root, run_dir / "result.json", MAX_CONTEXT, f"{role} result")
    result_text = raw_result_bytes.decode("utf-8")
    result = unique_json(result_text)
    validate_result(result, expected_task, expected_sources, role, enforce_raw_transport=False)
    result_sha = hashlib.sha256(raw_result_bytes).hexdigest()
    expected_model = expected_bound_role_model(
        root, role, expected_run_id, expected_head, expected_task, expected_sources, status
    )
    if (
        status.get("run_id") != expected_run_id or status.get("role") != role
        or status.get("head") != expected_head or status.get("model") != expected_model
        or status.get("result_sha256") != result_sha or snapshot.get("source_head") != expected_head
        or snapshot.get("task") != expected_task or snapshot.get("sources") != expected_sources
    ):
        raise ProposalError(f"metadata or snapshot mismatch in {expected_run_id}")
    if role == "worker":
        if status.get("state") != "PROPOSAL_READY" or result.get("status") != "PROPOSAL_READY":
            raise ProposalError(f"worker did not complete PROPOSAL_READY in {expected_run_id}")
    elif status.get("state") not in {"VERIFIED", "CHANGES_REQUIRED"} or result.get("status") != status.get("state"):
        raise ProposalError(f"reviewer returned invalid state {status.get('state')} in {expected_run_id}")
    if prior_worker is not None:
        prior_run_id, prior_sha, prior_text, prior_conv = prior_worker
        if (
            status.get("input_worker_run_id") != prior_run_id
            or status.get("input_proposal_sha256") != prior_sha
            or status.get("input_worker_conversation_id") != prior_conv
            or snapshot.get("proposal") != prior_text
        ):
            raise ProposalError(f"input worker binding mismatch in {expected_run_id}")
        if expected_feedback_sha is not None and status.get("input_feedback_sha256") != expected_feedback_sha:
            raise ProposalError(f"feedback SHA mismatch in {expected_run_id}")
    elif (
        status.get("input_worker_run_id") is not None
        or status.get("input_worker_conversation_id") is not None
        or status.get("input_proposal_sha256") is not None
    ):
        raise ProposalError(f"initial worker should not have input worker bindings in {expected_run_id}")
    if (expected_test_receipt is None) != (expected_test_evidence_sha is None):
        raise ProposalError(f"test evidence receipt and SHA must be provided together in {expected_run_id}")
    if expected_test_receipt is not None and expected_test_evidence_sha is not None:
        if role != "verifier":
            raise ProposalError(f"test evidence requires role verifier in {expected_run_id}")
        if prior_worker is None:
            raise ProposalError(f"test evidence requires prior worker in {expected_run_id}")
        prior_sha = prior_worker[1]
        validated_receipt = validate_test_receipt(
            expected_test_receipt, prior_sha, require_passed=True
        )
        if snapshot.get("test_evidence") != validated_receipt:
            raise ProposalError(f"task test_evidence mismatch in {expected_run_id}")
        if status.get("input_test_evidence_sha256") != expected_test_evidence_sha:
            raise ProposalError(f"status input_test_evidence_sha256 mismatch in {expected_run_id}")
    else:
        if "test_evidence" in snapshot or "input_test_evidence_sha256" in status:
            raise ProposalError(f"unexpected test evidence in {expected_run_id}")
    conv_id = status.get("conversation_id")
    if not isinstance(conv_id, str) or not conv_id.strip():
        raise ProposalError(f"conversation_id missing or empty in {expected_run_id}")
    if conv_id in seen_convs:
        raise ProposalError(f"reused conversation_id in {expected_run_id}")
    seen_convs.add(conv_id)
    return result, status, result_sha, result_text


def persist_pre_review_tests(
    root: Path,
    evidence_dir: Path,
    task: dict,
    worker_result: dict,
    worker_sha: str,
    index: int,
    state_check: Callable[[], None],
    state: dict,
) -> tuple[dict, Path, str]:
    if isinstance(root, str):
        root = Path(root)
    elif not isinstance(root, Path):
        raise ProposalError("root must be a Path or str")
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError("root must be an existing directory") from exc
    if not root.is_dir() or resolved_root != root:
        raise ProposalError("root must be a resolved canonical directory")

    if isinstance(evidence_dir, str):
        evidence_dir = Path(evidence_dir)
    elif not isinstance(evidence_dir, Path):
        raise ProposalError("evidence_dir must be a Path or str")
    try:
        resolved_evidence = evidence_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError("evidence_dir must be an existing directory") from exc
    if not evidence_dir.is_dir() or resolved_evidence != evidence_dir:
        raise ProposalError("evidence_dir must be an existing resolved canonical directory")
    if not evidence_dir.is_relative_to(root) or evidence_dir == root:
        raise ProposalError("evidence_dir must be inside root")

    if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index <= 2):
        raise ProposalError("index must be an integer between 0 and 2")

    if not isinstance(worker_sha, str) or not SHA_PATTERN.fullmatch(worker_sha):
        raise ProposalError("worker_sha must be a lowercase 64-hex SHA")

    receipt_path = evidence_dir / f"pre-review-tests-{index}.json"
    if receipt_path.exists() or receipt_path.is_symlink() or os.path.lexists(receipt_path):
        raise ProposalError(f"test receipt path already exists or is a symlink: {receipt_path}")

    if not isinstance(state, dict):
        raise ProposalError("state must be a dict")
    if not callable(state_check):
        raise ProposalError("state_check must be callable")

    receipt = run_candidate_tests(
        root, task, worker_result, worker_sha, timeout=1200
    )
    write_json(receipt_path, receipt)

    raw_bytes = bounded_file_bytes(
        root, receipt_path, MAX_CONTEXT, "pre-review test receipt"
    )
    expected_bytes = serialize_json(receipt)
    if raw_bytes != expected_bytes:
        raise ProposalError("test receipt byte mismatch with serialized receipt")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProposalError("test receipt contains invalid UTF-8") from exc
    parsed = unique_json(text)
    normalized = validate_test_receipt(parsed, worker_sha)
    if parsed != receipt or normalized != receipt:
        raise ProposalError("test receipt semantic mismatch with serialized receipt")

    receipt_sha = hashlib.sha256(raw_bytes).hexdigest()

    state.update(
        pre_review_test_path=receipt_path.relative_to(root).as_posix(),
        pre_review_test_sha256=receipt_sha,
        pre_review_test_state=normalized["state"],
    )
    state_check()
    return normalized, receipt_path, receipt_sha


def _bound_utf8_tail(text: str, max_bytes: int = 8192) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    tail = encoded[-max_bytes:]
    for offset in range(min(4, len(tail))):
        try:
            return tail[offset:].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return tail.decode("utf-8", errors="ignore")


def _bound_utf8(text: str, max_bytes: int = 8192) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    head = encoded[:max_bytes]
    for offset in range(min(4, len(head))):
        try:
            return head[:len(head) - offset].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return head.decode("utf-8", errors="ignore")


class _FailureNameNormalizer(ast.NodeTransformer):
    """Remove local variable spelling while retaining asserted APIs and constants."""

    def visit_Name(self, node: ast.Name) -> ast.Name:  # noqa: N802 - ast API name
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)


def _normalized_failure_statement(statement: str) -> str:
    compact = " ".join(statement.strip().split())
    try:
        tree = ast.parse(compact)
        normalized = _FailureNameNormalizer().visit(tree)
        ast.fix_missing_locations(normalized)
        return ast.dump(normalized, annotate_fields=True, include_attributes=False)
    except (SyntaxError, ValueError, RecursionError):
        return compact


def test_failure_facts(receipt: Mapping[str, Any]) -> dict[str, list[str]]:
    """Extract bounded stable pytest root-cause facts, excluding trace/warning noise."""
    proposal_sha = (
        receipt.get("proposal_sha256")
        if isinstance(receipt, Mapping) and not isinstance(receipt, (str, bytes))
        else ""
    )
    if not isinstance(proposal_sha, str):
        proposal_sha = ""
    validated = validate_test_receipt(receipt, proposal_sha)
    if validated["state"] != "FAILED":
        raise ProposalError(f"receipt state must be FAILED, got {validated['state']}")

    tests: list[str] = []
    exceptions: list[str] = []
    statements: list[str] = []

    def add_unique(bucket: list[str], value: str, limit: int) -> None:
        value = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", " ".join(value.split()))[:1024]
        if value and value not in bucket and len(bucket) < limit:
            bucket.append(value)

    combined = validated["stdout_tail"] + "\n" + validated["stderr_tail"]
    for raw_line in combined.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("FAILED ") or stripped.startswith("ERROR "):
            add_unique(tests, stripped, 16)
        if stripped.startswith("E "):
            add_unique(exceptions, stripped[2:].strip(), 16)
        marker = raw_line.lstrip()
        if marker.startswith(">"):
            add_unique(statements, _normalized_failure_statement(marker[1:]), 16)

    if not tests and not exceptions and not statements:
        fallback = _bound_utf8(" ".join(combined.split()), 2048)
        if fallback:
            exceptions.append(fallback)
    return {"tests": tests, "exceptions": exceptions, "statements": statements}


def test_failure_signature(receipt: Mapping[str, Any]) -> str:
    proposal_sha = (
        receipt.get("proposal_sha256")
        if isinstance(receipt, Mapping) and not isinstance(receipt, (str, bytes))
        else ""
    )
    if not isinstance(proposal_sha, str):
        proposal_sha = ""
    validated = validate_test_receipt(receipt, proposal_sha)
    if validated["state"] != "FAILED":
        raise ProposalError(f"receipt state must be FAILED, got {validated['state']}")
    facts = test_failure_facts(validated)
    payload = {
        "returncode": validated["returncode"],
        "selectors": validated["selectors"],
        "facts": {k: sorted(v) for k, v in facts.items()},
    }
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest().lower()


def test_gate_feedback(
    receipt: Mapping[str, Any],
    proposal_sha: str,
) -> dict[str, Any]:
    validated = validate_test_receipt(receipt, proposal_sha)
    if validated["state"] != "FAILED":
        raise ProposalError(f"receipt state must be FAILED, got {validated['state']}")

    rc = validated["returncode"]
    selectors = validated["selectors"]
    facts = test_failure_facts(validated)

    selector_count = len(selectors)
    sample_limit = 10
    if selector_count <= sample_limit:
        selector_sample = ", ".join(selectors)
    else:
        selector_sample = ", ".join(selectors[:sample_limit]) + f" ... ({selector_count - sample_limit} more)"

    root_causes = facts["exceptions"][:3] or facts["tests"][:3] or facts["statements"][:3]
    cause_text = "; ".join(root_causes)
    finding = _bound_utf8(
        f"Pre-review candidate tests failed with exit code {rc}. "
        f"Resolve every occurrence of the bounded root cause, not only the displayed line: {cause_text}",
        4096,
    )

    facts_text = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    evidence = [
        _bound_utf8(f"returncode: {rc}", 8192),
        _bound_utf8(f"selectors (count={selector_count}): {selector_sample}", 8192),
        _bound_utf8("normalized_failure_facts: " + facts_text, 8192),
    ]
    if validated["isolation"] == "static-byte-profile":
        # Already bounded/validated; retain every file AND total violation.
        # The generic pytest fallback above is intentionally only 2048 bytes.
        evidence.append(validated["stderr_tail"])

    feedback: dict[str, Any] = {
        "reviewer": "test-gate",
        "reviewed_proposal_sha256": proposal_sha,
        "findings": [finding],
        "test_evidence": evidence,
    }

    serialized = json.dumps(feedback, ensure_ascii=False)
    if len(serialized.encode("utf-8")) >= MAX_CONTEXT:
        budget = (MAX_CONTEXT - 1000) // len(evidence)
        evidence = [_bound_utf8(item, max(budget, 100)) for item in evidence]
        feedback["test_evidence"] = evidence
        serialized = json.dumps(feedback, ensure_ascii=False)

    return validate_correction_feedback(serialized, proposal_sha)


def run_provider_session(
    root: Path, task_path: Path, role: str, base_run_id: str, timeout: int,
    state_check: Callable[[], None], feedback: Path | None = None,
    proposal: Path | None = None, cli: Path | None = None,
    test_evidence: Path | None = None, expected_head: str | None = None,
) -> tuple[str, Path]:
    proposals_dir = root / ".local/two-site-refactor/proposals"
    attempts = (base_run_id, f'{base_run_id}-retry-1', f'{base_run_id}-retry-2')
    last_error: RetryableProviderSessionError | None = None
    output_errors = 0
    next_hint: dict[str, str | int] | None = None
    label = "reviewer" if role == "verifier" else "worker"
    if expected_head is None:
        expected_head = git(root, "rev-parse", "HEAD")
    for attempt_id in attempts:
        state_check()
        if (proposals_dir / attempt_id).exists():
            raise ProposalError(f"{label} run ID already exists: {attempt_id}")
        current_hint = next_hint
        next_hint = None
        try:
            runner_kwargs = {
                "feedback": feedback,
                "proposal": proposal,
                "cli": cli,
            }
            if test_evidence is not None:
                runner_kwargs["test_evidence"] = test_evidence
            if current_hint is not None:
                runner_kwargs["retry_hint"] = current_hint
            run_dir = runner_run(
                root, task_path, role, attempt_id, timeout, **runner_kwargs
            )
            return attempt_id, run_dir
        except RetryableProviderOutputError as exc:
            output_errors += 1
            last_error = exc
            next_hint = None
            if output_errors >= 2:
                raise ProposalError(
                    f"repeated provider output failure for {base_run_id} after 2 output attempts"
                ) from exc
            if (
                role == "worker"
                and feedback is None
                and proposal is None
                and isinstance(exc.__cause__, ProposalError)
            ):
                cause_text = str(exc.__cause__)
                if cause_text.startswith("generated Python syntax is invalid: "):
                    task, _ = load_task(root, task_path)
                    hint = extract_syntax_hint(cause_text, task["allowed_files"])
                    if hint is not None:
                        next_hint = hint
        except ForbiddenToolAttemptError:
            next_hint = None
            state_check()
            if role != "worker":
                raise
            policy = load_worker_routing_policy(ROOT / DEFAULT_POLICY_PATH, base_dir=ROOT)
            if not policy.enabled:
                raise
            task, _ = load_task(root, task_path)
            task_id = task.get("task_id") if isinstance(task, dict) else None
            if not isinstance(task_id, str) or not task_id.strip():
                raise
            route = select_worker_model_route(
                task_id,
                policy=policy,
                default_model=WORKER_MODEL,
                default_effort=WORKER_EFFORT,
            )
            if route.model != policy.model:
                raise
            fallback_id = f"{attempt_id}-gf"
            if len(fallback_id) > 80 or os.path.lexists(proposals_dir / fallback_id):
                raise ProposalError(f"fallback run ID invalid or already exists: {fallback_id}")
            fallback_kwargs = dict(runner_kwargs)
            fallback_kwargs.pop("retry_hint", None)
            fallback_kwargs["fallback_from_run_id"] = attempt_id
            fallback_dir = runner_run(
                root, task_path, role, fallback_id, timeout, **fallback_kwargs
            )
            return fallback_id, fallback_dir
        except RetryableProviderSessionError as exc:
            next_hint = None
            last_error = exc
        except ProposalError as exc:
            state_check()
            expected_task_id = None
            if role == "worker":
                policy = load_worker_routing_policy(ROOT / DEFAULT_POLICY_PATH, base_dir=ROOT)
                if policy.enabled:
                    task, _ = load_task(root, task_path)
                    expected_task_id = task["task_id"]
            extracted = _extract_bound_quota_evidence(
                root, proposals_dir / attempt_id, attempt_id, role, expected_head, expected_task_id
            )
            if extracted is not None:
                quota_evidence, quota_attempt = extracted
                raise QuotaBlockError(quota_evidence, quota_attempt) from None
            raise
    raise ProposalError(
        f"provider session retry exhausted for {base_run_id} after 3 attempts"
    ) from last_error


def _run_post_worker_remainder(
    *,
    root: Path,
    task_candidate: Path,
    task: dict,
    sources: dict,
    initial_head: str,
    initial_refs: dict[str, str | None],
    run_prefix: str,
    timeout: int,
    cli: Path | None,
    state: dict,
    evidence_dir: Path,
    state_check: Callable[[], None],
    worker_dir: Path,
    worker_res: dict,
    current_worker: tuple[str, str, str, str],
    seen_convs: set[str],
    correction_count: int,
    seen_norm_findings: set[str],
    seen_test_signatures: set[str],
    seen_bootstrap_signatures: set[str],
) -> None:
    """Execute post-worker verification, test execution, and correction loop."""
    prior_changes = worker_res["changes"]
    while True:
        worker_result_path = worker_dir / "result.json"
        bootstrap_findings = p1_00_characterization_bootstrap_findings(worker_res)
        assertion_findings = candidate_test_assertion_findings(worker_res)
        if bootstrap_findings or assertion_findings:
            if bootstrap_findings and assertion_findings:
                gate_name = "pre-review-static-tests"
            elif bootstrap_findings:
                gate_name = "P1-00-characterization-bootstrap"
            else:
                gate_name = "two-site-test-assertions"
            static_findings = (bootstrap_findings + assertion_findings)[:32]
            signature = hashlib.sha256(
                json.dumps(static_findings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if signature in seen_bootstrap_signatures:
                raise ProposalError(f"repeated test bootstrap failure signature: {signature}")
            seen_bootstrap_signatures.add(signature)
            feedback_payload = {
                "reviewer": "test-gate",
                "reviewed_proposal_sha256": current_worker[1],
                "findings": static_findings,
                "test_evidence": [
                    f"gate={gate_name}",
                    f"proposal_sha256={current_worker[1]}",
                    f"finding_count={len(static_findings)}",
                ],
            }
            validate_correction_feedback(json.dumps(feedback_payload), current_worker[1])
        else:
            receipt, receipt_path, receipt_sha = persist_pre_review_tests(
                root, evidence_dir, task, worker_res, current_worker[1],
                correction_count, state_check, state,
            )
            req_feedback = required_selector_gate_feedback(receipt, current_worker[1])
            if req_feedback is not None:
                validate_correction_feedback(json.dumps(req_feedback), current_worker[1])
                sig = hashlib.sha256(
                    json.dumps(req_feedback["findings"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if sig in seen_bootstrap_signatures:
                    raise ProposalError(f"repeated test bootstrap failure signature: {sig}")
                seen_bootstrap_signatures.add(sig)
                feedback_payload = req_feedback
            elif receipt["state"] == "FAILED":
                sig = test_failure_signature(receipt)
                if sig in seen_test_signatures:
                    raise ProposalError(f"repeated test failure signature: {sig}")
                seen_test_signatures.add(sig)
                feedback_payload = test_gate_feedback(receipt, current_worker[1])
            else:
                reviewer_run_id, reviewer_dir = run_provider_session(
                    root, task_candidate, "verifier", f"{run_prefix}-reviewer-{correction_count}", timeout,
                    state_check, proposal=worker_result_path, cli=cli,
                    test_evidence=receipt_path, expected_head=initial_head,
                )
                reviewer_res, _st, _sha, _txt = validate_run(
                    root, reviewer_dir, "verifier", reviewer_run_id, initial_head, task, sources,
                    seen_convs, prior_worker=current_worker,
                    expected_test_receipt=receipt, expected_test_evidence_sha=receipt_sha,
                )
                state.update(
                    final_reviewer_run_id=reviewer_run_id,
                    reviewer_path=(reviewer_dir / "result.json").relative_to(root).as_posix(),
                    correction_count=correction_count,
                )
                check_state(root, initial_head, initial_refs, task_candidate, task, sources)

                if reviewer_res["status"] == "VERIFIED":
                    if reviewer_res.get("findings"):
                        raise ProposalError("verified result cannot contain findings")
                    state["state"] = "READY_FOR_CODEX"
                    state["blockers"] = []
                    break

                findings = reviewer_res.get("findings", [])
                recurring = [f for f in findings if normalize_finding(f) in seen_norm_findings]
                if recurring:
                    raise ProposalError(f"recurring normalized findings: {recurring[0]}")
                for f in findings:
                    seen_norm_findings.add(normalize_finding(f))

                feedback_payload = {
                    "reviewer": "gemini",
                    "reviewed_proposal_sha256": current_worker[1],
                    "findings": findings,
                    "test_evidence": [
                        f"receipt_sha256={receipt_sha} proposal_sha256={current_worker[1]} selector_count={len(receipt['selectors'])}"
                    ],
                }
                validate_correction_feedback(json.dumps(feedback_payload), current_worker[1])

        if correction_count >= 2:
            raise ProposalError("correction exhaustion: maximum 2 corrections exceeded")

        correction_count += 1
        feedback_path = evidence_dir / f"feedback-correction-{correction_count}.json"
        if feedback_path.exists():
            raise ProposalError(f"feedback path already exists: {feedback_path}")
        write_json(feedback_path, feedback_payload)
        feedback_sha = file_sha256(feedback_path)

        worker_run_id, worker_dir = run_provider_session(
            root, task_candidate, "worker", f"{run_prefix}-worker-{correction_count}", timeout,
            state_check, feedback=feedback_path, proposal=worker_result_path, cli=cli,
            expected_head=initial_head,
        )
        worker_res, worker_st, worker_sha, worker_txt = validate_run(
            root, worker_dir, "worker", worker_run_id, initial_head, task, sources,
            seen_convs, prior_worker=current_worker, expected_feedback_sha=feedback_sha
        )
        if worker_sha == current_worker[1] or worker_res["changes"] == prior_changes:
            raise ProposalError("unchanged correction proposal")

        current_worker = (worker_run_id, worker_sha, worker_txt, worker_st["conversation_id"])
        prior_changes = worker_res["changes"]
        state.update(
            final_worker_run_id=worker_run_id,
            final_reviewer_run_id=None,
            reviewer_path=None,
            pre_review_test_path=None,
            pre_review_test_sha256=None,
            pre_review_test_state=None,
            proposal_sha256=worker_sha,
            changed_paths=[c["path"] for c in worker_res["changes"]],
            proposal_path=(worker_dir / "result.json").relative_to(root).as_posix(),
            correction_count=correction_count,
        )


def run_pipeline(root: Path, task_path: Path, run_prefix: str, timeout: int, cli: Path | None, state: dict) -> dict:
    root = root.resolve(strict=True)
    task_candidate = Path(os.path.abspath(task_path if task_path.is_absolute() else root / task_path))
    if not task_candidate.is_relative_to(root):
        raise ProposalError("task path must be inside root")
    if not RUN_PREFIX_RE.fullmatch(run_prefix):
        raise ProposalError("invalid run-prefix: must be canonical identifier up to 60 chars")
    if not 10 <= timeout <= 900:
        raise ProposalError("timeout must be between 10 and 900 seconds")

    with pipeline_lock(root) as pipeline_dir:
        if git(root, "branch", "--show-current") != BRANCH:
            raise ProposalError(f"pipeline requires branch {BRANCH}")
        initial_head = git(root, "rev-parse", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}", initial_head):
            raise ProposalError("invalid HEAD commit hash")
        if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ProposalError("pipeline requires clean worktree")
        initial_refs = ref_snapshot(root)
        task, sources = load_task(root, task_candidate)

        state["task_id"] = task["task_id"]
        state["source_head"] = initial_head
        evidence_dir = pipeline_dir / run_prefix
        if os.path.lexists(evidence_dir):
            raise ProposalError(f"pipeline evidence directory already exists: {run_prefix}")
        semantic_attempt = reserve_semantic_attempt(
            root, pipeline_dir, task, task["task_id"], run_prefix,
        )
        state["semantic_task_fingerprint"] = semantic_attempt["fingerprint"]

        evidence_dir.mkdir(mode=0o700)

        def state_check() -> None:
            check_state(root, initial_head, initial_refs, task_candidate, task, sources)

        state.setdefault("pre_review_test_path", None)
        state.setdefault("pre_review_test_sha256", None)
        state.setdefault("pre_review_test_state", None)

        seen_convs: set[str] = set()
        seen_norm_findings: set[str] = set()
        seen_test_signatures: set[str] = set()
        seen_bootstrap_signatures: set[str] = set()

        worker_run_id, worker_dir = run_provider_session(
            root, task_candidate, "worker", f"{run_prefix}-worker-0", timeout,
            state_check, cli=cli, expected_head=initial_head,
        )
        worker_res, worker_st, worker_sha, worker_txt = validate_run(
            root, worker_dir, "worker", worker_run_id, initial_head, task, sources, seen_convs
        )
        current_worker = (worker_run_id, worker_sha, worker_txt, worker_st["conversation_id"])
        prior_changes = worker_res["changes"]
        state.update(
            final_worker_run_id=worker_run_id, proposal_sha256=worker_sha,
            changed_paths=[c["path"] for c in worker_res["changes"]],
            proposal_path=(worker_dir / "result.json").relative_to(root).as_posix(),
        )

        correction_count = 0
        _run_post_worker_remainder(
            root=root,
            task_candidate=task_candidate,
            task=task,
            sources=sources,
            initial_head=initial_head,
            initial_refs=initial_refs,
            run_prefix=run_prefix,
            timeout=timeout,
            cli=cli,
            state=state,
            evidence_dir=evidence_dir,
            state_check=state_check,
            worker_dir=worker_dir,
            worker_res=worker_res,
            current_worker=current_worker,
            seen_convs=seen_convs,
            correction_count=correction_count,
            seen_norm_findings=seen_norm_findings,
            seen_test_signatures=seen_test_signatures,
            seen_bootstrap_signatures=seen_bootstrap_signatures,
        )

        check_state(root, initial_head, initial_refs, task_candidate, task, sources)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="project root directory")
    parser.add_argument("--task", type=Path, required=True, help="path to task definition JSON")
    parser.add_argument("--run-prefix", type=str, required=True, help="canonical unique prefix for run IDs")
    parser.add_argument("--timeout", type=int, default=300, help="timeout per model invocation in seconds")
    parser.add_argument("--cli", type=Path, default=None, help="path to existing Antigravity CLI executable")
    args = parser.parse_args()

    state: dict = {
        "state": "TASK_BLOCKED", "task_id": "", "source_head": "",
        "final_worker_run_id": None, "final_reviewer_run_id": None,
        "proposal_sha256": None, "changed_paths": [], "proposal_path": None,
        "reviewer_path": None, "correction_count": 0, "blockers": [],
        "pre_review_test_path": None, "pre_review_test_sha256": None,
        "pre_review_test_state": None,
        "semantic_task_fingerprint": None,
    }
    try:
        run_pipeline(args.root, args.task, args.run_prefix, args.timeout, args.cli, state)
    except QuotaBlockError as exc:
        state["state"] = "TASK_BLOCKED"
        state["quota_evidence"] = exc.quota_evidence
        if exc.quota_attempt is not None:
            state["quota_attempt"] = exc.quota_attempt
        state["blockers"] = ["observational quota, no replay authority"]
    except (ProposalError, OSError, subprocess.SubprocessError) as exc:
        msg = str(exc).strip() or type(exc).__name__
        state["state"], state["blockers"] = "TASK_BLOCKED", [msg[:200]]
    except Exception as exc:
        state["state"], state["blockers"] = "TASK_BLOCKED", [f"{type(exc).__name__}: {exc}"[:200]]

    if state["state"] == "READY_FOR_CODEX":
        state["blockers"] = []
    else:
        state["blockers"] = [b[:200].strip() for b in (state["blockers"] or ["task execution blocked"])[:20] if b.strip()]

    print(json.dumps(state, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0 if state["state"] == "READY_FOR_CODEX" else 1


if __name__ == "__main__":
    raise SystemExit(main())
