---
name: two-site-refactor
description: Execute one approved stage of the Finland-primary/Iran-standby refactor from the canonical plan with dependency, evidence, rollback, and authorization gates.
disable-model-invocation: true
---

# Two-Site Refactor Stage Executor

Use this skill only when the user explicitly invokes it and names one Stage ID.
The approved design does not authorize implementation or production activity.

## Load

1. Read `AGENTS.md`, then load project memory through `docs/memory/manifest.md`.
2. Read `docs/refactor/two-server/MASTER_PLAN.md`.
3. Read `docs/refactor/two-server/execution-order.yaml` and the matching section
   `EXECUTION_PLAN.md`.
4. Read only the Stage-specific contracts, code, tests and evidence referenced there.

Do not use the historical `candidate/wa-ir-standby-v1` as a merge source. It may be
read only when the canonical plan explicitly asks for historical comparison.

## Before changing anything

- Require an explicit Stage ID and explicit authorization to start that Stage.
- Verify every `depends_on` Stage is `COMPLETE` with evidence; design approval is not completion.
- Record branch, base SHA, worktree status, connectivity, Writer, release/config/schema and
  authority state relevant to the Stage.
- Execution belongs on the single temporary `refactor/two-site-architecture` branch/check-out.
  Do not create a sibling clone/worktree or switch/discard unrelated dirty changes.
- If an owner decision, High/Critical gap or required evidence is missing, stop with `BLOCKED`.

## Hard authorization stops

Obtain a new, scoped user authorization immediately before any production or external
mutation, including provisioning, deploy, database migration/repair/restore, real Object
Storage write/lifecycle change, secret access/rotation, DNS change, Writer transfer,
destructive cleanup or legacy retirement. Approval of this skill or Master Plan is not enough.

Never let deploy change DNS or Web Writer. Never auto-promote Writer. Never create dual Web
Writers or duplicate Telegram/Bot/Executor/job owners. Never bypass a gate with synthetic
Market data, checkpoint reset, evidence deletion or fixture-only High/Critical proof.

## Execute one Stage

1. Create a bounded plan from the Stage card; list exact files/components and invariants.
2. Capture or refresh the current baseline before implementation.
3. Make only the Stage-scoped change. Preserve Web/Bot surface differences and existing
   behavior unless an approved defect/change ID explicitly authorizes a product change.
4. Run success, failure and rollback/recovery verification proportional to risk.
5. Store machine-readable, redacted evidence outside repository `tmp` under the approved
   retention class; never store credentials or raw sensitive payloads.
6. Write a Persian report from `docs/refactor/two-server/templates/STAGE_REPORT.md` and append
   the state transition to `CHANGE_LEDGER.md`.

## Finish

Report `COMPLETE_CANDIDATE`, `BLOCKED`, or `FAILED`; only the owner can accept a Stage as
`COMPLETE`. Do not automatically start its dependent Stage, push, merge, deploy or clean up.
