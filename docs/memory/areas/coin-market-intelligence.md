# Coin Market Intelligence

- Herat: 3 facts/source/15m.
- Settlement: `خ ن ف/ف ن ف`, `خ ف/ف ف`, or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins; accept spacing/ZWNJ variants.
- Units: project-thousand; accept full-Toman/separators/zero variants. Resolve ambiguous 3/4-digit/tails only by family or unique causal near-time match—never fixed correction. Exclude quantity/year; `رب`=quarter; `پ/ت پ/پایین`=low-date.
- Named offers need anchors only for scale ambiguity. Unnamed: prior `MAIN_ONLINE` within 5m then 2h; contradiction fails. Bootstrap=3 messages/2 senders/30m/1.5%. Redacted learning supplies no economic fields.
- Trades use oldest root/exact branch. Unique counterparty sibling plus owner confirmation is reciprocal; quantity markers beat price tails. Reject ambiguity/overfill; only explicit reciprocal first fill amends quantity.
- Gate: 3 same-instrument offers/5m reject >max(5%, 6 deviations). Same-settlement wins; <=30m trade, 3 offers, or <=1% two-sided book may override.
- Receipt-derived `available_at_utc` controls reconciliation. Absent/rejected drop; pending/conditional/>5m-late are audit-only. `delivery` is non-authoritative; checks fail closed.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, 120s events, oneshot; transactional publisher; SAFE_NO_DATA=0/failure=3.
- Inputs: private gold, Herat, XAU, G1/G2; weak evidence abstains. Rates prefer private melted, else bounded fresh public flow. Malformed legacy rows are skipped with checkpoint advance.
- Staging mirrors Iran; Snapshot is read-only. v3 family bands ±10%; ties require choice. `پک` means full/half/quarter, quantity=100, `PACK_ONLY`.
- Fetch newest-first then sort; backlog oldest-first. Jobs run from `main`; collectors use `flock`. Closed-market fail-open requires healthy inputs.
- Capture: only `market_channel_event/1.0` and `coin_group_event/2.0`. Receipt, revisions, reply status, and allowlisted `source_id` are authoritative; raw lasts 3d; Store stays opaque.
- Primary melted: immutable 120s offer lifetime, bounded revisions. New/lower explicit remaining yields cumulative fill (`initial - remaining`); zero=full; positive closure=no-trade. Generic edit is not trade; inconsistency=ambiguous; partials finalize at deadline. Estimator freshness is separate (900s).
- Cutover seeds facts/calibration/external history—not raw Telegram. Analysis runs on `65.109.220.59`; promotion requires parity/freshness/magnitude/open-market live gate.
