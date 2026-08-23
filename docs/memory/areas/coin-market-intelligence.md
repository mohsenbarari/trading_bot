# Coin Market Intelligence

- USD/Herat: 3 facts/source/15m؛ no future.
- Settlement: خ ن ف/ف ن ف، خ ف/ف ف or none=tomorrow؛ خ ن/ف ن/نق=cash؛ future wins؛ accept joined/spaced/ZWNJ خف/فف.
- Prices: project-thousand؛ full-Toman /1,000؛ 1–3 digits ×1,000؛ 4 invalid. Exclude quantities/years؛ `رب`=quarter؛ `پ`/`ت پ`/`پایین`=low-date.
- Named needs no anchors. Unnamed=`MAIN_ONLINE` + 2h same-book anchors؛ contradictions fail. Bootstrap=3 messages/2 senders/30m/1.5% spread+fact.
- Trades: isolated replies, oldest root, reciprocal offerer؛ cancellation/rejection gate fills. Only explicit reciprocal first fill changes quantity؛ ambiguity/overfill audit.
- Hash mismatch never rewrites roots. Three causal same-instrument offers/5m make >max(5%, 6 deviations) audit. Prefer same-settlement؛ term structure yields to <=30m trade, 3 offers, or <=1% two-sided book.
- Reconcile rejects bad reply graphs, keeps first availability. Drop absent/rejected؛ pending/conditional/>5m-late audit. Models use `available_at_utc`.
- Malformed envelopes fail؛ inverted time cannot advance checkpoints. Private review authenticated؛ projection opaque؛ jobs on `main`.
- Estimator: `estimator-live`؛ CASH/TOMORROW؛ `/shadow` facts-only؛ `event_time`/120s؛ oneshot timers. `LOW_PAPER_FALLBACK` has no price authority؛ publisher transactional؛ SAFE_NO_DATA=0؛ failure=3.
- Regime: private-minute gold, Herat, XAU, G1/G2؛ split direction/volatility/books؛ low evidence abstains؛ hold-down/public fallback.
- Rates: private melted first؛ if absent use bounded fresh public aggregate/flow. Skip—never rescale—malformed legacy rows and advance checkpoints so later facts flow.
- Staging catalog mirrors Iran؛ Snapshot read-only. v3 bands؛ same-family ±10% confirm؛ ties require choice؛ edits reuse scope؛ no auto-select؛ stale taps fail.
- Packs require «پک»؛ infer تمام/نیم/ربع؛ quantity 100؛ no lots؛ audit=`PACK_ONLY`.
- No collector in bridge `After=`؛ use `flock`.
- Host readiness/relay stays dependency-light؛ never parse Compose `.env`. Closed-market staleness is `DEGRADED_GUARD_FAIL_OPEN` only with healthy inputs.
- Condition v2 separates settlement/form, phase, deadline, 11 families؛ verified spans. Sealed 240/live shadow stays off؛ retain evidence؛ resume needs explicit offline-evaluated decision.
- Index anchors؛ no message scan. Fetch newest-first/sort؛ oldest-first backlog preserves checkpoints.
