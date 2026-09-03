---
name: two-site-refactor
description: Coordinate or execute one assigned stage of the Finland-primary/Iran-standby refactor with bounded concurrency, independent Codex final review, evidence, rollback, and external-action gates.
disable-model-invocation: true
---

# Two-Site Refactor Coordinator / Stage Worker

Use this skill only when explicitly invoked with `role=coordinator|worker` and a
Stage ID. Cursor cannot act as `CODEX_FINAL_REVIEWER` and cannot approve its own
output. Standard non-external Stage execution has standing authorization; live
external or production mutation still requires an exact Codex pre-action receipt.

## Load

1. Read `AGENTS.md`, then load project memory through `docs/memory/manifest.md`.
2. Read `docs/refactor/two-server/MASTER_PLAN.md`.
3. Read `docs/refactor/two-server/EXECUTION_GOVERNANCE.md`.
4. Read `docs/refactor/two-server/execution-order.yaml` and the matching section
   `EXECUTION_PLAN.md`.
5. Read only the Stage-specific contracts, code, tests and evidence referenced there.

Do not use the historical `candidate/wa-ir-standby-v1` as a merge source. It may be
read only when the canonical plan explicitly asks for historical comparison.

## Before changing anything

- Require an explicit role, Stage ID and valid Work Assignment. A dependency-ready,
  non-external Stage uses the standing authorization in `execution-order.yaml`.
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
4. Run success, failure and rollback/recovery verification proportional to risk.
5. Store machine-readable, redacted evidence outside repository `tmp` under the approved
   retention class; never store credentials or raw sensitive payloads.
6. Write a Persian report from `docs/refactor/two-server/templates/STAGE_REPORT.md` and
   return `COMPLETE_CANDIDATE`, `BLOCKED`, or `FAILED` to the Coordinator. A Worker must
   not edit the central ledger or integrate its branch.

## Coordinator: integrate and hand off

1. Verify the Assignment, locks, dependency receipts, scope and Worker evidence.
2. Apply one candidate at a time onto the current integration HEAD; this is candidate
   assembly, not Stage approval or promotion to `main`.
3. Run the Stage integration tests after each integration and the wave barrier when due.
4. Assemble the exact review bundle described in `EXECUTION_GOVERNANCE.md`.
5. Request independent Codex final review; do not start a dependent Stage before its receipt.
6. Only after `APPROVE`, append the receipt to `CHANGE_LEDGER.md`, mark the exact integration
   commit `COMPLETE` and remove the closed stage worktree within its retention window.
7. Promote integration to `main` only at an approved branch barrier with a separate exact
   head/base Codex receipt.

## Finish

Cursor reports a candidate or integration result; only `CODEX_FINAL_REVIEWER` can issue
`APPROVE` and make a Stage `COMPLETE`. Do not push/merge to `main`, deploy, mutate an
external system or perform destructive cleanup without the exact receipt required by the
governance document.
