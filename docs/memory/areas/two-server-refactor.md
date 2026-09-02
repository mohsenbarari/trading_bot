# Two-Server Refactor

- 2026-09-02 | Owner approved `P1-02..06`: Finland uses two Web replicas, singleton
  jobs, split Bot and separate app/Market DBs. Shared app DB removes local sync;
  deterministic merge preserves sessions/IDs/media and blocks ambiguous/financial
  conflicts. Differential staging needs 465/465 mapping, six closed gaps, failure/
  browser/load proof, 24h soak and restore/rollback; no operation is authorized.
- 2026-09-02 | Owner approved `P1-00`: twelve behavior families, Web/Bot and
  provenance contracts, six mandatory evidence gaps and runtime-ownership seed.
  Unknown parity still blocks implementation.
- 2026-09-02 | Owner approved `P1-07` design: initial cutover is 90m/4m with 30s
  TTL, human checkpoints, exclusive Bot handoff, old-edge proxy without local
  writes, 2h observation and numeric rollback alerts. After target's first write,
  its DB stays canonical; production execution remains separately unauthorized.
- 2026-09-02 | Owner approved `P1-08` design: closure waits for Iran `P2-11` and
  7d quarantine; old edge needs 48h zero valid traffic then 24h monitoring. Old
  Bot/Web hosts retire sequentially 24h apart; the 30d migration backup needs a
  restore-tested replacement. Every deletion and WA-IR branch removal stays gated.
- 2026-09-02 | Owner approved `P2-00`: durable Messenger/read/media and logical
  notification/read sync; sessions, OTP, upload/browser/provider and Telegram
  runtime stay local. Minimal encrypted PII and row/field/command authority are
  mandatory; Offer/Request origins and Trade context remain immutable and every
  SQL/Redis/file/object item must be registered with zero unknowns.
- 2026-09-02 | Owner approved `P2-01`: domain streams use contiguous sequence and
  aggregate version, never timestamps/LWW. ACK follows atomic apply; rejection does
  not advance checkpoints. A 30s healthy gap blocks only that stream/dependencies;
  events are immutable and repair replays originals or uses an approved bootstrap.
- 2026-09-01 | No provisioning, deploy, migration, cleanup, DNS or cutover occurs
  before full-plan approval; production Stages still need explicit authorization.
- 2026-09-01 | Web Writer handover is human-only without lease or auto failover:
  fence/drain source, transfer signed receipt, verify Arvan DNS and sync gates,
  then explicitly activate destination. Bot remains independent.
