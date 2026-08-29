# Coin Market Persistence

- Event-driven; no forward-fill/minute-XAU. Inference=5s; XAU/USDT≤90s; persist Wallex/10s, XAU, 90s mean, 180s/600s features.
- Melted price/quantity are immutable; outcome/executed/remaining stay separate; no `final_price/final_quantity`.
- Facts use mTLS/HMAC, no PII. PostgreSQL Decimal/UTC/availability+atomic outbox are authority. Web encrypts raw/actors; facts/logs/health exclude them. Raw=3d, quarantine=14d, ACKed=7d behind checkpoint; identity permanent. Offer/outcome share root.
- Docker is commit-bound; state/secrets/locks isolated. Bot ships pinned artifact; web never builds.
- Capture: per-account FULL/fsync SQLite, 3d raw, 30m channel/6h group+2h ancestors, one owner. G1/G2+three melted sources persist. Event 2.1 has actor; old null identity is irrecoverable.
- Transport: logical/delivery sequence, atomic outbox, contiguous ACK, bounded repair. Redelivery allowed; duplicate apply forbidden. Product reads `estimator_snapshot_web_view/1.0`; health binds receipt/outbox/view.
- History import is ordered/idempotent with quarantine; web encrypts raw/actors, bot gets facts. Legacy IDs negative; Primary collisions fail; approval required.
- Parity freezes owner prefix; replay cannot replace live gates.
- Legacy is `NONE` for G1/G2/`MELTED_PRIMARY_FLOW` and is never their data oracle. The nine required sources are a unique subset, not total cardinality; each needs nonzero capture→parse→fact→archive/ACK→bot-Store→main-snapshot trace.
- Account2 reuses sender HMAC; rotation splits identity; no session overlap.
- 2026-08-28: Urgent `PRIVATE_PRIMARY` skips staging/soak. Product stays `LEGACY` until exact release, backup/off-host, one owner, nine-source gap audit, complete-grid `OK`, CAS. One-gram `NO_DATA` only for proven missing same-commodity anchor with fresh melted. Cutoff=`2026-08-25T09:33:00Z`. Exact-SHA control-release prepare changes no service/DB/authority.
- Adapter=500/cycle, causal cursors. Redact after checkpoint+3d; retain stream/sequence/fact/revision/hash; reject watermark regression.
- Account1 stores no live reply IDs; Account2 is bounded. Never use `/tmp`.
- Release binds image/env to SHA/tree/signature/content-ID and preflights disk/paths/secrets.
- Archive migration: root-only `pg_dump -Fc`, offline restore, verified copy, two idempotent passes; reject partial/unversioned.
- Receiver-first rollout. Pre-`PRIMARY_COMMITTED`: restore exact old containers/markers. Post-commit: never restore old runtime; CAS Product to prior bytes/LEGACY with inference off, retain PRIVATE_PRIMARY capture, forward-repair, never delete state.
