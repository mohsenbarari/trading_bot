# Independent Static Review: Automation Live Session Context Gate

- **Review Date**: 2026-09-14
- **Reviewer**: Gemini Contributor (Autonomous Proposal Workflow)
- **Source Head**: `d808241b6820bf28e202cbc27f888d27ea171e50`
- **Audited Files**:
  - `scripts/two_site_proposal_runner.py` (`3460d7d95c5e1e1477516ab6e482d8519eb03c90b358ed874cd04098bcb48e40`)
  - `tests/test_two_site_context_output_runner_gates.py` (`b7d8499ae49cf4b17553356ec8ac8a53d45ef8e7428f516f62cc287cdd43f23a`)
- **Gap Reference**: `docs/refactor/two-server/PROPOSAL_AUTOMATION.md#live-session-context-gate`
- **Conclusion**: **PASS** (runner gate logic and test invariants are sound; no material defects found)

---

## 1. Pre-Provider Detection Boundary (Requirement 1)

In `scripts/two_site_proposal_runner.py`, `_build_live_session_context` establishes a precise activation boundary:
- **Test-Only Scope**: The gate evaluates `test_only = bool(task.get("allowed_files")) and all(isinstance(path, str) and (path.startswith("tests/") or "/test" in path) for path in task.get("allowed_files", []))`. Implementation tasks affecting application modules (`api/`, `bot/`, `core/`, `scripts/`) immediately evaluate to `test_only = False` and bypass the live session gate entirely.
- **Adapter & Contract Detection**: Activation requires `requested = bool(test_only and LIVE_PATH_RE.search(contract_text) and (SESSION_TEXT_RE.search(contract_text) or adapters))`. AST inspection locates class definitions declaring an `execute` member (`_class_has_session_execute`). Standard `AsyncSession` tests without custom adapters or live/real execution wording remain unaffected.
- **Mandatory Entrypoint Enforcement**: In `validate_task_readiness`, if `session_ctx.get("requested")` is true, the validator enforces that both `adapter_symbols` and `root_symbols` are present. A test attempting to exercise a live path through a custom Session adapter without specifying an authoritative entrypoint fails immediately before provider invocation.

## 2. Bounded Static Call-Closure Resolution (Requirement 2)

The static closure analysis in `_build_live_session_context`, `_resolve_project_symbol`, and `_load_project_module` enforces strict deterministic resource boundaries:
- **Repository & Module Boundaries**: Module resolution is restricted to `PROJECT_MODULE_ROOTS = {"api", "bot", "core", "scripts"}`. Files are loaded using `bounded_file_bytes` with `MAX_SESSION_SOURCE_BYTES = 512_000`, which strictly prohibits directory escapes, hardlinks, symlinks, and concurrent file mutations.
- **Graph Limits**: Traversal is bounded by `MAX_SESSION_FLOW_SYMBOLS = 48`, a maximum call depth of 8, and a maximum re-export resolution chain of 8 hops with loop detection (`seen` set). Roots are bounded to 8, queries to `MAX_SESSION_QUERY_HINTS = 24`, and missing/unresolved symbols to `MAX_SESSION_FLOW_MISSING = 16`.
- **Canonical Excerpts & Explicit Mock Exclusion**: `_canonical_excerpt_blocks` imports facts and AST trees from `CANONICAL_SOURCE_EXCERPTS_JSON`. Dependencies explicitly patched with `AsyncMock`, `MagicMock`, or `Mock` via `_explicit_patch_targets` are excluded from recursive enqueueing, preventing over-inclusion of mocked paths.
- **Fail-Closed Readiness**: In `validate_task_readiness`, any non-zero `omitted_required_symbol_count`, `omitted_missing_symbol_count`, `omitted_query_operation_count`, or `omitted_unresolved_symbol_count` triggers an immediate `ProposalError("live session dependency closure exceeds bounded context: split the task")`. Unresolved or missing symbols raise `ProposalError("missing live session context: ...")`. Additionally, `enrich_repository_dependency_context` enforces an 8192-byte cap on total enriched metadata. No incomplete closure can silently be marked `TASK_READY`.

## 3. Query-Operation and Result-API Extraction & Gate Enforcement (Requirement 3)

- **Extraction Precision & Unresolved Callback Trapping**: Session operations are recognized on function parameters named `db`, `*_db`, or matching `*session*`. Call targets are matched against `SESSION_OPERATIONS` (`execute`, `scalar`, `scalars`, `get`, `add`, etc.). Any call receiving the session that cannot be resolved as a project callable or an explicitly safe builtin (`SESSION_CALL_SAFE_BUILTINS`) is captured as `path:function->callee` unresolved evidence. Chained consumption is traced through `_result_apis_for_call` across immediate AST call parents and variable assignments up to 8 AST levels, resolving consumption pipelines such as `.unique().scalars().all()`.
- **Statement Text Capture**: `_statement_text` unparses expression ASTs and resolves local assignment bindings up to 160 characters.
- **Gate Placement**: Both `check()` and `run()` invoke `enrich_repository_dependency_context()` followed immediately by `validate_task_readiness()`. In `run()`, this validation executes prior to acquiring the CLI execution context, disposable binary preparation, or subprocess execution. Any unresolved callback or overflow triggers fail-closed rejection before `proposal_cli_context`; direct execution cannot bypass the gate.

## 4. Focused Test Review & Controller Evidence (Requirement 4)

- **Test Integrity**: `tests/test_two_site_context_output_runner_gates.py` provides independent assertions for all critical behaviors:
  - `test_live_session_closure_rejects_all_missing_symbols_before_provider` verifies that `check()` and `run()` reject missing symbols (`api/live.py:live` and `core/lookup.py:lookup`) and asserts via monkeypatch trap that `proposal_cli_context` is never reached.
  - `test_complete_live_session_closure_exposes_query_result_contract` verifies that complete context yields `TASK_READY` with zero missing/unresolved symbols, exact query operations, and expected `result_api` (`unique.scalars.all`).
  - Patch exclusion is independently exercised in the test fixture where `core.patched.patched` is mocked with `AsyncMock` and excluded from the required closure.
  - `test_unresolved_live_session_call_fails_closed` verifies that an unresolvable runtime callback receiving the session (`api/live.py:live->runtime_callback`) fails closed with `ProposalError` rather than proceeding open.
- **Controller-Reported Test Evidence**: As reported by the controller, the full suite `tests/test_two_site_*.py` produced 1095 PASS results plus one sandbox NETLINK permission failure (which subsequently PASSED when verified outside the restricted sandbox).

## 5. Residual Static-Analysis Limitations

1. **Dynamic Dispatch & Dynamic Imports**: Dynamic reflection (`getattr`, `importlib`, `__import__`) or indirect call proxies outside static AST patterns cannot be resolved and will register as unresolved project symbols if referenced in contract roots.
2. **Parameter Naming Convention**: Database sessions passed via unconventional parameter names (not matching `db`, `*_db`, or `*session*`) will not have their internal query operations extracted.
3. **Deep Indirection**: Assignments or wrapper helpers exceeding 8 AST levels or across separate modules without passing the session reference are not expanded.

## 6. Audit Conclusion

The implementation of the live session context gate in `scripts/two_site_proposal_runner.py` satisfies all stated architectural, security, and resource-bounding requirements. The focused tests in `tests/test_two_site_context_output_runner_gates.py` independently prove fail-closed gate enforcement prior to provider contact.

**Verdict**: **PASS** (advisory static audit complete; does not constitute Stage transition or product behavior closure).
