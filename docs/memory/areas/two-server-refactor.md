# Two-Server Refactor

- 2026-09-03 | `P2-12` uses real DNS, identity-allowlisted `DRILL` mutations excluded
  from Market/KPI, 60–90m Iran Writer and ≤4m per human handover. Bot stays Finland;
  reconnect does not change role, reconciled RPO is zero, and recurrence is quarterly
  or after fundamental Writer/DNS/Sync/Auth change.
- 2026-09-03 | `P2-11` is one-time Iran Standby admission, not deploy or Writer
  activation. Restore-tested quarantine precedes clean bootstrap; Product stays
  blocked and critical failure restarts the 7d soak. Rebuild, irreparable drift or
  fundamental architecture change alone repeats it.
- 2026-09-02 | Maximal `P2-10` on final hosts/isolated existing storage covers all
  requirements/faults: 465 baseline+P2/P3, 2×24h soak, 14d+30% load, ten handovers,
  three DNS cycles, reboot/restore/provider smoke and zero skipped/orphan/unknown.
  It gates architecture/high-risk changes, not every hotfix.
- 2026-09-02 | `P2-09`: Product OTP/session are local and generation-bound; switches
  force Product, not Console, re-login. Stable-ID notification/read and final Messenger
  state/media sync monotonically; Push/cache/realtime/draft/incomplete upload stay local.
- 2026-09-02 | `P2-06..08`: independent password/TOTP Consoles use 24h sessions and
  narrow actuators. Finland alone controls one DNS-only A/TTL≤30s. Fixed site/human
  Web role, Finland Telegram, 30/90s peer status, receipts and vector barriers apply.
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
