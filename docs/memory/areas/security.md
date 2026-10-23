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
- Kernel/daemon work needs separate quiescence, restore and smoke checks; preserve
  DB, Bot/Executor, Writer and sync. CLI updates never imply reboot permission.
- Private control storage is an ACTIVE attached-volume bind despite its historical
  source name: never prune it. Identity recovery requires authentic evidence and
  crash safety, never persistent-marker deletion or weaker normal guards.
- Live inventory differs from the old quiesced handoff: the group unit projects
  facts; private-gold/public still collect Telegram. Preserve ingestion until
  ownership is reconciled. Identity-only recovery must refuse; stopping units
  solely to pass is not reconciliation.
