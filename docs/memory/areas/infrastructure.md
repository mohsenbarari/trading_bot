# Infrastructure

- 2026-09-02 | Owner approved `P1-02`: typed site/service/capability config stays
  separate from immutable business origin and human-only Writer state. Legacy
  dual-read requires semantic equality; mismatch fails closed and removal waits
  for `P1-08`. Approval authorizes no runtime change.
- 2026-09-02 | Owner approved the `P1-00` human baseline: twelve behavior
  families, preserved Web/Bot distinctions and provenance, mandatory evidence
  for six gaps, and the runtime-ownership seed. Exact target binding stays in
  `P1-03`; approval authorizes no implementation or operation.
- 2026-09-01 | Offer keeps immutable Web/Bot/Internal origin separate from
  `home_site`. Trade snapshots Offer origin, Request origin, execution surface,
  policy version and policy-sensitive actor/role/tier context so topology or
  relation changes cannot rewrite historical decision provenance.
- 2026-09-01 | No operational provisioning, deploy, migration, runtime cleanup,
  DNS change or cutover occurs before the complete plan is reviewed and approved;
  production Stages still require their own explicit authorization afterward.
- 2026-09-01 | The approved one-time two-Finland consolidation cutover reserves
  90m, completes preflight before freeze, allows at most 4m user access/write
  interruption, and aborts if target is not ready. It is not a deploy SLO. Initial
  acceptance uses 24h soak, steady CPU <=60% and RAM/disk/DB-pool <=70%; observe
  2h, fence old sources at least 7d, and retain the approved backup 30d.
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
