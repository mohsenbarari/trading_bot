# Coin Market Persistence

- Inputs are event-driven, never forward-filled. Inference=5s; XAU/USDT point=latest real ≤90s. Persist Wallex 10s polls and all XAU quotes; ledger 90s point/mean plus on-demand 180s/600s features. Never minute-compact XAU.
- Private melted offer price/quantity are immutable; `final_price/final_quantity` are forbidden. Store outcome separately with evidenced executed/remaining quantity.
- Market Facts use mTLS/HMAC outside product sync. Docker is commit-bound with isolated state/secrets and owner locks; legacy remains for rollback/parity.
- Cutover seeds facts/calibration/history, not Telegram raw. Analyze on `65.109.220.59`; promote only after parity/freshness/magnitude/open-market gates.
- Web archive is PostgreSQL 15, separate from product DB/Alembic. Wire Decimal, UTC/availability and atomic fact/outbox are authoritative.
- Selected raw/identity stays encrypted on web; Facts have no PII. Retention: raw/quarantine/outbox 3/14/7d, ledger permanent, rollback 7 open-market days.
- Stage 4: per-account FULL/fsynced SQLite, 3d retention, 30m channel/6h group reconciliation, 2h reply ancestors, parser-independent flow; live needs a release-bound session marker.
- Stages 5–7 isolate spools and durable Wallex/PAXG. XAU keeps all quotes and outranks guarded PAXG; public melted is transient, private offer/outcome separate, expiry is not retraction.
- Stages 8–10 separate logical/delivery sequence with atomic outbox, contiguous ACK and bounded repair. Snapshots retain ACK/web apply and timing/input trace; route loss is stale and failures close.
- History import is batch-hashed/idempotent with ordered revisions and digest-only quarantine. Web encrypts raw/actors; bot seeds facts only. Real import needs backup/cutover authorization.
- Stage 12 parity is redacted/signed with one owner, shared window, cloned store and aligned snapshots. Separate value/metadata/schema drift; minute XAU is not an oracle and replay cannot replace open-market gates.
- Account 2 reuses sender HMAC; rotation splits identity. Host/container session overlap is forbidden.
- Stage 13-A runs two-host Docker staging with container capture authority and `PRIVATE_SHADOW`; legacy sessions/units stay guarded rollback assets. `PRIVATE_PRIMARY`, product/WebApp authority and production need open-market parity plus separate approval.
- Adapter reads are bounded to 500/cycle with per-stream cursors and causal heap merge; unbounded payload fetch/sort is forbidden.
- Receiver payload is operational: redact after monotonic adapter checkpoint plus 3d; preserve stream/sequence/fact/revision/hash. Watermark regression fails closed.
- Capture startup/retention streams records; Account1 stores no live reply IDs and Account2 is bounded. Never stage releases in `/tmp`; stream or use the expiring disk-backed `/var/tmp/trading-bot-market-pipeline-transfer`.
