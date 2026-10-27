# Security runtime verification guards

- Docker Health is optional (null is unknown). An empty AppArmorProfile before
  first START is not an applied policy; verify it after actual START.
  Public Env deltas need literal-byte writers and real serialization tests.
- Artifact modes are typed: public source0644, private controls0600. Never
  change originals or substitute an identity mask for genuine UID/GID tests.
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
- Small bounded responses should remain in bounded RAM through collector
  closure; create logs afterward from immutable known bytes. No live output file
  then exists whose metadata could be adopted after close.
- Streaming logs need original-writer full9 and known byte/hash admission before
  selector/pipe closes, plus a retained reader checked immediately after writer
  close. Known bytes never justify inode or metadata replacement.
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
- Admit new adapter refusal codes in the outer closed mapping before costly CI;
  unknown exception operands remain private. Budget repeated source guards and
  full hashes within the global deadline; timing alone is an inferred cause.
