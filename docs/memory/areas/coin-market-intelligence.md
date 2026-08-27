# Coin Market Intelligence

- Settlement: `خ ن ف/ف ن ف`، `خ ف/ف ف` or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins; normalize spacing/ZWNJ.
- Units: project-thousand; accept full-Toman, separators and zeros. Resolve ambiguous tails only by family or unique causal near-time match, never constants. Exclude quantity/year; `رب`=quarter; `پ/ت پ/پایین`=low-date.
- Named anchors only resolve scale. Unnamed uses prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails; bootstrap=3 messages/2 senders/30m/1.5%.
- Trades: oldest root/exact branch; unique counterparty sibling plus owner confirmation is reciprocal. Quantity markers beat tails; reject ambiguity/overfill. Only an explicit first reciprocal fill amends quantity.
- Price gate: 3 same-instrument offers/5m reject >max(5%,6 deviations); same settlement wins. A ≤30m trade, 3 offers or ≤1% two-sided book may override.
- Receipt `available_at_utc` controls reconciliation. Drop absent/rejected; pending, conditional or >5m-late are audit-only. Ignore `delivery`; fail closed.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, 120s events; transactional publish; SAFE_NO_DATA=0/failure=3. Inputs: private gold, Herat, XAU, G1/G2. Abstain on weak evidence; prefer private melted, else bounded public flow.
- Staging mirrors Iran; snapshot is read-only. v3 bands ±10%; ties require choice. `پک`=full/half/quarter, quantity=100, `PACK_ONLY`. Fetch newest-first, backlog oldest-first.
- Capture contracts are `market_channel_event/1.0` and `coin_group_event/2.0`. Receipt, revisions, reply status and allowlisted `source_id` are authoritative; raw lasts 3d; Store stays opaque.
- Coin parser v9/trade linker v7 persist redacted evidence/version. Replay needs raw, Store, feedback and causal `MAIN_ONLINE`. Complete uncontradicted parses are eligible; ambiguity stays REVIEW.
- Private melted: first price/quantity immutable; lifetime 120s. Lower remaining is cumulative fill; zero=full, positive closure=no-trade. Generic edit≠trade; inconsistency=ambiguous; partials finalize at deadline. Post-expiry deletion does not retract economics. Estimator freshness=900s.
- Before cutover, seed coin anchors with same-time melted/Herat for seven days. A coin predating underlying coverage must abstain; this is not transport loss.
- Seed export uses per-source cutoffs and excludes target-existing keys. Public melted aggregate/flow is transient Store seed, never permanent archive. Import is manifest-bound, backup-gated and idempotent. Only unlinked private outcomes may be omitted; group outcomes remain fail-closed.
