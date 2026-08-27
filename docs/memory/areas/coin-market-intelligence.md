# Coin Market Intelligence

- Settlement: `خ ن ف/ف ن ف`، `خ ف/ف ف` or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins; normalize spacing.
- Units: project-thousand; accept full-Toman/separators/zeros. Resolve tails only by family or unique causal near-time match, never constants; exclude quantity/year. `رب`=quarter; `پ/ت پ/پایین`=low-date. Multiline joins only one incomplete offer: one side line, one explicit-quantity line, ≤1 named-family line. A duplicated terminal zero on a 7-digit low-price quote needs an explicit family; unnamed Imam-scale keeps `/10`.
- Named anchors resolve scale only. Unnamed uses prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails; bootstrap=3 messages/2 senders/30m/1.5%.
- Trades: oldest root/exact branch; unique counterparty sibling+owner confirmation is reciprocal. Quantity markers beat tails; reject ambiguity/overfill. Only explicit first reciprocal fill amends quantity.
- Price gate: 3 same-instrument offers/5m reject >max(5%,6 deviations). Override needs ≤30m trade, 3 offers or ≤1% two-sided book.
- `available_at_utc` controls reconciliation. Drop absent/rejected; pending/conditional/>5m-late are audit-only. Ignore `delivery`; fail closed.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, 120s events, transactional; SAFE_NO_DATA=0/failure=3. Inputs: private gold/Herat/XAU/G1/G2. Abstain on weak evidence; prefer private melted.
- Staging mirrors Iran; snapshot read-only. v3 bands ±10%; ties require choice. `پک`=full/half/quarter, quantity=100, `PACK_ONLY`. Backlog oldest-first.
- Capture=`market_channel_event/1.0`,`coin_group_event/2.0`. Receipt/revisions/reply status/allowlisted source are authoritative; raw=3d; Store opaque.
- Parser v9/linker v7 persist redacted evidence. Replay needs raw/Store/feedback/causal `MAIN_ONLINE`. Complete uncontradicted parses are eligible; ambiguity stays REVIEW.
- Private melted: first price/quantity immutable; lifetime=120s. Lower remaining=cumulative fill; zero=full, positive closure=no-trade. Generic edit≠trade; inconsistency=ambiguous; partials finalize at deadline. Post-expiry deletion keeps economics; estimator freshness=900s.
- Before cutover seed 7d same-time melted/Herat anchors. Coins predating coverage abstain; not transport loss.
- Seed export uses per-source cutoffs, excluding target keys. Public melted is transient. Import is manifest-bound/backup-gated/idempotent. Only unlinked private outcomes may be omitted; group outcomes fail closed.
