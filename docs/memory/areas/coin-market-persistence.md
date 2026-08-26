# Coin Market Persistence

- Inputs are event-driven, never forward-filled. Inference=5s; XAU/USDT point=latest real ≤90s. Persist successful Wallex 10s polls/XAU quotes; ledger 90s point/mean plus invoked 180s trend/600s regime. Never compact XAU by minute.
- Private melted offer price/quantity are immutable; `final_price/final_quantity` are forbidden. Store outcome separately with evidenced executed/remaining quantity.
- Market Facts use a durable mTLS/HMAC lane independent of product sync. Runtime is Docker-native: one commit-bound image, isolated commands, local volumes, no baked state/secrets/network SQLite; locks prevent concurrent session/SQLite owners. Legacy stays for parity/rollback.
- Cutover seeds facts/calibration/external history, not Telegram raw. Analyze on `65.109.220.59`; promote only after parity/freshness/magnitude/open-market gates.
- Web archive is PostgreSQL 15, separate from product DB/Alembic and bot SQLite. Wire Decimal, UTC/availability and atomic fact/outbox are authoritative.
- Selected raw/identity stays encrypted on web; Facts have no PII. Retention: raw/quarantine 3/14d, ACKed outbox 7d, ledger permanent, rollback 7 open-market days.
- Stage 4 has FULL/fsynced SQLite per account, 3d retention, 30m channel/6h group reconciliation, 2h reply ancestors and parser-independent flow. Live needs a release-bound session marker.
- Stages 5–7 use separate spools/sidecars and durable Wallex/PAXG capture. XAU keeps all quotes and outranks guarded PAXG; public melted is transient, private offer/outcome stay separate, expiry is not retraction, and 180s/600s features are on-demand.
- Stages 8–10 separate logical/delivery sequence and use atomic outbox, contiguous ACK, bounded repair, guarded atomic mapping and explicit feed lanes. Per-lane snapshots have durable pending/ACK/web apply, full timing/input trace and become stale on route loss; bad mapping/storage/sequence fail closed while capture survives rollback.
- History import is batch-hashed/idempotent with ordered revisions and digest-only quarantine. Web encrypts raw/actors, bot seeds facts only; real import needs backup/cutover authorization.
- Stage 12 parity is redacted/signed: one capture owner, exact receipt window/shared clock, cloned final-store and aligned-snapshot comparison, no raw scratch. Separate value/metadata/schema drift; minute-compacted XAU is not an event oracle. Parser drift needs approved labels; offline replay cannot replace the open-market gate.
- Account 2 cutover reuses sender HMAC; rotation would split reply identity. Host/container session overlap is forbidden.
- Stage 13-A runs the two-host Docker pipeline live in staging with container capture authority and `PRIVATE_SHADOW`; legacy sessions/units remain rollback assets behind a runtime single-owner guard. `PRIVATE_PRIMARY`, product/WebApp authority and production still require full open-market parity and separate approval.
