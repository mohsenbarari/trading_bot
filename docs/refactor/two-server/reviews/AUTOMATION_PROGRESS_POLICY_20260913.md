# Independent Static Audit Report: Automation Progress Policy & Maintenance Exception

- Date: 2026-09-13
- Task ID: `maint-docs-exception-audit-20260913-08`
- Inspected HEAD: `06a9c431ae7f96c4af2054708b2a1d85d80f7fe5`
- Auditor: Gemini Contributor (Independent Static Audit Role)
- Review Scope: Static audit of owner-authorized one-off Codex-authored prompt, documentation, and index maintenance.

## 1. Authorization Scope & Governance Boundaries

This audit evaluates a specific, owner-authorized one-off maintenance exception permitting Codex authoring of automation prompt, documentation, and inventory updates. Normal unattended project development roles remain strictly unchanged:

1. **Unattended Role Integrity**: In standard unattended development, Gemini Worker remains the sole author of product code, test implementations, and project documentation. The independent Gemini Reviewer acts purely as a non-authoring verifier (`changes = []`). Codex serves as the coordinator, final semantic reviewer, test executor, exact-byte patch applier, and sole Git committer.
2. **Standing Security & Permission Constraints**: Standing authorization (2026-09-09) strictly governs non-secret source, test, task, doc, proposal, and review transfer to Google Antigravity/Gemini without per-transfer approvals. Credentials, user/personal data, operational/production data, deployment modifications, DNS changes, and Web Writer operations remain strictly excluded. First-attempt invocations use narrow escalation rather than broad bypass.
3. **Model & Architecture Configuration**: Supervisor remains pinned to `gpt-5.6-sol/xhigh` and Gemini runtime remains pinned to `gemini-3.8-flash-high/high`. No model downgrades or gate relaxations have been introduced.

## 2. SHA-256 Source File Bindings

The static audit evaluated the following stable candidate files against their canonical SHA-256 digests:

| Source Path | Canonical SHA-256 |
| --- | --- |
| `docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md` | `e8eecc342572a30b9e7be33d22206f905b79a4c6275ae675ceb80a29ad4caa00` |
| `docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md` | `d29ff092676b5d327b99946002bb62fb2780b73d0dd064d653ec1fbaa99bffa1` |
| `docs/refactor/two-server/PROPOSAL_AUTOMATION.md` | `33a2752d1f028d4907ca4f910551aa8d4fb00202ad7424750f31de557626f1cf` |
| `docs/refactor/two-server/section-1/evidence-index.md` | `3c3f8f304b7e93ea29f3fda7128cbc087c2ff909639944c141a85ae1e262732c` |
| `docs/refactor/two-server/section-1/inventory/recent-characterization.json` | `8b9fc911cc22eb4633ae5df604ae8dc6be6e23577e40bac79d27ca30017cb5ee` |
| `docs/memory/areas/documentation-execution.md` | `184728139dee3a399f6d0b36981ccbc63f36c548b569acdfb4b90e6ecdf5b294` |
| `tests/test_two_site_prompt_transport_contract.py` | `0825ee6c52034306a0fa7e474a4c3b4750139f3a5eb286538f3b7e9e0f4e77ef` |

## 3. Contract Guidance, Unicode, Fixtures, and Verification Invariants (Requirement 1)

1. **`preparation_notes` Contract**: The helper interface provides bounded advisory metadata (`exact_blocker_ids`, `warnings`, `literal_hints`, `omitted_literal_count`) derived from original task sources before byte/secret gates. It is explicitly not an approval, not persisted as source identity, and does not alter the immutable 6-key task schema (`task_id`, `goal`, `context_files`, `allowed_files`, `requirements`, `test_selectors`).
2. **Literal Unicode & Persian Script**: The prompt contracts enforce explicit preservation of code points including Persian zero-width non-joiners (ZWNJ U+200C / nim-fasele), U+200D, and directional marks. The distinction between JSON escapes, Python source escapes, and runtime values is maintained. Tests must specify fixed independent expected values without evaluating or calling the function under test.
3. **Call Contracts & Fixture Schemas**: Assertions must verify exact positional argument counts, exact keyword sets, object identity where appropriate, await counts, execution ordering, and downstream suppression. Fixtures must strictly satisfy canonical envelope constraints (UUID, integer sequence, RFC3339 timestamps) only where the authoritative upstream schema requires them, not across all project fixtures.
4. **Reviewer Independence & Pipeline Order**: Pipeline execution materializes candidate bytes and executes focused test selectors in an isolated no-network candidate workspace *before* starting a fresh independent Gemini Reviewer. A test failure routes directly to correction, preventing reviewer budget waste. `PASSED` status is explicitly treated as execution evidence only, not semantic approval. The Reviewer enforces non-authoring behavior (`changes = []`).

## 4. Handler Batching, Reviewer Budget, and Locator Reconciliation (Requirement 2)

1. **Coherent Handler Grouping**: A single task package may group 2–3 closely related handlers in a shared user flow provided each scenario has independent assertions, explicit inclusion/exclusion boundaries, and the total requirement count normally remains <=6. Stages may not be bundled.
2. **Reviewer Package Sizing**: Sizing advisories target <=80,000 bytes for `TASK_READY` and <=160,000 bytes for the complete Reviewer payload (prompt template + task/sources + materialized proposal). Sizing targets remain advisories rather than lower hard limits; hard limits remain 160KB for proposals and 320KB for full prompts. Existing files with UTF-8 content >=8,192 bytes must use exact replacements.
3. **Reconciliation Cadence & State Preservation**: `units_since_reconciliation` is tracked within the `next_action` field and persists across process restarts. Reconciliation into `section-1/inventory/recent-characterization.json` is mandatory after at most 5 accepted behavior units before further characterization. The counter resets only after an accepted reconciliation.
4. **Tracked Locator & Blocker Visibility**: `recent-characterization.json` currently records 13 files and 39 test selectors, all marked `PARTIAL`. The canonical inventory has 135 evidenced handlers and 330 still-unexecuted seeds (465 total), plus 24 unresolved origin cells; this maintenance makes no acceptance/count changes. Active blockers (`P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`, `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`) remain open and visible. No automated seed/count closure, new queues, or services have been added.

## 5. Provider Telemetry, Controller Test Evidence, and Limits (Requirement 3)

1. **Honest Provider Metrics**: `provider_metrics` tracks same-prefix/task/head attempts, successful corrections, failed attempts, and observable token subtotals. Incomplete data is recorded as `UNKNOWN`/`null`, not zero consumption. `outside_provider_unattributed` is explicitly classified as unprofiled residual time, not wasted time. Reasoning tokens are treated as a subset of output tokens without double-counting.
2. **No Claimed Net Savings or Live Execution**: This audit is strictly static and performed offline. No live test execution, runtime stage completion, or net token savings are claimed by the auditor.
3. **Controller Test Evidence**: The controller reported passing execution of 166 focused offline tests and 193 broader offline tests against the candidate baseline. Static analysis confirms full contract compliance across all checked paths.
4. **Audit Conclusion**: Candidate prompt templates, documentation updates, and evidence index maintenance are consistent, safe, and adhere to all architectural invariants.
