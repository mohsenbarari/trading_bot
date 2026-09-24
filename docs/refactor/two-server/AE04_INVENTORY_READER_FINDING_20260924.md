# AE04 inventory reader mismatch (2026-09-24)

Status: **CONFIRMED READ-ONLY FAILURE; NO CODE FIX APPLIED**. On clean `refactor/two-site-architecture` at `a076e1e14e9d116d9f2ee7568c5e5e0d6e9d1590`, the real command

```sh
APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
  /usr/bin/python3 scripts/two_site_cycle_tools.py inventory \
  --behavior-id BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183
```

returns `INVENTORY_INCONSISTENT: canonical manifest inventory or sources invalid` (exit 1). This is fail-closed, not proof of a false-positive coverage claim. The 20 selected inventory-display and continuation-4 tests pass because one test family uses an older fixture and the other validates continuation 4 separately; they do not exercise the current manifest through `inspect_canonical_evidence`.

## Exact mismatch

`seed-handler-coverage.json` has six `evidence_sources` keys, including `continuation_4_path`, and counts 465 seeds / 377 evidenced / 88 remaining. `two_site_cycle_tools.py:inspect_canonical_evidence` requires exactly five keys through `continuation_3_path` and builds a fixed two-item continuation list. It rejects the current six-key manifest before reading any record. The fourth continuation holds two records for the same invitation-input test path: one current receipt and one historical provenance receipt. Merely accepting the sixth key is insufficient; the reader must validate its predecessor binding and distinguish historical blob provenance from current-HEAD coverage.

## Bounded repair gate after Gemini credit returns

1. Preserve old five-key fixture compatibility and add explicit six-key lineage handling. Validate continuation 4's predecessor path/SHA/last-commit against continuation 3 through bounded Git reads. Do not silently ignore a declared source.
2. For a current record, require live HEAD blob, latest touch, selectors and seed IDs as before. For an intentional historical provenance record, verify its `last_commit:path` blob SHA and ancestry, but do not count it as new current coverage. Same seed appearing in current and historical provenance is not automatically duplicate coverage; conflicting current records must fail closed.
3. Add one exact current-manifest integration selector that asserts `inventory_view` is `VALID` for the invitation-input behavior, plus negative fixtures for missing/tampered continuation 4, broken predecessor, duplicate/conflicting current evidence, stale live blob and over-budget input. Existing five-key fixture tests must remain green.
4. Rebuild the derived coverage/index only from validated accepted receipts. Keep bounded 2-MiB/file and 8-MiB total reads, full Reviewer diff, and reconciliation within five accepted units. Do not rewrite the 1.27-MiB scenario baseline just to remove this mismatch.

This is an AE04 reader contract problem, not a reason to bypass AE03's no-replay quota gate or activate parallel Workers. No Gemini call is issued while the user reports weekly credit exhausted.

## Consumer audit at integration HEAD `8f607e22`

A tracked-code search found one implementation path for this canonical manifest: `inspect_canonical_evidence` feeds `inventory_view` and the `two_site_cycle_tools.py inventory` CLI. The P1 characterization tests inspect the manifest and continuation files directly. Most reader/presentation tests in `test_two_site_canonical_evidence_inspect.py`, `test_two_site_canonical_evidence_read_bounds.py`, and `test_two_site_inventory_display.py` use older fixtures, but `test_real_worktree_baseline` in the first file does exercise the live manifest and currently fails. No direct call site in Supervisor or Pipeline was found by name; this does **not** prove that a human or another external caller never invokes the CLI. The exact live CLI still exits 1 with `INVENTORY_INCONSISTENT` at this HEAD.

Acceptance must therefore test both the pure reader **and** the real CLI on the current committed manifest, keep five-source fixtures green, and run the read-bound suite after adding continuation 4. A passing continuation-4 provenance test alone is not an end-to-end reader acceptance signal.

Focused baseline at this HEAD: `pytest -q tests/test_two_site_canonical_evidence_inspect.py tests/test_two_site_canonical_evidence_read_bounds.py tests/test_two_site_inventory_display.py tests/test_p1_00b_characterization_recent_inventory_continuation_4.py` gives **1 failed, 30 passed**. The failure is `test_real_worktree_baseline`, which expects `VALID` but gets `INVENTORY_INCONSISTENT` for the current repository; the 30 passing fixture/provenance cases do not negate it. The repair must turn this exact test green without weakening the fail-closed corruption tests.
