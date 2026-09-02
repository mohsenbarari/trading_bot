# Two-Server Refactor

- 2026-09-02 | Maximal `P2-10` runs on final FI/IR hosts and isolated existing storage:
  every requirement/mutation/fault needs executed evidence, never sampling. It covers
  465 baseline plus P2/P3, two 24h soaks, 14d+30% load, ten handovers, three DNS cycles,
  reboot/restore/provider smoke and zero skipped/orphan/unknown diff. This is an
  architecture/high-risk gate, not a blanket hotfix gate.
- 2026-09-02 | `P2-09`: Product OTP/session are local and generation-bound; switches
  force Product, not Console, re-login. Stable-ID notification/read and final Messenger
  state/media sync monotonically; Push/cache/realtime/draft/incomplete upload stay local.
- 2026-09-02 | `P2-06..08`: independent local-password/TOTP Consoles use 24h sessions
  and narrow actuators. One DNS-only A/TTL≤30s has Finland-only human Arvan control.
  Fixed site/human Web role, Finland Telegram, 30/90s peer observation, durable control,
  signed receipts and vector barriers prevent split-brain without stopping Bot.
- 2026-09-02 | `P1-00..06`: twelve behavior/provenance families and six evidence gaps
  define parity. Finland has two Web replicas, singleton jobs, split Bot and separate
  app/Market DBs; its shared app DB removes internal sync. Deterministic merge and
  465/465 differential, browser/load/24h/restore proof block unknown or financial drift.
- 2026-09-02 | `P1-07..08`: initial cutover is 90m/4m/TTL30s with human checkpoints,
  exclusive Bot handoff and old-edge proxy. Target DB stays canonical after first write;
  closure needs `P2-11`, 7d quarantine, staggered retirement and replacement backups.
- 2026-09-02 | `P2-00..05`: typed ownership and versioned streams use atomic ACK,
  blocking gaps/rejections and immutable repair. Signed encrypted storage has split
  credentials and 14d+30% spool. Aligned hashes plus distinct `FULL_SYNC/MARKET_READY`
  gate signed forward-only handover. Home alone mutates; Iran offers rehome atomically;
  restrictive field conflicts require humans, never LWW.
- 2026-09-01 | No provisioning, deploy, migration, cleanup, DNS or cutover occurs
  before full-plan approval; production Stages still need explicit authorization.
