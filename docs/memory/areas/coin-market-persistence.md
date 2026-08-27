# Coin Market Persistence

- Inputs are event-driven, without forward-fill. Inference=5s; XAU/USDT point=latest real ≤90s. Persist Wallex 10s polls/all XAU quotes; ledger records consumed 90s point/mean and on-demand 180s/600s features. Never minute-compact XAU.
- Private melted offer price/quantity are immutable; store evidenced outcome/executed/remaining quantity separately. No `final_price/final_quantity`.
- Market Facts use mTLS/HMAC outside product sync. Docker is commit-bound with isolated state/secrets/owner locks. Bot owns Git/build and ships one pinned artifact; web never builds/pulls.
- Web archive is separate PostgreSQL 15. Decimal, UTC/availability and atomic fact/outbox are authoritative. Selected raw/identity is encrypted; Facts contain no PII. Retention: raw/quarantine/outbox 3/14/7d, ledger permanent, rollback 7 open-market days.
- Capture: per-account FULL/fsynced SQLite, 3d retention, 30m channel/6h group reconciliation, 2h ancestors, release-bound ownership. Spools isolated; public melted transient; private offer/outcome separate; expiry≠retraction.
- Fact transport has logical/delivery sequence, atomic outbox, contiguous ACK and bounded repair. Product reads only receiver-issued `estimator_snapshot_web_view/1.0`; health/GET require latest receipt, delivered outbox and matching view. Raw/pending V2 is invisible.
- History import is batch-hashed/idempotent, ordered, with digest-only quarantine. Web encrypts raw/actors; bot receives fact seeds only. Calibration export accepts only active legacy authority and uses negative seed IDs; Primary ID collisions fail closed. Import needs backup/cutover authorization.
- Parity freezes one immutable prefix from the sole new capture owner and feeds isolated reference/candidate projections with aligned snapshots. Replay cannot replace open-market gates.
- Live legacy `GROUP_1`/`GROUP_2`/`PRIVATE_GOLD_CHANNEL` is unavailable: never oracle/gate/rollback. Roll back to the prior pinned private image/snapshot/owner marker without deleting state.
- Account 2 reuses sender HMAC; rotation splits identity. Host/container session overlap is forbidden.
- `PRIVATE_PRIMARY`, Product/WebApp authority and production require open-market parity plus separate approval. An independent release-bound verifier must authenticate schedule, model, transport and failure-drill receipts; operator assertions fail closed.
- Adapter reads are capped at 500/cycle with per-stream cursors and causal heap merge. Receiver redacts payload after monotonic checkpoint+3d while preserving stream/sequence/fact/revision/hash; watermark regression fails closed.
- Account1 stores no live reply IDs; Account2 is bounded. Never stage releases in `/tmp`; use streaming or expiring `/var/tmp/trading-bot-market-pipeline-transfer`.
- Product deploy may carry market source, but market image/Compose/migration/session/health/rollback need an explicit receiver-first path under the production lock; source transfer is not deployment.
