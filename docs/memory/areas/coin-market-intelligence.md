# Coin Market Intelligence

- Settlement: `خ ن ف/ف ن ف`,`خ ف/ف ف` or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins.
- Units: project-thousand; accept full-Toman/separators/zeros. Resolve tails by family or unique causal match, never constants. `رب`=quarter; `پ/ت پ/پایین`=low-date. Duplicated-zero low-price needs a family.
- Named anchors only resolve scale. Unnamed uses prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails; bootstrap=3 messages/2 senders/30m/1.5%.
- Trades: oldest root/exact branch; unique sibling+owner confirmation is reciprocal. Quantity markers beat tails; reject ambiguity/overfill. Only first explicit reciprocal fill amends quantity.
- Price gate: 3 same-instrument offers/5m reject >max(5%,6 deviations). Override needs ≤30m trade, 3 offers or ≤1% two-sided book.
- `available_at_utc` controls reconciliation. Drop absent/rejected; pending/conditional/>5m-late are audit-only. Ignore `delivery`; fail closed.
- Estimator: facts-only `/shadow`, 120s; no-data=0/failure=3. Inputs private gold/Herat/XAU/G1/G2; weak evidence abstains. Dashboard rates/inputs/health share one accepted `PRIVATE_PRIMARY`, never legacy state.
- Staging mirrors Iran; snapshots read-only. v3 bands ±10%; ties require choice. `پک`=full/half/quarter×100, `PACK_ONLY`; oldest-first.
- Capture=`market_channel_event/1.0`,`coin_group_event/2.0`; receipt/revision/reply/allowlist authoritative; raw=3d, Store opaque.
- Parser v10/linker v7. Promotion needs version bump+production dominance. Replay needs raw/Store/feedback/causal `MAIN_ONLINE`; ambiguity=REVIEW.
- Private melted: immutable price/quantity; lifetime=120s, freshness=900s. Lower remaining=cumulative fill; zero=full, positive closure=no-trade; edit≠trade; inconsistency=ambiguous; partials finalize at deadline.
- Private melted quote=first canonical header amount; amounts after `توضیحات/شرایط` are not prices. Out-of-range input is terminally filtered per message, never process-wide.
- Cutover needs real 7d coin/melted/Herat; `2026-08-25T09:33Z` waives nothing. Predating coins abstain; never synthesize anchors.
- 2026-08-29/30: Shadow→Legacy feeds G1/G2/private-gold; Product=`LEGACY`. Since cutoff: 1,249 offers/277 trades. Reviews causal; syntax needs raw; `human-feedback-r*`.
