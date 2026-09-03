# Work Assignment — `<ASSIGNMENT_ID>`

```yaml
stage_id: <STAGE_ID>
role: WORKER
status: READY
base_sha: <FULL_SHA>
branch: refactor/stage/<STAGE_ID>-<slug>
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
issued_by: CURSOR_COORDINATOR
```

## Dependency receipts

| Stage | Complete commit | Final-review receipt |
| --- | --- | --- |

## Scope notes

- Behavior invariants:
- Expected integration point:
- Stop conditions:
- Evidence retention class:

هر تغییر در Stage، base، scope، lock، peer یا external access این Assignment را
باطل می‌کند و نیازمند Assignment جدید است.
