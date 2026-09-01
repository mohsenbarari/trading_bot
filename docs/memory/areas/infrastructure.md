# Infrastructure

- 2026-09-01 | No operational provisioning, deploy, migration, runtime cleanup,
  DNS change or cutover occurs before the complete plan is reviewed and approved;
  production Stages still require their own explicit authorization afterward.
- 2026-09-01 | Finland cutover accepts at most four minutes of user access/write
  interruption. Initial acceptance uses 24h staging soak, steady CPU <=60% and
  RAM/disk/DB-pool <=70%; after cutover observe actively for 2h, fence old sources
  for at least 7d and retain the approved backup 30d, without automatic deletion.
- 2026-09-01 | Finland consolidation is behavior-preserving. An owner-reviewed,
  read-only current architecture dossier and feature-parity contract must cover
  every Web/Bot surface, tier/policy, state and side effect before implementation.
- 2026-09-01 | Web Writer handover is fully human-controlled: no renewable lease,
  timeout-driven role change, or automatic promotion/demotion. The operator can
  reach both sites, fences/drains the source, transfers a signed receipt, verifies
  Arvan DNS, then explicitly activates the destination; Bot remains independent.
- 2026-09-01 | Arvan API is approved only for explicitly human-triggered DNS and
  required storage operations. Validate and least-privilege the existing local
  credentials, then move them from quarantine to a secure mount without exposure.
- 2026-09-01 | Read-only SSH access to the unprovisioned Finland Primary
  `65.109.214.203` is established and its ED25519 fingerprint is verified as
  `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`. It has 16 vCPU, 31.3 GiB
  RAM and about 286 GiB free disk, but no container/application stack; firewall,
  SSH hardening, update, swap, monitoring and backup policy remain provisioning
  gates. Access and raw capacity do not authorize deploy or production use.
