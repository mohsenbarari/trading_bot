# Coin Market Intelligence

- Herat: 3 facts/source/15m.
- Settlement: `خ ن ف/ف ن ف`, `خ ف/ف ف`, or none=tomorrow; `خ ن/ف ن/نق`=cash. Future wins; accept spacing/ZWNJ variants.
- Unit is project-thousand. Accept full-Toman, separators, and zero variants. Ambiguous 3/4-digit/tails require family or unique causal near-time match—never fixed corrections. Exclude quantity/year; `رب`=quarter; `پ/ت پ/پایین`=low-date.
- Named offers need anchors only for scale ambiguity. Unnamed: `MAIN_ONLINE`, prior 5m then 2h; contradictions fail. Bootstrap=3 messages/2 senders/30m/1.5%. Redacted learning never supplies economic fields.
- Trades use oldest root and exact branch. Unique counterparty sibling plus owner confirmation is reciprocal; quantity markers beat price tails. Rejection, ambiguity, and overfill gate results; only explicit reciprocal first fill amends quantity.
- Three same-instrument offers/5m gate >max(5%, 6 deviations). Same-settlement wins; <=30m trade, 3 offers, or <=1% two-sided book may override.
- Receipt-derived `available_at_utc` controls reconciliation. Absent/rejected drop; pending/conditional/>5m-late are audit-only. `delivery` is non-authoritative; checks fail closed.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, 120s event-time, oneshot timers; transactional publisher; SAFE_NO_DATA=0/failure=3.
- Inputs: private gold, Herat, XAU, G1/G2; weak evidence abstains. Rates prefer private melted, else bounded fresh public flow. Malformed legacy rows are skipped with checkpoint advance.
- Staging catalog mirrors Iran; Snapshot is read-only. v3 family bands ±10%; ties require choice. `پک` means full/half/quarter, quantity=100, `PACK_ONLY`.
- Fetch newest-first/sort; backlog oldest-first. Jobs run from `main`; collectors use `flock`. Closed-market guard may fail open only with healthy inputs.
- Capture accepts only `market_channel_event/1.0` and `coin_group_event/2.0`; receipt time, revisions, reply status, and allowlisted `source_id` are authoritative. Raw state is protected for 3 days; Store facts stay opaque.
- Primary melted offers are tradable for 120s from publication; edits never extend this or imply trades. Market observations retain independent estimator freshness (physical currently 900s). Post-cutover may be offers-only.
- Cutover seeds normalized facts, parser calibration, and external history—not raw Telegram data. Capture analysis runs on `65.109.220.59`; consumers require parity, freshness, magnitude, and open-market live gates.
