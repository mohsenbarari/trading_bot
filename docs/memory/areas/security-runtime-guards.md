# Security runtime verification guards

- Docker Health is optional (null is unknown). An empty AppArmorProfile before
  first START is not an applied policy; verify it after actual START.
  Public Env deltas need literal-byte writers and real serialization tests.
- Readers/modes are typed: Root source0644, UID10001 extracted build sources,
  private controls0600. Preserve original owners/modes; new boundaries need
  genuine UID/GID tests.
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
- Keep small bounded responses in RAM until collectors close; log immutable
  known bytes afterward. Streaming files require original-writer full9 and
  byte/hash admission before selector/pipe closes, with a retained reader
  acquired before writer close and checked immediately afterward. Known bytes
  never justify inode or metadata replacement.
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
- FIFO writes naturally change mtime/ctime. Bind original dev/inode/mode/uid/gid/
  nlink/size and fd flags, plus exact stream length/hash/EOF; timestamps are
  observations. Keep every regular-file full9 guard unchanged.
- Host PID claims need host-visible processes. Retired dev/inode numbers can be
  reused; identify the actual mapped public payload before calling it an old library.
- Closed refusal mappings must cover new adapter codes; unknown operands stay
  private. Physical-finalizer errors outrank earlier errors; preserve both.
  Closure evidence needs actual driver/readers/collector closes and final names,
  bound to that invocation, never earlier success or published JSON alone.
  Budget repeated guards/hashing within the original deadline; timing alone is
  inference.
- Pilot every distinct frozen business/fixture API before dispatching all roles;
  one successful Capture2 pilot does not cover schema9/min_id or older replay APIs.
  Preserve historical assertions and business bytes when adapting fixtures.
- Constructor cleanup must handle the actual acquired prefix and preserve the
  original error. Do not call full-context guards before their fields exist;
  include a real constructor-prefix regression, beyond import/AST checks.
- Test exact Docker/systemd wire serialization and omitted-field behavior;
  valid hand-built dictionaries cannot verify the CLI template that produces them.
