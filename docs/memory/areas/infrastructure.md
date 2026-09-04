# Infrastructure

- 2026-09-04 | P1-00b executed eight more exact handlers (six bot commodity
  mutations plus trading-settings update/reset) and attached per-seed test
  nodes. Origin SUCCESS/policy/notification/side-effect-retry stay High.
  Thirty-two exact handlers are evidenced; 433 seeds stay High. Live hosts
  were not re-observed. `DR-018` stays `NOT_REINSPECTED`.

- 2026-09-04 | P1-00b executed ten more exact handlers (custom max-block,
  six commodity API mutations, three market-override mutations) and attached
  per-seed test nodes. Origin SUCCESS/policy/notification/side-effect-retry
  stay High. Twenty-four exact handlers are evidenced; 441 seeds stay High.
  Live hosts were not re-observed. `DR-018` stays `NOT_REINSPECTED`.

- 2026-09-04 | P1-00b executed six more exact handlers (unlimit, max-block,
  block-actions, limit-confirm, message update, message delete) and attached
  per-seed test nodes. Origin SUCCESS/policy/notification/side-effect-retry
  stay High. Fourteen exact handlers are evidenced; 451 seeds stay High.
  Live hosts were not re-observed. `DR-018` stays `NOT_REINSPECTED`.

- 2026-09-04 | P1-00b demoted channel-trade seeds that only mocked execute.
  Origin SUCCESS/policy/notification/side-effect-retry stay High. Eight exact
  handlers are evidenced; 457 seeds stay High. Live hosts were not
  re-observed. `DR-018` stays `NOT_REINSPECTED`.

- 2026-09-04 | Verifier `CORRECTIONS_REQUIRED` on `18926961`. Origin SUCCESS
  and sibling family-seed shortcuts were demoted. Live hosts were not
  re-observed. `DR-018` stays `NOT_REINSPECTED`.

- 2026-09-04 | Codex `CHANGES_REQUIRED` on integration `3a7344b3`. The P1-00b
  correction did not re-observe live hosts and did not read live Redis.
  `DR-018` stays `NOT_REINSPECTED`. Host facts stay `NOT_REOBSERVED`.

- 2026-09-04 | `P1-00b` freeze `WA-P1-00B-FREEZE-20260904-001` did not re-observe
  live hosts. Previous 2026-09-01 Bot-Finland / Web-Finland / target facts stay
  `NOT_REOBSERVED`. `DR-018` stays `NOT_REINSPECTED`. `DR-015` still blocks
  `P1-02` until `DPL-1`; do not estimate or rehearse.

- 2026-09-03 | After Codex `CHANGES_REQUIRED` on `5ec75ec5`, leftover current-behavior
  freeze is a new non-implementation Stage `P1-00b`, not `P1-04`/`P1-06` and not
  live-host work. Host facts stay `NOT_REOBSERVED`. `DR-015` still blocks `P1-02`
  until `DPL-1`; do not estimate or rehearse.

- 2026-09-03 | `P1-00` refresh `WA-P1-00-REFRESH-20260903-001` did not re-observe
  live hosts. Previous 2026-09-01 Bot-Finland / Web-Finland / target facts stay
  `NOT_REOBSERVED` until an Assignment with external access authorizes a new
  read-only inspection. Codex classified missing deploy/restart/backup/restore
  durations as `DR-015` `DEFERRED_BLOCKER` for `DPL-1`; do not estimate or
  rehearse, and do not start `P1-02` before `DPL-1`.

- 2026-09-03 | Initial provisioning and later infrastructure maintenance use version-controlled, idempotent Ansible against verified inventory; no operator shell execution is required. This neither automates Writer/DNS transfer nor authorizes server work before the refactor plan enters its explicitly approved execution phase.

- 2026-09-02 | Architecture Full Matrix uses the final Finland host, existing Iran
  host and existing Object Storage; no separate VM. Acceptance stacks, DB/Redis,
  Control Store, DNS and storage namespace/IAM remain isolated. Host-wide faults run
  pre-production or in an explicitly authorized maintenance window, never by simulation.
- 2026-09-02 | DNS mutation authority stays on Finland: one root-mounted least-privilege
  Arvan token can change only the registered product A record; Iran verifies only.
  Provider-panel fallback remains human, reconciled and fully verified/audited.
- 2026-09-01 | Arvan API is approved only for explicitly human-triggered DNS and
  required storage operations. Validate and least-privilege the existing local
  credentials, then move them from quarantine to a secure mount without exposure.
- 2026-09-01 | Read-only SSH access to the unprovisioned Finland Primary
  `65.109.214.203` is established and its ED25519 fingerprint is verified as
  `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`. It has 16 vCPU, 31.3 GiB
  RAM and about 286 GiB free disk, but no container/application stack; firewall,
  SSH hardening, update, swap, monitoring and backup policy remain provisioning
  gates. Access and raw capacity do not authorize deploy or production use.
