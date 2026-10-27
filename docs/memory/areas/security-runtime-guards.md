# Security runtime verification guards

- Optional Docker Health may be absent; null never means healthy. Public Env
  overrides use exact fixed-byte writers and real serialization tests, rather
  than passing raw values through the generic public-report validator.
- Artifact modes are typed: public source0644, private controls0600. Never
  change originals or substitute an identity mask for genuine UID/GID tests.
- Docker cp cannot target tmpfs. Stage the public CA under /build, verify its
  exact bytes, then use HTTPS; never read private PKI for this purpose.
- Ubuntu compatibility probes use -I -S -B to exclude stock sitecustomize.
  Keep loader guards. Output and payload bounds are distinct; no Go address
  space limit inherited from a small receipt helper.
- Removed cgroup directories may retain nlink2. Prove closure with held
  directory/events, named absence and relative ENOENT/retained ENODEV; polling
  alone is insufficient. Never relax regular-file unlink checks.
- A never-started, mount-free Docker export has no kernel/proof path exclusions:
  inspect every regular ELF, including /proc, /sys, /dev and /proof. TAR header
  metadata is not filesystem full9. Check the known archive digest before parse
  and after the borrowed FD closes.
- Retain stderr's original writer identity before selector/pipe closes; admit its
  known byte/hash binding while the writer remains open, then check the retained
  reader immediately after writer close. Known payload alone cannot admit a
  replacement inode or metadata transition.
- Docker can return CAP_-prefixed capability names. Normalize that known prefix
  only after validating typed lists, exact cardinality and the allowed set.
- Docker creates WORKDIR before a later cp. Copy public packet contents with
  `packet/.`, then verify the expected container file before START.
- Under workflow umask0077, mkdir0755 creates0700. Explicitly set newly-created
  public fixture directories0755 before file admission; keep private work0700.
  Prove real non-root readability and denial of the private sibling.
- Admit new adapter refusal codes in the outer closed mapping before costly CI;
  unknown exception operands remain private. Budget repeated source guards and
  full hashes within the global deadline; timing alone is an inferred cause.
