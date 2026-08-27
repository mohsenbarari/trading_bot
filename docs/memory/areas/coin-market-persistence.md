# Coin Market Persistence

- Event-driven, no forward-fill. Inference=5s; XAU/USDT point=latest real ≤90s. Persist Wallex 10s/all XAU; ledger has 90s mean and 180s/600s features. Never minute-compact XAU.
- Private melted price/quantity are immutable; outcome/executed/remaining are separate. No `final_price/final_quantity`.
- Facts use mTLS/HMAC outside product sync; no PII. Web archive=PostgreSQL 15; Decimal, UTC/availability, atomic fact/outbox are authoritative. Selected raw/identity encrypted; retention raw/quarantine/outbox=3/14/7d, ledger permanent. Hash validated payload only. Private melted offer/outcome share root identity; evidence revision stays attributes.
- Docker is commit-bound with isolated state/secrets/locks. Bot ships one pinned artifact; web never builds/pulls.
- Capture: per-account FULL/fsynced SQLite, 3d retention, 30m channel/6h group reconciliation, 2h ancestors, release-bound owner. Spools isolated; public melted transient; private offer/outcome separate. Revision identity covers persisted metadata including `silent`/`pinned`.
- Transport has logical/delivery sequence, atomic outbox, contiguous ACK, bounded repair. Product reads `estimator_snapshot_web_view/1.0`; health requires receipt+delivered outbox+matching view.
- History import is ordered, hashed/idempotent, digest-only quarantine; web encrypts raw/actors, bot gets facts. Legacy calibration uses negative IDs; Primary collisions fail closed. Import needs approval.
- Parity freezes one owner prefix for isolated reference/candidate projections. Replay cannot replace open-market gates.
- Live legacy `GROUP_1`/`GROUP_2`/`PRIVATE_GOLD_CHANNEL` is unavailable: never oracle/gate/rollback. Roll back to the prior pinned private image/snapshot/owner marker without deleting state.
- Account2 reuses sender HMAC; rotation splits identity. Session overlap is forbidden.
- `PRIVATE_PRIMARY` and Product/WebApp require open-market parity, approval and release-bound receipt verification.
- Adapter caps 500/cycle with per-stream cursors/causal merge. Receiver redacts payload after checkpoint+3d, retaining stream/sequence/fact/revision/hash; watermark regression fails closed.
- Account1 stores no live reply IDs; Account2 is bounded. Never stage in `/tmp`; use streaming.
- Release gates are opt-in: bot binds image/env to SHA/tree/signature/content-ID and `PRIVATE_SHADOW`; preflight installs SHA-scoped controls, streams same ID and checks disk/private/path/secret.
- Archive migration: bound root-only `pg_dump -Fc`, no-network restore, verified bot copy, two passes (`already_current` second). Empty may use `INITIAL_EMPTY`; partial/unversioned fail. Only DB starts; capture/Product off.
- Shadow rollout is receiver-first, excludes capture. Journals rollback exact release containers and preserve state. Older target runtimes fail pending a separate upgrade gate.
