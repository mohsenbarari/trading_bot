# Coin Market Intelligence

- Replay certification failure preserves evidence and live capture; storage/integrity faults stop. Quarantine degrades readiness. Verify recovery using Product's actual snapshot and rejected prices.

- Settlement: future=`خ ن ف/ف ن ف`,`خ ف/ف ف` or absent; cash=`خ ن/ف ن/نق`; future wins.
- Units: project-thousands; accept full-Toman/separators/zeros. Resolve tails by family. `رب`=quarter; `پ/ت پ/پایین`=low-date; duplicated-zero needs family.
- Named anchors set scale only. Unnamed uses prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails; bootstrap=3 messages/2 senders/30m/1.5%.
- Trades: oldest exact/root; unique sibling+owner confirmation is reciprocal. Quantity markers beat tails; reject ambiguity/overfill; first reciprocal fill amends quantity.
- `available_at_utc` governs reconciliation. Drop absent/rejected; pending/conditional/>5m late are audit-only. Ignore `delivery`; fail closed.
- Estimator: facts-only `/shadow`, 120s; no-data=0/failure=3; gold/Herat/XAU/G1/G2; weak evidence abstains. Dashboard uses `PRIVATE_PRIMARY` only.
- Staging mirrors Iran; snapshots read-only. Bands ±10%; ties require choice. Packs ×100/`PACK_ONLY`.
- Capture: `market_channel_event/1.0`,`coin_group_event/2.0`; receipt/revision/reply/allowlist authoritative; raw=3d, Store opaque.
- Parser v10/linker v7. Promotion needs version bump+dominance; replay needs raw/Store/feedback/causal `MAIN_ONLINE`; ambiguity=REVIEW.
- Private melted: price/quantity immutable; lifetime=120s, freshness=900s. Lower remaining=fill; zero=full; positive close=no-trade; edit≠trade; inconsistency=ambiguous; partials finalize at deadline.
- Private quote: first header amount; ignore after `توضیحات/شرایط`. Filter per-message ranges only.
- Cutover needs real 7d coin/gold/Herat; predated coins abstain; no synthesis.
- 2026-08-29/30: Shadow fed G1/G2/private-gold; causal review; syntax needs raw; `human-feedback-r*`.
- 2026-08-31: 22–23 Tehran uses melted/Herat fresh at 22:00 (`AFTER_CLOSE_HOLD`, +0.4%). Never revive older data; guard stays fail-open.
- Account2 captures G1/G2. Projection health derives from results, never heartbeats.
- Replay digests stream via `(run_id,account)` to avoid `/tmp` overflow. Empty retries require prior nonempty immutable manifest, attempt>1, zero quarantine. Never clear state/backfill.
