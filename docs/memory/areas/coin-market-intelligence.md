# Coin Market Intelligence

- Herat: 3 facts/source/15m; no future data.
- Settlement: `خ ن ف/ف ن ف`, `خ ف/ف ف`, or none=tomorrow; `خ ن/ف ن/نق`=cash; future wins; accept joined/spaced/ZWNJ.
- Unit is project-thousand. Accept full-Toman, decimals/separators, missing/redundant zero. Ambiguous 3/4-digit/tails need explicit family or unique causal near-time match; never fixed corrections. Bands are guards. Exclude quantity/year; `رب`=quarter; `پ/ت پ/پایین`=low-date.
- Named offers need anchors only for ambiguous scale. Unnamed: `MAIN_ONLINE`, prior 5m then 2h. Keep bucket edges/transitions. Contradictions fail. Bootstrap=3 messages/2 senders/30m/1.5%.
- Economic fields/validity are exact-event only. Number-redacted learning covers side/form/conditional, never commodity/price/quantity/settlement/validity.
- Trades: oldest root, exact branch. One unique counterparty sibling plus owner confirmation is reciprocal. Bare 3-digit or near-root 2-digit values are price tails; explicit quantity markers win. Rejection/cancellation/multi-user ambiguity/overfill gate results; only explicit reciprocal first fill amends quantity.
- Hash mismatch never rewrites roots. Three same-instrument offers/5m gate >max(5%, 6 deviations). Same-settlement wins; <=30m trade, 3 offers, or <=1% two-sided book may override.
- Reconcile at receipt-derived `available_at_utc`. Absent/rejected drop; pending/conditional/>5m-late are audit-only. `delivery` has no authority. Checks fail closed; reviews authenticated; projections opaque.
- Estimator: `estimator-live`, CASH/TOMORROW, facts-only `/shadow`, 120s event-time, oneshot timers. `LOW_PAPER_FALLBACK` has no authority; publisher transactional; SAFE_NO_DATA=0/failure=3.
- Regime inputs: private gold, Herat, XAU, G1/G2; weak evidence abstains. Rates prefer private melted, else bounded fresh public flow. Skip malformed legacy rows; advance checkpoints.
- Staging catalog mirrors Iran; Snapshot read-only. v3 bands; family ±10%; ties require choice; stale taps fail. `پک` implies full/half/quarter, quantity=100, no lots, `PACK_ONLY`.
- Anchor lookup is indexed. Fetch newest-first/sort; backlog oldest-first. Jobs run from `main`; collectors use `flock`, never bridge `After=`. Readiness ignores Compose `.env`.
- Closed-market staleness is `DEGRADED_GUARD_FAIL_OPEN` only with healthy inputs. Condition v2 separates settlement/form, phase, deadline, 11 families. Sealed-240/live-shadow stays off pending offline evaluation.
