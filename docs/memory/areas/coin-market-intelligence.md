# Coin Market Intelligence

- Settlement: `خ ن ف/ف ن ف`,`خ ف/ف ف` or none=tomorrow; `خ ن/ف ن/نق`=cash; future wins.
- Units: project-thousand; accept full-Toman/separators/zeros. Resolve tails causally by family, never constants. `رب`=quarter; `پ/ت پ/پایین`=low-date; duplicated-zero low-price needs family.
- Named anchors resolve scale only. Unnamed uses prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails; bootstrap=3 messages/2 senders/30m/1.5%.
- Trades: oldest root/exact branch; unique sibling+owner confirmation is reciprocal. Quantity markers beat tails; reject ambiguity/overfill; only first reciprocal fill amends quantity.
- `available_at_utc` governs reconciliation. Drop absent/rejected; pending/conditional/>5m-late are audit-only. Ignore `delivery`; fail closed.
- Estimator: facts-only `/shadow`, 120s; no-data=0/failure=3. Inputs gold/Herat/XAU/G1/G2; weak evidence abstains. Dashboard uses accepted `PRIVATE_PRIMARY`, never legacy.
- Staging mirrors Iran; snapshots read-only. v3 bands ±10%; ties require choice. Packs are ×100/`PACK_ONLY`; oldest-first.
- Capture: `market_channel_event/1.0`,`coin_group_event/2.0`; receipt/revision/reply/allowlist authoritative; raw=3d, Store opaque.
- Parser v10/linker v7. Promotion needs version bump+production dominance; replay needs raw/Store/feedback/causal `MAIN_ONLINE`; ambiguity=REVIEW.
- Private melted: immutable price/quantity; lifetime=120s, freshness=900s. Lower remaining=cumulative fill; zero=full; positive close=no-trade; edit≠trade; inconsistency=ambiguous; partials finalize at deadline.
- Private quote uses first canonical header amount; ignore amounts after `توضیحات/شرایط`. Filter out-of-range per message, never process-wide.
- Cutover needs real 7d coin/gold/Herat; predated coins abstain; never synthesize anchors.
- 2026-08-29/30: Shadow→Legacy feeds G1/G2/private-gold; Product=`LEGACY`. Review causal; syntax needs raw; `human-feedback-r*`.
- 2026-08-31 temporary bridge: 22:00–23:00 Tehran carries only melted/Herat fresh at 22:00, labels `AFTER_CLOSE_HOLD`, and widens 0.4%. Never revive earlier/previous-day data; stop at 23:00. The 120s hard guard stays fail-open on effective age.
