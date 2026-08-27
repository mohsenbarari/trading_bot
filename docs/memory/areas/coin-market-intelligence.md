# Coin Market Intelligence

- Settlement: `خ ن ف/ف ن ف`، `خ ف/ف ف` or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins; normalize spacing/ZWNJ.
- Units: project-thousand; accept full-Toman/separators/zeros. Resolve ambiguous 3/4-digit/tails only by family or unique causal near-time match, never constants. Exclude quantity/year; `رب`=quarter; `پ/ت پ/پایین`=low-date.
- Named anchors only for scale ambiguity. Unnamed uses prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails; bootstrap=3 messages/2 senders/30m/1.5%.
- Trades: oldest root/exact branch; unique counterparty sibling + owner confirmation is reciprocal; quantity markers beat tails. Reject ambiguity/overfill; only explicit first reciprocal fill amends quantity.
- Gate: 3 same-instrument offers/5m reject >max(5%,6 deviations); same-settlement wins. A ≤30m trade, 3 offers or ≤1% two-sided book may override.
- Receipt `available_at_utc` controls reconciliation. Drop absent/rejected; pending/conditional/>5m-late are audit-only. Ignore `delivery`; fail closed.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, 120s events; transactional publisher; SAFE_NO_DATA=0/failure=3. Inputs: private gold, Herat (3/source/15m), XAU, G1/G2. Abstain on weak evidence; prefer private melted, else bounded public flow.
- Staging mirrors Iran; Snapshot read-only. v3 bands ±10%; ties require choice. `پک`=full/half/quarter, quantity=100, `PACK_ONLY`. Fetch newest-first; backlog oldest-first. Jobs=`main`; collectors=`flock`.
- Capture: `market_channel_event/1.0`, `coin_group_event/2.0`. Receipt, revisions, reply status and allowlisted `source_id` are authoritative; raw lasts 3d; Store stays opaque.
- Coin parser v9/trade linker v7 persist redacted evidence/version. Valid replay needs raw staging, Store, feedback and causal `MAIN_ONLINE` ledger. Complete uncontradicted explicit parses are eligible; ambiguity stays REVIEW. Web corrections append privacy-safe calibration revisions.
- Private melted: first price/quantity immutable; lifetime 120s. New/lower remaining is cumulative fill; zero=full, positive closure=no-trade. Generic edit≠trade; inconsistency=ambiguous; partials finalize at deadline. Routine post-expiry deletion does not retract economics. Estimator freshness=900s.
- Before cutover, seed coin anchors and same-time melted/Herat underlyings for the seven-day horizon. Live-only Store abstention when a coin predates underlying coverage is not transport loss; keep fail-closed.
