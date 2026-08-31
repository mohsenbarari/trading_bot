# Coin Market Persistence

- XAU is event-driven. `wa-fi` may retain every quote; transfer/model input matches legacy: latest quote per fixed 15s bucket, 5s inference over 90s point/mean. No polling, synthetic row, forward-fill, or minute compaction.
- Melted price/quantity are immutable; outcome/executed/remaining stay separate; no `final_price/final_quantity`.
- Facts: mTLS/HMAC, no PII; PostgreSQL Decimal/UTC + availability/outbox authority. Raw=3d, quarantine=14d, ACKed=7d after checkpoint; identity permanent.
- Docker is commit-bound; state/secrets/locks isolated. Bot ships pinned artifact; web never builds.
- Capture: per-account FULL/fsync SQLite, raw=3d, bounded context, one owner. G1/G2 + three melted sources persist; Event 2.1 has actor, old null identity is irrecoverable.
- Transport: logical/delivery sequence, atomic outbox, contiguous ACK, bounded repair. Redelivery allowed; duplicate apply forbidden. Product reads `estimator_snapshot_web_view/1.0`; health binds receipt/outbox/view.
- History import is ordered/idempotent with quarantine; web encrypts raw/actors, bot gets facts. Legacy IDs negative; Primary collisions fail; approval required.
- Parity freezes owner prefix; replay cannot replace live gates.
- Legacy is `NONE` for G1/G2/`MELTED_PRIMARY_FLOW`. Each required source needs a nonzero capture→parse→fact→archive/ACK→Store→snapshot trace.
- Account2 reuses sender HMAC; rotation splits identity; no session overlap.
- 2026-08-28: Urgent `PRIVATE_PRIMARY` skips soak. Product stays `LEGACY` until exact release, backup/off-host, one owner, nine-source gap audit, complete-grid `OK`, CAS. One-gram `NO_DATA` only for proven missing same-commodity anchor. Cutoff=`2026-08-25T09:33:00Z`.
- Adapter=500/cycle, causal cursors. Redact after checkpoint+3d; retain stream/sequence/fact/revision/hash; reject watermark regression.
- Account1 stores no live reply IDs; Account2 is bounded. Never use `/tmp`.
- Release binds image/env to SHA/tree/signature/content-ID and preflights disk/paths/secrets.
- Archive migration: root-only `pg_dump -Fc`, offline restore, verified copy, two idempotent passes; reject partial/unversioned.
- Receiver-first. Pre-`PRIMARY_COMMITTED`: restore exact old containers/markers. Post-commit: never restore old runtime; CAS Product to prior `LEGACY` bytes; retain PRIVATE_PRIMARY capture; never delete state.
- 2026-08-29/30: Shadow→Legacy is one-way under locks. Reconcile `83029b12` cleared scoped reviews and reached `RATE_READY`; Product stays `LEGACY`. Timer `4d98b85d` is disabled for dropping `root_offer_fact_id`; re-enable only with a compatible bridge.
