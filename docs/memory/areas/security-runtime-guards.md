# Security runtime verification guards

Also load `security-io-custody.md` and `security-operation-recovery.md`.

- Root COPY: separate init, tar.umask0022, disjoint IDs, original multiplicity.
  No waivers/evidence deletion/hostdata/UID-mode masks. Restore fixture globals/modes.

- Health null is unknown; pre-START empty AppArmorProfile proves no policy.
  Verify after START. Public Env deltas need literal writers and real serialization.
- Web clone OOM1GiB;1.5GiB passed after reserve measurement, without changing
  production caps. Require scoped runtime/kernel OOM evidence; functional pass
  or incomplete Docker history proves none. Client timeout must exceed server
  grace within the original deadline; no blind retry.
- Docker cp cannot target tmpfs. Verify public CA under /build before HTTPS;
  never read private PKI.
- Ubuntu probes use -I -S -B; retain loader/output/payload guards. Never pass
  small-receipt RLIMIT_AS to Go.
- Removed cgroup directories may retain nlink2. Prove closure with held
  directory/events, named absence and relative ENOENT/retained ENODEV; polling
  alone is insufficient. Never relax regular-file unlink checks.
- A never-started mount-free export excludes no kernel/proof paths: inspect
  every regular ELF, including /proc, /sys, /dev and /proof. TAR metadata is not
  full9; hash the archive before parsing and after the borrowed FD closes.
- Normalize CAP_ only on typed capabilities with exact allowed set/cardinality.
  Foreign fixture chmod needs FOWNER/owner; preserve ownership and EPERM.
- Docker creates WORKDIR before cp: use `packet/.`; verify the expected file
  before START.
- Under umask0077, mkdir0755 yields0700: explicitly set public0755/private0700.
  Prove non-root access and private-sibling denial.
- Receiver work/nonce ancestors stay Root0700; engine changes only restore
  ownership. Genuine UID10001 needs the exact restore inode mounted at /clone.
  Never chmod ancestors or reuse the old immutable, .sqlite-only engine helper.
- Host PID claims need host-visible processes. Retired dev/inode numbers can be
  reused; identify the actual mapped public payload before calling it an old library.
  Proc-root uses its original stable5/FD/flags/OFD/name contract: its link count
  changes with process population. Regular source files retain full9.
- Pilot frozen business/fixture APIs; Capture2 cannot cover
  schema9/min_id or older replay. Preserve historical assertions/business bytes.
- Stopped timers may disappear only from their measured oneshot After/TriggeredBy
  pair via the context's stop receipt; preserve all other edges/policy.
  Before/After prove only order; preserve Requires/Triggers, CAS, HOST/writer
  census and owned stops only.
- Lifecycle fixtures require independent CREATED/RUNNING operands, admitted
  canonical HostConfig and full sorted unique mounts. Retain
  predicate/phase/operand before wrappers; vendor possibilities prove no historical cause.
- Scanner: idle nobody/nogroup65534, zero capabilities, NNP1, jail.
  systemd255 rejects unknown UIDs (217); avoid Root launchers/accounts.
