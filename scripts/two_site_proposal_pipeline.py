#!/usr/bin/env python3
"""Proposal-only coordinator for fresh Gemini Worker and Reviewer orchestration.

This pipeline is proposal-only and only removes orchestration repetition:
The coordinator still performs final exact task/source/proposal/review inspection,
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
        MAX_CONTEXT, MAX_OUTPUT, MODEL, WORKER_MODEL, WORKER_EFFORT, RUNTIME_CONTRACT, ForbiddenToolAttemptError,
        CorrectionDeltaError, ProposalError, ProposalProfileError, RetryableProviderOutputError, RetryableProviderSessionError,
        _load_bound_opus_fallback_receipt,
        bounded_file_bytes, bounded_file_text, file_sha256, git, load_task as _load_task,
        run as runner_run, serialize_json, unique_json, bound_gemini_role,
        is_retryable_interrupted_stream, is_retryable_zero_token_stream,
        _framed_input_plan, _framed_stream_projection,
        validate_correction_feedback, validate_generation_completion, validate_result, write_json,
    )
except ModuleNotFoundError:
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MAX_CONTEXT, MAX_OUTPUT, MODEL, WORKER_MODEL, WORKER_EFFORT, RUNTIME_CONTRACT, ForbiddenToolAttemptError,
        CorrectionDeltaError, ProposalError, ProposalProfileError, RetryableProviderOutputError, RetryableProviderSessionError,
        _load_bound_opus_fallback_receipt,
        bounded_file_bytes, bounded_file_text, file_sha256, git, load_task as _load_task,
        run as runner_run, serialize_json, unique_json, bound_gemini_role,
        is_retryable_interrupted_stream, is_retryable_zero_token_stream,
        _framed_input_plan, _framed_stream_projection,
        validate_correction_feedback, validate_generation_completion, validate_result, write_json,
    )

try:
    from scripts.two_site_pre_review_receipt import (
        candidate_test_assertion_findings,
        p1_00_characterization_bootstrap_findings,
        _run_candidate_tests_structured as run_candidate_tests,
        validate_test_receipt,
    )
except ImportError:
    from two_site_pre_review_receipt import (
        candidate_test_assertion_findings,
        p1_00_characterization_bootstrap_findings,
        _run_candidate_tests_structured as run_candidate_tests,
        validate_test_receipt,
    )

try:
    from scripts.two_site_semantic_attempts import reserve_semantic_attempt, read_existing_semantic_attempt
except ModuleNotFoundError:
    from two_site_semantic_attempts import reserve_semantic_attempt, read_existing_semantic_attempt

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

try:
    from scripts.two_site_proposal_byte_profile import (
        ERROR as BYTE_PROFILE_ERROR, ProposalByteProfileError,
        parse_proposal_utf8_budget_profile, require_proposal_utf8_budget_profile,
    )
except ImportError:
    from two_site_proposal_byte_profile import (
        ERROR as BYTE_PROFILE_ERROR, ProposalByteProfileError,
        parse_proposal_utf8_budget_profile, require_proposal_utf8_budget_profile,
    )


def load_task(root: Path, task_path: Path) -> tuple[dict, dict]:
    """Reject an unrepairable task declaration before reservation or Provider.

    All initial, state-recheck and continuation task reads use this boundary.
    The allowed set validates syntax only; no candidate paths are authorized.
    """
    task, sources = _load_task(root, task_path)
    try:
        parse_proposal_utf8_budget_profile(
            task["requirements"], task["allowed_files"], task["allowed_files"],
        )
    except ProposalByteProfileError:
        raise ProposalError(BYTE_PROFILE_ERROR) from None
    return task, sources


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
        rf"^{re.escape(orig_run_prefix)}(?:-(?:reviewer|verifier)-([0-2])|-v([0-2])r[12])(?:-retry-[12])?$",
        verifier_run_id,
    )
    if not verifier_match:
        raise ProposalError(
            f"quota_attempt.run_id {verifier_run_id!r} does not match original run_prefix "
            f"{orig_run_prefix!r} reviewer/verifier pattern"
        )
    verifier_index = int(verifier_match.group(1) or verifier_match.group(2))

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

    try:
        captured_role = bound_gemini_role(status, role)
    except ProposalError:
        return None
    if captured_role is not None:
        if expected_task_id is not None and status.get("task_id") != expected_task_id:
            return None
        expected_model = captured_role["model"]
    elif role == "verifier":
        expected_model = RUNTIME_CONTRACT["model"]
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
            expected_model = RUNTIME_CONTRACT["worker_model"]
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
    """Historical unsnapshotted routing; fresh runs carry their own role snapshot."""
    if role == "verifier":
        return RUNTIME_CONTRACT["model"]
    if role == "worker":
        policy = load_worker_routing_policy(ROOT / DEFAULT_POLICY_PATH, base_dir=ROOT)
        route = select_worker_model_route(
            task_id,
            policy=policy,
            default_model=RUNTIME_CONTRACT["worker_model"],
            default_effort=RUNTIME_CONTRACT["worker_effort"],
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
    bound_role = bound_gemini_role(status, role)
    if bound_role is not None:
        if "fallback_from_run_id" in status or "fallback_receipt" in status:
            raise ProposalError("configured Gemini role cannot use legacy fallback metadata")
        return bound_role["model"]
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
            return RUNTIME_CONTRACT["worker_model"]
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
        default_model=RUNTIME_CONTRACT["worker_model"],
        default_effort=RUNTIME_CONTRACT["worker_effort"],
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

    if status.get("model") != RUNTIME_CONTRACT["worker_model"]:
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

    return RUNTIME_CONTRACT["worker_model"]


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
    validate_generation_completion(
        root, run_dir, task=expected_task, sources=expected_sources, role=role,
        status=status, result=result,
    )
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


def _structured_failure_records(validated: dict[str, Any]) -> tuple[list[dict], str, str]:
    """Canonical associated observations; completeness never confers authority."""
    diagnostic = validated["pytest_diagnostics"]
    if diagnostic is None:
        return [], "unavailable", "unknown"
    unique = {
        json.dumps(record, sort_keys=True, separators=(",", ":")): record
        for record in diagnostic["records"]
    }
    records = [unique[key] for key in sorted(unique)]
    complete = (records and not diagnostic["partial"]
                and all(not record["partial"] for record in records))
    return records, "available", "complete" if complete else "unknown"


def _failed_test_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Screen exact-dict ingress before any lookup; preserve legacy Mapping ABI."""
    if type(receipt) is dict and (
        len(receipt) not in (10, 13) or any(
            type(key) is not str or len(key) > 32 or not key.isascii()
            for key in receipt
        )
    ):
        return validate_test_receipt(receipt, "")
    proposal_sha = (
        receipt.get("proposal_sha256")
        if isinstance(receipt, Mapping) and not isinstance(receipt, (str, bytes))
        else ""
    )
    if type(receipt) is dict and len(receipt) == 13 and type(proposal_sha) is not str:
        proposal_sha = ""  # Central V2 rejection without consulting __class__.
    if not isinstance(proposal_sha, str):
        proposal_sha = ""
    validated = validate_test_receipt(receipt, proposal_sha)
    if validated["state"] != "FAILED":
        raise ProposalError(f"receipt state must be FAILED, got {validated['state']}")
    return validated


def test_failure_facts(receipt: Mapping[str, Any]) -> dict[str, list[str]]:
    """V2 uses safe observations only; TEN retains legacy console compatibility."""
    validated = _failed_test_receipt(receipt)

    if validated.get("receipt_version") == 2:
        records, _availability, completeness = _structured_failure_records(validated)
        return {
            "tests": sorted({f"{record['phase']}: {record['nodeid']}" for record in records}),
            "exceptions": sorted({record["exception"] for record in records
                                  if record["exception"] is not None}
                                 | ({"UNKNOWN"} if completeness != "complete" else set())),
            "statements": sorted({record["assertion"] for record in records
                                  if record["assertion"] is not None}),
        }

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


_NEW_TEST_WHITESPACE_PREFIX = "New-test trailing whitespace at sorted change indices: "


def _is_canonical_new_test_whitespace_summary(summary_text: str) -> bool:
    if not isinstance(summary_text, str) or not summary_text.startswith(_NEW_TEST_WHITESPACE_PREFIX):
        return False
    rest = summary_text[len(_NEW_TEST_WHITESPACE_PREFIX):]
    if not rest:
        return False
    tokens = rest.split(",")
    prev = -1
    for token in tokens:
        if not token or len(token) > 2:
            return False
        for ch in token:
            if ch not in "0123456789":
                return False
        if len(token) > 1 and token[0] == "0":
            return False
        val = int(token)
        if not (0 <= val <= 31) or val <= prev:
            return False
        prev = val
    return True


def _byte_profile_failure_factors(receipt: Mapping[str, Any]) -> list[str] | None:
    """Recognize only complete canonical static-byte-profile gate diagnostics."""
    if (receipt["isolation"] != "static-byte-profile" or receipt["returncode"] != 1
            or receipt["stdout_tail"] or receipt["snapshot_files"] or receipt["snapshot_bytes"]):
        return None
    lines = receipt["stderr_tail"].split("\n")
    if not 1 <= len(lines) <= 34:
        return None
    if not lines[-1].startswith("AssertionError: "):
        if not _is_canonical_new_test_whitespace_summary(lines[-1]):
            return None
        byte_lines = lines[:-1]
    else:
        byte_lines = lines
    if not 1 <= len(byte_lines) <= 33:
        return None
    try:
        from scripts.two_site_agent_sandbox import _validate_overlay_path
    except ImportError:
        from two_site_agent_sandbox import _validate_overlay_path
    factors, seen = [], set()
    for line in byte_lines:
        match = re.fullmatch(
            r"AssertionError: (.+) exceeds ([1-9][0-9]{0,5}) byte limit: ([1-9][0-9]{0,6}) bytes",
            line,
        )
        if match is None:
            return None
        target, cap_text, actual_text = match.groups()
        cap, actual = int(cap_text), int(actual_text)
        if cap > 160000 or actual <= cap or target in seen:
            return None
        if target != "proposal total":
            try:
                if len(target.encode("utf-8")) > 128 or any(c in target for c in "*?[]{}"):
                    return None
                _validate_overlay_path(target)
            except ValueError:
                return None
        seen.add(target)
        factors.append(f"{target} exceeds {cap_text} byte limit")
    return sorted(factors)


def _branch_root_failure_factor(receipt: Mapping[str, Any]) -> str | None:
    """V2 canonical branch STRING observation only; TEN keeps its old detector.

    A child observation is not authenticated type identity or retry authority.
    """
    validated = _failed_test_receipt(receipt)

    target_exceptions = {
        f"scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}",
        f"two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}",
    }

    if validated.get("receipt_version") == 2:
        records, _availability, _completeness = _structured_failure_records(validated)
        has_typed_branch_exc = any(record["exception"] in target_exceptions for record in records)
    else:
        combined = validated["stdout_tail"] + "\n" + validated["stderr_tail"]
        has_typed_branch_exc = False
        for raw_line in combined.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("E "):
                exc_candidate = stripped[2:].strip()
                if exc_candidate in target_exceptions:
                    has_typed_branch_exc = True
                    break

    if not has_typed_branch_exc:
        return None

    normalized_selectors = sorted(set(validated["selectors"]))
    payload = {
        "branch": BRANCH,
        "factor": "repository_branch",
        "selectors": normalized_selectors,
    }
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest().lower()


def test_failure_signature(receipt: Mapping[str, Any]) -> str:
    validated = _failed_test_receipt(receipt)
    if validated.get("receipt_version") == 2:
        records, availability, completeness = _structured_failure_records(validated)
        diagnostic = validated["pytest_diagnostics"]
        payload = {
            "receipt_version": 2,
            "diagnostic_version": None if diagnostic is None else diagnostic["version"],
            "availability": availability,
            "completeness": completeness,
            "diagnostic_partial": None if diagnostic is None else diagnostic["partial"],
            "diagnostic_error": validated["pytest_diagnostics_error"],
            "returncode": validated["returncode"],
            "selectors": sorted(set(validated["selectors"])),
            "records": records,
        }
        if completeness != "complete":
            payload["proposal_sha256"] = validated["proposal_sha256"]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    facts = test_failure_facts(validated)
    payload = {
        "returncode": validated["returncode"],
        "selectors": validated["selectors"],
        "facts": {k: sorted(v) for k, v in facts.items()},
    }
    byte_factors = _byte_profile_failure_factors(validated)
    if byte_factors is not None:
        # Actual counts remain in feedback/receipts; only factor identity omits
        # them. Generic pytest assertions and unknown diagnostics stay exact.
        payload["facts"] = {"byte_budget_factors": byte_factors}
        payload["isolation"] = "static-byte-profile"
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
    if validated.get("receipt_version") != 2 and validated["isolation"] == "static-byte-profile":
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


_EMPTY_INITIAL_PROFILE = (
    "proposal runtime selected session is ambiguous or absent; "
    "LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0; "
    "PREP_DIAG phase=SECOND reason=INITIALIZER_FAILED attempts=2 "
    "invalid=0 sqlite=0 multiple=0 ui=NONEMPTY init=OWNED_INIT_ONLY"
)
_EMPTY_INITIAL_PROFILE_PRE_SECOND = _EMPTY_INITIAL_PROFILE.replace(
    "phase=SECOND reason=INITIALIZER_FAILED attempts=2",
    "phase=PRE_SECOND reason=PRISTINE_RECHECK_PERSISTED_WAL_CONTRACT attempts=1",
)
_EMPTY_INITIAL_PROFILES = frozenset({_EMPTY_INITIAL_PROFILE, _EMPTY_INITIAL_PROFILE_PRE_SECOND})


def _retryable_profile_preparation(
    root: Path, run_dir: Path, run_id: str, role: str, head: str,
    task: dict, error: ProposalProfileError,
) -> bool:
    """Admit only the observed owned failure before any generation dispatch."""
    if type(error) is not ProposalProfileError or str(error) not in _EMPTY_INITIAL_PROFILES:
        return False
    allowed = {"task.json", "prompt.txt", "response-schema.json", "status.json", "input-framing.json"}
    forbidden_fields = {
        "runtime_profile", "prepared_effort", "cli_pid", "conversation_id", "usage",
        "result_sha256", "delta_result_sha256", "completion_proof_sha256",
        "cli_returncode", "cli_normal_exit_observed", "generation_completion_outcome",
        "generation_capture_failure", "noneligible_completion_observations",
        "secondary_context_error_type", "quota_evidence",
    }
    try:
        info = run_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            return False
        names = set()
        for path in run_dir.iterdir():
            if path.name not in allowed:
                return False
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o022):
                return False
            names.add(path.name)
        if not {"task.json", "prompt.txt", "response-schema.json", "status.json"} <= names:
            return False
        status = unique_json(bounded_file_bytes(root, run_dir / "status.json", MAX_CONTEXT, "preparation status").decode())
        snapshot = unique_json(bounded_file_bytes(root, run_dir / "task.json", 3 * MAX_CONTEXT, "preparation task").decode())
        if not isinstance(status, dict) or not isinstance(snapshot, dict):
            return False
        return (
            status.get("run_id") == run_id and status.get("role") == role
            and status.get("head") == head and status.get("task_id") == task["task_id"]
            and status.get("model") == expected_bound_role_model(
                root, role, run_id, head, task, snapshot.get("sources", {}), status,
            )
            and status.get("state") == "BLOCKED"
            and status.get("authority") == "PROPOSAL_ONLY_NOT_ACCEPTANCE"
            and status.get("error") == "ProposalProfileError: " + str(error)
            and not forbidden_fields.intersection(status)
            and snapshot.get("source_head") == head and snapshot.get("task") == task
        )
    except Exception:
        return False


def run_provider_session(
    root: Path, task_path: Path, role: str, base_run_id: str, timeout: int,
    state_check: Callable[[], None], feedback: Path | None = None,
    proposal: Path | None = None, cli: Path | None = None,
    test_evidence: Path | None = None, expected_head: str | None = None,
) -> tuple[str, Path]:
    proposals_dir = root / ".local/two-site-refactor/proposals"
    attempts = (base_run_id, f'{base_run_id}-retry-1', f'{base_run_id}-retry-2')
    last_error: RetryableProviderSessionError | ProposalProfileError | None = None
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
            if (
                role == "worker" and feedback is not None and proposal is not None
                and (
                    type(exc.__cause__) is ProposalError and str(exc.__cause__) in {
                        "empty or unchanged file",
                        "plan-only or no-op response is not implementation",
                    }
                    or type(exc.__cause__) is CorrectionDeltaError
                    and str(exc.__cause__) == "complete no-op rejection: rebase yielded no canonical changes"
                )
            ):
                state_check()
                raise ProposalError("correction worker made no implementation progress") from exc
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
        except ProposalProfileError as exc:
            next_hint = None
            state_check()
            task, _ = load_task(root, task_path)
            if not _retryable_profile_preparation(
                root, proposals_dir / attempt_id, attempt_id, role, expected_head, task, exc,
            ):
                raise
            last_error = exc
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
            # Framed ERROR classification deliberately wraps the legacy error.
            # Admit only this exact proven interruption, never partial output.
            context = exc.__context__
            if (type(exc) is ProposalError
                    and type(context) is RetryableProviderSessionError
                    and exc.__cause__ is None and exc.__suppress_context__ is True
                    and str(exc) == str(context)
                    == "provider stream interrupted before a complete result"):
                next_hint = None
                last_error = context
                continue
            raise
    raise ProposalError(
        f"provider session retry exhausted for {base_run_id} after 3 attempts"
    ) from last_error


class ReviewerPhaseExhausted(ProposalError):
    """Known transient phase failure; prior successful phases remain untouched."""

    def __init__(self, receipt_path: str, failure: dict) -> None:
        super().__init__("reviewer transient recovery exhausted: two continuation batches used")
        self.phase = "reviewer"
        self.failure_code = "REVIEWER_TRANSIENT_BUDGET_EXHAUSTED"
        self.receipt_path = receipt_path
        self.phase_failure = copy.deepcopy(failure)


_REVIEWER_SUCCESS_ARTIFACTS = ("result.json", "delta-result.json", "transport-completion.json", "proposal.patch")


def _reviewer_batch_failure(
    root: Path, base_run_id: str, error: ProposalError, head: str,
    task: dict, sources: dict, worker: tuple[str, str, str, str],
    test_receipt: dict, test_sha: str,
) -> list[dict] | None:
    """Recognize only failed, fully retained transient attempts, never outputs."""
    if (type(error) is not ProposalError
            or str(error) != f"provider session retry exhausted for {base_run_id} after 3 attempts"
            or type(error.__cause__) not in {RetryableProviderSessionError, ProposalProfileError}):
        return None
    failures = []
    for run_id in (base_run_id, f"{base_run_id}-retry-1", f"{base_run_id}-retry-2"):
        run_dir = root / ".local/two-site-refactor/proposals" / run_id
        try:
            ds = run_dir.lstat()
            if (run_dir.resolve(strict=True) != run_dir or not stat.S_ISDIR(ds.st_mode)
                    or ds.st_uid != os.geteuid() or ds.st_mode & 0o022):
                return None
            if any(os.path.lexists(run_dir / name) for name in _REVIEWER_SUCCESS_ARTIFACTS):
                return None
            status_bytes = bounded_file_bytes(root, run_dir / "status.json", MAX_CONTEXT, "failed reviewer status")
            status = unique_json(status_bytes.decode("utf-8"))
            snapshot_bytes = bounded_file_bytes(root, run_dir / "task.json", 3 * MAX_CONTEXT, "failed reviewer task")
            snapshot = unique_json(snapshot_bytes.decode("utf-8"))
            prompt_bytes = bounded_file_bytes(root, run_dir / "prompt.txt", 2 * MAX_CONTEXT, "failed reviewer prompt")
            framing_path = run_dir / "input-framing.json"
            framing_bytes = (bounded_file_bytes(root, framing_path, 8192, "failed reviewer framing")
                             if os.path.lexists(framing_path) else None)
            if (not isinstance(status, dict) or not isinstance(snapshot, dict)
                    or status.get("state") != "BLOCKED" or status.get("run_id") != run_id
                    or status.get("role") != "verifier" or status.get("head") != head
                    or status.get("task_id") != task["task_id"]
                    or status.get("authority") != "PROPOSAL_ONLY_NOT_ACCEPTANCE"
                    or status.get("secondary_context_error_type") is not None
                    or status.get("quota_evidence") is not None
                    or snapshot.get("source_head") != head or snapshot.get("task") != task
                    or snapshot.get("sources") != sources or snapshot.get("proposal") != worker[2]
                    or snapshot.get("test_evidence") != test_receipt
                    or status.get("input_worker_run_id") != worker[0]
                    or status.get("input_worker_conversation_id") != worker[3]
                    or status.get("input_proposal_sha256") != worker[1]
                    or status.get("input_test_evidence_sha256") != test_sha):
                return None
            model = expected_bound_role_model(root, "verifier", run_id, head, task, sources, status)
            if status.get("model") != model:
                return None
            error_text = status.get("error")
            kind = None
            conversation_id = None
            stream_sha = None
            for message in _EMPTY_INITIAL_PROFILES:
                if error_text == "ProposalProfileError: " + message and _retryable_profile_preparation(
                    root, run_dir, run_id, "verifier", head, task, ProposalProfileError(message),
                ):
                    kind = "EMPTY_INITIAL_PROFILE"
                    break
            if kind is None:
                allowed_errors = {
                    "RetryableProviderSessionError: CLI did not complete one successful session",
                    "RetryableProviderSessionError: provider stream interrupted before a complete result",
                    "ProposalError: provider stream interrupted before a complete result",
                }
                if error_text not in allowed_errors:
                    return None
                raw = bounded_file_text(root, run_dir / "stream.jsonl", MAX_OUTPUT, "failed reviewer stream")
                stream_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                events = [unique_json(line) for line in raw.splitlines() if line.strip()]
                initial = [event for event in events if type(event) is dict and event.get("event") == "init"]
                if len(initial) != 1 or type(initial[0].get("conversation_id")) is not str:
                    return None
                conversation_id = initial[0]["conversation_id"]
                if status.get("input_transport") == "FULL_INPUT_ACK_V1":
                    manifest, _wires = _framed_input_plan(prompt_bytes.decode("utf-8"))
                    if (framing_bytes != serialize_json(manifest)
                            or hashlib.sha256(framing_bytes).hexdigest() != status.get("input_framing_sha256")):
                        return None
                    try:
                        _framed_stream_projection(raw, manifest, model, conversation_id, complete=False)
                    except ProposalError as framed_error:
                        cause = framed_error.__context__
                        if (type(framed_error) is not ProposalError
                                or type(cause) is not RetryableProviderSessionError
                                or framed_error.__cause__ is not None or not framed_error.__suppress_context__
                                or str(framed_error) != str(cause)
                                or str(framed_error) != "provider stream interrupted before a complete result"):
                            return None
                    else:
                        return None
                elif ("input_transport" in status or "input_framing_sha256" in status
                        or framing_bytes is not None
                        or not (is_retryable_interrupted_stream(raw, model) or is_retryable_zero_token_stream(raw, model))):
                    return None
                if any(item["conversation_id"] == conversation_id for item in failures):
                    return None
                kind = "PROVIDER_TRANSPORT_INTERRUPTED"
            failures.append({"run_id": run_id, "status_sha256": hashlib.sha256(status_bytes).hexdigest(),
                             "task_snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                             "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
                             "input_framing_sha256": hashlib.sha256(framing_bytes).hexdigest() if framing_bytes is not None else None,
                             "stream_sha256": stream_sha,
                             "kind": kind, "conversation_id": conversation_id})
        except (ProposalError, OSError, UnicodeError, ValueError):
            return None
    return failures


def _validate_reviewer_continuation(
    root: Path, task_path: Path, run_prefix: str, head: str, refs: dict,
    task: dict, sources: dict, state: dict, worker: tuple[str, str, str, str],
    test_receipt: dict, test_sha: str, correction_count: int,
) -> None:
    """Reuse the quota continuation's existing physical Worker/native readers."""
    if (state.get("state") in {"READY_FOR_CODEX", "ACCEPTED", "COMPLETE"}
            or type(correction_count) is not int or not 0 <= correction_count <= 2
            or state.get("correction_count") != correction_count
            or state.get("final_worker_run_id") != worker[0]
            or state.get("proposal_sha256") != worker[1]
            or state.get("final_reviewer_run_id") is not None or state.get("reviewer_path") is not None):
        raise ProposalError("reviewer continuation phase or proposal mismatch")
    check_state(root, head, refs, task_path, task, sources)
    if any(os.path.lexists(path) for path in (root / "STOP", root / ".local/two-site-refactor/autopilot/STOP")):
        raise ProposalError("STOP blocks reviewer continuation")
    pipeline_dir = root / ".local/two-site-refactor/pipeline"
    semantic = read_existing_semantic_attempt(root, pipeline_dir, task, run_prefix)
    if semantic["fingerprint"] != state.get("semantic_task_fingerprint"):
        raise ProposalError("reviewer continuation semantic reservation mismatch")
    artifacts = {
        "task_id": task["task_id"], "source_head": head,
        "original_run_prefix": run_prefix, "correction_count": correction_count,
        "final_worker_run_id": worker[0], "proposal_path": state.get("proposal_path"),
        "proposal_sha256": worker[1], "pre_review_test_state": state.get("pre_review_test_state"),
        "pre_review_test_path": state.get("pre_review_test_path"), "pre_review_test_sha256": test_sha,
    }
    if state.get("pre_review_test_sha256") != test_sha:
        raise ProposalError("reviewer continuation native receipt mismatch")
    receipt = _load_verifier_pre_review_receipt(root, artifacts)
    if receipt != test_receipt or receipt["selectors"] != task["test_selectors"]:
        raise ProposalError("reviewer continuation requires the same passed native selectors")
    try:
        from scripts.two_site_quota_resume_worker_reader import load_prior_worker_chain
    except ImportError:
        from two_site_quota_resume_worker_reader import load_prior_worker_chain
    validated = load_prior_worker_chain(root, task_path, artifacts)
    if (validated["result_sha"] != worker[1] or validated["result_text"] != worker[2]
            or validated["conversation_id"] != worker[3]):
        raise ProposalError("reviewer continuation prior Worker changed")


def _run_reviewer_phase(
    *, root: Path, task_path: Path, run_prefix: str, timeout: int, cli: Path | None,
    head: str, refs: dict, task: dict, sources: dict, state: dict,
    evidence_dir: Path, state_check: Callable[[], None],
    worker: tuple[str, str, str, str], worker_result_path: Path,
    receipt: dict, receipt_path: Path, receipt_sha: str, correction_count: int,
    seen_convs: set[str],
) -> tuple[str, Path]:
    """Keep Worker/native and allow at most two extra fresh transient-only batches.

    Caller retains the existing pipeline/Broker locks. Each batch uses the existing
    three-session bound; semantic corrections stay in the original 0..2 loop.
    No partial result, quota failure, semantic/output error or ambiguous cleanup is
    eligible. Immutable failed-batch records preserve counters and evidence.
    """
    expected_evidence = root / ".local/two-site-refactor/pipeline" / run_prefix
    evidence_stat = evidence_dir.lstat()
    if (evidence_dir != expected_evidence or evidence_dir.resolve(strict=True) != expected_evidence
            or not stat.S_ISDIR(evidence_stat.st_mode) or evidence_stat.st_uid != os.geteuid()
            or stat.S_IMODE(evidence_stat.st_mode) != 0o700):
        raise ProposalError("reviewer continuation evidence directory is not the owned pipeline directory")
    if state.get("state") in {"READY_FOR_CODEX", "ACCEPTED", "COMPLETE"}:
        raise ProposalError("completed pipeline cannot resume a reviewer")
    recorded_failures = []
    for batch in range(3):
        base = f"{run_prefix}-reviewer-{correction_count}" if batch == 0 else f"{run_prefix}-v{correction_count}r{batch}"
        if batch:
            _validate_reviewer_continuation(
                root, task_path, run_prefix, head, refs, task, sources, state,
                worker, receipt, receipt_sha, correction_count,
            )
            for prior in recorded_failures:
                directory = root / ".local/two-site-refactor/proposals" / prior["run_id"]
                for name, key, limit in (("status.json", "status_sha256", MAX_CONTEXT),
                                         ("task.json", "task_snapshot_sha256", 3 * MAX_CONTEXT),
                                         ("prompt.txt", "prompt_sha256", 2 * MAX_CONTEXT),
                                         ("input-framing.json", "input_framing_sha256", 8192),
                                         ("stream.jsonl", "stream_sha256", MAX_OUTPUT)):
                    p = directory / name
                    expected = prior[key]
                    if (expected is None and os.path.lexists(p)) or (expected is not None and
                            hashlib.sha256(bounded_file_bytes(root, p, limit, "prior failed reviewer evidence")).hexdigest() != expected):
                        raise ProposalError("failed reviewer evidence changed before continuation")
                if any(os.path.lexists(directory / name) for name in _REVIEWER_SUCCESS_ARTIFACTS):
                    raise ProposalError("failed reviewer acquired success artifacts before continuation")
        try:
            return run_provider_session(
                root, task_path, "verifier", base, timeout, state_check,
                proposal=worker_result_path, cli=cli, test_evidence=receipt_path, expected_head=head,
            )
        except ProposalError as exc:
            state_check()
            failures = _reviewer_batch_failure(root, base, exc, head, task, sources, worker, receipt, receipt_sha)
            if failures is None:
                raise
            failed_conversations = {item["conversation_id"] for item in failures
                                    if item["conversation_id"] is not None}
            if failed_conversations.intersection(seen_convs):
                raise ProposalError("reviewer continuation reused a prior conversation") from exc
            _validate_reviewer_continuation(
                root, task_path, run_prefix, head, refs, task, sources, state,
                worker, receipt, receipt_sha, correction_count,
            )
            recorded_failures.extend(failures)
            seen_convs.update(failed_conversations)
            failure = {
                "schema_version": "1.0", "phase": "reviewer", "failure_code": "TRANSIENT_REVIEWER_PHASE",
                "task_id": task["task_id"], "task_sha256": file_sha256(task_path),
                "source_head": head, "sources_sha256": hashlib.sha256(serialize_json(sources)).hexdigest(),
                "semantic_task_fingerprint": state["semantic_task_fingerprint"],
                "run_prefix": run_prefix, "correction_count": correction_count,
                "worker_run_id": worker[0], "proposal_sha256": worker[1], "pre_review_test_sha256": receipt_sha,
                "failed_attempts": failures, "resume_batches_used": batch, "maximum_resume_batches": 2,
                "remaining_resume_batches": 2 - batch,
                "action": "CONTINUE_SAME_REVIEWER_PHASE" if batch < 2 else "HALT_BUDGET_EXHAUSTED",
            }
            path = evidence_dir / f"reviewer-phase-{correction_count}-batch-{batch}.json"
            raw = serialize_json(failure)
            if len(raw) > 16384:
                raise ProposalError("reviewer phase failure record exceeds bound")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            parent_fd = os.open(evidence_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            if batch == 2:
                raise ReviewerPhaseExhausted(path.relative_to(root).as_posix(), failure) from exc
    raise AssertionError("unreachable reviewer phase")


def trusted_qoder_final_review_hook() -> Callable[..., dict] | None:
    """The outside Broker policy opts in; legacy callers never launch a model."""
    if os.environ.get("TWO_SITE_QODER_FINAL_REVIEW_REQUIRED") != "1":
        return None
    from scripts.two_site_qoder_review import review_proposal
    return review_proposal


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
    seen_branch_factors: set[str] | None = None,
    final_review_hook: Callable[..., dict] | None = None,
) -> None:
    """Execute post-worker verification, test execution, and correction loop."""
    prior_changes = worker_res["changes"]
    if seen_branch_factors is None:
        seen_branch_factors = set()
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
                branch_factor = _branch_root_failure_factor(receipt)
                if branch_factor is not None:
                    if branch_factor in seen_branch_factors:
                        raise ProposalError(f"repeated branch root factor: {branch_factor}")
                    seen_branch_factors.add(branch_factor)
                feedback_payload = test_gate_feedback(receipt, current_worker[1])
            else:
                reviewer_run_id, reviewer_dir = _run_reviewer_phase(
                    root=root, task_path=task_candidate, run_prefix=run_prefix, timeout=timeout, cli=cli,
                    head=initial_head, refs=initial_refs, task=task, sources=sources, state=state,
                    evidence_dir=evidence_dir, state_check=state_check, worker=current_worker,
                    worker_result_path=worker_result_path, receipt=receipt, receipt_path=receipt_path,
                    receipt_sha=receipt_sha, correction_count=correction_count, seen_convs=seen_convs,
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
                    if final_review_hook is None:
                        state["state"] = "READY_FOR_CODEX"
                        state["blockers"] = []
                        break
                    if bound_gemini_role(_st, "verifier") is None:
                        raise ProposalError("Qoder final review requires the captured current Gemini role policy")
                    # Production supplies the independent helper; tests/legacy callers
                    # leave this hook disabled. Preserve the real Gemini VERIFIED result.
                    qoder_receipt = final_review_hook(
                        root, task_candidate, current_worker[0], reviewer_run_id,
                        current_worker[1], timeout=timeout,
                    )
                    state_check()
                    qoder_path = root / ".local/two-site-refactor/qoder-reviews" / (current_worker[1] + ".json")
                    qoder_bytes = bounded_file_bytes(root, qoder_path, MAX_CONTEXT, "Qoder final review receipt")
                    if unique_json(qoder_bytes.decode("utf-8")) != qoder_receipt:
                        raise ProposalError("Qoder final review receipt changed")
                    value = qoder_receipt.get("result") if type(qoder_receipt) is dict else None
                    if (type(value) is not dict or set(value) != {"decision", "findings", "proposal_sha256", "source_head"}
                            or value.get("source_head") != initial_head or value.get("proposal_sha256") != current_worker[1]
                            or value.get("decision") not in {"APPROVE", "REQUEST_CHANGES"}
                            or type(value.get("findings")) is not list or len(value["findings"]) > 32
                            or not all(type(f) is str and f.strip() and len(f.encode()) <= 4096 for f in value["findings"])
                            or (value["decision"] == "APPROVE") != (value["findings"] == [])):
                        raise ProposalError("Qoder final review decision or proposal binding is invalid")
                    history_path = evidence_dir / f"qoder-review-{correction_count}.json"
                    history = {
                        "schema_version": "1.0", "correction_count": correction_count,
                        "worker_run_id": current_worker[0], "gemini_reviewer_run_id": reviewer_run_id,
                        "proposal_sha256": current_worker[1], "pre_review_test_sha256": receipt_sha,
                        "qoder_review_receipt": qoder_path.relative_to(root).as_posix(),
                        "qoder_review_receipt_sha256": hashlib.sha256(qoder_bytes).hexdigest(),
                        "decision": value["decision"],
                    }
                    fd = os.open(history_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    with os.fdopen(fd, "wb") as output:
                        output.write(serialize_json(history)); output.flush(); os.fsync(output.fileno())
                    parent_fd = os.open(evidence_dir, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                    if value["decision"] == "APPROVE":
                        state["state"] = "READY_FOR_CODEX"
                        state["blockers"] = []
                        break
                    findings = value["findings"]
                    feedback_reviewer = "qoder"
                else:
                    findings = reviewer_res.get("findings", [])
                    feedback_reviewer = "gemini"
                recurring = [f for f in findings if normalize_finding(f) in seen_norm_findings]
                if recurring:
                    raise ProposalError(f"recurring normalized findings: {recurring[0]}")
                for f in findings:
                    seen_norm_findings.add(normalize_finding(f))

                feedback_payload = {
                    "reviewer": feedback_reviewer,
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


def run_pipeline(root: Path, task_path: Path, run_prefix: str, timeout: int, cli: Path | None, state: dict,
                 *, final_review_hook: Callable[..., dict] | None = None) -> dict:
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
        # Broker supplies this provenance only for a fresh ordinary cycle.
        # Frozen quota continuations use their separate same-lineage route.
        if "TWO_SITE_AUTOPILOT_RUN_ID" in os.environ:
            try:
                require_proposal_utf8_budget_profile(task["requirements"], task["allowed_files"])
            except ProposalByteProfileError:
                raise ProposalError("normal task requires an explicit valid UTF-8 budget profile") from None

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
        seen_branch_factors: set[str] = set()

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
            seen_branch_factors=seen_branch_factors,
            final_review_hook=final_review_hook,
        )

        check_state(root, initial_head, initial_refs, task_candidate, task, sources)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="project root directory")
    parser.add_argument("--task", type=Path, required=True, help="path to task definition JSON")
    parser.add_argument("--run-prefix", type=str, required=True, help="canonical unique prefix for run IDs")
    parser.add_argument("--qoder-final-review", action="store_true", help="trusted Broker opt-in to independent final Qoder review")
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
        review_options = {}
        if args.qoder_final_review:
            from scripts.two_site_qoder_review import review_proposal
            review_options["final_review_hook"] = review_proposal
        run_pipeline(args.root, args.task, args.run_prefix, args.timeout, args.cli, state, **review_options)
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
