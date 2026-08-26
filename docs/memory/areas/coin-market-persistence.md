# Coin Market Persistence

- Inputs are event-driven, never synthetic/forward-filled. Inference=5s; XAU/USDT point=latest real event ≤90s. Persist each successful Wallex 10s poll and XAU quote; ledger consumed 90s point/mean, invoked 180s USDT trend and 600s regime. XAU minute compaction is unsafe.
- Private melted offer price/quantity are immutable; `final_price/final_quantity` are forbidden. Store outcome separately with evidenced executed/remaining quantity.
- Market Facts use a dedicated durable private-network lane; queue/checkpoints stay independent of product sync while reusing authenticated transport primitives.
- The replacement pipeline is Docker-native: one commit-bound image, separate commands, persistent local volumes, no baked data/secrets or network-shared SQLite. Host-native legacy stays for shadow/rollback until parity passes.
- Cutover seeds facts/calibration/external history, not Telegram raw. Analyze on `65.109.220.59`; promote only after parity/freshness/magnitude/open-market gates.
- Private-network Stage 1 passed bidirectionally: TLS 1.3, dual-key HMAC, replay/skew guards, ≥25 MiB/s, p95 <3.5ms, no public exposure, recovery/rollback. No runtime endpoint/cutover remains active.
- Market archive uses a dedicated PostgreSQL 15 database on the web/data host; product DB/Alembic and model-side SQLite remain separate. Wire decimals are strings, DB economics are NUMERIC, UTC/availability is authoritative, and fact/outbox commit atomically.
- Permanent Telegram identity/selected raw stays encrypted on web with HMAC lookup and audited reviewer decrypt; Facts have no PII. Retention: raw/quarantine 3/14d, ACKed outbox 7d, ledger permanent, rollback 7 open-market days.
- Docker Stage 3 passed pinned non-root/read-only images, isolated migration, persistent rollback and zero authority. Secret mounts are root-protected; locks guard sessions/SQLite.
- Stage 4 has SQLite FULL/fsynced capture per account, 3d retention, bounded 30m channel/6h group reconciliation, 2h reply ancestors and parser-independent flow. Live needs a release-bound session marker.
- Stages 5–6 consume Telegram spools separately with immutable calibration sidecars. XAU keeps every quote; public melted is transient. Private offer/outcome rows stay separate; routine expiry deletion is not economic retraction.
- Stage 7 adds durable Wallex/PAXG capture. Exact-Decimal ledgers reuse unchanged sample sets; 180s/600s features exist only when invoked. Direct XAU wins; PAXG is guarded proxy.
- Stage 8 separates logical `source_sequence` from revision delivery order. Facts/revisions publish atomically with a PostgreSQL outbox; the mTLS/HMAC lane has idempotent durable apply, contiguous ACK and bounded repair independent of Product Sync. The 1,000-fact loss/replay/down-receiver gate passed.
- Account 2 cutover reuses sender HMAC; rotation would split reply identity. Host/container session overlap is forbidden.
