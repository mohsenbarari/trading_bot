# Two-Site Refactor Safety

This workspace rule applies whenever two-site plan, deployment, infrastructure,
runtime-role, sync, Market model, dashboard, or execution-governance files are read or
changed.

Canonical sources:

- @../../docs/refactor/two-server/MASTER_PLAN.md
- @../../docs/refactor/two-server/EXECUTION_GOVERNANCE.md
- @../../docs/refactor/two-server/execution-order.yaml
- @../skills/two-site-refactor/SKILL.md

Hard invariants:

- The current proposal-only simplification is governed by
  `docs/refactor/two-server/PROPOSAL_AUTOMATION.md`: Codex applies changes; Gemini has
  no source/Git/state authority. One bounded cycle with an independent Codex verifier
  is validated; this is not Stage/production acceptance or a permanent daemon. The native
  Worker/fence/gateway mechanics below apply only to the paused legacy profile;
  do not activate that profile while completing proposal-runner work. Scope,
  independent acceptance, evidence and external/human Writer gates still apply.
  Enforce stdout+stderr limits during streaming, and fail closed if the pinned CLI
  version/SHA changes before, during or after a run; path names are not version proof.

- One Execution Coordinator owns the integration branch and ledger. One writing Worker
  is the default; a second requires the exact Codex pairing receipt and disjoint paths,
  locks, and rollback.
- Each Worker executes one valid Assignment in one registered worktree. Candidate
  integration is serial.
- Exactly one ACTIVE orchestrator fence owns mutations. Its platform, instance, epoch,
  integration head, Assignment and immutable-contract digest must match before every
  mutation phase or commit; takeover requires clean registered worktrees, is fail-closed,
  and supersedes the old Assignment.
- Workers use read-only `worker-check` while a candidate may be dirty. Only the
  Coordinator may run `stage-advance` after a clean descendant commit and before review.
- Before opening the next Stage, require `stage-open-check`; unknown metadata and every
  production/destructive/DNS/Writer/secret classification fail closed.
- Success, failure, rollback/recovery, and post-integration evidence are mandatory.
- No Coordinator, Worker, Cursor model, or Antigravity model may self-approve. Stage
  implementation needs an exact-SHA `CODEX_FINAL_REVIEWER` receipt; dependencies are
  released only after the patch-bound closeout receipt and post-commit verification.
- Deploy never changes DNS or Web Writer. Writer transfer remains an explicit human
  Dashboard action. Never create dual Web Writers or duplicate Telegram owners.
- Stop before production, external mutation, deploy, migration/restore, secret access,
  real Object Storage mutation, DNS/Writer change, destructive cleanup, or retirement
  without the exact scoped Codex pre-action receipt.
- Never bypass permissions broadly, manufacture approval, use fixture-only evidence for
  High/Critical gates, reset checkpoints, delete evidence, or synthesize Market events
  to make a gate green.
- Preserve user changes and existing Web/Bot behavior. Unknown parity blocks the
  affected path.
