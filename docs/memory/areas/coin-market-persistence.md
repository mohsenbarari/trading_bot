# Coin Market Persistence

- Event-driven, no forward-fill. Inference=5s; XAU/USDT point=latest real ≤90s. Persist Wallex 10s/all XAU; ledger keeps 90s mean and 180s/600s features. Never minute-compact XAU.
- Private melted price/quantity are immutable; outcome/executed/remaining are separate. No `final_price/final_quantity`.
- Facts use mTLS/HMAC outside product sync and no PII. Web archive is PostgreSQL 15; Decimal, UTC/availability and atomic fact/outbox are authoritative. Selected raw/identity is encrypted. Retention raw/quarantine/outbox=3/14/7d; ledger permanent.
- Docker is commit-bound with isolated state/secrets/locks. Bot ships one pinned artifact; web never builds/pulls.
- Capture: per-account FULL/fsynced SQLite, 3d retention, 30m channel/6h group reconciliation, 2h ancestors, release-bound owner. Spools isolated; public melted transient; private offer/outcome separate.
- Transport has logical/delivery sequence, atomic outbox, contiguous ACK and bounded repair. Product reads only `estimator_snapshot_web_view/1.0`; health/GET require receipt+delivered outbox+matching view.
- History import is ordered, batch-hashed/idempotent, digest-only quarantine; web encrypts raw/actors and bot gets fact seeds. Calibration legacy exports use negative IDs; Primary collisions fail closed. Import needs approval.
- Parity freezes one owner prefix for isolated reference/candidate projections. Replay cannot replace open-market gates.
- Live legacy `GROUP_1`/`GROUP_2`/`PRIVATE_GOLD_CHANNEL` is unavailable: never oracle/gate/rollback. Roll back to the prior pinned private image/snapshot/owner marker without deleting state.
- Account2 reuses sender HMAC; rotation splits identity. Session overlap is forbidden.
- `PRIVATE_PRIMARY` and Product/WebApp require open-market parity, approval and release-bound receipt verification.
- Adapter caps 500/cycle with per-stream cursors/causal merge. Receiver redacts payload after checkpoint+3d while preserving stream/sequence/fact/revision/hash; watermark regression fails closed.
- Account1 stores no live reply IDs; Account2 is bounded. Never stage in `/tmp`; use streaming.
- Release gates are opt-in: bot binds image/env to SHA/tree/signature/content-ID and `PRIVATE_SHADOW`; preflight installs SHA-scoped controls, streams the same ID and checks disk/private/path/secret.
- Archive migration requires bound root-only `pg_dump -Fc`, no-network restore, verified bot copy, then two passes with second `already_current`. Empty stores may use `INITIAL_EMPTY`; partial/unversioned fail closed. Only DB may start; capture/Product stay off.
- Shadow rollout is receiver-first and excludes all capture roles. Journals rollback only exact release containers and preserve DB/volumes/state. Existing older target runtimes fail closed pending a separate upgrade gate.
