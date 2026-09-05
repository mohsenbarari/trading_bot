---
name: two-site-refactor
description: Coordinates and executes the Finland-primary/Iran-standby refactor through Antigravity custom subagents, registered worktrees, serial integration, independent verification, exact-SHA Codex acceptance, and fail-closed external-action gates.
---

# Two-Site Refactor for Antigravity

Use this skill for any Antigravity execution or recovery of the approved two-site
refactor. The normal entrypoint is the `two-site-coordinator` primary custom agent.
Writing and verification are delegated to the project custom subagents; the project
owner must never carry reports between conversations.

## Required load

1. Read `AGENTS.md`; then read `docs/memory/manifest.md`, `docs/memory/brief.md`, and
   only the task-routed memory files.
2. Read `docs/refactor/two-server/MASTER_PLAN.md`, `EXECUTION_GOVERNANCE.md`, and
   `execution-order.yaml`.
3. Read `.agents/rules/two-site-refactor-safety.md` and
   [`resources/AUTONOMOUS_ORCHESTRATION.md`](resources/AUTONOMOUS_ORCHESTRATION.md).
   For the active Antigravity adapter, also read
   [`resources/TYPED_GATEWAY.md`](resources/TYPED_GATEWAY.md) completely before any
   tool action; it replaces direct shell use with one-use typed operations.
4. Read the matching section `EXECUTION_PLAN.md`, Stage card, Assignment, latest exact
   receipt, and only the source/tests/evidence referenced by that Stage.

Do not use `candidate/wa-ir-standby-v1` as a merge source. It is historical read-only
material only when the canonical plan explicitly requests comparison.

## Recovery before action

Reconstruct state instead of trusting a conversation summary. Use this precedence:

1. actual Git integration branch, HEAD, status, worktree list, and ancestry;
2. canonical Stage states and dependencies in `execution-order.yaml`;
3. `.local/two-site-refactor/worktrees.yaml` and the active Assignment;
4. the ACTIVE `.local/two-site-refactor/orchestrator-fence.json` matching the Assignment
   platform/epoch and Git integration head;
5. exact-SHA Codex/Verifier receipts and review bundle matching the registry;
6. coordinator log, handoff, and bootstrap hints.

Any conflict is `BLOCKED` until reconciled without discarding work. Record Antigravity
conversation IDs and current loop state in ignored
`.local/two-site-refactor/antigravity-state.json`; this cache cannot override Git,
YAML, Assignment, or receipt identity. Validate it against
`resources/antigravity-state.schema.json` before reuse.

Cursor-to-Antigravity takeover uses the fenced sequence in execution governance. Do not
claim the fence until Antigravity runtime discovery/read-only smoke succeeds and the old
Worker is non-running with clean matching Stage and integration worktrees. Require an
unexpired Assignment, anchor its immutable scope digest in the fence, bind the replacement
base/path to the registered Worktree, increment the epoch, supersede the old Assignment,
reconcile the registry, then activate the fence. Recheck it before every mutation and
commit; an old epoch can never resume.

## Stage loop

The Coordinator validates dependencies, Assignment, locks, scope, expiry, and the
registered worktree. It invokes one `two-site-stage-worker` with a self-contained prompt
and requires a tested, clean, committed candidate before invoking one
`two-site-stage-verifier`. The Worker uses read-only `worker-check` at mutation/commit
boundaries; the Coordinator then uses `stage-advance` to bind the clean descendant commit
to the registry before verification. Corrections go directly to the same idle Worker conversation.
The Coordinator integrates and commits one verified candidate, runs integration checks,
builds the exact review bundle, and calls
`scripts/two_site_codex_final_review.sh`.

`CHANGES_REQUIRED` returns to the Worker/Verifier loop. `APPROVE` accepts only the exact
reviewed implementation SHA. The Coordinator then stages only the canonical
ledger/state/durable-receipt closeout, obtains a patch-hash-bound Codex pre-action
receipt with `scripts/two_site_codex_closeout_review.sh`, commits that unchanged index,
and runs the script's `--verify-commit` gate. Only that verified closeout releases
dependencies and allows the ready set to be recomputed. Continue automatically across
all dependency-ready
non-external Stages. `REJECT` or `BLOCKED` stops only the affected path. A
missing/malformed receipt, changed SHA, inaccessible registered
worktree, killed Worker without a complete handoff, or CLI/auth failure is never
implicit approval.

Codex Stage, closeout, and governance review bridges are pinned to `gpt-6-astra` with
`high` reasoning and a read-only sandbox. Do not rely on a caller's default model or
reasoning configuration for final acceptance.

After verified closeout and fence `advance`, select the next dependency-ready candidate and
run fenced `stage-open-check`. Unknown metadata and production/destructive/DNS/Writer/secret
or external flags block automatic continuation. Only then commit its deterministic
`IN_PROGRESS` transition, `advance` again, create its Assignment/worktree at that exact
integration HEAD, and run fenced `stage-bind`. Never edit
the fence by hand or reuse the prior Stage Assignment for a different Stage.
The protected Stage-bind journal must exist before worktree/Assignment/registry effects;
only exact old-fence rollback or exact new-fence roll-forward is recoverable. Immediately
retire the prior COMPLETE worktree after binding, with exact path/branch/HEAD/common-dir
and Assignment checks. Ambiguity remains blocked.

Use `Workspace: inherit` for both subagents and pass the exact registered worktree path.
Do not ask Antigravity to generate an unregistered branch worktree. Default to one
writing Worker; a second may write only with the pairing receipt required by governance.
A second read-only reconnaissance subagent may run without that receipt.

## Authority boundary

Standard dependency-ready non-external Stage work has standing authorization. Every
production/external/destructive action requires its separate exact Codex pre-action
receipt. Web Writer transfer remains a human Dashboard operation. Never use
`--dangerously-skip-permissions`, global write/network grants, automatic Writer
promotion, or deploy-driven DNS change.

## Finish

Report milestones, accepted exact SHAs, residual risks, and genuine blockers. Do not
report routine Worker/Verifier handoffs to the project owner and do not pause for routine
commit, review, correction, or Stage-transition approval. Full execution and recovery
examples are in the orchestration resource.
