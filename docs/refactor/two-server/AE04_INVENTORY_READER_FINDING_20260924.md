# AE04 inventory reader mismatch (2026-09-24)

Status: **REPAIRED AND ACCEPTED ON 2026-09-25; HISTORICAL FAILURE BELOW**. On clean `refactor/two-site-architecture` at `a076e1e14e9d116d9f2ee7568c5e5e0d6e9d1590`, the real command

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

## Acceptance checkpoint (2026-09-25 UTC)

The first combined code+tests task 121 hit `MODEL_OUTPUT_TRUNCATED` before a proposal; zero bytes were applied. Rescoped reader-only task 122 used two Worker corrections for old-fixture semantics, then independent Reviewer verified proposal SHA `193865e3abba67326604eb0e4aa62fd8b9090856b4bd82b71e4ac4f8750bd6cb`. Codex checked exact applied bytes, 31 focused tests and the real CLI, then committed `74b6815682a6517711405e7ae5c854e99e38dc3b`. The reader now accepts exactly five or six known source keys, validates continuation-4 predecessor lineage, and verifies historical provenance at the old ancestor blob without counting it as current coverage.

Test-only task 123 needed one Worker correction for fixture errors; Codex accepted proposal SHA `d7481d519774bef0491825c6b6d98db609f57081ad8faa720ddf9bc30d7c9b1c` after 44 independent focused tests, commit `58f8beae991800d8eca0ef20357c375b6943b42f`. Codex found an overclaim in Reviewer wording: the CLI test sliced stdout only after unbounded `capture_output`. Narrow task 124 corrected capture with the existing streaming hard cap and replaced a nonexistent-hash ancestry test with a real side-branch commit. Proposal SHA `97c68c47871eebf6dccbd0d330a400856af3efa18978ae5987414732f11d0ea8`, 45 independent tests, commit `0983fae6d7eef3fb3de27a30aa6a99f2a5b8a568`.

At committed HEAD `0983fae6`, the exact real CLI request for `BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183` returns `status=VALID`, `seed_count=465`, and `requested_coverage=PROVEN_FOR_REQUESTED_SCENARIO`; the integration tree is clean. No inventory source, baseline, product, production or Supervisor runtime was changed. This closes the AE04 reader regression, not AE03 quota recovery or the empirical efficiency assessment.

## Independent supplemental-source finding (2026-10-20; Scope 767)

At clean accepted base `a082a2846495a78d71ba8a6a95a23c33871f9d74`, the current v1
manifest declares 18 evidence-source keys: baseline and base, 14 selected numbered
continuations, and the descriptive independent receipts
`notification_mark_all_750_path` (accepted 751 accounting packet) and
`trade_read_prequery_757_path` (accepted 757 accounting packet). Its recorded
partial counts are 393/465 evidenced and 72 remaining. The existing source-key
parser accepts only baseline/base and numbered continuation keys, so static
inspection identifies a rejection before either supplemental record is read.
This is a new compatibility finding; the September acceptance above remains
historical and does not establish compatibility with these later receipts.

The bounded reader contract admits descriptive independent `*_path` keys without
a per-Task list. Every `continuation_*` spelling remains reserved for the existing
strict numbered grammar. Supplemental documents belong directly under the
canonical inventory directory as `recent-characterization-*.json`, excluding the
base and continuation namespaces. They must retain the actual partial integration
or partial reconciliation envelope: exact integer schema 1, P1-00b, assignment,
PARTIAL coverage, full-seed acceptance not assessed, closures false, both High
blockers, and the actual COMPLETE/IN_PROGRESS Stage states. Integration receipts
bind ancestor source/parent commits; reconciliation receipts bind an ancestor
inspected HEAD. Base/predecessor links cannot impersonate independent receipts.

All declared supplemental documents and the complete numbered/sparse predecessor
closure are validated before the existing targeted-record filter. Declared
supplement records then use the same current/historical Git blob, mode, SHA,
selector-AST and duplicate checks as other records. An unlisted packet, including
the accepted business A2 packet currently absent from this manifest, contributes
no coverage merely by existing. Historical, unsupported or failed proof never
becomes current coverage. Existing target filtering and UNKNOWN semantics stay
unchanged. Supplements share the existing 64 continuation-source allowance with
distinct audited hidden predecessors; base/baseline exemptions, 2,000,000-byte
per-read and 8,000,000-byte total content limits remain unchanged.

This finding records no new source acceptance, seed/count promotion, Stage/High/
origin closure, runtime health or savings. The proposed code/tests require exact
independent nonauthor and Root review, the complete selected hermetic PRE/POST
union and the real current committed-repository CLI. Git-unavailable fixture
SKIPs in a hermetic snapshot are not live CLI compatibility evidence. No manifest,
old receipt or authority state is rewritten by this consumer change.
