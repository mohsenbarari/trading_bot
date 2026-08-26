# Coin Market Persistence

- Inputs are event-driven, never forward-filled. Inference=5s; XAU/USDT point=latest real event ≤90s. Persist successful Wallex 10s polls/XAU quotes; ledger 90s point/mean and invoked 180s trend/600s regime. XAU minute compaction is unsafe.
- Private melted offer price/quantity are immutable; `final_price/final_quantity` are forbidden. Store outcome separately with evidenced executed/remaining quantity.
- Market Facts use a durable mTLS/HMAC private lane independent of product sync. Runtime is Docker-native: one commit-bound image, isolated commands, local volumes, no baked state/secrets or network SQLite; locks prevent concurrent session/SQLite owners. Legacy stays through parity/rollback.
- Cutover seeds facts/calibration/external history, not Telegram raw. Analyze on `65.109.220.59`; promote only after parity/freshness/magnitude/open-market gates.
- The web archive is dedicated PostgreSQL 15, separate from product DB/Alembic and bot SQLite. Wire Decimal, UTC/availability and atomic fact/outbox are authoritative.
- Selected raw/identity stays encrypted on web; Facts have no PII. Retention: raw/quarantine 3/14d, ACKed outbox 7d, ledger permanent, rollback 7 open-market days.
- Stage 4 has SQLite FULL/fsynced capture per account, 3d retention, 30m channel/6h group reconciliation, 2h reply ancestors and parser-independent flow. Live needs a release-bound session marker.
- Stages 5–6 consume Telegram spools separately with calibration sidecars. XAU keeps every quote; public melted is transient. Private offer/outcome stay separate; expiry deletion is not economic retraction.
- Stage 7 adds durable Wallex/PAXG capture. Decimal ledgers reuse sample sets; 180s/600s features are on-demand. Direct XAU wins; PAXG is guarded proxy.
- Stage 8 separates logical source sequence from revision delivery order. Atomic PostgreSQL outbox plus mTLS/HMAC apply, contiguous ACK and bounded repair are Product-Sync-independent; the 1,000-fact loss/replay/down gate passed.
- Stage 9 atomically maps with unit/magnitude/time guards, revision projections and checkpoints. Feed modes are explicit; rollback keeps capture. Bad mappings isolate; storage/sequence fail closed. Herat keeps trade dimensions.
- Stage 10 returns per-lane snapshots over private mTLS/HMAC with monotonic version/hash ACK. Pending publish, lost ACK and web apply are durable/idempotent; the exact model web view carries timing/point/mean/fallback trace and goes stale on route loss. No authority changed.
- Stage 11 history import is source-batched, hash-reconciled and idempotent; revisions are ordered and incompatible rows quarantine digest-only while storage failures fail closed. Selected raw/actors must already be encrypted on web; bot seeds facts only. Real import needs backup and cutover authorization.
- Account 2 cutover reuses sender HMAC; rotation would split reply identity. Host/container session overlap is forbidden.
