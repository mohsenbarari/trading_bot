# Work Assignment — `<ASSIGNMENT_ID>`

```yaml
assignment_id: <ASSIGNMENT_ID>
stage_id: <STAGE_ID>
role: WORKER
status: READY
orchestrator: <CURSOR|ANTIGRAVITY>
orchestration_epoch: <POSITIVE_INTEGER>
supersedes: null
base_sha: <FULL_SHA>
branch: refactor/stage/<STAGE_ID>-<slug>
worktree_path: <ABSOLUTE_REGISTERED_WORKTREE_PATH>
worktree_registry_id: <ID>
allowed_paths: []
forbidden_paths: []
locks: []
parallel_peer: null
deliverables: []
tests: []
failure_tests: []
rollback_test: null
external_access: NONE
expires_at: <UTC>
issued_by: EXECUTION_COORDINATOR
issued_at: <UTC>
integration_branch: refactor/two-site-architecture
integration_head_at_issue: <FULL_SHA>
```

## Dependency receipts

| Stage | Complete commit | Final-review receipt |
| --- | --- | --- |

## Scope notes

- Behavior invariants:
- Expected integration point:
- Stop conditions:
- Evidence retention class:

هر تغییر در شناسه، Stage، base، worktree، scope، lock، peer یا external access این Assignment را
باطل می‌کند و نیازمند Assignment جدید است.
