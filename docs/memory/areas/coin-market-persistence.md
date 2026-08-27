# Coin Market Persistence

- Event-driven; no forward-fill/minute-XAU. Inference=5s; XAU/USDT=latest real ≤90s. Persist Wallex/10s, all XAU, 90s mean and 180s/600s features.
- Private melted price/quantity are immutable; outcome/executed/remaining are separate. No `final_price/final_quantity`.
- Facts use mTLS/HMAC, no PII. PostgreSQL Decimal/UTC/availability and atomic fact/outbox are authoritative. Selected raw/identity encrypted; raw/quarantine/outbox=3/14/7d, ledger permanent. Hash validated payload. Private offer/outcome share root; evidence stays attributes. Repair targets trade-bearing private/missing coin roots and known hash rejects, never full history.
- Docker is commit-bound; state/secrets/locks isolated. Bot ships one pinned artifact; web never builds/pulls.
- Capture: per-account FULL/fsync SQLite, 3d retention, 30m channel/6h group +2h ancestor reconciliation, release-bound owner. Spools isolated; public melted transient; private offer/outcome separate. Revision identity includes `silent`/`pinned`.
- Transport: logical/delivery sequence, atomic outbox, contiguous ACK, bounded repair. Product reads `estimator_snapshot_web_view/1.0`; health requires receipt, delivered outbox, matching view.
- History import is ordered/idempotent with digest quarantine; web encrypts raw/actors, bot gets facts. Legacy calibration IDs negative; Primary collisions fail. Import needs approval.
- Parity freezes one owner prefix for isolated lanes; replay cannot replace live gates.
- Legacy G1/G2/private-gold live feeds are unavailable: never oracle/gate/rollback. Roll back to pinned private image/snapshot/owner without deleting state.
- Account2 reuses sender HMAC; rotation splits identity. Session overlap is forbidden.
- With legacy feeds unavailable, `PRIVATE_PRIMARY` skips legacy/`PRIVATE_SHADOW` parity; require raw sequence/ledger gap audit, isolated real-snapshot migration rehearsal, parser contracts, approval and release-bound receipts.
- Adapter=500/cycle, per-stream cursors/causal merge. Receiver redacts payload after checkpoint+3d, retaining stream/sequence/fact/revision/hash; watermark regression fails.
- Account1 stores no live reply IDs; Account2 is bounded. Never stage in `/tmp`; use streaming.
- Release gates are opt-in: bot binds image/env to SHA/tree/signature/content-ID; preflight installs SHA-scoped controls and checks disk/private/path/secret.
- Archive migration: bound root-only `pg_dump -Fc`, offline restore, verified bot copy, two passes. Empty=`INITIAL_EMPTY`; partial/unversioned fail. Only DB starts.
- Shadow rollout is receiver-first, excludes capture. Journals rollback exact release containers and preserve state. Older target runtimes fail pending a separate upgrade gate.
