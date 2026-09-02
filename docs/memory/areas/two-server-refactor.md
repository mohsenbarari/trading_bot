# Two-Server Refactor

- 2026-09-02 | Owner approved `P2-06..08`: independent site Consoles use local
  password/TOTP, 24h sessions and narrow actuators; Product Admin/Grafana stay separate.
  `coin.gold-trade.ir` is one DNS-only A with TTL≤30s and Finland-only human Arvan
  control. Fixed site/human Web role, Finland Telegram owner, 30/90s observed peer,
  root control state, pre-fence cancel/post-fence forward-only, signed return receipts
  and vector barriers prevent split-brain while Bot continues.
- 2026-09-02 | Owner approved `P1-02..06`: Finland uses two Web replicas, singleton
  jobs, split Bot and separate app/Market DBs. Shared app DB removes local sync;
  deterministic merge preserves sessions/IDs/media and blocks ambiguous/financial
  conflicts. Differential staging needs 465/465 mapping, six closed gaps, failure/
  browser/load proof, 24h soak and restore/rollback; no operation is authorized.
- 2026-09-02 | Owner approved `P1-00`: twelve behavior families, Web/Bot and
  provenance contracts, six mandatory evidence gaps and runtime-ownership seed.
  Unknown parity still blocks implementation.
- 2026-09-02 | Owner approved `P1-07..08`: initial cutover is 90m/4m with 30s TTL,
  human checkpoints, exclusive Bot handoff and old-edge proxy. Target DB becomes
  canonical after first write. Closure waits for `P2-11` plus 7d quarantine; old
  Bot/Web retire 24h apart and backups require tested replacements; operations stay gated.
- 2026-09-02 | Owner approved `P2-00..05`: typed state ownership, durable Messenger/
  notification sync and minimal encrypted PII; versioned streams have atomic ACK,
  blocking gaps/rejections and immutable repair. Encrypted signed Object Storage uses
  split credentials and 14d+30% spool. Cutoff/replay, aligned hash barriers, separate
  FULL_SYNC/MARKET_READY and protected snapshots gate human signed forward-only
  handover. Home alone mutates; active Iran offers rehome atomically. Per-site quota
  budgets and field-level restrictive-wins require human conflict resolution, never LWW.
- 2026-09-01 | No provisioning, deploy, migration, cleanup, DNS or cutover occurs
  before full-plan approval; production Stages still need explicit authorization.
