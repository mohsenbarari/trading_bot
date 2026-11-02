# Infrastructure

- 2026-11-02 | Identified security scope CLOSED: Native9/Web11 healthy; durable refs,
  owned fences released. OS/glibc/reboots done. Standard/global/NVM Node24.22.1,
  npm/npx11.19.1; active Node20=0, parent preserved; see `security.md`.
  Oct25 Web: Docker29.9.2/containerd2.4.2/runc1.5.2/kernel38; PRIMARY restore/
  parity/cold-compare and ciphertext authentication/fsync/reread passed, backups intact.
- Web `/tmp` restored to original4058132480B, used0; tmpfs identity/fsid/1777/
  flags/owner retained. Temporary4GiB image-work override inactive; no remount,
  deletion, resize or billing.
- Native `/srv/trading-bot/production-data` holds staging-data bind. df/du mismatch
  is not deleted-open proof (0/6623FDs); relocation3.15GB contains protected Codex
  sessions. Cleanup freed~8.8GiB plus206729216B APT underlock and1753612288B restore
  copies. Preserve six originals,last8 tested App backups, active/rollback tools,
  images,exports,worktrees,IDE history and Market data. Refresh APT/capacity.
- Native dashboard TLS verified from Web: coin.362514.ir/30486, backend loopback30487;
  no Writer/DNS/auth-code change.
- Finland Primary65.109.214.203 remains unprovisioned:16vCPU/31.3GiBRAM,
  observed296220508160B free. SSH requires explicit `id_ed25519_finland_primary`
  (defaults omit it), pinned ED25519
  `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`.
  Only scoped ciphertext-backup staging authorized; no App/Writer/DNS/migration.
  Firewall, SSH hardening, updates, swap, monitoring and backups remain gates.
- P1-00b:135 live-endpoint exact handlers;330 seeds High, including origin SUCCESS/policy/
  notification/side-effect-retry, invitation-role and channel-trade. Mock/family
  shortcuts stay demoted; per-seed nodes required. DR-018 NOT_REINSPECTED.
  Sep04 observations covered no live Host/Redis; November security observations
  do not satisfy DR-015/P1-02: DPL-1 must measure deploy/restart/backup/restore,
  never estimate/rehearse them. Upload fence detects new test writes, not an
  existing ignored uploads directory, across integration/worktrees/temporary cwd.
- Provision/maintain through version-controlled idempotent Ansible/verified inventory;
  no early refactor production work or automatic human Writer/DNS transfer.
- Full Matrix: final Finland, existing Iran/Object Storage; no separate VM.
  Acceptance stacks/DB/Redis/Control Store/DNS/storage-IAM stay isolated; real
  Host faults require preproduction or approved maintenance, never simulation.
- DNS writes stay Finland: root-mounted least-privilege Arvan token, only registered
  product A record; Iran verifies. Panel fallback human/reconciled/verified/audited.
  API only explicitly human-triggered DNS/required storage; validate existing
  local credentials and move from quarantine to secure mount without exposure.
