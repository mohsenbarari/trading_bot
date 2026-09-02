# Infrastructure

- 2026-09-01 | Arvan API is approved only for explicitly human-triggered DNS and
  required storage operations. Validate and least-privilege the existing local
  credentials, then move them from quarantine to a secure mount without exposure.
- 2026-09-01 | Read-only SSH access to the unprovisioned Finland Primary
  `65.109.214.203` is established and its ED25519 fingerprint is verified as
  `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`. It has 16 vCPU, 31.3 GiB
  RAM and about 286 GiB free disk, but no container/application stack; firewall,
  SSH hardening, update, swap, monitoring and backup policy remain provisioning
  gates. Access and raw capacity do not authorize deploy or production use.
