# Coin Market Intelligence

- Settlement: `خ ن ف/ف ن ف`، `خ ف/ف ف` or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins.
- Units: project-thousand; accept full-Toman/separators/zeros. Resolve tails by family or unique causal near-time match, never constants. `رب`=quarter; `پ/ت پ/پایین`=low-date. One incomplete multiline offer only. Duplicated-zero low-price needs an explicit family.
- Named anchors resolve scale only. Unnamed uses prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails; bootstrap=3 messages/2 senders/30m/1.5%.
- Trades: oldest root/exact branch; unique counterparty sibling+owner confirmation is reciprocal. Quantity markers beat tails; reject ambiguity/overfill. Only the first explicit reciprocal fill amends quantity.
- Price gate: 3 same-instrument offers/5m reject >max(5%,6 deviations). Override needs ≤30m trade, 3 offers or ≤1% two-sided book.
- `available_at_utc` controls reconciliation. Drop absent/rejected; pending/conditional/>5m-late are audit-only. Ignore `delivery`; fail closed.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, events=120s; no-data=0/failure=3. Inputs: private gold/Herat/XAU/G1/G2. Weak evidence abstains; prefer private melted.
- Staging mirrors Iran; snapshots read-only. v3 bands ±10%; ties require choice. `پک`=full/half/quarter×100, `PACK_ONLY`; oldest-first.
- Capture=`market_channel_event/1.0`,`coin_group_event/2.0`; receipt/revisions/reply status/allowlist are authoritative; raw=3d, Store opaque.
- Parser v10/linker v7. Promotion needs version bump plus exact-production dominance. Replay needs raw/Store/feedback/causal `MAIN_ONLINE`; ambiguity=REVIEW.
- Private melted: initial price/quantity immutable; lifetime=120s. Lower remaining=cumulative fill; zero=full, positive closure=no-trade. Generic edit≠trade; inconsistency=ambiguous; partials finalize at deadline. Post-expiry deletion keeps economics; estimator freshness=900s.
- Cutover still needs a real 7d coin/melted/Herat horizon; cutoff `2026-08-25T09:33:00Z` does not waive it. Predating coins abstain; never synthesize anchors.
- 2026-08-29: Shadow→Legacy bridge feeds `GROUP_1`/`GROUP_2`/`PRIVATE_GOLD_CHANNEL` into current estimator inputs. Product stays `LEGACY`. Not a `PRIVATE_PRIMARY` cutover. Do not recreate healthy Shadow capture just to inject backfill env.
