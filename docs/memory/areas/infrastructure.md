# Infrastructure

- 2026-10-30 | HOST/CAS-checked cleanup removed16 obsolete generated venvs and
  three inactive IDE installations; kept worktrees/history/current/rollback tools.
  Root free~4.2GiB, SDB~9.1GiB; these measurements grant no restore admission.

- 2026-10-25 | Security maintenance: Native engine/kernel/Brotli completed,
 15 containers; bind Brotli evidence to current boot/PID/unit. Web OP08 has
 Docker29.9.2/containerd2.4.2/runc1.5.2/kernel38,27 containers/16 running/six
 stores, QA10 parked/F79 preserved. PRIMARY restore/parity/cleanup/cold compare
 passed. Final ciphertext authenticated/fsynced/reread; kernel38/new boot/four
 backup metadata entries unchanged. No Final App provisioning/migration authority.

- 2026-10-25 | Native dashboard TLS verified independently from Web on existing
  coin.362514.ir/30486; backend is loopback30487. No Writer/DNS/auth-code change.

- 2026-10-24 | Final Finland SSH uses existing `id_ed25519_finland_primary` explicitly;
  default identities omit it. Strict pinned-host access passed; root free303.6GB.
  Security maintenance may stage only scoped ciphertext backups there; no App,
  Writer/DNS or production-stack migration is implied.

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
