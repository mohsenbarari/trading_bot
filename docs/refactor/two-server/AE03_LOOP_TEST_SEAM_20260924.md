# AE-03 Loop Test Seam Status Note (2026-09-24)

- **Scope**: ONLY test seam contracts in `tests/test_two_site_codex_autopilot.py`. No production application code or runner logic changed.
- **Hook Status**: The `before_model` hook parameter exists on `run_cycle`, but `run_loop` is NOT yet wired to pass or invoke `before_model`.
- **Supervisor State**: The background supervisor remains STOPPED.
- **Runtime Effect**: Production runtime effect is UNKNOWN until supervisor wiring is completed and resumed.
- **Test Seam Updates**:
  - Test fakes for `fake_run_cycle` adopt the explicit signature `(root, codex, s_root, cycle, timeout, recovery=None, continuation=None, before_model=None)` without broad `**kwargs`, invoking `before_model` as a standalone one-shot callback when provided.
  - Early lifecycle and loop containment tests use canonical `validate_repository` mocks and command-aware `fake_git` dispatchers for HEAD, status, and merge-base commands (unexpected commands raise).
  - In `test_rescope_counter_resets_after_real_commit`, `fake_validate_repository(*args, **kwargs)` accepts keyword arguments (such as future `require_clean=True`) and truthfully tracks `current_head` transitions from `a * 40` to `b * 40` upon cycle 2 commit, maintaining truthful `b * 40` context for cycles 3 through 5.
- **History Preservation**: Previous operational and recovery records, including `AE03_RECOVERY_STATUS_20260924.md`, remain strictly untouched with no rewriting of status history.
