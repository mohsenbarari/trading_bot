# Coin Market Intelligence

- Herat: 3 facts/source/15m.
- Settlement: `خ ن ف/ف ن ف` or `خ ف/ف ف` or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins; spacing/ZWNJ variants apply.
- Units: project-thousand; accept full-Toman/separators/zeros. Resolve ambiguous 3/4-digit/tails only by family or unique causal near-time match, never constants. Exclude quantity/year; `رب`=quarter; `پ/ت پ/پایین`=low-date.
- Named offers need anchors only for scale ambiguity. Unnamed: prior `MAIN_ONLINE` ≤5m then 2h; contradiction fails. Bootstrap=3 messages/2 senders/30m/1.5%. Redacted learning has no economic fields.
- Trades use oldest root/exact branch. Unique counterparty sibling + owner confirmation is reciprocal; quantity markers beat price tails. Reject ambiguity/overfill; only explicit reciprocal first fill amends quantity.
- Gate: 3 same-instrument offers/5m reject >max(5%,6 deviations); same-settlement wins. ≤30m trade, 3 offers, or ≤1% two-sided book may override.
- Receipt-derived `available_at_utc` controls reconciliation. Absent/rejected drop; pending/conditional/>5m-late are audit-only. `delivery` is non-authoritative; fail closed.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, 120s events; transactional publisher; SAFE_NO_DATA=0/failure=3. Inputs: private gold, Herat, XAU, G1/G2; abstain on weak evidence. Prefer private melted, else bounded fresh public flow.
- Staging mirrors Iran; Snapshot read-only. v3 bands ±10%; ties require choice. `پک`=full/half/quarter, quantity=100, `PACK_ONLY`. Fetch newest-first then sort; backlog oldest-first. Jobs use `main`; collectors use `flock`.
- Capture contracts: `market_channel_event/1.0`, `coin_group_event/2.0`. Receipt, revisions, reply status, allowlisted `source_id` are authoritative; raw lasts 3d; Store stays opaque.
- Coin parser v9/trade linker v7 persist redacted field evidence and version. Production-shaped replay requires raw staging + Store + human feedback + causal `MAIN_ONLINE` ledger; omitting either sidecar is invalid. Uncontradicted complete explicit parses are eligible; unresolved ambiguity remains REVIEW. Web corrections append privacy-safe revisions to a calibration corpus.
- Private melted offer: immutable price/quantity from first revision, 120s lifetime. New/lower explicit remaining gives cumulative fill; zero=full, positive closure=no-trade. Generic edit≠trade; inconsistency=ambiguous; partials finalize at deadline. Routine source deletion after expiry does not retract the economic offer/outcome. Estimator freshness=900s.
