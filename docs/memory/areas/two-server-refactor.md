# Two-Server Refactor

- 2026-09-03 | Governance at `90b8bf4737ba` is approved: Cursor executes ready Stages;
  Codex accepts/scopes external actions; no user Stage gate. Next: main barrier, then
  one-writer `P1-00` refresh. Human Dashboard Writer remains; no Stage/action is approved.

- 2026-09-03 | `P2-12`: real DNS, allowlisted `DRILL`, Iran Writer 60–90m, handovers
  ≤4m, Bot in Finland and RPO=0. Repeat quarterly or after fundamental control change.
- 2026-09-03 | `P2-11`: one-time Iran Standby admission after restore-tested quarantine;
  Product stays blocked and critical failure restarts 7d soak.
- 2026-09-02 | `P2-10` covers 465 baseline+P2/P3, 2×24h soak, 14d+30% load,
  handover/DNS/reboot/restore/provider and zero skipped/orphan/unknown; not every hotfix.
- 2026-09-02 | `P2-09`: Product OTP/session are local and generation-bound; switches
  re-login Product, not Console. Stable-ID notification/read and final Messenger
  state/media sync; Push/cache/realtime/draft/incomplete upload stay local.
- 2026-09-02 | `P2-06..08`: password/TOTP Consoles use 24h sessions and narrow
  actuators. Finland alone controls DNS A/TTL≤30s. Fixed site/human Web role,
  Finland Telegram, 30/90s peer status, receipts and vector barriers apply.
- 2026-09-02 | `P1-00..06`: twelve behavior/provenance families and six gaps define
  parity. Finland has two Web replicas, singleton jobs, split Bot and separate app/Market
  DBs; target shared app DB removes internal sync. Deterministic merge plus
  465/465 differential/browser/load/24h/restore proof block unknown or financial drift.
- 2026-09-02 | `P1-07..08`: initial cutover is 90m/4m/TTL30s with human checkpoints,
  exclusive Bot handoff and old-edge proxy. Target DB stays canonical after first write;
  closure needs `P2-11`, 7d quarantine, staggered retirement and replacement backups.
- 2026-09-02 | `P2-00..05`: typed ownership and versioned streams use atomic ACK,
  blocking gaps/rejections and immutable repair. Signed encrypted storage has split
  credentials and 14d+30% spool. Aligned hashes plus distinct `FULL_SYNC/MARKET_READY`
  gate signed forward-only handover. Home alone mutates; Iran offers rehome atomically;
  restrictive field conflicts require humans, never LWW.
- 2026-09-01 | No operations before full-plan approval; production still needs
  explicit authorization.
