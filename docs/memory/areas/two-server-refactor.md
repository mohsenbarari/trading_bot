# Two-Server Refactor

- 2026-09-02 | Owner approved `P2-07`: keep `coin.gold-trade.ir` as one DNS-only A
  record with TTL≤30s and no AAAA/CNAME/proxy. Human Arvan changes use a Finland-only
  root-mounted token, CAS-like plan/apply, multi-layer signed proof and manual partition
  receipts; panel fallback is audited, and DNS never changes Writer automatically.
- 2026-09-02 | Owner approved `P2-06`: each site has an independent Operations
  Console on a product-independent HTTPS hostname with no IP allowlist. Local
  username/password/Google-Authenticator-compatible TOTP, absolute 24h sessions,
  sensitive-action re-auth, bounded audit/retention and a narrow actuator are required;
  Product Admin/Grafana stay separate and peer stale state can never appear current.
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
- 2026-09-02 | Owner approved `P2-00..05`: all SQL/Redis/file/object state has typed
  ownership with durable Messenger/notification sync, local ephemeral/provider/
  Telegram state and minimal encrypted PII. Domain streams use sequence/version,
  atomic ACK, blocking rejection/gap and immutable repair. Iran Object Storage has
  split buckets/credentials, client encryption/site signing, immutable objects,
  14d+30% spool and bounded retention. Bootstrap uses consistent cutoff+replay,
  aligned barriers/business-media hashes, separate FULL_SYNC/MARKET_READY gates and
  two protected restore-tested snapshots. Writer handover is human, signed,
  forward-only and fail-closed across OS/DB fences. Aggregate home alone mutates;
  active Iran offers rehome atomically on failback. Global quotas reserve per-site
  budgets; field conflicts use temporary restrictive-wins plus human resolution, never LWW.
- 2026-09-01 | No provisioning, deploy, migration, cleanup, DNS or cutover occurs
  before full-plan approval; production Stages still need explicit authorization.
