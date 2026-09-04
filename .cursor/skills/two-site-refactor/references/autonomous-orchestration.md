# Autonomous Cursor Orchestration

Use this mode from one long-lived parent Cursor conversation. The parent is the
Coordinator; writing and verification run as Cursor subagents, not separate chats the
project owner must relay between.

## Stage loop

1. Resolve the next dependency-ready Stage from `execution-order.yaml`, or resume the
   explicitly supplied in-progress Stage and Assignment.
2. Create/validate the Work Assignment and registered isolated Stage worktree.
3. Invoke `two-site-stage-worker` with all context needed from the clean subagent
   context: exact paths, IDs, base SHA, locks, deliverables, tests, and current receipt.
   The Worker uses the read-only fence `worker-check` at mutation/commit boundaries.
4. After the clean Worker commit, run fenced `stage-advance --stage-head <sha>` to prove
   ancestry and atomically bind the registry to that candidate; only then invoke
   `two-site-stage-verifier`.
5. On `CORRECTIONS_REQUIRED`, resume the same Worker agent with the complete findings.
   Repeat Worker → Verifier until verified or genuinely blocked.
6. Integrate one verified candidate onto the exact integration HEAD and run integration
   checks. Assemble the canonical review bundle.
7. Run `scripts/two_site_codex_final_review.sh` with exact Stage, Assignment, bundle,
   base, and head. Read the generated JSON receipt.
8. On `CHANGES_REQUIRED`, resume the same Worker with the Codex findings and repeat the
   loop. On `APPROVE`, stage only the minimal ledger/state/durable-receipt closeout, get
   a patch-bound `STAGE_CLOSEOUT_PREACTION` receipt through
   `scripts/two_site_codex_closeout_review.sh`, commit the unchanged index, and run its
   `--verify-commit` gate. Only then close the worktree, release dependencies, recompute
   the ready set, run fenced `stage-open-check`, and only then commit the selected Stage's
   deterministic `IN_PROGRESS` opening under standing authorization. Advance the fence,
   prepare its Assignment/worktree at the exact integration HEAD, bind it atomically with
   `stage-bind`, and continue. `REJECT` or
   `BLOCKED` stops the affected path.

The parent keeps returned subagent IDs so it can resume context rather than asking the
project owner to copy reports. Background read-only reconnaissance may run while the
single writer works. A second writer still requires the pairing receipt and isolation
defined by governance.

## Codex bridge

The bridge starts a fresh, non-interactive Codex reviewer from the repository. It is not
the Cursor model and does not reuse a Cursor conversation. It loads repository rules and
memory, reviews the exact integration commit in a read-only sandbox, and emits a
machine-readable receipt into the ignored review bundle. Because that review transmits the
selected private-repository diff and evidence to OpenAI Codex, the wrapper requires a
one-time explicit project-owner authorization at
`.local/two-site-refactor/authorizations/CODEX_SOURCE_REVIEW.md`. No Agent may manufacture
or infer this authorization.

Check availability without invoking a review:

```bash
scripts/two_site_codex_final_review.sh --check-only
```

Review an integrated candidate:

```bash
scripts/two_site_codex_final_review.sh \
  --stage P1-00 \
  --assignment WA-P1-00-REFRESH-20260903-001 \
  --bundle .local/two-site-refactor/review-bundles/P1-00-WA-P1-00-REFRESH-20260903-001 \
  --base <FULL_BASE_SHA> \
  --head <FULL_HEAD_SHA>
```

The Coordinator consumes `CODEX_FINAL_REVIEW.json`; the Markdown companion is for human
audit. Missing source-review authorization, a changed head, malformed receipt, CLI/auth
failure, or missing evidence is `BLOCKED`, never implicit approval.

## Stops

- Production/external/destructive operations still require their exact scoped Codex
  pre-action receipt.
- DNS and Web Writer never change as part of deploy. Web Writer transfer remains an
  explicit human Dashboard action.
- A product decision not fixed by the approved plan, a secret requirement, or a
  High/Critical unresolved gap is routed to Codex and blocks only the affected path.
