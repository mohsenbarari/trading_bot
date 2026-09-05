# Coin Market Intelligence

- Sep05: Account1 capture `a6dcd636` and parent-first processor `9c613d39` are live; replay quarantine retained. `docs/MARKET_CAPTURE_RECOVERY_20260905.md`. Check Product rates.
- Sep05: Docker capture liveness validates live role/schema/source/status/freshness/PID, while strict `healthcheck` remains the replay/promotion gate. A retained point-in-time replay quarantine must stay visible to strict readiness and must never be deleted or synthesized merely to make Docker green.

- Settlement: future=`خ ن ف/ف ن ف`,`خ ف/ف ف` or absent; cash=`خ ن/ف ن/نق`; future wins.
- Units are project-thousands; normalize zeros/separators and resolve tails by family (`رب`=quarter, `پ/ت پ/پایین`=low-date).
- Named anchors set scale only. Unnamed uses consistent `MAIN_ONLINE` anchors; contradiction or weak evidence abstains.
- Trades use oldest exact/root; quantity markers win; reject ambiguity/overfill. `available_at_utc` governs reconciliation; pending/conditional/late are audit-only.
- Estimator is facts-only `/shadow`; gold/Herat/XAU/G1/G2 feed it, weak evidence abstains. Staging mirrors Iran; snapshots read-only; packs are ×100/`PACK_ONLY`.
- Capture contracts are `market_channel_event/1.0` and `coin_group_event/2.0`; receipts/revisions/replies/allowlist govern. Raw is 3d; Store opaque.
- Parser v10/linker v7; promotion needs version bump+dominance and replay needs raw/Store/feedback/causal `MAIN_ONLINE`; ambiguity=REVIEW.
- Private melted: immutable price/quantity, lifetime=120s, freshness=900s; lower remaining=fill, zero=full, positive close=no-trade, edit≠trade.
- Cutover needs real 7d coin/gold/Herat; predated coins abstain; no synthesis.
- 2026-08-29/30: Shadow fed G1/G2/private-gold; causal review; syntax needs raw; `human-feedback-r*`.
- 2026-08-31: 22–23 Tehran uses melted/Herat fresh at 22:00 (`AFTER_CLOSE_HOLD`, +0.4%). Never revive older data; guard stays fail-open.
- Account2 captures G1/G2. Projection health derives from results, never heartbeats.
- Replay digests stream via `(run_id,account)` to avoid `/tmp` overflow. Empty retries require prior nonempty immutable manifest, attempt>1, zero quarantine. Never clear state/backfill.
