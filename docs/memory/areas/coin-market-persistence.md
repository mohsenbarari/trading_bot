# Coin Market Persistence

- External inputs are event-driven, never synthetic per-second rows. Inference refresh=5s; effective XAU/USDT point=latest real event within 90s, with no forward-fill.
- Retain exact consumed roles: 90s point/mean, invoked 180s USDT-anchor trend, and 600s regime features. Persist Wallex USDT per successful 10s poll and XAU per actual parsed quote; one-XAU/minute compaction is not live-parity-safe.
- Private melted persistence has immutable offered price/quantity and no `final_price/final_quantity`. Store outcome separately as `trade_status` plus evidenced executed/remaining quantity.
- Market Facts use a dedicated durable one-way lane over the provider private network; keep its queue/checkpoints independent of general product sync while reusing authenticated integrity-checked transport primitives.
- The replacement pipeline is Docker-native: one commit-bound image, separate service commands, persistent local volumes, no baked data/secrets, and no shared SQLite over a network filesystem. Host-native legacy stays only for shadow/rollback until container parity passes.
- Cutover seeds facts/calibration/external history, not raw Telegram. Analyze on `65.109.220.59`; promote only after parity/freshness/magnitude/open-market live gates.
- Private-network Stage 1 passed bidirectionally with TLS 1.3, dual-key HMAC, replay/skew guards, at least 25 MiB/s for 64 KiB probes, p95 below 3.5 ms, zero public exposure, failure recovery and full ephemeral rollback. No runtime endpoint or cutover remains active.
