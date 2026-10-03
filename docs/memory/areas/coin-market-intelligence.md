# Coin Market Intelligence

- Estimator `f5b98fee`: trade priority≤5m; non-fallback anchors require same source; retention7d. Prevent old trades masking fresh offers; retain rollback.
- Processor recovery retains peers/data and revision links; see `docs/MARKET_PROCESSOR_RECOVERY_20260907.md`.
- Oct03 sender `532a9804`: bound-ACK replay of originals, no checkpoint resets. Watchdogs: real endpoints/progress,15s, two failures, cooldown5m, deploy mutex; silence/catch-up never restart. `docs/MARKET_PIPELINE_RESILIENCE_20261003.md`.
- Account1 `a6dcd636`: retain replay quarantine. Liveness validates role/schema/source/status/freshness/PID; readiness exposes quarantine. No deleted/synthetic evidence.

- Settlement: future=`خ ن ف/ف ن ف`,`خ ف/ف ف` or absent; cash=`خ ن/ف ن/نق`.
- Units=project-thousands; normalize zeros/separators; `رب`=quarter and `پ/ت پ/پایین`=low-date.
- Named anchors set scale only. Unnamed uses consistent `MAIN_ONLINE`; contradiction/weak evidence abstains.
- Trades use oldest exact/root; quantity markers win; reject ambiguity/overfill. `available_at_utc` governs reconciliation; pending/conditional/late are audit-only.
- Estimator is facts-only `/shadow`; gold/Herat/XAU/G1/G2 feed it, weak evidence abstains. Staging mirrors Iran; snapshots read-only; packs are ×100/`PACK_ONLY`.
- Capture: `market_channel_event/1.0`/`coin_group_event/2.0`; receipts, revisions, replies and allowlist govern. Raw=3d.
- Parser v10/linker v7; promotion needs version bump+dominance; replay needs raw/Store/`human-feedback-r*`/causal `MAIN_ONLINE`; syntax needs raw, ambiguity=REVIEW.
- Private melted: immutable price/quantity, lifetime=120s, freshness=900s; lower remaining=fill, zero=full, positive close=no-trade, edit≠trade.
- Cutover needs real 7d coin/gold/Herat; predated coins abstain; no synthesis.
- 2026-08-31: 22–23 Tehran uses melted/Herat fresh at 22:00 (`AFTER_CLOSE_HOLD`, +0.4%). Never revive older data; guard stays fail-open.
- Account2 captures G1/G2. Projection health derives from results, never heartbeats.
- Replay digests stream via `(run_id,account)` to avoid `/tmp` overflow. Empty retries require prior nonempty immutable manifest, attempt>1, zero quarantine. Never clear state/backfill.
