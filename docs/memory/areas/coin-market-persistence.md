# Coin Market Persistence

- Event-driven; no forward-fill/minute-XAU. Inference=5s; XAU/USDT=latest real ≤90s. Persist Wallex/10s, all XAU, 90s mean and 180s/600s features.
- Private melted price/quantity are immutable; outcome/executed/remaining are separate. No `final_price/final_quantity`.
- Facts use mTLS/HMAC, no PII. PostgreSQL Decimal/UTC/availability and atomic fact/outbox are authoritative. Web encrypts selected raw plus coin actor Telegram ID/name; none enter facts, transport, logs or health. Raw=3d, quarantine=14d, ACKed envelope=7d behind checkpoint; identity ledger permanent. Private offer/outcome share root.
- Docker is commit-bound with isolated state/secrets/locks. Bot ships a pinned artifact; web never builds.
- Capture: per-account FULL/fsync SQLite, 3d retention, 30m channel/6h group +2h ancestor reconciliation, release-bound owner. GROUP_1/2 and three melted sources (`PRIVATE_GOLD_CHANNEL`, `MELTED_AGGREGATE`, `MELTED_FLOW`) are permanent research sources. Group event 2.1 carries numeric Telegram ID/name to web; legacy HMAC/null-name identity is irrecoverable.
- Transport: logical/delivery sequence, atomic outbox, contiguous ACK, bounded repair. Product reads `estimator_snapshot_web_view/1.0`; health requires receipt/outbox/view agreement.
- History import is ordered/idempotent with digest quarantine; web encrypts raw/actors, bot gets facts. Legacy calibration IDs negative; Primary collisions fail. Import needs approval.
- Parity freezes one owner prefix for isolated lanes; replay cannot replace live gates.
- Legacy G1/G2/private-gold feeds are unavailable: never oracle/gate/rollback. Roll back to pinned image/snapshot/owner without deleting state.
- Account2 reuses sender HMAC; rotation splits identity. Session overlap is forbidden.
- 2026-08-28: Owner authorizes urgent production `PRIVATE_PRIMARY` without multi-session soak because legacy G1/G2/private-gold feeds are unavailable. Fresh backup/restore, single ownership, release binding, gap audit, live snapshots, recent catch-up and rollback remain mandatory; never fabricate data or use stale legacy output.
- Adapter=500/cycle, per-stream cursors/causal merge. Receiver redacts payload after checkpoint+3d, retaining stream/sequence/fact/revision/hash; watermark regression fails.
- Account1 stores no live reply IDs; Account2 is bounded. Never stage in `/tmp`.
- Release gates are opt-in: bind image/env to SHA/tree/signature/content-ID; preflight checks disk/private paths/secrets.
- Archive migration: root-only bound `pg_dump -Fc`, offline restore, verified bot copy, two idempotent passes. Empty=`INITIAL_EMPTY`; partial/unversioned fail. Only DB starts.
- Shadow rollout is receiver-first and excludes capture. Journals preserve state and rollback exact containers; old runtimes require upgrade.
