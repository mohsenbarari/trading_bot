"""Semantic task builder helper for two-site tasks."""
from __future__ import annotations
import copy, inspect
from pathlib import Path
from typing import Any

try:
    from scripts import two_site_proposal_runner as runner
except ModuleNotFoundError:
    import two_site_proposal_runner as runner  # type: ignore
try:
    from scripts.two_site_assignment_scope import AssignmentScopeError, load_active_assignment_scope
except ModuleNotFoundError:
    from two_site_assignment_scope import AssignmentScopeError, load_active_assignment_scope  # type: ignore
try:
    from scripts.two_site_semantic_task import SemanticTaskError, build_identity, validate_identity
except ModuleNotFoundError:
    from two_site_semantic_task import SemanticTaskError, build_identity, validate_identity  # type: ignore

__all__ = ["SemanticTaskBuilderError", "build_task_identity", "validate_task_identity"]

# Identity binds the exact source bytes but does not send this file to either
# model. Its safe bound may therefore exceed the smaller model-context bound.
MAX_IDENTITY_SOURCE_BYTES = 2_000_000

class SemanticTaskBuilderError(ValueError):
    """Raised when semantic task identity building or validation fails."""

_RUNNER_ERRS = tuple(c for n in ("ProposalError", "ProposalRunnerError", "RunnerError") if isinstance(c := getattr(runner, n, None), type))
_KNOWN = (AssignmentScopeError, SemanticTaskError, OSError, RuntimeError, TypeError, KeyError, UnicodeError, *_RUNNER_ERRS)

def _wrap_errors(func):
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit, SemanticTaskBuilderError):
            raise
        except _KNOWN as exc:
            raise SemanticTaskBuilderError(str(exc)) from exc
    return wrapper

def _check_str_list(items: Any, name: str) -> list[str]:
    if not isinstance(items, list):
        raise SemanticTaskBuilderError(f"{name} must be list")
    seen: set[str] = set()
    for x in items:
        if not isinstance(x, str) or not x or x != x.strip() or x.startswith("/") or ".." in x.split("/"):
            raise SemanticTaskBuilderError(f"invalid item in {name}: {x!r}")
        if x in seen:
            raise SemanticTaskBuilderError(f"duplicate item in {name}: {x!r}")
        seen.add(x)
    return items

def _load_stage(root: Path) -> str:
    sig = inspect.signature(load_active_assignment_scope)
    res = load_active_assignment_scope(root) if sig.parameters else load_active_assignment_scope()
    st = res if isinstance(res, str) else (res.get("stage_id") or res.get("stage") if isinstance(res, dict) else getattr(res, "stage_id", getattr(res, "stage", None)))
    if not isinstance(st, str) or not st:
        raise SemanticTaskBuilderError(f"invalid stage from active scope: {st!r}")
    return st


def _identity_source_bytes(root: Path, target: Path, label: str) -> bytes:
    """Read a source for hashing with a bounded, link-safe identity allowance."""
    try:
        return runner.bounded_file_bytes(root, target, runner.MAX_CONTEXT, label)
    except runner.ProposalError as exc:
        if "exceeds the safe context budget" not in str(exc):
            raise
    return runner.bounded_file_bytes(root, target, MAX_IDENTITY_SOURCE_BYTES, label)

@_wrap_errors
def build_task_identity(root: Any, gap: Any, scenario: Any, context_files: Any, allowed_files: Any, excerpts: Any) -> dict[str, Any]:
    if not isinstance(root, (str, Path)):
        raise SemanticTaskBuilderError("root must be path or str")
    r = Path(root).resolve(strict=True)
    if not r.is_dir():
        raise SemanticTaskBuilderError(f"root not a directory: {r}")
    stage_id = _load_stage(r)
    ctx, alw = _check_str_list(context_files, "context_files"), _check_str_list(allowed_files, "allowed_files")
    if not isinstance(excerpts, list) or len(excerpts) > 32:
        raise SemanticTaskBuilderError("excerpts must be list <= 32")
    ctx_set, alw_set, exc_paths, exc_map, sym_groups = set(ctx), set(alw), set(), {}, []
    for e in excerpts:
        if not isinstance(e, dict) or set(e.keys()) != {"path", "sha256", "symbols", "ranges"}:
            raise SemanticTaskBuilderError("excerpt must have keys {path, sha256, symbols, ranges}")
        p, sha, syms, rngs = e["path"], e["sha256"], e["symbols"], e["ranges"]
        if not isinstance(p, str) or p in exc_paths or p in ctx_set or p in alw_set:
            raise SemanticTaskBuilderError(f"invalid/duplicate/overlapping excerpt path: {p!r}")
        exc_paths.add(p); exc_map[p] = sha; sym_groups.append({"path": p, "symbols": syms})
        if not isinstance(rngs, list):
            raise SemanticTaskBuilderError("ranges must be list")
        seen_rng: set[tuple[int, int]] = set()
        sorted_rng = []
        for rg in rngs:
            if not isinstance(rg, dict) or set(rg.keys()) != {"start", "end", "text"}:
                raise SemanticTaskBuilderError("range must have keys {start, end, text}")
            s, en, t = rg["start"], rg["end"], rg["text"]
            if type(s) is not int or type(en) is not int or not isinstance(t, str) or s < 0 or s > en or (s, en) in seen_rng:
                raise SemanticTaskBuilderError(f"invalid/duplicate range: {rg!r}")
            seen_rng.add((s, en)); sorted_rng.append((s, en))
        sorted_rng.sort()
        if any(sorted_rng[i][1] >= sorted_rng[i + 1][0] for i in range(len(sorted_rng) - 1)):
            raise SemanticTaskBuilderError("overlapping ranges")
    gap_path = gap.split("#")[0] if isinstance(gap, str) and "#" in gap else str(gap)
    all_paths = sorted(ctx_set | alw_set | exc_paths | {gap_path})
    alw_only = alw_set - ctx_set - exc_paths - {gap_path}
    manifest: dict[str, str | None] = {}
    for name in all_paths:
        target = runner.relative_file(r, name)
        if not target.exists():
            if name in alw_only:
                manifest[name] = None
                continue
            raise SemanticTaskBuilderError(f"missing required file: {name}")
        manifest[name] = runner.sha(_identity_source_bytes(r, target, name))
        if name in exc_map and manifest[name] != exc_map[name]:
            raise SemanticTaskBuilderError(f"stale excerpt sha for {name}")
    ident = build_identity(stage_id=stage_id, gap_reference=gap, scenario_key=scenario, symbol_groups=sym_groups, source_manifest=manifest)
    return copy.deepcopy(ident)

@_wrap_errors
def validate_task_identity(root: Any, gap: Any, scenario: Any, context_files: Any, allowed_files: Any, excerpts: Any, identity: Any) -> dict[str, Any]:
    val = validate_identity(identity)
    rebuilt = build_task_identity(root, gap, scenario, context_files, allowed_files, excerpts)
    if val != rebuilt or identity != rebuilt:
        raise SemanticTaskBuilderError("identity mismatch with rebuilt")
    return copy.deepcopy(rebuilt)
