# Security remediation

- Security is not topology acceptance. Preserve behavior; source/tests are not
  activation proof. Evidence: `docs/security/2026-10-23-remediation.md` and links.
- Upgrade FastAPI/Starlette/Pydantic and aiogram/aiohttp as compatible families;
  require parser/upload tests. Bound image bytes/pixels before EXIF/decode;
  never persist the original after a safety failure. All frontend responses,
  including SPA fallback, must remain inside immutable trusted dist.
- Patch OS/certifi separately; CA checks are not CVE scans. Keep TLS verification.
  Verify vendor Node fixes, not Brotli strings; enforce policy before cache lookup.
  Bootstrap stays pinned, bounded, checkout-local with scoped PATH, never global.
- Publishing existing main history plus reviewed security commits is authorized,
  not unrelated Market activation. Preserve LEGACY, capture owners and mounts.
- Keep complete publisher/chat fixtures and owner negatives; require PostgreSQL
  and real multiworker Redis. CI subprocesses need public config before imports,
  explicit scratch targets and disposable binds. Snapshots stay empty/read-only;
  preserve production path-creation guards and check contracts before fan-out.
- Native Docker/containerd29.9.2/2.4.2, kernel147 verified:
  15IDs/DB/API/Golden/mounts/unchangedM/J. Web/App remain open;
  see `docs/security/2026-10-24-native-postboot.md`.
- Private control storage is an active attached bind: never prune it. Recovery
  needs authentic crash-safe evidence, never marker deletion or weaker guards.
- Old handoff is stale: group projects facts; private-gold/public still collect.
  Reconcile ownership; never stop ingestion just to match an old receipt.
- A root fixture accidentally reached systemctl; exact collectors/timers and
  runtime/DB/API/oneshots were restored. Synthetic suites must deny all unmocked
  host commands; use named contexts, never shifted positional mocks.
- Resume refactor only after identified security findings are remediated and
  activated/verified. Then monitor30 genuinely accepted completed units, not
  health polls; failures must be repaired, never waived or counted as success.
- Inactive oneshots can disappear before GetUnit: retain non-starting RefUnit on
  one bus. Preserve guards/mounts/ownership/HEAD/manifest during owned recovery.
- Self-revoking SSH can strand Ansible housekeeping. Final revocation uses
  controller-local SSH; separately verify key/pin/authorized-entry absence and
  retain failed-transport evidence.
- 24Oct clarification: maximum90min from ACTUAL first downtime, not04:41Z.
  Fresh04 window is conservatively bounded05:20Z; keep old02/03 expiries intact.
  Require restore reserve; preparation is not actual engine acceptance.
- Actual combined backup roles are Native=foreign/trading_bot, Web=iran/current;
  bind real host/project/artifacts, never infer geography from role names.
  Process/cgroup death does not cancel accepted Docker/PID1 requests; ambiguous
  interrupted stop phases must not automatically restart writers.
