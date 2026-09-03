# Coin Market Persistence

- XAU is event-driven. `wa-fi` may retain every quote; transfer/model input uses latest per 15s bucket and 5s inference over 90s point/mean. No polling, synthesis, forward-fill or minute compaction.
- Melted price/quantity are immutable; outcome/executed/remaining stay separate; no `final_price/final_quantity`.
- Facts: mTLS/HMAC, no PII; PostgreSQL Decimal/UTC + availability/outbox authority. Accepted canonical facts and identity/revision lineage are permanent: 180d hot, then compressed cold with no automatic deletion. This covers G1/G2, private plus two melted sources, Herat, USDT, and future Iranian exchange data.
- Docker is commit-bound; state/secrets/locks stay isolated. Bot ships pinned artifacts; Web never builds.
- Capture: per-account FULL/fsync SQLite, raw=3d, bounded context, one owner. G1/G2 + three melted sources persist; Event 2.1 has actor, old null identity is irrecoverable.
- Transport: logical/delivery sequence, atomic outbox, contiguous ACK, bounded repair. Redelivery allowed; duplicate apply forbidden. Product reads `estimator_snapshot_web_view/1.0`; health binds receipt/outbox/view.
- History import is ordered/idempotent. Legacy IDs are negative; Primary collisions fail and require approval.
- Parity freezes owner prefix; replay cannot replace live gates.
- Legacy is `NONE` for G1/G2/`MELTED_PRIMARY_FLOW`. Each required source needs a nonzero capture→parse→fact→archive/ACK→Store→snapshot trace.
- Account2 reuses sender HMAC; rotation splits identity; no session overlap.
- 2026-08-28: Urgent `PRIVATE_PRIMARY` skips soak. Product stays `LEGACY` until exact release, backup/off-host, one owner, nine-source gap audit, complete-grid `OK`, CAS. One-gram `NO_DATA` only for proven missing same-commodity anchor. Cutoff=`2026-08-25T09:33:00Z`.
- Adapter=500/cycle, causal cursors. Redact after checkpoint+3d; retain stream/sequence/fact/revision/hash; reject watermark regression.
- Account1 stores no live reply IDs; Account2 is bounded. Never use `/tmp`.
- Release binds image/env to SHA/tree/signature/content-ID and preflights resources.
- Archive migration: root-only `pg_dump -Fc`, verified offline copy/restore and two idempotent passes; reject partial/unversioned.
- Receiver-first. Before `PRIMARY_COMMITTED`, restore exact old containers/markers; afterward never restore old runtime or delete state—CAS Product to prior `LEGACY` bytes and retain capture.
- 2026-08-29/30: Shadow→Legacy is one-way under locks. Reconcile `83029b12` cleared scoped reviews and reached `RATE_READY`; Product stays `LEGACY`. Timer `4d98b85d` is disabled for dropping `root_offer_fact_id`; re-enable only with a compatible bridge.
