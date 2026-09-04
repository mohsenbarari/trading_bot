---
name: two-site-refactor
description: Autonomously coordinate or execute one assigned Stage of the Finland-primary/Iran-standby refactor with Cursor subagents, independent Codex final review, bounded concurrency, evidence, rollback, and external-action gates.
disable-model-invocation: true
---

# Two-Site Refactor Orchestrator / Stage Worker

Use this skill only when explicitly invoked. The normal entrypoint is one long-lived
parent Cursor conversation:

```text
/two-site-refactor role=coordinator mode=autonomous stage=auto
```

`stage=<ID>` resumes or limits execution to one Stage; `stage=auto` continues through
dependency-ready non-external Stages. The parent Coordinator delegates writing and
verification to project subagents and receives their results directly. Do not require the
project owner to copy reports between conversations. `role=worker` remains available only
for recovery of an already-issued Assignment.

Cursor cannot act as `CODEX_FINAL_REVIEWER` and cannot approve its own output. The
Coordinator uses the local Codex bridge for independent review. Standard non-external
Stage execution has standing authorization; live external or production mutation still
requires an exact Codex pre-action receipt.

## Load

1. Read `AGENTS.md`, then load project memory through `docs/memory/manifest.md`.
2. Read `docs/refactor/two-server/MASTER_PLAN.md`.
3. Read `docs/refactor/two-server/EXECUTION_GOVERNANCE.md`.
4. Read `docs/refactor/two-server/execution-order.yaml` and the matching section
   `EXECUTION_PLAN.md`.
5. Read only the Stage-specific contracts, code, tests and evidence referenced there.
6. For `mode=autonomous`, read
   [`references/autonomous-orchestration.md`](references/autonomous-orchestration.md).

Do not use the historical `candidate/wa-ir-standby-v1` as a merge source. It may be
read only when the canonical plan explicitly asks for historical comparison.

## Before changing anything

- Require an explicit role, Stage ID and valid Work Assignment. A dependency-ready,
  non-external Stage uses the standing authorization in `execution-order.yaml`.
- Require one ACTIVE orchestrator fence matching platform `CURSOR`, the current
  Coordinator instance, integration HEAD, Assignment, epoch and immutable-contract digest
  before every mutation or commit. A takeover requires clean registered worktrees and
  supersedes the old Assignment through the fail-closed sequence in execution governance;
  stale epochs cannot resume.
- Verify every `depends_on` Stage is `COMPLETE` with evidence; design approval is not completion.
- Record branch, base SHA, worktree status, connectivity, Writer, release/config/schema and
  authority state relevant to the Stage.
- Coordinator alone owns `refactor/two-site-architecture`, the central ledger and integration.
  A Worker uses only its assigned `refactor/stage/<STAGE_ID>-<slug>` branch and registered
  ephemeral worktree; never share a worktree or discard unrelated changes.
- Default to one writing Worker. A second writing Worker requires a valid
  `CODEX_PAIRING_APPROVED` receipt and disjoint paths, locks and rollback.
- If a decision, High/Critical gap or required evidence is missing, stop with `BLOCKED`
  and route it to `CODEX_FINAL_REVIEWER`.

## Hard authorization stops

Obtain a new, scoped Codex Final Reviewer receipt immediately before any production or external
mutation, including provisioning, deploy, database migration/repair/restore, real Object
Storage write/lifecycle change, secret access/rotation, DNS change, Writer transfer,
destructive cleanup or legacy retirement. User re-approval is not required, but the exact
Codex receipt, target, SHA, preflight and validity window are mandatory.

Never let deploy change DNS or Web Writer. Never auto-promote Writer. Never create dual Web
Writers or duplicate Telegram/Bot/Executor/job owners. Never bypass a gate with synthetic
Market data, checkpoint reset, evidence deletion or fixture-only High/Critical proof.

## Worker: execute one Stage

1. Create a bounded plan from the Stage card; list exact files/components and invariants.
2. Capture or refresh the current baseline before implementation.
3. Make only the Stage-scoped change. Preserve Web/Bot surface differences and existing
   behavior unless an approved defect/change ID explicitly authorizes a product change.
   Use read-only `two_site_orchestrator_fence.py worker-check` before mutation and
   add/commit boundaries; legitimate dirtiness is reported but never written to registry.
4. Run success, failure and rollback/recovery verification proportional to risk.
5. Store machine-readable, redacted evidence outside repository `tmp` under the approved
   retention class; never store credentials or raw sensitive payloads.
6. Write a Persian report from `docs/refactor/two-server/templates/STAGE_REPORT.md` and
   return `COMPLETE_CANDIDATE`, `BLOCKED`, or `FAILED` to the Coordinator. A Worker must
   not edit the central ledger or integrate its branch.

## Coordinator: orchestrate, integrate, and review

1. Verify the Assignment, locks, dependency receipts and scope, then invoke the
   `two-site-stage-worker` subagent with complete context. Keep its returned agent ID.
2. After the clean Worker commit, run fenced `stage-advance --stage-head <sha>` to prove
   ancestry and update the Stage registry atomically. Then invoke the read-only
   `two-site-stage-verifier`. Route findings back by resuming the same
   Worker; do not ask the project owner to relay them.
3. Apply one verified candidate at a time onto the current integration HEAD; this is
   candidate assembly, not Stage approval or promotion to `main`.
4. Run the Stage integration tests after each integration and the wave barrier when due.
5. Assemble the exact review bundle and run `scripts/two_site_codex_final_review.sh`.
   Consume its JSON receipt directly. The bridge must fail closed until the project owner's
   explicit local source-review authorization exists; never create that authorization from
   an Agent inference or a general execution grant.
6. For `CHANGES_REQUIRED`, resume the same Worker with every finding and repeat Worker →
   Verifier → integration → Codex. `REJECT` or `BLOCKED` stops the affected path.
7. Only after `APPROVE`, stage the minimal ledger/state/durable-receipt closeout and run
   `scripts/two_site_codex_closeout_review.sh` to obtain a Codex pre-action receipt bound
   to the exact staged-patch SHA-256. Commit that unchanged index, run the script's
   `--verify-commit` gate, then remove the closed Stage worktree and recompute the ready
   set. Commit and fence-advance the deterministic `IN_PROGRESS` opening of the selected
   ready non-external Stage, then use `stage-bind` to atomically move fence/registry to
   its prepared Assignment. Dependents remain blocked until post-commit verification passes.
8. Continue automatically only through authorized non-external Stages. Promote integration
   to `main` only at an approved branch barrier with a separate exact head/base Codex receipt.

## Finish

The Coordinator reports milestones to the project owner, not mechanical handoffs. Only
`CODEX_FINAL_REVIEWER` can issue `APPROVE` and make a Stage `COMPLETE`. Do not push/merge to
`main`, deploy, mutate an external system or perform destructive cleanup without the exact
receipt required by the governance document. A malformed/missing Codex receipt, changed
head, or reviewer failure is `BLOCKED`, never approval.
