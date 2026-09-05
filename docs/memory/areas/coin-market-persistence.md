# Coin Market Persistence

- XAU is event-driven: transfer/model input takes each 15s bucket's latest and infers every 5s over 90s point/mean; no polling, synthesis, forward-fill or minute compaction.
- Melted price/quantity are immutable; outcome/executed/remaining stay separate.
- Facts: mTLS/HMAC, no PII, Decimal/UTC, outbox authority. Facts and identity/revision lineage are permanent: 180d hot then compressed cold; cover G1/G2, melted, Herat, USDT and future exchanges.
- Docker is commit-bound; state/secrets/locks are isolated. Bot ships pinned artifacts; Web never builds.
- Capture: per-account FULL/fsync SQLite, raw=3d, bounded context, one owner. Persist G1/G2 + three melted sources; Event 2.1 has actor; old null identity is irrecoverable.
- Transport: logical/delivery sequence, atomic outbox, contiguous ACK, bounded repair. Redelivery is allowed; duplicate apply forbidden. Product reads `estimator_snapshot_web_view/1.0`; health binds receipt/outbox/view.
- History import is ordered/idempotent. Legacy IDs are negative; Primary collisions need approval.
- Parity freezes owner prefix; replay cannot replace gates.
- Legacy is `NONE` for G1/G2/`MELTED_PRIMARY_FLOW`. Each required source needs a nonzero capture→parse→fact→archive/ACK→Store→snapshot trace.
- Account2 reuses sender HMAC; rotation splits identity without session overlap.
- 2026-08-28: Urgent `PRIVATE_PRIMARY` skips soak. Product stays `LEGACY` pending exact release/off-host backup, one owner, nine-source audit, complete-grid `OK` and CAS. One-gram `NO_DATA` needs a proven missing anchor. Cutoff=`2026-08-25T09:33:00Z`.
- Adapter=500/cycle with causal cursors. Redact after checkpoint+3d; retain stream/sequence/fact/revision/hash; reject watermark regression.
- Account1 stores no live reply IDs; Account2 is bounded. Never use `/tmp`.
- Release binds image/env to SHA/tree/signature/content-ID and resource preflight.
- Archive migration: root-only `pg_dump -Fc`, verified offline copy/restore, two idempotent passes; reject partial/unversioned.
- Receiver-first. Before `PRIMARY_COMMITTED`, restore exact old containers/markers; afterward never restore old runtime/delete state—CAS Product to prior `LEGACY` bytes and retain capture.
- 2026-08-29/30: Shadow→Legacy is one-way. Reconcile `83029b12` reached `RATE_READY`; Product remains `LEGACY`. Timer `4d98b85d` drops `root_offer_fact_id`; enable only with a compatible bridge.
- 2026-09-05: `AUTHORITY_TRANSFERRED` keeps legacy Telegram collectors off; the incremental bridge feeds `LEGACY` Product and whole-DB quick-check runs only in release/backup. Deploy validates handoff before/after; relay mode never owns collectors.
