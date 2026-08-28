# Coin Market Intelligence

- Settlement: `خ ن ف/ف ن ف`، `خ ف/ف ف` or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins.
- Units: project-thousand; accept full-Toman/separators/zeros. Resolve tails by family or unique causal near-time match, never constants; exclude quantity/year. `رب`=quarter; `پ/ت پ/پایین`=low-date. Join only one incomplete multiline offer (one side/quantity, ≤1 family). Duplicated-zero low-price needs an explicit family; unnamed Imam keeps `/10`.
- Named anchors resolve scale only. Unnamed uses prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails; bootstrap=3 messages/2 senders/30m/1.5%.
- Trades: oldest root/exact branch; unique counterparty sibling+owner confirmation is reciprocal. Quantity markers beat tails; reject ambiguity/overfill. Only the first explicit reciprocal fill amends quantity.
- Price gate: 3 same-instrument offers/5m reject >max(5%,6 deviations). Override needs ≤30m trade, 3 offers or ≤1% two-sided book.
- `available_at_utc` controls reconciliation. Drop absent/rejected; pending/conditional/>5m-late are audit-only. Ignore `delivery`; fail closed.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, events=120s; no-data=0/failure=3. Inputs: private gold/Herat/XAU/G1/G2. Weak evidence abstains; prefer private melted.
- Staging mirrors Iran; snapshots read-only. v3 bands ±10%; ties require choice. `پک`=full/half/quarter×100, `PACK_ONLY`; oldest-first.
- Capture=`market_channel_event/1.0`,`coin_group_event/2.0`; receipt/revisions/reply status/allowlist are authoritative; raw=3d, Store opaque.
- Parser v10/linker v7. v10 adds only proven multiline-offer and explicit low-price duplicated-zero recovery. Promotion needs version bump plus exact-production dominance; removal/mutation/unreviewed gain blocks. Replay needs raw/Store/feedback/causal `MAIN_ONLINE`; ambiguity=REVIEW.
- Private melted: initial price/quantity immutable; lifetime=120s. Lower remaining=cumulative fill; zero=full, positive closure=no-trade. Generic edit≠trade; inconsistency=ambiguous; partials finalize at deadline. Post-expiry deletion keeps economics; estimator freshness=900s.
- Before cutover seed the real 7d point-in-time coin/melted/Herat driver horizon. The 2026-08-25T09:33:00Z catch-up cutoff does not waive it; missing anchors block fresh `OK` 14/14 readiness. Predating coins abstain; never synthesize anchors or call absence transport loss.
