# Autonomous Antigravity Orchestration

## Primary-agent entry

Select `two-site-coordinator` from Antigravity's Agent Manager and send:

```text
Resume the approved two-site refactor autonomously from canonical repository and local
receipt state. Continue only dependency-ready non-external work. Do not perform any
production, external, destructive, DNS, Writer, secret, deploy, migration, or real
Object Storage action without its exact scoped Codex pre-action receipt.
```

The Coordinator must run `python3 scripts/validate_antigravity_two_site_adapter.py`
and the `--runtime` discovery plus exact custom-Coordinator read-only smoke before
takeover. It requires clean integration/Stage worktrees, an unexpired Assignment and
an immutable-contract digest, then performs the governance
`RECONCILING → ACTIVE` orchestrator-fence sequence, supersedes the prior Cursor
Assignment, and issues its replacement with platform `ANTIGRAVITY` plus the new epoch.
Until fence, registry and Assignment all match, no Worker may write.

## Worker invocation contract

Invoke custom type `two-site-stage-worker`, role `Stage writing Worker`, and workspace
`inherit`. The prompt must include the exact Stage/Assignment IDs, full base SHA,
branch, registered absolute worktree path, allowed/forbidden paths, locks, parallel
peer, deliverables, success/failure/rollback tests, evidence/report destinations,
expiry, orchestrator epoch, current receipt findings, and explicit external-access
value. The Worker must revalidate the ACTIVE fence before every mutation and commit.
It uses `two_site_orchestrator_fence.py worker-check`, which is read-only and therefore
does not confuse legitimate candidate dirtiness with ownership drift. After the final
clean Worker commit, only the Coordinator runs `stage-advance --stage-head <sha>`; that
command proves ancestry and atomically updates the registered Stage HEAD before review.

After exact-SHA approval, patch-bound closeout verification, and integration-fence
`advance`, the Coordinator deterministically selects a dependency-ready non-external
Stage, commits only its canonical `IN_PROGRESS` opening under standing authorization,
and advances the fence again. It then prepares the matching Assignment/worktree and runs
`stage-bind --next-assignment <id>`. This closes the prior local Stage record and
atomically rebinds registry/fence. It rejects external-access Assignments, which remain
behind their separate pre-action gate.

The Worker must commit the complete candidate on its assigned branch after tests pass,
return the exact commit SHA, and leave its worktree clean. It never pushes or integrates.

Keep the returned conversation ID. On corrections, use `send_message` to that same idle
Worker. If it was killed, invoke a new Worker only after constructing a complete handoff
from durable Assignment/diff/evidence state; never pretend its context survived.
Persist IDs and loop state atomically in
`.local/two-site-refactor/antigravity-state.json` using
`resources/antigravity-state.schema.json`. Do not store prompts, source, credentials, or
raw evidence in this cache.

## Verifier invocation contract

Invoke custom type `two-site-stage-verifier`, role `Independent Stage verifier`, and
workspace `inherit`. Supply exact candidate base/head, registered worktree, Assignment,
Worker report, changed paths, evidence, tests, and applicable receipts. The Verifier may
inspect and run safe tests but cannot author, integrate, approve, or modify central
state. Route all findings directly back to the Worker.

## Integration and final review

After `VERIFIED`, confirm the integration worktree is clean and still at the recorded
HEAD. Integrate and commit one candidate only, run Stage and wave-barrier checks, and
assemble the canonical bundle. Run:

```bash
scripts/two_site_codex_final_review.sh \
  --stage <STAGE_ID> \
  --assignment <ASSIGNMENT_ID> \
  --bundle .local/two-site-refactor/review-bundles/<STAGE_ID>-<ASSIGNMENT_ID> \
  --base <FULL_INTEGRATION_BASE_SHA> \
  --head <FULL_INTEGRATION_HEAD_SHA>
```

Consume `CODEX_FINAL_REVIEW.json` directly. Only `APPROVE` bound to that exact head
permits preparing ledger/Stage closeout. Stage only the minimal
ledger/state/durable-receipt diff and invoke `scripts/two_site_codex_closeout_review.sh`
before committing it. Commit only when `STAGE_CLOSEOUT_PREACTION=APPROVE`, then invoke
the same script with `--verify-commit`; only `CLOSEOUT_COMMIT=VERIFIED` releases
dependencies. Remove the closed Stage worktree inside its retention policy and continue
to the next ready non-external Stage without asking the owner. Any material correction
creates a new candidate commit, integration head, and review.

## Restart and failure scenarios

- **Parent restart:** recover from Git/YAML/registry/receipts, then from the optional
  Antigravity state cache. Reuse an idle conversation only when its ID and Assignment
  still match; otherwise create a new subagent with a full durable handoff.
- **Stale local bootstrap:** prefer observed Git and matching exact-SHA receipts; log
  the mismatch and refresh the hint only after reconciliation.
- **Inaccessible external worktree:** do not copy, move, reset, or delete it implicitly.
  Block that Stage until its path is mounted/authorized or a new Assignment safely
  reissues the work from a proven clean commit.
- **Subagent hang:** inspect with Agent Manager, preserve transcript and task state, then
  terminate only that subagent. Reconstruct a replacement from durable state.
- **Verifier/Codex rejection:** keep the same Assignment only if base, scope, locks, and
  expiry remain valid; otherwise reissue it.
- **External action reached:** stop the affected path and request the exact Codex
  pre-action review. Do not convert it into a routine Antigravity permission prompt.

## Optional CLI entry

When the official `agy` CLI is installed and authenticated, first confirm discovery
with `agy agents`, then use:

```bash
agy -p "Resume the approved two-site refactor autonomously from canonical state; run no external or production mutation." \
  --agent two-site-coordinator \
  --sandbox \
  --output-format stream-json \
  --print-timeout 60m
```

Do not add `--dangerously-skip-permissions`. The IDE path remains fully supported and
does not require the CLI.
