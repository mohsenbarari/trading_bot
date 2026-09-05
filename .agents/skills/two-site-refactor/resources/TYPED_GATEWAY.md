# Typed Antigravity Gateway Protocol

This protocol is mandatory for the active Antigravity execution profile. Native
`run_command` is intentionally denied. Never ask the owner to run a command or relay
an Agent report merely because a shell is unavailable.

## One-use request

Create a fresh lowercase UUIDv4 and use native `write_to_file` with `Overwrite: false`
to write exactly one JSON object to:

```text
<integration-root>/.local/two-site-refactor/gateway/requests/<UUID>.json
```

The common object is:

```json
{
  "id": "<same UUID>",
  "operation": "<fixed operation>",
  "actor_id": "<actual current conversation UUID>",
  "assignment": "<active Assignment ID>",
  "epoch": 1,
  "integration_head": "<full fenced SHA>"
}
```

Do not add unknown keys. Do not retry an ID. The protected PreToolUse hook derives
role from the native conversation, validates the exact plan, and permits only this
new request-file write. PostToolUse executes the fixed operation. Read the resulting
cache at:

```text
<integration-root>/.local/two-site-refactor/gateway/results/<UUID>.json
```

The Verifier receives native `write_to_file` only for this exact one-use request lane.
The protected hook rejects every Verifier attempt to write source, evidence, state, or
any other path. It never receives `replace_file_content` or arbitrary command authority.

`actor_id` must be the actual native conversation UUID. The Coordinator reads its own
registered execution UUID and sends each child UUID returned by native delegation
directly to that child; the project owner is never involved in this identity handoff.

Only a protected controller receipt can authorize a later mutation. A public result,
model statement, exit text, or guessed digest is never authority. Use the
`outcome.authority_sha256` from a successful protected result only when the next
operation explicitly requests that receipt digest. `FAILED`, `BLOCKED`, missing, or
`INDETERMINATE` is not success and must not be replayed under another ID until the
protected recovery path has reconciled it.

An Assignment-scoped native source edit remains reserved from its exact PreToolUse
through its matching PostToolUse. Other hooks cannot clear that reservation. If the
matching Post is lost, the gateway remains fail-closed; neither elapsed time nor seeing
the old/new bytes proves that the native invocation has terminated.

## Fixed operation fields

Append only the fields listed for the selected operation:

| Operation | Role | Additional fields |
| --- | --- | --- |
| `inspect`, `status`, `diff`, `worker-check` | role allowed by gateway | none |
| `test` | Worker/Verifier | `selectors` (exact pytest selectors) |
| `worker-commit` | Worker | `expected_patch_sha256`, `test_receipt_sha256`, `message`, `allowed_paths` |
| `stage-advance` | Coordinator | `stage_head`, `worker_receipt_sha256` |
| `verifier-submit` | Verifier | `candidate_sha`, `test_receipt_sha256`, `test_selectors`, `decision`, `findings` |
| `integrate` | Coordinator | `candidate_sha`, `expected_patch_sha256`, `test_selectors`, `verifier_receipt_sha256`, `message`, `allowed_paths` |
| `bundle-prepare` | Coordinator | `base_sha`, `head_sha`, `stage_report`, `final_review_request`, `handoff` |
| `review-request` | Coordinator | `bundle`, `base_sha`, `head_sha` |
| `closeout-prepare` | Coordinator | `accepted_head`, `final_receipt_sha256` |
| `closeout-review` | Coordinator | `accepted_head`, `final_receipt_sha256` |
| `closeout-commit` | Coordinator | `accepted_head`, `expected_patch_sha256`, `closeout_receipt_sha256`, `message` |
| `closeout-advance` | Coordinator | `closeout_head`, `closeout_commit_receipt_sha256` |
| `next-stage-prepare` | Coordinator | `next_stage`, `closeout_commit_receipt_sha256` |
| `next-stage-commit` | Coordinator | `next_stage`, `expected_patch_sha256`, `prepare_receipt_sha256`, `message` |
| `next-stage-bind` | Coordinator | `next_stage`, `next_assignment`, `next_stage_commit_receipt_sha256`, `contract` |
| `receipt-check` | Coordinator/Verifier | `receipt_kind`, `receipt_sha256`, `reviewed_sha` |

The `next-stage-bind.contract` has exactly these keys:

```json
{
  "slug": "bounded-lowercase-slug",
  "allowed_paths": ["named/path/**"],
  "forbidden_paths": [
    ".agents/**",
    "docs/refactor/two-server/CHANGE_LEDGER.md",
    "docs/refactor/two-server/execution-order.yaml",
    "docs/refactor/two-server/reviews/**",
    "scripts/two_site_agent_*.py"
  ],
  "locks": [],
  "deliverables": [],
  "tests": [],
  "failure_tests": [],
  "rollback_test": null,
  "scope_notes": "Human-readable bounded Stage scope and stop conditions."
}
```

The protected host derives branch, worktree path, registry ID, seven-day expiry,
timestamps, orchestrator, epoch, base and integration head. The contract cannot grant
external access or remove the mandatory protected paths. Before its first side effect,
`next-stage-bind` creates a root-owned transaction journal bound to the exact protected
request and plan. The planned `stage-bind` argv also carries the canonical SHA-256 of
that typed request; the fence rereads the bounded, duplicate-key-free request under its
canonical lock, rejects unknown/missing request keys or malformed receipt digests, and
independently enforces the nested contract's types, bounds, unique lists, safe paths and
mandatory protected patterns. It then proves the request, Assignment scope, derived branch/worktree/registry
identity, integration path/branch/owner, and prior Stage owner before its first write.
Recovery may only roll back while the old fence is still ACTIVE, or
roll forward an exact `RECONCILING/STAGE` or already-bound new fence. After a successful
bind, the prior COMPLETE Stage worktree is removed immediately only after its Assignment,
registry path, branch, HEAD, Git common directory and clean state all match.

Every rollback decision and side effect occurs while holding the same canonical fence
lock used by the fence program. Before removing or restoring anything, the host
revalidates the complete fence schema and exact Antigravity platform, Coordinator UUID,
epoch, active Assignment, integration branch/HEAD and actual clean integration Git state.
It reparses the old canonical Assignment and immutable-contract digest, derives every new
path/branch/registry identifier from the protected request, validates the journal's
before/after registry hashes and exact one-record semantic delta, and checks both the
integration owner and old Stage owner in the current byte-identical registry. Missing,
foreign, dirty, substituted, ambiguous or concurrently changed state is terminally
rejected without rollback effects. The next Assignment bytes and next branch ref are
validated before the first rollback removal. A `RECONCILING` retry releases the outer lock only so
the canonical fence process can acquire that same lock; recovery reacquires it and proves
the exact new ACTIVE owner before retirement. Recovery treats a precisely owned
`REMOVED` prior record as an idempotent post-retirement state only when its worktree is
both absent and no longer Git-registered, including interruptions after the COMPLETE
journal write but before the terminal receipt. A protected terminal receipt is never
mirrored or allowed to clear that journal until its complete identity and the exact
ACTIVE-new/REMOVED transition are revalidated under the lock. The prior registry record
must equal its journaled COMPLETE_CANDIDATE baseline except for the exact CLOSED/REMOVED
timestamps and states.

Privileged Python entrypoints never resolve imports through a candidate repository.
Fence and validator subprocesses run with the exact pinned Python binary in isolated
mode (`-I`); Git inside the fence is `/usr/bin/git`. The adapter validator loads its
reviewed session sibling by exact file path while excluding repository paths and rejects
a `yaml` module originating inside the project. The root installer is a standalone exact
reviewed Git blob, must itself run with `-I`, imports no repository package during
bootstrap, and re-reads its own bytes to prove equality with
`<accepted-sha>:scripts/install_two_site_agent_gateway.py` before installation. The
isolation and provenance checks are inside `install()` as well as its CLI, so importing
the module cannot bypass them. After an exact-SHA `APPROVE`, Codex—not the project owner
or Antigravity—must use `/usr/bin/git --no-replace-objects show` to materialize that exact
blob into a new root-owned mode-`0400` temporary file, invoke it with `/usr/bin/python3
-I`, and remove the temporary directory. It then compares the protected-source catalog
with the installed runtime manifest before activation. Running the installer from the
candidate working-tree path is forbidden even when that tree currently appears clean.

Review-bundle identities are canonical repository-relative POSIX paths in requests,
plans and receipts. Resolved absolute paths are used only inside the protected host for
bounded reads and hashes. Both final and closeout review supervisors revalidate the
relative identity and every bound digest immediately before and after the reviewer runs;
pre-run or in-review replacement fails closed and cannot produce authority.

Every hook invocation has one enforced wall-clock deadline covering host preparation,
Git observations, test snapshot creation, command/review execution, recovery and
descendant cleanup. Pre/Stop use 600 seconds and Post uses 2280 seconds; the generated
Antigravity hook timeouts add a fixed 120-second cleanup/output margin. Individual child
timeouts are capped by the remaining shared budget and reserve time to terminate their
complete process group. Deadline exhaustion never becomes success or approval.

## Causal sequence

Worker: `worker-check` → source edits → `test` → `worker-commit`.

Coordinator/Verifier: `stage-advance` → delegate Verifier → Verifier `test` and
`verifier-submit` → `integrate` → `bundle-prepare` → `review-request`. Corrections go
to the same Worker and repeat from tests/candidate integration on the cumulative Stage
range.

After exact-SHA `APPROVE`: `closeout-prepare` → `closeout-review` →
`closeout-commit` → `closeout-advance` → `next-stage-prepare` →
`next-stage-commit` → `next-stage-bind` → delegate the new Worker.

All three Codex review bridges pin `gpt-5.6-sol` with `xhigh` reasoning in a read-only
sandbox. Antigravity execution remains pinned separately to `gemini-3.8-flash-high`.

Never skip a receipt edge, manufacture an approval, use an arbitrary command, change
DNS/Writer/deploy/production, read secrets, or broaden an Assignment. Unknown state
is `BLOCKED`.
