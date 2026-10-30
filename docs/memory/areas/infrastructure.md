# Infrastructure

- 2026-10-30 | Web `/tmp` tmpfs limit temporarily4GiB, previously4058132480B.
  In-place remount preserved identity/fsid/1777/flags/existing entries; no disk
  resize, billing or persistent config. Restore old limit after scoped image
  operations if usage fits. Resource source/guards unchanged.

- 2026-10-30 | Cleanup freed~8.8GiB from expired tools/backups/caches and one
 completed builder; another206729216B from52 APT indexes under APT's own lock.
 Preserve active/rollback tools,worktrees/IDE history,build exports,last8 tested
 backups and Market data. APT needs fresh indexes before installation;
 fresh capacity remains mandatory for every operation.

- 2026-10-25 | Security maintenance: Native engine/kernel/Brotli completed,
 15 containers; bind Brotli evidence to current boot/PID/unit. Web OP08 has
 Docker29.9.2/containerd2.4.2/runc1.5.2/kernel38,27 containers/16 running/six
 stores, QA10 parked/F79 preserved. PRIMARY restore/parity/cleanup/cold compare
 passed. Final ciphertext authenticated/fsynced/reread; kernel38/new boot/four
 backup metadata entries unchanged. No Final App provisioning/migration authority.

- 2026-10-25 | Native dashboard TLS verified independently from Web on existing
  coin.362514.ir/30486; backend is loopback30487. No Writer/DNS/auth-code change.

- 2026-10-24 | Unprovisioned Finland65.109.214.203:16vCPU/31.3GiBRAM; root free303.6GB.
  SSH passed using explicit `id_ed25519_finland_primary` (defaults omit it), pinned
  ED25519 `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`. Only scoped
  ciphertext-backup staging is authorized; no App/Writer/DNS/migration. Firewall,
  SSH hardening, updates, swap, monitoring and backups remain provisioning gates.

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
