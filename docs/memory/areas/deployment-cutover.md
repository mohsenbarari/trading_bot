# Deployment Cutover

- 2026-09-03 | Product DNS TTL remains 30 seconds during normal operation as well as cutover/quarantine. Writer transfer still requires authoritative Arvan DNS verification plus independent probes and fencing; TTL is not writer authority, and failed or unverified DNS change blocks destination activation.

- 2026-09-03 | `DPL-10` approved: legacy remains the sole mutating authority while the new release system runs read-only shadow/rehearsal; dual authority is forbidden. Codex provisions the target Finland host and Iran control plane with Ansible, then validates a behavior-equivalent, non-owning candidate before the one-time cutover. The approved window is at most 90 minutes with at most four minutes of user interruption; every decision is resolved before the first mutation.

- 2026-09-03 | Before the first write on the new topology, fallback to the old runtime is allowed. After that point, rollback stays within the new architecture, and DNS must never point back without explicit data replay or restore. Legacy paths become read-only after cutover and are deleted—with no `.bak`, permanent aliases, or manual archives—after two normal deploys, one at-most-ten-minute hotfix drill, one rollback, one partition/reconnect, one restore, zero High/Critical gaps, and seven healthy days. Emergency `releasectl` uses the same controller and state machine.

- 2026-09-03 | Refactor Section 4 is approved and closed at plan-review level through `DPL-1` to `DPL-10`. Implementation remains deferred until the complete five-section plan is reviewed and explicitly authorized.
