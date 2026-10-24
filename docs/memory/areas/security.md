# Security remediation

- 2026-10-23 | Security is separate from topology acceptance; preserve Web/Bot
  behavior. Source/test completion is not production remediation. Evidence and
  activation blockers: `docs/security/2026-10-23-remediation.md` and linked reports.
- Upgrade FastAPI/Starlette/Pydantic and aiogram/aiohttp as compatible families;
  require parser/upload tests. Bound image bytes/pixels before EXIF/decode;
  never persist the original after a safety failure. All frontend responses,
  including SPA fallback, must remain inside immutable trusted dist.
- Patch image OS and certifi separately. The CA build gate is not a full CVE
  scan; never disable TLS verification. Verify exact vendor Node releases, not
  bundled Brotli strings. Enforce one Node policy before cache lookup; bootstrap
  stays pinned/SHA-verified, bounded, checkout-local and scoped-PATH, not global.
- Publishing existing main history plus reviewed security commits is authorized,
  not unrelated Market activation. Preserve LEGACY, capture owners and mounts.
- Publisher/chat/message fixtures must be complete; retain missing-owner negative
  assertions. Require PostgreSQL and real multiworker Redis. Each CI subprocess
  needs public unit config before imports and explicit scratch targets. Browser
  CI prepares every bind under disposable paths; keep snapshots empty/read-only
  and production create-host-path guards; check the contract before fan-out.
- Host maintenance needs quiescence, restore and smoke proofs; never infer patch
  activation from CLI or installed kernel. Bot Docker/containerd now run29.9.2/2.4.2
  with same15containerIDs/images and both projects restored. Actual kernel147
  reboot completed2026-10-23T21:19:23Z; fresh2026-10-24T03:14:22Z checks passed
  all15/DB/API/Golden/mounts/unchangedM/J. Web vendor fixes and application
  activation remain open. See dated security report.
- Private control storage is an ACTIVE attached-volume bind despite its historical
  source name: never prune it. Identity recovery requires authentic evidence and
  crash safety, never persistent-marker deletion or weaker normal guards.
- Live inventory differs from the old quiesced handoff: the group unit projects
  facts; private-gold/public still collect Telegram. Preserve ingestion until
  ownership is reconciled. Identity-only recovery must refuse; stopping units
  solely to pass is not reconciliation.
- 2026-10-23 | Root-run fixture context drift accidentally reached systemctl;
  exact baseline collectors/timers were restored and real runtime/DB/API plus
  fresh oneshots passed. Synthetic maintenance suites must unconditionally deny
  unmocked host commands; keep named contexts, never shift positional mock tuples.
- Resume refactor only after identified security findings are remediated and
  activated/verified. Then monitor30 genuinely accepted completed units, not
  health polls; failures must be repaired, never waived or counted as success.
- Inactive oneshots can disappear between systemctl and D-Bus GetUnit. Hold
  non-starting RefUnit references on one persistent private bus while checking;
  never remove mount/ownership checks to recover. Exact owned continuation
  preserved the original guard/HEAD/manifest and requested one actual reboot.
- A final SSH credential revocation may succeed before Ansible's subsequent
  remote housekeeping reconnect fails. Verify authorized-entry/key/pin absence
  separately; use a final raw SSH child on the controller, not another remote
  module that needs the credential after removing it. Retain failure evidence.
- User renewed maintenance2026-10-24: immediate maximum90min downtime; current
  bounded outer limit04:41Z(08:11Tehran). Do not extend it silently. Admission
  needs a restore reserve; prep/source tests are not actual engine acceptance.
