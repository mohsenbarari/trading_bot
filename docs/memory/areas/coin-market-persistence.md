# Coin Market Persistence

- Inputs are event-driven, never synthetic/forward-filled. Inference=5s; XAU/USDT point=latest real event ≤90s. Persist each successful Wallex 10s poll and XAU quote; ledger consumed 90s point/mean, invoked 180s USDT trend and 600s regime. XAU minute compaction is unsafe.
- Private melted offer price/quantity are immutable; `final_price/final_quantity` are forbidden. Store outcome separately with evidenced executed/remaining quantity.
- Market Facts use a dedicated durable private-network lane; queue/checkpoints stay independent of product sync while reusing authenticated transport primitives.
- The replacement pipeline is Docker-native: one commit-bound image, separate commands, persistent local volumes, no baked data/secrets or network-shared SQLite. Host-native legacy stays for shadow/rollback until parity passes.
- Cutover seeds facts/calibration/external history, not Telegram raw. Analyze on `65.109.220.59`; promote only after parity/freshness/magnitude/open-market gates.
- Private-network Stage 1 passed bidirectionally: TLS 1.3, dual-key HMAC, replay/skew guards, ≥25 MiB/s, p95 <3.5ms, no public exposure, recovery/rollback. No runtime endpoint/cutover remains active.
- Market archive uses a dedicated PostgreSQL 15 database on the web/data host; product DB/Alembic and model-side SQLite remain separate. Wire decimals are strings, DB economics are NUMERIC, UTC/availability is authoritative, and fact/outbox commit atomically.
- Permanent Telegram identity/selected raw stays encrypted on web with HMAC lookup and audited reviewer decrypt; Facts have no PII. Retention: raw/quarantine 3/14d, ACKed outbox 7d, ledger permanent, rollback 7 open-market days.
- Docker Stage 3 passed pinned images, non-root/read-only profiles, isolated migration, persistent rollback and zero live authority. Secrets: parents `root:root 0700`, files `root:10001 0440`; PostgreSQL adds group 10001. Mount locks guard sessions/SQLite. Transport/adapter/estimator remain fixture-only.
- Docker Stage 4 uses one SQLite FULL outbox/fsynced JSONL capture per account, exact 3d availability retention, bounded 30m channel/6h group reconciliation, 2h reply ancestors, per-source health and parser-independent flow. Live needs a release-bound session marker.
- Docker Stages 5–6 consume both Telegram spools with separate budgets, 3d staging and required immutable calibration sidecars. XAU keeps every quote; public melted is transient. Private offers/outcomes are separate; routine expiry deletion is not economic retraction.
- Stage 7 adds independent durable Wallex/PAXG capture and a third processor spool. Ledger is exact Decimal and reuses unchanged sample-set snapshots; 180s trend/600s regime roles exist only when invoked. Direct XAU wins; PAXG stays a guarded proxy. No deploy/model authority yet.
- Account 2 cutover reuses sender HMAC; rotation would split reply identity. Host/container session overlap is forbidden.
