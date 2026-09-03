# Infrastructure

- 2026-09-03 | Initial host provisioning and later infrastructure maintenance will be executed by Codex through version-controlled, idempotent Ansible against verified inventory; no operator shell execution is required. This does not automate Writer/DNS transfer or start server work before the refactor plan enters its explicitly authorized execution phase.

- 2026-08-31 | The sole planned Finland Primary host is `65.109.214.203`.
  TCP/SSH answered on port 22, but current keys could not authenticate as `root`
  or `ubuntu`; no remote command ran. Treat the host as unprovisioned and
  non-authoritative until its fingerprint is verified and access is established.
