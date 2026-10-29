# Security runtime verification guards

For source/collector APIs and FD/pipe custody also load `security-io-custody.md`.

- Docker Health is optional (null is unknown). An empty AppArmorProfile before
  first START is not an applied policy; verify it after actual START.
  Public Env deltas need literal-byte writers and real serialization tests.
- Docker cp cannot target tmpfs. Stage public CA bytes under /build and verify
  before HTTPS; never read private PKI for this purpose.
- Ubuntu probes use -I -S -B to exclude sitecustomize. Keep loader guards and
  separate output/payload bounds; never inherit small-receipt RLIMIT_AS into Go.
- Removed cgroup directories may retain nlink2. Prove closure with held
  directory/events, named absence and relative ENOENT/retained ENODEV; polling
  alone is insufficient. Never relax regular-file unlink checks.
- A never-started mount-free export excludes no kernel/proof paths: inspect
  every regular ELF, including /proc, /sys, /dev and /proof. TAR metadata is not
  full9; hash the archive before parsing and after the borrowed FD closes.
- Docker can return CAP_-prefixed capability names. Normalize that known prefix
  only after validating typed lists, exact cardinality and the allowed set.
  Foreign-owned fixture chmod needs genuine FOWNER or the actual owner;
  never mask ownership or hide EPERM.
- Docker creates WORKDIR before a later cp. Copy public packet contents with
  `packet/.`, then verify the expected container file before START.
- Under umask0077, mkdir0755 creates0700. Set new public fixture dirs0755;
  keep private work0700. Prove actual non-root access and private-sibling denial.
- Receiver work/nonce ancestors stay Root0700; engine changes only restore
  ownership. Genuine UID10001 needs the exact restore inode mounted at /clone.
  Never chmod ancestors or reuse the old immutable, .sqlite-only engine helper.
- Host PID claims need host-visible processes. Retired dev/inode numbers can be
  reused; identify the actual mapped public payload before calling it an old library.
- Pilot every distinct frozen business/fixture API before dispatching all roles;
  one successful Capture2 pilot does not cover schema9/min_id or older replay APIs.
  Preserve historical assertions and business bytes when adapting fixtures.
- Test exact Docker/systemd wire serialization and omitted-field behavior;
  valid hand-built dictionaries cannot verify the CLI template that produces them.
- systemd can remove a stopped timer from its oneshot's After and TriggeredBy.
  Admit only that measured pair through the same context's completed-stop receipt;
  preserve the original policy and every unrelated dependency.
- Systemd Before/After are ordering edges, not activation or proof a target
  cannot write. Record every edge; explicit `order-only` applies only to those
  properties. Keep Requires/Triggers, metadata CAS, writer/HOST census and stop
  ownership strict. Never add unrelated host services to business stop actions
  merely to close an ordering graph.
