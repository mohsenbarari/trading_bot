# Infrastructure

- 2026-09-04 | P1-00b has live-endpoint evidence for 135 exact handlers; 330 seeds remain High. Origin SUCCESS/policy/notification/side-effect-retry, invitation-role, and channel-trade remain High. `DR-018` is `NOT_REINSPECTED`.
- The portable upload fence must detect writes created by a test, not a pre-existing gitignored `uploads/` directory. It must work in the integration tree, worktrees, and temporary cwd.
- Intermediate handler counts are superseded by the 135/330 result. Corrections demoted mocked execute paths and family-seed shortcuts; per-seed nodes remain required. No Sep04 work re-observed live hosts or read live Redis.
- Bot-Finland, Web-Finland, and target-host facts remain `NOT_REOBSERVED` since 2026-09-01. `DR-015` blocks `P1-02` until `DPL-1` measures deploy/restart/backup/restore; do not estimate or rehearse them.
- Provisioning and maintenance use version-controlled, idempotent Ansible against verified inventory. This does not automate human Writer/DNS transfer or authorize early production work.
- The Architecture Full Matrix uses the final Finland host, existing Iran host, and existing Object Storage; no separate VM. Acceptance stacks, DB/Redis, Control Store, DNS, storage namespace, and IAM stay isolated. Host-wide faults require pre-production or an explicitly authorized maintenance window, never simulation.
- DNS mutation authority stays on Finland: a root-mounted least-privilege Arvan token may change only the registered Product A record; Iran verifies only. Provider-panel fallback is human, reconciled, verified, and audited.
- Arvan API access is approved only for explicitly human-triggered DNS and required storage operations. Validate and least-privilege the existing credentials, then move them from quarantine to a secure mount without exposure.
- Read-only SSH to unprovisioned Finland Primary `65.109.214.203` is established; ED25519 fingerprint `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`. It has 16 vCPU, 31.3 GiB RAM, and about 286 GiB free disk, but no app stack. Firewall, SSH hardening, updates, swap, monitoring, and backup policy remain provisioning gates; access/capacity do not authorize deployment.
