# Security I/O and custody

- QA: preflight schemas/APIs/returns/counts/modes/fixtures/bounds/DAG; retain assertions/limits/siblings. Survey all roles before exceptions. Bound B.run logs beforehand. .raw: bounded pread/hash. Scoped patches preserve context; GNU patch before CI. Freeze modes before admission; never retouch inputs/receipts. Profilers compare precomputed filenames/code; no pathlib/I/O per call.
- Typed modes: Root sources0644, receipts0600/0644, UID10001 build sources,
  private controls0600. Preserve original owners/modes; test real UID/GID.
- Keep small bounded responses in RAM until collectors close; log immutable
  known bytes afterward. Streaming files require original-writer full9 and
  byte/hash admission before selector/pipe closes, with a retained reader
  acquired before writer close and checked immediately afterward. Known bytes
  never justify inode or metadata replacement.
- Pipe/FIFO writes naturally change mtime/ctime. Bind original dev/inode/mode/uid/gid/
  nlink/size and fd flags, plus exact stream length/hash/EOF; timestamps are
  observations. Keep every regular-file full9 guard unchanged.
- Closed refusal mappings must cover new adapter codes; unknown operands stay
  private. Physical-finalizer errors outrank earlier errors; preserve both.
  Closure evidence needs actual driver/readers/collector closes and final names,
  bound to that invocation, never earlier success or published JSON alone.
  Budget repeated guards/hashing within the original deadline; timing alone is
  inference.
- Constructor cleanup must handle the actual acquired prefix and preserve the
  original error. Do not call full-context guards before their fields exist;
  include a real constructor-prefix regression, beyond import/AST checks.
- Source sealing requires an actual clean Main checkout. Use a registered
  temporary checkout; preserve the sealer's branch checks. Use full9 without
  atime and the actual host interpreter rather than a container Python path.
- Run report-writing verifiers from fresh packet copies. Preserve author and
  Root receipts separately; do not overwrite frozen QA metadata.
- S.Reader.raw_binding is None without a pin. Supply the exact pin or guarded hash. Test actual admission before payload/spawn, beyond stubs.
- Operations need bounded durable sanitized status before entry. Lost output proves neither closure nor retry eligibility; retain UNKNOWN.
- New custody code needs real acquisition-prefix failure, FD/flag drift, same-inode/exact-flags/different-offset replacement, restoration, no-op close and non-EBADF final-stat cases. Unadmitted/uncertain ownership stays HELD; restoration never heals it. Fixture disposal is separate evidence.
- Capture all original receivers and actual close-target aliases, including fd/original_fd/pidfd; deduplicate metadata reads, never alias bindings. Incomplete cohorts cannot prove closure. Retain actual body errors before postguards, not only JSON summaries.
- Pipe tests must write after admission across a real clock tick; an immediate same-jiffy test can miss timestamp changes. Preserve regular-file full9 and both FD flag classes.
