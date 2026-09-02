# Two-Server Refactor

- 2026-09-02 | Owner approved `P1-02..05`: typed capabilities separate origin
  and human Writer; Finland uses two Web replicas, singleton jobs, split Bot and
  separate app/Market DBs. Shared app DB removes local sync via ordered outbox/
  inbox. Merge uses table/row authority, preserves valid sessions, maps IDs/media,
  quarantines ambiguity/financial conflicts and must repeat within 4m/90m limits.
  `P1-06` also requires isolated differential staging, 465/465 mapping, six closed
  evidence gaps, Web/Bot/browser/failure matrices, relative performance guards,
  a 24h soak and restore/rollback proof. Legacy waits for `P1-08`; no operation is
  authorized and these budgets are not later deploy SLOs.
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
  mandatory; every SQL/Redis/file/object item must be registered with zero unknowns.
- 2026-09-01 | Offer origin is immutable and separate from `home_site`; Trade
  snapshots Offer/Request origin, execution surface, policy version and sensitive
  actor/role/tier context.
- 2026-09-01 | No provisioning, deploy, migration, cleanup, DNS or cutover occurs
  before full-plan approval; production Stages still need explicit authorization.
- 2026-09-01 | Initial Finland cutover reserves 90m with at most 4m interruption,
  24h soak, steady CPU <=60% and RAM/disk/pool <=70%; observe 2h, fence sources 7d
  and retain the approved backup 30d. These are not later deploy SLOs.
- 2026-09-01 | Web Writer handover is human-only without lease or auto failover:
  fence/drain source, transfer signed receipt, verify Arvan DNS and sync gates,
  then explicitly activate destination. Bot remains independent.
